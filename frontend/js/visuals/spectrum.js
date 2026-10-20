/**
 * spectrum.js - the animated frequency bars behind the lyrics.
 *
 * Draws the microphone's frequency spectrum onto a <canvas> that sits behind
 * the lyric text. It reuses the AnalyserNode that scoring already created,
 * so the audio is analysed once and used for both.
 *
 * When the microphone is off but a song is playing, it shows a gentle idle
 * wave so the stage never looks dead. When nothing is happening at all it
 * stops animating and holds a still frame - see setActive().
 */

// Only the lower part of the spectrum is drawn. An FFT covers up to half the
// sample rate (~24kHz), but singing lives well below 5kHz - the upper bins
// are nearly always empty and would leave a flat gap across the screen.
const USED_FRACTION = 0.42;

const BAR_COUNT = 64;

/**
 * A bar's height when nothing is playing: two offset sine waves, so the
 * pattern does not look obviously repetitive.
 */
function idleHeight(phase, i) {
    return 0.08 + 0.05 * Math.sin(phase + i * 0.25) + 0.03 * Math.sin(phase * 1.7 + i * 0.11);
}

const SMOOTHING = 0.75;   // how much of the previous frame each bar keeps

export class SpectrumVisualizer {
    constructor(canvas) {
        this.canvas = canvas;
        this.context = canvas.getContext('2d');

        this.micCapture = null;
        this.frameId = null;
        this.phase = 0;

        // Each bar eases toward its target height instead of snapping, which
        // is what makes the animation look fluid rather than twitchy.
        this.heights = new Float32Array(BAR_COUNT);

        this.resize();

        // Keep the canvas matched to its on-screen size when the window
        // changes, otherwise the drawing stretches. Resizing a canvas also
        // erases it, so when the animation is stopped the still frame has to
        // be drawn again - or the stage would be left blank.
        this.onResize = () => {
            this.resize();
            if (this.frameId === null) this.drawStill();
        };
        window.addEventListener('resize', this.onResize);
    }

    /**
     * Match the canvas's internal pixel grid to its CSS size.
     *
     * A canvas has two sizes: how big it looks (CSS) and how many pixels it
     * contains (width/height). Multiplying by devicePixelRatio makes it sharp
     * on high-DPI screens instead of blurry.
     */
    resize() {
        const ratio = window.devicePixelRatio || 1;
        const bounds = this.canvas.getBoundingClientRect();

        // A hidden canvas reports zero size; drawing into that throws.
        if (bounds.width === 0 || bounds.height === 0) return;

        this.canvas.width = Math.floor(bounds.width * ratio);
        this.canvas.height = Math.floor(bounds.height * ratio);

        // Scale the drawing context so the code below can work in CSS pixels.
        this.context.setTransform(ratio, 0, 0, ratio, 0, 0);

        this.width = bounds.width;
        this.height = bounds.height;
    }

    /** Use this microphone's spectrum data. Pass null to return to idle. */
    setSource(micCapture) {
        this.micCapture = micCapture;
    }

    start() {
        if (this.frameId !== null) return;
        const tick = () => {
            this.draw();
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

    /** Remove the resize listener - call when tearing the page down. */
    destroy() {
        this.stop();
        window.removeEventListener('resize', this.onResize);
    }

    /**
     * Animate only while there is something to show - the song playing, or
     * the microphone listening. Otherwise draw one still frame and stop.
     *
     * A running animation is never free, even when it looks calm: every frame
     * the canvas is redrawn, and everything layered over it has to be
     * composited again. On an idle karaoke screen this loop was most of the
     * page's drawing work, competing with typing and scrolling while showing
     * nothing new.
     */
    setActive(active) {
        if (active) {
            this.start();
        } else {
            this.stop();
            this.drawStill();
        }
    }

    /** The idle pattern, frozen at its current phase and drawn once. */
    drawStill() {
        if (!this.width || !this.height) this.resize();
        if (!this.width || !this.height) return;

        for (let i = 0; i < BAR_COUNT; i++) {
            this.heights[i] = idleHeight(this.phase, i);
        }
        this.paint();
    }

    draw() {
        if (!this.width || !this.height) {
            this.resize();
            return;
        }

        const spectrum = this.micCapture?.isActive ? this.micCapture.readSpectrum() : null;
        this.phase += 0.02;

        for (let i = 0; i < BAR_COUNT; i++) {
            let target;

            if (spectrum) {
                // Map this bar to a slice of the used part of the spectrum
                // and take the loudest bin in it, so narrow peaks are not
                // averaged away into nothing.
                const usable = Math.floor(spectrum.length * USED_FRACTION);
                const from = Math.floor((i / BAR_COUNT) * usable);
                const to = Math.floor(((i + 1) / BAR_COUNT) * usable);

                let peak = 0;
                for (let bin = from; bin < to; bin++) {
                    if (spectrum[bin] > peak) peak = spectrum[bin];
                }

                target = peak / 255;   // getByteFrequencyData returns 0-255
            } else {
                target = idleHeight(this.phase, i);
            }

            this.heights[i] = this.heights[i] * SMOOTHING + target * (1 - SMOOTHING);
        }

        this.paint();
    }

    /** Draw the bars at their current heights. */
    paint() {
        const ctx = this.context;
        ctx.clearRect(0, 0, this.width, this.height);

        const barWidth = this.width / BAR_COUNT;

        for (let i = 0; i < BAR_COUNT; i++) {
            const barHeight = this.heights[i] * this.height * 0.85;
            const x = i * barWidth;
            const y = this.height - barHeight;

            // Colour shifts across the screen from cyan to magenta, and
            // brightens with height, so loud bars stand out.
            const hue = 185 + (i / BAR_COUNT) * 135;
            const lightness = 45 + this.heights[i] * 25;
            ctx.fillStyle = `hsl(${hue}, 95%, ${lightness}%)`;

            // The small gap (barWidth * 0.15) is what makes these read as
            // separate bars rather than one solid shape.
            ctx.fillRect(x, y, barWidth * 0.85, barHeight);
        }
    }
}
