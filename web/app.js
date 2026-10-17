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
const compact = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 2 });
const fmt = {
  usd: (v, d = 0) => (v == null ? "—" : "US$ " + nf(d).format(v)),
  compactUsd: (v) => (v == null ? "—" : "US$ " + compact.format(v)),
  signedCompactUsd: (v) => (v == null ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") + "US$ " + compact.format(Math.abs(v))),
  usdMi: (v) => (v == null ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") + "US$ " + nf(1).format(Math.abs(v)) + " mi"),
  num: (v, d = 2) => (v == null ? "—" : nf(d).format(v)),
  pct: (v, d = 2) => (v == null ? "—" : nf(d).format(v) + "%"),
  signed: (v, d = 2, suffix = "") => (v == null ? "—" : (v > 0 ? "+" : "") + nf(d).format(v) + suffix),
  time: (iso) => (iso ? new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }) : "—"),
  dateTime: (iso) => (iso ? new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—"),
  shortDate: (ts) => new Date(ts * 1000).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }),
  isoDay: (d) => (d ? new Date(d + "T00:00:00").toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" }) : "—"),
  monthYear: (ts) => new Date(ts * 1000).toLocaleDateString("pt-BR", { month: "short", year: "2-digit" }).replace(". de ", "/").replace(" de ", "/"),
};

const $ = (id) => document.getElementById(id);
const setText = (id, text) => { const el = $(id); if (el) el.textContent = text; };

function setDelta(id, value, text) {
  const el = $(id);
  el.textContent = text;
  el.classList.toggle("up", value > 0);
  el.classList.toggle("down", value < 0);
}

function badge(status, text) {
  const span = document.createElement("span");
  span.className = "badge";
  span.dataset.status = status || "pendente";
  span.textContent = text || status || "pendente";
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

/** Alerta de falha de coleta: avisa se o card está mostrando cache ou nada. */
function setAlert(id, section, hasData) {
  const el = $(id);
  el.hidden = !section.error;
  if (section.error) {
    const error = section.error.length > 200 ? section.error.slice(0, 200) + "…" : section.error;
    el.textContent = hasData ? `Mostrando o último dado bom. Falha na coleta mais recente: ${error}` : `Sem dados: ${error}`;
  }
}

/** Linha de tabela a partir de [texto, classe] — sempre via textContent. */
function row(cells, cls = "") {
  const tr = document.createElement("tr");
  if (cls) tr.className = cls;
  for (const [text, tdCls] of cells) {
    const td = document.createElement("td");
    if (text instanceof Node) td.append(text); else td.textContent = text;
    if (tdCls) td.className = tdCls;
    tr.append(td);
  }
  return tr;
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
      : " ";
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

function baseOptions({ yFormat, legend = false, xTicks = 6 } = {}) {
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
      x: { grid: { display: false }, border: { color: C.border }, ticks: { color: C.muted, maxTicksLimit: xTicks, maxRotation: 0, autoSkipPadding: 16 } },
      y: { grid: { color: C.grid }, border: { display: false }, ticks: { color: C.muted, maxTicksLimit: 5, callback: yFormat } },
    },
  };
}

function lineDataset(label, color, data) {
  return { label, data, borderColor: color, backgroundColor: color, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4, tension: 0.2 };
}

function barDataset(label, color, data) {
  return { label, data, backgroundColor: color, borderRadius: 4, borderSkipped: "start", barPercentage: 0.85, categoryPercentage: 0.85 };
}

function upsertChart(key, canvasId, config) {
  if (charts[key]) {
    charts[key].data = config.data;
    charts[key].update("none");
  } else {
    charts[key] = new Chart($(canvasId), config);
  }
}

function lineChart(key, canvasId, points, label, yFormat, xLabel = (ts) => fmt.shortDate(ts)) {
  upsertChart(key, canvasId, {
    type: "line",
    data: { labels: points.map((p) => xLabel(p.ts)), datasets: [lineDataset(label, C.s1, points.map((p) => p.value))] },
    options: baseOptions({ yFormat }),
  });
}

