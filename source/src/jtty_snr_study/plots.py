"""Static charts (PNG + SVG) from analysis.json, for papers, posts and slides.

Each mode keeps one color on every chart, overs and macros alike (FT8 red, FT4 green, JTTY blue, PSK31 purple, RTTY
orange), with 2 px lines, >= 8 px markers, 10% confidence-band washes, recessive grid and axes, and a legend on every
multi-series chart. Red/green and blue/purple are hard to separate with color-vision deficiency, and these charts
rely on color to match a curve to its legend entry. JTTY message lengths use one ordinal blue ramp.
"""

from __future__ import annotations

import json
from itertools import product
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

MODE_COLORS: dict[str, str] = {
    "ft8": "#e34948",
    "ft4": "#008300",
    "jtty": "#2a78d6",
    "psk31": "#8a3fd1",
    "rtty": "#e08a00",
}
"""Each mode's one color on every chart in light mode; report.py's stylesheet holds these and the dark-mode values."""

LENGTH_RAMP: tuple[str, ...] = ("#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281")
"""Ordinal blue ramp for JTTY message length (short = light)."""

SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
TEXT_MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

LINE_WIDTH = 1.5
"""2 px at 96 dpi."""

MARKER_SIZE = 6.0
"""8 px at 96 dpi."""

SNR_AXIS_LABEL = "SNR in 2500 Hz (dB, true)"

SNR_AXIS_RANGE_DB: tuple[float, float] = (-21.0, 10.0)
"""The SNR span of every chart. Trials below it still count toward the tables (report.py's SNR_RANGE matches this)."""

MINUS_SIGN = "\N{MINUS SIGN}"


def _style() -> None:
    """Apply the chart system's chrome to matplotlib."""
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.size": 10,
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "axes.edgecolor": AXIS,
            "axes.labelcolor": TEXT_SECONDARY,
            "axes.titlecolor": TEXT_PRIMARY,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "xtick.color": TEXT_MUTED,
            "ytick.color": TEXT_MUTED,
            "xtick.labelcolor": TEXT_SECONDARY,
            "ytick.labelcolor": TEXT_SECONDARY,
            "legend.frameon": False,
            "legend.labelcolor": TEXT_PRIMARY,
            "svg.fonttype": "none",
        }
    )


_channel_label = "AWGN"
"""Channel description shown in every subtitle; set per run by make_figures."""


def channel_label(analysis: dict[str, Any]) -> str:
    """Return a short description of the run's propagation channel."""
    fading = analysis.get("fading")
    if not fading:
        return "AWGN"
    return f"{fading['name']} fading ({fading['spread_hz']:g} Hz spread, {fading['delay_ms']:g} ms)"


def _figure(title: str, subtitle: str) -> tuple[Figure, Axes]:
    """Return a new figure with a sentence-case title and a secondary subtitle naming the channel."""
    figure, axes = plt.subplots(figsize=(8.0, 5.0), layout="constrained")
    figure.suptitle(title, x=0.01, ha="left", fontsize=13, color=TEXT_PRIMARY, fontweight="bold")
    axes.set_title(f"{subtitle} · {_channel_label}", loc="left", fontsize=9.5, color=TEXT_SECONDARY)
    return figure, axes


def _save(figure: Figure, directory: Path, name: str) -> list[Path]:
    """Write the figure as PNG and SVG and close it."""
    directory.mkdir(parents=True, exist_ok=True)
    paths = [directory / f"{name}.png", directory / f"{name}.svg"]
    figure.savefig(paths[0], dpi=160)
    figure.savefig(paths[1])
    plt.close(figure)
    return paths


def _signed(value: float) -> str:
    """Format a dB value with an explicit sign and a true minus sign."""
    return f"{value:+.1f}".replace("-", MINUS_SIGN)


