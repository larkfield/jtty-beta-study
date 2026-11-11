/*
 * _rtty: amateur RTTY (ITA2/US Baudot, AFSK) modulator and reference demodulator.
 *
 * This is the RTTY baseline of the JTTY SNR study. Python adds the band noise and the receiver filter; this module
 * generates the clean transmitted audio and decodes the received audio, the way wsjt-codec does for the WSJT modes.
 *
 * Transmit: US-TTY Baudot with LTRS/FIGS shifts and unshift-on-space (USOS), 1 start bit, 5 data bits sent LSB
 * first, configurable stop bits (1.5 for amateur RTTY), continuous-phase FSK with mark = 1 and space = 0.
 *
 * Receive: the audio is mixed to baseband at the nominal mark and space tones and integrated over one bit (a
 * matched filter for each tone). A UART-style detector finds each start bit at the falling zero crossing of the
 * mark-minus-space energy, refines the character's timing over all seven of its bits, samples the start, data and
 * stop bits one bit apart, rejects framing errors, and decodes Baudot with shift state and USOS, as a well-adjusted
 * MMTTY/2Tone-style decoder does. It also estimates the SNR in the 2500 Hz reference bandwidth from the on-tone and
 * off-tone energies at the sampled bits, correcting for the known tone leakage between the two filters.
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

/* Baudot control codes (5-bit values; bit 0 is the first data bit on the air). */
enum
{
    BAUDOT_NUL = 0x00,
    BAUDOT_LF = 0x02,
    BAUDOT_SPACE = 0x04,
    BAUDOT_CR = 0x08,
    BAUDOT_FIGS = 0x1B,
    BAUDOT_LTRS = 0x1F
};

/* Printable meaning of each code in letters shift; 0 marks a shift or non-printing code. */
static const char LTRS_TABLE[32] = {
    0,   'E', '\n', 'A', ' ', 'S', 'I', 'U', '\r', 'D', 'R', 'J', 'N', 'F', 'C', 'K',
    'T', 'Z', 'L',  'W', 'H', 'Y', 'P', 'Q', 'O',  'B', 'G', 0,   'M', 'X', 'V', 0,
};

/* Printable meaning of each code in figures shift (US TTY variant; code 0x05 is BELL). */
static const char FIGS_TABLE[32] = {
    0,   '3', '\n', '-', ' ', '\a', '8', '7', '\r', '$', '4', '\'', ',', '!', ':', '(',
    '5', '"', ')',  '2', '#', '6',  '0', '1', '9',  '?', '&', 0,    '.', '/', ';', 0,
};

/* Shift state of a transmitter or receiver. */
typedef enum
{
    SHIFT_LTRS,
    SHIFT_FIGS
} Shift;

/* One keyed element of the transmitted waveform: a tone held for a number of bit periods. */
typedef struct
{
    int mark;           /* 1 = mark tone, 0 = space tone */
    double bits;        /* duration in bit periods */
} Element;

/* A growable list of keyed elements. */
typedef struct
{
    Element *items;
    size_t count;
    size_t capacity;
} ElementList;

/* A read-only view of an audio buffer converted to doubles. */
typedef struct
{
    double *samples;
    Py_ssize_t count;
} Audio;


/*
 * Append one element to the list, growing it as needed.
 * Returns 0 on success and -1 (with a Python MemoryError set) on allocation failure.
 */
static int
element_list_push(ElementList *list, int mark, double bits)
{
    if (list->count == list->capacity)
    {
        size_t capacity = list->capacity ? list->capacity * 2 : 256;
        Element *items = realloc(list->items, capacity * sizeof(Element));
        if (items == NULL)
        {
            PyErr_NoMemory();
            return -1;
        }
        list->items = items;
        list->capacity = capacity;
    }
    list->items[list->count].mark = mark;
    list->items[list->count].bits = bits;
    list->count++;
    return 0;
}


/*
 * Append the start bit, five data bits (LSB first) and the stop bits of one Baudot code.
 * Returns 0 on success and -1 with a Python exception set on failure.
 */
