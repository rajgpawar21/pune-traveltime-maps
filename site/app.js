const API = window.PUNE_MAP_API || "https://pune-traveltime-maps.rajgpawar21.workers.dev";
const returnedSession = new URLSearchParams(location.hash.slice(1)).get("session");
if (returnedSession) {
  sessionStorage.setItem("pune_session", returnedSession);
  history.replaceState(null, "", location.pathname + location.search);
}
const $ = (id) => document.getElementById(id);
let currentRunId = null;
let pollTimer = null;
const stages = ["download", "osrm", "matrix", "mds", "publish"];
function setStatus(label, message, progress, state) {
  $("status-pill").textContent = label;
  $("status-pill").className = "pill " + (state || "");
  $("status-message").textContent = message;
  $("progress-bar").style.width = progress + "%";
}
function setStage(active) {
  stages.forEach((name, i) => {
    const node = document.querySelector('[data-stage="' + name + '"]');
    node.className = i < active ? "done" : i === active ? "active" : "";
  });
}
async function api(path, options = {}) {
  const token = sessionStorage.getItem("pune_session");
  const response = await fetch(API + path, { ...options,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}), ...(options.headers || {}) } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Request failed (" + response.status + ")");
  return data;
}
function showLoggedIn(user) {
  $("auth-state").textContent = "Signed in as @" + user;
  $("login").hidden = true;
  $("logout").hidden = false;
  $("generate").disabled = false;
  $("generate").textContent = "↻  Generate Pune Map";
}
async function refreshAuth() {
  try { const data = await api("/api/me"); showLoggedIn(data.login); }
  catch (_) {
    $("auth-state").textContent = "Public map";
    $("login").hidden = false;
    $("login").href = API + "/login";
    $("generate").disabled = true;
    $("generate").textContent = "Sign in to generate";
  }
}
function renderHistory(items) {
  const select = $("history-select");
  select.innerHTML = "";
  if (!items || !items.length) {
    select.add(new Option("Latest map", "latest"));
    $("history-count").textContent = "No versions";
    $("history-detail").textContent = "No generated maps have been archived yet.";
    $("empty-map").hidden = false;
    return;
  }
  items.forEach((item, index) => select.add(new Option(
    (index === 0 ? "Latest · " : "") + new Date(item.generated_at).toLocaleString() + " · Run " + item.run_number,
    item.path
  )));
  $("history-count").textContent = items.length + (items.length === 1 ? " version" : " versions");
  $("history-detail").textContent = "Each successful generation is retained in the public archive.";
  $("empty-map").hidden = true;
  select.value = "latest";
}
async function loadHistory() {
  try {
    const response = await fetch("./history.json", { cache: "no-store" });
    if (!response.ok) throw new Error("No history yet");
    const data = await response.json();
    renderHistory(data.items || []);
  } catch (_) { renderHistory([]); }
}
$("history-select").addEventListener("change", () => {
  const value = $("history-select").value;
  const path = value === "latest" ? "./maps/latest/pune_travel_time_map.html" : "./" + value + "/pune_travel_time_map.html";
  $("map-frame").src = path;
  $("open-map").href = path;
  $("map-title").textContent = value === "latest" ? "Latest Pune cartogram" : "Archived Pune cartogram";
});
async function checkRun() {
  try {
    const data = await api("/api/status");
    const run = data.run;
    if (!run) return;
    $("logs").href = run.html_url;
    $("logs").hidden = false;
    const status = run.status;
    if (status === "completed") {
      clearInterval(pollTimer); pollTimer = null;
      if (run.conclusion === "success") {
        setStatus("Published", "Generation completed. The latest map and archive are updated.", 100, "success");
        setStage(stages.length);
        $("generate").disabled = false;
        await loadHistory();
        $("history-select").value = "latest";
        $("history-select").dispatchEvent(new Event("change"));
      } else {
        setStatus("Failed", "The workflow did not complete successfully. Open the run logs for details.", 0, "error");
        $("generate").disabled = false;
      }
      return;
    }
    const steps = run.steps || [];
    const completed = (name) => steps.some(step => step.name.includes(name) && step.status === "completed" && step.conclusion === "success");
    const active = (name) => steps.some(step => step.name.includes(name) && step.status === "in_progress");
    let stage = 0, progress = 8, detail = "Waiting for a GitHub-hosted runner.";
    if (completed("Download regional Pune road data")) { stage = 1; progress = 20; detail = "Regional road data downloaded."; }
    if (completed("Build self-hosted OSRM")) { stage = 2; progress = 38; detail = "OSRM routing engine is ready."; }
    if (active("Run complete cartogram pipeline")) { stage = 2; progress = 55; detail = "Calculating the travel-time matrix and constructing the cartogram."; }
    if (completed("Run complete cartogram pipeline")) { stage = 4; progress = 82; detail = "Cartogram generated. Preparing the public map and archive."; }
    if (active("Assemble public website")) { stage = 4; progress = 90; detail = "Preparing the latest map and historical versions."; }
    if (active("Publish website")) { stage = 4; progress = 96; detail = "Publishing the website and map archive."; }
    setStage(stage);
    setStatus("Running", detail, progress, "running");
    if (!pollTimer) pollTimer = setInterval(checkRun, 10000);
  } catch (error) { setStatus("Checking", error.message, 5, "running"); }
}
$("generate").addEventListener("click", async () => {
  $("generate").disabled = true;
  $("logs").hidden = true;
  setStatus("Queued", "Requesting a new generation from GitHub Actions…", 3, "running");
  setStage(0);
  try {
    const result = await api("/api/generate", { method: "POST", body: "{}" });
    currentRunId = result.run_id || null;
    setStatus("Queued", "Generation queued. GitHub Actions will start shortly.", 5, "running");
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(checkRun, 10000);
    setTimeout(checkRun, 3000);
  } catch (error) {
    setStatus("Could not start", error.message, 0, "error");
    $("generate").disabled = false;
  }
});
$("logout").addEventListener("click", () => {
  sessionStorage.removeItem("pune_session");
  location.reload();
});
refreshAuth();
loadHistory();
checkRun();