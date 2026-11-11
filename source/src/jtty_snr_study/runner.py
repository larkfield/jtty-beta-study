"""Running the sweep: trials per mode, message class and SNR, in parallel, resumably.

Every trial is identified by (mode, message class, SNR, trial index) and fully determined by the run's base seed:
message content comes from a generator keyed without the mode, so JTTY, RTTY and PSK31 send the same messages at the
same SNR and trial index; band noise, fading, frequency offset and timing come from a generator keyed with the mode.
Rows are appended to ``raw/trials.csv`` as trials finish, and keys already there are skipped, so an interrupted run
resumes and a run with more trials per point tops up the existing one.

The SNR grid comes from a pilot pass (1 dB steps from -21 to +10 dB, few trials) that finds where each curve rises
from 1% to 99%, extending below -21 dB 1 dB at a time until the curve is down to 1%. A fine pass (0.5 dB steps, full
trial count) then covers that span plus 3 dB each side, but never above the pilot's top: a curve that levels off
below 99% (an error floor under fast fading) gets full trials up to +10 dB and no further. Pilot and fine trials at
the same SNR share keys, so pilot trials count toward the fine totals. The grid is planned on characters copied in
runs (``run_chars``, scoring.COPY_RUN_MIN), not on all characters: single RTTY and PSK31 characters match by chance in
noise, so their character curves never fall to 1% and would stretch every sweep to its floor. A final noise stage
feeds pure band noise through each decoder and counts what it prints (``raw/noise.csv``).

Trials are scored in 5-character groups (scoring.UNIT_SCORING, recorded in config.json). A run scored in other units
(older runs: JTTY frames, FT8/FT4 messages and RTTY/PSK31 groups on the old alignment; or characters) cannot be
resumed; ``rescore_run`` copies it into a new run directory scored in 5-character groups, from the sent and printed
text it stored, without decoding anything again.
"""

from __future__ import annotations

import csv
import json
import math
import multiprocessing.util
import os
import platform
import re
import shutil
import sys
import time
import wave
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from jtty_snr_study import channel, codec, messages, modes, psk31, rtty, scoring
from jtty_snr_study.channel import FadingModel
from jtty_snr_study.messages import MacroToken, MessageClass, Over

MODES: tuple[str, ...] = ("ft8", "ft4", "jtty", "psk31", "rtty")
"""Modes in the order charts and tables present them (each mode's color is fixed by name, not position)."""

MODE_CLASSES: dict[str, tuple[MessageClass, ...]] = {
    "jtty": (MessageClass.CONTEST, MessageClass.TEXT, MessageClass.CONTEST_MACRO),
    "rtty": (MessageClass.CONTEST, MessageClass.TEXT, MessageClass.CONTEST_MACRO),
    "psk31": (MessageClass.CONTEST, MessageClass.TEXT, MessageClass.CONTEST_MACRO),
    "ft8": (MessageClass.QSO,),
    "ft4": (MessageClass.QSO,),
}
"""Message classes each mode sends."""

_MODE_KEYS = {"jtty": 1, "ft8": 2, "ft4": 3, "rtty": 4, "psk31": 5}
_CLASS_KEYS = {MessageClass.CONTEST: 1, MessageClass.TEXT: 2, MessageClass.QSO: 3, MessageClass.CONTEST_MACRO: 4}
_NOISE_KEY = 9

JTTY_FRAME_SAMPLES: int = 59 * 384
"""Samples in one JTTY frame (59 symbols at nsps 384)."""

JTTY_SEARCH_STEP: int = JTTY_FRAME_SAMPLES // 4
"""The JTTY receiver's search grid: a quarter frame from the start of each reception."""

SLOT_TIMING: dict[str, tuple[float, float, float, float]] = {
    "ft8": (0.5, -0.5, 0.5, 2400.0),
    "ft4": (0.3, -0.2, 0.5, 2350.0),
}
"""FT8/FT4: nominal transmit start in the period (s), DT offset range (s), and highest lowest-tone frequency (Hz)
that keeps the whole signal in the flat part of the SSB passband."""

SLOT_LOW_HZ: float = 400.0
"""Lowest FT8/FT4 lowest-tone frequency (inside the flat part of the SSB passband)."""

LEAD_RANGE_S: tuple[float, float] = (0.5, 2.5)
"""Random noise before a JTTY, RTTY or PSK31 transmission, so it starts asynchronously to the receiver."""

JTTY_OFFSET_HZ: float = 10.0
"""JTTY transmitter offset range from the Rx frequency (+/-)."""

RTTY_TUNING_ERROR_HZ: float = 5.0
"""RTTY tuning error range (+/-): how far off the operator's by-eye tuning is."""

PSK31_TUNING_ERROR_HZ: float = 5.0
"""PSK31 click error range (+/-): how far from the signal the operator's click on the waterfall lands (the decoder
snaps to the signal and tracks it with AFC)."""

NOISE_CHUNK_S: dict[str, float] = {"jtty": 60.0, "ft8": 15.0, "ft4": 7.5, "rtty": 60.0, "psk31": 60.0}
"""Length of one noise-only reception per mode: a minute of JTTY, RTTY or PSK31 listening, or one FT8/FT4 period."""

ROW_FIELDS: tuple[str, ...] = (
    "mode",
    "cls",
    "contest",
    "msg_kind",
    "snr_db",
    "trial",
    "stage",
    "units",
    "units_ok",
    "run_chars",
    "frames",
    "copied",
    "cer",
    "garbage",
    "noise_prints",
    "ap_filtered",
    "n_messages",
    "state",
    "failure",
    "snr_true_measured_db",
    "snr_reported_db",
    "snr_reported_floor",
    "freq_hz",
    "start_s",
    "grid_phase",
    "tuning_error_hz",
    "sent",
    "printed",
    "airtime_s",
    "copied_chars",
    "decode_s",
    "clipped",
    "error",
    "messages_json",
)
"""Columns of raw/trials.csv, one row per trial."""

CONTENT_VERSION: int = 2
"""Version of the trial-content generators (message classes and their random draws). Version 1 runs (before the
reference contest QSOs) cannot be resumed or replayed for the contest/qso classes, because the same trial key now
produces a different message."""

NOISE_FIELDS: tuple[str, ...] = ("mode", "chunk", "seconds", "false_prints", "printed", "decode_s", "error")
"""Columns of raw/noise.csv, one row per noise-only reception."""


PILOT_STEP_DB: float = 1.0
"""Spacing of the pilot (coarse) sweep, and the step by which it extends below its configured range."""

PILOT_FLOOR_DB: float = -40.0
"""The lowest SNR the pilot sweep may extend to, and the lowest the fine grid may reach."""

CURVE_LOW: float = 0.01
CURVE_HIGH: float = 0.99
"""The decode fractions that bound a curve's transition, which the fine grid covers plus its margin."""


@dataclass(frozen=True)
class Preset:
    """Trial counts, grid policy and noise-only hours for a named run size."""

    trials: int
    noise_hours: float
    pilot: bool = True
    fixed_snrs: tuple[float, ...] = ()


PRESETS: dict[str, Preset] = {
    "smoke": Preset(trials=20, noise_hours=0.05, pilot=False, fixed_snrs=(-26.0, -20.0, -14.0, -8.0, -2.0)),
    "quick": Preset(trials=200, noise_hours=4.0),
    "standard": Preset(trials=1000, noise_hours=24.0),
    "publication": Preset(trials=5000, noise_hours=100.0),
}
"""Named run sizes. ``smoke`` only checks the tool end to end; the others are real studies."""


