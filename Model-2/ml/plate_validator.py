"""
Indian license plate validation and correction.

Indian plate format:  XX 00 XX 0000
  - 2 letters:  State code (GJ, MH, DL, RJ, etc.)
  - 1-2 digits: District/RTO code (1-99)
  - 1-2 letters: Series code (A-Z, AA-ZZ)
  - 1-4 digits: Registration number (1-9999)

Example: GJ 14 DX 5823, MH 02 AB 1234, DL 8 C 4567
"""

from __future__ import annotations

import re
from typing import Optional

# All valid Indian state/UT codes
INDIAN_STATE_CODES = {
    "AN", "AP", "AR", "AS", "BR", "CG", "CH", "DD", "DL", "GA",
    "GJ", "HP", "HR", "JH", "JK", "KA", "KL", "LA", "LD", "MH",
    "ML", "MN", "MP", "MZ", "NL", "OD", "OR", "PB", "PY", "RJ",
    "SK", "TN", "TR", "TS", "UK", "UP", "WB",
}

# Common OCR character confusions: what the OCR reads → what it should be
# Context-dependent: letters in letter positions, digits in digit positions
_LETTER_FIXES = {
    "0": "O", "1": "I", "2": "Z", "5": "S", "8": "B",
}
_DIGIT_FIXES = {
    "O": "0", "I": "1", "Z": "2", "S": "5", "B": "8",
    "D": "0", "G": "6", "T": "7", "A": "4", "Q": "0",
}

# For state code correction: letter→letter confusions that produce wrong state codes
# e.g. GI is not a state, but GJ is (I↔J confusion is very common)
_STATE_LETTER_ALTS = {
    "I": ["J", "L"],  # I↔J (most common), I↔L
    "J": ["I"],        # J↔I
    "U": ["V"],        # U↔V
    "V": ["U"],
    "C": ["G"],        # C↔G
    "G": ["C"],
    "E": ["F"],        # E↔F
    "F": ["E"],
}

# Regex for a valid Indian plate after cleaning
# State(2) + District(1-2 digits) + Series(1-2 letters) + Number(1-4 digits)
_PLATE_PATTERN = re.compile(
    r"^([A-Z]{2})"       # State code
    r"(\d{1,2})"         # District/RTO
    r"([A-Z]{1,3})"      # Series (some states use 3 letters)
    r"(\d{1,4})$"        # Registration number
)


def _fix_char(ch: str, expect_digit: bool) -> str:
    """Apply contextual OCR correction for a single character."""
    if expect_digit:
        return _DIGIT_FIXES.get(ch, ch)
    else:
        return _LETTER_FIXES.get(ch, ch)


def correct_plate(raw: str) -> Optional[str]:
    """
    Try to correct common OCR errors in an Indian plate string.

    Applies context-dependent character fixes based on where letters vs digits
    are expected in the Indian plate format, then validates against the pattern.

    Returns the corrected plate string if it matches, or None if unrecoverable.
    """
    if not raw or len(raw) < 6:
        return None

    text = re.sub(r"[^A-Z0-9]", "", raw.upper())
    if len(text) < 6:
        return None

    # Strategy: try multiple interpretations of where the letter/digit
    # boundaries fall and see which one produces a valid plate.

    best = None

    for district_len in (1, 2):
        for series_len in (1, 2, 3):
            # State is always first 2 chars
            min_len = 2 + district_len + series_len + 1  # at least 1 digit at end
            if len(text) < min_len:
                continue

            state_end = 2
            district_end = state_end + district_len
            series_end = district_end + series_len

            # Apply contextual corrections
            corrected = []
            for i, ch in enumerate(text):
                if i < state_end:
                    corrected.append(_fix_char(ch, expect_digit=False))
                elif i < district_end:
                    corrected.append(_fix_char(ch, expect_digit=True))
                elif i < series_end:
                    corrected.append(_fix_char(ch, expect_digit=False))
                else:
                    corrected.append(_fix_char(ch, expect_digit=True))

            candidate = "".join(corrected)
            m = _PLATE_PATTERN.match(candidate)
            if m:
                state = m.group(1)
                # If state code isn't valid, try common OCR corrections on it
                if state not in INDIAN_STATE_CODES:
                    # Try fixing each char in the state code using letter alternatives
                    s0, s1 = state[0], state[1]
                    alts0 = [s0] + _STATE_LETTER_ALTS.get(s0, []) + [_LETTER_FIXES.get(s0, s0)]
                    alts1 = [s1] + _STATE_LETTER_ALTS.get(s1, []) + [_LETTER_FIXES.get(s1, s1)]
                    found = False
                    for c0 in alts0:
                        for c1 in alts1:
                            if c0 + c1 in INDIAN_STATE_CODES:
                                candidate = c0 + c1 + candidate[2:]
                                state = c0 + c1
                                found = True
                                break
                        if found:
                            break

                if state in INDIAN_STATE_CODES:
                    score = len(m.group(4))
                    if best is None or score > best[1]:
                        best = (candidate, score)

    return best[0] if best else None


def validate_plate(raw: str) -> Optional[str]:
    """
    Validate and optionally correct an Indian license plate.

    Returns:
        The cleaned/corrected plate if valid, or None if it doesn't match
        the Indian plate format even after correction attempts.
    """
    if not raw:
        return None

    text = re.sub(r"[^A-Z0-9]", "", raw.upper())

    # First check if it already matches perfectly
    m = _PLATE_PATTERN.match(text)
    if m and m.group(1) in INDIAN_STATE_CODES:
        return text

    # Try correction
    return correct_plate(text)
