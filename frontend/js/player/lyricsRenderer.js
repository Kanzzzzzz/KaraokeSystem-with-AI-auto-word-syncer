/**
 * lyricsRenderer.js - draws the lyrics and runs the word-by-word glow.
 *
 * ===========================================================================
 * HOW THE TIMING WORKS
 * ===========================================================================
 *
 * The obvious approach is to listen for the <audio> element's `timeupdate`
 * event. The problem is that browsers only fire it about four times a second,
 * which is far too coarse for an effect meant to feel rhythmic - the glow
 * would visibly step rather than flow.
 *
 * So this runs its own requestAnimationFrame loop (about 60 times a second,
 * synced to the display refresh) and reads `audio.currentTime` directly each
 * frame. `timeupdate` is still fine for coarse work like the progress bar,
 * but the glow needs this finer loop.
 *
 * ===========================================================================
 * THE WIPE: ONE LETTER AT A TIME
 * ===========================================================================
 *
 * Lyrics come from LRCLIB (or the sync editor) as line-timed LRC: each line
 * has the moment it starts, nothing finer. The active line is filled letter by
 * letter across its slot - from its own timestamp to the next line's - at a
 * steady pace, the way a videoke box wipes a line. buildWipeTimings() in
 * lrcParser.js works out when each letter fills.
 *
 * Every letter is its own inline-block <span>, grouped inside a <span> per
 * word. That structure is what makes a wrapped line wipe correctly. A single
 * gradient on the whole word or line is painted across the element's BOX, not
 * along the reading order - so when a long line wraps onto two rows, both
 * rows light up together. Filling letters one after another makes the wipe
 * follow the text: the first row finishes before the second begins. Keeping
 * each word inline-block means a wrap can only happen between words.
 *
 * ===========================================================================
 * HOW THE GLOW IS DRAWN
 * ===========================================================================
 *
 * JavaScript computes numbers and writes them as CSS custom properties; the
 * stylesheet does all the actual drawing:
 *
 *   --fill  0-100 on each LETTER, how much of it has been sung. The stylesheet
 *           uses this as a gradient stop, so colour sweeps across the letter.
 *   --glow  0-1 on each WORD, how strongly it is lit right now. Peaks as the
 *           word is being sung and settles afterwards.
 *
 * Splitting the work this way - JS computes, CSS paints - keeps the animation
 * on the GPU and the code simple.
 *
 * ===========================================================================
 * HOW THE LINES STAY CENTRED
 * ===========================================================================
 *
 * Every line is rendered into a track that is absolutely positioned inside
 * the lyrics box. Each time the active line changes, the track is moved with
 * a CSS transform so that the active line sits exactly in the middle of the
 * box, and a CSS transition makes that movement glide.
 *
 * The active line is enlarged with `transform: scale()` rather than a bigger
 * font-size, which matters: transforms do not affect layout, so the measured
 * line positions stay valid and the other lines never shift around.
 *
 * ===========================================================================
 * TWO LAYOUTS
 * ===========================================================================
 *
 *   'scroll' (default)  the active line centred, its neighbours visible above
 *                       and below. Used by the sync editor's preview.
 *
 *   'band'              the karaoke screen's lower-third band, videoke-TV
 *                       style: exactly two lines on show - the current line
 *                       on top (bright, full size) and the next line under it
 *                       (dim, smaller). When a line finishes, the track slides
 *                       up one line: the next line grows into the top slot and
 *                       the one after it fades in underneath. Nothing resets.
 *
 * Both use the same moving track, the same wipe and the same timing - 'band'
 * only changes WHERE the track is moved to (the current line and the next,
 * as a pair, centred in the band - instead of the current line alone centred
 * in the box) and marks which two lines are visible. The stylesheet does
 * the rest.
 */

import { buildWipeTimings, findActiveLine, parseLrc } from './lrcParser.js';

// How quickly the line-level flare fades after a new line starts, in seconds.
const BEAT_DECAY_SECONDS = 0.45;

// How lit a word stays once it has been sung. Not zero, so a finished line
// still reads as "done" rather than going dark.
const SUNG_GLOW = 0.18;

