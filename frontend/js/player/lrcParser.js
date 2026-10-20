/**
 * lrcParser.js - turns LRC text into timed lyric lines.
 *
 * LRC is the standard timestamped-lyric format:
 *
 *     [ti:Sunflower]
 *     [00:12.40]I want to know
 *     [00:15.10]Where I can go
 *
 * There is a matching parser in Python (backend/utils/lrc.py) used when
 * saving files. This one exists so the browser can parse lyrics itself
 * without asking the server to do it.
 */

// [mm:ss.xx] - the fractional part is optional in the LRC spec.
const TIMESTAMP_RE = /\[(\d+):(\d{1,2})(?:[.:](\d{1,3}))?\]/g;

// <mm:ss.xx> - a WORD timestamp, from the "Enhanced LRC" (A2) format:
//
//     [00:12.40]<00:12.40>Woah <00:12.80>I <00:13.00>want <00:13.40>
//
// Angle brackets rather than square ones, so word tags can never be confused
// with the line timestamp. A bare trailing tag (one with no word after it)
// marks where the last word stops.
//
// This is what lets the app use REAL measured word timings when a file has
// them, instead of estimating them from word length. Enhanced LRC is a strict
// superset of plain LRC, so files without these tags keep working unchanged.
const WORD_RE = /<(\d+):(\d{1,2})(?:[.:](\d{1,3}))?>/g;

// [ti:Title], [ar:Artist] and friends. Letters only, which distinguishes
// metadata from a timestamp (digits).
const METADATA_RE = /^\[([a-zA-Z]+):(.*)\]$/;

/**
 * Turn the three captured parts of a timestamp into seconds.
 *
 * ".4" is tenths, ".40" hundredths, ".400" thousandths - dividing by
 * 10^length handles all three without special-casing.
 */
function toSeconds(minutes, seconds, fraction) {
    let time = parseInt(minutes, 10) * 60 + parseInt(seconds, 10);
    if (fraction) {
        time += parseInt(fraction, 10) / Math.pow(10, fraction.length);
    }
    return time;
}

/**
 * Pull word timings out of a line body, if it has any.
 *
 * Returns { text, words, wordsEnd } where `text` is always the plain lyric
 * with every tag stripped - so display, scoring and the length-based estimator
 * all behave exactly as they did before, whether or not word tags are present.
 *
 * `words` is null for an ordinary line, which is the signal to fall back to
 * estimating word timings.
 */
function parseWordTags(body) {
    const matches = [...body.matchAll(WORD_RE)];

    if (matches.length === 0) {
        return { text: body.trim(), words: null, wordsEnd: null };
    }

    const words = [];
    let wordsEnd = null;

    for (let i = 0; i < matches.length; i++) {
        const match = matches[i];
        const time = toSeconds(match[1], match[2], match[3]);

        // The word is whatever sits between the end of this tag and the start
        // of the next one (or the end of the line for the last tag).
        const from = match.index + match[0].length;
        const to = i + 1 < matches.length ? matches[i + 1].index : body.length;
        const word = body.slice(from, to).trim();

        if (word) {
            words.push({ text: word, time });
        } else if (i === matches.length - 1) {
            // Nothing after the final tag: it marks the end of the last word
            // rather than introducing a new one.
            wordsEnd = time;
        }
    }

    // Replace tags with a SPACE, not an empty string: a file written without
    // spaces between words ("<00:01.0>Hello<00:01.5>there") would otherwise
    // collapse into "Hellothere".
    const text = body.replace(WORD_RE, ' ').replace(/\s+/g, ' ').trim();

    return { text, words: words.length > 0 ? words : null, wordsEnd };
}

/**
 * Parse LRC text into { metadata, lines }.
 *
 * Lines are returned sorted by time, each with an `end` time so the renderer
 * knows how long a line stays on screen. A line ends when the next one
 * begins; the final line gets a fixed few seconds since nothing follows it.
 */