def jtty_snr_floor(tree_root: Path = messages.TREE_ROOT) -> float:
    """Return JTTY's lowest reported SNR as set in the WSJT-X source (SNR_FLOOR_DB in lib/jtty/jtty_mdecode.f90)."""
    source = tree_root / "lib" / "jtty" / "jtty_mdecode.f90"
    if source.exists():
        match = re.search(r"SNR_FLOOR_DB\s*=\s*(-?[0-9.]+)", source.read_text(errors="replace"))
        if match:
            return float(match.group(1))
    return scoring.REPORTED_SNR_FLOOR_DB["jtty"]


def default_snr_floors() -> dict[str, float]:
    """Return each decoder's lowest reported SNR, with JTTY's read from the WSJT-X source."""
    return {**scoring.REPORTED_SNR_FLOOR_DB, "jtty": jtty_snr_floor()}


@dataclass(frozen=True)
class StudyConfig:
    """Everything that defines a run. Stored in config.json; the seed must match when a run is resumed.

    ``channel`` is ``awgn`` or an ITU-R F.1487 fading model (channel.FADING_MODELS). ``snr_floors`` are the
    decoders' lowest reports, used to flag censored SNR reports. ``turnaround_s`` is added per over in the contest
    QSO timing. ``noise_hours`` of pure noise per mode go through the noise stage.
    """

    run_dir: Path
    codec_binary: Path
    seed: int = 2026
    modes: tuple[str, ...] = MODES
    trials: int = 1000
    pilot: bool = True
    pilot_trials: int = 100
    pilot_snrs: tuple[float, ...] = tuple(float(s) for s in range(-21, 11))
    fine_step_db: float = 0.5
    fine_margin_db: float = 3.0
    fixed_snrs: tuple[float, ...] = ()
    workers: int = max(1, (os.cpu_count() or 2) - 2)
    batch_size: int = 10
    callsign_file: Path = messages.DEFAULT_CALLSIGN_FILE
    channel: str = "awgn"
    noise_hours: float = 0.0
    turnaround_s: float = 0.0
    snr_floors: dict[str, float] = field(default_factory=default_snr_floors)
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def fading(self) -> FadingModel | None:
        """The fading model of this run's channel, or None for plain AWGN."""
        return channel.FADING_MODELS.get(self.channel.upper())

    @classmethod
    def from_run(cls, run_dir: Path, codec_binary: Path | None = None) -> StudyConfig:
        """Rebuild the configuration of an existing run from its config.json (older runs lack some settings)."""
        data = json.loads((run_dir / "config.json").read_text())
        return cls(
            run_dir=run_dir,
            codec_binary=codec_binary or Path(data["codec_binary"]),
            seed=int(data["seed"]),
            modes=tuple(data.get("modes", MODES)),
            callsign_file=Path(data.get("callsign_file", messages.DEFAULT_CALLSIGN_FILE)),
            channel=data.get("channel", "awgn"),
            snr_floors=data.get("snr_floors", {**scoring.REPORTED_SNR_FLOOR_DB, "jtty": -17.0}),
            workers=1,
        )


@dataclass(frozen=True)
class Task:
    """A batch of trials of one mode, class and SNR, run by one worker."""

    mode: str
    cls: MessageClass
    snr_db: float
    trials: tuple[int, ...]
    stage: str


@dataclass(frozen=True)
class NoiseTask:
    """A batch of noise-only receptions of one mode."""

    mode: str
    chunks: tuple[int, ...]


def snr_key(snr_db: float) -> int:
    """Return the integer key of an SNR (centi-dB, offset to be non-negative) used in trial identity and seeds."""
    return round(snr_db * 100) + 100_000


def trial_key(mode: str, cls: str, snr_db: float, trial: int) -> tuple[str, str, int, int]:
    """Return the identity of a trial."""
    return mode, str(cls), snr_key(snr_db), trial


def content_rng(seed: int, cls: MessageClass, snr_db: float, trial: int) -> np.random.Generator:
    """Return the generator for a trial's message content.

    It is independent of mode, so JTTY, RTTY and PSK31 send the same messages, and the macro class sends the same
    overs as the contest class.
    """
    key = _CLASS_KEYS[MessageClass.CONTEST if cls is MessageClass.CONTEST_MACRO else cls]
    return np.random.default_rng(np.random.SeedSequence(seed, spawn_key=(key, snr_key(snr_db), trial, 0)))


def channel_rng(seed: int, mode: str, cls: MessageClass, snr_db: float, trial: int) -> np.random.Generator:
    """Return the generator for a trial's band noise, fading, frequency offset and timing."""
    keys = (_CLASS_KEYS[cls], snr_key(snr_db), trial, _MODE_KEYS[mode])
    return np.random.default_rng(np.random.SeedSequence(seed, spawn_key=keys))


def noise_rng(seed: int, mode: str, chunk: int) -> np.random.Generator:
    """Return the generator for one noise-only reception."""
    return np.random.default_rng(np.random.SeedSequence(seed, spawn_key=(_NOISE_KEY, _MODE_KEYS[mode], chunk)))


def _jtty_messages_json(decoded: Sequence[codec.JttyMessage]) -> str:
    """Return the decoded JTTY messages as compact JSON for the raw data."""
    return json.dumps(
        [
            {
                "start_s": round(m.start_s, 4),
                "freq": round(m.freq, 2),
                "snr": m.snr,
                "state": m.state,
                "text": m.text,
                "gaps": list(m.gaps),
            }
            for m in decoded
        ],
        separators=(",", ":"),
    )


def _slot_messages_json(decodes: Sequence[codec.SlotDecode]) -> str:
    """Return the FT8/FT4 decode lines as compact JSON for the raw data."""
    return json.dumps(
        [
            {
                "message": d.message,
                "snr": d.snr,
                "dt": round(d.dt, 3),
                "freq": d.freq,
                "ap": d.ap,
                "pass": d.decode_pass,
            }
            for d in decodes
        ],
        separators=(",", ":"),
    )


