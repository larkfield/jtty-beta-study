"""Command line: ``run`` the sweep, ``analyze`` it into tables, ``report`` figures and HTML, or ``all`` three.

``rescore`` copies a run scored in other units (older runs: frames, messages and groups on the old alignment; or
characters) into a new run directory scored in 5-character groups, which ``analyze`` and ``report`` then read like
any other run.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

from jtty_snr_study import analysis, channel, plots, report, runner
from jtty_snr_study.messages import MessageClass

STUDY_ROOT: Path = Path(__file__).resolve().parents[2]
"""The tool's folder (contrib/jtty_snr_study)."""

DEFAULT_RESULTS: Path = STUDY_ROOT / "results"
"""Where run directories go by default."""

DEFAULT_CODEC: Path = runner.messages.TREE_ROOT / "build" / "wsjt-codec"
"""The WSJT-X tree's wsjt-codec build."""


def _parser() -> argparse.ArgumentParser:
    """Return the argument parser."""
    parser = argparse.ArgumentParser(
        prog="jtty-snr-study",
        description=(
            "Measure JTTY decode rate vs SNR through a simulated HF station, with FT8, FT4, RTTY and PSK31 baselines."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def level(text: str) -> int:
        value = int(text)
        if not 1 <= value <= 99:
            raise argparse.ArgumentTypeError(f"must be a percentage from 1 to 99, not {value}")
        return value

    def add_level_option(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--level",
            type=level,
            default=analysis.HEADLINE_LEVEL,
            help=f"headline 5-character decode percentage for thresholds (default {analysis.HEADLINE_LEVEL})",
        )

    def add_run_options(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--preset",
            choices=sorted(runner.PRESETS),
            default="standard",
            help="run size: smoke (tool check), quick (200 trials/point), standard (1000), publication (5000)",
        )
        command.add_argument("--trials", type=int, help="trials per fine-grid SNR point (overrides the preset)")
        command.add_argument("--pilot-trials", type=int, default=100, help="trials per pilot SNR point (default 100)")
        command.add_argument(
            "--modes",
            default=",".join(runner.MODES),
            help="comma-separated modes to run (default: ft8,ft4,jtty,psk31,rtty)",
        )
        command.add_argument("--run-name", help="run directory name (default: <preset>-<date>-<time>)")
        command.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS, help="where run directories go")
        command.add_argument("--seed", type=int, default=2026, help="base seed; keep it to resume or extend a run")
        command.add_argument(
            "--workers",
            type=int,
            default=max(1, (os.cpu_count() or 2) - 2),
            help="parallel worker processes (default: CPU count - 2)",
        )
        command.add_argument("--codec", type=Path, default=DEFAULT_CODEC, help="path to the wsjt-codec binary")
        command.add_argument("--snr-min", type=float, help="fixed grid start (dB); skips the pilot pass")
        command.add_argument("--snr-max", type=float, help="fixed grid end (dB)")
        command.add_argument("--snr-step", type=float, default=0.5, help="fixed grid step (dB, default 0.5)")
        command.add_argument(
            "--channel",
            default="awgn",
            type=str.upper,
            choices=["AWGN", *channel.FADING_MODELS],
            help="propagation channel: AWGN (default) or an ITU-R F.1487 fading model (see README)",
        )
        command.add_argument(
            "--noise-hours",
            type=float,
            help="hours of pure band noise per mode for false-print rates (default by preset: 0.05/4/24/100)",
        )
        command.add_argument(
            "--turnaround",
            type=float,
            default=0.0,
            help="seconds added per over in the contest QSO timing (default 0: airtime only)",
        )

    add_run_options(commands.add_parser("run", help="run (or resume) the sweep"))
    for name, text in (("analyze", "compute tables from a run"), ("report", "analyze, draw figures and report.html")):
        command = commands.add_parser(name, help=text)
        command.add_argument("run_dir", type=Path, help="run directory")
        add_level_option(command)
    run_all = commands.add_parser("all", help="run, analyze and report")
    add_run_options(run_all)
    add_level_option(run_all)

    rescore = commands.add_parser(
        "rescore", help="copy a run scored in other units into a new directory, scored in 5-character groups"
    )
    rescore.add_argument("run_dir", type=Path, help="the old run directory (only read)")
    rescore.add_argument("target", type=Path, help="the new run directory (must not exist or be empty)")

    replay = commands.add_parser("replay", help="re-run one trial of a run exactly, optionally saving its audio")
    replay.add_argument("run_dir", type=Path, help="run directory")
    replay.add_argument("--mode", required=True, choices=runner.MODES)
    replay.add_argument("--cls", required=True, choices=[str(cls) for cls in MessageClass])
    replay.add_argument("--snr", required=True, type=float, help="the trial's nominal SNR (dB)")
    replay.add_argument("--trial", required=True, type=int, help="the trial index")
    replay.add_argument("--wav", type=Path, help="write the received audio here (12 kHz WAV, opens in WSJT-X)")
    replay.add_argument("--codec", type=Path, default=DEFAULT_CODEC, help="path to the wsjt-codec binary")
    return parser


def _config(args: argparse.Namespace) -> runner.StudyConfig:
    """Return the study configuration described by the run options."""
    preset = runner.PRESETS[args.preset]
    modes = tuple(mode.strip().lower() for mode in args.modes.split(",") if mode.strip())
    unknown = [mode for mode in modes if mode not in runner.MODES]
    if unknown or not modes:
        raise SystemExit(f"unknown modes {unknown}; choose from {', '.join(runner.MODES)}")

    fixed = preset.fixed_snrs
    if args.snr_min is not None or args.snr_max is not None:
        if args.snr_min is None or args.snr_max is None or args.snr_step <= 0 or args.snr_max < args.snr_min:
            raise SystemExit("--snr-min and --snr-max must both be given, with --snr-step > 0 and min <= max")
        count = round((args.snr_max - args.snr_min) / args.snr_step) + 1
        fixed = tuple(round(args.snr_min + i * args.snr_step, 2) for i in range(count))

    suffix = "" if args.channel == "AWGN" else f"-{args.channel}"
    name = args.run_name or f"{args.preset}{suffix}-{datetime.now():%Y%m%d-%H%M%S}"
    return runner.StudyConfig(
        run_dir=(args.results_dir / name).resolve(),
        codec_binary=args.codec.resolve(),
        seed=args.seed,
        modes=modes,
        trials=args.trials if args.trials else preset.trials,
        pilot=preset.pilot and not fixed,
        pilot_trials=min(args.pilot_trials, args.trials if args.trials else preset.trials),
        channel="awgn" if args.channel == "AWGN" else args.channel,
        noise_hours=preset.noise_hours if args.noise_hours is None else max(0.0, args.noise_hours),
        turnaround_s=max(0.0, args.turnaround),
        snr_floors=runner.default_snr_floors(),
        fixed_snrs=fixed,
        workers=max(1, args.workers),
        extra={"preset": args.preset},
    )


def _finish(run_dir: Path, level: int) -> None:
    """Analyze a run at a headline ``level`` (%) and draw its figures and report."""
    analysis.analyze(run_dir, level=level)
    plots.make_figures(run_dir)
    page = report.make_report(run_dir)
    print(f"Tables:  {run_dir / 'tables'}", file=sys.stderr)
    print(f"Figures: {run_dir / 'figures'}", file=sys.stderr)
    print(f"Report:  {page}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Run the command line; returns the process exit status."""
    args = _parser().parse_args(argv)
    if args.command in ("run", "all"):
        config = _config(args)
        runner.run_study(config)
        if args.command == "all":
            _finish(config.run_dir, args.level)
        return 0
    run_dir = args.run_dir.resolve()
    if args.command == "rescore":
        target = args.target.resolve()
        count = runner.rescore_run(run_dir, target)
        print(f"Rescored {count} trials into {target}", file=sys.stderr)
        return 0
    if args.command == "replay":
        if MessageClass(args.cls) not in runner.MODE_CLASSES[args.mode]:
            classes = ", ".join(str(cls) for cls in runner.MODE_CLASSES[args.mode])
            raise SystemExit(f"{args.mode} trials use the classes {classes}, not {args.cls}")
        row = runner.replay_trial(
            run_dir, args.mode, MessageClass(args.cls), args.snr, args.trial, args.codec.resolve(), args.wav
        )
        for name in (
            "mode",
            "cls",
            "contest",
            "msg_kind",
            "snr_db",
            "trial",
            "units",
            "units_ok",
            "frames",
            "copied",
            "failure",
            "grid_phase",
            "sent",
            "printed",
            "messages_json",
            "error",
        ):
            print(f"{name:>14}: {row.get(name, '')}")
        if args.wav is not None:
            print(f"{'audio':>14}: {args.wav}")
        return 0 if not row.get("error") else 1
    if args.command == "analyze":
        analysis.analyze(run_dir, level=args.level)
        print(f"Tables: {run_dir / 'tables'}", file=sys.stderr)
    else:
        _finish(run_dir, args.level)
    return 0
