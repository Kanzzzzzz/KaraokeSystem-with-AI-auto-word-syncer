/**
 * api.js - every call to the Flask backend goes through here.
 *
 * Keeping the fetch() calls in one file means error handling is written once
 * instead of in every feature module, and if a URL ever changes there is a
 * single place to update.
 */

/**
 * Wrapper around fetch() that parses JSON and turns HTTP errors into real
 * thrown Errors.
 *
 * By default a failed fetch() only rejects on network failure - a 404 or 500
 * still "succeeds", which is a classic source of confusing bugs. This checks
 * response.ok so callers can rely on try/catch.
 */
async function request(url, options = {}) {
    let response;

    try {
        response = await fetch(url, {
            headers: { 'Content-Type': 'application/json' },
            ...options,
        });
    } catch (networkError) {
        throw new Error('Cannot reach the karaoke server. Is it still running?');
    }

    // 204 No Content has an empty body, so parsing it as JSON would throw.
    if (response.status === 204) return null;

    let data;
    try {
        data = await response.json();
    } catch {
        data = null;
    }

    if (!response.ok) {
        throw new Error(data?.error || `Request failed (${response.status})`);
    }

    return data;
}

const postJson = (url, body) =>
    request(url, { method: 'POST', body: JSON.stringify(body ?? {}) });

/* --- Songs ------------------------------------------------------------- */

export const searchSongs = (query) =>
    request(`/api/songs?q=${encodeURIComponent(query)}`);

export const getSong = (songId) => request(`/api/songs/${songId}`);

export const getSongByCode = (code) =>
    request(`/api/songs/code/${encodeURIComponent(code)}`);

/** Edit a song: pass only the fields to change (title, artist, language, code). */
export const updateSong = (songId, fields) =>
    request(`/api/songs/${songId}`, { method: 'PATCH', body: JSON.stringify(fields) });

/** Remove a song; `deleteFiles` also deletes its playback track and lyrics. */
export const deleteSong = (songId, deleteFiles = false) =>
    request(`/api/songs/${songId}${deleteFiles ? '?files=1' : ''}`, { method: 'DELETE' });

export const getSongLyrics =(songId) => request(`/api/songs/${songId}/lyrics`);

/* --- Queue -------------------------------------------------------------- */

export const getQueue = () => request('/api/queue');

export const addToQueue = (payload) => postJson('/api/queue', payload);

export const removeFromQueue = (entryId) =>
    request(`/api/queue/${entryId}`, { method: 'DELETE' });

export const reorderQueue = (order) =>
    request('/api/queue/reorder', { method: 'PATCH', body: JSON.stringify({ order }) });

/** Finish the current song and start the next. `skipped` records why. */
export const advanceQueue = (skipped = false) =>
    postJson('/api/queue/advance', { skipped });

export const clearQueue = () => postJson('/api/queue/clear');

/* --- Scores ------------------------------------------------------------- */

export const saveScore = (payload) => postJson('/api/scores', payload);

export const getLeaderboard = () => request('/api/leaderboard');

/* --- Lyrics sync -------------------------------------------------------- */

/**
 * Save timed lines. `wordsIncluded` tells the server the lines carry every
 * word timing the sender wants kept, so none are carried over from the old
 * file - see save_lrc() in backend/routes/lyrics.py.
 */
export const saveLrc = (songId, lines, options = {}) =>
    postJson(`/api/lyrics/${songId}/lrc`, {
        lines,
        words_included: Boolean(options.wordsIncluded),
        // The editor had an auto-sync result loaded and it has been checked.
        autosync_reviewed: Boolean(options.autosyncReviewed),
    });

/**
 * Look a song up on LRCLIB. `force` replaces timing the song already has;
 * without it, a synced song is left alone.
 */
export const fetchFromLrclib = (songId, force = false) =>
    postJson(`/api/lyrics/${songId}/fetch`, { force });

/** Save lyrics pasted by hand as the song's plain lyric sheet. */
export const saveLyricsText = (songId, text) =>
    postJson(`/api/lyrics/${songId}/text`, { text });

/* --- Automatic word sync ------------------------------------------------ */

/** Queue auto sync for a song. The work happens in the background. */
export const startAutoSync = (songId, isolateVocals = true) =>
    postJson(`/api/songs/${songId}/auto-sync`, { isolate_vocals: isolateVocals });

/** { sync_status, sync_message, sync_locked, progress, result_available } */
export const getAutoSyncState = (songId) => request(`/api/songs/${songId}/auto-sync`);

/** The full result: every word's time, confidence and flags. */
export const getAutoSyncResult = (songId) => request(`/api/songs/${songId}/auto-sync/result`);

/* --- Adding songs --------------------------------------------------------- */

/**
 * Upload a song (a FormData with the files and fields - see
 * backend/routes/library.py). Uses XMLHttpRequest rather than fetch() for
 * one reason: fetch() cannot report upload progress, and a song file takes
 * a few seconds to send.
 *
 * @param {FormData} form
 * @param {(fraction: number) => void} onProgress - 0..1 as the file uploads
 * @returns {Promise<{job_id: number, title: string}>}
 */
export function uploadSong(form, onProgress = () => {}) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/library/upload');
        xhr.upload.addEventListener('progress', (event) => {
            if (event.lengthComputable) onProgress(event.loaded / event.total);
        });
        xhr.addEventListener('load', () => {
            let data = null;
            try { data = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
            if (xhr.status >= 200 && xhr.status < 300) resolve(data);
            else reject(new Error(data?.error || `Upload failed (${xhr.status})`));
        });
        xhr.addEventListener('error', () =>
            reject(new Error('Cannot reach the karaoke server. Is it still running?')));
        xhr.send(form);
    });
}

/** How an import job is going: { status, percent, stage, message, songs, notes, error }. */
export const getImportJob = (jobId) => request(`/api/library/jobs/${jobId}`);

/* --- Join info ---------------------------------------------------------- */

export const getJoinInfo = () => request('/api/join-info');
