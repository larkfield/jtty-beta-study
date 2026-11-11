"""report.html: one self-contained page with the results, for sharing.

The page embeds analysis.json and draws its charts with a small inline SVG renderer (no network access needed):
crosshair and one tooltip listing every series on hover and keyboard focus, light and dark themes, legends carrying
the threshold values, and the full tables as the accessible view of every value. Sections: mode reference table,
channel, decode rate, contest QSO time and bits, copy, CER, throughput, SNR calibration, strong-signal failures,
false prints in noise, thresholds, all results, method and caveats.
"""

from __future__ import annotations

import csv
import html
import json
import math
from pathlib import Path
from typing import Any

from jtty_snr_study.analysis import CLASS_LABELS, DECODE_LEVELS, KEYBOARD_MODES, MODE_LABELS, unmeasured_text
from jtty_snr_study.channel import FADING_MODELS

_STYLE = """
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --ft8: #e34948; --ft4: #008300; --jtty: #2a78d6; --psk31: #8a3fd1; --rtty: #e08a00;
  --len-1: #86b6ef; --len-2: #5598e7; --len-3: #2a78d6; --len-4: #1c5cab; --len-5: #104281;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --ft8: #e34948; --ft4: #008300; --jtty: #3987e5; --psk31: #b06fe0; --rtty: #c98500;
    --len-1: #184f95; --len-2: #256abf; --len-3: #3987e5; --len-4: #6da7ec; --len-5: #9ec5f4;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --ft8: #e34948; --ft4: #008300; --jtty: #3987e5; --psk31: #b06fe0; --rtty: #c98500;
  --len-1: #184f95; --len-2: #256abf; --len-3: #3987e5; --len-4: #6da7ec; --len-5: #9ec5f4;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 28px; margin: 0 0 4px; font-weight: 650; }
h2 { font-size: 19px; margin: 44px 0 6px; font-weight: 600; }
h3 { font-size: 15px; margin: 22px 0 6px; font-weight: 600; }
p, li { color: var(--ink-2); max-width: 78ch; }
.lede { font-size: 16px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin: 24px 0 8px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
.tile .label { color: var(--ink-2); font-size: 13px; display: flex; align-items: center; gap: 8px; }
.tile .value { font-size: 28px; font-weight: 600; margin-top: 2px; }
.tile .note { color: var(--muted); font-size: 12.5px; }
.key { display: inline-block; width: 14px; height: 3px; border-radius: 2px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 16px 16px 8px;
  margin-top: 12px; }
.legend { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 13px; color: var(--ink); margin-bottom: 4px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.chart { position: relative; outline: none; }
.chart:focus-visible { box-shadow: 0 0 0 2px var(--jtty); border-radius: 6px; }
.chart svg { display: block; width: 100%; height: auto; overflow: visible; }
.tip { position: absolute; pointer-events: none; background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 8px 10px; font-size: 12.5px; box-shadow: 0 4px 16px rgba(0,0,0,0.12);
  min-width: 170px; display: none; z-index: 2; }
.tip .head { color: var(--ink-2); margin-bottom: 4px; }
.tip .row { display: flex; align-items: center; gap: 8px; white-space: nowrap; }
.tip .row b { font-variant-numeric: tabular-nums; color: var(--ink); }
.tip .row span.name { color: var(--ink-2); }
.table-wrap { overflow-x: auto; margin-top: 8px; }
table { border-collapse: collapse; font-size: 13px; width: 100%; }
th, td { padding: 6px 10px; text-align: right; border-bottom: 1px solid var(--grid); white-space: nowrap;
  font-variant-numeric: tabular-nums; }
th { color: var(--ink-2); font-weight: 600; }
td:first-child, th:first-child { text-align: left; }
td.text, th.text { text-align: left; white-space: normal; }
details { margin-top: 10px; }
summary { cursor: pointer; color: var(--ink); font-weight: 600; font-size: 14px; }
code { font-size: 13px; }
.callout { background: var(--surface); border: 1px solid var(--border); border-left: 3px solid var(--jtty);
  border-radius: 8px; padding: 10px 14px; margin: 12px 0; }
.theme { position: absolute; top: 20px; right: 16px; }
.theme button { background: var(--surface); color: var(--ink-2); border: 1px solid var(--border); border-radius: 8px;
  padding: 4px 10px; font: inherit; font-size: 13px; cursor: pointer; }
header { position: relative; }
"""

