"""Trial scoring: 5-character groups for every mode, JTTY frame alignment, FT8/FT4 decodes, CER and garbage."""

from __future__ import annotations

import numpy as np
import pytest

from jtty_snr_study import scoring
from jtty_snr_study.codec import JttyMessage, SlotDecode
from jtty_snr_study.messages import Contest, keyboard_qso
from jtty_snr_study.rtty import SAMPLES_PER_CHAR, RttyReception

FRAMES = ("NAME ", "IS TO", "M QTH", " BOST", "ON RI", "G IC7", "300")
CANONICAL = "NAME IS TOM QTH BOSTON RIG IC7300"


def message(text: str, state: str = "complete", gaps: tuple[int, ...] = (), snr: int = -15) -> JttyMessage:
    return JttyMessage(message_id=1, state=state, text=text, gaps=gaps, snr=snr, freq=1500.0, start_s=1.0)


def test_complete_copy_credits_every_group() -> None:
    score = scoring.score_jtty(FRAMES, CANONICAL, [message(CANONICAL)])
    # 33 characters: six groups of five and a short final group "300".
    assert (score.units, score.units_ok, score.copied, score.garbage, score.cer) == (7, 7, True, 0, 0.0)
    assert score.frames == 7 and score.reported_snr == -15.0 and score.state == "complete"


def test_missed_first_frame_loses_its_group() -> None:
    score = scoring.score_jtty(FRAMES, CANONICAL, [message("IS TOM QTH BOSTON RIG IC7300")])
    # The group "NAME " is lost.
    assert score.units_ok == 6 and not score.copied and score.garbage == 0


def test_missed_middle_frame_shows_as_a_gap() -> None:
    text = "NAME IS TO ... BOSTON RIG IC7300"
    score = scoring.score_jtty(FRAMES, CANONICAL, [message(text, gaps=(text.index("..."),))])
    # The gap reads as a space: the group "M QTH" is lost.
    assert score.units_ok == 6 and not score.copied and score.garbage == 0
    assert 0 < score.cer < 0.3


def test_missed_last_frame_leaves_the_message_unfinished() -> None:
    score = scoring.score_jtty(FRAMES, CANONICAL, [message("NAME IS TOM QTH BOSTON RIG IC7", state="ended")])
    # The short final group "300" is lost.
    assert score.units_ok == 6 and not score.copied and score.state == "ended"


def test_message_split_in_two_after_a_long_gap() -> None:
    first = message("NAME IS TO", state="expired")
    second = JttyMessage(2, "complete", "G IC7300", (), -16, 1500.0, 12.0)
    score = scoring.score_jtty(FRAMES, CANONICAL, [first, second])
    # "NAME ", "IS TO", "G IC7" and "300" print whole; the three groups in between are lost.
    assert score.units_ok == 4 and not score.copied and score.garbage == 0


def test_wrong_frame_counts_as_garbage() -> None:
    score = scoring.score_jtty(FRAMES, CANONICAL, [message("NAME IS TOM QXZ7BOSTON RIG IC7300", state="complete")])
    # "XZ7" replaced "TH ", breaking the groups "M QTH" and " BOST".
    assert score.units_ok == 5 and score.garbage == 1 and not score.copied


def test_decode_from_noise_is_garbage() -> None:
    score = scoring.score_jtty(("CQ K1ABC CQ",), "CQ K1ABC CQ", [message("W9XYZ 599 123")])
    # No group prints whole and no frame is credited, so no SNR is reported.
    assert score.units_ok == 0 and score.garbage >= 1 and score.state == "garbage" and score.reported_snr is None


def test_nothing_decoded() -> None:
    score = scoring.score_jtty(FRAMES, CANONICAL, [])
    assert (score.units_ok, score.copied, score.cer, score.state) == (0, False, 1.0, "none")


def test_slot_scoring_discards_ap_decodes_and_counts_garbage() -> None:
    decodes = [
        SlotDecode("K1ABC W9XYZ -12", -20, 0.1, 1500.0, 0, 41),
        SlotDecode("K1ABC W9XYZ -12", -19, 0.1, 1500.0, 0, 50),
        SlotDecode("CQ N0CALL FN42", -22, 0.3, 900.0, 0, 50),
        SlotDecode("K1ABC W9XYZ RR73                    ?", -24, 0.1, 1500.0, 2, 50),
    ]
    score = scoring.score_slot("K1ABC W9XYZ -12", decodes)
    assert score.units == score.units_ok == 3 and score.copied_chars == len("K1ABC W9XYZ -12")
    assert score.copied and score.garbage == 1 and score.ap_filtered == 1
    assert score.reported_snr == -19.0


