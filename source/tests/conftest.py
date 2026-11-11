"""Shared fixtures: the built wsjt-codec binary and a client running it."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from jtty_snr_study.cli import DEFAULT_CODEC
from jtty_snr_study.codec import CodecClient


@pytest.fixture(scope="session")
def codec_binary() -> Path:
    """Return the wsjt-codec binary, skipping codec tests when it has not been built."""
    if not DEFAULT_CODEC.exists():
        pytest.skip(f"wsjt-codec not built at {DEFAULT_CODEC}")
    return DEFAULT_CODEC


@pytest.fixture(scope="module")
def codec_client(codec_binary: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[CodecClient]:
    """Return one codec process shared by a test module."""
    client = CodecClient(codec_binary, tmp_path_factory.mktemp("codec"))
    yield client
    client.close()
