/**
 * queueClient.js - renders the singing queue and the host's controls.
 *
 * The queue is shared state: the host screen and every phone are looking at
 * the same list. Rather than each client tracking changes itself, the server
 * sends the whole queue on every change (see sockets/queue_events.py) and
 * this module redraws from it. Re-rendering a list of a dozen rows is cheap,
 * and it removes a whole category of "my copy disagrees with yours" bugs.
 */

import { removeFromQueue, reorderQueue } from '../lib/api.js';

export class QueueClient {
    /**
     * @param {HTMLElement} container
     * @param {object} options - { isHost, onError }
     */
    constructor(container, options = {}) {
        this.container = container;
        this.isHost = options.isHost ?? false;
        this.onError = options.onError ?? (() => {});

        this.entries = [];
        this.nowPlaying = null;
        this.draggedId = null;
    }

    /** Redraw from a server payload. */
    update({ queue, now_playing }) {
        this.entries = queue ?? [];
        this.nowPlaying = now_playing ?? null;
        this.render();
    }

    render() {
        this.container.innerHTML = '';

        if (this.entries.length === 0) {
            this.container.innerHTML =
                '<div class="empty-note">The queue is empty. Search for a song to add one.</div>';
            return;
        }

        this.entries.forEach((entry, index) => {
            this.container.appendChild(this.buildRow(entry, index));
        });
    }

    buildRow(entry, index) {
        const isPlaying = entry.status === 'playing';

        const row = document.createElement('li');
        row.className = 'queue-item' + (isPlaying ? ' queue-item--playing' : '');
        row.dataset.entryId = String(entry.id);

        const position = document.createElement('span');
        position.className = 'queue-item__position';
        // A music note is clearer than a number for the song on screen now.
        position.textContent = isPlaying ? '♪' : String(index + 1);

        const details = document.createElement('div');

        const singer = document.createElement('div');
        singer.className = 'queue-item__singer';
        singer.textContent = entry.singer_name;

        const song = document.createElement('div');
        song.className = 'queue-item__song';
        song.textContent = `[${entry.song_code}] ${entry.song_title} — ${entry.song_artist}`;

        details.append(singer, song);
        row.append(position, details);

        if (this.isHost) {
            row.append(this.buildHostControls(entry, index, isPlaying));
            this.makeDraggable(row, entry, isPlaying);
        }

        return row;
    }

    buildHostControls(entry, index, isPlaying) {
        const controls = document.createElement('div');
        controls.className = 'queue-item__controls';

        const moveUp = this.buildIconButton('↑', 'Move up');
        // The first row cannot move up; nor can anything move above the song
        // currently playing.
        moveUp.disabled = index === 0 || isPlaying;
        moveUp.addEventListener('click', () => this.move(index, index - 1));

        const moveDown = this.buildIconButton('↓', 'Move down');
        moveDown.disabled = index >= this.entries.length - 1 || isPlaying;
        moveDown.addEventListener('click', () => this.move(index, index + 1));

        const remove = this.buildIconButton('✕', 'Remove from queue');
        remove.classList.add('icon-btn--danger');
        // The playing song is removed by skipping it, not deleting it, so
        // that the performance still gets recorded in the history.
        remove.disabled = isPlaying;
        remove.addEventListener('click', () => this.remove(entry.id));

        controls.append(moveUp, moveDown, remove);
        return controls;
    }

    buildIconButton(symbol, label) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'icon-btn';
        button.textContent = symbol;
        // Both attributes: title for a mouse tooltip, aria-label so screen
        // readers announce something more useful than the symbol.
        button.title = label;
        button.setAttribute('aria-label', label);
        return button;
    }

    /** Drag-and-drop reordering, as an alternative to the arrow buttons. */
    makeDraggable(row, entry, isPlaying) {
        if (isPlaying) return;

        row.draggable = true;
        row.classList.add('queue-item__handle');

        row.addEventListener('dragstart', (event) => {
            this.draggedId = entry.id;
            row.classList.add('queue-item--dragging');
            // Firefox will not start a drag unless some data is set.
            event.dataTransfer.setData('text/plain', String(entry.id));
            event.dataTransfer.effectAllowed = 'move';
        });

        row.addEventListener('dragend', () => {
            row.classList.remove('queue-item--dragging');
            this.draggedId = null;
        });

        row.addEventListener('dragover', (event) => {
            // The default action is "reject the drop", so this must be
            // prevented for the element to be a valid drop target.
            event.preventDefault();
            row.classList.add('queue-item--drop-target');
        });

        row.addEventListener('dragleave', () => {
            row.classList.remove('queue-item--drop-target');
        });

        row.addEventListener('drop', (event) => {
            event.preventDefault();
            row.classList.remove('queue-item--drop-target');

            if (this.draggedId === null || this.draggedId === entry.id) return;

            const from = this.entries.findIndex((e) => e.id === this.draggedId);
            const to = this.entries.findIndex((e) => e.id === entry.id);
            if (from >= 0 && to >= 0) this.move(from, to);
        });
    }

    /** Move an entry and send the new order to the server. */
    async move(from, to) {
        if (to < 0 || to >= this.entries.length) return;

        const reordered = [...this.entries];
        const [moved] = reordered.splice(from, 1);
        reordered.splice(to, 0, moved);

        // Redraw immediately rather than waiting for the server, so dragging
        // feels instant. The broadcast that follows confirms it.
        this.entries = reordered;
        this.render();

        try {
            await reorderQueue(reordered.map((entry) => entry.id));
        } catch (error) {
            this.onError(error.message);
        }
    }

    async remove(entryId) {
        try {
            await removeFromQueue(entryId);
        } catch (error) {
            this.onError(error.message);
        }
    }
}