def test_slot_ap_only_decode_does_not_count() -> None:
    score = scoring.score_slot("K1ABC W9XYZ -12", [SlotDecode("K1ABC W9XYZ -12", -24, 0.1, 1500.0, 1, 50)])
    assert score.units_ok == 0 and score.ap_filtered == 1 and score.cer == 1.0


def rtty_reception(printed: str, start: int, snr: float | None = -5.0) -> RttyReception:
    positions = np.array([start + round(i * SAMPLES_PER_CHAR) for i in range(len(printed))], dtype=np.int64)
    return RttyReception(text=printed, positions=positions, snr_db=snr, snr_chars=len(printed))


def test_rtty_perfect_copy() -> None:
    window = (1000, 1000 + round(14 * SAMPLES_PER_CHAR))
    score = scoring.score_rtty("CQ K1ABC CQ", rtty_reception(" CQ K1ABC CQ ", 1000), window)
    # "CQ K1", "ABC C" and the short final group "Q".
    assert (score.units, score.units_ok, score.copied, score.cer, score.garbage) == (3, 3, True, 0.0, 0)


def test_rtty_garbage_from_noise_can_match_a_short_final_group_by_chance() -> None:
    window = (0, round(40 * SAMPLES_PER_CHAR))
    score = scoring.score_rtty("RIG FTDX10 PWR 5W QTH AUSTIN", rtty_reception("LI FTSINF BVXSREFGDDTINBS", 0), window)
    # Six groups; only the short final group "TIN" lines up, by chance ("...DDTINBS"). No run of COPY_RUN_MIN does,
    # so no SNR report and nothing to plan on.
    assert score.units == 6 and score.units_ok == 1 and score.reported_snr is None and score.run_chars == 0
    assert not score.copied


def test_rtty_snr_report_needs_a_run_of_copied_characters() -> None:
    window = (0, round(40 * SAMPLES_PER_CHAR))
    four = scoring.score_rtty("ABCDEFGHIJ", rtty_reception("ABCDXXXXXX", 0), window)
    five = scoring.score_rtty("ABCDEFGHIJ", rtty_reception("ABCDEXXXXX", 0), window)
    assert four.units_ok == 0 and four.reported_snr is None
    assert five.units_ok == 1 and five.reported_snr == -5.0
    short = scoring.score_rtty("TU", rtty_reception("TU", 0), window)
    assert short.copied and short.reported_snr == -5.0


def test_rtty_substitution_loses_only_its_group() -> None:
    window = (0, round(30 * SAMPLES_PER_CHAR))
    score = scoring.score_rtty("ABCDEFGHIJKLMNO", rtty_reception(" ABCDEFGXIJKLMNO ", 0), window)
    # Throughput counts the characters of the two copied groups.
    assert score.units == 3 and score.units_ok == 2 and score.garbage == 1 and score.copied_chars == 10
    assert score.cer == pytest.approx(1 / 15)


def test_rtty_insertion_inside_a_group_breaks_it_but_between_groups_does_not() -> None:
    window = (0, round(30 * SAMPLES_PER_CHAR))
    inside = scoring.score_rtty("ABCDEFGHIJ", rtty_reception("ABCXDEFGHIJ", 0), window)
    between = scoring.score_rtty("ABCDEFGHIJ", rtty_reception("ABCDEXFGHIJ", 0), window)
    assert (inside.units, inside.units_ok, inside.failure) == (2, 1, "missed;false_text")
    assert (between.units, between.units_ok, between.failure) == (2, 2, "false_text")
    assert not inside.copied and not between.copied and between.cer == pytest.approx(1 / 10)


