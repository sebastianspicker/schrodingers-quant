"use strict";

const SVG = "http://www.w3.org/2000/svg";
const PERIODS = { train: "Train", validation: "Validation", heldout: "Held-out" };
const COSTS = { base: "0.5 % per side", stress: "1.0 % per side" };
const EXITS = { exit_signal: "10-day low", force_exit: "period end", stop_loss: "−20 % stop" };
const K2_FACTOR = 0.6; // H1.md: held-out drawdown ≤ 0.6 × buy-and-hold's
const TRADE_SCALE = 70; // fixed ±% domain for the trade bars, so runs compare
const state = { period: "heldout", cost: "base" };
let data = null;

const eur = new Intl.NumberFormat("en-GB", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const minus = (s) => s.replace("-", "−");
const pct = (v, signed = true) => minus(`${signed && v > 0 ? "+" : ""}${v.toFixed(1)} %`);

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
  return run.trades.map((t) => [index.get(t.open.slice(0, 10)) ?? 0, index.get(t.close.slice(0, 10)) ?? run.dates.length - 1]);
}

function yearTicks(dates) {
  const out = [];
  dates.forEach((d, i) => {
    if (d.slice(5) === "01-01" || (i > 0 && d.slice(0, 4) !== dates[i - 1].slice(0, 4))) out.push([i, d.slice(0, 4)]);
  });
  if (out.length < 2) {
    dates.forEach((d, i) => {
      if (d.slice(8) === "01" && ["01", "04", "07", "10"].includes(d.slice(5, 7)) && !out.some(([j]) => j === i)) {
        out.push([i, d.slice(0, 7)]);
      }
    });
  }
  return out.sort((a, b) => a[0] - b[0]);
}

function hours(open, close) {
  return (Date.parse(`${close.replace(" ", "T")}:00Z`) - Date.parse(`${open.replace(" ", "T")}:00Z`)) / 36e5;
}

function drawChart(container, run, opts) {
  container.textContent = "";
  const width = container.clientWidth;
  const height = container.clientHeight;
  const narrow = width < 520;
  const m = {
    top: 14,
    right: narrow ? 60 : 72,
    bottom: opts.spans ? 38 : 24,
    left: narrow ? 52 : 64,
  };
  const w = width - m.left - m.right;
  const h = height - m.top - m.bottom;
  const plotW = w;
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}` }, container);
  const art = el("g", { "aria-hidden": "true" }, svg);
  const n = run.dates.length;
  const series = opts.series;
  const all = series.flatMap((s) => s.values);
  let lo = Math.min(...all, opts.floor ?? Infinity);
  let hi = Math.max(...all, opts.ceil ?? -Infinity);
  const ticks = niceTicks(lo, hi, narrow ? Math.max(3, opts.tickCount - 1) : opts.tickCount);
  lo = Math.min(lo, ticks[0]);
  hi = Math.max(hi, ticks[ticks.length - 1]);
  const x = (i) => m.left + (i / (n - 1)) * plotW;
  const y = (v) => m.top + (1 - (v - lo) / (hi - lo)) * h;

  const grid = el("g", { class: "grid" }, art);
  for (const t of ticks) {
    el("line", { x1: m.left, x2: m.left + w, y1: y(t), y2: y(t) }, grid);
    el("text", { class: "tick", x: m.left - 8, y: y(t) + 4, "text-anchor": "end" }, art).textContent = opts.format(t);
  }
  el("line", { class: "baseline", x1: m.left, x2: m.left + plotW, y1: y(opts.baseline), y2: y(opts.baseline) }, art);
  for (const [i, label] of yearTicks(run.dates)) {
    el("text", { class: "tick", x: x(i), y: height - 6, "text-anchor": "middle" }, art).textContent = label;
  }

  if (opts.spans) {
    const g = el("g", {}, art);
    const top = m.top + h + 6;
    el("rect", { class: "exposure-track", x: m.left, y: top, width: plotW, height: 5 }, g);
    for (const [a, b] of opts.spans) {
      el("rect", { class: "exposure", x: x(a), y: top, width: Math.max(1.5, x(b) - x(a)), height: 5 }, g);
    }
  }

  if (opts.limit !== undefined) {
    const ly = y(opts.limit);
    el("line", { class: "limit-line", x1: m.left, x2: m.left + plotW, y1: ly, y2: ly }, art);
    el("text", { class: "limit-label", x: m.left + 6, y: ly + 15 }, art).textContent =
      `K2 limit ${opts.format(opts.limit, true)}`;
  }

  for (const s of series) {
    const points = s.values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`);
    if (opts.area && s.key === "strategy") {
      el("path", { class: `area-${s.key}`, d: `M${x(0)},${y(opts.baseline)}L${points.join("L")}L${x(n - 1)},${y(opts.baseline)}Z` }, art);
    }
    el("path", { class: `line ${s.key}`, d: `M${points.join("L")}` }, art);
  }

  if (opts.endLabels) {
    const ends = series.map((s) => ({ s, y: y(s.values[n - 1]) })).sort((a, b) => a.y - b.y);
    if (ends.length === 2 && ends[1].y - ends[0].y < 16) {
      const mid = (ends[0].y + ends[1].y) / 2;
      ends[0].y = mid - 8;
      ends[1].y = mid + 8;
    }
    for (const e of ends) {
      el("circle", { class: `dot ${e.s.key}`, cx: x(n - 1), cy: y(e.s.values[n - 1]), r: 3.5 }, art);
      el("text", { class: `end-label ${e.s.key}`, x: x(n - 1) + 8, y: e.y + 4 }, art).textContent = opts.format(e.s.values[n - 1]);
    }
  }

  const cross = el("line", { class: "crosshair", y1: m.top, y2: m.top + h, visibility: "hidden" }, art);
  const dots = series.map((s) => el("circle", { class: `dot ${s.key}`, r: 4, visibility: "hidden" }, art));
  const hit = el("rect", { class: "hit", x: m.left, y: m.top, width: plotW, height: h, tabindex: "0", role: "img", "aria-label": opts.aria }, svg);
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
    return Math.round(((clientX - box.left - m.left) / plotW) * (n - 1));
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
    else if (e.key === "Escape") hide();
    else return;
    e.preventDefault();
  });
}

