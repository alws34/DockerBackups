const API = "";
// Escape anything server-provided before it goes into innerHTML: result messages can
// contain raw text from upstream app APIs, which must never run as markup.
const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
let services = [];
let modalService = null;

async function fetchServices() {
  try {
    const res = await fetch(`${API}/api/services`);
    if (!res.ok) throw new Error("Failed to fetch services");
    services = await res.json();
    renderCards(services);
  } catch (e) {
    showToast("Failed to load services: " + e.message, "err");
  }
}

function statusBadge(svc) {
  if (!svc.enabled) return `<span class="badge badge-disabled">Disabled</span>`;
  if (svc.is_running) return `<span class="badge badge-running">Running…</span>`;
  const lr = svc.last_result;
  if (!lr) return `<span class="badge badge-never">Never run</span>`;
  if (lr.success) return `<span class="badge badge-success">OK</span>`;
  return `<span class="badge badge-error">Failed</span>`;
}

function lastRunLine(svc) {
  const lr = svc.last_result;
  if (!lr) return "";
  const dt = new Date(lr.finished_at).toLocaleString();
  const msg = lr.message ? ` — ${esc(lr.message.slice(0, 60))}` : "";
  return `<div class="last-run">${dt}${msg}</div>${uploadsLine(lr)}`;
}

function uploadsLine(lr) {
  if (!lr.uploads) return "";
  const parts = Object.entries(lr.uploads).map(([type, u]) => {
    const dest = destinations.find(d => d.type === type);
    const name = dest ? dest.display_name : type;
    return `<span class="${u.ok ? "upload-ok" : "upload-err"}" title="${esc(u.message)}">${u.ok ? "&#10003;" : "&#10007;"} ${esc(name)}</span>`;
  });
  return `<div class="last-run uploads">${parts.join(" ")}</div>`;
}

function envDots(svc) {
  if (!svc.env_vars || !svc.env_vars.length) return "";
  const dots = svc.env_vars.map(ev =>
    `<span class="env-dot ${ev.configured ? "ok" : "missing"}" title="${esc(ev.key)}: ${ev.configured ? "set" : "MISSING"}"></span>`
  ).join("");
  return `<div class="env-indicators">${dots}</div>`;
}

function renderCards(svcs) {
  const grid = document.getElementById("service-grid");
  if (!svcs.length) {
    grid.innerHTML = `<p style="color:var(--muted)">No services configured in services.json.</p>`;
    return;
  }
  grid.innerHTML = svcs.map(svc => `
    <div class="card" data-name="${esc(svc.name)}">
      <div class="card-header">
        <div>
          <div class="card-title">${esc(svc.display_name)}</div>
          <div class="card-type">${esc(svc.type)}</div>
        </div>
        ${statusBadge(svc)}
      </div>
      ${svc.description ? `<div class="card-desc">${esc(svc.description)}</div>` : ""}
      ${envDots(svc)}
      ${lastRunLine(svc)}
      <div class="toggle-row">
        <span class="toggle-label">Enabled</span>
        <label class="toggle">
          <input type="checkbox" ${svc.enabled ? "checked" : ""}
            onchange="toggleService('${svc.name}', this.checked)" />
          <span class="toggle-track"></span>
          <span class="toggle-thumb"></span>
        </label>
      </div>
      <div class="card-actions">
        <button class="btn btn-primary btn-sm"
          onclick="triggerService('${svc.name}')"
          ${!svc.enabled || svc.is_running ? "disabled" : ""}>
          Run Now
        </button>
        <button class="btn btn-secondary btn-sm" onclick="openModal('${svc.name}')">
          Configure
        </button>
      </div>
    </div>
  `).join("");
}

async function triggerService(name) {
  try {
    const res = await fetch(`${API}/api/services/${name}/trigger`, { method: "POST" });
    if (!res.ok) {
      const e = await res.json();
      throw new Error(e.detail || "Trigger failed");
    }
    showToast(`Triggered ${name}`, "ok");
    setTimeout(fetchServices, 1500);
  } catch (e) {
    showToast(e.message, "err");
  }
}

