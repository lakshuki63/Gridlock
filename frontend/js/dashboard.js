/**
 * VisionEnforce — Dashboard JS
 * Handles: WebSocket live feed, KPI polling, camera status,
 *          donut chart, demo controls, toasts, modal
 */

'use strict';

/* ─── Constants ─── */
const API = 'http://localhost:8000';
const WS_URL = 'ws://localhost:8000/ws/live';
const MAX_FEED_CARDS = 60;
const OFFICER_ID = (() => {
  let id = localStorage.getItem('officerId') || 'OFF-001';
  localStorage.setItem('officerId', id);
  return id;
})();

/* ─── State ─── */
let ws = null;
let wsReconnectDelay = 1000;
let wsReconnectTimer = null;
let donutChart = null;
let feedCards = [];
let currentFeedFilter = '';
let selectedViolation = null;

const violationTypeMeta = {
  RED_LIGHT_VIOLATION:  { label: 'Red Light Violation',  badgeClass: 'badge-red',    severity: 'critical', color: '#ef4444' },
  STOP_LINE_VIOLATION:  { label: 'Stop Line Violation',  badgeClass: 'badge-orange', severity: 'high',     color: '#f97316' },
  WRONG_SIDE_DRIVING:   { label: 'Wrong Side Driving',   badgeClass: 'badge-red',    severity: 'critical', color: '#dc2626' },
  ILLEGAL_PARKING:      { label: 'Illegal Parking',      badgeClass: 'badge-yellow', severity: 'medium',   color: '#eab308' },
};

const vehicleIconMap = {
  motorcycle: 'fa-motorcycle',
  car:        'fa-car',
  truck:      'fa-truck',
  bus:        'fa-bus',
  auto:       'fa-taxi',
  default:    'fa-car',
};

/* ─── Mock fallback data ─── */
const MOCK_SUMMARY = {
  total_today: 847,
  pending_review: 63,
  auto_processed: 712,
  active_cameras: 18,
  false_positive_rate: 3.2,
  detections_per_hour: 42,
  avg_latency_ms: 187,
  fps: 24,
};

const MOCK_CAMERAS = [
  { id: 'CAM-KR-01', name: 'KR Circle — North',     status: 'online',  violations_today: 124 },
  { id: 'CAM-KR-02', name: 'KR Circle — South',     status: 'online',  violations_today: 97  },
  { id: 'CAM-MG-01', name: 'MG Road — Brigade',     status: 'online',  violations_today: 83  },
  { id: 'CAM-SL-01', name: 'Silk Board — East',     status: 'warning', violations_today: 71  },
  { id: 'CAM-HL-01', name: 'Hebbal — Flyover',      status: 'online',  violations_today: 66  },
  { id: 'CAM-JP-01', name: 'Jayanagar — 4th Block', status: 'online',  violations_today: 58  },
  { id: 'CAM-EL-01', name: 'Electronic City — Toll',status: 'offline', violations_today: 0   },
  { id: 'CAM-WH-01', name: 'Whitefield — ITPL',     status: 'online',  violations_today: 49  },
];

const MOCK_VIOLATIONS = [
  { id:'EVD-2026-BLR-00891', camera_id:'CAM-KR-01', camera_name:'KR Circle — North', violation_type:'RED_LIGHT_VIOLATION',  vehicle_class:'motorcycle', license_plate:'KA05AB1234', plate_confidence:0.94, detection_confidence:0.89, violation_confidence:0.91, timestamp:'2026-06-17T22:54:36', review_status:'PENDING_HUMAN', severity:'CRITICAL', location:{landmark:'KR Circle'} },
  { id:'EVD-2026-BLR-00890', camera_id:'CAM-MG-01', camera_name:'MG Road — Brigade',  violation_type:'STOP_LINE_VIOLATION', vehicle_class:'car',        license_plate:'KA01CD5678', plate_confidence:0.88, detection_confidence:0.92, violation_confidence:0.85, timestamp:'2026-06-17T22:53:12', review_status:'PENDING_HUMAN', severity:'HIGH',     location:{landmark:'MG Road'} },
  { id:'EVD-2026-BLR-00889', camera_id:'CAM-SL-01', camera_name:'Silk Board — East',  violation_type:'WRONG_SIDE_DRIVING',  vehicle_class:'truck',      license_plate:'KA53EF9012', plate_confidence:0.79, detection_confidence:0.96, violation_confidence:0.93, timestamp:'2026-06-17T22:51:55', review_status:'AUTO_APPROVED',  severity:'CRITICAL', location:{landmark:'Silk Board'} },
  { id:'EVD-2026-BLR-00888', camera_id:'CAM-JP-01', camera_name:'Jayanagar 4th Block', violation_type:'ILLEGAL_PARKING',    vehicle_class:'car',        license_plate:'KA04GH3456', plate_confidence:0.91, detection_confidence:0.87, violation_confidence:0.82, timestamp:'2026-06-17T22:50:30', review_status:'PENDING_HUMAN', severity:'MEDIUM',   location:{landmark:'Jayanagar'} },
  { id:'EVD-2026-BLR-00887', camera_id:'CAM-HL-01', camera_name:'Hebbal — Flyover',    violation_type:'RED_LIGHT_VIOLATION', vehicle_class:'bus',        license_plate:'KA02IJ7890', plate_confidence:0.85, detection_confidence:0.90, violation_confidence:0.88, timestamp:'2026-06-17T22:49:18', review_status:'REJECTED',       severity:'CRITICAL', location:{landmark:'Hebbal'} },
];