_SCRIPT = r"""
(function () {
  "use strict";
  const data = JSON.parse(document.getElementById("analysis").textContent);
  const SVGNS = "http://www.w3.org/2000/svg";
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const fmtPct = (v) => (v == null ? "\u2013" : (100 * v).toFixed(1) + "%");
  const fmtDb = (v) => (v == null ? "\u2013" : (v > 0 ? "+" : v < 0 ? "\u2212" : "") + Math.abs(v).toFixed(1) + " dB");
  const SNR = "SNR in 2500 Hz (dB, true)";
  const SNR_RANGE = [-21, 10];  // Matches plots.SNR_AXIS_RANGE_DB.
  const inRange = (x) => x >= SNR_RANGE[0] && x <= SNR_RANGE[1];
  const QSO_TIME_AXIS_S = [0, 120];  // Matches plots.QSO_TIME_AXIS_S.
  const CER_AXIS = [0, 0.5];  // Matches plots.CER_AXIS_PERCENT.
  // Every series wears its mode's color, whatever chart it is on.
  const seriesColor = (s) => "--" + s.mode;

  // Overs and macros never share a chart; FT8 and FT4 have no macros, so they appear on both.
  const onChart = (mode, isMacro, macros) => mode === "ft8" || mode === "ft4" || isMacro === macros;

  function qsoChart(contest, macros) {
    const curves = ((data.contest_qso || {}).curves || []).filter((c) => c.contest === contest && c.points.length
      && onChart(c.mode, c.variant.endsWith("-macro"), macros));
    return {
      series: curves.map((c) => ({ label: c.label + " (best " + c.best_s.toFixed(0) + " s)", color: seriesColor(c),
        pts: c.points.map((p) => ({ x: p.snr_db, y: p.median_s })) })),
      y: "num", yRange: QSO_TIME_AXIS_S, xLabel: SNR,
      yLabel: "Seconds to complete", fmt: (p) => p.y.toFixed(0) + " s" };
  }

  // Copied characters per minute of airtime for the throughput series that ``keep`` selects.
  function throughputChart(keep) {
    return {
      series: (data.throughput || []).filter((s) => keep(s) && s.points.some((p) => p.throughput_cpm != null))
        .map((s) => ({ label: s.label, color: seriesColor(s),
          pts: s.points.filter((p) => p.throughput_cpm != null).map((p) => ({ x: p.snr_db, y: p.throughput_cpm })) })),
      y: "num", xLabel: SNR, yLabel: "Copied characters per minute of airtime",
      fmt: (p) => p.y.toFixed(0) + " chars/min" };
  }

  function copyChart(macros) {
    return {
      series: data.copy_contest.filter((s) => onChart(s.mode, s.key.endsWith("-contest-macro"), macros))
        .map((s) => ({ label: s.label, color: seriesColor(s),
          pts: s.points.map((p) => ({ x: p.snr_db, y: p.copy, lo: p.copy_lo, hi: p.copy_hi })) })),
      y: "pct", xLabel: SNR, yLabel: "Copied exactly" };
  }

  const charts = {
    decode: () => ({
      series: data.headline.map((s) => {
        const mark = s.threshold && s.threshold.headline_db != null ? s.threshold.headline_db : null;
        return { label: s.label + (mark != null ? " \u00b7 " + data.headline_level + "% at " + fmtDb(mark) : ""),
          color: "--" + s.mode, mark,
          pts: s.points.map((p) => ({ x: p.snr_db, y: p.decode, lo: p.decode_lo, hi: p.decode_hi })) };
      }),
      y: "pct", xLabel: SNR, yLabel: "5-character groups decoded", ref: data.headline_level / 100 }),
    "copy-overs": () => copyChart(false),
    "copy-macros": () => copyChart(true),
    length: () => ({
      series: data.copy_by_frames.map((s, i) => ({ label: s.label.replace("JTTY ", ""), color: "--len-" + (i + 1),
        pts: s.points.map((p) => ({ x: p.snr_db, y: p.copy, lo: p.copy_lo, hi: p.copy_hi })) })),
      y: "pct", xLabel: SNR, yLabel: "Copied exactly" }),
    cer: () => {
      const series = data.cer.map((s) => ({ label: s.label, color: "--" + s.mode,
        pts: s.points.map((p) => ({ x: p.snr_db, y: p.cer_mean })) }));
      if (data.cer.some((s) => s.mode === "rtty")) {
        series.push({ label: "RTTY theory", color: "--muted", dashed: true, noMarkers: true,
          pts: data.cer_theory_rtty.map((t) => ({ x: t.snr_db, y: t.cer })) });
      }
      if (data.cer.some((s) => s.mode === "psk31") && data.cer_theory_psk31) {
        series.push({ label: "PSK31 theory", color: "--muted", dotted: true, noMarkers: true,
          pts: data.cer_theory_psk31.map((t) => ({ x: t.snr_db, y: t.cer })) });
      }
      return { series, y: "pct", yRange: CER_AXIS, xLabel: SNR, yLabel: "Character error rate",
        fmt: (p) => (100 * p.y).toFixed(p.y < 0.01 ? 2 : 1) + "%" };
    },
    "throughput-contest": () => throughputChart((s) => s.key.endsWith("-contest") || s.key.endsWith("-qso")),
    "throughput-macros": () => throughputChart((s) => s.key.endsWith("-contest-macro") || s.key.endsWith("-qso")),
    "throughput-text": () => throughputChart((s) => s.key.endsWith("-text")),
    calibration: () => ({
      series: data.calibration.map((s) => ({ label: s.label, color: "--" + s.mode,
        pts: s.points.filter((p) => p.snr_reported_mean != null).map((p) => ({ x: p.snr_db, y: p.snr_reported_mean,
          lo: p.snr_reported_mean - (p.snr_reported_sd || 0), hi: p.snr_reported_mean + (p.snr_reported_sd || 0),
          meas: p.snr_true_measured_mean })) })).filter((s) => s.pts.length),
      y: "num", xLabel: SNR, yLabel: "Reported SNR (dB)", diagonal: true,
      fmt: (p) => fmtDb(p.y) + (p.meas != null ? " (true " + fmtDb(p.meas) + ")" : "") }),
  };

  function spec(name) {
    if (name.startsWith("qso:")) {
      const [, contest, kind] = name.split(":");
      return qsoChart(contest, kind === "macros");
    }
    return charts[name]();
  }

  let clipCount = 0;

  function el(name, attrs, parent) {
    const node = document.createElementNS(SVGNS, name);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    if (parent) parent.appendChild(node);
    return node;
  }

  function niceTicks(lo, hi, count) {
    const step = Math.pow(10, Math.floor(Math.log10((hi - lo) / count)));
    const err = (hi - lo) / count / step;
    const mult = err >= 7.5 ? 10 : err >= 3.5 ? 5 : err >= 1.5 ? 2 : 1;
    const s = step * mult, out = [];
    for (let v = Math.ceil(lo / s) * s; v <= hi + 1e-9; v += s) out.push(+v.toFixed(6));
    return out;
  }

  function render(host) {
    const sp = spec(host.dataset.chart);
    host.replaceChildren();
    const legend = document.createElement("div");
    legend.className = "legend";
    for (const s of sp.series) {
      const item = document.createElement("span");
      const key = document.createElement("i");
      key.className = "key";
      key.style.background = css(s.color);
      item.append(key, document.createTextNode(s.label));
      legend.appendChild(item);
    }
    host.appendChild(legend);
    if (!sp.series.length) {
      const empty = document.createElement("p");
      empty.textContent = "No data for this chart in this run.";
      host.appendChild(empty);
      return;
    }

    const W = Math.max(320, host.clientWidth), H = Math.round(Math.min(380, Math.max(260, W * 0.5)));
    const m = { l: 60, r: 16, t: 10, b: 44 };
    sp.series = sp.series.map((s) => ({ ...s, pts: s.pts.filter((p) => inRange(p.x)) }));
    const all = sp.series.flatMap((s) => s.pts);
    const xs = all.map((p) => p.x);
    const x0 = SNR_RANGE[0], x1 = SNR_RANGE[1];
    let y0, y1;
    if (sp.yRange) { [y0, y1] = sp.yRange; }
    else if (sp.y === "pct") { y0 = 0; y1 = 1; }
    else {
      const ys = all.flatMap((p) => [p.lo ?? p.y, p.hi ?? p.y]).concat(sp.diagonal ? xs : []);
      const lo = Math.min(...ys), hi = Math.max(...ys), pad = Math.max(1, 0.06 * (hi - lo));
      y0 = sp.diagonal ? Math.floor(lo - pad) : Math.min(0, Math.floor(lo - pad)); y1 = Math.ceil(hi + pad);
    }
    const sx = (x) => m.l + ((x - x0) / Math.max(x1 - x0, 1e-9)) * (W - m.l - m.r);
    const sy = (y) => H - m.b - ((y - y0) / (y1 - y0)) * (H - m.t - m.b);

    const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, role: "img",
      "aria-label": host.dataset.title + ". Values are listed in the tables below." }, host);
    const grid = el("g", {}, svg);
    // A chart with a fixed y range keeps every point and clips what runs off it, with room for a whole marker.
    let plot = svg;
    if (sp.yRange) {
      const clipId = "plot-clip-" + (++clipCount);
      const clip = el("clipPath", { id: clipId }, svg);
      el("rect", { x: m.l - 5, y: m.t - 5, width: W - m.l - m.r + 10, height: H - m.t - m.b + 10 }, clip);
      plot = el("g", { "clip-path": `url(#${clipId})` }, svg);
    }
    let yTicks;
    if (sp.y === "pct" && !sp.yRange) yTicks = [0, 0.25, 0.5, 0.75, 1];
    else yTicks = niceTicks(y0, y1, 6);
    for (const t of yTicks) {
      const yy = sy(t);
      el("line", { x1: m.l, x2: W - m.r, y1: yy, y2: yy, stroke: css("--grid"), "stroke-width": 1 }, grid);
      const label = el("text",
        { x: m.l - 8, y: yy + 4, "text-anchor": "end", "font-size": 11, fill: css("--ink-2") }, grid);
      label.textContent = sp.y === "pct" ? Math.round(t * 100) + "%" : t;
    }
    for (const t of niceTicks(x0, x1, Math.max(4, Math.floor(W / 90)))) {
      const label = el("text",
        { x: sx(t), y: H - m.b + 18, "text-anchor": "middle", "font-size": 11, fill: css("--ink-2") }, grid);
      label.textContent = t;
    }
    el("line", { x1: m.l, x2: W - m.r, y1: H - m.b, y2: H - m.b, stroke: css("--axis") }, grid);
    const xl = el("text",
      { x: (m.l + W - m.r) / 2, y: H - 6, "text-anchor": "middle", "font-size": 12, fill: css("--ink-2") }, grid);
    xl.textContent = sp.xLabel;
    const yl = el("text", { x: 14, y: (m.t + H - m.b) / 2, "text-anchor": "middle", "font-size": 12,
      fill: css("--ink-2"), transform: `rotate(-90 14 ${(m.t + H - m.b) / 2})` }, grid);
    yl.textContent = sp.yLabel;
    if (sp.ref != null) {
      el("line", { x1: m.l, x2: W - m.r, y1: sy(sp.ref), y2: sy(sp.ref), stroke: css("--axis"),
        "stroke-dasharray": "4 3" }, grid);
    }
    if (sp.diagonal) {
      const a = Math.max(x0, y0), b = Math.min(x1, y1);
      el("line", { x1: sx(a), y1: sy(a), x2: sx(b), y2: sy(b), stroke: css("--muted"),
        "stroke-dasharray": "4 3" }, grid);
    }

    for (const s of sp.series) {
      const color = css(s.color);
      const pts = s.pts.slice().sort((a, b) => a.x - b.x);
      if (pts.length && pts[0].lo != null) {
        const top = pts.map((p) => `${sx(p.x)},${sy(p.hi)}`);
        const bottom = pts.slice().reverse().map((p) => `${sx(p.x)},${sy(p.lo)}`);
        el("polygon", { points: top.concat(bottom).join(" "), fill: color, "fill-opacity": 0.1 }, plot);
      }
      el("polyline", { points: pts.map((p) => `${sx(p.x)},${sy(p.y)}`).join(" "), fill: "none", stroke: color,
        "stroke-width": s.dashed || s.dotted ? 1.5 : 2, "stroke-linejoin": "round", "stroke-linecap": "round",
        "stroke-dasharray": s.dotted ? "0.5 4" : s.dashed ? "5 4" : "none" }, plot);
      if (!s.noMarkers) {
        for (const p of pts) {
          el("circle", { cx: sx(p.x), cy: sy(p.y), r: 4, fill: color, stroke: css("--surface"),
            "stroke-width": 1.5 }, plot);
        }
      }
      if (s.mark != null && inRange(s.mark)) {
        const mx = sx(s.mark), my = sy(sp.ref);
        el("rect", { x: mx - 5, y: my - 5, width: 10, height: 10, transform: `rotate(45 ${mx} ${my})`, fill: color,
          stroke: css("--ink"), "stroke-width": 1 }, svg);
      }
    }

    const cross = el("line",
      { y1: m.t, y2: H - m.b, stroke: css("--muted"), "stroke-width": 1, visibility: "hidden" }, svg);
    const tip = document.createElement("div");
    tip.className = "tip";
    host.appendChild(tip);
    const gridX = [...new Set(all.map((p) => p.x))].sort((a, b) => a - b);
    let index = -1;

    function show(i) {
      index = Math.max(0, Math.min(gridX.length - 1, i));
      const x = gridX[index];
      cross.setAttribute("x1", sx(x)); cross.setAttribute("x2", sx(x)); cross.setAttribute("visibility", "visible");
      tip.replaceChildren();
      const head = document.createElement("div");
      head.className = "head";
      head.textContent = "SNR " + fmtDb(x);
      tip.appendChild(head);
      for (const s of sp.series) {
        const p = s.pts.find((q) => Math.abs(q.x - x) < 1e-6);
        if (!p) continue;
        const row = document.createElement("div");
        row.className = "row";
        const key = document.createElement("i");
        key.className = "key"; key.style.background = css(s.color);
        const value = document.createElement("b");
        if (sp.fmt) value.textContent = sp.fmt(p);
        else if (sp.y === "pct") {
          value.textContent = fmtPct(p.y)
            + (p.lo != null ? "  (" + fmtPct(p.lo) + "\u2013" + fmtPct(p.hi) + ")" : "");
        } else value.textContent = String(p.y);
        const name = document.createElement("span");
        name.className = "name"; name.textContent = s.label;
        row.append(key, value, name);
        tip.appendChild(row);
      }
      tip.style.display = "block";
      const px = (sx(x) / W) * host.clientWidth;
      const left = px + 14 + tip.offsetWidth > host.clientWidth ? px - 14 - tip.offsetWidth : px + 14;
      tip.style.left = Math.max(0, left) + "px";
      tip.style.top = legend.offsetHeight + 8 + "px";
    }
    function hide() { cross.setAttribute("visibility", "hidden"); tip.style.display = "none"; }
    svg.addEventListener("pointermove", (event) => {
      const box = svg.getBoundingClientRect();
      const x = x0 + (((event.clientX - box.left) / box.width) * W - m.l) / (W - m.l - m.r) * (x1 - x0);
      let best = 0;
      for (let i = 1; i < gridX.length; i++) if (Math.abs(gridX[i] - x) < Math.abs(gridX[best] - x)) best = i;
      show(best);
    });
    svg.addEventListener("pointerleave", hide);
    host.onfocus = () => show(index < 0 ? Math.floor(gridX.length / 2) : index);
    host.onblur = hide;
    host.onkeydown = (event) => {
      if (event.key === "ArrowRight") { show(index + 1); event.preventDefault(); }
      if (event.key === "ArrowLeft") { show(index - 1); event.preventDefault(); }
      if (event.key === "Escape") hide();
    };
  }

  const hosts = [...document.querySelectorAll(".chart")];
  const drawAll = () => hosts.forEach(render);
  let pending = null;
  window.addEventListener("resize", () => { clearTimeout(pending); pending = setTimeout(drawAll, 120); });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", drawAll);
  const button = document.getElementById("theme");
  const modes = ["auto", "light", "dark"];
  let theme = "auto";
  try { theme = localStorage.getItem("jtty-report-theme") || "auto"; } catch (e) { theme = "auto"; }
  const apply = () => {
    if (theme === "auto") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", theme);
    button.textContent = "Theme: " + theme;
    drawAll();
  };
  button.addEventListener("click", () => {
    theme = modes[(modes.indexOf(theme) + 1) % modes.length];
    try { localStorage.setItem("jtty-report-theme", theme); } catch (e) { /* storage unavailable */ }
    apply();
  });
  apply();
})();
"""

