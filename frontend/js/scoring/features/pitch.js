/**
 * pitch.js - PLACEHOLDER for pitch-matching. Not used in scoring yet.
 *
 * ===========================================================================
 * WHAT THIS FILE IS FOR
 * ===========================================================================
 *
 * The scoring system was built so pitch matching could be added later without
 * restructuring anything. This file is the proof: it already implements the
 * extractor interface, so switching it on is a two-line change in
 * scoreEngine.js. It is disabled by default because of the reference-data
 * problem explained below.
 *
 * ===========================================================================
 * HOW TO TURN IT ON
 * ===========================================================================
 *
 * 1. Add the pitchy library to index.html, before main.js:
 *
 *        <script type="module">
 *          import { PitchDetector } from 'https://esm.sh/pitchy@4';
 *          window.PitchDetector = PitchDetector;
 *        </script>
 *
 * 2. In scoreEngine.js, add the extractor and give it a weight:
 *
 *        import { PitchExtractor } from './features/pitch.js';
 *        ...
 *        this.extractors.pitch = new PitchExtractor();
 *        this.weights = { timing: 0.4, energy: 0.3, pitch: 0.3 };
 *
 * 3. Send the result to the backend - `pitch_score` is already a column in
 *    the scores table and already accepted by POST /api/scores, so no
 *    database migration is needed.
 *
 * ===========================================================================
 * THE PART THAT IS ACTUALLY HARD
 * ===========================================================================
 *
 * Detecting the singer's pitch is easy. pitchy does it in one call, using
 * the McLeod Pitch Method, and the detectPitch() function below is already
 * written. That is not the problem.
 *
 * The problem is the REFERENCE: to say a note was wrong, you need to know
 * what note was right, and nothing in this project currently knows that. The
 * audio files are instrumental backing tracks, so there is no vocal line to
 * compare against. Three ways to solve it:
 *
 *   A. Hand-authored note maps. Extend the LRC file so each line also carries
 *      its expected notes. Most accurate, and it works offline - but someone
 *      has to enter the melody for every song, which is far more work than
 *      tapping along in the sync editor.
 *
 *   B. Extract f0 from an original recording. Run the full version of the
 *      song (with vocals) through a pitch detector once and save the result
 *      as the reference. Accurate and automatic, but it needs a copy of the
 *      original vocal recording, which is exactly what this project avoids
 *      distributing.
 *
 *   C. Relative pitch scoring - RECOMMENDED. Score the singer against
 *      themselves rather than a reference melody: reward holding a steady
 *      note, hitting clean intervals, and landing near the centre of a
 *      semitone instead of drifting between two. It needs no reference data
 *      at all, works on any song immediately, and still meaningfully
 *      separates a confident singer from a wandering one.
 *
 * The methods below implement the measurements option C needs, which is why
 * they are written out rather than left as comments.
 */

/** Concert A4 = 440Hz, the reference all note calculations are built on. */
const A4_FREQUENCY = 440;
const A4_MIDI_NUMBER = 69;

// pitchy reports how confident it is in each reading. Anything below this is
// usually noise or a consonant rather than a sung note.
const MIN_CLARITY = 0.85;

// Human singing sits roughly between these. Readings outside are almost
// always octave errors or background noise.
const MIN_SINGING_HZ = 70;    // below a low bass note
const MAX_SINGING_HZ = 1200;  // above a high soprano note

/**
 * Convert a frequency to a MIDI note number.
 *
 * MIDI numbering makes pitch comparisons easy because it is LINEAR in
 * semitones, while frequency is exponential: the distance from 100Hz to
 * 200Hz and from 200Hz to 400Hz is one octave in both cases, but 100Hz and
 * 200Hz apart in frequency. In MIDI both are exactly 12.
 *
 * Returned unrounded, so the fractional part shows how far from the centre
 * of the note the singer is - which is what "in tune" actually means.
 */
