"""Scoring one trial: what fraction of the transmission the operator copied, and how cleanly.

Every mode is scored in 5-character groups of the sent text for the headline 5-character decode rate: five
characters is a callsign's worth (W9XYZ), so a copied group is a piece of text an operator could use. The sent text
is cut into groups of GROUP_CHARS from its start, and a trailing remainder of 1-4 characters is a group of its own
(group_starts). A group is copied when all its characters print exactly, as one unbroken piece (copied_groups:
the most groups that print this way, in order). Garbage printed between two groups breaks neither; garbage inside a
group breaks it. Spaces count. Case is ignored (PSK31 sends lower case).

- JTTY: the printed messages, with the `` ... `` markers the decoder prints for missed frames
  (lib/jtty/jtty_mdecode.f90, display_message_layout) read as spaces. Frames are still aligned against the per-frame
  text pieces the encoder reported, for the frame counts, garbage and failure classes.
- FT8/FT4: every group of the message when it decodes, none when it does not (AP decodes are discarded: the study
  runs AP off).
- RTTY and PSK31 (the keyboard modes): only characters printed while the message was on the air are scored (the
  operator is watching for it); garbage printed from noise outside that window is counted separately.

Each score also carries whole-message copy, character error rate, wrong printed units ("garbage") and the
decoder's reported SNR.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import NamedTuple, Protocol

import numpy as np
import numpy.typing as npt

from jtty_snr_study.codec import JttyMessage, SlotDecode
from jtty_snr_study.messages import Contest, MacroToken, keyboard_qso
from jtty_snr_study.rtty import SAMPLES_PER_CHAR, RttyReception

GAP_MARKER: str = "..."
"""What the JTTY decoder prints in place of missed frames."""

COPY_RUN_MIN: int = 5
"""Consecutive characters that make a run of real copy (or the whole text, if shorter). Single characters line up by
chance in garbage printed from noise (RTTY and PSK31 score about 15-20% of characters right in pure noise), but runs
this long do not. Only characters in such runs (``run_chars``) plan the SNR grid, and an RTTY/PSK31 demodulator SNR
estimate is reported only when the trial copied such a run."""

GROUP_CHARS: int = 5
"""Characters per scoring group: a callsign's worth (W9XYZ), the shortest piece of text that is useful on its own."""

UNIT_SCORING: str = "5-char groups"
"""The headline unit every non-macro trial is scored in, recorded in config.json. A run with another marker (or none:
JTTY frames, FT8/FT4 messages and RTTY/PSK31 5-character groups on the old alignment) must be rescored
(runner.rescore_run) before it is analyzed."""

LOOKALIKE_MAX_DIFF: int = 2
"""Most characters a printed word may differ from a contest field's value and still pass for it (look_alike)."""

LOOKALIKE_SHORT_FIELD: int = 3
"""Fields this short (states, 3-digit serials) pass only for words 1 character off, or every 2-letter word would pass
for every state."""

QUERY_MARK: str = "?"
"""At the end of a word, read as a query, not part of the word, when judging lookalikes: K2ABD? reads as K2ABD, AGN?
as AGN. Inside a word it is a garbled symbol like any other (K2AB?D is junk)."""

MAX_WRONG_FRAME_CHARS: int = 16
"""Longest text a single wrongly decoded JTTY frame is assumed to print (a typed call/exchange atom)."""

REPORTED_SNR_FLOOR_DB: dict[str, float] = {"jtty": -21.0, "ft4": -21.0, "ft8": -25.0}
"""Lowest SNR each decoder reports (lib/jtty/jtty_mdecode.f90 SNR_FLOOR_DB, lib/ft4_decode.f90, lib/ft8/ft8b.f90).

The JTTY value is the default; a run reads the current one from the WSJT-X source (runner.jtty_snr_floor)."""

_WHITESPACE = re.compile(r"\s+")


class KeyboardReception(Protocol):
    """What a keyboard-mode demodulator (RTTY or PSK31) printed.

    ``positions[i]`` is the sample where ``text[i]`` began on the air; ``snr_db`` is the demodulator's SNR estimate
    over the message, or None.
    """

    @property
    def text(self) -> str: ...

    @property
    def positions(self) -> npt.NDArray[np.int64]: ...

    @property
    def snr_db(self) -> float | None: ...


