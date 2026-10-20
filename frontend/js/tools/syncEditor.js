/**
 * syncEditor.js - the tap-along tool that gives lyrics their timing.
 *
 * WHY THIS EXISTS
 *
 * Synced lyrics need timing: every line - and, for the finest wipe, every
 * word - paired with the moment it is sung. Typing timestamps by hand is
 * miserable, so instead you play the song and tap a key as each one begins.
 * The editor records audio.currentTime for each tap.
 *
 * TWO MODES
 *
 *   Lines  one tap per line. Quick: a whole song in one playthrough.
 *   Words  one tap per word. Slower, but the wipe then follows how each word
 *          was actually sung, instead of moving across a line at one pace.
 *
 * Both edit the same song. Re-timing a line in Lines mode carries its word
 * timings along with it, so the two can be mixed freely.
 *
 * The timing RULES (what a stamp does, what may be saved) live in
 * wordTiming.js. This file is the interface: keys, clicks and drawing.
 *
 * KEYS - both modes
 *   SPACE / ENTER  play / pause
 *   J              stamp the next line (Lines) or the next word (Words)
 *   BACKSPACE      undo the last stamp
 *   ← / →          nudge: the last line by 0.1s, or the selected word by
 *                  0.05s (0.01s with Shift)
 *   , / .          seek back / forward 2s
 *   L              replay the current line
 *   S              playback speed: 100% -> 75% -> 50%
 *
 * KEYS - Words mode only
 *   ↑ / ↓          select the previous / next word
 *   K              mark where the singing of the line stops
 *   R              re-tap the current line from its first word
 *
 *   N              (both modes) jump to the next word auto sync was unsure of
 *
 * AUTO SYNC
 *
 * The Auto Sync panel starts automatic word sync on the server and shows its
 * progress. When it finishes, the song reopens with the words auto sync was
 * unsure of highlighted - fix those, save, done. For a song you tapped by
 * hand, the result is kept as a draft you can load instead.
 *
 * ADD A SONG
 *
 * The song picker has a form that uploads a song from this computer; the
 * server makes its minus-one, imports it and auto-syncs it in the background.
 *
 * PREVIEW
 *
 * Plays the song full screen with the karaoke screen's own lyric band, drawn
 * from exactly what Save would write.
 */

import {
    fetchFromLrclib,
    getAutoSyncResult,
    getAutoSyncState,
    getImportJob,
    getSong,
    getSongLyrics,
    saveLrc,
    saveLyricsText,
    searchSongs,
    startAutoSync,
    uploadSong,
    updateSong,
    deleteSong,
} from '../lib/api.js';
import { formatTime } from '../player/audioPlayer.js';
import { formatLrc } from '../player/lrcParser.js';
import { LyricsRenderer } from '../player/lyricsRenderer.js';
import * as timing from './wordTiming.js';
import { mountActivityBadge } from '../lib/activityBadge.js';

mountActivityBadge(document.querySelector('.topbar__actions'), {
    before: document.querySelector('.topbar__actions').firstChild,
});

const $ = (id) => document.getElementById(id);

const LINE_NUDGE = 0.1;
const WORD_NUDGE = 0.05;
const WORD_NUDGE_FINE = 0.01;
const SEEK_STEP = 2;

// Replays start a little before the line or word, so you hear the run-up.
const REPLAY_LEAD = 1;

const SPEEDS = [1, 0.75, 0.5];

// Typical human reaction time. People tap slightly AFTER hearing a word
// start, so every stamp is pulled back by this much to compensate. Without
// it, lyrics consistently light up a beat late.
const REACTION_OFFSET = 0.15;

// Enough undo steps for a whole song of word taps.
const MAX_HISTORY = 1000;

const state = {
    song: null,
    lines: [],          // the model described in wordTiming.js
    mode: 'lines',      // 'lines' or 'words'
    lineCursor: 0,      // Lines mode: the line J stamps next
    wordCursor: null,   // Words mode: { l, w } J stamps next
    selected: null,     // Words mode: { l, w } the arrow keys nudge
    history: [],        // undo steps, newest last
    speedIndex: 0,
    dirty: false,       // are there unsaved changes?
};

const audio = $('sync-audio');

// The preview strip uses the karaoke screen's own renderer, so what you see
// here is exactly what singers will see.
const preview = new LyricsRenderer($('sync-preview'));

// The full-screen preview uses the karaoke screen's own two-line band.
const bandPreview = new LyricsRenderer($('preview-lyrics'), { layout: 'band' });

// What the editor knows about auto sync for the open song.
const autoSync = {
    result: null,       // the song's .autosync.json, if it has one
    progress: null,     // { percent, stage, message } while a run is going
    timer: null,        // polls the server while a run is going
    loaded: false,      // highlights or a draft are in the editor (Save marks them checked)
    draftLoaded: false,
};

const RUNNING = ['queued', 'processing'];

/* ==========================================================================
   Song picker
   ========================================================================== */

// While any song is being auto-synced, the list refreshes itself.
let listTimer = null;

/** The small auto-sync badge for a song in the list, or null. */
function autoSyncBadge(song) {
    const labels = {
        queued: 'auto: queued', processing: 'auto: running', done: 'auto: done',
        needs_review: 'auto: check', failed: 'auto: failed',
    };
    const badge = document.createElement('span');
    if (song.sync_locked) {
        badge.className = 'badge-auto badge-auto--locked';
        badge.textContent = 'locked';
        badge.title = 'Auto sync never touches this song (song_overrides.json).';
        return badge;
    }
    if (!labels[song.sync_status]) return null;
    badge.className = `badge-auto badge-auto--${song.sync_status}`;
    badge.textContent = labels[song.sync_status];
    badge.title = song.sync_message || '';
    return badge;
}

/** A small Edit/Delete link inside a song row; clicking it does not open the song. */
function rowAction(label, onClick) {
    const action = document.createElement('span');
    action.className = 'song-row__action';
    action.setAttribute('role', 'button');
    action.tabIndex = 0;
    action.textContent = label;
    action.addEventListener('click', (event) => {
        event.stopPropagation();
        onClick();
    });
    action.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        event.stopPropagation();
        onClick();
    });
    return action;
}

let editingSong = null;

function openEditSong(song) {
    editingSong = song;
    $('edit-code').value = song.code;
    $('edit-title').value = song.title;
    $('edit-artist').value = song.artist;
    $('edit-language').value = song.language || '';
    $('edit-error').hidden = true;
    $('edit-song-dialog').showModal();
}

$('edit-cancel').addEventListener('click', () => $('edit-song-dialog').close());

