/**
 * main.js - wires the karaoke screen together.
 *
 * Every feature lives in its own module; this file is the conductor. It
 * creates each piece, connects them with callbacks, and owns the one thing
 * none of them can own alone: the flow of a performance.
 *
 * THE MAIN LOOP
 *
 *   1. A song is queued - usually from a guest's phone, or from the drawer
 *      behind the mic bubble on this screen. A toast says who added what.
 *   2. The queue advances  ->  load audio + lyrics  ->  play
 *        - a 3-2-1 countdown runs over the end of the intro
 *        - the microphone is scored silently in the background
 *   3. The song ENDS       ->  save the score  ->  reveal it, with a chime
 *      The song is SKIPPED ->  no score, no chime - straight to step 2
 *   4. Reveal closed       ->  advance the queue  ->  back to step 2
 *
 * THE SCREEN (see index.html and css/videoke.css)
 *
 *   top strip       song title and progress bar, kept small
 *   middle          the countdown, and every popup (bubble, drawer, toasts)
 *   lyrics band     current line + next line - the singer's space, which
 *                   nothing is allowed to cover (videoke/performanceZone.js)
 */

import * as api from './lib/api.js';
import { QueueSocket } from './lib/socket.js';
import { Leaderboard } from './leaderboard/leaderboard.js';
import { AudioPlayer } from './player/audioPlayer.js';
import { LyricsRenderer } from './player/lyricsRenderer.js';
import { parseLrc } from './player/lrcParser.js';
import { QueueClient } from './queue/queueClient.js';
import { SearchPanel } from './search/searchPanel.js';
import { buildScoringTargets } from './scoring/features/timing.js';
import { MicCapture } from './scoring/micCapture.js';
import { ScoreEngine } from './scoring/scoreEngine.js';
import { ScoreReveal } from './scoring/scoreReveal.js';
import { startBackground } from './videoke/background.js';
import { ChatHead } from './videoke/chatHead.js';
import { unlockChimeOnFirstGesture } from './videoke/chime.js';
import { Countdown, firstSungLineTime } from './videoke/countdown.js';
import { PerformanceZone } from './videoke/performanceZone.js';
import { Toasts } from './videoke/toasts.js';
import { UpNext } from './videoke/upNext.js';

/** Short helper - used constantly below. */
const $ = (id) => document.getElementById(id);

/* ==========================================================================
   Application state
   ========================================================================== */

const state = {
    nowPlaying: null,       // the queue entry currently on screen
    pendingSong: null,      // song picked in the drawer, waiting for a name
    practiceMode: false,    // practice mode never saves a score
    scoreFrameId: null,     // the silent scoring loop
    micAsked: false,        // has the "turn on the mic?" prompt been answered?
    knownEntryIds: null,    // queue entries already seen - for "X added Y" toasts
};

/* ==========================================================================
   Build the pieces
   ========================================================================== */

startBackground($('bg-video'));
unlockChimeOnFirstGesture();

const zone = new PerformanceZone({
    band: document.querySelector('.band'),
    countdown: $('countdown'),
    layer: $('overlay-layer'),
});

const toasts = new Toasts($('toasts'), zone);

function showToast(message, isError = true) {
    toasts.show(message, { error: isError });
}

const player = new AudioPlayer({
    audio: $('audio'),
    fill: $('scrub-fill'),
    current: $('time-current'),
    duration: $('time-duration'),
    scrub: $('scrub'),
});

// The 'band' layout: current line + next line, in the lower third.
const lyrics = new LyricsRenderer($('lyrics'), { layout: 'band' });

const countdown = new Countdown($('countdown'), zone);
// Share the lyrics' animation loop rather than running a second one.
lyrics.onFrame = (time) => countdown.update(time);
// Once the singing starts, the mic question has had its chance. (During
// the countdown itself the zone guard keeps it out of the count's way.)
countdown.onLineStart = () => hideMicPrompt();

const mic = new MicCapture();

// Echo cancellation is remembered in this browser (see the Host tab).
const ECHO_STORAGE_KEY = 'karaoke-echo-cancellation';
try {
    mic.echoCancellation = localStorage.getItem(ECHO_STORAGE_KEY) === 'on';
} catch {
    /* storage unavailable - it just starts off */
}
const scoreEngine = new ScoreEngine();

const reveal = new ScoreReveal({
    overlay: $('reveal'),
    score: $('reveal-score'),
    label: $('reveal-label'),
    detail: $('reveal-detail'),
    next: $('reveal-next'),
});

const leaderboard = new Leaderboard($('leaderboard'));

