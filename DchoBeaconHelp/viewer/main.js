const apiBaseUrlInput = document.getElementById("apiBaseUrl");
const scannerIdInput = document.getElementById("scannerId");
const limitInput = document.getElementById("limit");
const refreshBtn = document.getElementById("refreshBtn");
const toggleAutoBtn = document.getElementById("toggleAutoBtn");
const statusEl = document.getElementById("status");
const beaconsBody = document.getElementById("beaconsBody");
const historyBody = document.getElementById("historyBody");
const historyLabel = document.getElementById("historyLabel");
const totalBeaconsEl = document.getElementById("totalBeacons");
const strongSignalsEl = document.getElementById("strongSignals");
const lastRefreshEl = document.getElementById("lastRefresh");
const mapGridEl = document.getElementById("mapGrid");
const mapPointsEl = document.getElementById("mapPoints");

let autoRefreshEnabled = true;
let timer = null;
let selectedBeaconId = null;
const MAP_SIZE = 600;
const MAP_CENTER = MAP_SIZE / 2;
const MAP_MAX_RADIUS = 250;

function stableAngleFromId(id) {
  // Deterministic angle to spread points around scanner without jitter.
  let hash = 0;
  for (let i = 0; i < id.length; i += 1) {
    hash = (hash * 31 + id.charCodeAt(i)) % 3600;
  }
  return (hash / 3600) * Math.PI * 2;
}

function colorFromRssi(rssi) {
  const n = Number(rssi);
  if (n >= -60) return "#0ea35a";
  if (n >= -75) return "#f4b400";
  return "#db4437";
}

function drawMapGrid() {
  mapGridEl.innerHTML = "";
  const rings = [50, 100, 150, 200, 250];
  rings.forEach((r, idx) => {
    const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    c.setAttribute("cx", MAP_CENTER);
    c.setAttribute("cy", MAP_CENTER);
    c.setAttribute("r", String(r));
    c.setAttribute("class", "map-grid-ring");
    mapGridEl.appendChild(c);

    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("x", String(MAP_CENTER + r + 5));
    label.setAttribute("y", String(MAP_CENTER - 4));
    label.setAttribute("class", "map-grid-label");
    label.textContent = `${idx + 1}m`;
    mapGridEl.appendChild(label);
  });

  const axisH = document.createElementNS("http://www.w3.org/2000/svg", "line");
  axisH.setAttribute("x1", "50");
  axisH.setAttribute("y1", String(MAP_CENTER));
  axisH.setAttribute("x2", "550");
  axisH.setAttribute("y2", String(MAP_CENTER));
  axisH.setAttribute("class", "map-grid-axis");
  mapGridEl.appendChild(axisH);

  const axisV = document.createElementNS("http://www.w3.org/2000/svg", "line");
  axisV.setAttribute("x1", String(MAP_CENTER));
  axisV.setAttribute("y1", "50");
  axisV.setAttribute("x2", String(MAP_CENTER));
  axisV.setAttribute("y2", "550");
  axisV.setAttribute("class", "map-grid-axis");
  mapGridEl.appendChild(axisV);

  const center = document.createElementNS("http://www.w3.org/2000/svg", "circle");
  center.setAttribute("cx", String(MAP_CENTER));
  center.setAttribute("cy", String(MAP_CENTER));
  center.setAttribute("r", "7");
  center.setAttribute("class", "map-grid-center");
  mapGridEl.appendChild(center);

  const centerLabel = document.createElementNS("http://www.w3.org/2000/svg", "text");
  centerLabel.setAttribute("x", String(MAP_CENTER + 10));
  centerLabel.setAttribute("y", String(MAP_CENTER - 10));
  centerLabel.setAttribute("class", "map-grid-label");
  centerLabel.textContent = "Scanner";
  mapGridEl.appendChild(centerLabel);
}

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.style.background = isError ? "#ffe8e8" : "#eef3fb";
  statusEl.style.color = isError ? "#8a1f1f" : "#304a70";
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return date.toLocaleString();
}

function safeText(value) {
  return value === null || value === undefined || value === "" ? "-" : String(value);
}

function buildLatestUrl() {
  const base = apiBaseUrlInput.value.trim().replace(/\/+$/, "");
  const scannerId = scannerIdInput.value.trim();
  const limit = Number(limitInput.value || 100);

  const params = new URLSearchParams();
  params.set("limit", String(limit));
  if (scannerId) params.set("scanner_id", scannerId);
  return `${base}/beacons/latest?${params.toString()}`;
}

function buildHistoryUrl(beaconId) {
  const base = apiBaseUrlInput.value.trim().replace(/\/+$/, "");
  return `${base}/beacons/${encodeURIComponent(beaconId)}/history?limit=200`;
}

