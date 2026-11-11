"""Type stub for the C RTTY module (see _rtty.c for the full documentation)."""

from collections.abc import Buffer

def modulate(
    text: str,
    fs: float,
    mark_hz: float,
    space_hz: float,
    baud: float,
    stop_bits: float,
    lead_mark_s: float,
    idle_ltrs: int = 0,
    tail_ltrs: int = 0,
    /,
) -> tuple[bytes, list[int]]: ...
def demodulate(
    audio: Buffer,
    fs: float,
    mark_hz: float,
    space_hz: float,
    baud: float,
    stop_bits: float,
    snr_start: int = 0,
    snr_end: int = 0,
    /,
) -> tuple[str, list[int], float | None, int]: ...
def slice_bits(
    audio: Buffer, fs: float, mark_hz: float, space_hz: float, baud: float, first_bit_start: float, nbits: int, /
) -> bytes: ...
