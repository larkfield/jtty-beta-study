"""Sweep planning, seeding, and the resumable raw-data store."""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from jtty_snr_study import runner
from jtty_snr_study.messages import MessageClass


def test_fine_grid_spans_the_transition_plus_margin() -> None:
    rates = {float(s): (0.0 if s < -22 else 1.0 if s > -17 else 0.5) for s in range(-30, 6)}
    grid = runner.fine_grid(rates, 0.5, 1.5, (-35.0, 10.0))
    assert grid[0] == pytest.approx(-23.5) and grid[-1] == pytest.approx(-15.5)
    assert all(b - a == pytest.approx(0.5) for a, b in itertools.pairwise(grid))


def test_fine_grid_when_the_curve_never_rises() -> None:
    rates = {float(s): 0.0 for s in range(-30, 6)}
    grid = runner.fine_grid(rates, 0.5, 1.5, (-35.0, 10.0))
    assert grid[-1] <= 10.0 and grid[0] >= 3.0


def _counts(rates: dict[float, float], units: int = 100) -> runner.UnitCounts:
    """Return pilot unit counts with the given decode fractions, ``units`` units from as many trials per SNR."""
    return {snr: (round(rate * units), units, units) for snr, rate in rates.items()}


def test_monotone_fit_pools_chance_blips_and_dips() -> None:
    curve = {float(s): 0.0 for s in range(-21, -10)} | {-16.0: 0.01, -10.0: 0.4, -9.0: 0.9}
    curve |= {float(s): (0.97 if s == 15 else 1.0) for s in range(-8, 22)}
    fitted = runner.monotone_rates(_counts(curve))
    assert list(fitted) == sorted(curve)
    assert all(a <= b for a, b in itertools.pairwise(fitted.values()))
    assert fitted[-16.0] == pytest.approx(1 / 600) and fitted[-10.0] == pytest.approx(0.4)
    assert fitted[15.0] == pytest.approx(23.97 / 24) and min(fitted[float(s)] for s in range(-8, 22)) >= 0.99
    # Neither the blip at -16 dB nor the dip at +15 dB stretches the fine grid.
    grid = runner.fine_grid(fitted, 0.5, 3.0, (runner.PILOT_FLOOR_DB, 21.0))
    assert grid[0] == pytest.approx(-13.0) and grid[-1] == pytest.approx(-6.0)
    assert runner.monotone_rates({-3.0: (0, 0, 2), -2.0: (5, 10, 2)}) == {-2.0: 0.5}


def test_default_coarse_sweep_runs_minus_21_to_plus_10() -> None:
    config = runner.StudyConfig(run_dir=Path("unused"), codec_binary=Path("unused"))
    assert (min(config.pilot_snrs), max(config.pilot_snrs)) == (-21.0, 10.0)
    assert runner.MODES == ("ft8", "ft4", "jtty", "psk31", "rtty")
    assert MessageClass.CONTEST_MACRO in runner.MODE_CLASSES["jtty"]


def test_pilot_grid_extends_downward_until_the_curve_is_down_to_1_percent() -> None:
    base = tuple(float(s) for s in range(-21, 22))
    assert runner.pilot_grid(base, {}) == base
    ft8_like = {float(s): (0.52 if s == -21 else 1.0) for s in range(-21, 22)}
    assert runner.pilot_grid(base, _counts(ft8_like))[:2] == (-22.0, -21.0)
    ft8_like |= {-22.0: 0.19, -23.0: 0.02, -24.0: 0.0}
    grid = runner.pilot_grid(base, _counts(ft8_like))
    assert grid[0] == -24.0 and grid[-1] == 21.0 and len(grid) == 46
    # The top never extends, even for a curve that levels off short of 99% (an error floor under fading).
    never_saturates = {float(s): (0.0 if s < 0 else 0.97) for s in range(-21, 22)}
    assert runner.pilot_grid(base, _counts(never_saturates)) == base
    assert runner.pilot_grid(base, _counts(ft8_like | {-24.0: 0.5}), floor=-24.0)[0] == -24.0


