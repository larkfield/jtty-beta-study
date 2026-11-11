"""Type stub for the C PSK31 module (see _psk31.c for the full documentation)."""

from collections.abc import Buffer

def encode(text: str, /) -> bytes: ...
def modulate(
    text: str, fs: float, carrier_hz: float, baud: float, preamble_bits: int = 0, postamble_bits: int = 0, /
) -> tuple[bytes, list[int], int]: ...
def demodulate(
    audio: Buffer, fs: float, carrier_hz: float, baud: float, snr_start: int = 0, snr_end: int = 0, /
) -> tuple[str, list[int], float | None, int]: ...
def slice_bits(
    audio: Buffer, fs: float, carrier_hz: float, baud: float, first_bit_start: float, nbits: int, /
) -> bytes: ...