$('edit-song-form').addEventListener('submit', async (event) => {
    event.preventDefault();   // keep the dialog open until the server accepts the edit
    try {
        await updateSong(editingSong.id, {
            code: $('edit-code').value,
            title: $('edit-title').value,
            artist: $('edit-artist').value,
            language: $('edit-language').value,
        });
        $('edit-song-dialog').close();
        showToast('Song updated.', false);
        loadSongList();
    } catch (error) {
        $('edit-error').textContent = error.message;
        $('edit-error').hidden = false;
    }
});

async function removeSong(song) {
    if (!confirm(`Delete "${song.title}" (${song.code})?\n\n` +
                 'It is also removed from the queue and its scores are erased.')) return;
    const withFiles = confirm('Also delete its karaoke track and lyrics files?\n\n' +
        'OK = delete the files too. Cancel = keep them (the next import would add the song back).\n' +
        'The original recording is never deleted.');
    try {
        await deleteSong(song.id, withFiles);
        showToast(`Deleted ${song.title}.`, false);
        loadSongList();
    } catch (error) {
        showToast(error.message);
    }
}

async function loadSongList() {
    clearTimeout(listTimer);
    try {
        const data = await searchSongs('');
        const list = $('song-picker');
        list.innerHTML = '';

        if (data.songs.length === 0) {
            list.innerHTML =
                '<div class="empty-note">No songs imported yet. Run scripts/import_songs.py.</div>';
            return;
        }

        for (const song of data.songs) {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'song-row';

            const code = document.createElement('span');
            code.className = 'song-row__code';
            code.textContent = song.code;

            const details = document.createElement('span');
            const title = document.createElement('div');
            title.className = 'song-row__title';
            title.textContent = song.title;
            const artist = document.createElement('div');
            artist.className = 'song-row__artist';
            artist.textContent = song.artist;
            details.append(title, artist);

            const meta = document.createElement('span');
            meta.className = 'song-row__meta';
            const badge = document.createElement('span');

            // Songs that still need timing are exactly what this page is for,
            // so they are flagged rather than the synced ones.
            badge.className = song.is_synced ? '' : 'badge-unsynced';
            badge.textContent = song.is_synced ? 'synced' : 'needs manual sync';
            meta.appendChild(badge);

            const auto = autoSyncBadge(song);
            if (auto) meta.appendChild(auto);

            meta.append(
                rowAction('Edit', () => openEditSong(song)),
                rowAction('Delete', () => removeSong(song)),
            );

            row.append(code, details, meta);
            row.addEventListener('click', () => selectSong(song.id));
            list.appendChild(row);
        }

        if (data.songs.some((song) => RUNNING.includes(song.sync_status))) {
            listTimer = setTimeout(() => {
                if (!$('sync-picker-panel').hidden) loadSongList();
            }, 3000);
        }
    } catch (error) {
        showToast(error.message);
    }
}

/**
 * Show one of the three views: the song picker, the tap-along workspace, or
 * the paste box for a song with no lyrics. Exactly one is visible at a time.
 */
function showView(name) {
    $('sync-picker-panel').hidden = name !== 'picker';
    $('sync-workspace').hidden = name !== 'workspace';
    $('sync-paste-panel').hidden = name !== 'paste';
}

/**
 * Open a song for timing.
 *
 * @param {object} options - { quiet: true } skips the "already synced"
 *        message, for when the caller has just shown a more useful one
 */
async function selectSong(songId, options = {}) {
    // Losing a half-finished sync would be genuinely annoying, so confirm.
    if (state.dirty && !confirm('You have unsaved timing. Discard it?')) return;

    try {
        const song = await getSong(songId);
        const lyricData = await getSongLyrics(songId);

        state.song = song;
        state.lines = timing.createEditorLines(lyricData);
        state.lineCursor = 0;
        state.wordCursor = timing.firstUntimedPosition(state.lines);
        state.selected = null;
        state.history = [];
        state.dirty = false;

        stopWatchingAutoSync();
        await loadAutoSyncResult(song);

        if (lyricData.format === 'synced' && !options.quiet) {
            showToast('This song is already synced. You are editing its timing.', false);
        }

        audio.src = song.audio_url;
        audio.load();
        applySpeed();

        // No words at all: nothing to tap along to yet, so offer to paste
        // them (or try LRCLIB again) instead.
        if (state.lines.length === 0) {
            $('paste-title').textContent = `${song.title} — ${song.artist}`;
            $('paste-text').value = '';
            showView('paste');
            return;
        }

        $('sync-title').textContent = `${song.title} — ${song.artist}`;
        showSource(song, lyricData);
        showView('workspace');
        renderAutoSync();
        if (RUNNING.includes(song.sync_status)) watchAutoSync();

        // Skip over any leading section header so J starts on a real line.
        skipSections();
        render();
        refreshPreview();
    } catch (error) {
        showToast(error.message);
    }
}

/* ==========================================================================
   LRCLIB and pasted lyrics
   ========================================================================== */

/**
 * Say where this song's timing came from, and label the LRCLIB button to
 * match - "fetch" for an untimed song, "replace" for a timed one.
 *
 * The source is read from the file's [re:] tag, which the app writes on every
 * save: "Karaoke System sync editor" for timing tapped here, "LRCLIB (id ...)"
 * for a download. A file with no [re:] tag came from somewhere else.
 */
function showSource(song, lyricData) {
    let text;

    if (lyricData.format !== 'synced') {
        text = 'Not timed yet - needs manual sync. Tap along below, or try LRCLIB.';
    } else if (lyricData.metadata?.re) {
        text = `Timed by: ${lyricData.metadata.re}`;
    } else {
        text = 'Timed by: a .lrc file you supplied.';
    }

    $('sync-source').textContent = text;
    $('sync-fetch').textContent = song.is_synced
        ? 'Replace with LRCLIB timing'
        : 'Fetch timing from LRCLIB';
}

/** Disable the LRCLIB buttons while a lookup runs, so it cannot be doubled. */
function setFetching(busy) {
    const BUSY_LABEL = 'Looking up...';

    for (const id of ['sync-fetch', 'paste-fetch']) {
        const button = $(id);
        button.disabled = busy;

        if (busy) {
            button.dataset.label = button.textContent;
            button.textContent = BUSY_LABEL;
        } else if (button.textContent === BUSY_LABEL) {
            // Only restore the old label if nothing relabelled the button in
            // the meantime - reopening the song may have switched it from
            // "Fetch" to "Replace", and that newer label must win.
            button.textContent = button.dataset.label;
        }
    }
}

/**
 * Ask LRCLIB for this song, then reopen it to show whatever arrived.
 *
 * For a song that is already timed this REPLACES its timing, so it asks
 * first - timing tapped against your own audio may be better than a download.
 * The server keeps the old file as a .bak either way.
 */
