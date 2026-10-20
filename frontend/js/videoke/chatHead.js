/**
 * chatHead.js - the floating mic bubble and the drawer it opens.
 *
 * Before this, the song search, keypad and queue sat in a panel that was
 * always on screen. Using it meant working right next to the lyrics while
 * someone was singing. Now guests queue from their own phones, and this is
 * the backup for whoever is at the host computer: a small round button
 * ("chat head", like Messenger's) that stays out of the way until needed.
 *
 *   - Tap it: a drawer slides out from the edge the bubble sits on, holding
 *     the keypad, search and queue exactly as before.
 *   - Drag it: it follows the pointer and, on release, snaps to the nearest
 *     edge - left, right, top, or the bottom of the overlay area (just above
 *     the lyrics band, never on it). The spot is remembered in this browser.
 *   - Leave it: the drawer closes by itself after IDLE_MS without a touch or
 *     key press, or as soon as you click anywhere outside it.
 *
 * Everything lives inside #overlay-layer, so neither the bubble nor the
 * drawer can reach the lyrics band - see performanceZone.js.
 *
 * MOVEMENT
 * The bubble is positioned with transform: translate(), and the drawer slides
 * with transform too. Transforms are handled by the graphics chip without
 * re-laying-out or repainting the page, which is what keeps dragging smooth.
 */

const IDLE_MS = 9000;              // auto-close after this long untouched
const DRAG_THRESHOLD = 6;          // px of movement before a press is a drag
const MARGIN = 12;                 // gap between the bubble and the edge
const STORAGE_KEY = 'karaoke-bubble-position';

// Default: top-right corner. `at` is how far along the edge, 0 to 1.
const DEFAULT_POSITION = { edge: 'right', at: 0 };
const EDGES = ['left', 'right', 'top', 'bottom'];

function loadPosition() {
    // localStorage can be missing or throw (private mode, blocked storage);
    // the bubble then just starts in its default corner.
    try {
        const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
        if (EDGES.includes(saved?.edge) && Number.isFinite(saved?.at)) {
            return { edge: saved.edge, at: Math.min(1, Math.max(0, saved.at)) };
        }
    } catch { /* fall through to the default */ }
    return { ...DEFAULT_POSITION };
}

function savePosition(position) {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(position));
    } catch { /* not important enough to report */ }
}

export class ChatHead {
    /**
     * @param {object} elements - { layer, bubble, badge, drawer }
     * @param {object} [options]
     * @param {import('./performanceZone.js').PerformanceZone} [options.zone]
     */
    constructor({ layer, bubble, badge, drawer }, { zone } = {}) {
        this.layer = layer;
        this.bubble = bubble;
        this.badge = badge;
        this.drawer = drawer;
        this.zone = zone;

        this.position = loadPosition();
        this.isOpen = false;
        this.idleTimer = null;
        this.drag = null;
        this.justDragged = false;

        // Called with the tab name whenever a tab is shown.
        this.onTabChange = null;

        this.tabs = [...drawer.querySelectorAll('[role="tab"]')];
        this.panels = [...drawer.querySelectorAll('[role="tabpanel"]')];

        this.bindBubble();
        this.bindDrawer();
        this.close();
        this.place(false);

        // The layer changes size with the window; keep the bubble on its edge.
        new ResizeObserver(() => {
            if (this.isOpen) this.parkOnDrawer();
            else this.place(false);
        }).observe(layer);
    }

    /* ------------------------------------------------------------------ */
    /* Opening and closing                                                 */
    /* ------------------------------------------------------------------ */

    /** Open the drawer, optionally on a given tab ('songs', 'queue', ...). */
    open(tab) {
        if (tab) this.showTab(tab);

        this.drawer.className = `drawer drawer--from-${this.position.edge} drawer--open`;
        this.drawer.inert = false;
        this.isOpen = true;
        this.bubble.setAttribute('aria-expanded', 'true');
        this.bubble.classList.add('bubble--open');

        this.parkOnDrawer();
        this.resetIdle();
        this.zone?.guard();
    }

    close() {
        // `inert` makes the closed drawer unreachable by Tab and invisible
        // to screen readers, while it is still in the page to slide back.
        this.drawer.className = `drawer drawer--from-${this.position.edge}`;
        this.drawer.inert = true;
        this.isOpen = false;
        this.bubble.setAttribute('aria-expanded', 'false');
        this.bubble.classList.remove('bubble--open');

        clearTimeout(this.idleTimer);
        this.place(true);

        // Do not leave keyboard focus stranded inside a hidden drawer.
        if (this.drawer.contains(document.activeElement)) this.bubble.focus();
    }

    toggle() {
        if (this.isOpen) this.close();
        else this.open();
    }

    /** Restart the inactivity countdown. Any touch or key press calls this. */
    resetIdle() {
        clearTimeout(this.idleTimer);
        if (this.isOpen) this.idleTimer = setTimeout(() => this.close(), IDLE_MS);
    }

    showTab(name) {
        for (const tab of this.tabs) {
            const selected = tab.dataset.tab === name;
            tab.setAttribute('aria-selected', String(selected));
            tab.tabIndex = selected ? 0 : -1;
        }
        for (const panel of this.panels) {
            panel.hidden = panel.dataset.tab !== name;
        }
        this.onTabChange?.(name);
    }

    /** The small number on the bubble: songs waiting in the queue. */
    setBadge(count) {
        this.badge.hidden = count <= 0;
        this.badge.textContent = count > 99 ? '99+' : String(count);
    }