export function parseLrc(text) {
    const metadata = {};
    const lines = [];

    for (const rawLine of text.split(/\r?\n/)) {
        const trimmed = rawLine.trim();
        if (!trimmed) continue;

        const metaMatch = trimmed.match(METADATA_RE);
        if (metaMatch) {
            metadata[metaMatch[1].toLowerCase()] = metaMatch[2].trim();
            continue;
        }

        // matchAll needs the /g flag; lastIndex is reset automatically
        // because we build a fresh iterator each time.
        const stamps = [...trimmed.matchAll(TIMESTAMP_RE)];
        if (stamps.length === 0) continue;

        // Strip the line timestamps, then read any word timings from the rest.
        const body = trimmed.replace(TIMESTAMP_RE, '');
        const { text: lyricText, words, wordsEnd } = parseWordTags(body);

        // One line can carry several timestamps (a repeated chorus written
        // once). Each timestamp becomes its own entry.
        stamps.forEach(([, minutes, seconds, fraction], stampIndex) => {
            // Word timings are ABSOLUTE times, so they can only describe the
            // first place this line appears. A chorus written once but stamped
            // for three playbacks gets real word timings on the first and
            // falls back to estimation on the repeats.
            const isFirst = stampIndex === 0;

            lines.push({
                time: toSeconds(minutes, seconds, fraction),
                text: lyricText,
                words: isFirst ? words : null,
                wordsEnd: isFirst ? wordsEnd : null,
            });
        });
    }

    lines.sort((a, b) => a.time - b.time);

    // Give every line an end time, which the glow effect uses to know how
    // far through a line the singer is.
    const FINAL_LINE_SECONDS = 4;
    lines.forEach((line, index) => {
        const next = lines[index + 1];
        line.end = next ? next.time : line.time + FINAL_LINE_SECONDS;
        line.index = index;
    });

    return { metadata, lines };
}

/**
 * Seconds as "mm:ss.xx" - the inside of an LRC timestamp.
 *
 * Uses exactly the same arithmetic as format_timestamp() in
 * backend/utils/lrc.py - floor(seconds * 100 + 0.5), in whole hundredths -
 * so the sync editor's preview matches the saved file character for
 * character. Rounding once up front also handles 59.996 on its own: it
 * becomes 6000 hundredths, "01:00.00", never "00:59.100".
 */
function formatClock(seconds) {
    let total = Math.floor(Math.max(0, seconds) * 100 + 0.5);   // whole hundredths
    const hundredths = total % 100;
    total = (total - hundredths) / 100;                    // whole seconds
    const secs = total % 60;
    const minutes = (total - secs) / 60;

    const pad = (n) => String(n).padStart(2, '0');
    return `${pad(minutes)}:${pad(secs)}.${pad(hundredths)}`;
}

/**
 * Write timed lines back out as LRC text - the reverse of parseLrc().
 *
 * Takes the same shape the server's save route accepts:
 *     [{ time, text, words?: [{ time, text }], words_end? }]
 *
 * The sync editor uses this for its live preview, so the preview is drawn
 * from exactly the file that Save would write. That is also why lines are
 * sorted by time first: the server's build_lrc() sorts them the same way.
 */
export function formatLrc(lines) {
    // Sort a copy - the caller's array is left in its own order. Array sort
    // is stable, so equal times keep their order, as in Python's sort.
    const inTimeOrder = [...lines].sort((a, b) => a.time - b.time);

    return inTimeOrder.map((line) => {
        const stamp = `[${formatClock(line.time)}]`;

        if (!line.words || line.words.length === 0) {
            return stamp + line.text;
        }

        const parts = line.words.map((word) => `<${formatClock(word.time)}>${word.text}`);
        if (line.words_end !== null && line.words_end !== undefined) {
            parts.push(`<${formatClock(line.words_end)}>`);
        }
        return stamp + parts.join(' ');
    }).join('\n') + '\n';
}

/**
 * Find which line should be showing at a given playback time.
 *
 * Returns the line's index, or -1 before the first line starts.
 *
 * `hint` is the index we found last frame. Because playback moves forward,
 * checking the hint first usually answers in one comparison instead of
 * scanning the whole array 60 times a second. When the user seeks, the hint
 * is wrong and we fall back to a binary search.
 */
