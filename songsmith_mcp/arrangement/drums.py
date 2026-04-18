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


def write_drum_pattern(
    section_name: str,
    track_name: str,
    style: str = "pop",
    intensity: str = "normal",   # "light" | "normal" | "heavy"
    bars: int = 4,
    start_bar: int = 0,
    time_sig: tuple[int, int] = (4, 4),
) -> Clip:
    """Return a Clip filled with ``bars`` copies of the chosen style pattern."""
    pattern = _STYLES.get(style.lower(), _STYLES["pop"])
    beats_per_bar = time_sig[0] * (4 / time_sig[1])

    base_vel = {"light": 70, "normal": 95, "heavy": 115}.get(intensity, 95)

    notes: list[Note] = []
    for b in range(bars):
        bar_offset = b * beats_per_bar
        for note_num, hits in pattern.items():
            for hit in hits:
                # Small accent on beat 1 kicks.
                v = base_vel + (8 if (note_num == KICK and hit == 0.0) else 0)
                notes.append(
                    Note(
                        pitch=note_num,
                        start_beat=bar_offset + hit,
                        duration_beats=0.1,  # drums are essentially percussive
                        velocity=min(127, v),
                    )
                )
        # Crash on first bar of the clip.
        if b == 0 and intensity != "light":
            notes.append(Note(pitch=CRASH, start_beat=bar_offset, duration_beats=0.1, velocity=base_vel))
    # Fill: add a snare roll on the last half-beat of the last bar for "heavy".
    if intensity == "heavy" and bars >= 1:
        last = (bars - 1) * beats_per_bar
        for i, t in enumerate([3.0, 3.25, 3.5, 3.75]):
            notes.append(Note(pitch=SNARE, start_beat=last + t, duration_beats=0.1, velocity=95 + i * 3))

    return Clip(
        track=track_name,
        section=section_name,
        notes=notes,
        start_bar=start_bar,
        length_bars=bars,
    )
