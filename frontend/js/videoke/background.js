/**
 * background.js - the looping video behind everything, videoke style.
 *
 * Real videoke machines play generic scenic or mood loops behind the lyrics.
 * This puts one full-screen <video> at the very back of the page.
 *
 * FALLBACK
 * The page's own CSS background (a still navy/maroon gradient) is always
 * underneath. If the video is missing, fails to load, or the viewer has
 * asked their system for reduced motion, the <video> is simply removed and
 * that still gradient shows instead - the screen never goes blank or breaks.
 *
 * COST
 * A muted looping video is about the cheapest moving background there is:
 * browsers decode it on the graphics chip and draw it as its own layer, so
 * it adds almost nothing to the page's own drawing work. An animated CSS
 * gradient, by comparison, has to be repainted by the browser every frame.
 * The legibility scrim on top of it is a STILL gradient, painted once.
 */

import { BACKGROUND_VIDEO_URL } from './assets.js';

/**
 * @param {HTMLVideoElement} video - the <video> element in index.html
 */
export function startBackground(video) {
    const stage = video.parentElement;

    const giveUp = () => {
        stage.classList.add('bg-stage--still');
        video.remove();
    };

    const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (!BACKGROUND_VIDEO_URL || reducedMotion) {
        giveUp();
        return;
    }

    // muted + playsInline are what browsers require before they will start a
    // video on their own (no click needed) - including on phones.
    video.muted = true;
    video.defaultMuted = true;
    video.loop = true;
    video.playsInline = true;
    video.addEventListener('error', giveUp, { once: true });
    video.src = BACKGROUND_VIDEO_URL;

    video.play().catch(() => {
        // Some browsers in power-saving mode refuse even muted autoplay. The
        // first frame still shows; try again on the first interaction.
        const retry = () => video.play().catch(() => {});
        window.addEventListener('pointerdown', retry, { once: true });
        window.addEventListener('keydown', retry, { once: true });
    });

    // A hidden tab has nobody watching - stop decoding until it is back.
    document.addEventListener('visibilitychange', () => {
        if (!video.isConnected) return;
        if (document.hidden) video.pause();
        else video.play().catch(() => {});
    });
}
