"""Reference parameters of the five modes, for the report's mode table and the bit and airtime arithmetic.

Sources: FT8 and FT4 from the WSJT-X user guide (doc/user_guide/en/protocols.adoc); JTTY from
lib/jtty/jtty_design.md, lib/jtty/sjtty.f90 and the protocol summary table in protocols.adoc; RTTY from the
amateur standard (45.45 baud, 170 Hz shift, US-TTY Baudot with 1.5 stop bits); PSK31 from G3PLX's BPSK31 definition
(31.25 baud, Varicode). The measured 99% occupied bandwidth of each mode is added at run time from its actual rendered
signal.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from jtty_snr_study import messages, psk31


def mean_text_char_bits() -> float:
    """Return the mean bits PSK31 spends per character (Varicode plus the ``00`` gap) on the study's lower-case
    ragchew text, from a fixed sample of 1000 sentences."""
    rng = np.random.default_rng(0)
    text = "".join(messages.text_message(rng).lower() for _ in range(1000))
    return len(psk31.encode(text)) / len(text)


PSK31_CHAR_BITS: float = mean_text_char_bits()
"""Mean PSK31 bits per character of the study's lower-case text (about 6.7; capitals average about 8.5)."""


@dataclass(frozen=True)
class ModeInfo:
    """Nominal protocol parameters of one mode.

    ``frame_s`` is one JTTY frame, one FT8/FT4 transmission, one RTTY character, or one average PSK31 character.
    ``slot_s`` is the T/R period for the slotted modes (None for the asynchronous ones); ``tone_spacing_hz`` is None
    for a single-carrier mode. ``gaussian_bt`` is the Gaussian frequency-smoothing bandwidth-time product of the GFSK
    modes (smaller means more smoothing), None for a mode without it. Bits are per frame/transmission/character.
    """

    label: str
    modulation: str
    gaussian_bt: float | None
    tones: int
    tone_spacing_hz: float | None
    baud: float
    bits_per_symbol: float
    symbols: str
    frame_s: float
    slot_s: float | None
    info_bits: float
    crc_bits: int
    fec: str
    channel_bits: float
    nominal_bandwidth_hz: float
    synchronization: str
    notes: str

    @property
    def gross_bit_rate(self) -> float:
        """Channel bits per second while transmitting."""
        return self.channel_bits / self.frame_s

    @property
    def net_bit_rate(self) -> float:
        """Information bits per second while transmitting."""
        return self.info_bits / self.frame_s


MODES: dict[str, ModeInfo] = {
    "jtty": ModeInfo(
        label="JTTY",
        modulation="4-GFSK",
        gaussian_bt=2.0,
        tones=4,
        tone_spacing_hz=31.25,
        baud=31.25,
        bits_per_symbol=2,
        symbols="59 per frame (13 sync + 46 data)",
        frame_s=59 * 384 / 12000,
        slot_s=None,
        info_bits=34,
        crc_bits=12,
        fec="Tail-biting convolutional, K=10, r=1/2 (92,46)",
        channel_bits=59 * 2,
        nominal_bandwidth_hz=127.0,
        synchronization="Asynchronous; 13-symbol sync per frame",
        notes="One 34-bit source atom per frame: a compressed call, typed exchange, or 5 text characters",
    ),
    "ft8": ModeInfo(
        label="FT8",
        modulation="8-GFSK",
        gaussian_bt=2.0,
        tones=8,
        tone_spacing_hz=6.25,
        baud=6.25,
        bits_per_symbol=3,
        symbols="79 (58 data + 21 sync: three 7x7 Costas arrays)",
        frame_s=79 * 1920 / 12000,
        slot_s=15.0,
        info_bits=77,
        crc_bits=14,
        fec="LDPC (174,91)",
        channel_bits=79 * 3,
        nominal_bandwidth_hz=50.0,
        synchronization="Timed 15 s T/R periods (UTC); Costas sync",
        notes="One 77-bit message per transmission",
    ),
    "ft4": ModeInfo(
        label="FT4",
        modulation="4-GFSK",
        gaussian_bt=1.0,
        tones=4,
        tone_spacing_hz=12000 / 576,
        baud=12000 / 576,
        bits_per_symbol=2,
        symbols="105 (87 data + 16 sync: four 4x4 Costas arrays + 2 ramp)",
        frame_s=105 * 576 / 12000,
        slot_s=7.5,
        info_bits=77,
        crc_bits=14,
        fec="LDPC (174,91)",
        channel_bits=105 * 2,
        nominal_bandwidth_hz=83.3,
        synchronization="Timed 7.5 s T/R periods (UTC); Costas sync",
        notes="One 77-bit message per transmission",
    ),
    "rtty": ModeInfo(
        label="RTTY",
        modulation="2-FSK (continuous phase)",
        gaussian_bt=None,
        tones=2,
        tone_spacing_hz=170.0,
        baud=45.45,
        bits_per_symbol=1,
        symbols="7.5 per character (1 start + 5 data + 1.5 stop)",
        frame_s=7.5 / 45.45,
        slot_s=None,
        info_bits=5,
        crc_bits=0,
        fec="None",
        channel_bits=7.5,
        nominal_bandwidth_hz=250.0,
        synchronization="Asynchronous; start/stop bits per character",
        notes="US-TTY Baudot (ITA2) with LTRS/FIGS shifts and unshift-on-space",
    ),
    "psk31": ModeInfo(
        label="PSK31",
        modulation="Differential BPSK, raised-cosine reversals",
        gaussian_bt=None,
        tones=1,
        tone_spacing_hz=None,
        baud=psk31.BAUD,
        bits_per_symbol=1,
        symbols=f"Varicode: 1-10 bits + 2-bit gap per character ({PSK31_CHAR_BITS:.1f} on average for lower-case text)",
        frame_s=PSK31_CHAR_BITS / psk31.BAUD,
        slot_s=None,
        info_bits=5,
        crc_bits=0,
        fec="None",
        channel_bits=PSK31_CHAR_BITS,
        nominal_bandwidth_hz=60.0,
        synchronization="Asynchronous; carrier and symbol timing tracked continuously",
        notes="G3PLX Varicode; lower case codes are shorter: ragchew is sent in lower case, contest overs in capitals",
    ),
}
"""Nominal parameters per mode, in the study's fixed mode order."""
