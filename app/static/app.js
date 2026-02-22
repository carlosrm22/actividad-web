const state = {
  mode: "day",
  groupBy: "app",
  anchorDate: null,
  startDate: null,
  endDate: null,
  paused: false,
  categoryMap: {},
  privacyRules: [],
  lastOverview: null,
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
  "#f59e0b",
  "#14b8a6",
];

const CATEGORY_OPTIONS = [
  "Sin categoría",
  "Trabajo",
  "Desarrollo",
  "Comunicación",
  "Música",
  "Navegación",
  "Documentación",
  "Productividad",
  "Entretenimiento",
  "Sistema",
  "Inactividad",
];

function qs(id) {
  const el = document.getElementById(id);
  if (el) {
    return el;
  }

  console.warn(`[Actividad UI] elemento faltante: #${id}`);
  return {
    textContent: "",
    innerHTML: "",
    value: "",
    checked: false,
    files: null,
    style: {},
    classList: {
      add() {},
      remove() {},
      toggle() {},
    },
    appendChild() {},
    addEventListener() {},
    setAttribute() {},
    getAttribute() {
      return "";
    },
  };
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

async function fetchJson(url, options = undefined) {
  const res = await fetch(url, options);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`HTTP ${res.status}: ${body || "error"}`);
  }
  return res.json();
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

function buildOverviewUrl(params = {}) {
  const mode = params.mode || state.mode;
  const groupBy = params.groupBy || state.groupBy;
  const url = new URL("/api/overview", window.location.origin);
  url.searchParams.set("mode", mode);
  url.searchParams.set("group_by", groupBy);

  if (mode === "custom") {
    const startDate = params.startDate || state.startDate;
    const endDate = params.endDate || state.endDate;
    if (!startDate || !endDate) {
      throw new Error("Selecciona inicio y fin para el rango personalizado");
    }
    url.searchParams.set("start_date", startDate);
    url.searchParams.set("end_date", endDate);
    return url;
  }

  const anchorDate = params.anchorDate || state.anchorDate;
  if (anchorDate) {
    url.searchParams.set("anchor_date", anchorDate);
  }
  return url;
}

function buildExportUrl(format) {
  const url = new URL("/api/export/sessions", window.location.origin);
  url.searchParams.set("format", format);
  url.searchParams.set("mode", state.mode);

  if (state.mode === "custom") {
    if (!state.startDate || !state.endDate) {
      throw new Error("Selecciona inicio/fin para exportar rango personalizado");
    }
    url.searchParams.set("start_date", state.startDate);
    url.searchParams.set("end_date", state.endDate);
  } else if (state.anchorDate) {
    url.searchParams.set("anchor_date", state.anchorDate);
  }

  return url.toString();
}

function downloadObjectAsJson(payload, filename) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(href);
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
  const activeHuman = data.active_human || data.total_human || "0s";
  const effectiveHuman = data.effective_human || activeHuman;
  const passiveHuman = data.passive_human || "0s";
  const afkHuman = data.afk_human || "0s";
  const sleepHuman = data.sleep_human || "0s";

  qs("total-active").textContent = activeHuman;
  qs("effective-active").textContent = effectiveHuman;
  qs("passive-active").textContent = passiveHuman;
  qs("afk-active").textContent = afkHuman;
  qs("sleep-active").textContent = sleepHuman;
  qs("distinct-apps").textContent = String(data.distinct_apps || 0);
  qs("unknown-active").textContent = data.unattributed_human || "0s";

  qs("period-summary").textContent = formatPeriodSummary(data);
  qs("donut-total").textContent = activeHuman;

  const groupedByCategory = (data.group_by || "app") === "category";
  qs("grouping-summary").textContent = groupedByCategory
    ? "Agrupado por categoría."
    : "Agrupado por aplicación.";

  qs("ranking-subtitle").textContent = groupedByCategory
    ? "Detalle completo por categoría del período."
    : "Detalle completo por aplicación del período.";
}

