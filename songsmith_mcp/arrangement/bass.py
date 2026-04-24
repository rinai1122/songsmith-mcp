"""Bassline generators keyed off a chord progression."""

from __future__ import annotations

import random
from typing import Iterable

from music21 import key as m21_key
from music21 import pitch as m21_pitch

from ..state import Clip, Note


BASS_STYLES = ("roots", "root_fifth", "walking", "syncopated", "arp")


def write_bassline(
    chords_by_beat: dict[float, list[int]],
    section_name: str,
    track_name: str,
    key_str: str,
    style: str = "roots",     # "roots" | "root_fifth" | "walking" | "syncopated" | "arp"
    time_sig: tuple[int, int] = (4, 4),
    start_bar: int = 0,
    bars: int | None = None,
    seed: int | None = None,
) -> Clip:
    """Produce a bass clip in the chosen style."""
    if style not in BASS_STYLES:
        raise ValueError(
            f"unknown bass style {style!r}; expected one of {list(BASS_STYLES)}"
        )
    rng = random.Random(seed)
    beats_per_bar = time_sig[0] * (4 / time_sig[1])

    chord_beats = sorted(chords_by_beat.keys())
    if not chord_beats:
        return Clip(track=track_name, section=section_name, notes=[], start_bar=start_bar, length_bars=0)

    if bars is None:
        total_beats = max(chord_beats) + beats_per_bar
    else:
        total_beats = bars * beats_per_bar

    notes: list[Note] = []
    for i, cb in enumerate(chord_beats):
        next_cb = chord_beats[i + 1] if i + 1 < len(chord_beats) else total_beats
        chord = chords_by_beat[cb]
        root = _octave_down(chord[0], target=40)  # around E2
        fifth = _octave_down(chord[min(2, len(chord) - 1)], target=root + 7)

        if style == "roots":
            notes.append(Note(pitch=root, start_beat=cb, duration_beats=next_cb - cb, velocity=95))
        elif style == "root_fifth":
            half = (next_cb - cb) / 2
            notes.append(Note(pitch=root, start_beat=cb, duration_beats=half, velocity=95))
            notes.append(Note(pitch=fifth, start_beat=cb + half, duration_beats=next_cb - cb - half, velocity=90))
        elif style == "walking":
            # Four quarter-notes: root, scale-up, fifth, approach-tone.
            if i + 1 < len(chord_beats):
                next_root_src = chords_by_beat[chord_beats[i + 1]][0]
            else:
                next_root_src = chord[0]
            approach = _approach_tone(root, next_root=_octave_down(next_root_src, target=40))
            steps = [root, root + 2, fifth, approach]
            for j, p in enumerate(steps):
                notes.append(Note(pitch=p, start_beat=cb + j, duration_beats=1.0, velocity=88))
        elif style == "syncopated":
            # 1 & 2& rhythm: root(1) — root(1.5) — fifth(2.5) — root(3.5)
            for t, p in [(0.0, root), (0.5, root), (1.5, fifth), (2.5, root)]:
                if cb + t < next_cb:
                    notes.append(Note(pitch=p, start_beat=cb + t, duration_beats=0.5, velocity=95))
        elif style == "arp":
            arps = chord[:3] if len(chord) >= 3 else chord
            arps = [_octave_down(p, target=root) for p in arps] + [root]
            dur = (next_cb - cb) / len(arps)
            for j, p in enumerate(arps):
                notes.append(Note(pitch=p, start_beat=cb + j * dur, duration_beats=dur, velocity=90))

    length_bars = int(total_beats / beats_per_bar)
    return Clip(
        track=track_name,
        section=section_name,
        notes=notes,
        start_bar=start_bar,
        length_bars=length_bars,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _octave_down(midi: int, target: int) -> int:
    """Drop ``midi`` into bass range near ``target``."""
    while midi - target > 6:
        midi -= 12
    while midi - target < -6:
        midi += 12
    return midi


def _approach_tone(current_root: int, next_root: int) -> int:
    """Half-step or whole-step approach toward ``next_root`` from above/below."""
    if next_root > current_root:
        return next_root - 1
    if next_root < current_root:
        return next_root + 1
    return current_root + 2