const MOCK_DISTRIBUTION = { RED_LIGHT_VIOLATION: 312, STOP_LINE_VIOLATION: 198, WRONG_SIDE_DRIVING: 156, ILLEGAL_PARKING: 181 };

/* ─── DOM refs ─── */
const $ = id => document.getElementById(id);
const violationFeed = $('violationFeed');

/* ─── Init ─── */
document.addEventListener('DOMContentLoaded', () => {
  initOfficer();
  initKPIs();
  initCameras();
  initDonutChart();
  connectWebSocket();
  initControls();
  loadInitialFeed();
});

/* ─── Officer ─── */
function initOfficer() {
  $('officerLabel').textContent = OFFICER_ID;
  const initials = OFFICER_ID.replace(/[^A-Z0-9]/gi,'').slice(0,2).toUpperCase();
  $('officerInitials').textContent = initials || 'OF';
}

/* ─── KPI ─── */
async function initKPIs() {
  try {
    const res = await fetch(`${API}/api/analytics/summary`, { signal: AbortSignal.timeout(4000) });
    if (!res.ok) throw new Error();
    const d = await res.json();
    applyKPIs(d);
  } catch {
    applyKPIs(MOCK_SUMMARY);
  }
  setInterval(refreshKPIs, 30000);
}

async function refreshKPIs() {
  try {
    const res = await fetch(`${API}/api/analytics/summary`, { signal: AbortSignal.timeout(4000) });
    if (!res.ok) throw new Error();
    applyKPIs(await res.json());
  } catch { /* keep current */ }
}

function applyKPIs(d) {
  animateNumber($('kpiTotal'),    d.total_today        || 0);
  animateNumber($('kpiPending'),  d.pending_review     || 0);
  animateNumber($('kpiAuto'),     d.auto_processed     || 0);
  animateNumber($('kpiCameras'),  d.active_cameras     || 0);
  $('kpiFPR').textContent = (d.false_positive_rate || 0).toFixed(1) + '%';
  $('kpiTotalDelta').innerHTML   = `<i class="fas fa-arrow-up"></i> +${d.detections_per_hour||0}/hr today`;
  $('kpiAutoDelta').innerHTML    = `<i class="fas fa-robot"></i> ${Math.round((d.auto_processed||0)/(d.total_today||1)*100)||84}% auto-rate`;
  $('kpiCamerasDelta').innerHTML = `<i class="fas fa-wifi"></i> ${d.active_cameras||0} online`;
  $('kpiFPRDelta').innerHTML     = `<i class="fas fa-arrow-down"></i> -0.4% from yesterday`;
  $('fpsStat').textContent  = (d.fps || 24) + ' fps';
  $('latStat').textContent  = (d.avg_latency_ms || 187) + ' ms';
}

function animateNumber(el, target) {
  if (!el) return;
  const start = parseInt(el.textContent) || 0;
  const diff  = target - start;
  const steps = 24;
  let i = 0;
  const t = setInterval(() => {
    i++;
    el.textContent = Math.round(start + diff * easeOut(i / steps));
    if (i >= steps) clearInterval(t);
  }, 20);
}

function easeOut(t) { return 1 - Math.pow(1 - t, 3); }

