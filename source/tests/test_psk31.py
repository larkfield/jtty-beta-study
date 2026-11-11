"""The C PSK31 modulator and reference demodulator."""

from __future__ import annotations

import math
from collections import Counter

import numpy as np
import pytest

from jtty_snr_study import _psk31, channel, messages, psk31


def test_varicode_table_is_complete_and_shortest_first() -> None:
    codes = ["".join(map(str, _psk31.encode(chr(c))[:-2])) for c in range(128)]
    assert len(set(codes)) == 128
    assert all(code[0] == "1" and code[-1] == "1" and "00" not in code for code in codes)
    # Every legal code of up to 9 bits is used (1, 1, 2, 3, 5, ... of each length), and 40 of the 10-bit ones.
    assert sorted(Counter(map(len, codes)).items()) == [
        (1, 1), (2, 1), (3, 2), (4, 3), (5, 5), (6, 8), (7, 13), (8, 21), (9, 34), (10, 40)
    ]  # fmt: skip
    assert psk31.char_bits(" ") == 3 and psk31.char_bits("e") == 4 and psk31.char_bits("E") == 9


def test_round_trip_of_every_printable_character_in_both_cases() -> None:
    text = "".join(chr(c) for c in range(33, 127))
    transmission = psk31.modulate(text)
    reception = psk31.demodulate(np.concatenate([transmission.samples, np.zeros(2000, np.float32)]))
    assert reception.text.strip() == text


def test_cr_and_lf_print_as_spaces() -> None:
    reception = psk31.demodulate(psk31.modulate("\r\nK1ABC 599 MA\r\n", frame_with_spaces=False).samples)
    assert reception.text.split() == ["K1ABC", "599", "MA"]


def test_message_window_and_positions_match_the_transmitter() -> None:
    transmission = psk31.modulate("hello world")
    _, starts, _ = _psk31.modulate(" hello world ", 12000.0, psk31.CARRIER_HZ, psk31.BAUD, 32, 32)
    reception = psk31.demodulate(transmission.samples)
    inside = [
        (c, p)
        for c, p in zip(reception.text, reception.positions, strict=True)
        if transmission.message_start - psk31.SCORING_MARGIN <= p < transmission.message_end
    ]
    assert "".join(c for c, _ in inside).strip() == "hello world"
    printed_starts = [p for c, p in inside]
    assert np.max(np.abs(np.array(printed_starts) - np.array(starts))) < psk31.SAMPLES_PER_SYMBOL / 4


def test_unsupported_characters_are_rejected() -> None:
    with pytest.raises(ValueError):
        psk31.modulate("café")
    with pytest.raises(ValueError):
        psk31.modulate("tab\there")
    with pytest.raises(ValueError):
        psk31.modulate("", frame_with_spaces=False)
    with pytest.raises(ValueError):
        _psk31.modulate("Aé", 12000.0, 1000.0, 31.25, 0, 0)
    with pytest.raises(ValueError):
        _psk31.demodulate(np.zeros(100), 12000.0, 1000.0, 31.0)


def test_tuning_error_moves_the_carrier_and_the_decoder_snaps_to_it() -> None:
    rng = np.random.default_rng(2)
    transmission = psk31.modulate("cq cq de k1abc k1abc pse k", tuning_error_hz=4.5)
    spectrum = np.abs(np.fft.rfft(transmission.samples * np.hanning(len(transmission.samples)))) ** 2
    freqs = np.fft.rfftfreq(len(transmission.samples), 1 / channel.SAMPLE_RATE)
    centroid = float(np.sum(freqs * spectrum) / np.sum(spectrum))
    assert centroid == pytest.approx(psk31.CARRIER_HZ + 4.5, abs=1.0)
    lead = 9000
    reception = channel.receive(
        transmission.samples, lead, lead + len(transmission.samples) + 6000, -6.0, rng, channel.SSB_FILTER
    )
    window = (lead + transmission.message_start, lead + transmission.message_end)
    decoded = psk31.demodulate(reception.audio, snr_window=window)
    inside = "".join(
        c
        for c, p in zip(decoded.text, decoded.positions, strict=True)
        if window[0] - psk31.SCORING_MARGIN <= p < window[1] + psk31.SCORING_MARGIN
    )
    assert inside.strip() == "cq cq de k1abc k1abc pse k"


@pytest.mark.parametrize("ebn0_db", [4.0, 6.0])
def test_bit_error_rate_is_within_about_a_db_of_differential_bpsk_theory(ebn0_db: float) -> None:
    # The matched filter's inter-symbol interference costs about 0.5 dB at 4 dB Eb/N0 and 1 dB at 6 dB.
    rng = np.random.default_rng(11)
    text = "".join(rng.choice(list("etaoinshrdlu cmfwypvbgkqjxz"), 2000))
    audio, starts, _ = _psk31.modulate(text, 12000.0, 1000.0, 31.25, 0, 0)
    clean = np.concatenate([np.frombuffer(audio, np.float32).astype(np.float64), np.zeros(1000)])
    expected = np.frombuffer(_psk31.encode(text), np.uint8)
    bit_energy = float(np.mean(clean[:-1000] ** 2)) / 31.25
    sigma = math.sqrt(bit_energy / 10 ** (ebn0_db / 10) * 12000.0 / 2)
    noisy = clean + rng.normal(0.0, sigma, len(clean))
    bits = np.frombuffer(psk31.slice_bits(noisy, float(starts[0]), len(expected)), np.uint8)
    measured = float(np.mean(bits != expected))
    equivalent_db = 10 * math.log10(-math.log(2 * measured))
    assert ebn0_db - 1.3 < equivalent_db < ebn0_db + 0.2


def test_reported_snr_is_close_to_true_snr() -> None:
    rng = np.random.default_rng(4)
    text = " ".join(messages.text_message(rng).lower() for _ in range(3))
    transmission = psk31.modulate(text, tuning_error_hz=-3.0)
    lead = 6000
    total = lead + len(transmission.samples) + 6000
    for snr_db in (-10.0, 0.0, 10.0):
        reception = channel.receive(transmission.samples, lead, total, snr_db, rng, channel.SSB_FILTER)
        window = (lead + transmission.message_start, lead + transmission.message_end)
        decoded = psk31.demodulate(reception.audio, snr_window=window)
        assert decoded.snr_db is not None
        assert decoded.snr_db == pytest.approx(reception.snr_measured_db, abs=1.5)
        assert decoded.snr_chars > 50


def test_band_noise_prints_garbage_without_a_squelch() -> None:
    audio = channel.noise_only(60 * 12000, np.random.default_rng(3), channel.SSB_FILTER)
    assert len(psk31.demodulate(audio).text.strip()) > 20


def test_demodulator_accepts_int16_float32_and_float64_audio() -> None:
    samples = np.concatenate([psk31.modulate("cq cq").samples, np.zeros(2000, np.float32)])
    for audio in (samples, samples.astype(np.float64), (samples * 10000).astype(np.int16)):
        assert psk31.demodulate(audio).text.strip() == "cq cq"
    with pytest.raises(TypeError):
        _psk31.demodulate(np.zeros(10, dtype=np.int32), 12000.0, 1000.0, 31.25, 0, 10)