_MATCHED, _WRONG, _SILENT = 0, 1, 2
"""How the JTTY alignment explained a frame: printed exactly, replaced by a wrong decode, or invisible (spaces)."""


@dataclass(frozen=True)
class TrialScore:
    """Outcome of one trial.

    ``units``/``units_ok``: 5-character groups of the sent text sent / copied exactly (group_counts); one unit per
    trial for contest macros.
    ``copied``: the whole message printed exactly (JTTY also needs its end-of-message frame).
    ``cer``: edit distance between sent and printed text over the sent length (can exceed 1 with garbage).
    ``garbage``: wrong units printed (JTTY wrong frames, FT8/FT4 wrong messages, RTTY/PSK31 wrong characters).
    ``noise_prints``: RTTY/PSK31 characters printed outside the message window (0 for other modes).
    ``ap_filtered``: FT8/FT4 AP decodes discarded. ``state``: JTTY terminal state of the best message, else
    decoded/none. ``reported_snr``: the decoder's SNR report for the copy, or None.
    ``failure``: why the trial was not a clean copy, ';'-joined -- ``missed`` (units not copied), ``split`` (JTTY:
    every frame decoded but spread over several printed messages), ``no_eom`` (JTTY: every frame in one message
    but no end-of-message), ``false_text`` (wrong text printed) -- or empty. ``copied_chars``: characters of the sent
    text in copied 5-character groups, for throughput (JTTY: the text of correctly decoded frames, padding spaces left
    out; FT8/FT4: the whole message when decoded).
    ``frames``: JTTY frames sent, for the copy-by-length breakdown (None for other modes).
    ``run_chars``: copied characters in runs of COPY_RUN_MIN (one per copied macro), which plan the SNR grid.
    """

    units: int
    units_ok: int
    copied: bool
    cer: float
    garbage: int
    printed: str
    state: str
    reported_snr: float | None
    noise_prints: int = 0
    ap_filtered: int = 0
    n_messages: int = 0
    failure: str = ""
    copied_chars: int = 0
    frames: int | None = None
    run_chars: int = 0


def normalize(text: str) -> str:
    """Upper-case, collapse runs of whitespace to one space, and strip."""
    return _WHITESPACE.sub(" ", text).strip().upper()


