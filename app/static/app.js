const state = {
  date: null,
};

function qs(id) {
  return document.getElementById(id);
}

function formatDuration(totalSeconds) {
  const seconds = Math.max(0, Number(totalSeconds) || 0);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;

  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

function formatLocal(ts) {
  return new Date(ts * 1000).toLocaleString("es-ES", {
    hour12: false,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function setStatus(ok, text) {
  const pill = qs("status-pill");
  pill.textContent = text;
  pill.classList.toggle("error", !ok);
}

async function loadHealth() {
  const res = await fetch("/api/health");
  if (!res.ok) {
    throw new Error("No se pudo consultar /api/health");
  }
  const data = await res.json();

  if (data.tracker?.running) {
    setStatus(true, "Tracker activo");
  } else {
    setStatus(false, "Tracker detenido");
  }

  if (Array.isArray(data.notes) && data.notes.length > 0) {
    qs("updated-at").textContent = data.notes[0];
  }
}

function renderTopApps(topApps) {
  const root = qs("top-apps-list");
  if (!topApps?.length) {
    root.innerHTML = '<p class="empty">No hay actividad registrada en este día.</p>';
    return;
  }

  root.innerHTML = "";
  for (const item of topApps) {
    const row = document.createElement("div");
    row.className = "top-row";
    const pct = Math.max(1, Math.round(item.percentage || 0));
    row.innerHTML = `
      <header>
        <strong>${item.app}</strong>
        <span>${item.human} (${item.percentage}%)</span>
      </header>
      <div class="track"><div class="fill" style="width: ${pct}%;"></div></div>
    `;
    root.appendChild(row);
  }
}

function renderHours(byHour) {
  const root = qs("hourly-chart");
  if (!Array.isArray(byHour) || byHour.length !== 24) {
    root.innerHTML = '<p class="empty">Sin datos por hora.</p>';
    return;
  }

  const peak = Math.max(...byHour, 1);
  root.innerHTML = "";

  for (let i = 0; i < 24; i += 1) {
    const value = byHour[i];
    const pct = Math.round((value / peak) * 100);

    const row = document.createElement("div");
    row.className = "hour-row";
    row.innerHTML = `
      <span class="hour-label">${String(i).padStart(2, "0")}:00</span>
      <div class="bar"><span style="width: ${pct}%;"></span></div>
      <span class="hour-value">${formatDuration(value)}</span>
    `;
    root.appendChild(row);
  }
}

function renderRecent(items) {
  const body = qs("recent-body");
  if (!items?.length) {
    body.innerHTML = '<tr><td colspan="5" class="empty">Sin sesiones todavía.</td></tr>';
    return;
  }

  body.innerHTML = items
    .map(
      (item) => `
      <tr>
        <td>${formatLocal(item.start_ts)}</td>
        <td>${formatLocal(item.end_ts)}</td>
        <td>${item.duration_human}</td>
        <td>${item.app}</td>
        <td>${item.title || "(sin título)"}</td>
      </tr>
    `
    )
    .join("");
}

async function loadOverview() {
  const url = new URL("/api/overview", window.location.origin);
  if (state.date) {
    url.searchParams.set("date", state.date);
  }

  const res = await fetch(url.toString());
  if (!res.ok) {
    throw new Error("No se pudo consultar /api/overview");
  }

  const data = await res.json();
  qs("total-active").textContent = data.total_human;
  qs("distinct-apps").textContent = String(data.distinct_apps);
  qs("top-app").textContent = data.top_apps?.[0]?.app || "--";
  renderTopApps(data.top_apps || []);
  renderHours(data.by_hour_seconds || []);

  if (data.updated_at_ts) {
    const stamp = new Date(data.updated_at_ts * 1000).toLocaleTimeString("es-ES", { hour12: false });
    qs("updated-at").textContent = `Actualizado ${stamp}`;
  }
}

async function loadRecent() {
  const res = await fetch("/api/recent?limit=30");
  if (!res.ok) {
    throw new Error("No se pudo consultar /api/recent");
  }
  const data = await res.json();
  renderRecent(data.items || []);
}

async function refreshAll() {
  try {
    await loadHealth();
    await Promise.all([loadOverview(), loadRecent()]);
  } catch (err) {
    setStatus(false, "Error de conexión");
    qs("updated-at").textContent = String(err.message || err);
  }
}

function init() {
  const input = qs("date-input");
  const today = new Date().toISOString().slice(0, 10);
  input.value = today;
  state.date = today;

  input.addEventListener("change", () => {
    state.date = input.value || null;
  });

  qs("refresh-btn").addEventListener("click", () => {
    refreshAll();
  });

  refreshAll();
  setInterval(refreshAll, 15000);
}

document.addEventListener("DOMContentLoaded", init);
