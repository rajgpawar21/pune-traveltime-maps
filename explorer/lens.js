"use strict";
/* Proximity lens: an ADDITIONAL view on the same map. It never replaces the classic analysis.
 *
 * Loaded after app.js. It wraps a few of app.js's global functions (loadCity, rebuildField, drawCells,
 * drawLabels, hover, updateUI) and falls through to the originals whenever S.lens !== "prox".
 * Scores are recomputed here from the exported pillars, so weight/persona/category sliders are live.
 */
const P = {
  data: null, on: false, yf: 0, layer: "composite", persona: "buyer", rank: "score",
  w: { work: 0.4, lifestyle: 0.3, metro: 0.3 }, cw: {}, shape: "mm",
  comp: null, cl: null, list: [], maxDisp: 0,
};
S.lens = "classic";
const orig = { loadCity, rebuildField, drawCells, drawLabels, hover, updateUI };
const yIdx = () => Math.max(0, Math.min(P.data.years.length - 1, Math.round(P.yf)));
const pq = (q) => document.querySelector(q);

/* ---------- colours ---------- */
const RAMP = {
  work: [[12, 22, 50], [40, 90, 200], [140, 200, 255]],
  lifestyle: [[34, 10, 44], [175, 45, 150], [255, 150, 225]],
  metro: [[8, 30, 36], [30, 150, 140], [130, 255, 232]],
};
function ramp(stops, v) {
  v = Math.max(0, Math.min(1, v)); const t = v * (stops.length - 1), i = Math.min(Math.floor(t), stops.length - 2);
  return mix(stops[i], stops[i + 1], t - i);
}
const gradCss = (stops) => `linear-gradient(90deg,${stops.map((c) => rgb(c, 1)).join(",")})`;

/* ---------- scores (recomputed live) ---------- */
function lifestyleAt(y, i) {
  let num = 0, den = 0;
  for (const c of P.data.categories) { const w = P.cw[c] || 0; if (w > 0) { num += w * P.data.life[c][y][i]; den += w; } }
  return den ? num / den : 0;
}
function compositeAt(y, i) {
  const D = P.data, parts = [["work", D.work[y][i]], ["lifestyle", lifestyleAt(y, i)], ["metro", D.metro[y][i]]];
  let tw = 0, s = 0;
  for (const [k, v] of parts) { const w = P.w[k]; if (w > 0) { tw += w; s += w * Math.log(Math.max(v, 0.02)); } }
  return tw ? Math.exp(s / tw) : 0;
}
function recompute() {
  const D = P.data, n = S.cells.x.length, N = D.years.length;
  P.comp = []; for (let y = 0; y < N; y++) { const a = new Float32Array(n); for (let i = 0; i < n; i++) a[i] = compositeAt(y, i); P.comp.push(a); }
  buildClusters();
}
function buildClusters() {
  const D = P.data, y = yIdx(), a = P.comp[y], C = S.cells, n = C.x.length, nx = D.nx, ny = D.ny;
  const vals = []; for (let i = 0; i < n; i++) if (C.valid[i]) vals.push(a[i]);
  vals.sort((p, q) => p - q);
  const thr = vals[Math.floor(vals.length * 0.92)] ?? 1;
  const lab = new Int32Array(n), groups = [];
  for (let s = 0; s < n; s++) {
    if (lab[s] || !C.valid[s] || a[s] < thr) continue;
    const id = groups.length + 1, stack = [s], idx = []; lab[s] = id;
    while (stack.length) {
      const c = stack.pop(); idx.push(c); const cx = c % nx, cy = (c / nx) | 0;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        const x = cx + dx, yy = cy + dy; if (x < 0 || yy < 0 || x >= nx || yy >= ny) continue;
        const j = yy * nx + x; if (!lab[j] && C.valid[j] && a[j] >= thr) { lab[j] = id; stack.push(j); }
      }
    }
    groups.push(idx);
  }
  const rows = groups.filter((g) => g.length >= 3).map((idx) => {
    const m = (f) => idx.reduce((s, i) => s + f(i), 0) / idx.length, cx = m((i) => C.x[i]), cy = m((i) => C.y[i]);
    const names = S.vec.places.filter((p) => p.r >= 1 && p.r <= 2 && Math.hypot(p.x - cx, p.y - cy) < 3)
      .sort((p, q) => Math.hypot(p.x - cx, p.y - cy) - Math.hypot(q.x - cx, q.y - cy)).slice(0, 3).map((p) => p.n);
    return { idx, cx, cy, n: idx.length, score: m((i) => a[i]), rise: m((i) => a[i] - P.comp[0][i]), names: names.join(", ") || "unnamed" };
  });
  rows.sort((p, q) => (P.rank === "rise" ? q.rise - p.rise : q.score - p.score));
  rows.forEach((r, k) => { r.id = k + 1; });
  P.list = rows; P.cl = new Int32Array(n);
  for (const r of rows) for (const i of r.idx) P.cl[i] = r.id;
}

