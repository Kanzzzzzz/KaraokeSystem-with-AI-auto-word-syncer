/**
 * performanceZone.js - keeps every popup off the singer's part of the screen.
 *
 * THE RULE
 * The "performance zone" is what the singer is reading:
 *
 *   1. the lyrics band along the bottom of the screen
 *   2. the 3-2-1 countdown in the middle, while it is showing
 *
 * No drawer, toast, prompt or dialog may ever cover either of them, at any
 * screen size. Two layers of protection enforce this:
 *
 * LAYER 1 - STRUCTURAL (the band). Written in CSS, see videoke.css.
 *   Every overlay on the karaoke screen lives inside ONE element,
 *   #overlay-layer, which is the third row of the page grid:
 *
 *       row 1   top strip (song title, progress bar)
 *       row 2   up next (the waiting queue)
 *       row 3   #overlay-layer   <- drawers, toasts, dialogs live here
 *       row 4   the lyrics band
 *
 *   The layer clips its contents (overflow: hidden), so nothing inside it
 *   can be drawn past its bottom edge - which is exactly the top edge of the
 *   band. A drawer sliding up from the bottom is cut off at the band's edge,
 *   and a dialog's dark backdrop stops there too, leaving the lyrics clear.
 *   No sizes need to agree for this to hold: the grid places the band, and
 *   the layer can only ever fill the space above it.
 *
 * LAYER 2 - AT RUNTIME (the countdown). This module.
 *   The countdown sits in the middle of that same area, so structure alone
 *   cannot keep things off it. Instead, while the countdown is showing, this
 *   module measures every overlay (anything marked data-overlay) and fades
 *   out any whose box intersects the countdown's box. They come back the
 *   moment the countdown ends - about three seconds. Toasts that arrive
 *   during the countdown wait (whenClear) instead of appearing.
 *
 *   As a safety net it also checks every overlay against the band, which
 *   layer 1 should make impossible; if a future change ever breaks that, the
 *   overlay is hidden and a warning is logged rather than covering lyrics.
 */

/** Do two rectangles overlap by more than a pixel? */
export function rectsOverlap(a, b) {
    return a.left < b.right - 1 && b.left < a.right - 1
        && a.top < b.bottom - 1 && b.top < a.bottom - 1;
}

/** The part of rectangle `a` inside rectangle `b`, or null if none. */
function clip(a, b) {
    const left = Math.max(a.left, b.left);
    const top = Math.max(a.top, b.top);
    const right = Math.min(a.right, b.right);
    const bottom = Math.min(a.bottom, b.bottom);
    return right > left && bottom > top ? { left, top, right, bottom } : null;
}

/**
 * Where an overlay will sit once it has finished moving.
 *
 * getBoundingClientRect() reports where an element is drawn RIGHT NOW,
 * including any slide animation in progress - a drawer caught mid-slide
 * would be judged at the wrong place. The drawer (data-overlay="slide") is
 * placed with top/left/right/bottom and only slides in with a transform, so
 * its offsets - which ignore transforms - give its resting place directly.
 * Everything else is positioned where it is drawn.
 */
function restingRect(element, layerRect) {
    if (element.dataset.overlay !== 'slide') return element.getBoundingClientRect();

    const left = layerRect.left + element.offsetLeft;
    const top = layerRect.top + element.offsetTop;
    return {
        left, top,
        right: left + element.offsetWidth,
        bottom: top + element.offsetHeight,
        width: element.offsetWidth,
        height: element.offsetHeight,
    };
}

export class PerformanceZone {
    /**
     * @param {object} elements
     * @param {HTMLElement} elements.band      - the lyrics band
     * @param {HTMLElement} elements.countdown - the countdown's box
     * @param {HTMLElement} elements.layer     - #overlay-layer
     */
    constructor({ band, countdown, layer }) {
        this.band = band;
        this.countdown = countdown;
        this.layer = layer;

        this.counting = false;
        this.waiting = [];          // callbacks held until the countdown ends
        this.warned = false;

        // Screen size changes can move overlays into (or out of) the way.
        window.addEventListener('resize', () => this.guard());
    }

    /** Is the countdown showing right now? */
    get isCounting() {
        return this.counting;
    }

    /** Called by the countdown as it appears and disappears. */
    setCounting(active) {
        if (this.counting === active) return;
        this.counting = active;
        this.guard();

        if (!active) {
            const waiting = this.waiting;
            this.waiting = [];
            waiting.forEach((callback) => callback());
        }
    }

    /**
     * Run `callback` now, or as soon as the countdown has finished.
     * Used for anything that would pop up - a toast, a prompt.
     */
    whenClear(callback) {
        if (this.counting) this.waiting.push(callback);
        else callback();
    }

    /**
     * Check every overlay against the zone, and hide the ones in the way.
     * Call after anything opens or moves: the drawer opening, a toast
     * appearing, the countdown starting.
     */
    guard() {
        const layer = this.layer.getBoundingClientRect();
        const band = this.band.getBoundingClientRect();
        const countdown = this.counting ? this.countdown.getBoundingClientRect() : null;

        for (const overlay of this.layer.querySelectorAll('[data-overlay]')) {
            // Only the part inside the layer can be seen - the layer clips
            // the rest - so that is the part that has to stay clear.
            const present = !overlay.hidden && !overlay.inert;
            const box = present ? clip(restingRect(overlay, layer), layer) : null;

            const onBand = box !== null && rectsOverlap(box, band);
            const onCountdown = box !== null && countdown !== null && rectsOverlap(box, countdown);

            if (onBand && !this.warned) {
                this.warned = true;
                console.warn('An overlay reached the lyrics band and was hidden:', overlay);
            }

            overlay.classList.toggle('overlay--yield', onBand || onCountdown);
        }
    }
}