async function fetchCurrentFromLrclib() {
    const song = state.song;
    if (!song) return;

    if (song.is_synced && !confirm(
        'Replace this song\'s current timing with LRCLIB\'s?\n\n' +
        'The current file is kept as a .bak, so this can be undone.'
    )) return;

    if (state.dirty && !confirm('You have unsaved timing. Discard it?')) return;

    setFetching(true);
    try {
        const result = await fetchFromLrclib(song.id, song.is_synced);

        if (result.status === 'synced') {
            const m = result.matched;
            showToast(
                `Timed from LRCLIB: ${m.artist} - ${m.title} ` +
                `(${m.duration}s, ${result.line_count} lines).` +
                (result.backup ? ' Previous timing kept as a backup.' : ''),
                false
            );
        } else if (result.status === 'plain_only') {
            showToast(
                'LRCLIB has the words but no timing that fits this recording. ' +
                'They are loaded - tap along to time them.',
                false
            );
        } else if (result.status === 'already_synced') {
            showToast('This song is already timed.', false);
        } else {
            showToast('LRCLIB has nothing for this song. It needs manual sync.');
        }

        // Reopen the song so the editor shows what is now on disk.
        state.dirty = false;
        await selectSong(song.id, { quiet: true });
    } catch (error) {
        showToast(error.message);
    } finally {
        setFetching(false);
    }
}

/** Save pasted lyrics, then open the song for tapping along. */
async function savePastedLyrics() {
    const text = $('paste-text').value;
    if (!text.trim()) {
        showToast('Paste the lyrics first.');
        return;
    }

    try {
        await saveLyricsText(state.song.id, text);
        showToast('Lyrics saved. Press SPACE to play, then J as each line begins.', false);
        await selectSong(state.song.id, { quiet: true });
    } catch (error) {
        showToast(error.message);
    }
}

$('sync-fetch').addEventListener('click', fetchCurrentFromLrclib);
$('paste-fetch').addEventListener('click', fetchCurrentFromLrclib);
$('paste-save').addEventListener('click', savePastedLyrics);

/* ==========================================================================
   Auto sync
   ========================================================================== */

const AUTO_LABELS = {
    none: 'Not run yet',
    queued: 'Queued',
    processing: 'Running',
    done: 'Done',
    needs_review: 'Done - some words to check',
    failed: 'Failed',
};

/**
 * Fetch the song's auto-sync result, if it has one, and use it:
 *   applied to the .lrc  -> highlight the words it was unsure of
 *   a draft              -> offer "Load auto-sync draft"
 */
async function loadAutoSyncResult(song) {
    Object.assign(autoSync, { result: null, progress: null, loaded: false, draftLoaded: false });
    if (song.sync_locked) return;
    try {
        autoSync.result = await getAutoSyncResult(song.id);
    } catch {
        return;   // no result yet - a 404 is the normal answer
    }
    if (autoSync.result.applied) {
        autoSync.loaded = timing.markForReview(state.lines, autoSync.result) > 0;
    }
}

/** Bring the Auto Sync panel up to date. */
function renderAutoSync() {
    const song = state.song;
    if (!song) return;

    const running = RUNNING.includes(song.sync_status) || autoSync.progress !== null;
    const status = song.sync_locked ? 'locked' : (running && autoSync.progress ? 'processing' : song.sync_status);
    const statusBox = $('autosync-status');
    statusBox.className = `autosync__status autosync__status--${status}`;

    let label = song.sync_locked ? 'Locked' : (AUTO_LABELS[status] ?? status);
    if (running && autoSync.progress) label += ` - ${Math.round(autoSync.progress.percent ?? 0)}%`;
    statusBox.textContent = label;

    let message;
    if (song.sync_locked) {
        message = 'This song is locked in song_overrides.json, so auto sync never touches it. '
            + 'Timing it by hand here still works.';
    } else if (running) {
        message = autoSync.progress?.message || 'Waiting for its turn...';
    } else if (song.sync_status === 'none') {
        message = 'Times every word by listening to the singing. Takes about 20 seconds if the '
            + "song's audio was processed before, otherwise several minutes.";
    } else {
        message = song.sync_message || '';
    }
    $('autosync-message').textContent = message;

    $('autosync-progress-wrap').hidden = !running;
    $('autosync-progress').style.width = `${autoSync.progress?.percent ?? 0}%`;

    $('autosync-controls').hidden = song.sync_locked;
    const run = $('autosync-run');
    run.disabled = running;
    run.textContent = running ? 'Running...' : (song.sync_status === 'none' ? 'Auto Sync' : 'Re-run auto sync');

    $('autosync-draft').hidden = !(autoSync.result && autoSync.result.applied === false
                                   && !autoSync.draftLoaded && !running);

    const toCheck = timing.reviewCount(state.lines);
    const next = $('autosync-next');
    next.hidden = toCheck === 0;
    next.textContent = `Next word to check (N) - ${toCheck} left`;
}

async function runAutoSync() {
    const song = state.song;
    if (!song) return;
    if (state.dirty && !confirm(
        'You have unsaved timing here. When auto sync finishes, this song reopens with its ' +
        'result and those changes are lost. Start anyway?'
    )) return;

    try {
        const answer = await startAutoSync(song.id, $('autosync-isolate').checked);
        song.sync_status = answer.sync_status;
        autoSync.progress = answer.progress;
        renderAutoSync();
        watchAutoSync();
    } catch (error) {
        showToast(error.message);
    }
}

/** Ask the server how the run is going, once a second, until it finishes. */
function watchAutoSync() {
    stopWatchingAutoSync();
    const songId = state.song.id;

    autoSync.timer = setInterval(async () => {
        if (!state.song || state.song.id !== songId) {
            stopWatchingAutoSync();
            return;
        }
        let answer;
        try {
            answer = await getAutoSyncState(songId);
        } catch {
            return;   // a missed poll is harmless; try again next second
        }
        state.song.sync_status = answer.sync_status;
        state.song.sync_message = answer.sync_message;
        autoSync.progress = answer.progress;
        renderAutoSync();

        if (!RUNNING.includes(answer.sync_status) && !answer.progress) {
            stopWatchingAutoSync();
            await autoSyncFinished(songId, answer);
        }
    }, 1000);
}

function stopWatchingAutoSync() {
    clearInterval(autoSync.timer);
    autoSync.timer = null;
}

/** Reopen the song to show the result, and say how it went. */
async function autoSyncFinished(songId, answer) {
    if (answer.sync_status === 'failed') {
        showToast(`Auto sync failed: ${answer.sync_message}`);
        return;
    }
    state.dirty = false;   // the user agreed to lose unsaved work when starting
    await selectSong(songId, { quiet: true });

    const toCheck = timing.reviewCount(state.lines);
    if (autoSync.result && autoSync.result.applied === false) {
        showToast('Auto sync finished. Your own tapped timing was kept - press ' +
                  '"Load auto-sync draft" to see the result.', false);
    } else if (toCheck > 0) {
        setMode('words');
        showToast(`Auto sync finished. ${toCheck} highlighted word${toCheck > 1 ? 's' : ''} ` +
                  'to check - press N to jump to each one.', false);
    } else {
        showToast('Auto sync finished. Every word was timed with confidence.', false);
    }
}

