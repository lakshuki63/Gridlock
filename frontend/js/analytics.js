/**
 * VisionEnforce — Analytics JS
 * Handles: Leaflet.js map density rendering, Chart.js line and bar charts,
 *          API calls, and responsive updates.
 */

'use strict';

const API = 'http://localhost:8000';
const WS_URL = 'ws://localhost:8000/ws/live';
const OFFICER_ID = localStorage.getItem('officerId') || 'OFF-001';

// Chart singletons
let trendChart = null;
let distributionChart = null;
let mapInstance = null;
let mapCircles = [];
let ws = null;

const $ = id => document.getElementById(id);

const violationTypeColors = {
  RED_LIGHT_VIOLATION: '#ef4444',
  STOP_LINE_VIOLATION: '#f97316',
  WRONG_SIDE_DRIVING: '#dc2626',
  ILLEGAL_PARKING: '#eab308'
};

const violationLabels = {
  RED_LIGHT_VIOLATION: 'Red Light',
  STOP_LINE_VIOLATION: 'Stop Line',
  WRONG_SIDE_DRIVING: 'Wrong Side',
  ILLEGAL_PARKING: 'Illegal Parking'
};

/* ─── Mock Fallbacks ─── */
const MOCK_HEATMAP = [
  { camera_id: 'CAM-DEMO-01', name: 'KR Circle — North Entry', lat: 12.9716, lon: 77.5946, count: 489 },
  { camera_id: 'CAM-DEMO-02', name: 'Silk Board Junction — East', lat: 12.9177, lon: 77.6228, count: 312 },
  { camera_id: 'CAM-DEMO-03', name: 'MG Road Signal — West', lat: 12.9757, lon: 77.6086, count: 46 }
];

const MOCK_TIMESERIES = [
  { hour: '08:00', count: 18 }, { hour: '09:00', count: 42 }, { hour: '10:00', count: 56 },
  { hour: '11:00', count: 32 }, { hour: '12:00', count: 24 }, { hour: '13:00', count: 15 },
  { hour: '14:00', count: 28 }, { hour: '15:00', count: 35 }, { hour: '16:00', count: 49 },
  { hour: '17:00', count: 62 }, { hour: '18:00', count: 83 }, { hour: '19:00', count: 71 },
  { hour: '20:00', count: 44 }, { hour: '21:00', count: 30 }, { hour: '22:00', count: 19 }
];

document.addEventListener('DOMContentLoaded', () => {
  initOfficer();
  connectWebSocket();
  initMap();
  initCharts();
  loadData();

  $('timeRange').addEventListener('change', loadData);
});

function initOfficer() {
  $('officerLabel').textContent = OFFICER_ID;
  const initials = OFFICER_ID.replace(/[^A-Z0-9]/gi,'').slice(0,2).toUpperCase();
  $('officerInitials').textContent = initials || 'OF';
}

function connectWebSocket() {
  setWsState('connecting');
  try {
    ws = new WebSocket(WS_URL);
  } catch {
    setTimeout(connectWebSocket, 5000);
    return;
  }

  ws.onopen = () => setWsState('connected');
  ws.onclose = () => { setWsState('disconnected'); setTimeout(connectWebSocket, 5000); };
  ws.onerror = () => setWsState('disconnected');
}

function setWsState(state) {
  const ind = $('wsIndicator');
  const txt = $('wsStatus');
  ind.className = `ws-indicator ${state}`;
  const labels = { connecting: 'Connecting…', connected: 'Live', disconnected: 'Offline' };
  txt.textContent = labels[state] || state;
}

/* ─── Leaflet Map ─── */
function initMap() {
  // Center map on KR Circle, Bengaluru
  mapInstance = L.map('leaflet-map', {
    zoomControl: false,
    attributionControl: false
  }).setView([12.955, 77.610], 11.5);

  // Add premium CartoDB Dark Matter tile layer
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    maxZoom: 20
  }).addTo(mapInstance);
}

function updateMapData(heatmapData) {
  // Clear old markers
  mapCircles.forEach(c => mapInstance.removeLayer(c));
  mapCircles = [];

  if (!heatmapData || heatmapData.length === 0) return;

  heatmapData.forEach(loc => {
    if (!loc.lat || !loc.lon) return;

    // Size relative to incident counts
    const radius = Math.min(250 + (loc.count * 1.5), 1800);
    
    const circle = L.circle([loc.lat, loc.lon], {
      color: '#00d4ff',
      fillColor: '#7c3aed',
      fillOpacity: 0.35,
      weight: 1.5,
      radius: radius
    }).addTo(mapInstance);

    circle.bindPopup(`
      <div style="font-family:'Inter',sans-serif;color:#fff;background:#0d1426;padding:5px;">
        <strong style="font-size:0.85rem;">${loc.name}</strong><br/>
        <span style="font-size:0.75rem;color:#94a3b8;">ID: ${loc.camera_id}</span><br/>
        <span style="font-size:0.78rem;color:#00d4ff;font-weight:700;margin-top:4px;display:inline-block;">${loc.count} Incidents Today</span>
      </div>
    `, {
      closeButton: false,
      className: 'dark-popup'
    });

    mapCircles.push(circle);
  });

  // Fit bounds if we have multiple cameras
  const coords = heatmapData.filter(l => l.lat && l.lon).map(l => [l.lat, l.lon]);
  if (coords.length > 0) {
    mapInstance.fitBounds(coords, { padding: [40, 40], maxZoom: 13 });
  }
}

