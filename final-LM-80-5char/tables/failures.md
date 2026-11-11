# Strong-signal failures

Trials at least 3 dB above each series' 90% decode point that were not a clean copy. *missed*: units not copied; *split*: JTTY printed every frame but over several lines; *no_eom*: JTTY printed every frame in one line without its end-of-message; *ambiguous*: a contest field also printed a conflicting value; *false_text*: wrong text printed.

| Mode | Class | From SNR | Trials | Failed | Per 1000 | Missed | Split | No EOM | Ambiguous | False text |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FT8 | QSO messages | -14.0 | 2400 | 4 | 1.7 | 0 | 0 | 0 | 0 | 4 |
| FT4 | QSO messages | -8.0 | 29400 | 358 | 12.2 | 357 | 0 | 0 | 0 | 1 |
| JTTY | contest overs | -5.5 | 31000 | 542 | 17.5 | 493 | 9 | 0 | 0 | 43 |
| JTTY | contest macros | -8.0 | 20800 | 231 | 11.1 | 197 | 0 | 0 | 0 | 35 |
| JTTY | free text | -4.1 | 29000 | 3414 | 117.7 | 3185 | 134 | 0 | 0 | 125 |

Not measured (no 90% point, or no trials at the cut): PSK31 contest overs (from +15.6 dB); PSK31 contest macros (no 90% point found); PSK31 free text (from +13.8 dB); RTTY contest overs (from +14.6 dB); RTTY contest macros (from +15.0 dB); RTTY free text (from +12.8 dB).

JTTY split messages starting within 3% of a decoder search-window boundary: 136 of 143, against 5.6% of all 60000 strong-signal JTTY trials (one-sided binomial p = 1.6e-159).