async function loadSeries() {
  const get = (metric, days) => getJSON(`/api/series/${metric}?days=${days}`).catch((e) => { console.warn(e); return { points: [] }; });
  const [dvol, fng, d10, d2, stables, mvrv, hashrate] = await Promise.all([
    get("deribit.dvol", 7), get("fng.value", 30), get("fred.DGS10", 365), get("fred.DGS2", 365),
    get("stables.total_usd", 365), get("cm.mvrv", 365), get("mempool.hashrate_ehs", 90),
  ]);

  const hourLabel = (ts) => `${fmt.shortDate(ts)} ${String(new Date(ts * 1000).getHours()).padStart(2, "0")}h`;
  lineChart("dvol", "dvol-chart", dvol.points, "DVOL", (v) => fmt.num(v, 1), hourLabel);

  upsertChart("fng", "fng-chart", {
    type: "bar",
    data: {
      labels: fng.points.map((p) => fmt.shortDate(p.ts)),
      datasets: [{ ...barDataset("Fear & Greed", C.s1, fng.points.map((p) => p.value)), borderSkipped: "bottom" }],
    },
    options: (() => { const o = baseOptions({ yFormat: (v) => fmt.num(v, 0) }); o.scales.y.min = 0; o.scales.y.max = 100; return o; })(),
  });

  lineChart("stables", "stables-chart", stables.points, "Oferta total", (v) => fmt.compactUsd(v), fmt.monthYear);
  lineChart("mvrv", "mvrv-chart", mvrv.points, "MVRV", (v) => fmt.num(v, 2), fmt.monthYear);
  lineChart("hashrate", "hashrate-chart", hashrate.points, "Hashrate (EH/s)", (v) => fmt.num(v, 0));

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
      labels: ts.map(fmt.monthYear),
      datasets: [
        lineDataset("10 anos", C.s1, ts.map((t) => byTs.get(t).d10 ?? null)),
        lineDataset("2 anos", C.s2, ts.map((t) => byTs.get(t).d2 ?? null)),
      ],
    },
    options: baseOptions({ yFormat: (v) => fmt.pct(v, 2), legend: true, xTicks: 5 }),
  });
}

// ---------- faixa de resumo: temperatura do mercado ------------------------------

const UNIT_FMT = {
  "%": (v) => fmt.signed(v, 2, "%"),
  pts: (v) => fmt.signed(v, 2, " pts"),
  "×": (v) => fmt.num(v, 2) + "×",
  "US$ mi": (v) => fmt.usdMi(v),
  "": (v) => fmt.num(v, 0),
};
const anchorFmt = (v, unit) => (unit === "US$ mi" ? nf(0).format(v) : nf(unit === "×" ? 1 : 0, 2).format(v));

function contributionBar(c) {
  const wrap = document.createElement("div");
  wrap.className = "contrib-row";
  const bar = document.createElement("div");
  bar.className = "contrib";
  bar.setAttribute("aria-hidden", "true");
  const fill = document.createElement("span");
  const w = Math.abs(c) * 50;
  fill.className = c >= 0 ? "hot" : "cold";
  fill.style.left = (c >= 0 ? 50 : 50 - w) + "%";
  fill.style.width = w + "%";
  bar.append(fill);
  const val = document.createElement("b");
  val.textContent = fmt.signed(c, 2);
  wrap.append(bar, val);
  return wrap;
}