/* ---------- warp field source ---------- */
function proxField() {
  const F = S.F, n = F.nx * F.ny;
  if (!S.curDx || S.curDx.length !== n) { S.curDx = new Float32Array(n); S.curDy = new Float32Array(n); }
  let dxA, dyA, dxB, dyB, f = 0;
  if (P.shape === "free") { const k = F.dx.length - 1; dxA = dxB = F.dx[k]; dyA = dyB = F.dy[k]; }
  else if (P.shape === "peak") { dxA = dxB = P.f.dx[0]; dyA = dyB = P.f.dy[0]; }
  else {
    const N = P.data.years.length, y0 = Math.min(Math.floor(P.yf), N - 1), y1 = Math.min(y0 + 1, N - 1); f = P.yf - y0;
    dxA = P.f.dx[1 + y0]; dxB = P.f.dx[1 + y1]; dyA = P.f.dy[1 + y0]; dyB = P.f.dy[1 + y1];
  }
  for (let i = 0; i < n; i++) {
    S.curDx[i] = (dxA[i] * (1 - f) + dxB[i] * f) * S.t; S.curDy[i] = (dyA[i] * (1 - f) + dyB[i] * f) * S.t;
  }
}
rebuildField = function () { if (S.lens === "prox" && P.data) proxField(); else orig.rebuildField(); };