def test_fine_grid_spans_1_to_99_percent_plus_3_db() -> None:
    curve = {float(s): 0.0 for s in range(-24, 22)} | {-23.0: 0.02, -22.0: 0.19, -21.0: 0.52, -20.0: 0.93}
    curve |= {float(s): 1.0 for s in range(-19, 22)}
    rates = runner.monotone_rates(_counts(curve))
    grid = runner.fine_grid(rates, 0.5, runner.StudyConfig.fine_margin_db, (runner.PILOT_FLOOR_DB, 21.0))
    assert runner.StudyConfig.fine_margin_db == 3.0
    assert grid[0] == pytest.approx(-26.0) and grid[-1] == pytest.approx(-17.0)
    # A curve that never reaches 99% gets full trials up to the top of the pilot range and no further.
    floor_curve = {float(s): (0.0 if s < -10 else 0.9) for s in range(-21, 22)}
    capped = runner.fine_grid(runner.monotone_rates(_counts(floor_curve)), 0.5, 3.0, (runner.PILOT_FLOOR_DB, 21.0))
    assert capped[0] == pytest.approx(-13.0) and capped[-1] == pytest.approx(21.0)


def test_pilot_sweep_extends_each_series_and_stops(tmp_path: Path) -> None:
    config = runner.StudyConfig(
        run_dir=tmp_path, codec_binary=tmp_path / "missing", modes=("rtty",), pilot_trials=2, pilot_snrs=(-2.0, 2.0)
    )
    store = runner.TrialStore(tmp_path / "raw" / "trials.csv")
    pairs = [("rtty", MessageClass.CONTEST), ("rtty", MessageClass.TEXT)]
    thresholds = {"contest": -5.0, "text": 0.0}
    rounds = []

    def measure(grids: dict[tuple[str, MessageClass], tuple[float, ...]]) -> None:
        rounds.append(grids)
        rows = []
        for (mode, cls), snrs in grids.items():
            for snr in snrs:
                for trial in range(config.pilot_trials):
                    key = runner.trial_key(mode, cls, snr, trial)
                    if key in store.done_keys():
                        continue
                    ok = 10 if snr > thresholds[str(cls)] + 2 else (5 if snr > thresholds[str(cls)] - 2 else 0)
                    row: dict[str, object] = {name: "" for name in runner.ROW_FIELDS}
                    row.update(
                        mode=mode,
                        cls=str(cls),
                        snr_db=snr,
                        trial=trial,
                        stage="pilot",
                        units=10,
                        units_ok=ok,
                        run_chars=ok,
                        sent="X" * 10,
                    )
                    rows.append(row)
        store.append(rows)

    grids = runner._sweep_pilot(config, pairs, store, measure)
    assert grids[("rtty", MessageClass.CONTEST)] == (-7.0, -6.0, -5.0, -4.0, -3.0, -2.0, 2.0)
    assert grids[("rtty", MessageClass.TEXT)] == (-2.0, 2.0)
    assert rounds[-1] == grids and len(rounds) == 6
    fine = runner._fine_grids(config, pairs, store)
    assert fine[("rtty", MessageClass.CONTEST)][0] == pytest.approx(-9.0)
    assert fine[("rtty", MessageClass.TEXT)][-1] == pytest.approx(2.0)
    assert '"rtty/contest"' in (tmp_path / "grids.json").read_text()


def test_jtty_rtty_and_psk31_share_message_content_but_not_noise() -> None:
    a = runner.content_rng(7, MessageClass.TEXT, -18.0, 3).random()
    b = runner.content_rng(7, MessageClass.TEXT, -18.0, 3).random()
    c = runner.content_rng(7, MessageClass.TEXT, -18.5, 3).random()
    assert a == b != c
    noise = {
        mode: runner.channel_rng(7, mode, MessageClass.TEXT, -18.0, 3).random() for mode in ("jtty", "rtty", "psk31")
    }
    assert len(set(noise.values())) == 3


def test_trial_store_round_trip_and_resume_keys(tmp_path: Path) -> None:
    store = runner.TrialStore(tmp_path / "raw" / "trials.csv")
    assert store.done_keys() == set()
    good = {name: "" for name in runner.ROW_FIELDS} | {"mode": "rtty", "cls": "text", "snr_db": -8.5, "trial": 4}
    bad = good | {"trial": 5, "error": "codec exited"}
    store.append([good, bad])
    store.append([good | {"trial": 6}])
    assert store.done_keys() == {("rtty", "text", runner.snr_key(-8.5), 4), ("rtty", "text", runner.snr_key(-8.5), 6)}
    assert len(store.rows()) == 3


