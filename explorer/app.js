"use strict";
/* Travel-time map viewer.
 * Everything is drawn from vector geometry on a canvas: roads, water and parks are
 * warped vertex-by-vertex by a smooth displacement field, while labels are placed
 * afterwards in screen space, so text is never rotated, sheared or resized.
 */
const $ = (id) => document.getElementById(id);
const cv = $("map"), ctx = cv.getContext("2d");
const S = {
  city: null, meta: null, vec: null, F: null, cells: null,
  t: 1, snap: 0, mode: "map", metric: "M",
  view: { cx: 0, cy: 0, k: 30 }, W: 0, H: 0, dpr: 1,
  curDx: null, curDy: null, maxDisp: 0, centres: null, raf: 0,
};
const COL = {
  bg: "#0a0f1c", water: "#0f2e47", waterway: "#1a5078", green: "#10261d",
  minor: "#27324d", tertiary: "#3a4766", secondary: "#6475a0", major: "#d9b25f",
  rail: "#6f5f9f", metro: "#2ec4b6", text: "#e3e9f7", halo: "rgba(10,15,28,.92)", ghost: "rgba(255,255,255,.16)",
};

/* ---------- loading ---------- */
async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + " " + r.status);
  return r.json();
}
function pack(list) { // list of flat [x,y,x,y..] arrays -> {pts, start, bbox}
  let total = 0;
  for (const a of list) total += a.length / 2;
  const pts = new Float32Array(total * 2), start = new Int32Array(list.length + 1), bbox = new Float32Array(list.length * 4);
  let p = 0;
  list.forEach((a, i) => {
    start[i] = p;
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (let j = 0; j < a.length; j += 2) {
      const x = a[j], y = a[j + 1];
      pts[2 * p] = x; pts[2 * p + 1] = y; p++;
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
    bbox.set([x0, y0, x1, y1], i * 4);
  });
  start[list.length] = p;
  return { pts, start, bbox, n: list.length };
}
async function loadCity(key) {
  $("loading").hidden = false;
  const base = "data/" + key + "/";
  const [meta, vec, fields, cells] = await Promise.all(
    ["meta", "vector", "fields", "cells"].map((n) => getJSON(base + n + ".json")));
  S.meta = meta; S.cells = cells;
  S.F = { nx: fields.nx, ny: fields.ny, x0: fields.x0, y0: fields.y0, step: fields.step,
          dx: fields.dx.map((a) => Float32Array.from(a)), dy: fields.dy.map((a) => Float32Array.from(a)) };
  S.maxDisp = 0;
  for (const arr of [...S.F.dx, ...S.F.dy]) for (const v of arr) S.maxDisp = Math.max(S.maxDisp, Math.abs(v));
  const r = vec.roads;
  S.vec = {
    roads: { major: pack(r.major), secondary: pack(r.secondary), tertiary: pack(r.tertiary), minor: pack(r.minor) },
    rail: pack(vec.rail.rail), metro: pack(vec.rail.metro), waterways: pack(vec.waterways),
    water: pack(vec.water), green: pack(vec.green),
    places: vec.places.sort((a, b) => a.r - b.r), stations: vec.stations,
  };
  S.snap = 0;
  const N = meta.snapshots.length;
  const sl = $("snap"); sl.max = Math.max(N - 1, 0); sl.value = N - 1; S.snap = N - 1;
  $("timeSec").hidden = N < 2;
  $("snapTicks").innerHTML = meta.snapshots.map((s) => "<span>" + s.label + "</span>").join("");
  document.querySelector('input[value="change"]').disabled = N < 2;
  if (N < 2 && S.mode === "change") setMode("map");
  buildAbout();
  rebuildField();
  fitView();
  $("loading").hidden = true;
  updateUI();
  requestDraw();
}

/* ---------- warp field ---------- */
function rebuildField() {
  const F = S.F, N = F.dx.length;
  const s0 = Math.min(Math.floor(S.snap), N - 1), s1 = Math.min(s0 + 1, N - 1), f = S.snap - s0;
  const n = F.nx * F.ny;
  if (!S.curDx || S.curDx.length !== n) { S.curDx = new Float32Array(n); S.curDy = new Float32Array(n); }
  for (let i = 0; i < n; i++) {
    S.curDx[i] = (F.dx[s0][i] * (1 - f) + F.dx[s1][i] * f) * S.t;
    S.curDy[i] = (F.dy[s0][i] * (1 - f) + F.dy[s1][i] * f) * S.t;
  }
}
let WX = 0, WY = 0;
function warp(x, y) {
  const F = S.F;
  let fx = (x - F.x0) / F.step, fy = (y - F.y0) / F.step;
  if (fx < 0) fx = 0; else if (fx > F.nx - 1.001) fx = F.nx - 1.001;
  if (fy < 0) fy = 0; else if (fy > F.ny - 1.001) fy = F.ny - 1.001;
  const ix = fx | 0, iy = fy | 0, tx = fx - ix, ty = fy - iy, i = iy * F.nx + ix;
  const w00 = (1 - tx) * (1 - ty), w10 = tx * (1 - ty), w01 = (1 - tx) * ty, w11 = tx * ty, d = S.curDx, e = S.curDy;
  WX = x + d[i] * w00 + d[i + 1] * w10 + d[i + F.nx] * w01 + d[i + F.nx + 1] * w11;
  WY = y + e[i] * w00 + e[i + 1] * w10 + e[i + F.nx] * w01 + e[i + F.nx + 1] * w11;
}
const toSX = (x) => (x - S.view.cx) * S.view.k + S.W / 2;
const toSY = (y) => S.H / 2 - (y - S.view.cy) * S.view.k;
const fromSX = (sx) => (sx - S.W / 2) / S.view.k + S.view.cx;
const fromSY = (sy) => (S.H / 2 - sy) / S.view.k + S.view.cy;

/* ---------- drawing ---------- */
function viewBox() { // baseline-space box that could reach the screen, with margin for the warp
  const m = S.maxDisp * S.t + 0.5;
  return [fromSX(0) - m, fromSY(S.H) - m, fromSX(S.W) + m, fromSY(0) + m];
}
function strokeLayer(L, vb, warped) {
  ctx.beginPath();
  const { pts, start, bbox, n } = L;
  for (let f = 0; f < n; f++) {
    const b = f * 4;
    if (bbox[b + 2] < vb[0] || bbox[b] > vb[2] || bbox[b + 3] < vb[1] || bbox[b + 1] > vb[3]) continue;
    for (let p = start[f]; p < start[f + 1]; p++) {
      const x = pts[2 * p], y = pts[2 * p + 1];
      let sx, sy;
      if (warped) { warp(x, y); sx = toSX(WX); sy = toSY(WY); } else { sx = toSX(x); sy = toSY(y); }
      if (p === start[f]) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
    }
  }
  ctx.stroke();
}
function fillLayer(L, vb) {
  ctx.beginPath();
  const { pts, start, bbox, n } = L;
  for (let f = 0; f < n; f++) {
    const b = f * 4;
    if (bbox[b + 2] < vb[0] || bbox[b] > vb[2] || bbox[b + 3] < vb[1] || bbox[b + 1] > vb[3]) continue;
    for (let p = start[f]; p < start[f + 1]; p++) {
      warp(pts[2 * p], pts[2 * p + 1]);
      const sx = toSX(WX), sy = toSY(WY);
      if (p === start[f]) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
    }
    ctx.closePath();
  }
  ctx.fill();
}
function requestDraw() { if (!S.raf) S.raf = requestAnimationFrame(() => { S.raf = 0; draw(); }); }

function draw() {
  if (!S.vec) return;
  const k = S.view.k, vb = viewBox();
  ctx.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
  ctx.fillStyle = COL.bg; ctx.fillRect(0, 0, S.W, S.H);
  ctx.lineJoin = "round"; ctx.lineCap = "round";

  ctx.fillStyle = COL.green; fillLayer(S.vec.green, vb);
  ctx.fillStyle = COL.water; fillLayer(S.vec.water, vb);
  ctx.strokeStyle = COL.waterway; ctx.lineWidth = Math.max(1.2, Math.min(4, k / 14)); strokeLayer(S.vec.waterways, vb, true);

  const z = Math.min(1.5, Math.max(0.8, k / 40));
  if (k >= 40) { ctx.strokeStyle = COL.minor; ctx.lineWidth = 0.7 * z; strokeLayer(S.vec.roads.minor, vb, true); }
  if (k >= 20) { ctx.strokeStyle = COL.tertiary; ctx.lineWidth = 1.0 * z; strokeLayer(S.vec.roads.tertiary, vb, true); }
  if (k >= 11) { ctx.strokeStyle = COL.secondary; ctx.lineWidth = 1.5 * z; strokeLayer(S.vec.roads.secondary, vb, true); }
  ctx.setLineDash([6, 4]); ctx.strokeStyle = COL.rail; ctx.lineWidth = 1.4 * z; strokeLayer(S.vec.rail, vb, true);
  ctx.setLineDash([]);
  ctx.strokeStyle = COL.metro; ctx.lineWidth = 2.4 * z; strokeLayer(S.vec.metro, vb, true);

  drawCells();

  if ($("ghost").checked && S.t > 0.02) {
    ctx.setLineDash([2, 4]); ctx.strokeStyle = COL.ghost; ctx.lineWidth = 1;
    strokeLayer(S.vec.roads.major, vb, false); ctx.setLineDash([]);
  }
  ctx.strokeStyle = COL.major; ctx.lineWidth = 2.0 * z; strokeLayer(S.vec.roads.major, vb, true);

  if ($("labels").checked) drawLabels();
  updateScale();
}

/* ---------- colour scales ---------- */
const lerp = (a, b, t) => a + (b - a) * t;
function mix(c0, c1, t) { return [lerp(c0[0], c1[0], t), lerp(c0[1], c1[1], t), lerp(c0[2], c1[2], t)]; }
const rgb = (c, a) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
const TEAL = [46, 196, 182], NEUT = [38, 48, 76], HOT = [255, 120, 60];
function inflationColour(J) { // diverging on log2(area scale): teal = compressed (fast), orange = inflated (slow)
  const v = Math.max(-1, Math.min(1, Math.log2(Math.max(J, 0.05))));
  return v < 0 ? mix(NEUT, TEAL, -v) : mix(NEUT, HOT, v);
}
const SEQ = [[18, 24, 46], [74, 54, 124], [200, 70, 120], [255, 209, 102]];
function seqColour(v) {
  const t = Math.max(0, Math.min(1, v)) * (SEQ.length - 1), i = Math.min(Math.floor(t), SEQ.length - 2);
  return mix(SEQ[i], SEQ[i + 1], t - i);
}
function snapVal(arr, i) { // interpolate a per-snapshot array at the current fractional time
  const N = arr.length, s0 = Math.min(Math.floor(S.snap), N - 1), s1 = Math.min(s0 + 1, N - 1), f = S.snap - s0;
  return arr[s0][i] * (1 - f) + arr[s1][i] * f;
}
let metricRange = {};
function rangeFor(m) {
  if (metricRange[m]) return metricRange[m];
  let lo = 1e9, hi = -1e9;
  for (const arr of S.cells[m]) for (let i = 0; i < arr.length; i++) if (S.cells.valid[i]) { lo = Math.min(lo, arr[i]); hi = Math.max(hi, arr[i]); }
  return (metricRange[m] = [lo, hi === lo ? lo + 1 : hi]);
}
/* Night-time-lights style temporal composite: R = earliest, G = middle, B = latest.
 * White = stable, blue/cyan = new, red = faded. Dark cells are transparent. */
function compositeColour(i) {
  const arr = S.cells[S.metric], N = arr.length, [lo, hi] = rangeFor(S.metric);
  const nrm = (v) => Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
  const ch = [nrm(arr[0][i]), N > 2 ? nrm(arr[Math.floor((N - 1) / 2)][i]) : nrm(arr[N - 1][i]), nrm(arr[N - 1][i])];
  // Differences between snapshots are small next to the spread across the city, so
  // amplify each channel's departure from the cell's own mean. Equal channels stay
  // grey/white (stable); the hue only appears where the snapshots disagree.
  const m = (ch[0] + ch[1] + ch[2]) / 3, g = CHANGE_GAIN;
  const c = ch.map((v) => 255 * Math.max(0, Math.min(1, m + g * (v - m))));
  return { c, a: Math.min(0.88, 0.1 + 0.8 * Math.max(...ch)) };
}
const CHANGE_GAIN = 5;
function drawCells() {
  const C = S.cells, n = C.x.length;
  if (!S.centres || S.centres.length !== n * 2) S.centres = new Float32Array(n * 2);
  const hw = C.w / 2, hh = C.h / 2;
  for (let i = 0; i < n; i++) {
    warp(C.x[i], C.y[i]); S.centres[2 * i] = toSX(WX); S.centres[2 * i + 1] = toSY(WY);
  }
  if (S.mode === "map") return;
  if (S.mode === "change" && S.cells.act.length < 2) return;
  const labelCl = new Map();
  for (let i = 0; i < n; i++) {
    if (!C.valid[i]) continue;
    let fill = null;
    if (S.mode === "inflation") fill = rgb(inflationColour(snapVal(C.J, i)), 0.62);
    else if (S.mode === "micro") {
      const M = snapVal(C.M, i);
      if (M < 0.45) continue;
      fill = rgb(seqColour((M - 0.45) / 0.55), 0.3 + 0.55 * (M - 0.45) / 0.55);
      const cid = C.cluster[Math.round(S.snap)] ? C.cluster[Math.round(S.snap)][i] : 0;
      if (cid) { const e = labelCl.get(cid) || { sx: 0, sy: 0, n: 0 }; e.sx += S.centres[2 * i]; e.sy += S.centres[2 * i + 1]; e.n++; labelCl.set(cid, e); }
    } else if (S.mode === "change") { const { c, a } = compositeColour(i); fill = rgb(c, a); }
    if (!fill) continue;
    const x = C.x[i], y = C.y[i];
    ctx.beginPath();
    warp(x - hw, y - hh); ctx.moveTo(toSX(WX), toSY(WY));
    warp(x + hw, y - hh); ctx.lineTo(toSX(WX), toSY(WY));
    warp(x + hw, y + hh); ctx.lineTo(toSX(WX), toSY(WY));
    warp(x - hw, y + hh); ctx.lineTo(toSX(WX), toSY(WY));
    ctx.closePath(); ctx.fillStyle = fill; ctx.fill();
  }
  if (S.mode === "micro") {
    ctx.font = "700 12px Inter,system-ui,sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const [cid, e] of labelCl) {
      if (e.n < 3) continue;
      const sx = e.sx / e.n, sy = e.sy / e.n;
      ctx.lineWidth = 3; ctx.strokeStyle = COL.halo; ctx.strokeText("#" + cid, sx, sy);
      ctx.fillStyle = "#fff"; ctx.fillText("#" + cid, sx, sy);
    }
  }
}