const queue = new QueueClient($('queue-list'), {
    isHost: true,        // this is the host screen, so show the controls
    onError: showToast,
});

// The one-line "up next" queue under the top strip.
const upNext = new UpNext($('upnext-list'));

const chatHead = new ChatHead({
    layer: $('overlay-layer'),
    bubble: $('bubble'),
    badge: $('bubble-badge'),
    drawer: $('drawer'),
}, { zone });

const socket = new QueueSocket();

/* ==========================================================================
   Adding a song from the drawer (the backup to phones)
   ========================================================================== */

new SearchPanel(
    {
        input: $('search-input'),
        keypad: $('keypad'),
        display: $('code-display'),
        results: $('search-results'),
    },
    (song) => askWhoIsSinging(song)
);

/**
 * Picking a song shows a small "who's singing?" form at the top of the
 * drawer. It used to be a full-screen dialog; inside the drawer it stays out
 * of the singer's way like everything else.
 */
function askWhoIsSinging(song) {
    state.pendingSong = song;
    chatHead.open('songs');

    $('pending-song').textContent = `[${song.code}] ${song.title} — ${song.artist}`;
    $('singer-error').textContent = '';
    $('pending').hidden = false;
    // The form sits at the top of the tab; the list may be scrolled down.
    $('panel-songs').scrollTop = 0;

    const input = $('singer-name');
    // select() so typing replaces the previous singer's name, which is kept
    // because most people queue several songs in a row.
    input.focus();
    input.select();
}

function cancelPending() {
    $('pending').hidden = true;
    state.pendingSong = null;
}

async function confirmSinger() {
    const name = $('singer-name').value.trim();

    if (!name) {
        $('singer-error').textContent = 'Please enter a name.';
        $('singer-name').focus();
        return;
    }

    try {
        const payload = await api.addToQueue({ song_id: state.pendingSong.id, singer_name: name });
        cancelPending();
        // The reply carries the new queue - use it now rather than waiting
        // for the broadcast. It is also what raises the "added" toast.
        handleQueuePayload(payload);
    } catch (error) {
        $('singer-error').textContent = error.message;
    }
}

$('singer-confirm').addEventListener('click', confirmSinger);
$('singer-cancel').addEventListener('click', cancelPending);

$('singer-name').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') confirmSinger();
    if (event.key === 'Escape') {
        event.stopPropagation();   // cancel the form, not close the drawer
        cancelPending();
    }
});

/* ==========================================================================
   Loading and playing a performance
   ========================================================================== */

/**
 * Load a queue entry: fetch its song, audio and lyrics, and get ready to play.
 */
async function loadEntry(entry) {
    state.nowPlaying = entry;

    // Another screen may have moved the queue on while a score was showing.
    reveal.dismiss();
    $('start-prompt').hidden = true;

    const hasSong = Boolean(entry);
    $('play-button').disabled = !hasSong;
    $('skip-button').disabled = !hasSong;
    document.body.classList.toggle('is-idle', !hasSong);

    if (!hasSong) {
        $('np-singer').textContent = 'Nobody singing';
        $('np-song').textContent = 'Add a song to begin';
        $('lyrics-notice').hidden = true;
        player.stop();
        countdown.setTarget(null);
        hideMicPrompt();
        lyrics.showMessage('Scan the code with your phone to add a song 🎤');
        return;
    }

    $('np-singer').textContent = entry.singer_name;
    $('np-song').textContent = `${entry.song_title} — ${entry.song_artist}`;

    try {
        const song = await api.getSong(entry.song_id);
        player.load(song.audio_url);
        await loadLyrics(song);
        maybeAskForMic();
    } catch (error) {
        showToast(error.message);
    }
}

/**
 * Load lyrics, using timestamped ones when they exist and falling back to the
 * plain text sheet when they do not.
 */
async function loadLyrics(song) {
    const notice = $('lyrics-notice');

    if (song.lyrics_url) {
        try {
            const response = await fetch(song.lyrics_url);
            const text = await response.text();

            lyrics.loadLrc(text);
            lyrics.attach($('audio'));

            const { lines } = parseLrc(text);

            // The countdown ends as the first SUNG line starts.
            countdown.setTarget(firstSungLineTime(lines));

            // Every word the singer should sing, with its time - what the
            // silent scoring engine judges against (scoring/features/timing.js).
            scoreEngine.reset(buildScoringTargets(lines), {
                judging: !state.practiceMode,
            });

            notice.hidden = true;
            return;
        } catch {
            showToast('Could not load the lyric file.');
        }
    }

    // No timing - no countdown, and nothing to score against.
    countdown.setTarget(null);
    scoreEngine.reset(undefined, { judging: false });

    // Show the words if there are any, but explain why they will not move.
    try {
        const data = await api.getSongLyrics(song.id);

        if (data.format === 'plain' && data.lines.length > 0) {
            lyrics.loadPlain(data.lines);
            notice.innerHTML =
                'Needs manual sync: these lyrics will not scroll or be scored. ' +
                '<a href="/sync">Time them in the sync editor</a>.';
        } else {
            lyrics.showMessage('No lyrics are available for this song.');
            notice.innerHTML =
                'No lyrics yet. <a href="/sync">Add them in the sync editor</a>.';
        }
        notice.hidden = false;
    } catch {
        lyrics.showMessage('No lyrics are available for this song.');
    }
}

