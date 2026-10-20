/**
 * energy.js - the energy score: how the voice was used during the words.
 *
 * A "feature extractor" is any object with three methods:
 *
 *     reset(targets)      start a fresh performance (targets: the words,
 *                         from features/timing.js buildScoringTargets)
 *     process(frame, ctx) take one frame of microphone data
 *     summary()           return { score: 0-1, ... } at the end
 *
 * scoreEngine.js runs a list of these and blends their scores. Keeping them
 * behind that small interface is what makes the pitch upgrade a drop-in
 * addition rather than a rewrite - see features/pitch.js.
 *
 * (The timing score, which used to live here too, is now word by word - see
 * features/timing.js.)
 *
 * ===========================================================================
 * WHAT IT MEASURES, AND WHY ONLY DURING THE WORDS
 * ===========================================================================
 *
 * Only frames that fall inside a word's time count. Tested on whole songs,
 * measuring the whole song rewarded exactly the wrong things: random chatter
 * scored 88/100 and nonstop shouting 97, both above a real singer - chatting
 * and shouting are very "consistent". Inside the words, a non-singer has
 * nothing to show.
 *
 * Two parts:
 *
 *   dynamics  how much the loudness varies, as the COEFFICIENT OF VARIATION
 *             (standard deviation / mean, so loud and quiet singers are
 *             judged alike). Real singing rises and falls with the phrases:
 *             a variation of about 0.25-0.9 earns full marks. A flat drone
 *             (near 0) or wild swings (above ~1.5) earn less.
 *   presence  being audible at all: a strong enough voice earns full marks;
 *             shouting earns nothing extra.
 *
 * It is weighted lightly in scoreEngine.js. Loudness says little about
 * singing, and a non-singer can imitate it - the timing score carries the
 * weight.
 */

// Full marks for dynamics between these two coefficients of variation.
const NATURAL_LOW = 0.25;
const NATURAL_HIGH = 0.9;
const ERRATIC = 1.5;          // no marks for dynamics above this

// RMS at which presence earns full marks.
const PRESENT_RMS = 0.08;

// Fewer voiced frames than this inside the words: nothing was sung.
const MIN_VOICE_FRAMES = 30;

export class EnergyExtractor {
    constructor() {
        this.reset();
    }

    reset(targets = { words: [] }) {
        this.words = targets.words ?? [];
        this.wordIndex = 0;

        this.voiceFrames = 0;
        this.sum = 0;
        this.sumOfSquares = 0;
        this.peak = 0;

        // A downsampled record of loudness over time, for the recap graph.
        this.timeline = [];
        this.timelineAccumulator = 0;
        this.timelineCount = 0;
    }

    /** Is `time` inside one of the words? Walks forward as the song plays. */
    insideWord(time) {
        const words = this.words;
        // A seek backwards: start the walk again from the beginning.
        if (this.wordIndex > 0 && time < words[this.wordIndex - 1]?.start) this.wordIndex = 0;
        while (this.wordIndex < words.length && words[this.wordIndex].end <= time) this.wordIndex++;
        const word = words[this.wordIndex];
        return Boolean(word && word.start <= time);
    }

    process(frame, context = {}) {
        // The graph shows every frame, singing or not - silences too.
        this.timelineAccumulator += frame.level;
        this.timelineCount++;

        // Roughly every 10 frames (~6 samples/second) we store one averaged
        // point. Storing all 60/second would mean thousands of numbers per
        // song for a graph only a few hundred pixels wide.
        if (this.timelineCount >= 10) {
            this.timeline.push(
                Number((this.timelineAccumulator / this.timelineCount).toFixed(3))
            );
            this.timelineAccumulator = 0;
            this.timelineCount = 0;
        }

        if (!frame.isVoice || !this.insideWord(context.songTime ?? 0)) return;

        this.voiceFrames++;
        this.sum += frame.rms;
        this.sumOfSquares += frame.rms * frame.rms;
        this.peak = Math.max(this.peak, frame.rms);
    }

    summary() {
        if (this.voiceFrames < MIN_VOICE_FRAMES) {
            return { score: 0, mean: 0, dynamics: 0, presence: 0, timeline: this.timeline };
        }

        const mean = this.sum / this.voiceFrames;

        // Variance from running totals: E[x²] - (E[x])². This lets us compute
        // it in one pass, without keeping every sample in memory.
        // Math.max guards against a tiny negative from floating-point error.
        const variance = Math.max(0, this.sumOfSquares / this.voiceFrames - mean * mean);
        const variation = mean > 0 ? Math.sqrt(variance) / mean : 0;

        // Full marks inside the natural range, sloping to zero outside it.
        let dynamics = 1;
        if (variation < NATURAL_LOW) dynamics = variation / NATURAL_LOW;
        else if (variation > NATURAL_HIGH) {
            dynamics = Math.max(0, 1 - (variation - NATURAL_HIGH) / (ERRATIC - NATURAL_HIGH));
        }

        const presence = Math.min(1, mean / PRESENT_RMS);

        return {
            score: dynamics * 0.6 + presence * 0.4,
            mean,
            peak: this.peak,
            variation,
            dynamics,
            presence,
            timeline: this.timeline,
        };
    }
}
