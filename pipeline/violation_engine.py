"""
VisionEnforce — Violation Reasoning Engine (Finite State Machine)

Each tracked vehicle gets its own VehicleTrack state machine.
The engine evaluates all tracks per frame against calibrated ROIs
and signal state, emitting ViolationEvent objects when thresholds are met.
"""

import math
import time
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum

from config import settings

logger = logging.getLogger(__name__)


# ─────────── Signal State ─────────────────────────────────────────

class SignalState(str, Enum):
    RED    = "RED"
    YELLOW = "YELLOW"
    GREEN  = "GREEN"


class SimulatedSignalController:
    """
    Time-based simulated traffic signal controller.
    Cycles: GREEN → YELLOW → RED → repeat
    """

    def __init__(
        self,
        green_sec: int  = settings.SIGNAL_GREEN_DURATION,
        yellow_sec: int = settings.SIGNAL_YELLOW_DURATION,
        red_sec: int    = settings.SIGNAL_RED_DURATION,
    ):
        self.green_sec  = green_sec
        self.yellow_sec = yellow_sec
        self.red_sec    = red_sec
        self.cycle      = green_sec + yellow_sec + red_sec
        self._start     = time.time()

    @property
    def state(self) -> SignalState:
        elapsed = (time.time() - self._start) % self.cycle
        if elapsed < self.green_sec:
            return SignalState.GREEN
        elif elapsed < self.green_sec + self.yellow_sec:
            return SignalState.YELLOW
        else:
            return SignalState.RED

    @property
    def seconds_since_red(self) -> float:
        """How many seconds since RED phase started (0 if not RED)."""
        elapsed = (time.time() - self._start) % self.cycle
        red_start = self.green_sec + self.yellow_sec
        if elapsed >= red_start:
            return elapsed - red_start
        return 0.0


# ─────────── Geometry Helpers ─────────────────────────────────────

def point_in_polygon(px: float, py: float, polygon: list) -> bool:
    """Ray-casting algorithm for point-in-polygon test."""
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def normalize_polygon(poly: list, w: int, h: int) -> list:
    """Convert normalized [0..1] polygon to pixel coordinates."""
    return [[int(x * w), int(y * h)] for x, y in poly]


def compute_heading(positions: list) -> Optional[float]:
    """
    Compute heading angle in degrees from a list of (cx, cy) positions.
    0° = right, 90° = down, 180° = left, 270° = up.
    Returns None if not enough data.
    """
    if len(positions) < 4:
        return None
    # Use last 8 positions for stability
    pts = list(positions)[-8:]
    dx = pts[-1][0] - pts[0][0]
    dy = pts[-1][1] - pts[0][1]
    if abs(dx) < 1 and abs(dy) < 1:
        return None   # stationary
    angle = math.degrees(math.atan2(dy, dx))
    return angle % 360


def compute_velocity(positions: list) -> float:
    """Average pixel displacement per frame over last 5 positions."""
    if len(positions) < 2:
        return 0.0
    pts = list(positions)[-5:]
    displacements = [
        math.hypot(pts[i][0] - pts[i-1][0], pts[i][1] - pts[i-1][1])
        for i in range(1, len(pts))
    ]
    return sum(displacements) / len(displacements)


def angle_diff(a: float, b: float) -> float:
    """Smallest angle between two headings (degrees), -180..180."""
    d = (a - b + 180) % 360 - 180
    return d


# ─────────── Violation Event ──────────────────────────────────────

@dataclass
class ViolationEvent:
    track_id: int
    violation_type: str
    confidence: float
    frame: object            # np.ndarray — best evidence frame
    frame_index: int
    vehicle_class: str
    bbox: list
    extra: dict = field(default_factory=dict)   # parking_duration, heading_angle, etc.


# ─────────── Per-Track State Machine ─────────────────────────────

class TrackState(str, Enum):
    ACTIVE            = "ACTIVE"
    VIOLATION_PENDING = "VIOLATION_PENDING"
    VIOLATION_FIRED   = "VIOLATION_FIRED"   # emitted once per track


