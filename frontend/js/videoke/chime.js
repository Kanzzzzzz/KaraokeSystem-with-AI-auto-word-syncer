/**
 * chime.js - the happy "ding" when a score is revealed.
 *
 * AUTOPLAY RULES
 * Browsers refuse to play sound from a page the user has not interacted
 * with yet (a click, tap or key press). On the karaoke screen that is almost
 * never a problem - the song itself could not have played without one - but
 * phones and Safari are stricter: they allow sound per ELEMENT, only once
 * that element has been started during an interaction.
 *
 * So on the first interaction anywhere on the page, the chime is played
 * silently and stopped straight away. That "unlocks" it, and the real play
 * later - which happens when a song ends, with nobody touching anything -
 * is allowed. If a browser still refuses, the reveal simply stays silent;
 * nothing breaks and no error is shown.
 */

import { SCORE_CHIME_URL, SCORE_CHIME_VOLUME } from './assets.js';

let chime = null;

if (SCORE_CHIME_URL) {
    chime = new Audio(SCORE_CHIME_URL);
    chime.preload = 'auto';          // ready to play the instant it is needed
    chime.volume = SCORE_CHIME_VOLUME;
}

/** Call once at startup. Unlocks the chime on the first interaction. */
export function unlockChimeOnFirstGesture() {
    if (!chime) return;

    const unlock = () => {
        window.removeEventListener('pointerdown', unlock, true);
        window.removeEventListener('keydown', unlock, true);

        chime.muted = true;
        chime.play()
            .then(() => {
                chime.pause();
                chime.currentTime = 0;
            })
            .catch(() => { /* still locked - playChime() copes */ })
            .finally(() => { chime.muted = false; });
    };

    // Capture phase, so it runs even if something else stops the event.
    window.addEventListener('pointerdown', unlock, true);
    window.addEventListener('keydown', unlock, true);
}

/** Play the chime from the start. Silently does nothing if blocked. */
export function playChime() {
    if (!chime) return;
    chime.muted = false;
    chime.currentTime = 0;
    chime.play().catch(() => { /* blocked by autoplay rules - stay silent */ });
}
