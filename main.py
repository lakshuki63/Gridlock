"""
VisionEnforce Radar — FastAPI Backend Server
Provides REST API, WebSockets, and serves the static frontend + evidence.
"""

import asyncio
import logging
import time
from datetime import datetime, date
from typing import List, Optional, Any, Dict
from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, HTTPException, Query, BackgroundTasks
from fastapi.responses import StreamingResponse, FileResponse, Response
import cv2
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_

from config import settings
from database.crud import (
    init_db, get_db, AsyncSessionLocal, list_violations,
    update_violation_review, get_analytics_summary, get_cameras,
    get_hourly_timeseries, get_heatmap_data, get_camera_config, set_camera_config
)
from database.models import Violation, Camera, ReviewStatus
from pipeline.processor import processing_status, run_demo_simulation
from pipeline.risk_engine import compute_risk_scores

# ─────────── Logging Configuration ──────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─────────── FastAPI App ───────────────────────────────────────────
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Traffic Violation Detection and Evidence Management System",
)

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────── WebSocket Connection Manager ──────────────────────────
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket disconnected. Total active: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        if not self.active_connections:
            return
        logger.debug(f"Broadcasting message: {message.get('type')}")
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        
        for conn in disconnected:
            self.disconnect(conn)

manager = ConnectionManager()

# ─────────── Pydantic Models ───────────────────────────────────────
class ReviewRequest(BaseModel):
    action: str = Field(..., description="Action to perform: APPROVE, REJECT, ESCALATE")
    officer_id: str = Field(..., description="ID of the reviewing officer")
    notes: Optional[str] = Field("", description="Optional notes from the officer")
    license_plate: Optional[str] = Field(None, description="Optional corrected license plate text")

class ZoneConfigRequest(BaseModel):
    parking_threshold_minutes: float = Field(5.0, ge=1.0, le=30.0, description="Minutes parked before violation counts as long-duration")
    risk_window_minutes: int = Field(30, ge=5, le=120, description="Time window for risk score computation")
    no_parking_zones: Optional[List[Any]] = Field(None, description="List of polygon coordinate arrays")
    notes: Optional[str] = Field("", description="Config change notes")

# ─────────── Lifecycle Events ──────────────────────────────────────
@app.on_event("startup")
async def startup_event():
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized successfully.")

# ─────────── API Endpoints ─────────────────────────────────────────

# ── Risk Score Radar API ───────────────────────────────────────────

@app.get("/api/risk/scores")
async def api_risk_scores(
    db: AsyncSession = Depends(get_db),
    window_minutes: int = Query(30, ge=5, le=120),
    parking_threshold_minutes: float = Query(5.0, ge=1.0, le=30.0),
):
    """Compute real-time congestion-risk scores for all camera locations."""
    try:
        scores = await compute_risk_scores(
            session=db,
            window_minutes=window_minutes,
            parking_threshold_minutes=parking_threshold_minutes,
        )
        return {"scores": scores, "computed_at": datetime.utcnow().isoformat()}
    except Exception as e:
        logger.error(f"Risk score computation error: {e}")
        raise HTTPException(status_code=500, detail="Risk computation failed")


