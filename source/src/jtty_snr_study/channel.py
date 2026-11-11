"""HF receiver model: band noise, signal level, the rig's IF filter, and the sound card.

The chain mirrors a station. White Gaussian band noise and the signal arrive at the "antenna"; the rig's receive
filter shapes both; the sound card delivers 16-bit audio at a fixed gain (AGC off, as the WSJT-X user guide
recommends) with the no-signal level at about 30 dB on WSJT-X's input meter.

SNR follows the WSJT-X convention used by ft8sim, ft4sim and sjtty: signal power relative to the noise power in a
2500 Hz reference bandwidth, measured at the antenna (i.e. against the in-passband noise density). The clean
waveform is scaled from its measured power, so the renderer's normalization does not matter.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy import signal

SAMPLE_RATE: float = 12000.0
"""WSJT-X's internal sample rate (after its 48 kHz -> 12 kHz decimation), in Hz."""

NYQUIST_HZ: float = SAMPLE_RATE / 2.0
"""Bandwidth of the real white antenna noise, in Hz."""

REFERENCE_BANDWIDTH_HZ: float = 2500.0
"""Noise bandwidth that WSJT-X SNR reports are referred to."""

NOISE_METER_DB: float = 30.0
"""No-signal level on WSJT-X's input meter, 10*log10(mean square) of the int16 samples (lib/symspec.f90)."""

PREROLL_SAMPLES: int = 1200
"""Noise run through the filter before the trial starts, discarded, so the filter is in steady state."""


@dataclass(frozen=True)
class RigFilter:
    """A receiver IF filter, modeled as an analog-style causal elliptic bandpass (non-linear phase).

    ``low_hz``-``high_hz`` is the passband (ripple at most ``ripple_db``); ``enbw_hz`` is its equivalent noise
    bandwidth, the integral of the power response over 0..Nyquist, used to set the band-noise level.
    """

    name: str
    low_hz: float
    high_hz: float
    ripple_db: float
    sos: npt.NDArray[np.float64]
    enbw_hz: float

    @classmethod
    def design(
        cls, name: str, low_hz: float, high_hz: float, order: int = 6, ripple_db: float = 0.2, stop_db: float = 60.0
    ) -> RigFilter:
        """Design an elliptic bandpass with the given passband edges (``order`` per edge)."""
        sos = signal.ellip(order, ripple_db, stop_db, [low_hz, high_hz], btype="bandpass", fs=SAMPLE_RATE, output="sos")
        freqs, response = signal.sosfreqz(sos, worN=1 << 16, fs=SAMPLE_RATE)
        power = np.abs(response) ** 2
        enbw = float(np.sum(power) * (freqs[1] - freqs[0]))
        return cls(name=name, low_hz=low_hz, high_hz=high_hz, ripple_db=ripple_db, sos=sos, enbw_hz=enbw)

    def gain_db(self, freqs_hz: npt.ArrayLike) -> npt.NDArray[np.float64]:
        """Return the filter's power gain in dB at the given audio frequencies."""
        _, response = signal.sosfreqz(self.sos, worN=np.asarray(freqs_hz, dtype=np.float64), fs=SAMPLE_RATE)
        return 20.0 * np.log10(np.maximum(np.abs(response), 1e-12))


SSB_FILTER: RigFilter = RigFilter.design("SSB 2.4 kHz", 300.0, 2700.0)
"""USB/USB-D receive filter used for JTTY, FT8 and FT4."""

RTTY_FILTER: RigFilter = RigFilter.design("RTTY 500 Hz", 1960.0, 2460.0)
"""Narrow RTTY receive filter centered on the 2125/2295 Hz mark/space midpoint."""


@dataclass(frozen=True)
class Reception:
    """One trial's received audio.

    ``audio`` is what the sound card delivers (int16). ``snr_measured_db`` is the true SNR measured from this
    trial's actual samples: the scaled signal's power over the realized antenna-noise power during the
    transmission, referred to 2500 Hz. ``clipped`` counts samples that hit the int16 limits (should be 0).
    """

    audio: npt.NDArray[np.int16]
    snr_measured_db: float
    clipped: int