function renderRegime(r) {
  const has = r.score != null;
  setText("regime-score", has ? fmt.signed(r.score, 0) : "—");
  setText("regime-label", r.label || "dados insuficientes");
  const marker = $("regime-marker");
  marker.hidden = !has;
  if (has) marker.style.left = (r.score + 100) / 2 + "%";
  setText("regime-coverage", `${r.available} de ${r.total} componentes disponíveis (mínimo ${r.min_components}). Pesos iguais.`);
  setText("regime-method", r.method);
  setText("regime-bands", r.bands.map((b, i) => {
    const lo = i ? r.bands[i - 1].up_to : null;
    const range = lo == null ? `até ${b.up_to}` : b.up_to == null ? `acima de ${lo}` : `${lo} a ${b.up_to}`;
    return `${b.label} (${range})`;
  }).join(" · "));

  const tbody = $("regime-table");
  tbody.replaceChildren();
  for (const c of r.components) {
    const name = document.createElement("div");
    name.textContent = c.label;
    name.title = c.why;
    const tag = document.createElement("span");
    tag.className = "group-tag";
    tag.textContent = c.group;
    name.append(tag);
    const a = c.anchors;
    tbody.append(row([
      [name, ""],
      [(UNIT_FMT[c.unit] || UNIT_FMT[""])(c.value), "num"],
      [`${anchorFmt(a.cold, c.unit)} · ${anchorFmt(a.neutral, c.unit)} · ${anchorFmt(a.hot, c.unit)}`, "num muted"],
      [contributionBar(c.contribution), ""],
    ]));
  }
  const miss = $("regime-missing");
  miss.hidden = !r.missing.length;
  miss.textContent = r.missing.length ? `Sem dados agora: ${r.missing.join(", ")}.` : "";
}

// ---------- mercado ------------------------------------------------------------

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

const FNG_PT = { "Extreme Fear": "Medo extremo", Fear: "Medo", Neutral: "Neutro", Greed: "Ganância", "Extreme Greed": "Ganância extrema" };

function renderSentiment(s) {
  setMeta("sentiment", s);
  setText("fng-value", s.value ?? "—");
  setText("fng-class", FNG_PT[s.classification] || s.classification || "—");
  if (s.value != null) $("fng-marker").style.left = s.value + "%";
}

function renderTrend(t) {
  if (!t) { setMeta("trend", { source: "Binance Spot (candles)", status: "pendente" }); return; }
  setMeta("trend", t);
  setText("trend-mayer", t.mayer_multiple == null ? "—" : fmt.num(t.mayer_multiple, 2) + "×");
  setText("trend-sma50", fmt.usd(t.sma_50d));
  setText("trend-sma200", fmt.usd(t.sma_200d));
  setText("trend-sma200w", fmt.usd(t.sma_200w));
  setText("trend-dist200w", fmt.signed(t.dist_200w_pct, 1, "%"));
  setText("trend-rv", `${fmt.pct(t.realized_vol_7d, 1)} / ${fmt.pct(t.realized_vol_30d, 1)}`);
}

// ---------- derivativos -------------------------------------------------------

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
    const src = sources[name];
    const b = badge(src?.status);
    if (src?.error) b.title = src.error;
    tbody.append(row([
      [EXCHANGE_LABEL[name], ""],
      [e ? fmt.signed(e.funding_rate_pct, 4, "%") : "—", "num"],
      [e ? fmt.compactUsd(e.oi_usd) : "—", "num"],
      [b, ""],
    ]));
  }
  const fc = d.funding_context;
  setText("funding-context", fc
    ? `Binance agora vs. últimos 30 dias: z = ${fmt.signed(fc.zscore, 2)} (média ${fmt.pct(fc.mean_30d_pct, 4)}). Média ponderada pelo OI; ciclo de 8h.`
    : "Média ponderada pelo open interest. Ciclo de 8h.");

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

function renderVolatility(v, trend) {
  setMeta("volatility", v);
  setText("dvol-value", fmt.num(v.dvol, 2));
  setDelta("dvol-change", v.dvol_change_24h, fmt.signed(v.dvol_change_24h, 2));
  setText("dvol-rv", trend?.realized_vol_30d != null
    ? `Realizada 30d: ${fmt.pct(trend.realized_vol_30d, 1)} · DVOL − realizada: ${fmt.signed(trend.iv_rv_spread, 1, " pts")}`
    : "—");
}

