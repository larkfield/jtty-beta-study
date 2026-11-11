/*
 * _psk31: amateur PSK31 (BPSK31, G3PLX Varicode) modulator and reference demodulator.
 *
 * This is the PSK31 baseline of the JTTY SNR study. Python adds the band noise and the receiver filter; this module
 * generates the clean transmitted audio and decodes the received audio, the way wsjt-codec does for the WSJT modes.
 *
 * Transmit: each character is its Varicode followed by the "00" separator. A 0 bit reverses the carrier phase, a 1
 * keeps it. The waveform is a sum of raised-cosine pulses two symbols long, one per symbol instant, so the envelope
 * follows a cosine through zero across a reversal and stays constant otherwise; the first and last pulses give the
 * transmission a one-symbol rise and fall. A preamble of phase reversals and a postamble of steady carrier frame the
 * text, as fldigi sends them.
 *
 * Receive: the audio is mixed to baseband at the nominal (clicked) carrier and decimated by 16 through a two-stage
 * boxcar. As fldigi snaps a click on the waterfall to the nearby signal, the carrier is first located within
 * SNAP_RANGE_HZ as the peak of the squared baseband's spectrum. The baseband is then rotated by an AFC oscillator
 * starting there and passed through a filter matched to the transmit pulse. Symbol timing follows the peak of the
 * averaged matched-filter power over the symbol period (an early-late gate on fldigi-style per-phase averages). Bits
 * come from differential detection, the sign of Re(y[k] conj(y[k-1])). The AFC steers on the symbol-to-symbol phase
 * error with the modulation removed by squaring, weighted by how consistent that error has been (the squared magnitude
 * of the averaged squared phasor, the quality a PSK decoder's DCD uses), and otherwise relaxes to the snapped
 * frequency, so band noise barely moves it. There is no print squelch. The decoder also estimates the SNR in the 2500 Hz reference bandwidth from the symbol samples
 * against a carrier phase recovered by squaring: the signal amplitude from the in-phase parts, modeling the matched
 * filter's known inter-symbol interference, and the noise from the quadrature parts.
 *
 * Audio buffers are passed through the buffer protocol (int16, float32 or float64, C-contiguous), so the module
 * does not depend on numpy.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <math.h>
#include <stdlib.h>
#include <string.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* Decimation factor of the receiver's baseband (12000 Hz -> 750 Hz at the study's sample rate). */
#define DECIMATION 16

/* Longest Varicode (bits, without the "00" separator); longer runs of bits are not a character. */
#define MAX_CODE_BITS 10

/* Symbols on each side of a symbol whose squared samples set its carrier-phase reference in the SNR estimate. */
#define PHASE_SPAN 8

/* Per-symbol weight of a new sample in the timing averages and the AFC quality average. */
#define TIMING_ALPHA (1.0 / 16.0)
#define QUALITY_ALPHA (1.0 / 8.0)

/* Click-to-snap: how far from the tuned frequency (Hz) the carrier is searched for, and the search step (Hz). */
#define SNAP_RANGE_HZ 10.0
#define SNAP_STEP_HZ 0.05

/* AFC: loop gain per symbol (scaled by the squared quality), the per-symbol relaxation toward the snapped frequency
 * (scaled by one minus it), and the largest correction from the snapped frequency (Hz). */
#define AFC_GAIN 0.1
#define AFC_RELAX (1.0 / 32.0)
#define AFC_LIMIT_HZ 3.0

/* Early-late gate: the timing moves one decimated sample per symbol when the averaged power two samples late and two
 * samples early differ by more than this fraction of the power at the current instant. */
#define TIMING_DEADBAND 0.02

/* G3PLX Varicode, indexed by ASCII code. */
static const char *const VARICODE[128] = {
    "1010101011", "1011011011", "1011101101", "1101110111", "1011101011", "1101011111", "1011101111", "1011111101",
    "1011111111", "11101111",   "11101",      "1101101111", "1011011101", "11111",      "1101110101", "1110101011",
    "1011110111", "1011110101", "1110101101", "1110101111", "1101011011", "1101101011", "1101101101", "1101010111",
    "1101111011", "1101111101", "1110110111", "1101010101", "1101011101", "1110111011", "1011111011", "1101111111",
    "1",          "111111111",  "101011111",  "111110101",  "111011011",  "1011010101", "1010111011", "101111111",
    "11111011",   "11110111",   "101101111",  "111011111",  "1110101",    "110101",     "1010111",    "110101111",
    "10110111",   "10111101",   "11101101",   "11111111",   "101110111",  "101011011",  "101101011",  "110101101",
    "110101011",  "110110111",  "11110101",   "110111101",  "111101101",  "1010101",    "111010111",  "1010101111",
    "1010111101", "1111101",    "11101011",   "10101101",   "10110101",   "1110111",    "11011011",   "11111101",
    "101010101",  "1111111",    "111111101",  "101111101",  "11010111",   "10111011",   "11011101",   "10101011",
    "11010101",   "111011101",  "10101111",   "1101111",    "1101101",    "101010111",  "110110101",  "101011101",
    "101110101",  "101111011",  "1010101101", "111110111",  "111101111",  "111111011",  "1010111111", "101101101",
    "1011011111", "1011",       "1011111",    "101111",     "101101",     "11",         "111101",     "1011011",
    "101011",     "1101",       "111101011",  "10111111",   "11011",      "111011",     "1111",       "111",
    "111111",     "110111111",  "10101",      "10111",      "101",        "110111",     "1111011",    "1101011",
    "11011111",   "1011101",    "111010101",  "1010110111", "110111011",  "1010110101", "1011010111", "1110110101",
};