_CHART_TITLES = {
    "decode": "5-character decode rate vs SNR",
    "copy-overs": "Contest overs copied exactly vs SNR",
    "copy-macros": "Contest macros copied exactly vs SNR",
    "length": "JTTY message copied exactly by length vs SNR",
    "cer": "Character error rate of free text vs SNR",
    "throughput-contest": "Copied characters per minute of airtime vs SNR, contest overs",
    "throughput-macros": "Copied characters per minute of airtime vs SNR, contest macros",
    "throughput-text": "Copied characters per minute of airtime vs SNR, ragchew text",
    "calibration": "Reported SNR vs true SNR",
    "qso:ru:overs": "Time to complete an ARRL RTTY Roundup QSO vs SNR, overs",
    "qso:ru:macros": "Time to complete an ARRL RTTY Roundup QSO vs SNR, macros",
    "qso:serial:overs": "Time to complete a serial-number contest QSO vs SNR, overs",
    "qso:serial:macros": "Time to complete a serial-number contest QSO vs SNR, macros",
}

MINUS = "\N{MINUS SIGN}"
DASH = "\N{EN DASH}"


def _num(value: Any, digits: int = 1, signed: bool = True) -> str:
    """Format a number for the page (an en dash when missing, a true minus sign for negatives)."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return DASH
    text = f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"
    return text.replace("-", MINUS)


def _p_value(value: Any) -> str:
    """Format a p-value, in scientific notation when it is small."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return DASH
    return f"{value:.1e}" if value < 1e-3 else f"{value:.3f}"