class TrialRunner:
    """Runs single trials. Owns the wsjt-codec process (started on first use) and the callsign list.

    After each trial ``last_audio`` holds the received audio, so a trial can be replayed to a WAV file.
    """

    def __init__(self, config: StudyConfig, workdir: Path, wisdom: Path | None) -> None:
        self.config = config
        self.workdir = workdir
        self.wisdom = wisdom
        self.calls = messages.load_callsigns(config.callsign_file)
        self.last_audio: npt.NDArray[np.int16] | None = None
        self._codec: codec.CodecClient | None = None

    def close(self) -> None:
        """Stop the codec process, if one is running."""
        if self._codec is not None:
            self._codec.close()
            self._codec = None

    def client(self) -> codec.CodecClient:
        """Return the running codec client, starting one if needed."""
        if self._codec is None:
            self._codec = codec.CodecClient(self.config.codec_binary, self.workdir, self.wisdom)
        return self._codec

    def run(self, mode: str, cls: MessageClass, snr_db: float, trial: int, stage: str) -> dict[str, Any]:
        """Run one trial and return its raw-data row.

        A codec failure is retried once on a fresh process. Any failure that remains -- or any other exception, so
        one bad trial cannot stop a sweep -- is returned as an error row; error rows are excluded from the analysis
        and retried when the run is resumed.
        """
        error: Exception | None = None
        for _ in range(2):
            try:
                return self._run_once(mode, cls, snr_db, trial, stage)
            except codec.CodecError as failure:
                self.close()
                error = failure
            except Exception as failure:
                error = failure
                break
        row: dict[str, Any] = {name: "" for name in ROW_FIELDS}
        row.update(
            mode=mode, cls=str(cls), snr_db=snr_db, trial=trial, stage=stage, error=f"{type(error).__name__}: {error}"
        )
        return row

    def _run_once(self, mode: str, cls: MessageClass, snr_db: float, trial: int, stage: str) -> dict[str, Any]:
        """Run one trial (see ``run``); raises CodecError on decoder failure."""
        message = messages.message_for(cls, content_rng(self.config.seed, cls, snr_db, trial), self.calls)
        over = message if isinstance(message, Over) else None
        text = over.text if over is not None else str(message)
        # The information fields (calls, exchanges) the ambiguity rule checks, for a plain contest over or a macro.
        fields = (
            scoring.field_groups(over.kind, False, True) if over is not None and cls is MessageClass.CONTEST else None
        )
        macro_fields = scoring.field_groups(over.kind, True, True) if over is not None else None
        rng = channel_rng(self.config.seed, mode, cls, snr_db, trial)
        fading = self.config.fading
        row: dict[str, Any] = {
            "mode": mode,
            "cls": str(cls),
            "contest": str(over.contest) if over is not None else "",
            "msg_kind": over.kind if over is not None else "",
            "snr_db": snr_db,
            "trial": trial,
            "stage": stage,
            "grid_phase": "",
            "tuning_error_hz": "",
            "messages_json": "",
        }

        if mode == "jtty":
            freq = codec.JTTY_RX_HZ + rng.uniform(-JTTY_OFFSET_HZ, JTTY_OFFSET_HZ)
            lead = round(rng.uniform(*LEAD_RANGE_S) * channel.SAMPLE_RATE)
            macro = cls is MessageClass.CONTEST_MACRO and over is not None
            if macro and over is not None:
                render = self.client().render_jtty(over.macro_words, freq)
            else:
                render = self.client().render_jtty(over.jtty if over is not None else text, freq)
            total = lead + len(render.samples) + JTTY_FRAME_SAMPLES + round(0.5 * channel.SAMPLE_RATE)
            reception = channel.receive(render.samples, lead, total, snr_db, rng, channel.SSB_FILTER, fading)
            started = time.perf_counter()
            decoded = self.client().decode_jtty(reception.audio)
            elapsed = time.perf_counter() - started
            if macro and over is not None:
                score = scoring.score_jtty_macro(over.macro, render.frames, render.canonical, decoded, macro_fields)
            else:
                score = scoring.score_jtty(render.frames, render.canonical, decoded, fields)
            sent = render.canonical
            airtime = len(render.samples) / channel.SAMPLE_RATE
            row.update(
                freq_hz=round(freq, 3),
                start_s=round(lead / channel.SAMPLE_RATE, 4),
                grid_phase=round((lead % JTTY_SEARCH_STEP) / JTTY_SEARCH_STEP, 4),
                messages_json=_jtty_messages_json(decoded),
            )
        elif mode in ("ft8", "ft4"):
            nominal, dt_low, dt_high, freq_high = SLOT_TIMING[mode]
            freq = rng.uniform(SLOT_LOW_HZ, freq_high)
            start_s = nominal + rng.uniform(dt_low, dt_high)
            label = mode.upper()
            render_slot = self.client().render_slot(label, text, freq)
            total = codec.PERIOD_SAMPLES[label]
            start = round(start_s * channel.SAMPLE_RATE)
            reception = channel.receive(render_slot.samples, start, total, snr_db, rng, channel.SSB_FILTER, fading)
            started = time.perf_counter()
            decodes = self.client().decode_slot(label, reception.audio)
            elapsed = time.perf_counter() - started
            score = scoring.score_slot(render_slot.msgsent, decodes)
            sent = render_slot.msgsent
            slot = modes.MODES[mode].slot_s
            airtime = float(slot) if slot is not None else total / channel.SAMPLE_RATE
            row.update(freq_hz=round(freq, 3), start_s=round(start_s, 4), messages_json=_slot_messages_json(decodes))
        elif mode == "rtty":
            error_hz = rng.uniform(-RTTY_TUNING_ERROR_HZ, RTTY_TUNING_ERROR_HZ)
            macro = cls is MessageClass.CONTEST_MACRO and over is not None
            if macro and over is not None:
                transmission = rtty.modulate(over.macro_text, error_hz, frame_with_spaces=False)
            else:
                transmission = rtty.modulate(text, error_hz)
            lead = round(rng.uniform(*LEAD_RANGE_S) * channel.SAMPLE_RATE)
            total = lead + len(transmission.samples) + round(0.5 * channel.SAMPLE_RATE)
            reception = channel.receive(transmission.samples, lead, total, snr_db, rng, channel.RTTY_FILTER, fading)
            window = (lead + transmission.message_start, lead + transmission.message_end)
            started = time.perf_counter()
            received = rtty.demodulate(reception.audio, snr_window=window)
            elapsed = time.perf_counter() - started
            if macro and over is not None:
                score = scoring.score_rtty_macro(over.macro, received, window, macro_fields)
                sent = scoring.normalize(over.macro_text)
            else:
                score = scoring.score_rtty(transmission.text, received, window, fields)
                sent = transmission.text
            airtime = len(transmission.samples) / channel.SAMPLE_RATE
            row.update(
                freq_hz=rtty.MARK_HZ, start_s=round(lead / channel.SAMPLE_RATE, 4), tuning_error_hz=round(error_hz, 3)
            )
        elif mode == "psk31":
            error_hz = rng.uniform(-PSK31_TUNING_ERROR_HZ, PSK31_TUNING_ERROR_HZ)
            macro = cls is MessageClass.CONTEST_MACRO and over is not None
            if macro and over is not None:
                transmission_psk = psk31.modulate(over.macro_text, error_hz, frame_with_spaces=False)
            else:
                # Keyboard-to-keyboard text is typed in lower case, whose Varicode is shorter; contest software
                # sends capitals.
                sent_text = text.lower() if cls is MessageClass.TEXT else text
                transmission_psk = psk31.modulate(sent_text, error_hz)
            lead = round(rng.uniform(*LEAD_RANGE_S) * channel.SAMPLE_RATE)
            total = lead + len(transmission_psk.samples) + round(0.5 * channel.SAMPLE_RATE)
            reception = channel.receive(transmission_psk.samples, lead, total, snr_db, rng, channel.SSB_FILTER, fading)
            window = (lead + transmission_psk.message_start, lead + transmission_psk.message_end)
            started = time.perf_counter()
            received_psk = psk31.demodulate(reception.audio, snr_window=window)
            elapsed = time.perf_counter() - started
            if macro and over is not None:
                score = scoring.score_keyboard_macro(
                    over.macro, received_psk, window, psk31.SCORING_MARGIN, macro_fields
                )
                sent = scoring.normalize(over.macro_text)
            else:
                score = scoring.score_keyboard(
                    transmission_psk.text, received_psk, window, psk31.SCORING_MARGIN, fields
                )
                sent = transmission_psk.text
            airtime = len(transmission_psk.samples) / channel.SAMPLE_RATE
            row.update(
                freq_hz=psk31.CARRIER_HZ,
                start_s=round(lead / channel.SAMPLE_RATE, 4),
                tuning_error_hz=round(error_hz, 3),
            )
        else:
            raise ValueError(f"unknown mode {mode!r}")

        self.last_audio = reception.audio
        floor = self.config.snr_floors.get(mode)
        reported = score.reported_snr
        row.update(
            units=score.units,
            units_ok=score.units_ok,
            run_chars=score.run_chars,
            frames="" if score.frames is None else score.frames,
            copied=int(score.copied),
            cer=round(score.cer, 5),
            garbage=score.garbage,
            noise_prints=score.noise_prints,
            ap_filtered=score.ap_filtered,
            n_messages=score.n_messages,
            state=score.state,
            failure=score.failure,
            snr_true_measured_db=round(reception.snr_measured_db, 3),
            snr_reported_db="" if reported is None else round(reported, 2),
            snr_reported_floor=int(reported is not None and floor is not None and reported <= floor),
            sent=sent,
            printed=score.printed,
            airtime_s=round(airtime, 4),
            copied_chars=score.copied_chars,
            decode_s=round(elapsed, 4),
            clipped=reception.clipped,
            error="",
        )
        return row

    def run_noise(self, mode: str, chunk: int) -> dict[str, Any]:
        """Decode one noise-only reception and return its raw-data row (everything printed is false)."""
        seconds = NOISE_CHUNK_S[mode]
        rng = noise_rng(self.config.seed, mode, chunk)
        row: dict[str, Any] = {"mode": mode, "chunk": chunk, "seconds": seconds, "error": ""}
        try:
            for attempt in range(2):
                try:
                    started = time.perf_counter()
                    if mode in ("rtty", "psk31"):
                        rig = channel.RTTY_FILTER if mode == "rtty" else channel.SSB_FILTER
                        audio = channel.noise_only(round(seconds * channel.SAMPLE_RATE), rng, rig)
                        printed = rtty.demodulate(audio).text if mode == "rtty" else psk31.demodulate(audio).text
                        false_prints = sum(1 for char in printed if not char.isspace())
                        text = scoring.normalize(printed)
                    elif mode == "jtty":
                        audio = channel.noise_only(round(seconds * channel.SAMPLE_RATE), rng, channel.SSB_FILTER)
                        decoded = self.client().decode_jtty(audio)
                        false_prints = len(decoded)
                        text = " | ".join(m.text for m in decoded)
                    else:
                        label = mode.upper()
                        audio = channel.noise_only(codec.PERIOD_SAMPLES[label], rng, channel.SSB_FILTER)
                        found = {
                            scoring.normalize(d.message.rstrip("? "))
                            for d in self.client().decode_slot(label, audio)
                            if d.ap == 0
                        }
                        false_prints = len(found)
                        text = " | ".join(sorted(found))
                    row.update(
                        false_prints=false_prints, printed=text, decode_s=round(time.perf_counter() - started, 4)
                    )
                    return row
                except codec.CodecError:
                    self.close()
                    if attempt == 1:
                        raise
                    rng = noise_rng(self.config.seed, mode, chunk)
        except Exception as error:
            row.update(false_prints="", printed="", decode_s="", error=f"{type(error).__name__}: {error}")
        return row