/** Replace the editor's word times with the draft's. Nothing is saved yet. */
function loadDraft() {
    if (!autoSync.result) return;
    if (!confirm(
        'Load the auto-sync draft? It replaces the word timing shown here for every line it ' +
        'has a result for. Nothing is saved until you press Save, and reopening the song ' +
        'brings your own timing back.'
    )) return;

    // One step per line would take dozens of Backspaces to undo; reopening
    // the song is the way back instead (the confirm above says so).
    state.history = [];
    const { lines, flagged } = timing.applyDraft(state.lines, autoSync.result);
    autoSync.loaded = true;
    autoSync.draftLoaded = true;
    setMode('words');
    changed();
    renderAutoSync();
    showToast(`Draft loaded into ${lines} lines. ${flagged} word${flagged === 1 ? '' : 's'} ` +
              'to check (N). Press Save to keep it.', false);
}

/** N: select the next highlighted word and play from just before it. */
function jumpToReview() {
    const pos = timing.nextReviewPosition(state.lines, state.selected);
    if (!pos) {
        showToast('No highlighted words left to check.', false);
        return;
    }
    if (state.mode !== 'words') setMode('words');
    selectWord(pos);
    chipFor(pos)?.scrollIntoView({ block: 'center' });
    showToast(timing.reviewReason(state.lines[pos.l].words[pos.w].review), false);
}

$('autosync-run').addEventListener('click', runAutoSync);
$('autosync-draft').addEventListener('click', loadDraft);
$('autosync-next').addEventListener('click', jumpToReview);

/* ==========================================================================
   Shared helpers
   ========================================================================== */

/**
 * The moment a tap refers to.
 *
 * Two corrections are taken off the audio's current position, both in REAL
 * time, so both are scaled by the playback rate (0.15s of real time while
 * playing at 50% is only 0.075s of song):
 *
 *   REACTION_OFFSET  a person presses slightly AFTER hearing the word
 *   handling delay   the page may be busy for a moment when the key goes down,
 *                    so the handler can run a little after the press. Every
 *                    key event records WHEN it happened (event.timeStamp), so
 *                    that delay is measured and removed - the stamp is where
 *                    the song was at your keypress, not where it had got to by
 *                    the time the page answered.
 */
function tapTime(event) {
    let handlingDelay = 0;
    if (event && typeof event.timeStamp === 'number') {
        const seconds = (performance.now() - event.timeStamp) / 1000;
        // Anything outside 0-1s is not a real delay (an old browser using a
        // different clock, say) - ignore it rather than warp the stamp.
        if (seconds > 0 && seconds < 1) handlingDelay = seconds;
    }
    return Math.max(0, audio.currentTime - (REACTION_OFFSET + handlingDelay) * audio.playbackRate);
}

/** Record a line's timing before changing it, so the change can be undone. */
function remember(l) {
    state.history.push({
        l,
        snapshot: timing.snapshotLine(state.lines[l]),
        lineCursor: state.lineCursor,
        wordCursor: state.wordCursor,
        selected: state.selected,
    });
    if (state.history.length > MAX_HISTORY) state.history.shift();
}

/** Undo the last stamp, in either mode. */
function undo() {
    const step = state.history.pop();
    if (!step) {
        showToast('Nothing to undo.', false);
        return;
    }

    timing.restoreLine(state.lines[step.l], step.snapshot);
    state.lineCursor = step.lineCursor;
    state.wordCursor = step.wordCursor;
    state.selected = step.selected;
    changed();
}

/** Anything that alters the timing ends here: redraw and mark unsaved. */
function changed() {
    state.dirty = true;
    render();
    schedulePreview();
    renderAutoSync();
}

function seekTo(seconds) {
    const end = Number.isFinite(audio.duration) ? audio.duration : Infinity;
    audio.currentTime = Math.min(end, Math.max(0, seconds));
}

function play() {
    audio.play().catch(() => showToast('Could not start playback.'));
}

function togglePlay() {
    if (audio.paused) play();
    else audio.pause();
}

/** Where a line starts - or, if it has no time yet, the last time before it. */
function startTimeFor(l) {
    for (let i = l; i >= 0; i--) {
        if (state.lines[i].time !== null) return state.lines[i].time;
    }
    return 0;
}

/**
 * The line L replays (and, in Words mode, R re-taps): the selected word's
 * line, or the line being tapped.
 */
function currentLineIndex() {
    if (state.mode === 'words') {
        return (state.selected ?? state.wordCursor)?.l ?? -1;
    }
    const here = state.lines[state.lineCursor];
    return here && here.time !== null ? state.lineCursor : lastStampedLineIndex();
}

function replayLine() {
    const l = currentLineIndex();
    if (l < 0) {
        showToast('Nothing timed to replay yet.', false);
        return;
    }
    seekTo(startTimeFor(l) - REPLAY_LEAD);
    play();
}

/* ==========================================================================
   Speed
   ========================================================================== */

function applySpeed() {
    const rate = SPEEDS[state.speedIndex];
    audio.playbackRate = rate;
    // Loading a new song resets playbackRate to the default, so set both.
    audio.defaultPlaybackRate = rate;
    // Keep the singer's pitch when slowed, instead of dropping it like a
    // slowed-down tape - slowed audio stays recognisable.
    audio.preservesPitch = true;
    $('sync-speed').textContent = `Speed ${Math.round(rate * 100)}%`;
}

function cycleSpeed() {
    state.speedIndex = (state.speedIndex + 1) % SPEEDS.length;
    applySpeed();
}

/* ==========================================================================
   Modes
   ========================================================================== */

function setMode(mode) {
    state.mode = mode;

    $('mode-lines').setAttribute('aria-pressed', String(mode === 'lines'));
    $('mode-words').setAttribute('aria-pressed', String(mode === 'words'));
    $('help-lines').hidden = mode !== 'lines';
    $('help-words').hidden = mode !== 'words';
    $('sync-stamp').textContent = mode === 'words' ? 'Stamp Word (J)' : 'Stamp Line (J)';

    if (mode === 'words' && !state.wordCursor) {
        state.wordCursor = timing.firstUntimedPosition(state.lines);
    }

    render();
}

/* ==========================================================================
   Lines mode
   ========================================================================== */

/** Section headers like [Chorus] are labels, not sung lines - never stamped. */
function skipSections() {
    while (
        state.lineCursor < state.lines.length &&
        state.lines[state.lineCursor].kind === 'section'
    ) {
        state.lineCursor++;
    }
}

