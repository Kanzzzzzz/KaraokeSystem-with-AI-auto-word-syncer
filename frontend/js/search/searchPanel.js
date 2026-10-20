/**
 * searchPanel.js - the number keypad and search results.
 *
 * Searching works the same way a real karaoke remote does: punch in a code
 * and the matching songs narrow down as you type. Typing letters instead
 * searches titles and artists. The backend decides which kind of search a
 * query is (see models/song.py), so this module just sends whatever was
 * typed.
 */

import { searchSongs } from '../lib/api.js';
import { formatTime } from '../player/audioPlayer.js';

// Wait this long after the last keystroke before searching. Without it, a
// four-digit code would fire four separate requests, and the answers could
// arrive out of order.
const DEBOUNCE_MS = 220;

export class SearchPanel {
    /**
     * @param {object} elements - { input, keypad, display, results }
     * @param {Function} onSelect - called with the chosen song
     */
    constructor(elements, onSelect) {
        this.input = elements.input;
        this.keypad = elements.keypad;
        this.display = elements.display;
        this.results = elements.results;
        this.onSelect = onSelect;

        this.codeBuffer = '';
        this.debounceTimer = null;

        // Incremented on every search so late replies from earlier searches
        // can be recognised and thrown away.
        this.requestId = 0;

        this.bindEvents();
        this.search('');
    }

    bindEvents() {
        this.input.addEventListener('input', () => {
            // Typing in the text box clears whatever is on the LED display -
            // the two are alternative ways to search, not a combined one.
            this.codeBuffer = '';
            this.updateDisplay();
            this.debouncedSearch(this.input.value);
        });

        this.keypad.addEventListener('click', (event) => {
            const key = event.target.closest('[data-key]');
            if (key) this.pressKey(key.dataset.key);
        });

        // Physical number keys work too, as long as the user is not typing
        // in a text field at the time.
        document.addEventListener('keydown', (event) => {
            const tag = document.activeElement?.tagName;
            if (tag === 'INPUT' || tag === 'TEXTAREA') return;

            if (/^[0-9]$/.test(event.key)) {
                this.pressKey(event.key);
            } else if (event.key === 'Backspace') {
                this.pressKey('del');
            } else if (event.key === 'Enter' && this.codeBuffer) {
                this.pressKey('go');
            }
        });
    }

    pressKey(key) {
        if (key === 'clear') {
            this.codeBuffer = '';
        } else if (key === 'del') {
            this.codeBuffer = this.codeBuffer.slice(0, -1);
        } else if (key === 'go') {
            // "Go" picks the first result, so a full code plays straight away.
            this.firstResult?.click();
            return;
        } else {
            // Cap at 6 digits - longer than any code the app generates.
            if (this.codeBuffer.length >= 6) return;
            this.codeBuffer += key;
        }

        this.input.value = '';
        this.updateDisplay();
        this.debouncedSearch(this.codeBuffer);
    }

    updateDisplay() {
        const padded = this.codeBuffer.padEnd(4, '-');
        this.display.textContent = padded;
        this.display.classList.toggle('led-display__value--empty', this.codeBuffer === '');
    }

    debouncedSearch(query) {
        clearTimeout(this.debounceTimer);
        this.debounceTimer = setTimeout(() => this.search(query), DEBOUNCE_MS);
    }

    async search(query) {
        const id = ++this.requestId;

        try {
            const data = await searchSongs(query);

            // A slower earlier request may finish after a later one. Without
            // this check it would overwrite the newer, correct results.
            if (id !== this.requestId) return;

            this.renderResults(data.songs);
        } catch (error) {
            if (id !== this.requestId) return;
            this.results.innerHTML = `<div class="empty-note">${error.message}</div>`;
        }
    }

    renderResults(songs) {
        this.results.innerHTML = '';
        this.firstResult = null;

        if (songs.length === 0) {
            this.results.innerHTML = '<div class="empty-note">No songs matched.</div>';
            return;
        }

        for (const song of songs) {
            const row = document.createElement('button');
            row.type = 'button';
            row.className = 'song-row';

            // textContent (rather than innerHTML) for anything derived from
            // data: it writes text, never markup, so a song title containing
            // < or & cannot break the page.
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

            if (!song.is_synced) {
                const badge = document.createElement('span');
                badge.className = 'badge-unsynced';
                badge.textContent = 'needs manual sync';
                badge.title =
                    'LRCLIB had no timing for this song. Time it in the sync editor.';
                meta.appendChild(badge);
            }

            if (song.duration_sec) {
                const time = document.createElement('span');
                time.textContent = formatTime(song.duration_sec);
                meta.appendChild(time);
            }

            row.append(code, details, meta);
            row.addEventListener('click', () => this.onSelect(song));

            this.results.appendChild(row);
            if (!this.firstResult) this.firstResult = row;
        }
    }
}
