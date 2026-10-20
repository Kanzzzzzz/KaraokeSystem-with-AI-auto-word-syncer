# Web-Based Karaoke System

A browser-based karaoke machine built with Flask and vanilla JavaScript. Search
songs by number code like a real karaoke box, queue them up, sing along to
lyrics that glow in time with the music, and get scored on your performance.

> **Educational project — non-commercial.** This was built as a school
> assignment. It is not deployed publicly, sells nothing, and ships no
> copyrighted audio. See [Legal and audio files](#legal-and-audio-files).

---

## Features

- **A Pinoy videoke screen** — a looping mood video behind everything, the
  lyrics in a lower-third band (the current line and the next), a big
  **3-2-1 countdown** before the singing starts, and glowing gold, magenta and
  cyan on deep navy and maroon.
- **Queue from your phone** — a QR code stays on screen at all times. Guests
  scan it and add songs from their own phones, and a small toast announces
  each one ("🎤 Ana added Sunflower") without interrupting whoever is singing.
- **Number-code search** — every song gets a code like `1001`. Type it on the
  keypad (on a phone, or in the host's drawer), or search by title and artist.
- **Shared queue** — the queue updates live on every connected screen and
  phone, and advances automatically when a song ends.
- **Synced lyrics from LRCLIB** — when a song is imported, its timed lyrics
  are downloaded from [LRCLIB](https://lrclib.net), matched by title, artist
  and song length, and saved beside the song so it works offline after. Songs
  LRCLIB cannot time are flagged **needs manual sync**.
- **Letter-by-letter karaoke wipe** — the current line fills in one letter at a
  time like a videoke box, with each word glowing as it is sung, driven by a
  per-frame animation loop rather than a line-by-line switch.
- **Scoring, revealed at the end** — the microphone is analysed quietly with
  the Web Audio API while you sing: whether you came in on each word and sang
  it through, and stayed quiet in the instrumental breaks. Scores run from
  75 to 100, so nobody leaves the mic feeling bad. Nothing about the score is
  on screen until the song ends. Then it pops up with a count-up and a chime.
- **Built-in lyric sync editor** — play a song, tap `J` on each line (or on
  each word, for word-by-word timing), and it
  writes a proper `.lrc` timing file. For songs LRCLIB does not have, and for
  nudging a downloaded line that lands slightly off.
- **Just add the original** — drop in a song's original recording and the
  system removes the vocals itself (Demucs) to make the minus-one karaoke
  track.
- **Automatic word sync** — the backend listens to the singing on the
  original recording and times every word by itself,
  using vocal isolation and forced alignment anchored on LRCLIB's line times.
  Words it is unsure of are flagged for checking. Works for English, Tagalog
  and Taglish. See [Automatic word sync](#automatic-word-sync).
- **Session leaderboard** — a ranking for the night, reset whenever you start
  a new session.
- **A mic bubble for the host** — the keypad, search and queue live in a
  drawer behind a small draggable mic bubble ("chat head" style), which you can
  park on any edge. It closes by itself when left alone.
- **Nothing covers the lyrics** — no drawer, toast or dialog is ever drawn over
  the lyrics band or the countdown, at any screen size. See
  [The performance zone](#the-performance-zone).
- **Practice mode** — sing with the lyrics and no score.

---

## Quick start

You need **Python 3.10 or newer**. Everything else installs in one step.

```bash
# 1. Install the dependencies
pip install -r requirements.txt

# 2. Create the database
python scripts/init_db.py

# 3. Import the songs in data/
python scripts/import_songs.py

# 4. Run it
python backend/app.py
```

Then open **http://localhost:5000**.

The library comes with a short **demo song** (code `1001`) that is already
timed, so you can see the synced lyrics, the glow effect and the scoring
working straight away without setting anything up. It was generated from pure
maths by `scripts/make_sample_song.py` — nobody recorded it, so it carries no
copyright at all. It deliberately contains an instrumental break in the middle,
so you can hear the score drop if you keep singing through it.

| Page | Address | What it is |
|---|---|---|
| Karaoke screen | `http://localhost:5000` | The main machine — player, lyrics, scoring, host controls |
| Sync editor | `http://localhost:5000/sync` | Add timing to a song's lyrics |
| Phone remote | `http://localhost:5000/remote` | Search and join the queue from a phone |

### First run: timing comes from LRCLIB

Step 3 does more than import: every song without timing is looked up on
[LRCLIB](https://lrclib.net), and when it has a version that fits your audio
file the timing is saved straight away. Nothing more to do for those songs.

A song LRCLIB cannot time is flagged **needs manual sync**. Time it by hand in
one playthrough:

1. Open **http://localhost:5000/sync**
2. Pick a song
3. Press **SPACE** to play, then tap **J** as each line begins
4. Press **Save .lrc**

See [Timing lyrics](#timing-lyrics) for the details.

---

## How to use it

### Singing a song

1. **Scan the QR code** in the top-left corner of the karaoke screen with your
   phone. Enter your name once (the phone remembers it), then type a code
   (try `1001`) or search, and tap the song. A toast on the big screen says
   you have been added.
2. When a song is about to start, the screen asks once whether to **turn on
   the mic**. Say yes to be scored. It measures the room's background noise
   for a second, so a quiet moment helps.
3. Watch for the **3 - 2 - 1** in the middle of the screen. Start singing as
   it ends: that is when the first line lights up.
4. Sing along as each line wipes from white to gold. The next line waits,
   dimmed, underneath.
5. When the song ends, your **score is revealed** with a chime. The next song
   starts after a few seconds, or press **Next song**.

The first song may need one click on the karaoke screen before it can play.
Browsers do not let a page start sound by itself until someone has clicked
it. If that happens, a big **Tap to start the song** button appears.

### Hosting

Everything for the host is behind the round **mic bubble** (top-right to begin
with). Click it, or just type a song number on the keyboard, and a drawer
slides out with:

- **Songs** — the keypad and search, to add a song from the host computer
- **Queue** — reorder (arrows or drag), remove, or clear
- **Scores** — tonight's leaderboard
- **Host** — the microphone switch, practice mode, the big QR code, and the
  sync editor

**Play/Pause** and **Skip** sit at the top of the drawer. A skipped song is
never scored and plays no chime. Drag the bubble to any edge to move it out
of the way; the drawer then opens from that edge. The drawer closes by itself
after about nine seconds untouched, or when you click outside it.

### Letting people join from their phones

The QR code is always on the karaoke screen. Click it for a bigger one. Phones
must be on the same Wi-Fi network as the computer running the server. They
get search and the queue; audio, lyrics and the microphone stay on the host
screen.

---

## Timing lyrics

This is the part worth understanding, because it is what makes the synced
lyrics possible at all.

Lyrics need to be in **LRC format** — each line paired with the moment it is
sung:

```
[00:12.40]I want to know
[00:15.10]Where I can go
```

Plain lyric sheets have the words but no timestamps, and typing them by hand
for a whole song is miserable. There are two ways to get them instead:
download them from LRCLIB, or tap them in with the sync editor.

### Downloading from LRCLIB

[LRCLIB](https://lrclib.net) is a free, open database of timed lyrics with no
API key. `import_songs.py` asks it for every song that has no timing yet —
songs just added, and older ones still waiting, so a song LRCLIB lacked last
time gets another chance on every run. What it saves is an ordinary `.lrc`
file; after that, the song never needs the internet again.

Matching uses the title, the artist **and the length of your audio file**. The
length matters: LRCLIB often holds several versions of one song — Totoong Tayo
has entries at 180s, 227s, 228s and 275s — and timestamps only fit their own
recording. In order:

1. an exact lookup, where LRCLIB itself requires the length to match
2. a search by title and artist, taking the version closest in length
3. a free-text search, for small spelling differences

Versions more than **3 seconds** longer or shorter are never used for timing:
that is a different edit, and its lyrics would drift out of step.

What happens to each song:

| Result | Meaning |
|---|---|
| **synced** | a fitting version was found; its timing is saved |
| **needs manual sync** | LRCLIB has no timing that fits. If it had the words, they are saved for you to tap along to |
| **needs manual sync (no lyrics yet)** | nothing at all — paste the lyrics in the sync editor |

In the sync editor, the **Timing Source** panel shows where a song's timing
came from, and its button fetches from LRCLIB on demand — useful when you were
offline during import. On a song that is already timed the button reads
**Replace with LRCLIB timing** and asks first; the old file is kept as
`<name>.lrc.bak`, so a replacement can always be undone.

A song LRCLIB does not have at all opens in a **paste box**: paste the lyrics,
one sung line per line, and tap them along as usual.

Downloaded lyrics land in `data/library/lyrics/`, which is gitignored like the
audio, so they are never committed.

### Using the sync editor

The editor has two modes, switched with the **Lines / Words** buttons:

- **Lines** — one tap per line. A whole song in one playthrough.
- **Words** — one tap per word. Slower, but the karaoke wipe then follows how
  each word was actually sung instead of moving across the line at one pace.

Both edit the same song, so they mix freely: time the lines first, then go
back and word-time just the lines that deserve it.

| Key | Both modes |
|---|---|
| `SPACE` | Play / pause (`ENTER` works too) |
| `J` | Stamp the next line, or the next word |
| `BACKSPACE` | Undo the last stamp |
| `←` / `→` | Nudge: the last line by 0.1s, or the selected word by 0.05s (`Shift`: 0.01s) |
| `,` / `.` | Seek back / forward 2 seconds |
| `L` | Replay the current line |
| `S` | Playback speed: 100% → 75% → 50% |
| `N` | Jump to the next word auto sync was unsure of |

| Key | Words mode only |
|---|---|
| `↑` / `↓` | Select the previous / next word |
| `K` | Mark where the line's singing stops |
| `R` | Re-tap the current line from its first word |

Tips:

- Listen through once first so you know the song.
- Tap the moment a line or word **starts**, not when you finish reading it.
- Every stamp is shifted 0.15s earlier to allow for human reaction time, so do
  not try to tap early yourself. At reduced speed the shift shrinks to match,
  so slowed-down taps stay just as accurate.
- **Slow down for words.** At 75% there is a third more time between words.
- Press `K` just after a line's last word when a break follows: the wipe then
  stops with the singing instead of crawling through the solo.
- Click a word to hear it and select it — `J` then re-taps from there. In
  Lines mode, clicking a line does the same.
- The strip above the lyrics is a **live preview** using the real karaoke
  wipe, drawn from exactly what Save will write.
- Section headers like `[Chorus]` are skipped automatically.

**On saving:** a line is saved with word timing only once *every* word in it
is tapped — half a line of real timings mixed with guessed ones would wipe
unevenly — and the editor tells you if any were left half done. Words that
come out of order (usually a tap after seeking back) block the save, and the
editor selects the word to fix.

The saved `.lrc` lands next to the original lyric file, and the song is marked
as synced straight away. The previous version is kept as `.lrc.bak`.

---

### How the wipe is timed

LRC timestamps whole lines, not words. The player fills the active line one
letter at a time, at a steady pace, from that line's timestamp to the next
line's — the way a videoke box does it.

One side effect: when an instrumental break follows a line, that line's slot
runs until the singing resumes, so its wipe slows right down through the
break. A file can prevent this with a blank timestamped line where the break
starts (the wipe then stops there), but LRCLIB files rarely include one
mid-song. A slow wipe before a solo is therefore a pacing effect — the line
still *starts* at the right moment.

Word timing removes most of this. A word-timed line (Enhanced LRC, with a
`<mm:ss.xx>` tag before each word) fills each word during its own measured
slice of time, and its letters within it; a line marked with `K` also stops
exactly where the singing does:

```
[00:12.40]<00:12.40>I <00:12.80>want <00:13.00>to <00:13.40>know <00:14.60>
```

The last tag, with no word after it, is the `K` mark. When `K` was not
pressed, the editor ends the last word after a typical held note (1.5s) or
at the next line, whichever comes first.

## Automatic word sync

Tapping every word by hand takes a whole playthrough per song, often
several. Auto sync does it instead: it listens to the singing and works out
when each word of the known lyrics is sung, then saves the result in the same
Enhanced LRC format the editor writes. The player and the editor need nothing
new to use it.

### Setting it up (once)

Auto sync uses machine-learning models that are far too big for the main
app's `requirements.txt`, so they get their own environment:

```bash
python scripts/setup_autosync.py
```

This creates `.venv-align` (Python 3.10–3.13), installs PyTorch (CPU
version: no graphics card needed), Demucs, transformers, soundfile and
Whisper, and downloads the models, about 1.8 GB in all. It takes 10–20
minutes on a normal connection and can be re-run safely. **No ffmpeg is
needed.** The karaoke app itself runs fine without any of this; only auto
sync needs it.

### Adding a song: just the original recording

Put the **original recording, with vocals,** in `data/library/originals/`
and run `python scripts/import_songs.py`. The system does the rest:

1. **Makes the minus-one.** Demucs separates the vocals out, and everything
   else (drums, bass, instruments) becomes the karaoke track, saved as
   `data/library/audio/Artist - Title (minus).mp3` (320 kbps stereo). This
   takes about 4 minutes per song on a laptop.
2. **Imports it** like any other song, with its line timing from LRCLIB.
3. **Queues it for auto sync,** which times every word by listening to the
   *original's* vocals. The separation is cached, so it is not repeated.

Because the minus-one is made from the very same recording, the word times
measured on the original fit it exactly.

**About sound quality:** an AI-made minus-one is good but not studio-clean.
Expect a slightly "watery" sound in places, and sometimes a faint trace of
the voice, especially reverb and backing vocals. For a cleaner result, change
`MODEL_NAME` in `backend/autosync_engine/separation.py` to `"htdemucs_ft"`,
which is about 4 times slower.

**You can still use your own minus-one** from a karaoke source: put it in
`data/library/audio/` as before, and the original (for timing) in
`data/library/originals/`, with matching names. Suffixes like `(minus)` or
`(Official Audio)` are ignored when pairing. For names too different to pair,
add `"original": "<filename>"` to the song's entry in `song_overrides.json`.
Nothing is generated for an original that already has a playback track. A
bought minus-one may be a *different recording* (re-played by a karaoke band,
perhaps at another tempo), so auto sync checks it matches the original (see
*The minus-track check* below) and marks the song `needs_review` if not.

Originals, generated minus-ones and the cache all live under
`data/library/`, which is gitignored.

### From the sync editor

Open **http://localhost:5000/sync**.

- **Add a song:** open **+ Add a song** above the song list and choose the
  original recording from your computer. Artist and title are filled in
  from a file name like `Artist - Title (Official Audio).mp3`. Under *More
  options* you can give your own minus-one instead, or paste lyrics for a
  song LRCLIB does not have. Press **Add song**: the file uploads, then the
  minus-one is made, the song imported and auto-synced in the background,
  with progress shown under the form (about 8 minutes).
- **Auto Sync panel:** in a song, **Auto Sync** (or **Re-run auto sync**)
  starts a run and shows its progress. The song list shows each song's state
  (`auto: running`, `auto: done`, `auto: check`, `locked`).
- **Checking the result:** words auto sync was unsure of get a dashed amber
  outline and a dot. Hover one to see why. Press **N** (or *Next word to
  check*) to jump to the next one and hear it. Nudging or re-tapping a word
  clears its highlight. Pressing **Save** marks the song as checked.
- **Drafts:** for a song you word-tapped yourself, auto sync keeps its result
  as a draft; **Load auto-sync draft** puts it in the editor (nothing is
  saved until you press Save, and reopening the song brings your own timing
  back).
- **Preview:** plays the song full screen with the karaoke screen's own
  lyric band and word glow, drawn from exactly what Save would write. Space
  plays and pauses, `,` and `.` seek, and Esc closes it.

### Running it

- **Automatically:** every *new* song found by `import_songs.py` is queued.
  The server works through the queue in the background, one song at a time.
- **By hand:** `python scripts/auto_sync.py --song 1004`, or
  `--queued` to process the queue without the server running.
- **From the API:** `POST /api/songs/<id>/auto-sync` queues a song and
  returns straight away. `GET` on the same address reports progress.

Each song gets a **sync status**:

| Status | Meaning |
|---|---|
| `none` | never auto-synced |
| `queued` | waiting for its turn |
| `processing` | running now |
| `done` | synced, and at least 90% of words are high confidence |
| `needs_review` | synced, but check it: low-confidence words, a poor minus-track match, or no LRCLIB line timing |
| `failed` | could not run; the message says why (for example, no vocals to hear) |

**If you already word-tapped a song by hand,** auto sync does not overwrite
it. The result is kept as a **draft** in `Artist - Title.autosync.json` next
to the `.lrc`. Songs with only line timing get the result applied directly,
and the previous `.lrc` is kept as `.lrc.bak` as usual.

### Locking a song

Songs whose timing you never want touched can be **locked**. Add
`"sync_locked": true` to the song's entry in `data/song_overrides.json`:

```json
"Totoong Tayo - Jin DC (minus).mp3": {
    "artist": "Jin DC", "title": "Totoong Tayo", "language": "Filipino",
    "sync_locked": true
}
```

A locked song is never queued, never re-run, gets no draft, and auto sync
writes none of its files. The lock is checked again right before anything is
written, so locking a song while its job runs still protects it. The manual
sync editor keeps working on locked songs. Locks are reapplied from the
overrides file whenever the server starts or `import_songs.py` runs, so they
survive a database reset. `python tests/test_sync_lock.py` proves all of this
on a throwaway copy of `data/`.

### How it works

```
original ──Demucs──► vocals + accompaniment               (cached)
                          │            │
                          │   compare with the minus track → offset, match score
                          ▼
     MMS model: how likely each letter is, every 20 ms     (cached)
                          ▼
     Viterbi over the WHOLE song: every lyric word, in order
                          ▼
     check each line against LRCLIB → keep, re-align in its window, or even pace
                          ▼
     clean-up for singing → confidence → .lrc + .autosync.json
```

1. **Vocal isolation.** [Demucs](https://github.com/facebookresearch/demucs)
   (`htdemucs`) splits the recording into vocals and accompaniment.
   Alignment on isolated vocals is far more accurate than on the full mix.
   Stems are cached in `data/library/autosync_cache/stems/`, keyed by the
   file's contents, so this slowest step never runs twice on the same audio.
2. **The minus-track check.** The original's accompaniment should line up
   with the minus track beat for beat. Both are reduced to an *onset
   envelope* (how much new sound starts in each 10 ms), and
   cross-correlation finds the time shift where they agree best. That shift is
   applied to every word. The match score (0–1) and whether the shift stays
   the same across all four quarters of the song decide whether it is
   trusted. A re-recorded karaoke version at a different tempo fails this
   check and is marked `needs_review`.
   **Looped music is a trap:** a track built from a repeating loop matches
   itself almost perfectly when shifted by one loop. On *AMAZING* (a 1.91 s
   loop) that shift scored higher than the true one, and every word moved
   1.9 s. So when several shifts score nearly as well, the one closest to
   zero wins. When the minus-one was *made* from the original, the system
   knows the true shift is zero and uses exactly that.
3. **Forced alignment.** This is not speech-to-text: the lyrics are already
   known. The MMS model (Meta's Massively Multilingual Speech, fine-tuned for
   alignment on 1,130 languages) gives, every 20 ms, the probability of each
   letter a–z. The Viterbi algorithm then finds the most likely moment for
   each letter of the lyrics in order: dynamic programming over a grid of
   frames × letters. The whole song is aligned in one pass, with a wildcard
   allowed between lines for ad-libs and instrumentals. Because every line
   must fit, in order, the audio decides where each line really is. See the
   long comment at the top of
   [`ctc_align.py`](backend/autosync_engine/ctc_align.py). The letter
   probabilities are cached, so a re-run takes seconds.
4. **LRCLIB as a cross-check.** LRCLIB's line times are often right, but not
   always: on the test song *Happiness*, three lines were 1.3–2.7 s off. So
   instead of forcing words to follow LRCLIB, each line's audio position is
   compared with it. A line agrees when it starts within 1 s of LRCLIB's
   time *and* ends before the next line's. (On *AMAZING* a line began on
   time but was stretched over 10 s, swallowing the next two lines, so the
   start alone is not enough.) A line that disagrees keeps the audio's
   answer only if the model was confident about it **and** it still falls
   between its neighbours' LRCLIB times (LRCLIB's times can be off, but its
   *order* of lines is right). Otherwise the line is re-aligned inside its
   LRCLIB window. Words well outside their LRCLIB line are flagged for
   checking.
5. **A second listener.** Some lines the alignment model cannot hear at all:
   heavily processed vocals, fast repeated choruses. These used to get an
   even-paced wipe across LRCLIB's slot, which ran about 0.5 s behind the
   singing, because the singing usually ends well before the next line.
   Now Whisper transcribes the vocals with word times, and each such line's
   words are matched to what it heard there, in order, allowing near-misses
   like *waitin'* / *waiting*. On the test songs Whisper rescued 20 of 23
   such lines. Only a line neither model can hear keeps an even wipe, and
   that now covers only the part of the slot where singing is heard.
   (Letting Whisper also re-time single *words* the aligner was unsure of
   was tried and made them worse against hand taps, so it only handles
   whole lines.)
6. **Clean-up for singing.** The model was trained on speech. It notices a
   new letter slightly after it is sung, so every start is moved **0.15 s
   earlier**. The raw starts were 0.14–0.25 s late on the test song, measured
   against both hand taps and Whisper. Word ends are extended while the
   vocals are still sounding (held notes), gaps under 0.35 s are closed, a
   barely-heard word that sits far from its neighbours is re-placed between
   them, and order is enforced. The line's end mark is
   where the last word actually stops, so the wipe stops before instrumental
   breaks.
7. **Confidence.** Each word gets a score: the model's average probability for
   its letters where the path placed them. It is **high** confidence if that
   score is at least 0.05 and nothing had to be corrected. The cutoff was
   calibrated on 615 words of three songs against Whisper's word times.
   Words scoring below 0.05 were a median 0.5 s off; words above were as
   accurate as each other (about 0.08 s), so a higher cutoff only
   highlighted good words. It catches about half of the badly placed words;
   the other half score well and cannot be told apart by score.

**Fallbacks.** With lyrics but no LRCLIB timing, the whole-song alignment
is used as it is, with nothing to check it against (`needs_review`). With no lyrics at all, Whisper transcribes the
vocals first (`needs_review`, since the words themselves need checking).

### Tagalog, English and Taglish

The aligner works letter by letter, and the MMS model was trained on Tagalog
as well as English. Tagalog is written the way it sounds, which suits this
approach well. Mixed lines need no setting at all: accents are removed
(`ñ` → `n`), hyphens and apostrophes are dropped (`pag-iibigan`,
`kulita't`), and the letters are aligned.

The language setting (`"language"` in `song_overrides.json`) only matters for
the Whisper fallback: `Filipino` → Tagalog mode, which also copes with English
words mixed in.

If accuracy is poor for a song, try in this order: keep vocal isolation on;
check the minus-track match in the status message; check the LRCLIB line
times are roughly right (the window depends on them). Then fix the flagged
words in the editor; that is what the highlighting is for.

### Measuring accuracy

```bash
python scripts/measure_drift.py --song 1004
```

1. In the sync editor's Words mode, carefully tap a few lines and save.
2. Run auto sync. Because the song now has hand-tapped words, the result is
   kept as a draft and your taps stay in the `.lrc`.
3. Run the command above. It compares the two word by word.

It reports the **bias** (average early/late), the **median** and
**90th-percentile** error, and the share of words within 100/200/300 ms. It
shows the same figures for the **baseline**, the even letter-by-letter wipe
the karaoke would show without word timing, and splits the errors by
confidence level. `--words` lists every word and `--csv file.csv` exports
them. Hand taps are themselves off by about 0.05–0.1 s, so that is roughly
the floor of what this can measure.

To test without touching the song at all:

```bash
python scripts/auto_sync.py --song 1004 --dry-run --anchors lrclib --out result.json
python scripts/measure_drift.py --reference "data/library/lyrics/Artist - Title.lrc" --auto result.json
```

### Measured accuracy

Tested on three songs: *Happiness* against a full set of hand taps, and all
three against Whisper's own word timestamps as an independent second opinion
(Whisper's are rough, about ±0.2 s, but they come from a different method).

| Song | Compared with | Median error | Within 0.2 s | Within 0.3 s |
|---|---|---|---|---|
| Happiness (English) | hand taps, all lines | 0.15 s | 61% | 71% |
| Happiness | hand taps, lines where taps and Whisper agree | 0.13 s | 67% | 77% |
| Happiness | *baseline: LRCLIB lines, even wipe* | *0.54 s* | *23%* | *30%* |
| Mahal Magmahal (Tagalog) | Whisper | 0.07 s | 72% | 79% |
| Best Friend (English) | Whisper | 0.07 s | 87% | 91% |
| AMAZING (English) | Whisper | 0.09 s | 74% | 81% |
| XXL (English) | Whisper | 0.09 s | 70% | 76% |

On every song the average error is between 0.00 and +0.07 s: nothing runs
consistently late. (Lines Whisper itself rescued are left out of the
Whisper comparisons, since comparing Whisper with itself proves nothing.
Against the *Happiness* hand taps, Whisper's word times were typically
about 0.2 s off, so that is the realistic accuracy for rescued lines,
compared with 0.5 s late for the even wipe they replace.)

Two honest caveats. The 0.15 s start correction and the thresholds for
trusting the audio over LRCLIB were tuned on *Happiness*, so its numbers are
somewhat flattering; the Whisper comparisons on the other two songs were not
used for tuning. And hand taps are imperfect too: on three lines of the
*Happiness* taps, Whisper and auto sync agreed with each other but were about
a second away from the tap.

### Processing time

Measured on a mid-range laptop (AMD Ryzen 5 7520U, 4 cores, 8 GB RAM, no
graphics card), for 4½-minute songs:

| Step | Time |
|---|---|
| Making the minus-one / vocal isolation (Demucs), once per original | about 4 min |
| Letter probabilities (MMS model), once per song | 2.5–4 min |
| Minus-track check, Viterbi and clean-up | 5–25 s |
| Second listener (Whisper), only when some lines cannot be heard | about 2–2.5 min, once per song |
| **Adding a new song, start to finish** | **about 7–8 min** |
| **Re-running auto sync** (everything cached) | **about 20 s** |
| No-lyrics fallback (Whisper small) | not yet measured; expect several extra minutes |

Jobs run one at a time in the background, so the karaoke stays usable
meanwhile, though the computer will be busy.

These times vary. One re-run of the same song on the same laptop, plugged in,
took 11 minutes instead of 2.7. A warm or busy computer can easily be
several times slower.

## Adding new songs

1. Put the audio in `data/library/audio/` (`.mp3`, `.m4a`, `.ogg` or `.wav`).
2. Put matching lyrics in `data/library/lyrics/` as a `.txt` file.
3. Run `python scripts/import_songs.py`. It imports the song and looks its
   timing up on LRCLIB.
4. If it reports **needs manual sync**, time the song in the sync editor.
5. Or skip steps 1–2: put just the **original recording** in
   `data/library/originals/`, and the import makes the minus-one for you and
   queues it for [automatic word sync](#automatic-word-sync).

The `.txt` in step 2 is optional: without one, LRCLIB's words are used, or you
can paste them in the sync editor.

**Filenames matter.** Name files `Artist - Title.mp3` and the importer will
work out both fields. Recording labels in brackets are stripped, so
`Artist - Title (minus).mp3` and `Artist - Title.txt` are correctly recognised
as the same song.

### When the filename is backwards

Some files are named `Title - Artist` instead. Nothing can tell those apart
automatically, so state the correction in `data/song_overrides.json`:

```json
{
  "Totoong Tayo - Jin DC (minus).mp3": {
    "artist": "Jin DC",
    "title": "Totoong Tayo",
    "language": "Filipino"
  }
}
```

Copy `data/song_overrides.example.json` to get started. These corrections are
reapplied on every import, so they survive rebuilding the database.

### Useful commands

```bash
python scripts/import_songs.py --list   # show the library and sync status
python scripts/import_songs.py --no-lrclib   # import without going online
python scripts/import_songs.py --no-minus    # do not make minus-ones from originals
python scripts/import_songs.py --no-auto-sync  # do not queue new songs for auto sync
python scripts/init_db.py --reset       # wipe and rebuild (erases scores)
python scripts/make_sample_song.py      # regenerate the royalty-free demo song
```

---

## How it works

### Project layout

```
backend/          Flask app
  app.py            entry point and app factory
  config.py         all paths and settings
  database.py       SQLite connections
  schema.sql        the database tables
  models/           one module per table
  routes/           the HTTP API
  sockets/          live updates
  utils/            LRC parsing, LRCLIB lookups, song importing,
                    auto-sync jobs (autosync.py)
  autosync_engine/  the word-alignment pipeline - runs only in .venv-align

frontend/         the browser side
  index.html        karaoke screen
  sync.html         lyric timing editor
  remote.html       phone view
  css/              base tokens, retro theme, components,
                    videoke.css (the karaoke screen's own layout)
  assets/           placeholder background video and score chime
  js/
    player/           audio, LRC parsing, the glow effect
    scoring/          microphone, score engine, the end-of-song reveal
    videoke/          background video, countdown, mic bubble + drawer,
                      toasts, chime, and the performance-zone guard
    search/ queue/    search panel and queue list
    leaderboard/      tonight's top scores
    visuals/          spectrum animation (no longer on the karaoke screen)
    tools/            the sync editor

scripts/          import, auto sync, accuracy measurement, setup
tests/            test_sync_lock.py - proves locked songs are never touched

data/
  samples/          royalty-free demo songs (committed)
  library/          your own songs (NOT committed): audio/, lyrics/,
                    originals/ (with vocals, for auto sync),
                    autosync_cache/ (separated vocals, job files)
  karaoke.db        the database (created on first run)
```

### The database

Four tables in SQLite: **songs** (the library, with the number code),
**sessions** (one sitting of karaoke), **queue_entries** (who is singing what,
in order) and **scores** (finished performances). Full definitions with
comments are in [`backend/schema.sql`](backend/schema.sql).

Finished queue entries are kept rather than deleted, which is what gives the
session its history and lets the leaderboard link back to what was sung.

### The videoke screen

The karaoke screen is a four-row grid, laid over a full-screen looping video:

```
┌──────────────────────────────────────────────┐
│ KARAOKE   ANA  Sunflower ━━━━━━━━──── 1:02    │  row 1: slim strip - song and progress
├──────────────────────────────────────────────┤
│ UP NEXT (1) 1003 Ben  (2) 1002 Cy  (3) ...   │  row 2: the waiting queue - code + singer
├──────────────────────────────────────────────┤
│ [QR] Scan to add a song       toasts     (🎤) │
│                                              │  row 3: the countdown, plus every
│                     3                        │         popup (#overlay-layer)
│                                              │
├──────────────────────────────────────────────┤
│        I want to know where I can go         │  row 4: the lyrics band -
│          (the next line, dimmed)             │         current line + next line
└──────────────────────────────────────────────┘
```

Between the video and the text is a **scrim**: a still dark gradient, darkest
behind the strip and the lyrics, so the words stay readable over any footage.
Everything that moves animates only `transform` and `opacity`, which the
browser can animate without repainting.

The lyrics band uses the same renderer as the sync editor's preview, in a
`'band'` layout. Only two lines are shown. When a line finishes, the whole
strip of lines slides up one place: the next line grows into the top slot and
the one after fades in beneath it.

### The performance zone

The **performance zone** is what the singer is reading: the lyrics band, plus
the countdown while it shows. No popup may ever cover it. Two layers of
protection make sure of that
([`performanceZone.js`](frontend/js/videoke/performanceZone.js)):

1. **Structural, for the band.** Every popup (the mic bubble, the drawer,
   toasts, prompts, dialogs, the score reveal) lives inside one element,
   `#overlay-layer`. That element *is* the third row of the grid, and it clips
   anything that pokes out of it. So nothing inside it can be drawn past the
   top edge of the band, at any screen size. No sizes have to be kept in step;
   the grid decides where the band is. Even the drawer that slides up from the
   bottom stops at the band's edge.
2. **At runtime, for the countdown.** The countdown sits *inside* the middle
   row, so structure cannot protect it. Instead, while it shows, the guard
   measures every popup's box. Any popup that overlaps the countdown fades out
   until the count ends, about three seconds later. A toast that arrives
   mid-count waits instead of appearing.

The browser tests for this check that no popup's visible area crosses the
band's top edge at 1920×1080, 1366×768, 1024×600, and on phones in both
orientations.

### The countdown

LRC files have no "intro" or "count-in" marker, only the time each line
starts. So the countdown is anchored to the **first line that has words**
(blank lines are instrumental markers), and covers the three seconds before
it: 3 at *t*−3, 2 at *t*−2, 1 at *t*−1, and the line lights up at *t*. It reads
the song's own playback time, so pausing and seeking keep it in step. An
intro shorter than three seconds counts from lower down (a 2.4 s intro counts
"2, 1"); under one second there is no countdown at all.

It is exactly as accurate as that first timestamp. If a song's count ends
early or late, fix the first line in the sync editor.

### The wipe and the glow

The obvious way to sync lyrics is the `<audio>` element's `timeupdate` event —
but browsers only fire it about four times a second, which makes an effect
meant to feel rhythmic visibly step instead.

So [`lyricsRenderer.js`](frontend/js/player/lyricsRenderer.js) runs its own
`requestAnimationFrame` loop at ~60fps, reads `audio.currentTime` directly, and
writes numbers to CSS custom properties: `--fill` on each letter (how much of
it has been sung), `--glow` on each word, and `--beat` on the line. The
stylesheet does all the drawing from those values. JavaScript does the maths,
CSS does the drawing, and the animation stays on the GPU.

Every letter is its own `<span>`, grouped inside a `<span>` per word. That is
what makes a long line that wraps onto two rows wipe correctly: a single
gradient across a wrapped element is painted over its whole box, so both rows
would light up together. Filling the letters one after another makes the wipe
follow the reading order — the first row finishes before the second starts.

The line flare is an attack-and-decay curve: 1 when a line starts, falling
toward 0 over about half a second.

### The scoring

[`micCapture.js`](frontend/js/scoring/micCapture.js) builds a Web Audio graph:

```
getUserMedia()  ->  MediaStreamSource  ->  AnalyserNode
```

Note it is **not** connected to the speakers — routing a microphone to the
output would cause feedback. Noise suppression and auto gain are switched off,
because both would alter the very thing being measured. Echo cancellation is
off by default too, but can be switched on in the **Host** tab (see below).

Loudness is measured as **RMS** (root mean square) rather than peak amplitude,
so a single spike does not dominate. A frame counts as singing when it is
clearly louder than the background: the room, and the speakers leaking into
the mic. The background is measured when the mic is turned on, and then
**keeps adjusting itself** every half second, from the quietest moments of the
last 8 seconds (the breaths and line breaks, where only the background can be
heard). A one-off measurement was not enough: the mic is usually switched on
as a song starts, and on songs that start singing straight away it measured
the *singer* as background, after which a great singer scored the same as a
silent one.

This runs **silently** for the whole song: every frame the microphone is
measured and fed to the score engine, but nothing is drawn. The score is only
revealed when the song ends by itself
([`scoreReveal.js`](frontend/js/scoring/scoreReveal.js)). The labels under
the score ("Ang galing!", "Solid!", …) are in a table at the top of that file.
A skipped song is never scored.

Two things are then scored ([`scoring/features/`](frontend/js/scoring/features/)):

- **Timing (90%), word by word** — for every word of the song (from the word
  timings auto sync produces): did your voice **come in** near the start of
  the word (from quiet, or with a clear rise in loudness, a new syllable), and
  did you **hold** it for a good part of the word? Coming in counts 60%,
  holding 40%. Singing through long instrumental breaks costs up to 30%.
  Silence earns nothing by itself. For songs with line timing only, the words'
  times are estimated and the "on cue" window is wider.
- **Energy (10%)** — measured only *during the words*: whether the loudness
  rises and falls naturally (as the *coefficient of variation*, standard
  deviation ÷ mean, so loud and quiet singers are judged alike) and whether
  you were clearly audible.

**Shown from 75 to 100.** The blend (0 to 1) is mapped onto 75–100: nothing
sung is 75, every word on cue is 100. It's a straight-line mapping, so the
order of performances, and the leaderboard, is exactly what was measured; only
the numbers are kinder. The labels ("Ang galing!", "Solid!", …) are spread
over that range, in a table at the top of
[`scoreReveal.js`](frontend/js/scoring/scoreReveal.js). The raw 0–1 timing and
energy scores are saved with each score too.

**Why it was redesigned.** The first version asked only "was the mic loud
while a line was on screen?" plus "was the loudness steady?". Tested by
running the real scoring code over five whole songs with simulated singers
(built from the songs' separated vocal tracks), it rewarded the wrong things:

| Simulated singer | Old score (0–100) | New score (75–100) |
|---|---|---|
| Great singer (the original vocals) | 68–84 | 91–97 |
| Mumbles, but on cue | 59–64 | 89–92 |
| Sings only every other line | 58–63 | 85–89 |
| Chats randomly through the song | 64–66 | 80–81 |
| Shouts nonstop, ignoring the song | 72–84 | 75 |
| Silent | 30–43 | 75 |

**Known limits.** Loudness can only tell *when* someone sings, not *what*:
someone singing a full second late still comes in near *some* word most of the
time and scores only a few points lower (89–95). And if the speakers leak
strongly into the mic, a silent guest can reach the 80s; try **Echo
cancellation** in the Host tab, which removes what the page itself is playing
from the mic. Judging the actual melody against the original singer would fix
both; see *Upgrading to pitch matching* below.

### Upgrading to pitch matching

The scoring engine holds a list of **feature extractors**, each one an object
with `reset()`, `process(frame)` and `summary()`. The engine blends whatever it
is given using a weights table, so adding pitch detection means adding one
extractor and one weight — no other file changes.

[`frontend/js/scoring/features/pitch.js`](frontend/js/scoring/features/pitch.js)
is already written against that interface, with step-by-step instructions at
the top for switching it on. The `pitch_score` database column already exists,
so no migration is needed either.

The honest catch, and the reason it ships disabled: **detecting pitch is the
easy half.** The `pitchy` library reports the singer's frequency in one call.
The hard half is the *reference* — to say a note was wrong you need to know
what note was right, and the backing tracks have no vocal line to compare
against. The realistic answer is **relative pitch scoring**: reward holding a
steady note and landing cleanly in the centre of a semitone, without needing a
reference melody at all. That approach is implemented in the file.

### Live updates

Flask-SocketIO pushes the whole queue to every client whenever it changes. The
server sends complete state rather than incremental patches — re-rendering a
list of a dozen rows costs nothing, and it removes a whole category of "my copy
disagrees with yours" bugs.

The Socket.IO client is **served from this project** (`js/vendor/`) rather than
a CDN, so the live queue works with no internet at all — which matters in a
classroom, and avoids school networks that block or proxy CDN requests.

The connection stays on HTTP long-polling rather than upgrading to a WebSocket.
Werkzeug's development server cannot complete that upgrade here and answers it
with HTTP 500, so the upgrade is switched off deliberately in `app.py`.
Long-polling delivers updates in well under a second, which is indistinguishable
from a WebSocket for a song queue. Behind a production server (gunicorn with
eventlet) you would turn `allow_upgrades` back on and get real WebSockets.

If the client script somehow fails to load entirely, `js/lib/socket.js` falls
back to plain polling every two seconds.

---

## Design decisions

**Flask over FastAPI** — FastAPI is async-native with automatic API docs, which
is genuinely nicer for a production API. Flask was chosen because it is easier
to read and explain, and `Flask-SocketIO` handles WebSockets with far less
boilerplate than FastAPI's manual WebSocket handling.

**SQLite over a database server** — no installation, no service to run, no
credentials. The whole database is one file that can be deleted and rebuilt in
seconds. For a single room of singers, a server would be pure overhead.

**Vanilla JavaScript over a framework** — the app has maybe a dozen interactive
pieces, which ES modules organise perfectly well. A framework would add a build
step, a dependency tree and concepts to explain, in exchange for very little.

**Threading mode for SocketIO** — `eventlet` and `gevent` are faster under
heavy load, but they lag behind new Python releases and often need C build
tools on Windows. Threading mode has neither problem and comfortably handles a
roomful of phones.

---

## Troubleshooting

**Lyrics show but do not scroll or score.** That song needs manual sync —
LRCLIB either has no timing that fits it or could not be reached. Once you are
online, run `import_songs.py` again or use **Fetch timing from LRCLIB** in the
sync editor; otherwise tap it through.

**The lyrics run early or late all the way through.** LRCLIB probably matched a
different recording. Check the song's length on lrclib.net against your file.
The sync editor can fix a small constant offset line by line with the arrow
keys; for a different edit, tap the song through yourself.

**"Could not reach LRCLIB."** The import keeps going without it — those songs
simply stay unsynced and are looked up again next run. Each request is retried
once, so a brief network blip does not count.

**"Microphone access was denied."** Allow the microphone in the browser's
address-bar permission prompt. Browsers only permit microphone access on
`localhost` or over HTTPS — an IP address like `192.168.1.5` will be refused,
which is why scoring lives on the host screen rather than on phones.

**The score stays near 75 even for good singing.** The mic may be too far
away or too quiet: singing has to be clearly louder than the room and the
speakers. Move the mic closer. The background level adjusts itself during the
song, so it recovers from a noisy moment when the mic was turned on.

**The score is high even when nobody sings.** The speakers are leaking into
the mic, and the music counts as singing. Turn on **Echo cancellation** in the
**Host** tab, or turn the speakers away from the mic.

**Phones cannot reach the server.** They must be on the same Wi-Fi network, and
Windows Firewall may prompt to allow Python through — that prompt has to be
accepted.

**A song will not start by itself.** Browsers block a page from playing sound
until someone has clicked or pressed a key on it. The screen shows a big
**Tap to start the song** button when that happens; one click and every later
song starts on its own.

**No score appeared at the end.** A score is only revealed when the
microphone was on, practice mode was off, the song has timed lyrics, and it
played to the end (skipped songs are never scored). Turn the mic on from the
prompt before a song, or from the **Host** tab of the mic bubble's drawer.

**The score chime is silent.** The same autoplay rule applies. The chime is
unlocked by the first click or key press on the karaoke screen; without one it
stays silent rather than showing an error.

**The background video does not show.** A still gradient appears instead when
the video file is missing or fails to load, or when the computer is set to
reduce motion. Check the URL in `frontend/js/videoke/assets.js`.

**"ERROR: something is already running on port 5000."** A previous copy of the
app is still running — closing the terminal window does not always stop it.
Running two copies at once makes them fight over the same port and breaks the
live queue, so the app refuses to start a second one. Stop the old one with
`taskkill /F /IM python.exe` on Windows, or `pkill -f backend/app.py`
elsewhere, then start again.

**The queue does not update live.** Check the indicator in the top right. It
should read **Live**. If it reads **Offline**, the browser could not reach the
server — but the queue still refreshes every couple of seconds as a fallback,
so nothing is actually broken.

**Songs import with the artist and title swapped.** Use
`data/song_overrides.json` — see [Adding new songs](#adding-new-songs).

---

## Legal and audio files

This is a non-commercial educational project.

No copyrighted audio is stored in this repository. The `data/library/` folder,
where personal song files go, is listed in `.gitignore` and is never committed.
Only `data/samples/` — intended for royalty-free or self-created material — is
part of the repository.

If you add your own music, it stays on your machine. Keep it that way: do not
commit commercial recordings, and do not deploy this publicly with them.

The karaoke screen's background video and score chime in `frontend/assets/`
were generated from maths by `scripts/make_placeholder_assets.py`, like the
demo song, so they are free to share. Replace them only with media you have
the rights to — see [`frontend/assets/README.md`](frontend/assets/README.md).