/**
 * The glow value as written to CSS, rounded to steps of 0.1.
 *
 * Every new value restyles the word and redraws it. Steps of 0.1 still rise
 * and fall smoothly to the eye but change about half as often - and since an
 * unchanged value is never written, frames between steps cost nothing.
 */
function glowLevel(glow) {
    return (Math.round(glow * 10) / 10).toFixed(1);
}

/**
 * Show how much of one letter has been sung (0-100).
 *
 * Painting a colour gradient THROUGH the letter shapes is one of the more
 * expensive ways to draw text, and it was being done for every letter of the
 * song. But at any moment only one letter is part-way filled: everything
 * before it is fully lit and everything after it fully dim - and at 0% or
 * 100% the gradient looks exactly like a plain colour anyway. So each letter
 * is in one of three states, and only the one being sung uses the gradient:
 *
 *   lyrics__char                  not sung yet  plain dim colour
 *   lyrics__char--filling         being sung    gradient at --fill
 *   lyrics__char--sung            sung          plain lit colour
 *
 * The picture is identical; measured, the wipe's drawing cost drops by
 * about 40%. The class only changes when a letter starts or finishes -
 * a dozen or so times a second - and unchanged values are never rewritten.
 */
function applyFill(letter, fill) {
    if (letter.lastFill === fill) return;
    letter.lastFill = fill;

    let className = 'lyrics__char';
    if (fill >= 100) className += ' lyrics__char--sung';
    else if (fill > 0) className += ' lyrics__char--filling';

    if (letter.el.className !== className) letter.el.className = className;
    if (fill > 0 && fill < 100) letter.el.style.setProperty('--fill', String(fill));
}

export class LyricsRenderer {
    /**
     * @param {HTMLElement} container - the element the lyrics are drawn into
     * @param {object} [options]
     * @param {'scroll'|'band'} [options.layout] - see TWO LAYOUTS above
     */
    constructor(container, options = {}) {
        this.container = container;
        this.layout = options.layout === 'band' ? 'band' : 'scroll';
        this.container.classList.toggle('lyrics--band', this.layout === 'band');

        this.track = null;          // the moving element holding every line
        this.lines = [];            // parsed LRC lines
        this.lineElements = [];     // one <div> per line
        this.lineWords = [];        // per line: [{ text, start, end, el, letters }]
        this.lineStates = [];       // per line: 'past' | 'active' | 'future'
        this.lineSlots = [];        // band layout: 'lead' | 'next' | '' per line
        this.lineCentres = [];      // measured vertical centre of each line
        this.lineTops = [];         // measured top edge of each line (band layout)
        this.lineHeights = [];      // measured height of each line (band layout)

        this.activeIndex = -1;
        this.hintIndex = 0;         // lets findActiveLine take its fast path
        this.containerHeight = 0;

        this.audio = null;
        this.frameId = null;

        // Called once per animation frame with the playback time, so other
        // time-synced effects (the 3-2-1 countdown) can share this loop
        // instead of running a second one.
        this.onFrame = null;

        // Re-measure when the window changes, or the centring drifts.
        this.onResize = () => this.measure();
        window.addEventListener('resize', this.onResize);

        // A window resize is not the only way the box changes size. The stage
        // is often laid out AFTER the lyrics are loaded - a panel that was
        // hidden, a font that arrived late, the sidebar reflowing - and a
        // height measured before that would be stale or zero. Watching the
        // element itself catches all of those.
        if (typeof ResizeObserver !== 'undefined') {
            this.resizeObserver = new ResizeObserver(() => this.measure());
            this.resizeObserver.observe(this.container);
        }
    }

    /* ------------------------------------------------------------------ */
    /* Loading                                                             */
    /* ------------------------------------------------------------------ */

    /**
     * Load lyrics from LRC text. Returns the parsed line count so the caller
     * can tell whether the file contained anything usable.
     */
    loadLrc(lrcText) {
        const { lines } = parseLrc(lrcText);

        this.lines = lines;
        this.activeIndex = -1;
        this.hintIndex = 0;

        this.container.classList.remove('lyrics--unsynced');
        this.render();

        return lines.length;
    }