/* ---------- labels: horizontal, constant pixel size, collision-checked ---------- */
function drawLabels() {
  const k = S.view.k, placed = [];
  const minK = [0, 14, 34, 70], font = [15, 12.5, 11.5, 11];
  ctx.textAlign = "center"; ctx.textBaseline = "middle";
  const hit = (r) => placed.some((q) => r.x < q.x + q.w && r.x + r.w > q.x && r.y < q.y + q.h && r.y + r.h > q.y);
  const put = (text, sx, sy, size, weight, colour) => {
    if (sx < -50 || sx > S.W + 50 || sy < -20 || sy > S.H + 20) return;
    ctx.font = `${weight} ${size}px Inter,system-ui,sans-serif`;
    const w = ctx.measureText(text).width + 6, h = size + 4, r = { x: sx - w / 2, y: sy - h / 2, w, h };
    if (hit(r)) return;
    placed.push(r);
    ctx.lineWidth = 3.5; ctx.strokeStyle = COL.halo; ctx.strokeText(text, sx, sy);
    ctx.fillStyle = colour; ctx.fillText(text, sx, sy);
  };
  for (const p of S.vec.places) {
    if (k < minK[p.r]) continue;
    warp(p.x, p.y);
    put(p.n, toSX(WX), toSY(WY), font[p.r], p.r === 0 ? 700 : 500, p.r === 0 ? "#fff" : COL.text);
  }
  if (k >= 60) for (const st of S.vec.stations) {
    warp(st.x, st.y);
    const sx = toSX(WX), sy = toSY(WY);
    if (sx < 0 || sx > S.W || sy < 0 || sy > S.H) continue;
    ctx.fillStyle = st.m ? COL.metro : "#b7a8ee"; ctx.beginPath(); ctx.arc(sx, sy, 3, 0, 6.3); ctx.fill();
    if (k >= 90) put(st.n, sx, sy - 11, 10.5, 500, st.m ? COL.metro : "#c9bff2");
  }
}

