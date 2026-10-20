/**
 * scoreReveal.js - the celebration at the end of a song.
 *
 * While a song plays, nothing about scoring is on screen: the microphone is
 * measured silently in the background (see main.js, startScoring) so the
 * singer can concentrate on singing. The score is only revealed here, once
 * the song has finished naturally - like a videoke machine's end screen:
 *
 *   1. the card pops in (a CSS scale + fade animation)
 *   2. the number counts up from 0 to the final score
 *   3. a chime plays (see videoke/chime.js)
 *   4. after AUTO_CLOSE_MS - or when "Next" is pressed - it closes, and the
 *      screen moves on to the next song in the queue
 *
 * A song that is SKIPPED never reaches this screen: no score, no chime.
 */

import { playChime } from '../videoke/chime.js';

/**
 * The words shown under the score. Placeholders - edit freely. The first
 * entry whose `min` the score reaches is used, so keep them highest first.
 *
 * Scores run from 75 to 100 (see SCORE_FLOOR in scoreEngine.js), so the
 * bands are spread over that range - every label is an encouraging one.
 */
export const REVEAL_LABELS = [
    { min: 95, label: 'Ang galing!' },
    { min: 90, label: 'Solid!' },
    { min: 85, label: 'Sige pa!' },
    { min: 80, label: 'Kaya mo yan!' },
    { min: 0,  label: 'Palakpakan!' },
];

export function revealLabel(score) {
    return (REVEAL_LABELS.find((entry) => score >= entry.min) ?? REVEAL_LABELS.at(-1)).label;
}

const AUTO_CLOSE_MS = 9000;    // long enough to read, short enough to keep the night moving
const COUNT_UP_MS = 1400;      // how long the number takes to climb

export class ScoreReveal {
    /**
     * @param {object} elements - { overlay, score, label, detail, next }
     */
    constructor({ overlay, score, label, detail, next }) {
        this.overlay = overlay;
        this.scoreEl = score;
        this.labelEl = label;
        this.detailEl = detail;
        this.nextButton = next;

        this.closeTimer = null;
        this.countFrame = null;

        /** Called when the reveal closes - the cue to start the next song. */
        this.onClose = null;

        this.nextButton.addEventListener('click', () => this.hide());
    }

    get isOpen() {
        return !this.overlay.hidden;
    }

    /**
     * @param {object} result - from ScoreEngine.finish()
     * @param {object} entry  - the queue entry that was sung
     * @param {object} [options]
     * @param {boolean} [options.queueEmpty] - label the button accordingly
     */
    show(result, entry, { queueEmpty = false } = {}) {
        const score = result.finalScore;

        this.scoreEl.textContent = '0';
        this.labelEl.textContent = revealLabel(score);
        this.detailEl.textContent = entry ? `${entry.singer_name} — ${entry.song_title}` : '';
        this.nextButton.textContent = queueEmpty ? 'Done' : 'Next song';

        // Removing and re-adding the class restarts the pop-in animation,
        // even if the previous reveal's animation had not finished.
        this.overlay.classList.remove('reveal--in');
        this.overlay.hidden = false;
        void this.overlay.offsetWidth;   // flush, so the removal is seen
        this.overlay.classList.add('reveal--in');

        this.countUp(score);
        playChime();

        // Focus the button so Enter or Space moves on straight away.
        this.nextButton.focus({ preventScroll: true });

        clearTimeout(this.closeTimer);
        this.closeTimer = setTimeout(() => this.hide(), AUTO_CLOSE_MS);
    }

    /**
     * Count the number up with an ease-out curve: fast at first, settling
     * onto the final score. Only the text changes, and only when the whole
     * number does - about 60 small updates in total.
     */
    countUp(target) {
        cancelAnimationFrame(this.countFrame);
        const start = performance.now();
        let shown = 0;

        const step = (now) => {
            const t = Math.min(1, (now - start) / COUNT_UP_MS);
            const eased = 1 - Math.pow(1 - t, 3);
            const value = Math.round(target * eased);

            if (value !== shown) {
                shown = value;
                this.scoreEl.textContent = String(value);
            }
            if (t < 1) this.countFrame = requestAnimationFrame(step);
        };

        this.countFrame = requestAnimationFrame(step);
    }

    /** Close and move on (calls onClose). */
    hide() {
        if (!this.isOpen) return;
        this.dismiss();
        this.onClose?.();
    }

    /**
     * Close WITHOUT moving on - for when something else has already moved
     * the queue along (another screen advanced it, say).
     */
    dismiss() {
        clearTimeout(this.closeTimer);
        cancelAnimationFrame(this.countFrame);
        this.overlay.classList.remove('reveal--in');
        this.overlay.hidden = true;
    }
}
