const OWNER = "rajgpawar21";
const REPO = "pune-traveltime-maps";
const WORKFLOW = "generate-pune-map.yml";
const API = "https://api.github.com";
const encoder = new TextEncoder();

function cors(request, env) {
  const origin = request.headers.get("Origin") || "";
  if (origin !== env.PAGES_ORIGIN) return {};
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Vary": "Origin"
  };
}
function response(data, status, headers = {}) {
  return new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", ...headers } });
}
function cookie(name, value, maxAge, sameSite = "Lax") {
  return name + "=" + value + "; Path=/; Max-Age=" + maxAge + "; Secure; HttpOnly; SameSite=" + sameSite;
}
function readCookie(request, name) {
  const raw = request.headers.get("Cookie") || "";
  const part = raw.split(";").map(x => x.trim()).find(x => x.startsWith(name + "="));
  return part ? part.slice(name.length + 1) : "";
}
function b64(bytes) {
  let binary = "";
  for (const byte of new Uint8Array(bytes)) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}
function unb64(value) {
  const base = value.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(base + "=".repeat((4 - base.length % 4) % 4));
  return Uint8Array.from(binary, c => c.charCodeAt(0));
}
async function signingKey(env) {
  const digest = await crypto.subtle.digest("SHA-256", encoder.encode(env.SESSION_SECRET));
  return crypto.subtle.importKey("raw", digest, { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}
async function createSession(login, env) {
  const payload = b64(encoder.encode(JSON.stringify({ login, exp: Date.now() + 8 * 60 * 60 * 1000 })));
  const signature = await crypto.subtle.sign("HMAC", await signingKey(env), encoder.encode(payload));
  return payload + "." + b64(new Uint8Array(signature));
}
async function readSession(request, env) {
  try {
    const header = request.headers.get("Authorization") || "";
    if (!header.startsWith("Bearer ")) return null;
    const parts = header.slice(7).split(".");
    if (parts.length !== 2) return null;
    const valid = await crypto.subtle.verify("HMAC", await signingKey(env), unb64(parts[1]), encoder.encode(parts[0]));
    if (!valid) return null;
    const session = JSON.parse(new TextDecoder().decode(unb64(parts[0])));
    if (!session.login || !session.exp || session.exp < Date.now()) return null;
    return session;
  } catch (_) { return null; }
}
async function gh(path, env, options = {}) {
  const result = await fetch(API + path, {
    ...options,
    headers: {
      "Accept": "application/vnd.github+json",
      "Authorization": "Bearer " + env.GITHUB_TOKEN,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "pune-traveltime-maps",
      ...(options.headers || {})
    }
  });
  const body = result.status === 204 ? null : await result.json().catch(() => ({}));
  if (!result.ok) throw new Error(body?.message || "GitHub API returned " + result.status);
  return body;
}
async function githubUser(accessToken) {
  const r = await fetch(API + "/user", { headers: { Authorization: "Bearer " + accessToken, Accept: "application/vnd.github+json" } });
  if (!r.ok) throw new Error("GitHub could not verify your account.");
  return r.json();
}
function allowed(login, env) {
  const configured = (env.ALLOWED_GITHUB_USER || "*").toLowerCase();
  return configured === "*" || configured === (login || "").toLowerCase();
}
async function route(request, env) {
  const url = new URL(request.url);
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(request, env) });
  if (url.pathname === "/login") {
    const state = b64(crypto.getRandomValues(new Uint8Array(24)));
    const callback = url.origin + "/oauth/callback";
    const authorize = new URL("https://github.com/login/oauth/authorize");
    authorize.searchParams.set("client_id", env.GITHUB_CLIENT_ID);
    authorize.searchParams.set("redirect_uri", callback);
    authorize.searchParams.set("scope", "read:user");
    authorize.searchParams.set("state", state);
    const headers = new Headers({ Location: authorize.toString() });
    headers.append("Set-Cookie", cookie("pune_oauth_state", state, 600));
    return new Response(null, { status: 302, headers });
  }
  if (url.pathname === "/oauth/callback") {
    const state = url.searchParams.get("state") || "";
    const saved = readCookie(request, "pune_oauth_state");
    if (!state || state !== saved || !url.searchParams.get("code")) return new Response("OAuth state validation failed.", { status: 400 });
    const tokenResponse = await fetch("https://github.com/login/oauth/access_token", {
      method: "POST", headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ client_id: env.GITHUB_CLIENT_ID, client_secret: env.GITHUB_CLIENT_SECRET, code: url.searchParams.get("code"), redirect_uri: url.origin + "/oauth/callback" })
    });
    const tokenData = await tokenResponse.json();
    if (!tokenData.access_token) return new Response("GitHub sign-in failed.", { status: 401 });
    const user = await githubUser(tokenData.access_token);
    if (!allowed(user.login, env)) return new Response("This GitHub account is not authorised to generate maps.", { status: 403 });
    const value = await createSession(user.login, env);
    const headers = new Headers({ Location: env.PAGES_URL + "#session=" + encodeURIComponent(value) });
    headers.append("Set-Cookie", cookie("pune_oauth_state", "", 0));
    return new Response(null, { status: 302, headers });
  }
  if (url.pathname === "/logout" && request.method === "POST") {
    return response({ ok: true }, 200, { ...cors(request, env), "Set-Cookie": cookie("pune_session", "", 0, "None") });
  }
  if (url.pathname === "/api/me") {
    const session = await readSession(request, env);
    if (!session || !allowed(session.login, env)) return response({ error: "Sign in with the authorised GitHub account to generate maps." }, 401, cors(request, env));
    return response({ login: session.login }, 200, cors(request, env));
  }
  if (url.pathname === "/api/status") {
    const runs = await gh("/repos/" + OWNER + "/" + REPO + "/actions/workflows/" + WORKFLOW + "/runs?branch=main&per_page=1", env);
    const run = runs.workflow_runs?.[0];
    return response({ run: run ? { id: run.id, run_number: run.run_number, run_attempt: run.run_attempt, status: run.status, conclusion: run.conclusion, html_url: run.html_url } : null }, 200, cors(request, env));
  }
  if (url.pathname === "/api/generate" && request.method === "POST") {
    const origin = request.headers.get("Origin") || "";
    if (origin !== env.PAGES_ORIGIN) return response({ error: "Invalid website origin." }, 403, cors(request, env));
    const session = await unseal(readCookie(request, "pune_session"), env);
    if (!session || !allowed(session.login, env)) return response({ error: "Sign in with the authorised GitHub account first." }, 401, cors(request, env));
    const active = await gh("/repos/" + OWNER + "/" + REPO + "/actions/workflows/" + WORKFLOW + "/runs?branch=main&per_page=1", env);
    const latest = active.workflow_runs?.[0];
    if (latest && ["queued", "in_progress", "waiting", "requested"].includes(latest.status)) {
      return response({ error: "A map generation is already queued or running. Please wait for it to finish." }, 409, cors(request, env));
    }
    const result = await gh("/repos/" + OWNER + "/" + REPO + "/actions/workflows/" + WORKFLOW + "/dispatches", env, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ref: "main" })
    });
    return response({ ok: true, result: result || "Workflow dispatch accepted. Check the Actions page for the run." }, 202, cors(request, env));
  }
  return new Response("Pune Travel-Time Maps API", { status: 200 });
}
export default {
  async fetch(request, env) {
    try { return await route(request, env); }
    catch (error) { return response({ error: error.message || "Unexpected server error." }, 500, cors(request, env)); }
  }
};