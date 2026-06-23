"""
VisionEnforce — License Plate OCR Module

Detects license plate regions using YOLO (reusing main model)
then performs OCR with EasyOCR + Indian RTO normalization.
"""

import re
import cv2
import numpy as np
import logging
from collections import Counter
from typing import Optional
import easyocr

from config import settings

logger = logging.getLogger(__name__)

# ─────────── Indian RTO plate patterns ────────────────────────────
# Standard: KA05AB1234, DL3CBA4321
_RTO_PATTERN     = re.compile(r"^([A-Z]{2})(\d{1,2})([A-Z]{1,3})(\d{4})$")
# BH-series: 22BH0001AA
_BH_PATTERN      = re.compile(r"^(\d{2}BH\d{4}[A-Z]{2})$")
# Known Karnataka RTO codes
_KARNATAKA_CODES = {f"KA{i:02d}" for i in range(1, 71)}

# Common OCR misread corrections
_CHAR_FIXES = str.maketrans({
    "0": "O", "1": "I", "5": "S", "8": "B"   # applied only in letter positions
})
_DIGIT_FIXES = str.maketrans({
    "O": "0", "I": "1", "S": "5", "B": "8", "Z": "2", "Q": "0"
})


class PlateOCR:
    """
    Lightweight plate OCR using EasyOCR.
    Crops plate region from vehicle bounding box, enhances,
    then runs OCR with multi-frame aggregation.
    """

    _reader = None   # shared singleton

    @classmethod
    def _get_reader(cls) -> easyocr.Reader:
        if cls._reader is None:
            logger.info("Loading EasyOCR reader (first time may take ~30s)…")
            cls._reader = easyocr.Reader(
                settings.OCR_LANGUAGE,
                gpu=settings.DEVICE != "cpu",
                verbose=False,
            )
            logger.info("EasyOCR ready.")
        return cls._reader

    def __init__(self):
        self.reader = self._get_reader()

    # ─── Plate region extraction ───────────────────────────────────

    @staticmethod
    def crop_plate_region(frame: np.ndarray, vehicle_bbox: list) -> Optional[np.ndarray]:
        """
        Heuristic: license plate is in the lower-middle third of the vehicle bbox.
        Returns cropped plate region (None if bbox too small).
        """
        x1, y1, x2, y2 = [int(c) for c in vehicle_bbox]
        w = x2 - x1
        h = y2 - y1

        if w < 40 or h < 40:
            return None

        # Plate is roughly bottom 25% of vehicle height, center 70% width
        plate_y1 = y1 + int(h * 0.65)
        plate_y2 = y2
        plate_x1 = x1 + int(w * 0.15)
        plate_x2 = x2 - int(w * 0.15)

        crop = frame[plate_y1:plate_y2, plate_x1:plate_x2]
        if crop.size == 0:
            return None
        return crop

    @staticmethod
    def enhance_plate(crop: np.ndarray) -> np.ndarray:
        """Resize, denoise, and sharpen a plate crop for better OCR."""
        # Upscale to fixed height
        target_h = 64
        scale = target_h / crop.shape[0]
        resized = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

        # Convert to grayscale
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)

        # Denoise
        denoised = cv2.fastNlMeansDenoising(gray, h=10)

        # Adaptive threshold (helps with varied lighting)
        thresh = cv2.adaptiveThreshold(
            denoised, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )

        # Sharpen
        kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
        sharpened = cv2.filter2D(thresh, -1, kernel)

        # Return as BGR for consistency
        return cv2.cvtColor(sharpened, cv2.COLOR_GRAY2BGR)

    # ─── OCR ──────────────────────────────────────────────────────

    def read_plate(self, crop: np.ndarray) -> tuple[str, float]:
        """
        Run EasyOCR on a plate crop.
        Returns (text, confidence) or ("", 0.0) on failure.
        """
        if crop is None or crop.size == 0:
            return "", 0.0

        enhanced = self.enhance_plate(crop)

        results = self.reader.readtext(
            enhanced,
            allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
            detail=1,
            paragraph=False,
            width_ths=0.7,
        )

        if not results:
            return "", 0.0

        # Concatenate all text regions
        full_text = "".join(r[1].upper().strip() for r in results)
        avg_conf = sum(r[2] for r in results) / len(results) if results else 0.0

        return full_text, avg_conf

    # ─── Normalization ────────────────────────────────────────────

    @staticmethod
    def normalize_plate(raw: str) -> tuple[str, float]:
        """
        Normalize raw OCR text to Indian RTO plate format.
        Returns (normalized_text, extra_confidence_adjustment).
        """
        if not raw:
            return "", 0.0

        # Remove spaces, dashes, dots
        cleaned = re.sub(r"[\s\-\.]", "", raw.upper())

        # Try standard pattern
        if _RTO_PATTERN.match(cleaned):
            return cleaned, 0.05    # +5% confidence for valid format

        # Try BH pattern
        if _BH_PATTERN.match(cleaned):
            return cleaned, 0.05

        # Try fuzzy fix: apply digit/letter corrections by position
        if len(cleaned) >= 9:
            # State code: first 2 chars should be letters
            state = cleaned[:2].translate(_CHAR_FIXES)
            # RTO number: chars 2–4 should be digits
            rto_num = cleaned[2:4].translate(_DIGIT_FIXES)
            # Series: next 1–3 chars should be letters
            series = cleaned[4:-4].translate(_CHAR_FIXES) if len(cleaned) > 8 else ""
            # Number: last 4 should be digits
            num = cleaned[-4:].translate(_DIGIT_FIXES)

            candidate = state + rto_num + series + num
            if _RTO_PATTERN.match(candidate):
                return candidate, 0.02   # smaller bonus for fuzzy fixed

        # Return as-is but flag it
        return cleaned, -0.10   # penalty for non-standard format

    # ─── Multi-frame aggregation ──────────────────────────────────

    def aggregate_ocr(self, crops: list) -> dict:
        """
        Run OCR on multiple plate crops and aggregate results.
        Returns best result with confidence.
        """
        reads = []
        for crop in crops:
            if crop is None:
                continue
            raw_text, raw_conf = self.read_plate(crop)
            if not raw_text:
                continue
            norm_text, adj = self.normalize_plate(raw_text)
            if norm_text:
                reads.append({
                    "raw": raw_text,
                    "normalized": norm_text,
                    "confidence": max(0.0, raw_conf + adj),
                })

        if not reads:
            return {"text": None, "confidence": 0.0, "all_reads": []}

        # Majority vote on normalized text
        counter = Counter(r["normalized"] for r in reads)
        best_text, vote_count = counter.most_common(1)[0]

        # Average confidence for the winning text
        matching = [r["confidence"] for r in reads if r["normalized"] == best_text]
        avg_conf = sum(matching) / len(matching)

        # Agreement bonus: more votes → higher confidence
        agreement_bonus = min((vote_count - 1) * 0.04, 0.12)
        final_conf = min(avg_conf + agreement_bonus, 0.99)

        return {
            "text": best_text,
            "confidence": round(final_conf, 3),
            "all_reads": [r["normalized"] for r in reads],
            "vote_count": vote_count,
        }