_worker_runner: TrialRunner | None = None


def _init_worker(config: StudyConfig, wisdom: Path | None) -> None:
    """Pool initializer: give this worker process its own runner and codec working directory."""
    global _worker_runner
    workdir = config.run_dir / "work" / f"worker-{os.getpid()}"
    _worker_runner = TrialRunner(config, workdir, wisdom)
    multiprocessing.util.Finalize(None, _worker_runner.close, exitpriority=10)


def _run_task(task: Task | NoiseTask) -> list[dict[str, Any]]:
    """Pool entry point: run a batch of trials or noise receptions in this worker."""
    if _worker_runner is None:
        raise RuntimeError("worker not initialized")
    if isinstance(task, NoiseTask):
        return [_worker_runner.run_noise(task.mode, chunk) for chunk in task.chunks]
    return [_worker_runner.run(task.mode, task.cls, task.snr_db, trial, task.stage) for trial in task.trials]


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    """Return a CSV file's rows as dicts, as written (empty if the file does not exist); never rewrites the file."""
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


class CsvStore:
    """An append-only CSV of rows with fixed columns.

    A file written by an older version of the tool (different columns) is rewritten once with the current columns,
    the missing ones empty, so resumed runs never append misaligned rows.
    """

    def __init__(self, path: Path, fields: Sequence[str]) -> None:
        self.path = path
        self.fields = tuple(fields)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _migrate(self) -> None:
        """Rewrite the file with the current columns if its header differs."""
        if not self.path.exists():
            return
        with self.path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if tuple(reader.fieldnames or ()) == self.fields:
                return
            rows = list(reader)
        temporary = self.path.with_suffix(".migrating")
        with temporary.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows({name: row.get(name, "") for name in self.fields} for row in rows)
        temporary.replace(self.path)

    def rows(self) -> list[dict[str, str]]:
        """Return every row written so far (empty if the file does not exist yet)."""
        return read_csv_rows(self.path)

    def append(self, rows: Iterable[dict[str, Any]]) -> None:
        """Append rows, writing the header first if the file is new."""
        new = not self.path.exists()
        with self.path.open("a", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=self.fields, extrasaction="ignore")
            if new:
                writer.writeheader()
            writer.writerows(rows)


class TrialStore(CsvStore):
    """The raw trial data (raw/trials.csv)."""

    def __init__(self, path: Path) -> None:
        super().__init__(path, ROW_FIELDS)

    def done_keys(self) -> set[tuple[str, str, int, int]]:
        """Return the keys of trials already completed without error."""
        return {
            trial_key(row["mode"], row["cls"], float(row["snr_db"]), int(row["trial"]))
            for row in self.rows()
            if not row.get("error")
        }


class NoiseStore(CsvStore):
    """The noise-only receptions (raw/noise.csv)."""

    def __init__(self, path: Path) -> None:
        super().__init__(path, NOISE_FIELDS)

    def done_keys(self) -> set[tuple[str, int]]:
        """Return the (mode, chunk) keys already decoded without error."""
        return {(row["mode"], int(row["chunk"])) for row in self.rows() if not row.get("error")}


UnitCounts = dict[float, tuple[int, int, int]]
"""Per SNR of one series: (copied, sent, trials), error rows left out. For grid planning these are characters copied in
runs (run_chars) and characters sent (a contest macro: 1 of 1 when copied)."""


def unit_counts(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], UnitCounts]:
    """Return the grid-planning counts per (mode, class) and SNR from raw rows: characters copied in runs
    (run_chars), characters sent, and trials.

    Characters sent come from the stored sent text, not from ``units`` (5-character groups); a contest macro counts
    as one unit, as its run_chars does.
    """
    totals: dict[tuple[str, str], dict[float, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0, 0]))
    for row in rows:
        if row.get("error"):
            continue
        cell = totals[(str(row["mode"]), str(row["cls"]))][float(row["snr_db"])]
        macro = str(row["cls"]) == str(MessageClass.CONTEST_MACRO)
        cell[0] += int(row["run_chars"])
        cell[1] += int(row["units"]) if macro else max(len(scoring.normalize(str(row["sent"]))), 1)
        cell[2] += 1
    return {key: {snr: (ok, n, t) for snr, (ok, n, t) in grid.items()} for key, grid in totals.items()}


