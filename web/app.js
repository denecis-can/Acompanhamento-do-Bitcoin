// BTC Dashboard — front. Fala apenas com o backend (/api/*), nunca com APIs externas.
"use strict";

const REFRESH_SUMMARY_MS = 30_000;
const REFRESH_CANDLES_MS = 60_000;
const REFRESH_SERIES_MS = 5 * 60_000;

const css = getComputedStyle(document.documentElement);
const token = (name) => css.getPropertyValue(name).trim();
const C = {
  s1: token("--series-1"), s2: token("--series-2"),
  up: token("--up"), down: token("--down"),
  grid: token("--grid"), border: token("--border"),
  text: token("--text-secondary"), muted: token("--text-muted"), surface: token("--surface-1"),
};

// ---------- formatação --------------------------------------------------------

const nf = (min, max = min) => new Intl.NumberFormat("pt-BR", { minimumFractionDigits: min, maximumFractionDigits: max });
const fmt = {
  usd: (v, d = 0) => (v == null ? "—" : "US$ " + nf(d).format(v)),
  compactUsd: (v) => (v == null ? "—" : "US$ " + new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 2 }).format(v)),
  num: (v, d = 2) => (v == null ? "—" : nf(d).format(v)),
  pct: (v, d = 2) => (v == null ? "—" : nf(d).format(v) + "%"),
  signed: (v, d = 2, suffix = "") => (v == null ? "—" : (v > 0 ? "+" : "") + nf(d).format(v) + suffix),
  time: (iso) => (iso ? new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : "—"),
  dateTime: (iso) => (iso ? new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—"),
  shortDate: (ts) => new Date(ts * 1000).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }),
};

const $ = (id) => document.getElementById(id);
const setText = (id, text) => { const el = $(id); if (el) el.textContent = text; };

function setDelta(id, value, text) {
  const el = $(id);
  el.textContent = text;
  el.classList.toggle("up", value > 0);
  el.classList.toggle("down", value < 0);
}

function badge(status) {
  const span = document.createElement("span");
  span.className = "badge";
  span.dataset.status = status || "pendente";
  span.textContent = status || "pendente";
  return span;
}

/** Fonte · horário · status, no canto de cada card. */
function setMeta(key, { source, updated_at, status, error } = {}) {
  const el = document.querySelector(`[data-meta="${key}"]`);
  if (!el) return;
  el.replaceChildren();
  const src = document.createElement("span");
  src.textContent = source || "—";
  const when = document.createElement("time");
  when.textContent = fmt.time(updated_at);
  if (updated_at) when.dateTime = updated_at;
  when.title = updated_at ? new Date(updated_at).toLocaleString("pt-BR") : "sem dados";
  const b = badge(status);
  if (error) b.title = error;
  el.append(src, "·", when, b);
}

async function getJSON(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}

// ---------- gráfico de preço (Lightweight Charts) ----------------------------

let priceChart, candleSeries, candleInterval = "1h";

function initPriceChart() {
  const el = $("price-chart");
  priceChart = LightweightCharts.createChart(el, {
    autoSize: true,
    layout: { background: { color: C.surface }, textColor: C.text, fontSize: 11 },
    grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
    rightPriceScale: { borderColor: C.border },
    timeScale: { borderColor: C.border, timeVisible: true, secondsVisible: false },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    localization: { locale: "pt-BR", priceFormatter: (p) => nf(0).format(p) },
  });
  candleSeries = priceChart.addCandlestickSeries({
    upColor: C.up, downColor: C.down, borderVisible: false, wickUpColor: C.up, wickDownColor: C.down,
  });
  priceChart.subscribeCrosshairMove((param) => {
    const bar = param.seriesData && param.seriesData.get(candleSeries);
    $("ohlc-legend").textContent = bar
      ? `A ${nf(0).format(bar.open)}  M ${nf(0).format(bar.high)}  m ${nf(0).format(bar.low)}  F ${nf(0).format(bar.close)}`
      : " ";
  });

  document.querySelectorAll(".seg-btn").forEach((btn) =>
    btn.addEventListener("click", () => {
      candleInterval = btn.dataset.interval;
      document.querySelectorAll(".seg-btn").forEach((b) => {
        b.classList.toggle("active", b === btn);
        b.setAttribute("aria-selected", b === btn);
      });
      loadCandles(true);
    })
  );
}

