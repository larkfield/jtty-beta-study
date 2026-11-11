"""Turning raw trials into tables: decode rates with confidence intervals, thresholds, SNR calibration.

Statistics:
- 5-character decode % is 5-character groups copied / groups sent at each SNR, for every mode (scoring.group_counts;
  five characters is a callsign's worth, and an FT8/FT4 message gives all its groups or none). Groups within one trial
  are not independent, so their 95% intervals come from a bootstrap over trials; every per-trial proportion (message
  copy) uses Wilson score intervals. Contest-macro series count one unit per whole macro.
- Decode levels: the SNR at which each measured curve reaches 10%, 20%, ... 90%, read by linear interpolation from
  the curve fitted to never fall as SNR rises. A level not reached by +10 dB is left blank. The headline level
  (HEADLINE_LEVEL, 80% unless analyze is given another) is each mode's threshold; its 95% interval comes from
  repeating the reading on bootstrap resamples of trials.
- Logistic fit: a maximum-likelihood fit of unit success vs SNR, p = 1/(1+exp(-(SNR-mu)/w)), over the transition
  region, with 50% at mu and 90% at mu + w*ln 9. It sets the strong-signal failure cut and the contest-QSO model.
- Eb/N0 = SNR + 10*log10(2500 Hz / net information rate).
- Reported SNR: mean and SD of what the decoder printed, and its bias against the measured true SNR. A decoder never
  reports below its floor, so where reports hit it they are treated as left-censored and the mean and SD come from
  a censored-normal maximum-likelihood fit; elsewhere they are the plain sample statistics.
- Noise-only false prints: per hour of band noise with exact Poisson 95% intervals.
- Contest QSOs: per-over copy probabilities come from logistic fits of the measured contest trials of that over,
  combined into a best-case and a median time (failed overs cost an AGN? request plus the repeat, or a full T/R
  cycle for FT8/FT4). The median comes from simulating the retry process, not from summing per-over means.
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
from scipy import optimize, special, stats

from jtty_snr_study import modes as mode_info
from jtty_snr_study.messages import CONTEST_LABELS, QSO_KINDS
from jtty_snr_study.psk31 import BAUD as PSK31_BAUD
from jtty_snr_study.psk31 import encode as psk31_encode
from jtty_snr_study.rtty import BAUD
from jtty_snr_study.runner import monotone_rates
from jtty_snr_study.scoring import UNIT_SCORING

MODE_LABELS: dict[str, str] = {"ft8": "FT8", "ft4": "FT4", "jtty": "JTTY", "psk31": "PSK31", "rtty": "RTTY"}
"""Display names, in the study's fixed mode order."""

UNIT_NAMES: dict[str, str] = {mode: "5-char groups" for mode in MODE_LABELS}
"""What one scoring unit is for each mode."""

NET_BIT_RATE: dict[str, float] = {
    "jtty": 34.0 / 1.888,
    "ft8": 77.0 / 12.64,
    "ft4": 77.0 / 5.04,
    "rtty": BAUD * 5.0 / 7.5,
    "psk31": mode_info.MODES["psk31"].net_bit_rate,
}
"""Net information rate (bits/s): JTTY 34-bit atom per 1.888 s frame, FT8/FT4 77 bits per transmission, RTTY five
data bits per 7.5-bit character, PSK31 five bits per character at the mean Varicode length of lower-case text."""

BOOTSTRAP_ROUNDS: int = 400
"""Resamples for bootstrap intervals."""

FRAME_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (1, 1, "1 frame"),
    (2, 2, "2 frames"),
    (3, 5, "3-5 frames"),
    (6, 10, "6-10 frames"),
    (11, 99, "11+ frames"),
)
"""JTTY message lengths for the copy-by-length breakdown."""


@dataclass
class Trials:
    """Raw rows of one group of trials (one mode/class selection), as arrays keyed by SNR."""

    snr: npt.NDArray[np.float64]
    units: npt.NDArray[np.int64]
    units_ok: npt.NDArray[np.int64]
    copied: npt.NDArray[np.int64]
    cer: npt.NDArray[np.float64]
    garbage: npt.NDArray[np.int64]
    noise_prints: npt.NDArray[np.int64]
    snr_measured: npt.NDArray[np.float64]
    snr_reported: npt.NDArray[np.float64]
    snr_floor: npt.NDArray[np.bool_]
    decode_s: npt.NDArray[np.float64]
    airtime: npt.NDArray[np.float64]
    copied_chars: npt.NDArray[np.float64]

    @classmethod
    def from_rows(cls, rows: Sequence[dict[str, str]]) -> Trials:
        """Build arrays from CSV rows (rows with errors must already be excluded)."""

        def column(name: str, kind: type) -> npt.NDArray[Any]:
            return np.array([kind(row[name]) for row in rows], dtype=kind)

        reported = np.array([float(row["snr_reported_db"]) if row["snr_reported_db"] else np.nan for row in rows])
        return cls(
            snr=column("snr_db", float),
            units=column("units", int),
            units_ok=column("units_ok", int),
            copied=column("copied", int),
            cer=column("cer", float),
            garbage=column("garbage", int),
            noise_prints=column("noise_prints", int),
            snr_measured=column("snr_true_measured_db", float),
            snr_reported=reported,
            snr_floor=np.array([row["snr_reported_floor"] == "1" for row in rows], dtype=bool),
            decode_s=column("decode_s", float),
            airtime=np.array([_float_or_nan(row.get("airtime_s", "")) for row in rows]),
            copied_chars=np.array([_float_or_nan(row.get("copied_chars", "")) for row in rows]),
        )

    def grid(self) -> list[float]:
        """Return the distinct SNRs, ascending."""
        return sorted({float(s) for s in self.snr})

    def at(self, snr: float) -> npt.NDArray[np.bool_]:
        """Return the mask of trials at one SNR."""
        return np.isclose(self.snr, snr)


def _float_or_nan(value: str | None) -> float:
    """Parse a CSV number, NaN when empty (columns that older runs lack)."""
    return float(value) if value not in (None, "") else math.nan