/* Character of each Varicode value (the code read as a binary number; codes start with 1), or -1. */
static short DECODE_TABLE[1 << MAX_CODE_BITS];

/* A read-only view of an audio buffer converted to doubles. */
typedef struct
{
    double *samples;
    Py_ssize_t count;
} Audio;

/* A growable byte array of bits (0/1). */
typedef struct
{
    unsigned char *items;
    size_t count;
    size_t capacity;
} BitList;

/* Overall response of the receive chain, for the SNR estimate (see chain_constants). */
typedef struct
{
    double peak;        /* matched-filter output for one isolated unit transmit pulse, at its best instant */
    double isi;         /* output from each neighboring pulse at that instant, as a fraction of `peak` */
    double noise_gain;  /* output variance per unit input variance of complex white noise */
} Chain;

/* Decimated complex baseband. */
typedef struct
{
    double *re;
    double *im;
    Py_ssize_t count;
} Baseband;


/*
 * Fill DECODE_TABLE from VARICODE. Called once at module initialization.
 */
static void
build_decode_table(void)
{
    for (int i = 0; i < (1 << MAX_CODE_BITS); i++)
    {
        DECODE_TABLE[i] = -1;
    }
    for (int c = 0; c < 128; c++)
    {
        int value = 0;
        for (const char *bit = VARICODE[c]; *bit; bit++)
        {
            value = (value << 1) | (*bit == '1');
        }
        DECODE_TABLE[value] = (short)c;
    }
}


/*
 * Append one bit to the list, growing it as needed.
 * Returns 0 on success and -1 (with a Python MemoryError set) on allocation failure.
 */
static int
bit_list_push(BitList *list, int bit)
{
    if (list->count == list->capacity)
    {
        size_t capacity = list->capacity ? list->capacity * 2 : 1024;
        unsigned char *items = realloc(list->items, capacity);
        if (items == NULL)
        {
            PyErr_NoMemory();
            return -1;
        }
        list->items = items;
        list->capacity = capacity;
    }
    list->items[list->count++] = (unsigned char)bit;
    return 0;
}


/*
 * Append the Varicode of every character of text, each followed by "00", to the list. If char_bits is not NULL,
 * char_bits[i] receives the index in the list of the first bit of text[i].
 * Returns 0 on success and -1 with a Python exception set (ValueError for a non-ASCII byte) on failure.
 */
static int
encode_text(const char *text, size_t text_len, BitList *bits, size_t *char_bits)
{
    for (size_t i = 0; i < text_len; i++)
    {
        unsigned char c = (unsigned char)text[i];
        if (c >= 128)
        {
            PyErr_Format(PyExc_ValueError, "byte 0x%02x of the text has no Varicode", c);
            return -1;
        }
        if (char_bits != NULL)
        {
            char_bits[i] = bits->count;
        }
        for (const char *bit = VARICODE[c]; *bit; bit++)
        {
            if (bit_list_push(bits, *bit == '1') < 0)
            {
                return -1;
            }
        }
        if (bit_list_push(bits, 0) < 0 || bit_list_push(bits, 0) < 0)
        {
            return -1;
        }
    }
    return 0;
}


/*
 * Release a buffer view and convert it to a newly allocated array of doubles.
 * Accepts C-contiguous int16 ('h'), float32 ('f') or float64 ('d') buffers.
 * Returns 0 on success and -1 with a Python exception set on failure; on success the caller frees audio->samples.
 */
static int
read_audio(PyObject *object, Audio *audio)
{
    Py_buffer view;
    if (PyObject_GetBuffer(object, &view, PyBUF_C_CONTIGUOUS | PyBUF_FORMAT) < 0)
    {
        return -1;
    }

    const char *format = view.format ? view.format : "B";
    if (format[0] == '<' || format[0] == '=' || format[0] == '@')
    {
        format++;
    }
    int kind = 0;
    if (strcmp(format, "h") == 0 && view.itemsize == 2)
    {
        kind = 1;
    }
    else if (strcmp(format, "f") == 0 && view.itemsize == 4)
    {
        kind = 2;
    }
    else if (strcmp(format, "d") == 0 && view.itemsize == 8)
    {
        kind = 3;
    }
    else
    {
        PyBuffer_Release(&view);
        PyErr_SetString(PyExc_TypeError, "audio must be a C-contiguous int16, float32 or float64 buffer");
        return -1;
    }
    Py_ssize_t count = view.len / view.itemsize;

    double *samples = malloc((size_t)(count > 0 ? count : 1) * sizeof(double));
    if (samples == NULL)
    {
        PyBuffer_Release(&view);
        PyErr_NoMemory();
        return -1;
    }
    for (Py_ssize_t i = 0; i < count; i++)
    {
        if (kind == 1)
        {
            samples[i] = ((const short *)view.buf)[i];
        }
        else if (kind == 2)
        {
            samples[i] = ((const float *)view.buf)[i];
        }
        else
        {
            samples[i] = ((const double *)view.buf)[i];
        }
    }
    PyBuffer_Release(&view);
    audio->samples = samples;
    audio->count = count;
    return 0;
}


/*
 * Check the sample rate and baud: the symbol must be a whole number of decimated samples, at least two.
 * Returns the decimated samples per symbol, or 0 with a Python ValueError set.
 */
