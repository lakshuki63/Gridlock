"""
VisionEnforce — YOLOv8 Detection + ByteTrack Wrapper
"""

import cv2
import numpy as np
import torch
import functools

# Monkeypatch torch.load to default weights_only=False for PyTorch 2.6+ compatibility with YOLOv8 checkpoints
original_load = torch.load
@functools.wraps(original_load)
def patched_load(f, *args, **kwargs):
    if 'weights_only' not in kwargs:
        kwargs['weights_only'] = False
    return original_load(f, *args, **kwargs)
torch.load = patched_load

from pathlib import Path
from ultralytics import YOLO
from config import settings
import logging
import base64
import requests
import re
import json

class NvidiaNIMClient:
    def __init__(self, api_key: str = "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"):
        self.api_key = api_key
        self.url = "https://integrate.api.nvidia.com/v1/chat/completions"
        self.model = "nvidia/llama-3.1-nemotron-nano-vl-8b-v1"

    def detect_vehicles(self, frame: np.ndarray) -> list[dict]:
        """
        Sends frame to NVIDIA NIM API and returns list of objects with normalized coordinates.
        """
        if not self.api_key:
            return []
        
        try:
            # Resize frame to make the payload smaller and faster to upload/process
            h, w = frame.shape[:2]
            target_w = 640
            target_h = int(h * (target_w / w))
            resized_frame = cv2.resize(frame, (target_w, target_h))

            # Encode frame to base64
            _, buffer = cv2.imencode('.jpg', resized_frame)
            base64_image = base64.b64encode(buffer).decode('utf-8')

            prompt = (
                "Identify vehicles (car, motorcycle, bus, truck) in the image. "
                "For each, output the bounding box in normalized [ymin, xmin, ymax, xmax] format where coordinates are 0-1000. "
                "Format your response strictly as JSON list of dicts like: "
                "[{\"class\": \"car\", \"box\": [ymin, xmin, ymax, xmax]}]. "
                "Output ONLY the raw JSON list, no markdown wrapper, no explanations."
            )

            payload = {
                "model": self.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                            }
                        ]
                    }
                ],
                "max_tokens": 512,
                "temperature": 0.1
            }

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            response = requests.post(self.url, headers=headers, json=payload, timeout=6.0)
            if response.status_code != 200:
                logger.error(f"NVIDIA NIM API error: {response.status_code} - {response.text}")
                return []

            text = response.json()["choices"][0]["message"]["content"].strip()
            
            # Clean markdown code block wraps if any
            cleaned = text
            if cleaned.startswith("```"):
                cleaned = re.sub(r"^```(?:json)?\n", "", cleaned)
                cleaned = re.sub(r"\n```$", "", cleaned)
            
            # Parse json
            try:
                detections = json.loads(cleaned)
                return detections
            except Exception as je:
                logger.error(f"Failed to parse NVIDIA NIM JSON: {cleaned}. Error: {je}")
                return []
        except Exception as e:
            logger.error(f"NVIDIA NIM detection failed: {e}")
            return []

logger = logging.getLogger(__name__)

# ─────────── COCO class IDs of interest ───────────────────────────
# YOLOv8 COCO: 0=person, 1=bicycle, 2=car, 3=motorcycle,
#              5=bus, 7=truck, 9=traffic light

VEHICLE_CLASS_IDS  = {2, 3, 5, 7}   # car, motorcycle, bus, truck
PERSON_CLASS_ID    = 0
TRAFFIC_LIGHT_ID   = 9
BICYCLE_CLASS_ID   = 1

COCO_TO_LABEL = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
    9: "traffic_light",
}


class DetectionResult:
    """Single tracked detection result."""

    __slots__ = ["track_id", "class_id", "label", "conf", "bbox", "center"]

    def __init__(self, track_id: int, class_id: int, conf: float, bbox: list):
        self.track_id = track_id
        self.class_id = class_id
        self.label    = COCO_TO_LABEL.get(class_id, f"cls{class_id}")
        self.conf     = float(conf)
        self.bbox     = bbox          # [x1, y1, x2, y2] in pixels
        cx = (bbox[0] + bbox[2]) / 2
        cy = (bbox[1] + bbox[3]) / 2
        self.center   = (cx, cy)

    @property
    def is_vehicle(self) -> bool:
        return self.class_id in VEHICLE_CLASS_IDS or self.class_id == BICYCLE_CLASS_ID

    @property
    def is_person(self) -> bool:
        return self.class_id == PERSON_CLASS_ID

    @property
    def is_traffic_light(self) -> bool:
        return self.class_id == TRAFFIC_LIGHT_ID

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]

    def __repr__(self):
        return (f"<Det id={self.track_id} cls={self.label} "
                f"conf={self.conf:.2f} bbox={[int(x) for x in self.bbox]}>")