const tooltip = document.getElementById("tooltip");

function showTooltip(run, i, series, format, cx, cy) {
  tooltip.textContent = "";
  tooltip.appendChild(html("div", "date", run.dates[i]));
  for (const s of series) {
    const row = html("div", `row ${s.key}`);
    row.append(html("i", "swatch"), html("strong", "", format(s.values[i], true)), html("span", "", s.name));
    tooltip.appendChild(row);
  }
  const d = run.dates[i];
  const trade = run.trades.find((t) => t.open.slice(0, 10) <= d && d <= t.close.slice(0, 10));
  tooltip.appendChild(html("div", "flag", trade ? `H1 in a trade since ${trade.open.slice(0, 10)}` : "H1 in cash"));
  tooltip.hidden = false;
  const tw = tooltip.offsetWidth;
  const th = tooltip.offsetHeight;
  let left = cx + 16;
  if (left + tw > window.innerWidth - 8) left = cx - tw - 16;
  tooltip.style.left = `${Math.max(8, left)}px`;
  tooltip.style.top = `${Math.max(8, Math.min(cy - th / 2, window.innerHeight - th - 8))}px`;
}

function hideTooltip() {
  tooltip.hidden = true;
}

const mean = (xs) => xs.reduce((a, b) => a + b, 0) / xs.length;
const sd = (xs) => {
  const m = mean(xs);
  return Math.sqrt(xs.reduce((a, b) => a + (b - m) ** 2, 0) / (xs.length - 1));
};
const fixed = (v, digits = 2) => minus(v.toFixed(digits));

// Descriptive statistics from the daily series (last 4h close per UTC day).
function dailyStats(values) {
  const r = values.slice(1).map((v, i) => v / values[i] - 1);
  const m = mean(r);
  const s = sd(r);
  const downside = Math.sqrt(r.reduce((a, x) => a + Math.min(x, 0) ** 2, 0) / r.length);
  return { r, vol: s * Math.sqrt(365) * 100, sharpe: (m / s) * Math.sqrt(365), sortino: (m / downside) * Math.sqrt(365) };
}

