// Dashboard data-fetching, live updates (WebSocket), and the equity-curve
// chart (hand-rolled SVG — no charting library, matching the rest of this
// project's style; see src/backtesting/backtest_reports.py's static report
// for the same technique).

const $ = (id) => document.getElementById(id);

// -- Signals table + stat tiles ---------------------------------------------------------

function statusPillClass(status) {
  return status || "open";
}

function renderSignalsTable(signals) {
  const tbody = $("signals-body");
  if (!signals.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="empty-state">No signals yet — the scheduler hasn\'t found a BUY setup in this pass.</td></tr>';
  } else {
    tbody.innerHTML = signals.map((s) => `
      <tr>
        <td>${new Date(s.created_at).toLocaleString()}</td>
        <td>${s.symbol}</td>
        <td>${s.timeframe}</td>
        <td><span class="pill buy">${s.side.toUpperCase()}</span></td>
        <td>${s.entry.toFixed(4)}</td>
        <td>${s.sl.toFixed(4)}</td>
        <td>${s.tp.toFixed(4)}</td>
        <td>${s.risk_reward.toFixed(2)}</td>
        <td>${(s.confidence * 100).toFixed(0)}%</td>
        <td><span class="pill ${statusPillClass(s.status)}">${s.status}</span></td>
      </tr>
    `).join("");
  }

  $("stat-total").textContent = signals.length;
  $("stat-open").textContent = signals.filter((s) => s.status === "open").length;
  $("stat-tp").textContent = signals.filter((s) => s.status === "hit_tp").length;
  $("stat-sl").textContent = signals.filter((s) => s.status === "hit_sl").length;
}

async function loadSignals() {
  try {
    const resp = await fetch("/api/signals?limit=50");
    const signals = await resp.json();
    renderSignalsTable(signals);
  } catch (err) {
    $("signals-body").innerHTML = `<tr><td colspan="10" class="empty-state">Failed to load signals: ${err}</td></tr>`;
  }
}

function prependLiveSignal(event) {
  // A new signal arrived over the WebSocket — just reload the table from
  // the API rather than hand-merging state; 50 rows is cheap to refetch.
  loadSignals();
}

// -- WebSocket (live updates) ---------------------------------------------------------

function connectWebSocket() {
  const badge = $("conn-badge");
  const text = $("conn-text");
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onopen = () => {
    badge.classList.add("connected");
    text.textContent = "live";
  };
  ws.onclose = () => {
    badge.classList.remove("connected");
    text.textContent = "disconnected — retrying…";
    setTimeout(connectWebSocket, 3000);
  };
  ws.onerror = () => ws.close();
  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === "signal") prependLiveSignal(msg);
    } catch (_) { /* ignore malformed frames */ }
  };
}

// -- Equity curve chart (hand-rolled SVG, single series) ---------------------------------------------------------

function renderEquityCurve(container, equityCurve) {
  const values = [1.0, ...equityCurve.map((p) => p.equity)];
  const labels = [null, ...equityCurve.map((p) => p.timestamp)];

  if (values.length < 2) {
    container.innerHTML = '<p class="empty-state">Not enough trades to plot an equity curve.</p>';
    return;
  }

  const width = 640, height = 220, pad = 12;
  const lo = Math.min(...values), hi = Math.max(...values);
  const span = (hi - lo) || 1;
  const plotW = width - 2 * pad, plotH = height - 2 * pad;

  const point = (i, v) => [
    pad + (i / (values.length - 1)) * plotW,
    pad + (1 - (v - lo) / span) * plotH,
  ];

  const points = values.map((v, i) => point(i, v));
  const pathD = points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const [, baselineY] = point(0, 1.0);

  const seriesColor = getComputedStyle(document.body).getPropertyValue("--series-1").trim() || "#2a78d6";

  container.innerHTML = `
    <div class="equity-chart-wrap">
      <svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="Equity curve">
        <line x1="${pad}" y1="${baselineY.toFixed(1)}" x2="${width - pad}" y2="${baselineY.toFixed(1)}"
              stroke="var(--baseline)" stroke-dasharray="4,4" />
        <path d="${pathD}" fill="none" stroke="${seriesColor}" stroke-width="2"
              stroke-linecap="round" stroke-linejoin="round" />
        ${points.map(([x, y]) => `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.5" fill="${seriesColor}" opacity="0" class="ec-pt" />`).join("")}
      </svg>
      <div class="toast" id="ec-tooltip">Hover the line for equity at each trade close.</div>
    </div>
  `;

  const svg = container.querySelector("svg");
  const tooltip = container.querySelector("#ec-tooltip");
  svg.addEventListener("mousemove", (evt) => {
    const rect = svg.getBoundingClientRect();
    const x = ((evt.clientX - rect.left) / rect.width) * width;
    const i = Math.round(((x - pad) / plotW) * (values.length - 1));
    if (i < 0 || i >= values.length) return;
    const label = labels[i] ? new Date(labels[i]).toLocaleString() : "start";
    tooltip.textContent = `${label} — equity ${values[i].toFixed(4)}x`;
  });
  svg.addEventListener("mouseleave", () => {
    tooltip.textContent = "Hover the line for equity at each trade close.";
  });
}

// -- Backtest panel ---------------------------------------------------------

function fmtPct(x) { return x === null || x === undefined ? "–" : `${(x * 100).toFixed(1)}%`; }
function fmtSigned(x) { return x === null || x === undefined ? "–" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(1)}%`; }

