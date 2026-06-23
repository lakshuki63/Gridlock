"""
VisionEnforce — Main Video Processing Orchestrator

Reads a video file (or RTSP stream), runs the full pipeline,
saves evidence, and broadcasts events via WebSocket.
"""

import asyncio
import logging
import time
from collections import deque
from pathlib import Path
from typing import Optional, Callable, Awaitable

from config import settings, load_calibration
from pipeline.violation_engine import ViolationEngine, SimulatedSignalController

logger = logging.getLogger(__name__)


class ProcessingStatus:
    def __init__(self):
        self.is_running      = False
        self.video_path      = ""
        self.total_frames    = 0
        self.processed_frames = 0
        self.violations_found = 0
        self.fps             = 0.0
        self.start_time: Optional[float] = None
        self.error: Optional[str] = None

    def to_dict(self) -> dict:
        elapsed = (time.time() - self.start_time) if self.start_time else 0
        progress = (self.processed_frames / self.total_frames * 100) if self.total_frames > 0 else 0
        return {
            "is_running":        self.is_running,
            "video_path":        self.video_path,
            "total_frames":      self.total_frames,
            "processed_frames":  self.processed_frames,
            "progress_pct":      round(progress, 1),
            "violations_found":  self.violations_found,
            "processing_fps":    round(self.fps, 1),
            "elapsed_seconds":   round(elapsed, 1),
            "error":             self.error,
        }


# Global status singleton
processing_status = ProcessingStatus()


class VideoProcessor:
    """
    Full pipeline orchestrator for a single video source.

    Usage:
        processor = VideoProcessor(camera_id="CAM-DEMO-01", video_path="video.mp4")
        await processor.run(db_session, broadcast_fn)
    """

    def __init__(self, camera_id: str, video_path: str):
        from pipeline.detector import VisionEnforceDetector
        from pipeline.plate_ocr import PlateOCR
        from pipeline.evidence_packager import EvidencePackager

        self.camera_id  = camera_id
        self.video_path = video_path
        self.calibration = load_calibration(camera_id)
        self.camera_name = self.calibration.get("camera_name", camera_id)

        self.detector   = VisionEnforceDetector()
        self.signal     = SimulatedSignalController()
        self.engine     = ViolationEngine(self.calibration, self.signal)
        self.ocr        = PlateOCR()
        self.packager   = EvidencePackager()

        # Rolling frame buffer for clip generation
        self._frame_buffer: deque = deque(maxlen=int(25 * 6))   # 6s @ 25fps

        # Per-track OCR crop accumulator
        self._ocr_crops: dict[int, list] = {}

    async def run(
        self,
        db_session,
        broadcast: Callable[[dict], Awaitable[None]],
    ):
        """
        Main processing loop. Runs until video ends or is stopped.
        Calls broadcast(event_dict) for every confirmed violation.
        """
        global processing_status
        import cv2

        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            msg = f"Cannot open video: {self.video_path}"
            logger.error(msg)
            processing_status.error = msg
            processing_status.is_running = False
            return

        fps_src     = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total       = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        frame_idx   = 0
        skip_every  = 2          # process every Nth frame for speed
        t_last      = time.time()

        processing_status.is_running       = True
        processing_status.video_path       = self.video_path
        processing_status.total_frames     = total
        processing_status.processed_frames = 0
        processing_status.violations_found = 0
        processing_status.start_time       = time.time()
        processing_status.error            = None

        logger.info(f"Starting processing: {self.video_path} ({total} frames @ {fps_src:.1f} fps)")

        try:
            while cap.isOpened() and processing_status.is_running:
                ret, frame = cap.read()
                if not ret:
                    break

                frame_idx += 1
                processing_status.processed_frames = frame_idx

                # ── Adaptive frame skipping ──────────────────────────
                if frame_idx % skip_every != 0:
                    self._frame_buffer.append((frame_idx, frame.copy()))
                    continue

                # ── Detection + Tracking ─────────────────────────────
                detections = self.detector.process_frame(frame)

                # ── Violation Reasoning ──────────────────────────────
                events = self.engine.process_frame(
                    detections=detections,
                    frame=frame,
                    frame_idx=frame_idx,
                    frame_size=frame.shape,
                )

                # ── Accumulate OCR crops per track ───────────────────
                for det in detections:
                    if det.is_vehicle:
                        crop = PlateOCR.crop_plate_region(frame, det.bbox)
                        if crop is not None:
                            if det.track_id not in self._ocr_crops:
                                self._ocr_crops[det.track_id] = []
                            crops = self._ocr_crops[det.track_id]
                            if len(crops) < settings.OCR_MULTI_FRAME_ATTEMPTS:
                                crops.append(crop)

                # ── Process violation events ─────────────────────────
                for event in events:
                    await self._handle_event(event, db_session, broadcast)

                # ── Frame buffer (for clip) ──────────────────────────
                self._frame_buffer.append((frame_idx, frame.copy()))

                # ── FPS meter ────────────────────────────────────────
                now = time.time()
                processing_status.fps = skip_every / max(now - t_last, 1e-6)
                t_last = now

                # ── Yield to event loop every 10 frames ─────────────
                if frame_idx % 10 == 0:
                    await asyncio.sleep(0)

        except Exception as e:
            logger.exception(f"Processing error: {e}")
            processing_status.error = str(e)
        finally:
            cap.release()
            processing_status.is_running = False
            logger.info(f"Processing complete. {processing_status.violations_found} violations found.")

    async def _handle_event(self, event, db_session, broadcast):
        """Run OCR, package evidence, save to DB, and broadcast."""
        from database.crud import create_violation

        # ── OCR ──────────────────────────────────────────────────
        crops      = self._ocr_crops.get(event.track_id, [])
        plate_data = self.ocr.aggregate_ocr(crops)

        # ── Evidence packaging ────────────────────────────────────
        record = self.packager.package(
            violation_event=event,
            camera_id=self.camera_id,
            camera_name=self.camera_name,
            plate_result=plate_data,
            frame_buffer=list(self._frame_buffer),
            fps=25.0,
        )

        if not record:
            return   # below confidence threshold — discarded

        # ── Save to DB ────────────────────────────────────────────
        try:
            violation = await create_violation(db_session, record)
            processing_status.violations_found += 1

            # ── Broadcast via WebSocket ───────────────────────────
            cam_loc = self.calibration.get("location", {})
            msg = {
                "type": "violation_event",
                "data": {
                    "id":                    violation.id,
                    "camera_id":             self.camera_id,
                    "camera_name":           self.camera_name,
                    "violation_type":        event.violation_type,
                    "violation_label":       violation.to_dict().get("violation_label"),
                    "severity":              record["severity"],
                    "vehicle_class":         event.vehicle_class,
                    "license_plate":         plate_data.get("text"),
                    "plate_confidence":      plate_data.get("confidence", 0.0),
                    "detection_confidence":  round(event.confidence, 3),
                    "violation_confidence":  round(event.confidence, 3),
                    "timestamp":             record["timestamp_utc"].isoformat(),
                    "frame_url":             f"/evidence/frames/{violation.id}.jpg",
                    "clip_url":              f"/evidence/clips/{violation.id}.mp4",
                    "review_status":         record["review_status"].value,
                    "location":              cam_loc,
                    "extra":                 event.extra,
                },
            }
            await broadcast(msg)

        except Exception as e:
            logger.error(f"DB/broadcast error for event {event.track_id}: {e}")