/* ─── Charts ─── */
function initCharts() {
  const trendCtx = $('trendChart').getContext('2d');
  const distCtx = $('distributionChart').getContext('2d');

  // Trend line chart gradient
  const trendGrad = trendCtx.createLinearGradient(0, 0, 0, 240);
  trendGrad.addColorStop(0, 'rgba(0, 212, 255, 0.35)');
  trendGrad.addColorStop(1, 'rgba(0, 212, 255, 0.00)');

  trendChart = new Chart(trendCtx, {
    type: 'line',
    data: {
      labels: [],
      datasets: [{
        label: 'Incidents',
        data: [],
        borderColor: '#00d4ff',
        borderWidth: 2,
        backgroundColor: trendGrad,
        fill: true,
        tension: 0.35,
        pointBackgroundColor: '#00d4ff',
        pointHoverRadius: 5
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: 'rgba(255,255,255,0.03)' }, ticks: { color: '#94a3b8', font: { size: 10 } } },
        y: { grid: { color: 'rgba(255,255,255,0.03)' }, ticks: { color: '#94a3b8', font: { size: 10 } } }
      }
    }
  });

  distributionChart = new Chart(distCtx, {
    type: 'bar',
    data: {
      labels: ['Red Light', 'Stop Line', 'Wrong Side', 'Illegal Parking'],
      datasets: [{
        data: [0, 0, 0, 0],
        backgroundColor: ['#ef4444', '#f97316', '#dc2626', '#eab308'],
        borderWidth: 0,
        borderRadius: 4
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: 'rgba(255,255,255,0.03)' }, ticks: { color: '#94a3b8', font: { size: 10 } } },
        y: { grid: { display: false }, ticks: { color: '#94a3b8', font: { size: 11, weight: 'bold' } } }
      }
    }
  });
}

/* ─── API Requests ─── */
async function loadData() {
  const hours = $('timeRange').value;
  
  // Fetch summary
  try {
    const res = await fetch(`${API}/api/analytics/summary?hours=${hours}`);
    if (!res.ok) throw new Error();
    const d = await res.json();
    applySummary(d);
  } catch {
    // Show mock stats on network failure
    applySummary({
      total_violations: 847,
      pending_review: 63,
      auto_processed: 712,
      false_positive_rate: 3.2,
      by_type: { RED_LIGHT_VIOLATION: 312, STOP_LINE_VIOLATION: 198, WRONG_SIDE_DRIVING: 156, ILLEGAL_PARKING: 181 }
    });
  }

  // Fetch timeseries
  try {
    const res = await fetch(`${API}/api/analytics/timeseries?hours=${hours}`);
    if (!res.ok) throw new Error();
    const d = await res.json();
    updateTrendChart(d);
  } catch {
    updateTrendChart(MOCK_TIMESERIES);
  }

  // Fetch heatmap (coordinates and camera lists)
  try {
    const res = await fetch(`${API}/api/analytics/heatmap?hours=${hours}`);
    if (!res.ok) throw new Error();
    const d = await res.json();
    updateMapData(d);
    renderLeaderboard(d);
  } catch {
    updateMapData(MOCK_HEATMAP);
    renderLeaderboard(MOCK_HEATMAP);
  }
}

function applySummary(d) {
  $('statsTotal').textContent = d.total_violations || 0;
  $('statsPending').textContent = d.pending_review || 0;
  $('statsAuto').textContent = d.auto_processed || 0;
  
  const fpr = d.false_positive_rate || 0;
  $('statsAccuracy').textContent = (100 - fpr).toFixed(1) + '%';
  $('statsLatency').textContent = '185 ms';

  // Update distribution chart
  const types = d.by_type || {};
  const rl = types.RED_LIGHT_VIOLATION || 0;
  const sl = types.STOP_LINE_VIOLATION || 0;
  const ws = types.WRONG_SIDE_DRIVING || 0;
  const pk = types.ILLEGAL_PARKING || 0;

  distributionChart.data.datasets[0].data = [rl, sl, ws, pk];
  distributionChart.update();
}

function updateTrendChart(tsData) {
  if (!tsData || tsData.length === 0) return;
  trendChart.data.labels = tsData.map(t => t.hour);
  trendChart.data.datasets[0].data = tsData.map(t => t.count);
  trendChart.update();
}

function renderLeaderboard(cameras) {
  const container = $('leaderboardList');
  if (!cameras || cameras.length === 0) {
    container.innerHTML = `<div class="empty-state"><p>No camera data found.</p></div>`;
    return;
  }

  // Sort descending by incident counts
  const sorted = [...cameras].sort((a, b) => b.count - a.count);

  container.innerHTML = sorted.map((cam, idx) => {
    let badgeClass = 'rn';
    if (idx === 0) badgeClass = 'r1';
    else if (idx === 1) badgeClass = 'r2';
    else if (idx === 2) badgeClass = 'r3';

    return `
      <div class="leaderboard-item">
        <div class="rank-badge ${badgeClass}">${idx + 1}</div>
        <div class="leaderboard-info">
          <div style="font-size:0.83rem;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${cam.name}</div>
          <div style="font-size:0.71rem;color:var(--text-muted);">${cam.camera_id}</div>
        </div>
        <div style="font-size:0.85rem;font-weight:800;color:var(--cyan);">${cam.count}</div>
      </div>
    `;
  }).join('');
}
