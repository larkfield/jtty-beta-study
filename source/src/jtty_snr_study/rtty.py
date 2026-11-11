"""Amateur RTTY baseline: typed wrapper around the C modulator/demodulator in ``_rtty``.

The station settings are the usual amateur contest RTTY ones: 45.45 baud, 170 Hz shift with the standard AFSK
tones (mark 2125 Hz, space 2295 Hz, the MMTTY defaults), 1.5 stop bits, US-TTY Baudot with unshift-on-space.
A transmission starts with a short steady mark and LTRS idle ("diddles"), then a space, the message and a
space, then LTRS idle -- what a contest macro sends.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from jtty_snr_study import _rtty
from jtty_snr_study.channel import SAMPLE_RATE

BAUD: float = 45.45
"""Amateur RTTY symbol rate (bits per second)."""

MARK_HZ: float = 2125.0
"""Nominal audio mark tone (standard amateur AFSK)."""

SPACE_HZ: float = 2295.0
"""Nominal audio space tone (170 Hz shift)."""

STOP_BITS: float = 1.5
"""Stop-bit length in bit periods."""

LEAD_MARK_S: float = 0.1
"""Steady mark sent before the first idle character."""

IDLE_LTRS: int = 2
"""LTRS idle characters sent before the message."""

TAIL_LTRS: int = 2
"""LTRS idle characters sent after the message."""

SAMPLES_PER_BIT: float = SAMPLE_RATE / BAUD
"""Bit length in samples at the study's sample rate."""

SAMPLES_PER_CHAR: float = (1 + 5 + STOP_BITS) * SAMPLES_PER_BIT
"""Character length (start, five data bits, stop) in samples."""

RTTY_CHARACTERS: frozenset[str] = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -?:$!&#'(),./;\"\r\n")
"""Characters the US-TTY Baudot code can send."""


@dataclass(frozen=True)
class RttyTransmission:
    """A clean RTTY transmission of one message.

    ``samples`` is unit-amplitude float32 audio at ``SAMPLE_RATE``. ``message_start`` is the sample where the
    start bit of the space before the message begins, and ``message_end`` the sample where the space after it
    ends, so ``[message_start, message_end)`` is the span an operator watches for the message.
    """

    samples: npt.NDArray[np.float32]
    text: str
    message_start: int
    message_end: int


@dataclass(frozen=True)
class RttyReception:
    """What the reference demodulator printed.

    ``positions[i]`` is the sample where the start bit of ``text[i]`` was detected. ``snr_db`` is the decoder's
    SNR estimate (2500 Hz reference) over the characters inside the requested window, or None if there were none.
    """

    text: str
    positions: npt.NDArray[np.int64]
    snr_db: float | None
    snr_chars: int


def modulate(text: str, tuning_error_hz: float = 0.0, frame_with_spaces: bool = True) -> RttyTransmission:
    """Generate the RTTY transmission of ``text`` (upper-cased).

    The message is sent between single spaces unless ``frame_with_spaces`` is false (a contest macro carries its own
    CR/LF framing). ``tuning_error_hz`` shifts both tones, modeling the transmitting station being slightly off the
    frequency the receiving operator tuned to. Raises ValueError if the text has a character Baudot cannot send.
    """
    message = text.upper()
    bad = sorted(set(message) - RTTY_CHARACTERS)
    if bad:
        raise ValueError(f"characters with no Baudot code: {bad!r}")

    framed = f" {message} " if frame_with_spaces else message
    audio, starts = _rtty.modulate(
        framed,
        SAMPLE_RATE,
        MARK_HZ + tuning_error_hz,
        SPACE_HZ + tuning_error_hz,
        BAUD,
        STOP_BITS,
        LEAD_MARK_S,
        IDLE_LTRS,
        TAIL_LTRS,
    )
    samples = np.frombuffer(audio, dtype=np.float32).copy()
    return RttyTransmission(
        samples=samples,
        text=message,
        message_start=int(starts[0]),
        message_end=round(starts[-1] + SAMPLES_PER_CHAR),
    )


def demodulate(audio: npt.ArrayLike, snr_window: tuple[int, int] | None = None) -> RttyReception:
    """Decode received audio (int16 or float) tuned to the nominal mark and space tones.

    The SNR estimate uses only characters whose start bit lies in ``snr_window`` (sample indices, end exclusive);
    with no window it uses every printed character.
    """
    samples = np.ascontiguousarray(audio)
    if samples.dtype not in (np.int16, np.float32, np.float64):
        samples = samples.astype(np.float64)
    start, end = snr_window if snr_window is not None else (0, len(samples))
    text, positions, snr_db, snr_chars = _rtty.demodulate(
        samples, SAMPLE_RATE, MARK_HZ, SPACE_HZ, BAUD, STOP_BITS, start, end
    )
    return RttyReception(
        text=text,
        positions=np.asarray(positions, dtype=np.int64),
        snr_db=snr_db,
        snr_chars=snr_chars,
    )


def slice_bits(audio: npt.ArrayLike, first_bit_start: float, nbits: int, tuning_error_hz: float = 0.0) -> bytes:
    """Decide ``nbits`` bits at known timing with the demodulator's matched filters (for BER checks)."""
    samples = np.ascontiguousarray(audio, dtype=np.float64)
    return _rtty.slice_bits(
        samples, SAMPLE_RATE, MARK_HZ + tuning_error_hz, SPACE_HZ + tuning_error_hz, BAUD, first_bit_start, nbits
    )
