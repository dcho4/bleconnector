# BlueCharm Beacon Upload Stack

This project gives you:

- A **collector app** that scans BlueCharm/iBeacon BLE advertisements.
- A **web API** that stores beacon readings in SQLite.
- Endpoints your other web app can call to fetch latest beacons or history.

## 1) Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set your token in `.env` (same token is used by collector and API).

## 2) Run the API

```bash
source .venv/bin/activate
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

API docs: `http://127.0.0.1:8000/docs`

## 3) Run the collector

In another terminal:

```bash
source .venv/bin/activate
python collector/main.py
```

It will detect iBeacon packets, parse UUID/major/minor, capture BLE address, estimate distance, and upload to `/ingest`.

For best accuracy, tune these `.env` values:

- `SMOOTHING_WINDOW` (default `12`)
- `MIN_STABLE_SAMPLES` (default `5`)
- `OUTLIER_DBM_THRESHOLD` (default `8.0`)
- `RSSI_AT_1M` (default `-59.0`)
- `PATH_LOSS_EXPONENT` (default `2.0`)

## 4) Run the separate web app (viewer)

This simulates your other web app pulling data online from the API.

```bash
python3 -m http.server 5500 -d viewer
```

Then open:

- `http://127.0.0.1:5500`

In the viewer:

- Set **API Base URL** (for local test: `http://127.0.0.1:8000`)
- Click **Refresh** (or use auto-refresh every 5 seconds)
- Click any beacon row to load organized history for that beacon
- View **Estimated Beacon Location View** to visualize beacon proximity around the scanner

## API your other web app can use

Each reading contains:

- `beacon_id` (`uuid-major-minor`)
- `uuid`
- `major`
- `minor`
- `beacon_address` (BLE address when available)
- `mac_id` (same as `beacon_address` — BLE / MAC for the device)
- `rssi`
- `smoothed_rssi`
- `sample_size`
- `distance_confidence`
- `tx_power`
- `distance_m`
- `scanner_id`
- `observed_at`

Identity strategy:

- Primary logical ID: `beacon_id` (`uuid-major-minor`)
- Hardware-level disambiguation: `beacon_address`

### Latest beacons

```bash
curl "http://127.0.0.1:8000/beacons/latest?limit=100"
```

### Beacon history

```bash
curl "http://127.0.0.1:8000/beacons/<uuid-major-minor>/history?limit=200"
```

## Web app integration example (frontend JS)

```js
const res = await fetch("http://127.0.0.1:8000/beacons/latest?limit=100");
const data = await res.json();
console.log(data.items);
```

## Notes for BlueCharm beacons

- BlueCharm beacons generally broadcast in iBeacon format.
- If you want to ingest only one UUID, set `UUID_FILTER` in `.env`.
- BLE scan permissions are required by macOS; allow Bluetooth access when prompted.
- Distance is calculated from smoothed RSSI with a calibrated path-loss model.