async function runAll() {
  try {
    const res = await fetch(`${API}/api/services/trigger-all`, { method: "POST" });
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Failed"); }
    const data = await res.json();
    const n = data.triggered.length;
    showToast(n > 0 ? `Triggered ${n} service${n > 1 ? "s" : ""}` : "No enabled services to run", n > 0 ? "ok" : "err");
    if (n > 0) setTimeout(fetchServices, 1500);
  } catch (e) {
    showToast(e.message, "err");
  }
}

async function toggleService(name, enabled) {
  try {
    const res = await fetch(`${API}/api/services/${name}/enabled`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    if (!res.ok) {
      const e = await res.json();
      throw new Error(e.detail || "Failed");
    }
    showToast(`${name} ${enabled ? "enabled" : "disabled"}`, "ok");
    fetchServices();
  } catch (e) {
    showToast(e.message, "err");
    fetchServices(); // revert checkbox state
  }
}

function openModal(name) {
  modalService = services.find(s => s.name === name);
  if (!modalService) return;
  document.getElementById("modal-title").textContent = `Configure: ${modalService.display_name}`;
  renderEnvForm(modalService);
  document.getElementById("config-modal").classList.add("open");
}

function closeModal() {
  document.getElementById("config-modal").classList.remove("open");
  modalService = null;
}

function eyeInput(id, dataKey, isSecret, placeholder, value, extraAttrs = "") {
  if (!isSecret) {
    return `<input type="text" id="${esc(id)}" data-key="${esc(dataKey)}"
      placeholder="${esc(placeholder)}" value="${esc(value)}" autocomplete="off" spellcheck="false" ${extraAttrs} />`;
  }
  return `
    <div class="input-wrap">
      <input type="password" id="${esc(id)}" data-key="${esc(dataKey)}"
        placeholder="${esc(placeholder)}" value="" autocomplete="new-password" spellcheck="false" ${extraAttrs} />
      <button type="button" class="eye-btn" onclick="toggleEye('${id}')" tabindex="-1">&#x1F441;</button>
    </div>`;
}

function toggleEye(id) {
  const inp = document.getElementById(id);
  if (!inp) return;
  inp.type = inp.type === "password" ? "text" : "password";
}

function renderEnvForm(svc) {
  const form = document.getElementById("env-form");
  if (!svc.env_vars || !svc.env_vars.length) {
    form.innerHTML = `<p style="color:var(--muted);font-size:.85rem">No configurable env vars for this worker.</p>`;
    return;
  }
  form.innerHTML = svc.env_vars.map(ev => `
    <div class="field">
      <label>
        ${esc(ev.label)}
        ${ev.required ? '<span class="required"> *</span>' : ""}
      </label>
      ${eyeInput(
        `field-${ev.key}`, ev.key,
        ev.secret,
        ev.secret && ev.configured ? "(already set — leave blank to keep)" : "",
        ev.secret ? "" : (ev.value || "")
      )}
      ${ev.description ? `<span class="hint">${esc(ev.description)}</span>` : ""}
    </div>
  `).join("");
}

async function saveEnvVars() {
  if (!modalService) return;
  const inputs = document.querySelectorAll("#env-form input[data-key]");
  const updates = {};
  inputs.forEach(inp => {
    const val = inp.value.trim();
    if (val) updates[inp.dataset.key] = val;
  });
  if (!Object.keys(updates).length) {
    showToast("Nothing changed", "ok");
    return;
  }
  try {
    const res = await fetch(`${API}/api/env-vars/${modalService.type}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ updates }),
    });
    if (!res.ok) {
      const e = await res.json();
      throw new Error(e.detail || "Save failed");
    }
    showToast("Saved", "ok");
    closeModal();
    fetchServices();
  } catch (e) {
    showToast(e.message, "err");
  }
}

function showToast(msg, type = "ok") {
  let t = document.getElementById("toast");
  if (!t) {
    t = document.createElement("div");
    t.id = "toast";
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.className = `toast ${type} show`;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.classList.remove("show"); }, 4000);
}

// ── Destinations ────────────────────────────────────────────────────────────

let destinations = [];
let loginPoll = null;
const PROVIDERS = { google: "Google", microsoft: "Microsoft" };

async function api(path, options = {}) {
  const res = await fetch(`${API}${path}`, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
  return body;
}

const sendJson = (method, data) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(data),
});

async function fetchDestinations() {
  try {
    destinations = await api("/api/destinations");
    renderDestinations(destinations);
    if (services.length) renderCards(services);  // upload results need destination names
  } catch (e) {
    document.getElementById("destination-grid").innerHTML =
      `<p style="color:var(--error)">Failed to load destinations: ${esc(e.message)}</p>`;
  }
}

function destField(dest, ev) {
  return `
    <div class="field">
      <label>${esc(ev.label)}${ev.required ? ' <span class="required">*</span>' : ""}</label>
      ${eyeInput(
        `dest-field-${dest.type}-${ev.key}`, ev.key,
        ev.secret,
        ev.secret && ev.configured ? "(already set — leave blank to keep)" : "",
        ev.secret ? "" : (ev.value || ""),
        `data-dest="${esc(dest.type)}"`
      )}
      ${ev.description ? `<span class="hint">${esc(ev.description)}</span>` : ""}
    </div>`;
}

function loginSection(dest) {
  if (!dest.login_provider) return "";
  const who = PROVIDERS[dest.login_provider];
  const t = esc(dest.type);
  if (dest.login && dest.login.connected) {
    const account = dest.login.account ? ` as ${esc(dest.login.account)}` : "";
    return `
      <div class="creds-status ok">&#10003; Connected${account}</div>
      <button class="btn btn-secondary btn-sm" style="align-self:flex-start" onclick="disconnectDestination('${t}')">Disconnect</button>`;
  }
  return `
    <div class="creds-status missing">Not connected</div>
    <button class="btn btn-primary btn-sm" style="align-self:flex-start" onclick="startLogin('${t}')"
      ${dest.login_available ? "" : "disabled"}>Log in with ${who}</button>
    ${dest.login_available ? "" : `<span class="hint">This build has no built-in ${who} app. Add your own under Advanced.</span>`}`;
}

function sftpKeySection() {
  return `
    <div class="field">
      <label>SSH private key (optional)</label>
      <textarea id="sftp-key" class="code-input" rows="3" spellcheck="false"
        placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"></textarea>
      <span class="hint">Stored on this server, readable only by the app. Leave empty to log in with the password.</span>
    </div>
    <button class="btn btn-secondary btn-sm" style="align-self:flex-start" onclick="saveSftpKey()">Save key</button>`;
}

function googleOwnClientSection(dest) {
  const callbackUri = `${window.location.origin}/api/destinations/google_drive/oauth/callback`;
  return `
    <details class="advanced">
      <summary>Use your own Google OAuth client</summary>
      <div class="creds-status ${dest.credentials_uploaded ? "ok" : "missing"}">
        ${dest.credentials_uploaded ? "&#10003; client_secret.json uploaded" : "No client_secret.json uploaded"}
      </div>
      <div class="field">
        <label>OAuth client secret JSON</label>
        <textarea id="dest-credentials-google_drive" class="code-input" rows="4" spellcheck="false"
          placeholder="Paste your client_secret.json contents here"></textarea>
        <span class="hint">Google Cloud Console → APIs &amp; Services → Credentials → OAuth 2.0 Client IDs → Download JSON</span>
      </div>
      <button class="btn btn-secondary btn-sm" style="align-self:flex-start" onclick="uploadCredentials()">Upload client_secret.json</button>
      <div class="field">
        <label>Redirect URI to add to your OAuth client</label>
        <code class="code-input">${esc(callbackUri)}</code>
        <span class="hint">Google only accepts localhost or HTTPS addresses here.</span>
      </div>
      <button class="btn btn-secondary btn-sm" style="align-self:flex-start" onclick="authorizeGoogleDrive()"
        ${dest.credentials_uploaded ? "" : "disabled"}>Authorize with my client</button>
    </details>`;
}

function renderDestinations(dests) {
  const grid = document.getElementById("destination-grid");
  grid.innerHTML = dests.map(dest => {
    const t = esc(dest.type);
    const basic = dest.env_vars.filter(ev => !ev.advanced).map(ev => destField(dest, ev)).join("");
    const advanced = dest.env_vars.filter(ev => ev.advanced).map(ev => destField(dest, ev)).join("");
    return `
      <div class="dest-card ${dest.enabled ? "enabled" : ""}" id="dest-card-${t}">
        <div class="card-header">
          <div class="card-title">${esc(dest.display_name)}</div>
        </div>
        <div class="card-desc">${esc(dest.description)}</div>
        <div class="toggle-row">
          <span class="toggle-label">Upload here</span>
          <label class="toggle">
            <input type="checkbox" ${dest.enabled ? "checked" : ""}
              onchange="toggleDestination('${t}', '${esc(dest.enabled_key)}', this.checked)" />
            <span class="toggle-track"></span>
            <span class="toggle-thumb"></span>
          </label>
        </div>
        ${loginSection(dest)}
        <div class="env-form" id="dest-form-${t}">
          ${basic}
          ${advanced ? `<details class="advanced"><summary>Advanced</summary>${advanced}</details>` : ""}
        </div>
        ${dest.type === "sftp" ? sftpKeySection() : ""}
        ${dest.type === "google_drive" ? googleOwnClientSection(dest) : ""}
        <div class="save-row">
          <button class="btn btn-secondary btn-sm" onclick="testDestination('${t}')">Test connection</button>
          ${basic || advanced ? `<button class="btn btn-primary btn-sm" onclick="saveDestination('${t}')">Save</button>` : ""}
        </div>
      </div>`;
  }).join("");
}

async function toggleDestination(destType, enabledKey, enabled) {
  try {
    await api(`/api/destinations/${destType}/env-vars`, sendJson("PUT", { updates: { [enabledKey]: enabled ? "true" : "false" } }));
    const dest = destinations.find(d => d.type === destType);
    showToast(`${dest ? dest.display_name : destType}: uploads ${enabled ? "on" : "off"}`, "ok");
    fetchDestinations();
  } catch (e) {
    showToast(e.message, "err");
    fetchDestinations();
  }
}

// Returns true when something was saved.
async function saveDestination(destType, quiet = false) {
  const updates = {};
  document.querySelectorAll(`#dest-form-${destType} input[data-key]`).forEach(inp => {
    const val = inp.value.trim();
    if (val) updates[inp.dataset.key] = val;
  });
  if (!Object.keys(updates).length) {
    if (!quiet) showToast("Nothing changed", "ok");
    return false;
  }
  try {
    await api(`/api/destinations/${destType}/env-vars`, sendJson("PUT", { updates }));
    if (!quiet) showToast("Saved", "ok");
    return true;
  } catch (e) {
    showToast(e.message, "err");
    throw e;
  }
}

async function testDestination(destType) {
  try {
    await saveDestination(destType, true);
    showToast("Testing connection…", "ok");
    const r = await api(`/api/destinations/${destType}/test`, { method: "POST" });
    if (r.ok) {
      showToast(r.message, "ok");
    } else if (r.fingerprint) {
      const trust = confirm(
        `First connection to this server. Its host key fingerprint is:\n\n${r.fingerprint}\n\n` +
        "Trust it? To be sure, compare with `ssh-keygen -lf /etc/ssh/ssh_host_*_key.pub` on the server."
      );
      if (!trust) return;
      await api(`/api/destinations/${destType}/env-vars`, sendJson("PUT", { updates: { SFTP_HOST_FINGERPRINT: r.fingerprint } }));
      return testDestination(destType);
    } else {
      showToast(r.message, "err");
    }
    fetchDestinations();
  } catch (e) {
    showToast(e.message, "err");
  }
}

async function saveSftpKey() {
  const box = document.getElementById("sftp-key");
  const key = box ? box.value.trim() : "";
  if (!key) { showToast("Paste a private key first", "err"); return; }
  try {
    await saveDestination("sftp", true);
    await api("/api/destinations/sftp/key", sendJson("POST", { key }));
    box.value = "";
    showToast("SSH key saved", "ok");
    fetchDestinations();
  } catch (e) {
    showToast(e.message, "err");
  }
}

function setLoginStatus(text, isError = false) {
  const el = document.getElementById("login-status");
  el.textContent = text;
  el.style.color = isError ? "var(--error)" : "";
}

async function startLogin(destType) {
  const dest = destinations.find(d => d.type === destType);
  const who = PROVIDERS[dest.login_provider];
  try {
    await saveDestination(destType, true);
    const login = await api(`/api/destinations/${destType}/login`, { method: "POST" });
    const host = new URL(login.verification_url).host;
    document.getElementById("login-title").textContent = `Log in with ${who}`;
    document.getElementById("login-steps").textContent =
      `Open ${host} on any device, enter this code, and sign in with your ${who} account.`;
    document.getElementById("login-code").textContent = login.user_code;
    const link = document.getElementById("login-link");
    link.href = login.verification_url;
    link.textContent = `Open ${host}`;
    setLoginStatus("Waiting for you to finish signing in…");
    document.getElementById("login-modal").classList.add("open");
    clearInterval(loginPoll);
    loginPoll = setInterval(async () => {
      try {
        const s = await api(`/api/destinations/logins/${login.id}`);
        if (s.status === "connected") {
          closeLogin();
          showToast(`${dest.display_name} connected${s.message ? ` as ${s.message}` : ""}`, "ok");
          fetchDestinations();
        } else if (s.status === "failed") {
          clearInterval(loginPoll);
          setLoginStatus(s.message, true);
        }
      } catch (e) {
        clearInterval(loginPoll);
        setLoginStatus(e.message, true);
      }
    }, 3000);
  } catch (e) {
    showToast(e.message, "err");
  }
}

function closeLogin() {
  clearInterval(loginPoll);
  document.getElementById("login-modal").classList.remove("open");
}

async function copyLoginCode() {
  const code = document.getElementById("login-code").textContent;
  try {
    await navigator.clipboard.writeText(code);
    showToast("Code copied", "ok");
  } catch {
    // The clipboard API needs HTTPS or localhost; on a LAN IP select the code instead.
    getSelection().selectAllChildren(document.getElementById("login-code"));
    showToast("Code selected: press Ctrl+C / ⌘C", "ok");
  }
}

async function disconnectDestination(destType) {
  const dest = destinations.find(d => d.type === destType);
  if (!confirm(`Disconnect ${dest.display_name}? Backups stop uploading there until you log in again.`)) return;
  try {
    await api(`/api/destinations/${destType}/login`, { method: "DELETE" });
    showToast(`${dest.display_name} disconnected`, "ok");
    fetchDestinations();
  } catch (e) {
    showToast(e.message, "err");
  }
}

async function uploadCredentials() {
  const textarea = document.getElementById("dest-credentials-google_drive");
  const json_content = textarea ? textarea.value.trim() : "";
  if (!json_content) { showToast("Paste credentials JSON first", "err"); return; }
  try {
    const data = await api("/api/destinations/google_drive/credentials", sendJson("POST", { json_content }));
    showToast(`Credentials saved (client_id: ${data.client_id})`, "ok");
    textarea.value = "";
    fetchDestinations();
  } catch (e) {
    showToast(e.message, "err");
  }
}

async function authorizeGoogleDrive() {
  try {
    const { auth_url } = await api("/api/destinations/google_drive/oauth/start",
      sendJson("POST", { redirect_base: window.location.origin }));
    window.open(auth_url, "_blank", "noopener,noreferrer");
    showToast("Finish signing in in the new tab, then come back here", "ok");
    let polls = 0;
    const poll = setInterval(async () => {
      if (++polls > 60) { clearInterval(poll); return; }
      const dests = await api("/api/destinations").catch(() => null);
      const gd = dests && dests.find(d => d.type === "google_drive");
      if (gd && gd.login && gd.login.connected) {
        clearInterval(poll);
        destinations = dests;
        renderDestinations(dests);
        showToast("Google Drive connected", "ok");
      }
    }, 3000);
  } catch (e) {
    showToast(e.message, "err");
  }
}

// ── Settings ─────────────────────────────────────────────────────────────────

async function fetchSettings() {
  try {
    const res = await fetch(`${API}/api/settings`);
    if (!res.ok) throw new Error("Failed to fetch settings");
    const s = await res.json();
    renderSettings(s);
  } catch (e) {
    document.getElementById("settings-panel").innerHTML =
      `<p style="color:var(--error)">Failed to load settings: ${esc(e.message)}</p>`;
  }
}

function renderSettings(s) {
  document.getElementById("settings-panel").innerHTML = `
    <div class="dest-card" style="max-width:420px">
      <div class="field">
        <label>Repeat every N hours <span style="color:var(--muted);font-weight:400">(0 = use daily time below)</span></label>
        <input type="number" id="setting-interval-hours" value="${esc(s.interval_hours)}" min="0" step="1"
          style="max-width:120px" />
        <span class="hint">e.g. 6 = run every 6 hours. Set to 0 to run once daily at a fixed time.</span>
      </div>
      <div class="field">
        <label>Daily backup time (HH:MM, 24-hour) <span style="color:var(--muted);font-weight:400">— used when interval = 0</span></label>
        <input type="text" id="setting-daily-at" value="${esc(s.daily_at)}" placeholder="03:30"
          autocomplete="off" spellcheck="false" style="max-width:120px" />
      </div>
      <div class="field">
        <label>Local retention (days to keep old backups)</label>
        <input type="number" id="setting-keep-days" value="${esc(s.keep_days)}" min="1" step="1"
          style="max-width:120px" />
      </div>
      <div class="field">
        <label>Copies to keep at each destination (per service)</label>
        <input type="number" id="setting-remote-keep-count" value="${esc(s.remote_keep_count ?? 3)}" min="1" step="1"
          style="max-width:120px" />
        <span class="hint">After each upload the oldest copies beyond this count are removed. Files you put there yourself are never touched.</span>
      </div>
      <div class="toggle-row">
        <span class="toggle-label">Run backup on container start</span>
        <label class="toggle">
          <input type="checkbox" id="setting-run-on-start" ${s.run_on_start ? "checked" : ""} />
          <span class="toggle-track"></span>
          <span class="toggle-thumb"></span>
        </label>
      </div>
      <div class="save-row">
        <button class="btn btn-primary btn-sm" onclick="saveSettings()">Save</button>
      </div>
    </div>
  `;
}

async function saveSettings() {
  const daily_at = document.getElementById("setting-daily-at").value.trim();
  const keep_days = parseInt(document.getElementById("setting-keep-days").value, 10);
  const remote_keep_count = parseInt(document.getElementById("setting-remote-keep-count").value, 10);
  const interval_hours = parseInt(document.getElementById("setting-interval-hours").value, 10) || 0;
  const run_on_start = document.getElementById("setting-run-on-start").checked;

  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(daily_at)) {
    showToast("Invalid time — use HH:MM (e.g. 03:30)", "err");
    return;
  }
  if (!keep_days || keep_days < 1) {
    showToast("Retention must be at least 1 day", "err");
    return;
  }
  if (!remote_keep_count || remote_keep_count < 1) {
    showToast("Copies to keep must be at least 1", "err");
    return;
  }
  try {
    const res = await fetch(`${API}/api/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ daily_at, interval_hours, run_on_start, keep_days, remote_keep_count }),
    });
    if (!res.ok) {
      const e = await res.json();
      throw new Error(e.detail || "Save failed");
    }
    showToast("Settings saved", "ok");
  } catch (e) {
    showToast(e.message, "err");
  }
}

// ── Init ─────────────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", () => {
  fetchServices();
  fetchDestinations();
  fetchSettings();
  setInterval(fetchServices, 15000);

  document.getElementById("refresh-btn").addEventListener("click", fetchServices);

  document.getElementById("config-modal").addEventListener("click", e => {
    if (e.target === e.currentTarget) closeModal();
  });

  document.addEventListener("keydown", e => {
    if (e.key === "Escape") { closeModal(); closeLogin(); }
  });
});
