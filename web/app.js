const viewer = document.getElementById('viewer');
const plane = document.getElementById('plane');
const clip = document.getElementById('clip');
const divider = document.getElementById('divider');
const base = document.getElementById('base');
const detail = document.getElementById('detail');
const monthSelect = document.getElementById('month');
const statusEl = document.getElementById('status');
let split = 0.5;
let scale = 1;
let tx = 0, ty = 0;
let dragging = false, lastX = 0, lastY = 0;
let splitDragging = false;

function applyTransform(){ plane.style.transform = `translate(${tx}px,${ty}px) scale(${scale})`; }
function applySplit(){ const pct = `${split * 100}%`; clip.style.width = pct; divider.style.left = pct; const w = viewer.clientWidth; detail.style.width = `${w}px`; }
function reset(){ split=.5; scale=1; tx=0; ty=0; applyTransform(); applySplit(); }

function loadEntry(entry){
  base.src = entry.native;
  detail.src = entry.superres;
  statusEl.textContent = `${entry.month} · clear composite ${(entry.valid_fraction*100).toFixed(1)}%`;
  reset();
}

fetch('data/superres25/summary.json', {cache:'no-store'})
  .then(r => { if(!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
  .then(data => {
    data.entries.slice().reverse().forEach((e) => {
      const opt = document.createElement('option'); opt.value = e.month; opt.textContent = e.month; monthSelect.appendChild(opt);
    });
    monthSelect.addEventListener('change', () => loadEntry(data.entries.find(e => e.month === monthSelect.value)));
    monthSelect.value = data.entries[data.entries.length-1].month;
    loadEntry(data.entries[data.entries.length-1]);
  }).catch(err => statusEl.textContent = `โหลดข้อมูลไม่สำเร็จ: ${err.message}`);

document.getElementById('reset').addEventListener('click', reset);
viewer.addEventListener('wheel', (e) => { e.preventDefault(); const factor = e.deltaY < 0 ? 1.15 : 1/1.15; scale = Math.min(6, Math.max(1, scale*factor)); applyTransform(); }, {passive:false});
viewer.addEventListener('pointerdown', e => {
  const x = e.offsetX / viewer.clientWidth;
  if (Math.abs(x - split) < 0.04 && scale === 1) splitDragging = true; else dragging = true;
  lastX=e.clientX; lastY=e.clientY; viewer.setPointerCapture(e.pointerId);
});
viewer.addEventListener('pointermove', e => {
  if(splitDragging){ split = Math.max(0.02, Math.min(.98, e.offsetX/viewer.clientWidth)); applySplit(); return; }
  if(dragging && scale>1){ tx += e.clientX-lastX; ty += e.clientY-lastY; lastX=e.clientX; lastY=e.clientY; applyTransform(); }
});
viewer.addEventListener('pointerup', e => { dragging=false; splitDragging=false; viewer.releasePointerCapture(e.pointerId); });
window.addEventListener('resize', applySplit);