static int
symbol_samples(double fs, double baud)
{
    if (!(fs > 0.0) || !(baud > 0.0))
    {
        PyErr_SetString(PyExc_ValueError, "invalid PSK31 timing parameters");
        return 0;
    }
    double per_symbol = fs / baud / DECIMATION;
    int rounded = (int)lround(per_symbol);
    if (rounded < 2 || fabs(per_symbol - rounded) > 1e-9)
    {
        PyErr_SetString(PyExc_ValueError, "fs / baud must be a whole multiple of 16 samples (at least 32)");
        return 0;
    }
    return rounded;
}


/* The transmit pulse (raised cosine two symbols long) at t samples into it, for sps samples per symbol. */
static double
pulse(double t, double sps)
{
    if (t < 0.0 || t >= 2.0 * sps)
    {
        return 0.0;
    }
    return 0.5 * (1.0 - cos(M_PI * t / sps));
}


/* Matched-filter tap i of the 2*td taps at the decimated rate (the transmit pulse at the decimated sample centers). */
static double
matched_tap(int i, int td)
{
    return 0.5 * (1.0 - cos(M_PI * ((double)i + 0.5) / (double)td));
}


/*
 * Mix audio to baseband at carrier_hz and decimate by DECIMATION through two cascaded boxcars of that length (unit
 * DC gain). Decimated sample m is centered on input sample m * DECIMATION.
 * Returns 0 on success and -1 with a Python MemoryError set; on success the caller frees out->re and out->im.
 */
static int
to_baseband(const Audio *audio, double fs, double carrier_hz, Baseband *out)
{
    Py_ssize_t n = audio->count;
    Py_ssize_t count = n / DECIMATION;
    size_t size = (size_t)(count > 0 ? count : 1);
    out->re = calloc(size, sizeof(double));
    out->im = calloc(size, sizeof(double));
    if (out->re == NULL || out->im == NULL)
    {
        free(out->re);
        free(out->im);
        PyErr_NoMemory();
        return -1;
    }
    out->count = count;

    /* Ring buffers of the last DECIMATION mixed samples and first-stage sums. */
    double ring1_re[DECIMATION] = {0}, ring1_im[DECIMATION] = {0};
    double ring2_re[DECIMATION] = {0}, ring2_im[DECIMATION] = {0};
    double sum1_re = 0.0, sum1_im = 0.0, sum2_re = 0.0, sum2_im = 0.0;
    double w = 2.0 * M_PI * carrier_hz / fs;
    double scale = 1.0 / ((double)DECIMATION * (double)DECIMATION);
    /* Output m is centered on input m*D; the two boxcars delay by D-1 samples, so it is taken at input m*D + D-1. */
    for (Py_ssize_t k = 0; k < n; k++)
    {
        double phase = fmod(w * (double)k, 2.0 * M_PI);
        double x = audio->samples[k];
        double re = x * cos(phase);
        double im = -x * sin(phase);
        int slot = (int)(k % DECIMATION);
        sum1_re += re - ring1_re[slot];
        sum1_im += im - ring1_im[slot];
        ring1_re[slot] = re;
        ring1_im[slot] = im;
        sum2_re += sum1_re - ring2_re[slot];
        sum2_im += sum1_im - ring2_im[slot];
        ring2_re[slot] = sum1_re;
        ring2_im[slot] = sum1_im;
        Py_ssize_t m = (k - (DECIMATION - 1)) / DECIMATION;
        if (k >= DECIMATION - 1 && (k - (DECIMATION - 1)) % DECIMATION == 0 && m < count)
        {
            out->re[m] = sum2_re * scale;
            out->im[m] = sum2_im * scale;
        }
    }
    return 0;
}


/*
 * Return the receive chain's response to the transmit pulse and its white-noise gain (see Chain), computed from the
 * same boxcars and matched filter the demodulator uses, as one filter at the input rate. The result for the last
 * symbol length asked for is cached.
 * Returns 0 on success and -1 with a Python MemoryError set.
 */
static int
chain_constants(int td, Chain *chain)
{
    static int cached_td = 0;
    static Chain cached;
    if (td == cached_td)
    {
        *chain = cached;
        return 0;
    }
    int sps = td * DECIMATION;
    int cic_len = 2 * DECIMATION - 1;
    int g_len = cic_len + (2 * td - 1) * DECIMATION;
    double *g = calloc((size_t)g_len, sizeof(double));
    if (g == NULL)
    {
        PyErr_NoMemory();
        return -1;
    }
    for (int i = 0; i < 2 * td; i++)
    {
        for (int j = 0; j < cic_len; j++)
        {
            double triangle = (double)(j < DECIMATION ? j + 1 : cic_len - j) / ((double)DECIMATION * DECIMATION);
            g[i * DECIMATION + j] += matched_tap(i, td) * triangle;
        }
    }

    double noise_gain = 0.0;
    for (int n = 0; n < g_len; n++)
    {
        noise_gain += g[n] * g[n];
    }
    /* Output for a pulse starting s samples before the filter's newest sample: sum over n of g[n] pulse(s - n). */
    double peak = 0.0, isi = 0.0;
    for (int s = 0; s < g_len + 2 * sps; s++)
    {
        double here = 0.0;
        for (int n = 0; n < g_len; n++)
        {
            here += g[n] * pulse((double)(s - n), (double)sps);
        }
        if (here > peak)
        {
            double early = 0.0, late = 0.0;
            for (int n = 0; n < g_len; n++)
            {
                early += g[n] * pulse((double)(s - n + sps), (double)sps);
                late += g[n] * pulse((double)(s - n - sps), (double)sps);
            }
            peak = here;
            isi = 0.5 * (early + late);
        }
    }
    free(g);
    chain->peak = peak;
    chain->isi = peak > 0.0 ? isi / peak : 0.0;
    chain->noise_gain = noise_gain;
    cached = *chain;
    cached_td = td;
    return 0;
}


