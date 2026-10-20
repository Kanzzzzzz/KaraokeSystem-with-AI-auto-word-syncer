/**
 * micCapture.js - reads the microphone with the Web Audio API.
 *
 * THE AUDIO GRAPH
 *
 *     getUserMedia()  ->  MediaStreamSource  ->  AnalyserNode
 *
 * Note the graph is NOT connected to the speakers. Routing the microphone to
 * the output would cause feedback howl, and we only need to measure the
 * signal, not hear it.
 *
 * WHAT WE MEASURE
 *
 * Each frame we pull two things out of the AnalyserNode:
 *
 *   - the time-domain waveform, from which we compute RMS (root mean square).
 *     RMS is the standard measure of "how loud is this", better than peak
 *     amplitude because a single spike does not dominate it.
 *
 *   - the frequency spectrum, which the background visualiser draws. Sharing
 *     one AnalyserNode means we analyse the signal once and use it twice.
 *
 * NOISE FLOOR
 *
 * A room is never truly silent, and the speakers playing the song leak into
 * the mic too. Anything near that background level has to count as
 * silence - otherwise an air conditioner, or the backing track itself,
 * would score as enthusiastic singing.
 *
 * The background is measured when the mic is switched on (calibrate), but
 * that moment is often badly chosen: the mic is usually turned on as a song
 * starts, and if the singing starts straight away the SINGER gets measured
 * as background - in testing, a great singer then scored the same as a
 * silent one. So the level keeps adjusting itself afterwards (NoiseFloor
 * below): it is re-measured every half second from the quietest moments of
 * the last few seconds - the breaths between phrases, the gaps between
 * lines - which is where only the room and the speakers can be heard.
 *
 * ECHO CANCELLATION
 *
 * Off by default, because it can reshape the singer's voice. But it exists
 * precisely to remove what the page itself is playing from the mic, so with
 * speakers close to the mic it may help; the Host tab has a switch to try it.
 */

// 2048 samples per analysis window. Larger means finer frequency detail but
// slower response; this is the usual balance and gives 1024 spectrum bins.
const FFT_SIZE = 2048;

// Smooths the spectrum between frames so the visualiser does not strobe.
// It does not affect the time-domain data RMS is computed from.
const SMOOTHING = 0.8;

// How long to sample the room before deciding what counts as silence.
const CALIBRATION_MS = 1500;

// Voice must exceed the noise floor by this multiple to count as singing.
// Generous enough to ignore room hum, low enough to catch quiet singers.
const VOICE_MARGIN = 2.5;

// If the room is near-silent, the measured floor can be almost zero, and any
// tiny sound would then clear the margin. This sets a sensible minimum.
const MIN_THRESHOLD = 0.012;

// The self-adjusting background level: the 10th percentile (the quiet end)
// of the last 8 seconds, re-measured every half second once 2 seconds have
// been heard. 8 seconds always contains some breaths or a line break, even
// in a busy song; the quiet end of it is the background, not the singer.
const ADAPT_WINDOW_SECONDS = 8;
const ADAPT_PERCENTILE = 0.1;
const ADAPT_EVERY_SECONDS = 0.5;
const ADAPT_AFTER_SECONDS = 2;

/**
 * The background level, kept up to date from what the mic hears.
 * Separate from MicCapture so it can be tested without a microphone.
 */
export class NoiseFloor {
    constructor(initialFloor = 0) {
        this.reset(initialFloor);
    }

    reset(initialFloor = 0) {
        this.floor = initialFloor;
        this.samples = [];          // { rms, time }
        this.head = 0;              // samples before this index are too old
        this.lastUpdate = -Infinity;
    }

    /** Loudness above which a frame counts as someone singing. */
    get threshold() {
        return Math.max(MIN_THRESHOLD, this.floor * VOICE_MARGIN);
    }

    /** Hear one frame (time in seconds). */
    add(rms, time) {
        this.samples.push({ rms, time });
        while (this.samples[this.head].time < time - ADAPT_WINDOW_SECONDS) this.head++;

        // Drop the old samples now and then rather than on every frame.
        if (this.head > 1000) {
            this.samples = this.samples.slice(this.head);
            this.head = 0;
        }

        const span = time - this.samples[this.head].time;
        if (span >= ADAPT_AFTER_SECONDS && time - this.lastUpdate >= ADAPT_EVERY_SECONDS) {
            const recent = this.samples.slice(this.head).map((s) => s.rms).sort((a, b) => a - b);
            this.floor = recent[Math.floor(recent.length * ADAPT_PERCENTILE)];
            this.lastUpdate = time;
        }
    }
}

export class MicCapture {
    constructor() {
        this.stream = null;
        this.context = null;
        this.analyser = null;
        this.source = null;

        this.timeData = null;        // waveform samples, for RMS
        this.frequencyData = null;   // spectrum bins, for the visualiser

        this.noise = new NoiseFloor();
        this.isActive = false;

        // Set before start(): see ECHO CANCELLATION at the top of this file.
        this.echoCancellation = false;
    }