function stampLine(event) {
    if (state.lineCursor >= state.lines.length) {
        showToast('Every line has been timed. Press Save.', false);
        return;
    }

    remember(state.lineCursor);
    // setLineTime also moves any word timings the line has, by the same amount.
    timing.setLineTime(state.lines[state.lineCursor], tapTime(event));
    state.lineCursor++;
    skipSections();
    changed();
}

function lastStampedLineIndex() {
    for (let i = state.lineCursor - 1; i >= 0; i--) {
        const line = state.lines[i];
        if (line.kind !== 'section' && line.time !== null) return i;
    }
    return -1;
}

function nudgeLine(seconds) {
    const i = lastStampedLineIndex();
    if (i < 0) return;
    timing.setLineTime(state.lines[i], state.lines[i].time + seconds);
    changed();
}

/* ==========================================================================
   Words mode
   ========================================================================== */

function stampWord(event) {
    const pos = state.wordCursor;
    if (!pos) {
        showToast('Every word is timed. Press Save.', false);
        return;
    }

    remember(pos.l);
    timing.stampWord(state.lines, pos, tapTime(event));

    // Select what was just tapped, so ← / → can correct it straight away.
    state.selected = pos;
    state.wordCursor = timing.nextPosition(state.lines, pos);
    changed();
}

function nudgeWord(seconds) {
    const pos = state.selected;
    if (!pos) {
        showToast('Select a word first: click it, or use ↑ / ↓.', false);
        return;
    }
    if (!timing.nudgeWord(state.lines[pos.l], pos.w, seconds)) {
        showToast('That word has no time yet - tap it with J first.', false);
        return;
    }
    changed();
}

function moveSelection(step) {
    const from = state.selected ?? state.wordCursor ?? timing.allPositions(state.lines)[0];
    if (!from) return;

    const to = step > 0
        ? timing.nextPosition(state.lines, from)
        : timing.previousPosition(state.lines, from);

    if (to) {
        state.selected = to;
        render();
        chipFor(to)?.scrollIntoView({ block: 'nearest' });
    }
}

/** Click a word: select it, make it the next to tap, and play from just before it. */
function selectWord(pos) {
    state.selected = pos;
    state.wordCursor = pos;
    render();

    const time = state.lines[pos.l].words[pos.w].time;
    if (time !== null) {
        seekTo(time - REPLAY_LEAD);
        play();
    }
}

/**
 * K: mark where the singing stops, on the line whose last word was just
 * tapped - the line of the word before the cursor.
 */
function markEnd(event) {
    const pos = state.wordCursor
        ? timing.previousPosition(state.lines, state.wordCursor)
        : timing.allPositions(state.lines).at(-1);
    if (!pos) return;

    remember(pos.l);
    const error = timing.markLineEnd(state.lines[pos.l], tapTime(event));
    if (error) {
        state.history.pop();   // nothing changed, so nothing to undo
        showToast(error);
        return;
    }

    showToast('End of the line marked.', false);
    changed();
}

/** R: clear the current line's word timings and replay it to tap again. */
function retapLine() {
    const l = currentLineIndex();
    if (l < 0 || state.lines[l].words.length === 0) return;

    // Where to replay from is worked out BEFORE clearing - afterwards the
    // line may have no time of its own left.
    const start = startTimeFor(l);

    remember(l);
    timing.clearLineWords(state.lines[l]);
    state.wordCursor = { l, w: 0 };
    state.selected = null;
    changed();

    seekTo(start - 1.5);
    play();
    showToast('Re-tap this line: J on each word.', false);
}

/* ==========================================================================
   Rendering
   ========================================================================== */

// Word chips by "l:w" -> { chip, time }, so updates and the playing-word
// highlight can reach them without searching the page.
let chipElements = new Map();
let rowTimeElements = [];   // per line: its time label
let endElements = [];       // per line: its "end" marker (null if it has no words)
let builtFor = null;        // the song (its lines array) the word view was built for
let playingKey = null;
let lastCursorLine = -1;

const keyOf = (pos) => `${pos.l}:${pos.w}`;
const samePos = (a, b) => Boolean(a && b && a.l === b.l && a.w === b.w);
const chipFor = (pos) => chipElements.get(keyOf(pos))?.chip;

/** Change an element's text only if it differs - an unchanged write still costs a redraw. */
function setText(element, text) {
    if (element.textContent !== text) element.textContent = text;
}

function render() {
    if (state.mode === 'words') {
        // The word view is BUILT once per song and only UPDATED after that.
        // Rebuilding ~400 chips on every tap was needless work at exactly the
        // moment timing matters most.
        if (builtFor !== state.lines) buildWords();
        else updateWords();
    } else {
        builtFor = null;   // the lines view replaces the chips
        renderLines();
    }
    renderProgress();
}

function renderLines() {
    const container = $('sync-lines');
    container.innerHTML = '';
    chipElements = new Map();

    state.lines.forEach((line, index) => {
        const row = document.createElement('div');
        row.className = 'sync-line';

        if (line.kind === 'section') row.classList.add('sync-line--section');
        if (index === state.lineCursor) row.classList.add('sync-line--current');
        if (line.time !== null) row.classList.add('sync-line--stamped');

        const time = document.createElement('span');
        time.className = 'sync-line__time';
        time.textContent = line.time !== null ? formatTimestamp(line.time) : '--:--.--';

        const text = document.createElement('span');
        text.className = 'sync-line__text';
        text.textContent = line.kind === 'section' ? `— ${line.text} —` : line.text;

        if (timing.isFullyTimed(line)) {
            const tag = document.createElement('span');
            tag.className = 'sync-line__tag';
            tag.textContent = 'words';
            tag.title = 'This line has word timings. Re-timing the line moves them too.';
            text.appendChild(tag);
        }

        const toCheck = line.words.filter((word) => word.review).length;
        if (toCheck > 0) {
            const tag = document.createElement('span');
            tag.className = 'sync-line__tag sync-line__tag--review';
            tag.textContent = `check ${toCheck}`;
            tag.title = 'Auto sync was unsure of some words in this line. Press N to go to them.';
            text.appendChild(tag);
        }

        row.append(time, text);

        // Clicking a line makes it the next one to stamp - handy for
        // restarting partway through a song.
        row.addEventListener('click', () => {
            if (line.kind === 'section') return;
            state.lineCursor = index;
            render();
            // Jump to just before this line if it has a time, to hear the run-up.
            if (line.time !== null) seekTo(line.time - 2);
        });

        container.appendChild(row);
    });

    // Keep the line being stamped in view without the user scrolling.
    container.querySelector('.sync-line--current')?.scrollIntoView({
        block: 'center',
        behavior: 'smooth',
    });
}

