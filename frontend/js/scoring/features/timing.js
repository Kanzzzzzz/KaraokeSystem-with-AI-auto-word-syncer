/**
 * timing.js - the timing score: did you sing each word, on cue?
 *
 * ===========================================================================
 * WHY WORD BY WORD
 * ===========================================================================
 *
 * The first version asked a coarser question every frame: "is the mic loud
 * while a line is on screen?". Tested on whole songs with simulated singers,
 * that was easy to fool - someone shouting nonstop and ignoring the song
 * outscored the real singer on 4 of 5 songs, random chatter came close, and
 * silence during instrumental breaks counted as a correct answer.
 *
 * Auto sync now gives every word its own time, so this score asks two
 * questions about EACH word instead:
 *
 *   entry  Did your voice come in near the start of the word? A voice
 *          "comes in" when it starts from quiet, or jumps clearly in
 *          loudness (a new syllable). This is what rhythm sounds like - and
 *          a steady shout has no entries at all.
 *   hold   Was there voice for a good part of the word's duration?
 *
 *   word score = 0.6 x entry + 0.4 x hold
 *
 * Then a penalty for singing through long instrumental breaks, where
 * nothing should be sung. Silence earns nothing by itself.
 *
 * On the same simulated singers the old score gave 72-84 to nonstop
 * shouting; this gives it 10 (before the 75-100 display scale in
 * scoreEngine.js).
 *
 * ===========================================================================
 * HOW IT IS COMPUTED
 * ===========================================================================
 *
 * Every frame is recorded as it arrives (time, loudness, voice or not);
 * the words are only judged at the end, in summary(). A song is a few
 * thousand frames, which is nothing to keep, and judging at the end means a
 * word can look slightly AHEAD in time - a voice that comes in 0.2 s early
 * still counts as on cue.
 */

import { buildWipeTimings, estimateSungDuration } from '../../player/lrcParser.js';

// A word counts as "entered on cue" if the voice comes in this close to it.
// Slightly more room after the start than before: people come in late more
// often than early. Words whose times are only ESTIMATED (a song with line
// timing but no word timing) get a wider window, since the estimate itself
// can be off.
const ENTRY_EARLY = 0.25;
const ENTRY_LATE = 0.35;
const ENTRY_WIDENED = 0.6;

// Loudness must rise by this factor within ONSET_LOOKBACK seconds to count
// as a new entry while already singing (a new syllable after a legato one).
const ONSET_RISE = 1.6;
const ONSET_LOOKBACK = 0.1;

// A word is "held" when voice fills at least this share of it. Only the
// first HOLD_MAX seconds of a long held note are judged - running out of
// breath on a 4-second note should not cost the whole word.
const HOLD_SHARE = 0.4;
const HOLD_MAX = 1.5;
const HOLD_MIN = 0.15;

const ENTRY_WEIGHT = 0.6;
const HOLD_WEIGHT = 0.4;

// Breaks longer than this, with no word in them, are instrumental: singing
// there costs up to GAP_PENALTY of the timing score.
const GAP_SECONDS = 3;
const GAP_MARGIN = 0.5;
const GAP_PENALTY = 0.3;

/**
 * Every word the singer is expected to sing, as { start, end, estimated },
 * in song order. Uses the same timings as the on-screen wipe
 * (lrcParser.buildWipeTimings), so the score judges exactly what the singer
 * was shown - real word times for an auto-synced or word-tapped song,
 * letter-paced estimates for a line-timed one.
 */