/* ─── Cameras ─── */
async function initCameras() {
  try {
    const res = await fetch(`${API}/api/cameras`, { signal: AbortSignal.timeout(4000) });
    if (!res.ok) throw new Error();
    renderCameras(await res.json());
  } catch {
    renderCameras(MOCK_CAMERAS);
  }
  setInterval(async () => {
    try {
      const res = await fetch(`${API}/api/cameras`, { signal: AbortSignal.timeout(4000) });
      if (res.ok) renderCameras(await res.json());
    } catch { }
  }, 15000);
}

function renderCameras(cameras) {
  const list = $('cameraList');
  const online = cameras.filter(c => c.status === 'online').length;
  $('camOnlineCount').textContent = `${online}/${cameras.length} online`;
  $('camOnlineCount').className   = `badge ${online === cameras.length ? 'badge-green' : online > cameras.length/2 ? 'badge-yellow' : 'badge-red'}`;

  list.innerHTML = cameras.map(c => `
    <div class="camera-item">
      <div class="cam-status-dot ${c.status}"></div>
      <div class="cam-info">
        <div class="cam-name">${c.name}</div>
        <div class="cam-detail">${c.id} · ${c.status}</div>
      </div>
      <div class="cam-count">${c.violations_today}</div>
    </div>
  `).join('');
}

/* ─── Donut Chart ─── */
function initDonutChart() {
  const ctx = $('donutChart').getContext('2d');
  donutChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels: ['Red Light', 'Stop Line', 'Wrong Side', 'Parking'],
      datasets: [{
        data: [0, 0, 0, 0],
        backgroundColor: ['#ef4444', '#f97316', '#dc2626', '#eab308'],
        borderColor: 'transparent',
        borderWidth: 0,
        hoverOffset: 6,
      }]
    },
    options: {
      responsive: false,
      cutout: '72%',
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#0d1426',
          borderColor: 'rgba(255,255,255,0.08)',
          borderWidth: 1,
          padding: 10,
          titleColor: '#f1f5f9',
          bodyColor: '#94a3b8',
          callbacks: {
            label: ctx => ` ${ctx.label}: ${ctx.parsed} violations`
          }
        }
      },
      animation: { duration: 700, easing: 'easeOutQuart' }
    }
  });
  loadDistribution();
  setInterval(loadDistribution, 60000);
}

async function loadDistribution() {
  let dist = MOCK_DISTRIBUTION;
  try {
    const res = await fetch(`${API}/api/analytics/summary`, { signal: AbortSignal.timeout(4000) });
    if (res.ok) {
      const d = await res.json();
      if (d.by_type) dist = d.by_type;
    }
  } catch { }
  updateDonut(dist);
}

function updateDonut(dist) {
  const rl = dist.RED_LIGHT_VIOLATION || 0;
  const sl = dist.STOP_LINE_VIOLATION || 0;
  const ws = dist.WRONG_SIDE_DRIVING  || 0;
  const pk = dist.ILLEGAL_PARKING     || 0;
  donutChart.data.datasets[0].data = [rl, sl, ws, pk];
  donutChart.update();
  $('leg-rl').textContent = rl;
  $('leg-sl').textContent = sl;
  $('leg-ws').textContent = ws;
  $('leg-pk').textContent = pk;
}

/* ─── Initial Feed Load ─── */
async function loadInitialFeed() {
  try {
    const res = await fetch(`${API}/api/violations?page=1&limit=12`, { signal: AbortSignal.timeout(5000) });
    if (!res.ok) throw new Error();
    const data = await res.json();
    const items = data.items || data;
    violationFeed.innerHTML = '';
    (Array.isArray(items) ? items : MOCK_VIOLATIONS).forEach(v => prependViolationCard(v, false));
  } catch {
    violationFeed.innerHTML = '';
    MOCK_VIOLATIONS.forEach(v => prependViolationCard(v, false));
  }
}

/* ─── WebSocket ─── */
function connectWebSocket() {
  setWsState('connecting');
  try {
    ws = new WebSocket(WS_URL);
  } catch {
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    setWsState('connected');
    wsReconnectDelay = 1000;
    showToast('Live feed connected', 'Receiving real-time violation events', 'info');
  };

  ws.onmessage = e => {
    try {
      const msg = JSON.parse(e.data);
      handleWsMessage(msg);
    } catch { }
  };

  ws.onclose = () => {
    setWsState('disconnected');
    scheduleReconnect();
  };

  ws.onerror = () => {
    setWsState('disconnected');
  };
}