    /**
     * Show untimed lyrics as a plain, scrollable sheet.
     *
     * Used for songs that have not been through the sync editor: the words
     * are still useful to sing from, they just cannot glow in time.
     */
    loadPlain(plainLines) {
        this.stop();
        this.lines = [];
        this.lineElements = [];
        this.lineWords = [];
        this.lineSlots = [];
        this.lineCentres = [];
        this.lineTops = [];
        this.lineHeights = [];

        this.container.classList.add('lyrics--unsynced');
        this.container.innerHTML = '';

        const track = document.createElement('div');
        track.className = 'lyrics__track';

        for (const line of plainLines) {
            const element = document.createElement('div');
            element.className = 'lyrics__line';
            // Section markers like [Chorus] are labels, not words to sing.
            element.textContent =
                line.kind === 'section' ? `— ${line.text} —` : line.text;
            track.appendChild(element);
        }

        this.container.appendChild(track);
        this.track = track;
    }

    clear() {
        this.stop();
        this.lines = [];
        this.lineElements = [];
        this.lineWords = [];
        this.lineSlots = [];
        this.lineCentres = [];
        this.lineTops = [];
        this.lineHeights = [];
        this.activeIndex = -1;
        this.track = null;
        this.container.innerHTML = '';
        this.container.classList.remove('lyrics--unsynced');
    }

    showMessage(message) {
        this.clear();
        const element = document.createElement('div');
        element.className = 'empty-note';
        element.textContent = message;
        this.container.appendChild(element);
    }

    /* ------------------------------------------------------------------ */
    /* Rendering                                                           */
    /* ------------------------------------------------------------------ */

    /**
     * Build the DOM once: every line, split into word spans.
     *
     * All lines are rendered up front rather than a moving window, because
     * the track is scrolled with a single transform - which is both smoother
     * and simpler than rebuilding the DOM whenever the line changes.
     */
    render() {
        this.container.innerHTML = '';
        this.lineElements = [];
        this.lineWords = [];
        this.lineStates = [];
        this.lineSlots = [];

        if (this.lines.length === 0) return;

        const track = document.createElement('div');
        track.className = 'lyrics__track';

        this.lines.forEach((line, index) => {
            const lineElement = document.createElement('div');
            lineElement.className = 'lyrics__line lyrics__line--future';
            lineElement.dataset.index = String(index);

            const words = buildWipeTimings(line);

            if (words.length === 0) {
                // A blank line in the file - an instrumental break or the
                // end of the song. Render a music note so the layout still
                // has something.
                lineElement.textContent = '♪';
            } else {
                words.forEach((word, wordIndex) => {
                    const wordElement = document.createElement('span');
                    wordElement.className = 'lyrics__word';
                    word.el = wordElement;

                    for (const letter of word.letters) {
                        const letterElement = document.createElement('span');
                        letterElement.className = 'lyrics__char';
                        letterElement.textContent = letter.char;
                        letter.el = letterElement;
                        wordElement.appendChild(letterElement);
                    }

                    lineElement.appendChild(wordElement);

                    // A real space between words - the only place a long
                    // line is allowed to wrap.
                    if (wordIndex < words.length - 1) {
                        lineElement.appendChild(document.createTextNode(' '));
                    }
                });
            }

            this.lineWords.push(words);
            this.lineStates.push('future');
            this.lineSlots.push('');
            this.lineElements.push(lineElement);
            track.appendChild(lineElement);
        });

        this.container.appendChild(track);
        this.track = track;

        this.applySlots();
        this.measure();
    }

    /**
     * Band layout: mark the two lines that are on show.
     *
     *   lead  the current line - or, before the first line starts, the first
     *         line, so the singer can read it during the intro
     *   next  the line after it
     *
     * Every other line is hidden by the stylesheet. Like setActiveLine(),
     * only lines whose slot actually changes are touched.
     */
    applySlots() {
        if (this.layout !== 'band') return;

        const lead = Math.max(0, this.activeIndex);

        this.lineElements.forEach((element, i) => {
            const wanted = i === lead ? 'lead' : i === lead + 1 ? 'next' : '';
            if (this.lineSlots[i] === wanted) return;

            element.classList.remove('lyrics__line--lead', 'lyrics__line--next');
            if (wanted) element.classList.add(`lyrics__line--${wanted}`);
            this.lineSlots[i] = wanted;
        });
    }