/* ---------- drawing ---------- */
function layerValue(i, y) {
  const D = P.data;
  switch (P.layer) {
    case "work": return D.work[y][i];
    case "lifestyle": return lifestyleAt(y, i);
    case "metro": return D.metro[y][i];
    case "premium": return Math.min(1, D.rel_gain[y][i] / 0.3);
    default: return P.comp[y][i];
  }
}
function changeColour(i) {
  const N = P.data.years.length, ys = [0, Math.floor((N - 1) / 2), N - 1];
  const raw = ys.map((y) => P.comp[y][i]), m = (raw[0] + raw[1] + raw[2]) / 3, g = CHANGE_GAIN;
  return { c: raw.map((v) => 255 * Math.max(0, Math.min(1, m + g * (v - m) * 1))), a: Math.min(0.88, 0.1 + 0.8 * Math.max(...raw)) };
}
drawCells = function () {
  if (S.lens !== "prox" || !P.data) return orig.drawCells();
  const C = S.cells, n = C.x.length, hw = C.w / 2, hh = C.h / 2, y = yIdx();
  if (!S.centres || S.centres.length !== n * 2) S.centres = new Float32Array(n * 2);
  for (let i = 0; i < n; i++) { warp(C.x[i], C.y[i]); S.centres[2 * i] = toSX(WX); S.centres[2 * i + 1] = toSY(WY); }
  const lab = new Map();
  for (let i = 0; i < n; i++) {
    if (!C.valid[i]) continue;
    let fill;
    if (P.layer === "change") { const { c, a } = changeColour(i); fill = rgb(c, a); }
    else {
      const v = layerValue(i, y);
      if (P.layer === "composite") {
        if (v < 0.3) continue; fill = rgb(ramp(SEQ, (v - 0.3) / 0.7), 0.25 + 0.6 * (v - 0.3) / 0.7);
      } else {
        const key = P.layer === "premium" ? "metro" : P.layer; if (v < 0.04) continue;
        fill = rgb(ramp(RAMP[key], v), 0.2 + 0.65 * v);
      }
    }
    const x = C.x[i], yy = C.y[i];
    ctx.beginPath();
    warp(x - hw, yy - hh); ctx.moveTo(toSX(WX), toSY(WY));
    warp(x + hw, yy - hh); ctx.lineTo(toSX(WX), toSY(WY));
    warp(x + hw, yy + hh); ctx.lineTo(toSX(WX), toSY(WY));
    warp(x - hw, yy + hh); ctx.lineTo(toSX(WX), toSY(WY));
    ctx.closePath(); ctx.fillStyle = fill; ctx.fill();
    if (P.layer === "composite" && P.cl[i]) { const e = lab.get(P.cl[i]) || { sx: 0, sy: 0, n: 0 }; e.sx += S.centres[2 * i]; e.sy += S.centres[2 * i + 1]; e.n++; lab.set(P.cl[i], e); }
  }
  if (P.layer === "composite") {
    ctx.font = "700 12px Inter,system-ui,sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (const [id, e] of lab) { const sx = e.sx / e.n, sy = e.sy / e.n; ctx.lineWidth = 3; ctx.strokeStyle = COL.halo; ctx.strokeText("#" + id, sx, sy); ctx.fillStyle = "#fff"; ctx.fillText("#" + id, sx, sy); }
  }
};
drawLabels = function () {
  if (S.lens !== "prox" || !P.data) return orig.drawLabels();
  drawMetro();
  const keep = S.vec.stations; S.vec.stations = [];     // the classic OSM station dots would duplicate ours
  orig.drawLabels();
  S.vec.stations = keep;
};
function drawMetro() {
  const k = S.view.k, y = yIdx(), active = new Set(P.data.years[y].lines_active), z = Math.min(1.5, Math.max(0.8, k / 40));
  const stroke = (arr) => { ctx.beginPath(); for (let j = 0; j < arr.length; j += 2) { warp(arr[j], arr[j + 1]); const sx = toSX(WX), sy = toSY(WY); if (j === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy); } ctx.stroke(); };
  ctx.lineCap = "round"; ctx.lineJoin = "round";
  P.data.lines.forEach((ln, li) => {
    const on = active.has(li);
    ctx.strokeStyle = ln.color; ctx.globalAlpha = on ? 1 : 0.7; ctx.lineWidth = (on ? 3.6 : 2.6) * z; ctx.setLineDash(on ? [] : [7, 5]);
    for (const w of ln.ways) stroke(w);
  });
  ctx.setLineDash([]); ctx.globalAlpha = 1;
  const placed = [];
  P.data.lines.forEach((ln, li) => {
    const on = active.has(li);
    for (const st of ln.stations) {
      warp(st.x, st.y); const sx = toSX(WX), sy = toSY(WY);
      if (sx < -20 || sx > S.W + 20 || sy < -20 || sy > S.H + 20) continue;
      if (k >= 20) {
        ctx.beginPath(); ctx.arc(sx, sy, 3.6, 0, 6.3); ctx.fillStyle = on ? "#fff" : COL.bg; ctx.fill();
        ctx.lineWidth = 2; ctx.strokeStyle = ln.color; ctx.stroke();
      }
      if (k >= 70) {
        ctx.font = "500 10.5px Inter,system-ui,sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
        const w = ctx.measureText(st.n).width + 6, r = { x: sx - w / 2, y: sy - 19, w, h: 13 };
        if (placed.some((q) => r.x < q.x + q.w && r.x + r.w > q.x && r.y < q.y + q.h && r.y + r.h > q.y)) continue;
        placed.push(r); ctx.lineWidth = 3; ctx.strokeStyle = COL.halo; ctx.strokeText(st.n, sx, sy - 12); ctx.fillStyle = on ? "#dff" : "#9ab"; ctx.fillText(st.n, sx, sy - 12);
      }
    }
  });
}

