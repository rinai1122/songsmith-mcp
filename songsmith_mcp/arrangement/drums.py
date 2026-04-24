"""GM-drum pattern generator. MIDI note numbers follow General MIDI drum map
on channel 10 — the default kit map REAPER's built-in ReaDrums/ReaSynth/any
GM-compatible VST will understand.
"""

from __future__ import annotations

from ..state import Clip, Note


# Standard GM drum note numbers.
KICK = 36
SNARE = 38
CLAP = 39
CLOSED_HAT = 42
OPEN_HAT = 46
RIDE = 51
CRASH = 49
TOM_LO = 45
TOM_HI = 50


# A style pattern is expressed as beats-within-a-bar (4/4 assumed here).
DRUM_INTENSITIES = ("light", "normal", "heavy")


_STYLES = {
    "rock": {
        KICK:       [0.0, 2.0],
        SNARE:      [1.0, 3.0],
        CLOSED_HAT: [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
    },
    "pop": {
        KICK:       [0.0, 2.0, 2.5],
        SNARE:      [1.0, 3.0],
        CLOSED_HAT: [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
    },
    "ballad": {
        KICK:       [0.0, 2.0],
        SNARE:      [2.0],
        CLOSED_HAT: [0.0, 1.0, 2.0, 3.0],
    },
    "halftime": {
        KICK:       [0.0],
        SNARE:      [2.0],
        CLOSED_HAT: [0.0, 1.0, 2.0, 3.0],
    },
    "edm": {
        KICK:       [0.0, 1.0, 2.0, 3.0],
        CLAP:       [1.0, 3.0],
        CLOSED_HAT: [0.5, 1.5, 2.5, 3.5],
    },
    "hiphop": {
        KICK:       [0.0, 1.5, 2.5],
        SNARE:      [1.0, 3.0],
        CLOSED_HAT: [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5],
    },
    "jazz_swing": {
        KICK:       [0.0, 2.0],
        RIDE:       [0.0, 0.66, 1.0, 2.0, 2.66, 3.0],
        SNARE:      [1.0, 3.0],
    },
}


DRUM_STYLES = tuple(_STYLES.keys())


def write_drum_pattern(
    section_name: str,
    track_name: str,
    style: str = "pop",
    intensity: str = "normal",   # "light" | "normal" | "heavy"
    bars: int = 4,
    start_bar: int = 0,
    time_sig: tuple[int, int] = (4, 4),
    fill: bool = False,
    add_crash: bool | None = None,
) -> Clip:
    """Return a Clip filled with ``bars`` copies of the chosen style pattern.

    ``intensity`` reshapes the pattern, not just the velocity: ``light`` thins
    the hi-hat to quarter notes and drops syncopated kicks; ``heavy`` adds
    open hats on the & of 2 and 4 and a quiet ghost snare on the last 16ths.
    ``fill=True`` replaces the last bar's second half with a tom fill — pass
    it in the bar before a chorus/drop so the section actually lifts.
    """
    style_key = style.lower()
    if style_key not in _STYLES:
        raise ValueError(
            f"unknown drum style {style!r}; expected one of {list(DRUM_STYLES)}"
        )
    if intensity not in DRUM_INTENSITIES:
        raise ValueError(
            f"unknown drum intensity {intensity!r}; expected one of {list(DRUM_INTENSITIES)}"
        )
    base_pattern = _STYLES[style_key]
    pattern = _apply_intensity(base_pattern, intensity)
    beats_per_bar = time_sig[0] * (4 / time_sig[1])

    base_vel = {"light": 70, "normal": 95, "heavy": 115}.get(intensity, 95)
    if add_crash is None:
        add_crash = intensity != "light"

    notes: list[Note] = []
    for b in range(bars):
        bar_offset = b * beats_per_bar
        is_last_bar = b == bars - 1
        use_fill = fill and is_last_bar
        bar_pattern = _fill_pattern() if use_fill else pattern

        for note_num, hits in bar_pattern.items():
            for hit, velocity in _iter_hits(hits, note_num, base_vel):
                notes.append(
                    Note(
                        pitch=note_num,
                        start_beat=bar_offset + hit,
                        duration_beats=0.1,  # drums are essentially percussive
                        velocity=min(127, max(1, velocity)),
                    )
                )
        # Crash on the first bar of the clip.
        if b == 0 and add_crash:
            notes.append(Note(pitch=CRASH, start_beat=bar_offset, duration_beats=0.1, velocity=base_vel))

    return Clip(
        track=track_name,
        section=section_name,
        notes=notes,
        start_bar=start_bar,
        length_bars=bars,
    )


# ---------------------------------------------------------------------------
# Pattern helpers
# ---------------------------------------------------------------------------

def _apply_intensity(
    pattern: dict[int, list[float]], intensity: str
) -> dict[int, list[float] | list[tuple[float, int]]]:
    """Return a pattern shaped by ``intensity``.

    Values are either a flat ``[hit, ...]`` list (velocity comes from base)
    or a ``[(hit, velocity_offset), ...]`` list — ``_iter_hits`` handles both.
    """
    if intensity == "light":
        out: dict[int, list[float]] = {}
        for num, hits in pattern.items():
            if num == KICK:
                out[num] = [h for h in hits if float(h).is_integer() and h in (0.0, 2.0)]
            elif num in (SNARE, CLAP):
                out[num] = [h for h in hits if h in (1.0, 3.0)]
            elif num in (CLOSED_HAT, RIDE):
                out[num] = [0.0, 1.0, 2.0, 3.0]
            else:
                out[num] = list(hits)
        return out

    if intensity == "heavy":
        out2: dict[int, list] = {k: list(v) for k, v in pattern.items()}
        # Open hats punctuate the offbeat lift every bar.
        out2.setdefault(OPEN_HAT, []).extend([1.5, 3.5])
        # Ghost snares on the final 16th of beats 2 and 4 — quiet, supportive.
        ghost_offset = -40
        existing = out2.setdefault(SNARE, [])
        existing.extend([(1.75, ghost_offset), (3.75, ghost_offset)])
        return out2

    return {k: list(v) for k, v in pattern.items()}


def _fill_pattern() -> dict[int, list]:
    """One-bar tom fill. Plain 1-and on the kick/snare, then toms on the
    second half to signal a section transition."""
    return {
        KICK:   [0.0],
        SNARE:  [1.0],
        TOM_HI: [2.0, 2.5],
        TOM_LO: [3.0, 3.5],
    }


def _iter_hits(hits, note_num: int, base_vel: int):
    """Yield ``(hit, velocity)`` pairs. ``hits`` may be floats or
    ``(hit, velocity_offset)`` tuples; a pure-float entry gets a small
    velocity bump on beat-1 kicks."""
    for h in hits:
        if isinstance(h, tuple):
            yield h[0], base_vel + h[1]
        else:
            bump = 8 if (note_num == KICK and h == 0.0) else 0
            yield h, base_vel + bump
