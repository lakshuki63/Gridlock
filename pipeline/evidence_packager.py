"""
VisionEnforce — Evidence Packager

Creates court-grade evidence records: annotated JPEG frame,
5-second MP4 clip, SHA-256 provenance hash, and JSON record.
"""

import cv2
import json
import uuid
import hashlib
import logging
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import settings
from database.models import ViolationType, ReviewStatus, VIOLATION_SEVERITY, Severity, VIOLATION_LABELS

logger = logging.getLogger(__name__)

VIOLATION_COLORS = {
    "RED_LIGHT_VIOLATION":  (0,   0,   255),   # Red
    "STOP_LINE_VIOLATION":  (0,   128, 255),   # Orange-ish
    "WRONG_SIDE_DRIVING":   (0,   0,   200),   # Dark red
    "ILLEGAL_PARKING":      (0,   215, 255),   # Yellow
}


def _generate_evidence_id() -> str:
    now  = datetime.now(timezone.utc)
    uid  = str(uuid.uuid4()).replace("-", "")[:8].upper()
    return f"EVD-{now.year}-BLR-{uid}"


def _compute_hash(file_path: Path) -> str:
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return f"sha256:{sha.hexdigest()}"


def annotate_evidence_frame(
    frame: np.ndarray,
    violation_type: str,
    bbox: list,
    plate_text: Optional[str],
    confidence: float,
    camera_name: str,
    timestamp: datetime,
    evidence_id: str,
) -> np.ndarray:
    """Draw a professional annotation overlay on the evidence frame."""
    annotated = frame.copy()
    h, w = annotated.shape[:2]
    color = VIOLATION_COLORS.get(violation_type, (0, 0, 255))
    label = VIOLATION_LABELS.get(violation_type, violation_type.replace("_", " "))

    # ── Vehicle bounding box ───────────────────────────────────────
    x1, y1, x2, y2 = [int(c) for c in bbox]
    cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 3)

    # ── Violation badge at top of bbox ────────────────────────────
    badge_text = f"⚠ {label}"
    (bw, bh), _ = cv2.getTextSize(badge_text, cv2.FONT_HERSHEY_DUPLEX, 0.65, 1)
    cv2.rectangle(annotated, (x1, y1 - bh - 12), (x1 + bw + 10, y1), color, -1)
    cv2.putText(annotated, badge_text, (x1 + 5, y1 - 5),
                cv2.FONT_HERSHEY_DUPLEX, 0.65, (255, 255, 255), 1, cv2.LINE_AA)

    # ── Plate text (below bbox) ───────────────────────────────────
    if plate_text:
        plate_str = f"Plate: {plate_text}  Conf: {confidence:.0%}"
        (pw, ph), _ = cv2.getTextSize(plate_str, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.rectangle(annotated, (x1, y2), (x1 + pw + 10, y2 + ph + 10), (20, 20, 20), -1)
        cv2.putText(annotated, plate_str, (x1 + 5, y2 + ph + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 200), 1, cv2.LINE_AA)

    # ── Bottom info bar ───────────────────────────────────────────
    bar_h = 36
    overlay = annotated.copy()
    cv2.rectangle(overlay, (0, h - bar_h), (w, h), (10, 10, 10), -1)
    cv2.addWeighted(overlay, 0.75, annotated, 0.25, 0, annotated)

    ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    info = f"  {camera_name}  |  {ts_str}  |  ID: {evidence_id}  |  VisionEnforce v1.0"
    cv2.putText(annotated, info, (8, h - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1, cv2.LINE_AA)

    # ── Top-right VisionEnforce watermark ────────────────────────
    wm = "[ VisionEnforce | BTP ]"
    (ww, wh), _ = cv2.getTextSize(wm, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
    cv2.putText(annotated, wm, (w - ww - 10, 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1, cv2.LINE_AA)

    return annotated


class EvidencePackager:
    """
    Assembles evidence records from ViolationEvent objects.
    Saves annotated frames, clips, and returns a database-ready dict.
    """

    def __init__(self):
        settings.FRAMES_DIR.mkdir(parents=True, exist_ok=True)
        settings.CLIPS_DIR.mkdir(parents=True, exist_ok=True)

    def package(
        self,
        violation_event,        # ViolationEvent from violation_engine
        camera_id: str,
        camera_name: str,
        plate_result: dict,     # from PlateOCR.aggregate_ocr
        frame_buffer: list,     # list of (frame_idx, np.ndarray) for clip generation
        fps: float = 25.0,
    ) -> dict:
        """
        Create full evidence record.
        Returns dict ready for database insertion.
        """
        evidence_id = _generate_evidence_id()
        now_utc = datetime.now(timezone.utc)

        vtype    = violation_event.violation_type
        severity = VIOLATION_SEVERITY.get(vtype, Severity.MEDIUM)

        plate_text = plate_result.get("text")
        plate_conf = plate_result.get("confidence", 0.0)
        all_reads  = plate_result.get("all_reads", [])

        # ── Annotated frame ───────────────────────────────────────
        annotated = annotate_evidence_frame(
            frame=violation_event.frame,
            violation_type=vtype,
            bbox=violation_event.bbox,
            plate_text=plate_text,
            confidence=violation_event.confidence,
            camera_name=camera_name,
            timestamp=now_utc,
            evidence_id=evidence_id,
        )

        frame_path = settings.FRAMES_DIR / f"{evidence_id}.jpg"
        cv2.imwrite(str(frame_path), annotated,
                    [cv2.IMWRITE_JPEG_QUALITY, settings.EVIDENCE_JPEG_QUALITY])

        provenance_hash = _compute_hash(frame_path)

        # ── Short clip (best available frames from buffer) ─────────
        clip_path = None
        if frame_buffer:
            clip_path = settings.CLIPS_DIR / f"{evidence_id}.mp4"
            h_f, w_f = frame_buffer[0][1].shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(clip_path), fourcc, fps, (w_f, h_f))
            for _, frm in frame_buffer[-int(fps * 5):]:   # last 5 seconds
                writer.write(frm)
            writer.release()

        # ── Determine review routing ──────────────────────────────
        vc = violation_event.confidence
        if vc >= settings.AUTO_PROCESS_THRESHOLD:
            review_status = ReviewStatus.AUTO_PROCESSED
        elif vc >= settings.HUMAN_REVIEW_THRESHOLD:
            review_status = ReviewStatus.PENDING_HUMAN
        else:
            review_status = None   # discard — below minimum threshold

        if review_status is None:
            logger.debug(f"[{evidence_id}] Confidence {vc:.2f} below threshold — discarded.")
            frame_path.unlink(missing_ok=True)
            return {}

        record = {
            "id": evidence_id,
            "camera_id": camera_id,
            "track_id": str(violation_event.track_id),
            "violation_type": vtype,
            "severity": severity.value,
            "vehicle_class": violation_event.vehicle_class,
            "vehicle_color": None,
            "license_plate": plate_text,
            "plate_raw_reads": all_reads,
            "plate_confidence": plate_conf,
            "detection_confidence": violation_event.confidence,
            "violation_confidence": round(violation_event.confidence, 3),
            "frames_with_violation": violation_event.extra.get("frames_count", 1),
            "timestamp_utc": now_utc.replace(tzinfo=None),
            "annotated_frame_path": str(frame_path),
            "annotated_clip_path": str(clip_path) if clip_path else None,
            "provenance_hash": provenance_hash,
            "review_status": review_status,
            "extra_metadata": violation_event.extra,
        }

        logger.info(
            f"[{evidence_id}] {vtype} | "
            f"Plate: {plate_text or 'N/A'} ({plate_conf:.0%}) | "
            f"Conf: {vc:.2f} → {review_status.value}"
        )
        return record
