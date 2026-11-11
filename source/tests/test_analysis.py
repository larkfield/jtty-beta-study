"""Statistics: Wilson intervals, bootstrap, logistic thresholds, Eb/N0, and the table/JSON outputs."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pytest

from jtty_snr_study import analysis, modes, plots, report, runner


def test_wilson_interval_known_values() -> None:
    low, high = analysis.wilson(50, 100)
    assert low == pytest.approx(0.4038, abs=1e-3) and high == pytest.approx(0.5962, abs=1e-3)
    assert analysis.wilson(0, 100)[0] == 0.0 and analysis.wilson(0, 100)[1] == pytest.approx(0.037, abs=2e-3)
    assert analysis.wilson(0, 0) == (0.0, 0.0)


def test_logistic_fit_recovers_known_threshold() -> None:
    rng = np.random.default_rng(3)
    snr = np.arange(-26.0, -12.0, 0.5)
    n = np.full(len(snr), 2000)
    ok = rng.binomial(n, analysis.logistic(snr, -19.3, 0.8))
    fit = analysis.fit_logistic(snr, ok, n)
    assert fit is not None
    assert fit[0] == pytest.approx(-19.3, abs=0.1) and fit[1] == pytest.approx(0.8, abs=0.1)


def test_logistic_fit_needs_a_transition() -> None:
    assert analysis.fit_logistic([-10.0, -9.0], [100, 100], [100, 100]) is None


def test_interpolated_crossing() -> None:
    assert analysis.interpolate_crossing([-3, -2, -1], [0.1, 0.4, 0.8], 0.5) == pytest.approx(-1.75)
    assert analysis.interpolate_crossing([-3, -2], [0.6, 0.9], 0.5) is None


def test_bootstrap_interval_contains_the_ratio() -> None:
    rng = np.random.default_rng(0)
    units = rng.integers(1, 10, 500)
    ok = rng.binomial(units, 0.6)
    low, high = analysis.bootstrap_ratio(ok, units, rng)
    ratio = ok.sum() / units.sum()
    assert low < ratio < high and high - low < 0.1


def test_ebn0_offsets() -> None:
    assert analysis.ebn0_offset_db("ft8") == pytest.approx(10 * math.log10(2500 / (77 / 12.64)))
    assert analysis.ebn0_offset_db("jtty") == pytest.approx(10 * math.log10(2500 / (34 / 1.888)))
    # PSK31: 5 information bits per character of the mean lower-case Varicode length (about 6.7 bits).
    psk31_rate = 5 * 31.25 / modes.PSK31_CHAR_BITS
    assert analysis.ebn0_offset_db("psk31") == pytest.approx(10 * math.log10(2500 / psk31_rate))
    assert 6.0 < modes.PSK31_CHAR_BITS < 7.5
    # A series' own text sets its basis: contest capitals take longer per character than lower-case text.
    text_bits = analysis.psk31_char_bits([{"sent": "qth reno"}])
    contest_bits = analysis.psk31_char_bits([{"sent": "W9XYZ 599 MA"}, {"sent": ""}])
    assert text_bits is not None and contest_bits is not None and contest_bits > text_bits + 2
    assert analysis.ebn0_offset_db("psk31", contest_bits) == pytest.approx(
        10 * math.log10(2500 / (5 * 31.25 / contest_bits))
    )
    assert analysis.psk31_char_bits([]) is None


def _write_synthetic_run(run_dir: Path) -> None:
    """Write a raw trials.csv with known logistic curves for JTTY and FT8."""
    rng = np.random.default_rng(9)
    rows = []
    for mode, cls, mu in (("jtty", "contest", -18.0), ("jtty", "text", -17.0), ("ft8", "qso", -21.0)):
        for snr in np.arange(mu - 5, mu + 5.5, 0.5):
            for trial in range(150):
                units = 1 if cls != "text" else 6
                ok = int(rng.binomial(units, analysis.logistic(snr, mu, 0.7)))
                row: dict[str, object] = {name: "" for name in runner.ROW_FIELDS}
                reported = snr + rng.normal(0, 1) if ok else None
                row.update(
                    mode=mode,
                    cls=cls,
                    snr_db=float(snr),
                    trial=trial,
                    stage="fine",
                    units=units,
                    units_ok=ok,
                    frames=units if mode == "jtty" else "",
                    copied=int(ok == units),
                    cer=round(1 - ok / units, 3),
                    garbage=0,
                    noise_prints=0,
                    ap_filtered=0,
                    n_messages=int(ok > 0),
                    state="complete" if ok else "none",
                    snr_true_measured_db=float(snr),
                    snr_reported_db="" if reported is None else round(max(reported, -17.0), 2),
                    snr_reported_floor=int(reported is not None and reported <= -17.0),
                    decode_s=0.1,
                    clipped=0,
                )
                rows.append(row)
    (run_dir / "raw").mkdir(parents=True)
    with (run_dir / "raw" / "trials.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=runner.ROW_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


SYNTHETIC_CONFIG: dict[str, object] = {
    "seed": 1,
    "snr_floors": {"jtty": -17.0, "ft8": -25.0},
    "unit_scoring": "5-char groups",
}
"""config.json of the synthetic run: scored in 5-character groups, so analyze accepts it."""


def test_analyze_writes_tables_and_finds_thresholds(tmp_path: Path) -> None:
    _write_synthetic_run(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(SYNTHETIC_CONFIG))
    result = analysis.analyze(tmp_path)
    assert result["headline_level"] == 80
    thresholds = {row["series"]: row for row in result["thresholds"]}
    assert thresholds["FT8"]["snr50_db"] == pytest.approx(-21.0, abs=0.3)
    assert thresholds["FT8"]["snr50_lo"] < -21.0 < thresholds["FT8"]["snr50_hi"]
    assert thresholds["JTTY contest overs"]["snr50_db"] == pytest.approx(-18.0, abs=0.3)
    assert thresholds["JTTY"]["unit"] == "5-char groups"
    expected_80 = -21.0 + 0.7 * math.log(4.0)
    assert thresholds["FT8"]["headline_level"] == 80
    assert thresholds["FT8"]["headline_db"] == pytest.approx(expected_80, abs=0.3)
    assert thresholds["FT8"]["headline_lo"] < thresholds["FT8"]["headline_db"] < thresholds["FT8"]["headline_hi"]
    assert thresholds["FT8"]["decode_80_db"] == thresholds["FT8"]["headline_db"]
    for name in (
        "summary.csv",
        "summary.md",
        "thresholds.csv",
        "thresholds.md",
        "decode_levels.md",
        "failures.md",
        "modes.md",
    ):
        assert (tmp_path / "tables" / name).stat().st_size > 0
    data = json.loads((tmp_path / "analysis.json").read_text())
    assert plots.make_figures(tmp_path), "every static chart should render"
    assert [series["mode"] for series in data["headline"]] == ["ft8", "jtty"]
    assert [row["mode"] for row in data["decode_levels"]] == ["ft8", "jtty"]
    assert data["copy_by_frames"] and data["single_frame"]
    summary = list(csv.DictReader((tmp_path / "tables" / "summary.csv").open()))
    floor_rows = [row for row in summary if row["mode"] == "jtty" and int(row["n_reported_floor"]) > 0]
    assert floor_rows, "JTTY reports at the -17 dB floor should be counted"


def test_analyze_reads_the_headline_at_another_level(tmp_path: Path) -> None:
    _write_synthetic_run(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps(SYNTHETIC_CONFIG))
    result = analysis.analyze(tmp_path, level=90)
    ft8 = next(row for row in result["thresholds"] if row["series"] == "FT8")
    assert result["headline_level"] == 90 and ft8["headline_level"] == 90
    assert ft8["headline_db"] == pytest.approx(-21.0 + 0.7 * math.log(9.0), abs=0.3)
    assert ft8["decode_90_db"] == ft8["headline_db"]
    assert "| 90% (dB) |" in (tmp_path / "tables" / "thresholds.md").read_text()


def test_analyze_refuses_a_run_scored_in_other_units(tmp_path: Path) -> None:
    _write_synthetic_run(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"seed": 1}))
    with pytest.raises(SystemExit, match="rescore"):
        analysis.analyze(tmp_path)
    (tmp_path / "config.json").write_text(json.dumps({"seed": 1, "unit_scoring": "characters"}))
    with pytest.raises(SystemExit, match="characters"):
        analysis.analyze(tmp_path)


def test_measured_level_interpolates_and_ignores_a_chance_dip() -> None:
    grid = [-4.0, -3.0, -2.0, -1.0, 0.0]
    assert analysis.measured_level_snr(grid, [0, 40, 60, 100, 100], [100] * 5, 0.8) == pytest.approx(-1.5)
    # The chance dip at -1 dB pools with -2 dB (150 of 200), which the 80% reading must then cross after.
    assert analysis.measured_level_snr(grid, [0, 40, 90, 60, 100], [100] * 5, 0.8) == pytest.approx(-0.8)


def test_measured_level_reads_a_curve_exactly_at_the_level_at_its_lowest_snr() -> None:
    assert analysis.measured_level_snr([-2.0, -1.0, 0.0], [80, 90, 100], [100] * 3, 0.8) == -2.0
    assert analysis.measured_level_snr([-2.0, -1.0, 0.0], [85, 90, 100], [100] * 3, 0.8) is None


def test_measured_level_is_none_when_not_reached_by_the_ceiling() -> None:
    grid = [8.0, 9.0, 10.0]
    assert analysis.measured_level_snr(grid, [10, 20, 40], [100] * 3, 0.8) is None
    high_grid = [9.0, 11.0, 13.0]
    assert analysis.measured_level_snr(high_grid, [10, 50, 95], [100] * 3, 0.9) is None


def _trials_from_counts(curve: dict[float, tuple[int, int]]) -> analysis.Trials:
    """Return FT8 trials with the given (decoded, sent) counts at each SNR, one unit per trial."""
    rows = []
    for snr, (ok, sent) in curve.items():
        for trial in range(sent):
            row: dict[str, object] = {name: "" for name in runner.ROW_FIELDS}
            decoded = trial < ok
            row.update(
                mode="ft8",
                cls="qso",
                snr_db=snr,
                trial=trial,
                stage="fine",
                units=1,
                units_ok=int(decoded),
                copied=int(decoded),
                cer=0.0 if decoded else 1.0,
                garbage=0,
                noise_prints=0,
                ap_filtered=0,
                n_messages=int(decoded),
                state="complete" if decoded else "none",
                snr_true_measured_db=snr,
                snr_reported_db=snr if decoded else "",
                snr_reported_floor=0,
                decode_s=0.1,
                clipped=0,
            )
            rows.append({key: str(value) for key, value in row.items()})
    return analysis.Trials.from_rows(rows)


def test_threshold_leaves_the_interval_blank_when_resamples_often_miss_the_level() -> None:
    trials = _trials_from_counts({8.0: (60, 100), 9.0: (75, 100), 10.0: (82, 100)})
    row, _ = analysis._threshold("ft8", "FT8", trials, np.random.default_rng(1), None, 80)
    assert row["headline_db"] == pytest.approx(9.0 + 5 / 7)
    assert math.isnan(row["headline_lo"]) and math.isnan(row["headline_hi"])


def test_threshold_reports_the_measured_point_when_the_logistic_fit_fails() -> None:
    trials = _trials_from_counts({-2.0: (0, 100), -1.0: (0, 100), 0.0: (100, 100), 1.0: (100, 100)})
    row, fit = analysis._threshold("ft8", "FT8", trials, np.random.default_rng(1), None, 80)
    assert fit is None
    assert row["headline_db"] == pytest.approx(-0.2)
    assert row["headline_lo"] == pytest.approx(-0.2) and row["headline_hi"] == pytest.approx(-0.2)
    assert math.isfinite(row["ebn0_headline_db"])


def test_strong_signal_failures_name_series_with_no_trials_above_the_cut() -> None:
    row = {"mode": "ft8", "cls": "qso", "snr_db": "-10.0"}
    thresholds = [
        {"mode": "rtty", "series_cls": "text", "snr90_db": 9.0},
        {"mode": "ft8", "series_cls": "qso", "snr90_db": 8.0},
        {"mode": "psk31", "series_cls": "contest", "snr90_db": math.nan},
    ]
    result = analysis.strong_signal_failures([row], thresholds)
    assert [(u["mode"], u["cls"], u["from_snr_db"]) for u in result["unmeasured"]] == [
        ("ft8", "qso", 11.0),
        ("psk31", "contest", None),
        ("rtty", "text", 12.0),
    ]
    assert result["groups"] == []
    assert (
        analysis.unmeasured_text(result["unmeasured"])
        == "FT8 QSO messages (from +11.0 dB); PSK31 contest overs (no 90% point found); RTTY free text (from +12.0 dB)"
    )


def test_mode_table_gives_gaussian_smoothing_its_own_value() -> None:
    table = {m["mode"]: m for m in analysis.mode_table(("ft8", "ft4", "jtty", "psk31", "rtty"), {})}
    assert {mode: m["gaussian_bt"] for mode, m in table.items()} == {
        "ft8": 2.0,
        "ft4": 1.0,
        "jtty": 2.0,
        "psk31": None,
        "rtty": None,
    }
    assert "BT" not in table["jtty"]["modulation"]
    html = report._modes_section({"mode_table": list(table.values())})
    assert "Gaussian smoothing" in html and "BT 1" in html


def test_channel_section_shows_only_the_first_paragraph_until_expanded() -> None:
    html = report._channel_section({"fading": None})
    before, rest = html.split("<details>", 1)
    assert before.count("<p>") == 1 and "AWGN" in before and "<table" not in before
    assert "<details open" not in html
    assert "<table" in rest and "Long Island" in rest and rest.rstrip().endswith("</details>")


def test_overs_and_macros_never_share_a_chart() -> None:
    assert plots.on_chart("jtty", False, False) and not plots.on_chart("jtty", True, False)
    assert plots.on_chart("rtty", True, True) and not plots.on_chart("rtty", False, True)
    for macros_chart in (False, True):
        assert plots.on_chart("ft8", False, macros_chart) and plots.on_chart("ft4", False, macros_chart)


def test_copy_and_qso_figures_keep_overs_and_macros_apart(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    drawn: dict[str, list[str]] = {}
    colors: dict[str, list[str]] = {}

    def capture(figure: Any, directory: Path, name: str) -> list[Path]:
        drawn[name] = figure.axes[0].get_legend_handles_labels()[1]
        lines = [line for line in figure.axes[0].get_lines() if not line.get_label().startswith("_")]
        colors[name] = [line.get_color() for line in lines]
        plt.close(figure)
        return []

    monkeypatch.setattr(plots, "_save", capture)
    rate = [{"snr_db": -5.0, "copy": 0.5, "copy_lo": 0.4, "copy_hi": 0.6}]
    copy = [
        {"key": key, "mode": mode, "label": label, "points": rate}
        for key, mode, label in (
            ("ft8-qso", "ft8", "FT8"),
            ("jtty-contest", "jtty", "JTTY overs"),
            ("jtty-contest-macro", "jtty", "JTTY macros"),
            ("rtty-contest-macro", "rtty", "RTTY macros"),
        )
    ]
    plots.message_copy_vs_snr({"copy_contest": copy}, tmp_path)
    assert drawn["message_copy_vs_snr"] == ["FT8", "JTTY overs"]
    assert drawn["message_copy_macros_vs_snr"] == ["FT8", "JTTY macros", "RTTY macros"]
    assert colors["message_copy_macros_vs_snr"] == [plots.MODE_COLORS[m] for m in ("ft8", "jtty", "rtty")]

    point = [{"snr_db": -5.0, "median_s": 30.0}]
    curves = [
        {"contest": "ru", "variant": variant, "mode": mode, "label": label, "best_s": 20.0, "points": point}
        for variant, mode, label in (
            ("ft4", "ft4", "FT4"),
            ("jtty", "jtty", "JTTY"),
            ("jtty-macro", "jtty", "JTTY M"),
            ("psk31-macro", "psk31", "PSK31 M"),
        )
    ]
    plots.qso_time_vs_snr({"contest_qso": {"curves": curves}}, tmp_path)
    assert drawn["qso_time_ru"] == ["FT4 (best 20 s)", "JTTY (best 20 s)"]
    assert drawn["qso_time_ru_macros"] == ["FT4 (best 20 s)", "JTTY M (best 20 s)", "PSK31 M (best 20 s)"]
    assert colors["qso_time_ru_macros"] == [plots.MODE_COLORS[m] for m in ("ft4", "jtty", "psk31")]
    assert colors["qso_time_ru"][:2] == [plots.MODE_COLORS[m] for m in ("ft4", "jtty")]


def test_character_error_chart_stops_at_50_percent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    limits: list[tuple[float, float]] = []

    def capture(figure: Any, directory: Path, name: str) -> list[Path]:
        limits.append(figure.axes[0].get_ylim())
        plt.close(figure)
        return []

    monkeypatch.setattr(plots, "_save", capture)
    points = [{"snr_db": -5.0, "cer_mean": 0.8}, {"snr_db": 0.0, "cer_mean": 0.1}]
    plots.cer_vs_snr({"cer": [{"mode": "jtty", "label": "JTTY free text", "points": points}]}, tmp_path)
    assert limits == [(0.0, 50.0)]
    assert "yRange: CER_AXIS" in report._SCRIPT and "const CER_AXIS = [0, 0.5];" in report._SCRIPT


def test_each_mode_has_one_color_in_the_charts_and_the_report() -> None:
    assert plots.MODE_COLORS == {
        "ft8": "#e34948",
        "ft4": "#008300",
        "jtty": "#2a78d6",
        "psk31": "#8a3fd1",
        "rtty": "#e08a00",
    }
    for mode, color in plots.MODE_COLORS.items():
        assert f"--{mode}: {color};" in report._STYLE
    assert "macro:" not in report._STYLE and "-macro" not in report._SCRIPT.split("const seriesColor")[1].split("\n")[0]


def test_qso_intro_names_jtty_macros_only_when_the_run_has_them() -> None:
    base = {"breakdown": [], "curves": [], "turnaround_s": 0.0}
    total = {
        "contest": "ru",
        "label": "x",
        "overs": 1,
        "units": 1,
        "unit": "frames",
        "info_bits": 1,
        "channel_bits": 1,
        "airtime_s": 1.0,
        "best_s": 1.0,
    }
    variants = {"jtty-macro": "JTTY macros", "rtty-macro": "RTTY macros", "psk31-macro": "PSK31 macros"}
    every = [{**total, "variant": variant, "label": label} for variant, label in variants.items()]
    with_macros = {"contest_qso": {**base, "totals": every}}
    without = {"contest_qso": {**base, "totals": every[1:]}}
    assert "JTTY, RTTY and PSK31 send a typical contest macro" in report._qso_section(with_macros)
    assert "This run has no JTTY macro data." not in report._qso_section(with_macros)
    assert "RTTY and PSK31 send a typical contest macro" in report._qso_section(without)
    assert "This run has no JTTY macro data." in report._qso_section(without)
    assert "JTTY, RTTY and PSK31 send" not in report._qso_section(without)
    assert 'data-chart="qso:ru:overs"' in report._qso_section(without)
    assert 'data-chart="qso:ru:macros"' in report._qso_section(without)


def test_censored_normal_recovers_mean_and_sd() -> None:
    rng = np.random.default_rng(4)
    true_mu, true_sd, floor = -18.0, 1.5, -17.0
    values = rng.normal(true_mu, true_sd, 4000)
    floored = values <= floor
    reported = np.where(floored, floor, values)
    fit = analysis.censored_normal(reported, floored, floor)
    assert fit is not None
    assert fit[0] == pytest.approx(true_mu, abs=0.1) and fit[1] == pytest.approx(true_sd, abs=0.1)
    naive = reported[~floored].mean()
    assert naive > true_mu + 1.0, "dropping floored reports biases the mean high"


def test_reported_stats_use_plain_mean_without_floored_reports() -> None:
    reported = np.array([-10.0, -11.0, -12.0, np.nan])
    result = analysis.reported_snr_stats(reported, np.zeros(4, bool), np.full(4, -11.5), -21.0)
    assert result["mean"] == pytest.approx(-11.0) and result["bias"] == pytest.approx(0.5)


def test_noise_rates_poisson_interval(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    with (tmp_path / "raw" / "noise.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=runner.NOISE_FIELDS)
        writer.writeheader()
        for chunk in range(120):
            writer.writerow(
                {
                    "mode": "jtty",
                    "chunk": chunk,
                    "seconds": 60.0,
                    "false_prints": int(chunk < 3),
                    "printed": "TU NOW K9ZZ" if chunk < 3 else "",
                    "decode_s": 0.1,
                    "error": "",
                }
            )
    (rate,) = analysis.noise_rates(tmp_path)
    assert rate["hours"] == pytest.approx(2.0) and rate["false_prints"] == 3
    assert rate["per_hour"] == pytest.approx(1.5)
    assert rate["per_hour_lo"] == pytest.approx(0.309, abs=0.01) and rate["per_hour_hi"] == pytest.approx(
        4.38, abs=0.02
    )


def _contest_rows(p_by_kind: dict[str, float]) -> list[dict[str, str]]:
    """Rows of JTTY contest trials whose copy rate is p_by_kind[kind] at every SNR from -20 to -5 dB."""
    rng = np.random.default_rng(6)
    rows = []
    for snr in np.arange(-20.0, -4.5, 1.0):
        for kind, p in p_by_kind.items():
            for _ in range(200):
                p_here = analysis.logistic([snr], -16.0, 0.8)[0] * p
                rows.append(
                    {
                        "mode": "jtty",
                        "cls": "contest",
                        "contest": "ru",
                        "msg_kind": kind,
                        "snr_db": str(float(snr)),
                        "copied": "1" if rng.random() < p_here else "0",
                    }
                )
    return rows


def test_contest_qso_best_and_median_time() -> None:
    reference = {
        "qso": {
            "ru": {
                "jtty": [
                    {
                        "kind": "call",
                        "sender": "sp",
                        "text": "W9XYZ",
                        "units": 1,
                        "airtime_s": 1.888,
                        "info_bits": 34,
                        "channel_bits": 118,
                    },
                    {
                        "kind": "exch_run",
                        "sender": "run",
                        "text": "W9XYZ 599 MA",
                        "units": 2,
                        "airtime_s": 3.776,
                        "info_bits": 68,
                        "channel_bits": 236,
                    },
                    {
                        "kind": "exch_sp",
                        "sender": "sp",
                        "text": "599 WI",
                        "units": 1,
                        "airtime_s": 1.888,
                        "info_bits": 34,
                        "channel_bits": 118,
                    },
                    {
                        "kind": "tu",
                        "sender": "run",
                        "text": "TU K1ABC CQ",
                        "units": 1,
                        "airtime_s": 1.888,
                        "info_bits": 34,
                        "channel_bits": 118,
                    },
                    {
                        "kind": "agn",
                        "sender": "sp",
                        "text": "AGN?",
                        "units": 1,
                        "airtime_s": 1.888,
                        "info_bits": 34,
                        "channel_bits": 118,
                    },
                ]
            }
        }
    }
    rows = _contest_rows({"call": 1.0, "exch_run": 1.0, "exch_sp": 1.0, "tu": 1.0, "agn": 1.0})
    result = analysis.contest_qso(rows, reference, turnaround=0.0)
    (total,) = result["totals"]
    assert total["overs"] == 4 and total["units"] == 5 and total["info_bits"] == 170
    assert total["best_s"] == pytest.approx(5 * 1.888)
    (curve,) = result["curves"]
    strong = next(p for p in curve["points"] if p["snr_db"] == -5.0)
    assert strong["median_s"] == pytest.approx(total["best_s"], rel=0.01)
    at = {p["snr_db"]: p["median_s"] for p in curve["points"]}
    p = float(analysis.logistic([-16.0], -16.0, 0.8)[0])
    mean = sum(t / p + (1 / p - 1) * 1.888 / p for t in (1.888, 3.776, 1.888, 1.888))
    assert total["best_s"] < at[-16.0] < mean, "the retry-time distribution is right-skewed, so median < mean"
    with_turnaround = analysis.contest_qso(rows, reference, turnaround=0.5)["totals"][0]["best_s"]
    assert with_turnaround == pytest.approx(5 * 1.888 + 4 * 0.5)


def test_median_qso_time_matches_the_geometric_median() -> None:
    # FT8-style over with p = 0.15: P(N <= 4) = 0.478 and P(N <= 5) = 0.556, so the median is 5 attempts = 9 slots.
    assert analysis._median_qso_time([0.15], [15.0], 15.0, 0.0, 1.0) == pytest.approx(9 * 15.0)
    # A reliable over with a reliable AGN? request costs exactly its airtime.
    assert analysis._median_qso_time([1.0], [2.0], None, 2.0, 1.0) == pytest.approx(2.0)
    # Keyboard over with p = 0.15 and a certain AGN?: median 5 attempts, 4 requests.
    assert analysis._median_qso_time([0.15], [2.0], None, 1.0, 1.0) == pytest.approx(5 * 2.0 + 4 * 1.0)


def test_strong_signal_failures_and_grid_phase_test() -> None:
    rows = []
    for trial in range(400):
        split = trial < 8
        phase = 0.01 if split else (trial % 100) / 100 + 0.005
        rows.append(
            {
                "mode": "jtty",
                "cls": "text",
                "snr_db": "-5.0",
                "trial": str(trial),
                "units": "5",
                "units_ok": "5",
                "copied": "0" if split else "1",
                "garbage": "0",
                "n_messages": "2" if split else "1",
                "failure": "split" if split else "",
                "grid_phase": str(phase),
                "start_s": "1.0",
                "sent": "X",
                "printed": "X",
            }
        )
    thresholds = [{"mode": "jtty", "series_cls": "text", "snr90_db": -14.7}]
    result = analysis.strong_signal_failures(rows, thresholds)
    (group,) = result["groups"]
    assert group["trials"] == 400 and group["n_split"] == 8 and group["per_1000"] == pytest.approx(20.0)
    test = result["grid_phase_test"]
    assert test["splits"] == 8 and test["splits_near"] == 8 and test["p_value"] < 1e-6
    macro_rows = [{**row, "cls": "contest-macro"} for row in rows]
    macro_thresholds = [{"mode": "jtty", "series_cls": "contest-macro", "snr90_db": -14.7}]
    assert analysis.strong_signal_failures(macro_rows, macro_thresholds)["grid_phase_test"] == {}


def test_failure_classes_are_derived_for_older_runs() -> None:
    old = {"copied": "0", "units": "5", "units_ok": "5", "garbage": "0", "n_messages": "2", "start_s": "1.0"}
    assert analysis._failure_of(old) == "split"
    assert analysis._failure_of(old | {"n_messages": "1"}) == "no_eom"
    assert analysis._failure_of(old | {"units_ok": "4", "garbage": "1"}) == "missed;false_text"
    assert analysis._grid_phase_of(old) == pytest.approx((12000 % 5664) / 5664)


def test_mode_table_has_every_parameter() -> None:
    (jtty,) = analysis.mode_table(["jtty"], {"mode_info_measured": {"jtty": {"bw99_hz": 126.0}}})
    assert jtty["tones"] == 4 and jtty["baud"] == pytest.approx(31.25) and jtty["measured_bw99_hz"] == 126.0
    assert jtty["net_bit_rate"] == pytest.approx(34 / 1.888) and jtty["slot_s"] is None
    (psk31,) = analysis.mode_table(["psk31"], {"mode_info_measured": {"psk31": {"bw99_hz": 45.0}}})
    assert psk31["baud"] == pytest.approx(31.25) and psk31["gross_bit_rate"] == pytest.approx(31.25)
    assert psk31["net_bit_rate"] == pytest.approx(5 * 31.25 / modes.PSK31_CHAR_BITS)


def test_new_rows_keep_their_recorded_failure() -> None:
    copied_macro = {
        "copied": "1",
        "garbage": "2",
        "units": "1",
        "units_ok": "1",
        "n_messages": "0",
        "failure": "",
        "airtime_s": "4.4",
        "grid_phase": "",
    }
    assert analysis._failure_of(copied_macro) == ""