function renderDonut(topItems) {
  const donut = qs("apps-donut");
  const legend = qs("apps-legend");

  if (!Array.isArray(topItems) || !topItems.length) {
    donut.style.background = "conic-gradient(#d5dfdc 0% 100%)";
    legend.innerHTML = '<p class="empty">Sin actividad para mostrar distribución.</p>';
    return;
  }

  const selected = topItems.slice(0, 7).map((item) => ({
    app: item.app,
    seconds: Number(item.seconds) || 0,
  }));

  const restSeconds = topItems.slice(7).reduce((acc, item) => acc + (Number(item.seconds) || 0), 0);
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

function renderRanking(topItems) {
  const body = qs("ranking-body");
  if (!topItems?.length) {
    body.innerHTML = '<tr><td colspan="4" class="empty">Sin datos para este período.</td></tr>';
    return;
  }

  body.innerHTML = topItems
    .slice(0, 30)
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

function renderMiniSeries(containerId, labels, values) {
  const root = qs(containerId);
  if (!Array.isArray(values) || values.length === 0) {
    root.innerHTML = '<p class="empty">Sin datos.</p>';
    return;
  }

  const peak = Math.max(...values, 1);
  root.innerHTML = "";

  for (let i = 0; i < values.length; i += 1) {
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
}

function getAverageSeconds(data) {
  const daysCount = Math.max(1, Number(data.days_count) || 1);
  const total = Math.max(0, Number(data.active_seconds ?? data.total_seconds) || 0);
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
  qs("cmp-afk-current").textContent = "--";

  setDeltaElement("cmp-total-delta", 0, "--");
  setDeltaElement("cmp-avg-delta", 0, "--");
  setDeltaElement("cmp-apps-delta", 0, "--");
  setDeltaElement("cmp-unknown-delta", 0, "--", true);
  setDeltaElement("cmp-afk-delta", 0, "--", true);
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
  url.searchParams.set("group_by", state.groupBy);
  return fetchJson(url.toString());
}

function renderComparison(current, previous, previousRange) {
  qs("compare-reference").textContent = `Referencia: ${formatShortDate(previousRange.start)} - ${formatShortDate(previousRange.end)}`;

  const currentTotal = Number(current.active_seconds ?? current.total_seconds) || 0;
  const currentAvg = getAverageSeconds(current);
  const currentApps = Number(current.distinct_apps) || 0;
  const currentUnknown = Number(current.unattributed_seconds) || 0;
  const currentAfk = Number(current.afk_seconds) || 0;

  const prevTotal = Number(previous.active_seconds ?? previous.total_seconds) || 0;
  const prevAvg = getAverageSeconds(previous);
  const prevApps = Number(previous.distinct_apps) || 0;
  const prevUnknown = Number(previous.unattributed_seconds) || 0;
  const prevAfk = Number(previous.afk_seconds) || 0;

  qs("cmp-total-current").textContent = formatDuration(currentTotal);
  qs("cmp-avg-current").textContent = formatDuration(currentAvg);
  qs("cmp-apps-current").textContent = String(currentApps);
  qs("cmp-unknown-current").textContent = formatDuration(currentUnknown);
  qs("cmp-afk-current").textContent = formatDuration(currentAfk);

  const totalDiff = currentTotal - prevTotal;
  const avgDiff = currentAvg - prevAvg;
  const appsDiff = currentApps - prevApps;
  const unknownDiff = currentUnknown - prevUnknown;
  const afkDiff = currentAfk - prevAfk;

  setDeltaElement("cmp-total-delta", totalDiff, formatDelta(totalDiff, prevTotal, (n) => formatDuration(n)));
  setDeltaElement("cmp-avg-delta", avgDiff, formatDelta(avgDiff, prevAvg, (n) => formatDuration(n)));
  setDeltaElement("cmp-apps-delta", appsDiff, formatDelta(appsDiff, prevApps, (n) => String(n)));
  setDeltaElement("cmp-unknown-delta", unknownDiff, formatDelta(unknownDiff, prevUnknown, (n) => formatDuration(n)), true);
  setDeltaElement("cmp-afk-delta", afkDiff, formatDelta(afkDiff, prevAfk, (n) => formatDuration(n)), true);
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

async function loadCategories() {
  const data = await fetchJson("/api/categories");
  const mapping = {};
  for (const item of data.items || []) {
    mapping[item.app] = item.category;
  }
  state.categoryMap = mapping;
  return mapping;
}

function appCategoryCandidates(byApp) {
  const ignored = new Set(["Inactivo", "Proceso", "Desconocido", "Privado"]);
  return (byApp || [])
    .map((row) => row.app)
    .filter((name) => name && !ignored.has(name))
    .slice(0, 20);
}

function buildCategoryOptions(current) {
  const set = new Set(CATEGORY_OPTIONS);
  if (current) set.add(current);
  return Array.from(set.values());
}

function renderCategoryEditor(byApp, mapping) {
  const body = qs("category-editor-body");
  const apps = appCategoryCandidates(byApp);
  if (!apps.length) {
    body.innerHTML = '<tr><td colspan="2" class="empty">Sin aplicaciones para categorizar.</td></tr>';
    return;
  }

  body.innerHTML = apps
    .map((appName) => {
      const current = mapping[appName] || "Sin categoría";
      const options = buildCategoryOptions(current)
        .map((opt) => `<option value="${opt}" ${opt === current ? "selected" : ""}>${opt}</option>`)
        .join("");
      return `
        <tr>
          <td>${appName}</td>
          <td>
            <select class="category-select" data-app="${appName}">
              ${options}
            </select>
          </td>
        </tr>
      `;
    })
    .join("");
}

async function saveCategories() {
  const status = qs("category-save-status");
  const selects = Array.from(document.querySelectorAll(".category-select"));
  if (!selects.length) {
    status.textContent = "No hay categorías para guardar.";
    return;
  }

  status.textContent = "Guardando...";
  let changed = 0;

  for (const sel of selects) {
    const appName = sel.getAttribute("data-app") || "";
    const newCategory = sel.value || "Sin categoría";
    const current = state.categoryMap[appName] || "Sin categoría";
    if (newCategory === current) {
      continue;
    }

    await fetchJson(`/api/categories/${encodeURIComponent(appName)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category: newCategory }),
    });
    state.categoryMap[appName] = newCategory;
    changed += 1;
  }

  status.textContent = changed > 0 ? `Guardado (${changed} cambios).` : "Sin cambios.";

  if (changed > 0) {
    await refreshAll();
  }
}

function renderPrivacyRules(rules) {
  const body = qs("privacy-rules-body");
  if (!rules?.length) {
    body.innerHTML = '<tr><td colspan="5" class="empty">Sin reglas.</td></tr>';
    return;
  }

  body.innerHTML = rules
    .map(
      (rule) => `
      <tr>
        <td>${rule.scope === "title" ? "Título" : "Aplicación"}</td>
        <td>${rule.match_mode}</td>
        <td><code>${rule.pattern}</code></td>
        <td class="privacy-enabled"><input class="privacy-toggle" type="checkbox" data-rule-id="${rule.id}" ${
          rule.enabled ? "checked" : ""
        } /></td>
        <td><button class="rule-danger privacy-delete" data-rule-id="${rule.id}" type="button">Eliminar</button></td>
      </tr>
    `
    )
    .join("");
}

function updatePrivacySummary() {
  const enabled = state.privacyRules.filter((r) => Boolean(r.enabled)).length;
  qs("privacy-summary").textContent = `Reglas activas: ${enabled} / ${state.privacyRules.length}`;
}

async function loadPrivacyRules() {
  const data = await fetchJson("/api/privacy/rules");
  state.privacyRules = data.items || [];
  renderPrivacyRules(state.privacyRules);
  updatePrivacySummary();
}

async function createPrivacyRule() {
  const scope = qs("privacy-scope").value;
  const matchMode = qs("privacy-mode").value;
  const pattern = (qs("privacy-pattern").value || "").trim();
  if (!pattern) {
    qs("backup-status").textContent = "Escribe un patrón para la exclusión.";
    return;
  }

  await fetchJson("/api/privacy/rules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope, match_mode: matchMode, pattern, enabled: true }),
  });

  qs("privacy-pattern").value = "";
  await loadPrivacyRules();
  qs("backup-status").textContent = "Regla de privacidad agregada.";
}

async function togglePrivacyRule(ruleId, enabled) {
  await fetchJson(`/api/privacy/rules/${ruleId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  await loadPrivacyRules();
}

async function deletePrivacyRule(ruleId) {
  await fetchJson(`/api/privacy/rules/${ruleId}`, { method: "DELETE" });
  await loadPrivacyRules();
}

async function exportCsv() {
  const url = buildExportUrl("csv");
  window.location.assign(url);
}

async function exportJson() {
  const data = await fetchJson(buildExportUrl("json"));
  const stamp = new Date().toISOString().replaceAll(":", "-").slice(0, 19);
  downloadObjectAsJson(data, `actividad-export-${stamp}.json`);
}

async function exportBackup() {
  const data = await fetchJson("/api/backup/export");
  const stamp = new Date().toISOString().replaceAll(":", "-").slice(0, 19);
  downloadObjectAsJson(data, `actividad-backup-${stamp}.json`);
  qs("backup-status").textContent = "Backup exportado.";
}

async function restoreBackup() {
  const input = qs("restore-file-input");
  const replace = Boolean(qs("restore-replace").checked);
  const file = input.files?.[0];
  if (!file) {
    qs("backup-status").textContent = "Selecciona un archivo JSON de backup.";
    return;
  }

  const raw = await file.text();
  const payload = JSON.parse(raw);

  qs("backup-status").textContent = "Restaurando...";
  const url = new URL("/api/backup/restore", window.location.origin);
  url.searchParams.set("replace", replace ? "1" : "0");

  const result = await fetchJson(url.toString(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  qs("backup-status").textContent = `Restaurado: sesiones ${result.inserted_sessions}, categorías ${result.saved_categories}, reglas ${result.saved_privacy_rules}.`;
  await refreshAll();
}

async function loadRolling30() {
  const end = todayIso();
  const start = addDaysIso(end, -29);
  const data = await loadCustomOverview(start, end);

  const byDay = Array.isArray(data.by_day) ? data.by_day : [];
  const labels = byDay.map((row) => formatDayLabel(row.date));
  const values = byDay.map((row) => Number(row.seconds || 0));

  qs("rolling30-summary").textContent = `Del ${formatShortDate(start)} al ${formatShortDate(end)} · Activo ${
    data.active_human || data.total_human
  } · AFK ${data.afk_human || "0s"}`;

  renderMiniSeries("rolling30-chart", labels, values);
}

async function loadHealth() {
  const data = await fetchJson("/api/health");

  const running = Boolean(data.tracker?.running);
  const paused = Boolean(data.tracker?.paused);
  state.paused = paused;

  if (!running) {
    setStatus(false, "Tracker detenido");
  } else if (paused) {
    setStatus(true, "Pausado");
  } else {
    setStatus(true, "Tracker activo");
  }

  qs("pause-btn").textContent = paused ? "Reanudar" : "Pausar";

  if (Array.isArray(data.notes) && data.notes.length > 0) {
    qs("updated-at").textContent = data.notes[0];
  }

  if (data.privacy && typeof data.privacy.rules_count === "number") {
    qs("privacy-summary").textContent = `Reglas activas: ${data.privacy.enabled_rules || 0} / ${data.privacy.rules_count}`;
  }
}

async function loadOverview() {
  const data = await fetchJson(buildOverviewUrl().toString());
  state.lastOverview = data;

  setOverviewMetrics(data);
  renderDonut(data.top_apps || []);
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

  const categories = await loadCategories();
  renderCategoryEditor(data.by_app || [], categories);

  if (data.updated_at_ts) {
    const stamp = new Date(data.updated_at_ts * 1000).toLocaleTimeString("es-ES", { hour12: false });
    qs("updated-at").textContent = `Actualizado ${stamp}`;
  }
}

async function loadRecent() {
  const data = await fetchJson("/api/recent?limit=30");
  renderRecent(data.items || []);
}

async function loadWindows() {
  const data = await fetchJson("/api/windows?limit=400");
  renderOpenApps(data);
}

async function togglePause() {
  const path = state.paused ? "/api/control/resume" : "/api/control/pause";
  await fetchJson(path, { method: "POST" });
  await refreshAll();
}

async function refreshAll() {
  try {
    await loadHealth();
    await Promise.all([loadOverview(), loadRecent(), loadWindows(), loadRolling30(), loadPrivacyRules()]);
  } catch (err) {
    setStatus(false, "Error de conexión");
    qs("updated-at").textContent = String(err.message || err);
  }
}

function init() {
  const modeSelect = qs("range-mode");
  const groupSelect = qs("group-by");
  const anchorInput = qs("anchor-date");
  const startInput = qs("start-date");
  const endInput = qs("end-date");

  const today = todayIso();
  state.mode = "day";
  state.groupBy = "app";
  state.anchorDate = today;
  state.startDate = today;
  state.endDate = today;

  modeSelect.value = state.mode;
  groupSelect.value = state.groupBy;
  anchorInput.value = today;
  startInput.value = today;
  endInput.value = today;
  setModeUi(state.mode);

  modeSelect.addEventListener("change", () => {
    state.mode = modeSelect.value;
    setModeUi(state.mode);
  });

  groupSelect.addEventListener("change", () => {
    state.groupBy = groupSelect.value;
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

  qs("pause-btn").addEventListener("click", () => {
    togglePause().catch((err) => {
      qs("updated-at").textContent = String(err.message || err);
    });
  });

  qs("save-categories-btn").addEventListener("click", () => {
    saveCategories().catch((err) => {
      qs("category-save-status").textContent = `Error: ${String(err.message || err)}`;
    });
  });

  qs("privacy-add-btn").addEventListener("click", () => {
    createPrivacyRule().catch((err) => {
      qs("backup-status").textContent = `Error privacidad: ${String(err.message || err)}`;
    });
  });

  qs("privacy-rules-body").addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || !target.classList.contains("privacy-toggle")) {
      return;
    }

    const id = Number(target.getAttribute("data-rule-id") || "0");
    if (!id) {
      return;
    }

    togglePrivacyRule(id, target.checked).catch((err) => {
      qs("backup-status").textContent = `Error privacidad: ${String(err.message || err)}`;
      target.checked = !target.checked;
    });
  });

  qs("privacy-rules-body").addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement) || !target.classList.contains("privacy-delete")) {
      return;
    }

    const id = Number(target.getAttribute("data-rule-id") || "0");
    if (!id) {
      return;
    }

    deletePrivacyRule(id)
      .then(() => {
        qs("backup-status").textContent = "Regla eliminada.";
      })
      .catch((err) => {
        qs("backup-status").textContent = `Error privacidad: ${String(err.message || err)}`;
      });
  });

  qs("export-csv-btn").addEventListener("click", () => {
    exportCsv().catch((err) => {
      qs("backup-status").textContent = `Error exportando CSV: ${String(err.message || err)}`;
    });
  });

  qs("export-json-btn").addEventListener("click", () => {
    exportJson().catch((err) => {
      qs("backup-status").textContent = `Error exportando JSON: ${String(err.message || err)}`;
    });
  });

  qs("backup-export-btn").addEventListener("click", () => {
    exportBackup().catch((err) => {
      qs("backup-status").textContent = `Error exportando backup: ${String(err.message || err)}`;
    });
  });

  qs("restore-btn").addEventListener("click", () => {
    restoreBackup().catch((err) => {
      qs("backup-status").textContent = `Error restaurando backup: ${String(err.message || err)}`;
    });
  });

  refreshAll();
  setInterval(refreshAll, 15000);
}

document.addEventListener("DOMContentLoaded", init);
