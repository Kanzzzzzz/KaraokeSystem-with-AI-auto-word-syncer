/**
 * wordTiming.js - the rules of word-by-word timing, with no screen code.
 *
 * The sync editor (syncEditor.js) handles keys, clicks and drawing; every
 * decision about the timing itself lives here instead. Keeping the two apart
 * means these rules can be tested on their own, without a browser, and the
 * editor file stays about the interface.
 *
 * ---------------------------------------------------------------------------
 * THE EDITOR'S MODEL OF A SONG
 * ---------------------------------------------------------------------------
 *
 * One entry per lyric-sheet line:
 *
 *     { text:     "I want to know",
 *       kind:     "lyric" | "section",     // [Chorus] etc. are never timed
 *       time:     12.40 | null,            // when the line starts
 *       baseTime: 12.40 | null,            // the line's own time, used when
 *                                          //   its first word has none
 *       words:    [{ text: "I", time: 12.40 | null,
 *                    review: null | { flags, score } }, ...],
 *       wordsEnd: 13.90 | null }           // when the singing of it stops
 *
 * `review` is set on words that AUTO SYNC was unsure of, so the editor can
 * highlight them for checking (see the "Auto sync results" section below).
 * Changing a word's time by hand clears it: you have checked that word.
 *
 * A line's time always follows its first word once that word is timed - a
 * line starts when its first word is sung. `baseTime` is what the line falls
 * back to if that word's time is removed again.
 *
 * A word position is { l, w }: line index and word index within it.
 */

// How long the last word of a line is assumed to last when its end has not
// been marked with K. The last word is very often held, and 1.5s is a typical
// held note. It is always cut short at the next line.
const HELD_LAST_WORD_SECONDS = 1.5;

/* ==========================================================================
   Building the model
   ========================================================================== */

/** "I want to know" -> [{text:"I", time:null}, {text:"want", time:null}, ...] */
function splitWords(text) {
    return (text || '')
        .split(/\s+/)
        .filter(Boolean)
        .map((word) => ({ text: word, time: null, review: null }));
}

/**
 * Turn the lyrics API's answer into the editor's model.
 *
 * Existing word timings are loaded only if they fit the line's words exactly.
 * If the text was edited since they were made, they belong to different words,
 * and loading them would pin the wrong times to the wrong words.
 */
export function createEditorLines(lyricData) {
    if (lyricData.format === 'synced') {
        return lyricData.lines.map((line) => {
            const words = splitWords(line.text);
            const saved = Array.isArray(line.words) ? line.words : [];

            const fits =
                saved.length === words.length &&
                saved.every((savedWord, i) => savedWord.text === words[i].text);

            if (fits) {
                saved.forEach((savedWord, i) => { words[i].time = savedWord.time; });
            }

            return {
                text: line.text,
                kind: 'lyric',
                time: line.time,
                baseTime: line.time,
                words,
                wordsEnd: fits ? (line.words_end ?? null) : null,
            };
        });
    }

    // Plain lyrics: nothing is timed yet. Section labels get no words.
    return lyricData.lines.map((line) => ({
        text: line.text,
        kind: line.kind,
        time: null,
        baseTime: null,
        words: line.kind === 'lyric' ? splitWords(line.text) : [],
        wordsEnd: null,
    }));
}

/* ==========================================================================
   Moving between words
   ========================================================================== */

/** Every word position in the song, in singing order. */
export function allPositions(lines) {
    const positions = [];
    lines.forEach((line, l) => {
        line.words.forEach((_, w) => positions.push({ l, w }));
    });
    return positions;
}

function indexOf(positions, pos) {
    return positions.findIndex((p) => p.l === pos.l && p.w === pos.w);
}

/** The word after `pos`, crossing into the next line; null after the last. */
export function nextPosition(lines, pos) {
    const positions = allPositions(lines);
    const i = indexOf(positions, pos);
    return i >= 0 && i + 1 < positions.length ? positions[i + 1] : null;
}

/** The word before `pos`; null before the first. */
export function previousPosition(lines, pos) {
    const positions = allPositions(lines);
    const i = indexOf(positions, pos);
    return i > 0 ? positions[i - 1] : null;
}

/** The first word without a time - where tapping should start. */
export function firstUntimedPosition(lines) {
    return allPositions(lines).find((p) => lines[p.l].words[p.w].time === null) ?? null;
}

/* ==========================================================================
   Changing times
   ========================================================================== */

/** Keep a line's time in step with its first word (see the top of the file). */
export function syncLineTime(line) {
    const first = line.words[0];
    line.time = first && first.time !== null ? first.time : line.baseTime;
}

/** Record when a word is sung. */
export function stampWord(lines, pos, time) {
    const line = lines[pos.l];
    line.words[pos.w].time = Math.max(0, time);
    line.words[pos.w].review = null;
    syncLineTime(line);
}

/** Move one word's time; never below zero. */
export function nudgeWord(line, w, seconds) {
    const word = line.words[w];
    if (word.time === null) return false;
    word.time = Math.max(0, word.time + seconds);
    word.review = null;
    syncLineTime(line);
    return true;
}