/* ==========================================================================
   Silent scoring
   ========================================================================== */

/**
 * Feed the microphone to the score engine every frame while the song plays.
 *
 * Nothing is drawn: there is no live meter any more. Knowing your score
 * mid-song mostly makes people watch the number instead of singing, so it
 * is kept for the reveal at the end.
 */
function startScoring() {
    if (!mic.isActive || state.scoreFrameId !== null) return;

    const tick = () => {
        const frame = mic.readFrame(performance.now() / 1000);
        scoreEngine.process(frame, player.currentTime);
        state.scoreFrameId = requestAnimationFrame(tick);
    };

    state.scoreFrameId = requestAnimationFrame(tick);
}

function stopScoring() {
    if (state.scoreFrameId !== null) {
        cancelAnimationFrame(state.scoreFrameId);
        state.scoreFrameId = null;
    }
}

/**
 * The song reached its end by itself: work out the score and reveal it.
 * (A skipped song never gets here - see the skip button below.)
 */
function finishPerformance() {
    stopScoring();

    const entry = state.nowPlaying;
    const result = scoreEngine.finish();

    // Nothing to reveal if the mic was off, in practice mode, or when the
    // song had no timing to judge against.
    if (!mic.isActive || state.practiceMode || !result.judged) {
        showToast(
            state.practiceMode
                ? 'Practice run finished - no score.'
                : 'Song finished. Turn on the mic to get a score next time.',
            false
        );
        nextSinger();
        return;
    }

    // Reveal first, save alongside: the celebration should not wait on the
    // network.
    const waiting = queue.entries.filter((e) => e.status === 'waiting').length;
    reveal.show(result, entry, { queueEmpty: waiting === 0 });

    api.saveScore({
        song_id: entry.song_id,
        queue_entry_id: entry.id,
        singer_name: entry.singer_name,
        final_score: result.finalScore,
        timing_score: result.timingScore,
        energy_score: result.energyScore,
        pitch_score: result.pitchScore,
        energy_timeline: result.energyTimeline,
    })
        .then(refreshLeaderboard)
        .catch((error) => showToast(`Could not save the score: ${error.message}`));
}

/** The reveal closed (Next, or its timer): on to the next song. */
reveal.onClose = () => nextSinger();

async function nextSinger() {
    try {
        await api.advanceQueue(false);
    } catch (error) {
        showToast(error.message);
    }
}

/* ==========================================================================
   Player events
   ========================================================================== */

player.onEnded = () => finishPerformance();
player.onError = (message) => showToast(message);

// The browser would not start the song by itself - it needs one click on
// this page first. Offer a big, obvious button for exactly that.
player.onBlocked = () => {
    $('start-prompt').hidden = false;
    zone.guard();
};

$('start-prompt').addEventListener('click', () => {
    $('start-prompt').hidden = true;
    player.play();
});

player.onPlay = () => {
    $('play-button').textContent = 'Pause';
    $('start-prompt').hidden = true;
    countdown.setPaused(false);
    if (mic.isActive) startScoring();
};

// Browsers fire 'pause' when a track reaches its end too.
player.onPause = () => {
    $('play-button').textContent = 'Play';
    countdown.setPaused(true);
    stopScoring();
};

$('play-button').addEventListener('click', () => player.togglePlay());

$('skip-button').addEventListener('click', async () => {
    // During the reveal the song already finished - "skip" then just means
    // "next", and the song must still be recorded as sung, not skipped.
    if (reveal.isOpen) {
        reveal.hide();
        return;
    }

    // Skipped: no score, no chime - straight on to the next song.
    stopScoring();
    player.pause();
    try {
        await api.advanceQueue(true);   // true = skipped, not completed
    } catch (error) {
        showToast(error.message);
    }
});

$('clear-queue').addEventListener('click', async () => {
    try {
        await api.clearQueue();
        showToast('Queue cleared.', false);
    } catch (error) {
        showToast(error.message);
    }
});