/*
 * Return the carrier's offset from the tuned frequency (rad per decimated sample) within SNAP_RANGE_HZ, as fldigi
 * snaps a click on the waterfall to the nearest signal: the peak of the spectrum of the squared baseband, where
 * squaring removes the phase modulation and leaves a line at twice the offset. With no signal it is the largest
 * noise peak in the range.
 */
static double
snap_offset(const Baseband *base, double fs_decimated)
{
    double best = 0.0, best_power = -1.0;
    int steps = (int)lround(SNAP_RANGE_HZ / SNAP_STEP_HZ);
    for (int i = -steps; i <= steps; i++)
    {
        double w = 2.0 * M_PI * 2.0 * (double)i * SNAP_STEP_HZ / fs_decimated;
        double step_re = cos(w), step_im = -sin(w);
        double rot_re = 1.0, rot_im = 0.0;
        double sum_re = 0.0, sum_im = 0.0;
        for (Py_ssize_t m = 0; m < base->count; m++)
        {
            double sq_re = base->re[m] * base->re[m] - base->im[m] * base->im[m];
            double sq_im = 2.0 * base->re[m] * base->im[m];
            sum_re += sq_re * rot_re - sq_im * rot_im;
            sum_im += sq_re * rot_im + sq_im * rot_re;
            double next_re = rot_re * step_re - rot_im * step_im;
            rot_im = rot_re * step_im + rot_im * step_re;
            rot_re = next_re;
            if ((m & 1023) == 1023)
            {
                /* Keep the rotator on the unit circle. */
                double norm = hypot(rot_re, rot_im);
                rot_re /= norm;
                rot_im /= norm;
            }
        }
        double power = sum_re * sum_re + sum_im * sum_im;
        if (power > best_power)
        {
            best_power = power;
            best = 2.0 * M_PI * (double)i * SNAP_STEP_HZ / fs_decimated;
        }
    }
    return best;
}


PyDoc_STRVAR(encode_doc,
             "encode(text)\n"
             "--\n\n"
             "Return the bits (bytes of 0/1) PSK31 sends for text: each character's Varicode followed by \"00\".\n"
             "Raises ValueError for a non-ASCII character.");


/* Python entry point: see encode_doc. */
static PyObject *
psk31_encode(PyObject *Py_UNUSED(module), PyObject *args)
{
    const char *text = NULL;
    Py_ssize_t text_len = 0;
    if (!PyArg_ParseTuple(args, "s#", &text, &text_len))
    {
        return NULL;
    }
    BitList bits = {NULL, 0, 0};
    PyObject *result = NULL;
    if (encode_text(text, (size_t)text_len, &bits, NULL) == 0)
    {
        result = PyBytes_FromStringAndSize((const char *)bits.items, (Py_ssize_t)bits.count);
    }
    free(bits.items);
    return result;
}


PyDoc_STRVAR(modulate_doc,
             "modulate(text, fs, carrier_hz, baud, preamble_bits, postamble_bits)\n"
             "--\n\n"
             "Generate BPSK31 audio for text (peak amplitude 1, float32 bytes).\n\n"
             "The bits sent are preamble_bits zeros (phase reversals), the Varicode of each character followed by\n"
             "\"00\", then postamble_bits ones (steady carrier). The envelope rises over the first symbol and falls\n"
             "over the last. Returns (audio_bytes, char_starts, nbits): char_starts holds the sample index at which\n"
             "the first bit of each character of text begins, and nbits is the number of bits sent. Raises\n"
             "ValueError for a non-ASCII character.");