function scheduleReconnect() {
  clearTimeout(wsReconnectTimer);
  wsReconnectTimer = setTimeout(() => {
    connectWebSocket();
    wsReconnectDelay = Math.min(wsReconnectDelay * 2, 30000);
  }, wsReconnectDelay);
}

function setWsState(state) {
  const ind = $('wsIndicator');
  const txt = $('wsStatus');
  ind.className = `ws-indicator ${state}`;
  const labels = { connecting: 'Connecting…', connected: 'Live', disconnected: 'Offline' };
  txt.textContent = labels[state] || state;
}

function handleWsMessage(msg) {
  switch (msg.type) {
    case 'violation_event':
      handleNewViolation(msg.data);
      break;
    case 'stats_update':
      if (msg.data) applyKPIs(msg.data);
      break;
    case 'camera_status':
      if (msg.data) updateCameraStatus(msg.data);
      break;
    case 'processing_progress':
      handleProgress(msg.data);
      break;
  }
}

function handleNewViolation(v) {
  if (currentFeedFilter && v.violation_type !== currentFeedFilter) return;
  prependViolationCard(v, true);
  const meta = violationTypeMeta[v.violation_type] || {};
  if (meta.severity === 'critical') {
    showToast(
      `🚨 ${meta.label}`,
      `${v.license_plate} · ${v.camera_name} · ${v.location?.landmark || ''}`,
      'critical'
    );
  } else if (meta.severity === 'high') {
    showToast(`⚠️ ${meta.label}`, `${v.license_plate} at ${v.camera_name}`, 'high');
  }
  // Update KPI
  const kpiEl = $('kpiTotal');
  if (kpiEl) {
    const cur = parseInt(kpiEl.textContent) || 0;
    animateNumber(kpiEl, cur + 1);
  }
  // Update donut
  const meta2 = violationTypeMeta[v.violation_type];
  if (meta2) {
    const idx = ['RED_LIGHT_VIOLATION','STOP_LINE_VIOLATION','WRONG_SIDE_DRIVING','ILLEGAL_PARKING'].indexOf(v.violation_type);
    if (idx >= 0) {
      donutChart.data.datasets[0].data[idx]++;
      donutChart.update('none');
      const legIds = ['leg-rl','leg-sl','leg-ws','leg-pk'];
      const legEl = $(legIds[idx]);
      if (legEl) legEl.textContent = donutChart.data.datasets[0].data[idx];
    }
  }
}

function updateCameraStatus(data) {
  // Re-fetch cameras on status change
  initCameras();
}

function handleProgress(data) {
  if (!data) return;
  const wrap = $('proc-progress-wrap');
  const bar  = $('procProgress');
  const pct  = $('procPct');
  const stat = $('procStatus');
  if (data.status === 'processing') {
    wrap.style.display = 'flex';
    stat.textContent = 'Processing';
    const p = Math.round((data.processed / data.total) * 100) || 0;
    bar.style.width = p + '%';
    pct.textContent = p + '%';
  } else if (data.status === 'complete') {
    stat.textContent = 'Complete';
    bar.style.width = '100%';
    pct.textContent = '100%';
    setTimeout(() => { wrap.style.display = 'none'; stat.textContent = 'Idle'; }, 3000);
    showToast('Processing Complete', `${data.processed || 0} frames analyzed`, 'success');
  } else if (data.status === 'idle') {
    wrap.style.display = 'none';
    stat.textContent = 'Idle';
  }
}