# ─────────── Demo simulation (no video file) ─────────────────────

import random
from database.models import ViolationType, ReviewStatus, Severity, VIOLATION_LABELS, VIOLATION_SEVERITY

_DEMO_CAMERAS = [
    ("CAM-DEMO-01", "KR Circle — North Entry",             12.9716, 77.5946),
    ("CAM-DEMO-02", "Silk Board Junction — East",           12.9177, 77.6228),
    ("CAM-DEMO-03", "MG Road Signal — West",               12.9757, 77.6086),
    ("CAM-DEMO-04", "Koramangala 5th Block — Main Rd",      12.9352, 77.6245),
    ("CAM-DEMO-05", "Indiranagar 100 Ft Road — East",       12.9784, 77.6408),
    ("CAM-DEMO-06", "Hebbal Flyover — North",               13.0382, 77.5919),
    ("CAM-DEMO-07", "Electronic City Toll — South",         12.8452, 77.6602),
    ("CAM-DEMO-08", "Whitefield ITPL Main Rd",              12.9850, 77.7360),
    ("CAM-DEMO-09", "Jayanagar 4th Block Signal",           12.9250, 77.5938),
    ("CAM-DEMO-10", "Marathahalli Bridge — West",           12.9563, 77.7010),
]

# Weighted distribution: biased toward illegal parking since it's the primary
# congestion precursor signal. Other violation types also present.
_DEMO_VIOLATION_WEIGHTS = [
    (ViolationType.ILLEGAL_PARKING,      0.45),
    (ViolationType.WRONG_SIDE_DRIVING,   0.15),
    (ViolationType.RED_LIGHT_VIOLATION,  0.20),
    (ViolationType.STOP_LINE_VIOLATION,  0.20),
]

_DEMO_VEHICLES = ["motorcycle", "car", "bus", "truck", "bicycle"]
_DEMO_PLATES   = [
    "KA05AB1234", "KA01EF5678", "KA03CD9012", "KA50GH3456",
    "MH12BB7890", "TN09AA1111", "DL3CBA4321", "KA19XY2222",
]


