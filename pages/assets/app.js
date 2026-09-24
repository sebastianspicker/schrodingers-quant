"use strict";

const SVG = "http://www.w3.org/2000/svg";
const PERIODS = { train: "Train", validation: "Validation", heldout: "Held-out" };
const COSTS = { base: "0.5 % per side", stress: "1.0 % per side" };
const state = { period: "heldout", cost: "base" };
let data = null;

const eur = new Intl.NumberFormat("en-GB", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const pct = (v, signed = true) => `${signed && v > 0 ? "+" : ""}${v.toFixed(1)} %`;
const dateLabel = (iso) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

function el(name, attrs = {}, parent) {
  const node = document.createElementNS(SVG, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

function html(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function niceTicks(min, max, count) {
  const span = max - min || 1;
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= count) || 10 * mag;
  const ticks = [];
  for (let t = Math.floor(min / step) * step; t <= max + step * 1e-9; t += step) ticks.push(+t.toFixed(10));
  return ticks;
}

function drawdowns(values) {
  let peak = -Infinity;
  return values.map((v) => {
    peak = Math.max(peak, v);
    return -((peak - v) / peak) * 100;
  });
}

function positionSpans(run) {
  const index = new Map(run.dates.map((d, i) => [d, i]));
  return run.trades.map((t) => {
    const open = t.open.slice(0, 10);
    const close = t.close.slice(0, 10);
    return [index.get(open) ?? 0, index.get(close) ?? run.dates.length - 1, t];
  });
}

function yearTicks(dates) {
  const out = [];
  let last = null;
  dates.forEach((d, i) => {
    const y = d.slice(0, 4);
    const m = d.slice(5, 7);
    if (y !== last && (m === "01" || i === 0)) {
      if (m === "01") out.push([i, y]);
      last = y;
    }
  });
  if (out.length < 2) {
    dates.forEach((d, i) => {
      if (d.slice(8) === "01" && ["01", "04", "07", "10"].includes(d.slice(5, 7))) {
        if (!out.some(([j]) => j === i)) out.push([i, d.slice(0, 7)]);
      }
    });
  }
  return out.sort((a, b) => a[0] - b[0]);
}

function drawChart(container, run, opts) {
  container.textContent = "";
  const width = container.clientWidth;
  const height = container.clientHeight;
  const narrow = width < 520;
  const m = { top: 16, right: opts.endLabels ? (narrow ? 52 : 64) : 12, bottom: 26, left: narrow ? opts.yWidth - 10 : opts.yWidth };
  const w = width - m.left - m.right;
  const h = height - m.top - m.bottom;
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true" }, container);
  const n = run.dates.length;
  const series = opts.series;
  const all = series.flatMap((s) => s.values);
  let lo = Math.min(...all, opts.floor ?? Infinity);
  let hi = Math.max(...all, opts.ceil ?? -Infinity);
  const ticks = niceTicks(lo, hi, opts.tickCount);
  lo = Math.min(lo, ticks[0]);
  hi = Math.max(hi, ticks[ticks.length - 1]);
  const x = (i) => m.left + (i / (n - 1)) * w;
  const y = (v) => m.top + (1 - (v - lo) / (hi - lo)) * h;

  if (opts.spans) {
    const g = el("g", {}, svg);
    for (const [a, b] of opts.spans) {
      el("rect", { class: "position", x: x(a), y: m.top, width: Math.max(1, x(b) - x(a)), height: h }, g);
    }
  }

  const grid = el("g", { class: "grid" }, svg);
  for (const t of ticks) {
    el("line", { x1: m.left, x2: m.left + w, y1: y(t), y2: y(t) }, grid);
    const label = el("text", { class: "tick", x: m.left - 8, y: y(t) + 4, "text-anchor": "end" }, svg);
    label.textContent = opts.format(t);
  }
  el("line", { class: "baseline", x1: m.left, x2: m.left + w, y1: y(opts.baseline), y2: y(opts.baseline) }, svg);
  for (const [i, label] of yearTicks(run.dates)) {
    const t = el("text", { class: "tick", x: x(i), y: height - 6, "text-anchor": "middle" }, svg);
    t.textContent = label;
  }

  for (const s of series) {
    const points = s.values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
    if (opts.area) {
      el("path", { class: `area-${s.key}`, d: `M${x(0)},${y(opts.baseline)}L${points.join("L")}L${x(n - 1)},${y(opts.baseline)}Z` }, svg);
    }
    el("path", { class: `line ${s.key}`, d: `M${points.join("L")}` }, svg);
  }

  if (opts.endLabels) {
    const ends = series.map((s) => ({ s, y: y(s.values[n - 1]) })).sort((a, b) => a.y - b.y);
    if (ends.length === 2 && ends[1].y - ends[0].y < 16) {
      const mid = (ends[0].y + ends[1].y) / 2;
      ends[0].y = mid - 8;
      ends[1].y = mid + 8;
    }
    for (const e of ends) {
      el("circle", { class: `dot ${e.s.key}`, cx: x(n - 1), cy: y(e.s.values[n - 1]), r: 4 }, svg);
      const t = el("text", { class: "end-label", x: x(n - 1) + 8, y: e.y + 4 }, svg);
      t.textContent = opts.format(e.s.values[n - 1]);
    }
  }

  const cross = el("line", { class: "crosshair", y1: m.top, y2: m.top + h, visibility: "hidden" }, svg);
  const dots = series.map((s) => el("circle", { class: `dot ${s.key}`, r: 4, visibility: "hidden" }, svg));
  const hit = el("rect", { class: "hit", x: m.left, y: m.top, width: w, height: h, tabindex: "0", role: "img", "aria-label": opts.aria }, svg);
  let focusIndex = n - 1;

  const show = (i, clientX, clientY) => {
    i = Math.max(0, Math.min(n - 1, i));
    focusIndex = i;
    cross.setAttribute("x1", x(i));
    cross.setAttribute("x2", x(i));
    cross.setAttribute("visibility", "visible");
    series.forEach((s, k) => {
      dots[k].setAttribute("cx", x(i));
      dots[k].setAttribute("cy", y(s.values[i]));
      dots[k].setAttribute("visibility", "visible");
    });
    const box = container.getBoundingClientRect();
    showTooltip(run, i, series, opts.format, clientX ?? box.left + x(i), clientY ?? box.top + m.top + 20);
  };
  const hide = () => {
    cross.setAttribute("visibility", "hidden");
    dots.forEach((d) => d.setAttribute("visibility", "hidden"));
    hideTooltip();
  };
  const indexAt = (clientX) => {
    const box = svg.getBoundingClientRect();
    return Math.round(((clientX - box.left - m.left) / w) * (n - 1));
  };
  hit.addEventListener("pointermove", (e) => show(indexAt(e.clientX), e.clientX, e.clientY));
  hit.addEventListener("pointerleave", hide);
  hit.addEventListener("focus", () => show(focusIndex));
  hit.addEventListener("blur", hide);
  hit.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 30 : 1;
    if (e.key === "ArrowRight") show(focusIndex + step);
    else if (e.key === "ArrowLeft") show(focusIndex - step);
    else if (e.key === "Home") show(0);
    else if (e.key === "End") show(n - 1);
    else return;
    e.preventDefault();
  });
}

const tooltip = document.getElementById("tooltip");

function showTooltip(run, i, series, format, cx, cy) {
  tooltip.textContent = "";
  tooltip.appendChild(html("div", "date", dateLabel(run.dates[i])));
  for (const s of series) {
    const row = html("div", "row");
    const key = html("i", "key");
    key.style.background = `var(--series-${s.key})`;
    row.append(key, html("strong", "", format(s.values[i], true)), html("span", "", s.name));
    tooltip.appendChild(row);
  }
  const d = run.dates[i];
  const trade = run.trades.find((t) => t.open.slice(0, 10) <= d && d <= t.close.slice(0, 10));
  if (trade) tooltip.appendChild(html("div", "flag", `In a trade since ${dateLabel(trade.open.slice(0, 10))}`));
  tooltip.hidden = false;
  const tw = tooltip.offsetWidth;
  const th = tooltip.offsetHeight;
  let left = cx + 14;
  if (left + tw > window.innerWidth - 8) left = cx - tw - 14;
  tooltip.style.left = `${Math.max(8, left)}px`;
  tooltip.style.top = `${Math.max(8, Math.min(cy - th / 2, window.innerHeight - th - 8))}px`;
}

function hideTooltip() {
  tooltip.hidden = true;
}

function renderTiles(run) {
  const tiles = document.getElementById("tiles");
  tiles.textContent = "";
  const winners = run.trades.filter((t) => t.return_pct > 0).length;
  const items = [
    ["Net return", pct(run.net_return_pct), `${pct(run.cagr_pct)} a year`],
    ["Max drawdown", pct(run.mtm_max_drawdown_pct, false), "marked to market, 4h closes"],
    ["Trades", String(run.trades.length), `${winners} winners, ${run.trades.length - winners} losers`],
    ["Buy and hold", pct(run.buy_hold_net_return_pct), `${pct(run.buy_hold_max_drawdown_pct, false)} max drawdown`],
  ];
  for (const [label, value, sub] of items) {
    const tile = html("div", "tile");
    tile.append(html("div", "label", label), html("div", "value", value), html("div", "sub", sub));
    tiles.appendChild(tile);
  }
}

function renderTrades(run) {
  document.getElementById("trades-title").textContent = `All ${run.trades.length} trades in this run`;
  const body = document.getElementById("trades-body");
  body.textContent = "";
  run.trades.forEach((t, i) => {
    const tr = document.createElement("tr");
    const ret = html("td", `num ${t.return_pct >= 0 ? "up" : "down"}`, pct(t.return_pct));
    tr.append(html("td", "", String(i + 1)), html("td", "", t.open), html("td", "", t.close), ret, html("td", "", t.exit.replace(/_/g, " ")));
    body.appendChild(tr);
  });
}

function render() {
  if (!data) return;
  const run = data.runs[`${state.period}-${state.cost}`];
  for (const seg of document.querySelectorAll(".seg")) {
    for (const b of seg.querySelectorAll("button")) {
      b.setAttribute("aria-checked", String(b.dataset.value === state[seg.dataset.key]));
    }
  }
  document.getElementById("equity-sub").textContent =
    `${PERIODS[state.period]} period, ${dateLabel(run.dates[0])} to ${dateLabel(run.dates[run.dates.length - 1])}. ` +
    `Costs ${COSTS[state.cost]} for H1; buy and hold always 0.5 %. Marked to market at each day's last 4h close.`;
  renderTiles(run);
  renderTrades(run);
  const spans = positionSpans(run);
  drawChart(document.getElementById("equity-chart"), run, {
    series: [
      { key: "strategy", name: "H1", values: run.strategy },
      { key: "hold", name: "Buy and hold", values: run.buy_hold },
    ],
    spans,
    baseline: 1000,
    tickCount: 5,
    yWidth: 62,
    endLabels: true,
    format: (v) => eur.format(v),
    aria: `Equity chart for the ${PERIODS[state.period]} period. Use the arrow keys to read daily values.`,
  });
  const ddStrategy = drawdowns(run.strategy);
  const ddHold = drawdowns(run.buy_hold);
  drawChart(document.getElementById("drawdown-chart"), run, {
    series: [
      { key: "strategy", name: "H1", values: ddStrategy },
      { key: "hold", name: "Buy and hold", values: ddHold },
    ],
    baseline: 0,
    ceil: 0,
    tickCount: 3,
    yWidth: 62,
    area: true,
    format: (v) => `${Math.abs(v) < 0.05 ? "0" : v.toFixed(0)} %`,
    aria: `Drawdown chart for the ${PERIODS[state.period]} period. Use the arrow keys to read daily values.`,
  });
}

function readUrl() {
  const params = new URLSearchParams(location.search);
  if (params.get("period") in PERIODS) state.period = params.get("period");
  if (params.get("cost") in COSTS) state.cost = params.get("cost");
  const theme = params.get("theme");
  if (theme === "dark" || theme === "light") document.documentElement.dataset.theme = theme;
}

function writeUrl() {
  const params = new URLSearchParams(location.search);
  params.set("period", state.period);
  params.set("cost", state.cost);
  history.replaceState(null, "", `?${params}${location.hash}`);
}

for (const seg of document.querySelectorAll(".seg")) {
  const buttons = [...seg.querySelectorAll("button")];
  buttons.forEach((b, i) => {
    b.addEventListener("click", () => {
      state[seg.dataset.key] = b.dataset.value;
      writeUrl();
      render();
    });
    b.addEventListener("keydown", (e) => {
      const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!d) return;
      const next = buttons[(i + d + buttons.length) % buttons.length];
      next.focus();
      next.click();
      e.preventDefault();
    });
  });
}

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(render, 120);
});

readUrl();
fetch("data/equity-curves.json")
  .then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  })
  .then((json) => {
    data = json;
    render();
  })
  .catch((err) => {
    document.getElementById("tiles").textContent = `Could not load the recorded data (${err.message}).`;
  });