function renderBasis(b) {
  setMeta("basis", b);
  const h = b.headline;
  setText("basis-headline", h ? fmt.signed(h.annualized_pct, 2, "%") : "—");
  setText("basis-sub", h ? `Futuro ${fmt.isoDay(h.date)} (${Math.round(h.days)} dias) · bruto ${fmt.signed(h.basis_pct, 2, "%")}` : "—");
  const curve = b.curve || [];
  upsertChart("basis", "basis-chart", {
    type: "line",
    data: {
      labels: curve.map((c) => `${fmt.isoDay(c.date)} (${Math.round(c.days)}d)`),
      datasets: [{ ...lineDataset("Basis anualizado", C.s1, curve.map((c) => c.annualized_pct)), pointRadius: 3 }],
    },
    options: baseOptions({ yFormat: (v) => fmt.pct(v, 1), xTicks: 4 }),
  });
}

function renderOptions(o) {
  setMeta("options", o);
  setAlert("options-error", o, o.total_oi_usd != null);
  if (o.total_oi_usd == null) return;
  setText("opt-oi", `${fmt.compactUsd(o.total_oi_usd)} (${nf(0).format(o.total_oi_btc)} BTC)`);
  setText("opt-pcr-oi", fmt.num(o.put_call_oi, 2));
  setText("opt-pcr-vol", fmt.num(o.put_call_volume, 2));
  const m = o.next_major_expiry;
  setText("opt-major", m ? `${fmt.isoDay(m.date)} · ${Math.round(m.days)} d · ${fmt.pct(m.oi_share_pct, 0)} do OI` : "—");
  setText("opt-maxpain", m ? fmt.usd(m.max_pain) : "—");
  const ref = o.ref_30d;
  setText("opt-atm", ref ? `${fmt.pct(ref.atm_iv, 1)} (${fmt.isoDay(ref.date)})` : "—");
  setText("opt-skew", ref?.skew_25d != null ? fmt.signed(ref.skew_25d, 2, " pts") : "—");
  setText("opt-spot", `· spot ${fmt.usd(o.spot)}`);

  const strikes = o.oi_by_strike || [];
  const kLabel = (k) => (k >= 1000 ? nf(0, 1).format(k / 1000) + "k" : String(k));
  upsertChart("oiStrike", "oi-strike-chart", {
    type: "bar",
    data: {
      labels: strikes.map((s) => kLabel(s.strike)),
      datasets: [barDataset("Calls", C.s1, strikes.map((s) => s.calls)), barDataset("Puts", C.s2, strikes.map((s) => s.puts))],
    },
    options: (() => {
      const opt = baseOptions({ yFormat: (v) => nf(0).format(v) + " BTC", legend: true, xTicks: 12 });
      opt.plugins.tooltip.callbacks.title = (items) => `Strike ${fmt.usd(strikes[items[0].dataIndex].strike)}`;
      return opt;
    })(),
  });

  const withIv = (o.expiries || []).filter((e) => e.atm_iv != null);
  upsertChart("ivTerm", "iv-term-chart", {
    type: "line",
    data: {
      labels: withIv.map((e) => `${fmt.isoDay(e.date)} (${Math.round(e.days)}d)`),
      datasets: [{ ...lineDataset("IV no dinheiro", C.s1, withIv.map((e) => e.atm_iv)), pointRadius: 3 }],
    },
    options: baseOptions({ yFormat: (v) => fmt.pct(v, 1), xTicks: 5 }),
  });

  const tbody = $("opt-expiries");
  tbody.replaceChildren();
  for (const e of o.expiries || []) {
    tbody.append(row([
      [fmt.isoDay(e.date) + "/" + e.date.slice(2, 4), ""],
      [nf(0, 1).format(e.days), "num"],
      [fmt.compactUsd(e.oi_usd), "num"],
      [fmt.pct(e.oi_share_pct, 1), "num"],
      [fmt.num(e.put_call_oi, 2), "num"],
      [fmt.usd(e.max_pain), "num"],
      [e.atm_iv == null ? "—" : fmt.pct(e.atm_iv, 1), "num"],
      [e.skew_25d == null ? "—" : fmt.signed(e.skew_25d, 2), "num"],
    ]));
  }
}

// ---------- fluxos ---------------------------------------------------------------