function renderStats(run) {
  const root = document.getElementById("stats");
  const set = (field, text) => {
    root.querySelector(`[data-f="${field}"]`).textContent = text;
  };
  const h1 = dailyStats(run.strategy);
  const bh = dailyStats(run.buy_hold);
  const cov = mean(h1.r.map((x, i) => (x - mean(h1.r)) * (bh.r[i] - mean(bh.r)))) * (h1.r.length / (h1.r.length - 1));
  const beta = cov / sd(bh.r) ** 2;
  const corr = cov / (sd(h1.r) * sd(bh.r));

  set("ret", pct(run.net_return_pct));
  set("b-ret", pct(run.buy_hold_net_return_pct));
  set("cagr", pct(run.cagr_pct));
  set("b-cagr", pct(run.buy_hold_cagr_pct));
  set("dd", pct(run.mtm_max_drawdown_pct, false));
  set("b-dd", pct(run.buy_hold_max_drawdown_pct, false));
  set("vol", pct(h1.vol, false));
  set("b-vol", pct(bh.vol, false));
  set("sharpe", fixed(h1.sharpe));
  set("b-sharpe", fixed(bh.sharpe));
  set("sortino", fixed(h1.sortino));
  set("b-sortino", fixed(bh.sortino));
  set("calmar", fixed(run.net_return_pct / run.mtm_max_drawdown_pct));
  set("b-calmar", fixed(run.buy_hold_net_return_pct / run.buy_hold_max_drawdown_pct));
  set("beta", `${fixed(beta)}, ${fixed(corr)}`);

  const r = run.trades.map((t) => t.return_pct);
  const wins = r.filter((x) => x > 0);
  const losses = r.filter((x) => x <= 0);
  const sum = (xs) => xs.reduce((a, b) => a + b, 0);
  const top = [...r].sort((a, b) => b - a).slice(0, 2);
  const held = run.trades.map((t) => hours(t.open, t.close)).sort((a, b) => a - b);
  const median = held.length % 2 ? held[(held.length - 1) / 2] : (held[held.length / 2 - 1] + held[held.length / 2]) / 2;
  const t = mean(r) / (sd(r) / Math.sqrt(r.length));
  set("n", `${r.length}, ${wins.length}`);
  set("mean", `${pct(mean(r))} (${pct(sd(r), false)})`);
  set("t", fixed(t));
  set("pf", losses.length ? fixed(sum(wins) / -sum(losses)) : "n/a");
  set("wl", `${pct(mean(wins))} / ${losses.length ? pct(mean(losses)) : "n/a"}`);
  set("top2", minus(`${pct(sum(top)).replace(" %", " pp")} / ${pct(sum(r) - sum(top)).replace(" %", " pp")}`));
  set("hold", `${Math.round(median / 24)} d`);
  set("status", `t < 2: the mean trade is not distinguishable from zero at conventional levels (n = ${r.length}).`);
  document.getElementById("stats-range").textContent = `${run.dates[0]} → ${run.dates.at(-1)}`;
  root.classList.remove("is-loading");
}

function renderTrades(run) {
  document.getElementById("trades-title").textContent = `Trades (${run.trades.length})`;
  const body = document.getElementById("trades-body");
  body.textContent = "";
  run.trades.forEach((t, i) => {
    const tr = document.createElement("tr");
    const held = hours(t.open, t.close);
    const bar = html("div", "bar");
    const fill = html("i", t.return_pct < 0 ? "neg" : "");
    const share = (Math.min(Math.abs(t.return_pct), TRADE_SCALE) / TRADE_SCALE) * 50;
    fill.style.left = t.return_pct < 0 ? `${50 - share}%` : "50%";
    fill.style.width = `${Math.max(share, 0.4)}%`;
    bar.appendChild(fill);
    const barCell = html("td");
    barCell.setAttribute("aria-hidden", "true");
    barCell.appendChild(bar);
    const exit = html("td", "exit", EXITS[t.exit] ?? t.exit.replace(/_/g, " "));
    exit.title = t.exit;
    tr.append(
      html("td", "num", String(i + 1)),
      html("td", "", t.open),
      html("td", "", t.close),
      html("td", "num", held < 48 ? `${Math.round(held)} h` : `${Math.round(held / 24)} d`),
      html("td", `num${t.return_pct < 0 ? " neg" : ""}`, pct(t.return_pct)),
      barCell,
      exit,
    );
    body.appendChild(tr);
  });
}

