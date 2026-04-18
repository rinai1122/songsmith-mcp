"""Align lyrics to a rhythm template — one syllable per note, with stressed
syllables preferentially placed on strong beats of the bar.
"""

from __future__ import annotations

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


def align_lyrics_to_rhythm(
    lyrics: str,
    time_sig: tuple[int, int] = (4, 4),
    bars_hint: int | None = None,
    rhythm: str | list[tuple[float, float]] = "eighths",
) -> AlignedLine:
    """Return one Note per syllable of ``lyrics``.

    - If ``rhythm`` is a string, we expand that template bar by bar until we
      have one rhythmic slot per syllable.
    - Stressed syllables that would land on an off-beat get swapped with an
      adjacent slot when feasible, so stresses line up with beats 1 and 3.
    """
    syls = [s for s in syllabify(lyrics) if _is_vocal(s)]
    if not syls:
        return AlignedLine(notes=[], syllables=[], bars_used=0.0)

    beats_per_bar = time_sig[0] * (4 / time_sig[1])

    if isinstance(rhythm, str):
        template = DEFAULT_RHYTHMS.get(rhythm, DEFAULT_RHYTHMS["eighths"])
    else:
        template = list(rhythm)

    slots: list[tuple[float, float]] = []
    bar_idx = 0
    while len(slots) < len(syls):
        bar_offset = bar_idx * beats_per_bar
        for start, dur in template:
            slots.append((start + bar_offset, dur))
            if len(slots) >= len(syls):
                break
        bar_idx += 1
        if bars_hint is not None and bar_idx >= bars_hint and len(slots) >= len(syls):
            break

    # Swap stress/off-beat misalignments where a cheap local swap fixes it.
    for i, syl in enumerate(syls):
        if syl.stress >= 2 and not _is_on_strong_beat(slots[i][0]):
            # Try swapping with neighbours that are on strong beats.
            for j in (i - 1, i + 1):
                if 0 <= j < len(slots) and _is_on_strong_beat(slots[j][0]) and syls[j].stress < 2:
                    slots[i], slots[j] = slots[j], slots[i]
                    break

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


def _is_on_strong_beat(beat: float) -> bool:
    """Strong beats are beats 1 and 3 of a 4/4 bar (⇒ even integer beat index)."""
    beat_in_bar = beat % 4
    return abs(beat_in_bar - round(beat_in_bar)) < 1e-3 and int(round(beat_in_bar)) % 2 == 0