/* ==========================================================================
   Microphone
   ========================================================================== */

function updateMicButton() {
    $('mic-button').textContent = mic.isActive ? 'Turn off Microphone' : 'Turn on Microphone';
    $('mic-button').classList.toggle('btn--gold', mic.isActive);
}

async function enableMic() {
    try {
        await mic.start();
        updateMicButton();
        showToast('🎤 Mic on - your score appears when the song ends.', false);

        // Measure the room's background noise so it does not count as
        // singing. Done quietly; it takes about a second and a half.
        await mic.calibrate();
        if (player.isPlaying) startScoring();
    } catch (error) {
        showToast(error.message);
        updateMicButton();
    }
}

function disableMic() {
    mic.stop();
    stopScoring();
    updateMicButton();
    showToast('Mic off - songs will not be scored.', false);
}

$('mic-button').addEventListener('click', () => {
    if (mic.isActive) disableMic();
    else enableMic();
});

/**
 * Echo cancellation: removes what this page is playing from the mic, for
 * when the speakers are close enough to leak into it (which otherwise
 * counts as singing). Off by default because it can also reshape the voice.
 * It only takes effect when the mic starts, so a running mic is restarted.
 */
function updateEchoButton() {
    $('echo-button').textContent = `Echo cancellation: ${mic.echoCancellation ? 'On' : 'Off'}`;
    $('echo-button').setAttribute('aria-pressed', String(mic.echoCancellation));
    $('echo-button').classList.toggle('btn--gold', mic.echoCancellation);
}

$('echo-button').addEventListener('click', async () => {
    mic.echoCancellation = !mic.echoCancellation;
    try {
        localStorage.setItem(ECHO_STORAGE_KEY, mic.echoCancellation ? 'on' : 'off');
    } catch {
        /* not remembered - still applies now */
    }
    updateEchoButton();

    if (mic.isActive) {
        mic.stop();
        stopScoring();
        await enableMic();
    } else {
        showToast(`Echo cancellation ${mic.echoCancellation ? 'on' : 'off'} - applies when the mic is turned on.`, false);
    }
});

updateEchoButton();

/**
 * The one-time "turn on the mic?" question, asked as a song loads.
 *
 * The mic used to be switched on from the live-score panel, which no longer
 * exists. Asking just before a song is the moment it matters. It never
 * blocks the song, it goes away by itself (when the first line starts, or
 * after 15 seconds), and once answered either way it is not asked again - the switch
 * stays available in the drawer's Host tab.
 */
let micPromptTimer = null;

function maybeAskForMic() {
    // Not once the count-in has begun: after it, someone is singing, and a
    // question popping up then is exactly the interruption to avoid.
    if (mic.isActive || state.micAsked || zone.isCounting) return;

    $('mic-prompt').hidden = false;
    zone.guard();
    clearTimeout(micPromptTimer);
    micPromptTimer = setTimeout(hideMicPrompt, 15000);
}

function hideMicPrompt() {
    clearTimeout(micPromptTimer);
    $('mic-prompt').hidden = true;
}

$('mic-prompt-yes').addEventListener('click', () => {
    state.micAsked = true;
    hideMicPrompt();
    enableMic();
});

$('mic-prompt-no').addEventListener('click', () => {
    state.micAsked = true;
    hideMicPrompt();
});

/* ==========================================================================
   Practice mode
   ========================================================================== */

$('practice-toggle').addEventListener('click', (event) => {
    state.practiceMode = !state.practiceMode;

    const button = event.currentTarget;
    button.setAttribute('aria-pressed', String(state.practiceMode));
    button.classList.toggle('btn--gold', state.practiceMode);

    // Re-arm the engine so the change takes effect for this song too.
    scoreEngine.judging = !state.practiceMode;

    showToast(
        state.practiceMode
            ? 'Practice mode on - sing freely, nothing is scored.'
            : 'Practice mode off - performances are scored again.',
        false
    );
});

/* ==========================================================================
   Join by Phone
   ========================================================================== */

/**
 * Fill in the always-visible join chip. The QR image is generated by the
 * server for this computer's current network address; if the qrcode package
 * is missing the image is dropped and the address alone is shown.
 */
