# Strong-signal failures

Trials at least 3 dB above each series' 90% decode point that were not a clean copy. *missed*: units not copied; *split*: JTTY printed every frame but over several lines; *no_eom*: JTTY printed every frame in one line without its end-of-message; *ambiguous*: a contest field also printed a conflicting value; *false_text*: wrong text printed.

| Mode | Class | From SNR | Trials | Failed | Per 1000 | Missed | Split | No EOM | Ambiguous | False text |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FT4 | QSO messages | -2.6 | 26000 | 553 | 21.3 | 550 | 0 | 0 | 0 | 3 |
| JTTY | contest overs | -7.7 | 3700 | 45 | 12.2 | 35 | 4 | 0 | 0 | 8 |
| JTTY | contest macros | -8.3 | 4700 | 39 | 8.3 | 21 | 0 | 0 | 0 | 18 |
| JTTY | free text | -7.2 | 19800 | 1797 | 90.8 | 1202 | 447 | 0 | 0 | 167 |

Not measured (no 90% point, or no trials at the cut): FT8 QSO messages (from +47.4 dB); PSK31 contest overs (no 90% point found); PSK31 contest macros (no 90% point found); PSK31 free text (no 90% point found); RTTY contest overs (from +16.3 dB); RTTY contest macros (from +16.8 dB); RTTY free text (from +14.6 dB).

JTTY split messages starting within 3% of a decoder search-window boundary: 126 of 451, against 5.3% of all 23500 strong-signal JTTY trials (one-sided binomial p = 3.1e-54).
