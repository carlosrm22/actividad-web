const state = {
  mode: "day",
  anchorDate: null,
  startDate: null,
  endDate: null,
};

const CHART_COLORS = [
  "#0f766e",
  "#2563eb",
  "#16a34a",
  "#d97706",
  "#dc2626",
  "#7c3aed",
  "#0ea5e9",
  "#15803d",
];

function qs(id) {
  return document.getElementById(id);
}

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

function parseIsoDateUtc(isoDate) {
  const [year, month, day] = String(isoDate).split("-").map((x) => Number(x));
  return new Date(Date.UTC(year, month - 1, day));
}

function toIsoDateUtc(dateObj) {
  return dateObj.toISOString().slice(0, 10);
}

function addDaysIso(isoDate, days) {
  const d = parseIsoDateUtc(isoDate);
  d.setUTCDate(d.getUTCDate() + days);
  return toIsoDateUtc(d);
}

function formatShortDate(isoDate) {
  return parseIsoDateUtc(isoDate).toLocaleDateString("es-ES", {
    day: "2-digit",
    month: "short",
  });
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

function formatDayLabel(dayIso) {
  return parseIsoDateUtc(dayIso).toLocaleDateString("es-ES", { day: "2-digit", month: "2-digit" });
}

function setStatus(ok, text) {
  const pill = qs("status-pill");
  pill.textContent = text;
  pill.classList.toggle("error", !ok);
}

function setModeUi(mode) {
  const custom = qs("custom-range");
  custom.classList.toggle("hidden", mode !== "custom");
}

function buildOverviewUrl() {
  const url = new URL("/api/overview", window.location.origin);
  url.searchParams.set("mode", state.mode);

  if (state.mode === "custom") {
    if (!state.startDate || !state.endDate) {
      throw new Error("Selecciona inicio y fin para el rango personalizado");
    }
    url.searchParams.set("start_date", state.startDate);
    url.searchParams.set("end_date", state.endDate);
    return url;
  }

  if (state.anchorDate) {
    url.searchParams.set("anchor_date", state.anchorDate);
  }
  return url;
}

function formatPeriodSummary(data) {
  const start = data.range_start_date || data.date;
  const end = data.range_end_date_inclusive || data.date;
  if (!start || !end) {
    return "Período: --";
  }

  const map = {
    day: "Día",
    week: "Semana",
    month: "Mes",
    custom: "Rango",
  };
  const modeText = map[data.mode] || "Período";
  return `${modeText}: ${formatShortDate(start)} - ${formatShortDate(end)}`;
}

function setOverviewMetrics(data) {
  qs("total-active").textContent = data.total_human;
  qs("distinct-apps").textContent = String(data.distinct_apps);
  qs("unknown-active").textContent = data.unattributed_human || "0s";

  const daysCount = Math.max(1, Number(data.days_count) || 1);
  const totalSeconds = Math.max(0, Number(data.total_seconds) || 0);
  const dailyAverage = Math.round(totalSeconds / daysCount);
  qs("daily-average").textContent = formatDuration(dailyAverage);

  qs("period-summary").textContent = formatPeriodSummary(data);
  qs("donut-total").textContent = data.total_human || "0s";
}

function renderDonut(topApps, totalSeconds) {
  const donut = qs("apps-donut");
  const legend = qs("apps-legend");

  if (!Array.isArray(topApps) || !topApps.length || totalSeconds <= 0) {
    donut.style.background = "conic-gradient(#d5dfdc 0% 100%)";
    legend.innerHTML = '<p class="empty">Sin actividad para mostrar distribución.</p>';
    return;
  }

  const selected = topApps.slice(0, 6).map((item) => ({
    app: item.app,
    seconds: Number(item.seconds) || 0,
  }));

  const restSeconds = topApps.slice(6).reduce((acc, item) => acc + (Number(item.seconds) || 0), 0);
  if (restSeconds > 0) {
    selected.push({ app: "Otros", seconds: restSeconds });
  }

  const effectiveTotal = selected.reduce((acc, item) => acc + item.seconds, 0);
  if (effectiveTotal <= 0) {
    donut.style.background = "conic-gradient(#d5dfdc 0% 100%)";
    legend.innerHTML = '<p class="empty">Sin actividad para mostrar distribución.</p>';
    return;
  }

  let cursor = 0;
  const segments = selected.map((item, idx) => {
    const pct = (item.seconds / effectiveTotal) * 100;
    const start = cursor;
    const end = cursor + pct;
    cursor = end;
    return {
      ...item,
      pct,
      start,
      end,
      color: CHART_COLORS[idx % CHART_COLORS.length],
    };
  });

  donut.style.background = `conic-gradient(${segments
    .map((s) => `${s.color} ${s.start}% ${s.end}%`)
    .join(", ")})`;

  legend.innerHTML = segments
    .map(
      (s) => `
      <div class="legend-row">
        <span class="legend-dot" style="background:${s.color};"></span>
        <span class="legend-label">${s.app}</span>
        <span class="legend-meta">${formatDuration(s.seconds)} (${s.pct.toFixed(1)}%)</span>
      </div>
    `
    )
    .join("");
}

function renderRanking(topApps) {
  const body = qs("ranking-body");
  if (!topApps?.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty">Sin datos para este período.</td></tr>';
    return;
  }

  body.innerHTML = topApps
    .slice(0, 25)
    .map(
      (item, idx) => `
      <tr>
        <td>${idx + 1}</td>
        <td>${item.app}</td>
        <td>${item.human}</td>
        <td>${item.percentage}%</td>
      </tr>
    `
    )
    .join("");
}

function renderPeriodChart(data) {
  const title = qs("period-chart-title");
  const root = qs("period-chart");

  let labels = [];
  let values = [];

  if (state.mode === "day") {
    title.textContent = "Actividad por hora";
    labels = Array.from({ length: 24 }, (_, i) => `${String(i).padStart(2, "0")}:00`);
    values = Array.isArray(data.by_hour_seconds) ? data.by_hour_seconds : [];
  } else {
    title.textContent = "Actividad por día";
    const byDay = Array.isArray(data.by_day) ? data.by_day : [];
    labels = byDay.map((x) => formatDayLabel(x.date));
    values = byDay.map((x) => Number(x.seconds || 0));
  }

  if (!labels.length || !values.length) {
    root.innerHTML = '<p class="empty">Sin datos para graficar.</p>';
    return { labels: [], values: [] };
  }

  const peak = Math.max(...values, 1);
  root.innerHTML = "";

  for (let i = 0; i < labels.length; i += 1) {
    const value = values[i] || 0;
    const pct = Math.round((value / peak) * 100);
    const row = document.createElement("div");
    row.className = "hour-row";
    row.innerHTML = `
      <span class="hour-label">${labels[i]}</span>
      <div class="bar"><span style="width: ${pct}%;"></span></div>
      <span class="hour-value">${formatDuration(value)}</span>
    `;
    root.appendChild(row);
  }

  return { labels, values };
}

function renderTrendChart(labels, values) {
  const root = qs("trend-chart");
  if (!Array.isArray(values) || values.length === 0) {
    root.innerHTML = '<p class="empty">Sin tendencia para mostrar.</p>';
    return;
  }

  const width = 640;
  const height = 180;
  const chartLeft = 20;
  const chartRight = width - 18;
  const chartTop = 18;
  const chartBottom = 138;
  const chartWidth = chartRight - chartLeft;
  const chartHeight = chartBottom - chartTop;
  const max = Math.max(...values, 1);

  const points = values.map((value, idx) => {
    const x = values.length === 1 ? chartLeft + chartWidth / 2 : chartLeft + (idx / (values.length - 1)) * chartWidth;
    const y = chartBottom - (value / max) * chartHeight;
    return { x, y };
  });

  const linePath = points.map((point, idx) => `${idx === 0 ? "M" : "L"}${point.x.toFixed(2)} ${point.y.toFixed(2)}`).join(" ");
  const areaPath = `${linePath} L ${points[points.length - 1].x.toFixed(2)} ${chartBottom} L ${points[0].x.toFixed(2)} ${chartBottom} Z`;

  const grid = [0.25, 0.5, 0.75]
    .map((v) => {
      const y = chartBottom - v * chartHeight;
      return `<line x1="${chartLeft}" y1="${y}" x2="${chartRight}" y2="${y}"></line>`;
    })
    .join("");

  const dots = points.map((point) => `<circle class="trend-dot" cx="${point.x.toFixed(2)}" cy="${point.y.toFixed(2)}" r="2.5"></circle>`).join("");

  const tickIndexes = Array.from(new Set([0, Math.floor((labels.length - 1) / 2), labels.length - 1])).filter(
    (idx) => idx >= 0 && idx < labels.length
  );

  const ticks = tickIndexes
    .map((idx) => {
      const point = points[idx];
      const label = labels[idx];
      return `<text class="trend-x-label" x="${point.x.toFixed(2)}" y="${height - 8}">${label}</text>`;
    })
    .join("");

  root.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Tendencia de actividad">
      <defs>
        <linearGradient id="trend-fill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#2dd4bf" stop-opacity="0.35"></stop>
          <stop offset="100%" stop-color="#2dd4bf" stop-opacity="0.02"></stop>
        </linearGradient>
      </defs>
      <g class="trend-grid">${grid}</g>
      <path class="trend-area" d="${areaPath}"></path>
      <path class="trend-line" d="${linePath}"></path>
      ${dots}
      ${ticks}
    </svg>
  `;
}

function getAverageSeconds(data) {
  const daysCount = Math.max(1, Number(data.days_count) || 1);
  const total = Math.max(0, Number(data.total_seconds) || 0);
  return Math.round(total / daysCount);
}

function formatDelta(diff, previous, formatter) {
  if (diff === 0) {
    return "Sin cambio";
  }

  const sign = diff > 0 ? "+" : "-";
  const absPart = formatter(Math.abs(diff));
  if (previous > 0) {
    const pct = (diff / previous) * 100;
    const pctText = `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
    return `${sign}${absPart} (${pctText})`;
  }
  return `${sign}${absPart} (base 0)`;
}