    /**
     * Record where each line sits, so scrolling does not have to ask the
     * browser for layout on every frame.
     *
     * This stays valid as lines highlight because the active line grows via
     * `transform: scale()`, which does not affect layout.
     */
    measure() {
        if (!this.track || this.lineElements.length === 0) return;

        this.containerHeight = this.container.clientHeight;
        this.lineCentres = this.lineElements.map(
            (el) => el.offsetTop + el.offsetHeight / 2
        );
        this.lineTops = this.lineElements.map((el) => el.offsetTop);
        this.lineHeights = this.lineElements.map((el) => el.offsetHeight);

        this.applyScroll(false);
    }

    /**
     * Move the track so the active line sits in the middle of the box.
     *
     * @param {boolean} animated - false snaps instantly (used on load, so the
     *        first line does not visibly slide in from nowhere)
     */
    applyScroll(animated = true) {
        if (!this.track || this.lineCentres.length === 0) return;

        // Read the height fresh rather than trusting the cached one. If the
        // box had no height when the lyrics loaded (still hidden, or the page
        // had not finished laying out), a cached zero would centre every line
        // on the top edge of the box for the rest of the song.
        const height = this.container.clientHeight;
        if (height > 0) this.containerHeight = height;

        // Nothing sensible to compute yet - wait for the ResizeObserver to
        // fire once the box actually has a size.
        if (this.containerHeight <= 0) return;

        // Before the first line starts, show line 0 rather than jumping.
        const index = Math.max(0, this.activeIndex);
        const centre = this.lineCentres[index];
        if (centre === undefined) return;

        // Band: the current line and the next one, as a pair, are centred
        // in the band - so a line that wraps onto two rows nudges the pair
        // up rather than pushing the next line out of view. Scroll: the
        // current line's centre goes to the middle of the box.
        let offset;
        if (this.layout === 'band') {
            const next = Math.min(index + 1, this.lineTops.length - 1);
            const pairHeight = this.lineTops[next] + this.lineHeights[next] - this.lineTops[index];
            offset = Math.max(0, (this.containerHeight - pairHeight) / 2) - this.lineTops[index];
        } else {
            offset = this.containerHeight / 2 - centre;
        }

        if (!animated) {
            // Temporarily drop the transition so the jump is not animated.
            this.track.style.transition = 'none';
            this.track.style.transform = `translateY(${offset}px)`;
            // Reading offsetHeight forces the browser to apply that now,
            // before the transition is put back - otherwise both changes
            // collapse into one frame and the snap animates anyway.
            void this.track.offsetHeight;
            this.track.style.transition = '';
            return;
        }

        this.track.style.transform = `translateY(${offset}px)`;
    }

    /* ------------------------------------------------------------------ */
    /* The animation loop                                                  */
    /* ------------------------------------------------------------------ */

    /** Start following an <audio> element. */
    attach(audioElement) {
        this.audio = audioElement;
        this.start();
    }

    start() {
        if (this.frameId !== null) return;   // already running

        const tick = () => {
            this.update();
            if (this.onFrame && this.audio) this.onFrame(this.audio.currentTime);
            this.frameId = requestAnimationFrame(tick);
        };
        this.frameId = requestAnimationFrame(tick);
    }

    stop() {
        if (this.frameId !== null) {
            cancelAnimationFrame(this.frameId);
            this.frameId = null;
        }
    }

    /** Release the observers - call when tearing the page down. */
    destroy() {
        this.stop();
        window.removeEventListener('resize', this.onResize);
        this.resizeObserver?.disconnect();
    }

    /**
     * One animation frame: find the current line, scroll if it changed, and
     * update the word-level glow.
     */
    update() {
        if (!this.audio || this.lines.length === 0) return;

        const currentTime = this.audio.currentTime;

        // Nothing is active until playback has actually moved. Without this,
        // a first line stamped at 00:00.00 would already count as reached
        // the moment the song loads, and sit highlighted before Play.
        const index = currentTime > 0
            ? findActiveLine(this.lines, currentTime, this.hintIndex)
            : -1;

        if (index !== this.activeIndex) {
            this.setActiveLine(index);
        }

        this.updateWords(currentTime);
        this.updateLineBeat(currentTime);
    }