def test_rtty_deletion_and_out_of_window_noise() -> None:
    window = (10_000, 10_000 + round(12 * SAMPLES_PER_CHAR))
    noise = rtty_reception("QZX", 0)
    message_part = rtty_reception(" HELO WORLD ", 10_000)
    combined = RttyReception(
        text=noise.text + message_part.text,
        positions=np.concatenate([noise.positions, message_part.positions]),
        snr_db=-4.0,
        snr_chars=12,
    )
    score = scoring.score_rtty("HELLO WORLD", combined, window)
    # One L is lost, breaking "HELLO"; " WORL" and "D" print whole. The noise before the window is not scored.
    assert score.noise_prints == 3 and score.units == 3 and score.units_ok == 2
    assert score.cer == pytest.approx(1 / 11)
    assert score.reported_snr == -4.0


def test_free_end_alignment_ignores_garbage_before_and_after() -> None:
    ops = scoring.align_free_ends("ABC", "XXABCYY")
    assert [op for op, _, _ in ops].count("M") == 3
    assert sum(1 for op, _, _ in ops if op == "I") == 4


def test_levenshtein() -> None:
    assert scoring.levenshtein("KITTEN", "SITTING") == 3
    assert scoring.levenshtein("", "ABC") == 3
    assert scoring.levenshtein("SAME", "SAME") == 0


@pytest.mark.parametrize(
    ("text", "garbage_at_least"),
    [("CQ K1ABC CQ ... X ... Y", 2), ("W9XYZ599123ABCDEFGHIJKLM ... Q", 2), ("CQ K1ABC CQ ... X", 1)],
)
def test_gaps_after_garbage_past_the_last_frame(text: str, garbage_at_least: int) -> None:
    gaps = tuple(i for i in range(len(text)) if text.startswith("...", i))
    score = scoring.score_jtty(("CQ K1ABC CQ",), "CQ K1ABC CQ", [message(text, state="ended", gaps=gaps)])
    assert score.garbage >= garbage_at_least
    if text.startswith("CQ K1ABC CQ"):
        assert score.units_ok == score.units == 3 and score.failure == "false_text"
    else:
        # Garbage lines up a few characters by chance ("1ABC"), but never a run of real copy.
        assert score.units_ok < score.units and score.run_chars == 0 and "missed" in score.failure


def test_all_space_frame_is_never_credited_for_free() -> None:
    score = scoring.score_jtty(("CQ K1ABC", " ", "TU"), "CQ K1ABC TU", [message("CQ K1ABC TU")])
    # Every group printed, but the all-space frame is not credited as a frame, so it stays a frame miss.
    assert score.units_ok == score.units == 3 and score.garbage == 0 and score.failure == "missed"


def test_jtty_failure_classes_and_copied_characters() -> None:
    clean = scoring.score_jtty(FRAMES, CANONICAL, [message(CANONICAL)])
    assert clean.failure == "" and clean.copied_chars == sum(len(f.strip()) for f in FRAMES)
    missed = scoring.score_jtty(FRAMES, CANONICAL, [message("IS TOM QTH BOSTON RIG IC7300")])
    assert missed.failure == "missed"
    split_a = JttyMessage(1, "expired", "NAME IS TOM QTH", (), -10, 1500.0, 1.0)
    split_b = JttyMessage(2, "complete", "BOSTON RIG IC7300", (), -10, 1500.0, 9.0)
    assert scoring.score_jtty(FRAMES, CANONICAL, [split_a, split_b]).failure == "split"
    assert scoring.score_jtty(FRAMES, CANONICAL, [message(CANONICAL, state="ended")]).failure == "no_eom"
    extra = scoring.score_jtty(
        FRAMES, CANONICAL, [message(CANONICAL), JttyMessage(2, "complete", "TU NOW K9ZZ", (), -15, 1500.0, 5.0)]
    )
    assert extra.copied and extra.failure == "false_text"


def test_slot_and_rtty_failures_and_copied_characters() -> None:
    hit = scoring.score_slot("K1ABC W9XYZ -12", [SlotDecode("K1ABC W9XYZ -12", -20, 0.1, 1500.0, 0, 50)])
    assert hit.failure == "" and hit.copied_chars == len("K1ABC W9XYZ -12")
    miss = scoring.score_slot("K1ABC W9XYZ -12", [SlotDecode("CQ N0CALL FN42", -20, 0.1, 900.0, 0, 50)])
    assert miss.failure == "missed;false_text" and miss.copied_chars == 0
    window = (0, round(30 * SAMPLES_PER_CHAR))
    rtty_score = scoring.score_rtty("ABCDEFGHIJKLMNO", rtty_reception(" ABCDEFGXIJKLMNO ", 0), window)
    assert rtty_score.failure == "missed;false_text" and rtty_score.copied_chars == 10


