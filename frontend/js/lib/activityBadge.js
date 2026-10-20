/**
 * A small "still working" badge for the background jobs (making the minus-one
 * with Demucs, importing, auto sync). The jobs run on the server, so the badge
 * asks the server what is going on and shows it on any page, whether or not
 * that page started the job.
 *
 * It sits inside an element the page already has, so it never covers the
 * lyrics, and it stays hidden while nothing is running.
 */

const POLL_MS = 3000;

export function mountActivityBadge(host, { before = null } = {}) {
    const badge = document.createElement('span');
    badge.className = 'activity-badge';
    badge.hidden = true;
    badge.setAttribute('role', 'status');
    badge.innerHTML = '<span class="activity-badge__dot"></span><span class="activity-badge__text"></span>';
    host.insertBefore(badge, before);
    const text = badge.querySelector('.activity-badge__text');

    async function tick() {
        try {
            const response = await fetch('/api/library/activity');
            const state = await response.json();
            if (!state.busy) {
                badge.hidden = true;
                return;
            }
            const [first] = state.jobs;
            const what = first.kind === 'import' ? 'Making karaoke track' : 'Syncing lyrics';
            const percent = first.queued ? '' : ` ${Math.round(first.percent ?? 0)}%`;
            const more = state.jobs.length > 1 ? ` (+${state.jobs.length - 1} more)` : '';
            text.textContent = `${what}: ${first.title}${percent}${more}`;
            badge.title = first.message || '';
            badge.hidden = false;
        } catch {
            // Server unreachable: leave the badge as it was and try again.
        }
    }

    tick();
    setInterval(tick, POLL_MS);
}
