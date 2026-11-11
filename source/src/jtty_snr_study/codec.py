"""Client for the WSJT-X tree's ``wsjt-codec`` streaming mode: renders and decodes JTTY, FT8 and FT4.

Protocol (lib/streaming_io.f90, lib/streaming_control.f90, lib/streaming_jtty.f90): after an 8-byte header, stdin
carries frames ``[type u8][length u32 LE][body]`` -- 0x01 int16 LE mono audio at 12 kHz, 0x02 compact JSON
control. stdout is NDJSON. Requests are answered in order, so a ``pack`` request after a trial's audio and
``flush`` acts as a barrier: everything the trial produced arrives before its ``packed`` reply.

JTTY is decoded through the GUI's own receive path (jtty_rx_*); ``flush`` ends the JTTY session and runs the
final-window search, so unfinished messages are reported as ``ended``. FT8 and FT4 decode a whole period as soon
as its last sample arrives. Decoder settings mirror an operator's: JTTY at Rx 1500 Hz with F Tol 20 Hz; FT8/FT4 at
Deep depth with a-priori (AP) decoding off and no callsigns configured.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import shutil
import struct
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

SESSION_HEADER: bytes = b"WSJT" + bytes([0, 1]) + struct.pack("<H", 12)
"""Stream header: magic, sample format 0 (int16), 1 channel, 12 kHz."""

FRAME_AUDIO: int = 0x01
"""Frame type of an audio chunk."""

FRAME_CONTROL: int = 0x02
"""Frame type of a JSON control message."""

WISDOM_FILE: str = "jt9_wisdom.dat"
"""FFTW wisdom file wsjt-codec reads from and saves to its ``-a`` directory."""

JTTY_RX_HZ: float = 1500.0
"""JTTY receive frequency (the GUI's default on entering JTTY)."""

JTTY_FTOL_HZ: int = 20
"""JTTY frequency tolerance (the GUI's default, widgets/mainwindow_settings.cpp)."""

DECODE_LOW_HZ: int = 300
"""Lower edge of the decoders' search range: the rig's passband edge, as an operator sets WSJT-X's Wide Graph.

A range reaching into the filter skirts, where the noise floor falls away, skews the FT8/FT4 noise-baseline fit:
FT4 then misses strong signals near the band edge and both modes over-report SNR by up to ~20 dB."""

DECODE_HIGH_HZ: int = 2700
"""Upper edge of the decoders' search range (the rig's passband edge; see DECODE_LOW_HZ)."""

PERIOD_SAMPLES: dict[str, int] = {"FT8": 180_000, "FT4": 90_000}
"""Samples in one T/R period at 12 kHz."""

CONFIGURE: dict[str, dict[str, Any]] = {
    "JTTY": {
        "t": "configure",
        "mode": "JTTY",
        "rxfreq": int(JTTY_RX_HZ),
        "ntol": JTTY_FTOL_HZ,
        "nfa": DECODE_LOW_HZ,
        "nfb": DECODE_HIGH_HZ,
    },
    **{
        mode: {
            "t": "configure",
            "mode": mode,
            "depth": 3,
            "ft8_ap_on": False,
            "mycall": "",
            "his_call": "",
            "his_grid": "",
            "nfa": DECODE_LOW_HZ,
            "nfb": DECODE_HIGH_HZ,
        }
        for mode in ("FT8", "FT4")
    },
}
"""configure frame per mode. Every key is sent explicitly: wsjt-codec silently ignores unknown keys and keeps
FT8 AP on with K1ABC/W9XYZ/EN37 unless told otherwise."""


class CodecError(RuntimeError):
    """wsjt-codec reported an error, exited, timed out, or answered unexpectedly."""


@dataclass(frozen=True)
class JttyRender:
    """A clean JTTY transmission: the text the receiver should print, its per-frame pieces, and full-scale audio."""

    canonical: str
    frames: tuple[str, ...]
    samples: npt.NDArray[np.int16]
    substituted: bool


@dataclass(frozen=True)
class SlotRender:
    """A clean FT8/FT4 transmission: the message as encoded and full-scale audio (no lead-in)."""

    mode: str
    msgsent: str
    samples: npt.NDArray[np.int16]


@dataclass(frozen=True)
class JttyMessage:
    """The final state of one decoded JTTY message.

    ``state`` is growing, complete (end-of-message frame decoded), expired or ended (reception ended first).
    ``gaps`` are offsets in ``text`` of each ``...`` marker standing for missed frames. ``start_s`` is the time of
    its first frame from the start of the trial's audio; ``snr`` is the decoder's report for the first frame.
    """

    message_id: int
    state: str
    text: str
    gaps: tuple[int, ...]
    snr: int
    freq: float
    start_s: float


@dataclass(frozen=True)
class SlotDecode:
    """One FT8/FT4 decode line. ``ap`` is 0 for a decode that used no a-priori information."""

    message: str
    snr: int
    dt: float
    freq: float
    ap: int
    decode_pass: int


def encode_frame(frame_type: int, body: bytes) -> bytes:
    """Return one stream frame: type byte, little-endian 32-bit length, body."""
    return bytes([frame_type]) + struct.pack("<I", len(body)) + body


def encode_control(message: dict[str, Any]) -> bytes:
    """Return a control frame carrying compact JSON (the parser tolerates no other whitespace)."""
    return encode_frame(FRAME_CONTROL, json.dumps(message, separators=(",", ":")).encode())


def encode_audio(samples: npt.ArrayLike) -> bytes:
    """Return an audio frame of int16 little-endian samples."""
    return encode_frame(FRAME_AUDIO, np.asarray(samples, dtype="<i2").tobytes())


class CodecClient:
    """One long-lived wsjt-codec process, used for many trials in sequence.

    The process runs in ``workdir`` (also its wisdom and temp directory) with one OpenMP thread. If ``wisdom`` is
    given it is copied in first, so FFTW does not re-plan. All methods raise CodecError on protocol failure; the
    caller should then close this client and start a new one.
    """

    def __init__(self, binary: Path, workdir: Path, wisdom: Path | None = None, timeout_s: float = 120.0) -> None:
        self._timeout = timeout_s
        self._mode: str | None = None
        self._next_id = 1
        self._lines: queue.Queue[dict[str, Any] | None] = queue.Queue()
        workdir.mkdir(parents=True, exist_ok=True)
        (workdir / "rx_messages.txt").unlink(missing_ok=True)
        if wisdom is not None and wisdom.exists():
            shutil.copyfile(wisdom, workdir / WISDOM_FILE)
        self.workdir = workdir

        env = {**os.environ, "OMP_NUM_THREADS": "1"}
        try:
            self._process = subprocess.Popen(
                [str(binary), "-a", str(workdir), "-t", str(workdir)],
                cwd=workdir,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as error:
            raise CodecError(f"cannot start {binary}: {error}") from error
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        try:
            self._write(SESSION_HEADER)
            ready = self._next_line()
            if ready.get("t") != "ready" or "JTTY" not in ready.get("modes", []):
                raise CodecError(f"unexpected handshake: {ready}")
        except CodecError:
            self._process.kill()
            self._process.wait()
            self._reader.join(timeout=5)
            for pipe in (self._process.stdin, self._process.stdout):
                if pipe is not None:
                    pipe.close()
            raise

    def __enter__(self) -> CodecClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """Ask the codec to halt (which saves FFTW wisdom), then make sure the process is gone."""
        if self._process.poll() is None:
            try:
                self._write(encode_control({"t": "halt"}))
                if self._process.stdin is not None:
                    self._process.stdin.close()
                self._process.wait(timeout=30)
            except (CodecError, OSError, subprocess.TimeoutExpired):
                self._process.kill()
                self._process.wait()

    def render_jtty(self, message: str | dict[str, Any], freq_hz: float) -> JttyRender:
        """Render a JTTY message with its lowest tone at ``freq_hz``.

        ``message`` is plain text, or wsjt-codec render fields such as a native function-key ``template`` with its
        ``profile``, ``his_call``, ``exchange`` and ``serial`` (lib/streaming_jtty.f90). Raises CodecError if the
        message needs more than one segment.
        """
        request_id = self._new_id()
        fields = {"text": message} if isinstance(message, str) else dict(message)
        self._write(
            encode_control({"t": "render", "id": request_id, **fields, "freq": round(freq_hz, 3), "rate": 12000})
        )
        lines = self._read_until(lambda line: line.get("t") == "rendered" and line.get("id") == request_id)
        segments = [line for line in lines if line.get("t") == "segment" and line.get("id") == request_id]
        if len(segments) != 1:
            raise CodecError(f"JTTY render of {fields!r} produced {len(segments)} segments")
        segment = segments[0]
        return JttyRender(
            canonical=str(segment["canonical"]),
            frames=tuple(str(frame["text"]) for frame in segment["frames"]),
            samples=_collect_pcm(lines, request_id),
            substituted=bool(segment.get("substituted", False)),
        )

    def render_slot(self, mode: str, text: str, freq_hz: float) -> SlotRender:
        """Render an FT8 or FT4 message with its lowest tone at ``freq_hz``."""
        request_id = self._new_id()
        self._write(
            encode_control(
                {"t": "render", "id": request_id, "mode": mode, "text": text, "freq": round(freq_hz, 3), "rate": 12000}
            )
        )
        lines = self._read_until(lambda line: line.get("t") == "rendered" and line.get("id") == request_id)
        encoded = [line for line in lines if line.get("t") == "encoded" and line.get("id") == request_id]
        if len(encoded) != 1:
            raise CodecError(f"{mode} render of {text!r} gave no encoded line")
        return SlotRender(mode=mode, msgsent=str(encoded[0]["msgsent"]), samples=_collect_pcm(lines, request_id))

    def decode_jtty(self, audio: npt.ArrayLike) -> list[JttyMessage]:
        """Decode one JTTY reception (all of ``audio``) and return each message's final state, by start time."""
        self._configure("JTTY")
        lines = self._exchange(audio)
        origin_s = 0.0
        latest: dict[int, dict[str, Any]] = {}
        for line in lines:
            if line.get("t") == "session":
                origin_s = float(line.get("origin", 0)) / 12000.0
            elif line.get("t") == "jtty_update":
                latest[int(line["id"])] = line
        messages = [
            JttyMessage(
                message_id=message_id,
                state=str(line["state"]),
                text=str(line["text"]),
                gaps=tuple(int(gap) for gap in line.get("gaps", [])),
                snr=int(line["snr"]),
                freq=float(line["freq"]),
                start_s=float(line["start"]) - origin_s,
            )
            for message_id, line in latest.items()
        ]
        return sorted(messages, key=lambda message: message.start_s)

    def decode_slot(self, mode: str, audio: npt.ArrayLike) -> list[SlotDecode]:
        """Decode one FT8/FT4 period (``audio`` must be exactly one period long)."""
        samples = np.asarray(audio)
        if len(samples) != PERIOD_SAMPLES[mode]:
            raise ValueError(f"{mode} audio must be {PERIOD_SAMPLES[mode]} samples, not {len(samples)}")
        self._configure(mode)
        lines = self._exchange(samples)
        return [
            SlotDecode(
                message=str(line["message"]),
                snr=int(line["snr"]),
                dt=float(line["dt"]),
                freq=float(line["freq"]),
                ap=int(line.get("ap", 0)),
                decode_pass=int(line.get("pass", 0)),
            )
            for line in lines
            if line.get("t") == "decode"
        ]

    def _configure(self, mode: str) -> None:
        """Switch the codec to ``mode`` (with this study's decoder settings) unless it is already there."""
        if self._mode != mode:
            self._write(encode_control(CONFIGURE[mode]))
            self._mode = mode

    def _exchange(self, audio: npt.ArrayLike) -> list[dict[str, Any]]:
        """Send one trial's audio, end it with flush, and return every line up to the barrier's reply."""
        barrier = self._new_id()
        self._write(
            encode_audio(audio)
            + encode_control({"t": "flush"})
            + encode_control({"t": "pack", "id": barrier, "text": "E"})
        )
        return self._read_until(lambda line: line.get("t") == "packed" and line.get("id") == barrier)

    def _new_id(self) -> int:
        """Return a request id not used before by this client."""
        request_id = self._next_id
        self._next_id += 1
        return request_id

    def _write(self, data: bytes) -> None:
        """Write bytes to the codec's stdin. Raises CodecError if the process has gone away."""
        stdin = self._process.stdin
        if stdin is None:
            raise CodecError("codec stdin is closed")
        try:
            stdin.write(data)
            stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as error:
            raise CodecError(f"codec exited (status {self._process.poll()})") from error

    def _read_stdout(self) -> None:
        """Reader thread: parse every NDJSON line onto the queue; None marks end of output."""
        stdout = self._process.stdout
        if stdout is not None:
            for raw in stdout:
                try:
                    self._lines.put(json.loads(raw))
                except json.JSONDecodeError:
                    self._lines.put({"t": "unparsed", "raw": raw.decode(errors="replace")})
        self._lines.put(None)

    def _next_line(self) -> dict[str, Any]:
        """Return the next output line. Raises CodecError on timeout, end of output, or an error line."""
        try:
            line = self._lines.get(timeout=self._timeout)
        except queue.Empty as error:
            raise CodecError(f"no reply from codec within {self._timeout} s") from error
        if line is None:
            raise CodecError(f"codec exited (status {self._process.poll()})")
        if line.get("t") == "error":
            raise CodecError(f"codec error: {line}")
        return line

    def _read_until(self, done: Any) -> list[dict[str, Any]]:
        """Collect lines up to and including the first one for which ``done(line)`` is true."""
        lines: list[dict[str, Any]] = []
        while True:
            line = self._next_line()
            lines.append(line)
            if done(line):
                return lines


def _collect_pcm(lines: list[dict[str, Any]], request_id: int) -> npt.NDArray[np.int16]:
    """Join a render reply's base64 ``pcm`` lines (in ``seq`` order) into int16 samples."""
    chunks = sorted(
        (line for line in lines if line.get("t") == "pcm" and line.get("id") == request_id),
        key=lambda line: (int(line.get("seg", 0)), int(line.get("seq", 0))),
    )
    if not chunks:
        raise CodecError(f"render {request_id} returned no audio")
    data = b"".join(base64.b64decode(chunk["data"]) for chunk in chunks)
    return np.frombuffer(data, dtype="<i2").astype(np.int16)