def test_jtty_macro_needs_the_call_and_one_exchange_copy() -> None:
    over = next(o for o in keyboard_qso(Contest.RU, "K1ABC", "W9XYZ", "MA", "WI") if o.kind == "exch_run")
    frames, canonical = ("W9XYZ", "599 MA", "MA"), over.macro_words
    assert canonical == "W9XYZ 599 MA MA"
    clean = scoring.score_jtty_macro(over.macro, frames, canonical, [message(canonical)])
    assert clean.copied and (clean.units, clean.units_ok) == (1, 1) and clean.copied_chars == len(canonical)
    assert clean.failure == ""
    lost_repeat = scoring.score_jtty_macro(over.macro, frames, canonical, [message("W9XYZ 599 MA", state="expired")])
    assert lost_repeat.copied and lost_repeat.failure == ""
    lost_first = message("W9XYZ ... MA", state="expired", gaps=(6,))
    assert scoring.score_jtty_macro(over.macro, frames, canonical, [lost_first]).copied
    lost_call = scoring.score_jtty_macro(over.macro, frames, canonical, [message("... 599 MA MA", gaps=(0,))])
    assert not lost_call.copied and lost_call.units_ok == 0 and lost_call.copied_chars == 0
    assert lost_call.failure == "missed" and lost_call.reported_snr is None
    assert not scoring.score_jtty_macro(over.macro, frames, canonical, []).copied


def test_rtty_macro_needs_the_call_and_one_exchange_copy() -> None:
    from jtty_snr_study.messages import MacroToken

    tokens = (MacroToken("W9XYZ", 0), MacroToken("599"), MacroToken("MA", 1), MacroToken("MA", 1))
    window = (0, round(40 * SAMPLES_PER_CHAR))
    lost_repeat = scoring.score_rtty_macro(tokens, rtty_reception("W9XYZ 5Q9 MA", 0), window)
    assert lost_repeat.copied and lost_repeat.failure == "false_text"
    # MX could pass for MA: the exchange is received twice with different values, so it is ambiguous.
    first_bad = scoring.score_rtty_macro(tokens, rtty_reception("W9XYZ 5Q9 MX MA", 0), window)
    assert not first_bad.copied and first_bad.failure == "ambiguous;false_text"
    garbage_before = scoring.score_rtty_macro(tokens, rtty_reception("QX W9XYZ 599 MA MA", 0), window)
    assert garbage_before.copied
    both_bad = scoring.score_rtty_macro(tokens, rtty_reception("W9XYZ 599 MX MZ", 0), window)
    assert not both_bad.copied and both_bad.failure == "missed;false_text"
    bad_call = scoring.score_rtty_macro(tokens, rtty_reception("W9XYQ 599 MA MA", 0), window)
    assert not bad_call.copied
    inserted = scoring.score_rtty_macro(tokens, rtty_reception("W9X3YZ 599 MA MA", 0), window)
    assert not inserted.copied


def test_rtty_macro_call_with_an_attached_character_is_not_copied() -> None:
    from jtty_snr_study.messages import MacroToken

    tokens = (MacroToken("W9XYZ", 0), MacroToken("599"), MacroToken("MA", 1), MacroToken("MA", 1))
    window = (0, round(40 * SAMPLES_PER_CHAR))
    for printed in ("W9XYZT 599 MA MA", "KW9XYZ 599 MA MA", "W9XYZ599 MA MA", "W9XYZ 599 MAMA"):
        assert not scoring.score_rtty_macro(tokens, rtty_reception(printed, 0), window).copied, printed
    for printed in ("W9XYZ T 599 MA MA", "E W9XYZ 599 MA MA", "W9XYZ 599 MA MA E", "W9XYZ 599 MA E MA"):
        assert scoring.score_rtty_macro(tokens, rtty_reception(printed, 0), window).copied, printed
    conflicting = scoring.score_rtty_macro(tokens, rtty_reception("W9XYZ 599 MA MX", 0), window)
    assert not conflicting.copied and conflicting.failure == "ambiguous;false_text"