def _in_view(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the points inside the charts' SNR span, so data off the axis cannot rescale the other axis."""
    return [p for p in points if SNR_AXIS_RANGE_DB[0] <= p["snr_db"] <= SNR_AXIS_RANGE_DB[1]]


def _snr_axis(axes: Axes) -> None:
    """Label the x axis as SNR and fix it to the charts' span."""
    axes.set_xlabel(SNR_AXIS_LABEL)
    axes.set_xlim(*SNR_AXIS_RANGE_DB)


def _percent_axis(axes: Axes) -> None:
    """Format the y axis as 0-100% decode probability."""
    axes.set_ylim(-2, 102)
    axes.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value:.0f}%"))


def _plot_rate(axes: Axes, series: dict[str, Any], key: str, color: str, label: str) -> None:
    """Draw one proportion-vs-SNR series (``key`` is decode or copy) with its 95% band."""
    points = _in_view(series["points"])
    snr = [p["snr_db"] for p in points]
    value = [100 * p[key] for p in points]
    low = [100 * p[f"{key}_lo"] for p in points]
    high = [100 * p[f"{key}_hi"] for p in points]
    axes.fill_between(snr, low, high, color=color, alpha=0.10, linewidth=0)
    axes.plot(
        snr,
        value,
        color=color,
        linewidth=LINE_WIDTH,
        marker="o",
        markersize=MARKER_SIZE,
        markeredgecolor=SURFACE,
        markeredgewidth=1.0,
        solid_capstyle="round",
        solid_joinstyle="round",
        label=label,
    )


def decode_vs_snr(analysis: dict[str, Any], directory: Path) -> list[Path]:
    """Headline chart: 5-character decode % vs SNR for every mode, with each mode's headline-level point labeled."""
    _style()
    level = analysis["headline_level"]
    figure, axes = _figure(
        "5-character decode rate vs SNR",
        "5-character groups (a callsign's worth) printed exactly · simulated HF station · 95% intervals",
    )
    axes.axhline(level, color=AXIS, linewidth=0.8, linestyle=(0, (4, 3)), zorder=0)
    for series in analysis["headline"]:
        color = MODE_COLORS[series["mode"]]
        threshold = series.get("threshold") or {}
        mu = threshold.get("headline_db")
        # Thresholds of neighboring modes can sit ~1.5 dB apart, so the value rides in the legend, not beside the mark.
        label = series["label"]
        if mu is not None:
            label += f" · {level}% at {_signed(mu)} dB"
        _plot_rate(axes, series, "decode", color, label)
        if mu is not None:
            axes.plot([mu], [level], marker="D", markersize=7, color=color, markeredgecolor=TEXT_PRIMARY, zorder=5)
    _snr_axis(axes)
    axes.set_ylabel("5-character groups decoded")
    _percent_axis(axes)
    axes.legend(loc="lower right", fontsize=9)
    return _save(figure, directory, "decode_vs_snr")


CER_AXIS_PERCENT: tuple[float, float] = (0.0, 50.0)
"""The character-error axis; above 50% copy is likely unreadable (report.py's CER_AXIS matches this)."""

SLOT_MODES: tuple[str, ...] = ("ft8", "ft4")
"""Modes with no macro form, shown on both the overs and the macros charts."""


def on_chart(mode: str, is_macro: bool, macros_chart: bool) -> bool:
    """Return whether a series belongs on an overs or a macros chart: overs and macros never share one."""
    return mode in SLOT_MODES or is_macro == macros_chart


def message_copy_vs_snr(analysis: dict[str, Any], directory: Path) -> list[Path]:
    """Whole-message copy % for contest/QSO traffic, with overs and macros on separate charts."""
    paths: list[Path] = []
    for macros_chart, title, sent, name in (
        (False, "Contest overs", "randomized run/S&P exchanges", "message_copy_vs_snr"),
        (True, "Contest macros", "exchange sent twice, call + one copy required", "message_copy_macros_vs_snr"),
    ):
        _style()
        keyboard = dict.fromkeys(
            series["mode"].upper()
            for series in analysis["copy_contest"]
            if series["mode"] not in SLOT_MODES and series["key"].endswith("-contest-macro") == macros_chart
        )
        figure, axes = _figure(
            f"{title} copied exactly vs SNR",
            f"{', '.join(keyboard)}: {sent} · FT8/FT4: standard QSO messages · 95% Wilson intervals",
        )
        for series in analysis["copy_contest"]:
            macro = series["key"].endswith("-contest-macro")
            if on_chart(series["mode"], macro, macros_chart):
                _plot_rate(axes, series, "copy", MODE_COLORS[series["mode"]], series["label"])
        _snr_axis(axes)
        axes.set_ylabel("Copied exactly")
        _percent_axis(axes)
        axes.legend(loc="lower right", fontsize=9)
        paths += _save(figure, directory, name)
    return paths


