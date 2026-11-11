# Decode thresholds

SNR in a 2500 Hz reference bandwidth (true SNR at the antenna). The 80% point is read from the measured decode curve, fitted to never fall as SNR rises, with bootstrap 95% intervals; '-' means not reached by +10 dB. *Reported* is the decoders' mean report (censored-normal fit where reports hit the floor) at the grid point nearest the 80% point.

Eb/N0 = SNR + 10*log10(2500 Hz / net bit rate), with JTTY 34 information bits per 1.888 s frame, FT8 and FT4 77 bits per 12.64 s / 5.04 s transmission, RTTY 5 data bits per 7.5-bit character at 45.45 baud (start, stop and shift characters count as overhead, not information), and PSK31 5 bits per character at 31.25 baud with the mean Varicode length (gap included) of the text each series sent: lower-case free text averages 6.73 bits per character, contest overs in capitals about 9.

Trials are transmissions; units are what Decode % counts: 5-character groups of the sent text (a callsign's worth; a final 1-4 characters form a short group of their own) printed exactly, as one unbroken piece, for every mode (an FT8 or FT4 message gives all its groups or none). Decode intervals come from a bootstrap over whole trials, so groups sharing a transmission are not treated as independent; every Copy % interval is a Wilson interval over trials. A contest-macro series counts one unit per whole macro, copied only when every field printed exactly in some repetition and none printed a conflicting value (ambiguous).

| Series | Unit | Trials | Units | 80% (dB) | 95% CI | Eb/N0 at 80% | Reported at 80% |
|---|---|---:|---:|---:|---|---:|---:|
| FT8 | 5-char groups | 23400 | 91404 | -17.5 | -17.6 to -17.5 | 8.6 | -17.6 |
| FT4 | 5-char groups | 53500 | 209112 | -13.4 | -13.6 to -13.3 | 8.7 | -13.8 |
| JTTY | 5-char groups | 126000 | 546322 | -10.8 | -10.9 to -10.7 | 10.7 | -11.1 |
| JTTY contest overs | 5-char groups | 63000 | 139890 | -11.5 | -11.7 to -11.0 | 9.9 | -11.6 |
| JTTY contest macros | macros | 47800 | 47800 | -12.3 | -12.5 to -12.2 | 9.1 | -12.4 |
| JTTY free text | 5-char groups | 63000 | 406432 | -10.6 | -10.7 to -10.5 | 10.8 | -10.7 |
| JTTY single-frame contest | 5-char groups | 52492 | 108375 | -11.8 | -12.0 to -11.6 | 9.6 | -12.1 |
| PSK31 | 5-char groups | 116500 | 509459 | - | - to - | - | - |
| PSK31 contest overs | 5-char groups | 57300 | 127243 | - | - to - | - | - |
| PSK31 contest macros | macros | 57300 | 57300 | - | - to - | - | - |
| PSK31 free text | 5-char groups | 59200 | 382216 | - | - to - | - | - |
| RTTY | 5-char groups | 91800 | 406664 | 8.3 | 7.8 to 8.6 | 27.5 | 6.7 |
| RTTY contest overs | 5-char groups | 44000 | 97817 | 9.3 | 9.1 to 9.6 | 28.4 | 7.3 |
| RTTY contest macros | macros | 47800 | 47800 | - | - to - | - | - |
| RTTY free text | 5-char groups | 47800 | 308847 | 7.4 | 7.0 to 8.2 | 26.6 | 5.9 |