/* Python entry point: see modulate_doc. */
static PyObject *
psk31_modulate(PyObject *Py_UNUSED(module), PyObject *args)
{
    const char *text = NULL;
    Py_ssize_t text_len = 0;
    double fs = 0.0, carrier_hz = 0.0, baud = 0.0;
    int preamble_bits = 0, postamble_bits = 0;
    if (!PyArg_ParseTuple(args, "s#ddd|ii", &text, &text_len, &fs, &carrier_hz, &baud, &preamble_bits,
                          &postamble_bits))
    {
        return NULL;
    }
    if (!(fs > 0.0) || !(baud > 0.0) || preamble_bits < 0 || postamble_bits < 0)
    {
        PyErr_SetString(PyExc_ValueError, "invalid PSK31 timing parameters");
        return NULL;
    }

    BitList bits = {NULL, 0, 0};
    size_t *char_bits = malloc((size_t)(text_len > 0 ? text_len : 1) * sizeof(size_t));
    unsigned char *signs = NULL;
    PyObject *result = NULL;
    if (char_bits == NULL)
    {
        return PyErr_NoMemory();
    }
    for (int i = 0; i < preamble_bits; i++)
    {
        if (bit_list_push(&bits, 0) < 0)
        {
            goto done;
        }
    }
    if (encode_text(text, (size_t)text_len, &bits, char_bits) < 0)
    {
        goto done;
    }
    for (int i = 0; i < postamble_bits; i++)
    {
        if (bit_list_push(&bits, 1) < 0)
        {
            goto done;
        }
    }

    {
        /* Pulse k (k = 0..nbits) carries sign a[k]; bit j (1-based) is the change from a[j-1] to a[j]. Pulse k spans
         * [k*sps, (k+2)*sps), so bit j occupies [j*sps, (j+1)*sps) and the waveform lasts (nbits+2) symbols. */
        size_t nbits = bits.count;
        signs = malloc(nbits + 1);
        if (signs == NULL)
        {
            PyErr_NoMemory();
            goto done;
        }
        signs[0] = 1;
        for (size_t j = 1; j <= nbits; j++)
        {
            signs[j] = bits.items[j - 1] ? signs[j - 1] : (unsigned char)!signs[j - 1];
        }

        double sps = fs / baud;
        Py_ssize_t total = (Py_ssize_t)ceil((double)(nbits + 2) * sps);
        PyObject *audio = PyBytes_FromStringAndSize(NULL, total * (Py_ssize_t)sizeof(float));
        PyObject *starts = PyList_New(text_len);
        if (audio == NULL || starts == NULL)
        {
            Py_XDECREF(audio);
            Py_XDECREF(starts);
            goto done;
        }
        float *out = (float *)PyBytes_AS_STRING(audio);
        double w = 2.0 * M_PI * carrier_hz / fs;
        for (Py_ssize_t n = 0; n < total; n++)
        {
            Py_ssize_t newest = (Py_ssize_t)floor((double)n / sps);
            double envelope = 0.0;
            for (Py_ssize_t k = newest - 1; k <= newest; k++)
            {
                if (k >= 0 && (size_t)k <= nbits)
                {
                    envelope += (signs[k] ? 1.0 : -1.0) * pulse((double)n - (double)k * sps, sps);
                }
            }
            out[n] = (float)(envelope * cos(fmod(w * (double)n, 2.0 * M_PI)));
        }
        for (Py_ssize_t i = 0; i < text_len; i++)
        {
            PyObject *start = PyLong_FromSsize_t((Py_ssize_t)ceil((double)(char_bits[i] + 1) * sps));
            if (start == NULL)
            {
                Py_DECREF(audio);
                Py_DECREF(starts);
                goto done;
            }
            PyList_SET_ITEM(starts, i, start);
        }
        result = Py_BuildValue("(NNn)", audio, starts, (Py_ssize_t)nbits);
    }

done:
    free(bits.items);
    free(char_bits);
    free(signs);
    return result;
}


/*
 * Append one printed character and its position to the growable output arrays.
 * Returns 0 on success and -1 with a Python MemoryError set.
 */
static int
push_char(char **text, Py_ssize_t **positions, size_t *count, size_t *capacity, char c, Py_ssize_t position)
{
    if (*count == *capacity)
    {
        size_t grown = *capacity * 2;
        char *grown_text = realloc(*text, grown);
        if (grown_text == NULL)
        {
            PyErr_NoMemory();
            return -1;
        }
        *text = grown_text;
        Py_ssize_t *grown_positions = realloc(*positions, grown * sizeof(Py_ssize_t));
        if (grown_positions == NULL)
        {
            PyErr_NoMemory();
            return -1;
        }
        *positions = grown_positions;
        *capacity = grown;
    }
    (*text)[*count] = c;
    (*positions)[*count] = position;
    (*count)++;
    return 0;
}


/*
 * Estimate the SNR (dB, 2500 Hz reference bandwidth) from the symbol samples y (count of them, at input-sample
 * instants `at`) whose instants lie in [start, end), into *snr_db: NAN when fewer than three symbols qualify or the
 * estimate is not positive. Returns 0 on success and -1 with a Python MemoryError set.
 *
 * Each symbol's carrier phase comes from the sum of the squared samples within PHASE_SPAN symbols (squaring removes
 * the modulation), unwrapped from symbol to symbol. Against it, the in-phase part carries the signal, fitted as
 * A * (1 + isi * (p + q)) plus a skew term (see below) with p, q the coherent sign products with the neighboring
 * symbols, and the quadrature part carries only noise. The transmit envelope's mean square follows from the decisions
 * (1 for a held phase, 1/2 across a reversal), and the chain constants convert amplitude and noise to the input.
 */