async function setUpJoinChip() {
    $('join-qr').addEventListener('error', () => $('join-qr').remove(), { once: true });
    // Cache-busting timestamp: the LAN address can change between sessions,
    // and a stale cached QR would point at the wrong machine.
    $('join-qr').src = `/api/join-qr.png?t=${Date.now()}`;

    try {
        const info = await api.getJoinInfo();
        // Shown without the "http://" - shorter, and phones add it anyway.
        $('join-url').textContent = info.url.replace(/^https?:\/\//, '');
        $('qr-url').textContent = info.url;
    } catch {
        /* the chip still works as a button to the big QR code */
    }
}

function openQrModal() {
    $('qr-image').src = `/api/join-qr.png?t=${Date.now()}`;
    $('qr-modal').hidden = false;
    zone.guard();
    $('qr-close').focus();
}

$('join-chip').addEventListener('click', openQrModal);
$('qr-button').addEventListener('click', () => {
    chatHead.close();
    openQrModal();
});
$('qr-close').addEventListener('click', () => { $('qr-modal').hidden = true; });
// A click on the dark backdrop around the popup closes it too.
$('qr-modal').addEventListener('click', (event) => {
    if (event.target === event.currentTarget) $('qr-modal').hidden = true;
});

/* ==========================================================================
   Live updates
   ========================================================================== */

/**
 * "🎤 Ana added Sunflower — Rex Orange County to the queue"
 *
 * Worked out by comparing the queue with the last one seen, so it works the
 * same whether the song came from a phone, from this screen's drawer, or
 * arrived by the polling fallback - and a screen that joins late does not
 * announce the whole existing queue (the first queue seen is the baseline).
 */
function announceNewEntries(entries) {
    const ids = new Set(entries.map((entry) => entry.id));

    if (state.knownEntryIds !== null) {
        for (const entry of entries) {
            if (state.knownEntryIds.has(entry.id)) continue;
            showToast(
                `🎤 ${entry.singer_name} added ${entry.song_title} — ${entry.song_artist} to the queue`,
                false
            );
        }
    }

    state.knownEntryIds = ids;
}

/** Every copy of the queue - socket, poll, or a reply - comes through here. */
function handleQueuePayload(payload) {
    if (!payload) return;

    queue.update(payload);
    upNext.update(payload.queue ?? []);
    announceNewEntries(payload.queue ?? []);

    const waiting = (payload.queue ?? []).filter((e) => e.status === 'waiting').length;
    chatHead.setBadge(waiting);

    // Start the next song automatically when the server says one is playing
    // and it is not the one already loaded. Adding a song NEVER opens the
    // drawer - the toast above is the only sign, so the singer is left alone.
    const playing = payload.now_playing;
    if (playing && playing.id !== state.nowPlaying?.id) {
        loadEntry(playing).then(() => player.play());
    } else if (!playing && state.nowPlaying) {
        loadEntry(null);
    }
}

socket.on('queue:updated', handleQueuePayload);

socket.on('player:now_playing', (payload) => {
    if (!payload.entry) loadEntry(null);
});

socket.on('score:recorded', () => refreshLeaderboard());

socket.onStatusChange = (online) => {
    const element = $('connection-status');
    element.textContent = online ? 'Live' : 'Offline';
    element.className = `status-dot status-dot--${online ? 'online' : 'offline'}`;
};

async function refreshQueue() {
    try {
        handleQueuePayload(await api.getQueue());
    } catch {
        // Silent: the connection indicator already reports the problem, and
        // a toast every two seconds while offline would be unbearable.
    }
}

async function refreshLeaderboard() {
    try {
        const data = await api.getLeaderboard();
        leaderboard.update(data.leaderboard);
    } catch {
        // Same reasoning as above.
    }
}

/* ==========================================================================
   Keyboard
   ========================================================================== */

document.addEventListener('keydown', (event) => {
    const typing = ['INPUT', 'TEXTAREA'].includes(document.activeElement?.tagName);

    // Punching in a song number on the keyboard opens the drawer at the
    // keypad, like picking up a videoke remote. SearchPanel reads the digit
    // itself; this only makes sure you can see it.
    if (!typing && /^[0-9]$/.test(event.key) && !reveal.isOpen) {
        if (!chatHead.isOpen) chatHead.open('songs');
        else chatHead.resetIdle();
    }

    if (event.key === 'Escape') {
        if (!$('qr-modal').hidden) $('qr-modal').hidden = true;
        else if (!$('pending').hidden) cancelPending();
        else if (chatHead.isOpen) chatHead.close();
    }
});

/* ==========================================================================
   Start up
   ========================================================================== */

socket.connect(refreshQueue);
setUpJoinChip();
lyrics.showMessage('Scan the code with your phone to add a song 🎤');
document.body.classList.add('is-idle');

// The first queue seen: loads (and tries to start) whatever is playing,
// and sets the baseline for the "added" toasts.
refreshQueue();
refreshLeaderboard();