def levenshtein(a: str, b: str) -> int:
    """Return the edit distance (unit-cost insert, delete, substitute) between two strings."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]


def align_free_ends(sent: str, printed: str) -> list[tuple[str, int, int]]:
    """Align ``sent`` inside ``printed``, with extra printed characters at either end free.

    Returns the edit operations in order as (op, sent_index, printed_index): ``M`` match, ``S`` substitution,
    ``D`` sent character not printed (printed_index is where it would be), ``I`` extra printed character
    (sent_index is the next sent character). Leading/trailing extra printed characters appear as ``I`` operations
    but cost nothing in the alignment.
    """
    n, m = len(sent), len(printed)
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = i
        for j in range(1, m + 1):
            cost[i][j] = min(
                cost[i - 1][j - 1] + (sent[i - 1] != printed[j - 1]),
                cost[i - 1][j] + 1,
                cost[i][j - 1] + 1,
            )
    end = min(range(m + 1), key=lambda j: (cost[n][j], j))

    ops: list[tuple[str, int, int]] = [("I", n, j) for j in range(m - 1, end - 1, -1)]
    i, j = n, end
    while i > 0:
        if j > 0 and cost[i][j] == cost[i - 1][j - 1] + (sent[i - 1] != printed[j - 1]):
            ops.append(("M" if sent[i - 1] == printed[j - 1] else "S", i - 1, j - 1))
            i, j = i - 1, j - 1
        elif cost[i][j] == cost[i - 1][j] + 1:
            ops.append(("D", i - 1, j))
            i -= 1
        else:
            ops.append(("I", i, j - 1))
            j -= 1
    ops.extend(("I", 0, k) for k in range(j - 1, -1, -1))
    ops.reverse()
    return ops


def character_matches(sent: str, printed: str) -> list[tuple[int, int]]:
    """Return the (sent index, printed index) pairs of the characters of ``sent`` copied exactly in ``printed``.

    Both are normalized first. The pairs are a longest common subsequence: the most sent characters that print in
    order, however much extra text is printed before, between or after them. So a substituted or missing sent
    character is not copied, and garbage printed between sent characters never uncopies them, whatever its length.
    Unlike align_free_ends, which minimizes edits (and so may skip correct text to avoid a burst of garbage), this
    can line up single characters by chance in garbage printed from noise; COPY_RUN_MIN guards what must not count
    such matches. Where several pairings copy as many characters, the one in the fewest runs (copied_runs) is kept,
    so a chance letter in garbage never takes a sent character away from the clean copy beside it.
    """
    sent, printed = normalize(sent), normalize(printed)
    n, m = len(sent), len(printed)
    # Scores rank pairings by copied characters, then by adjacent pairs (fewest runs): copied * weight + adjacent.
    weight = n + 1
    unset = -1
    # best[i][j]: the best pairing within sent[:i] and printed[:j]; ending[i][j]: the best that pairs i-1 with j-1.
    best = [[0] * (m + 1) for _ in range(n + 1)]
    ending = [[unset] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        row, above, end_row, end_above, char = best[i], best[i - 1], ending[i], ending[i - 1], sent[i - 1]
        for j in range(1, m + 1):
            if char == printed[j - 1]:
                extend = end_above[j - 1] + weight + 1 if end_above[j - 1] != unset else unset
                end_row[j] = max(above[j - 1] + weight, extend)
            row[j] = max(above[j], row[j - 1], end_row[j])

    matches: list[tuple[int, int]] = []
    i, j = n, m
    in_run = False
    while i > 0 and j > 0:
        if in_run or (ending[i][j] != unset and best[i][j] == ending[i][j]):
            matches.append((i - 1, j - 1))
            previous = ending[i - 1][j - 1]
            in_run = previous != unset and ending[i][j] == previous + weight + 1
            i, j = i - 1, j - 1
        elif best[i][j] == best[i - 1][j]:
            i -= 1
        else:
            j -= 1
    matches.reverse()
    return matches


def copied_runs(matches: Sequence[tuple[int, int]]) -> list[int]:
    """Return the lengths of the runs of consecutive sent characters copied as consecutive printed characters.

    ``matches`` comes from character_matches; a lost character or one inserted between two copied ones ends a run.
    """
    runs: list[int] = []
    previous: tuple[int, int] | None = None
    for pair in matches:
        if previous is not None and pair == (previous[0] + 1, previous[1] + 1):
            runs[-1] += 1
        else:
            runs.append(1)
        previous = pair
    return runs


def run_characters(matches: Sequence[tuple[int, int]], sent_length: int) -> int:
    """Return the copied characters that lie in runs of at least COPY_RUN_MIN (or all ``sent_length``, if shorter)."""
    shortest = min(COPY_RUN_MIN, sent_length)
    return sum(run for run in copied_runs(matches) if run >= shortest)


def group_starts(length: int) -> list[int]:
    """Return the start index of each scoring group in a sent text of ``length`` characters.

    Groups are GROUP_CHARS long from the start; a trailing remainder of 1-4 characters is a group of its own. An empty
    text still has one (empty) group, so every trial sends at least one unit.
    """
    return list(range(0, max(length, 1), GROUP_CHARS))


def copied_group_text(sent: str, printed: str) -> tuple[int, int]:
    """Return how many scoring groups of ``sent`` are copied in ``printed``, and how many characters they hold.

    Both are normalized first. A group is copied when it prints exactly, as one unbroken piece, as a callsign must.
    The count is the most groups that print this way in order, each in its own stretch of the printed text, whatever
    is printed before, between or after them: garbage between two groups breaks neither, garbage inside a group breaks
    it. Among equally many groups, the choice holding the most characters is kept (a short final group holds fewer).
    An empty text's group is never copied. The characters are what throughput counts.
    """
    sent, printed = normalize(sent), normalize(printed)
    m = len(printed)
    # Scores rank by groups, then characters: groups * weight + characters, with characters at most len(sent).
    weight = len(sent) + 1
    # most[p]: the best score so far within printed[:p], for the groups handled so far.
    most = [0] * (m + 1)
    for start in group_starts(len(sent)):
        group = sent[start : start + GROUP_CHARS]
        size = len(group)
        updated = [0] * (m + 1)
        for p in range(1, m + 1):
            updated[p] = max(updated[p - 1], most[p])
            if group and p >= size and printed[p - size : p] == group:
                updated[p] = max(updated[p], most[p - size] + weight + size)
        most = updated
    groups, characters = divmod(most[m], weight)
    return groups, characters


def copied_groups(sent: str, printed: str) -> int:
    """Return how many scoring groups of ``sent`` are copied in ``printed`` (see copied_group_text)."""
    return copied_group_text(sent, printed)[0]


class GroupCounts(NamedTuple):
    """A non-macro trial's counts: ``sent`` and ``copied`` 5-character groups (the headline units), ``in_runs`` (the
    copied characters in runs of COPY_RUN_MIN, which plan the SNR grid) and ``characters`` in the copied groups
    (RTTY/PSK31/FT8/FT4 throughput; JTTY throughput counts decoded frame text instead)."""

    sent: int
    copied: int
    in_runs: int
    characters: int


def jtty_readable(printed: str) -> str:
    """Return JTTY printed text with its missed-frame markers read as spaces, as an operator reads the line."""
    return normalize(printed.replace(GAP_MARKER, " "))


def group_counts(mode: str, sent: str, printed: str, copied: bool) -> GroupCounts:
    """Return a non-macro trial's group and character counts from its raw-data text.

    ``sent`` and ``printed`` are a trial row's columns of the same names and ``copied`` its whole-message copy; the
    trial scorers count this way, so rescoring a stored row reproduces what scoring it live gives. An FT8 or FT4
    message gives all its groups and characters or none.
    """
    length = len(normalize(sent))
    groups = len(group_starts(length))
    if mode in ("ft8", "ft4"):
        return GroupCounts(groups, groups if copied else 0, length if copied else 0, length if copied else 0)
    readable = jtty_readable(printed) if mode == "jtty" else printed
    matches = character_matches(sent, readable)
    groups_ok, characters = copied_group_text(sent, readable)
    return GroupCounts(groups, groups_ok, run_characters(matches, length), characters)


def _segments(message: JttyMessage) -> list[str]:
    """Split a JTTY message's printed text at its missed-frame markers."""
    pieces: list[str] = []
    position = 0
    for gap in sorted(message.gaps):
        pieces.append(message.text[position:gap])
        position = gap + len(GAP_MARKER)
    pieces.append(message.text[position:])
    return pieces


