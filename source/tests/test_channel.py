"""The HF receiver model: filters, noise level, SNR calibration, clipping."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import signal

from jtty_snr_study import channel


def test_ssb_filter_is_flat_in_passband_and_steep_outside() -> None:
    gain = channel.SSB_FILTER.gain_db(np.linspace(300, 2700, 200))
    assert gain.max() < 0.05 and gain.min() > -0.25
    assert channel.SSB_FILTER.gain_db([50, 100, 150, 200]).max() < -40
    assert channel.SSB_FILTER.gain_db([3400, 4000, 5000]).max() < -40


def test_rtty_filter_is_500_hz_around_mark_and_space() -> None:
    gain = channel.RTTY_FILTER.gain_db(np.linspace(1960, 2460, 100))
    assert gain.max() < 0.05 and gain.min() > -0.25
    assert channel.RTTY_FILTER.gain_db([1500, 1700, 1800, 2620, 2800, 3000]).max() < -40
    assert 480 < channel.RTTY_FILTER.enbw_hz < 560


@pytest.mark.parametrize("rig", [channel.SSB_FILTER, channel.RTTY_FILTER])
def test_no_signal_level_reads_30_db_on_the_wsjtx_meter(rig: channel.RigFilter) -> None:
    rng = np.random.default_rng(1)
    silence = np.zeros(10, dtype=np.float32)
    reception = channel.receive(silence + 1.0, 0, 240_000, -200.0, rng, rig)
    assert channel.meter_db(reception.audio) == pytest.approx(30.0, abs=0.5)


def _spectral_snr(audio: np.ndarray, tone_hz: float, rig: channel.RigFilter) -> float:
    """Measure SNR (2500 Hz reference) from the audio's spectrum, referred back to the antenna through the filter's
    known response: tone power over the noise density beside it."""
    freqs, psd = signal.welch(audio.astype(np.float64), fs=channel.SAMPLE_RATE, nperseg=8192)
    psd = psd / 10 ** (rig.gain_db(freqs) / 10)
    df = freqs[1] - freqs[0]
    near = (np.abs(freqs - tone_hz) > 60) & (np.abs(freqs - tone_hz) < 400)
    density = float(np.mean(psd[near]))  # one-sided, counts^2 / Hz
    tone = np.abs(freqs - tone_hz) <= 8
    tone_power = float(np.sum(psd[tone]) * df) - density * float(np.count_nonzero(tone)) * df
    return 10 * np.log10(tone_power / (density * channel.REFERENCE_BANDWIDTH_HZ))


@pytest.mark.parametrize("snr_db", [-20.0, -5.0, 5.0])
@pytest.mark.parametrize("filtered", [False, True])
def test_snr_is_calibrated_in_2500_hz_before_and_after_the_filter(snr_db: float, filtered: bool) -> None:
    rig = channel.SSB_FILTER if filtered else channel.RigFilter.design("flat", 20.0, 5900.0)
    rng = np.random.default_rng(7)
    n = 12_000 * 180
    tone = np.cos(2 * np.pi * 1500.0 * np.arange(n) / channel.SAMPLE_RATE)
    reception = channel.receive(tone, 0, n, snr_db, rng, rig)
    assert _spectral_snr(reception.audio, 1500.0, rig) == pytest.approx(snr_db, abs=0.1)
    assert reception.snr_measured_db == pytest.approx(snr_db, abs=0.05)


def test_measured_true_snr_tracks_the_noise_realization() -> None:
    values = []
    for seed in range(40):
        rng = np.random.default_rng(seed)
        tone = np.cos(2 * np.pi * 1500.0 * np.arange(2400) / channel.SAMPLE_RATE)
        values.append(channel.receive(tone, 1000, 6000, -10.0, rng, channel.SSB_FILTER).snr_measured_db)
    assert np.mean(values) == pytest.approx(-10.0, abs=0.05)
    assert np.std(values) > 0.01


def test_no_clipping_at_the_top_of_the_sweep() -> None:
    rng = np.random.default_rng(2)
    tone = np.cos(2 * np.pi * 1500.0 * np.arange(120_000) / channel.SAMPLE_RATE)
    assert channel.receive(tone, 0, 120_000, 10.0, rng, channel.SSB_FILTER).clipped == 0


def test_transmission_must_fit_and_must_not_be_silent() -> None:
    rng = np.random.default_rng(0)
    with pytest.raises(ValueError):
        channel.receive(np.ones(100), 50, 120, 0.0, rng, channel.SSB_FILTER)
    with pytest.raises(ValueError):
        channel.receive(np.zeros(100), 0, 200, 0.0, rng, channel.SSB_FILTER)


def test_watterson_without_fading_is_the_identity() -> None:
    tone = np.cos(2 * np.pi * 1500.0 * np.arange(24_000) / channel.SAMPLE_RATE)
    static = channel.FadingModel("static", 0.0, 0.0, "")
    faded = np.real(channel.watterson(signal.hilbert(tone), len(tone), static, np.random.default_rng(0)))
    assert np.max(np.abs(faded - tone)) < 1e-9


@pytest.mark.parametrize("name", ["MQ", "MM", "MD", "HM"])
def test_watterson_keeps_the_transmission_power(name: str) -> None:
    tone = np.cos(2 * np.pi * 1500.0 * np.arange(12_000 * 20) / channel.SAMPLE_RATE)
    model = channel.FADING_MODELS[name]
    faded = np.real(channel.watterson(signal.hilbert(tone), len(tone), model, np.random.default_rng(1)))
    assert np.mean(faded**2) == pytest.approx(0.5, rel=0.02)


def test_watterson_doppler_spread_matches_the_model() -> None:
    # One path's process has power spectrum exp(-2 (f/spread)^2): an RMS width of spread/2.
    n = 12_000 * 400
    tone = np.exp(2j * np.pi * 1500.0 * np.arange(n) / channel.SAMPLE_RATE)
    model = channel.FadingModel("x", 1.0, 0.0, "")
    faded = channel.watterson(tone, n, model, np.random.default_rng(2))
    spectrum = np.abs(np.fft.fft(faded)) ** 2
    freqs = np.fft.fftfreq(n, 1 / channel.SAMPLE_RATE) - 1500.0
    near = np.abs(freqs) < 5
    width = np.sqrt(np.sum(spectrum[near] * freqs[near] ** 2) / np.sum(spectrum[near]))
    assert width == pytest.approx(0.5, rel=0.25)


def test_faded_reception_keeps_snr_on_average() -> None:
    values = []
    for seed in range(30):
        tone = np.cos(2 * np.pi * 1500.0 * np.arange(24_000) / channel.SAMPLE_RATE)
        rx = channel.receive(
            tone, 6000, 40_000, -8.0, np.random.default_rng(seed), channel.SSB_FILTER, channel.FADING_MODELS["MM"]
        )
        values.append(rx.snr_measured_db)
    assert np.mean(values) == pytest.approx(-8.0, abs=0.1)


def test_occupied_bandwidth_of_a_tone_and_of_noise() -> None:
    tone = np.cos(2 * np.pi * 1000.0 * np.arange(120_000) / channel.SAMPLE_RATE)
    low, high, width = channel.occupied_bandwidth(tone)
    assert low < 1000 < high and width < 3
    noise = channel.noise_only(240_000, np.random.default_rng(0), channel.SSB_FILTER)
    assert channel.occupied_bandwidth(noise)[2] == pytest.approx(2400, rel=0.05)


def test_noise_only_reads_30_db() -> None:
    audio = channel.noise_only(240_000, np.random.default_rng(5), channel.RTTY_FILTER)
    assert channel.meter_db(audio) == pytest.approx(30.0, abs=0.5)
