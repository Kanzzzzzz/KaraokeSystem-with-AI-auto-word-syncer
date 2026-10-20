"""
Forced alignment: finding when each letter of the KNOWN lyrics is sung.

-------------------------------------------------------------------------------
STEP 1 - THE MODEL: "which letter is being sung right now?"
-------------------------------------------------------------------------------

The model is MMS (Meta's Massively Multilingual Speech), fine-tuned for
alignment on 1,130 languages including Tagalog. For every 20 ms of audio it
outputs a probability for each letter a-z, plus one extra symbol, BLANK,
meaning "no new letter starts here" (silence, or a letter still being held).

This style of model is trained with CTC (Connectionist Temporal
Classification), which is why the blank exists: a sung "sinta" might come out
frame by frame as

    s s - i i i - - n t a a a a a a -        (- is blank)

and collapsing repeats and removing blanks gives back "sinta".

-------------------------------------------------------------------------------
STEP 2 - VITERBI: "given the words, what is the most likely timing?"
-------------------------------------------------------------------------------

Speech-to-text asks "what was sung?". We already know that, so we ask a
different, easier question: "the letters are s-i-n-t-a, in that order - at
which frame does each one happen?". That is forced alignment.

Picture a grid (the TRELLIS): one column per 20 ms frame, one row per state,
where the states are the lyric's letters with a blank between each pair:

    blank, s, blank, i, blank, n, blank, t, blank, a, blank

A path runs from the left edge to the right edge, moving one column per
frame, and at each step may:
  - stay on the same row           (the letter or pause continues)
  - move down one row              (the next state begins)
  - move down two rows, skipping a blank, when the two letters differ
    (two different letters can follow with no pause in between)

The path's score is the sum of the log-probabilities of the rows it passes
through. Dynamic programming (the Viterbi algorithm) finds the best path
without trying every one: for each cell, only the best way of reaching it
matters, so each column is computed from the previous one. Tracing the
winning path back tells us which frames belong to each letter - and so when
each word starts and ends.

-------------------------------------------------------------------------------
THE WILDCARD
-------------------------------------------------------------------------------

Each line is aligned inside a time window slightly wider than the line,
so the window's edges may contain the END of the previous line or the START
of the next one. Those sounds are not blank, so a plain trellis would be
forced to stretch this line's letters over them. A WILDCARD state at each end
of the line can absorb "some other singing" at a small cost per frame, which
lets the real letters settle where they fit best. (The same idea is called a
"star" token in the ctc-forced-aligner project.)
"""

import numpy as np

from .audio import ALIGN_RATE, FRAME_SECONDS

MODEL_ID = "MahmoudAshraf/mms-300m-1130-forced-aligner"

# The model reads 30-second pieces, each with 2 s of extra audio on both
# sides for context, so words cut by a piece boundary are still heard whole.
CHUNK_SECONDS = 30
CONTEXT_SECONDS = 2

# Cost per frame (in log-probability) of explaining a sound with the
# wildcard instead of a real letter. Small enough to absorb a neighbouring
# line's singing, large enough never to beat this line's own letters.
WILDCARD_PENALTY = 1.5

WILDCARD = -1          # marks a wildcard in a token list
_IMPOSSIBLE = -1e30    # log-probability of a path that cannot happen