def test_matched_words_keeps_every_word_it_can() -> None:
    assert scoring.matched_words(["W9XYZ", "599", "MA", "MA"], ["W9XYZ", "T", "599", "MA", "MA"]) == {0, 1, 2, 3}
    assert scoring.matched_words(["W9XYZ", "599", "MA"], ["W9XYZ599", "MA"]) == {2}
    assert scoring.matched_words(["A", "B"], []) == set()


def test_macro_prefers_the_required_call_when_matches_tie() -> None:
    from jtty_snr_study.messages import MacroToken

    tokens = (MacroToken("CQ"), MacroToken("W1AW", 0), MacroToken("W1AW", 0), MacroToken("CQ"))
    window = (0, round(40 * SAMPLES_PER_CHAR))
    assert scoring.score_rtty_macro(tokens, rtty_reception("W1AW CQ CQ", 0), window).copied
    assert scoring.matched_words(["CQ", "W1AW", "W1AW", "CQ"], ["W1AW", "CQ", "CQ"], [1, 5, 5, 1]) == {1, 3}


def test_keyboard_scoring_handles_psk31_lower_case_and_its_margin() -> None:
    symbol = 384
    printed = " qth reno "
    positions = np.array([10_000 + 8 * symbol * i for i in range(len(printed))], dtype=np.int64)
    reception = RttyReception(text="xz" + printed, positions=np.concatenate([[0, 4000], positions]), snr_db=1.0,
                              snr_chars=len(printed))  # fmt: skip
    window = (10_000, int(positions[-1]) + 8 * symbol)
    score = scoring.score_keyboard("qth reno", reception, window, 4 * symbol)
    assert (score.units, score.units_ok, score.copied, score.noise_prints) == (2, 2, True, 2)
    assert score.printed == "QTH RENO" and score.reported_snr == 1.0
    # The RTTY wrapper is the same scorer with half an RTTY character of margin.
    rtty_window = (1000, 1000 + round(14 * SAMPLES_PER_CHAR))
    assert scoring.score_rtty("CQ K1ABC CQ", rtty_reception(" CQ K1ABC CQ ", 1000), rtty_window) == (
        scoring.score_keyboard(
            "CQ K1ABC CQ", rtty_reception(" CQ K1ABC CQ ", 1000), rtty_window, SAMPLES_PER_CHAR / 2.0
        )
    )


def count_matches(sent: str, printed: str) -> int:
    """Return how many sent characters character_matches pairs with printed ones."""
    return len(scoring.character_matches(sent, printed))


def test_character_matches_count_exact_matches_only() -> None:
    assert count_matches("CQ K1ABC", "cq  k1abc") == 8
    assert count_matches("ABCDE", "XXABXDEYY") == 4
    assert count_matches("ABC", "") == 0
    assert count_matches("", "ABC") == 0


@pytest.mark.parametrize(
    ("sent", "printed"),
    [
        ("CQ TEST W1AW", "CQ TEST W1AXW"),  # garbage before the last sent character
        ("ABCDE", "AXBCDE"),  # garbage after the first sent character
        ("ABCDEFGH", "ABDXEFGH"),  # a dropped character next to a garbage one
        ("ABCDEFGH", "ABCEDFGH"),  # two characters swapped: one of them is still in place
        ("ABCDEFGHIJ", "ABXXXCDEFGHIJ"),  # a burst of garbage longer than the text before it
        ("ABCDEFGHIJKLMNOP", "ABCDEXXXXXXXXFGHIJKLMNOP"),
        ("CQ TEST W1AW DE K1ABC", "CQ TEST W1AW EEEEETTTTTT DE K1ABC"),
    ],
)
def test_garbage_beside_a_right_character_does_not_uncopy_it(sent: str, printed: str) -> None:
    # Garbage printed between sent characters, however long, must not uncopy any of them.
    lost = {"ABDXEFGH": 1, "ABCEDFGH": 1}.get(printed, 0)
    assert count_matches(sent, printed) == len(sent) - lost


