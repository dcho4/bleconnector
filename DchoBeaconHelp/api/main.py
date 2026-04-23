import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Float, Integer, String, create_engine, desc, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from dotenv import load_dotenv

# Load repo-root .env regardless of where uvicorn was started; .env wins over stray shell vars.
_root = Path(__file__).resolve().parent.parent
load_dotenv(_root / ".env", override=True)


class Base(DeclarativeBase):
    pass


class BeaconReading(Base):
    __tablename__ = "beacon_readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    beacon_id: Mapped[str] = mapped_column(String(128), index=True)
    uuid: Mapped[str] = mapped_column(String(64), index=True)
    major: Mapped[int] = mapped_column(Integer, index=True)
    minor: Mapped[int] = mapped_column(Integer, index=True)
    beacon_address: Mapped[Optional[str]] = mapped_column(
        String(64), index=True, nullable=True
    )
    rssi: Mapped[int] = mapped_column(Integer)
    smoothed_rssi: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sample_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    distance_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tx_power: Mapped[int] = mapped_column(Integer)
    distance_m: Mapped[float] = mapped_column(Float)
    scanner_id: Mapped[str] = mapped_column(String(128), index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


engine = create_engine("sqlite:///./beacons.db", future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base.metadata.create_all(bind=engine)


def ensure_schema():
    # Lightweight migration for existing SQLite DBs when new fields are added.
    with engine.begin() as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(beacon_readings)").fetchall()
        existing_columns = {row[1] for row in rows}
        if "beacon_address" not in existing_columns:
            conn.exec_driver_sql(
                "ALTER TABLE beacon_readings ADD COLUMN beacon_address VARCHAR(64)"
            )
        if "smoothed_rssi" not in existing_columns:
            conn.exec_driver_sql("ALTER TABLE beacon_readings ADD COLUMN smoothed_rssi FLOAT")
        if "sample_size" not in existing_columns:
            conn.exec_driver_sql("ALTER TABLE beacon_readings ADD COLUMN sample_size INTEGER")
        if "distance_confidence" not in existing_columns:
            conn.exec_driver_sql(
                "ALTER TABLE beacon_readings ADD COLUMN distance_confidence FLOAT"
            )


ensure_schema()

app = FastAPI(title="BlueCharm Beacon Ingest API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class IngestPayload(BaseModel):
    beacon_id: str = Field(..., description="Format: uuid-major-minor")
    uuid: str
    major: int
    minor: int
    beacon_address: Optional[str] = None
    mac_id: Optional[str] = Field(
        default=None, description="BLE address / MAC (same as beacon_address if both sent)"
    )
    rssi: int
    smoothed_rssi: Optional[float] = None
    sample_size: Optional[int] = None
    distance_confidence: Optional[float] = None
    tx_power: int
    distance_m: float
    scanner_id: str
    observed_at: datetime


class BeaconLatest(BaseModel):
    beacon_id: str
    uuid: str
    major: int
    minor: int
    beacon_address: Optional[str] = None
    mac_id: Optional[str] = None
    rssi: int
    smoothed_rssi: Optional[float] = None
    sample_size: Optional[int] = None
    distance_confidence: Optional[float] = None
    tx_power: int
    distance_m: float
    scanner_id: str
    observed_at: datetime


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def require_ingest_token(x_ingest_token: Optional[str] = Header(default=None)):
    expected = os.getenv("INGEST_TOKEN", "change-me-token")
    if x_ingest_token != expected:
        raise HTTPException(status_code=401, detail="Invalid ingest token")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/ingest", dependencies=[Depends(require_ingest_token)])
def ingest(reading: IngestPayload, db: Session = Depends(get_db)):
    ble_addr = reading.mac_id or reading.beacon_address
    row = BeaconReading(
        beacon_id=reading.beacon_id,
        uuid=reading.uuid,
        major=reading.major,
        minor=reading.minor,
        beacon_address=ble_addr,
        rssi=reading.rssi,
        smoothed_rssi=reading.smoothed_rssi,
        sample_size=reading.sample_size,
        distance_confidence=reading.distance_confidence,
        tx_power=reading.tx_power,
        distance_m=reading.distance_m,
        scanner_id=reading.scanner_id,
        observed_at=reading.observed_at,
    )
    db.add(row)
    db.commit()
    return {"ok": True, "id": row.id}


@app.get("/beacons/latest")
def list_latest(
    scanner_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    stmt = select(BeaconReading)
    if scanner_id:
        stmt = stmt.where(BeaconReading.scanner_id == scanner_id)
    stmt = stmt.order_by(desc(BeaconReading.observed_at), desc(BeaconReading.id))
    rows = db.execute(stmt).scalars().all()

    latest_by_beacon = {}
    for row in rows:
        if row.beacon_id not in latest_by_beacon:
            latest_by_beacon[row.beacon_id] = row
        if len(latest_by_beacon) >= limit:
            break

    output = [
        BeaconLatest(
            beacon_id=item.beacon_id,
            uuid=item.uuid,
            major=item.major,
            minor=item.minor,
            beacon_address=item.beacon_address,
            mac_id=item.beacon_address,
            rssi=item.rssi,
            smoothed_rssi=item.smoothed_rssi,
            sample_size=item.sample_size,
            distance_confidence=item.distance_confidence,
            tx_power=item.tx_power,
            distance_m=item.distance_m,
            scanner_id=item.scanner_id,
            observed_at=item.observed_at,
        )
        for item in latest_by_beacon.values()
    ]
    return {"count": len(output), "items": output}


@app.get("/beacons/{beacon_id}/history")
def beacon_history(
    beacon_id: str,
    limit: int = Query(default=100, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    stmt = (
        select(BeaconReading)
        .where(BeaconReading.beacon_id == beacon_id)
        .order_by(desc(BeaconReading.observed_at), desc(BeaconReading.id))
        .limit(limit)
    )
    rows = db.execute(stmt).scalars().all()
    return {
        "beacon_id": beacon_id,
        "count": len(rows),
        "items": [
            {
                "uuid": r.uuid,
                "major": r.major,
                "minor": r.minor,
                "beacon_address": r.beacon_address,
                "mac_id": r.beacon_address,
                "rssi": r.rssi,
                "smoothed_rssi": r.smoothed_rssi,
                "sample_size": r.sample_size,
                "distance_confidence": r.distance_confidence,
                "tx_power": r.tx_power,
                "distance_m": r.distance_m,
                "scanner_id": r.scanner_id,
                "observed_at": r.observed_at,
            }
            for r in rows
        ],
    }