def jtty_copy_by_length(analysis: dict[str, Any], directory: Path) -> list[Path]:
    """JTTY whole-message copy % by message length: every frame must decode."""
    _style()
    figure, axes = _figure(
        "JTTY message copied exactly, by length",
        "All frames plus end-of-message decoded, text exact · 95% Wilson intervals",
    )
    for series, color in zip(analysis["copy_by_frames"], LENGTH_RAMP, strict=False):
        _plot_rate(axes, series, "copy", color, series["label"].removeprefix("JTTY "))
    _snr_axis(axes)
    axes.set_ylabel("Copied exactly")
    _percent_axis(axes)
    axes.legend(loc="lower right", fontsize=9, title="Message length", title_fontsize=9)
    return _save(figure, directory, "jtty_copy_by_length")


def cer_vs_snr(analysis: dict[str, Any], directory: Path) -> list[Path]:
    """Character error rate of free text (RTTY-style print quality), with RTTY and PSK31 theory."""
    _style()
    figure, axes = _figure(
        "Character error rate of free text vs SNR",
        "Edit distance / sent length · theory: RTTY noncoherent FSK (dashed), PSK31 differential BPSK (dotted)",
    )
    for series in analysis["cer"]:
        points = _in_view(series["points"])
        snr = [p["snr_db"] for p in points]
        cer = [min(100 * p["cer_mean"], 100.0) for p in points]
        color = MODE_COLORS[series["mode"]]
        axes.plot(
            snr,
            cer,
            color=color,
            linewidth=LINE_WIDTH,
            marker="o",
            markersize=MARKER_SIZE,
            markeredgecolor=SURFACE,
            markeredgewidth=1.0,
            label=series["label"],
        )
    theory = _in_view(analysis.get("cer_theory_rtty") or [])
    if theory and any(series["mode"] == "rtty" for series in analysis["cer"]):
        axes.plot(
            [t["snr_db"] for t in theory],
            [100 * t["cer"] for t in theory],
            color=TEXT_MUTED,
            linewidth=1.0,
            linestyle=(0, (4, 3)),
            label="RTTY theory",
        )
    theory_psk31 = _in_view(analysis.get("cer_theory_psk31") or [])
    if theory_psk31 and any(series["mode"] == "psk31" for series in analysis["cer"]):
        axes.plot(
            [t["snr_db"] for t in theory_psk31],
            [100 * t["cer"] for t in theory_psk31],
            color=TEXT_MUTED,
            linewidth=1.0,
            linestyle=(0, (1, 2)),
            label="PSK31 theory",
        )
    _snr_axis(axes)
    axes.set_ylabel("Character error rate")
    _percent_axis(axes)
    axes.set_ylim(*CER_AXIS_PERCENT)
    axes.legend(loc="upper right", fontsize=9)
    return _save(figure, directory, "cer_vs_snr")