@pytest.mark.parametrize(
    ("sent", "printed"),
    [
        ("TU", "TUXU"),
        ("TU", "TU E U"),
        ("CQ CQ DE W1AW W1AW K", "CQ CQ DE W1AW W1AW K XQ K"),
        ("CQ CQ DE W1AW W1AW K", "CQ CQ DE W1AW W1AW K ORLD AW K"),
    ],
)
def test_trailing_garbage_does_not_split_a_clean_copy(sent: str, printed: str) -> None:
    # Letters of the sent text in garbage after it could pair with its end; the pairing in fewest runs keeps the copy.
    matches = scoring.character_matches(sent, printed)
    assert scoring.copied_runs(matches) == [len(sent)]
    assert scoring.run_characters(matches, len(sent)) == len(sent)


def test_a_garbage_burst_splits_runs_but_keeps_them_as_real_copy() -> None:
    matches = scoring.character_matches("CQ TEST W1AW DE K1ABC", "CQ TEST W1AW EEEEETTTTTT DE K1ABC")
    runs = scoring.copied_runs(matches)
    # The space before "DE" may pair on either side of the burst; either way both halves are runs of real copy.
    assert len(runs) == 2 and min(runs) >= scoring.COPY_RUN_MIN and sum(runs) == 21
    assert scoring.run_characters(matches, 21) == 21


def test_copied_runs_break_at_a_loss_or_an_inserted_character() -> None:
    assert scoring.copied_runs(scoring.character_matches("ABCDEFGHIJ", "ABCXDEFGHIJ")) == [3, 7]
    assert scoring.copied_runs(scoring.character_matches("ABCDEFGHIJ", "ABCQEFGHIJ")) == [3, 6]
    assert scoring.copied_runs(scoring.character_matches("ABC", "")) == []
    assert scoring.run_characters(scoring.character_matches("ABCDEFGHIJ", "ABCXDEFGHIJ"), 10) == 7
    assert scoring.run_characters(scoring.character_matches("TU", "TU"), 2) == 2


def test_snr_report_needs_a_run_without_inserted_garbage() -> None:
    window = (0, round(40 * SAMPLES_PER_CHAR))
    # Every character copied, but garbage inside splits the copy into runs of 3: no group prints whole, no run of
    # real copy.
    score = scoring.score_rtty("ABCDEFGHI", rtty_reception("ABCXDEFXGHI", 0), window)
    assert score.copied_chars == 0 and score.units_ok == 0 and score.run_chars == 0 and score.reported_snr is None


def test_group_counts_from_raw_columns_match_live_scoring() -> None:
    """Rescoring a stored row (runner.rescore_row) must give what scoring the trial live gives."""
    gapped = "NAME IS TO ... BOSTON RIG IC7300"
    jtty_cases = [
        [message(CANONICAL)],
        [message(gapped, gaps=(gapped.index("..."),))],
        [message("NAME IS TO", state="expired"), JttyMessage(2, "complete", "G IC7300", (), -16, 1500.0, 12.0)],
        [message("W9XYZ 599 123")],
    ]
    for messages in jtty_cases:
        live = scoring.score_jtty(FRAMES, CANONICAL, messages)
        counts = scoring.group_counts("jtty", CANONICAL, live.printed, live.copied)
        assert counts[:3] == (live.units, live.units_ok, live.run_chars)
    window = (0, round(40 * SAMPLES_PER_CHAR))
    for printed in (" ABCDEFGXIJKLMNO ", "ABCXDEFGHIJKLMNO", "QQ", ""):
        live = scoring.score_rtty("ABCDEFGHIJKLMNO", rtty_reception(printed, 0), window)
        counts = scoring.group_counts("rtty", "ABCDEFGHIJKLMNO", live.printed, live.copied)
        assert counts[:3] == (live.units, live.units_ok, live.run_chars)
        assert counts.characters == live.copied_chars
    for decodes in ([SlotDecode("K1ABC W9XYZ -12", -20, 0.1, 1500.0, 0, 50)], []):
        live = scoring.score_slot("K1ABC W9XYZ -12", decodes)
        counts = scoring.group_counts("ft8", "K1ABC W9XYZ -12", live.printed, live.copied)
        assert counts[:3] == (live.units, live.units_ok, live.run_chars)
        assert counts.characters == live.copied_chars


def test_group_starts_make_a_short_final_group_of_the_remainder() -> None:
    assert scoring.group_starts(5) == [0]
    assert scoring.group_starts(11) == [0, 5, 10]
    assert scoring.group_starts(14) == [0, 5, 10]
    assert scoring.group_starts(15) == [0, 5, 10]
    assert scoring.group_starts(3) == [0]
    assert scoring.group_starts(0) == [0]


