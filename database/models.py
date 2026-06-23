"""
VisionEnforce — SQLAlchemy Database Models
"""

from datetime import datetime
from sqlalchemy import (
    Column, String, Float, Integer, Boolean,
    DateTime, Text, JSON, ForeignKey, Enum as SAEnum
)
from sqlalchemy.orm import DeclarativeBase, relationship
import enum


class Base(DeclarativeBase):
    pass


# ─────────── Enums ────────────────────────────────────────────────

class ViolationType(str, enum.Enum):
    RED_LIGHT_VIOLATION = "RED_LIGHT_VIOLATION"
    STOP_LINE_VIOLATION = "STOP_LINE_VIOLATION"
    WRONG_SIDE_DRIVING  = "WRONG_SIDE_DRIVING"
    ILLEGAL_PARKING     = "ILLEGAL_PARKING"
    HELMET_NON_COMPLIANCE    = "HELMET_NON_COMPLIANCE"
    SEATBELT_NON_COMPLIANCE  = "SEATBELT_NON_COMPLIANCE"
    TRIPLE_RIDING            = "TRIPLE_RIDING"


class ReviewStatus(str, enum.Enum):
    AUTO_PROCESSED  = "AUTO_PROCESSED"
    PENDING_HUMAN   = "PENDING_HUMAN"
    APPROVED        = "APPROVED"
    REJECTED        = "REJECTED"
    ESCALATED       = "ESCALATED"


class Severity(str, enum.Enum):
    CRITICAL = "CRITICAL"
    HIGH     = "HIGH"
    MEDIUM   = "MEDIUM"
    LOW      = "LOW"


VIOLATION_SEVERITY = {
    ViolationType.WRONG_SIDE_DRIVING:       Severity.CRITICAL,
    ViolationType.RED_LIGHT_VIOLATION:      Severity.CRITICAL,
    ViolationType.TRIPLE_RIDING:            Severity.HIGH,
    ViolationType.HELMET_NON_COMPLIANCE:    Severity.HIGH,
    ViolationType.SEATBELT_NON_COMPLIANCE:  Severity.MEDIUM,
    ViolationType.STOP_LINE_VIOLATION:      Severity.MEDIUM,
    ViolationType.ILLEGAL_PARKING:          Severity.MEDIUM,
}

VIOLATION_LABELS = {
    ViolationType.RED_LIGHT_VIOLATION:      "Red Light Violation",
    ViolationType.STOP_LINE_VIOLATION:      "Stop Line Violation",
    ViolationType.WRONG_SIDE_DRIVING:       "Wrong Side Driving",
    ViolationType.ILLEGAL_PARKING:          "Illegal Parking",
    ViolationType.HELMET_NON_COMPLIANCE:    "Helmet Non-Compliance",
    ViolationType.SEATBELT_NON_COMPLIANCE:  "Seatbelt Non-Compliance",
    ViolationType.TRIPLE_RIDING:            "Triple Riding",
}


# ─────────── Camera ───────────────────────────────────────────────

class Camera(Base):
    __tablename__ = "cameras"

    id          = Column(String, primary_key=True)     # "CAM-KR-01"
    name        = Column(String, nullable=False)
    location_lat = Column(Float)
    location_lon = Column(Float)
    landmark    = Column(String)
    stream_url  = Column(String)                       # RTSP or file path
    is_active   = Column(Boolean, default=True)
    last_heartbeat = Column(DateTime)
    calibration = Column(JSON)                         # stored calibration dict

    violations  = relationship("Violation", back_populates="camera_rel")


# ─────────── Violation ────────────────────────────────────────────

class Violation(Base):
    __tablename__ = "violations"

    # Identity
    id              = Column(String, primary_key=True)   # EVD-2026-BLR-XXXXXXXX
    camera_id       = Column(String, ForeignKey("cameras.id"), nullable=False)
    track_id        = Column(String)                     # ByteTrack track ID

    # Violation info
    violation_type  = Column(SAEnum(ViolationType), nullable=False)
    severity        = Column(SAEnum(Severity), nullable=False)
    vehicle_class   = Column(String)                     # motorcycle, car, truck…
    vehicle_color   = Column(String)

    # License plate
    license_plate       = Column(String)                 # normalized text
    plate_raw_reads     = Column(JSON)                   # list of OCR attempts
    plate_confidence    = Column(Float, default=0.0)

    # Confidence scores
    detection_confidence    = Column(Float, default=0.0)
    violation_confidence    = Column(Float, default=0.0)
    frames_with_violation   = Column(Integer, default=0)

    # Timestamps
    timestamp_utc   = Column(DateTime, default=datetime.utcnow, index=True)
    created_at      = Column(DateTime, default=datetime.utcnow)

    # Evidence files
    annotated_frame_path    = Column(String)
    annotated_clip_path     = Column(String)
    provenance_hash         = Column(String)

    # Review
    review_status       = Column(SAEnum(ReviewStatus), default=ReviewStatus.PENDING_HUMAN, index=True)
    assigned_officer_id = Column(String)
    officer_notes       = Column(Text)
    reviewed_at         = Column(DateTime)
    challan_issued      = Column(Boolean, default=False)

    # Extra metadata
    extra_metadata  = Column(JSON)          # violation-specific data (e.g., parking_duration)
    astrам_incident_id = Column(String)

    camera_rel = relationship("Camera", back_populates="violations")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "camera_id": self.camera_id,
            "camera_name": self.camera_rel.name if self.camera_rel else self.camera_id,
            "violation_type": self.violation_type.value if self.violation_type else None,
            "violation_label": VIOLATION_LABELS.get(self.violation_type, "Unknown"),
            "severity": self.severity.value if self.severity else None,
            "vehicle_class": self.vehicle_class,
            "vehicle_color": self.vehicle_color,
            "license_plate": self.license_plate,
            "plate_confidence": self.plate_confidence,
            "detection_confidence": self.detection_confidence,
            "violation_confidence": self.violation_confidence,
            "frames_with_violation": self.frames_with_violation,
            "timestamp": self.timestamp_utc.isoformat() if self.timestamp_utc else None,
            "frame_url": f"/evidence/frames/{self.id}.jpg" if self.annotated_frame_path else "/evidence/frames/demo_frame.jpg",
            "clip_url": f"/evidence/clips/{self.id}.mp4" if self.annotated_clip_path else "/evidence/clips/demo_clip.mp4",
            "review_status": self.review_status.value if self.review_status else None,
            "assigned_officer_id": self.assigned_officer_id,
            "officer_notes": self.officer_notes,
            "challan_issued": self.challan_issued,
            "extra_metadata": self.extra_metadata or {},
            "location": {
                "lat": self.camera_rel.location_lat if self.camera_rel else None,
                "lon": self.camera_rel.location_lon if self.camera_rel else None,
                "landmark": self.camera_rel.landmark if self.camera_rel else None,
            },
        }


# ─────────── Officer ──────────────────────────────────────────────

class Officer(Base):
    __tablename__ = "officers"

    id          = Column(String, primary_key=True)   # "OFF-2847"
    name        = Column(String)
    badge_no    = Column(String)
    is_active   = Column(Boolean, default=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