export function buildScoringTargets(lines) {
    const words = [];
    for (const line of lines) {
        const estimated = !(Array.isArray(line.words) && line.words.length > 0);

        // An estimated line's wipe runs all the way to the next line - across
        // an instrumental break too. For judging, its words are squeezed into
        // how long the line takes to SING (estimateSungDuration), so the
        // singer is not expected to keep going through the band's solo.
        let squeeze = 1;
        if (estimated) {
            const slot = Math.max(line.end - line.time, 0.2);
            squeeze = Math.min(1, estimateSungDuration(line.text) / slot);
        }
        const at = (t) => line.time + (t - line.time) * squeeze;

        for (const word of buildWipeTimings(line)) {
            words.push({ start: at(word.start), end: at(word.end), estimated });
        }
    }
    words.sort((a, b) => a.start - b.start);

    const gaps = [];
    for (let i = 0; i + 1 < words.length; i++) {
        const from = words[i].end + GAP_MARGIN;
        const to = words[i + 1].start - GAP_MARGIN;
        if (words[i + 1].start - words[i].end > GAP_SECONDS && to > from) {
            gaps.push({ start: from, end: to });
        }
    }
    return { words, gaps };
}

/** Index of the first entry in `times` (sorted) that is >= t. */
function lowerBound(times, t) {
    let low = 0;
    let high = times.length;
    while (low < high) {
        const mid = (low + high) >> 1;
        if (times[mid] < t) low = mid + 1;
        else high = mid;
    }
    return low;
}

export class WordTimingExtractor {
    constructor() {
        this.reset();
    }

    /** @param {{words: Array, gaps: Array}} targets - from buildScoringTargets() */
    reset(targets = { words: [], gaps: [] }) {
        this.targets = targets;
        this.frames = [];      // { t, rms, voice, entry } in the order they arrived
    }

    process(frame, context) {
        const t = context.songTime;
        const previous = this.frames[this.frames.length - 1];

        // Did the voice just come in? From quiet to voice - or, while
        // singing, a clear jump in loudness since a moment ago.
        let entry = false;
        if (frame.isVoice) {
            if (!previous || !previous.voice || t < previous.t) {
                entry = true;
            } else {
                let k = this.frames.length - 1;
                while (k > 0 && this.frames[k].t > t - ONSET_LOOKBACK) k--;
                entry = frame.rms > ONSET_RISE * Math.max(this.frames[k].rms, 1e-4);
            }
        }
        this.frames.push({ t, rms: frame.rms, voice: frame.isVoice, entry });
    }

    summary() {
        const { words, gaps } = this.targets;

        // Sort the frames by song time once (a seek back replays a stretch;
        // both passes are kept, which only ever helps the singer).
        const frames = [...this.frames].sort((a, b) => a.t - b.t);
        const times = frames.map((f) => f.t);
        const between = (from, to) => frames.slice(lowerBound(times, from), lowerBound(times, to));

        let judged = 0;
        let total = 0;
        let entered = 0;
        let held = 0;

        for (const word of words) {
            const holdEnd = Math.max(word.start + HOLD_MIN, Math.min(word.end, word.start + HOLD_MAX));
            const during = between(word.start, holdEnd);
            if (during.length === 0) continue;   // never played (seeked past): not judged

            const early = word.estimated ? ENTRY_WIDENED : ENTRY_EARLY;
            const late = word.estimated ? ENTRY_WIDENED : ENTRY_LATE;
            const entry = between(word.start - early, word.start + late).some((f) => f.entry) ? 1 : 0;
            const hold = during.filter((f) => f.voice).length / during.length >= HOLD_SHARE ? 1 : 0;

            judged++;
            entered += entry;
            held += hold;
            total += ENTRY_WEIGHT * entry + HOLD_WEIGHT * hold;
        }

        let gapFrames = 0;
        let gapVoice = 0;
        for (const gap of gaps) {
            const inside = between(gap.start, gap.end);
            gapFrames += inside.length;
            gapVoice += inside.filter((f) => f.voice).length;
        }
        const gapShare = gapFrames ? gapVoice / gapFrames : 0;

        if (judged === 0) {
            return { score: 0, words: 0, entered: 0, held: 0, gapShare };
        }

        return {
            score: Math.max(0, total / judged - GAP_PENALTY * gapShare),
            words: judged,
            entered: entered / judged,     // share of words come in on cue
            held: held / judged,           // share of words sung through
            gapShare,                      // share of instrumental breaks sung over
        };
    }
}