/* ---------- tooltip ---------- */
hover = function (mx, my) {
  if (S.lens !== "prox" || !P.data) return orig.hover(mx, my);
  if (!S.centres) { $("tip").hidden = true; return; }
  const C = S.cells; let best = -1, bd = (S.view.k * C.w * 0.7) ** 2;
  for (let i = 0; i < C.x.length; i++) {
    if (!C.valid[i]) continue;
    const dx = S.centres[2 * i] - mx, dy = S.centres[2 * i + 1] - my, d = dx * dx + dy * dy;
    if (d < bd) { bd = d; best = i; }
  }
  const tip = $("tip"); if (best < 0) { tip.hidden = true; return; }
  const D = P.data, y = yIdx(), N = D.years.length, i = best, pct = (v) => Math.round(v * 100);
  const ts = D.t_station[y][i], jobs = (v) => Math.round(v / 100) * 100;
  let html = `<b>Composite ${P.comp[y][i].toFixed(2)}</b><br>Work ${D.work[y][i].toFixed(2)} · Lifestyle ${lifestyleAt(y, i).toFixed(2)} · Metro ${D.metro[y][i].toFixed(2)}<br>` +
    (ts < 900 ? `Nearest station ${ts.toFixed(0)} min away · ${D.stn15[y][i]} within 15 min<br>` : `No metro in service this year<br>`) +
    `Jobs within ${D.job_horizon_min} min: ${jobs(D.jobs30_drive[y][i]).toLocaleString()} by road` +
    (D.jobs30[y][i] - D.jobs30_drive[y][i] > 50 ? `, ${jobs(D.jobs30[y][i]).toLocaleString()} with metro (+${pct(D.rel_gain[y][i])}%)` : "");
  if (N > 1) { const d = P.comp[y][i] - P.comp[0][i]; html += `<br>Since ${D.years[0].label}: ${d >= 0 ? "+" : ""}${d.toFixed(2)}`; }
  tip.innerHTML = html; tip.hidden = false;
  tip.style.left = Math.min(mx + 14, S.W - 250) + "px"; tip.style.top = Math.min(my + 14, S.H - 150) + "px";
};