function renderBacktestResults(data) {
  const s = data.stats;
  const v = data.validation;
  const checksHtml = Object.entries(v.checks).map(([name, ok]) => `
    <li><span class="check-icon ${ok ? "pass" : "fail"}">${ok ? "✓" : "✗"}</span> ${name}</li>
  `).join("");

  const returnClass = s.total_return_pct >= 0 ? "good" : "critical";
  const pfDisplay = s.profit_factor === null ? "∞" : s.profit_factor.toFixed(2);

  $("backtest-results").innerHTML = `
    <p class="verdict ${v.passed ? "pass" : "fail"}">${v.passed ? "PASSED" : "FAILED"} validation</p>
    <div class="stat-tiles">
      <div class="stat-tile"><div class="label">Trades</div><div class="value">${s.total_trades}</div></div>
      <div class="stat-tile"><div class="label">Win Rate</div><div class="value">${fmtPct(s.win_rate)}</div></div>
      <div class="stat-tile"><div class="label">Profit Factor</div><div class="value">${pfDisplay}</div></div>
      <div class="stat-tile"><div class="label">Max Drawdown</div><div class="value critical">${fmtPct(s.max_drawdown_pct)}</div></div>
      <div class="stat-tile"><div class="label">Total Return</div><div class="value ${returnClass}">${fmtSigned(s.total_return_pct)}</div></div>
      <div class="stat-tile"><div class="label">Sharpe (per-trade)</div><div class="value">${s.sharpe_ratio.toFixed(2)}</div></div>
    </div>
    <h2 style="margin-top:20px;">Equity Curve</h2>
    <div id="equity-curve-container"></div>
    <ul class="check-list">${checksHtml}</ul>
    <h2 style="margin-top:20px;">Trade Log (${data.trades.length})</h2>
    <div class="table-scroll">
      <table>
        <thead><tr><th>Entry Time</th><th>Entry</th><th>SL</th><th>TP</th><th>Exit</th><th>Reason</th><th>P&amp;L</th></tr></thead>
        <tbody>
          ${data.trades.map((t) => `
            <tr>
              <td>${new Date(t.entry_time).toLocaleString()}</td>
              <td>${t.entry.toFixed(4)}</td>
              <td>${t.sl.toFixed(4)}</td>
              <td>${t.tp.toFixed(4)}</td>
              <td>${t.exit_price.toFixed(4)}</td>
              <td><span class="pill ${t.exit_reason}">${t.exit_reason}</span></td>
              <td class="${t.pnl_pct >= 0 ? "" : ""}" style="color:${t.pnl_pct >= 0 ? "var(--delta-good)" : "var(--status-critical)"}">${fmtSigned(t.pnl_pct)}</td>
            </tr>
          `).join("") || '<tr><td colspan="7" class="empty-state">No trades in this backtest.</td></tr>'}
        </tbody>
      </table>
    </div>
  `;
  renderEquityCurve($("equity-curve-container"), data.equity_curve);
}

function setupBacktestForm() {
  const form = $("backtest-form");
  const toast = $("bt-toast");
  const submitBtn = $("bt-submit");

  form.addEventListener("submit", async (evt) => {
    evt.preventDefault();
    const symbol = $("bt-symbol").value.trim();
    const timeframe = $("bt-timeframe").value;
    const limit = $("bt-limit").value;
    if (!symbol) return;

    submitBtn.disabled = true;
    toast.textContent = `Running backtest for ${symbol} (${timeframe})… this fetches live data, may take a few seconds.`;
    $("backtest-results").innerHTML = "";

    try {
      const resp = await fetch(`/api/backtest?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}`);
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      toast.textContent = `Done — ${data.candles_used} candles used.`;
      renderBacktestResults(data);
    } catch (err) {
      toast.textContent = `Backtest failed: ${err.message || err}`;
    } finally {
      submitBtn.disabled = false;
    }
  });
}