@app.get("/api/config/zones/{camera_id}")
async def api_get_zone_config(camera_id: str, db: AsyncSession = Depends(get_db)):
    """Get zone configuration for a specific camera."""
    try:
        config = await get_camera_config(db, camera_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Camera not found")
        return config
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Zone config get error: {e}")
        raise HTTPException(status_code=500, detail="Config fetch failed")


@app.post("/api/config/zones/{camera_id}")
async def api_set_zone_config(
    camera_id: str,
    req: ZoneConfigRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update zone threshold configuration for a camera (persisted to DB)."""
    try:
        updated = await set_camera_config(
            db,
            camera_id,
            parking_threshold_minutes=req.parking_threshold_minutes,
            risk_window_minutes=req.risk_window_minutes,
            no_parking_zones=req.no_parking_zones,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Camera not found")
        return {"status": "saved", "camera_id": camera_id, "config": updated}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Zone config set error: {e}")
        raise HTTPException(status_code=500, detail="Config save failed")


@app.get("/api/analytics/summary")
async def api_analytics_summary(db: AsyncSession = Depends(get_db), hours: int = Query(24, ge=1)):
    """Get summarized metrics for the last N hours."""
    try:
        summary = await get_analytics_summary(db, hours=hours)
        summary["fps"] = round(processing_status.fps, 1)
        summary["avg_latency_ms"] = 185
        return summary
    except Exception as e:
        logger.error(f"Error getting analytics: {e}")
        raise HTTPException(status_code=500, detail="Database error")

@app.get("/api/analytics/timeseries")
async def api_analytics_timeseries(db: AsyncSession = Depends(get_db), hours: int = Query(24, ge=1)):
    """Get hourly timeseries data for the last N hours."""
    try:
        return await get_hourly_timeseries(db, hours=hours)
    except Exception as e:
        logger.error(f"Error getting timeseries: {e}")
        raise HTTPException(status_code=500, detail="Database error")

@app.get("/api/analytics/heatmap")
async def api_analytics_heatmap(db: AsyncSession = Depends(get_db), hours: int = Query(24, ge=1)):
    """Get geolocated camera density heatmap data."""
    try:
        return await get_heatmap_data(db, hours=hours)
    except Exception as e:
        logger.error(f"Error getting heatmap: {e}")
        raise HTTPException(status_code=500, detail="Database error")

@app.get("/api/cameras")
async def api_get_cameras(db: AsyncSession = Depends(get_db)):
    """Get list of cameras and their today's violation statistics."""
    try:
        cameras_list = await get_cameras(db)
        
        # Format list to include status and violations_today for dashboard.js
        formatted_cameras = []
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        for cam in cameras_list:
            cam_id = cam["id"]
            # Count violations for this camera today
            viol_count = await db.scalar(
                select(func.count(Violation.id)).where(
                    and_(Violation.camera_id == cam_id, Violation.timestamp_utc >= today_start)
                )
            ) or 0
            
            # Map database schema properties to frontend fields
            is_active = cam["is_active"]
            status = "online" if is_active else "offline"
            if cam_id == "CAM-DEMO-03":  # Simulate warning status on one offline-ish camera
                status = "warning"
                
            formatted_cameras.append({
                "id": cam_id,
                "name": cam["name"],
                "status": status,
                "violations_today": viol_count,
                "landmark": cam["landmark"],
                "location": cam["location"],
                "last_heartbeat": cam["last_heartbeat"]
            })
            
        return formatted_cameras
    except Exception as e:
        logger.error(f"Error getting cameras: {e}")
        raise HTTPException(status_code=500, detail="Database error")

@app.get("/api/violations")
async def api_list_violations(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    violation_type: Optional[str] = None,
    camera_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Get paginated violation logs."""
    try:
        res = await list_violations(
            session=db,
            page=page,
            limit=limit,
            status=status,
            violation_type=violation_type,
            camera_id=camera_id
        )
        # Convert items to dictionaries
        res["items"] = [item.to_dict() for item in res["items"]]
        return res
    except Exception as e:
        logger.error(f"Error listing violations: {e}")
        raise HTTPException(status_code=500, detail="Database error")

@app.post("/api/violations/{violation_id}/review")
async def api_review_violation(
    violation_id: str,
    req: ReviewRequest,
    db: AsyncSession = Depends(get_db)
):
    """Approve or Reject a pending violation."""
    try:
        updated = await update_violation_review(
            session=db,
            violation_id=violation_id,
            action=req.action,
            officer_id=req.officer_id,
            notes=req.notes,
            license_plate=req.license_plate
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Violation not found")
        
        # Broadcast camera status update to force dashboard to refresh counters
        await manager.broadcast({
            "type": "camera_status",
            "data": {"camera_id": updated.camera_id}
        })
        
        # Also broadcast stats update
        summary = await get_analytics_summary(db, hours=24)
        summary["fps"] = round(processing_status.fps, 1)
        summary["avg_latency_ms"] = 185
        await manager.broadcast({
            "type": "stats_update",
            "data": summary
        })
        
        return updated.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reviewing violation: {e}")
        raise HTTPException(status_code=500, detail="Database error")

# ─────────── Simulation Control Endpoints ─────────────────────────

async def run_simulation_task():
    """Wrapper task to run simulation with independent DB session."""
    logger.info("Starting demo simulation task...")
    async with AsyncSessionLocal() as db:
        try:
            # Send initial progress state
            await manager.broadcast({
                "type": "processing_progress",
                "data": {"status": "processing", "processed": 0, "total": 30 * 25}
            })
            
            # Simulation function runs and broadcasts directly
            await run_demo_simulation(
                db_session=db,
                broadcast=manager.broadcast,
                n_events=35
            )
            
            # Send completed state
            await manager.broadcast({
                "type": "processing_progress",
                "data": {"status": "complete", "processed": 35 * 25, "total": 35 * 25}
            })
        except Exception as e:
            logger.error(f"Simulation task failed: {e}")
            processing_status.is_running = False
            processing_status.error = str(e)
            await manager.broadcast({
                "type": "processing_progress",
                "data": {"status": "idle"}
            })

@app.post("/api/demo/start")
async def start_demo_simulation(background_tasks: BackgroundTasks):
    """Start generation of mock violation streams in the background."""
    if processing_status.is_running:
        raise HTTPException(status_code=400, detail="Video processor or simulation is already running")
    
    background_tasks.add_task(run_simulation_task)
    return {"status": "started", "message": "Demo simulation running in background."}

@app.get("/api/demo/status")
async def get_demo_status():
    """Retrieve the current processing stats."""
    status_str = "processing" if processing_status.is_running else "idle"
    return {
        "status": status_str,
        "processed": processing_status.processed_frames,
        "total": processing_status.total_frames,
        "violations": processing_status.violations_found,
        "fps": round(processing_status.fps, 1),
        "video_path": processing_status.video_path,
        "error": processing_status.error
    }

# ─────────── Real ML Video Feed ────────────────────────────────────
import time
import concurrent.futures

_detector = None
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
_nim_future = None
_last_nim_query_time = 0.0
_nim_results = {}  # camera_id -> {"detections": list, "time": str}

def get_detector():
    global _detector
    if _detector is None:
        from pipeline.detector import VisionEnforceDetector
        _detector = VisionEnforceDetector()
    return _detector

def _run_nim_inference(detector, frame_copy, camera_id):
    try:
        detections = detector.nim_client.detect_vehicles(frame_copy)
        verified_time = datetime.now().strftime("%H:%M:%S")
        return {"camera_id": camera_id, "detections": detections, "time": verified_time}
    except Exception as e:
        logger.error(f"Background NIM inference error: {e}")
        return {"camera_id": camera_id, "detections": [], "time": None}

def video_stream_generator(camera_id: str):
    global _nim_future, _last_nim_query_time
    
    # Map camera_id to actual local videos
    CAMERA_VIDEO_PATHS = {
        "CAM-VODRA-NORTH": "d:/12345/Gridlock/Vodra/North.mp4",
        "CAM-VODRA-SOUTH": "d:/12345/Gridlock/Vodra/South.mp4",
        "CAM-VODRA-WEST": "d:/12345/Gridlock/Vodra/weast.mp4",
        "CAM-TALAIMARI-NE": "d:/12345/Gridlock/Talaimari/North-East.mp4",
        "CAM-TALAIMARI-NW": "d:/12345/Gridlock/Talaimari/North-Weast.mp4",
        "CAM-TALAIMARI-WEST": "d:/12345/Gridlock/Talaimari/Weast.mp4",
        "CAM-TALAIMARI-EAST": "d:/12345/Gridlock/Talaimari/east.mp4",
    }
    
    video_path = CAMERA_VIDEO_PATHS.get(camera_id, "d:/12345/Gridlock/demo_traffic.mp4")
    cap = cv2.VideoCapture(video_path)
    
    if not cap.isOpened():
        logger.error(f"Cannot open video file {video_path}")
        return

    detector = get_detector()
    
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            # Loop the video
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue
            
        try:
            current_time = time.time()
            cam_result = _nim_results.get(camera_id, {"detections": None, "time": None})
            
            # Check if background job finished
            if _nim_future is not None and _nim_future.done():
                try:
                    res = _nim_future.result()
                    if res and res.get("time"):
                        _nim_results[res["camera_id"]] = {
                            "detections": res["detections"],
                            "time": res["time"]
                        }
                except Exception as e:
                    logger.error(f"Error reading NIM future result: {e}")
                _nim_future = None
                
            # Submit new verification task if 4 seconds passed and executor is free
            if _nim_future is None and (current_time - _last_nim_query_time) > 4.0:
                frame_copy = frame.copy()
                _last_nim_query_time = current_time
                _nim_future = _executor.submit(_run_nim_inference, detector, frame_copy, camera_id)
            
            # Process frame with local YOLOv8
            detections = detector.process_frame(frame)
            
            # Annotate using local YOLO detections + latest background NIM verification results
            annotated_frame = detector.annotate_frame(
                frame, 
                detections, 
                nvidia_detections=cam_result["detections"],
                nvidia_verified_time=cam_result["time"]
            )
            
            # Encode frame as JPEG
            _, buffer = cv2.imencode('.jpg', annotated_frame)
            frame_bytes = buffer.tobytes()
            
            # Yield in multipart format
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
                   
            # Limit the generator to yield ~15-20 frames per second to reduce CPU usage
            time.sleep(0.05)
            
        except Exception as e:
            logger.error(f"Error during inference stream: {e}")
            break
            
    cap.release()

@app.get("/api/video_feed/{camera_id}")
async def video_feed(camera_id: str):
    """MJPEG stream of YOLOv8 inference on real traffic video."""
    return StreamingResponse(
        video_stream_generator(camera_id), 
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

# ─────────── Interactive ONNX Polygon Detection ───────────────────
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

class StartDetectionRequest(BaseModel):
    polygon: List[List[float]]
    video: str

_current_polygon = None
_current_video = "parking.mp4"
_onnx_detector = None

@app.get("/interactive")
async def get_interactive_page():
    return FileResponse("frontend/interactive.html")

@app.get("/video_frame")
async def get_video_frame(video: str = "parking.mp4"):
    video_path = f"d:/12345/Gridlock/{video}"
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise HTTPException(status_code=400, detail="Cannot open video file")
    
    ret, frame = cap.read()
    cap.release()
    if not ret:
        raise HTTPException(status_code=500, detail="Failed to read first frame")
        
    _, buffer = cv2.imencode('.jpg', frame)
    return Response(content=buffer.tobytes(), media_type="image/jpeg")

@app.post("/start_detection")
async def start_detection(req: StartDetectionRequest):
    global _current_polygon, _current_video, _onnx_detector
    _current_polygon = req.polygon
    _current_video = req.video
    
    from pipeline.onnx_detector import ONNXDetector
    _onnx_detector = ONNXDetector()
    return {"status": "started", "video": _current_video, "polygon": _current_polygon}

def onnx_video_stream_generator():
    global _current_polygon, _current_video, _onnx_detector
    video_path = f"d:/12345/Gridlock/{_current_video}"
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error(f"Cannot open video file {video_path}")
        return
        
    if _onnx_detector is None:
        from pipeline.onnx_detector import ONNXDetector
        _onnx_detector = ONNXDetector()
        
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue
            
        try:
            annotated_frame, violations = _onnx_detector.process_frame(frame, _current_polygon)
            _, buffer = cv2.imencode('.jpg', annotated_frame)
            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            time.sleep(0.04)
        except Exception as e:
            logger.error(f"Error in ONNX streaming: {e}")
            break
    cap.release()

@app.get("/video_stream")
async def video_stream():
    return StreamingResponse(
        onnx_video_stream_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )

@app.get("/api/onnx_violations")
async def get_onnx_violations():
    global _onnx_detector
    if _onnx_detector is None:
        return {"violations": []}
    return {"violations": [{"id": oid, "time": t} for oid, t in _onnx_detector.violations]}

# ─────────── WebSockets ────────────────────────────────────────────
@app.websocket("/ws/live")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive by listening for potential client messages
            data = await websocket.receive_text()
            logger.debug(f"Received WS ping: {data}")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket exception: {e}")
        manager.disconnect(websocket)

# ─────────── Static File Mounting ──────────────────────────────────
# Mount evidence folder for images and video playback
app.mount("/evidence", StaticFiles(directory=str(settings.EVIDENCE_DIR)), name="evidence")

# Serve the static website files. Mount at "/" last, so it doesn't block APIs.
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
