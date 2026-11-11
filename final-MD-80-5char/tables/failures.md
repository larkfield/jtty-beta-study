# Strong-signal failures

Trials at least 3 dB above each series' 90% decode point that were not a clean copy. *missed*: units not copied; *split*: JTTY printed every frame but over several lines; *no_eom*: JTTY printed every frame in one line without its end-of-message; *ambiguous*: a contest field also printed a conflicting value; *false_text*: wrong text printed.

| Mode | Class | From SNR | Trials | Failed | Per 1000 | Missed | Split | No EOM | Ambiguous | False text |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FT8 | QSO messages | -14.2 | 3400 | 5 | 1.5 | 0 | 0 | 0 | 0 | 5 |
| FT4 | QSO messages | -9.8 | 9600 | 92 | 9.6 | 90 | 0 | 0 | 0 | 2 |
| JTTY | contest overs | -6.0 | 17800 | 359 | 20.2 | 333 | 5 | 0 | 0 | 27 |
| JTTY | contest macros | -7.5 | 21700 | 223 | 10.3 | 190 | 0 | 0 | 0 | 33 |
| JTTY | free text | -5.0 | 31000 | 3235 | 104.4 | 3020 | 123 | 0 | 0 | 117 |
| PSK31 | free text | +9.0 | 2000 | 1075 | 537.5 | 1055 | 0 | 0 | 0 | 814 |

Not measured (no 90% point, or no trials at the cut): PSK31 contest overs (from +10.2 dB); PSK31 contest macros (from +11.8 dB); RTTY contest overs (from +13.9 dB); RTTY contest macros (from +14.6 dB); RTTY free text (from +12.4 dB).

JTTY split messages starting within 3% of a decoder search-window boundary: 124 of 128, against 5.5% of all 48800 strong-signal JTTY trials (one-sided binomial p = 1.1e-149).
