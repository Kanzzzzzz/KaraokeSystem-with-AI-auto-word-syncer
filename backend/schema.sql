-- ===========================================================================
-- Web-Based Karaoke System - database schema
--
-- Four tables:
--   songs         - the library you can search by number code or title
--   sessions      - one karaoke sitting; scopes the queue and the leaderboard
--   queue_entries - who is singing what, and in what order
--   scores        - the result of one finished performance
--
-- "IF NOT EXISTS" everywhere means this file is safe to run against an
-- existing database - it will only create what is missing.
-- ===========================================================================

-- Make SQLite actually enforce the REFERENCES clauses below. SQLite has
-- foreign keys switched OFF by default for backwards compatibility, so
-- database.py turns this on for every connection too.
PRAGMA foreign_keys = ON;


-- ---------------------------------------------------------------------------
-- SONGS: one row per track in the library.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS songs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,

    -- The number you punch in on a real karaoke machine, e.g. "1024".
    -- Stored as TEXT (not INTEGER) so leading zeros survive and so we can
    -- do prefix matching like code LIKE '10%' while the user is still typing.
    code             TEXT    NOT NULL UNIQUE,

    title            TEXT    NOT NULL,
    artist           TEXT    NOT NULL,

    -- Paths are stored RELATIVE to the data/ folder (e.g.
    -- "library/audio/song.mp3"). Relative paths mean the database still works
    -- if the project folder is moved or opened on another computer.
    audio_path       TEXT    NOT NULL,

    -- The timestamped .lrc file. NULL until someone runs the sync editor.
    lyrics_path      TEXT,

    -- The original plain-text lyrics. The sync editor reads this to get the
    -- lines it will attach timestamps to.
    source_text_path TEXT,

    duration_sec     REAL,
    language         TEXT,

    -- 1 once a valid .lrc exists. The UI uses this to send the user to the
    -- sync editor instead of starting a song with no lyric timing.
    is_synced        INTEGER NOT NULL DEFAULT 0,

    date_added       TEXT    NOT NULL DEFAULT (datetime('now')),

    -- Automatic word sync (backend/utils/autosync.py). One of:
    --   'none' | 'queued' | 'processing' | 'done' | 'needs_review' | 'failed'
    sync_status      TEXT    NOT NULL DEFAULT 'none',

    -- A human-readable note about the last auto-sync run: why it failed, or
    -- what needs reviewing.
    sync_message     TEXT,

    -- 1 = auto sync must never touch this song: no queueing, no runs, no
    -- drafts, no file writes. For songs whose timing was carefully tapped by
    -- hand. Set it in data/song_overrides.json ("sync_locked": true) so it
    -- survives a database reset.
    sync_locked      INTEGER NOT NULL DEFAULT 0
);

-- Indexes for the two ways users search. Without these, every keystroke
-- would scan the whole table.
CREATE INDEX IF NOT EXISTS idx_songs_code   ON songs(code);
CREATE INDEX IF NOT EXISTS idx_songs_title  ON songs(title);
CREATE INDEX IF NOT EXISTS idx_songs_artist ON songs(artist);


-- ---------------------------------------------------------------------------
-- SESSIONS: one karaoke sitting. Restarting the app starts a new session,
-- which is what gives us a per-night leaderboard instead of an all-time one.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    label      TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at   TEXT
);


-- ---------------------------------------------------------------------------
-- QUEUE_ENTRIES: the shared singing queue.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS queue_entries (
    id          INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,

    -- ON DELETE CASCADE: clearing a session removes its queue automatically,
    -- so we never leave orphaned rows pointing at a session that is gone.
    session_id  INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    song_id     INTEGER NOT NULL REFERENCES songs(id),

    singer_name TEXT    NOT NULL,

    -- Sort order. The host's drag-to-reorder rewrites these numbers.
    position    INTEGER NOT NULL,

    -- 'waiting' | 'playing' | 'done' | 'skipped'
    -- Finished entries stay in the table rather than being deleted, so the
    -- session history and the recap screen still have something to show.
    status      TEXT    NOT NULL DEFAULT 'waiting',

    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- The queue is always read as "this session, in position order", so a single
-- index covering both columns answers that query directly.
CREATE INDEX IF NOT EXISTS idx_queue_session ON queue_entries(session_id, position);


-- ---------------------------------------------------------------------------
-- SCORES: one row per completed performance.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    queue_entry_id  INTEGER REFERENCES queue_entries(id),
    song_id         INTEGER NOT NULL REFERENCES songs(id),
    singer_name     TEXT    NOT NULL,

    final_score     INTEGER NOT NULL,   -- 0-100, what the singer actually sees

    -- The components behind final_score, kept separately so the recap screen
    -- can explain WHY a score was what it was.
    timing_score    REAL,               -- sang during lines / stayed quiet in gaps
    energy_score    REAL,               -- volume consistency

    -- Reserved for the future pitch-matching upgrade (see js/scoring/features/
    -- pitch.js). The column exists now so adding pitch scoring later needs no
    -- database migration - it just starts writing a value instead of NULL.
    pitch_score     REAL,

    -- A JSON array of energy samples over time, used to draw the recap graph.
    -- SQLite has no array type, and this value is only ever read back whole
    -- (never filtered or sorted on), so storing it as JSON text is fine.
    energy_timeline TEXT,

    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The leaderboard query is "top scores for this session", so index exactly
-- that, descending, letting SQLite read rows already in ranking order.
CREATE INDEX IF NOT EXISTS idx_scores_leaderboard ON scores(session_id, final_score DESC);
