/**
 * VisionEnforce — Review Console JS
 * Handles: Paginated log fetching, filters, modal reviewer,
 *          RTO regex plate validation, video player loading,
 *          and post-review updates.
 */

'use strict';

const API = 'http://localhost:8000';
const WS_URL = 'ws://localhost:8000/ws/live';

// State
let ws = null;
let currentPage = 1;
let itemsLimit = 12;
let totalItems = 0;
let currentItems = [];
let selectedViolation = null;

const OFFICER_ID = localStorage.getItem('officerId') || 'OFF-001';

const violationTypeMeta = {
  RED_LIGHT_VIOLATION:  { label: 'Red Light Violation',  badgeClass: 'badge-red',    severity: 'critical' },
  STOP_LINE_VIOLATION:  { label: 'Stop Line Violation',  badgeClass: 'badge-orange', severity: 'high' },
  WRONG_SIDE_DRIVING:   { label: 'Wrong Side Driving',   badgeClass: 'badge-red',    severity: 'critical' },
  ILLEGAL_PARKING:      { label: 'Illegal Parking',      badgeClass: 'badge-yellow', severity: 'medium' },
};

const RTO_REGEX = /^([A-Z]{2})([0-9]{1,2})([A-Z]{1,3})([0-9]{4})$/;
const BH_REGEX = /^([0-9]{2})BH([0-9]{4})([A-Z]{2})$/;

// DOM
const $ = id => document.getElementById(id);
const reviewGrid = $('reviewGrid');