def _align_segment(segment: str, frames: Sequence[str], first_frame: int) -> tuple[list[int], int, int]:
    """Explain one gap-free run of printed JTTY text as consecutive frames starting at or after ``first_frame``.

    Spaces are ignored (frame boundaries move them). Each frame is either printed exactly (credited) or replaced by
    a wrong decode of 1..MAX_WRONG_FRAME_CHARS characters (garbage); wrong decodes may also run past the last frame.
    Returns (credited frame indices, wrong-frame count, index after the last frame used), maximizing credit, then
    minimizing garbage, then preferring the earliest start.
    """
    text = segment.replace(" ", "")
    if not text:
        return [], 0, first_frame
    pieces = [frame.replace(" ", "") for frame in frames]
    # Wrong decodes (at least one character each) may run past the last frame, and an earlier segment may already
    # have done so, so size the frame axis from wherever this segment may start.
    limit = max(len(frames), first_frame) + len(text)
    worst = (-1, 0)
    # best[pos][k] = (credit, -garbage) after printing text[:pos] with frame k next; back[pos][k] = predecessor and
    # how frame k was explained (_MATCHED, _WRONG, or _SILENT for a frame that prints no visible characters).
    best = [[worst] * (limit + 1) for _ in range(len(text) + 1)]
    back: list[list[tuple[int, int, int] | None]] = [[None] * (limit + 1) for _ in range(len(text) + 1)]
    for k in range(first_frame, limit + 1):
        best[0][k] = (0, 0)
    for pos in range(len(text) + 1):
        for k in range(first_frame, limit):
            here = best[pos][k]
            if here == worst:
                continue
            if k < len(pieces) and not pieces[k]:
                # A frame of only spaces cannot be verified from the text: pass over it without credit.
                if here > best[pos][k + 1]:
                    best[pos][k + 1] = here
                    back[pos][k + 1] = (pos, k, _SILENT)
            elif k < len(pieces) and text.startswith(pieces[k], pos):
                step = pos + len(pieces[k])
                candidate = (here[0] + 1, here[1])
                if candidate > best[step][k + 1]:
                    best[step][k + 1] = candidate
                    back[step][k + 1] = (pos, k, _MATCHED)
            for width in range(1, min(MAX_WRONG_FRAME_CHARS, len(text) - pos) + 1):
                candidate = (here[0], here[1] - 1)
                if candidate > best[pos + width][k + 1]:
                    best[pos + width][k + 1] = candidate
                    back[pos + width][k + 1] = (pos, k, _WRONG)

    end = max(range(first_frame, limit + 1), key=lambda k: (best[len(text)][k], -k))
    credited: list[int] = []
    garbage = 0
    pos, k = len(text), end
    while pos > 0:
        step = back[pos][k]
        if step is None:
            break
        pos, k, how = step
        if how == _MATCHED:
            credited.append(k)
        elif how == _WRONG:
            garbage += 1
    return sorted(credited), garbage, end


