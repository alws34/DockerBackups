const API = "";
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
  const msg = lr.message ? ` — ${lr.message.slice(0, 60)}` : "";
  return `<div class="last-run">${dt}${msg}</div>`;
}

function envDots(svc) {
  if (!svc.env_vars || !svc.env_vars.length) return "";
  const dots = svc.env_vars.map(ev =>
    `<span class="env-dot ${ev.configured ? "ok" : "missing"}" title="${ev.key}: ${ev.configured ? "set" : "MISSING"}"></span>`
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
    <div class="card" data-name="${svc.name}">
      <div class="card-header">
        <div>
          <div class="card-title">${svc.display_name}</div>
          <div class="card-type">${svc.type}</div>
        </div>
        ${statusBadge(svc)}
      </div>
      ${svc.description ? `<div class="card-desc">${svc.description}</div>` : ""}
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
    return `<input type="text" id="${id}" data-key="${dataKey}"
      placeholder="${placeholder}" value="${value}" autocomplete="off" spellcheck="false" ${extraAttrs} />`;
  }
  return `
    <div class="input-wrap">
      <input type="password" id="${id}" data-key="${dataKey}"
        placeholder="${placeholder}" value="" autocomplete="new-password" spellcheck="false" ${extraAttrs} />
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
        ${ev.label}
        ${ev.required ? '<span class="required"> *</span>' : ""}
      </label>
      ${eyeInput(
        `field-${ev.key}`, ev.key,
        ev.secret,
        ev.secret && ev.configured ? "(already set — leave blank to keep)" : "",
        ev.secret ? "" : (ev.value || "")
      )}
      ${ev.description ? `<span class="hint">${ev.description}</span>` : ""}
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

async function fetchDestinations() {
  try {
    const res = await fetch(`${API}/api/destinations`);
    if (!res.ok) throw new Error("Failed to fetch destinations");
    destinations = await res.json();
    renderDestinations(destinations);
  } catch (e) {
    document.getElementById("destination-grid").innerHTML =
      `<p style="color:var(--error)">Failed to load destinations: ${e.message}</p>`;
  }
}

function renderDestinations(dests) {
  const grid = document.getElementById("destination-grid");
  if (!dests.length) {
    grid.innerHTML = `<p style="color:var(--muted)">No destinations configured.</p>`;
    return;
  }
  grid.innerHTML = dests.map(dest => {
    const fields = dest.env_vars
      .filter(ev => ev.key !== "GOOGLE_DRIVE_ENABLED")
      .map(ev => `
        <div class="field">
          <label>${ev.label}${ev.required ? ' <span class="required">*</span>' : ""}</label>
          ${eyeInput(
            `dest-field-${dest.type}-${ev.key}`, ev.key,
            ev.secret,
            ev.secret && ev.configured ? "(already set — leave blank to keep)" : "",
            ev.secret ? "" : (ev.value || ""),
            `data-dest="${dest.type}"`
          )}
          ${ev.description ? `<span class="hint">${ev.description}</span>` : ""}
        </div>
      `).join("");

    const callbackUri = `${window.location.origin}/api/destinations/google_drive/oauth/callback`;
    const credentialsSection = dest.type === "google_drive" ? `
      <div style="display:flex;flex-direction:column;gap:.5rem">
        <div class="creds-status ${dest.credentials_uploaded ? "ok" : "missing"}">
          ${dest.credentials_uploaded
            ? "&#10003; client_secret.json uploaded"
            : "&#9888; No credentials — paste client_secret.json below and click Upload"}
        </div>
        <div class="creds-status ${dest.authorized ? "ok" : "missing"}">
          ${dest.authorized
            ? "&#10003; Google Drive authorized"
            : "&#9888; Not authorized — click Authorize below"}
        </div>
      </div>
      <div class="field" style="margin-top:.25rem">
        <label>OAuth2 Client Secret JSON</label>
        <textarea
          id="dest-credentials-${dest.type}"
          rows="4"
          placeholder="Paste your client_secret.json contents here…"
          style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:.5rem .75rem;color:var(--text);font-size:.8rem;font-family:monospace;width:100%;resize:vertical"
          spellcheck="false"
        ></textarea>
        <span class="hint">Google Cloud Console → APIs &amp; Services → Credentials → OAuth 2.0 Client IDs → Download JSON</span>
      </div>
      <button class="btn btn-secondary btn-sm" style="align-self:flex-start" onclick="uploadCredentials('${dest.type}')">Upload client_secret.json</button>
      <div class="field" style="margin-top:.25rem">
        <label>Redirect URI (add this to your Google Cloud OAuth client)</label>
        <div style="display:flex;gap:.4rem;align-items:center">
          <code style="background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:.3rem .6rem;font-size:.78rem;flex:1;overflow-x:auto;white-space:nowrap">${callbackUri}</code>
          <button class="btn btn-secondary btn-sm" onclick="navigator.clipboard.writeText('${callbackUri}').then(()=>showToast('Copied','ok'))">Copy</button>
        </div>
        <span class="hint">Google Cloud Console → OAuth client → Authorized redirect URIs → Add URI</span>
      </div>
      <div style="display:flex;gap:.5rem;flex-wrap:wrap">
        <button class="btn btn-primary btn-sm" onclick="authorizeGoogleDrive()" ${!dest.credentials_uploaded ? "disabled title='Upload client_secret.json first'" : ""}>
          Authorize Google Drive
        </button>
        ${dest.authorized ? `<button class="btn btn-secondary btn-sm" onclick="revokeGoogleDrive()">Revoke Authorization</button>` : ""}
      </div>
    ` : "";

    return `
      <div class="dest-card ${dest.enabled ? "enabled" : ""}" id="dest-card-${dest.type}">
        <div class="card-header">
          <div>
            <div class="card-title">${dest.display_name}</div>
          </div>
        </div>
        <div class="card-desc">${dest.description}</div>
        <div class="toggle-row">
          <span class="toggle-label">Enabled</span>
          <label class="toggle">
            <input type="checkbox" ${dest.enabled ? "checked" : ""}
              onchange="toggleDestination('${dest.type}', this.checked)" />
            <span class="toggle-track"></span>
            <span class="toggle-thumb"></span>
          </label>
        </div>
        <div class="env-form" id="dest-form-${dest.type}">${fields}</div>
        ${credentialsSection}
        <div class="save-row">
          <button class="btn btn-primary btn-sm" onclick="saveDestination('${dest.type}')">Save</button>
        </div>
      </div>
    `;
  }).join("");
}

async function toggleDestination(destType, enabled) {
  const updates = { GOOGLE_DRIVE_ENABLED: enabled ? "true" : "false" };
  try {
    const res = await fetch(`${API}/api/destinations/${destType}/env-vars`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ updates }),
    });
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Failed"); }
    showToast(`Google Drive ${enabled ? "enabled" : "disabled"}`, "ok");
    fetchDestinations();
  } catch (e) {
    showToast(e.message, "err");
  }
}

async function saveDestination(destType) {
  const inputs = document.querySelectorAll(`#dest-form-${destType} input[data-key]`);
  const updates = {};
  inputs.forEach(inp => {
    const val = inp.value.trim();
    if (val) updates[inp.dataset.key] = val;
  });
  if (!Object.keys(updates).length) { showToast("Nothing changed", "ok"); return; }
  try {
    const res = await fetch(`${API}/api/destinations/${destType}/env-vars`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ updates }),
    });
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Save failed"); }
    showToast("Saved", "ok");
    fetchDestinations();
  } catch (e) {
    showToast(e.message, "err");
  }
}