function renderLatest(items) {
  beaconsBody.innerHTML = "";
  if (!items.length) {
    beaconsBody.innerHTML = `<tr><td colspan="9">No beacons found.</td></tr>`;
    return;
  }

  items.forEach((item) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="mono">${safeText(item.beacon_id)}</td>
      <td class="mono">${safeText(item.mac_id ?? item.beacon_address)}</td>
      <td>${safeText(item.scanner_id)}</td>
      <td>${safeText(item.rssi)} / ${safeText(item.smoothed_rssi)}</td>
      <td>${safeText(item.tx_power)}</td>
      <td>${safeText(item.distance_confidence)}</td>
      <td>${safeText(item.sample_size)}</td>
      <td>${safeText(item.distance_m)}</td>
      <td>${formatDate(item.observed_at)}</td>
    `;
    tr.addEventListener("click", () => {
      selectedBeaconId = item.beacon_id;
      loadHistory(item.beacon_id);
    });
    beaconsBody.appendChild(tr);
  });
}

function renderHistory(items) {
  historyBody.innerHTML = "";
  if (!items.length) {
    historyBody.innerHTML = `<tr><td colspan="8">No history yet.</td></tr>`;
    return;
  }

  items.forEach((item) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${formatDate(item.observed_at)}</td>
      <td class="mono">${safeText(item.mac_id ?? item.beacon_address)}</td>
      <td>${safeText(item.scanner_id)}</td>
      <td>${safeText(item.rssi)} / ${safeText(item.smoothed_rssi)}</td>
      <td>${safeText(item.tx_power)}</td>
      <td>${safeText(item.distance_confidence)}</td>
      <td>${safeText(item.sample_size)}</td>
      <td>${safeText(item.distance_m)}</td>
    `;
    historyBody.appendChild(tr);
  });
}

function renderLocationMap(items) {
  mapPointsEl.innerHTML = "";
  if (!items.length) return;

  items.forEach((item) => {
    const beaconId = safeText(item.beacon_id);
    const distance = Math.max(0, Number(item.distance_m) || 0);
    const clampedMeters = Math.min(distance, 5);
    const radius = (clampedMeters / 5) * MAP_MAX_RADIUS;
    const angle = stableAngleFromId(beaconId);
    const x = MAP_CENTER + Math.cos(angle) * radius;
    const y = MAP_CENTER + Math.sin(angle) * radius;

    const point = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    point.setAttribute("cx", String(x));
    point.setAttribute("cy", String(y));
    point.setAttribute("r", "8");
    point.setAttribute("class", "map-point");
    point.setAttribute("fill", colorFromRssi(item.rssi));
    const confidence = Number(item.distance_confidence);
    const opacity = Number.isFinite(confidence) ? Math.min(Math.max(confidence, 0.2), 1) : 0.45;
    point.setAttribute("opacity", String(opacity));
    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    const mac = safeText(item.mac_id ?? item.beacon_address);
    title.textContent = `MAC ${mac} | ${beaconId} | ${distance.toFixed(2)}m | raw RSSI ${safeText(item.rssi)} | smooth ${safeText(item.smoothed_rssi)} | conf ${safeText(item.distance_confidence)}`;
    point.appendChild(title);
    mapPointsEl.appendChild(point);

    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("x", String(x + 10));
    label.setAttribute("y", String(y - 10));
    label.setAttribute("class", "map-point-label");
    const shortId = beaconId.length > 16 ? `${beaconId.slice(0, 16)}...` : beaconId;
    label.textContent = `${shortId} (${distance.toFixed(1)}m)`;
    mapPointsEl.appendChild(label);
  });
}

async function loadLatest() {
  const url = buildLatestUrl();
  setStatus(`Loading ${url}`);
  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`API returned ${response.status}`);
    }
    const data = await response.json();
    const items = Array.isArray(data.items) ? data.items : [];
    renderLatest(items);
    renderLocationMap(items);

    totalBeaconsEl.textContent = String(items.length);
    strongSignalsEl.textContent = String(
      items.filter((x) => Number(x.smoothed_rssi ?? x.rssi) >= -70).length
    );
    lastRefreshEl.textContent = new Date().toLocaleTimeString();
    setStatus(`Loaded ${items.length} beacons`);

    if (selectedBeaconId) {
      await loadHistory(selectedBeaconId);
    }
  } catch (error) {
    setStatus(`Failed to load latest beacons: ${error.message}`, true);
  }
}

async function loadHistory(beaconId) {
  historyLabel.textContent = `History for: ${beaconId}`;
  const url = buildHistoryUrl(beaconId);
  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`API returned ${response.status}`);
    }
    const data = await response.json();
    const items = Array.isArray(data.items) ? data.items : [];
    renderHistory(items);
  } catch (error) {
    historyBody.innerHTML = "";
    historyLabel.textContent = `History failed for ${beaconId}: ${error.message}`;
  }
}

function startAutoRefresh() {
  clearInterval(timer);
  timer = setInterval(() => {
    if (autoRefreshEnabled) {
      loadLatest();
    }
  }, 5000);
}

refreshBtn.addEventListener("click", loadLatest);
toggleAutoBtn.addEventListener("click", () => {
  autoRefreshEnabled = !autoRefreshEnabled;
  toggleAutoBtn.textContent = autoRefreshEnabled ? "Pause Auto Refresh" : "Resume Auto Refresh";
});

startAutoRefresh();
drawMapGrid();
loadLatest();
