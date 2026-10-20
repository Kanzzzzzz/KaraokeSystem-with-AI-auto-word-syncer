/**
 * audioPlayer.js - controls the <audio> element and the transport bar.
 *
 * This module owns playback only. It announces what happens through
 * callbacks (onEnded, onLoaded, ...) rather than reaching into the lyrics or
 * scoring code itself, which keeps those pieces independent and testable.
 */

/** Format seconds as m:ss for the transport display. */
export function formatTime(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
    const minutes = Math.floor(seconds / 60);
    // padStart guarantees "3:05" rather than "3:5".
    return `${minutes}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
}

export class AudioPlayer {
    /**
     * @param {object} elements - { audio, fill, current, duration, scrub }
     */
    constructor(elements) {
        this.audio = elements.audio;
        this.fill = elements.fill;
        this.currentLabel = elements.current;
        this.durationLabel = elements.duration;
        this.scrub = elements.scrub;

        // Callbacks the host page fills in.
        this.onEnded = null;
        this.onLoaded = null;
        this.onPlay = null;
        this.onPause = null;
        this.onError = null;
        // Called instead of onError when the browser's autoplay policy
        // refused to start playback (no click or key press on the page yet).
        // The karaoke screen answers it with a big "Start" button.
        this.onBlocked = null;

        this.bindEvents();
    }

    bindEvents() {
        // timeupdate is coarse (~4/sec) but perfectly adequate for a progress
        // bar. The lyrics use their own finer loop - see lyricsRenderer.js.
        this.audio.addEventListener('timeupdate', () => this.updateProgress());

        this.audio.addEventListener('loadedmetadata', () => {
            this.durationLabel.textContent = formatTime(this.audio.duration);
            this.onLoaded?.(this.audio.duration);
        });

        this.audio.addEventListener('ended', () => this.onEnded?.());
        this.audio.addEventListener('play', () => this.onPlay?.());
        this.audio.addEventListener('pause', () => this.onPause?.());

        this.audio.addEventListener('error', () => {
            // audio.error is null if the failure was not a media error.
            const code = this.audio.error?.code;
            this.onError?.(
                code === 4
                    ? 'This audio file could not be played. The format may not be supported.'
                    : 'Could not load the audio file.'
            );
        });

        // Click anywhere on the bar to seek there.
        this.scrub?.addEventListener('click', (event) => {
            if (!Number.isFinite(this.audio.duration)) return;

            // getBoundingClientRect gives the bar's position on screen, so
            // subtracting it converts a page click into a 0-1 fraction.
            const bounds = this.scrub.getBoundingClientRect();
            const fraction = (event.clientX - bounds.left) / bounds.width;

            this.audio.currentTime = Math.max(0, Math.min(1, fraction)) * this.audio.duration;
        });
    }

    updateProgress() {
        const { currentTime, duration } = this.audio;
        this.currentLabel.textContent = formatTime(currentTime);

        if (Number.isFinite(duration) && duration > 0) {
            this.fill.style.width = `${(currentTime / duration) * 100}%`;
        }
    }

    /** Point the player at a new track. Does not start it playing. */
    load(audioUrl) {
        this.audio.src = audioUrl;
        this.audio.load();
        this.fill.style.width = '0%';
        this.currentLabel.textContent = '0:00';
    }

    /**
     * Start playback.
     *
     * play() returns a promise that rejects when the browser blocks autoplay,
     * which it does until the user has interacted with the page. Catching it
     * keeps that from surfacing as an unhandled rejection in the console.
     */
    async play() {
        try {
            await this.audio.play();
            return true;
        } catch (error) {
            // NotAllowedError is the autoplay block. Anything else - usually
            // an AbortError because a new track was loaded mid-start - is not
            // something the user needs to act on.
            if (error?.name === 'NotAllowedError') {
                if (this.onBlocked) this.onBlocked();
                else this.onError?.('Playback was blocked. Press play to start.');
            }
            return false;
        }
    }

    pause() {
        this.audio.pause();
    }

    togglePlay() {
        if (this.audio.paused) {
            this.play();
        } else {
            this.pause();
        }
    }

    stop() {
        this.audio.pause();
        this.audio.removeAttribute('src');
        // Without load() after clearing src, some browsers keep buffering the
        // old file in the background.
        this.audio.load();
        this.fill.style.width = '0%';
        this.currentLabel.textContent = '0:00';
        this.durationLabel.textContent = '0:00';
    }

    get isPlaying() {
        return !this.audio.paused && !this.audio.ended;
    }

    get currentTime() {
        return this.audio.currentTime;
    }
}