def antenna_noise_sigma(rig: RigFilter) -> float:
    """Return the antenna white-noise standard deviation that puts the filtered noise at ``NOISE_METER_DB``."""
    target_rms = 10.0 ** (NOISE_METER_DB / 20.0)
    return target_rms * float(np.sqrt(NYQUIST_HZ / rig.enbw_hz))


def signal_power(snr_db: float, noise_sigma: float) -> float:
    """Return the signal power (counts^2) that gives ``snr_db`` against white noise of ``noise_sigma``."""
    noise_density = noise_sigma**2 / NYQUIST_HZ
    return noise_density * REFERENCE_BANDWIDTH_HZ * 10.0 ** (snr_db / 10.0)


def meter_db(audio: npt.ArrayLike) -> float:
    """Return the reading of WSJT-X's input level meter for int16 audio: 10*log10(mean square)."""
    samples = np.asarray(audio, dtype=np.float64)
    return float(10.0 * np.log10(np.mean(samples**2)))


@dataclass(frozen=True)
class FadingModel:
    """An ITU-R F.1487 / CCIR Watterson HF channel: two equal Rayleigh-fading paths.

    ``spread_hz`` is the Doppler spread of each path's fading, ``delay_ms`` the second path's extra delay.
    """

    name: str
    spread_hz: float
    delay_ms: float
    description: str


FADING_MODELS: dict[str, FadingModel] = {
    model.name: model
    for model in (
        FadingModel("LQ", 0.5, 0.5, "Low latitude, quiet"),
        FadingModel("LM", 1.5, 2.0, "Low latitude, moderate"),
        FadingModel("LD", 10.0, 6.0, "Low latitude, disturbed (post-sunset equatorial spread-F)"),
        FadingModel("MQ", 0.1, 0.5, "Mid latitude, quiet (CCIR 'good')"),
        FadingModel("MM", 0.5, 1.0, "Mid latitude, moderate (CCIR 'moderate')"),
        FadingModel("MD", 1.0, 2.0, "Mid latitude, disturbed (CCIR 'poor')"),
        FadingModel("HQ", 0.5, 1.0, "High latitude, quiet"),
        FadingModel("HM", 10.0, 3.0, "High latitude, moderate (auroral flutter)"),
        FadingModel("HD", 30.0, 7.0, "High latitude, disturbed (strong auroral flutter)"),
    )
}
"""The ITU-R F.1487 channel parameter sets, as WSJT-X's own simulators use them (lib/jtty/sjtty.f90)."""


def watterson(analytic: npt.ArrayLike, signal_samples: int, model: FadingModel, rng: np.random.Generator):
    """Apply Watterson fading to a complex analytic signal; a port of WSJT-X's lib/ft8/watterson.f90.

    Two paths, each multiplied by a complex Gaussian process whose spectrum is shaped by exp(-(f/spread)^2); the
    second path is delayed by ``delay_ms``. As in WSJT-X, the result is renormalized to unit mean power over the
    ``signal_samples`` the signal occupies, so the SNR stays the average over the transmission.
    """
    c = np.asarray(analytic, dtype=np.complex128)
    npts = len(c)
    nonzero = 0
    cs1 = cs2 = np.ones(npts, dtype=np.complex128)
    if model.spread_hz > 0.0:
        freqs = np.fft.fftfreq(npts, d=1.0 / SAMPLE_RATE)
        x = (freqs / model.spread_hz) ** 2
        shape = np.where(x <= 50.0, np.exp(-np.minimum(x, 50.0)), 0.0)
        nonzero = int(np.count_nonzero((shape > 0.0) & (np.abs(freqs) < 10.0)))
        paths = []
        for _ in range(2):
            white = 0.707 * (rng.normal(size=npts) + 1j * rng.normal(size=npts))
            paths.append(np.fft.ifft(np.fft.fft(white) * shape))
        cs1, cs2 = paths
    shift = round(0.001 * model.delay_ms * SAMPLE_RATE)
    delayed = np.roll(c, shift) if model.delay_ms > 0.0 else np.zeros(npts, dtype=np.complex128)
    # With no usable Doppler shaping (spread too small for the record length) both paths stay static, as in WSJT-X.
    faded = 0.5 * (cs1 * c + cs2 * delayed) if nonzero > 1 else 0.5 * (c + delayed)
    power = float(np.sum(np.abs(faded) ** 2)) / max(signal_samples, 1)
    return faded / np.sqrt(power) if power > 0.0 else faded


