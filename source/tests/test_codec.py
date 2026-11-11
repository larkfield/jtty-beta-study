"""The wsjt-codec client: frame encoding, and real renders and decodes through the built binary."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
import pytest

from jtty_snr_study import channel, codec, messages, runner, scoring
from jtty_snr_study.codec import CodecClient
from jtty_snr_study.messages import MessageClass, Over


def test_session_header_and_frames() -> None:
    assert codec.SESSION_HEADER == b"WSJT\x00\x01\x0c\x00"
    frame = codec.encode_control({"t": "flush"})
    assert frame[0] == codec.FRAME_CONTROL
    assert struct.unpack("<I", frame[1:5])[0] == len(frame) - 5
    assert json.loads(frame[5:]) == {"t": "flush"} and b" " not in frame[5:]
    audio = codec.encode_audio(np.array([1, -2, 32767], dtype=np.int16))
    assert audio[0] == codec.FRAME_AUDIO and audio[5:] == struct.pack("<3h", 1, -2, 32767)


def test_configure_frames_turn_ap_off_and_blank_the_calls() -> None:
    for mode in ("FT8", "FT4"):
        frame = codec.CONFIGURE[mode]
        assert frame["ft8_ap_on"] is False and frame["depth"] == 3
        assert frame["mycall"] == frame["his_call"] == frame["his_grid"] == ""
    assert codec.CONFIGURE["JTTY"]["rxfreq"] == 1500 and codec.CONFIGURE["JTTY"]["ntol"] == 20


def _jtty_trial(client: CodecClient, text: str, snr_db: float, seed: int, lead: int = 12_000):
    render = client.render_jtty(text, 1503.3)
    total = lead + len(render.samples) + runner.JTTY_FRAME_SAMPLES + 6000
    rx = channel.receive(render.samples, lead, total, snr_db, np.random.default_rng(seed), channel.SSB_FILTER)
    return render, client.decode_jtty(rx.audio)


@pytest.mark.codec
@pytest.mark.parametrize("text", ["CQ K1ABC CQ", "W9XYZ 599 123", "NAME IS TOM QTH BOSTON RIG IC7300 ANT DIPOLE"])
def test_jtty_clean_signal_decodes_exactly(codec_client: CodecClient, text: str) -> None:
    render, messages = _jtty_trial(codec_client, text, 0.0, 1)
    score = scoring.score_jtty(render.frames, render.canonical, messages)
    groups = len(scoring.group_starts(len(render.canonical)))
    assert score.copied and score.units_ok == score.units == groups and score.garbage == 0
    assert score.frames == len(render.frames)
    assert "".join(render.frames).replace(" ", "") == render.canonical.replace(" ", "")
    assert messages[0].start_s == pytest.approx(1.0, abs=0.1)


@pytest.mark.codec
def test_jtty_frame_count_matches_the_design_document(codec_client: CodecClient) -> None:
    assert len(codec_client.render_jtty("CQ KA1ABC CQ", 1500.0).frames) == 1
    assert len(codec_client.render_jtty("WB9XYZ 599 123", 1500.0).frames) == 2
    assert len(codec_client.render_jtty("1D EMA", 1500.0).frames) == 1


@pytest.mark.codec
def test_jtty_at_minus_30_db_decodes_nothing(codec_client: CodecClient) -> None:
    render, messages = _jtty_trial(codec_client, "CQ K1ABC CQ", -30.0, 2)
    assert scoring.score_jtty(render.frames, render.canonical, messages).units_ok == 0


@pytest.mark.codec
def test_back_to_back_trials_do_not_leak(codec_client: CodecClient) -> None:
    _, first = _jtty_trial(codec_client, "CQ K1ABC CQ", 0.0, 3)
    _, second = _jtty_trial(codec_client, "W9XYZ TU", -30.0, 4, lead=20_000)
    assert first and not any("K1ABC" in message.text for message in second)


@pytest.mark.codec
@pytest.mark.parametrize("mode", ["ft8", "ft4"])
def test_slot_modes_decode_across_the_dt_and_frequency_range(codec_client: CodecClient, mode: str) -> None:
    nominal, dt_low, dt_high, freq_high = runner.SLOT_TIMING[mode]
    label = mode.upper()
    for start_s in (nominal + dt_low, nominal + dt_high):
        for freq in (runner.SLOT_LOW_HZ, freq_high):
            render = codec_client.render_slot(label, "K1ABC W9XYZ R-07", freq)
            rx = channel.receive(
                render.samples,
                round(start_s * 12000),
                codec.PERIOD_SAMPLES[label],
                -8.0,
                np.random.default_rng(5),
                channel.SSB_FILTER,
            )
            score = scoring.score_slot(render.msgsent, codec_client.decode_slot(label, rx.audio))
            assert score.copied, f"{label} at {freq} Hz, start {start_s} s"
            assert score.reported_snr is not None and abs(score.reported_snr + 8.0) <= 3.0


@pytest.mark.codec
def test_slot_period_length_is_enforced(codec_client: CodecClient) -> None:
    with pytest.raises(ValueError):
        codec_client.decode_slot("FT8", np.zeros(1000, dtype=np.int16))


@pytest.mark.codec
def test_a_missing_binary_is_reported(tmp_path) -> None:
    with pytest.raises(codec.CodecError):
        CodecClient(tmp_path / "no-such-codec", tmp_path / "work")


def test_failed_handshake_does_not_leave_the_process_running(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "fake-codec"
    fake.write_text('#!/bin/sh\nhead -c 8 >/dev/null\necho \'{"v":1,"t":"nope"}\'\nexec sleep 60\n')
    fake.chmod(0o755)
    started = []
    original = codec.subprocess.Popen

    def spy(*args, **kwargs):
        process = original(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(codec.subprocess, "Popen", spy)
    with pytest.raises(codec.CodecError):
        CodecClient(fake, tmp_path / "work", timeout_s=10)
    assert started and started[0].poll() is not None


@pytest.mark.codec
def test_native_contest_templates_give_the_design_document_frame_counts(codec_client: CodecClient) -> None:
    from jtty_snr_study.messages import Contest, keyboard_qso

    expected = {"cq": 1, "call": 1, "exch_run": 2, "exch_sp": 1, "tu": 1, "agn": 1}
    for contest, run_exch, sp_exch in ((Contest.RU, "MA", "WI"), (Contest.SERIAL, 101, 57)):
        for over in keyboard_qso(contest, "K1ABC", "W9XYZ", run_exch, sp_exch):
            render = codec_client.render_jtty(over.jtty, 1500.0)
            assert len(render.frames) == expected[over.kind], (contest, over.kind, render.frames)
            assert render.canonical == over.text


@pytest.mark.codec
@pytest.mark.parametrize("mode", ["FT8", "FT4"])
def test_ru_contest_messages_render_and_decode(codec_client: CodecClient, mode: str) -> None:
    for text in ("CQ RU K1ABC FN42", "K1ABC W9XYZ 579 WI", "W9XYZ K1ABC R 589 MA", "K1ABC W9XYZ RR73"):
        render = codec_client.render_slot(mode, text, 1200.0)
        assert render.msgsent == text
        rx = channel.receive(
            render.samples, 4800, codec.PERIOD_SAMPLES[mode], -6.0, np.random.default_rng(7), channel.SSB_FILTER
        )
        assert scoring.score_slot(render.msgsent, codec_client.decode_slot(mode, rx.audio)).copied, text


@pytest.mark.codec
def test_measured_bandwidths_match_the_protocol_documents(codec_client: CodecClient) -> None:
    for mode, nominal in (("FT8", 50.0), ("FT4", 83.3)):
        width = channel.occupied_bandwidth(codec_client.render_slot(mode, "K1ABC W9XYZ EN37", 1500.0).samples)[2]
        assert width == pytest.approx(nominal, rel=0.1), mode
    jtty = codec_client.render_jtty("THE QUICK BROWN FOX JUMPS", 1500.0)
    assert channel.occupied_bandwidth(jtty.samples)[2] == pytest.approx(127.0, rel=0.1)


@pytest.mark.codec
def test_reference_build_counts_jtty_frames_and_rtty_characters(codec_client: CodecClient, tmp_path) -> None:
    config = runner.StudyConfig(run_dir=tmp_path, codec_binary=tmp_path / "unused")
    reference = runner.build_reference(config, codec_client)
    ru = reference["qso"]["ru"]
    assert [o["units"] for o in ru["jtty"]] == [1, 1, 2, 1, 1, 1]
    assert sum(o["info_bits"] for o in ru["jtty"] if o["kind"] in ("call", "exch_run", "exch_sp", "tu")) == 170
    exch = next(o for o in ru["rtty"] if o["kind"] == "exch_run")
    assert exch["info_bits"] == 5 * len("W9XYZ 599 MA") and exch["units"] > len("W9XYZ 599 MA")
    assert [o["kind"] for o in ru["ft8"]] == ["cq", "call_exch", "exch_run", "tu"]
    assert set(reference["mode_info_measured"]) == {"jtty", "ft8", "ft4", "rtty", "psk31"}


def test_jtty_macro_trial_sends_the_macro_words_and_scores_one_unit(codec_binary: Path, tmp_path: Path) -> None:
    config = runner.StudyConfig(run_dir=tmp_path, codec_binary=codec_binary, modes=("jtty",))
    trial_runner = runner.TrialRunner(config, tmp_path / "work", None)
    row = trial_runner.run("jtty", MessageClass.CONTEST_MACRO, 10.0, 0, "fixed")
    trial_runner.close()
    over = messages.message_for(
        MessageClass.CONTEST_MACRO,
        runner.content_rng(config.seed, MessageClass.CONTEST_MACRO, 10.0, 0),
        trial_runner.calls,
    )
    assert isinstance(over, Over)
    assert row["error"] == "" and row["sent"] == over.macro_words
    assert row["units"] == 1 and row["units_ok"] == 1 and row["copied"] == 1


def test_reference_has_jtty_macro_overs(codec_client: CodecClient, tmp_path: Path) -> None:
    config = runner.StudyConfig(run_dir=tmp_path, codec_binary=tmp_path / "x", modes=("jtty",))
    overs = {over["kind"]: over for over in runner.build_reference(config, codec_client)["qso"]["ru"]["jtty-macro"]}
    assert overs["call"]["units"] == 2 and overs["exch_run"]["units"] == 3 and overs["tu"]["units"] == 1