async function loadCandles(fit = false) {
  try {
    const { candles } = await getJSON(`/api/candles?interval=${candleInterval}&limit=500`);
    candleSeries.setData(candles.map((c) => ({
      time: Math.floor(c.open_time / 1000), open: c.open, high: c.high, low: c.low, close: c.close,
    })));
    if (fit) priceChart.timeScale().fitContent();
  } catch (e) { console.warn(e); }
}

// ---------- gráficos de linha/barra (Chart.js) --------------------------------

const charts = {};

function baseOptions({ yFormat, legend = false } = {}) {
  return {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "index", intersect: false }, // crosshair: tooltip no x mais próximo
    plugins: {
      legend: { display: legend, labels: { color: C.text, boxWidth: 10, boxHeight: 10, useBorderRadius: true, borderRadius: 2 } },
      tooltip: {
        backgroundColor: "#2a2a27", borderColor: C.border, borderWidth: 1, titleColor: "#f0efec", bodyColor: C.text,
        callbacks: yFormat ? { label: (ctx) => `${ctx.dataset.label}: ${yFormat(ctx.parsed.y)}` } : {},
      },
    },
    scales: {
      x: { grid: { display: false }, border: { color: C.border }, ticks: { color: C.muted, maxTicksLimit: 6, maxRotation: 0 } },
      y: { grid: { color: C.grid }, border: { display: false }, ticks: { color: C.muted, maxTicksLimit: 5 } },
    },
  };
}

function lineDataset(label, color, data) {
  return { label, data, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.2 };
}

function upsertChart(key, canvasId, config) {
  if (charts[key]) {
    charts[key].data = config.data;
    charts[key].update("none");
  } else {
    charts[key] = new Chart($(canvasId), config);
  }
}

async function loadSeries() {
  try {
    const [dvol, fng, d10, d2] = await Promise.all([
      getJSON("/api/series/deribit.dvol?days=7"),
      getJSON("/api/series/fng.value?days=30"),
      getJSON("/api/series/fred.DGS10?days=365"),
      getJSON("/api/series/fred.DGS2?days=365"),
    ]);

    const hourLabel = (ts) => {
      const d = new Date(ts * 1000);
      return `${fmt.shortDate(ts)} ${String(d.getHours()).padStart(2, "0")}h`;
    };
    upsertChart("dvol", "dvol-chart", {
      type: "line",
      data: { labels: dvol.points.map((p) => hourLabel(p.ts)), datasets: [lineDataset("DVOL", C.s1, dvol.points.map((p) => p.value))] },
      options: baseOptions({ yFormat: (v) => fmt.num(v, 2) }),
    });

    upsertChart("fng", "fng-chart", {
      type: "bar",
      data: {
        labels: fng.points.map((p) => fmt.shortDate(p.ts)),
        datasets: [{ label: "Fear & Greed", data: fng.points.map((p) => p.value), backgroundColor: C.s1, borderRadius: { topLeft: 4, topRight: 4 }, borderSkipped: "bottom", barPercentage: 0.8, categoryPercentage: 0.9 }],
      },
      options: (() => { const o = baseOptions({ yFormat: (v) => fmt.num(v, 0) }); o.scales.y.min = 0; o.scales.y.max = 100; return o; })(),
    });

    // 2a e 10a compartilham a mesma unidade (%), então um único eixo é correto.
    const byTs = new Map(d10.points.map((p) => [p.ts, { d10: p.value }]));
    d2.points.forEach((p) => byTs.set(p.ts, { ...(byTs.get(p.ts) || {}), d2: p.value }));
    const ts = [...byTs.keys()].sort((a, b) => a - b);
    $("yields-chart").hidden = !ts.length;
    $("yields-empty").hidden = !!ts.length;
    if (!ts.length) return;
    upsertChart("yields", "yields-chart", {
      type: "line",
      data: {
        labels: ts.map((t) => new Date(t * 1000).toLocaleDateString("pt-BR", { month: "short", year: "2-digit" })),
        datasets: [
          lineDataset("10 anos", C.s1, ts.map((t) => byTs.get(t).d10 ?? null)),
          lineDataset("2 anos", C.s2, ts.map((t) => byTs.get(t).d2 ?? null)),
        ],
      },
      options: baseOptions({ yFormat: (v) => fmt.pct(v, 2), legend: true }),
    });
  } catch (e) { console.warn(e); }
}