/* ---------- UI ---------- */
function sliderRow(label, id, value, max, onInput) {
  const row = document.createElement("div"); row.className = "sl";
  row.innerHTML = `<span>${label}</span><input type="range" min="0" max="${max}" step="5" value="${value}" aria-label="${label} weight"><output>${value}</output>`;
  const inp = row.querySelector("input"), out = row.querySelector("output");
  inp.oninput = () => { out.textContent = inp.value; onInput(+inp.value); };
  row._set = (v) => { inp.value = v; out.textContent = v; };
  return row;
}
const pillarRows = {}, catRows = {};
function buildProxUI() {
  const D = P.data;
  $("persona").innerHTML = Object.entries(D.personas).map(([k, v]) => `<option value="${k}">${v.label}</option>`).join("") + `<option value="custom">Custom</option>`;
  $("pillars").innerHTML = ""; $("cats").innerHTML = "";
  for (const [k, label] of [["work", "Work"], ["lifestyle", "Lifestyle"], ["metro", "Metro"]]) {
    pillarRows[k] = sliderRow(label, k, Math.round(P.w[k] * 100), 100, (v) => { P.w[k] = v / 100; $("persona").value = "custom"; refresh(); });
    $("pillars").appendChild(pillarRows[k]);
  }
  for (const c of D.categories) {
    P.cw[c] = D.category_defaults[c] ?? 1;
    catRows[c] = sliderRow(c[0].toUpperCase() + c.slice(1), c, Math.round(P.cw[c] * 100), 200, (v) => { P.cw[c] = v / 100; refresh(); });
    $("cats").appendChild(catRows[c]);
  }
  const N = D.years.length, sl = $("pyear"); sl.max = N - 1; sl.value = P.yf = N > 1 ? D.years.findIndex((y) => !y.scenario && /current/i.test(y.label)) : 0;
  if (P.yf < 0) P.yf = N - 1; sl.value = P.yf;
  $("pyearTicks").innerHTML = D.years.map((y) => `<span>${y.label.split(" (")[0].replace("Planned: ", "")}</span>`).join("");
  $("pyearSec").hidden = N < 2;
  P.f = D.fields;
  P.maxDisp = 0; for (const arr of [...P.f.dx, ...P.f.dy]) for (const v of arr) P.maxDisp = Math.max(P.maxDisp, Math.abs(v));
}
function applyPersona(k) {
  const pr = P.data.personas[k]; if (!pr) return;
  P.persona = k; for (const key of ["work", "lifestyle", "metro"]) { P.w[key] = pr[key]; pillarRows[key]._set(Math.round(pr[key] * 100)); }
}
function proxLegend() {
  const D = P.data, y = yIdx(), parts = {
    composite: [SEQ, "Composite of the three pillars, as a weighted geometric mean: a place cannot score high with nothing in one pillar. Numbered blocks are contiguous clusters in the top 8% of cells."],
    work: [RAMP.work, `Jobs reachable quickly at peak hour (offices + SEZ boost), ${D.half_life.work}-min half-life, by road or metro.`],
    lifestyle: [RAMP.lifestyle, `Restaurants, bars, malls, cinemas, parks reachable quickly (${D.half_life.lifestyle}-min half-life), weighted by the category sliders.`],
    metro: [RAMP.metro, "Closeness to a station in service that year (65%) plus how many more jobs metro adds within reach (35%)."],
    premium: [RAMP.metro, `Extra jobs reachable within ${D.job_horizon_min} min thanks to metro, relative to road only. Bright = large gain (saturates at +30%).`],
  };
  if (P.layer === "change") {
    return `<div class="chips"><span class="chip"><i style="background:#fff"></i>stable</span><span class="chip"><i style="background:#3fa9ff"></i>rising</span><span class="chip"><i style="background:#ff4040"></i>fading</span></div>` +
      `<p>Temporal RGB composite as in night-time-lights: red = ${D.years[0].label}, green = middle year, blue = ${D.years[D.years.length - 1].label}; composite score, differences amplified ×${CHANGE_GAIN}.</p>`;
  }
  const [stops, text] = parts[P.layer];
  return `<div class="bar" style="background:${gradCss(stops)}"></div><div class="ends"><span>low</span><span>high</span></div><p>${text}</p>`;
}
function proxAbout() {
  const D = P.data;
  return `<p><b>Proximity lens</b> scores every cell on three things people actually move for: <b>Work</b> (offices), <b>Lifestyle</b> (dining, nightlife, malls, cinemas, parks) and <b>Metro</b>. It sits beside the classic analysis and does not replace it.</p>` +
    `<p><b>Times</b> are peak-hour: road speeds are ${Math.round(D.peak_speed_factor * 100)}% of free-flow (an assumption); metro is unaffected by traffic and includes waiting, dwell and transfer time. Switch <i>Stretch map shape</i> to see the city reshaped by drive-only vs drive+metro times.</p>` +
    `<p><b>Offices</b> come from OSM (area-weighted headcount proxy) plus ${D.assumptions.sez_count} nearby SEZs from the SEZ atlas, each spread over ~${D.assumptions.sez_sigma_km} km because the atlas positions are locality-level. OSM office coverage in India is incomplete.</p>` +
    `<p><b>Metro years</b> come from an editable draft file and are <b>unverified</b>; lines are modelled as fully in service from their first opening year. Years marked planned are scenarios, not history.</p>` +
    `<p>Ranking is a hypothesis generator, not a price signal: back-test against rents or prices before acting.</p>`;
}
function proxList() {
  const D = P.data, N = D.years.length;
  $("pListTitle").textContent = "Top areas";
  $("pRank").hidden = N < 2;
  $("pList").innerHTML = P.list.slice(0, 8).map((r) => `<li data-x="${r.cx}" data-y="${r.cy}"><b>#${r.id}</b> ${r.names}<small>${r.n} km² · score ${r.score.toFixed(2)}${N > 1 ? " · Δ " + (r.rise >= 0 ? "+" : "") + r.rise.toFixed(2) : ""}</small></li>`).join("") ||
    "<li>No clusters at these settings</li>";
  $("pList").querySelectorAll("li[data-x]").forEach((li) => li.onclick = () => flyTo(+li.dataset.x, +li.dataset.y, 60));
}
function refresh() {
  if (!P.data) return;
  recompute(); updateUI(); requestDraw();
}
function setLens(l) {
  S.lens = l; P.on = l === "prox";
  document.querySelectorAll("#lensRow button").forEach((b) => b.classList.toggle("on", b.dataset.lens === l));
  if (P.on) { S.maxDisp = Math.max(S.maxDisp, P.maxDisp); recompute(); } else buildAbout();
  rebuildField(); updateUI(); requestDraw();
}
updateUI = function () {
  orig.updateUI();
  const prox = S.lens === "prox" && P.data;
  $("classicCtl").hidden = prox; $("proxCtl").hidden = !prox;
  if (!prox) return;
  $("listSec").hidden = true;
  const y = yIdx(), sn = P.data.years[y];
  $("pyearOut").textContent = sn.label; $("pScenario").hidden = !sn.scenario;
  $("pLegend").innerHTML = proxLegend(); $("aboutBody").innerHTML = proxAbout(); proxList();
};