static int
estimate_snr(const double *y_re, const double *y_im, const Py_ssize_t *at, Py_ssize_t count, Py_ssize_t start,
             Py_ssize_t end, const Chain *chain, double fs, double *snr_db)
{
    *snr_db = NAN;
    Py_ssize_t first = -1, last = -1;
    for (Py_ssize_t k = 0; k < count; k++)
    {
        if (at[k] >= start && at[k] < end)
        {
            if (first < 0)
            {
                first = k;
            }
            last = k;
        }
    }
    if (first < 0 || last - first + 1 < 3)
    {
        return 0;
    }
    Py_ssize_t span = last - first + 1;
    double *in_phase = malloc((size_t)span * sizeof(double));
    double *quadrature = malloc((size_t)span * sizeof(double));
    int *sign = malloc((size_t)span * sizeof(int));
    if (in_phase == NULL || quadrature == NULL || sign == NULL)
    {
        free(in_phase);
        free(quadrature);
        free(sign);
        PyErr_NoMemory();
        return -1;
    }

    double previous_phase = 0.0;
    for (Py_ssize_t i = 0; i < span; i++)
    {
        Py_ssize_t k = first + i;
        double sq_re = 0.0, sq_im = 0.0;
        for (Py_ssize_t j = k - PHASE_SPAN; j <= k + PHASE_SPAN; j++)
        {
            if (j >= 0 && j < count)
            {
                sq_re += y_re[j] * y_re[j] - y_im[j] * y_im[j];
                sq_im += 2.0 * y_re[j] * y_im[j];
            }
        }
        double phase = 0.5 * atan2(sq_im, sq_re);
        if (i > 0)
        {
            /* The squared phasor fixes the phase only modulo pi: take the branch nearest the previous symbol's. */
            phase += M_PI * round((previous_phase - phase) / M_PI);
        }
        previous_phase = phase;
        double re = y_re[k] * cos(phase) + y_im[k] * sin(phase);
        double im = y_im[k] * cos(phase) - y_re[k] * sin(phase);
        sign[i] = re >= 0.0 ? 1 : -1;
        in_phase[i] = fabs(re);
        quadrature[i] = im;
    }

    /* Least squares for I = A * (1 + isi * (p + q)) + skew * (p - q), where p and q are the sign products with the
     * previous and next symbol: the skew term absorbs the unequal interference of the two neighbors when the symbol
     * is sampled off its peak. With no reversal in the span the skew is undetermined and left at zero. */
    double saa = 0.0, sab = 0.0, sbb = 0.0, sya = 0.0, syb = 0.0, envelope = 0.0;
    for (Py_ssize_t i = 0; i < span; i++)
    {
        double p = i > 0 ? sign[i - 1] * sign[i] : 0.0;
        double q = i + 1 < span ? sign[i + 1] * sign[i] : 0.0;
        if (i > 0)
        {
            envelope += sign[i - 1] == sign[i] ? 1.0 : 0.5;
        }
        double a = 1.0 + chain->isi * (p + q);
        double b = p - q;
        saa += a * a;
        sab += a * b;
        sbb += b * b;
        sya += in_phase[i] * a;
        syb += in_phase[i] * b;
    }
    double det = saa * sbb - sab * sab;
    double amplitude = 0.0;
    if (det > 1e-9 * saa * sbb && sbb > 0.0)
    {
        amplitude = (sya * sbb - syb * sab) / det;
    }
    else if (saa > 0.0)
    {
        amplitude = sya / saa;
    }
    /* Noise from the quadrature part alone: it holds half the noise power and none of the signal, whereas the
     * in-phase residual also holds interference that varies with the symbol-to-symbol timing. */
    double noise = 0.0;
    for (Py_ssize_t i = 0; i < span; i++)
    {
        noise += 2.0 * quadrature[i] * quadrature[i];
    }
    noise /= (double)span;
    envelope /= (double)(span - 1);
    free(in_phase);
    free(quadrature);
    free(sign);

    /* Mixing a carrier of peak amplitude P gives a baseband amplitude P/2, so P = 2 * amplitude / peak. Real white
     * noise of variance s2 becomes complex noise of variance s2, so s2 = noise / noise_gain; its density over
     * 0..fs/2 puts s2 * 2500 / (fs/2) in the reference bandwidth. */
    double carrier = 2.0 * amplitude / chain->peak;
    double signal_power = 0.5 * carrier * carrier * envelope;
    double noise_in_reference = noise / chain->noise_gain * 2500.0 / (0.5 * fs);
    if (signal_power > 0.0 && noise_in_reference > 0.0)
    {
        *snr_db = 10.0 * log10(signal_power / noise_in_reference);
    }
    return 0;
}


PyDoc_STRVAR(demodulate_doc,
             "demodulate(audio, fs, carrier_hz, baud, snr_start, snr_end)\n"
             "--\n\n"
             "Decode BPSK31 from audio with the reference demodulator tuned to carrier_hz.\n\n"
             "Returns (text, positions, snr_db, snr_chars). text holds every printed character in order (CR and LF\n"
             "print as spaces; other control characters print nothing); positions[i] is the sample index at which\n"
             "the first bit of text[i] began. The SNR estimate, in dB in a 2500 Hz reference bandwidth, uses the\n"
             "symbols whose instants fall in [snr_start, snr_end), or is None when it has too few or no signal.\n"
             "snr_chars counts printed characters starting in that span.");