def _esc(value: Any) -> str:
    """Escape a value for HTML text."""
    return html.escape(str(value))


def _chart(key: str) -> str:
    """Return the placeholder card for one chart."""
    return (
        f'<div class="card"><div class="chart" tabindex="0" data-chart="{_esc(key)}" '
        f'data-title="{_esc(_CHART_TITLES.get(key, key))}"></div></div>'
    )


def _table(headers: list[str], rows: list[list[str]], text_columns: tuple[int, ...] = ()) -> str:
    """Return an HTML table (cells are escaped; ``text_columns`` are left-aligned and may wrap)."""

    def cell(tag: str, index: int, value: str) -> str:
        cls = ' class="text"' if index in text_columns else ""
        return f"<{tag}{cls}>{_esc(value)}</{tag}>"

    head = "".join(cell("th", i, h) for i, h in enumerate(headers))
    body = "".join("<tr>" + "".join(cell("td", i, c) for i, c in enumerate(row)) + "</tr>" for row in rows)
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _tiles(analysis: dict[str, Any]) -> str:
    """Return the headline stat tiles: each mode's headline 5-character decode point."""
    tiles = []
    level = analysis["headline_level"]
    for series in analysis["headline"]:
        threshold = series.get("threshold") or {}
        mu = threshold.get("headline_db")
        low, high = threshold.get("headline_lo"), threshold.get("headline_hi")
        interval = f"95% CI {_num(low)} to {_num(high)} dB" if low is not None else "interval not available"
        value = f"{_num(mu)} dB" if mu is not None else "not reached"
        tiles.append(
            f'<div class="tile"><div class="label"><i class="key" style="background:var(--{_esc(series["mode"])})">'
            f"</i>{_esc(series['label'])} {level}% 5-character decode</div>"
            f'<div class="value">{value}</div>'
            f'<div class="note">{_esc(interval)} · Eb/N0 {_num(threshold.get("ebn0_headline_db"), signed=False)} dB'
            "</div></div>"
        )
    return "".join(tiles)


def _modes_section(analysis: dict[str, Any]) -> str:
    """Return the mode reference table, one column per mode."""
    table = analysis.get("mode_table", [])
    if not table:
        return "<p>No mode table.</p>"

    def measured(m: dict[str, Any]) -> str:
        value = m.get("measured_bw99_hz")
        return f"{m['nominal_bandwidth_hz']:.0f} / " + (_num(value, 0, signed=False) if value is not None else DASH)

    def smoothing(m: dict[str, Any]) -> str:
        # Analyses made before this row existed have no key, which is unknown rather than "None".
        if "gaussian_bt" not in m:
            return DASH
        return "None" if m["gaussian_bt"] is None else f"BT {m['gaussian_bt']:.1f}"

    parameters: list[tuple[str, Any]] = [
        ("Modulation", lambda m: m["modulation"]),
        ("Gaussian smoothing", smoothing),
        ("Tones", lambda m: str(m["tones"])),
        ("Tone spacing (Hz)", lambda m: DASH if m["tone_spacing_hz"] is None else f"{m['tone_spacing_hz']:.2f}"),
        ("Keying rate (baud)", lambda m: f"{m['baud']:.2f}"),
        ("Bits per symbol", lambda m: f"{m['bits_per_symbol']:g}"),
        ("Symbols", lambda m: m["symbols"]),
        ("Frame / transmission", lambda m: f"{m['frame_s']:.3f} s"),
        ("T/R slot", lambda m: f"{m['slot_s']:.1f} s" if m.get("slot_s") else "none (asynchronous)"),
        ("Information bits per frame", lambda m: f"{m['info_bits']:g}"),
        ("CRC bits", lambda m: str(m["crc_bits"])),
        ("FEC", lambda m: m["fec"]),
        ("Gross bit rate (bit/s)", lambda m: f"{m['gross_bit_rate']:.1f}"),
        ("Net bit rate (bit/s)", lambda m: f"{m['net_bit_rate']:.1f}"),
        ("Bandwidth (Hz): nominal / measured 99%", measured),
        ("Synchronization", lambda m: m["synchronization"]),
        ("Notes", lambda m: m["notes"]),
    ]
    rows = [[name, *(value(m) for m in table)] for name, value in parameters]
    note = (
        "<p>A frame is one JTTY frame, one FT8/FT4 transmission, one RTTY character, or one PSK31 character of "
        "average length for lower-case text. Gross bit rate counts every channel bit while transmitting (sync, FEC, "
        "start/stop, Varicode gaps); net bit rate counts information bits. The measured bandwidth holds 99% of the "
        "power of each mode's actual rendered signal.</p>"
    )
    columns = tuple(range(len(table) + 1))
    return note + _table(["Parameter", *(m["label"] for m in table)], rows, text_columns=columns)


LONG_ISLAND = """
<h3>Choosing a fading model: a station on Long Island, NY</h3>
<p>Long Island (about 40.8&deg; N, 73&deg; W) sits near 50&deg; N <em>geomagnetic</em> latitude, because the
geomagnetic pole (about 81&deg; N) lies at almost the same longitude. That makes it a mid-latitude station, but
fading depends on the whole propagation path:</p>
<ul>
<li><b>MM (mid-latitude moderate)</b> &mdash; the primary model: domestic paths and most transatlantic contest
contacts, i.e. most ARRL RTTY Roundup traffic.</li>
<li><b>MQ and MD</b> &mdash; bracket it: a quiet day and an unsettled or multihop day.</li>
<li><b>HM (high-latitude moderate)</b> &mdash; the stress case: northern transatlantic paths to Scandinavia and NE
Europe pass near Labrador and Greenland (about 60&ndash;65&deg; geomagnetic, the auroral oval's edge), polar paths
to Japan and Asia cross it, and in geomagnetic storms (Kp &ge; 6&ndash;7) the oval reaches about 50&deg;, so even
nearby paths flutter.</li>
<li><b>LM (low-latitude moderate)</b> &mdash; optional, for paths to South America and Africa across the geomagnetic
equator; LD only for night-time equatorial spread-F.</li>
</ul>
<p>Run the recommended set with one run per channel:
<code>for ch in MQ MM MD HM; do ./run_study.sh --channel $ch --run-name li-$ch; done</code></p>
"""


def _channel_section(analysis: dict[str, Any]) -> str:
    """Return the channel description, with the fading-model table and Long Island notes in a closed collapse."""
    fading = analysis.get("fading")
    if fading:
        intro = (
            f"<p>This run used <b>{_esc(fading['name'])}</b> Watterson fading ({_esc(fading['description'])}): two "
            f"equal-strength Rayleigh-fading paths with a Gaussian Doppler spread of {fading['spread_hz']:g} Hz, the "
            f"second delayed {fading['delay_ms']:g} ms. As in WSJT-X's own simulators, each transmission is "
            "renormalized to its clean power, so the SNR is the average over the transmission. Band noise is added "
            "after the fading.</p>"
        )
    else:
        intro = (
            "<p>This run used <b>AWGN</b>: white Gaussian band noise only, with no fading, impulse noise or QRM. "
            "That is the standard benchmark (and how FT8/FT4's published thresholds are measured), but HF also "
            "fades; the study can run any of the ITU-R F.1487 Watterson models below with <code>--channel</code>.</p>"
        )
    rows = [[m.name, m.description, f"{m.spread_hz:g}", f"{m.delay_ms:g}"] for m in FADING_MODELS.values()]
    models = _table(["Model", "Represents", "Doppler spread (Hz)", "Path delay (ms)"], rows, text_columns=(1,))
    return f"{intro}<details><summary>Fading models and how to choose one</summary>{models}{LONG_ISLAND}</details>"