/** Create the word view: a row per line, a chip per word. */
function buildWords() {
    const container = $('sync-lines');
    container.innerHTML = '';
    chipElements = new Map();
    rowTimeElements = [];
    endElements = [];
    playingKey = null;
    lastCursorLine = -1;

    state.lines.forEach((line, l) => {
        const row = document.createElement('div');
        row.className = 'sync-line';

        const time = document.createElement('span');
        time.className = 'sync-line__time';
        rowTimeElements[l] = time;
        endElements[l] = null;

        const body = document.createElement('div');

        if (line.kind === 'section') {
            row.classList.add('sync-line--section');
            body.className = 'sync-line__text';
            body.textContent = `— ${line.text} —`;
        } else if (line.words.length === 0) {
            // A blank line in a timed file marks a break or the song's end.
            body.className = 'sync-line__text';
            body.textContent = '♪';
        } else {
            body.className = 'word-chips';

            line.words.forEach((word, w) => {
                const pos = { l, w };
                const chip = document.createElement('button');
                chip.type = 'button';

                const text = document.createElement('span');
                text.textContent = word.text;
                const stamp = document.createElement('span');
                stamp.className = 'word-chip__time';

                chip.append(text, stamp);
                chip.addEventListener('click', () => selectWord(pos));
                chipElements.set(keyOf(pos), { chip, time: stamp });
                body.appendChild(chip);
            });

            // Always present, hidden until the line has an end marked.
            const end = document.createElement('span');
            end.className = 'word-chip-end';
            end.hidden = true;
            body.appendChild(end);
            endElements[l] = end;
        }

        row.append(time, body);
        container.appendChild(row);
    });

    builtFor = state.lines;
    updateWords();   // fill in times and states - one place does that
}

/**
 * Bring the word view up to date, changing only what differs.
 *
 * A tap changes two or three chips; everything else is compared and left
 * alone, so the browser only redraws what actually changed.
 */
function updateWords() {
    state.lines.forEach((line, l) => {
        setText(rowTimeElements[l], line.time !== null ? formatTimestamp(line.time) : '--:--.--');

        const end = endElements[l];
        if (end) {
            const show = line.wordsEnd !== null;
            if (end.hidden === show) end.hidden = !show;
            if (show) setText(end, `end ${formatShort(line.wordsEnd)}`);
        }

        line.words.forEach((word, w) => {
            const pos = { l, w };
            const entry = chipElements.get(keyOf(pos));
            if (!entry) return;

            let className = 'word-chip';
            if (word.time !== null) className += ' word-chip--timed';
            if (samePos(pos, state.wordCursor)) className += ' word-chip--cursor';
            if (samePos(pos, state.selected)) className += ' word-chip--selected';
            if (keyOf(pos) === playingKey) className += ' word-chip--playing';
            if (word.review) className += ' word-chip--review';
            if (entry.chip.className !== className) entry.chip.className = className;

            const tip = timing.reviewReason(word.review);
            if (entry.chip.title !== tip) entry.chip.title = tip;

            setText(entry.time, word.time !== null ? formatShort(word.time) : '–');
        });
    });

    keepCursorInView();
}

/**
 * Keep the line being tapped in view, so the lines coming up can be read.
 *
 * Scrolls only when the cursor has moved to a new line AND drifted out of the
 * middle band of the list - and jumps there instantly. A smooth scroll
 * animates the whole list for a third of a second, which landed exactly when
 * the next word was due.
 */
function keepCursorInView() {
    const cursorLine = state.wordCursor?.l ?? -1;
    if (cursorLine === lastCursorLine) return;
    lastCursorLine = cursorLine;

    const chip = state.wordCursor && chipFor(state.wordCursor);
    if (!chip) return;

    const list = $('sync-lines');
    const box = list.getBoundingClientRect();
    const offset = chip.getBoundingClientRect().top - box.top;

    if (offset < box.height * 0.15 || offset > box.height * 0.65) {
        list.scrollTop += offset - box.height * 0.35;
    }
}

function renderProgress() {
    const p = timing.progress(state.lines);
    const toCheck = timing.reviewCount(state.lines);
    $('sync-progress').textContent = (state.mode === 'words'
        ? `${p.wordsTimed} / ${p.wordsTotal} words timed · ${p.linesWordTimed} lines complete`
        : `${p.linesTimed} / ${p.linesTotal} lines timed`)
        + (toCheck ? ` · ${toCheck} to check` : '');
    $('sync-save').disabled = p.linesTimed === 0;
}

/**
 * Light up the word being sung right now, every frame.
 *
 * This is the reviewing aid: play a line back and watch which chip lights at
 * each moment. A chip lighting early or late is the word to nudge.
 */
function highlightPlayingWord() {
    requestAnimationFrame(highlightPlayingWord);
    if (state.mode !== 'words' || $('sync-workspace').hidden) return;

    const key = playingWordKey(audio.currentTime);
    if (key === playingKey) return;

    chipElements.get(playingKey)?.chip.classList.remove('word-chip--playing');
    chipElements.get(key)?.chip.classList.add('word-chip--playing');
    playingKey = key;
}

/** The most recent word to start at or before `time` - if it began lately. */
function playingWordKey(time) {
    let best = null;
    let bestTime = -Infinity;

    state.lines.forEach((line, l) => {
        line.words.forEach((word, w) => {
            if (word.time !== null && word.time <= time && word.time > bestTime) {
                best = `${l}:${w}`;
                bestTime = word.time;
            }
        });
    });

    // Past a few seconds it is an instrumental gap, not that word.
    return best !== null && time - bestTime < 3 ? best : null;
}

/* ==========================================================================
   Live preview
   ========================================================================== */

let previewTimer = null;
let previewStale = false;

/**
 * Rebuild the preview after changes - but never while the song is playing.
 *
 * A rebuild redraws every letter of the song: the heaviest job this page
 * does. Done while tapping, it landed between taps and competed with them.
 * So during playback the preview is only marked out of date, and it catches
 * up the moment playback pauses. While paused, it follows each change
 * shortly after you stop pressing keys.
 */
function schedulePreview() {
    clearTimeout(previewTimer);
    if (!audio.paused) {
        if (!previewStale) {
            previewStale = true;
            // The preview would now be animating timing that is out of date -
            // misleading, and measured as most of this page's drawing work
            // while tapping. So stop it until playback pauses.
            preview.stop();
            $('sync-preview').parentElement.classList.add('sync__preview--stale');
        }
        return;
    }
    previewTimer = setTimeout(refreshPreview, 200);
}

/**
 * Draw the preview from exactly what Save would send, written out with the
 * same formatter the server uses - so the preview IS the saved result.
 */
function refreshPreview() {
    previewStale = false;
    $('sync-preview').parentElement.classList.remove('sync__preview--stale');
    const { lines } = timing.buildSavePayload(state.lines);

    if (lines.length === 0) {
        preview.showMessage('The preview appears here once something is timed.');
        return;
    }

    preview.loadLrc(formatLrc(lines));
    preview.attach(audio);
}

/* ==========================================================================
   Formatting
   ========================================================================== */