def score_jtty(
    frames: Sequence[str],
    canonical: str,
    messages: Sequence[JttyMessage],
    fields: Sequence[int | None] | None = None,
) -> TrialScore:
    """Score a JTTY trial from the sent frames/canonical text and the decoder's final messages.

    ``fields`` (a contest over's field per sent word, field_groups) makes whole-message copy also require that no
    field printed ambiguously anywhere in the printout (ambiguous_fields; failure class ``ambiguous``).
    """
    credited: set[int] = set()
    garbage = 0
    best_message: JttyMessage | None = None
    best_credit = 0
    for message in messages:
        message_credit: set[int] = set()
        next_frame = 0
        for index, segment in enumerate(_segments(message)):
            frames_ok, wrong, end = _align_segment(segment, frames, next_frame)
            message_credit.update(frames_ok)
            garbage += wrong
            next_frame = end + (1 if index < len(message.gaps) else 0)
        credited |= message_credit
        if len(message_credit) > best_credit:
            best_message, best_credit = message, len(message_credit)

    sent = normalize(canonical)
    exact = any(
        message.state == "complete" and not message.gaps and normalize(message.text) == sent for message in messages
    )
    printed = normalize(" ".join(message.text for message in messages))
    readable = jtty_readable(printed)
    ambiguous = fields is not None and bool(ambiguous_fields(sent.split(), fields, readable.split()))
    copied = exact and not ambiguous

    failure: list[str] = []
    if len(credited) < len(frames):
        failure.append("missed")
    elif not exact and best_credit < len(credited):
        failure.append("split")
    elif not exact and not garbage:
        failure.append("no_eom")
    if exact and ambiguous:
        failure.append("ambiguous")
    if garbage:
        failure.append("false_text")
    counts = group_counts("jtty", canonical, printed, copied)
    return TrialScore(
        units=counts.sent,
        units_ok=counts.copied,
        run_chars=counts.in_runs,
        copied=copied,
        cer=levenshtein(sent, readable) / max(len(sent), 1),
        garbage=garbage,
        printed=printed,
        state=best_message.state if best_message is not None else ("garbage" if messages else "none"),
        reported_snr=float(best_message.snr) if best_message is not None else None,
        n_messages=len(messages),
        failure=";".join(failure),
        copied_chars=sum(len(frames[k].strip()) for k in credited),
        frames=len(frames),
    )


def score_slot(msgsent: str, decodes: Sequence[SlotDecode]) -> TrialScore:
    """Score an FT8/FT4 trial; decodes that used AP are discarded and counted in ``ap_filtered``."""
    kept = [decode for decode in decodes if decode.ap == 0]
    sent = normalize(msgsent)
    found: dict[str, SlotDecode] = {}
    for decode in kept:
        text = normalize(decode.message.rstrip("? "))
        if text not in found or decode.decode_pass > found[text].decode_pass:
            found[text] = decode
    ok = sent in found
    wrong = len([text for text in found if text != sent])
    failure = ";".join(name for name, flag in (("missed", not ok), ("false_text", wrong > 0)) if flag)
    counts = group_counts("ft8", sent, "", ok)
    return TrialScore(
        failure=failure,
        copied_chars=counts.characters,
        units=counts.sent,
        units_ok=counts.copied,
        run_chars=counts.in_runs,
        copied=ok,
        cer=0.0 if ok else 1.0,
        garbage=len([text for text in found if text != sent]),
        printed=" | ".join(found),
        state="decoded" if ok else ("garbage" if found else "none"),
        reported_snr=float(found[sent].snr) if ok else None,
        ap_filtered=len(decodes) - len(kept),
        n_messages=len(found),
    )


