/**
 * scoreEngine.js - runs the feature extractors and blends them into a score.
 *
 * THE DESIGN
 *
 * The engine itself knows nothing about HOW anything is measured. It holds a
 * list of extractors, hands each one every frame, and at the end combines
 * their scores using a weights table.
 *
 * That separation is what makes the pitch upgrade easy: adding pitch
 * detection means adding one entry to `extractors` and one to `weights` -
 * no other file changes. See features/pitch.js for the full walkthrough.
 *
 * THE DISPLAYED SCORE: 75 TO 100
 *
 * The blend comes out between 0 (nothing sung) and 1 (every word on cue).
 * It is shown on a friendlier scale - SCORE_FLOOR to 100 - so nobody walks
 * away from the mic with a humiliating number: a party is not an exam.
 * The mapping is a straight line, so the ORDER of performances (and so the
 * leaderboard) is exactly what the raw measurements say; only the numbers
 * are kinder. The raw 0-1 parts are still saved alongside (timing_score,
 * energy_score), for anyone who wants the unvarnished version.
 *
 * PRACTICE MODE
 *
 * When `judging` is false the engine still runs everything but never saves
 * a score. That is practice mode: sing with the lyrics and no judgement.
 */

import { EnergyExtractor } from './features/energy.js';
import { WordTimingExtractor } from './features/timing.js';

/** The lowest score anyone is ever shown; a flawless performance gets 100. */
export const SCORE_FLOOR = 75;

/** Score bands, on the displayed 75-100 scale. Highest first. */
const GRADES = [
    { min: 97, label: 'Perfect',     note: 'A flawless performance!' },
    { min: 93, label: 'Superstar',   note: 'Outstanding singing.' },
    { min: 88, label: 'Great',       note: 'Strong and confident.' },
    { min: 83, label: 'Good',        note: 'Solid effort - keep going.' },
    { min: 79, label: 'Not Bad',     note: 'Getting there.' },
    { min: 0,  label: 'Warming Up',  note: 'Sing out a bit more next time!' },
];

export function gradeFor(score) {
    return GRADES.find((grade) => score >= grade.min) ?? GRADES[GRADES.length - 1];
}

/** 0-1 raw blend -> the displayed score. */
export function displayScore(raw) {
    const clamped = Math.max(0, Math.min(1, raw));
    return Math.round(SCORE_FLOOR + clamped * (100 - SCORE_FLOOR));
}

export class ScoreEngine {
    constructor() {
        /**
         * The extractors, keyed by name. To add pitch scoring:
         *     this.extractors.pitch = new PitchExtractor();
         */
        this.extractors = {
            timing: new WordTimingExtractor(),
            energy: new EnergyExtractor(),
        };

        /**
         * How much each extractor contributes. They are normalised at the
         * end, so they do not have to add up to exactly 1.
         *
         * Timing dominates: singing the words on cue is the core of karaoke,
         * and it is the part that is hard to fake. Energy only measures how
         * loud and how varied the voice was, which a non-singer can imitate,
         * so it adds a little on top rather than half the score.
         */
        this.weights = {
            timing: 0.9,
            energy: 0.1,
            // pitch: 0.3,   <- add when enabling features/pitch.js
        };

        this.judging = true;
    }

    /**
     * Prepare for a new performance.
     *
     * @param {object} targets - from features/timing.js buildScoringTargets()
     * @param {object} options - { judging: false } for practice mode
     */
    reset(targets = { words: [], gaps: [] }, options = {}) {
        this.judging = options.judging !== false;
        for (const extractor of Object.values(this.extractors)) {
            extractor.reset(targets);
        }
    }

    /**
     * Feed one frame of microphone data to every extractor.
     *
     * @param {object} frame - from MicCapture.readFrame()
     * @param {number} songTime - the audio element's currentTime
     */
    process(frame, songTime) {
        const context = { songTime };
        for (const extractor of Object.values(this.extractors)) {
            extractor.process(frame, context);
        }
    }

    /**
     * Finish the performance and produce the final result.
     *
     * Weights are normalised over whichever extractors actually ran, so
     * enabling or disabling one cannot distort the range.
     */
    finish() {
        const summaries = {};
        let weightedTotal = 0;
        let weightSum = 0;

        for (const [name, extractor] of Object.entries(this.extractors)) {
            const summary = extractor.summary();
            summaries[name] = summary;

            const weight = this.weights[name] ?? 0;
            if (weight > 0) {
                weightedTotal += summary.score * weight;
                weightSum += weight;
            }
        }

        const raw = weightSum > 0 ? weightedTotal / weightSum : 0;
        const finalScore = displayScore(raw);

        return {
            finalScore,
            rawScore: raw,
            grade: gradeFor(finalScore),
            timingScore: summaries.timing?.score ?? null,
            energyScore: summaries.energy?.score ?? null,
            pitchScore: summaries.pitch?.enabled ? summaries.pitch.score : null,
            energyTimeline: summaries.energy?.timeline ?? [],
            details: summaries,
            judged: this.judging,
        };
    }
}