export function findActiveLine(lines, currentTime, hint = 0) {
    if (lines.length === 0) return -1;

    // Fast path: still on the hinted line.
    const hinted = lines[hint];
    if (hinted && currentTime >= hinted.time && currentTime < hinted.end) {
        return hint;
    }

    // Fast path: moved on to the next one (the common case).
    const nextLine = lines[hint + 1];
    if (nextLine && currentTime >= nextLine.time && currentTime < nextLine.end) {
        return hint + 1;
    }

    if (currentTime < lines[0].time) return -1;

    // Slow path (a seek): binary search, O(log n) instead of O(n).
    let low = 0;
    let high = lines.length - 1;

    while (low <= high) {
        const mid = (low + high) >> 1;   // >> 1 is a fast integer divide by 2

        if (currentTime < lines[mid].time) {
            high = mid - 1;
        } else if (currentTime >= lines[mid].end) {
            low = mid + 1;
        } else {
            return mid;
        }
    }

    // Between lines (an instrumental gap): report the last line that started,
    // so the display holds rather than going blank.
    return Math.max(0, Math.min(high, lines.length - 1));
}

/**
 * Estimate how long a line actually takes to SING. Used by the scoring
 * (scoring/features/timing.js) for lines that have no word timing.
 *
 * This is deliberately not the same as the line's `end`. A line's end is
 * simply when the next line starts, which is fine for deciding how long to
 * keep words on screen - but it is wrong for scoring. If a verse is followed
 * by a 30-second instrumental break, the last line's end is 30 seconds away,
 * and treating all of that as "you should be singing" would punish the
 * singer for the band's solo.
 *
 * So the duration is estimated from the text instead. Sung English runs at
 * very roughly 9 characters per second; the bounds keep very short lines
 * ("Oh!") and very long ones within sensible limits.
 */
export function estimateSungDuration(text) {
    const SECONDS_PER_CHARACTER = 0.11;
    const MINIMUM = 1.5;
    const MAXIMUM = 10;

    const estimate = (text?.length ?? 0) * SECONDS_PER_CHARACTER;
    return Math.min(MAXIMUM, Math.max(MINIMUM, estimate));
}

/**
 * Work out when every letter of a line fills, for the videoke-style wipe.
 *
 * Returns one entry per word, in the shape lyricsRenderer.js draws from:
 *
 *     [{ text, start, end, el: null,
 *        letters: [{ char, start, end, el: null }, ...] }, ...]
 *
 * `el` starts empty; the renderer fills it in with the <span> it creates.
 * A blank line returns [] and the renderer shows a music note instead.
 *
 * Two sources of timing, best first:
 *
 *   1. Real word timings (an Enhanced LRC line, which has `line.words`).
 *      Each word runs from its own timestamp until the next word's, and its
 *      letters share that span evenly. The wipe then follows how the song
 *      was actually sung.
 *
 *   2. Line timing only (every LRCLIB file, and every tap-synced file).
 *      The whole line's slot - from its timestamp to the next line's - is
 *      shared evenly between its letters, so the wipe moves at one steady
 *      pace, the way a videoke box wipes a line.
 */
export function buildWipeTimings(line) {
    if (Array.isArray(line.words) && line.words.length > 0) {
        return line.words.map((word, index) => {
            const next = line.words[index + 1];

            // A word lasts until the next word starts. The last word runs to
            // the file's end-of-line marker if it has one, else to the end
            // of the line's slot.
            let end = next ? next.time : (line.wordsEnd ?? line.end);

            // Guard against a malformed file giving a zero or backwards span,
            // which would make the letters' progress divide by zero.
            end = Math.max(end, word.time + 0.05);

            return spreadLetters(word.text, word.time, end);
        });
    }

    const words = (line.text || '').split(/\s+/).filter(Boolean);
    if (words.length === 0) return [];

    // Count letters with Array.from rather than .length: .length counts
    // UTF-16 code units, so an emoji or other symbol outside the basic range
    // would be counted as two letters and throw the pacing off.
    const totalLetters = words.reduce((sum, w) => sum + Array.from(w).length, 0);
    const slot = Math.max(line.end - line.time, 0.2);
    const perLetter = slot / totalLetters;

    // Walk along the line, giving each word the run of time its letters use.
    let cursor = line.time;
    return words.map((word) => {
        const start = cursor;
        cursor += Array.from(word).length * perLetter;
        return spreadLetters(word, start, cursor);
    });
}

/**
 * One word's entry: its letters share the word's time span evenly, so they
 * fill one after another from left to right.
 */
function spreadLetters(text, start, end) {
    const chars = Array.from(text);
    const step = (end - start) / chars.length;

    return {
        text,
        start,
        end,
        el: null,
        letters: chars.map((char, i) => ({
            char,
            start: start + step * i,
            end: start + step * (i + 1),
            el: null,
        })),
    };
}
