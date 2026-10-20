/**
 * upNext.js - the waiting queue, in one line under the top strip.
 *
 *     UP NEXT  (1) 1002 Ana   (2) 1005 Ben   (3) 1001 Cy ...
 *
 * Each waiting song shows its place in line, its song code and the singer's
 * name - enough for everyone in the room to see when their turn is coming
 * without opening anything. The song playing right now is left out: it is
 * already in the strip above. The full list, with the host's reorder and
 * remove controls, stays in the drawer's Queue tab.
 *
 * It is redrawn from each queue update the server sends, like the drawer's
 * queue list. Songs that were not there last time get a short slide-in, so a
 * newly added song catches the eye.
 */

export class UpNext {
    /** @param {HTMLElement} list - the <ol> to fill */
    constructor(list) {
        this.list = list;
        this.shownIds = null;   // ids on screen last time; null before the first draw
    }

    /** @param {Array} entries - the queue from the server, in order */
    update(entries) {
        const waiting = entries.filter((entry) => entry.status === 'waiting');
        const previous = this.shownIds;
        this.shownIds = new Set(waiting.map((entry) => entry.id));

        this.list.replaceChildren();

        if (waiting.length === 0) {
            const empty = document.createElement('li');
            empty.className = 'upnext__empty';
            empty.textContent = 'Nothing queued yet - scan the code to add a song';
            this.list.appendChild(empty);
            return;
        }

        waiting.forEach((entry, index) => {
            const item = document.createElement('li');
            item.className = 'upnext__item';
            // Only animate songs that are new since the last draw - and none
            // on the very first draw, or the whole row would slide in at once.
            if (previous && !previous.has(entry.id)) item.classList.add('upnext__item--new');
            // The full title, for anyone who hovers over it.
            item.title = `${entry.song_title} — ${entry.song_artist}`;

            const position = document.createElement('span');
            position.className = 'upnext__pos';
            position.textContent = String(index + 1);

            const code = document.createElement('span');
            code.className = 'upnext__code';
            code.textContent = entry.song_code;

            // textContent, never innerHTML: names are typed by guests.
            const name = document.createElement('span');
            name.className = 'upnext__name';
            name.textContent = entry.singer_name;

            item.append(position, code, name);
            this.list.appendChild(item);
        });
    }
}
