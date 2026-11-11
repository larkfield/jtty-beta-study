"""The C RTTY modulator and reference demodulator."""

from __future__ import annotations

import math

import numpy as np
import pytest

from jtty_snr_study import _rtty, channel, rtty

LETTER_CODES = "\0E\nA SIU\rDRJNFCKTZLWHYPQOBG\0MXV\0"


def test_round_trip_of_every_character_with_shifts_and_unshift_on_space() -> None:
    text = "CQ TEST K1ABC 599 123 5NN/? A1B2 -3 '4' (5) 6.7,8;9 $10 !# &: \"Z\""
    transmission = rtty.modulate(text)
    reception = rtty.demodulate(transmission.samples)
    assert reception.text.strip() == text


def test_figures_after_a_space_resend_the_figures_shift() -> None:
    # With unshift-on-space the receiver drops to letters after each space, so "1 2" must still print as figures.
    reception = rtty.demodulate(rtty.modulate("1 2 3").samples)
    assert reception.text.strip() == "1 2 3"


def test_message_window_covers_the_message_characters() -> None:
    transmission = rtty.modulate("HELLO")
    reception = rtty.demodulate(transmission.samples)
    inside = [
        c
        for c, p in zip(reception.text, reception.positions, strict=True)
        if transmission.message_start - 5 <= p < transmission.message_end
    ]
    assert "".join(inside).strip() == "HELLO"


def test_unsupported_characters_are_rejected() -> None:
    with pytest.raises(ValueError):
        rtty.modulate("NO @ SIGN")
    with pytest.raises(ValueError):
        _rtty.modulate("A@", 12000.0, 2125.0, 2295.0, 45.45, 1.5, 0.0, 0, 0)


def test_tuning_error_moves_both_tones() -> None:
    samples = rtty.modulate("RYRYRYRYRY", tuning_error_hz=40.0).samples
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    freqs = np.fft.rfftfreq(len(samples), 1 / channel.SAMPLE_RATE)
    peak = freqs[np.argmax(spectrum)]
    assert min(abs(peak - 2165.0), abs(peak - 2335.0)) < 15


@pytest.mark.parametrize("ebn0_db", [6.0, 10.0])
def test_bit_error_rate_matches_noncoherent_fsk_theory(ebn0_db: float) -> None:
    rng = np.random.default_rng(11)
    letters = "".join(rng.choice(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"), 3000))
    audio, starts = _rtty.modulate(letters, 12000.0, 2125.0, 2295.0, 45.45, 1.0, 0.0, 0, 0)
    clean = np.frombuffer(audio, np.float32).astype(np.float64)
    expected = []
    for char in letters:
        code = LETTER_CODES.index(char)
        expected += [0] + [(code >> bit) & 1 for bit in range(5)] + [1]
    bit_energy = 0.5 / 45.45
    sigma = math.sqrt(bit_energy / 10 ** (ebn0_db / 10) * 12000.0 / 2)
    noisy = clean + rng.normal(0.0, sigma, len(clean))
    bits = np.frombuffer(
        _rtty.slice_bits(noisy, 12000.0, 2125.0, 2295.0, 45.45, float(starts[0]), len(expected)), np.uint8
    )
    measured = float(np.mean(bits != np.array(expected)))
    theory = 0.5 * math.exp(-(10 ** (ebn0_db / 10)) / 2)
    assert 0.75 * theory < measured < 1.35 * theory


def test_reported_snr_is_close_to_true_snr_at_moderate_snr() -> None:
    rng = np.random.default_rng(4)
    transmission = rtty.modulate("THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG 0123456789 " * 3)
    lead = 6000
    total = lead + len(transmission.samples) + 6000
    reception = channel.receive(transmission.samples, lead, total, -2.0, rng, channel.RTTY_FILTER)
    window = (lead + transmission.message_start, lead + transmission.message_end)
    decoded = rtty.demodulate(reception.audio, snr_window=window)
    assert decoded.snr_db is not None
    assert decoded.snr_db == pytest.approx(reception.snr_measured_db, abs=1.0)
    assert decoded.snr_chars > 100


def test_demodulator_accepts_int16_float32_and_float64_audio() -> None:
    samples = rtty.modulate("CQ CQ").samples
    for audio in (samples, samples.astype(np.float64), (samples * 10000).astype(np.int16)):
        assert rtty.demodulate(audio).text.strip() == "CQ CQ"
    with pytest.raises(TypeError):
        _rtty.demodulate(np.zeros(10, dtype=np.int32), 12000.0, 2125.0, 2295.0, 45.45, 1.5, 0, 10)