def monotone_rates(counts: UnitCounts) -> dict[float, float]:
    """Return each SNR's decode fraction, fitted to never fall as SNR rises (weighted pool-adjacent-violators).

    A decode curve rises with SNR. Fitting it that way pools a lone chance failure at a strong signal, or a chance
    copy deep in the noise, into its neighbors, so one unlucky pilot point cannot stretch the sweep or the fine
    grid by tens of dB. SNRs with no units sent are left out.
    """
    snrs = [snr for snr in sorted(counts) if counts[snr][1]]
    blocks: list[list[int]] = []
    for snr in snrs:
        ok, n, _ = counts[snr]
        blocks.append([ok, n, 1])
        while len(blocks) > 1 and blocks[-2][0] * blocks[-1][1] > blocks[-1][0] * blocks[-2][1]:
            ok_last, n_last, points = blocks.pop()
            blocks[-1][0] += ok_last
            blocks[-1][1] += n_last
            blocks[-1][2] += points

    fitted: dict[float, float] = {}
    index = 0
    for ok, n, points in blocks:
        for snr in snrs[index : index + points]:
            fitted[snr] = ok / n
        index += points
    return fitted


def pilot_grid(
    base: Sequence[float], counts: UnitCounts, step: float = PILOT_STEP_DB, floor: float = PILOT_FLOOR_DB
) -> tuple[float, ...]:
    """Return one series' pilot SNRs: ``base``, extended downward until the curve is down to CURVE_LOW.

    The sweep steps down while the (monotone-fitted) decode fraction at its lowest point is still above CURVE_LOW,
    no lower than ``floor``. It never extends above the base range. The first point without data ends the walk, so
    the result names the next point to measure; the caller measures it and asks again until the grid stops changing.
    """
    rates = monotone_rates(counts)
    grid = sorted({float(snr) for snr in base})
    while grid[0] in rates and rates[grid[0]] > CURVE_LOW and grid[0] - step >= floor - 1e-9:
        grid.insert(0, round(grid[0] - step, 2))
    return tuple(grid)


def fine_grid(rates: dict[float, float], step: float, margin: float, bounds: tuple[float, float]) -> tuple[float, ...]:
    """Return the fine SNR grid covering the pilot curve's 1%-99% span plus ``margin`` dB each side."""
    snrs = sorted(rates)
    rising = [snr for snr in snrs if rates[snr] > CURVE_LOW]
    unsaturated = [snr for snr in snrs if rates[snr] < CURVE_HIGH]
    low = min(rising) if rising else snrs[-1]
    high = max(unsaturated) if unsaturated else snrs[0]
    if high < low:
        low, high = high, low
    first = max(bounds[0], math.floor((low - margin) / step) * step)
    last = min(bounds[1], math.ceil((high + margin) / step) * step)
    count = round((last - first) / step) + 1
    return tuple(round(first + i * step, 2) for i in range(count))


def _file_sha256(path: Path) -> str:
    """Return the hex SHA-256 of a file."""
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _wsjtx_version() -> str:
    """Return the WSJT-X version of this source tree, as recorded by its build."""
    version_file = messages.TREE_ROOT / "build" / "wsjtx_config.h"
    if version_file.exists():
        for line in version_file.read_text(errors="replace").splitlines():
            if "WSJTX_VERSION_STRING" in line or "WSJTX_RC" in line:
                return line.strip()
    return messages.TREE_ROOT.name


def write_config(config: StudyConfig) -> None:
    """Record the run's settings in config.json, refusing to resume a run made with another seed or channel."""
    path = config.run_dir / "config.json"
    if path.exists():
        stored = json.loads(path.read_text())
        if stored.get("seed") != config.seed:
            raise SystemExit(f"{path} was made with seed {stored.get('seed')}; resume with the same --seed")
        if stored.get("channel", "awgn") != config.channel:
            raise SystemExit(f"{path} was made with channel {stored.get('channel', 'awgn')}; use the same --channel")
        if stored.get("content_version", 1) != CONTENT_VERSION:
            raise SystemExit(
                f"{path} was made by an older version of this tool whose messages differ; start a new run "
                "(the old run can still be analyzed and reported as it is)"
            )
        if stored.get("unit_scoring") != scoring.UNIT_SCORING:
            raise SystemExit(
                f"{path} was scored in {stored.get('unit_scoring') or 'the old units'}, not {scoring.UNIT_SCORING}; "
                "rescore it into a new directory (the rescore command) or start a new run, rather than mixing units"
            )
        if stored.get("snr_floors") and stored["snr_floors"] != config.snr_floors:
            raise SystemExit(
                f"{path} was made with decoder SNR floors {stored['snr_floors']}, but the decoders now use "
                f"{config.snr_floors}; start a new run rather than mixing decoders"
            )

    data = asdict(config)
    fading = config.fading
    data.update(
        content_version=CONTENT_VERSION,
        unit_scoring=scoring.UNIT_SCORING,
        pilot_floor_db=PILOT_FLOOR_DB,
        run_dir=str(config.run_dir),
        codec_binary=str(config.codec_binary),
        callsign_file=str(config.callsign_file),
        codec_sha256=_file_sha256(config.codec_binary) if config.codec_binary.exists() else None,
        wsjtx_version=_wsjtx_version(),
        written=datetime.now(UTC).isoformat(timespec="seconds"),
        host=platform.node(),
        python=sys.version.split()[0],
        fading=asdict(fading) if fading is not None else None,
        receiver={
            "ssb_filter_hz": [channel.SSB_FILTER.low_hz, channel.SSB_FILTER.high_hz],
            "rtty_filter_hz": [channel.RTTY_FILTER.low_hz, channel.RTTY_FILTER.high_hz],
            "noise_meter_db": channel.NOISE_METER_DB,
            "reference_bandwidth_hz": channel.REFERENCE_BANDWIDTH_HZ,
            "jtty_rx_hz": codec.JTTY_RX_HZ,
            "jtty_ftol_hz": codec.JTTY_FTOL_HZ,
            "jtty_offset_hz": JTTY_OFFSET_HZ,
            "decode_band_hz": [codec.DECODE_LOW_HZ, codec.DECODE_HIGH_HZ],
            "rtty_tuning_error_hz": RTTY_TUNING_ERROR_HZ,
            "rtty": {
                "baud": rtty.BAUD,
                "mark_hz": rtty.MARK_HZ,
                "space_hz": rtty.SPACE_HZ,
                "stop_bits": rtty.STOP_BITS,
            },
            "psk31_tuning_error_hz": PSK31_TUNING_ERROR_HZ,
            "psk31": {
                "baud": psk31.BAUD,
                "carrier_hz": psk31.CARRIER_HZ,
                "preamble_bits": psk31.PREAMBLE_BITS,
                "postamble_bits": psk31.POSTAMBLE_BITS,
                "text_case": "lower",
                "contest_case": "upper",
            },
            "ft_decoder": {"depth": 3, "ap": False},
        },
    )
    path.write_text(json.dumps(data, indent=2) + "\n")


def _psk31_entry(text: str, frame_with_spaces: bool) -> dict[str, Any]:
    """Return the bits and airtime of one PSK31 transmission of ``text`` (case as given).

    ``units`` counts the characters sent (framing spaces and CR/LF included); information bits are 5 per printed
    message character, the same yardstick as RTTY, and channel bits are every bit sent (preamble, Varicode, gaps and
    postamble).
    """
    transmission = psk31.modulate(text, frame_with_spaces=frame_with_spaces)
    printed = scoring.normalize(text)
    return {
        "text": printed,
        "airtime_s": round(len(transmission.samples) / channel.SAMPLE_RATE, 4),
        "units": len(text) + (2 if frame_with_spaces else 0),
        "info_bits": 5 * len(printed),
        "channel_bits": transmission.bits,
    }