function renderEtf(e) {
  setMeta("etf", e);
  const has = !!e.last_day;
  setAlert("etf-error", e, has);
  $("etf-body").hidden = !has;
  if (!has) return;
  setText("etf-last", fmt.usdMi(e.last_day.total));
  setText("etf-last-date", `fluxo líquido em ${fmt.isoDay(e.last_day.date)}` + (e.pending_dates?.length ? ` · ${fmt.isoDay(e.pending_dates[0])} pendente` : ""));
  setText("etf-5d", fmt.usdMi(e.sum_5d));
  setText("etf-10d", fmt.usdMi(e.sum_10d));
  setText("etf-cum", e.cumulative_total == null ? "—" : fmt.usdMi(e.cumulative_total));

  const days = e.days || [];
  upsertChart("etf", "etf-chart", {
    type: "bar",
    data: {
      labels: days.map((d) => fmt.isoDay(d.date)),
      datasets: [{ ...barDataset("Fluxo líquido", days.map((d) => (d.total >= 0 ? C.up : C.down)), days.map((d) => d.total)), barPercentage: 0.8, categoryPercentage: 0.9 }],
    },
    options: baseOptions({ yFormat: (v) => fmt.usdMi(v), xTicks: 8 }),
  });

  const tbody = $("etf-table");
  tbody.replaceChildren();
  const rows = [...(e.by_ticker || [])].sort((a, b) => Math.abs(b.cumulative ?? 0) - Math.abs(a.cumulative ?? 0));
  for (const t of rows) {
    tbody.append(row([
      [t.ticker, ""], [t.issuer, "muted"],
      [fmt.usdMi(t.last_day), "num"], [fmt.usdMi(t.sum_5d), "num"], [t.cumulative == null ? "—" : fmt.usdMi(t.cumulative), "num"],
    ]));
  }
}

function renderStablecoins(s) {
  setMeta("stablecoins", s);
  const t = s.total;
  if (!t) return;
  setText("stables-total", fmt.compactUsd(t.supply));
  setDelta("stables-7d", t.change_7d, `${fmt.signedCompactUsd(t.change_7d)} (${fmt.signed(t.change_7d_pct, 2, "%")})`);
  setDelta("stables-30d", t.change_30d, `${fmt.signedCompactUsd(t.change_30d)} (${fmt.signed(t.change_30d_pct, 2, "%")})`);

  const tbody = $("stables-table");
  tbody.replaceChildren();
  for (const [sym, a] of Object.entries(s.assets || {})) {
    const price = document.createElement("span");
    price.textContent = a.price == null ? "—" : "US$ " + nf(4).format(a.price);
    if (a.depeg) price.append(" ", badge("atrasado", "fora da paridade"));
    tbody.append(row([
      [sym, ""], [fmt.compactUsd(a.supply), "num"], [fmt.pct(s.share_pct?.[sym], 1), "num"],
      [fmt.signed(a.change_7d_pct, 2, "%"), "num"], [fmt.signed(a.change_30d_pct, 2, "%"), "num"], [price, "num"],
    ]));
  }
}

// ---------- on-chain -------------------------------------------------------------

