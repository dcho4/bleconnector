import asyncio
import statistics
import math
import os
from pathlib import Path
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Deque, Dict, Optional
from uuid import UUID

import httpx
from bleak import BleakScanner
from dotenv import load_dotenv

_root = Path(__file__).resolve().parent.parent
load_dotenv(_root / ".env", override=True)

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")
INGEST_TOKEN = os.getenv("INGEST_TOKEN", "change-me-token")
SCANNER_ID = os.getenv("SCANNER_ID", "scanner-1")
UUID_FILTER = os.getenv("UUID_FILTER", "").lower().strip()
MIN_SECONDS_BETWEEN_UPLOADS = float(os.getenv("MIN_SECONDS_BETWEEN_UPLOADS", "2.0"))
SMOOTHING_WINDOW = int(os.getenv("SMOOTHING_WINDOW", "12"))
MIN_STABLE_SAMPLES = int(os.getenv("MIN_STABLE_SAMPLES", "5"))
OUTLIER_DBM_THRESHOLD = float(os.getenv("OUTLIER_DBM_THRESHOLD", "8.0"))
RSSI_AT_1M = float(os.getenv("RSSI_AT_1M", "-59.0"))
PATH_LOSS_EXPONENT = float(os.getenv("PATH_LOSS_EXPONENT", "2.0"))


@dataclass
class ParsedIBeacon:
    uuid: str
    major: int
    minor: int
    tx_power: int

    @property
    def beacon_id(self) -> str:
        return f"{self.uuid}-{self.major}-{self.minor}"


def parse_ibeacon(manufacturer_data: Dict[int, bytes]) -> Optional[ParsedIBeacon]:
    # iBeacon format: Apple company id 0x004C + type 0x02 + len 0x15 + payload
    apple_data = manufacturer_data.get(0x004C)
    if not apple_data or len(apple_data) < 23:
        return None
    if apple_data[0] != 0x02 or apple_data[1] != 0x15:
        return None

    uuid_bytes = apple_data[2:18]
    major = int.from_bytes(apple_data[18:20], "big")
    minor = int.from_bytes(apple_data[20:22], "big")
    tx_power = int.from_bytes(apple_data[22:23], "big", signed=True)

    parsed_uuid = str(UUID(bytes=uuid_bytes)).lower()
    return ParsedIBeacon(uuid=parsed_uuid, major=major, minor=minor, tx_power=tx_power)


def estimate_distance_m(rssi: int, tx_power: int) -> float:
    # Rough distance estimate from RSSI and calibrated Tx power.
    if rssi == 0:
        return -1.0
    ratio = rssi / tx_power if tx_power != 0 else 0
    if ratio < 1.0:
        return round(math.pow(ratio, 10), 3)
    return round((0.89976 * math.pow(ratio, 7.7095)) + 0.111, 3)


def estimate_distance_calibrated_m(smoothed_rssi: float) -> float:
    # Log-distance path loss model with tunable environment constants.
    exponent = PATH_LOSS_EXPONENT if PATH_LOSS_EXPONENT > 0 else 2.0
    distance = math.pow(10.0, (RSSI_AT_1M - smoothed_rssi) / (10.0 * exponent))
    return round(max(0.05, min(distance, 50.0)), 3)


def smooth_rssi(samples: Deque[int]) -> Optional[tuple[float, int, float]]:
    if len(samples) < MIN_STABLE_SAMPLES:
        return None

    values = list(samples)
    med = statistics.median(values)
    filtered = [x for x in values if abs(x - med) <= OUTLIER_DBM_THRESHOLD]
    if len(filtered) < MIN_STABLE_SAMPLES:
        return None

    mean_value = statistics.fmean(filtered)
    stdev = statistics.pstdev(filtered) if len(filtered) > 1 else 0.0
    return mean_value, len(filtered), stdev


def estimate_confidence(sample_size: int, stdev: float) -> float:
    sample_factor = min(sample_size / max(MIN_STABLE_SAMPLES, 1), 1.0)
    stability_factor = max(0.0, 1.0 - min(stdev / 8.0, 1.0))
    return round(sample_factor * stability_factor, 3)


async def post_reading(
    client: httpx.AsyncClient,
    parsed: ParsedIBeacon,
    raw_rssi: int,
    smoothed_rssi: float,
    sample_size: int,
    distance_confidence: float,
    beacon_address: Optional[str] = None,
):
    # Calibrated model is used as primary distance estimate; legacy formula retained for fallback use.
    distance = estimate_distance_calibrated_m(smoothed_rssi)
    if distance <= 0:
        distance = estimate_distance_m(int(smoothed_rssi), parsed.tx_power)
    payload = {
        "beacon_id": parsed.beacon_id,
        "uuid": parsed.uuid,
        "major": parsed.major,
        "minor": parsed.minor,
        "beacon_address": beacon_address,
        "mac_id": beacon_address,
        "rssi": raw_rssi,
        "smoothed_rssi": round(smoothed_rssi, 2),
        "sample_size": sample_size,
        "distance_confidence": distance_confidence,
        "tx_power": parsed.tx_power,
        "distance_m": distance,
        "scanner_id": SCANNER_ID,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    res = await client.post(
        f"{API_URL}/ingest",
        headers={"x-ingest-token": INGEST_TOKEN},
        json=payload,
        timeout=10.0,
    )
    res.raise_for_status()


def on_upload_done(task: asyncio.Task):
    exc = task.exception()
    if exc:
        print(f"Upload failed: {exc}")


async def run():
    print(f"Starting BlueCharm collector: api={API_URL} scanner_id={SCANNER_ID}")
    print("Press Ctrl+C to stop.")
    last_uploaded_at: Dict[str, float] = {}
    rssi_windows: Dict[str, Deque[int]] = {}

    async with httpx.AsyncClient() as client:
        def callback(device, advertisement_data):
            parsed = parse_ibeacon(advertisement_data.manufacturer_data)
            if not parsed:
                return
            if UUID_FILTER and parsed.uuid != UUID_FILTER:
                return

            raw_rssi = int(advertisement_data.rssi)
            window = rssi_windows.setdefault(parsed.beacon_id, deque(maxlen=SMOOTHING_WINDOW))
            window.append(raw_rssi)
            stable = smooth_rssi(window)
            if stable is None:
                return
            smoothed_rssi, sample_size, stdev = stable
            confidence = estimate_confidence(sample_size, stdev)

            now = asyncio.get_event_loop().time()
            previous = last_uploaded_at.get(parsed.beacon_id, 0.0)
            if now - previous < MIN_SECONDS_BETWEEN_UPLOADS:
                return

            last_uploaded_at[parsed.beacon_id] = now
            print(
                f"Detected beacon {parsed.beacon_id} "
                f"raw_rssi={raw_rssi} smooth={smoothed_rssi:.2f} "
                f"samples={sample_size} confidence={confidence:.2f}"
            )
            beacon_address = getattr(device, "address", None)
            task = asyncio.create_task(
                post_reading(
                    client,
                    parsed,
                    raw_rssi,
                    smoothed_rssi,
                    sample_size,
                    confidence,
                    beacon_address=beacon_address,
                )
            )
            task.add_done_callback(on_upload_done)

        async with BleakScanner(callback):
            while True:
                await asyncio.sleep(1.0)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nCollector stopped.")