function setupTestAlertButton() {
  const btn = $("test-alert-btn");
  const toast = $("alert-toast");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    toast.textContent = "Sending…";
    try {
      const resp = await fetch("/api/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: "Test alert from the dashboard" }),
      });
      const data = await resp.json();
      const entries = Object.entries(data.dispatched || {});
      toast.textContent = entries.length
        ? entries.map(([ch, ok]) => `${ch}: ${ok ? "sent" : "failed"}`).join(", ")
        : "No channels are currently enabled (check config/alerts.yaml).";
    } catch (err) {
      toast.textContent = `Failed: ${err}`;
    } finally {
      btn.disabled = false;
    }
  });
}

// -- Level Watcher Alerts (stats + charts + table) ---------------------------------------------------------
//
// Unlike Recent Signals (server-computed via /api/signals), stats here are
// derived client-side from the full /api/level-alerts list — the same
// pattern used for the Recent Signals stat tiles above, kept consistent
// rather than adding a separate aggregating endpoint.

function seriesColorVar(varName, fallback) {
  const v = getComputedStyle(document.body).getPropertyValue(varName).trim();
  return v || fallback;
}

function computeDailyCountsByKind(alerts, days) {
  const dates = [];
  const now = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    dates.push(d.toISOString().slice(0, 10));
  }
  const index = Object.fromEntries(dates.map((d, i) => [d, i]));
  const supportCounts = dates.map(() => 0);
  const resistanceCounts = dates.map(() => 0);
  alerts.forEach((a) => {
    const key = String(a.created_at).slice(0, 10);
    if (!(key in index)) return;
    if (a.kind === "support") supportCounts[index[key]]++;
    else resistanceCounts[index[key]]++;
  });
  return {
    categories: dates.map((d) => d.slice(5)), // MM-DD
    series: [
      { name: "แนวรับ", color: seriesColorVar("--series-1", "#2a78d6"), values: supportCounts },
      { name: "แนวต้าน", color: seriesColorVar("--series-2", "#eb6834"), values: resistanceCounts },
    ],
  };
}

function computeSymbolCounts(alerts, topN) {
  const counts = {};
  alerts.forEach((a) => { counts[a.symbol] = (counts[a.symbol] || 0) + 1; });
  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, topN);
  return { labels: sorted.map((s) => s[0]), values: sorted.map((s) => s[1]) };
}

// A stacked vertical bar chart — thin bars, 2px gap between adjacent bars
// AND between stacked segments (dataviz mark spec), rounded top on the
// topmost segment only, hover tooltip per segment.
function renderStackedBarChart(container, { categories, series }) {
  const width = 640, height = 220, pad = 12, topPad = 16, bottomPad = 26;
  const plotW = width - 2 * pad;
  const plotH = height - topPad - bottomPad;
  const totals = categories.map((_, i) => series.reduce((sum, s) => sum + s.values[i], 0));
  const max = Math.max(...totals, 1);
  const slotW = plotW / categories.length;
  const barW = Math.max(4, slotW - 3);
  const segGap = 2;

  let rects = "";
  categories.forEach((cat, i) => {
    const x = pad + i * slotW + (slotW - barW) / 2;
    let yCursor = topPad + plotH;
    series.forEach((s) => {
      const v = s.values[i];
      if (v <= 0) return;
      const h = (v / max) * plotH;
      const y = yCursor - h;
      const segH = Math.max(h - segGap, 1);
      rects += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${segH.toFixed(1)}" rx="2" fill="${s.color}" class="bar-rect" data-label="${cat} ${s.name}" data-value="${v}"></rect>`;
      yCursor = y;
    });
  });

  const labels = categories
    .map((cat, i) => `<text x="${(pad + i * slotW + slotW / 2).toFixed(1)}" y="${height - 8}" font-size="9" fill="var(--text-muted)" text-anchor="middle">${cat}</text>`)
    .join("");

  const legend = series
    .map((s) => `<span style="display:inline-flex;align-items:center;gap:4px;"><span class="swatch" style="background:${s.color};"></span>${s.name}</span>`)
    .join("");

  container.innerHTML = `
    <div class="chart-legend" style="font-size:0.75rem;color:var(--text-secondary);margin-bottom:6px;">${legend}</div>
    <div class="equity-chart-wrap">
      <svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="Alerts per day">
        <line x1="${pad}" y1="${topPad + plotH}" x2="${width - pad}" y2="${topPad + plotH}" stroke="var(--baseline)"></line>
        ${rects}
        ${labels}
      </svg>
    </div>
    <div class="toast" id="la-daily-tooltip">วางเมาส์บนแท่งกราฟเพื่อดูจำนวน</div>
  `;
  const tooltip = container.querySelector("#la-daily-tooltip");
  container.querySelectorAll(".bar-rect").forEach((rect) => {
    rect.addEventListener("mouseenter", () => { tooltip.textContent = `${rect.dataset.label}: ${rect.dataset.value}`; });
    rect.addEventListener("mouseleave", () => { tooltip.textContent = "วางเมาส์บนแท่งกราฟเพื่อดูจำนวน"; });
  });
}

