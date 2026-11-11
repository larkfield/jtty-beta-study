# Decode thresholds

SNR in a 2500 Hz reference bandwidth (true SNR at the antenna). The 80% point is read from the measured decode curve, fitted to never fall as SNR rises, with bootstrap 95% intervals; '-' means not reached by +10 dB. *Reported* is the decoders' mean report (censored-normal fit where reports hit the floor) at the grid point nearest the 80% point.

Eb/N0 = SNR + 10*log10(2500 Hz / net bit rate), with JTTY 34 information bits per 1.888 s frame, FT8 and FT4 77 bits per 12.64 s / 5.04 s transmission, RTTY 5 data bits per 7.5-bit character at 45.45 baud (start, stop and shift characters count as overhead, not information), and PSK31 5 bits per character at 31.25 baud with the mean Varicode length (gap included) of the text each series sent: lower-case free text averages 6.73 bits per character, contest overs in capitals about 9.

Trials are transmissions; units are what Decode % counts: 5-character groups of the sent text (a callsign's worth; a final 1-4 characters form a short group of their own) printed exactly, as one unbroken piece, for every mode (an FT8 or FT4 message gives all its groups or none). Decode intervals come from a bootstrap over whole trials, so groups sharing a transmission are not treated as independent; every Copy % interval is a Wilson interval over trials. A contest-macro series counts one unit per whole macro, copied only when every field printed exactly in some repetition and none printed a conflicting value (ambiguous).

| Series | Unit | Trials | Units | 80% (dB) | 95% CI | Eb/N0 at 80% | Reported at 80% |
|---|---|---:|---:|---:|---|---:|---:|
| FT8 | 5-char groups | 23400 | 91404 | -17.7 | -17.8 to -17.6 | 8.4 | -17.5 |
| FT4 | 5-char groups | 30700 | 119957 | -13.9 | -14.0 to -13.8 | 8.3 | -14.3 |
| JTTY | 5-char groups | 110900 | 521335 | -10.2 | -10.3 to -10.0 | 11.3 | -10.1 |
| JTTY contest overs | 5-char groups | 45900 | 102030 | -11.0 | -11.2 to -10.7 | 10.5 | -11.2 |
| JTTY contest macros | macros | 49700 | 49700 | -12.0 | -12.2 to -11.7 | 9.5 | -11.9 |
| JTTY free text | 5-char groups | 65000 | 419305 | -10.0 | -10.1 to -9.8 | 11.5 | -10.1 |
| JTTY single-frame contest | 5-char groups | 38261 | 79120 | -11.4 | -11.6 to -11.0 | 10.0 | -11.7 |
| PSK31 | 5-char groups | 122200 | 537941 | 1.8 | 1.4 to 2.2 | 22.5 | -8.2 |
| PSK31 contest overs | 5-char groups | 59200 | 131509 | 2.3 | 1.3 to 3.4 | 24.1 | -8.0 |
| PSK31 contest macros | macros | 59200 | 59200 | 6.3 | 5.7 to 8.6 | 28.0 | -8.1 |
| PSK31 free text | 5-char groups | 63000 | 406432 | 1.8 | 1.0 to 2.2 | 22.1 | -8.3 |
| RTTY | 5-char groups | 97500 | 427349 | 7.4 | 7.1 to 7.7 | 26.6 | 6.1 |
| RTTY contest overs | 5-char groups | 47800 | 106299 | 9.1 | 8.3 to 9.6 | 28.3 | 7.2 |
| RTTY contest macros | macros | 45900 | 45900 | - | - to - | - | - |
| RTTY free text | 5-char groups | 49700 | 321050 | 7.1 | 6.8 to 7.5 | 26.3 | 5.7 |