def test_rtty_trial_runs_without_a_codec(tmp_path: Path) -> None:
    config = runner.StudyConfig(run_dir=tmp_path, codec_binary=tmp_path / "missing", modes=("rtty",))
    trial_runner = runner.TrialRunner(config, tmp_path / "work", None)
    row = trial_runner.run("rtty", MessageClass.CONTEST, 0.0, 0, "fixed")
    assert row["error"] == "" and row["units_ok"] == row["units"] and row["copied"] == 1
    assert row["snr_reported_db"] != "" and abs(float(row["snr_reported_db"]) - 0.0) < 1.5
    again = trial_runner.run("rtty", MessageClass.CONTEST, 0.0, 0, "fixed")
    assert again["sent"] == row["sent"] and again["snr_true_measured_db"] == row["snr_true_measured_db"]


def test_unexpected_exception_becomes_an_error_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = runner.StudyConfig(run_dir=tmp_path, codec_binary=tmp_path / "missing", modes=("rtty",))
    trial_runner = runner.TrialRunner(config, tmp_path / "work", None)

    def explode(*_: object) -> dict[str, object]:
        raise ZeroDivisionError("boom")

    monkeypatch.setattr(trial_runner, "_run_once", explode)
    row = trial_runner.run("rtty", MessageClass.TEXT, -8.0, 3, "fine")
    assert row["error"] == "ZeroDivisionError: boom" and row["trial"] == 3 and row["units"] == ""


def _pilot_rows(ok_above: int) -> list[dict[str, object]]:
    """Return complete pilot rows (4 trials per SNR) whose decode rate steps up above ``ok_above`` dB."""
    rows = []
    for snr in range(-21, 22):
        for trial in range(4):
            row: dict[str, object] = {name: "" for name in runner.ROW_FIELDS}
            ok = 5 if snr > ok_above else (2 if snr > ok_above - 5 else 0)
            row.update(
                mode="rtty",
                cls="contest",
                snr_db=float(snr),
                trial=trial,
                stage="pilot",
                units=5,
                units_ok=ok,
                run_chars=ok,
                sent="X" * 5,
            )
            rows.append(row)
    return rows


def test_grid_planning_ignores_characters_matched_by_chance() -> None:
    rows = []
    for snr in (-22.0, -21.0, -20.0):
        row: dict[str, object] = {name: "" for name in runner.ROW_FIELDS}
        # 17% of characters line up by chance in noise, but none in a run of real copy.
        row.update(mode="rtty", cls="text", snr_db=snr, trial=0, units=20, units_ok=3, run_chars=0, sent="X" * 100)
        rows.append(row)
    counts = runner.unit_counts(rows)[("rtty", "text")]
    # Planning counts characters sent (100), not the row's 5-character groups (20).
    assert all(ok == 0 and units == 100 for ok, units, _ in counts.values())
    assert runner.pilot_grid((-21.0, -20.0), counts)[0] == -21.0, "a curve at its chance floor must not extend"


def test_fine_grids_are_saved_and_reused(tmp_path: Path) -> None:
    config = runner.StudyConfig(run_dir=tmp_path, codec_binary=tmp_path / "missing", modes=("rtty",), pilot_trials=4)
    store = runner.TrialStore(tmp_path / "raw" / "trials.csv")
    store.append(_pilot_rows(-6))
    pairs = [("rtty", MessageClass.CONTEST)]
    first = runner._fine_grids(config, pairs, store)
    assert (tmp_path / "grids.json").exists()
    # More pilot data that would move the grid must not move a saved one...
    store.append(_pilot_rows(-16))
    assert runner._fine_grids(config, pairs, store) == first
    # ...unless the grid settings change, which invalidates the saved grids.
    changed = runner.StudyConfig(
        run_dir=tmp_path, codec_binary=tmp_path / "missing", modes=("rtty",), pilot_trials=4, fine_margin_db=2.5
    )
    assert runner._fine_grids(changed, pairs, store) != first