@dataclass
class VehicleTrack:
    track_id: int
    vehicle_class: str

    # Positional history (centre-point of bbox)
    position_history: deque = field(default_factory=lambda: deque(maxlen=60))
    frame_history: deque    = field(default_factory=lambda: deque(maxlen=30))  # (frame_idx, bbox)

    # Stationary tracking (for parking)
    stationary_since: Optional[float] = None   # wall-clock time
    stationary_frames: int = 0

    # Violation counting
    violation_frame_counts: dict = field(default_factory=dict)  # {vtype: count}
    violation_best_frame: dict   = field(default_factory=dict)  # {vtype: (conf, frame, idx)}

    # State
    state: TrackState = TrackState.ACTIVE
    fired_violations: set = field(default_factory=set)

    first_seen: float = field(default_factory=time.time)
    last_seen:  float = field(default_factory=time.time)

    def update(self, cx: float, cy: float, frame_idx: int, bbox: list, frame):
        self.position_history.append((cx, cy))
        self.frame_history.append((frame_idx, bbox, frame))
        self.last_seen = time.time()

    def velocity(self) -> float:
        return compute_velocity(self.position_history)

    def heading(self) -> Optional[float]:
        return compute_heading(self.position_history)

    def seconds_stationary(self) -> float:
        if self.stationary_since is None:
            return 0.0
        return time.time() - self.stationary_since

    def increment_violation(self, vtype: str, conf: float, frame, frame_idx: int):
        self.violation_frame_counts[vtype] = self.violation_frame_counts.get(vtype, 0) + 1
        best = self.violation_best_frame.get(vtype)
        if best is None or conf > best[0]:
            self.violation_best_frame[vtype] = (conf, frame, frame_idx)


# ─────────── Main Violation Engine ────────────────────────────────

