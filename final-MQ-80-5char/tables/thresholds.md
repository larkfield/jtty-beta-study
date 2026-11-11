# Decode thresholds

SNR in a 2500 Hz reference bandwidth (true SNR at the antenna). The 80% point is read from the measured decode curve, fitted to never fall as SNR rises, with bootstrap 95% intervals; '-' means not reached by +10 dB. *Reported* is the decoders' mean report (censored-normal fit where reports hit the floor) at the grid point nearest the 80% point.

Eb/N0 = SNR + 10*log10(2500 Hz / net bit rate), with JTTY 34 information bits per 1.888 s frame, FT8 and FT4 77 bits per 12.64 s / 5.04 s transmission, RTTY 5 data bits per 7.5-bit character at 45.45 baud (start, stop and shift characters count as overhead, not information), and PSK31 5 bits per character at 31.25 baud with the mean Varicode length (gap included) of the text each series sent: lower-case free text averages 6.73 bits per character, contest overs in capitals about 9.

Trials are transmissions; units are what Decode % counts: 5-character groups of the sent text (a callsign's worth; a final 1-4 characters form a short group of their own) printed exactly, as one unbroken piece, for every mode (an FT8 or FT4 message gives all its groups or none). Decode intervals come from a bootstrap over whole trials, so groups sharing a transmission are not treated as independent; every Copy % interval is a Wilson interval over trials. A contest-macro series counts one unit per whole macro, copied only when every field printed exactly in some repetition and none printed a conflicting value (ambiguous).

| Series | Unit | Trials | Units | 80% (dB) | 95% CI | Eb/N0 at 80% | Reported at 80% |
|---|---|---:|---:|---:|---|---:|---:|
| FT8 | 5-char groups | 27300 | 106628 | -18.7 | -18.8 to -18.5 | 7.4 | -18.3 |
| FT4 | 5-char groups | 25000 | 97709 | -16.4 | -16.5 to -16.3 | 5.8 | -16.7 |
| JTTY | 5-char groups | 84500 | 431630 | -11.4 | -11.6 to -11.3 | 10.0 | -11.3 |
| JTTY contest overs | 5-char groups | 26900 | 59640 | -14.7 | -14.7 to -14.6 | 6.8 | -14.4 |
| JTTY contest macros | macros | 36500 | 36500 | -14.6 | -14.9 to -14.4 | 6.8 | -14.0 |
| JTTY free text | 5-char groups | 57600 | 371990 | -10.1 | -10.3 to -9.9 | 11.4 | -10.0 |
| JTTY single-frame contest | 5-char groups | 22462 | 46329 | -14.8 | -15.0 to -14.7 | 6.6 | -14.9 |
| PSK31 | 5-char groups | 112700 | 516816 | -9.2 | -9.5 to -8.9 | 11.4 | -9.1 |
| PSK31 contest overs | 5-char groups | 49700 | 110384 | -10.8 | -11.0 to -10.5 | 11.0 | -10.7 |
| PSK31 contest macros | macros | 53500 | 53500 | -9.2 | -9.5 to -8.8 | 12.5 | -9.0 |
| PSK31 free text | 5-char groups | 63000 | 406432 | -8.6 | -8.8 to -8.5 | 11.7 | -8.9 |
| RTTY | 5-char groups | 105100 | 468145 | -5.2 | -5.4 to -5.1 | 13.9 | -4.5 |
| RTTY contest overs | 5-char groups | 49700 | 110485 | -6.5 | -6.6 to -6.4 | 12.6 | -5.9 |
| RTTY contest macros | macros | 51600 | 51600 | -5.9 | -6.1 to -5.6 | 13.3 | -5.5 |
| RTTY free text | 5-char groups | 55400 | 357660 | -4.6 | -4.8 to -4.4 | 14.6 | -4.0 |