export function frequencyToMidi(frequency) {
    if (frequency <= 0) return null;
    return 12 * Math.log2(frequency / A4_FREQUENCY) + A4_MIDI_NUMBER;
}

/** How far off the nearest exact semitone, in cents (100 cents = 1 semitone). */
export function centsOffPitch(midiNumber) {
    if (midiNumber === null) return null;
    const nearestNote = Math.round(midiNumber);
    return (midiNumber - nearestNote) * 100;
}

/** Note name for display, e.g. 69 -> "A4". */
export function midiToNoteName(midiNumber) {
    if (midiNumber === null) return '--';

    const names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];
    const rounded = Math.round(midiNumber);

    // MIDI note 12 is C0, so subtracting 12 before dividing by 12 gives the
    // octave number used in scientific pitch notation.
    return `${names[rounded % 12]}${Math.floor(rounded / 12) - 1}`;
}

/**
 * Detect the fundamental frequency of one frame.
 *
 * Returns { frequency, clarity, midi, cents } or null when no clear note was
 * found. Requires pitchy to be loaded (see the instructions at the top).
 */
export function detectPitch(timeData, sampleRate) {
    if (typeof window.PitchDetector === 'undefined') return null;

    // Cached on the function itself so the detector (which allocates internal
    // buffers) is built once rather than 60 times a second.
    if (!detectPitch._detector || detectPitch._size !== timeData.length) {
        detectPitch._detector = window.PitchDetector.forFloat32Array(timeData.length);
        detectPitch._size = timeData.length;
    }

    const [frequency, clarity] = detectPitch._detector.findPitch(timeData, sampleRate);

    if (clarity < MIN_CLARITY) return null;
    if (frequency < MIN_SINGING_HZ || frequency > MAX_SINGING_HZ) return null;

    const midi = frequencyToMidi(frequency);
    return { frequency, clarity, midi, cents: centsOffPitch(midi) };
}

/**
 * The extractor itself, implementing option C (relative pitch).
 *
 * Scores two things that need no reference melody:
 *   - accuracy: how close each note sits to the centre of a semitone
 *   - stability: how steadily a note is held, rather than sliding around
 */
export class PitchExtractor {
    constructor() {
        this.reset();
    }

    reset() {
        this.readings = 0;
        this.centsErrorSum = 0;
        this.previousMidi = null;
        this.jumpSum = 0;
        this.jumpCount = 0;
        this.notes = [];
    }

    process(frame, context) {
        // Only analyse frames where the singer is actually making a sound.
        if (!frame.isVoice || !frame.timeData) return;

        const detected = detectPitch(frame.timeData, frame.sampleRate);
        if (!detected) return;

        this.readings++;
        this.centsErrorSum += Math.abs(detected.cents);
        this.notes.push(Number(detected.midi.toFixed(2)));

        if (this.previousMidi !== null) {
            const jump = Math.abs(detected.midi - this.previousMidi);

            // Jumps beyond an octave are nearly always octave-detection
            // errors, not real singing, so they are discarded rather than
            // counted as instability.
            if (jump < 12) {
                this.jumpSum += jump;
                this.jumpCount++;
            }
        }

        this.previousMidi = detected.midi;
    }

    summary() {
        if (this.readings < 20) {
            return { score: 0.5, readings: this.readings, enabled: false };
        }

        const averageCentsError = this.centsErrorSum / this.readings;

        // 50 cents is the worst possible error - exactly between two notes.
        const accuracy = Math.max(0, 1 - averageCentsError / 50);

        // Average movement between consecutive frames. Around 0 is a held
        // note; 2+ semitones per frame is sliding around. The divisor of 2
        // puts the zero point there.
        const averageJump = this.jumpCount > 0 ? this.jumpSum / this.jumpCount : 0;
        const stability = Math.max(0, Math.min(1, 1 - averageJump / 2));

        return {
            score: accuracy * 0.6 + stability * 0.4,
            accuracy,
            stability,
            averageCentsError,
            readings: this.readings,
            enabled: true,
        };
    }
}
