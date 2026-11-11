"""Randomized message traffic for the trials, and the reference contest QSOs.

Message classes, each drawn fresh for every trial from a seeded generator:

- ``contest``: one over of a reference contest QSO (ARRL RTTY Roundup or a serial-number contest; CQ, call,
  exchanges, TU or an AGN? request), as the JTTY design document's run sequence (lib/jtty/jtty_design.md). JTTY
  sends the exchanges as native function-key atoms; RTTY sends the same text.
- ``contest-macro``: the same overs as RTTY contesters' typical macros (CR/LF framing, exchange sent twice).
- ``text``: keyboard-to-keyboard ragchew sentences, about 4-10 JTTY frames. Sent by JTTY and RTTY.
- ``qso``: one over of the FT8/FT4 equivalent QSO (RU contest mode, or the standard QSO).

Callsigns come from ALLCALL7.TXT at the top of the WSJT-X tree (about 100,000 calls spotted in FT8/FT4), limited
to standard calls -- no portable/compound forms -- so every one fits a single JTTY call atom and a non-hashed
FT8/FT4 call field. All generated text uses only characters JTTY and US-TTY Baudot both carry.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np

TREE_ROOT: Path = Path(__file__).resolve().parents[4]
"""Root of the WSJT-X source tree this study lives in (contrib/jtty_snr_study/src/jtty_snr_study/...)."""

DEFAULT_CALLSIGN_FILE: Path = TREE_ROOT / "ALLCALL7.TXT"
"""Callsign database shipped with WSJT-X."""

STANDARD_CALL: re.Pattern[str] = re.compile(r"^(?:[A-Z]{1,2}|[A-Z][0-9]|[0-9][A-Z])[0-9][A-Z]{1,3}$")
"""A standard amateur call: 1-2 character prefix, one digit, 1-3 letter suffix (as FT8 packs without hashing)."""


class MessageClass(StrEnum):
    """Kind of traffic a trial sends."""

    CONTEST = "contest"
    TEXT = "text"
    QSO = "qso"
    CONTEST_MACRO = "contest-macro"


class Contest(StrEnum):
    """The contest a reference QSO belongs to."""

    RU = "ru"
    SERIAL = "serial"


CONTEST_LABELS: dict[str, str] = {"ru": "ARRL RTTY Roundup", "serial": "Serial-number contest"}
"""Display names of the contests."""


US_STATES: tuple[str, ...] = (
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
)
"""State abbreviations used as RTTY Roundup-style QTH exchanges."""

_NAMES = ("TOM", "BOB", "JOE", "ANN", "SUE", "BILL", "MIKE", "DAVE", "JIM", "MARY", "PAT", "STEVE", "KEN", "LIZ")
_CITIES = ("BOSTON", "DENVER", "AUSTIN", "SEATTLE", "TOLEDO", "MIAMI", "OMAHA", "TAMPA", "RENO", "DALLAS", "DULUTH")
_RIGS = ("IC7300", "FT991A", "K3", "TS590", "FTDX10", "IC7610", "K4", "FLEX6600")
_ANTENNAS = ("DIPOLE", "VERT", "YAGI", "EFHW", "LOOP", "G5RV", "HEXBEAM")
_WEATHER = ("SUNNY", "RAIN", "SNOW", "CLOUDY", "WINDY", "FOG")

TEXT_MIN_CHARS: int = 18
"""Shortest ragchew sentence; with TEXT5 packing this is about 4 frames."""

TEXT_MAX_CHARS: int = 50
"""Longest ragchew sentence; about 10 frames, well inside JTTY's 80-character, 16-frame limit."""


def load_callsigns(path: Path = DEFAULT_CALLSIGN_FILE) -> tuple[str, ...]:
    """Read the standard-form calls from a JTDX/WSJT-X callsign file (``//`` comments, ``CALL,...`` lines).

    Raises FileNotFoundError if the file is missing and ValueError if it has fewer than 100 usable calls.
    """
    calls: set[str] = set()
    with path.open(encoding="latin-1") as stream:
        for line in stream:
            if line.startswith("//"):
                continue
            call = line.split(",", 1)[0].strip().upper()
            if STANDARD_CALL.match(call):
                calls.add(call)
    if len(calls) < 100:
        raise ValueError(f"{path} has only {len(calls)} standard callsigns")
    return tuple(sorted(calls))


def _pick[T](rng: np.random.Generator, items: Sequence[T]) -> T:
    """Return a uniformly chosen element of a non-empty sequence."""
    return items[int(rng.integers(len(items)))]


def _two_calls(rng: np.random.Generator, calls: Sequence[str]) -> tuple[str, str]:
    """Return two different random calls."""
    first = _pick(rng, calls)
    second = _pick(rng, calls)
    while second == first:
        second = _pick(rng, calls)
    return first, second