def wilson(successes: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Return the Wilson score 95% interval for a binomial proportion (0, 0 when n is 0)."""
    if n == 0:
        return 0.0, 0.0
    p = successes / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def bootstrap_ratio(
    ok: npt.NDArray[np.int64], units: npt.NDArray[np.int64], rng: np.random.Generator
) -> tuple[float, float]:
    """Return a 95% percentile interval for sum(ok)/sum(units), resampling trials (clusters of units).

    At 0% or 100% the bootstrap has no spread, so the Wilson interval over the number of trials is used.
    """
    if len(ok) == 0:
        return 0.0, 0.0
    if ok.sum() == 0 or ok.sum() == units.sum():
        # A percentile bootstrap collapses to zero width at 0% and 100%; bound by trials instead (Wilson).
        return wilson(len(ok) if ok.sum() else 0, len(ok))
    index = rng.integers(0, len(ok), size=(BOOTSTRAP_ROUNDS, len(ok)))
    ratios = ok[index].sum(axis=1) / np.maximum(units[index].sum(axis=1), 1)
    low, high = np.percentile(ratios, [2.5, 97.5])
    return float(low), float(high)


def logistic(snr: npt.ArrayLike, mu: float, width: float) -> npt.NDArray[np.float64]:
    """Return the logistic decode-probability curve at the given SNRs."""
    return special.expit((np.asarray(snr, dtype=np.float64) - mu) / width)


def fit_logistic(snr: npt.ArrayLike, ok: npt.ArrayLike, n: npt.ArrayLike) -> tuple[float, float] | None:
    """Return the maximum-likelihood (mu, width) of a logistic fit to aggregated binomial counts, or None.

    Only the transition region is fitted (2 dB beyond the outermost points between 2% and 98%), so flat tails do
    not dominate. Returns None when there is no transition to fit.
    """
    s = np.asarray(snr, dtype=np.float64)
    k = np.asarray(ok, dtype=np.float64)
    total = np.asarray(n, dtype=np.float64)
    keep = total > 0
    s, k, total = s[keep], k[keep], total[keep]
    if len(s) < 2:
        return None
    p = k / total
    middle = s[(p > 0.02) & (p < 0.98)]
    if len(middle) == 0:
        below = s[p <= 0.5]
        above = s[p > 0.5]
        if len(below) == 0 or len(above) == 0:
            return None
        middle = np.array([below.max(), above.min()])
    region = (s >= middle.min() - 2.0) & (s <= middle.max() + 2.0)
    s, k, total = s[region], k[region], total[region]

    def negative_log_likelihood(params: npt.NDArray[np.float64]) -> float:
        mu, log_width = params
        logits = (s - mu) / math.exp(log_width)
        return float(-np.sum(k * -np.logaddexp(0.0, -logits) + (total - k) * -np.logaddexp(0.0, logits)))

    start = interpolate_crossing(s, k / total, 0.5)
    start_mu = start if start is not None else float(np.mean(s))
    result = optimize.minimize(
        negative_log_likelihood,
        x0=[start_mu, 0.0],
        method="Nelder-Mead",
        options={"xatol": 1e-4, "fatol": 1e-6, "maxiter": 4000, "maxfev": 8000},
    )
    if not np.all(np.isfinite(result.x)) or not math.isfinite(float(result.fun)):
        return None
    mu, log_width = (float(v) for v in result.x)
    width = math.exp(log_width)
    if not (s.min() - 10 < mu < s.max() + 10 and 0.01 < width < 20):
        return None
    return mu, width


DECODE_LEVELS: tuple[int, ...] = (10, 20, 30, 40, 50, 60, 70, 80, 90)
"""Decode percentages read from each measured curve for the decode-level table."""

HEADLINE_LEVEL: int = 80
"""The default 5-character decode percentage reported as each mode's threshold."""

DECODE_LEVEL_CEILING_DB: float = 10.0
"""A decode level counts as reached only at or below this SNR (the top of the study's sweep)."""


def interpolate_crossing(snr: npt.ArrayLike, p: npt.ArrayLike, level: float) -> float | None:
    """Return the SNR where the measured curve first rises through ``level`` (linear interpolation), or None."""
    s = np.asarray(snr, dtype=np.float64)
    values = np.asarray(p, dtype=np.float64)
    order = np.argsort(s)
    s, values = s[order], values[order]
    for i in range(1, len(s)):
        if values[i - 1] < level <= values[i]:
            return float(s[i - 1] + (level - values[i - 1]) * (s[i] - s[i - 1]) / (values[i] - values[i - 1]))
    return None


def measured_level_snr(grid: Sequence[float], ok: Sequence[int], units: Sequence[int], level: float) -> float | None:
    """Return the SNR where a measured decode curve reaches ``level`` (a fraction), or None if it cannot be read.

    The curve is first fitted to never fall as SNR rises, so one chance result cannot move the reading. A curve that
    is exactly at the level at its lowest SNR reads as that SNR. None means the level is not reached by
    DECODE_LEVEL_CEILING_DB, or the curve is already above it at the lowest measured SNR.
    """
    rates = monotone_rates({float(s): (k, n, 0) for s, k, n in zip(grid, ok, units, strict=True)})
    snrs = sorted(rates)
    crossing = (
        snrs[0] if snrs and rates[snrs[0]] == level else interpolate_crossing(snrs, [rates[s] for s in snrs], level)
    )
    return crossing if crossing is not None and crossing <= DECODE_LEVEL_CEILING_DB else None


def ebn0_offset_db(mode: str, char_bits: float | None = None) -> float:
    """Return Eb/N0 minus SNR (2500 Hz reference) for a mode, in dB.

    PSK31's character time depends on the text: ``char_bits`` (mean Varicode bits per character, gap included) sets
    its net rate for a particular series; without it the lower-case text average of the mode table is used.
    """
    if mode == "psk31" and char_bits:
        return 10.0 * math.log10(2500.0 / (5.0 * PSK31_BAUD / char_bits))
    return 10.0 * math.log10(2500.0 / NET_BIT_RATE[mode])


def psk31_char_bits(rows: Iterable[dict[str, str]]) -> float | None:
    """Return the mean Varicode bits per character (gap included) of the text the given PSK31 trials sent, or None.

    Text trials record their lower-case text and contest trials their capitals, so each series gets its own basis.
    Macro trials record their text with CR/LF folded to spaces, which shortens it by a few bits per over.
    """
    bits = chars = 0
    for row in rows:
        sent = row.get("sent", "")
        if sent:
            bits += len(psk31_encode(sent))
            chars += len(sent)
    return bits / chars if chars else None


def censored_normal(values: npt.ArrayLike, floored: npt.ArrayLike, floor: float) -> tuple[float, float] | None:
    """Return the maximum-likelihood (mean, SD) of normal data left-censored at ``floor``, or None.

    ``values`` are the reports; ``floored`` marks those clamped at the floor, which contribute P(X <= floor) instead
    of a density. Needs at least two uncensored values.
    """
    x = np.asarray(values, dtype=np.float64)
    censored = np.asarray(floored, dtype=bool)
    exact = x[~censored]
    n_censored = int(censored.sum())
    if len(exact) < 2:
        return None

    def negative_log_likelihood(params: npt.NDArray[np.float64]) -> float:
        mu, log_sd = params
        sd = math.exp(log_sd)
        return float(-(np.sum(stats.norm.logpdf(exact, mu, sd)) + n_censored * stats.norm.logcdf((floor - mu) / sd)))

    start = [float(np.mean(x)), math.log(max(float(np.std(x)), 0.5))]
    result = optimize.minimize(
        negative_log_likelihood, x0=start, method="Nelder-Mead", options={"xatol": 1e-4, "fatol": 1e-6, "maxiter": 4000}
    )
    if not np.all(np.isfinite(result.x)):
        return None
    mu, log_sd = (float(v) for v in result.x)
    if not floor - 20 < mu < floor + 60:
        return None
    return mu, math.exp(log_sd)


def reported_snr_stats(
    reported: npt.NDArray[np.float64],
    floored: npt.NDArray[np.bool_],
    measured: npt.NDArray[np.float64],
    floor: float | None,
) -> dict[str, float]:
    """Return mean, SD and bias (mean minus measured true SNR) of the reported SNRs of a set of trials.

    Floored reports are treated as censored (censored-normal fit); with none at the floor these are plain sample
    statistics. NaN where there are too few reports.
    """
    have = ~np.isnan(reported)
    values, at_floor, truth = reported[have], floored[have], measured[have]
    mean = sd = math.nan
    if floor is not None and at_floor.any():
        fit = censored_normal(values, at_floor, floor)
        if fit is not None:
            mean, sd = fit
    elif len(values):
        mean = float(values.mean())
        sd = float(values.std(ddof=1)) if len(values) > 1 else math.nan
    bias = mean - float(truth.mean()) if len(truth) and math.isfinite(mean) else math.nan
    return {"mean": mean, "sd": sd, "bias": bias}


def _point_summary(
    trials: Trials, snr: float, unit_ci: str, rng: np.random.Generator, floor: float | None
) -> dict[str, Any]:
    """Return the summary statistics of all trials at one SNR."""
    mask = trials.at(snr)
    n = int(mask.sum())
    ok, units = trials.units_ok[mask], trials.units[mask]
    decode = float(ok.sum() / max(units.sum(), 1))
    if unit_ci == "wilson":
        decode_lo, decode_hi = wilson(int(ok.sum()), int(units.sum()))
    else:
        decode_lo, decode_hi = bootstrap_ratio(ok, units, rng)
    copies = int(trials.copied[mask].sum())
    copy_lo, copy_hi = wilson(copies, n)
    garbage = int(trials.garbage[mask].sum())

    reported = trials.snr_reported[mask]
    floored = trials.snr_floor[mask]
    measured = trials.snr_measured[mask]
    snr_stats = reported_snr_stats(reported, floored, measured, floor)
    airtime = trials.airtime[mask]
    chars = trials.copied_chars[mask]
    throughput = (
        60.0 * float(np.nansum(chars)) / float(np.nansum(airtime))
        if n and np.isfinite(airtime).any() and np.nansum(airtime) > 0
        else math.nan
    )
    return {
        "snr_db": snr,
        "n_trials": n,
        "units": int(units.sum()),
        "units_ok": int(ok.sum()),
        "decode": decode,
        "decode_lo": decode_lo,
        "decode_hi": decode_hi,
        "copy": copies / max(n, 1),
        "copy_lo": copy_lo,
        "copy_hi": copy_hi,
        "cer_mean": float(trials.cer[mask].mean()) if n else math.nan,
        "garbage_per_1000": 1000.0 * garbage / max(n, 1),
        "garbage_upper_per_1000": 3000.0 / max(n, 1) if garbage == 0 else math.nan,
        "noise_prints_mean": float(trials.noise_prints[mask].mean()) if n else math.nan,
        "throughput_cpm": throughput,
        "snr_true_measured_mean": float(measured.mean()) if n else math.nan,
        "snr_reported_mean": snr_stats["mean"],
        "snr_reported_sd": snr_stats["sd"],
        "snr_bias_mean": snr_stats["bias"],
        "n_reported": int((~np.isnan(reported)).sum()),
        "n_reported_floor": int(floored.sum()),
        "decode_s_mean": float(trials.decode_s[mask].mean()) if n else math.nan,
    }


def _threshold(
    mode: str,
    label: str,
    trials: Trials,
    rng: np.random.Generator,
    floor: float | None,
    level: int,
    ebn0_offset: float | None = None,
) -> tuple[dict[str, Any], tuple[float, float] | None]:
    """Return the threshold row for one series (measured decode levels, logistic points) and its logistic fit.

    ``level`` is the headline decode percentage, read into the ``headline_*`` columns. ``ebn0_offset`` (Eb/N0 minus
    SNR, dB) defaults to the mode's (ebn0_offset_db).
    """
    grid = trials.grid()
    ok = [int(trials.units_ok[trials.at(s)].sum()) for s in grid]
    units = [int(trials.units[trials.at(s)].sum()) for s in grid]
    fit = fit_logistic(grid, ok, units)
    levels = {decode: measured_level_snr(grid, ok, units, decode / 100.0) for decode in DECODE_LEVELS}
    headline_snr = measured_level_snr(grid, ok, units, level / 100.0)
    row: dict[str, Any] = {
        "series": label,
        "mode": mode,
        "unit": UNIT_NAMES[mode],
        "points": len(grid),
        "trials": len(trials.snr),
        "units_sent": int(trials.units.sum()),
        "headline_level": level,
        "headline_db": math.nan if headline_snr is None else headline_snr,
        "headline_lo": math.nan,
        "headline_hi": math.nan,
        "ebn0_headline_db": math.nan,
        "reported_snr_at_headline_db": math.nan,
        **{f"decode_{decode}_db": math.nan if snr is None else snr for decode, snr in levels.items()},
        "snr50_db": math.nan,
        "snr50_lo": math.nan,
        "snr50_hi": math.nan,
        "snr90_db": math.nan,
        "snr90_lo": math.nan,
        "snr90_hi": math.nan,
        "width_db": math.nan,
    }
    if fit is None and headline_snr is None:
        return row, None

    ln9 = math.log(9.0)
    mus: list[float] = []
    p90s: list[float] = []
    headline_samples: list[float] = []
    groups = [np.flatnonzero(trials.at(s)) for s in grid]
    rounds = BOOTSTRAP_ROUNDS // 2
    for _ in range(rounds):
        sample_ok, sample_units = [], []
        for members in groups:
            pick = rng.choice(members, size=len(members), replace=True)
            sample_ok.append(int(trials.units_ok[pick].sum()))
            sample_units.append(int(trials.units[pick].sum()))
        if headline_snr is not None:
            crossing = measured_level_snr(grid, sample_ok, sample_units, level / 100.0)
            if crossing is not None:
                headline_samples.append(crossing)
        refit = fit_logistic(grid, sample_ok, sample_units) if fit is not None else None
        if refit is not None:
            mus.append(refit[0])
            p90s.append(refit[0] + refit[1] * ln9)

    if headline_snr is not None:
        nearest = min(grid, key=lambda s: abs(s - headline_snr))
        mask = trials.at(nearest)
        at_level = reported_snr_stats(
            trials.snr_reported[mask], trials.snr_floor[mask], trials.snr_measured[mask], floor
        )
        row.update(
            ebn0_headline_db=headline_snr + (ebn0_offset if ebn0_offset is not None else ebn0_offset_db(mode)),
            reported_snr_at_headline_db=at_level["mean"],
        )
        # An interval is only honest if nearly every resample reached the level; otherwise its top end is open.
        if len(headline_samples) >= 0.975 * rounds:
            row.update(
                headline_lo=float(np.percentile(headline_samples, 2.5)),
                headline_hi=float(np.percentile(headline_samples, 97.5)),
            )

    if fit is None:
        return row, None

    mu, width = fit
    row.update(
        snr50_db=mu,
        snr90_db=mu + width * ln9,
        width_db=width,
    )
    if len(mus) >= 20:
        row.update(
            snr50_lo=float(np.percentile(mus, 2.5)),
            snr50_hi=float(np.percentile(mus, 97.5)),
            snr90_lo=float(np.percentile(p90s, 2.5)),
            snr90_hi=float(np.percentile(p90s, 97.5)),
        )
    return row, fit


def load_rows(run_dir: Path) -> list[dict[str, str]]:
    """Return the run's raw rows, excluding trials that ended in a decoder error."""
    path = run_dir / "raw" / "trials.csv"
    if not path.exists():
        raise SystemExit(f"no raw data at {path}; run the sweep first")
    with path.open(newline="", encoding="utf-8") as stream:
        return [row for row in csv.DictReader(stream) if not row.get("error")]


def _load_json(path: Path) -> dict[str, Any]:
    """Return a JSON file's content, or an empty dict if it does not exist."""
    return json.loads(path.read_text()) if path.exists() else {}


def _select(
    rows: Iterable[dict[str, str]],
    mode: str,
    classes: Sequence[str] | None = None,
    frames: tuple[int, int] | None = None,
) -> list[dict[str, str]]:
    """Return the rows of one mode, optionally some classes and a range of JTTY frame counts (the frames column)."""
    return [
        row
        for row in rows
        if row["mode"] == mode
        and (classes is None or row["cls"] in classes)
        and (frames is None or _frames_in(row, frames))
    ]


def _frames_in(row: dict[str, str], frames: tuple[int, int]) -> bool:
    """Return whether a row's JTTY frame count is within ``frames`` (rows with no count never are)."""
    count = row.get("frames") or ""
    return count.isdigit() and frames[0] <= int(count) <= frames[1]


def _series(key: str, label: str, mode: str, points: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    """Return one chart series in the analysis.json layout."""
    return {"key": key, "label": label, "mode": mode, "unit": UNIT_NAMES[mode], "points": points, **extra}


CLASS_LABELS: dict[str, str] = {
    "all": "contest + text",
    "contest": "contest overs",
    "text": "free text",
    "qso": "QSO messages",
    "contest-macro": "contest macros",
}
"""Display names of message classes and selections."""

HEADLINE_CLASSES: dict[str, tuple[str, ...]] = {
    "jtty": ("contest", "text"),
    "rtty": ("contest", "text"),
    "psk31": ("contest", "text"),
}
"""Classes pooled into a mode's headline curve (the contest-macro variants are reported on their own)."""

KEYBOARD_MODES: tuple[str, ...] = ("rtty", "psk31")
"""The character-by-character modes: no squelch, contest macros."""

QSO_UNITS: dict[str, str] = {"jtty": "frames", "rtty": "Baudot characters", "psk31": "Varicode characters"}
"""What a contest QSO's units count, per mode (FT8/FT4: messages)."""

NEAR_BOUNDARY: float = 0.03
"""A JTTY transmission whose start is within this fraction of the decoder's search step of a window boundary."""

STRONG_MARGIN_DB: float = 3.0
"""Strong-signal failures are counted at SNRs at least this far above a series' 90% point."""

QSO_SNR_COLUMNS: tuple[float, ...] = (-20.0, -17.0, -14.0, -11.0, -8.0, -5.0, 0.0)
"""SNRs at which the contest-QSO time table is tabulated."""


def _failure_of(row: dict[str, str]) -> str:
    """Return a trial's failure classes, deriving them for runs made before the failure column existed."""
    if row.get("airtime_s") not in (None, ""):
        return row.get("failure", "")
    if row.get("copied") == "1" and int(row.get("garbage") or 0) == 0:
        return ""
    parts = []
    if int(row["units_ok"]) < int(row["units"]):
        parts.append("missed")
    elif row.get("copied") != "1" and int(row.get("n_messages") or 0) > 1:
        parts.append("split")
    elif row.get("copied") != "1" and int(row.get("garbage") or 0) == 0:
        parts.append("no_eom")
    if int(row.get("garbage") or 0) > 0:
        parts.append("false_text")
    return ";".join(parts)


def _grid_phase_of(row: dict[str, str]) -> float:
    """Return a JTTY trial's start offset within the decoder's search step (0-1), from start_s for older runs."""
    if row.get("grid_phase"):
        return float(row["grid_phase"])
    if not row.get("start_s"):
        return math.nan
    step = 59 * 384 // 4
    return (round(float(row["start_s"]) * 12000) % step) / step


FAILURE_CLASSES: tuple[str, ...] = ("missed", "split", "no_eom", "ambiguous", "false_text")
"""Failure classes counted in the strong-signal table (scoring.TrialScore.failure)."""


def strong_signal_failures(rows: Sequence[dict[str, str]], thresholds: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Return failures at strong signal (>= each series' 90% point + STRONG_MARGIN_DB), by type, with examples.

    For JTTY split messages it also tests whether they start near the decoder's search-window boundaries: the share
    of splits within NEAR_BOUNDARY of a boundary against the share of all strong-signal trials (one-sided binomial).
    """
    p90 = {(t["mode"], t["series_cls"]): t["snr90_db"] for t in thresholds if t.get("series_cls")}
    order = list(MODE_LABELS)
    groups: list[dict[str, Any]] = []
    unmeasured: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []
    jtty_strong: list[dict[str, str]] = []
    for (mode, cls), level in sorted(p90.items(), key=lambda item: (order.index(item[0][0]), item[0][1])):
        if level is None or not math.isfinite(level):
            unmeasured.append({"mode": mode, "cls": cls, "from_snr_db": None})
            continue
        strong = [
            r for r in rows if r["mode"] == mode and r["cls"] == cls and float(r["snr_db"]) >= level + STRONG_MARGIN_DB
        ]
        if not strong:
            unmeasured.append({"mode": mode, "cls": cls, "from_snr_db": level + STRONG_MARGIN_DB})
            continue
        if mode == "jtty" and cls != "contest-macro":
            jtty_strong += strong
        counts: dict[str, int] = defaultdict(int)
        failed = 0
        for row in strong:
            failure = _failure_of(row)
            if failure:
                failed += 1
                for part in failure.split(";"):
                    counts[part] += 1
                if len(examples) < 40:
                    examples.append(
                        {
                            "mode": mode,
                            "cls": cls,
                            "snr_db": float(row["snr_db"]),
                            "trial": int(row["trial"]),
                            "failure": failure,
                            "grid_phase": _grid_phase_of(row) if mode == "jtty" else None,
                            "sent": row["sent"],
                            "printed": row["printed"],
                        }
                    )
        groups.append(
            {
                "mode": mode,
                "cls": cls,
                "from_snr_db": level + STRONG_MARGIN_DB,
                "trials": len(strong),
                "failed": failed,
                "per_1000": 1000.0 * failed / len(strong),
                **{f"n_{name}": counts.get(name, 0) for name in FAILURE_CLASSES},
            }
        )

    def near_boundary(row: dict[str, str]) -> bool:
        phase = _grid_phase_of(row)
        return abs(phase - round(phase)) < NEAR_BOUNDARY

    phase_test: dict[str, Any] = {}
    jtty_strong = [r for r in jtty_strong if math.isfinite(_grid_phase_of(r))]
    if jtty_strong:
        baseline = sum(near_boundary(r) for r in jtty_strong) / len(jtty_strong)
        splits = [r for r in jtty_strong if "split" in _failure_of(r)]
        near_splits = sum(1 for r in splits if near_boundary(r))
        p_value = (
            float(stats.binomtest(near_splits, len(splits), baseline, alternative="greater").pvalue)
            if splits and 0 < baseline < 1
            else math.nan
        )
        phase_test = {
            "strong_trials": len(jtty_strong),
            "baseline_near_share": baseline,
            "splits": len(splits),
            "splits_near": near_splits,
            "p_value": p_value,
            "window": NEAR_BOUNDARY,
        }
    return {"groups": groups, "unmeasured": unmeasured, "examples": examples, "grid_phase_test": phase_test}


def unmeasured_text(unmeasured: Sequence[dict[str, Any]]) -> str:
    """Return the series left out of the strong-signal table, each with the SNR it needed or why it has none."""
    return "; ".join(
        f"{MODE_LABELS[u['mode']]} {CLASS_LABELS.get(u['cls'], u['cls'])} "
        + (f"(from {u['from_snr_db']:+.1f} dB)" if u["from_snr_db"] is not None else "(no 90% point found)")
        for u in unmeasured
    )


def noise_rates(run_dir: Path) -> list[dict[str, Any]]:
    """Return false prints per hour of pure band noise per mode (RTTY, PSK31: printed characters per minute)."""
    path = run_dir / "raw" / "noise.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        rows = [row for row in csv.DictReader(stream) if not row.get("error")]
    out = []
    for mode in MODE_LABELS:
        mine = [row for row in rows if row["mode"] == mode]
        if not mine:
            continue
        hours = sum(float(row["seconds"]) for row in mine) / 3600.0
        prints = sum(int(row["false_prints"]) for row in mine)
        low = stats.chi2.ppf(0.025, 2 * prints) / 2 / hours if prints else 0.0
        high = stats.chi2.ppf(0.975, 2 * prints + 2) / 2 / hours
        examples = [row["printed"] for row in mine if row["printed"]][:6]
        out.append(
            {
                "mode": mode,
                "hours": hours,
                "false_prints": prints,
                "unit": "characters"
                if mode in KEYBOARD_MODES
                else ("decodes" if mode in ("ft8", "ft4") else "messages"),
                "per_hour": prints / hours,
                "per_hour_lo": float(low),
                "per_hour_hi": float(high),
                "per_minute": prints / hours / 60.0,
                "examples": examples,
            }
        )
    return out


def _copy_curve(rows: Sequence[dict[str, str]]) -> tuple[list[float], list[int], list[int]]:
    """Return (SNRs, copies, trials) of a set of trials."""
    grid = sorted({float(r["snr_db"]) for r in rows})
    copies = [sum(1 for r in rows if math.isclose(float(r["snr_db"]), s) and r["copied"] == "1") for s in grid]
    totals = [sum(1 for r in rows if math.isclose(float(r["snr_db"]), s)) for s in grid]
    return grid, copies, totals


def _copy_probability(rows: Sequence[dict[str, str]]):
    """Return a function SNR -> copy probability for one over, from a logistic fit (or the measured curve)."""
    grid, copies, totals = _copy_curve(rows)
    if not grid:
        return None
    fit = fit_logistic(grid, copies, totals)
    if fit is not None:
        mu, width = fit
        return lambda snr: float(logistic([snr], mu, width)[0])
    rates = [c / t for c, t in zip(copies, totals, strict=True)]
    return lambda snr: float(np.interp(snr, grid, rates))


QSO_VARIANTS: dict[str, tuple[str, str, str]] = {
    "ft8": ("ft8", "qso", "FT8"),
    "ft4": ("ft4", "qso", "FT4"),
    "jtty": ("jtty", "contest", "JTTY"),
    "jtty-macro": ("jtty", "contest-macro", "JTTY macros"),
    "psk31": ("psk31", "contest", "PSK31"),
    "psk31-macro": ("psk31", "contest-macro", "PSK31 macros"),
    "rtty": ("rtty", "contest", "RTTY"),
    "rtty-macro": ("rtty", "contest-macro", "RTTY macros"),
}
"""Contest-QSO variants: (mode, trial class, label)."""


QSO_TIME_SAMPLES = 20000
"""Simulated QSOs per SNR point when estimating the median completion time."""

QSO_TIME_SEED = 20261105
"""Seed of the QSO-time simulation; reused at every SNR so the curves are smooth and reproducible."""


def _median_qso_time(
    ps: Sequence[float],
    airtimes: Sequence[float],
    slot: float | None,
    agn_time: float,
    p_agn: float,
) -> float:
    """Return the median time to complete a QSO whose overs copy with probabilities ``ps``.

    Each over is repeated until it copies, so its number of attempts is geometric. FT8/FT4 (``slot`` set) spend one
    T/R slot per attempt plus a lost cycle (two slots) per failure. JTTY/RTTY/PSK31 spend the over's airtime per
    attempt, plus an AGN? request costing ``agn_time`` after every failure, itself repeated until it copies
    (probability ``p_agn``). The overs are independent; the same random stream is used on every call so neighbouring
    SNRs differ only through the probabilities.
    """
    rng = np.random.default_rng(QSO_TIME_SEED)
    total = np.zeros(QSO_TIME_SAMPLES)
    for p, airtime in zip(ps, airtimes, strict=True):
        attempts = rng.geometric(p, QSO_TIME_SAMPLES)
        failures = attempts - 1
        if slot is not None:
            total += slot * (2 * attempts - 1)
            continue
        requests = failures.copy()
        pending = failures > 0
        requests[pending] += rng.negative_binomial(failures[pending], p_agn)
        total += airtime * attempts + agn_time * requests
    return float(np.median(total))


def contest_qso(rows: Sequence[dict[str, str]], reference: dict[str, Any], turnaround: float) -> dict[str, Any]:
    """Return the reference contest QSOs' per-over bits and airtimes, and median completion time vs SNR."""
    qsos = reference.get("qso", {})
    breakdown: list[dict[str, Any]] = []
    totals: list[dict[str, Any]] = []
    curves: list[dict[str, Any]] = []
    all_snrs = sorted({float(r["snr_db"]) for r in rows})
    snr_axis = np.arange(min(all_snrs), max(all_snrs) + 0.25, 0.5) if all_snrs else np.array([])
    for contest, per_mode in qsos.items():
        for variant, (mode, cls, label) in QSO_VARIANTS.items():
            overs = per_mode.get(variant)
            if not overs:
                continue
            family = "keyboard" if mode in ("jtty", *KEYBOARD_MODES) else f"ft-{contest}"
            kinds = QSO_KINDS[family]
            used = [o for o in overs if o["kind"] in kinds]
            for over in overs:
                breakdown.append(
                    {
                        "contest": contest,
                        "variant": variant,
                        "label": label,
                        "in_qso": over["kind"] in kinds,
                        **{
                            k: over.get(k)
                            for k in ("kind", "sender", "text", "units", "info_bits", "channel_bits", "airtime_s")
                        },
                    }
                )
            slot = mode_info.MODES[mode].slot_s
            airtime = sum(o["airtime_s"] for o in used)
            best = len(used) * slot if slot is not None else airtime + len(used) * turnaround
            totals.append(
                {
                    "contest": contest,
                    "variant": variant,
                    "label": label,
                    "overs": len(used),
                    "units": sum(o["units"] for o in used),
                    "unit": QSO_UNITS.get(mode, "messages"),
                    "info_bits": sum(o["info_bits"] for o in used),
                    "channel_bits": sum(o["channel_bits"] for o in used),
                    "airtime_s": sum(o["airtime_s"] for o in used),
                    "best_s": best,
                }
            )

            mine = [r for r in rows if r["mode"] == mode and r["cls"] == cls and r.get("contest") == contest]
            probability = {k: _copy_probability([r for r in mine if r.get("msg_kind") == k]) for k in kinds}
            agn = next((o for o in overs if o["kind"] == "agn"), None)
            p_agn = _copy_probability([r for r in mine if r.get("msg_kind") == "agn"]) if agn else None
            if any(p is None for p in probability.values()) or (slot is None and p_agn is None):
                continue
            points = []
            for snr in snr_axis:
                ps = [probability[o["kind"]](snr) for o in used]
                pa = p_agn(snr) if p_agn is not None else 1.0
                if min(ps) < 0.05 or pa < 0.05:
                    continue
                t_agn = (agn["airtime_s"] if agn else 0.0) + turnaround
                median = _median_qso_time(ps, [o["airtime_s"] + turnaround for o in used], slot, t_agn, pa)
                points.append({"snr_db": float(snr), "median_s": median, "min_p": min(ps)})
            curves.append(
                {"contest": contest, "variant": variant, "label": label, "mode": mode, "best_s": best, "points": points}
            )
    return {"breakdown": breakdown, "totals": totals, "curves": curves, "turnaround_s": turnaround}


def mode_table(run_modes: Sequence[str], reference: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the mode reference table: nominal parameters plus the measured 99% occupied bandwidth."""
    measured = reference.get("mode_info_measured", {})
    table = []
    for mode in run_modes:
        info = mode_info.MODES[mode]
        table.append(
            {
                "mode": mode,
                "label": info.label,
                "modulation": info.modulation,
                "gaussian_bt": info.gaussian_bt,
                "tones": info.tones,
                "tone_spacing_hz": info.tone_spacing_hz,
                "baud": info.baud,
                "bits_per_symbol": info.bits_per_symbol,
                "symbols": info.symbols,
                "frame_s": info.frame_s,
                "slot_s": info.slot_s,
                "info_bits": info.info_bits,
                "crc_bits": info.crc_bits,
                "fec": info.fec,
                "channel_bits": info.channel_bits,
                "gross_bit_rate": info.gross_bit_rate,
                "net_bit_rate": info.net_bit_rate,
                "nominal_bandwidth_hz": info.nominal_bandwidth_hz,
                "measured_bw99_hz": measured.get(mode, {}).get("bw99_hz"),
                "synchronization": info.synchronization,
                "notes": info.notes,
            }
        )
    return table


def analyze(run_dir: Path, seed: int = 1, level: int = HEADLINE_LEVEL) -> dict[str, Any]:
    """Compute every table and chart series for a run; write tables/ and analysis.json; return the analysis.

    ``level`` is the headline 5-character decode percentage (1-99). A run scored in other units is refused: rescore it
    first (runner.rescore_run).
    """
    if not 1 <= level <= 99:
        raise SystemExit(f"the headline level must be a percentage from 1 to 99, not {level}")
    config = _load_json(run_dir / "config.json")
    if config.get("unit_scoring") != UNIT_SCORING:
        raise SystemExit(
            f"{run_dir} was scored in {config.get('unit_scoring') or 'the old units'}, not {UNIT_SCORING}; rescore it "
            "into a new directory with the rescore command, then analyze that"
        )
    rows = load_rows(run_dir)
    if not rows:
        raise SystemExit("raw data has no successful trials")
    reference = _load_json(run_dir / "reference.json")
    floors: dict[str, float] = config.get("snr_floors") or {"jtty": -17.0, "ft4": -21.0, "ft8": -25.0}
    rng = np.random.default_rng(seed)
    modes = [mode for mode in MODE_LABELS if any(row["mode"] == mode for row in rows)]

    summary: list[dict[str, Any]] = []
    thresholds: list[dict[str, Any]] = []
    headline: list[dict[str, Any]] = []
    single_frame: list[dict[str, Any]] = []
    copy_contest: list[dict[str, Any]] = []
    copy_by_frames: list[dict[str, Any]] = []
    cer: list[dict[str, Any]] = []
    calibration: list[dict[str, Any]] = []
    throughput: list[dict[str, Any]] = []

    for mode in modes:
        floor = floors.get(mode)
        unit_ci = "bootstrap"
        classes = sorted({row["cls"] for row in rows if row["mode"] == mode})
        selections: list[tuple[str, Sequence[str] | None]] = [(cls, (cls,)) for cls in classes]
        pooled = [cls for cls in HEADLINE_CLASSES.get(mode, ()) if cls in classes]
        if len(pooled) > 1:
            selections.insert(0, ("all", pooled))
        for cls_label, cls in selections:
            selected = _select(rows, mode, cls)
            trials = Trials.from_rows(selected)
            points = [_point_summary(trials, snr, unit_ci, rng, floor) for snr in trials.grid()]
            for point in points:
                summary.append({"mode": mode, "cls": cls_label, **point})
            label = (
                MODE_LABELS[mode] if cls_label in ("all", "qso") else f"{MODE_LABELS[mode]} {CLASS_LABELS[cls_label]}"
            )
            offset = ebn0_offset_db(mode, psk31_char_bits(selected)) if mode == "psk31" else None
            threshold, fit = _threshold(mode, label, trials, rng, floor, level, offset)
            if cls_label == "contest-macro":
                threshold["unit"] = "macros"
            threshold["series_cls"] = cls_label if cls_label != "all" else ""
            thresholds.append(threshold)
            if cls_label in ("all", "qso") or (cls_label == "contest" and len(pooled) < 2 and mode in HEADLINE_CLASSES):
                fit_data = {"mu": fit[0], "width": fit[1]} if fit else None
                headline.append(_series(mode, MODE_LABELS[mode], mode, points, fit=fit_data, threshold=threshold))
                calibration.append(_series(mode, MODE_LABELS[mode], mode, points))
            if cls_label in ("contest", "qso", "contest-macro"):
                copy_contest.append(_series(f"{mode}-{cls_label}", label, mode, points))
            if cls_label == "text":
                cer.append(_series(f"{mode}-text", label, mode, points))
            if cls_label != "all":
                throughput.append(_series(f"{mode}-{cls_label}", label, mode, points))

        if mode == "jtty":
            one = Trials.from_rows(_select(rows, mode, ("contest",), (1, 1)))
            if len(one.snr):
                points = [_point_summary(one, snr, "bootstrap", rng, floor) for snr in one.grid()]
                threshold, _ = _threshold(mode, "JTTY single-frame contest", one, rng, floor, level)
                threshold["series_cls"] = ""
                thresholds.append(threshold)
                single_frame.append(_series("jtty-1frame", "JTTY 1-frame", mode, points, threshold=threshold))
            for low, high, bucket in FRAME_BUCKETS:
                subset = Trials.from_rows(_select(rows, mode, ("contest", "text"), (low, high)))
                if len(subset.snr):
                    points = [_point_summary(subset, snr, "bootstrap", rng, floor) for snr in subset.grid()]
                    copy_by_frames.append(_series(f"jtty-{low}-{high}", f"JTTY {bucket}", mode, points))

    all_snrs = sorted({float(row["snr_db"]) for row in rows})
    theory = []
    theory_psk31 = []
    for snr in np.arange(min(all_snrs), max(all_snrs) + 0.25, 0.25):
        ebn0 = 10 ** ((snr + 10 * math.log10(2500.0 / BAUD)) / 10)
        bit_error = 0.5 * math.exp(-ebn0 / 2)
        theory.append({"snr_db": float(snr), "cer": 1 - (1 - bit_error) ** 5})
        # Ideal differential BPSK per channel bit; a character is lost if any of its bits (Varicode and gap) is.
        ebn0_psk = 10 ** ((snr + 10 * math.log10(2500.0 / PSK31_BAUD)) / 10)
        bit_error_psk = 0.5 * math.exp(-ebn0_psk)
        theory_psk31.append({"snr_db": float(snr), "cer": 1 - (1 - bit_error_psk) ** mode_info.PSK31_CHAR_BITS})

    decode_levels = [
        {
            "series": series["label"],
            "mode": series["mode"],
            "unit": series["unit"],
            **{f"decode_{level}_db": series["threshold"][f"decode_{level}_db"] for level in DECODE_LEVELS},
        }
        for series in headline
    ]
    failures = strong_signal_failures(rows, thresholds)
    noise = noise_rates(run_dir)
    qso = contest_qso(rows, reference, float(config.get("turnaround_s", 0.0)))
    analysis = {
        "run": run_dir.name,
        "modes": modes,
        "trials": len(rows),
        "seed": config.get("seed"),
        "channel": config.get("channel", "awgn"),
        "fading": config.get("fading"),
        "headline_level": level,
        "headline": headline,
        "single_frame": single_frame,
        "copy_contest": copy_contest,
        "copy_by_frames": copy_by_frames,
        "cer": cer,
        "cer_theory_rtty": theory,
        "cer_theory_psk31": theory_psk31,
        "calibration": calibration,
        "throughput": throughput,
        "thresholds": thresholds,
        "decode_levels": decode_levels,
        "failures": failures,
        "noise": noise,
        "contest_qso": qso,
        "mode_table": mode_table(modes, reference),
        "reported_snr_floor_db": floors,
        "ebn0_offset_db": {mode: ebn0_offset_db(mode) for mode in modes},
        "contest_labels": CONTEST_LABELS,
    }
    _write_tables(run_dir, summary, thresholds, analysis)
    (run_dir / "analysis.json").write_text(json.dumps(_finite(analysis), indent=1) + "\n")
    return analysis


def _finite(value: Any) -> Any:
    """Replace NaN/inf with None, recursively, so the JSON is standard."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_finite(item) for item in value]
    return value


def _fmt(value: Any, digits: int = 1) -> str:
    """Format a number for a Markdown table ('-' for missing)."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _write_csv(path: Path, data: Sequence[dict[str, Any]]) -> None:
    """Write rows of dicts as CSV (columns from the first row)."""
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(data[0]) if data else ["empty"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(_finite(list(data)))


EBN0_BASIS: str = (
    "Eb/N0 = SNR + 10*log10(2500 Hz / net bit rate), with JTTY 34 information bits per 1.888 s frame, FT8 and FT4 "
    "77 bits per 12.64 s / 5.04 s transmission, RTTY 5 data bits per 7.5-bit character at 45.45 baud (start, "
    "stop and shift characters count as overhead, not information), and PSK31 5 bits per character at 31.25 baud "
    "with the mean Varicode length (gap included) of the text each series sent: lower-case free text averages "
    f"{mode_info.PSK31_CHAR_BITS:.2f} bits per character, contest overs in capitals about 9."
)

CI_NOTE: str = (
    "Trials are transmissions; units are what Decode % counts: 5-character groups of the sent text (a callsign's "
    "worth; a final 1-4 characters form a short group of their own) printed exactly, as one unbroken piece, for "
    "every mode (an FT8 or FT4 message gives all its groups or none). Decode intervals come from a bootstrap over "
    "whole trials, so groups sharing a transmission are not treated as independent; every Copy % interval is a "
    "Wilson interval over trials. A contest-macro series counts one unit per whole macro, copied only when every "
    "field printed exactly in some repetition and none printed a conflicting value (ambiguous)."
)


def _write_tables(
    run_dir: Path, summary: list[dict[str, Any]], thresholds: list[dict[str, Any]], analysis: dict[str, Any]
) -> None:
    """Write every table under tables/ as CSV and Markdown."""
    tables = run_dir / "tables"
    tables.mkdir(parents=True, exist_ok=True)
    level = analysis["headline_level"]
    _write_csv(tables / "summary.csv", summary)
    _write_csv(tables / "thresholds.csv", thresholds)
    _write_csv(tables / "decode_levels.csv", analysis["decode_levels"])

    lines = [
        "# Decode thresholds",
        "",
        f"SNR in a 2500 Hz reference bandwidth (true SNR at the antenna). The {level}% point is read from the measured "
        "decode curve, fitted to never fall as SNR rises, with bootstrap 95% intervals; '-' means not reached by "
        "+10 dB. *Reported* is the decoders' mean report (censored-normal fit where reports hit the floor) at the "
        f"grid point nearest the {level}% point.",
        "",
        EBN0_BASIS,
        "",
        CI_NOTE,
        "",
        f"| Series | Unit | Trials | Units | {level}% (dB) | 95% CI | Eb/N0 at {level}% | Reported at {level}% |",
        "|---|---|---:|---:|---:|---|---:|---:|",
    ]
    for row in thresholds:
        lines.append(
            f"| {row['series']} | {row['unit']} | {row['trials']} | {row['units_sent']} | {_fmt(row['headline_db'])} | "
            f"{_fmt(row['headline_lo'])} to {_fmt(row['headline_hi'])} | {_fmt(row['ebn0_headline_db'])} | "
            f"{_fmt(row['reported_snr_at_headline_db'])} |"
        )
    (tables / "thresholds.md").write_text("\n".join(lines) + "\n")

    lines = [
        "# SNR at each decode rate",
        "",
        "SNR (dB, 2500 Hz reference, true SNR at the antenna) where each mode's measured decode curve reaches the "
        "rate, from the curve fitted to never fall as SNR rises. '-' means not reached by +10 dB, or already above "
        "that rate at the lowest SNR measured.",
        "",
        "| Series | Unit | " + " | ".join(f"{level}%" for level in DECODE_LEVELS) + " |",
        "|---|---|" + "---:|" * len(DECODE_LEVELS),
    ]
    for row in analysis["decode_levels"]:
        cells = " | ".join(_fmt(row[f"decode_{level}_db"]) for level in DECODE_LEVELS)
        lines.append(f"| {row['series']} | {row['unit']} | {cells} |")
    (tables / "decode_levels.md").write_text("\n".join(lines) + "\n")

    lines = [
        "# Results by SNR",
        "",
        CI_NOTE,
        "",
        "Copy % is whole messages printed exactly. Garbage is wrong units printed per 1000 trials (upper 95% bound in "
        "brackets when none were seen). Chars/min is throughput: 60 x characters copied / seconds of airtime, both "
        "summed over the trials at that SNR. Characters are those of the normalized sent text (spaces between words "
        "count, framing does not). Copied: RTTY/PSK31 the characters of 5-character groups printed whole; JTTY the "
        "text of correctly decoded frames with each frame's edge spaces left out (so spaces at frame boundaries do "
        "not count); FT8/FT4 the whole message when decoded; contest macros the macro's words "
        "when the macro counts as copied. Airtime: the whole transmission (RTTY/PSK31 lead-in, idle, framing, "
        "envelope and tail included; JTTY whole 1.888 s frames), or a whole 15 s / 7.5 s T/R slot per FT8/FT4 "
        "message. True SNR is "
        "measured from each trial's samples; reported SNR is the decoders' mean report, from a censored-normal fit "
        "where reports hit the decoder's floor (count in *At floor*).",
    ]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in summary:
        groups[(row["mode"], row["cls"])].append(row)
    for (mode, cls), points in groups.items():
        lines += [
            "",
            f"## {MODE_LABELS[mode]} - {CLASS_LABELS.get(cls, cls)}",
            "",
            "| SNR | Trials (transmissions) | Units sent | Decode % | 95% CI | Copy % | CER | "
            "Garbage per 1000 trials | Chars/min | True SNR (meas.) | Reported SNR | Bias | At floor |",
            "|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for p in points:
            garbage = _fmt(p["garbage_per_1000"])
            if p["garbage_per_1000"] == 0:
                garbage = f"0 [<{_fmt(p['garbage_upper_per_1000'])}]"
            reported = f"{_fmt(p['snr_reported_mean'])} ± {_fmt(p['snr_reported_sd'])}"
            lines.append(
                f"| {p['snr_db']:+.1f} | {p['n_trials']} | {p['units']} | {100 * p['decode']:.1f} | "
                f"{100 * p['decode_lo']:.1f} to {100 * p['decode_hi']:.1f} | {100 * p['copy']:.1f} | "
                f"{p['cer_mean']:.3f} | {garbage} | {_fmt(p['throughput_cpm'])} | "
                f"{_fmt(p['snr_true_measured_mean'], 2)} | {reported} | {_fmt(p['snr_bias_mean'])} | "
                f"{p['n_reported_floor']} |"
            )
    (tables / "summary.md").write_text("\n".join(lines) + "\n")

    failures = analysis["failures"]
    _write_csv(tables / "failures.csv", failures["examples"])
    lines = [
        "# Strong-signal failures",
        "",
        f"Trials at least {STRONG_MARGIN_DB:.0f} dB above each series' 90% decode point that were not a clean copy. "
        "*missed*: units not copied; *split*: JTTY printed every frame but over several lines; *no_eom*: JTTY printed "
        "every frame in one line without its end-of-message; *ambiguous*: a contest field also printed a conflicting "
        "value; *false_text*: wrong text printed.",
        "",
        "| Mode | Class | From SNR | Trials | Failed | Per 1000 | Missed | Split | No EOM | Ambiguous | False text |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for g in failures["groups"]:
        lines.append(
            f"| {MODE_LABELS[g['mode']]} | {CLASS_LABELS.get(g['cls'], g['cls'])} | {g['from_snr_db']:+.1f} | "
            f"{g['trials']} | {g['failed']} | {g['per_1000']:.1f} | {g['n_missed']} | {g['n_split']} | "
            f"{g['n_no_eom']} | {g.get('n_ambiguous', 0)} | {g['n_false_text']} |"
        )
    if failures.get("unmeasured"):
        lines += [
            "",
            f"Not measured (no 90% point, or no trials at the cut): {unmeasured_text(failures['unmeasured'])}.",
        ]
    test = failures.get("grid_phase_test") or {}
    if test:
        lines += [
            "",
            f"JTTY split messages starting within {100 * test['window']:.0f}% of a decoder search-window boundary: "
            f"{test['splits_near']} of {test['splits']}, against {100 * test['baseline_near_share']:.1f}% of all "
            f"{test['strong_trials']} strong-signal JTTY trials (one-sided binomial p = "
            f"{test['p_value']:.1e}).",
        ]
    (tables / "failures.md").write_text("\n".join(lines) + "\n")

    noise = analysis["noise"]
    if noise:
        _write_csv(tables / "noise.csv", [{k: v for k, v in n.items() if k != "examples"} for n in noise])
        lines = [
            "# False prints in band noise",
            "",
            "Pure band noise through the same receiver; everything printed is false. Exact Poisson 95% intervals. RTTY "
            "and PSK31 have no squelch here, so they print characters from noise continuously (shown per minute).",
            "",
            "| Mode | Hours of noise | False prints | Unit | Rate | 95% CI | Examples |",
            "|---|---:|---:|---|---:|---|---|",
        ]
        for n in noise:
            if n["mode"] in KEYBOARD_MODES:
                per = f"{n['per_minute']:.1f}/min"
                interval = f"{n['per_hour_lo'] / 60:.1f} to {n['per_hour_hi'] / 60:.1f}/min"
            else:
                per = f"{n['per_hour']:.2f}/h"
                interval = f"{n['per_hour_lo']:.2f} to {n['per_hour_hi']:.2f}/h"
            examples = "; ".join(e[:40] for e in n["examples"][:3]).replace("|", "/")
            lines.append(
                f"| {MODE_LABELS[n['mode']]} | {n['hours']:.2f} | {n['false_prints']} | {n['unit']} | {per} | "
                f"{interval} | {examples} |"
            )
        (tables / "noise.md").write_text("\n".join(lines) + "\n")

    qso = analysis["contest_qso"]
    if qso["totals"]:
        _write_csv(tables / "contest_qso.csv", qso["breakdown"])
        lines = [
            "# Contest QSO: bits and time to complete an exchange",
            "",
            "S&P (W9XYZ in WI) answers K1ABC (MA) running. Overs counted from the S&P's call to the final TU/RR73 (the "
            "CQ and the AGN? request are listed for reference). JTTY uses its native function-key atoms; FT8/FT4 "
            "take one T/R slot per over. Best case: no errors"
            + (f", plus {qso['turnaround_s']:.1f} s turnaround per over" if qso["turnaround_s"] else "")
            + ".",
        ]
        for contest in dict.fromkeys(t["contest"] for t in qso["totals"]):
            lines += [
                "",
                f"## {CONTEST_LABELS.get(contest, contest)}",
                "",
                "| Mode | Overs | Size | Information bits | Channel bits | Airtime (s) | Best-case QSO (s) |",
                "|---|---:|---|---:|---:|---:|---:|",
            ]
            for t in (t for t in qso["totals"] if t["contest"] == contest):
                lines.append(
                    f"| {t['label']} | {t['overs']} | {t['units']} {t['unit']} | {t['info_bits']:.0f} | "
                    f"{t['channel_bits']:.0f} | {t['airtime_s']:.1f} | {t['best_s']:.1f} |"
                )
            lines += [
                "",
                "| Mode | Over | Sender | Text | Size | Info bits | Airtime (s) |",
                "|---|---|---|---|---:|---:|---:|",
            ]
            for b in (b for b in qso["breakdown"] if b["contest"] == contest):
                marker = "" if b["in_qso"] else " (ref.)"
                text = str(b["text"]).replace("|", "/")
                lines.append(
                    f"| {b['label']} | {b['kind']}{marker} | {b['sender']} | `{text}` | {b['units']} | "
                    f"{_fmt(b['info_bits'], 0)} | {_fmt(b['airtime_s'], 2)} |"
                )
        (tables / "contest_qso.md").write_text("\n".join(lines) + "\n")

        time_rows = []
        for curve in qso["curves"]:
            row: dict[str, Any] = {"contest": curve["contest"], "mode": curve["label"], "best_s": curve["best_s"]}
            for snr in QSO_SNR_COLUMNS:
                point = next((p for p in curve["points"] if math.isclose(p["snr_db"], snr)), None)
                row[f"{snr:+.0f} dB"] = point["median_s"] if point else math.nan
            time_rows.append(row)
        _write_csv(tables / "contest_qso_time.csv", time_rows)
        header = " | ".join(f"{snr:+.0f} dB" for snr in QSO_SNR_COLUMNS)
        lines = [
            "# Median time to complete a contest QSO vs SNR (seconds)",
            "",
            "From the measured copy probability of each over at each SNR: JTTY/RTTY/PSK31 repeat a failed over after "
            "an AGN? request (itself possibly repeated); FT8/FT4 lose a full T/R cycle per failed over. Blank where "
            "any over copies less than 5% of the time.",
            "",
            f"| Contest | Mode | Best case | {header} |",
            "|---|---|---:|" + "---:|" * len(QSO_SNR_COLUMNS),
        ]
        for row in time_rows:
            cells = " | ".join(_fmt(row[f"{snr:+.0f} dB"]) for snr in QSO_SNR_COLUMNS)
            lines.append(
                f"| {CONTEST_LABELS.get(row['contest'], row['contest'])} | {row['mode']} | "
                f"{row['best_s']:.1f} | {cells} |"
            )
        (tables / "contest_qso_time.md").write_text("\n".join(lines) + "\n")

    table = analysis["mode_table"]
    _write_csv(tables / "modes.csv", table)
    parameters = (
        ("Modulation", "modulation"), ("Gaussian smoothing (BT)", "gaussian_bt"), ("Tones", "tones"),
        ("Tone spacing (Hz)", "tone_spacing_hz"),
        ("Keying rate (baud)", "baud"), ("Bits per symbol", "bits_per_symbol"), ("Symbols", "symbols"),
        ("Frame / transmission (s)", "frame_s"), ("T/R slot (s)", "slot_s"), ("Information bits", "info_bits"),
        ("CRC bits", "crc_bits"), ("FEC", "fec"), ("Gross bit rate (bit/s)", "gross_bit_rate"),
        ("Net bit rate (bit/s)", "net_bit_rate"), ("Nominal bandwidth (Hz)", "nominal_bandwidth_hz"),
        ("Measured 99% bandwidth (Hz)", "measured_bw99_hz"), ("Synchronization", "synchronization"),
    )  # fmt: skip
    lines = [
        "# Modes",
        "",
        "| Parameter | " + " | ".join(m["label"] for m in table) + " |",
        "|---|" + "---|" * len(table),
    ]
    for name, key in parameters:
        cells = []
        for m in table:
            value = m[key]
            if value is None:
                cells.append("asynchronous" if key == "slot_s" else "-")
            elif isinstance(value, float):
                digits = 1 if key.endswith("_hz") and key != "tone_spacing_hz" else 3
                cells.append(f"{value:.{digits}f}".rstrip("0").rstrip("."))
            else:
                cells.append(str(value))
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    (tables / "modes.md").write_text("\n".join(lines) + "\n")
