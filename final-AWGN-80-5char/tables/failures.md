# Strong-signal failures

Trials at least 3 dB above each series' 90% decode point that were not a clean copy. *missed*: units not copied; *split*: JTTY printed every frame but over several lines; *no_eom*: JTTY printed every frame in one line without its end-of-message; *ambiguous*: a contest field also printed a conflicting value; *false_text*: wrong text printed.

| Mode | Class | From SNR | Trials | Failed | Per 1000 | Missed | Split | No EOM | Ambiguous | False text |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FT8 | QSO messages | -17.2 | 3700 | 6 | 1.6 | 0 | 0 | 0 | 0 | 6 |
| FT4 | QSO messages | -13.7 | 4300 | 1 | 0.2 | 0 | 0 | 0 | 0 | 1 |
| JTTY | contest overs | -11.5 | 5000 | 2 | 0.4 | 0 | 0 | 0 | 0 | 2 |
| JTTY | contest macros | -11.9 | 4100 | 2 | 0.5 | 1 | 0 | 0 | 0 | 1 |
| JTTY | free text | -11.6 | 4100 | 18 | 4.4 | 0 | 6 | 0 | 0 | 12 |
| PSK31 | contest overs | -7.6 | 5600 | 17 | 3.0 | 13 | 0 | 0 | 0 | 4 |
| PSK31 | contest macros | -7.5 | 5600 | 11 | 2.0 | 5 | 0 | 0 | 0 | 6 |
| PSK31 | free text | -7.6 | 7500 | 44 | 5.9 | 39 | 0 | 0 | 0 | 18 |
| RTTY | contest overs | -3.3 | 6100 | 12 | 2.0 | 12 | 0 | 0 | 0 | 11 |
| RTTY | contest macros | -3.4 | 2300 | 10 | 4.3 | 1 | 0 | 0 | 2 | 10 |
| RTTY | free text | -3.7 | 5200 | 47 | 9.0 | 47 | 0 | 0 | 0 | 45 |

JTTY split messages starting within 3% of a decoder search-window boundary: 6 of 6, against 5.8% of all 9100 strong-signal JTTY trials (one-sided binomial p = 3.9e-08).
