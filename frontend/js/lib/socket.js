/**
 * socket.js - a thin wrapper around Socket.IO.
 *
 * Two jobs:
 *   1. hide the connection details from the feature modules
 *   2. fall back to polling if the Socket.IO library failed to load
 *
 * The fallback matters because the Socket.IO client is loaded from a CDN. If
 * the classroom has no internet, that script will not load - and the queue
 * should still work, just with a small delay instead of instant updates.
 */

const POLL_INTERVAL_MS = 2000;

export class QueueSocket {
    constructor() {
        this.socket = null;
        this.handlers = new Map();
        this.pollTimer = null;
        this.onStatusChange = null;
    }

    /** Register a handler for an event name. */
    on(event, handler) {
        this.handlers.set(event, handler);

        // If we connected before this handler was registered, attach it now.
        if (this.socket) this.socket.on(event, handler);
    }

    emitLocal(event, payload) {
        this.handlers.get(event)?.(payload);
    }

    /**
     * Connect, or start polling if Socket.IO is unavailable.
     * @param {Function} pollFn - called on each poll tick in fallback mode
     */
    connect(pollFn) {
        if (typeof window.io === 'undefined') {
            console.warn('Socket.IO did not load - falling back to polling.');
            this.startPolling(pollFn);
            return;
        }

        this.socket = window.io({
            // Deliberately NOT setting `transports` here.
            //
            // Socket.IO's default is to open with HTTP long-polling and then
            // upgrade to a WebSocket once the connection is established. That
            // upgrade path is what the development server supports.
            //
            // Forcing `transports: ['websocket', 'polling']` instead makes the
            // client open a WebSocket directly, with no handshake first, and
            // Werkzeug's development server answers that with HTTP 500. The
            // app still works, because the client then retries with polling -
            // but every page load logs 500 errors from /socket.io/, which is
            // alarming and looks like a broken server.
            //
            // Leaving the default alone avoids the 500s and still ends up on
            // a WebSocket after the upgrade.
            reconnectionDelay: 1000,
            reconnectionDelayMax: 5000,
        });

        for (const [event, handler] of this.handlers) {
            this.socket.on(event, handler);
        }

        this.socket.on('connect', () => this.onStatusChange?.(true));
        this.socket.on('disconnect', () => this.onStatusChange?.(false));

        this.socket.on('connect_error', () => {
            this.onStatusChange?.(false);
            // Repeated failures mean the server is unreachable over sockets,
            // so poll instead of leaving the queue frozen.
            this.startPolling(pollFn);
        });
    }

    startPolling(pollFn) {
        if (this.pollTimer || typeof pollFn !== 'function') return;
        this.pollTimer = setInterval(pollFn, POLL_INTERVAL_MS);
        pollFn();   // fetch immediately rather than waiting for the first tick
    }

    stopPolling() {
        if (this.pollTimer) {
            clearInterval(this.pollTimer);
            this.pollTimer = null;
        }
    }

    disconnect() {
        this.stopPolling();
        this.socket?.disconnect();
        this.socket = null;
    }
}
