/**
 * leaderboard.js - tonight's top scores.
 *
 * Shown in the "Scores" tab of the host drawer. Purely presentation: it is
 * handed the rows from GET /api/leaderboard and draws them.
 */

export class Leaderboard {
    constructor(container) {
        this.container = container;
    }

    update(entries) {
        this.container.innerHTML = '';

        if (!entries || entries.length === 0) {
            this.container.innerHTML =
                '<div class="empty-note">No scores yet tonight. Sing something!</div>';
            return;
        }

        entries.forEach((entry, index) => {
            const rank = index + 1;

            const row = document.createElement('li');
            // The rank class only exists for 1-3, which is what gives the top
            // three their medal colours.
            row.className = `leader-row${rank <= 3 ? ` leader-row--${rank}` : ''}`;

            const rankCell = document.createElement('span');
            rankCell.className = 'leader-row__rank';
            rankCell.textContent = String(rank);

            const details = document.createElement('div');
            const name = document.createElement('div');
            name.className = 'leader-row__name';
            name.textContent = entry.singer_name;

            const song = document.createElement('div');
            song.className = 'leader-row__song';
            song.textContent = entry.song_title;

            details.append(name, song);

            const score = document.createElement('span');
            score.className = 'leader-row__score';
            score.textContent = String(entry.final_score);

            row.append(rankCell, details, score);
            this.container.appendChild(row);
        });
    }
}