function setDeltaElement(elementId, diff, text, invertDirection = false) {
  const el = qs(elementId);
  el.textContent = text;
  el.classList.remove("up", "down", "neutral");

  let direction = "neutral";
  if (diff > 0) direction = "up";
  if (diff < 0) direction = "down";
  if (invertDirection && direction !== "neutral") {
    direction = direction === "up" ? "down" : "up";
  }

  el.classList.add(direction);
}

function clearComparison(reason) {
  qs("compare-reference").textContent = reason || "Referencia: --";
  qs("cmp-total-current").textContent = "--";
  qs("cmp-avg-current").textContent = "--";
  qs("cmp-apps-current").textContent = "--";
  qs("cmp-unknown-current").textContent = "--";

  setDeltaElement("cmp-total-delta", 0, "--");
  setDeltaElement("cmp-avg-delta", 0, "--");
  setDeltaElement("cmp-apps-delta", 0, "--");
  setDeltaElement("cmp-unknown-delta", 0, "--", true);
}

function buildPreviousRange(current) {
  const start = current.range_start_date;
  const daysCount = Math.max(1, Number(current.days_count) || 1);
  if (!start) {
    return null;
  }

  const prevEnd = addDaysIso(start, -1);
  const prevStart = addDaysIso(prevEnd, -(daysCount - 1));
  return { start: prevStart, end: prevEnd };
}