async def run_demo_simulation(db_session, broadcast: Callable, n_events: int = 30):
    """
    Generate realistic mock violation events for demo purposes
    (when no video file is available).
    """
    global processing_status
    processing_status.is_running       = True
    processing_status.video_path       = "DEMO SIMULATION MODE"
    processing_status.total_frames     = n_events * 25
    processing_status.processed_frames = 0
    processing_status.violations_found = 0
    processing_status.start_time       = time.time()

    from database.crud import create_violation
    from datetime import datetime, timezone

    for i in range(n_events):
        if not processing_status.is_running:
            break

        # Random delay between events (0.8 – 3.5 seconds)
        await asyncio.sleep(random.uniform(0.8, 3.5))

        cam_id, cam_name, lat, lon = random.choice(_DEMO_CAMERAS)

        # Weighted violation type selection
        vtypes, weights = zip(*_DEMO_VIOLATION_WEIGHTS)
        vtype = random.choices(vtypes, weights=weights, k=1)[0]

        vehicle  = random.choice(_DEMO_VEHICLES)
        plate    = random.choice(_DEMO_PLATES)
        severity = VIOLATION_SEVERITY.get(vtype, Severity.MEDIUM)
        v_conf   = round(random.uniform(0.62, 0.97), 3)
        p_conf   = round(random.uniform(0.78, 0.98), 3)

        import uuid
        now_utc = datetime.now(timezone.utc)
        ev_id   = f"EVD-{now_utc.year}-BLR-{str(uuid.uuid4()).replace('-','')[:8].upper()}"

        if v_conf >= settings.AUTO_PROCESS_THRESHOLD:
            status = ReviewStatus.AUTO_PROCESSED
        else:
            status = ReviewStatus.PENDING_HUMAN

        # Build rich extra_metadata for risk scoring
        extra = {}
        if vtype == ViolationType.ILLEGAL_PARKING:
            # Biased toward longer durations so the risk score has something to work with
            # For demo: 60% chance of long parking (>5 min), 40% short
            if random.random() < 0.60:
                dur_sec = random.randint(320, 900)   # 5.3 to 15 minutes
            else:
                dur_sec = random.randint(60, 300)    # 1 to 5 minutes
            extra["parking_duration_seconds"] = dur_sec
            extra["stationary_frames"] = dur_sec // 2  # approx at 0.5fps

        # Vehicle density: clusters at busy cameras
        density_base = {"CAM-DEMO-01": 9, "CAM-DEMO-02": 12, "CAM-DEMO-03": 6,
                        "CAM-DEMO-04": 8, "CAM-DEMO-05": 10}.get(cam_id, 7)
        extra["vehicle_density"] = random.randint(max(3, density_base - 3), density_base + 5)

        record = {
            "id":                    ev_id,
            "camera_id":             cam_id,
            "track_id":              str(random.randint(100, 999)),
            "violation_type":        vtype,
            "severity":              severity.value,
            "vehicle_class":         vehicle,
            "vehicle_color":         random.choice(["red", "white", "black", "silver", "blue"]),
            "license_plate":         plate,
            "plate_raw_reads":       [plate],
            "plate_confidence":      p_conf,
            "detection_confidence":  v_conf,
            "violation_confidence":  v_conf,
            "frames_with_violation": random.randint(3, 15),
            "timestamp_utc":         now_utc.replace(tzinfo=None),
            "annotated_frame_path":  None,
            "annotated_clip_path":   None,
            "provenance_hash":       f"sha256:demo{i:06d}",
            "review_status":         status,
            "extra_metadata":        extra,
        }

        try:
            await create_violation(db_session, record)
            processing_status.violations_found += 1
            processing_status.processed_frames += 25

            msg = {
                "type": "violation_event",
                "data": {
                    "id":                   ev_id,
                    "camera_id":            cam_id,
                    "camera_name":          cam_name,
                    "violation_type":       vtype.value,
                    "violation_label":      VIOLATION_LABELS.get(vtype, "Unknown"),
                    "severity":             severity.value,
                    "vehicle_class":        vehicle,
                    "license_plate":        plate,
                    "plate_confidence":     p_conf,
                    "detection_confidence": v_conf,
                    "violation_confidence": v_conf,
                    "timestamp":            now_utc.isoformat(),
                    "frame_url":            None,
                    "clip_url":             None,
                    "review_status":        status.value,
                    "location":             {"lat": lat, "lon": lon, "landmark": cam_name},
                    "extra":                {},
                },
            }
            await broadcast(msg)
            logger.info(f"[DEMO] Event {i+1}/{n_events}: {vtype.value} @ {cam_name} — {plate}")

        except Exception as e:
            logger.error(f"Demo event error: {e}")

    processing_status.is_running = False
    logger.info("Demo simulation complete.")