def test_incomplete_pilot_grid_is_not_saved(tmp_path: Path) -> None:
    config = runner.StudyConfig(run_dir=tmp_path, codec_binary=tmp_path / "missing", modes=("rtty",), pilot_trials=5)
    store = runner.TrialStore(tmp_path / "raw" / "trials.csv")
    store.append(_pilot_rows(-6))
    runner._fine_grids(config, [("rtty", MessageClass.CONTEST)], store)
    assert '"rtty/contest"' not in (tmp_path / "grids.json").read_text()


def test_old_trials_file_is_migrated_to_the_current_columns(tmp_path: Path) -> None:
    path = tmp_path / "raw" / "trials.csv"
    path.parent.mkdir()
    path.write_text("mode,cls,snr_db,trial,units,units_ok,error\nrtty,text,-8.5,4,7,7,\n")
    store = runner.TrialStore(path)
    rows = store.rows()
    assert list(rows[0]) == list(runner.ROW_FIELDS) and rows[0]["units_ok"] == "7" and rows[0]["failure"] == ""
    store.append([{"mode": "rtty", "cls": "text", "snr_db": -8.0, "trial": 5}])
    assert len(store.rows()) == 2 and store.done_keys() == {
        ("rtty", "text", runner.snr_key(-8.5), 4),
        ("rtty", "text", runner.snr_key(-8.0), 5),
    }


def test_jtty_snr_floor_is_read_from_the_wsjtx_source() -> None:
    assert runner.jtty_snr_floor() == -21.0
    assert runner.default_snr_floors()["jtty"] == -21.0


def test_noise_plan_covers_the_requested_hours(tmp_path: Path) -> None:
    config = runner.StudyConfig(run_dir=tmp_path, codec_binary=tmp_path / "x", modes=("jtty", "ft8"), noise_hours=0.5)
    tasks = runner._plan_noise(config, done={("ft8", 0)})
    chunks = {mode: sum(len(t.chunks) for t in tasks if t.mode == mode) for mode in ("jtty", "ft8")}
    assert chunks == {"jtty": 30, "ft8": 119}


def test_rtty_noise_reception_prints_garbage(tmp_path: Path) -> None:
    config = runner.StudyConfig(run_dir=tmp_path, codec_binary=tmp_path / "missing", modes=("rtty",))
    row = runner.TrialRunner(config, tmp_path / "work", None).run_noise("rtty", 0)
    assert row["error"] == "" and row["seconds"] == 60.0 and row["false_prints"] > 50


def test_replay_reproduces_a_trial_exactly(tmp_path: Path) -> None:
    config = runner.StudyConfig(run_dir=tmp_path, codec_binary=tmp_path / "missing", modes=("rtty",), channel="MM")
    runner.write_config(config)
    original = runner.TrialRunner(config, tmp_path / "work", None).run("rtty", MessageClass.CONTEST_MACRO, -6.0, 3, "f")
    wav = tmp_path / "trial.wav"
    replayed = runner.replay_trial(tmp_path, "rtty", MessageClass.CONTEST_MACRO, -6.0, 3, tmp_path / "missing", wav)
    for key in ("sent", "printed", "snr_true_measured_db", "copied", "msg_kind", "contest"):
        assert replayed[key] == original[key], key
    import wave

    with wave.open(str(wav)) as stream:
        assert stream.getframerate() == 12000 and stream.getsampwidth() == 2 and stream.getnframes() > 12000


def test_config_round_trips_through_config_json(tmp_path: Path) -> None:
    config = runner.StudyConfig(
        run_dir=tmp_path,
        codec_binary=tmp_path / "c",
        seed=77,
        channel="HM",
        snr_floors={"jtty": -21.0, "ft8": -25.0, "ft4": -21.0},
    )
    runner.write_config(config)
    loaded = runner.StudyConfig.from_run(tmp_path)
    assert (loaded.seed, loaded.channel, loaded.snr_floors["jtty"]) == (77, "HM", -21.0)
    assert loaded.fading is not None and loaded.fading.spread_hz == 10.0
    with pytest.raises(SystemExit):
        runner.write_config(runner.StudyConfig(run_dir=tmp_path, codec_binary=tmp_path / "c", seed=77, channel="MM"))