/* ─── Violation Card ─── */
function prependViolationCard(v, animate = true) {
  const meta = violationTypeMeta[v.violation_type] || { label: v.violation_type, badgeClass: 'badge-gray', severity: 'medium', color: '#64748b' };
  const icon = vehicleIconMap[v.vehicle_class?.toLowerCase()] || vehicleIconMap.default;
  const conf = Math.round((v.violation_confidence || 0) * 100);
  const confClass = conf >= 85 ? 'high' : conf >= 65 ? 'medium' : 'low';
  const ts = formatTime(v.timestamp);

  const card = document.createElement('div');
  card.className = `violation-card severity-${meta.severity}`;
  if (!animate) card.style.animation = 'none';
  card.dataset.id = v.id;

  card.innerHTML = `
    <div class="violation-card-header">
      <div style="display:flex;align-items:center;gap:8px;">
        <span class="badge ${meta.badgeClass}">
          <i class="fas fa-circle" style="font-size:5px;"></i> ${meta.label}
        </span>
      </div>
      <div class="violation-id">${v.id}</div>
    </div>
    <div class="violation-card-meta">
      <span><i class="fas ${icon}"></i> ${capitalize(v.vehicle_class || 'Unknown')}</span>
      <span class="plate-badge">${v.license_plate || 'N/A'}</span>
      <span><i class="fas fa-camera"></i> ${v.camera_name || v.camera_id}</span>
      <span style="margin-left:auto;color:var(--text-muted);font-size:0.72rem;"><i class="fas fa-clock"></i> ${ts}</span>
    </div>
    <div style="margin-bottom:2px;">
      <div style="display:flex;justify-content:space-between;margin-bottom:4px;font-size:0.72rem;color:var(--text-muted);">
        <span>Confidence</span>
      </div>
      <div class="confidence-bar-wrap">
        <div class="confidence-bar-track">
          <div class="confidence-bar-fill ${confClass}" style="width:0%" data-target="${conf}"></div>
        </div>
        <span class="confidence-val">${conf}%</span>
      </div>
    </div>
    <div class="violation-card-footer">
      <div style="display:flex;align-items:center;gap:5px;font-size:0.72rem;color:var(--text-muted);">
        <i class="fas fa-map-pin"></i> ${v.location?.landmark || 'Bengaluru'}
      </div>
      <div style="display:flex;gap:6px;">
        <button class="btn btn-ghost btn-sm" onclick="openModal('${v.id}', event)"><i class="fas fa-eye"></i> Review</button>
        <button class="btn btn-success btn-sm" onclick="quickApprove('${v.id}', event)"><i class="fas fa-check"></i> Approve</button>
      </div>
    </div>
  `;

  card.addEventListener('click', () => openModal(v.id));
  // Store violation data on card
  card._violation = v;

  // Prepend to feed
  if (violationFeed.firstChild) {
    violationFeed.insertBefore(card, violationFeed.firstChild);
  } else {
    violationFeed.appendChild(card);
  }

  // Animate confidence bar
  requestAnimationFrame(() => {
    setTimeout(() => {
      const fill = card.querySelector('.confidence-bar-fill');
      if (fill) fill.style.width = fill.dataset.target + '%';
    }, 50);
  });

  // Trim excess
  feedCards.unshift(card);
  while (feedCards.length > MAX_FEED_CARDS) {
    const old = feedCards.pop();
    old?.remove();
  }
}

