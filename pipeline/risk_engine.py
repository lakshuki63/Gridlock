"""
VisionEnforce Radar — Transparent Congestion Risk Engine

Computes a per-camera congestion-risk score from recent violations.
Design philosophy: every factor is named and traceable to a real traffic insight.
No black-box model — the explanation IS the model.
"""

from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_
from database.models import Violation, Camera, ViolationType
import logging

logger = logging.getLogger(__name__)

# ─────────── Score thresholds ──────────────────────────────────────
LEVEL_CRITICAL = 70
LEVEL_HIGH     = 45
LEVEL_MEDIUM   = 20

# ─────────── Time-of-day peak multiplier ──────────────────────────
# IST peak hours: morning rush 7-10, evening rush 17-21
def _peak_multiplier(hour_ist: int) -> float:
    if 7 <= hour_ist <= 10:
        return 1.35
    if 17 <= hour_ist <= 21:
        return 1.30
    if 11 <= hour_ist <= 16:
        return 0.90   # midday, calmer
    return 0.75       # night hours

def _day_multiplier(weekday: int) -> float:
    """weekday 0=Monday … 6=Sunday"""
    if weekday in (5, 6):  # Saturday, Sunday
        return 0.80
    return 1.00

def _score_to_level(score: float) -> str:
    if score >= LEVEL_CRITICAL:
        return "CRITICAL"
    if score >= LEVEL_HIGH:
        return "HIGH"
    if score >= LEVEL_MEDIUM:
        return "MEDIUM"
    return "LOW"


async def compute_risk_scores(
    session: AsyncSession,
    window_minutes: int = 30,
    parking_threshold_minutes: float = 5.0,
) -> list[dict]:
    """
    Compute a risk score for every camera from violations in the last `window_minutes`.
    Returns a list of risk dicts, one per camera.
    """
    cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)

    # Fetch all cameras
    cameras = (await session.execute(select(Camera))).scalars().all()

    # Fetch all violations in window (with camera join)
    violations_result = await session.execute(
        select(Violation).where(Violation.timestamp_utc >= cutoff)
    )
    violations = violations_result.scalars().all()

    # Group violations by camera
    by_camera: dict[str, list[Violation]] = {cam.id: [] for cam in cameras}
    for v in violations:
        if v.camera_id in by_camera:
            by_camera[v.camera_id].append(v)

    # IST is UTC+5:30 → add 330 minutes
    now_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
    hour_ist = now_ist.hour
    weekday  = now_ist.weekday()

    peak_mult = _peak_multiplier(hour_ist)
    day_mult  = _day_multiplier(weekday)

    results = []

    for cam in cameras:
        cam_violations = by_camera.get(cam.id, [])

        base_score = 0.0
        factors = []           # human-readable contributing factors
        type_set = set()       # distinct violation types seen

        parking_count   = 0
        parking_long    = 0    # parked > threshold
        wrong_side      = 0
        signal_jump     = 0
        total_density   = 0
        density_samples = 0

        for v in cam_violations:
            vtype = v.violation_type
            meta  = v.extra_metadata or {}

            if vtype == ViolationType.ILLEGAL_PARKING:
                parking_count += 1
                type_set.add("parking")
                dur_sec = meta.get("parking_duration_seconds", 0)
                dur_min = dur_sec / 60.0
                if dur_min >= parking_threshold_minutes:
                    parking_long += 1
                    weight = min(dur_min / parking_threshold_minutes, 4.0)
                    base_score += 15.0 * weight
                else:
                    base_score += 3.0   # noise-level parking, still register

            elif vtype == ViolationType.WRONG_SIDE_DRIVING:
                wrong_side += 1
                type_set.add("wrong_side")
                base_score += 20.0

            elif vtype in (ViolationType.RED_LIGHT_VIOLATION, ViolationType.STOP_LINE_VIOLATION):
                signal_jump += 1
                type_set.add("signal")
                base_score += 10.0

            else:
                # Other violation types (helmet etc.) contribute a small baseline
                type_set.add("other")
                base_score += 2.0

            # Vehicle density from detection metadata
            density = meta.get("vehicle_density", 0)
            if density > 0:
                total_density += density
                density_samples += 1

        # Vehicle density bonus
        avg_density = (total_density / density_samples) if density_samples > 0 else 0
        if avg_density > 6:
            density_bonus = min(20.0, 4.0 * (avg_density - 6))
            base_score += density_bonus

        # Type diversity compound bonus
        n_types = len(type_set)
        if n_types >= 3:
            base_score *= 1.4
            factors.append("multiple violation types clustering")
        elif n_types >= 2:
            base_score *= 1.2
            factors.append("mixed violation pattern")

        # Time-of-day and day-of-week multipliers
        base_score *= peak_mult
        base_score *= day_mult

        final_score = round(min(100.0, base_score), 1)
        level = _score_to_level(final_score)

        # ── Build human-readable explanation ───────────────────────
        parts = []
        if parking_long > 0:
            parts.append(
                f"{parking_long} vehicle{'s' if parking_long > 1 else ''} "
                f"illegally parked >{parking_threshold_minutes:.0f} min"
            )
        elif parking_count > 0:
            parts.append(f"{parking_count} short-stop parking event{'s' if parking_count > 1 else ''}")

        if wrong_side > 0:
            parts.append(f"{wrong_side} wrong-side driving incident{'s' if wrong_side > 1 else ''}")

        if signal_jump > 0:
            parts.append(f"{signal_jump} signal violation{'s' if signal_jump > 1 else ''}")

        if avg_density > 6:
            parts.append(f"elevated vehicle density ({avg_density:.0f} vehicles/frame)")

        if peak_mult > 1.0:
            tod_label = "morning" if hour_ist <= 12 else "evening"
            parts.append(f"{tod_label} peak-hour multiplier active")

        if not parts:
            explanation = "No significant violations detected in the last 30 minutes."
        else:
            explanation = f"{level.capitalize()} risk: " + "; ".join(parts) + f" near {cam.landmark or cam.name}."

        # Recommended action
        if level == "CRITICAL":
            action = "Dispatch traffic personnel immediately. Consider temporary lane closure advisory."
        elif level == "HIGH":
            action = "Send patrol unit to clear illegally parked vehicles within 5 minutes."
        elif level == "MEDIUM":
            action = "Monitor closely. Alert nearest patrol unit to increase visibility."
        else:
            action = "Normal operations. Continue routine monitoring."

        results.append({
            "camera_id":   cam.id,
            "camera_name": cam.name,
            "landmark":    cam.landmark,
            "lat":         cam.location_lat,
            "lon":         cam.location_lon,
            "is_active":   cam.is_active,
            "score":       final_score,
            "level":       level,
            "explanation": explanation,
            "action":      action,
            "factors": {
                "parking_total":         parking_count,
                "parking_long_duration": parking_long,
                "wrong_side":            wrong_side,
                "signal_violations":     signal_jump,
                "avg_vehicle_density":   round(avg_density, 1),
                "peak_hour_multiplier":  round(peak_mult, 2),
                "day_multiplier":        round(day_mult, 2),
                "type_diversity":        n_types,
                "total_violations":      len(cam_violations),
            },
            "window_minutes": window_minutes,
            "computed_at":    datetime.utcnow().isoformat(),
        })

    # Sort by score descending so highest-risk comes first
    results.sort(key=lambda x: x["score"], reverse=True)
    return results
