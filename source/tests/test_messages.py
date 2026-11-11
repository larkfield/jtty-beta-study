"""Message generators: valid calls, character sets, reference contest QSOs, reproducibility."""

from __future__ import annotations

import numpy as np

from jtty_snr_study import messages
from jtty_snr_study.messages import Contest, MessageClass, Over
from jtty_snr_study.rtty import RTTY_CHARACTERS


def test_callsign_file_has_many_standard_calls() -> None:
    calls = messages.load_callsigns()
    assert len(calls) > 50_000
    assert all(messages.STANDARD_CALL.match(call) for call in calls[:1000])
    assert not any("/" in call for call in calls)


def test_contest_overs_cover_every_kind_and_use_baudot_characters() -> None:
    calls = messages.load_callsigns()
    rng = np.random.default_rng(0)
    seen = set()
    for _ in range(600):
        over = messages.contest_over(rng, calls)
        seen.add((over.contest, over.kind))
        assert set(over.text) <= RTTY_CHARACTERS and set(over.macro_text) <= RTTY_CHARACTERS
        assert over.text == over.text.strip() and "  " not in over.text
        assert over.jtty, "every keyboard over must say how JTTY sends it"
    assert seen == {(contest, kind) for contest in Contest for kind in messages.KEYBOARD_TRIAL_KINDS}


def test_free_text_uses_only_characters_baudot_carries() -> None:
    rng = np.random.default_rng(1)
    for _ in range(300):
        text = messages.text_message(rng)
        assert set(text) <= RTTY_CHARACTERS and messages.TEXT_MIN_CHARS <= len(text) <= messages.TEXT_MAX_CHARS


def test_keyboard_qso_uses_native_templates_per_contest() -> None:
    ru = {o.kind: o for o in messages.keyboard_qso(Contest.RU, "K1ABC", "W9XYZ", "MA", "WI")}
    assert ru["exch_run"].text == "W9XYZ 599 MA"
    assert ru["exch_run"].jtty == {"template": "%H %E", "profile": "rtty", "his_call": "W9XYZ", "exchange": "MA"}
    assert ru["exch_sp"].jtty == {"template": "%E", "profile": "rtty", "exchange": "WI"}
    assert ru["exch_run"].macro_text == "\r\nW9XYZ 599 MA MA\r\n"
    serial = {o.kind: o for o in messages.keyboard_qso(Contest.SERIAL, "K1ABC", "W9XYZ", 101, 57)}
    assert serial["exch_run"].text == "W9XYZ 599 101" and serial["exch_sp"].text == "599 057"
    assert serial["exch_sp"].jtty == {"template": "599 %N", "profile": "none", "serial": 57}


def test_macro_groups_require_the_call_and_one_exchange_copy() -> None:
    exch = {o.kind: o for o in messages.keyboard_qso(Contest.RU, "K1ABC", "W9XYZ", "MA", "WI")}["exch_run"]
    groups = [token.group for token in exch.macro]
    assert groups == [0, None, 1, 1]


def test_ft_qsos() -> None:
    rng = np.random.default_rng(2)
    ru = messages.ft_qso(Contest.RU, "K1ABC", "W9XYZ", rng)
    assert [o.kind for o in ru] == ["cq", "call_exch", "exch_run", "tu"]
    assert ru[0].text.startswith("CQ RU K1ABC ") and " 579 " in ru[1].text and " R 589 " in ru[2].text
    standard = messages.ft_qso(Contest.SERIAL, "K1ABC", "W9XYZ", rng)
    assert [o.kind for o in standard] == ["cq", "call", "exch_run", "exch_sp", "tu"]
    assert standard[-1].text == "W9XYZ K1ABC RR73"


def test_reference_qsos_are_fixed_and_complete() -> None:
    first, second = messages.reference_qsos(), messages.reference_qsos()
    assert first == second
    for contest in Contest:
        keyboard_kinds = [o.kind for o in first[contest]["keyboard"]]
        assert set(messages.QSO_KINDS["keyboard"]) <= set(keyboard_kinds) and "agn" in keyboard_kinds
        ft_kinds = {o.kind for o in first[contest]["ft"]}
        assert set(messages.QSO_KINDS[f"ft-{contest}"]) <= ft_kinds


def test_message_for_dispatches_by_class_and_is_reproducible() -> None:
    calls = messages.load_callsigns()
    assert isinstance(messages.message_for(MessageClass.TEXT, np.random.default_rng(3), calls), str)
    assert isinstance(messages.message_for(MessageClass.CONTEST, np.random.default_rng(3), calls), Over)
    assert isinstance(messages.message_for(MessageClass.QSO, np.random.default_rng(3), calls), Over)
    a = messages.message_for(MessageClass.CONTEST, np.random.default_rng(42), calls)
    b = messages.message_for(MessageClass.CONTEST_MACRO, np.random.default_rng(42), calls)
    assert a == b


def test_grids_are_valid_and_never_rr73() -> None:
    rng = np.random.default_rng(3)
    for _ in range(2000):
        grid = messages._grid(rng)
        assert grid != "RR73" and "A" <= grid[0] <= "R" and "A" <= grid[1] <= "R" and grid[2:].isdigit()