def _macro_sentence(totals: list[dict[str, Any]]) -> str:
    """Return the QSO intro's description of contest macros, naming only the modes this run measured them for."""
    names = list(dict.fromkeys(t["label"].removesuffix(" macros") for t in totals if t["variant"].endswith("-macro")))
    framed = [name for name in names if name != "JTTY"]
    if "JTTY" not in names:
        missing = "This run has no JTTY macro data. "
        if not framed:
            return missing
        return (
            f"{' and '.join(framed)} send a typical contest macro with CR/LF framing and the exchange sent twice. "
            f"{missing}"
        )
    if not framed:
        return "JTTY sends a typical contest macro as plain text, with the exchange sent twice. "
    return (
        f"JTTY, {' and '.join(framed)} send a typical contest macro with the exchange sent twice: JTTY as plain text, "
        f"{' and '.join(framed)} with CR/LF framing. "
    )


def _qso_section(analysis: dict[str, Any]) -> str:
    """Return the contest QSO section: bits and best-case time, per-over breakdown, median time chart and table."""
    qso = analysis.get("contest_qso") or {}
    if not qso.get("totals"):
        return "<p>This run has no reference-QSO data (it was made with an older version of the tool).</p>"
    labels = analysis.get("contest_labels", {})
    turnaround = qso.get("turnaround_s") or 0.0
    parts = [
        "<p>The S&amp;P station (W9XYZ in WI) answers K1ABC (in MA) running, from the S&amp;P's call to the final "
        "TU/RR73. As <b>overs</b>, JTTY sends its native function-key atoms (a call is one frame, an exchange one "
        "frame), and RTTY and PSK31 send the same text in capitals. As <b>macros</b>, "
        + _macro_sentence(qso["totals"])
        + "PSK31's airtime includes fldigi's 1 s preamble and postamble on every over. FT8 and FT4 have no macros, "
        "so they appear on both graphs; they use their RU contest mode for RTTY Roundup and the standard QSO for the "
        "serial contest, one T/R slot per over. <b>Best case</b> is the error-free time"
        + (f" with {turnaround:.1f} s turnaround per over" if turnaround else " (airtime only, no turnaround)")
        + ". <b>Median</b> is the median time of simulated QSOs, using the measured copy probability of each over at "
        "each SNR (an over or macro with an ambiguous field counts as not copied; see Contest exchanges): JTTY, "
        "RTTY and PSK31 repeat a failed over after an AGN? request, which may itself need "
        "repeating; FT8 and FT4 lose a whole T/R cycle per failed over.</p>"
    ]
    for contest in dict.fromkeys(t["contest"] for t in qso["totals"]):
        parts.append(f"<h3>{_esc(labels.get(contest, contest))}</h3>")
        rows = [
            [
                t["label"],
                str(t["overs"]),
                f"{t['units']} {t['unit']}",
                f"{t['info_bits']:.0f}",
                f"{t['channel_bits']:.0f}",
                f"{t['airtime_s']:.1f}",
                f"{t['best_s']:.1f}",
            ]
            for t in qso["totals"]
            if t["contest"] == contest
        ]
        parts.append(
            _table(
                ["Mode", "Overs", "Size", "Information bits", "Channel bits", "Airtime (s)", "Best-case QSO (s)"], rows
            )
        )
        detail = [
            [
                b["label"],
                b["kind"] + ("" if b["in_qso"] else " (ref.)"),
                b["sender"],
                str(b["text"]),
                str(b["units"]),
                _num(b.get("info_bits"), 0, signed=False),
                _num(b.get("airtime_s"), 2, signed=False),
            ]
            for b in qso["breakdown"]
            if b["contest"] == contest
        ]
        parts.append(
            "<details><summary>Every over: text, size, bits, airtime</summary>"
            + _table(
                ["Mode", "Over", "Sender", "Text", "Frames / chars / msgs", "Info bits", "Airtime (s)"],
                detail,
                text_columns=(3,),
            )
            + "</details>"
        )
        for kind, heading in (("overs", "Overs"), ("macros", "Macros")):
            parts.append(f"<h4>{heading}</h4>")
            parts.append(_chart(f"qso:{contest}:{kind}"))
    return "".join(parts)


def _failures_section(analysis: dict[str, Any], run_name: str) -> str:
    """Return the strong-signal failures section with the grid-phase evidence and replay commands."""
    failures = analysis.get("failures") or {}
    groups = failures.get("groups") or []
    unmeasured = failures.get("unmeasured") or []
    not_measured = (
        f"<p>Not measured (no 90% point, or no trials at the cut): {_esc(unmeasured_text(unmeasured))}.</p>"
        if unmeasured
        else ""
    )
    if not groups:
        return f"<p>No strong-signal data in this run.</p>{not_measured}"
    rows = [
        [
            MODE_LABELS[g["mode"]],
            CLASS_LABELS.get(g["cls"], g["cls"]),
            _num(g["from_snr_db"]),
            str(g["trials"]),
            str(g["failed"]),
            f"{g['per_1000']:.1f}",
            str(g["n_missed"]),
            str(g["n_split"]),
            str(g["n_no_eom"]),
            str(g.get("n_ambiguous", 0)),
            str(g["n_false_text"]),
        ]
        for g in groups
    ]
    parts = [
        "<p>Trials at least 3 dB above each series' 90% decode point that were still not a clean copy. "
        "<i>Split</i>: JTTY decoded every frame but printed the message over several lines. <i>No EOM</i>: every "
        "frame in one line but no end-of-message. <i>Ambiguous</i>: a contest field printed right but also with a "
        "conflicting value. <i>False text</i>: wrong text printed alongside.</p>",
        _table(
            [
                "Mode",
                "Class",
                "From SNR (dB)",
                "Trials",
                "Failed",
                "Per 1000",
                "Missed",
                "Split",
                "No EOM",
                "Ambiguous",
                "False text",
            ],
            rows,
        ),
        not_measured,
    ]
    test = failures.get("grid_phase_test") or {}
    if test and test.get("splits"):
        parts.append(
            '<div class="callout"><b>JTTY split messages and the search grid.</b> '
            f"{test['splits_near']} of {test['splits']} split messages started within "
            f"{100 * test['window']:.0f}% of a decoder search-window boundary (the receiver searches every quarter "
            f"frame), against {100 * test['baseline_near_share']:.1f}% of all {test['strong_trials']} strong-signal "
            f"JTTY trials (one-sided binomial p = {_p_value(test['p_value'])}). This points at the "
            "frame-to-message assembly in the JTTY receiver when a frame lands on a window boundary.</div>"
        )
    examples = failures.get("examples") or []
    if examples:
        rows = []
        for e in examples[:20]:
            phase = "" if e.get("grid_phase") is None else f"{e['grid_phase']:.3f}"
            command = (
                f"uv run python -m jtty_snr_study replay results/{run_name} --mode {e['mode']} --cls {e['cls']} "
                f"--snr {e['snr_db']:g} --trial {e['trial']} --wav trial.wav"
            )
            rows.append(
                [MODE_LABELS[e["mode"]], _num(e["snr_db"]), e["failure"], phase, e["sent"], e["printed"], command]
            )
        parts.append(
            "<details><summary>Examples and replay commands (the WAV opens in WSJT-X)</summary>"
            + _table(
                ["Mode", "SNR", "Failure", "Grid phase", "Sent", "Printed", "Replay"], rows, text_columns=(4, 5, 6)
            )
            + "</details>"
        )
    return "".join(parts)