    /**
     * Switch which line is highlighted, and slide the track to centre it.
     *
     * Every line is brought up to date, not just the neighbours: a seek can
     * jump any distance, and lines skipped over must read as sung (forwards)
     * or unsung (backwards). Only lines whose state actually changes are
     * touched, so the normal one-line step costs almost nothing.
     */
    setActiveLine(index) {
        this.activeIndex = index;
        this.hintIndex = Math.max(0, index);

        this.lineElements.forEach((element, i) => {
            const wanted = i < index ? 'past' : i === index ? 'active' : 'future';
            if (this.lineStates[i] === wanted) return;

            element.classList.remove(
                'lyrics__line--past', 'lyrics__line--active', 'lyrics__line--future'
            );
            element.classList.add(`lyrics__line--${wanted}`);
            this.lineStates[i] = wanted;

            if (wanted !== 'active') {
                element.style.removeProperty('--beat');
                this.fillLine(i, wanted === 'past');
            }
        });

        this.applySlots();
        this.applyScroll(true);
    }

    /**
     * Set every letter in a line to fully sung or fully unsung at once.
     * Used when a line stops being the active one.
     */
    fillLine(lineIndex, sung) {
        const words = this.lineWords[lineIndex];
        if (!words) return;

        const fill = sung ? 100 : 0;
        const glow = glowLevel(sung ? SUNG_GLOW : 0);

        for (const word of words) {
            if (!word.el) continue;

            for (const letter of word.letters) {
                applyFill(letter, fill);
            }

            if (word.lastGlow !== glow) {
                word.el.style.setProperty('--glow', glow);
                word.lastGlow = glow;
            }
        }
    }

    /**
     * Update the wipe for each letter, and the glow for each word, of the
     * active line.
     *
     * Values are rounded before being written: the eye cannot see finer, and
     * skipping unchanged values avoids a style recalculation per letter per
     * frame. In practice only the one letter being sung changes each frame,
     * which is what keeps this smooth with a long line on screen.
     */
    updateWords(currentTime) {
        const words = this.lineWords[this.activeIndex];
        if (!words) return;

        for (const word of words) {
            if (!word.el) continue;

            for (const letter of word.letters) {
                applyFill(letter, Math.round(progressThrough(letter, currentTime) * 100));
            }

            const duration = word.end - word.start;
            const progress =
                duration > 0 ? (currentTime - word.start) / duration : 1;
            const clamped = Math.max(0, Math.min(1, progress));

            // Glow rises and falls smoothly across the word: sin() peaks in
            // the middle and returns to zero at both edges, which reads as
            // the word lighting up and settling rather than snapping on.
            let glow;
            if (progress <= 0) {
                glow = 0;
            } else if (progress >= 1) {
                glow = SUNG_GLOW;
            } else {
                glow = SUNG_GLOW + (1 - SUNG_GLOW) * Math.sin(Math.PI * clamped);
            }

            const glowText = glowLevel(glow);

            // Only touch the DOM when the value actually changed.
            if (word.lastGlow !== glowText) {
                word.el.style.setProperty('--glow', glowText);
                word.lastGlow = glowText;
            }
        }
    }

    /**
     * The line-level flare: a brighter pulse right as a new line begins,
     * decaying over BEAT_DECAY_SECONDS so each line arrives with emphasis.
     */
    updateLineBeat(currentTime) {
        const element = this.lineElements[this.activeIndex];
        const line = this.lines[this.activeIndex];
        if (!element || !line) return;

        const elapsed = currentTime - line.time;

        // 1 at the line start, falling to 0 once the decay has finished.
        const beat = Math.max(0, 1 - elapsed / BEAT_DECAY_SECONDS);

        element.style.setProperty('--beat', beat.toFixed(2));
    }
}

/** How far through its own time span a letter is, from 0 to 1. */
function progressThrough(span, currentTime) {
    const duration = span.end - span.start;
    if (duration <= 0) return currentTime >= span.start ? 1 : 0;
    return Math.max(0, Math.min(1, (currentTime - span.start) / duration));
}