/* Python entry point: see demodulate_doc. */
static PyObject *
psk31_demodulate(PyObject *Py_UNUSED(module), PyObject *args)
{
    PyObject *audio_object = NULL;
    double fs = 0.0, carrier_hz = 0.0, baud = 0.0;
    Py_ssize_t snr_start = 0, snr_end = 0;
    if (!PyArg_ParseTuple(args, "Oddd|nn", &audio_object, &fs, &carrier_hz, &baud, &snr_start, &snr_end))
    {
        return NULL;
    }
    int td = symbol_samples(fs, baud);
    if (td == 0)
    {
        return NULL;
    }
    int taps = 2 * td;

    Chain chain;
    if (chain_constants(td, &chain) < 0)
    {
        return NULL;
    }
    Audio audio = {NULL, 0};
    if (read_audio(audio_object, &audio) < 0)
    {
        return NULL;
    }
    Baseband base = {NULL, NULL, 0};
    if (to_baseband(&audio, fs, carrier_hz, &base) < 0)
    {
        free(audio.samples);
        return NULL;
    }
    free(audio.samples);

    Py_ssize_t m_count = base.count;
    size_t sym_capacity = (size_t)(m_count / (td - 1) + 4);
    double *sym_re = malloc(sym_capacity * sizeof(double));
    double *sym_im = malloc(sym_capacity * sizeof(double));
    Py_ssize_t *sym_at = malloc(sym_capacity * sizeof(Py_ssize_t));
    double *ring_re = calloc((size_t)taps, sizeof(double));
    double *ring_im = calloc((size_t)taps, sizeof(double));
    double *timing = calloc((size_t)td, sizeof(double));
    double *taps_h = malloc((size_t)taps * sizeof(double));
    size_t capacity = 256, count = 0;
    char *text = malloc(capacity);
    Py_ssize_t *positions = malloc(capacity * sizeof(Py_ssize_t));
    PyObject *result = NULL;
    if (sym_re == NULL || sym_im == NULL || sym_at == NULL || ring_re == NULL || ring_im == NULL || timing == NULL ||
        taps_h == NULL || text == NULL || positions == NULL)
    {
        PyErr_NoMemory();
        goto done;
    }
    for (int i = 0; i < taps; i++)
    {
        taps_h[i] = matched_tap(i, td);
    }

    {
        /* Input-sample instant of matched-filter output m: the center of its 2*td-sample span. */
        double center_offset = (double)DECIMATION * ((double)taps - 1.0) / 2.0;
        double afc_limit = 2.0 * M_PI * AFC_LIMIT_HZ / (fs / DECIMATION);
        double snapped = snap_offset(&base, fs / DECIMATION);
        double afc = snapped, afc_phase = 0.0;
        double quality_re = 0.0, quality_im = 0.0;
        Py_ssize_t symbols = 0;
        Py_ssize_t next = taps;
        unsigned int code = 0;
        int code_bits = 0, previous_bit = 1, overflow = 0;
        Py_ssize_t code_start = 0, previous_at = 0;
        Py_ssize_t snr_chars = 0;

        for (Py_ssize_t m = 0; m < m_count; m++)
        {
            /* AFC rotation, then the matched filter over the last `taps` rotated samples. */
            double c = cos(afc_phase), s = sin(afc_phase);
            int slot = (int)(m % taps);
            ring_re[slot] = base.re[m] * c + base.im[m] * s;
            ring_im[slot] = base.im[m] * c - base.re[m] * s;
            afc_phase = fmod(afc_phase + afc, 2.0 * M_PI);
            double y_re = 0.0, y_im = 0.0;
            for (int i = 0; i < taps; i++)
            {
                int j = (int)((m - i) % taps);
                if (j < 0)
                {
                    j += taps;
                }
                y_re += taps_h[i] * ring_re[j];
                y_im += taps_h[i] * ring_im[j];
            }
            int phase_bin = (int)(m % td);
            timing[phase_bin] += TIMING_ALPHA * (y_re * y_re + y_im * y_im - timing[phase_bin]);
            if (m != next)
            {
                continue;
            }

            /* Early-late gate on the averaged power. */
            double early = timing[(phase_bin + td - 2) % td];
            double late = timing[(phase_bin + 2) % td];
            int adjust = 0;
            if (late - early > TIMING_DEADBAND * timing[phase_bin])
            {
                adjust = 1;
            }
            else if (early - late > TIMING_DEADBAND * timing[phase_bin])
            {
                adjust = -1;
            }
            next = m + td + adjust;

            Py_ssize_t at = (Py_ssize_t)lround((double)m * DECIMATION - center_offset);
            sym_re[symbols] = y_re;
            sym_im[symbols] = y_im;
            sym_at[symbols] = at;
            symbols++;
            if (symbols < 2)
            {
                previous_at = at;
                continue;
            }

            /* Differential detection, then the AFC on the phase error with the modulation squared away. */
            double p_re = sym_re[symbols - 2], p_im = sym_im[symbols - 2];
            double u_re = y_re * p_re + y_im * p_im;
            double u_im = y_im * p_re - y_re * p_im;
            int bit = u_re > 0.0;
            double u2_re = u_re * u_re - u_im * u_im;
            double u2_im = 2.0 * u_re * u_im;
            double magnitude = hypot(u2_re, u2_im);
            if (magnitude > 0.0)
            {
                quality_re += QUALITY_ALPHA * (u2_re / magnitude - quality_re);
                quality_im += QUALITY_ALPHA * (u2_im / magnitude - quality_im);
            }
            double weight = quality_re * quality_re + quality_im * quality_im;
            if (magnitude > 0.0)
            {
                afc += AFC_GAIN * weight * 0.5 * atan2(u2_im, u2_re) / (double)td;
            }
            afc -= AFC_RELAX * (1.0 - weight) * (afc - snapped);
            afc = snapped + fmax(-afc_limit, fmin(afc_limit, afc - snapped));

            /* Varicode: a character ends at "00"; the bit before this one started the symbol before this one. */
            if (bit == 1 && code_bits == 0)
            {
                code_start = previous_at;
            }
            if (bit == 0 && previous_bit == 0)
            {
                if (code_bits > 1 && !overflow)
                {
                    unsigned int value = code >> 1;
                    int character = value < (1u << MAX_CODE_BITS) ? DECODE_TABLE[value] : -1;
                    char printed = 0;
                    if (character == '\r' || character == '\n')
                    {
                        printed = ' ';
                    }
                    else if (character >= 32 && character < 127)
                    {
                        printed = (char)character;
                    }
                    if (printed != 0)
                    {
                        if (push_char(&text, &positions, &count, &capacity, printed, code_start) < 0)
                        {
                            goto done;
                        }
                        if (code_start >= snr_start && code_start < snr_end)
                        {
                            snr_chars++;
                        }
                    }
                }
                code = 0;
                code_bits = 0;
                overflow = 0;
            }
            else if (bit == 1 || code_bits > 0)
            {
                code = (code << 1) | (unsigned int)bit;
                code_bits++;
                if (code_bits > MAX_CODE_BITS + 1)
                {
                    overflow = 1;
                    code_bits = MAX_CODE_BITS + 1;
                }
            }
            previous_bit = bit;
            previous_at = at;
        }

        PyObject *position_list = PyList_New((Py_ssize_t)count);
        if (position_list == NULL)
        {
            goto done;
        }
        for (size_t i = 0; i < count; i++)
        {
            PyObject *value = PyLong_FromSsize_t(positions[i]);
            if (value == NULL)
            {
                Py_DECREF(position_list);
                goto done;
            }
            PyList_SET_ITEM(position_list, (Py_ssize_t)i, value);
        }

        double snr_db = NAN;
        if (estimate_snr(sym_re, sym_im, sym_at, symbols, snr_start, snr_end, &chain, fs, &snr_db) < 0)
        {
            Py_DECREF(position_list);
            goto done;
        }
        PyObject *snr = isnan(snr_db) ? Py_NewRef(Py_None) : PyFloat_FromDouble(snr_db);
        if (snr == NULL)
        {
            Py_DECREF(position_list);
            goto done;
        }
        result = Py_BuildValue("(s#NNn)", text, (Py_ssize_t)count, position_list, snr, snr_chars);
    }

done:
    free(base.re);
    free(base.im);
    free(sym_re);
    free(sym_im);
    free(sym_at);
    free(ring_re);
    free(ring_im);
    free(timing);
    free(taps_h);
    free(text);
    free(positions);
    return result;
}