async function uploadCredentials(destType) {
  const textarea = document.getElementById(`dest-credentials-${destType}`);
  const json_content = textarea ? textarea.value.trim() : "";
  if (!json_content) { showToast("Paste credentials JSON first", "err"); return; }
  try {
    const res = await fetch(`${API}/api/destinations/${destType}/credentials`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ json_content }),
    });
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Upload failed"); }
    const data = await res.json();
    showToast(`Credentials saved (client_id: ${data.client_id})`, "ok");
    if (textarea) textarea.value = "";
    fetchDestinations();
  } catch (e) {
    showToast(e.message, "err");
  }
}

async function authorizeGoogleDrive() {
  try {
    const res = await fetch(`${API}/api/destinations/google_drive/oauth/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ redirect_base: window.location.origin }),
    });
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Failed to start OAuth"); }
    const { auth_url } = await res.json();
    window.open(auth_url, "_blank", "noopener,noreferrer");
    showToast("Authorization window opened — complete login then return here", "ok");
    // Poll for completion
    let polls = 0;
    const poll = setInterval(async () => {
      polls++;
      if (polls > 60) { clearInterval(poll); return; }
      const r = await fetch(`${API}/api/destinations`).catch(() => null);
      if (!r || !r.ok) return;
      const dests = await r.json();
      const gd = dests.find(d => d.type === "google_drive");
      if (gd && gd.authorized) {
        clearInterval(poll);
        destinations = dests;
        renderDestinations(dests);
        showToast("Google Drive authorized!", "ok");
      }
    }, 3000);
  } catch (e) {
    showToast(e.message, "err");
  }
}

async function revokeGoogleDrive() {
  if (!confirm("Revoke Google Drive authorization?")) return;
  try {
    const res = await fetch(`${API}/api/destinations/google_drive/oauth`, { method: "DELETE" });
    if (!res.ok) { const e = await res.json(); throw new Error(e.detail || "Revoke failed"); }
    showToast("Authorization revoked", "ok");
    fetchDestinations();
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
      `<p style="color:var(--error)">Failed to load settings: ${e.message}</p>`;
  }
}

function renderSettings(s) {
  document.getElementById("settings-panel").innerHTML = `
    <div class="dest-card" style="max-width:420px">
      <div class="field">
        <label>Repeat every N hours <span style="color:var(--muted);font-weight:400">(0 = use daily time below)</span></label>
        <input type="number" id="setting-interval-hours" value="${s.interval_hours}" min="0" step="1"
          style="max-width:120px" />
        <span class="hint">e.g. 6 = run every 6 hours. Set to 0 to run once daily at a fixed time.</span>
      </div>
      <div class="field">
        <label>Daily backup time (HH:MM, 24-hour) <span style="color:var(--muted);font-weight:400">— used when interval = 0</span></label>
        <input type="text" id="setting-daily-at" value="${s.daily_at}" placeholder="03:30"
          autocomplete="off" spellcheck="false" style="max-width:120px" />
      </div>
      <div class="field">
        <label>Local retention (days to keep old backups)</label>
        <input type="number" id="setting-keep-days" value="${s.keep_days}" min="1" step="1"
          style="max-width:120px" />
      </div>
      <div class="field">
        <label>Google Drive — max backups to keep per service</label>
        <input type="number" id="setting-drive-keep-count" value="${s.drive_keep_count ?? 3}" min="1" step="1"
          style="max-width:120px" />
        <span class="hint">Oldest files beyond this count are deleted from Drive after each upload.</span>
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
  const drive_keep_count = parseInt(document.getElementById("setting-drive-keep-count").value, 10);
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
  if (!drive_keep_count || drive_keep_count < 1) {
    showToast("Drive keep count must be at least 1", "err");
    return;
  }
  try {
    const res = await fetch(`${API}/api/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ daily_at, interval_hours, run_on_start, keep_days, drive_keep_count }),
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
  setInterval(fetchDestinations, 15000);

  document.getElementById("refresh-btn").addEventListener("click", fetchServices);

  document.getElementById("config-modal").addEventListener("click", e => {
    if (e.target === e.currentTarget) closeModal();
  });

  document.addEventListener("keydown", e => {
    if (e.key === "Escape") closeModal();
  });
});
