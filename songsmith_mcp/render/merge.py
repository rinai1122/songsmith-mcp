"""Flatten a SongState into per-role audio stems.

Each Clip lives in its own ``(section, start_bar)`` coordinate. For rendering
we want absolute-time events sorted per role, because the synth backends
don't know anything about sections or bars — they just want
``(pitch, start_seconds, duration_seconds, velocity)``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..state import SongState


@dataclass
class AudioNote:
    pitch: int
    start_s: float
    duration_s: float
    velocity: int
    lyric: str | None = None


@dataclass
class Stem:
    role: str          # "chords" | "melody" | "bass" | "drums" | "pad" | "vocal"
    track_name: str
    notes: list[AudioNote]


def flatten(state: SongState) -> tuple[list[Stem], float]:
    """Return (stems, total_duration_s).

    ``Clip.notes[i].start_beat`` is already absolute beats from section start,
    but clips themselves sit at ``clip.start_bar``, which we already computed
    in form.recompute(). We convert beats→seconds using the current tempo.
    """
    beats_per_bar = state.time_sig[0] * (4 / state.time_sig[1])
    sec_per_beat = 60.0 / state.tempo

    stems: list[Stem] = []
    latest_end = 0.0

    for track in state.tracks.values():
        notes: list[AudioNote] = []
        for clip in track.clips:
            clip_offset_beats = clip.start_bar * beats_per_bar
            for n in clip.notes:
                start_beat_abs = clip_offset_beats + n.start_beat
                start_s = start_beat_abs * sec_per_beat
                dur_s = n.duration_beats * sec_per_beat
                notes.append(AudioNote(
                    pitch=n.pitch,
                    start_s=start_s,
                    duration_s=dur_s,
                    velocity=n.velocity,
                    lyric=n.lyric,
                ))
                latest_end = max(latest_end, start_s + dur_s)
        if notes:
            notes.sort(key=lambda a: a.start_s)
            stems.append(Stem(role=track.role, track_name=track.name, notes=notes))

    # Round up to nearest bar so trailing reverb/release doesn't get cut off.
    total_bars = state.total_bars()
    song_end = total_bars * beats_per_bar * sec_per_beat
    duration = max(latest_end, song_end) + 0.5  # half-second tail

    return stems, duration