    /** Loudness above which a frame counts as singing (see NOISE FLOOR above). */
    get voiceThreshold() {
        return this.noise.threshold;
    }

    /**
     * Ask for microphone access and build the audio graph.
     *
     * Throws a friendly Error if permission is refused or no mic exists, so
     * the UI can show something more useful than the raw browser message.
     */
    async start() {
        if (this.isActive) return;

        try {
            this.stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    // All three are normally ON by default, and all three
                    // can distort what we are trying to measure:
                    // echoCancellation and noiseSuppression remove parts of
                    // the signal, and autoGainControl actively fights the
                    // volume variation the energy score is looking for.
                    // Echo cancellation can be switched on from the Host tab.
                    echoCancellation: this.echoCancellation,
                    noiseSuppression: false,
                    autoGainControl: false,
                },
            });
        } catch (error) {
            if (error.name === 'NotAllowedError') {
                throw new Error('Microphone access was denied. Allow it to enable scoring.');
            }
            if (error.name === 'NotFoundError') {
                throw new Error('No microphone was found on this device.');
            }
            throw new Error(`Could not start the microphone: ${error.message}`);
        }

        this.context = new (window.AudioContext || window.webkitAudioContext)();

        // Browsers start an AudioContext suspended until a user gesture.
        if (this.context.state === 'suspended') {
            await this.context.resume();
        }

        this.analyser = this.context.createAnalyser();
        this.analyser.fftSize = FFT_SIZE;
        this.analyser.smoothingTimeConstant = SMOOTHING;

        this.source = this.context.createMediaStreamSource(this.stream);
        this.source.connect(this.analyser);   // NOT connected to the speakers

        // Allocated once and reused every frame. Creating these arrays inside
        // the animation loop would produce garbage 60 times a second.
        this.timeData = new Float32Array(this.analyser.fftSize);
        this.frequencyData = new Uint8Array(this.analyser.frequencyBinCount);

        this.isActive = true;
    }

    /**
     * Measure the room's background level.
     *
     * Call this while nobody is singing - typically during a song's intro.
     * The median is used rather than the mean because one cough during
     * calibration would drag a mean upward and make the threshold far too
     * high for the rest of the song.
     */
    async calibrate(durationMs = CALIBRATION_MS) {
        if (!this.isActive) return this.voiceThreshold;

        const samples = [];
        const start = performance.now();

        await new Promise((resolve) => {
            const sample = () => {
                if (performance.now() - start >= durationMs) return resolve();
                samples.push(this.readRms());
                requestAnimationFrame(sample);
            };
            sample();
        });

        if (samples.length > 0) {
            samples.sort((a, b) => a - b);
            // A starting point only: NoiseFloor keeps correcting it from
            // here on, in case this moment was not a quiet one.
            this.noise.reset(samples[Math.floor(samples.length / 2)]);
        }

        return this.voiceThreshold;
    }

    /**
     * Current loudness as RMS.
     *
     * RMS = square root of the mean of the squared samples. Squaring makes
     * negative samples count the same as positive ones (a waveform swings
     * both ways), and the square root brings the result back to the original
     * scale. Output is roughly 0 (silence) to 1 (clipping).
     */
    readRms() {
        if (!this.isActive) return 0;

        this.analyser.getFloatTimeDomainData(this.timeData);

        let sumOfSquares = 0;
        for (let i = 0; i < this.timeData.length; i++) {
            sumOfSquares += this.timeData[i] * this.timeData[i];
        }

        return Math.sqrt(sumOfSquares / this.timeData.length);
    }

    /** The frequency spectrum, for the background visualiser. */
    readSpectrum() {
        if (!this.isActive) return null;
        this.analyser.getByteFrequencyData(this.frequencyData);
        return this.frequencyData;
    }

    /**
     * One complete measurement for this frame.
     *
     * This is the object the scoring engine consumes, so adding a new
     * measurement later (pitch, for example) means adding a field here.
     */
    readFrame(timestamp) {
        const rms = this.readRms();
        this.noise.add(rms, timestamp);

        return {
            time: timestamp,
            rms,
            // Normalised 0-1 for the on-screen level bar. Dividing by 0.35
            // rather than 1.0 because normal singing rarely exceeds ~0.35 RMS,
            // so this uses the full width of the bar.
            level: Math.min(1, rms / 0.35),
            isVoice: rms > this.voiceThreshold,
            spectrum: this.frequencyData,
            // The raw waveform, needed by a future pitch detector.
            timeData: this.timeData,
            sampleRate: this.context?.sampleRate ?? 48000,
        };
    }

    /** Release the microphone and free the audio resources. */
    stop() {
        // Stopping every track is what actually turns off the browser's
        // recording indicator - closing the context alone does not.
        this.stream?.getTracks().forEach((track) => track.stop());
        this.source?.disconnect();

        if (this.context && this.context.state !== 'closed') {
            this.context.close();
        }

        this.stream = null;
        this.context = null;
        this.analyser = null;
        this.source = null;
        this.isActive = false;
    }
}