/* ─── Quick Approve ─── */
async function quickApprove(violationId, evt) {
  if (evt) evt.stopPropagation();
  try {
    await fetch(`${API}/api/violations/${violationId}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'APPROVE', officer_id: OFFICER_ID, notes: 'Quick approved from dashboard' }),
      signal: AbortSignal.timeout(5000),
    });
  } catch { }
  // Update card UI
  const card = violationFeed.querySelector(`[data-id="${violationId}"]`);
  if (card) {
    card.style.opacity = '0.5';
    card.style.pointerEvents = 'none';
    const footer = card.querySelector('.violation-card-footer');
    if (footer) {
      footer.innerHTML = `<span style="font-size:0.78rem;color:var(--green);"><i class="fas fa-check-circle"></i> Approved by ${OFFICER_ID}</span>`;
    }
  }
  showToast('Violation Approved', `${violationId} marked as approved`, 'success');
}

/* ─── Modal ─── */
function openModal(violationId, evt) {
  if (evt) evt.stopPropagation();
  // Find the violation data
  const card = violationFeed.querySelector(`[data-id="${violationId}"]`);
  const v = card?._violation || MOCK_VIOLATIONS.find(m => m.id === violationId);
  if (!v) return;

  selectedViolation = v;
  const meta = violationTypeMeta[v.violation_type] || {};

  $('modalTitle').textContent = meta.label || v.violation_type;
  $('modalId').textContent    = v.id;
  $('mdType').textContent     = meta.label || v.violation_type;
  $('mdSeverity').innerHTML   = `<span class="badge ${meta.badgeClass || 'badge-gray'}">${v.severity || meta.severity?.toUpperCase()}</span>`;
  $('mdPlate').textContent    = v.license_plate || '—';
  $('mdVehicle').textContent  = capitalize(v.vehicle_class || '—');
  $('mdCamera').textContent   = v.camera_name || v.camera_id;
  $('mdTime').textContent     = formatFullTime(v.timestamp);
  $('mdLocation').textContent = v.location?.landmark || 'Bengaluru';
  $('mdReviewStatus').innerHTML = statusBadge(v.review_status);

  const dc = Math.round((v.detection_confidence || 0) * 100);
  const vc = Math.round((v.violation_confidence  || 0) * 100);
  const pc = Math.round((v.plate_confidence      || 0) * 100);

  $('mdDetConf').textContent  = dc + '%';
  $('mdViolConf').textContent = vc + '%';
  $('mdPlateConf').textContent= pc + '%';

  ['mdDetConfBar','mdViolConfBar','mdPlateConfBar'].forEach(id => {
    const el = $(id); if (el) { el.style.width = '0%'; el.className = `confidence-bar-fill ${confClass2(parseInt(el.nextElementSibling?.textContent) || 0)}`; }
  });

  setTimeout(() => {
    setBarWidth('mdDetConfBar', dc);
    setBarWidth('mdViolConfBar', vc);
    setBarWidth('mdPlateConfBar', pc);
  }, 60);

  $('violationModal').classList.remove('hidden');
}

function setBarWidth(id, pct) {
  const el = $(id);
  if (!el) return;
  const cls = pct >= 85 ? 'high' : pct >= 65 ? 'medium' : 'low';
  el.className = `confidence-bar-fill ${cls}`;
  el.style.width = pct + '%';
}

function confClass2(pct) { return pct >= 85 ? 'high' : pct >= 65 ? 'medium' : 'low'; }

function statusBadge(s) {
  const map = {
    PENDING_HUMAN: ['badge-orange', 'Pending Review'],
    AUTO_APPROVED: ['badge-green',  'Auto-Approved'],
    APPROVED:      ['badge-green',  'Approved'],
    REJECTED:      ['badge-red',    'Rejected'],
    FLAGGED:       ['badge-yellow', 'Flagged'],
  };
  const [cls, lbl] = map[s] || ['badge-gray', s || 'Unknown'];
  return `<span class="badge ${cls}">${lbl}</span>`;
}

/* ─── Controls ─── */
function initControls() {
  $('modalClose').addEventListener('click', () => $('violationModal').classList.add('hidden'));
  $('violationModal').addEventListener('click', e => { if (e.target === $('violationModal')) $('violationModal').classList.add('hidden'); });

  $('mdApproveBtn').addEventListener('click', async () => {
    if (!selectedViolation) return;
    await quickApprove(selectedViolation.id, null);
    $('violationModal').classList.add('hidden');
  });

  $('mdRejectBtn').addEventListener('click', async () => {
    if (!selectedViolation) return;
    try {
      await fetch(`${API}/api/violations/${selectedViolation.id}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'REJECT', officer_id: OFFICER_ID, notes: 'Rejected from dashboard' }),
        signal: AbortSignal.timeout(5000),
      });
    } catch { }
    showToast('Violation Rejected', selectedViolation.id, 'high');
    $('violationModal').classList.add('hidden');
  });

  $('clearFeedBtn').addEventListener('click', () => {
    violationFeed.innerHTML = '<div class="empty-state"><i class="fas fa-satellite-dish"></i><p>Feed cleared — waiting for new events…</p></div>';
    feedCards = [];
  });

  $('feedFilter').addEventListener('change', e => {
    currentFeedFilter = e.target.value;
  });

  $('refreshChartBtn').addEventListener('click', loadDistribution);

  $('startDemoBtn').addEventListener('click', startDemo);
  $('demoStatusBtn').addEventListener('click', checkDemoStatus);
}

async function startDemo() {
  const btn = $('startDemoBtn');
  btn.disabled = true;
  btn.innerHTML = '<div class="spinner" style="width:14px;height:14px;border-width:2px;"></div> Starting…';
  try {
    const res = await fetch(`${API}/api/demo/start`, { method: 'POST', signal: AbortSignal.timeout(8000) });
    if (res.ok) {
      showToast('Demo Started', 'Processing demo footage — violations will stream live', 'success');
      $('procStatus').textContent = 'Processing';
      $('proc-progress-wrap').style.display = 'flex';
    } else {
      throw new Error();
    }
  } catch {
    showToast('Demo Mode', 'Backend offline — showing simulated live data', 'info');
    simulateLiveFeed();
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<i class="fas fa-play"></i> Start Demo';
  }
}