@pytest.mark.parametrize(
    ("sent", "printed", "copied"),
    [
        ("ABCDEFGHIJKL", "ABCDEFGHIJKL", 3),  # every group, including the short final "KL"
        ("ABCDEFGHIJKL", "ABCDEXXXFGHIJKL", 3),  # garbage between groups breaks neither
        ("ABCDEFGHIJKL", "ABCXDEFGHIJKL", 2),  # garbage inside a group breaks it
        ("ABCDEFGHIJKL", "ABCDEFGHIJK", 2),  # a lost character costs its group
        ("ABCDEFGHIJ", "ABCDEEFGHIJ", 2),  # a repeated letter at a group boundary breaks neither group
        ("K1ABC K1ABC", "K1ABC", 1),  # one copy of a repeated call counts once
        ("abcdefghij", "ABCDE fghij", 2),  # case is ignored, and the space falls between the groups
        ("ABCDEFGHIJKL", "", 0),
        ("", "ABC", 0),  # an empty text's group is never copied
    ],
)
def test_copied_groups(sent: str, printed: str, copied: int) -> None:
    assert scoring.copied_groups(sent, printed) == copied


def test_ft8_message_gives_all_its_groups_or_none() -> None:
    hit = scoring.group_counts("ft8", "K1ABC W9XYZ -12", "", True)
    miss = scoring.group_counts("ft4", "K1ABC W9XYZ -12", "", False)
    assert tuple(hit) == (3, 3, 15, 15) and tuple(miss) == (3, 0, 0, 0)


@pytest.mark.parametrize(
    ("sent", "printed", "expected"),
    [
        ("ABCDEFGHIJKL", "ABCDEFGHIJKL", (3, 12)),
        ("ABCDEFGHIJKL", "ABCXDEFGHIJKL", (2, 7)),  # only the copied groups' characters count
        ("ABCDEFGHIJKL", "QXZQXZ", (0, 0)),  # scattered chance matches in noise count nothing
        ("ABCDEFGHIJKL", "KL FGHIJ", (1, 5)),  # out of order, only one group counts: the one holding more characters
        ("ABCDEFGHIJKL", "", (0, 0)),
    ],
)
def test_copied_group_text_counts_characters_only_in_copied_groups(
    sent: str, printed: str, expected: tuple[int, int]
) -> None:
    assert scoring.copied_group_text(sent, printed) == expected


@pytest.mark.parametrize(
    ("word", "value", "expected"),
    [
        ("K2ABD", "K2ABC", True),  # one character off
        ("QRLEW", "W4LEW", True),  # two characters off
        ("K8$':", "K8DJC", False),  # three characters off: junk
        ("2BMJ", "KE2BMJ", False),  # a fragment, another length
        ("WY", "WI", True),  # a short field: one character off passes
        ("CQ", "WI", False),  # but not two
        ("K2ABC", "K2ABC", False),  # the value itself is not a conflict
        ("HA<JDU", "HAWJDU", False),  # junk: a symbol no call, state or serial has
        ("W4.WF", "W4LWF", False),
        ("K2ABD?", "K2ABC", True),  # a query mark is ignored: K2ABD? reads as K2ABD
        ("K2ABC?", "K2ABC", False),  # and K2ABC? reads as the value itself
        ("K2AB?D", "K2ABC", False),  # inside a word a ? is a garbled symbol: junk
        ("W?Y", "WI", False),
        ("?K2ABD", "K2ABC", False),
    ],
)
def test_looks_like(word: str, value: str, expected: bool) -> None:
    assert scoring.looks_like(word, value) is expected


def test_a_correct_fixed_word_never_conflicts_with_a_field() -> None:
    # The serial 591 is one character from the fixed 599, but a correctly printed 599 is a sent word.
    words, groups = ["K8WTF", "599", "591", "591"], [0, None, 1, 1]
    assert scoring.ambiguous_fields(words, groups, ["K8WTF", "599", "591"]) == set()
    assert scoring.ambiguous_fields(words, groups, ["K8WTF", "599", "591", "592"]) == {1}
    # 599? reads as the sent 599 (a query mark is ignored), so it does not conflict with the serial either.
    assert scoring.ambiguous_fields(words, groups, ["K8WTF", "599?", "591"]) == set()


