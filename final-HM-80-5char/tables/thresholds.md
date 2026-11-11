# Decode thresholds

SNR in a 2500 Hz reference bandwidth (true SNR at the antenna). The 80% point is read from the measured decode curve, fitted to never fall as SNR rises, with bootstrap 95% intervals; '-' means not reached by +10 dB. *Reported* is the decoders' mean report (censored-normal fit where reports hit the floor) at the grid point nearest the 80% point.

Eb/N0 = SNR + 10*log10(2500 Hz / net bit rate), with JTTY 34 information bits per 1.888 s frame, FT8 and FT4 77 bits per 12.64 s / 5.04 s transmission, RTTY 5 data bits per 7.5-bit character at 45.45 baud (start, stop and shift characters count as overhead, not information), and PSK31 5 bits per character at 31.25 baud with the mean Varicode length (gap included) of the text each series sent: lower-case free text averages 6.73 bits per character, contest overs in capitals about 9.

Trials are transmissions; units are what Decode % counts: 5-character groups of the sent text (a callsign's worth; a final 1-4 characters form a short group of their own) printed exactly, as one unbroken piece, for every mode (an FT8 or FT4 message gives all its groups or none). Decode intervals come from a bootstrap over whole trials, so groups sharing a transmission are not treated as independent; every Copy % interval is a Wilson interval over trials. A contest-macro series counts one unit per whole macro, copied only when every field printed exactly in some repetition and none printed a conflicting value (ambiguous).

| Series | Unit | Trials | Units | 80% (dB) | 95% CI | Eb/N0 at 80% | Reported at 80% |
|---|---|---:|---:|---:|---|---:|---:|
| FT8 | 5-char groups | 49700 | 194300 | - | - to - | - | - |
| FT4 | 5-char groups | 53500 | 209138 | -9.4 | -9.6 to -9.3 | 12.7 | -10.0 |
| JTTY | 5-char groups | 67100 | 327445 | -11.3 | -11.3 to -11.2 | 10.2 | -12.4 |
| JTTY contest overs | 5-char groups | 25000 | 55458 | -11.5 | -11.6 to -11.3 | 10.0 | -12.5 |
| JTTY contest macros | macros | 25000 | 25000 | -12.0 | -12.1 to -11.9 | 9.4 | -12.7 |
| JTTY free text | 5-char groups | 42100 | 271987 | -11.2 | -11.2 to -11.2 | 10.2 | -12.0 |
| JTTY single-frame contest | 5-char groups | 20795 | 42845 | -11.6 | -11.7 to -11.4 | 9.9 | -12.5 |
| PSK31 | 5-char groups | 19600 | 84986 | - | - to - | - | - |
| PSK31 contest overs | 5-char groups | 9800 | 21798 | - | - to - | - | - |
| PSK31 contest macros | macros | 9800 | 9800 | - | - to - | - | - |
| PSK31 free text | 5-char groups | 9800 | 63188 | - | - to - | - | - |
| RTTY | 5-char groups | 88000 | 382156 | - | - to - | - | - |
| RTTY contest overs | 5-char groups | 44000 | 97817 | - | - to - | - | - |
| RTTY contest macros | macros | 38300 | 38300 | - | - to - | - | - |
| RTTY free text | 5-char groups | 44000 | 284339 | - | - to - | - | - |