async function loadCustomOverview(startDate, endDate) {
  const url = new URL("/api/overview", window.location.origin);
  url.searchParams.set("mode", "custom");
  url.searchParams.set("start_date", startDate);
  url.searchParams.set("end_date", endDate);

  const res = await fetch(url.toString());
  if (!res.ok) {
    throw new Error("No se pudo consultar período comparativo");
  }
  return res.json();
}

function renderComparison(current, previous, previousRange) {
  qs("compare-reference").textContent = `Referencia: ${formatShortDate(previousRange.start)} - ${formatShortDate(previousRange.end)}`;

  const currentTotal = Number(current.total_seconds) || 0;
  const currentAvg = getAverageSeconds(current);
  const currentApps = Number(current.distinct_apps) || 0;
  const currentUnknown = Number(current.unattributed_seconds) || 0;

  const prevTotal = Number(previous.total_seconds) || 0;
  const prevAvg = getAverageSeconds(previous);
  const prevApps = Number(previous.distinct_apps) || 0;
  const prevUnknown = Number(previous.unattributed_seconds) || 0;

  qs("cmp-total-current").textContent = formatDuration(currentTotal);
  qs("cmp-avg-current").textContent = formatDuration(currentAvg);
  qs("cmp-apps-current").textContent = String(currentApps);
  qs("cmp-unknown-current").textContent = formatDuration(currentUnknown);

  const totalDiff = currentTotal - prevTotal;
  const avgDiff = currentAvg - prevAvg;
  const appsDiff = currentApps - prevApps;
  const unknownDiff = currentUnknown - prevUnknown;

  setDeltaElement("cmp-total-delta", totalDiff, formatDelta(totalDiff, prevTotal, (n) => formatDuration(n)));
  setDeltaElement("cmp-avg-delta", avgDiff, formatDelta(avgDiff, prevAvg, (n) => formatDuration(n)));
  setDeltaElement("cmp-apps-delta", appsDiff, formatDelta(appsDiff, prevApps, (n) => String(n)));
  setDeltaElement(
    "cmp-unknown-delta",
    unknownDiff,
    formatDelta(unknownDiff, prevUnknown, (n) => formatDuration(n)),
    true
  );
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

function renderOpenApps(data) {
  const root = qs("open-apps-list");
  const counts = data?.app_counts || [];
  const activeApp = data?.active?.app || "";

  if (!counts.length) {
    root.innerHTML = '<p class="empty">No se pudieron leer ventanas abiertas.</p>';
    return;
  }

  root.innerHTML = "";
  for (const item of counts.slice(0, 20)) {
    const row = document.createElement("div");
    row.className = "top-row";
    const activeBadge = item.app === activeApp ? " (activa)" : "";
    row.innerHTML = `
      <header>
        <strong>${item.app}${activeBadge}</strong>
        <span>${item.windows} ventana(s)</span>
      </header>
      <div class="track"><div class="fill" style="width: ${Math.min(100, item.windows * 12)}%;"></div></div>
    `;
    root.appendChild(row);
  }
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

async function loadOverview() {
  const url = buildOverviewUrl();
  const res = await fetch(url.toString());
  if (!res.ok) {
    throw new Error("No se pudo consultar /api/overview");
  }

  const data = await res.json();
  setOverviewMetrics(data);
  renderDonut(data.top_apps || [], Number(data.total_seconds) || 0);
  renderRanking(data.top_apps || []);

  const period = renderPeriodChart(data);
  renderTrendChart(period.labels, period.values);

  const previousRange = buildPreviousRange(data);
  if (!previousRange) {
    clearComparison("Referencia: sin datos comparativos");
  } else {
    try {
      const previous = await loadCustomOverview(previousRange.start, previousRange.end);
      renderComparison(data, previous, previousRange);
    } catch {
      clearComparison("Referencia: error al cargar comparativo");
    }
  }

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

async function loadWindows() {
  const res = await fetch("/api/windows?limit=400");
  if (!res.ok) {
    throw new Error("No se pudo consultar /api/windows");
  }
  const data = await res.json();
  renderOpenApps(data);
}

async function refreshAll() {
  try {
    await loadHealth();
    await Promise.all([loadOverview(), loadRecent(), loadWindows()]);
  } catch (err) {
    setStatus(false, "Error de conexión");
    qs("updated-at").textContent = String(err.message || err);
  }
}

function init() {
  const modeSelect = qs("range-mode");
  const anchorInput = qs("anchor-date");
  const startInput = qs("start-date");
  const endInput = qs("end-date");

  const today = todayIso();
  state.mode = "day";
  state.anchorDate = today;
  state.startDate = today;
  state.endDate = today;

  modeSelect.value = state.mode;
  anchorInput.value = today;
  startInput.value = today;
  endInput.value = today;
  setModeUi(state.mode);

  modeSelect.addEventListener("change", () => {
    state.mode = modeSelect.value;
    setModeUi(state.mode);
  });

  anchorInput.addEventListener("change", () => {
    state.anchorDate = anchorInput.value || todayIso();
  });

  startInput.addEventListener("change", () => {
    state.startDate = startInput.value || null;
  });

  endInput.addEventListener("change", () => {
    state.endDate = endInput.value || null;
  });

  qs("refresh-btn").addEventListener("click", () => {
    refreshAll();
  });

  refreshAll();
  setInterval(refreshAll, 15000);
}

document.addEventListener("DOMContentLoaded", init);