/* ---------- scale bar (map units are reference-speed km => minutes) ---------- */
function updateScale() {
  const k = S.view.k, target = 90 / k, nice = [0.25, 0.5, 1, 2, 5, 10, 20, 50];
  const km = nice.find((v) => v >= target) || 50;
  $("scaleBar").style.width = km * k + "px";
  const mins = km * 60 / S.meta.ref_speed_kmh;
  $("scaleText").textContent = S.t < 0.05 ? km + " km" : km + " map-km ≈ " + (mins < 10 ? mins.toFixed(1) : Math.round(mins)) + " min";
  $("scale").title = "In the travel-time map one map-km is the distance covered in " + (60 / S.meta.ref_speed_kmh).toFixed(1) + " minutes at the city's typical speed.";
}

/* ---------- view control ---------- */
function resize() {
  S.dpr = Math.min(window.devicePixelRatio || 1, 2);
  S.W = window.innerWidth; S.H = window.innerHeight;
  cv.width = S.W * S.dpr; cv.height = S.H * S.dpr;
  requestDraw();
}
function fitView() {
  const [x0, y0, x1, y1] = S.meta.core;
  const phone = S.W < 720, padL = phone ? 0 : 330;
  S.view.k = Math.min((S.W - padL) / ((x1 - x0) * 1.06), (S.H * (phone ? 0.42 : 1)) / ((y1 - y0) * 1.06));
  S.view.cx = (x0 + x1) / 2 - padL / 2 / S.view.k;
  S.view.cy = (y0 + y1) / 2 - (phone ? S.H * 0.14 / S.view.k : 0);
}
function zoomAt(sx, sy, factor) {
  const k0 = S.view.k, k1 = Math.max(5, Math.min(500, k0 * factor));
  const wx = fromSX(sx), wy = fromSY(sy);
  S.view.k = k1;
  S.view.cx = wx - (sx - S.W / 2) / k1;
  S.view.cy = wy + (sy - S.H / 2) / k1;
  requestDraw();
}
const ptrs = new Map(); let lastPinch = 0, moved = false;
cv.addEventListener("pointerdown", (e) => { cv.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, [e.clientX, e.clientY]); cv.classList.add("drag"); moved = false; });
cv.addEventListener("pointerup", (e) => { ptrs.delete(e.pointerId); lastPinch = 0; cv.classList.remove("drag"); });
cv.addEventListener("pointercancel", (e) => { ptrs.delete(e.pointerId); lastPinch = 0; });
cv.addEventListener("pointermove", (e) => {
  if (!ptrs.has(e.pointerId)) { hover(e.clientX, e.clientY); return; }
  const prev = ptrs.get(e.pointerId); ptrs.set(e.pointerId, [e.clientX, e.clientY]); moved = true;
  if (ptrs.size === 1) {
    S.view.cx -= (e.clientX - prev[0]) / S.view.k; S.view.cy += (e.clientY - prev[1]) / S.view.k; $("tip").hidden = true; requestDraw();
  } else if (ptrs.size === 2) {
    const [a, b] = [...ptrs.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
    if (lastPinch) zoomAt((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, d / lastPinch);
    lastPinch = d;
  }
});
cv.addEventListener("wheel", (e) => { e.preventDefault(); zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0016))); }, { passive: false });
cv.addEventListener("dblclick", (e) => zoomAt(e.clientX, e.clientY, e.shiftKey ? 0.5 : 2));
$("zin").onclick = () => zoomAt(S.W / 2, S.H / 2, 1.5);
$("zout").onclick = () => zoomAt(S.W / 2, S.H / 2, 1 / 1.5);
$("zreset").onclick = () => { fitView(); requestDraw(); };
window.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "SELECT") return;
  if (e.key === "+" || e.key === "=") zoomAt(S.W / 2, S.H / 2, 1.3);
  if (e.key === "-") zoomAt(S.W / 2, S.H / 2, 1 / 1.3);
});

