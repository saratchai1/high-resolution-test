const viewer = document.getElementById('viewer');
const plane = document.getElementById('plane');
const base = document.getElementById('base');
const detail = document.getElementById('detail');
const divider = document.getElementById('divider');
const timeline = document.getElementById('timeline');
const swipe = document.getElementById('swipe');
const statusEl = document.getElementById('status');
const monthLabel = document.getElementById('monthLabel');
const validStat = document.getElementById('validStat');
const sceneStat = document.getElementById('sceneStat');
const fallbackStat = document.getElementById('fallbackStat');
const playBtn = document.getElementById('play');
const boundary = document.getElementById('boundary');
const boundaryPath = document.getElementById('boundaryPath');
const boundaryToggle = document.getElementById('boundaryToggle');
const kmzChip = document.getElementById('kmzChip');

const KMZ_PATH = 'M 80.4 181.6 L 61.2 238.4 L 137.9 266.6 L 137.9 266.2 L 132.8 260.3 L 63.1 236.6 L 78.5 187.2 L 78.3 190.7 L 65.9 235.1 L 70.1 238.3 L 140.4 261.2 L 157.6 212.7 L 184.9 127.0 L 186.2 127.4 L 159.7 213.3 L 146.7 254.5 L 139.9 266.5 L 138.6 270.9 L 109.4 293.6 L 107.9 300.2 L 113.1 313.6 L 174.3 424.8 L 181.4 426.2 L 265.4 380.9 L 331.2 345.8 L 332.7 347.5 L 266.2 383.3 L 177.8 429.8 L 194.4 459.0 L 191.4 460.2 L 173.0 425.6 L 112.1 314.1 L 106.4 302.0 L 107.4 293.4 L 137.2 270.5 L 137.6 268.3 L 114.8 280.8 L 100.0 291.2 L 44.8 329.2 L 61.6 348.0 L 107.2 379.6 L 134.0 414.0 L 169.6 470.0 L 330.0 414.0 L 440.4 321.6 L 456.4 313.2 L 460.4 283.2 L 455.2 282.4 L 334.4 346.8 L 274.8 234.4 L 122.8 316.0 L 116.4 310.4 L 179.2 274.0 L 242.8 227.6 L 260.4 203.2 L 260.0 196.4 L 226.0 159.6 L 218.4 139.2 L 180.8 125.2 L 186.0 112.8 L 147.2 89.2 L 134.8 59.6 L 118.4 51.6 L 80.4 181.6 Z M 97.2 147.2 L 116.0 152.0 L 120.4 155.6 L 131.2 158.4 L 130.4 164.8 L 95.2 154.0 L 97.2 147.2 Z';
boundaryPath.setAttribute('d', KMZ_PATH);

let entries = [];
let index = 0;
let split = 62;
let scale = 1;
let tx = 0;
let ty = 0;
let dragging = false;
let lastX = 0;
let lastY = 0;
let timer = null;

const prefersReducedMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function monthText(yyyyMm) {
  const [year, month] = yyyyMm.split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, 1));
  return new Intl.DateTimeFormat('th-TH', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(date);
}

function clampPan() {
  const w = viewer.clientWidth || 1;
  const h = viewer.clientHeight || w;
  const minX = -w * (scale - 1);
  const minY = -h * (scale - 1);
  tx = Math.max(minX, Math.min(0, tx));
  ty = Math.max(minY, Math.min(0, ty));
}

function applyTransform() {
  clampPan();
  plane.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
}

function setScale(nextScale, focalX = viewer.clientWidth / 2, focalY = viewer.clientHeight / 2) {
  const next = Math.max(1, Math.min(6, nextScale));
  if (next === scale) return;
  const worldX = (focalX - tx) / scale;
  const worldY = (focalY - ty) / scale;
  tx = focalX - worldX * next;
  ty = focalY - worldY * next;
  scale = next;
  applyTransform();
}

function applySplit() {
  split = Math.max(0, Math.min(100, Number(swipe.value)));
  detail.style.clipPath = `inset(0 ${100 - split}% 0 0)`;
  divider.style.left = `${split}%`;
}

function resetView() {
  scale = 1;
  tx = 0;
  ty = 0;
  swipe.value = '62';
  applySplit();
  applyTransform();
}

function stopAuto() {
  if (timer) window.clearInterval(timer);
  timer = null;
  playBtn.textContent = '▶ เล่น';
  playBtn.setAttribute('aria-pressed', 'false');
}