async function checkDemoStatus() {
  try {
    const res = await fetch(`${API}/api/demo/status`, { signal: AbortSignal.timeout(4000) });
    if (res.ok) {
      const d = await res.json();
      showToast('Demo Status', `Status: ${d.status || 'unknown'} — ${d.processed || 0} frames processed`, 'info');
    }
  } catch {
    showToast('Status Check', 'Backend is not reachable', 'high');
  }
}

/* ─── Simulate Live Feed (offline mode) ─── */
const SIMULATED_VIOLATIONS = [
  { id:'EVD-SIM-001', camera_id:'CAM-KR-01', camera_name:'KR Circle — North', violation_type:'RED_LIGHT_VIOLATION', vehicle_class:'motorcycle', license_plate:'KA05MN2233', plate_confidence:0.92, detection_confidence:0.88, violation_confidence:0.90, severity:'CRITICAL', location:{landmark:'KR Circle'} },
  { id:'EVD-SIM-002', camera_id:'CAM-MG-01', camera_name:'MG Road — Brigade', violation_type:'STOP_LINE_VIOLATION', vehicle_class:'car', license_plate:'KA01RS4455', plate_confidence:0.86, detection_confidence:0.91, violation_confidence:0.84, severity:'HIGH', location:{landmark:'MG Road'} },
  { id:'EVD-SIM-003', camera_id:'CAM-HL-01', camera_name:'Hebbal — Flyover', violation_type:'WRONG_SIDE_DRIVING', vehicle_class:'truck', license_plate:'KA53TU6677', plate_confidence:0.78, detection_confidence:0.95, violation_confidence:0.92, severity:'CRITICAL', location:{landmark:'Hebbal'} },
  { id:'EVD-SIM-004', camera_id:'CAM-JP-01', camera_name:'Jayanagar 4th Block', violation_type:'ILLEGAL_PARKING', vehicle_class:'car', license_plate:'KA04VW8899', plate_confidence:0.89, detection_confidence:0.85, violation_confidence:0.80, severity:'MEDIUM', location:{landmark:'Jayanagar'} },
  { id:'EVD-SIM-005', camera_id:'CAM-WH-01', camera_name:'Whitefield — ITPL', violation_type:'RED_LIGHT_VIOLATION', vehicle_class:'bus', license_plate:'KA02XY0011', plate_confidence:0.84, detection_confidence:0.89, violation_confidence:0.87, severity:'CRITICAL', location:{landmark:'Whitefield'} },
];

let simIdx = 0;
function simulateLiveFeed() {
  const interval = setInterval(() => {
    const base = SIMULATED_VIOLATIONS[simIdx % SIMULATED_VIOLATIONS.length];
    const v = {
      ...base,
      id: `EVD-SIM-${Date.now()}`,
      timestamp: new Date().toISOString(),
      review_status: 'PENDING_HUMAN',
    };
    handleNewViolation(v);
    simIdx++;
    if (simIdx >= 20) clearInterval(interval);
  }, 1800);
}

/* ─── Toasts ─── */
function showToast(title, msg, type = 'info', duration = 5000) {
  const container = $('toastContainer');
  const iconMap = { critical: 'fa-circle-exclamation', high: 'fa-triangle-exclamation', medium: 'fa-info-circle', info: 'fa-circle-info', success: 'fa-circle-check' };
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <i class="fas ${iconMap[type] || 'fa-circle-info'} toast-icon"></i>
    <div class="toast-body">
      <div class="toast-title">${title}</div>
      <div class="toast-msg">${msg}</div>
    </div>
    <i class="fas fa-xmark toast-close"></i>
  `;
  toast.querySelector('.toast-close').addEventListener('click', () => removeToast(toast));
  container.appendChild(toast);
  setTimeout(() => removeToast(toast), duration);
  // Keep max 5 toasts
  const toasts = container.querySelectorAll('.toast:not(.removing)');
  if (toasts.length > 5) removeToast(toasts[0]);
}

function removeToast(toast) {
  if (toast.classList.contains('removing')) return;
  toast.classList.add('removing');
  setTimeout(() => toast.remove(), 320);
}

/* ─── Helpers ─── */
function formatTime(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  } catch { return ts; }
}

function formatFullTime(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleString('en-IN', { day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit', hour12: false });
  } catch { return ts; }
}

function capitalize(s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ''; }

// Expose globals needed by inline handlers
window.openModal    = openModal;
window.quickApprove = quickApprove;