/** 67.4 -> "01:07.40" (line times). */
function formatTimestamp(seconds) {
    const minutes = Math.floor(seconds / 60);
    const remainder = seconds - minutes * 60;
    // toFixed(2) then padStart gives "01:07.40" rather than "1:7.4".
    return `${String(minutes).padStart(2, '0')}:${remainder.toFixed(2).padStart(5, '0')}`;
}

/** 67.4 -> "1:07.40" - shorter, for the word chips. */
function formatShort(seconds) {
    const minutes = Math.floor(seconds / 60);
    return `${minutes}:${(seconds - minutes * 60).toFixed(2).padStart(5, '0')}`;
}

/* ==========================================================================
   Saving
   ========================================================================== */

async function save() {
    // Refuse out-of-order words up front and point at the problem, rather
    // than letting the server reject the whole save with a line of text.
    const problem = timing.findOrderProblem(state.lines);
    if (problem) {
        state.selected = { l: problem.l, w: problem.w };
        state.wordCursor = state.selected;
        setMode('words');
        chipFor(state.selected)?.scrollIntoView({ block: 'center' });
        showToast(`Cannot save yet: ${problem.message} Fix it with ← / →, or re-tap it.`);
        return;
    }

    const { lines, partial } = timing.buildSavePayload(state.lines);
    if (lines.length === 0) {
        showToast('Nothing to save - time some lines first.');
        return;
    }

    const toCheck = timing.reviewCount(state.lines);
    if (autoSync.loaded && toCheck > 0 && !confirm(
        `${toCheck} highlighted word${toCheck > 1 ? 's have' : ' has'} not been changed. ` +
        'Save anyway, and mark the song as checked?'
    )) return;

    try {
        const result = await saveLrc(state.song.id, lines, {
            wordsIncluded: true,
            autosyncReviewed: autoSync.loaded,
        });
        state.dirty = false;

        // Checked and saved: the highlights have done their job.
        if (result.autosync_reviewed) {
            for (const line of state.lines) for (const word of line.words) word.review = null;
            autoSync.loaded = false;
            if (autoSync.result) Object.assign(autoSync.result, { reviewed: true, applied: true });
            Object.assign(state.song, result.song);
            render();
            renderAutoSync();
        }

        state.song.is_synced = true;
        $('sync-source').textContent = 'Timed by: Karaoke System sync editor';
        $('sync-fetch').textContent = 'Replace with LRCLIB timing';

        let message = `Saved ${result.line_count} lines`;
        if (result.word_timed_lines) message += `, ${result.word_timed_lines} with word timing`;
        message += '.';
        if (partial) {
            message += ` ${partial} half-tapped line${partial > 1 ? 's were' : ' was'} saved with` +
                ' line timing only - tap all its words to keep them.';
        }
        showToast(message, false);
    } catch (error) {
        showToast(error.message);
    }
}

/* ==========================================================================
   Keyboard
   ========================================================================== */

document.addEventListener('keydown', (event) => {
    // While the preview is open it has its own few keys, and nothing else runs.
    if (!$('preview').hidden) {
        previewKey(event);
        return;
    }

    // Only while tapping - not in the song picker or the paste box.
    if ($('sync-workspace').hidden) return;
    const tag = document.activeElement?.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;

    // Leave browser shortcuts alone: Ctrl+R must still reload the page, not
    // re-tap a line.
    if (event.ctrlKey || event.metaKey || event.altKey) return;

    // A clicked button keeps keyboard focus, and SPACE or ENTER would "press"
    // it again on top of the action below - clicking a word chip and then
    // pressing SPACE would re-select it. Dropping focus first prevents that.
    if (document.activeElement instanceof HTMLButtonElement) {
        document.activeElement.blur();
    }

    // Letters compared in lower case, so Caps Lock does not matter.
    const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
    const inWords = state.mode === 'words';
    const handled = () => event.preventDefault();

    switch (key) {
        case ' ':
        case 'Enter':
            handled();   // stops SPACE also scrolling the page
            togglePlay();
            break;

        case 'j':
            handled();
            // Holding the key down auto-repeats it; each stamp must be a
            // deliberate tap, not a burst.
            if (!event.repeat) (inWords ? stampWord : stampLine)(event);
            break;

        case 'Backspace':
            handled();
            undo();
            break;

        case 'ArrowLeft':
        case 'ArrowRight': {
            handled();
            const direction = key === 'ArrowLeft' ? -1 : 1;
            if (inWords) nudgeWord(direction * (event.shiftKey ? WORD_NUDGE_FINE : WORD_NUDGE));
            else nudgeLine(direction * LINE_NUDGE);
            break;
        }

        case 'ArrowUp':
        case 'ArrowDown':
            if (inWords) {
                handled();
                moveSelection(key === 'ArrowUp' ? -1 : 1);
            }
            break;

        case ',':
            handled();
            seekTo(audio.currentTime - SEEK_STEP);
            break;

        case '.':
            handled();
            seekTo(audio.currentTime + SEEK_STEP);
            break;

        case 'l':
            handled();
            replayLine();
            break;

        case 's':
            handled();
            cycleSpeed();
            break;

        case 'k':
            if (inWords) {
                handled();
                if (!event.repeat) markEnd(event);
            }
            break;

        case 'r':
            if (inWords) {
                handled();
                retapLine();
            }
            break;

        case 'n':
            handled();
            jumpToReview();
            break;
    }
});

/* ==========================================================================
   Transport and buttons
   ========================================================================== */

audio.addEventListener('timeupdate', () => {
    $('sync-time').textContent = formatTime(audio.currentTime);
    if (Number.isFinite(audio.duration)) {
        $('sync-duration').textContent = formatTime(audio.duration);
    }
});

audio.addEventListener('play', () => { $('sync-play').textContent = 'Pause'; });
audio.addEventListener('pause', () => {
    $('sync-play').textContent = 'Play';
    // Changes made while playing were held back from the preview; show them now.
    if (previewStale) refreshPreview();
});

$('sync-play').addEventListener('click', togglePlay);
$('sync-stamp').addEventListener('click', (event) => (state.mode === 'words' ? stampWord : stampLine)(event));
$('sync-undo').addEventListener('click', undo);
$('sync-speed').addEventListener('click', cycleSpeed);
$('sync-save').addEventListener('click', save);
$('mode-lines').addEventListener('click', () => setMode('lines'));
$('mode-words').addEventListener('click', () => setMode('words'));

$('sync-restart').addEventListener('click', () => {
    if (!confirm('Clear every timestamp - lines and words - and start over?')) return;

    for (const line of state.lines) {
        line.time = null;
        line.baseTime = null;
        line.wordsEnd = null;
        for (const word of line.words) word.time = null;
    }
    state.lineCursor = 0;
    state.wordCursor = timing.firstUntimedPosition(state.lines);
    state.selected = null;
    state.history = [];
    seekTo(0);
    skipSections();
    changed();
});

