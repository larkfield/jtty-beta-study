# Strong-signal failures

Trials at least 3 dB above each series' 90% decode point that were not a clean copy. *missed*: units not copied; *split*: JTTY printed every frame but over several lines; *no_eom*: JTTY printed every frame in one line without its end-of-message; *ambiguous*: a contest field also printed a conflicting value; *false_text*: wrong text printed.

| Mode | Class | From SNR | Trials | Failed | Per 1000 | Missed | Split | No EOM | Ambiguous | False text |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FT8 | QSO messages | -15.0 | 7300 | 35 | 4.8 | 26 | 0 | 0 | 0 | 9 |
| FT4 | QSO messages | -12.7 | 8000 | 18 | 2.2 | 16 | 0 | 0 | 0 | 2 |
| JTTY | contest overs | -10.8 | 5900 | 57 | 9.7 | 51 | 2 | 0 | 0 | 5 |
| JTTY | contest macros | -9.8 | 11500 | 200 | 17.4 | 184 | 0 | 0 | 1 | 17 |
| JTTY | free text | -4.6 | 18600 | 2189 | 117.7 | 2077 | 54 | 0 | 0 | 82 |
| PSK31 | contest overs | -5.9 | 20600 | 514 | 25.0 | 496 | 0 | 0 | 0 | 289 |
| PSK31 | contest macros | -3.2 | 21300 | 922 | 43.3 | 413 | 0 | 0 | 83 | 749 |
| PSK31 | free text | -2.7 | 26000 | 2600 | 100.0 | 2537 | 0 | 0 | 0 | 1565 |
| RTTY | contest overs | -1.0 | 22000 | 646 | 29.4 | 633 | 0 | 0 | 0 | 614 |
| RTTY | contest macros | +0.5 | 20000 | 912 | 45.6 | 431 | 0 | 0 | 77 | 896 |
| RTTY | free text | +1.7 | 17000 | 2216 | 130.4 | 2151 | 0 | 0 | 0 | 1944 |

JTTY split messages starting within 3% of a decoder search-window boundary: 49 of 56, against 5.5% of all 24500 strong-signal JTTY trials (one-sided binomial p = 3.3e-54).