def score_rtty(
    text: str, reception: RttyReception, window: tuple[int, int], fields: Sequence[int | None] | None = None
) -> TrialScore:
    """Score an RTTY trial (see score_keyboard), counting characters that start within half a character of it."""
    return score_keyboard(text, reception, window, SAMPLES_PER_CHAR / 2.0, fields)


def score_keyboard(
    text: str,
    reception: KeyboardReception,
    window: tuple[int, int],
    margin: float,
    fields: Sequence[int | None] | None = None,
) -> TrialScore:
    """Score an RTTY or PSK31 trial; ``window`` is the message's on-air span in samples (from the transmitter).

    Printed characters that began within ``margin`` samples of the window are scored; the rest are ``noise_prints``.
    Like the WSJT-X decoders, which report SNR only for a decode, the demodulator's SNR estimate is reported only
    when COPY_RUN_MIN consecutive characters were copied; otherwise it would be an estimate over garbage
    printed from noise. Units are 5-character groups (copied_groups); CER and garbage come from align_free_ends.
    ``fields`` (a contest over's field per sent word, field_groups) makes whole-message copy also require that no
    field printed ambiguously (ambiguous_fields; failure class ``ambiguous``).
    """
    inside = [
        char
        for char, position in zip(reception.text, reception.positions, strict=True)
        if window[0] - margin <= position < window[1] + margin
    ]
    noise_prints = len(reception.text) - len(inside)
    printed = normalize("".join(inside))
    sent = normalize(text)
    ops = align_free_ends(sent, printed)
    matches = character_matches(sent, printed)
    in_runs = run_characters(matches, len(sent))
    groups = len(group_starts(len(sent)))
    groups_ok, copied_chars = copied_group_text(sent, printed)

    errors = 0
    garbage = 0
    for op, sent_index, _ in ops:
        if op == "I":
            garbage += 1
            errors += 0 < sent_index < len(sent)
        elif op != "M":
            errors += 1
            garbage += op == "S"

    missed = groups_ok < groups
    exact = errors == 0
    ambiguous = fields is not None and bool(ambiguous_fields(sent.split(), fields, printed.split()))
    flags = (("missed", missed), ("ambiguous", exact and ambiguous), ("false_text", garbage > 0))
    return TrialScore(
        failure=";".join(name for name, flag in flags if flag),
        copied_chars=copied_chars,
        units=groups,
        units_ok=groups_ok,
        run_chars=in_runs,
        copied=exact and not ambiguous,
        cer=errors / max(len(sent), 1),
        garbage=garbage,
        printed=printed,
        state="",
        reported_snr=reception.snr_db if in_runs else None,
        noise_prints=noise_prints,
    )


