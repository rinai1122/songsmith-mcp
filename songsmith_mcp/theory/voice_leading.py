"""Voice-leading utilities: revoice block chords so adjacent voicings move
smoothly, and produce backing-voice harmonization for a melody line.
"""

from __future__ import annotations

from typing import Literal

from ..state import Clip, Note


Voicing = Literal["close", "open", "drop2", "drop3", "spread"]


def revoice_clip(clip: Clip, style: Voicing = "close", center: int = 60) -> Clip:
    """Return a new clip whose chord voicings have been rewritten to minimize
    voice-leading movement between consecutive chords.

    Input: a clip containing *block chords* (multiple notes starting at the
    same beat, same duration).
    """
    # Group notes by (start_beat, duration) → chord.
    groups: dict[tuple[float, float], list[Note]] = {}
    for n in clip.notes:
        groups.setdefault((n.start_beat, n.duration_beats), []).append(n)
    chord_times = sorted(groups.keys())

    new_notes: list[Note] = []
    prev_voicing: list[int] | None = None

    for t in chord_times:
        original = groups[t]
        pitch_classes = sorted({p.pitch % 12 for p in original})
        # Style shapes the starting voicing.
        voicing = _initial_voicing(pitch_classes, style, center=center)
        if prev_voicing is not None:
            voicing = _min_movement(prev_voicing, pitch_classes, style, center=center)
        prev_voicing = voicing
        for p in voicing:
            new_notes.append(
                Note(
                    pitch=p,
                    start_beat=t[0],
                    duration_beats=t[1],
                    velocity=original[0].velocity,
                )
            )

    return Clip(
        track=clip.track,
        section=clip.section,
        notes=new_notes,
        start_bar=clip.start_bar,
        length_bars=clip.length_bars,
        chord_symbol=clip.chord_symbol,
    )


def harmonize_line(
    melody: list[Note],
    chord_at_beat: dict[float, list[int]],
    voices: int = 2,
    interval_preference: tuple[int, ...] = (3, 4, 5),  # thirds, fourths, fifths below
) -> list[list[Note]]:
    """Return ``voices`` parallel lines below ``melody``, snapping each backing
    pitch to the chord tone closest to the preferred interval below.

    ``chord_at_beat`` maps start_beat → MIDI pitches of the chord tone pool.
    """
    out: list[list[Note]] = [[] for _ in range(voices)]
    chord_beats = sorted(chord_at_beat.keys())

    for note in melody:
        chord_start = max((b for b in chord_beats if b <= note.start_beat), default=None)
        pool = chord_at_beat[chord_start] if chord_start is not None else []
        if not pool:
            continue
        for v in range(voices):
            target = note.pitch - interval_preference[v % len(interval_preference)] - v * 3
            best = min(pool, key=lambda p: abs((p % 12) - (target % 12)) + abs(p - target) / 12)
            # Octave-shift to sit just below the target.
            while best > target + 2:
                best -= 12
            while best < target - 10:
                best += 12
            out[v].append(
                Note(
                    pitch=best,
                    start_beat=note.start_beat,
                    duration_beats=note.duration_beats,
                    velocity=max(40, note.velocity - 15),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _initial_voicing(pcs: list[int], style: Voicing, center: int = 60) -> list[int]:
    """Build a first voicing for a pitch-class set in the given style."""
    if not pcs:
        return []
    root = pcs[0]
    midi = [center + ((pc - center) % 12) for pc in pcs]
    midi.sort()

    if style == "close":
        return _compact(midi, center)
    if style == "open":
        if len(midi) >= 3:
            midi[1] += 12  # spread the middle voice up an octave
        return _compact(midi, center)
    if style == "drop2":
        if len(midi) >= 4:
            midi[-2] -= 12
        return _compact(midi, center)
    if style == "drop3":
        if len(midi) >= 4:
            midi[-3] -= 12
        return _compact(midi, center)
    if style == "spread":
        midi = [m + 12 * i for i, m in enumerate(midi)]
        return _compact(midi, center)
    return _compact(midi, center)


def _compact(midi: list[int], center: int = 60) -> list[int]:
    """Shift the whole voicing so its mean sits near ``center``."""
    if not midi:
        return []
    mean = sum(midi) / len(midi)
    shift = 0
    while mean + shift < center - 6:
        shift += 12
    while mean + shift > center + 6:
        shift -= 12
    return sorted(m + shift for m in midi)


def _min_movement(
    prev: list[int], pcs: list[int], style: Voicing, center: int = 60
) -> list[int]:
    """Map each pitch class in ``pcs`` to whichever octave is closest to
    *some* voice in ``prev`` (greedy, with no repeats).
    """
    if not prev or not pcs:
        return _initial_voicing(pcs, style, center=center)

    used_prev: set[int] = set()
    voiced: list[int] = []
    remaining_pcs = list(pcs)

    for pc in remaining_pcs:
        # candidate octaves near each previous voice
        best_note = None
        best_cost = 10_000
        for i, pv in enumerate(prev):
            if i in used_prev:
                continue
            # find the octave of `pc` closest to pv
            candidate = pv + ((pc - pv) % 12)
            if candidate - pv > 6:
                candidate -= 12
            cost = abs(candidate - pv)
            if cost < best_cost:
                best_cost = cost
                best_note = candidate
                best_i = i
        if best_note is None:
            # fall back: land near center
            best_note = center + ((pc - center) % 12)
        else:
            used_prev.add(best_i)
        voiced.append(best_note)
    voiced.sort()
    return voiced
