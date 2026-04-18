"""Syllabify lyrics with ``pyphen`` + crude stress heuristics.

The LLM authors the words; this tool decomposes them into (syllable, stress)
pairs so rhythm alignment can place stressed syllables on strong beats.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pyphen


@dataclass
class Syllable:
    text: str
    stress: int        # 0 unstressed, 1 secondary, 2 primary
    is_word_start: bool
    word: str


_DIC = pyphen.Pyphen(lang="en_US")

# Very cheap English-stress heuristic: for multi-syllable words, stress falls
# on the first syllable unless the word looks like a verb with a prefix. We
# keep this simple on purpose — good enough to bias the aligner; a future
# upgrade could call CMUdict.
_VERB_PREFIXES = ("re", "un", "be", "de", "pre", "mis", "over", "under", "out")


def syllabify(text: str) -> list[Syllable]:
    """Split ``text`` into syllables with stress markers."""
    out: list[Syllable] = []
    for word in _tokenize(text):
        core = word.lower().strip("'\".,!?;:()[]-–—")
        if not core.isalpha():
            # Keep punctuation as a zero-stress marker so rhythm can breathe.
            if word.strip():
                out.append(Syllable(text=word, stress=0, is_word_start=True, word=word))
            continue
        syls = _DIC.inserted(core).split("-") if core else [core]
        if not syls or syls == [""]:
            syls = [core]
        stressed = _primary_stress_index(syls, core)
        for i, s in enumerate(syls):
            stress = 2 if i == stressed else (1 if len(syls) > 2 and i == 0 and stressed != 0 else 0)
            out.append(
                Syllable(
                    text=s,
                    stress=stress,
                    is_word_start=(i == 0),
                    word=word,
                )
            )
    return out


def count_syllables(text: str) -> int:
    """How many singable syllables does ``text`` contain?"""
    return sum(1 for s in syllabify(text) if s.text and any(c.isalpha() for c in s.text))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    # Preserve punctuation as its own tokens so rhythm can insert rests.
    return [t for t in re.findall(r"[A-Za-z']+|[.,!?;:]", text) if t]


def _primary_stress_index(syls: list[str], word: str) -> int:
    if len(syls) == 1:
        return 0
    # Words ending in -tion, -sion, -ity → stress on the syllable before it.
    for suffix in ("tion", "sion", "ity", "ical", "ic"):
        if word.endswith(suffix) and len(syls) >= 2:
            return max(0, len(syls) - 2)
    if word.startswith(_VERB_PREFIXES) and len(syls) >= 2:
        return 1
    return 0