    /* ------------------------------------------------------------------ */
    /* Positioning                                                         */
    /* ------------------------------------------------------------------ */

    /** The space the bubble can move in, and its own size. */
    bounds() {
        return {
            width: this.layer.clientWidth,
            height: this.layer.clientHeight,
            size: this.bubble.offsetWidth,
        };
    }

    moveBubble(x, y, animated) {
        this.bubble.classList.toggle('bubble--gliding', animated);
        this.bubble.style.transform = `translate3d(${Math.round(x)}px, ${Math.round(y)}px, 0)`;
    }

    /** Put the bubble at its docked spot on the chosen edge. */
    place(animated) {
        const { width, height, size } = this.bounds();
        const { edge, at } = this.position;

        // How far the bubble can travel along each direction.
        const spanX = Math.max(0, width - size - MARGIN * 2);
        const spanY = Math.max(0, height - size - MARGIN * 2);

        let x = MARGIN + at * spanX;
        let y = MARGIN + at * spanY;
        if (edge === 'left') x = MARGIN;
        if (edge === 'right') x = width - size - MARGIN;
        if (edge === 'top') y = MARGIN;
        if (edge === 'bottom') y = height - size - MARGIN;

        this.moveBubble(x, y, animated);
    }

    /**
     * While the drawer is open, the bubble sits in the drawer's top corner
     * (the header leaves room for it) and becomes its close button - so it
     * never covers the drawer's contents, whichever edge it opened from.
     */
    parkOnDrawer() {
        const { size } = this.bounds();
        const right = this.drawer.offsetLeft + this.drawer.offsetWidth;
        this.moveBubble(right - size - MARGIN / 2, this.drawer.offsetTop + MARGIN / 2, true);
    }

    /** After a drag, pick the nearest edge and how far along it to sit. */
    snap(x, y) {
        const { width, height, size } = this.bounds();
        const distances = {
            left: x,
            right: width - size - x,
            top: y,
            bottom: height - size - y,
        };
        const edge = EDGES.reduce((best, e) => (distances[e] < distances[best] ? e : best));

        const spanX = Math.max(1, width - size - MARGIN * 2);
        const spanY = Math.max(1, height - size - MARGIN * 2);
        const along = edge === 'left' || edge === 'right'
            ? (y - MARGIN) / spanY
            : (x - MARGIN) / spanX;

        this.position = { edge, at: Math.min(1, Math.max(0, along)) };
        savePosition(this.position);
        this.drawer.className = `drawer drawer--from-${edge}`;
        this.place(true);
    }

    /* ------------------------------------------------------------------ */
    /* Events                                                              */
    /* ------------------------------------------------------------------ */

    bindBubble() {
        const bubble = this.bubble;

        bubble.addEventListener('pointerdown', (event) => {
            if (event.button !== 0) return;
            const box = bubble.getBoundingClientRect();
            const layer = this.layer.getBoundingClientRect();
            this.drag = {
                id: event.pointerId,
                startX: event.clientX,
                startY: event.clientY,
                // Where inside the bubble it was grabbed, so it does not jump
                // to centre itself under the pointer.
                grabX: event.clientX - box.left,
                grabY: event.clientY - box.top,
                layerLeft: layer.left,
                layerTop: layer.top,
                moving: false,
            };
            bubble.setPointerCapture(event.pointerId);
        });

        bubble.addEventListener('pointermove', (event) => {
            const drag = this.drag;
            if (!drag || drag.id !== event.pointerId) return;

            if (!drag.moving) {
                const moved = Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY);
                if (moved < DRAG_THRESHOLD) return;   // still just a tap
                drag.moving = true;
                if (this.isOpen) this.close();
                bubble.classList.add('bubble--dragging');
            }

            const { width, height, size } = this.bounds();
            const x = Math.min(width - size, Math.max(0, event.clientX - drag.layerLeft - drag.grabX));
            const y = Math.min(height - size, Math.max(0, event.clientY - drag.layerTop - drag.grabY));
            drag.x = x;
            drag.y = y;
            this.moveBubble(x, y, false);
        });

        const endDrag = (event) => {
            const drag = this.drag;
            if (!drag || drag.id !== event.pointerId) return;
            this.drag = null;
            bubble.classList.remove('bubble--dragging');

            if (drag.moving) {
                // The browser still fires a click after a drag; swallow it.
                this.justDragged = true;
                setTimeout(() => { this.justDragged = false; }, 0);
                this.snap(drag.x ?? 0, drag.y ?? 0);
            }
        };
        bubble.addEventListener('pointerup', endDrag);
        bubble.addEventListener('pointercancel', endDrag);

        // Opening is on click, not pointerup, so the keyboard (Enter/Space
        // on the focused button) opens it too.
        bubble.addEventListener('click', () => {
            if (this.justDragged) return;
            this.toggle();
        });
    }

    bindDrawer() {
        for (const tab of this.tabs) {
            tab.addEventListener('click', () => this.showTab(tab.dataset.tab));
        }

        // Anything done inside the drawer counts as activity.
        for (const type of ['pointerdown', 'keydown', 'input', 'wheel', 'focusin']) {
            this.drawer.addEventListener(type, () => this.resetIdle(), { passive: true });
        }

        // A press anywhere outside the drawer and the bubble closes it.
        // Capture phase, so it is seen even if the target stops the event.
        document.addEventListener('pointerdown', (event) => {
            if (!this.isOpen) return;
            if (this.drawer.contains(event.target) || this.bubble.contains(event.target)) return;
            this.close();
        }, true);
    }
}