def _noise_section(analysis: dict[str, Any]) -> str:
    """Return the false-prints-in-noise section."""
    noise = analysis.get("noise") or []
    if not noise:
        return "<p>This run has no noise-only data (use <code>--noise-hours</code>).</p>"
    rows = []
    for n in noise:
        if n["mode"] in KEYBOARD_MODES:
            rate = f"{n['per_minute']:.1f} per minute"
            interval = f"{n['per_hour_lo'] / 60:.1f} to {n['per_hour_hi'] / 60:.1f} per minute"
        else:
            rate = f"{n['per_hour']:.2f} per hour"
            interval = f"{n['per_hour_lo']:.2f} to {n['per_hour_hi']:.2f} per hour"
        rows.append(
            [
                MODE_LABELS[n["mode"]],
                f"{n['hours']:.2f}",
                f"{n['false_prints']} {n['unit']}",
                rate,
                interval,
                "; ".join(e[:40] for e in n["examples"][:3]),
            ]
        )
    return (
        "<p>Pure band noise through the same receiver, decoded the same way: everything printed is false. JTTY "
        "counts printed messages, FT8/FT4 decodes; exact Poisson 95% intervals. RTTY and PSK31 have no squelch in "
        "this study, so they print characters from noise continuously, as an unsquelched decoder does.</p>"
        + _table(["Mode", "Hours of noise", "False prints", "Rate", "95% CI", "Examples"], rows, text_columns=(5,))
    )


def _thresholds(analysis: dict[str, Any]) -> str:
    """Return the thresholds table."""
    level = analysis["headline_level"]
    rows = []
    for row in analysis["thresholds"]:
        rows.append(
            [
                row["series"],
                row["unit"],
                str(row["trials"]),
                str(row.get("units_sent", "")),
                f"{_num(row.get('headline_db'))} dB",
                f"{_num(row.get('headline_lo'))} to {_num(row.get('headline_hi'))}",
                _num(row.get("ebn0_headline_db"), signed=False),
                _num(row.get("reported_snr_at_headline_db")),
            ]
        )
    headers = [
        "Series",
        "Unit",
        "Trials",
        "Units",
        f"{level}%",
        "95% CI",
        f"Eb/N0 at {level}%",
        f"Reported at {level}%",
    ]
    note = (
        f"<p>SNR is true SNR at the antenna in a 2500 Hz reference bandwidth. The {level}% point is read from the "
        "measured decode curve, fitted to never fall as SNR rises, with bootstrap intervals; a dash means it was not "
        f"reached by +10 dB. <em>Reported</em> is what the decoder printed on average near the {level}% point (a "
        "censored-normal fit where reports hit the decoder's floor).</p>"
        "<p><b>Eb/N0</b> = SNR + 10&middot;log<sub>10</sub>(2500 Hz / net bit rate), with JTTY 34 information bits per "
        "1.888 s frame, FT8 and FT4 77 bits per 12.64 s / 5.04 s transmission, RTTY only the 5 data bits of each "
        "7.5-bit character (start, stop and shift characters are overhead), and PSK31 5 bits per character at the "
        "mean Varicode length of the text each series sent (lower-case free text is shorter than contest capitals). "
        "<b>Trials</b> are transmissions; <b>units</b> are what decode % counts: 5-character groups, except one per "
        "whole contest macro. Intervals come from a bootstrap over whole trials, so groups sharing a transmission are "
        "not treated as independent.</p>"
    )
    return note + _table(headers, rows)


def _decode_levels(analysis: dict[str, Any]) -> str:
    """Return the table of the SNR at which each mode reaches each decode rate, or "" for older analyses."""
    levels = analysis.get("decode_levels")
    if not levels:
        return ""
    rows = [
        [row["series"], row["unit"], *(_num(row[f"decode_{level}_db"]) for level in DECODE_LEVELS)] for row in levels
    ]
    note = (
        "<p>SNR in dB (2500 Hz reference, true SNR at the antenna) at which each mode's measured decode curve reaches "
        "the rate. A dash means it was not reached by +10 dB, or was already above that rate at the lowest SNR "
        "measured.</p>"
    )
    return note + _table(["Mode", "Unit", *(f"{level}%" for level in DECODE_LEVELS)], rows)


def _summary(run_dir: Path) -> str:
    """Return the per-SNR results tables, one collapsible section per mode and class."""
    path = run_dir / "tables" / "summary.csv"
    if not path.exists():
        return "<p>No summary table.</p>"
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        groups.setdefault((row["mode"], row["cls"]), []).append(row)

    def pct(value: str) -> str:
        return f"{100 * float(value):.1f}" if value else DASH

    def num(value: str, digits: int = 1) -> str:
        return _num(float(value), digits) if value else DASH

    sections = [
        "<p>Garbage counts wrong units printed per 1000 trials. Chars/min is the throughput: correctly copied "
        "characters per minute of airtime, as defined exactly in the Throughput section (RTTY and PSK31 count the "
        "characters of copied 5-character groups; FT8/FT4 airtime is a whole 15 s / 7.5 s T/R slot per message). "
        "Reported SNR is mean &plusmn; SD (censored-normal fit where reports hit the floor).</p>"
    ]
    headers = [
        "SNR",
        "Trials",
        "Units",
        "Decode %",
        "95% CI",
        "Copy %",
        "CER",
        "Garbage per 1000 trials",
        "Chars/min",
        "True SNR (meas.)",
        "Reported SNR",
        "Bias",
        "At floor",
    ]
    for (mode, cls), points in groups.items():
        table_rows = [
            [
                f"{float(p['snr_db']):+.1f}".replace("-", MINUS),
                p["n_trials"],
                p.get("units", ""),
                pct(p["decode"]),
                f"{pct(p['decode_lo'])}{DASH}{pct(p['decode_hi'])}",
                pct(p["copy"]),
                f"{float(p['cer_mean']):.3f}" if p["cer_mean"] else DASH,
                f"{float(p['garbage_per_1000']):.1f}",
                _num(float(p["throughput_cpm"]), 0, signed=False) if p.get("throughput_cpm") else DASH,
                num(p["snr_true_measured_mean"], 2),
                f"{num(p['snr_reported_mean'])} ± {float(p['snr_reported_sd']):.1f}"
                if p["snr_reported_sd"]
                else num(p["snr_reported_mean"]),
                num(p["snr_bias_mean"]),
                p["n_reported_floor"],
            ]
            for p in points
        ]
        title = f"{MODE_LABELS.get(mode, mode)} {DASH} {CLASS_LABELS.get(cls, cls)}"
        sections.append(f"<details><summary>{_esc(title)}</summary>{_table(headers, table_rows)}</details>")
    return "".join(sections)


