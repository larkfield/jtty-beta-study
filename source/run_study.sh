#!/usr/bin/env bash
# Run the JTTY SNR study end to end: build wsjt-codec if needed, sweep, analyze, and write the report.
#
# Usage: ./run_study.sh [--preset smoke|quick|standard|publication] [--trials N] [--modes ft8,ft4,jtty,psk31,rtty]
#                       [--channel AWGN|MQ|MM|MD|HM|...] [--noise-hours H] [--turnaround S]
#                       [--run-name NAME] [--workers N] [--seed S] [--snr-min A --snr-max B --snr-step C]
#                       [--level P]   (headline 5-character decode %, default 80)
# Re-running with the same --run-name resumes an interrupted run or tops it up to a larger --trials.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
tree="$(cd "$here/../.." && pwd)"
codec="${WSJT_CODEC:-$tree/build/wsjt-codec}"

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required: https://docs.astral.sh/uv/" >&2
    exit 1
fi

if [[ -z "${WSJT_CODEC:-}" ]]; then
    if [[ ! -f "$tree/build/CMakeCache.txt" ]]; then
        echo "No configured WSJT-X build at $tree/build; configure it with CMake first, or set WSJT_CODEC." >&2
        exit 1
    fi
    # Always build: it's a quick no-op when nothing changed, and picks up decoder source changes when something did.
    echo "Building wsjt-codec (incremental)..." >&2
    cmake --build "$tree/build" --target wsjt-codec -j"$(nproc)" >/dev/null
elif [[ ! -x "$codec" ]]; then
    echo "WSJT_CODEC=$codec is not executable." >&2
    exit 1
fi

cd "$here"
exec uv run --quiet python -m jtty_snr_study all --codec "$codec" "$@"