def test_resuming_a_run_from_an_older_tool_or_other_decoder_is_refused(tmp_path: Path) -> None:
    import json

    config = runner.StudyConfig(run_dir=tmp_path, codec_binary=tmp_path / "c")
    runner.write_config(config)
    stored = json.loads((tmp_path / "config.json").read_text())
    (tmp_path / "config.json").write_text(json.dumps({**stored, "content_version": 1}))
    with pytest.raises(SystemExit):
        runner.write_config(config)
    (tmp_path / "config.json").write_text(json.dumps({**stored, "snr_floors": {"jtty": -17.0}}))
    with pytest.raises(SystemExit):
        runner.write_config(config)
    old_units = {key: value for key, value in stored.items() if key != "unit_scoring"}
    (tmp_path / "config.json").write_text(json.dumps(old_units))
    with pytest.raises(SystemExit, match="rescore"):
        runner.write_config(config)


def test_replay_reports_a_trial_whose_message_changed(tmp_path: Path) -> None:
    config = runner.StudyConfig(run_dir=tmp_path, codec_binary=tmp_path / "missing", modes=("rtty",))
    runner.write_config(config)
    store = runner.TrialStore(tmp_path / "raw" / "trials.csv")
    row = {name: "" for name in runner.ROW_FIELDS} | {
        "mode": "rtty",
        "cls": "contest",
        "snr_db": -6.0,
        "trial": 2,
        "sent": "AN OLDER MESSAGE",
    }
    store.append([row])
    replayed = runner.replay_trial(tmp_path, "rtty", MessageClass.CONTEST, -6.0, 2, tmp_path / "missing", None)
    assert replayed["error"].startswith("replay mismatch")


def test_replay_rejects_a_class_the_mode_never_runs(tmp_path: Path) -> None:
    from jtty_snr_study import cli

    with pytest.raises(SystemExit):
        cli.main(["replay", str(tmp_path), "--mode", "ft8", "--cls", "contest", "--snr", "-10", "--trial", "0"])


def test_psk31_trials_run_without_a_codec_and_send_text_in_lower_case(tmp_path: Path) -> None:
    config = runner.StudyConfig(run_dir=tmp_path, codec_binary=tmp_path / "missing", modes=("psk31",))
    trial_runner = runner.TrialRunner(config, tmp_path / "work", None)
    rtty_runner = runner.TrialRunner(
        runner.StudyConfig(run_dir=tmp_path, codec_binary=tmp_path / "missing", modes=("rtty",)), tmp_path, None
    )
    for cls in (MessageClass.CONTEST, MessageClass.TEXT, MessageClass.CONTEST_MACRO):
        row = trial_runner.run("psk31", cls, 0.0, 2, "fixed")
        assert row["error"] == "" and row["units_ok"] == row["units"] and row["copied"] == 1
        assert row["snr_reported_db"] != "" and abs(float(row["snr_reported_db"])) < 2.0
        same = rtty_runner.run("rtty", cls, 0.0, 2, "fixed")
        assert row["sent"].upper() == same["sent"]
        if cls is MessageClass.TEXT:
            assert row["sent"] == row["sent"].lower() and row["printed"] == row["sent"].upper()
        elif cls is MessageClass.CONTEST:
            assert row["sent"] == row["sent"].upper()
    again = trial_runner.run("psk31", MessageClass.TEXT, 0.0, 2, "fixed")
    assert (
        again["snr_true_measured_db"]
        == trial_runner.run("psk31", MessageClass.TEXT, 0.0, 2, "fixed")["snr_true_measured_db"]
    )


def test_psk31_noise_reception_prints_characters(tmp_path: Path) -> None:
    config = runner.StudyConfig(run_dir=tmp_path, codec_binary=tmp_path / "missing", modes=("psk31",))
    row = runner.TrialRunner(config, tmp_path / "work", None).run_noise("psk31", 0)
    assert row["error"] == "" and row["seconds"] == 60.0 and int(row["false_prints"]) > 0


def test_reference_includes_psk31_qsos_and_bandwidth(tmp_path: Path) -> None:
    config = runner.StudyConfig(run_dir=tmp_path, codec_binary=tmp_path / "missing", modes=("psk31",))
    reference = runner.build_reference(config, None)
    assert 30.0 < reference["mode_info_measured"]["psk31"]["bw99_hz"] < 80.0
    overs = reference["qso"]["ru"]["psk31"]
    call = next(over for over in overs if over["kind"] == "call")
    # " W9XYZ " in capitals: 64 preamble/postamble bits plus Varicode and gaps; 1-symbol rise and fall.
    assert call["units"] == 7 and call["info_bits"] == 25
    assert call["airtime_s"] == pytest.approx((call["channel_bits"] + 2) / 31.25, abs=1e-3)
    assert {over["kind"] for over in reference["qso"]["ru"]["psk31-macro"]} == {over["kind"] for over in overs}