function render() {
  if (!data) return;
  const run = data.runs[`${state.period}-${state.cost}`];
  syncControls();
  const heldout = state.period === "heldout";
  const first = run.dates[0];
  const last = run.dates[run.dates.length - 1];
  document.getElementById("equity-sub").textContent =
    `${PERIODS[state.period]}, ${first} → ${last}; H1 ${COSTS[state.cost]}, buy and hold 0.5 % per side; daily marks.`;
  renderStats(run);
  renderTrades(run);

  const equity = document.getElementById("equity-chart");
  drawChart(equity, run, {
    series: [
      { key: "strategy", name: "H1", values: run.strategy },
      { key: "hold", name: "Buy and hold", values: run.buy_hold },
    ],
    spans: positionSpans(run),
    baseline: 1000,
    tickCount: 5,
    endLabels: true,
    format: (v) => eur.format(v),
    aria: `Equity chart, ${PERIODS[state.period]} period: H1 ends at ${eur.format(run.strategy.at(-1))}, buy and hold at ${eur.format(run.buy_hold.at(-1))}. Use the arrow keys to read daily values.`,
  });

  const limit = heldout && state.cost === "base" ? -K2_FACTOR * run.buy_hold_max_drawdown_pct : undefined;
  document.getElementById("drawdown-sub").textContent =
    "Daily marks, so troughs can be shallower than the 4h figures in the table." +
    (limit === undefined ? "" : " Dashed: the K2 limit.");
  const drawdown = document.getElementById("drawdown-chart");
  drawChart(drawdown, run, {
    series: [
      { key: "strategy", name: "H1", values: drawdowns(run.strategy) },
      { key: "hold", name: "Buy and hold", values: drawdowns(run.buy_hold) },
    ],
    baseline: 0,
    ceil: 0,
    floor: limit,
    limit,
    tickCount: 3,
    area: true,
    format: (v, exact) => (Math.abs(v) < 0.05 ? "0 %" : minus(`${v.toFixed(exact ? 1 : 0)} %`)),
    aria: `Drawdown chart, ${PERIODS[state.period]} period. Use the arrow keys to read daily values.`,
  });
}

function syncControls() {
  for (const group of document.querySelectorAll(".choice")) {
    for (const b of group.querySelectorAll("button")) {
      const on = b.dataset.value === state[group.dataset.key];
      b.setAttribute("aria-checked", String(on));
      b.tabIndex = on ? 0 : -1;
    }
  }
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

function showError(message) {
  for (const id of ["equity-chart", "drawdown-chart"]) {
    const chart = document.getElementById(id);
    chart.textContent = "";
    chart.appendChild(html("p", "chart-state error", message));
  }
  document.querySelector(".trades").hidden = true;
  document.querySelector('#stats [data-f="status"]').textContent =
    "No run loaded. The decision table under Protocol comes from the experiment record and is unaffected.";
}

for (const group of document.querySelectorAll(".choice")) {
  const buttons = [...group.querySelectorAll("button")];
  buttons.forEach((b, i) => {
    b.addEventListener("click", () => {
      if (state[group.dataset.key] === b.dataset.value) return;
      state[group.dataset.key] = b.dataset.value;
      writeUrl();
      render();
    });
    b.addEventListener("keydown", (e) => {
      const d = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
      if (!d) return;
      const next = buttons[(i + d + buttons.length) % buttons.length];
      next.focus();
      next.click();
      e.preventDefault();
    });
  });
}

let resizeTimer;
let lastWidth = window.innerWidth;
window.addEventListener("resize", () => {
  if (window.innerWidth === lastWidth) return; // mobile URL-bar resizes change only the height
  lastWidth = window.innerWidth;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => render(), 120);
});

readUrl();
syncControls();
document.getElementById("stats").classList.add("is-loading");
fetch("data/equity-curves.json")
  .then((r) => {
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  })
  .then((json) => {
    data = json;
    render();
  })
  .catch((err) => showError(`Could not load data/equity-curves.json (${err.message}).`));