// A horizontal ranked bar chart for "which symbols got alerted most" —
// single measure (count), so one hue per the dataviz sequential/magnitude
// rule, sorted descending.
function renderRankedBarChart(container, { labels, values }) {
  if (!labels.length) {
    container.innerHTML = '<p class="empty-state">No alerts yet.</p>';
    return;
  }
  const width = 640, rowH = 24, pad = 8, labelW = 90;
  const height = labels.length * rowH + pad * 2;
  const plotW = width - pad * 2 - labelW - 36;
  const max = Math.max(...values, 1);
  const color = seriesColorVar("--series-1", "#2a78d6");

  const rows = labels.map((label, i) => {
    const y = pad + i * rowH;
    const w = Math.max((values[i] / max) * plotW, 2);
    return `
      <text x="${labelW - 6}" y="${(y + rowH / 2 + 3).toFixed(1)}" font-size="10" fill="var(--text-secondary)" text-anchor="end">${label}</text>
      <rect x="${labelW}" y="${(y + 4).toFixed(1)}" width="${w.toFixed(1)}" height="${rowH - 8}" rx="3" fill="${color}"></rect>
      <text x="${(labelW + w + 6).toFixed(1)}" y="${(y + rowH / 2 + 3).toFixed(1)}" font-size="10" fill="var(--text-muted)">${values[i]}</text>
    `;
  }).join("");

  container.innerHTML = `
    <div class="equity-chart-wrap">
      <svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" role="img" aria-label="Alerts by symbol">${rows}</svg>
    </div>
  `;
}

function renderLevelAlertStats(alerts) {
  const todayKey = new Date().toISOString().slice(0, 10);
  $("la-stat-total").textContent = alerts.length;
  $("la-stat-today").textContent = alerts.filter((a) => String(a.created_at).slice(0, 10) === todayKey).length;
  $("la-stat-support").textContent = alerts.filter((a) => a.kind === "support").length;
  $("la-stat-resistance").textContent = alerts.filter((a) => a.kind === "resistance").length;
}

function renderLevelAlertsTable(alerts) {
  const tbody = $("level-alerts-body");
  if (!alerts.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty-state">ยังไม่มี alert — ไม่มีสัญลักษณ์ไหนเข้าใกล้แนวรับ/แนวต้านตามเกณฑ์ที่ตั้งไว้</td></tr>';
    return;
  }
  tbody.innerHTML = alerts.slice(0, 50).map((a) => `
    <tr>
      <td>${new Date(a.created_at).toLocaleString()}</td>
      <td>${a.symbol}</td>
      <td>${a.timeframe}</td>
      <td><span class="pill ${a.kind}">${a.kind === "support" ? "แนวรับ" : "แนวต้าน"}</span></td>
      <td>${a.level.toFixed(4)}</td>
      <td>${a.price.toFixed(4)}</td>
      <td>${(a.distance_pct * 100).toFixed(2)}%</td>
    </tr>
  `).join("");
}

async function loadLevelAlerts() {
  try {
    const resp = await fetch("/api/level-alerts?limit=1000");
    const alerts = await resp.json();
    renderLevelAlertStats(alerts);
    renderStackedBarChart($("la-chart-daily"), computeDailyCountsByKind(alerts, 14));
    renderRankedBarChart($("la-chart-symbol"), computeSymbolCounts(alerts, 10));
    renderLevelAlertsTable(alerts);
  } catch (err) {
    $("level-alerts-body").innerHTML = `<tr><td colspan="7" class="empty-state">Failed to load: ${err}</td></tr>`;
  }
}

// -- Init ---------------------------------------------------------

loadSignals();
loadLevelAlerts();
connectWebSocket();
setupBacktestForm();
setupTestAlertButton();
setInterval(loadSignals, 30000); // fallback refresh even if a WS message is missed
setInterval(loadLevelAlerts, 30000);
