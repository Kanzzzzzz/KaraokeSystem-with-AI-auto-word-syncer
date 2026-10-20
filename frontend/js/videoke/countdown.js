/**
 * countdown.js - the big 3-2-1 before the first line is sung.
 *
 * During a song's instrumental intro the singer has nothing to read and no
 * idea when to come in. Videoke machines solve that with a countdown, and so
 * does this: large numbers in the middle of the screen, the last one ending
 * exactly as the first lyric line lights up.
 *
 * WHERE THE TIMING COMES FROM
 * LRC files have no "intro" or "count-in" marker - only the time each line
 * starts. So the countdown is anchored to the first line that has words in
 * it (blank lines are instrumental markers, not singing), and covers the
 * three seconds before it:
 *
 *      song time:   ...  t-3      t-2      t-1       t   (first line)
 *      on screen:         [ 3 ]    [ 2 ]    [ 1 ]    lyrics light up
 *
 * It is exactly as accurate as that first timestamp - see the note in the
 * README about LRCLIB timing, and fix a late or early first line in the
 * sync editor.
 *
 * SHORT INTROS
 * With less than three seconds of intro there is no room for a full count,
 * so it starts lower: a 2.4 s intro counts "2, 1". Under one second there is
 * no countdown at all - a lone "1" flashing for half a second would only
 * startle the singer.
 *
 * It is driven by the playback time (not a separate clock), so pausing,
 * seeking and resuming all keep it in step with the music automatically.
 */

const COUNT_FROM = 3;

/**
 * The time of the first line that is actually sung, or null when there is
 * none. Blank lines ("♪" instrumental markers) are skipped.
 */
export function firstSungLineTime(lines) {
    const first = lines.find((line) => (line.text ?? '').trim() !== '');
    return first ? first.time : null;
}

/**
 * Which number should be showing: 3, 2, 1, or 0 for none.
 *
 * Exported separately so it can be tested without a browser.
 */
export function countdownNumber(target, currentTime) {
    if (target === null || currentTime <= 0) return 0;

    const remaining = target - currentTime;
    if (remaining <= 0) return 0;

    // The count can only start as high as the intro has whole seconds for.
    const highest = Math.min(COUNT_FROM, Math.floor(target));
    const number = Math.ceil(remaining);
    return number <= highest ? number : 0;
}

export class Countdown {
    /**
     * @param {HTMLElement} element - the countdown box (#countdown)
     * @param {import('./performanceZone.js').PerformanceZone} zone
     */
    constructor(element, zone) {
        this.element = element;
        this.zone = zone;
        this.target = null;
        this.shown = 0;
        this.reached = false;

        // Called when the count starts.
        this.onStart = null;
        // Called once when the first sung line begins - with or without a
        // countdown before it (a very short intro has none).
        this.onLineStart = null;
    }

    /** Set the song's first sung line time, or null for no countdown. */
    setTarget(time) {
        this.target = Number.isFinite(time) ? time : null;
        this.reached = false;
        this.render(0);
    }

    /** Freeze the number's animation while the song is paused. */
    setPaused(paused) {
        this.element.classList.toggle('countdown--paused', paused);
    }

    /** Called every animation frame with the playback time. */
    update(currentTime) {
        const number = countdownNumber(this.target, currentTime);
        if (number !== this.shown) this.render(number);

        if (!this.reached && this.target !== null && currentTime >= this.target) {
            this.reached = true;
            this.onLineStart?.();
        }
    }

    render(number) {
        const starting = this.shown === 0 && number > 0;
        this.shown = number;

        if (number === 0) {
            this.element.classList.remove('countdown--on');
            this.element.replaceChildren();
            this.zone.setCounting(false);
            return;
        }

        // A fresh element per number restarts its CSS pop animation - an
        // animation only replays when the element it is on is new.
        const digit = document.createElement('span');
        digit.className = 'countdown__number';
        digit.textContent = String(number);
        this.element.replaceChildren(digit);
        this.element.classList.add('countdown--on');

        if (starting) this.onStart?.();
        this.zone.setCounting(true);
    }
}