def _old_row(mode: str, cls: str, units: int, units_ok: int, sent: str, printed: str, copied: int) -> dict[str, str]:
    """Return a raw row as a run scored in frames, messages and 5-character groups wrote it (no frames column)."""
    fields = [name for name in runner.ROW_FIELDS if name != "frames"]
    row = {name: "" for name in fields}
    row.update(
        mode=mode,
        cls=cls,
        snr_db="-10.0",
        trial="0",
        units=str(units),
        units_ok=str(units_ok),
        copied=str(copied),
        sent=sent,
        printed=printed,
        copied_chars="5",
        snr_reported_db="-9.0",
    )
    return row


def test_rescore_run_copies_an_old_run_scored_in_groups(tmp_path: Path) -> None:
    import csv
    import json

    source, target = tmp_path / "old", tmp_path / "new"
    (source / "raw").mkdir(parents=True)
    (source / "config.json").write_text(json.dumps({"seed": 5, "run_dir": str(source), "content_version": 2}))
    (source / "grids.json").write_text("{}")
    (source / "raw" / "noise.csv").write_text("mode,chunk\n")
    rows = [
        _old_row("jtty", "text", 7, 6, "NAME IS TOM QTH BOSTON RIG IC7300", "IS TOM QTH BOSTON RIG IC7300", 0),
        _old_row("jtty", "contest-macro", 1, 1, "W9XYZ 599 MA MA", "W9XYZ 599 MA", 1),
        _old_row("ft8", "qso", 1, 1, "K1ABC W9XYZ -12", "K1ABC W9XYZ -12", 1),
        _old_row("ft4", "qso", 1, 0, "K1ABC W9XYZ R-12", "", 0),
        _old_row("rtty", "text", 3, 2, "ABCDEFGHIJKLMNO", "ABCDEFGXIJKLMNO", 0),
        _old_row("rtty", "text", 2, 1, "ABCDEFGHIJ", "ABCXDEFGHIJ", 0)
        | {"failure": "missed;false_text", "garbage": "1"},
        _old_row("psk31", "contest", 1, 0, "W9XYZ 599", "W9XYZ 5Q9", 0),
        _old_row("rtty", "text", 2, 0, "HELLO WORLD", "", 0) | {"error": "RuntimeError: boom"},
    ]
    old_fields = list(rows[0])
    with (source / "raw" / "trials.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=old_fields)
        writer.writeheader()
        writer.writerows(rows)
    before = (source / "raw" / "trials.csv").read_bytes()

    assert runner.rescore_run(source, target) == len(rows)
    new = runner.read_csv_rows(target / "raw" / "trials.csv")
    assert tuple(new[0]) == runner.ROW_FIELDS
    counts = [(r["mode"], r["cls"], r["units"], r["units_ok"], r["frames"], r["copied_chars"]) for r in new]
    # Runs of 5+: the inserted X splits ABCDEFGHIJ into 3 + 7, and W9XYZ 5Q9 keeps "W9XYZ 5".
    assert [r["run_chars"] for r in new] == ["28", "1", "15", "0", "14", "7", "7", ""]
    # Units are 5-character groups (a final 1-4 characters form their own group); RTTY/PSK31 copied_chars counts the
    # characters of the copied groups, for throughput.
    assert counts == [
        ("jtty", "text", "7", "6", "7", "5"),
        ("jtty", "contest-macro", "1", "1", "", "5"),
        ("ft8", "qso", "3", "3", "", "5"),
        ("ft4", "qso", "4", "0", "", "5"),
        ("rtty", "text", "3", "2", "", "10"),
        ("rtty", "text", "2", "1", "", "5"),
        ("psk31", "contest", "2", "1", "", "5"),
        ("rtty", "text", "2", "0", "", "5"),
    ]
    # The X inserted inside "ABCDE" breaks that group.
    assert [r["failure"] for r in new[4:7]] == ["missed", "missed;false_text", "missed"]
    assert all(r["snr_reported_db"] == "-9.0" and r["sent"] == o["sent"] for r, o in zip(new, rows, strict=True))
    config = json.loads((target / "config.json").read_text())
    assert config["unit_scoring"] == "5-char groups" and config["run_dir"] == str(target) and config["seed"] == 5
    assert config["rescored_from"] == str(source)
    assert (target / "grids.json").exists() and (target / "raw" / "noise.csv").exists()
    assert (source / "raw" / "trials.csv").read_bytes() == before, "the source run must not change"
    assert "unit_scoring" not in json.loads((source / "config.json").read_text())

    with pytest.raises(SystemExit, match="not empty"):
        runner.rescore_run(source, target)
    with pytest.raises(SystemExit, match="already scored"):
        runner.rescore_run(target, tmp_path / "again")


def test_rescore_row_keeps_the_frame_count_of_a_run_that_has_one() -> None:
    # A run scored in characters stores JTTY frames in their own column; its units are characters, not frames.
    row = _old_row("jtty", "text", 33, 28, "NAME IS TOM QTH BOSTON RIG IC7300", "IS TOM QTH BOSTON RIG IC7300", 0)
    rescored = runner.rescore_row(row | {"frames": "7"})
    assert (rescored["units"], rescored["units_ok"], rescored["frames"]) == (7, 6, "7")


def test_grid_planning_divides_run_characters_by_characters_sent() -> None:
    clean: dict[str, object] = {name: "" for name in runner.ROW_FIELDS}
    clean.update(mode="rtty", cls="text", snr_db=0.0, trial=0, units=3, units_ok=3, run_chars=11, sent="CQ K1ABC CQ")
    macro: dict[str, object] = {name: "" for name in runner.ROW_FIELDS}
    macro.update(mode="rtty", cls="contest-macro", snr_db=0.0, trial=0, units=1, units_ok=1, run_chars=1, sent="W9XYZ")
    counts = runner.unit_counts([clean, macro])
    assert counts[("rtty", "text")][0.0] == (11, 11, 1), "a clean copy plans as 100%, not 11 / 3 groups"
    assert counts[("rtty", "contest-macro")][0.0] == (1, 1, 1)


def test_rescore_applies_the_ambiguity_rule_to_contest_rows() -> None:
    macro = _old_row("rtty", "contest-macro", 1, 1, "W4LEW W4LEW", "QRLEW W4LEW", 1) | {
        "msg_kind": "call",
        "garbage": "2",
    }
    lost = _old_row("rtty", "contest-macro", 1, 1, "W4LEW W4LEW", "W4LEW", 1) | {"msg_kind": "call"}
    over = _old_row("jtty", "contest", 1, 1, "K2ABC", "K2ABC K2ABD", 1) | {"msg_kind": "call", "garbage": "1"}
    ft8 = _old_row("ft8", "qso", 1, 1, "K1ABC W9XYZ -12", "K1ABC W9XYZ -12 | K1ABC W9XYD -12", 1)
    rescored = [runner.rescore_row(row) for row in (macro, lost, over, ft8)]
    assert (rescored[0]["copied"], rescored[0]["units_ok"], rescored[0]["copied_chars"]) == (0, 0, 0)
    assert rescored[0]["failure"] == "ambiguous;false_text" and rescored[0]["snr_reported_db"] == ""
    assert rescored[1]["copied"] == 1 and rescored[1]["copied_chars"] == len("W4LEW W4LEW")
    # A JTTY over keeps its stored frame-based failure classes (none here) and gains "ambiguous".
    assert rescored[2]["copied"] == 0 and rescored[2]["failure"] == "ambiguous"
    assert rescored[3]["copied"] == "1", "FT8/FT4 decodes are whole messages: no ambiguity rule"


def test_rescore_never_finds_the_agn_request_ambiguous() -> None:
    agn = _old_row("rtty", "contest-macro", 1, 1, "AGN? AGN?", "AGN? AGNR", 1) | {"msg_kind": "agn", "garbage": "1"}
    rescored = runner.rescore_row(agn)
    assert rescored["copied"] == 1 and rescored["failure"] == "false_text"