class LetterModel:
    """The MMS model, and the letter <-> number mapping it uses."""

    def __init__(self):
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
        vocab = tokenizer.get_vocab()

        self.blank_id = vocab["<blank>"]
        # Only real letters; "'" is left out because it is never pronounced.
        self.letter_ids = {ch: i for ch, i in vocab.items() if len(ch) == 1 and ch.isalpha()}
        self.vocab_size = max(vocab.values()) + 1
        self._model = None

    @property
    def model(self):
        """
        The 1.2 GB network itself, loaded only when first needed - a re-run
        whose letter probabilities are cached never loads it at all.
        """
        if self._model is None:
            import torch
            from transformers import AutoModelForCTC

            torch.set_grad_enabled(False)
            self._model = AutoModelForCTC.from_pretrained(MODEL_ID).eval()
        return self._model

    @property
    def alphabet(self) -> set[str]:
        return set(self.letter_ids)

    def log_probs(self, audio: np.ndarray, report=lambda fraction: None) -> np.ndarray:
        """
        Letter log-probabilities for every 20 ms frame of `audio` (16 kHz).

        Returns an array of shape (frames, vocabulary). Log-probabilities are
        used instead of probabilities because a path's score is a SUM of logs
        rather than a product of many small numbers, which would underflow to 0.
        """
        import torch

        hop = int(ALIGN_RATE * FRAME_SECONDS)                 # 320 samples
        total = len(audio) // hop

        # Frames the model does not cover (the last few samples) default to
        # "certainly blank", which no letter will want to claim.
        out = np.full((total, self.vocab_size), -30.0, dtype=np.float32)
        out[:, self.blank_id] = 0.0

        # The model was trained on audio scaled to mean 0 and spread 1. One
        # scale for the whole song keeps quiet and loud lines comparable.
        mean, std = float(audio.mean()), float(audio.std()) + 1e-7

        chunk = CHUNK_SECONDS * ALIGN_RATE
        context = CONTEXT_SECONDS * ALIGN_RATE
        starts = list(range(0, len(audio), chunk))

        for number, start in enumerate(starts):
            left = max(0, start - context)
            right = min(len(audio), start + chunk + context)
            piece = (audio[left:right] - mean) / std

            logits = self.model(torch.from_numpy(piece.astype(np.float32))[None]).logits[0]
            log_probs = torch.log_softmax(logits, dim=-1).numpy()

            # Keep only this piece's own frames, not its context.
            first = (start - left) // hop
            frame = start // hop
            count = min(chunk // hop, total - frame, len(log_probs) - first)
            if count > 0:
                out[frame:frame + count] = log_probs[first:first + count]
            report((number + 1) / len(starts))

        return out


def viterbi(emissions: np.ndarray, tokens: list[int], blank_id: int) -> np.ndarray | None:
    """
    The best path through the trellis described at the top of this file.

    `emissions` is (frames, vocabulary + 1): the last column holds the
    wildcard's score. `tokens` is the sequence to align, where WILDCARD
    entries may be used or skipped.

    Returns, for each frame, the index of the state the path is in
    (state 2k+1 is token k; even states are blanks) - or None if the window
    is too short to fit every letter.
    """
    frames = emissions.shape[0]
    n = len(tokens)
    states = 2 * n + 1
    if n == 0 or frames < n:
        return None

    wildcard_column = emissions.shape[1] - 1
    # Which emission column each state reads from.
    column = np.full(states, blank_id)
    column[1::2] = [wildcard_column if t == WILDCARD else t for t in tokens]

    # May a state be reached by jumping two rows?
    jump = np.zeros(states, dtype=bool)
    for k in range(1, n):
        # Letter to letter, skipping the blank between: only if they differ,
        # or the model could not tell "aa" from a single long "a".
        jump[2 * k + 1] = tokens[k] != tokens[k - 1]
    for k in range(n):
        # Blank to blank, skipping a wildcard: this is what makes it optional.
        if tokens[k] == WILDCARD:
            jump[2 * k + 2] = True

    # Where a path may begin and end: on the first/last blank or token -
    # or past an optional wildcard at either end.
    starts = [0, 1] + ([2, 3] if tokens[0] == WILDCARD and n > 1 else [])
    ends = [states - 1, states - 2] + (
        [states - 3, states - 4] if tokens[-1] == WILDCARD and n > 1 else [])

    score = np.full(states, _IMPOSSIBLE)
    score[starts] = emissions[0, column[starts]]
    came_from = np.zeros((frames, states), dtype=np.int8)   # 0 stay, 1 step, 2 jump

    for t in range(1, frames):
        stay = score
        step = np.concatenate(([_IMPOSSIBLE], score[:-1]))
        skip = np.concatenate(([_IMPOSSIBLE, _IMPOSSIBLE], score[:-2]))
        skip[~jump] = _IMPOSSIBLE

        options = np.stack([stay, step, skip])
        choice = options.argmax(axis=0)
        score = options[choice, np.arange(states)] + emissions[t, column]
        came_from[t] = choice

    end = max(ends, key=lambda s: score[s])
    if score[end] <= _IMPOSSIBLE / 2:
        return None

    # Walk the winning path backwards from its last frame.
    path = np.empty(frames, dtype=np.int32)
    state = end
    for t in range(frames - 1, -1, -1):
        path[t] = state
        # int(): came_from holds 8-bit numbers; without it, `state` would
        # become an 8-bit number too, and overflow past state 127.
        state -= int(came_from[t, state])
    return path


def align_words(
    log_probs: np.ndarray,
    first_frame: int,
    words: list[str],
    model: LetterModel,
    wildcard_before: set[int] = frozenset(),
) -> list[dict | None] | None:
    """
    Align a list of words (already reduced to letters) to a stretch of audio.

    `log_probs` covers only the window; `first_frame` is where it starts in
    the song, so the times returned are song times. A wildcard is always
    allowed at both ends, and before any word index in `wildcard_before`
    (used between lines when a whole song is aligned at once).

    Returns one entry per word: {"start", "end", "score"} in seconds, or None
    for a word with no letters. Returns None altogether if alignment failed.
    """
    # The wildcard's score per frame: as good as the best letter, minus a
    # fixed penalty (see WILDCARD_PENALTY at the top).
    letter_columns = sorted(model.letter_ids.values())
    wildcard = log_probs[:, letter_columns].max(axis=1, keepdims=True) - WILDCARD_PENALTY
    emissions = np.hstack([log_probs, wildcard])

    tokens = [WILDCARD]
    owner = [None]                        # which word each token belongs to
    for index, word in enumerate(words):
        if index in wildcard_before and index > 0:
            tokens.append(WILDCARD)
            owner.append(None)
        for letter in word:
            tokens.append(model.letter_ids[letter])
            owner.append(index)
    tokens.append(WILDCARD)
    owner.append(None)

    if all(o is None for o in owner):
        return [None] * len(words)

    path = viterbi(emissions, tokens, model.blank_id)
    if path is None:
        return None

    # Gather, per word, the frames on which one of its letters was active.
    word_frames: list[list[int]] = [[] for _ in words]
    for frame, state in enumerate(path):
        if state % 2 == 1:
            word = owner[state // 2]
            if word is not None:
                word_frames[word].append(frame)

    results = []
    for index, frames in enumerate(word_frames):
        if not frames:
            results.append(None)
            continue
        # Confidence: the average probability the model gave to the letters
        # the path assigned, on the frames it assigned them. Near 1 = the
        # model clearly heard these letters here; near 0 = it was forced.
        token_ids = [tokens[path[f] // 2] for f in frames]
        score = float(np.mean(np.exp(log_probs[frames, token_ids])))
        results.append({
            "start": (first_frame + frames[0]) * FRAME_SECONDS,
            "end": (first_frame + frames[-1] + 1) * FRAME_SECONDS,
            "score": score,
        })
    return results
