# ⚙️ SETUP — How to Run VisionEnforce

## Prerequisites

- Python **3.11+** (tested on 3.14)
- Git
- ~500MB free disk space
- https://drive.google.com/drive/folders/1aLRPnS59XJiNl5zhtVXxbai0D1r6z0K6?usp=drive_link 
download this folder -- contains demo videos .. copy the contain of this folder in the main folder as it is .

---

## 1. Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/Gridlock.git
cd Gridlock
```

---

## 2. Install Dependencies

```bash
pip install -r requirements.txt
```

**Core packages installed:**
- `fastapi`, `uvicorn` — web server
- `onnxruntime` — ONNX model inference (CPU)
- `opencv-python` — video processing
- `shapely` — polygon zone geometry
- `sqlalchemy`, `aiosqlite` — async database

> ⚠️ **Note:** `torch`, `ultralytics`, `easyocr` are in requirements but optional — only needed if you want to re-export the ONNX model. The `traffic_model.onnx` is already included.

---

## 3. Run the Server

```bash
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Expected output:
```
INFO:     Uvicorn running on http://0.0.0.0:8000
INFO:     Application startup complete.
```

---

## 4. Open the App

| Page | URL |
|---|---|
| 🗺️ Congestion Radar (Main) | http://localhost:8000/radar.html |
| 🎯 Interactive Zone Detection | http://localhost:8000/interactive |
| 📋 Officer Review Queue | http://localhost:8000/review.html |
| 📊 Analytics | http://localhost:8000/analytics.html |

---

## 5. Using Interactive Zone Detection

1. Navigate to **http://localhost:8000/interactive**
2. Select video: `parking.mp4` (best for demo)
3. Click **Load Frame** — first video frame appears on canvas
4. Click points on the canvas to draw a polygon (the no-parking zone)
5. Click **Start Detect**
6. Watch vehicles get detected and tracked in real-time
7. Violations appear in the log panel (vehicle in zone > 15 frames = ILLEGAL PARKING)

---

## 6. Git Push (First Time)

```bash
# Initialize (if not already a git repo)
git init
git add .
git commit -m "Initial commit - VisionEnforce AI Traffic System"

# Push to GitHub
git remote add origin https://github.com/YOUR_USERNAME/Gridlock.git
git branch -M main
git push -u origin main
```

> ⚠️ **Large files**: `parking.mp4`, `demo_traffic.mp4`, `intersection.mp4`, and `traffic_model.onnx` (12MB) will be pushed. If you hit GitHub's 100MB file limit, use [Git LFS](https://git-lfs.com):
> ```bash
> git lfs install
> git lfs track "*.mp4" "*.onnx"
> git add .gitattributes
> ```

---

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError` | Run `pip install -r requirements.txt` |
| Port 8000 busy | Change port: `--port 8080` |
| Video not loading | Ensure `.mp4` files are in project root |
| ONNX model missing | `traffic_model.onnx` must be in project root |
| Slow inference | Normal on CPU — YOLOv8n is lightweight, ~15-25 FPS |

---

## Environment (Tested On)

- Windows 11, Python 3.14
- No GPU required (CPU-only ONNX inference)