function renderOnchain(o) {
  const n = o.network;
  setMeta("network", n);
  if (n.hashrate_ehs != null) {
    setText("net-hashrate", fmt.num(n.hashrate_ehs, 0));
    const da = n.difficulty_adjustment;
    setText("net-diff", `${fmt.signed(da.estimated_change_pct, 2, "%")} em ${nf(0).format(da.remaining_blocks)} blocos (~${fmt.shortDate(da.estimated_ts)})`);
    setText("net-fees", `${n.fees_sat_vb.fast} / ${n.fees_sat_vb.economy} sat/vB`);
    setText("net-mempool", `${nf(0).format(n.mempool_tx)} tx · ${fmt.num(n.mempool_vmb, 1)} vMB`);
    setText("net-halving", `${nf(0).format(n.halving.blocks_remaining)} blocos (~${fmt.monthYear(n.halving.estimated_ts)})`);
  }

  const v = o.valuation;
  setMeta("valuation", v);
  setMeta("activity", v);
  if (v.mvrv == null) return;
  setText("val-mvrv", fmt.num(v.mvrv, 2));
  setText("val-nupl", fmt.num(v.nupl, 2));
  setText("val-realized", fmt.compactUsd(v.realized_cap_usd));
  setText("val-mcap", fmt.compactUsd(v.market_cap_usd));
  setText("val-range", `${fmt.num(v.mvrv_1y_min, 2)} – ${fmt.num(v.mvrv_1y_max, 2)}`);
  setText("act-addr", fmt.num(v.active_addresses_7d, 0));
  setDelta("act-addr-trend", v.active_addresses_trend_pct, fmt.signed(v.active_addresses_trend_pct, 1, "%"));
  setText("act-tx", fmt.num(v.tx_count_7d, 0));
  setText("act-fees", v.fees_btc_7d == null ? "—" : fmt.num(v.fees_btc_7d, 2) + " BTC");
  setText("act-supply", v.supply_btc == null ? "—" : fmt.num(v.supply_btc / 1e6, 3) + " mi BTC");
  setText("act-date", fmt.isoDay(v.date));
}

// ---------- macro e notícias -------------------------------------------------------

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
    const f = MACRO_FMT[id] || ((v) => fmt.num(v));
    const change = s.change == null ? "—" : (id === "WM2NS" ? fmt.signed(s.change, 0, " bi") : fmt.signed(s.change, 2));
    tbody.append(row([[s.label, ""], [f(s.value), "num"], [change, "num"], [new Date(s.date + "T00:00:00").toLocaleDateString("pt-BR"), ""]]));
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
    if (r.name.includes("FOMC")) li.className = "fomc";
    const t = document.createElement("time");
    t.dateTime = r.date;
    t.textContent = fmt.isoDay(r.date);
    li.append(t, r.name + (r.sep ? " (com projeções)" : ""));
    ul.append(li);
  }
}

function renderNews(n) {
  setMeta("news", { ...n, source: "RSS" });
  const ul = $("news-list");
  ul.replaceChildren();
  for (const it of n.items || []) {
    if (!/^https?:\/\//.test(it.link)) continue;
    const li = document.createElement("li");
    const when = document.createElement("div");
    when.className = "when";
    when.textContent = new Date(it.published_ts * 1000).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
    const src = document.createElement("span");
    src.textContent = it.source;
    when.append(src);
    const a = document.createElement("a");
    a.href = it.link;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.textContent = it.title;
    li.append(when, a);
    ul.append(li);
  }
  const feeds = Object.entries(n.feeds || {}).map(([k, v]) => `${k} ${v === "ok" ? "●" : "✕"}`).join(" · ");
  setText("news-feeds", feeds ? `Feeds: ${feeds}. Filtro pelo título: BTC, ETFs e macro.` : "");
}

async function loadSummary() {
  try {
    const s = await getJSON("/api/summary");
    const parts = [
      () => renderRegime(s.regime),
      () => renderPrice(s.price),
      () => renderPremium(s.coinbase_premium, s.sources),
      () => renderSentiment(s.sentiment),
      () => renderTrend(s.trend),
      () => renderDerivatives(s.derivatives, s.sources),
      () => renderVolatility(s.volatility, s.trend),
      () => renderBasis(s.basis),
      () => renderOptions(s.options),
      () => renderEtf(s.etf),
      () => renderStablecoins(s.stablecoins),
      () => renderOnchain(s.onchain),
      () => renderMacro(s.macro),
      () => renderNews(s.news),
    ];
    // um card com dado inesperado não pode apagar os outros
    for (const render of parts) { try { render(); } catch (e) { console.warn(e); } }

    const statuses = Object.values(s.sources).map((x) => x.status);
    const overall = statuses.every((x) => x === "ok") ? "ok" : statuses.includes("erro") ? "erro" : "atrasado";
    const el = $("overall-status");
    el.dataset.status = overall;
    const bad = Object.entries(s.sources).filter(([, v]) => v.status !== "ok").map(([, v]) => `${v.source}: ${v.status}`);
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