def _grid_item(config: dict[str, Any]) -> str:
    """Return the method item describing how the run chose its SNR points."""
    fixed = config.get("fixed_snrs") or []
    if fixed:
        return (
            f"<b>SNR grid:</b> fixed, {_num(fixed[0], 1)} to {_num(fixed[-1], 1)} dB ({len(fixed)} points, "
            f"{_esc(config.get('trials'))} trials each)."
        )
    pilot = config.get("pilot_snrs") or []
    if not pilot:
        return ""
    extended = "pilot_floor_db" in config
    extends = ", extended downward 1 dB at a time until each curve is at or below 1%" if extended else ""
    capped = f", up to at most {_num(max(pilot), 0)} dB" if extended else ""
    return (
        f"<b>SNR grid:</b> a pilot sweep from {_num(min(pilot), 0)} to {_num(max(pilot), 0)} dB in 1 dB steps "
        f"({_esc(config.get('pilot_trials'))} trials per point){extends}; then a "
        f"{_num(config.get('fine_step_db', 0.5), 1, signed=False)} dB grid at full trials over each curve's "
        f"1{DASH}99% span plus {_num(config.get('fine_margin_db', 1.5), 1, signed=False)} dB each side{capped}. "
        f"Charts show both, from {MINUS}21 to +10 dB."
    )


def _psk31_item(receiver: dict[str, Any]) -> str:
    """Return the method bullet describing the PSK31 station, or "" for runs made before PSK31 was added."""
    settings = receiver.get("psk31")
    if not settings:
        return ""
    return (
        f"<b>PSK31:</b> BPSK31 at {settings['baud']:g} baud, G3PLX Varicode, carrier {settings['carrier_hz']:.0f} Hz "
        f"(fldigi default), {settings['preamble_bits']} preamble reversals and {settings['postamble_bits']} bits of "
        f"postamble carrier. The operator's click lands up to ±{receiver.get('psk31_tuning_error_hz', 5):.0f} Hz "
        "off; the decoder snaps to the signal within ±10 Hz as fldigi does, then tracks it with AFC. Differential "
        "detection after a matched filter; no squelch. Free text in lower case, contest overs in capitals."
    )


def _method(config: dict[str, Any], analysis: dict[str, Any]) -> str:
    """Return the method and caveats section from the run's config.json and analysis."""
    receiver = config.get("receiver", {})
    ssb = receiver.get("ssb_filter_hz", [300.0, 2700.0])
    narrow = receiver.get("rtty_filter_hz", [1960.0, 2460.0])
    band = receiver.get("decode_band_hz", [300, 2700])
    floors = analysis.get("reported_snr_floor_db", {})
    failures = analysis.get("failures") or {}
    jtty_groups = [g for g in failures.get("groups", []) if g["mode"] == "jtty"]
    strong = sum(g["trials"] for g in jtty_groups)
    splits = sum(g["n_split"] for g in jtty_groups)
    false_text = sum(g["n_false_text"] for g in jtty_groups)
    channel = analysis.get("channel", "awgn")
    items = [
        "Every trial is generated, put through a simulated HF station, and decoded: <b>JTTY, FT8 and FT4</b> are "
        "rendered and decoded by this WSJT-X tree's own <code>wsjt-codec</code> (JTTY through the GUI's receive "
        "path); <b>RTTY</b> and <b>PSK31</b> by C reference modulators and matched-filter demodulators.",
        f"<b>Channel:</b> {'AWGN only' if channel == 'awgn' else _esc(channel) + ' Watterson fading plus AWGN'}. "
        "SNR is the WSJT-X convention: signal power over noise power in 2500 Hz, at the antenna.",
        f"<b>Receiver:</b> USB with a {ssb[0]:.0f}{DASH}{ssb[1]:.0f} Hz SSB filter for JTTY/FT8/FT4/PSK31; RTTY "
        f"through a {narrow[0]:.0f}{DASH}{narrow[1]:.0f} Hz filter. AGC off; no-signal level "
        f"{receiver.get('noise_meter_db', 30):.0f} dB on the WSJT-X meter; 16-bit audio at 12 kHz.",
        f"<b>JTTY:</b> Rx {receiver.get('jtty_rx_hz', 1500):.0f} Hz, F Tol {receiver.get('jtty_ftol_hz', 20)} Hz "
        f"(GUI defaults), transmitter within ±{receiver.get('jtty_offset_hz', 10):.0f} Hz, random start time. "
        f"Lowest SNR report {_num(floors.get('jtty'), 0)} dB.",
        f"<b>FT8/FT4:</b> Deep decoding with a-priori (AP) decoding off; decoders search the rig's "
        f"{band[0]}{DASH}{band[1]} Hz passband; random frequency across the passband and DT within about ±0.5 s.",
        f"<b>RTTY:</b> 45.45 baud, 170 Hz shift (mark 2125 / space 2295 Hz), 1.5 stop bits, US-TTY Baudot with "
        f"unshift-on-space; tuning error up to ±{receiver.get('rtty_tuning_error_hz', 5):.0f} Hz, no AFC. Only "
        "characters printed while the message was on the air are scored.",
        _psk31_item(receiver),
        "<b>Messages:</b> reference contest QSO overs (RTTY Roundup and serial-number) and ragchew sentences for "
        "JTTY, RTTY and PSK31 (the same texts at the same trial; PSK31 types the sentences in lower case), and the "
        "equivalent FT8/FT4 QSO messages.",
        _grid_item(config),
        f"<b>Run:</b> seed {_esc(config.get('seed'))}, {_esc(config.get('trials'))} trials per fine-grid point "
        f"({_esc(config.get('pilot_trials'))} at pilot points), {_esc(config.get('wsjtx_version', ''))}, wsjt-codec "
        f"SHA-256 <code>{_esc(str(config.get('codec_sha256', ''))[:16])}…</code>.",
    ]
    caveats = [
        "AWGN is the standard benchmark, not the whole of HF: fading, QRN impulses and QRM change the picture "
        "(see the Channel section for fading runs).",
        f"JTTY searches its Rx frequency ± F Tol plus fixed channels over 1200{DASH}1800 Hz (about 600 Hz) and has no "
        "common time reference; FT8/FT4 search the whole 2.4 kHz passband but know when each period starts. A "
        "narrower search has fewer false starts, which can be worth a fraction of a dB.",
        "The RTTY decoder is a careful reference implementation (about 1 dB from ideal noncoherent FSK), not MMTTY "
        "or 2Tone.",
        "The PSK31 decoder is likewise a reference implementation (differential detection about 1 dB from ideal "
        "DBPSK near 1% bit errors, from the matched filter's inter-symbol interference), not fldigi.",
        "Every mode is scored in 5-character groups, but they are lost differently: an FT8 or FT4 message decodes "
        "whole or not at all and a JTTY frame nearly so, while RTTY and PSK31 lose characters one at a time, so one "
        "lost character costs its group. A message's final short group (1-4 characters) can be matched by chance in "
        "garbage printed from noise. Compare thresholds with the Eb/N0 column, and contest throughput with the "
        "contest-QSO section.",
        "Runs with the same seed share their trials, so two runs agreeing is not independent confirmation; "
        "replicate with a different <code>--seed</code>.",
    ]
    if strong:
        caveats.append(
            f"At strong signals JTTY split {splits} of {strong} messages over two lines and printed false text in "
            f"{false_text}; see Strong-signal failures."
        )
    return (
        "<ul>"
        + "".join(f"<li>{item}</li>" for item in items if item)
        + "</ul><h3>Caveats</h3><ul>"
        + "".join(f"<li>{item}</li>" for item in caveats)
        + "</ul>"
    )


