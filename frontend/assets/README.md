# Karaoke screen media

Placeholder media for the karaoke screen. Both files were **generated from
maths** by [`scripts/make_placeholder_assets.py`](../../scripts/make_placeholder_assets.py).
Nothing was recorded or downloaded, so they carry no copyright and are safe to
commit and share.

| File | What it is | Size |
|---|---|---|
| `background/videoke-loop.mp4` | The looping mood video behind the lyrics: gold, magenta and cyan colour bands drifting over navy. 24 s, 640×360, silent, and it loops with no seam. | ~250 KB |
| `sounds/score-chime.wav` | The "ding" when a score is revealed: a quick rising bell arpeggio. | ~110 KB |

## Swapping in your own

The URLs are set in **one place**:
[`frontend/js/videoke/assets.js`](../js/videoke/assets.js).

1. Put your file in this folder, e.g. `background/beach.mp4`.
2. Change `BACKGROUND_VIDEO_URL` (or `SCORE_CHIME_URL`) in `assets.js` to
   `/assets/background/beach.mp4`.
3. Refresh the karaoke screen.

Set either URL to `''` to turn it off. With no video, the screen shows a
still navy/maroon gradient instead.

### Keep a replacement video light

The video plays for hours on whatever laptop runs the karaoke screen, next to
the lyrics animation. So:

- **H.264 MP4**, no audio track. It plays everywhere, usually decoded by the
  graphics chip.
- **640×360 to 1280×720.** It sits behind a dark scrim, so extra resolution is
  wasted.
- **A few MB at most.** A long, gentle loop compresses far better than busy
  footage.
- **Make sure you may use it.** Public-domain or CC0 footage, or your own. Do
  not commit anything you do not have the rights to share.

If you have ffmpeg, this shrinks almost any clip to a suitable size:

```bash
ffmpeg -i input.mp4 -vf scale=-2:540 -c:v libx264 -crf 28 -an -movflags +faststart output.mp4
```
