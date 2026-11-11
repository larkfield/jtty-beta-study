# Decode thresholds

SNR in a 2500 Hz reference bandwidth (true SNR at the antenna). The 80% point is read from the measured decode curve, fitted to never fall as SNR rises, with bootstrap 95% intervals; '-' means not reached by +10 dB. *Reported* is the decoders' mean report (censored-normal fit where reports hit the floor) at the grid point nearest the 80% point.

Eb/N0 = SNR + 10*log10(2500 Hz / net bit rate), with JTTY 34 information bits per 1.888 s frame, FT8 and FT4 77 bits per 12.64 s / 5.04 s transmission, RTTY 5 data bits per 7.5-bit character at 45.45 baud (start, stop and shift characters count as overhead, not information), and PSK31 5 bits per character at 31.25 baud with the mean Varicode length (gap included) of the text each series sent: lower-case free text averages 6.73 bits per character, contest overs in capitals about 9.

Trials are transmissions; units are what Decode % counts: 5-character groups of the sent text (a callsign's worth; a final 1-4 characters form a short group of their own) printed exactly, as one unbroken piece, for every mode (an FT8 or FT4 message gives all its groups or none). Decode intervals come from a bootstrap over whole trials, so groups sharing a transmission are not treated as independent; every Copy % interval is a Wilson interval over trials. A contest-macro series counts one unit per whole macro, copied only when every field printed exactly in some repetition and none printed a conflicting value (ambiguous).

| Series | Unit | Trials | Units | 80% (dB) | 95% CI | Eb/N0 at 80% | Reported at 80% |
|---|---|---:|---:|---:|---|---:|---:|
| FT8 | 5-char groups | 19700 | 76935 | -20.4 | -20.5 to -20.3 | 5.7 | -20.3 |
| FT4 | 5-char groups | 23300 | 91008 | -16.9 | -17.0 to -16.9 | 5.2 | -17.2 |
| JTTY | 5-char groups | 48100 | 203997 | -15.0 | -15.1 to -15.0 | 6.4 | -14.9 |
| JTTY contest overs | 5-char groups | 25000 | 55363 | -15.0 | -15.1 to -14.9 | 6.4 | -14.9 |
| JTTY contest macros | macros | 23100 | 23100 | -15.4 | -15.5 to -15.3 | 6.1 | -15.3 |
| JTTY free text | 5-char groups | 23100 | 148634 | -15.0 | -15.1 to -15.0 | 6.4 | -14.9 |
| JTTY single-frame contest | 5-char groups | 20894 | 43048 | -15.0 | -15.1 to -14.9 | 6.4 | -14.9 |
| PSK31 | 5-char groups | 61400 | 274380 | -11.5 | -11.5 to -11.4 | 9.2 | -11.2 |
| PSK31 contest overs | 5-char groups | 28800 | 63985 | -11.5 | -11.6 to -11.4 | 10.3 | -11.1 |
| PSK31 contest macros | macros | 30700 | 30700 | -11.3 | -11.5 to -11.0 | 10.3 | -11.2 |
| PSK31 free text | 5-char groups | 32600 | 210395 | -11.5 | -11.5 to -11.4 | 8.8 | -11.3 |
| RTTY | 5-char groups | 55700 | 238375 | -7.4 | -7.5 to -7.4 | 11.8 | -6.9 |
| RTTY contest overs | 5-char groups | 28800 | 64213 | -7.3 | -7.4 to -7.2 | 11.9 | -6.9 |
| RTTY contest macros | macros | 25000 | 25000 | -7.1 | -7.2 to -6.9 | 12.1 | -6.5 |
| RTTY free text | 5-char groups | 26900 | 174162 | -7.4 | -7.5 to -7.4 | 11.7 | -6.9 |