def text_message(rng: np.random.Generator) -> str:
    """Return one random keyboard-to-keyboard sentence of ``TEXT_MIN_CHARS``-``TEXT_MAX_CHARS`` characters."""
    phrases = [
        f"NAME IS {_pick(rng, _NAMES)}",
        f"QTH {_pick(rng, _CITIES)}",
        f"RIG {_pick(rng, _RIGS)}",
        f"ANT {_pick(rng, _ANTENNAS)}",
        f"WX {_pick(rng, _WEATHER)} {int(rng.integers(10, 100))}F",
        f"UR RST {_pick(rng, ('559', '569', '579', '589', '599'))}",
        f"PWR {_pick(rng, ('5', '10', '50', '100', '500'))}W",
        "TNX FER CALL",
        "HW CPY?",
        "73 ES GUD DX",
    ]
    order = rng.permutation(len(phrases))
    words: list[str] = []
    for index in order:
        candidate = " ".join([*words, phrases[int(index)]])
        if len(candidate) > TEXT_MAX_CHARS:
            continue
        words.append(phrases[int(index)])
        if len(candidate) >= TEXT_MIN_CHARS and rng.random() < 0.5:
            break
    return " ".join(words)


def _grid(rng: np.random.Generator) -> str:
    """Return a random 4-character Maidenhead locator (never RR73, which FT8 reads as a sign-off)."""
    while True:
        grid = (
            chr(ord("A") + int(rng.integers(18)))
            + chr(ord("A") + int(rng.integers(18)))
            + str(int(rng.integers(10)))
            + str(int(rng.integers(10)))
        )
        if grid != "RR73":
            return grid


@dataclass(frozen=True)
class MacroToken:
    """One word of an RTTY contest macro. Tokens sharing a ``group`` are repetitions of one field: the over is
    copied when, for every group, at least one of its tokens prints exactly. ``group`` None is not required."""

    text: str
    group: int | None = None


@dataclass(frozen=True)
class Over:
    """One transmission of a contest QSO.

    ``text`` is the message as JTTY prints it and as minimal RTTY sends it (or the FT8/FT4 message). ``jtty`` holds
    the wsjt-codec render fields that send it from JTTY: a native function-key template with its profile and
    fields, or plain text (which packs CQ, call and TU into the same one-frame atoms). ``macro`` is the RTTY
    typical-macro version.
    """

    contest: Contest
    kind: str
    sender: str
    text: str
    jtty: dict[str, Any] = field(default_factory=dict)
    macro: tuple[MacroToken, ...] = ()

    @property
    def macro_words(self) -> str:
        """The macro's words without RTTY's CR/LF framing: what JTTY sends, since its frames need no line framing."""
        return " ".join(token.text for token in self.macro)

    @property
    def macro_text(self) -> str:
        """The RTTY macro as sent: CR/LF, the tokens, CR/LF."""
        return f"\r\n{self.macro_words}\r\n"


QSO_KINDS: dict[str, tuple[str, ...]] = {
    "keyboard": ("call", "exch_run", "exch_sp", "tu"),
    "ft-ru": ("call_exch", "exch_run", "tu"),
    "ft-serial": ("call", "exch_run", "exch_sp", "tu"),
}
"""The overs that complete a QSO after the CQ, for JTTY/RTTY ('keyboard') and FT8/FT4 per contest."""

KEYBOARD_TRIAL_KINDS: tuple[str, ...] = ("cq", "call", "exch_run", "exch_sp", "tu", "agn")
"""JTTY/RTTY contest-class trials: every over of the QSO plus the AGN? repeat request."""


def keyboard_qso(contest: Contest, run: str, sp: str, run_exch: str | int, sp_exch: str | int) -> list[Over]:
    """Return the JTTY/RTTY reference QSO (CQ, call, exchanges, TU) plus the AGN? request.

    For RTTY Roundup the exchanges are states (JTTY ``rtty`` profile); for the serial contest they are serial
    numbers (``none`` profile, sent as at least three digits).
    """

    def token(text: str, group: int | None = None) -> MacroToken:
        return MacroToken(text, group)

    if contest is Contest.RU:
        run_value, sp_value = str(run_exch), str(sp_exch)
        exch_run_jtty = {"template": "%H %E", "profile": "rtty", "his_call": sp, "exchange": run_value}
        exch_sp_jtty = {"template": "%E", "profile": "rtty", "exchange": sp_value}
    else:
        run_value, sp_value = f"{int(run_exch):03d}", f"{int(sp_exch):03d}"
        exch_run_jtty = {"template": "%H 599 %N", "profile": "none", "his_call": sp, "serial": int(run_exch)}
        exch_sp_jtty = {"template": "599 %N", "profile": "none", "serial": int(sp_exch)}
    return [
        Over(
            contest,
            "cq",
            "run",
            f"CQ {run} CQ",
            {"text": f"CQ {run} CQ"},
            (token("CQ"), token(run, 0), token(run, 0), token("CQ")),
        ),
        Over(contest, "call", "sp", sp, {"text": sp}, (token(sp, 0), token(sp, 0))),
        Over(
            contest,
            "exch_run",
            "run",
            f"{sp} 599 {run_value}",
            exch_run_jtty,
            (token(sp, 0), token("599"), token(run_value, 1), token(run_value, 1)),
        ),
        Over(
            contest,
            "exch_sp",
            "sp",
            f"599 {sp_value}",
            exch_sp_jtty,
            (token("TU"), token("599"), token(sp_value, 0), token(sp_value, 0)),
        ),
        Over(
            contest, "tu", "run", f"TU {run} CQ", {"text": f"TU {run} CQ"}, (token("TU", 0), token(run, 1), token("CQ"))
        ),
        Over(contest, "agn", "sp", "AGN?", {"text": "AGN?"}, (token("AGN?", 0), token("AGN?", 0))),
    ]


