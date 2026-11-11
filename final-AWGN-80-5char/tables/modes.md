# Modes

| Parameter | FT8 | FT4 | JTTY | PSK31 | RTTY |
|---|---|---|---|---|---|
| Modulation | 8-GFSK | 4-GFSK | 4-GFSK | Differential BPSK, raised-cosine reversals | 2-FSK (continuous phase) |
| Gaussian smoothing (BT) | 2 | 1 | 2 | - | - |
| Tones | 8 | 4 | 4 | 1 | 2 |
| Tone spacing (Hz) | 6.25 | 20.833 | 31.25 | - | 170 |
| Keying rate (baud) | 6.25 | 20.833 | 31.25 | 31.25 | 45.45 |
| Bits per symbol | 3 | 2 | 2 | 1 | 1 |
| Symbols | 79 (58 data + 21 sync: three 7x7 Costas arrays) | 105 (87 data + 16 sync: four 4x4 Costas arrays + 2 ramp) | 59 per frame (13 sync + 46 data) | Varicode: 1-10 bits + 2-bit gap per character (6.7 on average for lower-case text) | 7.5 per character (1 start + 5 data + 1.5 stop) |
| Frame / transmission (s) | 12.64 | 5.04 | 1.888 | 0.215 | 0.165 |
| T/R slot (s) | 15 | 7.5 | asynchronous | asynchronous | asynchronous |
| Information bits | 77 | 77 | 34 | 5 | 5 |
| CRC bits | 14 | 14 | 12 | 0 | 0 |
| FEC | LDPC (174,91) | LDPC (174,91) | Tail-biting convolutional, K=10, r=1/2 (92,46) | None | None |
| Gross bit rate (bit/s) | 18.75 | 41.667 | 62.5 | 31.25 | 45.45 |
| Net bit rate (bit/s) | 6.092 | 15.278 | 18.008 | 23.205 | 30.3 |
| Nominal bandwidth (Hz) | 50 | 83.3 | 127 | 60 | 250 |
| Measured 99% bandwidth (Hz) | 49.8 | 79.5 | 126.3 | 45.4 | 254.2 |
| Synchronization | Timed 15 s T/R periods (UTC); Costas sync | Timed 7.5 s T/R periods (UTC); Costas sync | Asynchronous; 13-symbol sync per frame | Asynchronous; carrier and symbol timing tracked continuously | Asynchronous; start/stop bits per character |