document.addEventListener('DOMContentLoaded', () => {
  initOfficer();
  connectWebSocket();
  bindEvents();
  fetchLogs();
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

function bindEvents() {
  $('btnApplyFilters').addEventListener('click', () => {
    currentPage = 1;
    fetchLogs();
  });
  
  $('btnRefresh').addEventListener('click', fetchLogs);
  
  $('btnPrevPage').addEventListener('click', () => {
    if (currentPage > 1) {
      currentPage--;
      fetchLogs();
    }
  });

  $('btnNextPage').addEventListener('click', () => {
    if (currentPage * itemsLimit < totalItems) {
      currentPage++;
      fetchLogs();
    }
  });

  $('modalClose').addEventListener('click', closeModal);
  $('btnModalClose').addEventListener('click', closeModal);
  $('reviewModal').addEventListener('click', e => { if (e.target === $('reviewModal')) closeModal(); });

  $('mdPlateInput').addEventListener('input', e => {
    validatePlateFormat(e.target.value.toUpperCase().replace(/\s/g, ''));
  });

  $('btnToggleMedia').addEventListener('click', loadVideoPlayer);

  $('btnApprove').addEventListener('click', () => submitReview('APPROVE'));
  $('btnReject').addEventListener('click', () => submitReview('REJECT'));
}

async function fetchLogs() {
  showSkeletons();
  
  const searchPlate = $('searchPlate').value.trim();
  const filterCamera = $('filterCamera').value;
  const filterType = $('filterType').value;
  const filterStatus = $('filterStatus').value;

  let url = `${API}/api/violations?page=${currentPage}&limit=${itemsLimit}`;
  if (filterCamera) url += `&camera_id=${filterCamera}`;
  if (filterType) url += `&violation_type=${filterType}`;
  if (filterStatus) url += `&status=${filterStatus}`;
  
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error();
    const data = await res.json();
    currentItems = data.items || [];
    totalItems = data.total || 0;
    renderGrid();
  } catch {
    reviewGrid.innerHTML = `
      <div class="empty-state w-full" style="grid-column: 1/-1;">
        <i class="fas fa-circle-exclamation text-red"></i>
        <p>Failed to connect to backend server. Ensure run_demo.py is active.</p>
      </div>
    `;
    updatePaginationControls(0);
  }
}

function showSkeletons() {
  reviewGrid.innerHTML = Array(6).fill(0).map(() => `
    <div class="evidence-card" style="cursor:default;">
      <div class="skeleton" style="height:120px;width:100%;"></div>
      <div class="evidence-body">
        <div class="skeleton" style="height:14px;width:50%;margin-bottom:6px;"></div>
        <div class="skeleton" style="height:10px;width:70%;margin-bottom:6px;"></div>
        <div class="skeleton" style="height:4px;width:100%;"></div>
      </div>
    </div>
  `).join('');
}

function renderGrid() {
  if (currentItems.length === 0) {
    reviewGrid.innerHTML = `
      <div class="empty-state w-full" style="grid-column: 1/-1; padding: 60px 0;">
        <i class="fas fa-folder-open"></i>
        <p>No traffic incidents match your filter criteria.</p>
      </div>
    `;
    updatePaginationControls(0);
    return;
  }

  reviewGrid.innerHTML = currentItems.map(v => {
    const meta = violationTypeMeta[v.violation_type] || { label: v.violation_type, badgeClass: 'badge-gray', severity: 'medium' };
    const conf = Math.round((v.violation_confidence || 0) * 100);
    const confClass = conf >= 85 ? 'high' : conf >= 65 ? 'medium' : 'low';
    
    const timeStr = formatTime(v.timestamp);
    const dateStr = formatDate(v.timestamp);

    // Frame preview fallback
    const thumbUrl = v.frame_url ? `${API}${v.frame_url}` : 'https://placehold.co/640x360/0d1426/94a3b8?text=Evidence+Frame';

    return `
      <div class="evidence-card" onclick="openReviewModal('${v.id}')">
        <div class="review-card-conf ${confClass}">${conf}% Conf</div>
        <div class="evidence-thumb">
          <img src="${thumbUrl}" alt="Evidence Frame" loading="lazy" onerror="this.src='https://placehold.co/640x360/0d1426/94a3b8?text=Image+Load+Error'" />
          <div class="thumb-overlay"></div>
        </div>
        <div class="evidence-body">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
            <span class="badge ${meta.badgeClass}">${meta.label}</span>
            <span style="font-size:0.69rem;color:var(--text-muted);font-family:monospace;">${v.id.split('-').pop()}</span>
          </div>
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">
            <span class="plate-badge">${v.license_plate || 'UNKNOWN'}</span>
            <span style="font-size:0.73rem;color:var(--text-secondary);"><i class="fas fa-camera"></i> ${v.camera_name || v.camera_id}</span>
          </div>
          <div style="display:flex;justify-content:space-between;font-size:0.71rem;color:var(--text-muted);">
            <span>${dateStr}</span>
            <span>${timeStr}</span>
          </div>
        </div>
      </div>
    `;
  }).join('');

  updatePaginationControls(currentItems.length);
}

function updatePaginationControls(renderedCount) {
  const start = (currentPage - 1) * itemsLimit + 1;
  const end = Math.min(currentPage * itemsLimit, totalItems);
  
  if (totalItems === 0) {
    $('paginationText').textContent = 'Showing 0-0 of 0 entries';
  } else {
    $('paginationText').textContent = `Showing ${start}-${end} of ${totalItems} entries`;
  }
  
  $('btnPrevPage').disabled = currentPage === 1;
  $('btnNextPage').disabled = currentPage * itemsLimit >= totalItems;
}

function openReviewModal(violationId) {
  const v = currentItems.find(item => item.id === violationId);
  if (!v) return;

  selectedViolation = v;
  const meta = violationTypeMeta[v.violation_type] || {};

  $('mdTitle').textContent = meta.label || v.violation_type;
  $('mdId').textContent = v.id;
  $('mdPlateInput').value = v.license_plate || '';
  $('mdPlateConfVal').textContent = Math.round((v.plate_confidence || 0) * 100) + '%';
  
  // Set media image
  $('mdMediaContainer').innerHTML = `<img src="${v.frame_url ? API + v.frame_url : 'https://placehold.co/640x360/0d1426/94a3b8?text=No+Frame'}" id="mdFrameImage" alt="Violation Frame" />`;
  
  // Configure clip load button
  const clipBtn = $('btnToggleMedia');
  if (v.clip_url) {
    clipBtn.disabled = false;
    clipBtn.textContent = 'Load Video Clip';
    clipBtn.className = 'btn btn-ghost btn-sm';
  } else {
    clipBtn.disabled = true;
    clipBtn.textContent = 'Video Clip Unavailable';
    clipBtn.className = 'btn btn-ghost btn-sm';
  }

  // Set attributes details
  $('mdVehicleClass').textContent = capitalize(v.vehicle_class || 'Unknown');
  $('mdCameraName').textContent = v.camera_name || v.camera_id;
  $('mdViolationType').textContent = meta.label || v.violation_type;
  $('mdTime').textContent = formatFullTime(v.timestamp);

  // Set conf breakdown bars
  const dc = Math.round((v.detection_confidence || 0) * 100);
  const vc = Math.round((v.violation_confidence || 0) * 100);
  
  $('mdDetConfVal').textContent = dc + '%';
  $('mdViolConfVal').textContent = vc + '%';

  setBar('mdDetConfBar', dc);
  setBar('mdViolConfBar', vc);

  $('mdNotes').value = v.officer_notes || '';
  $('mdProvenanceHash').textContent = v.provenance_hash || 'Provenance Verification Key: —';

  // Initial plate format check
  validatePlateFormat(v.license_plate || '');

  $('reviewModal').classList.remove('hidden');
}

function setBar(id, pct) {
  const el = $(id);
  if (!el) return;
  const cls = pct >= 85 ? 'high' : pct >= 65 ? 'medium' : 'low';
  el.className = `confidence-bar-fill ${cls}`;
  el.style.width = pct + '%';
}

function validatePlateFormat(plate) {
  const cleanPlate = plate.toUpperCase().replace(/\s/g, '');
  const badge = $('mdRTOStatus');
  
  if (RTO_REGEX.test(cleanPlate)) {
    badge.textContent = 'Valid RTO Format';
    badge.className = 'badge badge-green';
  } else if (BH_REGEX.test(cleanPlate)) {
    badge.textContent = 'Valid BH Plate';
    badge.className = 'badge badge-green';
  } else {
    badge.textContent = 'Non-Standard Format';
    badge.className = 'badge badge-orange';
  }
}

function loadVideoPlayer() {
  if (!selectedViolation || !selectedViolation.clip_url) return;
  
  const clipUrl = API + selectedViolation.clip_url;
  $('mdMediaContainer').innerHTML = `
    <video autoplay loop controls style="width:100%;height:100%;object-fit:contain;">
      <source src="${clipUrl}" type="video/mp4">
      Your browser does not support the video tag.
    </video>
  `;
  $('btnToggleMedia').disabled = true;
  $('btnToggleMedia').textContent = 'Clip Active';
}

async function submitReview(action) {
  if (!selectedViolation) return;
  
  const notes = $('mdNotes').value.trim();
  const correctedPlate = $('mdPlateInput').value.trim().toUpperCase();

  const reqBody = {
    action: action,
    officer_id: OFFICER_ID,
    notes: notes,
    license_plate: correctedPlate // Passed if backend accepts
  };

  try {
    const res = await fetch(`${API}/api/violations/${selectedViolation.id}/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reqBody)
    });
    
    if (!res.ok) throw new Error();
    
    showToast(
      action === 'APPROVE' ? 'Incidence Approved' : 'Incidence Dismissed',
      `Violation ${selectedViolation.id} has been recorded.`,
      action === 'APPROVE' ? 'success' : 'high'
    );
    
    closeModal();
    fetchLogs(); // refresh table
  } catch {
    showToast('Failed to Save', 'Failed to update review status on the server.', 'critical');
  }
}

function closeModal() {
  $('reviewModal').classList.add('hidden');
  selectedViolation = null;
}

// Helpers
function formatTime(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  } catch { return ts; }
}

function formatDate(ts) {
  if (!ts) return '—';
  try {
    const d = new Date(ts);
    return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
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

function showToast(title, msg, type = 'info') {
  const container = $('toastContainer');
  const iconMap = { critical: 'fa-circle-exclamation', high: 'fa-triangle-exclamation', info: 'fa-circle-info', success: 'fa-circle-check' };
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
  toast.querySelector('.toast-close').addEventListener('click', () => toast.remove());
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}
