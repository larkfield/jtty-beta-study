# JTTY SNR study

A tool that measures how JTTY's decode rate falls as the signal sinks into band noise, the way FT8 and FT4 are
characterized, with **FT8, FT4, RTTY and PSK31** run through the same simulated HF station for comparison. One command
produces the raw data, tables, charts and a shareable HTML report.

Every trial renders a real transmission, puts it on the air at a known SNR (band noise, optional HF fading, the
rig's receive filter, a sound card at a fixed level), and decodes it:

- **JTTY, FT8 and FT4** are rendered and decoded by this WSJT-X tree's own `wsjt-codec` in streaming mode. JTTY
  goes through the GUI's receive path (`jtty_rx_*`) and sends contest exchanges as its native function-key atoms.
- **RTTY** is generated and decoded by a C extension module: a standard amateur AFSK modulator and a matched-filter
  reference demodulator.
- **PSK31** likewise: a C BPSK31 modulator (G3PLX Varicode) and a reference differential demodulator with
  click-to-snap tuning and AFC.

## Running it

```sh
cd contrib/jtty_snr_study
./run_study.sh                          # standard study: 1000 trials per SNR point, 24 h of noise per mode
./run_study.sh --preset quick           # 200 trials per point, for a first look
./run_study.sh --preset publication     # 5000 trials per point
./run_study.sh --preset smoke           # tool check only: 5 SNRs x 20 trials
./run_study.sh --channel MM             # the same through mid-latitude moderate HF fading
```

**Requirements:**
- A configured WSJT-X build in `../../build`. The script always runs the incremental `wsjt-codec` build first, so
  decoder source changes take effect.
- [uv](https://docs.astral.sh/uv/).
- A C compiler. uv installs numpy, scipy and matplotlib and builds the C modules on first run.

Results go to `results/<run-name>/`; the script prints where. Open `report.html` in a browser.

| Option | Meaning |
|---|---|
| `--preset smoke\|quick\|standard\|publication` | Trials per SNR point: 20 / 200 / 1000 / 5000 (default `standard`) |
| `--trials N` | Override the preset's trials per point |
| `--modes ft8,ft4,jtty,psk31,rtty` | Run a subset of modes |
| `--channel AWGN\|MQ\|MM\|MD\|HM\|...` | Propagation channel: AWGN (default) or an ITU-R F.1487 fading model (see below) |
| `--noise-hours H` | Hours of pure band noise per mode for false-print rates (preset default 0.05 / 4 / 24 / 100) |
| `--turnaround S` | Seconds added per over in the contest-QSO timing (default 0: airtime only) |
| `--run-name NAME` | Run directory name (default `<preset>[-<channel>]-<date>-<time>`) |
| `--seed S` | Base seed (default 2026); every trial is reproducible from it |
| `--workers N` | Parallel workers (default: CPU count − 2), e.g. `--workers 24` to use at most 24 cores |
| `--snr-min A --snr-max B --snr-step C` | Fixed SNR grid instead of the automatic pilot/fine grid |
| `--pilot-trials N` | Trials per pilot point (default 100) |
| `--level P` | Headline 5-character decode percentage for the thresholds (default 80) |

**SNR grid.** A pilot pass sweeps −21 to +10 dB in 1 dB steps at `--pilot-trials` trials per point. Where a curve
is still above 1% at −21 dB (FT8 is about 50% there), the pilot extends downward 1 dB at a time until the curve is
at or below 1%; it never extends above +10 dB. The fine pass then runs the full trial count every 0.5 dB over each
curve's 1–99% span plus 3 dB each side, capped at +10 dB: a curve that levels off below 99% (an error floor under
fast fading) gets full trials up to +10 dB. The span is read from a fit of the pilot curve that is constrained to
rise with SNR, so a single chance miss at a strong signal does not stretch it. The grid is planned on characters
copied in runs of 5 or more (`run_chars`), because single RTTY and PSK31 characters match by chance in noise (about
15–20% of them at any SNR) and would stretch every sweep to −40 dB. Charts and tables use both passes.
Every chart spans −21 to +10 dB; points measured below −21 dB count toward the tables but are not drawn.
`--pilot-trials 1000` gives full trials across the whole range, at a little over twice the run time.

**Resuming and extending.** Rows are saved as trials finish. Re-running with the same `--run-name`, `--seed` and
`--channel` skips finished trials, so an interrupted run resumes and a larger `--trials` tops up an existing run.

**Rescoring an older run.** A run scored in other units cannot be resumed or analyzed as it is: older runs counted
JTTY frames, FT8/FT4 messages and RTTY/PSK31 groups on the old alignment, and the `-90` runs counted characters.
`rescore` copies one into a new run directory scored in 5-character groups, from the sent and printed text it stored,
without decoding anything again; the old run is only read. Then analyze or report the new directory:

```sh
uv run python -m jtty_snr_study rescore results/final-MM results/final-MM-80-5char
uv run python -m jtty_snr_study report results/final-MM-80-5char      # --level 90 for another headline level
```

A rescored run keeps the RTTY and PSK31 SNR estimates its source stored: the original runs stored them only for
trials that copied a whole 5-character group on the old alignment, while new runs (and the `-90` runs) report after
5 consecutive characters are copied. Contest-macro
series are scored one unit per macro either way.
For example, run `quick` first and then `--preset publication --run-name <same name>`. The fine SNR grid is fixed in
`grids.json` after the pilot pass; delete that file to recompute it. Trials that failed are saved as error rows,
left out of the analysis, and retried on resume. Raw files from older versions of the tool are migrated to the
current columns automatically.

**Replication.** Runs with the same seed share their trials (the quick run's trials are a subset of the standard
run's), so their agreement is not independent confirmation. To replicate, use another seed:
`./run_study.sh --seed 7 --run-name standard-seed7`.

**Final experiment.** `./run_final_experiment.sh` runs the standard study on AWGN, MM, MQ, MD, HM and LM, one channel
after another, on 16 workers at nice 19, with its own seed (2027) so the data is independent of earlier runs. It prints
overall progress and an approximate ETA, writes `results/final-<channel>/`, and keeps each channel's full output in
`results/final-logs/<channel>.log`. Running it again resumes. Extra options are passed on to `run_study.sh`.

**Run stages separately:** `uv run python -m jtty_snr_study analyze results/<run>` rebuilds the tables, and
`... report results/<run>` the figures and report.

**Replaying a trial.** Any trial can be regenerated exactly and saved as a 12 kHz WAV that WSJT-X opens (File |
Open, in JTTY mode) -- the reproducer for the decoder findings below:

```sh
uv run python -m jtty_snr_study replay results/<run> --mode jtty --cls text --snr -5 --trial 51 --wav trial.wav
```

**Runtime** (measured on a 32-core machine with 24 workers):

| Preset | Trials | Noise | Time |
|---|---:|---:|---:|
| quick | 53,900 | 1 h per mode | 5.5 min (measured) |
| standard | about 180,000 | 24 h per mode | about 18 min (estimated) |
| publication | about 810,000 | 100 h per mode | about 80 min (estimated) |

FT8 trials dominate (about 0.4 s of decoding per 15 s period), then JTTY free text (about 0.25 s); RTTY and PSK31 cost
almost nothing. (These timings predate PSK31, which adds about a fifth more trials.) Runtime scales with the number of workers.

## What it measures

**SNR** is the WSJT-X convention: signal power over noise power in a 2500 Hz reference bandwidth, at the antenna.
It is the number FT8 prints, and the convention `ft8sim`, `ft4sim` and `sjtty` use.

| Metric | JTTY | FT8 / FT4 | RTTY and PSK31 |
|---|---|---|---|
| **5-character decode %** (headline) | 5-character groups printed exactly | every group of a decoded message | 5-character groups printed exactly |
| **Whole-message copy %** | all frames + end-of-message, exact (macros: call + one exchange copy), no ambiguous field | exact message | exact text (macros: call + one exchange copy), no ambiguous field |
| **Character error rate** | edit distance / sent length | (0 or 1) | edit distance / sent length |
| **Throughput** (60 × characters copied ÷ seconds of airtime, both summed over the trials at an SNR) | text of correctly decoded frames, each frame's edge spaces left out ÷ whole 1.888 s frames sent | whole message when decoded ÷ a 15 s / 7.5 s T/R slot per message | characters of 5-character groups printed whole ÷ the whole transmission (lead-in, idle, framing and tail included) |
| **Garbage** | wrong frames printed | wrong messages | wrong characters while the message was on the air |
| **Reported SNR** | decoder's report (first frame) | decoder's report | demodulator's estimate |

Scoring ignores letter case, so PSK31's lower-case text is compared with the same upper-case reference.

Details:
- **Trials and units.** A trial is one transmission; units are what decode % counts: 5-character groups of the
  sent text (one unit per whole macro for the contest-macro series). Decode intervals come from a bootstrap over
  whole trials, so groups sharing a transmission are not treated as independent. All copy % intervals are Wilson
  intervals over trials. Garbage is counted per 1000 trials.
- **5-character groups.** Five characters is a callsign's worth (W9XYZ), the shortest piece of text that is useful on
  its own, so that is the unit of copy. The sent text, spaces included, is cut into groups of five from its start; a
  final 1–4 characters form a short group of their own. A group counts when all its characters print exactly, as one
  unbroken piece: garbage printed between two groups breaks neither, garbage inside a group breaks it. Characters
  are matched as the most sent characters that print in order (a longest common subsequence). JTTY's ` ... `
  missed-frame markers read as spaces. An FT8 or FT4 message gives all its groups or none. A message's short final
  group can line up by chance in garbage printed from noise, which puts a small floor under the RTTY and PSK31
  curves deep in the noise.
- **Ambiguous contest fields.** A contest over or macro is not copied when one of its fields (call, exchange) is
  received with two different values, since the operator can't tell which is right: a printed word that could pass
  for the field (same length, at most 2 characters different, or 1 for fields of 3 characters or fewer) without being
  any sent word. `W4LEW W4LEW` printed `QRLEW W4LEW` is not copied; `W4LEW` with the other copy lost is. Fragments
  (`2BMJ` for `KE2BMJ`), words of another length, and junk with any symbol other than a trailing query mark
  (`HA<JDU`, `K2AB?D`; every call, state and serial is letters and digits) don't count; a `?` at the end of a word is
  ignored, so `K2ABD?` reads as `K2ABD`. This affects exchange copy, the contest-macro
  thresholds and QSO times, not the 5-character decode rate; FT8/FT4 decodes are whole, CRC-checked messages.
  Required fixed words (the repeated AGN? request, TU in the TU over) are never ambiguous: a garble only loses them.
- **True SNR** is recorded per trial two ways: the nominal sweep value, and the value measured from the trial's
  actual signal and noise samples.
- **Reported SNR.** A decoder never reports below its floor (JTTY −21 dB as of this tree, FT4 −21 dB, FT8 −25 dB).
  Where reports hit the floor they are treated as censored and the mean and SD come from a censored-normal fit, so
  averages near a floor are not biased high.
- **Thresholds.** Each mode's SNR at 10%, 20%, ... 90% decode is read by linear interpolation from its measured
  curve, fitted to never fall as SNR rises; a rate not reached by +10 dB is left blank. The 80% point (`--level`)
  is the headline threshold, with a bootstrap 95% interval, Eb/N0 and the mean SNR the decoder reported there.
- **Eb/N0** = SNR + 10·log10(2500 Hz / net bit rate): JTTY 34 information bits per 1.888 s frame, FT8 and FT4
  77 bits per 12.64 s / 5.04 s transmission, RTTY only the 5 data bits of each 7.5-bit character (start, stop and
  shift characters are overhead), PSK31 the same 5 bits per character at the mean Varicode length (plus the `00`
  gap) of the text each series sent: 6.7 bits for lower-case free text, about 9 for contest overs in capitals.
- **Strong-signal failures.** Trials at least 3 dB above each series' logistic-fit 90% point that still were not a
  clean copy (a series with no 90% point, or no trials that high now that the sweep stops at +10 dB, is listed as not
  measured),
  classified as *missed* frames, *split* (JTTY decoded every frame but printed the message over several lines),
  *no_eom* (every frame in one line but no end-of-message) or *false_text*. For JTTY splits the analysis tests
  whether they start near a decoder search-window boundary (the receiver searches every quarter frame).
- **False prints in band noise.** Pure noise through the same receiver and decoders: false prints per hour with
  exact Poisson intervals. RTTY and PSK31 have no squelch here, so they print characters from noise continuously.

### Contest QSO: time and bits to complete an exchange

Two reference QSOs, the S&P station (W9XYZ in WI) answering K1ABC (in MA) running:

| | JTTY, RTTY and PSK31 | FT8 and FT4 |
|---|---|---|
| **ARRL RTTY Roundup** | `CQ K1ABC CQ` → `W9XYZ` → `W9XYZ 599 MA` → `599 WI` → `TU K1ABC CQ` | RU mode: `CQ RU K1ABC FN42` → `K1ABC W9XYZ 579 WI` → `W9XYZ K1ABC R 589 MA` → `K1ABC W9XYZ RR73` |
| **Serial-number contest** | the same shape with `599 101` / `599 057` | standard QSO: grid → report → R-report → RR73 |

- **JTTY** sends the exchanges as native function-key atoms (`%H %E`, `%E`, `%H 599 %N`, `599 %N` with the `rtty` or
  `none` profile), so its callsign compression and typed exchanges come straight from the real encoder: a call is one
  frame, an exchange one frame.
- **RTTY and PSK31** are sent two ways, in capitals: minimally (the same text between spaces) and as typical contest
  macros (CR/LF framing, exchange sent twice, e.g. `W9XYZ 599 MA MA`), which counts as copied when the call is exact
  and at least one exchange repetition is. JTTY also sends those macro words, without the CR/LF, and is scored the
  same way. Every PSK31 over carries fldigi's 1 s preamble and 1 s postamble.
- **Bits:** information bits (JTTY 34 per frame, FT8/FT4 77 per message, RTTY and PSK31 5 per printed character) and
  channel bits (everything transmitted: sync, FEC, start/stop, shifts, Varicode gaps, preamble, framing).
- **Best case:** the error-free QSO time: summed airtime for JTTY/RTTY/PSK31 (plus `--turnaround` per over), one T/R slot
  per over for FT8/FT4.
- **Median:** at each SNR, the median time of 20,000 simulated QSOs (fixed seed), from the measured copy probability
  of each over (a logistic fit of the contest trials of that over). JTTY, RTTY and PSK31 repeat a failed over after an `AGN?` request (itself possibly repeated); FT8 and FT4 lose a
  full T/R cycle per failed over. Shown only where every over copies at least 5% of the time.

### The modes

Every report has a mode table with modulation, tones, tone spacing, baud, symbols, frame and slot length,
information and CRC bits, FEC, gross and net bit rates, synchronization, and bandwidth -- nominal from the protocol
documents and measured (99% of power) from each mode's actual rendered signal.

| Mode | Modulation | Baud | FEC | Frame | Net bit/s | Bandwidth |
|---|---|---:|---|---:|---:|---:|
| JTTY | 4-GFSK | 31.25 | K=10 r=½ (92,46), 34-bit atom + 12-bit CRC | 1.888 s, asynchronous | 18.0 | 127 Hz |
| FT8 | 8-GFSK | 6.25 | LDPC (174,91), 77 bits + 14-bit CRC | 12.64 s in 15 s slots | 6.1 | 50 Hz |
| FT4 | 4-GFSK | 20.83 | LDPC (174,91), 77 bits + 14-bit CRC | 5.04 s in 7.5 s slots | 15.3 | 83 Hz |
| RTTY | 2-FSK, 170 Hz shift | 45.45 | none | 0.165 s per character | 30.3 | about 250 Hz |
| PSK31 | differential BPSK | 31.25 | none | 0.215 s per lower-case character on average (Varicode) | 23.2 | 45 Hz |

## The simulated station

Settings mimic an HF rig as closely as practical:

- **Band noise:** white Gaussian (AWGN), added at the antenna before the receive filter.
- **Fading (optional):** a Watterson HF channel, below.
- **Receive filter:**
  - JTTY/FT8/FT4/PSK31: USB with a 2.4 kHz SSB filter (300–2700 Hz); a PSK31 operator watches the waterfall.
  - RTTY: a 500 Hz filter (1960–2460 Hz), as RTTY contesters run.
  - Both are elliptic IIR bandpasses with crystal/DSP-like skirts and non-linear phase.
- **Sound card:** AGC off and the no-signal level at 30 dB on the WSJT-X input meter, as the WSJT-X user guide
  recommends. 16-bit audio at 12 kHz.
- **Frequency:** static offsets with no drift, as from a modern TCXO rig. JTTY's transmitter is within ±10 Hz of the
  Rx frequency, FT8/FT4 anywhere in 400–2400 Hz, RTTY has a ±5 Hz tuning error with no AFC, and the PSK31 operator's
  click lands within ±5 Hz of the signal.
- **Timing:** JTTY, RTTY and PSK31 start at a random time after 0.5–2.5 s of noise, asynchronous to the receiver. FT8/FT4
  DT is within about ±0.5 s.
- **JTTY decoder:** Rx 1500 Hz, F Tol 20 Hz, nsps 384, the GUI defaults. It searches Rx ± F Tol plus fixed channels
  over 1200–1800 Hz (about 600 Hz) and has no common time reference.
- **FT8/FT4 decoders:** Deep depth with a-priori (AP) decoding off, for a like-for-like comparison with JTTY,
  which has no AP. FT4 has no AP switch, so decodes that used AP are discarded and counted in `ap_filtered`.
- **Search range:** the FT8/FT4 decoders search 300–2700 Hz, the rig's passband, as an operator sets the Wide
  Graph, and they know when each period starts. A range reaching into the filter skirts skews their noise-baseline
  estimate: FT4 then misses strong signals near the band edge, and both over-report SNR by up to about 20 dB.
- **RTTY:**
  - Signal: 45.45 baud, 170 Hz shift (mark 2125 / space 2295 Hz), 1.5 stop bits, US-TTY Baudot with
    unshift-on-space. Transmissions start with a short mark and LTRS diddles.
  - Demodulator: matched filters on both tones. Timing comes from the start-bit edge, then is refined over the
    whole character. Framing errors are rejected.
  - Verified: its bit error rate matches noncoherent-FSK theory within 5%.
  - Scoring: only characters printed while the message was on the air count; garbage printed from noise before
    and after is counted separately (`noise_prints`).
- **PSK31:**
  - Signal: BPSK31 at 31.25 baud on a 1000 Hz audio carrier (the fldigi default), G3PLX Varicode, raised-cosine
    phase reversals. Each transmission is 32 reversals of preamble, a space, the message, a space, and 32 bits of
    steady carrier, as fldigi sends. Ragchew text is typed in lower case (shorter Varicode, about 56 wpm); contest
    overs and macros are sent in capitals, as contest loggers send them (about 40 wpm).
  - Demodulator: mixes to baseband at the clicked frequency, then snaps to the signal within ±10 Hz (the peak of the
    squared signal's spectrum), as fldigi does when you click near a trace; a matched filter, early-late symbol
    timing, differential detection, and an AFC weighted by signal quality. No squelch.
  - Verified: its bit error rate at known timing is 0.5–1 dB from ideal differential BPSK at 4–6 dB Eb/N0 (the
    matched filter's inter-symbol interference), and a ±5 Hz click error costs nothing measurable. Its SNR estimate
    reads within about 0.5 dB of the true SNR from −10 to +20 dB.
  - Scoring: as RTTY, ignoring letter case.

**Messages** are fresh random content for every trial:
- **contest:** one over of a reference contest QSO (either contest; CQ, call, exchanges, TU, or an AGN? request),
  with real calls from `ALLCALL7.TXT`, random states and serials. JTTY, RTTY and PSK31 also send them as macros.
- **text:** ragchew sentences of about 4–10 JTTY frames (PSK31 sends them in lower case).
- **qso:** one over of the FT8/FT4 reference QSOs.

JTTY, RTTY and PSK31 send the same text at the same SNR and trial number.

## Fading channels

`--channel` selects one of the ITU-R F.1487 (CCIR 520) Watterson models that WSJT-X's own simulators use
(`lib/jtty/sjtty.f90`, `lib/ft8/watterson.f90`). Each is two equal-strength paths, each Rayleigh fading with a
Gaussian Doppler spectrum, the second delayed. As in WSJT-X, each transmission is renormalized to its clean power,
so the SNR stays the average over the transmission; band noise is added after the fading. One channel per run.

| Model | Doppler spread | Path delay | Represents |
|---|---:|---:|---|
| MQ | 0.1 Hz | 0.5 ms | Mid latitude, quiet: stable F layer, slow gentle fading (CCIR "good") |
| MM | 0.5 Hz | 1 ms | Mid latitude, moderate: a typical day (CCIR "moderate", the usual benchmark) |
| MD | 1 Hz | 2 ms | Mid latitude, disturbed: unsettled ionosphere, multihop (CCIR "poor") |
| LQ | 0.5 Hz | 0.5 ms | Low latitude, quiet |
| LM | 1.5 Hz | 2 ms | Low latitude, moderate: paths through the equatorial ionosphere |
| LD | 10 Hz | 6 ms | Low latitude, disturbed: post-sunset equatorial spread-F |
| HQ | 0.5 Hz | 1 ms | High latitude, quiet |
| HM | 10 Hz | 3 ms | High latitude, moderate: auroral flutter |
| HD | 30 Hz | 7 ms | High latitude, disturbed: strong auroral flutter |

Fading depends on the propagation path, not only the station's location.

### Which models matter for a station on Long Island, NY

Long Island (about 40.8° N, 73° W) sits near 50° N *geomagnetic* latitude, because the geomagnetic pole (about 81° N)
lies at almost the same longitude: a mid-latitude station.

- **MM -- primary.** Domestic paths and most transatlantic contest contacts, i.e. most ARRL RTTY Roundup traffic.
- **MQ and MD -- bracket it.** A quiet day and an unsettled or multihop day.
- **HM -- the stress case.** Northern transatlantic paths to Scandinavia and NE Europe pass near Labrador and
  Greenland (about 60–65° geomagnetic, the auroral oval's edge), polar paths to Japan and Asia cross it, and in
  geomagnetic storms (Kp ≳ 6–7) the oval reaches about 50°, so even nearby paths flutter.
- **LM -- optional.** Paths to South America and Africa across the geomagnetic equator; LD only for night-time
  equatorial spread-F.

Run the recommended set, one run directory per channel:

```sh
for ch in MQ MM MD HM; do ./run_study.sh --channel $ch --run-name li-$ch; done
```

## Outputs (`results/<run-name>/`)

| Path | Contents |
|---|---|
| `config.json` | Every setting, the seed, channel, decoder SNR floors, WSJT-X version, `wsjt-codec` SHA-256, date, host |
| `reference.json` | Measured mode bandwidths; the reference QSOs' frames, characters, bits and airtimes |
| `raw/trials.csv` | One row per trial: mode, class, contest and over, SNR (nominal, measured, reported), offsets, search-grid phase, sent and printed text, scores (5-character groups sent and copied, characters in runs of 5, JTTY frames sent), failure class, airtime, copied characters, decoded messages as JSON |
| `raw/noise.csv` | One row per noise-only reception: what the decoder printed |
| `tables/summary.csv`, `.md` | Per mode/class/SNR: trials, units, decode % with CI, copy %, CER, garbage, throughput, true and reported SNR |
| `tables/thresholds.csv`, `.md` | Headline-level (default 80%) thresholds with CIs, Eb/N0, reported SNR at threshold |
| `tables/decode_levels.csv`, `.md` | SNR for each mode at 10%, 20%, ... 90% decode |
| `tables/modes.csv`, `.md` | The mode reference table |
| `tables/contest_qso.*`, `contest_qso_time.*` | Bits and best-case time per QSO and per over; median QSO time vs SNR |
| `tables/failures.*`, `noise.*` | Strong-signal failures with the grid-phase test; false prints per hour of noise |
| `analysis.json` | Every chart series and table, for further plotting |
| `figures/*.png`, `*.svg` | Decode vs SNR (headline), contest copy and QSO time (overs and macros on separate charts), JTTY copy by length, CER (with RTTY and PSK31 theory), reported vs true SNR, QSO time per contest |
| `report.html` | Self-contained page: mode table, channel, interactive charts, contest QSO, failures, noise, tables, method, caveats |

## Findings worth knowing

- **JTTY split messages at strong signal.** About 0.2–0.4% of strong JTTY messages are printed over two lines although
  every frame decoded. In the standard AWGN run all six began within 3% of a decoder search-window boundary, against
  5% of all trials (one-sided binomial p ≈ 2×10⁻⁸): the frame-to-message assembly misbehaves when a frame lands on a
  quarter-frame window edge. `replay` regenerates any such trial as a WAV for the developers.
- **JTTY false text at strong signal.** A few phantom lines (e.g. `TU NOW K95NVP`) appear mid-transmission on strong
  signals, at the same frequency, with no timing pattern -- a separate effect from the splits.
- **JTTY's SNR report floor.** It was −17 dB while JTTY decodes to about −19.5 dB, so most reports near threshold
  read −17. This tree now uses −21 dB (`SNR_FLOOR_DB` in `lib/jtty/jtty_mdecode.f90`); the study reads the floor from
  the source. With it, JTTY's reports track the true SNR within about 0.3 dB down to −15 dB and read 1–2 dB high at
  −18 to −19 dB, because near threshold only noise-favored decodes succeed (FT8 shows the same effect, about
  +0.5 dB at −22 dB).
- **Splits carry a frequency clue.** In a replayed split (`--cls text --snr -5 --trial 51` of the standard run), the
  two printed lines decoded at 1500.0 and 1511.4 Hz for a 1506.4 Hz signal, and consecutive frames alternate between
  them -- consistent with the receiver's continuation tolerance rejecting frames whose frequency estimate is skewed
  when they straddle a search-window boundary.

## Caveats

- **AWGN is the standard benchmark, not the whole of HF.** Use the fading channels; impulsive QRN and QRM are not
  modeled.
- **Search and timing differ.** JTTY searches about 600 Hz with no common time reference; FT8/FT4 search the whole
  passband but know when each period starts. A narrower search has fewer false starts, which can be worth a fraction
  of a dB.
- **The RTTY decoder is a careful reference implementation (about 1 dB from ideal noncoherent FSK), not MMTTY or
  2Tone.** Treat its curve as a well-adjusted decoder's, not any particular program's.
- **The PSK31 decoder is likewise a reference implementation, not fldigi** (about 1 dB from ideal differential
  BPSK near 1% bit errors). Its click-to-snap tuning finds the signal from the whole reception, as an operator does
  from a trace already on the waterfall.
- **Groups are lost differently.** Every mode is scored in 5-character groups, but an FT8 or FT4 message decodes
  whole or not at all and a JTTY frame nearly so, while RTTY and PSK31 lose characters one at a time, so a single
  lost character costs its whole group. Compare thresholds with the Eb/N0 column and the contest-QSO section as well as SNR.
- **RTTY's SNR estimate** is reported only when 5 consecutive characters were copied, as the WSJT-X decoders report
  only for a decode. It reads about 0.5–1 dB high across the range where RTTY copies.

## Development

```sh
uv run pytest                 # unit tests; integration tests use ../../build/wsjt-codec when it exists
uv run ruff check . && uv run ruff format --check . && uv run ty check
uv sync --reinstall-package jtty-snr-study   # rebuild the C modules after editing _rtty.c or _psk31.c
```

| Module | Role |
|---|---|
| `channel.py` | HF receiver model, Watterson fading, occupied bandwidth |
| `codec.py` | `wsjt-codec` streaming client (text and native-template renders) |
| `_rtty.c` / `rtty.py` | RTTY modulator/demodulator |
| `_psk31.c` / `psk31.py` | PSK31 modulator/demodulator |
| `messages.py` | Traffic generators and the reference contest QSOs |
| `modes.py` | Nominal mode parameters |
| `scoring.py` | Per-trial scoring and failure classes |
| `runner.py` | Sweep, noise stage, seeding, resumable storage, replay |
| `analysis.py` | Statistics and tables |
| `plots.py` | Static charts |
| `report.py` | HTML report |
| `cli.py` | Command line |
