# Decode thresholds

SNR in a 2500 Hz reference bandwidth (true SNR at the antenna). The 80% point is read from the measured decode curve, fitted to never fall as SNR rises, with bootstrap 95% intervals; '-' means not reached by +10 dB. *Reported* is the decoders' mean report (censored-normal fit where reports hit the floor) at the grid point nearest the 80% point.

Eb/N0 = SNR + 10*log10(2500 Hz / net bit rate), with JTTY 34 information bits per 1.888 s frame, FT8 and FT4 77 bits per 12.64 s / 5.04 s transmission, RTTY 5 data bits per 7.5-bit character at 45.45 baud (start, stop and shift characters count as overhead, not information), and PSK31 5 bits per character at 31.25 baud with the mean Varicode length (gap included) of the text each series sent: lower-case free text averages 6.73 bits per character, contest overs in capitals about 9.

Trials are transmissions; units are what Decode % counts: 5-character groups of the sent text (a callsign's worth; a final 1-4 characters form a short group of their own) printed exactly, as one unbroken piece, for every mode (an FT8 or FT4 message gives all its groups or none). Decode intervals come from a bootstrap over whole trials, so groups sharing a transmission are not treated as independent; every Copy % interval is a Wilson interval over trials. A contest-macro series counts one unit per whole macro, copied only when every field printed exactly in some repetition and none printed a conflicting value (ambiguous).

| Series | Unit | Trials | Units | 80% (dB) | 95% CI | Eb/N0 at 80% | Reported at 80% |
|---|---|---:|---:|---:|---|---:|---:|
| FT8 | 5-char groups | 25300 | 98837 | -18.1 | -18.1 to -18.0 | 8.1 | -17.9 |
| FT4 | 5-char groups | 26900 | 105156 | -14.5 | -14.6 to -14.4 | 7.6 | -14.8 |
| JTTY | 5-char groups | 107200 | 465510 | -9.5 | -9.6 to -9.3 | 12.0 | -9.6 |
| JTTY contest overs | 5-char groups | 53500 | 118749 | -10.5 | -10.8 to -10.3 | 11.0 | -10.5 |
| JTTY contest macros | macros | 42200 | 42200 | -11.7 | -11.9 to -11.4 | 9.7 | -11.2 |
| JTTY free text | 5-char groups | 53700 | 346761 | -9.1 | -9.2 to -9.0 | 12.3 | -9.1 |
| JTTY single-frame contest | 5-char groups | 44644 | 92190 | -11.1 | -11.5 to -10.8 | 10.3 | -11.2 |
| PSK31 | 5-char groups | 122200 | 537941 | -3.1 | -3.3 to -2.9 | 17.5 | -4.9 |
| PSK31 contest overs | 5-char groups | 59200 | 131509 | -3.0 | -3.2 to -2.2 | 18.7 | -4.8 |
| PSK31 contest macros | macros | 63000 | 63000 | -1.0 | -1.6 to -0.3 | 20.6 | -3.4 |
| PSK31 free text | 5-char groups | 63000 | 406432 | -3.2 | -3.4 to -3.0 | 17.1 | -5.0 |
| RTTY | 5-char groups | 105100 | 460090 | 3.0 | 2.5 to 3.2 | 22.1 | 3.2 |
| RTTY contest overs | 5-char groups | 51600 | 114637 | 2.7 | 2.3 to 3.2 | 21.8 | 2.8 |
| RTTY contest macros | macros | 49700 | 49700 | 4.7 | 4.1 to 5.2 | 23.8 | 4.6 |
| RTTY free text | 5-char groups | 53500 | 345453 | 3.1 | 2.7 to 3.5 | 22.2 | 3.2 |