// ---------- cards a partir do /api/summary -------------------------------------

const EXCHANGE_LABEL = { binance_futures: "Binance", bybit: "Bybit", okx: "OKX" };

function renderPrice(p) {
  setMeta("price", { ...p, source: p.source ? p.source + (p.fallback ? " (fallback)" : "") : null });
  setText("price-value", fmt.usd(p.value, 2));
  setDelta("price-change", p.change_24h_pct, fmt.signed(p.change_24h_pct, 2, "%"));
  setText("price-high", fmt.usd(p.high_24h));
  setText("price-low", fmt.usd(p.low_24h));
  setText("price-volume", fmt.compactUsd(p.volume_usd_24h));
  if (p.value) document.title = `${fmt.usd(p.value)} · BTC Dashboard`;
}

function renderPremium(pr, sources) {
  if (!pr) {
    setMeta("premium", { source: "Coinbase vs. Binance", status: sources.coinbase?.status === "ok" ? "erro" : sources.coinbase?.status });
    return;
  }
  setMeta("premium", pr);
  // hero em tinta de texto; a direção (cor + seta) vai no % abaixo
  setText("premium-usd", (pr.usd > 0 ? "+" : "") + fmt.usd(pr.usd, 2));
  setDelta("premium-pct", pr.pct, fmt.signed(pr.pct, 3, "%"));
  setText("premium-cb", fmt.usd(pr.coinbase_price, 2));
  setText("premium-bn", fmt.usd(pr.binance_price, 2));
}

function renderDerivatives(d, sources) {
  const wf = d.weighted_funding;
  const fut = sources.binance_futures || {};
  setMeta("funding", { source: wf ? wf.sources.map((s) => EXCHANGE_LABEL[s]).join(" + ") : "Binance + Bybit + OKX", updated_at: fut.updated_at, status: wf ? fut.status : "erro" });
  setText("funding-weighted", wf ? fmt.signed(wf.rate_pct, 4, "%") : "—");
  setText("funding-annual", wf ? `≈ ${fmt.signed(wf.annualized_pct, 1, "%")} ao ano` : "—");

  const tbody = $("funding-table");
  tbody.replaceChildren();
  for (const name of ["binance_futures", "bybit", "okx"]) {
    const e = d.exchanges[name];
    const tr = document.createElement("tr");
    const cells = [EXCHANGE_LABEL[name], e ? fmt.signed(e.funding_rate_pct, 4, "%") : "—", e ? fmt.compactUsd(e.oi_usd) : "—"];
    cells.forEach((text, i) => { const td = document.createElement("td"); td.textContent = text; if (i) td.className = "num"; tr.append(td); });
    const td = document.createElement("td");
    const src = sources[name];
    const b = badge(src?.status);
    if (src?.error) b.title = src.error;
    td.append(b);
    tr.append(td);
    tbody.append(tr);
  }

  setMeta("oi", { source: "Binance + Bybit + OKX", updated_at: fut.updated_at, status: fut.status });
  setText("oi-total", wf ? fmt.compactUsd(wf.total_oi_usd) : "—");
  const oi = d.open_interest;
  setDelta("oi-change", oi?.change_24h_pct, oi ? fmt.signed(oi.change_24h_pct, 2, "%") : "—");

  const ls = d.long_short;
  if (ls) {
    $("ls-long").style.width = ls.long_pct + "%";
    $("ls-short").style.width = ls.short_pct + "%";
    setText("ls-long-pct", fmt.pct(ls.long_pct, 1));
    setText("ls-short-pct", fmt.pct(ls.short_pct, 1));
    setText("ls-ratio", fmt.num(ls.ratio, 2));
  }
}

function renderVolatility(v) {
  setMeta("volatility", v);
  setText("dvol-value", fmt.num(v.dvol, 2));
  setDelta("dvol-change", v.dvol_change_24h, fmt.signed(v.dvol_change_24h, 2));
}

