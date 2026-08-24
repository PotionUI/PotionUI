from difflib import Differ
from typing import List, Optional, Tuple

WordDiff = List[Tuple[str, Optional[str]]]


def word_diff(text1: str, text2: str) -> WordDiff:
    """
    Diff two strings word-by-word (rather than line-by-line), preserving spaces
    as separate tokens so whitespace changes are visible in the diff too.

    Internally diffs character-by-character (so partial-word edits aren't lost),
    then regroups the result into words and spaces, carrying forward the '+'/'-'
    marker for tokens that changed. Unmarked (unchanged) tokens get `None`.

    Returns a list of (token, marker) pairs where marker is "+", "-", or None.
    """
    d = Differ()
    char_diff = list(d.compare(text1, text2))

    result: WordDiff = []
    current_word = ""
    current_marker = None

    for item in char_diff:
        marker = item[0]
        char = item[2] if len(item) > 2 else ""

        # If we encounter a different marker or a space, flush the current word
        if (marker != current_marker and current_word) or (char.isspace() and current_word):
            if current_word:
                result.append((current_word, current_marker if current_marker != " " else None))
            current_word = ""

        # If it's a space, add it as a separate token
        if char.isspace():
            result.append((char, marker if marker != " " else None))
        else:
            # Start new word or continue current word
            if not current_word:
                current_marker = marker
            current_word += char

    # Add the last word if there is one
    if current_word:
        result.append((current_word, current_marker if current_marker != " " else None))

    return result
