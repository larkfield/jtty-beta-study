# JTTY beta SNR study

This repository holds the results of a test of the beta version of JTTY, as implemented in **WSJT-X 3.3.0-beta5**.

The test measures how JTTY's decode rate falls as the signal sinks into band noise. FT8, FT4, RTTY and PSK31 were run
through the same simulated HF station for comparison. Each run uses 1000 trials per SNR point, plus 24 hours of pure
band noise per mode to count false prints.

## Reports

There is one report per propagation channel: plain white noise (AWGN), and five ITU-R F.1487 HF fading models.

| Channel | Conditions | Doppler spread | Delay | Report |
|---|---|---|---|---|
| AWGN | White noise only, no fading | – | – | [Open report](https://larkfield.github.io/jtty-beta-study/final-AWGN-80-5char/report.html) |
| MQ | Mid latitude, quiet (CCIR "good") | 0.1 Hz | 0.5 ms | [Open report](https://larkfield.github.io/jtty-beta-study/final-MQ-80-5char/report.html) |
| MM | Mid latitude, moderate (CCIR "moderate") | 0.5 Hz | 1 ms | [Open report](https://larkfield.github.io/jtty-beta-study/final-MM-80-5char/report.html) |
| MD | Mid latitude, disturbed (CCIR "poor") | 1 Hz | 2 ms | [Open report](https://larkfield.github.io/jtty-beta-study/final-MD-80-5char/report.html) |
| LM | Low latitude, moderate | 1.5 Hz | 2 ms | [Open report](https://larkfield.github.io/jtty-beta-study/final-LM-80-5char/report.html) |
| HM | High latitude, moderate (auroral flutter) | 10 Hz | 3 ms | [Open report](https://larkfield.github.io/jtty-beta-study/final-HM-80-5char/report.html) |

## What is in each run directory

Each `final-<channel>-80-5char` directory holds one complete run. The name means that the headline threshold is the SNR
at which 80% of 5-character groups are copied.

- `report.html` – the full report, in a single self-contained file.
- `figures/` – the charts as PNG and SVG.
- `tables/` – the result tables as CSV and Markdown.
- `raw/` – every trial and noise measurement, as gzipped CSV.
- `config.json`, `grids.json`, `reference.json`, `analysis.json` – the run settings, SNR grid, reference data and
  computed results.

## Source

[`source/`](source/) contains the tool that produced these results. See [`source/README.md`](source/README.md) for how
it works and how to run it against a WSJT-X build.