class VisionEnforceDetector:
    """
    Wraps YOLOv8 + ByteTrack for vehicle and person tracking.
    All inference is performed on a single model instance.
    """

    def __init__(self):
        device = self._resolve_device()
        model_name = settings.DETECTION_MODEL

        # Auto-download if not in models dir
        model_path = settings.MODELS_DIR / model_name
        if not model_path.exists():
            logger.info(f"Model not found locally, downloading {model_name}…")
            model_path = model_name   # ultralytics will download to cache

        self.model = YOLO(str(model_path))
        self.model.to(device)
        self.device = device
        logger.info(f"Detector loaded: {model_name} on {device}")
        
        # Instantiate NVIDIA NIM Client
        self.nim_client = NvidiaNIMClient()

        # Warm-up pass
        dummy = np.zeros((640, 640, 3), dtype=np.uint8)
        self.model.track(dummy, persist=True, verbose=False, conf=0.01, imgsz=640)
        logger.info("Detector warm-up done.")

    @staticmethod
    def _resolve_device() -> str:
        if settings.DEVICE == "auto":
            return "cuda:0" if torch.cuda.is_available() else "cpu"
        return settings.DEVICE

    def process_frame(self, frame: np.ndarray) -> list[DetectionResult]:
        """
        Run tracking on a single BGR frame.
        Returns list of DetectionResult for all tracked objects.
        """
        results = self.model.track(
            frame,
            persist=True,
            verbose=False,
            conf=settings.DETECTION_CONFIDENCE,
            classes=list(VEHICLE_CLASS_IDS | {PERSON_CLASS_ID, BICYCLE_CLASS_ID}),
            tracker=settings.TRACKER_CONFIG,
            imgsz=640,
        )

        detections = []
        if results and results[0].boxes is not None:
            boxes = results[0].boxes
            for i in range(len(boxes)):
                track_id = int(boxes.id[i]) if boxes.id is not None else -1
                class_id = int(boxes.cls[i])
                conf     = float(boxes.conf[i])
                xyxy     = boxes.xyxy[i].tolist()
                detections.append(DetectionResult(track_id, class_id, conf, xyxy))

        return detections

    def annotate_frame(
        self,
        frame: np.ndarray,
        detections: list[DetectionResult],
        violations: list = None,
        nvidia_detections: list = None,
        nvidia_verified_time: str = None,
    ) -> np.ndarray:
        """Draw bounding boxes, track IDs, and violation highlights on the frame."""
        annotated = frame.copy()
        h, w = frame.shape[:2]
        violation_ids = {v.track_id for v in violations} if violations else set()

        for det in detections:
            x1, y1, x2, y2 = [int(c) for c in det.bbox]
            is_violator = det.track_id in violation_ids

            color = (0, 200, 100) if not is_violator else (0, 0, 255)
            thickness = 2 if not is_violator else 3

            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, thickness)

            label = f"#{det.track_id} {det.label} {det.conf:.0%}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            cv2.rectangle(annotated, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
            cv2.putText(annotated, label, (x1 + 2, y1 - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

            if is_violator:
                # Flashing red border around violating vehicle
                cv2.rectangle(annotated, (x1 - 3, y1 - 3), (x2 + 3, y2 + 3), (0, 0, 255), 2)

        # Draw NVIDIA NIM Detections if available
        if nvidia_detections:
            for det in nvidia_detections:
                box = det.get("box", [])
                if len(box) == 4:
                    ymin, xmin, ymax, xmax = box
                    # Convert from 0-1000 scale to pixel scale
                    x1 = int(xmin / 1000.0 * w)
                    y1 = int(ymin / 1000.0 * h)
                    x2 = int(xmax / 1000.0 * w)
                    y2 = int(ymax / 1000.0 * h)
                    cls = det.get("class", "vehicle")

                    # Draw vibrant magenta bounding box
                    cv2.rectangle(annotated, (x1, y1), (x2, y2), (255, 0, 255), 2)
                    
                    label = f"NVIDIA: {cls}"
                    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
                    cv2.rectangle(annotated, (x1, y2), (x1 + tw + 4, y2 + th + 6), (255, 0, 255), -1)
                    cv2.putText(annotated, label, (x1 + 2, y2 + th + 2),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)

        # Draw status bar overlay
        cv2.rectangle(annotated, (0, 0), (w, 32), (30, 30, 30), -1)
        status_text = "NVIDIA NIM Cloud VLM: ACTIVE | Model: llama-3.1-nemotron-nano-vl-8b-v1"
        if nvidia_verified_time:
            status_text += f" | Verified: {nvidia_verified_time}"
            if nvidia_detections is not None:
                status_text += f" ({len(nvidia_detections)} vehicles)"
        else:
            status_text += " | Verification: Pending..."
        
        cv2.putText(annotated, status_text, (10, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
        
        # Draw legend in the corner of the status bar
        # Green: YOLOv8, Magenta: NVIDIA NIM
        legend_yolo = "YOLOv8 (Edge 30FPS)"
        legend_nim = "NVIDIA NIM (Cloud)"
        (y_w, _), _ = cv2.getTextSize(legend_yolo, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
        (n_w, _), _ = cv2.getTextSize(legend_nim, cv2.FONT_HERSHEY_SIMPLEX, 0.4, 1)
        
        # Draw YOLO legend
        cv2.rectangle(annotated, (w - y_w - n_w - 30, 8), (w - y_w - n_w - 20, 18), (0, 200, 100), -1)
        cv2.putText(annotated, legend_yolo, (w - y_w - n_w - 15, 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1, cv2.LINE_AA)
        
        # Draw NIM legend
        cv2.rectangle(annotated, (w - n_w - 15, 8), (w - n_w - 5, 18), (255, 0, 255), -1)
        cv2.putText(annotated, legend_nim, (w - n_w, 18),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1, cv2.LINE_AA)

        return annotated