function startAuto() {
  if (prefersReducedMotion) {
    statusEl.textContent = `${statusEl.textContent} · ปิด autoplay ตาม Reduce Motion ของระบบ`;
    return;
  }
  stopAuto();
  playBtn.textContent = '❚❚ หยุด';
  playBtn.setAttribute('aria-pressed', 'true');
  timer = window.setInterval(() => {
    index = (index + 1) % entries.length;
    timeline.value = String(index);
    loadEntry(index, false);
  }, 1100);
}

function preloadAround(i) {
  [-1, 1].forEach((delta) => {
    const e = entries[i + delta];
    if (!e) return;
    [e.native, e.superres].forEach((src) => {
      const img = new Image();
      img.src = src;
    });
  });
}

function loadEntry(nextIndex, reset = false) {
  if (!entries.length) return;
  index = Math.max(0, Math.min(entries.length - 1, Number(nextIndex)));
  const entry = entries[index];
  timeline.value = String(index);
  timeline.setAttribute('aria-valuetext', monthText(entry.month));

  base.src = entry.native;
  detail.src = entry.superres;
  monthLabel.textContent = monthText(entry.month);

  const valid = Number(entry.valid_fraction || 0);
  const scenes = Array.isArray(entry.source_items) ? entry.source_items.length : 0;
  const fallback = Boolean(entry.fallback_window_used);
  validStat.textContent = `${(valid * 100).toFixed(1)}%`;
  sceneStat.textContent = `${scenes} scene${scenes === 1 ? '' : 's'}`;
  fallbackStat.textContent = fallback ? 'ใช้ภาพข้างเคียงเดือน' : 'ไม่ใช้';
  statusEl.textContent = `${entry.month} · clear ${(valid * 100).toFixed(1)}% · ${scenes} Sentinel-2 scenes${fallback ? ' · cloud fallback' : ''}`;

  if (reset) resetView();
  preloadAround(index);
}

function moveMonth(delta) {
  stopAuto();
  loadEntry(index + delta, false);
}

fetch('data/superres25/summary.json', { cache: 'no-store' })
  .then((response) => {
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  })
  .then((data) => {
    entries = Array.isArray(data.entries) ? data.entries : [];
    if (!entries.length) throw new Error('ไม่พบ monthly entries');
    timeline.min = '0';
    timeline.max = String(entries.length - 1);
    index = entries.length - 1;
    timeline.value = String(index);
    loadEntry(index, true);
  })
  .catch((error) => {
    statusEl.textContent = `โหลดข้อมูลไม่สำเร็จ: ${error.message}`;
  });

timeline.addEventListener('input', () => {
  stopAuto();
  loadEntry(Number(timeline.value), false);
});

swipe.addEventListener('input', applySplit);
document.getElementById('prev').addEventListener('click', () => moveMonth(-1));
document.getElementById('next').addEventListener('click', () => moveMonth(1));
playBtn.addEventListener('click', () => timer ? stopAuto() : startAuto());
document.getElementById('reset').addEventListener('click', resetView);
document.getElementById('zoomIn').addEventListener('click', () => setScale(scale * 1.3));
document.getElementById('zoomOut').addEventListener('click', () => setScale(scale / 1.3));

boundaryToggle.addEventListener('change', () => {
  const visible = boundaryToggle.checked;
  boundary.hidden = !visible;
  kmzChip.hidden = !visible;
});

viewer.addEventListener('wheel', (event) => {
  event.preventDefault();
  const rect = viewer.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  setScale(scale * (event.deltaY < 0 ? 1.15 : 1 / 1.15), x, y);
}, { passive: false });

viewer.addEventListener('pointerdown', (event) => {
  if (scale <= 1) return;
  dragging = true;
  lastX = event.clientX;
  lastY = event.clientY;
  viewer.setPointerCapture(event.pointerId);
});

viewer.addEventListener('pointermove', (event) => {
  if (!dragging) return;
  tx += event.clientX - lastX;
  ty += event.clientY - lastY;
  lastX = event.clientX;
  lastY = event.clientY;
  applyTransform();
});

function endDrag(event) {
  dragging = false;
  if (viewer.hasPointerCapture && viewer.hasPointerCapture(event.pointerId)) viewer.releasePointerCapture(event.pointerId);
}
viewer.addEventListener('pointerup', endDrag);
viewer.addEventListener('pointercancel', endDrag);

viewer.addEventListener('keydown', (event) => {
  if (event.key === 'ArrowLeft') moveMonth(-1);
  if (event.key === 'ArrowRight') moveMonth(1);
  if (event.key === '+' || event.key === '=') setScale(scale * 1.3);
  if (event.key === '-') setScale(scale / 1.3);
});

window.addEventListener('resize', applyTransform);
window.addEventListener('pagehide', stopAuto);
applySplit();
applyTransform();