def _rtty_entry(text: str, frame_with_spaces: bool) -> dict[str, Any]:
    """Return the bits and airtime of one RTTY transmission of ``text``.

    ``units`` counts every Baudot character sent (idle, framing and shift characters included); information bits are
    5 per printed message character, so shifts, idle, start and stop bits are overhead, as in the Eb/N0 basis.
    """
    transmission = rtty.modulate(text, frame_with_spaces=frame_with_spaces)
    total_bits = len(transmission.samples) / rtty.SAMPLES_PER_BIT
    codes = round((total_bits - rtty.LEAD_MARK_S * rtty.BAUD) / (1 + 5 + rtty.STOP_BITS))
    printed = scoring.normalize(text)
    return {
        "text": printed,
        "airtime_s": round(len(transmission.samples) / channel.SAMPLE_RATE, 4),
        "units": codes,
        "info_bits": 5 * len(printed),
        "channel_bits": round(total_bits, 1),
    }


def build_reference(config: StudyConfig, client: codec.CodecClient | None) -> dict[str, Any]:
    """Return the per-mode measured bandwidths and the reference contest QSOs' frames, bits and airtimes."""
    measured: dict[str, dict[str, float]] = {}
    if client is not None:
        jtty_render = client.render_jtty("THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG", 1500.0)
        low, high, width = channel.occupied_bandwidth(jtty_render.samples)
        measured["jtty"] = {"low_hz": low, "high_hz": high, "bw99_hz": width}
        for label in ("FT8", "FT4"):
            slot = client.render_slot(label, "K1ABC W9XYZ EN37", 1500.0)
            low, high, width = channel.occupied_bandwidth(slot.samples)
            measured[label.lower()] = {"low_hz": low, "high_hz": high, "bw99_hz": width}
    long_text = rtty.modulate("THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG 0123456789 " * 4)
    low, high, width = channel.occupied_bandwidth(long_text.samples)
    measured["rtty"] = {"low_hz": low, "high_hz": high, "bw99_hz": width}
    long_psk = psk31.modulate("the quick brown fox jumps over the lazy dog 0123456789 " * 4)
    low, high, width = channel.occupied_bandwidth(long_psk.samples)
    measured["psk31"] = {"low_hz": low, "high_hz": high, "bw99_hz": width}

    qsos: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for contest, families in messages.reference_qsos().items():
        per_mode: dict[str, list[dict[str, Any]]] = {}
        for over in families["keyboard"]:
            base = {"kind": over.kind, "sender": over.sender}
            if client is not None and "jtty" in config.modes:
                for variant, fields in (("jtty", over.jtty), ("jtty-macro", over.macro_words)):
                    render = client.render_jtty(fields, 1500.0)
                    per_mode.setdefault(variant, []).append(
                        {
                            **base,
                            "text": render.canonical,
                            "frames": list(render.frames),
                            "units": len(render.frames),
                            "airtime_s": round(len(render.samples) / channel.SAMPLE_RATE, 4),
                            "info_bits": len(render.frames) * modes.MODES["jtty"].info_bits,
                            "channel_bits": len(render.frames) * modes.MODES["jtty"].channel_bits,
                        }
                    )
            per_mode.setdefault("rtty", []).append({**base, **_rtty_entry(over.text, True)})
            per_mode.setdefault("rtty-macro", []).append({**base, **_rtty_entry(over.macro_text, False)})
            per_mode.setdefault("psk31", []).append({**base, **_psk31_entry(over.text, True)})
            per_mode.setdefault("psk31-macro", []).append({**base, **_psk31_entry(over.macro_text, False)})
        for over in families["ft"]:
            for mode in ("ft8", "ft4"):
                if client is None or mode not in config.modes:
                    continue
                info = modes.MODES[mode]
                msgsent = client.render_slot(mode.upper(), over.text, 1500.0).msgsent
                per_mode.setdefault(mode, []).append(
                    {
                        "kind": over.kind,
                        "sender": over.sender,
                        "text": msgsent,
                        "units": 1,
                        "airtime_s": round(info.frame_s, 4),
                        "slot_s": info.slot_s,
                        "info_bits": info.info_bits,
                        "channel_bits": info.channel_bits,
                    }
                )
        qsos[str(contest)] = per_mode
    return {"mode_info_measured": measured, "qso": qsos, "turnaround_s": config.turnaround_s}


def warm_up(config: StudyConfig) -> Path | None:
    """Prepare the run: save FFTW wisdom for the workers and write reference.json (bandwidths, reference QSOs).

    One JTTY, FT8 and FT4 sample is decoded in a throwaway codec so its wisdom file exists. Returns the wisdom
    path, or None when no codec mode is run.
    """
    uses_codec = any(mode in config.modes for mode in ("jtty", "ft8", "ft4"))
    if not uses_codec:
        reference = build_reference(config, None)
        (config.run_dir / "reference.json").write_text(json.dumps(reference, indent=1) + "\n")
        return None
    workdir = config.run_dir / "work" / "warmup"
    shutil.rmtree(workdir, ignore_errors=True)
    rng = np.random.default_rng(0)
    with codec.CodecClient(config.codec_binary, workdir) as client:
        render = client.render_jtty("CQ K1ABC CQ", codec.JTTY_RX_HZ)
        total = 12000 + len(render.samples) + JTTY_FRAME_SAMPLES
        client.decode_jtty(channel.receive(render.samples, 12000, total, -10.0, rng, channel.SSB_FILTER).audio)
        for label in ("FT8", "FT4"):
            slot = client.render_slot(label, "CQ K1ABC FN42", 1500.0)
            audio = channel.receive(slot.samples, 6000, codec.PERIOD_SAMPLES[label], -10.0, rng, channel.SSB_FILTER)
            client.decode_slot(label, audio.audio)
        reference = build_reference(config, client)
    (config.run_dir / "reference.json").write_text(json.dumps(reference, indent=1) + "\n")
    wisdom = workdir / codec.WISDOM_FILE
    return wisdom if wisdom.exists() else None


def _plan_tasks(
    config: StudyConfig,
    grids: Mapping[tuple[str, MessageClass], Sequence[float]],
    trials: int,
    stage: str,
    done: set[tuple[str, str, int, int]],
) -> list[Task]:
    """Return the batches of not-yet-done trials for the given grids."""
    tasks: list[Task] = []
    for (mode, cls), snrs in grids.items():
        for snr_db in snrs:
            pending = [t for t in range(trials) if trial_key(mode, cls, snr_db, t) not in done]
            for start in range(0, len(pending), config.batch_size):
                batch = tuple(pending[start : start + config.batch_size])
                tasks.append(Task(mode=mode, cls=cls, snr_db=snr_db, trials=batch, stage=stage))
    # Long tasks first keeps the pool busy to the end.
    weight = {"jtty": 2.0, "ft8": 3.0, "ft4": 1.5, "rtty": 0.5, "psk31": 0.5}
    tasks.sort(key=lambda task: -weight[task.mode] * len(task.trials) * (3.0 if task.cls == "text" else 1.0))
    return tasks


def _plan_noise(config: StudyConfig, done: set[tuple[str, int]]) -> list[NoiseTask]:
    """Return the batches of not-yet-decoded noise-only receptions (about 5 minutes of audio per task)."""
    tasks: list[NoiseTask] = []
    for mode in config.modes:
        chunk_s = NOISE_CHUNK_S[mode]
        count = math.ceil(config.noise_hours * 3600.0 / chunk_s)
        per_task = max(1, round(300.0 / chunk_s))
        pending = [chunk for chunk in range(count) if (mode, chunk) not in done]
        tasks += [NoiseTask(mode, tuple(pending[i : i + per_task])) for i in range(0, len(pending), per_task)]
    return tasks