/* ---------- hover ---------- */
function hover(mx, my) {
  if (!S.centres || (S.mode === "map")) { $("tip").hidden = true; return; }
  const C = S.cells; let best = -1, bd = (S.view.k * C.w * 0.7) ** 2;
  for (let i = 0; i < C.x.length; i++) {
    if (!C.valid[i]) continue;
    const dx = S.centres[2 * i] - mx, dy = S.centres[2 * i + 1] - my, d = dx * dx + dy * dy;
    if (d < bd) { bd = d; best = i; }
  }
  const tip = $("tip");
  if (best < 0) { tip.hidden = true; return; }
  const N = C.M.length, v = (a) => snapVal(a, best);
  const J = v(C.J);
  let html = `<b>${J > 1.15 ? "Inflated" : J < 0.87 ? "Compressed" : "Typical"}</b> · area ×${J.toFixed(2)}<br>` +
    `Slowness index ${v(C.fric).toFixed(2)} (1 = reference)<br>` +
    `Activity ${v(C.act).toFixed(2)} · Access ${v(C.acc).toFixed(2)}<br>Micro-market score <b>${v(C.M).toFixed(2)}</b>`;
  if (N > 1) {
    const dM = C.M[N - 1][best] - C.M[0][best], dA = C.acc[N - 1][best] - C.acc[0][best];
    html += `<br>Change ${S.meta.snapshots[0].label} → ${S.meta.snapshots[N - 1].label}: score ${dM >= 0 ? "+" : ""}${dM.toFixed(2)}, access ${dA >= 0 ? "+" : ""}${dA.toFixed(2)}`;
  }
  tip.innerHTML = html; tip.hidden = false;
  tip.style.left = Math.min(mx + 14, S.W - 250) + "px"; tip.style.top = Math.min(my + 14, S.H - 130) + "px";
}

