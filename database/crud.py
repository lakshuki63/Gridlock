"""
VisionEnforce — Database CRUD operations (async SQLAlchemy)
"""

from datetime import datetime, timedelta
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select, func, and_, desc
from database.models import Base, Camera, Violation, Officer, ViolationType, ReviewStatus, Severity, VIOLATION_SEVERITY
from sqlalchemy.orm import joinedload
from config import settings
import logging

logger = logging.getLogger(__name__)

# ─────────── Engine + Session ─────────────────────────────────────

engine = create_async_engine(
    settings.DB_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def init_db():
    """Create all tables and seed default camera."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        # Seed default demo camera if not exists
        result = await session.execute(select(Camera).where(Camera.id == "CAM-DEMO-01"))
        if not result.scalar_one_or_none():
            cam = Camera(
                id="CAM-DEMO-01",
                name="KR Circle — North Entry",
                location_lat=12.9716,
                location_lon=77.5946,
                landmark="KR Circle, Bengaluru",
                stream_url="demo",
                is_active=True,
                last_heartbeat=datetime.utcnow(),
            )
            session.add(cam)

            cam2 = Camera(
                id="CAM-DEMO-02",
                name="Silk Board Junction — East",
                location_lat=12.9177,
                location_lon=77.6228,
                landmark="Silk Board Junction, Bengaluru",
                stream_url="demo",
                is_active=True,
                last_heartbeat=datetime.utcnow(),
            )
            session.add(cam2)

            cam3 = Camera(
                id="CAM-DEMO-03",
                name="MG Road Signal — West",
                location_lat=12.9757,
                location_lon=77.6086,
                landmark="MG Road, Bengaluru",
                stream_url="demo",
                is_active=False,
                last_heartbeat=datetime.utcnow() - timedelta(hours=2),
            )
            session.add(cam3)

            cam4 = Camera(
                id="CAM-DEMO-04",
                name="Koramangala 5th Block — Main Rd",
                location_lat=12.9352,
                location_lon=77.6245,
                landmark="Koramangala 5th Block, Bengaluru",
                stream_url="demo",
                is_active=True,
                last_heartbeat=datetime.utcnow(),
                calibration={"parking_threshold_minutes": 5.0, "risk_window_minutes": 30},
            )
            session.add(cam4)

            cam5 = Camera(
                id="CAM-DEMO-05",
                name="Indiranagar 100 Ft Road — East",
                location_lat=12.9784,
                location_lon=77.6408,
                landmark="Indiranagar 100 Ft Road, Bengaluru",
                stream_url="demo",
                is_active=True,
                last_heartbeat=datetime.utcnow(),
                calibration={"parking_threshold_minutes": 5.0, "risk_window_minutes": 30},
            )
            session.add(cam5)

            cam6 = Camera(
                id="CAM-DEMO-06",
                name="Hebbal Flyover — North",
                location_lat=13.0382,
                location_lon=77.5919,
                landmark="Hebbal Flyover, Bengaluru",
                stream_url="demo",
                is_active=True,
                last_heartbeat=datetime.utcnow(),
                calibration={"parking_threshold_minutes": 5.0, "risk_window_minutes": 30},
            )
            session.add(cam6)

            cam7 = Camera(
                id="CAM-DEMO-07",
                name="Electronic City Toll — South",
                location_lat=12.8452,
                location_lon=77.6602,
                landmark="Electronic City, Bengaluru",
                stream_url="demo",
                is_active=True,
                last_heartbeat=datetime.utcnow(),
                calibration={"parking_threshold_minutes": 5.0, "risk_window_minutes": 30},
            )
            session.add(cam7)

            cam8 = Camera(
                id="CAM-DEMO-08",
                name="Whitefield ITPL Main Rd",
                location_lat=12.9850,
                location_lon=77.7360,
                landmark="Whitefield, Bengaluru",
                stream_url="demo",
                is_active=True,
                last_heartbeat=datetime.utcnow(),
                calibration={"parking_threshold_minutes": 5.0, "risk_window_minutes": 30},
            )
            session.add(cam8)

            cam9 = Camera(
                id="CAM-DEMO-09",
                name="Jayanagar 4th Block Signal",
                location_lat=12.9250,
                location_lon=77.5938,
                landmark="Jayanagar, Bengaluru",
                stream_url="demo",
                is_active=True,
                last_heartbeat=datetime.utcnow(),
                calibration={"parking_threshold_minutes": 5.0, "risk_window_minutes": 30},
            )
            session.add(cam9)

            cam10 = Camera(
                id="CAM-DEMO-10",
                name="Marathahalli Bridge — West",
                location_lat=12.9563,
                location_lon=77.7010,
                landmark="Marathahalli, Bengaluru",
                stream_url="demo",
                is_active=True,
                last_heartbeat=datetime.utcnow(),
                calibration={"parking_threshold_minutes": 5.0, "risk_window_minutes": 30},
            )
            session.add(cam10)

            # Seed default officer
            officer = Officer(id="OFF-001", name="Constable Ravi Kumar", badge_no="KA-BTP-2847")
            session.add(officer)

            await session.commit()
            logger.info("Database seeded with demo cameras and officer.")

        # Seed Vodra & Talaimari cameras if they don't exist yet
        result_vodra = await session.execute(select(Camera).where(Camera.id == "CAM-VODRA-NORTH"))
        if not result_vodra.scalar_one_or_none():
            logger.info("Seeding Vodra and Talaimari cameras...")
            vodra_cams = [
                Camera(
                    id="CAM-VODRA-NORTH",
                    name="Vodra Junction — North",
                    location_lat=24.3697,
                    location_lon=88.6251,
                    landmark="Vodra Junction, Rajshahi",
                    stream_url="d:/12345/Gridlock/Vodra/North.mp4",
                    is_active=True,
                    last_heartbeat=datetime.utcnow(),
                    calibration={"parking_threshold_minutes": 5.0, "risk_window_minutes": 30},
                ),
                Camera(
                    id="CAM-VODRA-SOUTH",
                    name="Vodra Junction — South",
                    location_lat=24.3692,
                    location_lon=88.6251,
                    landmark="Vodra Junction, Rajshahi",
                    stream_url="d:/12345/Gridlock/Vodra/South.mp4",
                    is_active=True,
                    last_heartbeat=datetime.utcnow(),
                    calibration={"parking_threshold_minutes": 5.0, "risk_window_minutes": 30},
                ),
                Camera(
                    id="CAM-VODRA-WEST",
                    name="Vodra Junction — West",
                    location_lat=24.3695,
                    location_lon=88.6245,
                    landmark="Vodra Junction, Rajshahi",
                    stream_url="d:/12345/Gridlock/Vodra/weast.mp4",
                    is_active=True,
                    last_heartbeat=datetime.utcnow(),
                    calibration={"parking_threshold_minutes": 5.0, "risk_window_minutes": 30},
                ),
                Camera(
                    id="CAM-TALAIMARI-NE",
                    name="Talaimari Junction — North-East",
                    location_lat=24.3638,
                    location_lon=88.6288,
                    landmark="Talaimari Junction, Rajshahi",
                    stream_url="d:/12345/Gridlock/Talaimari/North-East.mp4",
                    is_active=True,
                    last_heartbeat=datetime.utcnow(),
                    calibration={"parking_threshold_minutes": 5.0, "risk_window_minutes": 30},
                ),
                Camera(
                    id="CAM-TALAIMARI-NW",
                    name="Talaimari Junction — North-West",
                    location_lat=24.3638,
                    location_lon=88.6282,
                    landmark="Talaimari Junction, Rajshahi",
                    stream_url="d:/12345/Gridlock/Talaimari/North-Weast.mp4",
                    is_active=True,
                    last_heartbeat=datetime.utcnow(),
                    calibration={"parking_threshold_minutes": 5.0, "risk_window_minutes": 30},
                ),
                Camera(
                    id="CAM-TALAIMARI-WEST",
                    name="Talaimari Junction — West",
                    location_lat=24.3635,
                    location_lon=88.6280,
                    landmark="Talaimari Junction, Rajshahi",
                    stream_url="d:/12345/Gridlock/Talaimari/Weast.mp4",
                    is_active=True,
                    last_heartbeat=datetime.utcnow(),
                    calibration={"parking_threshold_minutes": 5.0, "risk_window_minutes": 30},
                ),
                Camera(
                    id="CAM-TALAIMARI-EAST",
                    name="Talaimari Junction — East",
                    location_lat=24.3635,
                    location_lon=88.6290,
                    landmark="Talaimari Junction, Rajshahi",
                    stream_url="d:/12345/Gridlock/Talaimari/east.mp4",
                    is_active=True,
                    last_heartbeat=datetime.utcnow(),
                    calibration={"parking_threshold_minutes": 5.0, "risk_window_minutes": 30},
                ),
            ]
            for cam in vodra_cams:
                session.add(cam)
            await session.commit()
            logger.info("Database seeded with Vodra and Talaimari cameras.")


async def get_db():
    """FastAPI dependency for DB session."""
    async with AsyncSessionLocal() as session:
        yield session


# ─────────── Violation CRUD ───────────────────────────────────────

async def create_violation(session: AsyncSession, data: dict) -> Violation:
    v = Violation(**data)
    session.add(v)
    await session.commit()
    # Eagerly load camera relationship
    result = await session.execute(
        select(Violation).where(Violation.id == v.id).options(joinedload(Violation.camera_rel))
    )
    return result.scalar_one()


async def get_violation(session: AsyncSession, violation_id: str) -> Optional[Violation]:
    result = await session.execute(
        select(Violation).where(Violation.id == violation_id).options(joinedload(Violation.camera_rel))
    )
    return result.scalar_one_or_none()


async def list_violations(
    session: AsyncSession,
    page: int = 1,
    limit: int = 20,
    status: Optional[str] = None,
    violation_type: Optional[str] = None,
    camera_id: Optional[str] = None,
    hours: Optional[int] = None,
) -> dict:
    q = select(Violation).options(joinedload(Violation.camera_rel)).order_by(desc(Violation.timestamp_utc))

    if status:
        q = q.where(Violation.review_status == status)
    if violation_type:
        q = q.where(Violation.violation_type == violation_type)
    if camera_id:
        q = q.where(Violation.camera_id == camera_id)
    if hours:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        q = q.where(Violation.timestamp_utc >= cutoff)

    count_q = select(func.count()).select_from(q.subquery())
    total = (await session.execute(count_q)).scalar()

    q = q.offset((page - 1) * limit).limit(limit)
    items = (await session.execute(q)).scalars().all()

    return {"total": total, "page": page, "limit": limit, "items": items}


async def update_violation_review(
    session: AsyncSession,
    violation_id: str,
    action: str,
    officer_id: str,
    notes: str = "",
    license_plate: Optional[str] = None,
) -> Optional[Violation]:
    v = await get_violation(session, violation_id)
    if not v:
        return None

    status_map = {
        "APPROVE": ReviewStatus.APPROVED,
        "REJECT": ReviewStatus.REJECTED,
        "ESCALATE": ReviewStatus.ESCALATED,
    }
    v.review_status = status_map.get(action, ReviewStatus.PENDING_HUMAN)
    v.assigned_officer_id = officer_id
    v.officer_notes = notes
    v.reviewed_at = datetime.utcnow()

    if license_plate is not None:
        v.license_plate = license_plate

    if action == "APPROVE":
        v.challan_issued = True

    await session.commit()
    await session.refresh(v)
    return v


# ─────────── Analytics CRUD ───────────────────────────────────────

async def get_analytics_summary(session: AsyncSession, hours: int = 24) -> dict:
    cutoff = datetime.utcnow() - timedelta(hours=hours)

    # Total violations in window
    total = (await session.execute(
        select(func.count(Violation.id)).where(Violation.timestamp_utc >= cutoff)
    )).scalar()

    # By review status
    status_counts = {}
    for status in ReviewStatus:
        cnt = (await session.execute(
            select(func.count(Violation.id)).where(
                and_(Violation.timestamp_utc >= cutoff, Violation.review_status == status)
            )
        )).scalar()
        status_counts[status.value] = cnt

    # By violation type
    type_counts = {}
    for vt in ViolationType:
        cnt = (await session.execute(
            select(func.count(Violation.id)).where(
                and_(Violation.timestamp_utc >= cutoff, Violation.violation_type == vt)
            )
        )).scalar()
        if cnt > 0:
            type_counts[vt.value] = cnt

    # Top violation type
    top_type = max(type_counts, key=type_counts.get) if type_counts else None

    # Cameras active
    cams = (await session.execute(select(func.count(Camera.id)).where(Camera.is_active == True))).scalar()
    total_cams = (await session.execute(select(func.count(Camera.id)))).scalar()

    # False positive estimate (from rejected violations)
    rejected = status_counts.get("REJECTED", 0)
    approved = status_counts.get("APPROVED", 0)
    reviewed = rejected + approved
    fpr = round(rejected / reviewed, 3) if reviewed > 0 else 0.0

    return {
        "window_hours": hours,
        "total_violations": total,
        "auto_processed": status_counts.get("AUTO_PROCESSED", 0),
        "pending_review": status_counts.get("PENDING_HUMAN", 0),
        "approved": approved,
        "rejected": rejected,
        "escalated": status_counts.get("ESCALATED", 0),
        "top_violation_type": top_type,
        "by_type": type_counts,
        "active_cameras": cams,
        "total_cameras": total_cams,
        "false_positive_rate": fpr,
    }


async def get_hourly_timeseries(session: AsyncSession, hours: int = 24) -> list:
    """Return violation counts per hour for the last N hours."""
    rows = []
    now = datetime.utcnow()
    for i in range(hours - 1, -1, -1):
        start = now - timedelta(hours=i + 1)
        end   = now - timedelta(hours=i)
        cnt = (await session.execute(
            select(func.count(Violation.id)).where(
                and_(Violation.timestamp_utc >= start, Violation.timestamp_utc < end)
            )
        )).scalar()
        rows.append({"hour": start.strftime("%H:%M"), "count": cnt})
    return rows


async def get_heatmap_data(session: AsyncSession, hours: int = 24) -> list:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    cameras = (await session.execute(select(Camera))).scalars().all()
    result = []
    for cam in cameras:
        cnt = (await session.execute(
            select(func.count(Violation.id)).where(
                and_(
                    Violation.camera_id == cam.id,
                    Violation.timestamp_utc >= cutoff,
                )
            )
        )).scalar()
        if cam.location_lat:
            result.append({
                "camera_id": cam.id,
                "name": cam.name,
                "lat": cam.location_lat,
                "lon": cam.location_lon,
                "count": cnt,
            })
    return result


async def get_cameras(session: AsyncSession) -> list:
    cameras = (await session.execute(select(Camera))).scalars().all()
    return [
        {
            "id": c.id,
            "name": c.name,
            "is_active": c.is_active,
            "landmark": c.landmark,
            "location": {"lat": c.location_lat, "lon": c.location_lon},
            "last_heartbeat": c.last_heartbeat.isoformat() if c.last_heartbeat else None,
        }
        for c in cameras
    ]


# ─────────── Zone Config CRUD ──────────────────────────────────────────────

_DEFAULT_CAM_CONFIG = {
    "parking_threshold_minutes": 5.0,
    "risk_window_minutes": 30,
    "no_parking_zones": [],
}

async def get_camera_config(session: AsyncSession, camera_id: str) -> Optional[dict]:
    """Return the stored zone config for a camera, with defaults filled in."""
    result = await session.execute(select(Camera).where(Camera.id == camera_id))
    cam = result.scalar_one_or_none()
    if cam is None:
        return None
    base = dict(_DEFAULT_CAM_CONFIG)
    if cam.calibration:
        base.update({
            k: v for k, v in cam.calibration.items()
            if k in _DEFAULT_CAM_CONFIG
        })
    return {
        "camera_id": cam.id,
        "camera_name": cam.name,
        "landmark": cam.landmark,
        **base,
    }


async def set_camera_config(
    session: AsyncSession,
    camera_id: str,
    parking_threshold_minutes: float = 5.0,
    risk_window_minutes: int = 30,
    no_parking_zones: Optional[list] = None,
) -> Optional[dict]:
    """Persist zone threshold config to the camera's calibration JSON field."""
    result = await session.execute(select(Camera).where(Camera.id == camera_id))
    cam = result.scalar_one_or_none()
    if cam is None:
        return None

    existing = dict(cam.calibration) if cam.calibration else {}
    existing["parking_threshold_minutes"] = parking_threshold_minutes
    existing["risk_window_minutes"] = risk_window_minutes
    if no_parking_zones is not None:
        existing["no_parking_zones"] = no_parking_zones

    # SQLAlchemy JSON column mutation requires reassignment
    cam.calibration = existing
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(cam, "calibration")

    await session.commit()
    await session.refresh(cam)
    return {
        "camera_id": cam.id,
        "camera_name": cam.name,
        "landmark": cam.landmark,
        "parking_threshold_minutes": parking_threshold_minutes,
        "risk_window_minutes": risk_window_minutes,
        "no_parking_zones": existing.get("no_parking_zones", []),
    }
