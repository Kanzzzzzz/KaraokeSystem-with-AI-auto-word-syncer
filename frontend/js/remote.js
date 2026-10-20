/**
 * remote.js - the phone view.
 *
 * A deliberately small page: search for a song, add yourself to the queue,
 * and watch the queue update. No audio, no lyrics, no microphone - all of
 * that stays on the host screen.
 *
 * It reuses SearchPanel and QueueClient unchanged. QueueClient is created
 * with isHost: false, which is what hides the reorder and remove controls -
 * the same component, told it is not the host.
 */

import * as api from './lib/api.js';
import { QueueSocket } from './lib/socket.js';
import { QueueClient } from './queue/queueClient.js';
import { SearchPanel } from './search/searchPanel.js';

const $ = (id) => document.getElementById(id);

// Key for remembering the singer's name in this browser.
const NAME_STORAGE_KEY = 'karaoke-singer-name';

/* --- Name, remembered between visits ----------------------------------- */

const nameInput = $('remote-name');

// localStorage can throw in private browsing mode, so both access points are
// wrapped. The page still works fine without it - the name just is not kept.
try {
    nameInput.value = localStorage.getItem(NAME_STORAGE_KEY) ?? '';
} catch {
    /* storage unavailable - not a problem */
}

nameInput.addEventListener('change', () => {
    try {
        localStorage.setItem(NAME_STORAGE_KEY, nameInput.value.trim());
    } catch {
        /* storage unavailable - not a problem */
    }
});

/* --- Toast -------------------------------------------------------------- */

let toastTimer = null;

function showToast(message, isError = true) {
    const toast = $('toast');
    toast.textContent = message;
    toast.classList.toggle('toast--error', isError);
    toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toast.hidden = true; }, 3500);
}

/* --- Queue -------------------------------------------------------------- */

const queue = new QueueClient($('queue-list'), {
    isHost: false,
    onError: showToast,
});

/* --- Search ------------------------------------------------------------- */

new SearchPanel(
    {
        input: $('search-input'),
        keypad: $('keypad'),
        display: $('code-display'),
        results: $('search-results'),
    },
    async (song) => {
        const name = nameInput.value.trim();

        if (!name) {
            showToast('Enter your name first.');
            nameInput.focus();
            // scrollIntoView because on a phone the name box is usually
            // scrolled off the top by the time you pick a song.
            nameInput.scrollIntoView({ behavior: 'smooth', block: 'center' });
            return;
        }

        try {
            await api.addToQueue({ song_id: song.id, singer_name: name });
            showToast(`"${song.title}" added for ${name}`, false);
        } catch (error) {
            showToast(error.message);
        }
    }
);

/* --- Live updates ------------------------------------------------------- */

const socket = new QueueSocket();

socket.on('queue:updated', (payload) => queue.update(payload));

socket.onStatusChange = (online) => {
    const element = $('connection-status');
    element.textContent = online ? 'Live' : 'Offline';
    element.className = `status-dot status-dot--${online ? 'online' : 'offline'}`;
};

async function refreshQueue() {
    try {
        queue.update(await api.getQueue());
    } catch {
        /* the status indicator already shows the connection state */
    }
}

socket.connect(refreshQueue);
refreshQueue();