const FNG_PT = { "Extreme Fear": "Medo extremo", Fear: "Medo", Neutral: "Neutro", Greed: "Ganância", "Extreme Greed": "Ganância extrema" };

function renderSentiment(s) {
  setMeta("sentiment", s);
  setText("fng-value", s.value ?? "—");
  setText("fng-class", FNG_PT[s.classification] || s.classification || "—");
  if (s.value != null) $("fng-marker").style.left = s.value + "%";
}

const MACRO_FMT = {
  DGS10: (v) => fmt.pct(v, 2), DGS2: (v) => fmt.pct(v, 2), FEDFUNDS: (v) => fmt.pct(v, 2),
  DTWEXBGS: (v) => fmt.num(v, 2), VIXCLS: (v) => fmt.num(v, 2), NASDAQCOM: (v) => fmt.num(v, 0),
  WM2NS: (v) => "US$ " + fmt.num(v / 1000, 2) + " tri",
};

function renderMacro(m) {
  setMeta("macro", m);
  const err = $("macro-error");
  err.hidden = !m.error;
  err.textContent = m.error || "";

  const tbody = $("macro-table");
  tbody.replaceChildren();
  for (const [id, s] of Object.entries(m.series || {})) {
    const tr = document.createElement("tr");
    const f = MACRO_FMT[id] || ((v) => fmt.num(v));
    const change = s.change == null ? "—" : (id === "WM2NS" ? fmt.signed(s.change, 0, " bi") : fmt.signed(s.change, 2));
    [[s.label, ""], [f(s.value), "num"], [change, "num"], [new Date(s.date + "T00:00:00").toLocaleDateString("pt-BR"), ""]]
      .forEach(([text, cls]) => { const td = document.createElement("td"); td.textContent = text; td.className = cls; tr.append(td); });
    tbody.append(tr);
  }
  setText("spread-2s10s", m.spread_2s10s == null ? "—" : fmt.signed(m.spread_2s10s * 100, 0, " bps"));

  const ul = $("releases");
  ul.replaceChildren();
  const releases = m.upcoming_releases || [];
  if (!releases.length) {
    const li = document.createElement("li");
    li.className = "muted";
    li.textContent = "Sem dados de calendário.";
    ul.append(li);
  }
  for (const r of releases) {
    const li = document.createElement("li");
    const t = document.createElement("time");
    t.dateTime = r.date;
    t.textContent = new Date(r.date + "T00:00:00").toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" });
    li.append(t, r.name);
    ul.append(li);
  }
}

async function loadSummary() {
  try {
    const s = await getJSON("/api/summary");
    renderPrice(s.price);
    renderPremium(s.coinbase_premium, s.sources);
    renderDerivatives(s.derivatives, s.sources);
    renderVolatility(s.volatility);
    renderSentiment(s.sentiment);
    renderMacro(s.macro);

    const statuses = Object.values(s.sources).map((x) => x.status);
    const overall = statuses.every((x) => x === "ok") ? "ok" : statuses.includes("erro") ? "erro" : "atrasado";
    const el = $("overall-status");
    el.dataset.status = overall;
    const bad = Object.entries(s.sources).filter(([, v]) => v.status !== "ok").map(([k, v]) => `${v.source}: ${v.status}`);
    el.textContent = overall === "ok" ? "todas as fontes ok" : `${bad.length} fonte(s) com problema`;
    el.title = bad.join("\n");
    setText("generated-at", fmt.dateTime(s.generated_at));
  } catch (e) {
    console.warn(e);
    const el = $("overall-status");
    el.dataset.status = "erro";
    el.textContent = "backend indisponível";
  }
}

// ---------- bootstrap -------------------------------------------------------------

function start() {
  initPriceChart();
  loadSummary();
  loadCandles(true);
  loadSeries();
  setInterval(loadSummary, REFRESH_SUMMARY_MS);
  setInterval(loadCandles, REFRESH_CANDLES_MS);
  setInterval(loadSeries, REFRESH_SERIES_MS);
}

if (window.LightweightCharts && window.Chart) start();
else window.addEventListener("load", start);