/* ---------- UI ---------- */
function setMode(m) {
  S.mode = m;
  document.querySelector(`input[name=mode][value=${m}]`).checked = true;
  updateUI(); requestDraw();
}
function updateUI() {
  $("stretchOut").textContent = Math.round(S.t * 100) + "%";
  const N = S.meta.snapshots.length, sn = S.meta.snapshots[Math.min(Math.round(S.snap), N - 1)];
  $("snapOut").textContent = sn.label;
  $("scenarioBadge").hidden = !sn.scenario;
  $("metricRow").hidden = S.mode !== "change";
  const lg = $("legend");
  const grad = (stops) => `<div class="bar" style="background:linear-gradient(90deg,${stops.map((c) => rgb(c, 1)).join(",")})"></div>`;
  if (S.mode === "inflation") {
    lg.innerHTML = grad([TEAL, NEUT, HOT]) +
      `<div class="ends"><span>compressed · fast, well connected</span><span>inflated · slow</span></div>` +
      `<p>Area scale of each patch of the city in the travel-time map (1 km² of ground becomes ×0.5 … ×2).</p>`;
  } else if (S.mode === "micro") {
    lg.innerHTML = grad(SEQ) + `<div class="ends"><span>low</span><span>high</span></div>` +
      `<p>Score = active places that are also quickly reachable from other active places. Numbered blocks are contiguous clusters of high scorers.</p>`;
  } else if (S.mode === "change") {
    lg.innerHTML = `<div class="chips"><span class="chip"><i style="background:#fff"></i>stable</span>` +
      `<span class="chip"><i style="background:#3fa9ff"></i>new / rising</span><span class="chip"><i style="background:#ff4040"></i>fading</span>` +
      `<span class="chip"><i style="background:#ff4"></i>mid-period peak</span></div>` +
      `<p>Temporal RGB composite as in night-time-lights: red = earliest snapshot, green = middle, blue = latest. Dark cells are transparent. Differences are amplified ×5 so small changes are visible.</p>`;
  } else lg.innerHTML = `<p>Drag to pan, scroll or pinch to zoom, double-click to zoom in.</p>`;
  const list = $("listSec");
  list.hidden = S.mode !== "micro";
  if (S.mode === "micro") buildList();
}
function buildList() {
  const C = S.cells, N = C.M.length, last = N - 1, cl = C.cluster[last], groups = new Map();
  for (let i = 0; i < cl.length; i++) if (cl[i]) {
    const g = groups.get(cl[i]) || { id: cl[i], idx: [] }; g.idx.push(i); groups.set(cl[i], g);
  }
  const rows = [...groups.values()].map((g) => {
    const mean = (a) => g.idx.reduce((s, i) => s + a[i], 0) / g.idx.length;
    const cx = mean(C.x), cy = mean(C.y);
    const near = S.vec.places.filter((p) => p.r >= 1 && p.r <= 2 && Math.hypot(p.x - cx, p.y - cy) < 3)
      .sort((a, b) => Math.hypot(a.x - cx, a.y - cy) - Math.hypot(b.x - cx, b.y - cy)).slice(0, 3).map((p) => p.n);
    return { id: g.id, n: g.idx.length, cx, cy, score: mean(C.M[last]), d: N > 1 ? mean(C.M[last]) - mean(C.M[0]) : null, names: near.join(", ") || "unnamed" };
  });
  rows.sort((a, b) => (N > 1 ? b.d - a.d : b.score - a.score));
  $("listTitle").textContent = N > 1 ? "Fastest-rising micro-markets" : "Top micro-markets";
  $("list").innerHTML = rows.slice(0, 8).map((r) =>
    `<li data-x="${r.cx}" data-y="${r.cy}"><b>#${r.id}</b> ${r.names}<small>${r.n} km² · score ${r.score.toFixed(2)}${r.d !== null ? " · Δ " + (r.d >= 0 ? "+" : "") + r.d.toFixed(2) : ""}</small></li>`).join("");
  $("list").querySelectorAll("li").forEach((li) => li.onclick = () => flyTo(+li.dataset.x, +li.dataset.y, 60));
}
function flyTo(x, y, k) {
  warp(x, y);
  const start = { ...S.view }, tx = WX, ty = WY, t0 = performance.now();
  const step = (now) => {
    const u = Math.min(1, (now - t0) / 600), e = u * u * (3 - 2 * u);
    S.view.k = start.k * Math.pow(k / start.k, e); S.view.cx = lerp(start.cx, tx, e); S.view.cy = lerp(start.cy, ty, e);
    draw(); if (u < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
function buildAbout() {
  const m = S.meta, f = m.snapshots[m.snapshots.length - 1].fidelity;
  const bands = f.bands_km.map((b) => `${b.band} km: map ${(b.err_map * 100).toFixed(0)}% vs plain geography ${(b.err_geo * 100).toFixed(0)}%`).join(" · ");
  $("aboutBody").innerHTML =
    `<p><b>Stretch</b> blends between the ordinary geographic layout and the travel-time layout. Distances in the travel-time layout are drive times, so 1 map-km ≈ ${(60 / m.ref_speed_kmh).toFixed(1)} min. This scale is fixed across snapshots, so changes between them are real.</p>` +
    `<p><b>Inflation</b> = how much a patch of ground grows (slow, poorly connected) or shrinks (fast, well connected). Time-to-reach is measured on free-flow speeds unless a traffic matrix was supplied; with free-flow data the dense core is usually <i>compressed</i>, and congestion shows up only with peak-hour data.</p>` +
    `<p><b>Accuracy</b> (median error vs travel time): ${bands}. The warp helps at city scale but cannot fix street-scale detours such as river crossings. Read regions, not blocks.</p>` +
    `<p>Activity = OSM points of interest + 0.1 × buildings. OSM mapping effort grows over time, so activity trends need validation against independent data (e.g. night lights).</p>`;
}

$("stretch").oninput = (e) => { S.t = e.target.value / 100; rebuildField(); updateUI(); requestDraw(); };
$("snap").oninput = (e) => { S.snap = +e.target.value; rebuildField(); updateUI(); requestDraw(); };
document.querySelectorAll("input[name=mode]").forEach((r) => r.onchange = () => setMode(r.value));
$("metric").onchange = (e) => { S.metric = e.target.value; requestDraw(); };
$("ghost").onchange = $("labels").onchange = requestDraw;
$("collapse").onclick = () => { const p = $("panel"); p.classList.toggle("collapsed"); $("collapse").textContent = p.classList.contains("collapsed") ? "+" : "–"; };
$("city").onchange = (e) => loadCity(e.target.value);
let anim = null;
function animate(btn, getV, setV, lo, hi, ms) {
  if (anim) { cancelAnimationFrame(anim.id); const same = anim.btn === btn; anim.btn.textContent = anim.label; anim = null; if (same) return; }
  const label = btn.textContent, t0 = performance.now(), v0 = getV();
  btn.textContent = "■ Stop"; anim = { btn, label, id: 0 };
  const step = (now) => {
    const u = ((now - t0) / ms + (v0 - lo) / (hi - lo)) % 2, p = u <= 1 ? u : 2 - u; // ping-pong
    setV(lo + (hi - lo) * p); anim.id = requestAnimationFrame(step);
  };
  anim.id = requestAnimationFrame(step);
}
$("stretchPlay").onclick = () => animate($("stretchPlay"), () => S.t, (v) => { S.t = v; $("stretch").value = v * 100; rebuildField(); updateUI(); draw(); }, 0, 1, 3500);
$("snapPlay").onclick = () => { const N = S.meta.snapshots.length; if (N > 1) animate($("snapPlay"), () => S.snap, (v) => { S.snap = v; $("snap").value = v; rebuildField(); updateUI(); draw(); }, 0, N - 1, 2500 * (N - 1)); };
window.addEventListener("resize", resize);

(async function init() {
  resize();
  try {
    const index = await getJSON("data/index.json");
    $("city").innerHTML = index.map((c) => `<option value="${c.key}">${c.name}${c.tier ? " · T" + c.tier : ""}</option>`).join("");
    const want = new URLSearchParams(location.search).get("city");
    if (want && index.some((c) => c.key === want)) $("city").value = want;
    await loadCity($("city").value);
    const q = new URLSearchParams(location.search);
    if (q.get("mode")) setMode(q.get("mode"));
    if (q.get("t")) { S.t = +q.get("t"); $("stretch").value = S.t * 100; rebuildField(); updateUI(); requestDraw(); }
  } catch (err) {
    $("loading").textContent = "Could not load data: " + err.message + ". Run `python -m traveltime.build` and serve /web over http.";
  }
})();