def make_report(run_dir: Path) -> Path:
    """Write report.html for a run from its analysis.json, tables and config.json; return its path."""
    analysis = json.loads((run_dir / "analysis.json").read_text())
    config_path = run_dir / "config.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    channel = analysis.get("channel", "awgn")
    channel_text = "AWGN" if channel == "awgn" else f"{channel} fading"
    run_info = (
        f"Run <code>{_esc(run_dir.name)}</code> · {analysis['trials']:,} trials · "
        f"{_esc(', '.join(MODE_LABELS[m] for m in analysis['modes']))} · {_esc(channel_text)} · "
        f"seed {_esc(config.get('seed', ''))} · {_esc(config.get('written', ''))}"
    )
    body = f"""
<main>
<header>
<h1>JTTY SNR Study</h1>
<p class="lede">How often JTTY copies as the signal sinks into band noise, measured the way FT8 and FT4 are
characterized, with FT8, FT4, RTTY and PSK31 run through the same simulated HF station for comparison.</p>
<p class="note">{run_info}</p>
<div class="theme"><button type="button" id="theme">Theme: auto</button></div>
</header>
<div class="tiles">{_tiles(analysis)}</div>

<h2>The modes</h2>
{_modes_section(analysis)}

<h2>Channel</h2>
{_channel_section(analysis)}

<h2>5-character decode rate</h2>
<p>The headline curve: the share of transmitted 5-character groups printed exactly, for every mode. Five characters
is a callsign's worth (W9XYZ), the shortest piece of text that is useful on its own, so a group counts only when all
its characters print as one unbroken piece; garbage between groups does not break them. Each message is cut into
groups from its start, and a final 1{DASH}4 characters form a short group of their own. An FT8 or FT4 message gives
all its groups or none. The diamonds mark each mode's {analysis["headline_level"]}% point; the legend gives its
value.</p>
{_chart("decode")}
<h3>SNR at each decode rate</h3>
{_decode_levels(analysis)}

<h2>Contest QSO: time and bits to complete an exchange</h2>
{_qso_section(analysis)}

<h2>Contest exchanges copied exactly</h2>
<h3>Overs</h3>
<p>Each over of the reference QSOs printed with no error, and with no information field (a call or an exchange)
also printed with a conflicting value elsewhere in the printout. FT8 and FT4 appear on both graphs
with their QSO messages; their decodes are whole, CRC-checked messages, so the ambiguity rule does not apply to them.
</p>
{_chart("copy-overs")}
<h3>Macros</h3>
<p>A macro counts as copied when every required word (the call, the exchange, an AGN? request) prints exactly in at
least one of its repetitions and no call or exchange is <b>ambiguous</b>. A field is ambiguous when the same
information is received with a different value, so the operator cannot tell which is right: a printed word that
could pass for the field (the same length, at most 2 characters different, or 1 for fields of 3 characters or fewer
such as a state) without being any sent word. <code>W4LEW W4LEW</code> printed as <code>QRLEW W4LEW</code> is not
copied; printed as <code>W4LEW</code> with the other copy lost, it is. A fragment such as <code>2BMJ</code> for
<code>KE2BMJ</code>, a word of another length, or junk with any symbol other than a trailing query mark
(<code>HA&lt;JDU</code>, <code>K2AB?D</code>, since every call, state and serial is letters and digits) does not
make a field ambiguous. A <code>?</code> at the end of a word is read as a query and ignored, so <code>K2ABD?</code>
reads as <code>K2ABD</code>.</p>
{_chart("copy-macros")}

<h2>JTTY copy by message length</h2>
<p>A JTTY message is copied exactly only if every frame and its end-of-message flag decode, so longer messages need
more signal.</p>
{_chart("length")}

<h2>Character error rate of free text</h2>
<p>RTTY-style print quality on keyboard-to-keyboard sentences: edit distance between what was sent and what printed,
divided by the sent length, shown up to 50% because copy above that is likely unreadable.
Dashed: noncoherent-FSK theory for RTTY with
ideal character sync. Dotted: ideal differential-BPSK theory for PSK31 (a character lost if any of its bits is).</p>
{_chart("cer")}

<h2>Throughput</h2>
<p>Correctly copied text per minute of airtime, folding each mode's speed and its errors into one operator-facing
number. At each SNR it is 60 &times; (characters copied, summed over every trial at that SNR) &divide; (seconds of
airtime, summed over the same trials). Characters are those of the sent message after normalizing it: upper case,
each run of spaces read as one space, none at the ends. Spaces between words count (except at JTTY frame
boundaries, below); framing does not.</p>
<ul>
<li><b>Characters copied.</b> RTTY and PSK31: the characters of the message's 5-character groups that printed whole
(the headline unit), so scattered characters matched by chance in noise count nothing. JTTY: the text of each frame
the decoder printed correctly, with the spaces at the frame's two ends left out, whether or not the rest of the
message arrived; a space between two words that falls at a frame boundary is therefore not counted, so a fully
copied JTTY message counts fewer characters than its normalized text (about 7% fewer for free text, 2% for contest
overs) (JTTY contest macros count every character of the macro's words). FT8 and FT4: every character of the message
when it decoded, none when it did not. Contest macros (JTTY, RTTY and PSK31): every character of the macro's words
when the macro counts as copied (the call and one copy of the exchange), none when it does not.</li>
<li><b>Airtime.</b> RTTY: the whole transmission at 45.45 baud with 1.5 stop bits {DASH} 0.1 s of steady mark, two
LTRS idle characters, the framing spaces, the message with its shift characters, and two LTRS at the end. PSK31: the
whole transmission at 31.25 baud {DASH} 32 bits of preamble, the framing spaces, the message in Varicode (free text
is sent in lower case, whose Varicode is shorter; contest overs in capitals), 32 bits of postamble, and one symbol
each of envelope rise and fall. JTTY: the rendered transmission, a whole number of 1.888 s frames. FT8 and FT4: one
whole 15 s / 7.5 s T/R slot per message, since each message takes a slot. A contest macro's airtime is its whole
transmission, the repeated exchange (and the RTTY and PSK31 CR/LF) included.</li>
</ul>
<h3>Contest overs</h3>
<p>FT8 and FT4 appear here with their QSO messages, the same series as the contest-overs copy chart.</p>
{_chart("throughput-contest")}
<h3>Contest macros</h3>
<p>The same overs as typical contest macros with the exchange sent twice (JTTY sends the same words, without CR/LF).
A macro counts as copied when the call and one copy of the exchange print exactly. FT8 and FT4 have no macros, so
their QSO messages are repeated here for reference.</p>
{_chart("throughput-macros")}
<h3>Ragchew text</h3>
<p>Free-text sentences. FT8 and FT4 have no free-text series in this study.</p>
{_chart("throughput-text")}

<h2>Reported vs true SNR</h2>
<p>What each decoder prints as the signal report against the true SNR. Reports cannot go below a decoder's floor
(JTTY {_num(analysis.get("reported_snr_floor_db", {}).get("jtty"), 0)} dB, FT4 {MINUS}21 dB, FT8 {MINUS}25 dB);
where they hit it, the mean comes from a censored-normal fit rather than dropping them.</p>
{_chart("calibration")}

<h2>Strong-signal failures</h2>
{_failures_section(analysis, run_dir.name)}

<h2>False prints in band noise</h2>
{_noise_section(analysis)}

<h2>Thresholds</h2>
{_thresholds(analysis)}

<h2>All results by SNR</h2>
{_summary(run_dir)}

<h2>Method</h2>
{_method(config, analysis)}
</main>
"""
    data = json.dumps(analysis).replace("</", "<\\/")
    page = (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>JTTY SNR Study</title>\n'
        f"<style>{_STYLE}</style>\n</head>\n<body>{body}"
        f'<script type="application/json" id="analysis">{data}</script>\n<script>{_SCRIPT}</script>\n</body>\n</html>\n'
    )
    path = run_dir / "report.html"
    path.write_text(page, encoding="utf-8")
    return path
