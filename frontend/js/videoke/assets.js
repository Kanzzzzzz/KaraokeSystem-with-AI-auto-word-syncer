/**
 * assets.js - the karaoke screen's swappable media, named in one place.
 *
 * Both files are placeholders generated for this project (so they are safe to
 * commit and share - see frontend/assets/README.md). To use your own:
 *
 *   1. drop the file into frontend/assets/ (or anywhere the server serves)
 *   2. change the URL below
 *
 * Nothing else needs editing. Keep a replacement video small - a muted,
 * 640x360 to 1280x720 H.264 MP4 of a few MB at most. It is shown behind a
 * dark scrim, so extra resolution is wasted, and it must play smoothly on a
 * school laptop alongside everything else.
 */

/** The looping background video behind the lyrics. Set to '' for none. */
export const BACKGROUND_VIDEO_URL = '/assets/background/videoke-loop.mp4';

/** The chime played when the score is revealed. Set to '' for silence. */
export const SCORE_CHIME_URL = '/assets/sounds/score-chime.wav';

/** Chime loudness, 0-1. The song has just ended, so it needs no competing. */
export const SCORE_CHIME_VOLUME = 0.7;