def ft_qso(contest: Contest, run: str, sp: str, rng: np.random.Generator) -> list[Over]:
    """Return the FT8/FT4 reference QSO: RU contest mode for RTTY Roundup, the standard QSO otherwise."""
    run_grid, sp_grid = _grid(rng), _grid(rng)
    if contest is Contest.RU:
        run_state, sp_state = _pick(rng, US_STATES), _pick(rng, US_STATES)
        return [
            Over(contest, "cq", "run", f"CQ RU {run} {run_grid}"),
            Over(contest, "call_exch", "sp", f"{run} {sp} 579 {sp_state}"),
            Over(contest, "exch_run", "run", f"{sp} {run} R 589 {run_state}"),
            Over(contest, "tu", "sp", f"{run} {sp} RR73"),
        ]
    first, second = int(rng.integers(-24, 10)), int(rng.integers(-24, 10))
    return [
        Over(contest, "cq", "run", f"CQ {run} {run_grid}"),
        Over(contest, "call", "sp", f"{run} {sp} {sp_grid}"),
        Over(contest, "exch_run", "run", f"{sp} {run} {first:+03d}"),
        Over(contest, "exch_sp", "sp", f"{run} {sp} R{second:+03d}"),
        Over(contest, "tu", "run", f"{sp} {run} RR73"),
    ]


def contest_over(rng: np.random.Generator, calls: Sequence[str]) -> Over:
    """Return one random over of a random JTTY/RTTY reference QSO (fresh calls, states and serials)."""
    contest = _pick(rng, (Contest.RU, Contest.SERIAL))
    kind = _pick(rng, KEYBOARD_TRIAL_KINDS)
    run, sp = _two_calls(rng, calls)
    if contest is Contest.RU:
        run_exch: str | int = _pick(rng, US_STATES)
        sp_exch: str | int = _pick(rng, US_STATES)
    else:
        run_exch, sp_exch = int(rng.integers(1, 2000)), int(rng.integers(1, 2000))
    return next(over for over in keyboard_qso(contest, run, sp, run_exch, sp_exch) if over.kind == kind)


def qso_over(rng: np.random.Generator, calls: Sequence[str]) -> Over:
    """Return one random over of a random FT8/FT4 reference QSO."""
    contest = _pick(rng, (Contest.RU, Contest.SERIAL))
    run, sp = _two_calls(rng, calls)
    overs = ft_qso(contest, run, sp, rng)
    return _pick(rng, overs)


def reference_qsos() -> dict[str, dict[str, list[Over]]]:
    """Return the fixed example QSOs used for the bits and airtime tables (K1ABC in MA runs, W9XYZ in WI calls).

    Keyed by contest, then 'keyboard' (JTTY and RTTY) or 'ft' (FT8 and FT4).
    """
    return {
        Contest.RU: {
            "keyboard": keyboard_qso(Contest.RU, "K1ABC", "W9XYZ", "MA", "WI"),
            "ft": [
                Over(Contest.RU, "cq", "run", "CQ RU K1ABC FN42"),
                Over(Contest.RU, "call_exch", "sp", "K1ABC W9XYZ 579 WI"),
                Over(Contest.RU, "exch_run", "run", "W9XYZ K1ABC R 589 MA"),
                Over(Contest.RU, "tu", "sp", "K1ABC W9XYZ RR73"),
            ],
        },
        Contest.SERIAL: {
            "keyboard": keyboard_qso(Contest.SERIAL, "K1ABC", "W9XYZ", 101, 57),
            "ft": [
                Over(Contest.SERIAL, "cq", "run", "CQ K1ABC FN42"),
                Over(Contest.SERIAL, "call", "sp", "K1ABC W9XYZ EN52"),
                Over(Contest.SERIAL, "exch_run", "run", "W9XYZ K1ABC -12"),
                Over(Contest.SERIAL, "exch_sp", "sp", "K1ABC W9XYZ R-09"),
                Over(Contest.SERIAL, "tu", "run", "W9XYZ K1ABC RR73"),
            ],
        },
    }


def message_for(cls: MessageClass, rng: np.random.Generator, calls: Sequence[str]) -> Over | str:
    """Return a random message of the given class: an Over for contest/QSO traffic, a sentence for free text."""
    if cls is MessageClass.TEXT:
        return text_message(rng)
    if cls is MessageClass.QSO:
        return qso_over(rng, calls)
    return contest_over(rng, calls)