/* ---------- wiring ---------- */
loadCity = async function (key) {
  P.data = null; S.lens = "classic"; P.on = false;
  await orig.loadCity(key);
  $("lensRow").hidden = true; $("classicCtl").hidden = false; $("proxCtl").hidden = true;
  if (!S.meta.has_proximity) return;
  try { P.data = await getJSON("data/" + key + "/proximity.json"); } catch (e) { return; }
  buildProxUI(); applyPersona(P.persona); recompute();
  $("lensRow").hidden = false;
  document.querySelectorAll("#lensRow button").forEach((b) => b.classList.toggle("on", b.dataset.lens === "classic"));
  const q = new URLSearchParams(location.search);
  if (q.get("lens") === "prox") {
    if (q.get("layer")) { P.layer = q.get("layer"); document.querySelector(`input[name=pmode][value=${P.layer}]`).checked = true; }
    if (q.get("shape")) { P.shape = q.get("shape"); $("shape").value = P.shape; }
    if (q.get("persona")) { $("persona").value = q.get("persona"); applyPersona(q.get("persona")); }
    if (q.get("y") !== null && q.get("y") !== "") { P.yf = +q.get("y"); $("pyear").value = P.yf; }
    setLens("prox");
  }
};
document.querySelectorAll("#lensRow button").forEach((b) => b.onclick = () => P.data && setLens(b.dataset.lens));
$("persona").onchange = (e) => { if (e.target.value !== "custom") applyPersona(e.target.value); refresh(); };
$("shape").onchange = (e) => { P.shape = e.target.value; rebuildField(); requestDraw(); };
$("pyear").oninput = (e) => { P.yf = +e.target.value; rebuildField(); buildClusters(); updateUI(); requestDraw(); };
document.querySelectorAll("input[name=pmode]").forEach((r) => r.onchange = () => { P.layer = r.value; updateUI(); requestDraw(); });
$("pRank").onchange = (e) => { P.rank = e.target.value; buildClusters(); updateUI(); requestDraw(); };
$("pyearPlay").onclick = () => {
  const N = P.data.years.length; if (N < 2) return;
  animate($("pyearPlay"), () => P.yf, (v) => { P.yf = v; $("pyear").value = v; rebuildField(); buildClusters(); updateUI(); draw(); }, 0, N - 1, 2200 * (N - 1));
};
