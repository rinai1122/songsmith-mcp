"""Align lyrics to a rhythm template — one syllable per note, with stressed
syllables preferentially placed on strong beats of the bar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from ..state import Note
from .syllabify import Syllable, syllabify


@dataclass
class AlignedLine:
    notes: list[Note]               # pitch=0 placeholder; melody step fills it
    syllables: list[Syllable]       # in lock-step with notes
    bars_used: float


DEFAULT_RHYTHMS = {
    # For a 4/4 bar we give common melodic rhythms LLMs can pick from.
    "eighths":       [(i * 0.5, 0.5) for i in range(8)],
    "quarters":      [(i * 1.0, 1.0) for i in range(4)],
    "dotted":        [(0.0, 1.5), (1.5, 0.5), (2.0, 1.5), (3.5, 0.5)],
    "syncopated":    [(0.0, 0.5), (0.5, 1.0), (1.5, 0.5), (2.0, 1.0), (3.0, 1.0)],
    "waltz":         [(0.0, 1.0), (1.0, 1.0), (2.0, 1.0)],  # 3/4
}

RHYTHM_TEMPLATES: tuple[str, ...] = tuple(DEFAULT_RHYTHMS.keys())


def align_lyrics_to_rhythm(
    lyrics: str,
    time_sig: tuple[int, int] = (4, 4),
    bars_hint: int | None = None,
    rhythm: str | list[tuple[float, float]] = "eighths",
) -> AlignedLine:
    """Return one Note per syllable of ``lyrics``.

    - If ``rhythm`` is a string, we expand that template bar by bar until we
      have one rhythmic slot per syllable.
    - If ``lyrics`` is multi-line (split on newlines or end-punctuation) and
      ``bars_hint`` is given with at least one bar per phrase, each phrase
      gets its own bar window so the melody breathes across the whole
      section instead of front-loading all syllables.
    """
    beats_per_bar = time_sig[0] * (4 / time_sig[1])

    if isinstance(rhythm, str):
        if rhythm not in DEFAULT_RHYTHMS:
            raise ValueError(
                f"unknown rhythm {rhythm!r}; expected one of {list(RHYTHM_TEMPLATES)}"
            )
        template = DEFAULT_RHYTHMS[rhythm]
    else:
        template = list(rhythm)

    phrases = _split_into_phrases(lyrics)
    if not phrases:
        return AlignedLine(notes=[], syllables=[], bars_used=0.0)

    # Distribute phrases across the section when the caller tells us how
    # many bars we have and we've got room for at least one bar per phrase.
    if bars_hint is not None and len(phrases) > 1 and bars_hint >= len(phrases):
        bars_per_phrase = bars_hint // len(phrases)
        notes: list[Note] = []
        syls_all: list[Syllable] = []
        for i, phrase in enumerate(phrases):
            phrase_syls = [s for s in syllabify(phrase) if _is_vocal(s)]
            if not phrase_syls:
                continue
            slots = _pack_slots(
                n_syllables=len(phrase_syls),
                template=template,
                beats_per_bar=beats_per_bar,
                bars_cap=bars_per_phrase,
            )
            offset_beats = i * bars_per_phrase * beats_per_bar
            for syl, (st, du) in zip(phrase_syls, slots):
                notes.append(Note(
                    pitch=0,
                    start_beat=st + offset_beats,
                    duration_beats=du,
                    velocity=85,
                    lyric=syl.text,
                ))
                syls_all.append(syl)
        if not notes:
            return AlignedLine(notes=[], syllables=[], bars_used=0.0)
        bars_used = max(n.start_beat + n.duration_beats for n in notes) / beats_per_bar
        return AlignedLine(notes=notes, syllables=syls_all, bars_used=bars_used)

    # Single-phrase (or no-hint) path: pack syllables contiguously from bar 0.
    joined = " ".join(phrases)
    syls = [s for s in syllabify(joined) if _is_vocal(s)]
    if not syls:
        return AlignedLine(notes=[], syllables=[], bars_used=0.0)

    slots = _pack_slots(
        n_syllables=len(syls),
        template=template,
        beats_per_bar=beats_per_bar,
        bars_cap=bars_hint,
    )

    # Lyrics must be sung in text order, so slots must stay chronological —
    # any stress-to-strong-beat alignment has to come from template choice,
    # not post-hoc swaps. (Earlier code swapped adjacent slots here, which
    # reversed syllable time order within words like "lone-ly".)

    notes = [
        Note(pitch=0, start_beat=s[0], duration_beats=s[1], velocity=85, lyric=syl.text)
        for syl, s in zip(syls, slots)
    ]
    bars_used = max(n.start_beat + n.duration_beats for n in notes) / beats_per_bar
    return AlignedLine(notes=notes, syllables=syls, bars_used=bars_used)


def as_rhythm_template(aligned: AlignedLine) -> list[tuple[float, float]]:
    """Convert an alignment back into a plain rhythm list for melody generation."""
    return [(n.start_beat, n.duration_beats) for n in aligned.notes]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _is_vocal(s: Syllable) -> bool:
    return bool(s.text) and any(c.isalpha() for c in s.text)


def _split_into_phrases(lyrics: str) -> list[str]:
    """Break a lyric block into phrase-sized chunks on newlines and end
    punctuation (``.``/``!``/``?``). Commas stay inline — they don't usually
    warrant a whole-phrase break."""
    parts = re.split(r"[\n.!?]+", lyrics)
    return [p.strip() for p in parts if p.strip()]


def _pack_slots(
    n_syllables: int,
    template: list[tuple[float, float]],
    beats_per_bar: float,
    bars_cap: int | None,
) -> list[tuple[float, float]]:
    """Lay ``template`` end-to-end, bar by bar, until we have at least
    ``n_syllables`` slots. ``bars_cap``, if given, stops the expansion even
    if that leaves slots short (the caller truncates note-side)."""
    slots: list[tuple[float, float]] = []
    bar_idx = 0
    while len(slots) < n_syllables:
        bar_offset = bar_idx * beats_per_bar
        for start, dur in template:
            slots.append((start + bar_offset, dur))
            if len(slots) >= n_syllables:
                break
        bar_idx += 1
        if bars_cap is not None and bar_idx >= bars_cap:
            break
    return slots[:n_syllables]