static int
push_code(ElementList *list, int code, double stop_bits)
{
    if (element_list_push(list, 0, 1.0) < 0)
    {
        return -1;
    }
    for (int bit = 0; bit < 5; bit++)
    {
        if (element_list_push(list, (code >> bit) & 1, 1.0) < 0)
        {
            return -1;
        }
    }
    return element_list_push(list, 1, stop_bits);
}


/*
 * Find the Baudot code for a character in the given shift table.
 * Returns the code, or -1 if the table has no such printable character.
 */
static int
find_code(const char *table, char c)
{
    for (int code = 0; code < 32; code++)
    {
        if (table[code] == c && c != 0)
        {
            return code;
        }
    }
    return -1;
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
    Py_ssize_t count = 0;
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
    count = view.len / view.itemsize;

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
 * Matched-filter energies of the mark and space tones: em[n] and es[n] are |sum of x[k] e^{-j w k}|^2 over the
 * window_len samples ending at n (0 before the first full window).
 * Returns 0 on success and -1 with a Python MemoryError set on failure; on success the caller frees *em and *es.
 */
static int
tone_energies(const Audio *audio, double fs, double mark_hz, double space_hz, Py_ssize_t window_len, double **em,
              double **es)
{
    Py_ssize_t n = audio->count;
    size_t size = (size_t)(n > 0 ? n : 1);
    double *zm = malloc(2 * size * sizeof(double));
    double *zs = malloc(2 * size * sizeof(double));
    double *out_m = calloc(size, sizeof(double));
    double *out_s = calloc(size, sizeof(double));
    if (zm == NULL || zs == NULL || out_m == NULL || out_s == NULL)
    {
        free(zm);
        free(zs);
        free(out_m);
        free(out_s);
        PyErr_NoMemory();
        return -1;
    }

    double wm = 2.0 * M_PI * mark_hz / fs;
    double ws = 2.0 * M_PI * space_hz / fs;
    for (Py_ssize_t k = 0; k < n; k++)
    {
        double x = audio->samples[k];
        double pm = fmod(wm * (double)k, 2.0 * M_PI);
        double ps = fmod(ws * (double)k, 2.0 * M_PI);
        zm[2 * k] = x * cos(pm);
        zm[2 * k + 1] = -x * sin(pm);
        zs[2 * k] = x * cos(ps);
        zs[2 * k + 1] = -x * sin(ps);
    }

    double mr = 0.0, mi = 0.0, sr = 0.0, si = 0.0;
    for (Py_ssize_t k = 0; k < n; k++)
    {
        mr += zm[2 * k];
        mi += zm[2 * k + 1];
        sr += zs[2 * k];
        si += zs[2 * k + 1];
        if (k >= window_len)
        {
            Py_ssize_t old = k - window_len;
            mr -= zm[2 * old];
            mi -= zm[2 * old + 1];
            sr -= zs[2 * old];
            si -= zs[2 * old + 1];
        }
        if (k >= window_len - 1)
        {
            out_m[k] = mr * mr + mi * mi;
            out_s[k] = sr * sr + si * si;
        }
    }
    free(zm);
    free(zs);
    *em = out_m;
    *es = out_s;
    return 0;
}


PyDoc_STRVAR(modulate_doc,
             "modulate(text, fs, mark_hz, space_hz, baud, stop_bits, lead_mark_s, idle_ltrs, tail_ltrs)\n"
             "--\n\n"
             "Generate continuous-phase AFSK RTTY audio for text (unit amplitude, float32 bytes).\n\n"
             "The transmission is lead_mark_s seconds of steady mark, idle_ltrs LTRS idle codes, the text, then\n"
             "tail_ltrs LTRS codes. Characters are encoded in US-TTY Baudot; after a space the transmitter assumes\n"
             "the receiver has unshifted (USOS) and re-sends FIGS before the next figure. Returns (audio_bytes,\n"
             "char_starts): char_starts holds the sample index at which each character's start bit begins, one entry\n"
             "per character of text (shift codes inserted for it are not counted). Raises ValueError for a character\n"
             "with no Baudot code.");


/* Python entry point: see modulate_doc. */
static PyObject *
rtty_modulate(PyObject *Py_UNUSED(module), PyObject *args)
{
    const char *text = NULL;
    double fs = 0.0, mark_hz = 0.0, space_hz = 0.0, baud = 0.0, stop_bits = 0.0, lead_mark_s = 0.0;
    int idle_ltrs = 0, tail_ltrs = 0;
    if (!PyArg_ParseTuple(args, "sdddddd|ii", &text, &fs, &mark_hz, &space_hz, &baud, &stop_bits, &lead_mark_s,
                          &idle_ltrs, &tail_ltrs))
    {
        return NULL;
    }
    if (fs <= 0.0 || baud <= 0.0 || stop_bits < 1.0 || lead_mark_s < 0.0 || idle_ltrs < 0 || tail_ltrs < 0)
    {
        PyErr_SetString(PyExc_ValueError, "invalid RTTY timing parameters");
        return NULL;
    }

    size_t text_len = strlen(text);
    ElementList elements = {NULL, 0, 0};
    size_t *char_element = malloc((text_len > 0 ? text_len : 1) * sizeof(size_t));
    if (char_element == NULL)
    {
        return PyErr_NoMemory();
    }

    PyObject *result = NULL;
    Shift shift = SHIFT_LTRS;
    if (lead_mark_s > 0.0 && element_list_push(&elements, 1, lead_mark_s * baud) < 0)
    {
        goto done;
    }
    for (int i = 0; i < idle_ltrs; i++)
    {
        if (push_code(&elements, BAUDOT_LTRS, stop_bits) < 0)
        {
            goto done;
        }
    }
    for (size_t i = 0; i < text_len; i++)
    {
        char c = text[i];
        int code = -1;
        if (c == ' ' || c == '\n' || c == '\r')
        {
            code = c == ' ' ? BAUDOT_SPACE : (c == '\n' ? BAUDOT_LF : BAUDOT_CR);
        }
        else
        {
            int letter = find_code(LTRS_TABLE, c);
            int figure = find_code(FIGS_TABLE, c);
            if (letter >= 0)
            {
                if (shift != SHIFT_LTRS)
                {
                    if (push_code(&elements, BAUDOT_LTRS, stop_bits) < 0)
                    {
                        goto done;
                    }
                    shift = SHIFT_LTRS;
                }
                code = letter;
            }
            else if (figure >= 0)
            {
                if (shift != SHIFT_FIGS)
                {
                    if (push_code(&elements, BAUDOT_FIGS, stop_bits) < 0)
                    {
                        goto done;
                    }
                    shift = SHIFT_FIGS;
                }
                code = figure;
            }
            else
            {
                PyErr_Format(PyExc_ValueError, "byte 0x%02x of the text has no Baudot code", (unsigned char)c);
                goto done;
            }
        }
        char_element[i] = elements.count;
        if (push_code(&elements, code, stop_bits) < 0)
        {
            goto done;
        }
        if (code == BAUDOT_SPACE)
        {
            shift = SHIFT_LTRS;
        }
    }
    for (int i = 0; i < tail_ltrs; i++)
    {
        if (push_code(&elements, BAUDOT_LTRS, stop_bits) < 0)
        {
            goto done;
        }
    }

    {
        double samples_per_bit = fs / baud;
        double total_bits = 0.0;
        for (size_t i = 0; i < elements.count; i++)
        {
            total_bits += elements.items[i].bits;
        }
        Py_ssize_t total = (Py_ssize_t)ceil(total_bits * samples_per_bit);
        PyObject *audio = PyBytes_FromStringAndSize(NULL, total * (Py_ssize_t)sizeof(float));
        PyObject *starts = PyList_New((Py_ssize_t)text_len);
        if (audio == NULL || starts == NULL)
        {
            Py_XDECREF(audio);
            Py_XDECREF(starts);
            goto done;
        }
        float *out = (float *)PyBytes_AS_STRING(audio);

        double wm = 2.0 * M_PI * mark_hz / fs;
        double ws = 2.0 * M_PI * space_hz / fs;
        double phase = 0.0;
        double end_bits = 0.0;
        Py_ssize_t sample = 0;
        size_t next_char = 0;
        for (size_t e = 0; e < elements.count; e++)
        {
            double start_bits = end_bits;
            end_bits += elements.items[e].bits;
            if (next_char < text_len && char_element[next_char] == e)
            {
                PyObject *start = PyLong_FromSsize_t((Py_ssize_t)ceil(start_bits * samples_per_bit));
                if (start == NULL)
                {
                    Py_DECREF(audio);
                    Py_DECREF(starts);
                    goto done;
                }
                PyList_SET_ITEM(starts, (Py_ssize_t)next_char, start);
                next_char++;
            }
            double end_sample = end_bits * samples_per_bit;
            double w = elements.items[e].mark ? wm : ws;
            while (sample < total && (double)sample < end_sample)
            {
                out[sample] = (float)cos(phase);
                phase = fmod(phase + w, 2.0 * M_PI);
                sample++;
            }
        }
        result = Py_BuildValue("(NN)", audio, starts);
    }

done:
    free(elements.items);
    free(char_element);
    return result;
}


PyDoc_STRVAR(demodulate_doc,
             "demodulate(audio, fs, mark_hz, space_hz, baud, stop_bits, snr_start, snr_end)\n"
             "--\n\n"
             "Decode RTTY from audio with the reference matched-filter demodulator.\n\n"
             "Returns (text, positions, snr_db, snr_chars). text holds every printed character in order (CR and LF\n"
             "print as spaces; NUL, BELL and shift codes print nothing); positions[i] is the sample index at which\n"
             "the start bit of text[i] was detected. Characters whose start bit falls in [snr_start, snr_end)\n"
             "contribute to the SNR estimate, which is in dB in a 2500 Hz reference bandwidth, or None when none\n"
             "contributed. snr_chars is the number that did.");


/* Python entry point: see demodulate_doc. */
static PyObject *
rtty_demodulate(PyObject *Py_UNUSED(module), PyObject *args)
{
    PyObject *audio_object = NULL;
    double fs = 0.0, mark_hz = 0.0, space_hz = 0.0, baud = 0.0, stop_bits = 0.0;
    Py_ssize_t snr_start = 0, snr_end = 0;
    if (!PyArg_ParseTuple(args, "Oddddd|nn", &audio_object, &fs, &mark_hz, &space_hz, &baud, &stop_bits, &snr_start,
                          &snr_end))
    {
        return NULL;
    }
    if (fs <= 0.0 || baud <= 0.0 || stop_bits < 1.0)
    {
        PyErr_SetString(PyExc_ValueError, "invalid RTTY timing parameters");
        return NULL;
    }

    Audio audio = {NULL, 0};
    if (read_audio(audio_object, &audio) < 0)
    {
        return NULL;
    }

    double bit_len = fs / baud;
    Py_ssize_t window_len = (Py_ssize_t)lround(bit_len);
    double *em = NULL, *es = NULL;
    if (tone_energies(&audio, fs, mark_hz, space_hz, window_len, &em, &es) < 0)
    {
        free(audio.samples);
        return NULL;
    }

    Py_ssize_t n = audio.count;
    size_t capacity = 256, count = 0;
    char *text = malloc(capacity);
    Py_ssize_t *positions = malloc(capacity * sizeof(Py_ssize_t));
    PyObject *result = NULL;
    if (text == NULL || positions == NULL)
    {
        PyErr_NoMemory();
        goto done;
    }

    {
        Shift shift = SHIFT_LTRS;
        double sum_on = 0.0, sum_off = 0.0;
        Py_ssize_t snr_chars = 0;
        Py_ssize_t pos = window_len;
        while (pos < n)
        {
            double before = em[pos - 1] - es[pos - 1];
            double after = em[pos] - es[pos];
            if (!(before >= 0.0 && after < 0.0))
            {
                pos++;
                continue;
            }

            /* The falling zero crossing of a one-bit integrator lies half a bit into the start bit, whatever follows,
             * so the start bit's full window ends half a bit later. That single edge is noisy, so refine the timing
             * over the whole character: with every window inside one bit, the seven sampled |mark - space| energy
             * differences are largest. */
            double crossing = (double)(pos - 1) + before / (before - after);
            double coarse = crossing + bit_len / 2.0;
            if ((Py_ssize_t)lround(coarse + 6.0 * bit_len + bit_len / 3.0) >= n)
            {
                break;
            }
            double first = coarse;
            double best_fit = -1.0;
            Py_ssize_t reach = (Py_ssize_t)(bit_len / 3.0);
            for (Py_ssize_t shift_samples = -reach; shift_samples <= reach; shift_samples++)
            {
                double candidate = coarse + (double)shift_samples;
                if (candidate < (double)(window_len - 1))
                {
                    continue;
                }
                double fit = 0.0;
                for (int k = 0; k < 7; k++)
                {
                    Py_ssize_t i = (Py_ssize_t)lround(candidate + k * bit_len);
                    fit += fabs(em[i] - es[i]);
                }
                if (fit > best_fit)
                {
                    best_fit = fit;
                    first = candidate;
                }
            }
            Py_ssize_t index[7];
            for (int k = 0; k < 7; k++)
            {
                index[k] = (Py_ssize_t)lround(first + k * bit_len);
            }
            if (em[index[0]] - es[index[0]] >= 0.0 || em[index[6]] - es[index[6]] <= 0.0)
            {
                /* False start or framing error: resume hunting just past this crossing. */
                pos++;
                continue;
            }

            int code = 0;
            for (int bit = 0; bit < 5; bit++)
            {
                if (em[index[bit + 1]] - es[index[bit + 1]] > 0.0)
                {
                    code |= 1 << bit;
                }
            }
            Py_ssize_t start_sample = (Py_ssize_t)lround(first - bit_len + 1.0);

            if (start_sample >= snr_start && start_sample < snr_end)
            {
                for (int k = 0; k < 7; k++)
                {
                    int mark = k == 6 || (k > 0 && ((code >> (k - 1)) & 1));
                    double on = mark ? em[index[k]] : es[index[k]];
                    double off = mark ? es[index[k]] : em[index[k]];
                    sum_on += on;
                    sum_off += off;
                }
                snr_chars++;
            }

            char printed = 0;
            if (code == BAUDOT_LTRS)
            {
                shift = SHIFT_LTRS;
            }
            else if (code == BAUDOT_FIGS)
            {
                shift = SHIFT_FIGS;
            }
            else if (code != BAUDOT_NUL)
            {
                printed = shift == SHIFT_LTRS ? LTRS_TABLE[code] : FIGS_TABLE[code];
                if (printed == '\n' || printed == '\r')
                {
                    printed = ' ';
                }
                if (printed == '\a')
                {
                    printed = 0;
                }
                if (code == BAUDOT_SPACE)
                {
                    shift = SHIFT_LTRS;
                }
            }
            if (printed != 0)
            {
                if (count == capacity)
                {
                    capacity *= 2;
                    char *grown_text = realloc(text, capacity);
                    if (grown_text == NULL)
                    {
                        PyErr_NoMemory();
                        goto done;
                    }
                    text = grown_text;
                    Py_ssize_t *grown_positions = realloc(positions, capacity * sizeof(Py_ssize_t));
                    if (grown_positions == NULL)
                    {
                        PyErr_NoMemory();
                        goto done;
                    }
                    positions = grown_positions;
                }
                text[count] = printed;
                positions[count] = start_sample;
                count++;
            }
            /* Resume at the end of the first stop-bit window: the next falling crossing is the next start bit. */
            pos = index[6] > pos ? index[6] : pos + 1;
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

        /* 170 Hz shift at 45.45 baud is not orthogonal: a fraction `leak` of the on-tone's energy appears in the
         * off-tone filter. With E_on = S + N and E_off = leak*S + N, solve for S and N. */
        double dw = 2.0 * M_PI * (space_hz - mark_hz) / fs;
        double leak_re = 0.0, leak_im = 0.0;
        for (Py_ssize_t k = 0; k < window_len; k++)
        {
            leak_re += cos(dw * (double)k);
            leak_im += sin(dw * (double)k);
        }
        double leak = (leak_re * leak_re + leak_im * leak_im) / ((double)window_len * (double)window_len);
        double signal_energy = (sum_on - sum_off) / (1.0 - leak);
        double noise_energy = sum_off - leak * signal_energy;

        PyObject *snr = NULL;
        if (snr_chars > 0 && signal_energy > 0.0 && noise_energy > 0.0)
        {
            snr = PyFloat_FromDouble(10.0 * log10(signal_energy / noise_energy) - 10.0 * log10(2500.0 / baud));
        }
        else
        {
            snr = Py_NewRef(Py_None);
        }
        if (snr == NULL)
        {
            Py_DECREF(position_list);
            goto done;
        }
        result = Py_BuildValue("(s#NNn)", text, (Py_ssize_t)count, position_list, snr, snr_chars);
    }

done:
    free(text);
    free(positions);
    free(em);
    free(es);
    free(audio.samples);
    return result;
}


PyDoc_STRVAR(slice_bits_doc,
             "slice_bits(audio, fs, mark_hz, space_hz, baud, first_bit_start, nbits)\n"
             "--\n\n"
             "Decide nbits consecutive bits at known timing with the same matched filters the demodulator uses.\n\n"
             "Bit k occupies samples [first_bit_start + k*L, first_bit_start + (k+1)*L), L = fs/baud. Returns bytes\n"
             "of 0 (space) / 1 (mark). This isolates the detector from character synchronization, so its bit error\n"
             "rate can be checked against noncoherent-FSK theory.");


/* Python entry point: see slice_bits_doc. */
static PyObject *
rtty_slice_bits(PyObject *Py_UNUSED(module), PyObject *args)
{
    PyObject *audio_object = NULL;
    double fs = 0.0, mark_hz = 0.0, space_hz = 0.0, baud = 0.0, first_bit_start = 0.0;
    Py_ssize_t nbits = 0;
    if (!PyArg_ParseTuple(args, "Odddddn", &audio_object, &fs, &mark_hz, &space_hz, &baud, &first_bit_start, &nbits))
    {
        return NULL;
    }
    if (fs <= 0.0 || baud <= 0.0 || nbits < 0 || first_bit_start < 0.0)
    {
        PyErr_SetString(PyExc_ValueError, "invalid bit-slicing parameters");
        return NULL;
    }

    Audio audio = {NULL, 0};
    if (read_audio(audio_object, &audio) < 0)
    {
        return NULL;
    }
    double bit_len = fs / baud;
    Py_ssize_t window_len = (Py_ssize_t)lround(bit_len);
    double *em = NULL, *es = NULL;
    if (tone_energies(&audio, fs, mark_hz, space_hz, window_len, &em, &es) < 0)
    {
        free(audio.samples);
        return NULL;
    }

    PyObject *bits = PyBytes_FromStringAndSize(NULL, nbits);
    if (bits != NULL)
    {
        char *out = PyBytes_AS_STRING(bits);
        for (Py_ssize_t k = 0; k < nbits; k++)
        {
            Py_ssize_t end = (Py_ssize_t)lround(first_bit_start + (double)(k + 1) * bit_len) - 1;
            if (end >= audio.count)
            {
                Py_DECREF(bits);
                bits = NULL;
                PyErr_SetString(PyExc_ValueError, "audio is too short for the requested bits");
                break;
            }
            out[k] = em[end] > es[end] ? 1 : 0;
        }
    }
    free(em);
    free(es);
    free(audio.samples);
    return bits;
}


static PyMethodDef rtty_methods[] = {
    {"modulate", rtty_modulate, METH_VARARGS, modulate_doc},
    {"demodulate", rtty_demodulate, METH_VARARGS, demodulate_doc},
    {"slice_bits", rtty_slice_bits, METH_VARARGS, slice_bits_doc},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef rtty_module = {
    PyModuleDef_HEAD_INIT,
    "_rtty",
    "Amateur RTTY (US-TTY Baudot, AFSK) modulator and reference matched-filter demodulator.",
    -1,
    rtty_methods,
    NULL,
    NULL,
    NULL,
    NULL,
};


PyMODINIT_FUNC
PyInit__rtty(void)
{
    return PyModule_Create(&rtty_module);
}
