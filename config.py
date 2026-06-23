"""
VisionEnforce — Central Configuration
All tunable parameters in one place.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path
import json

BASE_DIR = Path(__file__).parent


class Settings(BaseSettings):
    # ─────────────────────────── App ──────────────────────────────
    APP_NAME: str = "VisionEnforce"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    # ─────────────────────────── Paths ────────────────────────────
    EVIDENCE_DIR: Path = BASE_DIR / "evidence"
    FRAMES_DIR: Path = BASE_DIR / "evidence" / "frames"
    CLIPS_DIR: Path = BASE_DIR / "evidence" / "clips"
    MODELS_DIR: Path = BASE_DIR / "models"
    DEMO_DIR: Path = BASE_DIR / "demo"
    DB_URL: str = f"sqlite+aiosqlite:///{BASE_DIR}/visionenforce.db"

    # ─────────────────────────── ML Models ────────────────────────
    DETECTION_MODEL: str = "yolov8n.pt"          # auto-downloads from Ultralytics
    PLATE_MODEL: str = "yolov8n.pt"               # same model, filter plate class
    DETECTION_CONFIDENCE: float = 0.35
    PLATE_CONFIDENCE: float = 0.40
    DEVICE: str = "auto"                          # "auto" | "cpu" | "cuda:0"

    # ─────────────────────────── Tracking ─────────────────────────
    TRACKER_CONFIG: str = "bytetrack.yaml"
    TRACK_MAX_AGE: int = 30                        # frames before track is dropped
    TRACK_MIN_HITS: int = 3                        # frames before track is confirmed

    # ─────────────────────────── Violation Thresholds ─────────────
    # Confidence thresholds per violation type
    CONF_RED_LIGHT: float = 0.72
    CONF_STOP_LINE: float = 0.68
    CONF_WRONG_SIDE: float = 0.65
    CONF_ILLEGAL_PARKING: float = 0.80

    # Auto-process if violation_confidence > this; else route to human review
    AUTO_PROCESS_THRESHOLD: float = 0.88
    HUMAN_REVIEW_THRESHOLD: float = 0.55          # below this → discard

    # Parking: seconds stationary before violation
    PARKING_VIOLATION_SECONDS: int = 120          # 2 minutes for demo (real = 300s)
    PARKING_STATIONARY_VELOCITY_PX: float = 3.0  # pixels/frame

    # Wrong-side driving: minimum frames for confirmation
    WRONG_SIDE_MIN_FRAMES: int = 10

    # Red-light / stop-line: seconds after signal change to tolerate clearing
    SIGNAL_CLEARING_GRACE_SECONDS: float = 2.0

    # ─────────────────────────── Signal Simulation ────────────────
    # For demo: simulated signal cycles (seconds)
    SIGNAL_GREEN_DURATION: int = 25
    SIGNAL_YELLOW_DURATION: int = 5
    SIGNAL_RED_DURATION: int = 30

    # ─────────────────────────── Evidence ─────────────────────────
    EVIDENCE_CLIP_SECONDS_BEFORE: float = 3.0
    EVIDENCE_CLIP_SECONDS_AFTER: float = 2.0
    EVIDENCE_JPEG_QUALITY: int = 92

    # ─────────────────────────── OCR ──────────────────────────────
    OCR_LANGUAGE: list = ["en"]
    OCR_MULTI_FRAME_ATTEMPTS: int = 5             # best of N crops for OCR
    PLATE_MIN_CONFIDENCE: float = 0.50

    # ─────────────────────────── API ──────────────────────────────
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    CORS_ORIGINS: list = ["*"]

    # ─────────────────────────── ASTraM Integration ───────────────
    ASTRАМ_ENABLED: bool = False
    ASTRАМ_BASE_URL: str = "http://astrам-api.example.com/v1"
    ASTRАМ_API_KEY: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

# ─────────── Ensure directories exist ─────────────────────────────
for d in [settings.EVIDENCE_DIR, settings.FRAMES_DIR, settings.CLIPS_DIR,
          settings.MODELS_DIR, settings.DEMO_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# ─────────── Default Camera Calibration ───────────────────────────
# These ROIs are in NORMALIZED coordinates [0..1] so they work
# at any video resolution.  Load from calibration/{camera_id}.json
# if present; otherwise use defaults.

DEFAULT_CALIBRATION = {
    "camera_id": "CAM-DEMO-01",
    "camera_name": "Demo Intersection - KR Circle",
    "location": {"lat": 12.9716, "lon": 77.5946, "landmark": "KR Circle"},
    "resolution": [1280, 720],

    # Normalized polygon [[x,y], ...]  (values 0..1)
    "stop_line": [[0.2, 0.55], [0.8, 0.55], [0.8, 0.58], [0.2, 0.58]],
    "intersection_box": [[0.15, 0.35], [0.85, 0.35], [0.85, 0.58], [0.15, 0.58]],

    # No-parking zones (list of polygons)
    "no_parking_zones": [
        [[0.0, 0.65], [0.20, 0.65], [0.20, 1.0], [0.0, 1.0]],
        [[0.80, 0.65], [1.0, 0.65], [1.0, 1.0], [0.80, 1.0]]
    ],

    # Expected travel direction per lane (degrees, 0=right, 90=down, 180=left, 270=up)
    "lanes": [
        {"id": 1, "poly": [[0.0, 0.4], [0.5, 0.4], [0.5, 0.9], [0.0, 0.9]], "direction": 270},
        {"id": 2, "poly": [[0.5, 0.4], [1.0, 0.4], [1.0, 0.9], [0.5, 0.9]], "direction": 90}
    ]
}


def load_calibration(camera_id: str) -> dict:
    """Load camera-specific calibration or fall back to defaults."""
    cal_path = BASE_DIR / "calibration" / f"{camera_id}.json"
    if cal_path.exists():
        with open(cal_path) as f:
            return json.load(f)
    return DEFAULT_CALIBRATION.copy()