def occupied_bandwidth(samples: npt.ArrayLike, fraction: float = 0.99) -> tuple[float, float, float]:
    """Return (low, high, width) in Hz of the band holding ``fraction`` of a signal's power (equal tails cut)."""
    data = np.asarray(samples, dtype=np.float64)
    freqs, psd = signal.welch(data, fs=SAMPLE_RATE, nperseg=min(len(data), 1 << 15))
    cumulative = np.cumsum(psd) / np.sum(psd)
    tail = (1.0 - fraction) / 2.0
    low = float(freqs[np.searchsorted(cumulative, tail)])
    high = float(freqs[min(np.searchsorted(cumulative, 1.0 - tail), len(freqs) - 1)])
    return low, high, high - low


def receive(
    clean: npt.ArrayLike,
    start: int,
    total: int,
    snr_db: float,
    rng: np.random.Generator,
    rig: RigFilter,
    fading: FadingModel | None = None,
) -> Reception:
    """Put a clean transmission on the air and return what the station's sound card delivers.

    The clean waveform (any amplitude) begins at sample ``start`` of a ``total``-sample reception and is scaled to
    ``snr_db``. With ``fading``, it first passes through that Watterson channel (its power over the transmission is
    kept, so ``snr_db`` is the transmission's average). Raises ValueError if the transmission does not fit or is
    silent.
    """
    signal_in = np.asarray(clean, dtype=np.float64)
    if start < 0 or start + len(signal_in) > total:
        raise ValueError(f"transmission of {len(signal_in)} samples at {start} does not fit in {total}")
    clean_power = float(np.mean(signal_in**2))
    if not clean_power > 0.0:
        raise ValueError("clean transmission is silent")

    sigma = antenna_noise_sigma(rig)
    target = signal_power(snr_db, sigma)
    if fading is None:
        scaled = signal_in * np.sqrt(target / clean_power)
        noise = rng.normal(0.0, sigma, total + PREROLL_SAMPLES)
        span = slice(PREROLL_SAMPLES + start, PREROLL_SAMPLES + start + len(scaled))
        antenna = noise.copy()
        antenna[span] += scaled
        on_air = scaled
    else:
        placed = np.zeros(total)
        placed[start : start + len(signal_in)] = signal_in
        faded = np.real(watterson(signal.hilbert(placed), len(signal_in), fading, rng))
        window = faded[start : start + len(signal_in)]
        gain = np.sqrt(target / max(float(np.mean(window**2)), 1e-30))
        noise = rng.normal(0.0, sigma, total + PREROLL_SAMPLES)
        span = slice(PREROLL_SAMPLES + start, PREROLL_SAMPLES + start + len(signal_in))
        antenna = noise.copy()
        antenna[PREROLL_SAMPLES:] += gain * faded
        on_air = gain * window

    filtered = signal.sosfilt(rig.sos, antenna)[PREROLL_SAMPLES:]
    rounded = np.rint(filtered)
    clipped = int(np.count_nonzero((rounded > 32767) | (rounded < -32768)))
    audio = np.clip(rounded, -32768, 32767).astype(np.int16)

    realized_density = float(np.mean(noise[span] ** 2)) / NYQUIST_HZ
    measured = float(np.mean(on_air**2)) / (realized_density * REFERENCE_BANDWIDTH_HZ)
    return Reception(audio=audio, snr_measured_db=10.0 * float(np.log10(measured)), clipped=clipped)


def noise_only(total: int, rng: np.random.Generator, rig: RigFilter) -> npt.NDArray[np.int16]:
    """Return ``total`` samples of band noise alone, through the same rig filter and sound-card level."""
    noise = rng.normal(0.0, antenna_noise_sigma(rig), total + PREROLL_SAMPLES)
    filtered = signal.sosfilt(rig.sos, noise)[PREROLL_SAMPLES:]
    return np.clip(np.rint(filtered), -32768, 32767).astype(np.int16)
