# Strong-signal failures

Trials at least 3 dB above each series' 90% decode point that were not a clean copy. *missed*: units not copied; *split*: JTTY printed every frame but over several lines; *no_eom*: JTTY printed every frame in one line without its end-of-message; *ambiguous*: a contest field also printed a conflicting value; *false_text*: wrong text printed.

| Mode | Class | From SNR | Trials | Failed | Per 1000 | Missed | Split | No EOM | Ambiguous | False text |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FT8 | QSO messages | -14.5 | 5300 | 4 | 0.8 | 1 | 0 | 0 | 0 | 3 |
| FT4 | QSO messages | -10.6 | 5900 | 18 | 3.1 | 15 | 0 | 0 | 0 | 3 |
| JTTY | contest overs | -5.7 | 22500 | 388 | 17.2 | 374 | 1 | 0 | 0 | 16 |
| JTTY | contest macros | -7.0 | 11200 | 175 | 15.6 | 155 | 0 | 0 | 1 | 21 |
| JTTY | free text | -4.0 | 15700 | 1749 | 111.4 | 1645 | 50 | 0 | 0 | 67 |
| PSK31 | contest overs | +5.3 | 10000 | 1480 | 148.0 | 1443 | 0 | 0 | 0 | 872 |
| PSK31 | contest macros | +6.8 | 7000 | 1174 | 167.7 | 446 | 0 | 0 | 164 | 985 |
| PSK31 | free text | +4.1 | 12000 | 3428 | 285.7 | 3390 | 0 | 0 | 0 | 2265 |
| RTTY | contest overs | +9.5 | 2000 | 416 | 208.0 | 401 | 0 | 0 | 0 | 363 |
| RTTY | free text | +9.3 | 2000 | 873 | 436.5 | 849 | 0 | 0 | 0 | 743 |

Not measured (no 90% point, or no trials at the cut): RTTY contest macros (from +10.8 dB).

JTTY split messages starting within 3% of a decoder search-window boundary: 45 of 51, against 5.6% of all 38200 strong-signal JTTY trials (one-sided binomial p = 5.0e-50).