def test_field_groups_cover_every_keyboard_over() -> None:
    from jtty_snr_study.messages import Contest, keyboard_qso

    for contest in Contest:
        for over in (
            keyboard_qso(contest, "K1ABC", "W9XYZ", "MA", "WI")
            if contest is Contest.RU
            else keyboard_qso(contest, "K1ABC", "W9XYZ", 12, 34)
        ):
            plain, macro = scoring.field_groups(over.kind, False), scoring.field_groups(over.kind, True)
            assert plain is not None and len(plain) == len(scoring.normalize(over.text).split())
            assert macro == tuple(token.group for token in over.macro)
    assert scoring.field_groups("exch_run", False) == (0, None, 1)
    assert scoring.field_groups("nonsense", False) is None


def test_contest_over_with_a_conflicting_duplicate_is_not_copied() -> None:
    window = (0, round(40 * SAMPLES_PER_CHAR))
    fields = scoring.field_groups("call", False)
    clean = scoring.score_rtty("K2ABC", rtty_reception("K2ABC", 0), window, fields)
    doubled = scoring.score_rtty("K2ABC", rtty_reception("K2ABC K2ABD", 0), window, fields)
    assert clean.copied and clean.failure == ""
    assert not doubled.copied and doubled.failure == "ambiguous;false_text"
    jtty = scoring.score_jtty(("K2ABC",), "K2ABC", [message("K2ABC"), message("K2ABD")], fields)
    assert not jtty.copied and "ambiguous" in jtty.failure and "split" not in jtty.failure


def test_jtty_macro_with_a_conflicting_repeat_is_ambiguous() -> None:
    over = next(o for o in keyboard_qso(Contest.RU, "K1ABC", "W4LEW", "MA", "WI") if o.kind == "call")
    frames, canonical = ("W4LEW", "W4LEW"), over.macro_words
    lost = scoring.score_jtty_macro(over.macro, frames, canonical, [message("W4LEW ...", gaps=(6,))])
    conflict = scoring.score_jtty_macro(over.macro, frames, canonical, [message("QRLEW W4LEW")])
    assert lost.copied and lost.failure == ""
    assert not conflict.copied and conflict.failure.startswith("ambiguous")


def test_required_fixed_words_are_never_ambiguous() -> None:
    # AGN? and the TU of a TU over are required, but carry no information that could arrive with a different value.
    window = (0, round(40 * SAMPLES_PER_CHAR))
    agn = next(o for o in keyboard_qso(Contest.RU, "K1ABC", "W9XYZ", "MA", "WI") if o.kind == "agn")
    tu = next(o for o in keyboard_qso(Contest.RU, "K1ABC", "G4KCM", "MA", "WI") if o.kind == "tu")
    info = scoring.field_groups("agn", True, True)
    assert info == (None, None) and scoring.field_groups("tu", True, True) == (None, 1, None)
    garbled = scoring.score_rtty_macro(agn.macro, rtty_reception("AGN? AGNR", 0), window, info)
    assert garbled.copied and "ambiguous" not in garbled.failure
    tu_info = scoring.field_groups("tu", True, True)
    cu = scoring.score_rtty_macro(tu.macro, rtty_reception("TU K1ABC CU", 0), window, tu_info)
    assert cu.copied
    # The call in the TU over is information: a conflicting copy still makes it ambiguous.
    call = scoring.score_rtty_macro(tu.macro, rtty_reception("TU K1ABC K1ABD CQ", 0), window, tu_info)
    assert not call.copied and call.failure.startswith("ambiguous")


def test_junk_with_symbols_never_makes_a_macro_ambiguous() -> None:
    from jtty_snr_study.messages import MacroToken

    tokens = (MacroToken("HAWJDU", 0), MacroToken("HAWJDU", 0))
    window = (0, round(40 * SAMPLES_PER_CHAR))
    junk = scoring.score_rtty_macro(tokens, rtty_reception("HA<JDU HAWJDU", 0), window)
    queried = scoring.score_rtty_macro(tokens, rtty_reception("HAWJDU HAWJDA?", 0), window)
    assert junk.copied and "ambiguous" not in junk.failure
    assert not queried.copied and queried.failure.startswith("ambiguous")
