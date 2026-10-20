/**
 * toasts.js - small notices that appear, then fade away on their own.
 *
 * Used for "Ana added Sunflower to the queue" and friends. A toast never
 * takes focus and never needs to be dismissed, so it can report what is
 * happening without interrupting whoever is singing.
 *
 * They stack in a single container near the top of the screen (inside the
 * overlay layer, so they can never reach the lyrics band), newest at the
 * bottom, at most MAX_VISIBLE at once. A toast that arrives during the 3-2-1
 * countdown waits for it to finish - see PerformanceZone.whenClear().
 *
 * The fade is a CSS transition on opacity and transform only, which the
 * browser can animate without repainting anything.
 */

const SHOW_MS = 4200;       // how long a toast stays before fading
const FADE_MS = 300;        // must match the transition in videoke.css
const MAX_VISIBLE = 3;

export class Toasts {
    /**
     * @param {HTMLElement} container - the toast stack (#toasts)
     * @param {import('./performanceZone.js').PerformanceZone} zone
     */
    constructor(container, zone) {
        this.container = container;
        this.zone = zone;
    }

    /**
     * @param {string} message
     * @param {object} [options]
     * @param {boolean} [options.error] - red styling, for things that failed
     */
    show(message, { error = false } = {}) {
        this.zone.whenClear(() => this.add(message, error));
    }

    add(message, error) {
        const toast = document.createElement('div');
        toast.className = 'toast-item' + (error ? ' toast-item--error' : '');
        // Errors are announced immediately; everything else politely.
        toast.setAttribute('role', error ? 'alert' : 'status');
        toast.textContent = message;
        this.container.appendChild(toast);

        // Drop the oldest if the stack is full, so a burst of songs being
        // queued cannot grow a tower of toasts down the screen.
        while (this.container.children.length > MAX_VISIBLE) {
            this.container.firstElementChild.remove();
        }

        // Next frame, so the browser sees the starting state first and
        // actually animates to the shown state.
        requestAnimationFrame(() => toast.classList.add('toast-item--in'));
        this.zone.guard();

        setTimeout(() => {
            toast.classList.remove('toast-item--in');
            setTimeout(() => toast.remove(), FADE_MS);
        }, SHOW_MS);
    }
}