/**
 * Give a whole line a new start time - the line-mode stamp and nudge.
 *
 * Any word timings in the line move by the same amount, so their spacing is
 * kept: re-timing a line in line mode carries its words along instead of
 * leaving them pinned to the old position.
 */
export function setLineTime(line, time) {
    time = Math.max(0, time);
    const delta = line.time === null ? 0 : time - line.time;

    for (const word of line.words) {
        if (word.time !== null) word.time = Math.max(0, word.time + delta);
        // Re-timing the whole line by hand counts as checking its words.
        if (delta !== 0) word.review = null;
    }
    if (line.wordsEnd !== null) line.wordsEnd = Math.max(0, line.wordsEnd + delta);

    line.baseTime = time;
    line.time = time;
}

/**
 * Mark where the singing of a line stops (the K key).
 *
 * This is what ends the wipe on time before an instrumental break, instead of
 * the last word crawling on until the next line starts. It must come after
 * the line's last word, so every word has to be timed first.
 */
export function markLineEnd(line, time) {
    if (!isFullyTimed(line)) {
        return 'Time every word in this line first.';
    }
    const lastStart = line.words[line.words.length - 1].time;
    if (time <= lastStart) {
        return 'The end must come after the last word starts.';
    }
    line.wordsEnd = time;
    return null;
}

/** Clear a line's word timings so it can be tapped again (the R key). */
export function clearLineWords(line) {
    for (const word of line.words) {
        word.time = null;
        word.review = null;
    }
    line.wordsEnd = null;
    syncLineTime(line);
}

/* ==========================================================================
   Undo
   ========================================================================== */

/**
 * Everything about a line's timing, so an action can be reversed exactly.
 * The editor stores one of these before each stamp and restores it on undo.
 */
export function snapshotLine(line) {
    return {
        time: line.time,
        baseTime: line.baseTime,
        wordTimes: line.words.map((word) => word.time),
        reviews: line.words.map((word) => word.review),
        wordsEnd: line.wordsEnd,
    };
}

export function restoreLine(line, snapshot) {
    line.time = snapshot.time;
    line.baseTime = snapshot.baseTime;
    snapshot.wordTimes.forEach((time, i) => { line.words[i].time = time; });
    snapshot.reviews.forEach((review, i) => { line.words[i].review = review; });
    line.wordsEnd = snapshot.wordsEnd;
}

/* ==========================================================================
   Checking and saving
   ========================================================================== */

/** A line counts as word-timed only when every one of its words has a time. */
export function isFullyTimed(line) {
    return line.words.length > 0 && line.words.every((word) => word.time !== null);
}

/**
 * Find the first word that comes earlier than the word before it.
 *
 * Words out of order cannot be drawn - the wipe would have to run backwards -
 * so saving is refused until they are fixed. Usually the cause is a tap that
 * landed after seeking back. Returns { l, w, message } or null.
 */
export function findOrderProblem(lines) {
    for (let l = 0; l < lines.length; l++) {
        const words = lines[l].words;
        for (let w = 1; w < words.length; w++) {
            const before = words[w - 1].time;
            const here = words[w].time;
            if (before !== null && here !== null && here < before) {
                return {
                    l, w,
                    message: `"${words[w].text}" is timed before "${words[w - 1].text}".`,
                };
            }
        }
    }
    return null;
}

/**
 * Where the last word of a line stops when K was not pressed: after a typical
 * held note, but never later than the next line's start.
 */
export function autoWordsEnd(line, nextLineTime) {
    const lastStart = line.words[line.words.length - 1].time;
    let end = lastStart + HELD_LAST_WORD_SECONDS;
    if (nextLineTime !== null && nextLineTime !== undefined) {
        end = Math.min(end, nextLineTime);
    }
    // Always leave the last word at least a sliver of time to fill in.
    return Math.max(end, lastStart + 0.05);
}

/**
 * The lines to send to the server when saving.
 *
 * Every timed line is sent with its start time. A line whose words are ALL
 * timed also carries them, plus where its singing ends. A line with only some
 * words timed is saved with its line time alone - half a line of word timings
 * would leave the untimed words wiping at a guessed pace in the middle of real
 * ones - and is counted in `partial` so the editor can say so.
 */
export function buildSavePayload(lines) {
    const out = [];
    let partial = 0;

    lines.forEach((line, l) => {
        if (line.kind === 'section' || line.time === null) return;

        const entry = { time: line.time, text: line.text };

        if (isFullyTimed(line)) {
            const next = lines.slice(l + 1).find((n) => n.kind !== 'section' && n.time !== null);
            entry.words = line.words.map((word) => ({ time: word.time, text: word.text }));
            entry.words_end = line.wordsEnd ?? autoWordsEnd(line, next ? next.time : null);
        } else if (line.words.some((word) => word.time !== null)) {
            partial++;
        }

        out.push(entry);
    });

    return { lines: out, partial };
}

