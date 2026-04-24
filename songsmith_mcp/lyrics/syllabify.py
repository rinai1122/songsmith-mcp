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
        # Pyphen is a hyphenation dictionary, not a syllable splitter — it
        # under-splits short common words (e.g. "city", "tonight", "many").
        # If it returns a single syllable but the word clearly has ≥2 vowel
        # groups, fall back to a vowel-group heuristic.
        if len(syls) == 1 and _vowel_group_count(core) >= 2:
            syls = _vowel_group_split(core)
        # Pyphen also sometimes fuses a hiatus into one chunk (e.g. "pia-no"
        # or "ra-dio"); re-split any syllable that internally contains a
        # hiatus boundary.
        refined: list[str] = []
        for s in syls:
            if len(_vowel_groups(s)) >= 2:
                refined.extend(_vowel_group_split(s))
            else:
                refined.append(s)
        syls = refined
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


_VOWELS = set("aeiouy")

# Consecutive-vowel pairs that are almost always pronounced as two
# syllables (hiatus), not one (diphthong). Without splitting these we'd
# under-count words like "neon", "lion", "create", "piano".
_HIATUS_PAIRS = frozenset({
    "ia", "ie", "io", "iu",
    "eo",
    "ua", "ue", "ui", "uo",
})


def _vowel_groups(word: str) -> list[tuple[int, int]]:
    """Return [(start, end)] of vowel runs in ``word``, split at hiatus
    boundaries so e.g. ``neon`` -> [(1,2), (2,3)] (two syllables), not
    ``[(1,3)]`` (one)."""
    w = word.lower()
    groups: list[tuple[int, int]] = []
    i = 0
    while i < len(w):
        if w[i] in _VOWELS:
            # 'u' following 'q' is part of the /kw/ digraph, not a vowel
            # nucleus — 'quiet' is qui-et (2), not qu-i-et (3).
            if w[i] == "u" and i > 0 and w[i - 1] == "q":
                i += 1
                continue
            j = i
            while j < len(w) and w[j] in _VOWELS:
                j += 1
            # Split this vowel run at hiatus pair boundaries.
            run_start = i
            k = i
            while k < j - 1:
                pair = w[k] + w[k + 1]
                if pair in _HIATUS_PAIRS:
                    groups.append((run_start, k + 1))
                    run_start = k + 1
                k += 1
            groups.append((run_start, j))
            i = j
        else:
            i += 1
    # Silent terminal 'e' after a consonant (e.g. "lone", "fire") doesn't
    # form its own syllable. 'le' endings ("apple", "little") do.
    if (
        groups
        and groups[-1] == (len(w) - 1, len(w))
        and w[-1] == "e"
        and len(w) >= 2
        and w[-2] not in _VOWELS
        and w[-2] != "l"
    ):
        groups.pop()
    return groups


def _vowel_group_count(word: str) -> int:
    n = len(_vowel_groups(word))
    return n if n > 0 else 1


def _vowel_group_split(word: str) -> list[str]:
    """Split ``word`` between consecutive vowel groups using a V-CV / VC-CV
    heuristic. Only called as a fallback when pyphen under-splits."""
    groups = _vowel_groups(word)
    if len(groups) < 2:
        return [word]
    pieces: list[str] = []
    prev = 0
    for k in range(len(groups) - 1):
        _, end_k = groups[k]
        start_next, _ = groups[k + 1]
        gap = start_next - end_k
        if gap <= 1:
            # V-V or V-CV: consonant (if any) attaches to next vowel.
            boundary = end_k
        else:
            # VC-CV / VCC-V: split in the middle of the consonant cluster.
            boundary = end_k + gap // 2
        pieces.append(word[prev:boundary])
        prev = boundary
    pieces.append(word[prev:])
    return [p for p in pieces if p]


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