PyDoc_STRVAR(slice_bits_doc,
             "slice_bits(audio, fs, carrier_hz, baud, first_bit_start, nbits)\n"
             "--\n\n"
             "Decide nbits consecutive bits at known timing and frequency with the demodulator's filters.\n\n"
             "Bit k occupies samples [first_bit_start + k*L, first_bit_start + (k+1)*L), L = fs/baud, between the\n"
             "symbol instants at its two ends; it is the sign of Re(y_end conj(y_start)). Returns bytes of 0/1.\n"
             "This isolates the detector from timing and frequency recovery, so its bit error rate can be checked\n"
             "against differential-BPSK theory.");


/* Python entry point: see slice_bits_doc. */
static PyObject *
psk31_slice_bits(PyObject *Py_UNUSED(module), PyObject *args)
{
    PyObject *audio_object = NULL;
    double fs = 0.0, carrier_hz = 0.0, baud = 0.0, first_bit_start = 0.0;
    Py_ssize_t nbits = 0;
    if (!PyArg_ParseTuple(args, "Oddddn", &audio_object, &fs, &carrier_hz, &baud, &first_bit_start, &nbits))
    {
        return NULL;
    }
    if (nbits < 0 || first_bit_start < 0.0)
    {
        PyErr_SetString(PyExc_ValueError, "invalid bit-slicing parameters");
        return NULL;
    }
    int td = symbol_samples(fs, baud);
    if (td == 0)
    {
        return NULL;
    }
    int taps = 2 * td;

    Audio audio = {NULL, 0};
    if (read_audio(audio_object, &audio) < 0)
    {
        return NULL;
    }
    Baseband base = {NULL, NULL, 0};
    if (to_baseband(&audio, fs, carrier_hz, &base) < 0)
    {
        free(audio.samples);
        return NULL;
    }
    free(audio.samples);

    double sps = fs / baud;
    double center_offset = (double)DECIMATION * ((double)taps - 1.0) / 2.0;
    PyObject *bits = PyBytes_FromStringAndSize(NULL, nbits);
    if (bits != NULL)
    {
        char *out = PyBytes_AS_STRING(bits);
        double prev_re = 0.0, prev_im = 0.0;
        for (Py_ssize_t k = 0; k <= nbits; k++)
        {
            /* The output whose center is nearest the symbol instant first_bit_start + k*sps. */
            Py_ssize_t m = (Py_ssize_t)lround((first_bit_start + (double)k * sps + center_offset) / DECIMATION);
            if (m - taps + 1 < 0 || m >= base.count)
            {
                Py_DECREF(bits);
                bits = NULL;
                PyErr_SetString(PyExc_ValueError, "audio is too short for the requested bits");
                break;
            }
            double y_re = 0.0, y_im = 0.0;
            for (int i = 0; i < taps; i++)
            {
                y_re += matched_tap(i, td) * base.re[m - i];
                y_im += matched_tap(i, td) * base.im[m - i];
            }
            if (k > 0)
            {
                out[k - 1] = (y_re * prev_re + y_im * prev_im) > 0.0 ? 1 : 0;
            }
            prev_re = y_re;
            prev_im = y_im;
        }
    }
    free(base.re);
    free(base.im);
    return bits;
}


static PyMethodDef psk31_methods[] = {
    {"encode", psk31_encode, METH_VARARGS, encode_doc},
    {"modulate", psk31_modulate, METH_VARARGS, modulate_doc},
    {"demodulate", psk31_demodulate, METH_VARARGS, demodulate_doc},
    {"slice_bits", psk31_slice_bits, METH_VARARGS, slice_bits_doc},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef psk31_module = {
    PyModuleDef_HEAD_INIT,
    "_psk31",
    "Amateur PSK31 (BPSK31, Varicode) modulator and reference differential demodulator.",
    -1,
    psk31_methods,
    NULL,
    NULL,
    NULL,
    NULL,
};


PyMODINIT_FUNC
PyInit__psk31(void)
{
    build_decode_table();
    return PyModule_Create(&psk31_module);
}