class ViolationEngine:
    """
    Stateful engine that evaluates every frame's detections against
    calibrated rules and emits ViolationEvent objects.
    """

    def __init__(self, calibration: dict, signal_controller: Optional[SimulatedSignalController] = None):
        self.calibration = calibration
        self.signal = signal_controller or SimulatedSignalController()
        self.tracks: dict[int, VehicleTrack] = {}
        self.frame_count = 0

        # Parse calibration into pixel space lazily (set on first frame)
        self._w: Optional[int] = None
        self._h: Optional[int] = None
        self._stop_poly:         Optional[list] = None
        self._intersection_poly: Optional[list] = None
        self._no_parking_polys:  Optional[list] = None
        self._lanes:             Optional[list] = None

    def _init_rois(self, w: int, h: int):
        """Convert normalised calibration to pixel polygons (once per resolution)."""
        if self._w == w and self._h == h:
            return
        self._w, self._h = w, h
        self._stop_poly         = normalize_polygon(self.calibration["stop_line"], w, h)
        self._intersection_poly = normalize_polygon(self.calibration["intersection_box"], w, h)
        self._no_parking_polys  = [normalize_polygon(z, w, h) for z in self.calibration.get("no_parking_zones", [])]
        self._lanes = []
        for lane in self.calibration.get("lanes", []):
            self._lanes.append({
                "id":        lane["id"],
                "direction": lane["direction"],
                "poly":      normalize_polygon(lane["poly"], w, h),
            })

    def process_frame(
        self,
        detections: list,
        frame,
        frame_idx: int,
        frame_size: tuple,
    ) -> list[ViolationEvent]:
        """
        Evaluate current detections.
        Returns list of newly confirmed ViolationEvents.
        """
        h, w = frame_size[:2]
        self._init_rois(w, h)
        self.frame_count += 1
        signal_state = self.signal.state

        events = []
        active_ids = set()

        for det in detections:
            if not det.is_vehicle:
                continue

            tid = det.track_id
            cx, cy = det.center
            active_ids.add(tid)

            # Get or create track
            if tid not in self.tracks:
                self.tracks[tid] = VehicleTrack(track_id=tid, vehicle_class=det.label)
            track = self.tracks[tid]
            track.update(cx, cy, frame_idx, det.bbox, frame)

            # ── 1. STOP-LINE VIOLATION ─────────────────────────────
            if (signal_state in (SignalState.RED, SignalState.YELLOW)
                    and "STOP_LINE_VIOLATION" not in track.fired_violations
                    and self.signal.seconds_since_red > settings.SIGNAL_CLEARING_GRACE_SECONDS
                    and track.velocity() > 2.0
                    and point_in_polygon(cx, cy, self._stop_poly)):

                track.increment_violation("STOP_LINE_VIOLATION", det.conf, frame, frame_idx)
                count = track.violation_frame_counts.get("STOP_LINE_VIOLATION", 0)
                if count >= 2:
                    conf = min(0.65 + count * 0.03, 0.92)
                    best = track.violation_best_frame["STOP_LINE_VIOLATION"]
                    events.append(ViolationEvent(
                        track_id=tid, violation_type="STOP_LINE_VIOLATION",
                        confidence=conf, frame=best[1], frame_index=best[2],
                        vehicle_class=det.label, bbox=det.bbox,
                    ))
                    track.fired_violations.add("STOP_LINE_VIOLATION")

            # ── 2. RED-LIGHT VIOLATION ─────────────────────────────
            if (signal_state == SignalState.RED
                    and "RED_LIGHT_VIOLATION" not in track.fired_violations
                    and self.signal.seconds_since_red > settings.SIGNAL_CLEARING_GRACE_SECONDS
                    and track.velocity() > 3.0
                    and point_in_polygon(cx, cy, self._intersection_poly)):

                track.increment_violation("RED_LIGHT_VIOLATION", det.conf, frame, frame_idx)
                count = track.violation_frame_counts.get("RED_LIGHT_VIOLATION", 0)
                if count >= 3:
                    conf = min(0.70 + count * 0.03, 0.95)
                    best = track.violation_best_frame["RED_LIGHT_VIOLATION"]
                    events.append(ViolationEvent(
                        track_id=tid, violation_type="RED_LIGHT_VIOLATION",
                        confidence=conf, frame=best[1], frame_index=best[2],
                        vehicle_class=det.label, bbox=det.bbox,
                    ))
                    track.fired_violations.add("RED_LIGHT_VIOLATION")
                    # If both stop-line and red-light fired, keep only red-light (more severe)
                    track.fired_violations.add("STOP_LINE_VIOLATION")

            # ── 3. WRONG-SIDE DRIVING ─────────────────────────────
            if "WRONG_SIDE_DRIVING" not in track.fired_violations:
                heading = track.heading()
                if heading is not None and track.velocity() > 4.0:
                    for lane in (self._lanes or []):
                        if point_in_polygon(cx, cy, lane["poly"]):
                            diff = abs(angle_diff(heading, lane["direction"]))
                            if diff > 150:  # opposing direction
                                track.increment_violation("WRONG_SIDE_DRIVING", det.conf, frame, frame_idx)
                                count = track.violation_frame_counts.get("WRONG_SIDE_DRIVING", 0)
                                if count >= settings.WRONG_SIDE_MIN_FRAMES:
                                    conf = min(0.68 + count * 0.01, 0.92)
                                    best = track.violation_best_frame["WRONG_SIDE_DRIVING"]
                                    events.append(ViolationEvent(
                                        track_id=tid, violation_type="WRONG_SIDE_DRIVING",
                                        confidence=conf, frame=best[1], frame_index=best[2],
                                        vehicle_class=det.label, bbox=det.bbox,
                                        extra={"heading_angle": round(heading, 1), "expected_angle": lane["direction"]},
                                    ))
                                    track.fired_violations.add("WRONG_SIDE_DRIVING")
                            break   # only check first matching lane

            # ── 4. ILLEGAL PARKING ────────────────────────────────
            if "ILLEGAL_PARKING" not in track.fired_violations:
                velocity = track.velocity()
                in_no_park = any(
                    point_in_polygon(cx, cy, poly) for poly in (self._no_parking_polys or [])
                )
                if in_no_park:
                    if velocity < settings.PARKING_STATIONARY_VELOCITY_PX:
                        track.stationary_frames += 1
                        if track.stationary_since is None:
                            track.stationary_since = time.time()
                    else:
                        # Vehicle moved — reset parking timer
                        track.stationary_since = None
                        track.stationary_frames = 0

                    duration = track.seconds_stationary()
                    if duration >= settings.PARKING_VIOLATION_SECONDS:
                        conf = min(0.80 + duration / 1000, 0.97)
                        best_frame = track.frame_history[-1] if track.frame_history else None
                        events.append(ViolationEvent(
                            track_id=tid, violation_type="ILLEGAL_PARKING",
                            confidence=conf,
                            frame=best_frame[2] if best_frame else frame,
                            frame_index=best_frame[0] if best_frame else frame_idx,
                            vehicle_class=det.label, bbox=det.bbox,
                            extra={"parking_duration_seconds": round(duration, 1)},
                        ))
                        track.fired_violations.add("ILLEGAL_PARKING")

        # Clean up very old tracks that are no longer active
        stale = [tid for tid, t in self.tracks.items()
                 if tid not in active_ids and (time.time() - t.last_seen) > 30]
        for tid in stale:
            del self.tracks[tid]

        return events
