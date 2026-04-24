"""Melody generation.

Rules-based for now (per plan §Open questions): snap to chord tones on
strong beats, fill with scale tones, bias motion by an explicit contour
curve, respect a singable range and maximum-leap limit.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

from music21 import key as m21_key
from music21 import pitch as m21_pitch
from music21 import scale as m21_scale

from ..state import Clip, Note


Contour = Literal["arch", "descending", "ascending", "wave", "flat"]

MELODY_CONTOURS: tuple[str, ...] = ("arch", "descending", "ascending", "wave", "flat")


@dataclass
class MelodyCandidate:
    notes: list[Note]
    summary: str      # e.g. "arch contour, 12 notes, range A3–E5"


def propose_melody(
    key_str: str,
    chords_by_beat: dict[float, list[int]],
    rhythm: list[tuple[float, float]],  # list of (start_beat, duration_beats)
    contour: Contour = "arch",
    vocal_range: tuple[int, int] = (57, 76),  # A3 to E5 — comfortable alto/tenor
    max_leap: int = 7,                        # semitones
    seed: int | None = None,
    strong_beat_interval: float = 1.0,        # every beat is strong; set to 2.0 for half-notes
) -> MelodyCandidate:
    """Generate a melody whose rhythm is given and whose pitches follow the
    contour, snapping to chord tones on strong beats.

    If rhythm is empty, nothing is generated.
    """
    if contour not in MELODY_CONTOURS:
        raise ValueError(
            f"unknown melody contour {contour!r}; expected one of {list(MELODY_CONTOURS)}"
        )
    rng = random.Random(seed)
    if not rhythm:
        return MelodyCandidate(notes=[], summary="empty rhythm — no melody generated")

    # Scale from key.
    k = _parse_key(key_str)
    scl = k.getScale()
    scale_pcs = sorted({p.pitchClass for p in scl.getPitches("C1", "C6")})

    # Contour over [0, 1] → target MIDI pitch.
    lo, hi = vocal_range
    total = rhythm[-1][0] + rhythm[-1][1]

    def target(t: float) -> int:
        x = t / total if total > 0 else 0
        if contour == "ascending":
            return int(lo + x * (hi - lo))
        if contour == "descending":
            return int(hi - x * (hi - lo))
        if contour == "flat":
            return (lo + hi) // 2
        if contour == "wave":
            import math
            return int((lo + hi) / 2 + ((hi - lo) / 3) * math.sin(2 * math.pi * x))
        # arch (default)
        import math
        return int(lo + (hi - lo) * math.sin(math.pi * x))

    # Realize.
    chord_beats = sorted(chords_by_beat.keys())
    notes: list[Note] = []
    prev_pitch: int | None = None

    for start, dur in rhythm:
        chord_start = max((b for b in chord_beats if b <= start), default=chord_beats[0] if chord_beats else None)
        chord = chords_by_beat.get(chord_start, []) if chord_start is not None else []
        tgt = target(start)

        on_strong = _is_strong(start, strong_beat_interval)
        if on_strong and chord:
            pool = [p % 12 for p in chord]
        else:
            pool = scale_pcs

        pitch = _choose_pitch(
            tgt, pool, prev_pitch=prev_pitch, max_leap=max_leap, rng=rng,
            range_lo=lo, range_hi=hi,
        )
        notes.append(Note(pitch=pitch, start_beat=start, duration_beats=dur, velocity=85))
        prev_pitch = pitch

    pitches = [n.pitch for n in notes]
    lo_used, hi_used = min(pitches), max(pitches)
    summary = (
        f"{contour} contour, {len(notes)} notes, range "
        f"{m21_pitch.Pitch(midi=lo_used).nameWithOctave}"
        f"–{m21_pitch.Pitch(midi=hi_used).nameWithOctave}"
    )
    return MelodyCandidate(notes=notes, summary=summary)


def build_melody_clip(
    candidate: MelodyCandidate,
    section_name: str,
    track_name: str,
    start_bar: int,
    length_bars: int,
) -> Clip:
    return Clip(
        track=track_name,
        section=section_name,
        notes=list(candidate.notes),
        start_bar=start_bar,
        length_bars=length_bars,
    )


def humanize(
    notes: list[Note],
    timing_jitter_ticks: float = 8.0,  # expressed in fractional beats
    velocity_jitter: int = 8,
    seed: int | None = None,
) -> list[Note]:
    """Return a new note list with small random micro-timing/velocity drift."""
    rng = random.Random(seed)
    out: list[Note] = []
    for n in notes:
        dt = rng.uniform(-timing_jitter_ticks, timing_jitter_ticks) / 480.0
        dv = rng.randint(-velocity_jitter, velocity_jitter)
        out.append(
            Note(
                pitch=n.pitch,
                start_beat=max(0.0, n.start_beat + dt),
                duration_beats=n.duration_beats,
                velocity=max(1, min(127, n.velocity + dv)),
                lyric=n.lyric,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _parse_key(key_str: str) -> m21_key.Key:
    # Reuse chord module's robust parser.
    from .chords import _parse_key as _pk
    return _pk(key_str)


def _is_strong(beat: float, interval: float) -> bool:
    """A beat is 'strong' if it falls on a multiple of ``interval``."""
    ratio = beat / interval
    return abs(ratio - round(ratio)) < 1e-3


def _choose_pitch(
    target: int,
    pool_pc: list[int],
    prev_pitch: int | None,
    max_leap: int,
    rng: random.Random,
    range_lo: int,
    range_hi: int,
) -> int:
    """Pick the MIDI pitch whose pitch class ∈ pool_pc and whose octave
    position is closest to ``target`` — but not more than ``max_leap``
    semitones from ``prev_pitch``, and always inside ``[range_lo, range_hi]``.

    The range filter happens during candidate generation, not after, so
    clamping can never smuggle a chromatic pitch-class into the melody
    (e.g. clamping an out-of-range C to a ``range_hi`` of 85 used to
    produce a C# that wasn't in the chosen pool).
    """
    if range_hi < range_lo:
        range_lo, range_hi = range_hi, range_lo
    if not pool_pc:
        pool_pc = list(range(12))

    # Generate every in-pool pitch inside the range, then rank by distance.
    candidates: list[int] = []
    for pc in pool_pc:
        # Walk every octave whose pitch class == pc and keep the ones in range.
        p = pc + 12 * max(0, (range_lo - pc) // 12)
        while p <= range_hi:
            if p >= range_lo:
                candidates.append(p)
            p += 12

    # Fallback: no pool pitch fits in the range (very narrow vocal_range with
    # a sparse scale). Grab the closest in-pool pitch outside the range; it's
    # better to drift a little than to emit an out-of-pool chromatic note.
    if not candidates:
        for pc in pool_pc:
            for k in range(-2, 3):
                candidates.append(pc + 12 * ((range_lo // 12) + k))

    clamped_target = max(range_lo, min(range_hi, target))

    def score(c: int) -> float:
        s = abs(c - clamped_target)
        if prev_pitch is not None:
            leap = abs(c - prev_pitch)
            if leap > max_leap:
                s += 100
            s += 0.3 * leap
        return s

    candidates.sort(key=score)
    top = candidates[:3]
    return rng.choice(top)