def snr_report_calibration(analysis: dict[str, Any], directory: Path) -> list[Path]:
    """Decoder-reported SNR vs measured true SNR, per mode, with y = x and reporting floors."""
    _style()
    figure, axes = _figure(
        "Reported SNR vs true SNR",
        "Mean ± SD of decoder reports (values at a reporting floor excluded) · dashed: y = x",
    )
    lows, highs = [], []
    for series in analysis["calibration"]:
        points = [p for p in _in_view(series["points"]) if p["snr_reported_mean"] is not None]
        if not points:
            continue
        color = MODE_COLORS[series["mode"]]
        x = [p["snr_db"] for p in points]
        y = [p["snr_reported_mean"] for p in points]
        sd = [p["snr_reported_sd"] or 0.0 for p in points]
        axes.errorbar(
            x,
            y,
            yerr=sd,
            color=color,
            linewidth=LINE_WIDTH,
            marker="o",
            markersize=MARKER_SIZE,
            markeredgecolor=SURFACE,
            elinewidth=0.8,
            capsize=0,
            label=series["label"],
        )
        lows.append(min(x))
        highs.append(max(x))
        floor = (analysis.get("reported_snr_floor_db") or {}).get(series["mode"])
        if floor is not None:
            axes.axhline(floor, color=color, linewidth=0.8, linestyle=(0, (1, 2)), zorder=0)
            axes.annotate(
                f"{series['label']} floor",
                (min(x), floor),
                xytext=(2, 3),
                textcoords="offset points",
                fontsize=8,
                color=TEXT_SECONDARY,
            )
    if lows:
        span = [min(lows) - 1, max(highs) + 1]
        axes.plot(span, span, color=TEXT_MUTED, linewidth=1.0, linestyle=(0, (4, 3)), zorder=0)
    _snr_axis(axes)
    axes.set_ylabel("Reported SNR (dB)")
    axes.legend(loc="upper left", fontsize=9)
    return _save(figure, directory, "snr_report_calibration")


QSO_TIME_AXIS_S: tuple[float, float] = (0.0, 120.0)
"""The seconds axis of the contest-QSO charts; slower QSOs run off the top (report.py's qsoChart matches this)."""


def qso_time_vs_snr(analysis: dict[str, Any], directory: Path) -> list[Path]:
    """Median time to complete each reference contest QSO vs SNR: per contest, one overs and one macros chart."""
    qso = analysis.get("contest_qso") or {}
    paths: list[Path] = []
    labels = analysis.get("contest_labels", {})
    contests = dict.fromkeys(curve["contest"] for curve in qso.get("curves", []))
    for contest, (macros_chart, kind, suffix) in product(contests, ((False, "overs", ""), (True, "macros", "_macros"))):
        _style()
        figure, axes = _figure(
            f"Time to complete a contest QSO, {kind}: {labels.get(contest, contest)}",
            "Median, with AGN? repeats (JTTY/RTTY/PSK31) or lost T/R cycles (FT8/FT4) · dotted: error-free",
        )
        for curve in qso["curves"]:
            if curve["contest"] != contest:
                continue
            if not on_chart(curve["mode"], curve["variant"].endswith("-macro"), macros_chart):
                continue
            color = MODE_COLORS[curve["mode"]]
            points = _in_view(curve["points"])
            if points:
                axes.plot(
                    [p["snr_db"] for p in points],
                    [p["median_s"] for p in points],
                    color=color,
                    linewidth=LINE_WIDTH,
                    label=f"{curve['label']} (best {curve['best_s']:.0f} s)",
                )
            axes.axhline(curve["best_s"], color=color, linewidth=0.9, linestyle=(0, (1, 2)), zorder=0)
        axes.set_ylim(*QSO_TIME_AXIS_S)
        _snr_axis(axes)
        axes.set_ylabel("Seconds to complete the QSO")
        axes.legend(loc="upper right", fontsize=9)
        paths += _save(figure, directory, f"qso_time_{contest}{suffix}")
    return paths


def make_figures(run_dir: Path) -> list[Path]:
    """Render every static chart for a run from its analysis.json into figures/."""
    global _channel_label
    analysis = json.loads((run_dir / "analysis.json").read_text())
    _channel_label = channel_label(analysis)
    directory = run_dir / "figures"
    paths: list[Path] = []
    charts = (
        decode_vs_snr,
        message_copy_vs_snr,
        jtty_copy_by_length,
        cer_vs_snr,
        snr_report_calibration,
        qso_time_vs_snr,
    )
    for chart in charts:
        paths += chart(analysis, directory)
    return paths
