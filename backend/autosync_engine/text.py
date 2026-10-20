"""
Turning a lyric word into the letters the alignment model knows.

The MMS alignment model's alphabet is just a-z (plus an apostrophe we do not
use). So every word is reduced to those letters:

    "Naaalala"      -> "naaalala"
    "pag-iibigan?"  -> "pagiibigan"     hyphen and punctuation dropped
    "kulita't"      -> "kulitat"        apostrophes are never pronounced
    "Niño"          -> "nino"           accents removed (ñ -> n)
    "(huwag"        -> "huwag"          backing-vocal brackets dropped

Tagalog is written the way it sounds, which suits letter-by-letter alignment
very well. English spelling is less regular, but the model has heard enough
English to cope. Because only letters matter, a line mixing both languages
needs no special handling at all.

A "word" with no letters left - a lone dash, a number such as "2" - cannot
be aligned. It is kept, so the lyric text never changes, and given the time
of its neighbour by postprocess.py.
"""

import unicodedata


def letters_only(word: str, alphabet: set[str]) -> str:
    """Reduce one lyric word to the characters in `alphabet`."""
    # NFKD splits "ñ" into "n" + a combining tilde, so dropping combining
    # marks removes accents while keeping the base letter.
    decomposed = unicodedata.normalize("NFKD", word)
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return "".join(ch for ch in plain.lower() if ch in alphabet)


def split_line(text: str) -> list[str]:
    """
    The words of a line, exactly as the sync editor splits them (on
    whitespace). Keeping the SAME split is what lets the editor load the
    result: it only accepts word timings whose words match its own.
    """
    return text.split()
