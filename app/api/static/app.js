"use strict";
// No inline handlers anywhere: every click goes through the delegated listeners at the
// bottom, so the page runs under a strict Content-Security-Policy (script-src 'self').

const $ = sel => document.querySelector(sel);
// Escape anything server-provided before it goes into innerHTML: result messages can
// contain raw text from upstream app APIs, which must never run as markup.
const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const PROVIDERS = { google: "Google", microsoft: "Microsoft" };
let services = [];
let destinations = [];
let authState = null;
let openTile = null;      // "s:<service name>" or "d:<destination type>"
let loginPoll = null;
let refreshTimer = null;

// ── helpers ────────────────────────────────────────────────────────────────

async function api(path, options = {}) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  // A 401 anywhere but the auth endpoints means the session ended: back to sign-in.
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    authState = await api("/api/auth/status").catch(() => authState);
    showAuthScreen();
  }
  if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
  return body;
}

const sendJson = (method, data) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.toggle("err", isError);
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { t.hidden = true; }, isError ? 6000 : 3000);
}

// In-page confirm (window.confirm can't be styled and blocks the page).
function ask({ title, text, ok, code = "" }) {
  const dlg = $("#confirm");
  $("#c-title").textContent = title;
  $("#c-text").textContent = text;
  $("#c-ok").textContent = ok;
  $("#c-code").textContent = code;
  $("#c-code").hidden = !code;
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise(resolve =>
    dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true }));
}

function when(iso) {
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const days = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 864e5);
  if (days === 0) return `Today ${time}`;
  if (days === 1) return `Yesterday ${time}`;
  return `${d.toLocaleDateString([], { day: "numeric", month: "short" })} ${time}`;
}