def _execute(
    pool: ProcessPoolExecutor, tasks: Sequence[Task | NoiseTask], store: CsvStore, label: str, unit: str = "trials"
) -> None:
    """Run tasks on the pool, appending rows as batches finish and printing progress."""
    if not tasks:
        print(f"[{label}] nothing to do", file=sys.stderr)
        return
    total = sum(len(task.chunks) if isinstance(task, NoiseTask) else len(task.trials) for task in tasks)
    finished = errors = 0
    started = last_report = time.monotonic()
    pending: set[Future[list[dict[str, Any]]]] = {pool.submit(_run_task, task) for task in tasks}
    while pending:
        done, pending = wait(pending, timeout=5.0, return_when=FIRST_COMPLETED)
        for future in done:
            rows = future.result()
            store.append(rows)
            finished += len(rows)
            errors += sum(1 for row in rows if row.get("error"))
        now = time.monotonic()
        if now - last_report >= 10.0 or not pending:
            rate = finished / max(now - started, 1e-9)
            eta = (total - finished) / rate if rate > 0 else float("inf")
            print(
                f"[{label}] {finished}/{total} {unit}, {rate:.1f}/s, {errors} errors, ETA {eta / 60:.1f} min",
                file=sys.stderr,
            )
            last_report = now


def _pilot_counts(store: TrialStore) -> dict[tuple[str, str], UnitCounts]:
    """Return the unit counts of the run's pilot rows per (mode, class) and SNR."""
    return unit_counts(row for row in store.rows() if row.get("stage") == "pilot")


def _sweep_pilot(
    config: StudyConfig,
    pairs: Sequence[tuple[str, MessageClass]],
    store: TrialStore,
    measure: Callable[[dict[tuple[str, MessageClass], tuple[float, ...]]], None],
) -> dict[tuple[str, MessageClass], tuple[float, ...]]:
    """Run the pilot pass: measure the base grid, then extend each series downward until it is down to 1%.

    ``measure`` runs the not-yet-done pilot trials of the grids it is given. Each round adds at most one point to
    each series, and the loop ends when a round leaves every grid unchanged, so it also ends when some trials keep
    failing. Returns the final pilot grid of each pair.
    """
    grids = {pair: tuple(config.pilot_snrs) for pair in pairs}
    while True:
        measure(grids)
        counts = _pilot_counts(store)
        extended = {(mode, cls): pilot_grid(config.pilot_snrs, counts.get((mode, str(cls)), {})) for mode, cls in pairs}
        if extended == grids:
            return grids
        for (mode, cls), grid in extended.items():
            if grid != grids[(mode, cls)]:
                print(f"[pilot] {mode}/{cls}: extending to {grid[0]:+.0f} to {grid[-1]:+.0f} dB", file=sys.stderr)
        grids = extended


def _fine_grids(
    config: StudyConfig, pairs: Sequence[tuple[str, MessageClass]], store: TrialStore
) -> dict[tuple[str, MessageClass], tuple[float, ...]]:
    """Return the fine SNR grid of each mode and class.

    Grids are computed from the pilot rows and saved in grids.json once a pair's pilot is complete (every point of
    its extended pilot grid has its pilot trials), so a resumed or extended run fills the same points instead of a
    grid that drifts as trials accumulate. Saved grids are reused only while the pilot SNRs, extension floor, fine
    step, margin and channel are unchanged; delete grids.json to force a recomputation. The fine grid never reaches
    above the top pilot SNR.
    """
    path = config.run_dir / "grids.json"
    settings = {
        "pilot_snrs": list(config.pilot_snrs),
        "pilot_floor_db": PILOT_FLOOR_DB,
        "fine_step_db": config.fine_step_db,
        "fine_margin_db": config.fine_margin_db,
        "channel": config.channel,
    }
    stored = json.loads(path.read_text()) if path.exists() else {}
    saved: dict[str, list[float]] = stored.get("grids", {}) if stored.get("settings") == settings else {}

    counts = _pilot_counts(store)
    grids: dict[tuple[str, MessageClass], tuple[float, ...]] = {}
    for mode, cls in pairs:
        name = f"{mode}/{cls}"
        if name in saved:
            grids[(mode, cls)] = tuple(saved[name])
            continue
        curve = counts.get((mode, str(cls)), {})
        rates = monotone_rates(curve)
        if not rates:
            raise SystemExit(f"no pilot data for {name}; run with the pilot enabled")
        bounds = (PILOT_FLOOR_DB, max(config.pilot_snrs))
        grids[(mode, cls)] = fine_grid(rates, config.fine_step_db, config.fine_margin_db, bounds)
        pilot = pilot_grid(config.pilot_snrs, curve)
        if all(snr in curve and curve[snr][2] >= config.pilot_trials for snr in pilot):
            saved[name] = list(grids[(mode, cls)])
    path.write_text(json.dumps({"settings": settings, "grids": saved}, indent=1) + "\n")
    return grids


def run_study(config: StudyConfig) -> None:
    """Run (or resume) the sweep and noise stage described by ``config``; writes raw/, config.json, reference.json."""
    if any(mode in config.modes for mode in ("jtty", "ft8", "ft4")) and not config.codec_binary.exists():
        raise SystemExit(f"wsjt-codec not found at {config.codec_binary}; build it or pass --codec")
    if config.channel.lower() != "awgn" and config.fading is None:
        raise SystemExit(f"unknown channel {config.channel!r}; choose awgn or {', '.join(channel.FADING_MODELS)}")
    config.run_dir.mkdir(parents=True, exist_ok=True)
    write_config(config)
    store = TrialStore(config.run_dir / "raw" / "trials.csv")
    noise_store = NoiseStore(config.run_dir / "raw" / "noise.csv")
    pairs = [(mode, cls) for mode in config.modes for cls in MODE_CLASSES[mode]]

    print(f"Run directory: {config.run_dir}", file=sys.stderr)
    wisdom = warm_up(config)
    with ProcessPoolExecutor(max_workers=config.workers, initializer=_init_worker, initargs=(config, wisdom)) as pool:
        try:
            if config.fixed_snrs:
                grids = {pair: config.fixed_snrs for pair in pairs}
                _execute(pool, _plan_tasks(config, grids, config.trials, "fixed", store.done_keys()), store, "fixed")
            else:
                if config.pilot:

                    def measure(pilot: dict[tuple[str, MessageClass], tuple[float, ...]]) -> None:
                        tasks = _plan_tasks(config, pilot, config.pilot_trials, "pilot", store.done_keys())
                        _execute(pool, tasks, store, "pilot")

                    _sweep_pilot(config, pairs, store, measure)
                grids = _fine_grids(config, pairs, store)
                for (mode, cls), grid in grids.items():
                    print(f"[fine] {mode}/{cls}: {grid[0]:+.1f} to {grid[-1]:+.1f} dB", file=sys.stderr)
                _execute(pool, _plan_tasks(config, grids, config.trials, "fine", store.done_keys()), store, "fine")
            if config.noise_hours > 0:
                _execute(pool, _plan_noise(config, noise_store.done_keys()), noise_store, "noise", "receptions")
        except KeyboardInterrupt:
            pool.shutdown(wait=False, cancel_futures=True)
            print("Interrupted: finished trials are saved; run the same command again to resume.", file=sys.stderr)
            raise


def write_wav(path: Path, audio: npt.NDArray[np.int16]) -> None:
    """Write 12 kHz mono 16-bit audio as a WAV file (WSJT-X can open it with File | Open)."""
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(int(channel.SAMPLE_RATE))
        out.writeframes(np.asarray(audio, dtype="<i2").tobytes())