def matched_words(sent: Sequence[str], printed: Sequence[str], weights: Sequence[int] | None = None) -> set[int]:
    """Return the indices of sent words found, in order, among the printed words.

    This is a longest common subsequence weighted by ``weights`` (default 1 each): extra printed words anywhere cost
    nothing, so a stray word never displaces a correctly printed one, and among equally good matches the heavier
    words win (a macro's required call over a fixed word such as CQ).
    """
    weight = list(weights) if weights is not None else [1] * len(sent)
    rows, cols = len(sent), len(printed)
    best = [[0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(1, rows + 1):
        for j in range(1, cols + 1):
            skip = max(best[i - 1][j], best[i][j - 1])
            take = best[i - 1][j - 1] + weight[i - 1] if sent[i - 1] == printed[j - 1] else -1
            best[i][j] = max(skip, take)
    found: set[int] = set()
    i, j = rows, cols
    while i > 0 and j > 0:
        if sent[i - 1] == printed[j - 1] and best[i][j] == best[i - 1][j - 1] + weight[i - 1]:
            found.add(i - 1)
            i, j = i - 1, j - 1
        elif best[i - 1][j] >= best[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return found


def looks_like(word: str, value: str) -> bool:
    """Return whether a printed ``word`` could pass for a contest field's ``value`` without being it.

    A trailing query mark (QUERY_MARK) is dropped first. What remains must be letters and digits only, as every
    call, state and serial is: a word with any other symbol is junk no operator would take for the field (HA<JDU,
    W4.WF). It must then be the same length and differ in 1 to LOOKALIKE_MAX_DIFF characters (1 for a value of
    LOOKALIKE_SHORT_FIELD characters or fewer): K2ABD (or K2ABD?) passes for K2ABC and WY for WI, while a fragment
    (2BMJ for KE2BMJ), a word of another length, or junk differing in more characters does not.
    """
    word = word.rstrip(QUERY_MARK)
    if not (word.isascii() and word.isalnum()):
        return False
    if word == value or len(word) != len(value) or not value:
        return False
    limit = 1 if len(value) <= LOOKALIKE_SHORT_FIELD else LOOKALIKE_MAX_DIFF
    return sum(a != b for a, b in zip(word, value, strict=True)) <= limit


def ambiguous_fields(sent_words: Sequence[str], groups: Sequence[int | None], printed_words: Sequence[str]) -> set[int]:
    """Return the fields of a contest over or macro whose value printed ambiguously.

    ``groups`` gives each sent word's field (None for a fixed word such as CQ or 599; repetitions of a field share
    it). A field is ambiguous when the printout holds a word that looks like its value (looks_like, which ignores junk
    with symbols) and is not exactly any sent word: the same information received with a different value, so the
    operator cannot tell which is right. A copy that is simply lost is not ambiguous.
    """
    # A printed word is read without a trailing query mark (see looks_like), so 599? is the sent 599, not a conflict.
    known = {word.rstrip(QUERY_MARK) for word in sent_words}
    values = {group: word for word, group in zip(sent_words, groups, strict=True) if group is not None}
    return {
        group
        for group, value in values.items()
        if any(word.rstrip(QUERY_MARK) not in known and looks_like(word, value) for word in printed_words)
    }


_PLACEHOLDER_INFORMATION: tuple[str, ...] = ("K1AAA", "K2BBB", "MA", "WI")
"""The placeholder run call, S&P call and exchanges _field_structure sends: the words that carry information."""


@functools.cache
def _field_structure() -> dict[tuple[str, bool, bool], tuple[int | None, ...]]:
    """Return each keyboard over's field per word, keyed by (over kind, sent as a macro, information only).

    The fields are the macro's required token groups (MacroToken.group). Information only keeps the fields that carry
    information that can arrive with a different value (calls and exchanges) and drops required fixed words (the
    repeated AGN? request, TU in the TU over), which a garble can only lose, not change. Built from
    messages.keyboard_qso with distinct placeholder values, so it is the structure the trials were sent with; both
    contests share it.
    """
    structure: dict[tuple[str, bool, bool], tuple[int | None, ...]] = {}
    for over in keyboard_qso(Contest.RU, *_PLACEHOLDER_INFORMATION):
        field_of = {normalize(token.text): token.group for token in over.macro if token.group is not None}
        for macro, words in (
            (True, [normalize(token.text) for token in over.macro]),
            (False, normalize(over.text).split()),
        ):
            groups = tuple(field_of.get(word) for word in words)
            structure[(over.kind, macro, False)] = groups
            structure[(over.kind, macro, True)] = tuple(
                group if word in _PLACEHOLDER_INFORMATION else None for word, group in zip(words, groups, strict=True)
            )
    return structure


def field_groups(kind: str, macro: bool, information: bool = False) -> tuple[int | None, ...] | None:
    """Return the field of each sent word of a keyboard contest over of ``kind`` (plain or macro), or None if unknown.

    ``information`` keeps only the fields that carry information (calls and exchanges), for the ambiguity rule; the
    full set also holds required fixed words. Fields are positional: a serial number can read 599, the same as the
    fixed 599 beside it.
    """
    return _field_structure().get((kind, macro, information))


def macro_verdict(
    tokens: Sequence[MacroToken], printed_words: Sequence[str], information: Sequence[int | None] | None = None
) -> tuple[bool, bool]:
    """Return whether every field of a macro printed exactly in some repetition, and whether any field is ambiguous.

    ``information`` gives the fields checked for ambiguity (field_groups(..., information=True)); by default every
    token group. The macro is copied when the first is true and the second false (see ambiguous_fields).
    """
    exact = _macro_groups(tokens, printed_words)
    sent_words = [normalize(token.text) for token in tokens]
    checked = list(information) if information is not None else [token.group for token in tokens]
    ambiguous = ambiguous_fields(sent_words, checked, printed_words)
    return all(exact.values()), bool(ambiguous)


def copy_failure(exact: bool, ambiguous: bool, false_text: bool) -> str:
    """Return a macro's failure classes: ``missed`` (a field never printed exactly), ``ambiguous`` (every field did,
    but one also printed a conflicting value) and ``false_text``."""
    flags = (("missed", not exact), ("ambiguous", exact and ambiguous), ("false_text", false_text))
    return ";".join(name for name, flag in flags if flag)


def score_rtty_macro(
    tokens: Sequence[MacroToken],
    reception: RttyReception,
    window: tuple[int, int],
    information: Sequence[int | None] | None = None,
) -> TrialScore:
    """Score an RTTY contest-macro trial (see score_keyboard_macro), with a margin of half a character."""
    return score_keyboard_macro(tokens, reception, window, SAMPLES_PER_CHAR / 2.0, information)


def _macro_groups(tokens: Sequence[MacroToken], printed: Sequence[str]) -> dict[int, bool]:
    """Return, for each required token group of a macro, whether at least one repetition printed exactly."""
    heavy = 1 + len(tokens)
    exact = matched_words(
        [normalize(token.text) for token in tokens],
        printed,
        [heavy if token.group is not None else 1 for token in tokens],
    )
    groups: dict[int, bool] = {}
    for index, token in enumerate(tokens):
        if token.group is not None:
            groups[token.group] = groups.get(token.group, False) or index in exact
    return groups


def score_jtty_macro(
    tokens: Sequence[MacroToken],
    frames: Sequence[str],
    canonical: str,
    messages: Sequence[JttyMessage],
    information: Sequence[int | None] | None = None,
) -> TrialScore:
    """Score a JTTY contest-macro trial by the same rule as RTTY and PSK31 macros (see score_keyboard_macro).

    Copied when, for every token group, one repetition prints exactly and none prints a conflicting value
    (macro_verdict), whether or not every frame or the end of message decoded: a lost frame holding one copy of a
    repeated field is what the repetition is for. Units: one per
    trial, as for the other macros. CER, garbage and state come from the ordinary JTTY score.
    """
    base = score_jtty(frames, canonical, messages)
    exact, ambiguous = macro_verdict(tokens, jtty_readable(base.printed).split(), information)
    copied = exact and not ambiguous
    failure = copy_failure(exact, ambiguous, base.garbage > 0)
    return replace(
        base,
        units=1,
        units_ok=int(copied),
        run_chars=int(copied),
        copied=copied,
        reported_snr=base.reported_snr if copied else None,
        failure=failure,
        copied_chars=len(normalize(canonical)) if copied else 0,
    )


def score_keyboard_macro(
    tokens: Sequence[MacroToken],
    reception: KeyboardReception,
    window: tuple[int, int],
    margin: float,
    information: Sequence[int | None] | None = None,
) -> TrialScore:
    """Score an RTTY or PSK31 contest-macro trial: copied when, for every token group, at least one repetition prints
    exactly and no printed word gives an information field a conflicting value (macro_verdict; ``information`` as
    there). ``window`` and ``margin`` select the characters scored, as in score_keyboard.

    This is how an operator reads a macro such as ``W9XYZ 599 MA MA``: word by word. A token counts only if it
    prints as exactly that word, in order (matched_words), so a stray word between two tokens hurts neither, while a
    glued or extended word ("W9XYZ599", "W9XYZT") does not match. The call must be right and one good copy of the
    repeated state is enough; the fixed 599 is not needed. Units: one per trial. CER and false text are counted on
    characters.
    """
    inside = [
        char
        for char, position in zip(reception.text, reception.positions, strict=True)
        if window[0] - margin <= position < window[1] + margin
    ]
    printed = normalize("".join(inside))
    sent = normalize(" ".join(token.text for token in tokens))
    char_ops = align_free_ends(sent, printed)
    errors = sum(1 for op, i, _ in char_ops if op in ("S", "D") or (op == "I" and 0 < i < len(sent)))
    wrong_chars = sum(1 for op, _, _ in char_ops if op in ("S", "I"))

    exact, ambiguous = macro_verdict(tokens, printed.split(), information)
    copied = exact and not ambiguous
    failure = copy_failure(exact, ambiguous, wrong_chars > 0)
    return TrialScore(
        units=1,
        units_ok=int(copied),
        run_chars=int(copied),
        copied=copied,
        cer=errors / max(len(sent), 1),
        garbage=wrong_chars,
        printed=printed,
        state="",
        reported_snr=reception.snr_db if copied else None,
        noise_prints=len(reception.text) - len(inside),
        failure=failure,
        copied_chars=len(sent) if copied else 0,
    )