function size(bytes) {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

const destName = type => destinations.find(d => d.type === type)?.display_name || type;
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

// ── status ─────────────────────────────────────────────────────────────────

const missingSettings = svc => svc.env_vars.filter(ev => ev.required && !ev.configured);
const failedUploads = lr => Object.entries(lr?.uploads || {}).filter(([, u]) => !u.ok);

function serviceStatus(svc) {
  if (!svc.enabled) return ["off", "Disabled"];
  if (svc.is_running) return ["run", "Running"];
  if (missingSettings(svc).length) return ["setup", "Needs setup"];
  const lr = svc.last_result;
  if (!lr) return ["never", "Not run yet"];
  if (!lr.success) return ["fail", "Failed"];
  if (failedUploads(lr).length) return ["fail", "Upload failed"];
  return ["ok", "OK"];
}

function serviceProblem(svc) {
  const lr = svc.last_result;
  if (!svc.enabled || !lr) return "";
  if (!lr.success) return lr.message;
  return failedUploads(lr).map(([type, u]) => `Couldn't upload to ${destName(type)}. ${u.message}`).join(" ");
}

function destinationStatus(dest) {
  if (!dest.enabled) return ["off", "Off"];
  if (dest.login_provider && !dest.login?.connected) return ["setup", "Needs login"];
  if (dest.env_vars.some(ev => ev.required && !ev.configured)) return ["setup", "Needs setup"];
  const failed = services.some(s => s.enabled && s.last_result?.uploads?.[dest.type]?.ok === false);
  return failed ? ["fail", "Failed"] : ["ok", "On"];
}

// ── rendering ──────────────────────────────────────────────────────────────

function tileHead(name, [cls, word]) {
  return `<button class="tile-head" data-act="open" aria-haspopup="dialog">
    <span class="tile-name">${esc(name)}</span>
    <span class="badge ${cls}">${esc(word)}</span>
  </button>`;
}

function field(id, ev) {
  const state = ev.configured ? ["set", "saved"] : ev.required ? ["missing", "missing"] : ["", "optional"];
  const placeholder = ev.secret && ev.configured ? "Saved. Leave blank to keep it." : "";
  return `<div class="field">
    <label for="${esc(id)}">${esc(ev.label)} <span class="state ${state[0]}">${state[1]}</span></label>
    <input id="${esc(id)}" data-key="${esc(ev.key)}" type="${ev.secret ? "password" : "text"}"
      value="${ev.secret ? "" : esc(ev.value)}" placeholder="${esc(placeholder)}"
      autocomplete="${ev.secret ? "new-password" : "off"}" spellcheck="false" />
    ${ev.description ? `<div class="hint">${esc(ev.description)}</div>` : ""}
  </div>`;
}

function fieldList(prefix, envVars) {
  const basic = envVars.filter(ev => !ev.advanced).map(ev => field(`${prefix}-${ev.key}`, ev)).join("");
  const advanced = envVars.filter(ev => ev.advanced).map(ev => field(`${prefix}-${ev.key}`, ev)).join("");
  return `<div class="fields">${basic}</div>` +
    (advanced ? `<details><summary>Advanced</summary><div class="fields">${advanced}</div></details>` : "");
}

function serviceBody(svc) {
  const lr = svc.last_result;
  const miss = missingSettings(svc);
  const problem = serviceProblem(svc);
  const facts = lr ? `<dl class="facts">
      <dt>Last backup</dt><dd>${esc(when(lr.finished_at))}</dd>
      ${lr.size_bytes != null ? `<dt>Size</dt><dd>${esc(size(lr.size_bytes))}</dd>` : ""}
      <dt>Took</dt><dd>${Math.max(0, Math.round((new Date(lr.finished_at) - new Date(lr.started_at)) / 1000))} s</dd>
      ${lr.uploads ? `<dt>Delivered to</dt><dd class="deliv">${Object.entries(lr.uploads).map(([type, u]) =>
        `<span class="${u.ok ? "y" : "n"}">${u.ok ? "✓" : "✗"} ${esc(destName(type))}</span>`).join("")}</dd>` : ""}
    </dl>`
    : `<p class="desc">No backup yet.${miss.length ? ` Fill in ${miss.length === 1 ? "the missing setting" : "the missing settings"}, then run it once to check.` : ""}</p>`;
  return `<div class="tile-body">
    <div>
      <p class="desc">${esc(svc.description)}</p>
      ${problem ? `<p class="problem">${esc(problem)}</p>` : ""}
      <h4>Last run</h4>
      ${facts}
    </div>
    <div>
      <h4>Settings${miss.length ? ` · ${miss.length} missing` : ""}</h4>
      ${fieldList(`s-${svc.name}`, svc.env_vars)}
    </div>
    <div class="tile-foot">
      <label class="switch"><input type="checkbox" data-act="svc-toggle" ${svc.enabled ? "checked" : ""} /> Back up on schedule</label>
      <div class="spacer"></div>
      <button class="btn quiet" data-act="svc-remove">Remove from dashboard</button>
      <button class="btn" data-act="svc-save">Save</button>
      <button class="btn primary" data-act="svc-run" ${!svc.enabled || svc.is_running || miss.length ? "disabled" : ""}>Back up now</button>
    </div>
  </div>`;
}

function loginSection(dest) {
  if (!dest.login_provider) return "";
  const who = PROVIDERS[dest.login_provider];
  if (dest.login?.connected) {
    return `<p class="connected">Connected${dest.login.account ? ` as ${esc(dest.login.account)}` : ""}.</p>
      <button class="btn" data-act="dst-logout">Disconnect</button>`;
  }
  return `<button class="btn primary" data-act="dst-login" ${dest.login_available ? "" : "disabled"}>Log in with ${who}</button>
    ${dest.login_available ? "" : `<div class="hint">This build has no built-in ${who} app. Use your own under Advanced.</div>`}`;
}

function googleOwnClient(dest) {
  const callback = `${location.origin}/api/destinations/google_drive/oauth/callback`;
  return `<details>
    <summary>Use your own Google OAuth client</summary>
    <div class="fields">
      <p class="muted small">${dest.credentials_uploaded ? "client_secret.json is uploaded." : "No client_secret.json uploaded yet."}</p>
      <div class="field">
        <label for="g-cred">OAuth client secret JSON</label>
        <textarea id="g-cred" rows="4" spellcheck="false" placeholder="Paste your client_secret.json"></textarea>
        <div class="hint">Google Cloud Console → APIs &amp; Services → Credentials → OAuth 2.0 Client IDs → Download JSON</div>
      </div>
      <div><button class="btn" data-act="g-upload">Upload client_secret.json</button></div>
      <div class="field">
        <label>Redirect URI to add to your OAuth client</label>
        <code class="code-block">${esc(callback)}</code>
        <div class="hint">Google only accepts localhost or HTTPS addresses here.</div>
      </div>
      <div><button class="btn" data-act="g-auth" ${dest.credentials_uploaded ? "" : "disabled"}>Authorize with my client</button></div>
    </div>
  </details>`;
}

function sftpKey() {
  return `<details>
    <summary>Log in with an SSH key instead</summary>
    <div class="fields">
      <div class="field">
        <label for="sftp-key">SSH private key</label>
        <textarea id="sftp-key" rows="3" spellcheck="false" placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea>
        <div class="hint">Stored on this server, readable only by the app.</div>
      </div>
      <div><button class="btn" data-act="sftp-key">Save key</button></div>
    </div>
  </details>`;
}

function destinationBody(dest) {
  const failures = services
    .filter(s => s.enabled && s.last_result?.uploads?.[dest.type]?.ok === false)
    .map(s => `${s.display_name}: ${s.last_result.uploads[dest.type].message}`);
  return `<div class="tile-body">
    <div>
      <p class="desc">${esc(dest.description)}</p>
      ${failures.map(f => `<p class="problem">${esc(f)}</p>`).join("")}
      ${loginSection(dest)}
    </div>
    <div>
      ${dest.env_vars.length ? `<h4>Settings</h4>${fieldList(`d-${dest.type}`, dest.env_vars)}` : ""}
      ${dest.type === "sftp" ? sftpKey() : ""}
      ${dest.type === "google_drive" ? googleOwnClient(dest) : ""}
    </div>
    <div class="tile-foot">
      <label class="switch"><input type="checkbox" data-act="dst-toggle" ${dest.enabled ? "checked" : ""} /> Upload backups here</label>
      <div class="spacer"></div>
      <button class="btn" data-act="dst-test">Test connection</button>
      ${dest.env_vars.length ? `<button class="btn primary" data-act="dst-save">Save</button>` : ""}
    </div>
  </div>`;
}

function renderSummary() {
  const active = services.filter(s => s.enabled && !missingSettings(s).length);
  const good = active.filter(s => serviceStatus(s)[0] === "ok").length;
  const shipping = destinations.some(d => d.enabled);
  const done = shipping ? "backed up and delivered" : "backed up";
  $("#sum-title").textContent =
    !services.length ? "Add the apps you run to start backing them up."
      : !active.length ? "Nothing is ready to back up yet."
        : good === active.length ? `All ${plural(active.length, "service")} ${done}.`
          : `${good} of ${plural(active.length, "service")} ${done}.`;

  const notes = [];
  const link = (tile, label) => `<button class="link" data-goto="${esc(tile)}">${label}</button>`;
  for (const s of services) {
    const [cls] = serviceStatus(s);
    if (cls === "fail") notes.push(`${esc(s.display_name)}: ${esc(serviceProblem(s).slice(0, 140))} ${link(`s:${s.name}`, "Open")}`);
    if (cls === "setup") notes.push(`${esc(s.display_name)} needs ${plural(missingSettings(s).length, "setting")} before its first backup. ${link(`s:${s.name}`, "Set up")}`);
  }
  for (const d of destinations) {
    if (destinationStatus(d)[0] === "setup") notes.push(`${esc(d.display_name)} is switched on but not connected. ${link(`d:${d.type}`, "Connect")}`);
  }
  const off = services.filter(s => !s.enabled).length;
  if (off) notes.push(`${off} disabled.`);
  if (services.length && !shipping) notes.push(`Backups stay on this server only. Switch on a destination below to keep a copy elsewhere.`);
  $("#sum-list").innerHTML = notes.map(n => `<li>${n}</li>`).join("");
}

// Re-rendering replaces the open details' inputs; carry over what the user was typing.
function keepTyping(render) {
  const typed = [...document.querySelectorAll("#detail-body input[id]:not([type=checkbox]), #detail-body textarea[id]")]
    .map(el => [el.id, el.value]);
  const focused = document.activeElement?.id;
  render();
  for (const [id, value] of typed) {
    const el = document.getElementById(id);
    if (el) el.value = value;
  }
  if (focused) document.getElementById(focused)?.focus();
}

function renderDetail() {
  const item = openTile && tileItem({ dataset: { tile: openTile } });
  const dlg = $("#detail");
  if (!item) {
    openTile = null;
    if (dlg.open) dlg.close();
    return;
  }
  const isService = openTile.startsWith("s:");
  const [cls, word] = isService ? serviceStatus(item) : destinationStatus(item);
  $("#detail-name").textContent = item.display_name;
  $("#detail-badge").className = `badge ${cls}`;
  $("#detail-badge").textContent = word;
  const body = $("#detail-body");
  body.dataset.tile = openTile;
  keepTyping(() => { body.innerHTML = isService ? serviceBody(item) : destinationBody(item); });
  if (!dlg.open) dlg.showModal();
}

function render() {
  $("#svc-grid").innerHTML = services.map(svc =>
    `<div class="tile ${svc.enabled ? "" : "is-off"}" data-tile="${esc(`s:${svc.name}`)}">
      ${tileHead(svc.display_name, serviceStatus(svc))}
    </div>`).join("") + `<button class="add-tile" data-act="catalog">+ Add a service</button>`;
  $("#dst-grid").innerHTML = destinations.map(dest =>
    `<div class="tile ${dest.enabled ? "" : "is-off"}" data-tile="${esc(`d:${dest.type}`)}">
      ${tileHead(dest.display_name, destinationStatus(dest))}
    </div>`).join("");
  if (openTile) renderDetail();
  renderSummary();
  $("#svc-count").textContent = services.length ? plural(services.length, "app") : "";
  $("#dst-count").textContent = `${destinations.filter(d => d.enabled).length} switched on`;
}

async function refresh() {
  try {
    [services, destinations] = await Promise.all([api("/api/services"), api("/api/destinations")]);
    render();
  } catch (e) {
    toast(`Couldn't load: ${e.message}`, true);
  }
}

function openAndShow(key) {
  openTile = key;
  renderDetail();
}

// ── services ───────────────────────────────────────────────────────────────

function typedValues(tile) {
  const updates = {};
  tile.querySelectorAll("input[data-key]").forEach(inp => {
    const value = inp.value.trim();
    if (value) updates[inp.dataset.key] = value;
  });
  return updates;
}

async function saveService(svc, tile) {
  const updates = typedValues(tile);
  if (!Object.keys(updates).length) return toast("Nothing changed");
  await api(`/api/env-vars/${svc.type}`, sendJson("PUT", { updates }));
  tile.querySelectorAll("input[type=password]").forEach(inp => { inp.value = ""; });
  toast("Saved");
  await refresh();
}

async function setServiceEnabled(svc, enabled) {
  if (!enabled && !await ask({
    title: `Disable ${svc.display_name}?`,
    text: "It won't be backed up on schedule until you turn it back on. Backups already made are kept.",
    ok: "Disable",
  })) return render();
  await api(`/api/services/${svc.name}/enabled`, sendJson("PUT", { enabled }));
  toast(`${svc.display_name} ${enabled ? "enabled" : "disabled"}`);
  await refresh();
}

async function removeService(svc) {
  if (!await ask({
    title: `Remove ${svc.display_name} from the dashboard?`,
    text: "Its backups stop. Backups already made and its saved settings are kept, so adding it back picks up where you left off.",
    ok: "Remove",
  })) return;
  await api(`/api/services/${svc.name}`, { method: "DELETE" });
  openTile = null;
  $("#detail").close();
  toast(`${svc.display_name} removed. Add it back any time.`);
  await refresh();
}

async function runService(svc) {
  await api(`/api/services/${svc.name}/trigger`, { method: "POST" });
  toast(`Backing up ${svc.display_name}…`);
  setTimeout(refresh, 1500);
}

async function openCatalog() {
  $("#cat-q").value = "";
  openCatalog.items = await api("/api/catalog");
  renderCatalog();
  $("#catalog").showModal();
}

function renderCatalog() {
  const q = $("#cat-q").value.trim().toLowerCase();
  const items = (openCatalog.items || []).filter(c => !q || `${c.display_name} ${c.description}`.toLowerCase().includes(q));
  $("#cat-list").innerHTML = items.map(c => `<div class="cat-item">
      <div class="row"><span class="tile-name">${esc(c.display_name)}</span>
        ${c.added ? `<span class="badge off">Added</span>` : `<button class="btn" data-add="${esc(c.type)}">Add</button>`}</div>
      <p>${esc(c.description)}</p>
      ${c.settings.length ? `<div class="needs">Needs: ${c.settings.map(esc).join(", ")}</div>` : ""}
    </div>`).join("") || `<p class="muted">No app matches “${esc(q)}”.</p>`;
}

async function addService(type) {
  await api("/api/services", sendJson("POST", { type }));
  $("#catalog").close();
  await refresh();
  openAndShow(`s:${type}`);
  toast(`${services.find(s => s.name === type)?.display_name || type} added. Fill in its settings.`);
}

// ── destinations ───────────────────────────────────────────────────────────

// Returns true when something was saved.
async function saveDestination(dest, tile, quiet = false) {
  const updates = typedValues(tile);
  if (!Object.keys(updates).length) {
    if (!quiet) toast("Nothing changed");
    return false;
  }
  await api(`/api/destinations/${dest.type}/env-vars`, sendJson("PUT", { updates }));
  if (!quiet) {
    toast("Saved");
    await refresh();
  }
  return true;
}

async function setDestinationEnabled(dest, enabled) {
  if (!enabled && !await ask({
    title: `Stop uploading to ${dest.display_name}?`,
    text: "New backups stay on this server and any other destinations. Copies already uploaded are kept.",
    ok: "Stop uploading",
  })) return render();
  await api(`/api/destinations/${dest.type}/env-vars`, sendJson("PUT", { updates: { [dest.enabled_key]: enabled ? "true" : "false" } }));
  toast(`${dest.display_name}: uploads ${enabled ? "on" : "off"}`);
  await refresh();
}

async function testDestination(dest, tile) {
  await saveDestination(dest, tile, true);
  toast("Testing connection…");
  const r = await api(`/api/destinations/${dest.type}/test`, { method: "POST" });
  if (r.ok) {
    toast(r.message);
  } else if (r.fingerprint) {
    const trust = await ask({
      title: "Trust this server?",
      text: "This is the first connection to this server. Check that its host key matches by running " +
        "ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub on the server.",
      code: r.fingerprint,
      ok: "Trust and connect",
    });
    if (!trust) return;
    await api(`/api/destinations/${dest.type}/env-vars`, sendJson("PUT", { updates: { SFTP_HOST_FINGERPRINT: r.fingerprint } }));
    return testDestination(dest, tile);
  } else {
    toast(r.message, true);
  }
  await refresh();
}

function setLoginStatus(text, isError = false) {
  const el = $("#login-status");
  el.textContent = text;
  el.classList.toggle("status-error", isError);
}

async function startLogin(dest, tile) {
  const who = PROVIDERS[dest.login_provider];
  await saveDestination(dest, tile, true);
  const login = await api(`/api/destinations/${dest.type}/login`, { method: "POST" });
  const host = new URL(login.verification_url).host;
  $("#login-title").textContent = `Log in with ${who}`;
  $("#login-steps").textContent = `Open ${host} on any device, enter this code, and sign in with your ${who} account.`;
  $("#login-code").textContent = login.user_code;
  $("#login-link").href = login.verification_url;
  $("#login-link").textContent = `Open ${host}`;
  setLoginStatus("Waiting for you to finish signing in…");
  $("#login-modal").showModal();
  clearInterval(loginPoll);
  loginPoll = setInterval(async () => {
    try {
      const s = await api(`/api/destinations/logins/${login.id}`);
      if (s.status === "connected") {
        $("#login-modal").close();
        toast(`${dest.display_name} connected${s.message ? ` as ${s.message}` : ""}`);
        refresh();
      } else if (s.status === "failed") {
        clearInterval(loginPoll);
        setLoginStatus(s.message, true);
      }
    } catch (e) {
      clearInterval(loginPoll);
      setLoginStatus(e.message, true);
    }
  }, 3000);
}

async function copyLoginCode() {
  try {
    await navigator.clipboard.writeText($("#login-code").textContent);
    toast("Code copied");
  } catch {
    // The clipboard API needs HTTPS or localhost; on a LAN IP select the code instead.
    getSelection().selectAllChildren($("#login-code"));
    toast("Code selected: press Ctrl+C or ⌘C");
  }
}

async function disconnect(dest) {
  if (!await ask({
    title: `Disconnect ${dest.display_name}?`,
    text: "Backups stop uploading there until you log in again. Copies already there are kept.",
    ok: "Disconnect",
  })) return;
  await api(`/api/destinations/${dest.type}/login`, { method: "DELETE" });
  toast(`${dest.display_name} disconnected`);
  await refresh();
}

async function saveSftpKey(dest, tile) {
  const box = $("#sftp-key");
  const key = box.value.trim();
  if (!key) return toast("Paste a private key first", true);
  await saveDestination(dest, tile, true);
  await api("/api/destinations/sftp/key", sendJson("POST", { key }));
  box.value = "";
  toast("SSH key saved");
  await refresh();
}

async function uploadGoogleCredentials() {
  const box = $("#g-cred");
  const json_content = box.value.trim();
  if (!json_content) return toast("Paste the client_secret.json contents first", true);
  const data = await api("/api/destinations/google_drive/credentials", sendJson("POST", { json_content }));
  box.value = "";
  toast(`Saved (client ID ${data.client_id})`);
  await refresh();
}

async function authorizeOwnGoogleClient() {
  const { auth_url } = await api("/api/destinations/google_drive/oauth/start",
    sendJson("POST", { redirect_base: location.origin }));
  window.open(auth_url, "_blank", "noopener,noreferrer");
  toast("Finish signing in in the new tab, then come back here");
  let polls = 0;
  const poll = setInterval(async () => {
    if (++polls > 60) return clearInterval(poll);
    const dests = await api("/api/destinations").catch(() => null);
    if (dests?.find(d => d.type === "google_drive")?.login?.connected) {
      clearInterval(poll);
      toast("Google Drive connected");
      refresh();
    }
  }, 3000);
}

// ── settings ───────────────────────────────────────────────────────────────

function showSchedule(s) {
  $("#next-run").innerHTML = s.interval_hours > 0
    ? `Backs up every <b>${esc(plural(s.interval_hours, "hour"))}</b>`
    : `Backs up daily at <b>${esc(s.daily_at)}</b>`;
}

async function openSettings() {
  const s = await api("/api/settings");
  showSchedule(s);
  $("#setting-daily-at").value = s.daily_at;
  $("#setting-interval-hours").value = s.interval_hours;
  $("#setting-keep-days").value = s.keep_days;
  $("#setting-remote-keep-count").value = s.remote_keep_count ?? 3;
  $("#setting-run-on-start").checked = Boolean(s.run_on_start);
  $("#auth-mode").value = authState.mode;
  $("#auth-proxies").value = authState.trusted_proxies;
  $("#auth-header").value = authState.proxy_header;
  showAuthModeHint();
  $("#settings-dialog").showModal();
}

async function saveSchedule(e) {
  e.preventDefault();
  const body = {
    daily_at: $("#setting-daily-at").value,
    interval_hours: parseInt($("#setting-interval-hours").value, 10) || 0,
    keep_days: parseInt($("#setting-keep-days").value, 10),
    remote_keep_count: parseInt($("#setting-remote-keep-count").value, 10),
    run_on_start: $("#setting-run-on-start").checked,
  };
  await api("/api/settings", sendJson("PUT", body));
  showSchedule(body);
  toast("Schedule saved");
}

function showAuthModeHint() {
  const mode = $("#auth-mode").value;
  $("#proxy-fields").hidden = mode !== "proxy";
  $("#auth-mode-hint").textContent = {
    password: "Recommended. Sessions last up to 30 days; 5 wrong passwords lock that device out for 15 minutes.",
    proxy: "If the proxy addresses are wrong you'll be locked out; set AUTH_MODE=password in .env to recover.",
    off: "Anyone who can reach this page can read and change every credential here. Only use on a network nobody else can reach.",
  }[mode];
}

async function saveAuthSettings(e) {
  e.preventDefault();
  const mode = $("#auth-mode").value;
  if (mode === "off" && !await ask({
    title: "Turn off sign-in?",
    text: "Anyone who can reach this page gets full access to every saved credential.",
    ok: "Turn off sign-in",
  })) return;
  await api("/api/auth/settings", sendJson("PUT", {
    mode,
    trusted_proxies: $("#auth-proxies").value,
    proxy_header: $("#auth-header").value,
    current_password: $("#auth-current").value,
  }));
  $("#auth-current").value = "";
  toast("Sign-in settings saved");
  authState = await api("/api/auth/status");
  if (!authState.authenticated) {
    $("#settings-dialog").close();
    showAuthScreen();
  }
  $("#logout-btn").hidden = authState.mode !== "password";
}

async function changePassword() {
  const next = $("#auth-new").value;
  if (next !== $("#auth-new-confirm").value) return toast("The new passwords don't match", true);
  await api("/api/auth/password", sendJson("POST", {
    current_password: $("#auth-current").value,
    new_password: next,
  }));
  for (const id of ["#auth-current", "#auth-new", "#auth-new-confirm"]) $(id).value = "";
  toast("Password changed. Other devices are signed out.");
}

// ── sign-in ────────────────────────────────────────────────────────────────

function showAuthScreen() {
  clearInterval(refreshTimer);
  document.querySelectorAll("dialog[open]").forEach(d => d.close());
  const setup = Boolean(authState?.setup_required);
  const proxy = authState?.mode === "proxy";
  $("#app").hidden = true;
  $("#top-nav").hidden = true;
  $("#auth-heading").textContent = setup ? "Create the admin password" : "Sign in";
  $("#auth-intro").textContent = proxy
    ? "This app expects your sign-in proxy (Authelia, Authentik…) to log you in. Open it through the proxy."
    : setup
      ? "You need the one-time setup code from the server logs."
      : "Enter the admin password.";
  $("#setup-code-field").hidden = !setup;
  $("#auth-confirm-field").hidden = !setup;
  $("#auth-password-field").hidden = proxy;
  $("#auth-password").required = !proxy;
  $("#auth-password").autocomplete = setup ? "new-password" : "current-password";
  $("#auth-submit").hidden = proxy;
  $("#auth-submit").textContent = setup ? "Create password" : "Sign in";
  $("#auth-error").textContent = "";
  $("#auth-screen").hidden = false;
  (setup ? $("#auth-setup-code") : $("#auth-password")).focus();
}

async function submitAuth(e) {
  e.preventDefault();
  const setup = authState.setup_required;
  const password = $("#auth-password").value;
  if (setup && password !== $("#auth-confirm").value) {
    $("#auth-error").textContent = "The passwords don't match.";
    return;
  }
  try {
    await api(setup ? "/api/auth/setup" : "/api/auth/login",
      sendJson("POST", setup ? { setup_code: $("#auth-setup-code").value, password } : { password }));
  } catch (err) {
    $("#auth-error").textContent = err.message;
    return;
  }
  $("#auth-form").reset();
  authState = await api("/api/auth/status");
  startApp();
}

function startApp() {
  $("#auth-screen").hidden = true;
  $("#app").hidden = false;
  $("#top-nav").hidden = false;
  $("#logout-btn").hidden = authState.mode !== "password";
  api("/api/settings").then(showSchedule).catch(() => {});
  refresh();
  clearInterval(refreshTimer);
  refreshTimer = setInterval(refresh, 15000);
}

async function logout() {
  await api("/api/auth/logout", { method: "POST" }).catch(() => {});
  location.reload();
}

async function checkAuth() {
  try {
    authState = await api("/api/auth/status");
  } catch (e) {
    toast(`Can't reach the server: ${e.message}`, true);
    return;
  }
  if (authState.authenticated) startApp();
  else showAuthScreen();
}

// ── events ─────────────────────────────────────────────────────────────────

const tileActions = {
  "svc-save": saveService,
  "svc-run": runService,
  "svc-remove": removeService,
  "dst-save": (dest, tile) => saveDestination(dest, tile),
  "dst-test": testDestination,
  "dst-login": startLogin,
  "dst-logout": disconnect,
  "sftp-key": saveSftpKey,
  "g-upload": uploadGoogleCredentials,
  "g-auth": authorizeOwnGoogleClient,
};

function tileItem(tile) {
  const [kind, id] = tile.dataset.tile.split(/:(.*)/);
  return kind === "s" ? services.find(s => s.name === id) : destinations.find(d => d.type === id);
}

// Every action reports its own failure as a toast.
const guarded = fn => (...args) => Promise.resolve(fn(...args)).catch(e => toast(e.message, true));

document.addEventListener("click", guarded(async e => {
  const el = e.target.closest("[data-act], [data-goto], [data-add], [data-close]");
  if (!el) return;
  if (el.dataset.close !== undefined) return el.closest("dialog").close();
  if (el.dataset.goto) return openAndShow(el.dataset.goto);
  if (el.dataset.add) return addService(el.dataset.add);
  const act = el.dataset.act;
  if (act === "catalog") return openCatalog();
  if (act === "svc-toggle" || act === "dst-toggle") return;   // handled on change
  const tile = el.closest("[data-tile]");
  if (act === "open") return openAndShow(tile.dataset.tile);
  const item = tileItem(tile);
  if (item && tileActions[act]) await tileActions[act](item, tile);
}));

document.addEventListener("change", guarded(async e => {
  const act = e.target.dataset.act;
  if (act !== "svc-toggle" && act !== "dst-toggle") return;
  const item = tileItem(e.target.closest("[data-tile]"));
  if (act === "svc-toggle") await setServiceEnabled(item, e.target.checked);
  else await setDestinationEnabled(item, e.target.checked);
}));

document.addEventListener("DOMContentLoaded", () => {
  $("#auth-form").addEventListener("submit", submitAuth);
  $("#logout-btn").addEventListener("click", logout);
  $("#run-all").addEventListener("click", guarded(async () => {
    const r = await api("/api/services/trigger-all", { method: "POST" });
    const n = r.triggered.length;
    toast(n ? `Backing up ${plural(n, "service")}…` : "Nothing to back up: no enabled service is idle", !n);
    if (n) setTimeout(refresh, 1500);
  }));
  $("#open-settings").addEventListener("click", guarded(openSettings));
  $("#schedule-form").addEventListener("submit", guarded(saveSchedule));
  $("#security-form").addEventListener("submit", guarded(saveAuthSettings));
  $("#auth-mode").addEventListener("change", showAuthModeHint);
  $("#change-password").addEventListener("click", guarded(changePassword));
  $("#copy-code").addEventListener("click", copyLoginCode);
  $("#login-modal").addEventListener("close", () => clearInterval(loginPoll));
  $("#detail").addEventListener("close", () => { openTile = null; });
  $("#cat-q").addEventListener("input", renderCatalog);
  checkAuth();
});
