"""Amateur PSK31 baseline: typed wrapper around the C modulator/demodulator in ``_psk31``.

The station settings are the usual amateur ones: BPSK31 at 31.25 baud with G3PLX Varicode and raised-cosine phase
reversals, on an audio carrier of 1000 Hz (the fldigi default) through the rig's SSB filter, as an operator watching
the waterfall runs it. A transmission is a preamble of phase reversals, a space, the message and a space, then a
postamble of steady carrier -- what fldigi sends for a macro. The case of the text is kept: Varicode gives lower-case
letters shorter codes, so the caller chooses what an operator would type.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from jtty_snr_study import _psk31
from jtty_snr_study.channel import SAMPLE_RATE

BAUD: float = 31.25
"""PSK31 symbol rate (bits per second)."""

CARRIER_HZ: float = 1000.0
"""Nominal audio carrier frequency (the fldigi default)."""

PREAMBLE_BITS: int = 32
"""Phase reversals sent before the text (fldigi's preamble)."""

POSTAMBLE_BITS: int = 32
"""Bits of steady carrier sent after the text (fldigi's postamble)."""

SAMPLES_PER_SYMBOL: float = SAMPLE_RATE / BAUD
"""Symbol length in samples at the study's sample rate."""

SCORING_MARGIN: float = 4 * SAMPLES_PER_SYMBOL
"""How far outside the message window a printed character may begin and still be scored: about half a lower-case
character, as RTTY scoring allows half a character for timing."""

PSK31_CHARACTERS: frozenset[str] = frozenset(chr(code) for code in range(32, 127)) | frozenset("\r\n")
"""Characters the study sends: printable ASCII plus CR and LF (Varicode covers all of ASCII)."""


@dataclass(frozen=True)
class Psk31Transmission:
    """A clean PSK31 transmission of one message.

    ``samples`` is float32 audio of peak amplitude 1 at ``SAMPLE_RATE``. ``message_start`` is the sample where the
    first bit of the space before the message begins, and ``message_end`` the sample where the "00" after the space
    following it ends, so ``[message_start, message_end)`` is the span an operator watches for the message.
    ``bits`` counts every bit sent (preamble, Varicode, separators and postamble).
    """

    samples: npt.NDArray[np.float32]
    text: str
    message_start: int
    message_end: int
    bits: int


@dataclass(frozen=True)
class Psk31Reception:
    """What the reference demodulator printed.

    ``positions[i]`` is the sample where the first bit of ``text[i]`` began. ``snr_db`` is the decoder's SNR
    estimate (2500 Hz reference) over the symbols inside the requested window, or None if it had none.
    """

    text: str
    positions: npt.NDArray[np.int64]
    snr_db: float | None
    snr_chars: int


def encode(text: str) -> bytes:
    """Return the bits (0/1 bytes) PSK31 sends for ``text``: each character's Varicode followed by ``00``."""
    return _psk31.encode(text)


def char_bits(char: str) -> int:
    """Return the bits one character takes on the air, its Varicode plus the two-bit separator."""
    return len(_psk31.encode(char))


def modulate(text: str, tuning_error_hz: float = 0.0, frame_with_spaces: bool = True) -> Psk31Transmission:
    """Generate the PSK31 transmission of ``text`` (case kept).

    The message is sent between single spaces unless ``frame_with_spaces`` is false (a contest macro carries its own
    CR/LF framing). ``tuning_error_hz`` moves the carrier, modeling the operator's click on the waterfall landing
    slightly off the signal. Raises ValueError if the text has a character outside ``PSK31_CHARACTERS``, or if nothing
    would be sent (empty text without framing).
    """
    bad = sorted(set(text) - PSK31_CHARACTERS)
    if bad:
        raise ValueError(f"characters the study does not send in PSK31: {bad!r}")

    framed = f" {text} " if frame_with_spaces else text
    if not framed:
        raise ValueError("nothing to send")
    audio, starts, bits = _psk31.modulate(
        framed, SAMPLE_RATE, CARRIER_HZ + tuning_error_hz, BAUD, PREAMBLE_BITS, POSTAMBLE_BITS
    )
    samples = np.frombuffer(audio, dtype=np.float32).copy()
    return Psk31Transmission(
        samples=samples,
        text=text,
        message_start=int(starts[0]),
        message_end=round(starts[-1] + char_bits(framed[-1]) * SAMPLES_PER_SYMBOL),
        bits=bits,
    )


def demodulate(audio: npt.ArrayLike, snr_window: tuple[int, int] | None = None) -> Psk31Reception:
    """Decode received audio (int16 or float) tuned to the nominal carrier.

    The SNR estimate uses only symbols in ``snr_window`` (sample indices, end exclusive); with no window it uses every
    symbol.
    """
    samples = np.ascontiguousarray(audio)
    if samples.dtype not in (np.int16, np.float32, np.float64):
        samples = samples.astype(np.float64)
    start, end = snr_window if snr_window is not None else (0, len(samples))
    text, positions, snr_db, snr_chars = _psk31.demodulate(samples, SAMPLE_RATE, CARRIER_HZ, BAUD, start, end)
    return Psk31Reception(
        text=text,
        positions=np.asarray(positions, dtype=np.int64),
        snr_db=snr_db,
        snr_chars=snr_chars,
    )


def slice_bits(audio: npt.ArrayLike, first_bit_start: float, nbits: int, tuning_error_hz: float = 0.0) -> bytes:
    """Decide ``nbits`` bits at known timing and frequency with the demodulator's filters (for BER checks)."""
    samples = np.ascontiguousarray(audio, dtype=np.float64)
    return _psk31.slice_bits(samples, SAMPLE_RATE, CARRIER_HZ + tuning_error_hz, BAUD, first_bit_start, nbits)
