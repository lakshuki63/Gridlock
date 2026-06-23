# VisionEnforce — AI Traffic Violation Detection System

> Real-time traffic violation detection using **YOLOv8 ONNX** + **FastAPI** + interactive zone drawing on live CCTV feeds.

---

## ✨ Features

| Feature | Description |
|---|---|
|  **Congestion Radar** | Live map with real-time risk scores per camera |
|  **Interactive Zone Detection** | Draw custom polygon zones → ONNX runs violation detection |
|  **Review Queue** | Officer challan review & approval workflow |
|  **Analytics** | Historical violation charts & heatmaps |
|  **ONNX Inference** | YOLOv8n (CPU) — no GPU required |
|  **Real Video Support** | Vodra & Talaimari CCTV footage included |

---

##  Architecture

```
CCTV Video
    │
    ▼
YOLOv8n ONNX (CPU Inference)
    │
    ▼
Centroid Tracker + Shapely Polygon Check
    │
    ▼
Violation Engine → FastAPI REST + WebSocket
    │
    ▼
Browser Dashboard (HTML/JS/Canvas)
```

---

##  Project Structure

```
Gridlock/
├── main.py                    # FastAPI server + all API endpoints
├── config.py                  # Settings & configuration
├── requirements.txt           # Python dependencies
├── traffic_model.onnx         # YOLOv8n ONNX model (12MB)
│
├── pipeline/
│   ├── onnx_detector.py       # ONNX inference + centroid tracker
│   ├── processor.py           # Demo simulation engine
│   ├── violation_engine.py    # Violation state machine
│   ├── risk_engine.py         # Congestion risk scoring
│   ├── evidence_packager.py   # Evidence capture
│   └── plate_ocr.py           # License plate OCR
│
├── database/
│   ├── models.py              # SQLAlchemy schemas
│   └── crud.py                # Async DB operations
│
├── frontend/
│   ├── radar.html             #  Main map dashboard
│   ├── interactive.html       # ONNX zone detection
│   ├── review.html            # Officer review console
│   ├── analytics.html         # Charts & stats
│   └── monitor.html           # Live feed monitor
│
├── Vodra/                     # Real CCTV footage (Vodra junction)
└── Talaimari/                 # Real CCTV footage (Talaimari)
|__demo_traffic.mp4
|__intersection.mp4
|__parking.mp4
```

---

##  Quick Start

See **[SETUP.md](SETUP.md)** for full installation and run instructions.
https://drive.google.com/drive/folders/1aLRPnS59XJiNl5zhtVXxbai0D1r6z0K6?usp=drive_link 
download this folder -- contains demo videos .. copy the contain of this folder in the main folder as it is .

```bash
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```
Then open → **http://localhost:8000**

---

## Interactive Zone Detection (Key Feature)

1. Go to **http://localhost:8000/interactive**
2. Select a video feed (`parking.mp4` recommended)
3. Click **Load Frame** to display the first video frame
4. **Click on the canvas** to draw polygon points (No-Parking Zone)
5. Click **Start Detect** — YOLOv8n ONNX runs frame-by-frame
6. Vehicles staying inside the zone **> 15 frames** → logged as **ILLEGAL PARKING**

---

## Tech Stack

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy (async SQLite)
- **ML**: YOLOv8n via ONNX Runtime (CPU), OpenCV, Shapely
- **Frontend**: Vanilla HTML/JS, Tailwind CSS, Canvas API
- **Tracking**: Custom centroid tracker with NMS

---

*Built for the ASTraM Smart-City Challenge — VisionEnforce Team.*