$('sync-back').addEventListener('click', () => {
    if (state.dirty && !confirm('You have unsaved timing. Discard it?')) return;
    audio.pause();
    stopWatchingAutoSync();
    showView('picker');
    state.dirty = false;
    loadSongList();
});

// Browsers only show a generic warning here, but any prompt is better than
// silently losing a song's worth of tapping.
window.addEventListener('beforeunload', (event) => {
    if (state.dirty) event.preventDefault();
});

/* ==========================================================================
   Preview
   ========================================================================== */

/**
 * Full-screen preview: the song with its word glow exactly as singers will
 * see it - the karaoke screen's renderer in its two-line "band" layout,
 * drawn from what Save would write. It plays from where the song is now;
 * "From the start" rewinds.
 */
function openPreview() {
    const { lines } = timing.buildSavePayload(state.lines);
    if (lines.length === 0) {
        showToast('Nothing is timed yet, so there is nothing to preview.');
        return;
    }
    preview.stop();   // the small preview strip rests meanwhile
    $('preview-title').textContent = `${state.song.title} — ${state.song.artist}`;
    $('preview').hidden = false;   // visible BEFORE loading: the band measures its lines
    bandPreview.loadLrc(formatLrc(lines));
    bandPreview.attach(audio);
    play();
}

function closePreview() {
    audio.pause();
    bandPreview.stop();
    $('preview').hidden = true;
    refreshPreview();
}

function previewKey(event) {
    const key = event.key;
    if (key === 'Escape') closePreview();
    else if (key === ' ' || key === 'Enter') togglePlay();
    else if (key === ',') seekTo(audio.currentTime - SEEK_STEP);
    else if (key === '.') seekTo(audio.currentTime + SEEK_STEP);
    else return;
    event.preventDefault();
}

$('sync-preview-open').addEventListener('click', openPreview);
$('preview-close').addEventListener('click', closePreview);
$('preview-play').addEventListener('click', togglePlay);
$('preview-start').addEventListener('click', () => { seekTo(0); play(); });

audio.addEventListener('timeupdate', () => {
    if (!$('preview').hidden) $('preview-time').textContent = formatTime(audio.currentTime);
});
audio.addEventListener('play', () => { $('preview-play').textContent = 'Pause'; });
audio.addEventListener('pause', () => { $('preview-play').textContent = 'Play'; });

/* ==========================================================================
   Add a song
   ========================================================================== */

// Words in brackets that describe the RECORDING, not the song - the same
// list the importer strips (backend/utils/importer.py).
const RECORDING_NOISE = /\s*[([]\s*(minus|minus one|instrumental|karaoke|backing track|off ?vocal|no vocals?|lyrics|audio|official.*?|hd|hq)\s*[)\]]/gi;

/** "Artist - Title (Official Audio).mp3" -> { artist, title } */
function guessFromFilename(filename) {
    const stem = filename.replace(/\.[^.]+$/, '').replace(RECORDING_NOISE, '').trim();
    const cut = stem.indexOf(' - ');
    return cut > 0
        ? { artist: stem.slice(0, cut).trim(), title: stem.slice(cut + 3).trim() }
        : { artist: '', title: stem };
}

/** Fill in artist and title from the chosen file, unless already typed. */
function fillFromFile(input) {
    const file = input.files[0];
    if (!file) return;
    const guess = guessFromFilename(file.name);
    if (!$('add-artist').value) $('add-artist').value = guess.artist;
    if (!$('add-title').value) $('add-title').value = guess.title;
}

$('add-original').addEventListener('change', () => fillFromFile($('add-original')));
$('add-minus').addEventListener('change', () => fillFromFile($('add-minus')));

function setAddStatus(text, { error = false, percent = null } = {}) {
    const status = $('add-status');
    status.textContent = text;
    status.classList.toggle('add-song__status--error', error);
    $('add-progress-wrap').hidden = percent === null;
    if (percent !== null) $('add-progress').style.width = `${percent}%`;
}

async function submitSong(event) {
    event.preventDefault();
    const original = $('add-original').files[0];
    const minus = $('add-minus').files[0];
    if (!original && !minus) {
        setAddStatus('Choose the original recording first.', { error: true });
        return;
    }

    const form = new FormData();
    if (original) form.append('original', original);
    if (minus) form.append('minus', minus);
    form.append('artist', $('add-artist').value);
    form.append('title', $('add-title').value);
    form.append('language', $('add-language').value);
    form.append('lyrics', $('add-lyrics').value);
    form.append('auto_sync', $('add-autosync').checked ? '1' : '0');

    $('add-submit').disabled = true;
    try {
        // The upload is the first 10% of the bar; the import is the rest.
        const answer = await uploadSong(form, (fraction) =>
            setAddStatus(`Uploading... ${Math.round(fraction * 100)}%`, { percent: fraction * 10 }));
        watchImport(answer.job_id, answer.title);
    } catch (error) {
        setAddStatus(error.message, { error: true });
        $('add-submit').disabled = false;
    }
}

/** Follow the import job on the server until it is done. */
function watchImport(jobId, title) {
    setAddStatus(`${title}: waiting for its turn...`, { percent: 10 });
    const timer = setInterval(async () => {
        let job;
        try {
            job = await getImportJob(jobId);
        } catch (error) {
            clearInterval(timer);
            setAddStatus(error.message, { error: true });
            $('add-submit').disabled = false;
            return;
        }

        if (job.status === 'done') {
            clearInterval(timer);
            $('add-song-form').reset();
            $('add-submit').disabled = false;
            const codes = job.songs.map((song) => song.code).join(', ');
            setAddStatus(`Added ${title} as song ${codes}. ${job.notes.join(' ')}`);
            loadSongList();
        } else if (job.status === 'failed') {
            clearInterval(timer);
            $('add-submit').disabled = false;
            setAddStatus(`Could not add ${title}: ${job.error}`, { error: true });
        } else {
            setAddStatus(`${title}: ${job.message} ${Math.round(job.percent ?? 0)}%`,
                         { percent: 10 + 0.9 * (job.percent ?? 0) });
        }
    }, 1500);
}

$('add-song-form').addEventListener('submit', submitSong);

/* ==========================================================================
   Toast
   ========================================================================== */

let toastTimer = null;

function showToast(message, isError = true) {
    const toast = $('toast');
    toast.textContent = message;
    toast.classList.toggle('toast--error', isError);
    toast.hidden = false;
    clearTimeout(toastTimer);
    // Longer messages stay up longer, so there is time to read them.
    toastTimer = setTimeout(() => { toast.hidden = true; }, Math.max(4000, message.length * 60));
}

/* ========================================================================== */

setMode('lines');
applySpeed();
loadSongList();
requestAnimationFrame(highlightPlayingWord);