/** Counts for the progress display. */
export function progress(lines) {
    const lyricLines = lines.filter((line) => line.kind !== 'section');
    const words = lyricLines.flatMap((line) => line.words);
    return {
        linesTimed: lyricLines.filter((line) => line.time !== null).length,
        linesTotal: lyricLines.length,
        wordsTimed: words.filter((word) => word.time !== null).length,
        wordsTotal: words.length,
        linesWordTimed: lyricLines.filter(isFullyTimed).length,
    };
}

/* ==========================================================================
   Auto sync results
   ==========================================================================

   Auto sync saves each word's confidence in the song's .autosync.json (the
   server sends it as `result`). Two uses here:

     markForReview()  the result was APPLIED to the .lrc: flag the words it
                      was unsure of, so they can be checked
     applyDraft()     the result is a DRAFT (the song had hand-tapped words):
                      load its times into the editor, flags included

   Lines are matched by their text and the nearest time, so a chorus sung
   three times gets each repeat's own result.
   ========================================================================== */

// Plain-English reasons for the flags auto sync puts on words.
const REVIEW_REASONS = {
    weak: 'the model barely heard this word',
    reestimated: 'the model could not place it, so it was put between its neighbours',
    unaligned: 'this line could not be heard, so it wipes at an even pace',
    outside_line: "it is well outside LRCLIB's time for this line",
    order_fixed: 'it was moved to keep the words in order',
    no_letters: 'it has no letters to listen for',
    second_opinion: 'the alignment model could not hear this line, so Whisper timed it',
};

/** A tooltip saying why a word is highlighted for checking. */
export function reviewReason(review) {
    if (!review) return '';
    const reasons = review.flags.map((flag) => REVIEW_REASONS[flag]).filter(Boolean);
    if (reasons.length === 0) {
        reasons.push(`the model was unsure of it (confidence ${Math.round(review.score * 100)}%)`);
    }
    return `Check this word: ${reasons.join('; ')}.`;
}

const textKey = (text) => (text || '').toLowerCase().replace(/[^a-z0-9]/g, '');

/** Pair each editor line with the result line for it: Map(editor index -> result line). */
function matchResultLines(lines, result) {
    const pool = result.lines.filter((line) => line.words && line.words.length > 0);
    const pairs = new Map();

    lines.forEach((line, l) => {
        if (line.kind === 'section' || line.words.length === 0) return;
        const candidates = pool.filter((r) => textKey(r.text) === textKey(line.text));
        if (candidates.length === 0) return;

        const reference = line.time ?? line.baseTime;
        const best = reference === null
            ? candidates[0]
            : candidates.reduce((a, b) =>
                Math.abs(b.time - reference) < Math.abs(a.time - reference) ? b : a);
        if (best.words.length !== line.words.length) return;

        pool.splice(pool.indexOf(best), 1);
        pairs.set(l, best);
    });
    return pairs;
}

function reviewOf(resultWord) {
    return resultWord.confidence === 'low'
        ? { flags: resultWord.flags || [], score: resultWord.score ?? 0 }
        : null;
}

/**
 * Flag the low-confidence words of an applied result - but only words whose
 * time is still exactly what auto sync wrote. One you have already changed
 * has been checked. Returns how many words were flagged.
 */
export function markForReview(lines, result) {
    if (!result || result.reviewed) return 0;
    let count = 0;
    for (const [l, resultLine] of matchResultLines(lines, result)) {
        lines[l].words.forEach((word, w) => {
            const resultWord = resultLine.words[w];
            // The .lrc stores hundredths, the result thousandths: allow for rounding.
            const untouched = word.time !== null && Math.abs(word.time - resultWord.start) < 0.011;
            word.review = untouched ? reviewOf(resultWord) : null;
            if (word.review) count++;
        });
    }
    return count;
}

/**
 * Load a draft result's word times into the editor, replacing the times of
 * every line it has a result for. Returns { lines, flagged }.
 */
export function applyDraft(lines, result) {
    let changed = 0;
    let flagged = 0;
    for (const [l, resultLine] of matchResultLines(lines, result)) {
        const line = lines[l];
        line.words.forEach((word, w) => {
            word.time = resultLine.words[w].start;
            word.review = reviewOf(resultLine.words[w]);
            if (word.review) flagged++;
        });
        line.wordsEnd = resultLine.words_end ?? null;
        syncLineTime(line);
        changed++;
    }
    return { lines: changed, flagged };
}

/** How many words are still highlighted for checking. */
export function reviewCount(lines) {
    return lines.reduce((sum, line) => sum + line.words.filter((w) => w.review).length, 0);
}

/** The next highlighted word after `pos` (wrapping to the start), or null. */
export function nextReviewPosition(lines, pos) {
    const positions = allPositions(lines).filter((p) => lines[p.l].words[p.w].review);
    if (positions.length === 0) return null;
    if (!pos) return positions[0];
    return positions.find((p) => p.l > pos.l || (p.l === pos.l && p.w > pos.w)) ?? positions[0];
}