def replay_trial(
    run_dir: Path, mode: str, cls: MessageClass, snr_db: float, trial: int, codec_binary: Path, wav: Path | None
) -> dict[str, Any]:
    """Re-run one trial of an existing run exactly (same seed and channel) and optionally save its audio as a WAV."""
    config = StudyConfig.from_run(run_dir, codec_binary)
    workdir = run_dir / "work" / "replay"
    runner = TrialRunner(config, workdir, None)
    try:
        row = runner.run(mode, cls, snr_db, trial, "replay")
    finally:
        runner.close()
    key = trial_key(mode, str(cls), snr_db, trial)
    recorded = next(
        (
            stored
            for stored in read_csv_rows(run_dir / "raw" / "trials.csv")
            if not stored.get("error")
            and trial_key(stored["mode"], stored["cls"], float(stored["snr_db"]), int(stored["trial"])) == key
        ),
        None,
    )
    if recorded is not None and recorded["sent"] != row.get("sent") and not row.get("error"):
        row["error"] = (
            f"replay mismatch: this run recorded {recorded['sent']!r} but the trial now regenerates "
            f"{row.get('sent')!r} (the run was made with an older tool version for this message class)"
        )
    if wav is not None and runner.last_audio is not None:
        write_wav(wav, runner.last_audio)
    return row


RESCORE_COPIED_FILES: tuple[str, ...] = ("grids.json", "reference.json", "raw/noise.csv")
"""Files of a run that rescore_run copies unchanged (when present)."""


def rescore_row(row: dict[str, str]) -> dict[str, Any]:
    """Return a raw trial row rescored in 5-character groups (see rescore_run); other columns are unchanged.

    JTTY rows keep their frame count: the ``frames`` column when the source has one, else the old unit count (an
    original run counted JTTY frames). RTTY and PSK31 rows also get the characters copied for throughput and the
    failure class the keyboard scorer now gives (``missed`` when a group is not copied, from the stored garbage count
    for ``false_text``). Every row gets ``run_chars`` for grid planning (a macro: 1 when copied). JTTY, RTTY and PSK31
    contest overs and macros get whole-message copy under the ambiguity rule (rescore_contest_copy). Error rows are
    returned as they were; old JTTY macro rows have no frame count.
    """
    rescored: dict[str, Any] = {name: row.get(name, "") for name in ROW_FIELDS}
    if row.get("error"):
        return rescored
    if row["cls"] == str(MessageClass.CONTEST_MACRO):
        rescored["run_chars"] = row["units_ok"]
        rescored.update(rescore_contest_copy(row, rescored))
        return rescored
    mode = row["mode"]
    counts = scoring.group_counts(mode, row["sent"], row["printed"], row["copied"] == "1")
    units, units_ok = counts.sent, counts.copied
    rescored.update(units=units, units_ok=units_ok, run_chars=counts.in_runs)
    if mode == "jtty":
        rescored["frames"] = row.get("frames") or row["units"]
    elif mode in ("rtty", "psk31"):
        flags = (("missed", units_ok < units), ("false_text", int(row["garbage"] or 0) > 0))
        rescored.update(copied_chars=counts.characters, failure=";".join(name for name, flag in flags if flag))
    if row["cls"] == str(MessageClass.CONTEST):
        rescored.update(rescore_contest_copy(row, rescored))
    return rescored


KEYBOARD_CONTEST_MODES: tuple[str, ...] = ("jtty", "rtty", "psk31")
"""Modes whose contest overs and macros follow the ambiguity rule (FT8/FT4 decodes are whole, CRC-checked messages)."""


def rescore_contest_copy(row: Mapping[str, str], rescored: Mapping[str, Any]) -> dict[str, Any]:
    """Return the columns a contest over or macro row changes under the ambiguity rule (scoring.ambiguous_fields).

    A macro's copy is recomputed from its sent and printed words (scoring.macro_verdict), with its units, run_chars,
    copied_chars, failure and, when it is no longer copied, its reported SNR (a macro reports SNR only for a copy).
    A plain over keeps its stored exact-copy verdict, which now also needs no ambiguous field; ``ambiguous`` joins
    its failure classes. FT8/FT4 rows and rows whose words don't fit their over's structure are left unchanged.
    """
    mode, macro = row["mode"], row["cls"] == str(MessageClass.CONTEST_MACRO)
    groups = scoring.field_groups(row["msg_kind"], macro)
    information = scoring.field_groups(row["msg_kind"], macro, True)
    sent_words = scoring.normalize(row["sent"]).split()
    if mode not in KEYBOARD_CONTEST_MODES or groups is None or information is None or len(groups) != len(sent_words):
        return {}
    printed = scoring.jtty_readable(row["printed"]) if mode == "jtty" else scoring.normalize(row["printed"])
    printed_words = printed.split()
    false_text = int(row["garbage"] or 0) > 0
    if macro:
        tokens = [MacroToken(word, group) for word, group in zip(sent_words, groups, strict=True)]
        exact, ambiguous = scoring.macro_verdict(tokens, printed_words, information)
        copied = exact and not ambiguous
        changes: dict[str, Any] = {
            "copied": int(copied),
            "units_ok": int(copied),
            "run_chars": int(copied),
            "copied_chars": len(" ".join(sent_words)) if copied else 0,
            "failure": scoring.copy_failure(exact, ambiguous, false_text),
        }
        if not copied:
            changes.update(snr_reported_db="", snr_reported_floor=0)
        return changes
    exact = row["copied"] == "1"
    if not (exact and scoring.ambiguous_fields(sent_words, information, printed_words)):
        return {}
    parts = [part for part in str(rescored["failure"]).split(";") if part and part != "false_text"]
    parts += ["ambiguous", *(["false_text"] if "false_text" in str(rescored["failure"]).split(";") else [])]
    return {"copied": 0, "failure": ";".join(parts)}


def rescore_run(source: Path, target: Path) -> int:
    """Copy a run scored in other units into ``target``, scored in 5-character groups.

    Nothing is decoded again: each trial is rescored from the sent and printed text in raw/trials.csv, exactly as
    the trial scorers score it live (scoring.group_counts). RTTY and PSK31 reported SNRs are kept as the source
    stored them (an original run stored them only for trials that copied a whole group on the old alignment). The target
    must not exist yet or be empty; the source is only read. Returns the number of rows written.
    """
    config_path = source / "config.json"
    trials_path = source / "raw" / "trials.csv"
    if not config_path.exists() or not trials_path.exists():
        raise SystemExit(f"{source} has no config.json and raw/trials.csv to rescore")
    config = json.loads(config_path.read_text())
    if config.get("unit_scoring") == scoring.UNIT_SCORING:
        raise SystemExit(f"{source} is already scored in {scoring.UNIT_SCORING}")
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f"{target} already exists and is not empty; choose a new directory")

    (target / "raw").mkdir(parents=True, exist_ok=True)
    for name in RESCORE_COPIED_FILES:
        if (source / name).exists():
            shutil.copyfile(source / name, target / name)
    rows = [rescore_row(row) for row in read_csv_rows(trials_path)]
    temporary = target / "raw" / "trials.csv.writing"
    with temporary.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=ROW_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(target / "raw" / "trials.csv")
    config.update(
        run_dir=str(target),
        unit_scoring=scoring.UNIT_SCORING,
        rescored_from=str(source),
        rescored=datetime.now(UTC).isoformat(timespec="seconds"),
    )
    (target / "config.json").write_text(json.dumps(config, indent=2) + "\n")
    return len(rows)
