"""User-facing direct edits to the song, bypassing the proposal pipeline.

The proposal workflow is for *generator* output — the LLM proposes, the human
accepts. These helpers are the other side of the contract: the human wants to
hand-edit a note, or hand back a MIDI clip they tweaked in REAPER / MuseScore.
Every operation re-renders the affected clip's ``.mid`` file so the on-disk
artefact stays in lock-step with ``SongState``.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import mido

from .hitl.proposals import create_proposal
from .reaper_bridge import get_bridge
from .state import Clip, Note, Track, get_state


def _find_clip(track_name: str, section: str, clip_index: int = 0) -> tuple[Track, Clip]:
    st = get_state()
    tr = st.tracks.get(track_name)
    if not tr:
        raise KeyError(f"no such track: {track_name!r}")
    matches = [c for c in tr.clips if c.section == section]
    if not matches:
        raise KeyError(f"no clip on {track_name!r} in section {section!r}")
    if clip_index < 0 or clip_index >= len(matches):
        raise IndexError(
            f"clip_index {clip_index} out of range ({len(matches)} clips in section)"
        )
    return tr, matches[clip_index]


def _rerender(clip: Clip) -> str:
    return get_bridge().insert_clip(clip, get_state(), proposal_id=None)


def edit_note(
    track_name: str,
    section: str,
    note_index: int,
    *,
    pitch: int | None = None,
    start_beat: float | None = None,
    duration_beats: float | None = None,
    velocity: int | None = None,
    lyric: str | None = None,
    clip_index: int = 0,
) -> dict[str, Any]:
    _, clip = _find_clip(track_name, section, clip_index)
    if note_index < 0 or note_index >= len(clip.notes):
        raise IndexError(
            f"note_index {note_index} out of range ({len(clip.notes)} notes in clip)"
        )
    n = clip.notes[note_index]
    if pitch is not None:
        n.pitch = int(pitch)
    if start_beat is not None:
        n.start_beat = float(start_beat)
    if duration_beats is not None:
        n.duration_beats = float(duration_beats)
    if velocity is not None:
        n.velocity = int(velocity)
    if lyric is not None:
        n.lyric = lyric or None
    path = _rerender(clip)
    return {
        "ok": True,
        "track": track_name,
        "section": section,
        "note_index": note_index,
        "note": asdict(n),
        "written_midi": path,
    }


def delete_note(
    track_name: str, section: str, note_index: int, clip_index: int = 0
) -> dict[str, Any]:
    _, clip = _find_clip(track_name, section, clip_index)
    if note_index < 0 or note_index >= len(clip.notes):
        raise IndexError(
            f"note_index {note_index} out of range ({len(clip.notes)} notes in clip)"
        )
    removed = clip.notes.pop(note_index)
    path = _rerender(clip)
    return {
        "ok": True,
        "removed": asdict(removed),
        "remaining_notes": len(clip.notes),
        "written_midi": path,
    }


def add_note(
    track_name: str,
    section: str,
    pitch: int,
    start_beat: float,
    duration_beats: float,
    velocity: int = 90,
    lyric: str | None = None,
    clip_index: int = 0,
) -> dict[str, Any]:
    _, clip = _find_clip(track_name, section, clip_index)
    n = Note(
        pitch=int(pitch),
        start_beat=float(start_beat),
        duration_beats=float(duration_beats),
        velocity=int(velocity),
        lyric=lyric,
    )
    clip.notes.append(n)
    clip.notes.sort(key=lambda x: (x.start_beat, x.pitch))
    path = _rerender(clip)
    return {
        "ok": True,
        "track": track_name,
        "section": section,
        "note_index": clip.notes.index(n),
        "note": asdict(n),
        "written_midi": path,
    }


def read_midi_notes(path: str | Path) -> tuple[list[Note], int]:
    """Parse a Standard MIDI File into (notes, ticks_per_beat).

    Note start_beat/duration_beats are absolute quarter-note beats from tick 0
    — matching the convention used by ``reaper_bridge.write_clip_midi``.
    """
    mid = mido.MidiFile(str(path))
    tpb = mid.ticks_per_beat or 480
    notes: list[Note] = []
    for tr in mid.tracks:
        abs_tick = 0
        open_notes: dict[int, tuple[int, int]] = {}
        for msg in tr:
            abs_tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                open_notes[msg.note] = (abs_tick, msg.velocity)
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                if msg.note in open_notes:
                    start_tick, vel = open_notes.pop(msg.note)
                    notes.append(
                        Note(
                            pitch=msg.note,
                            start_beat=start_tick / tpb,
                            duration_beats=max(1, abs_tick - start_tick) / tpb,
                            velocity=vel,
                        )
                    )
    notes.sort(key=lambda n: (n.start_beat, n.pitch))
    return notes, tpb


def import_midi(
    path: str,
    track_name: str,
    section: str,
    as_proposal: bool = False,
    role: str | None = None,
    clip_index: int = 0,
) -> dict[str, Any]:
    """Read a .mid (likely edited in a DAW) back into state as a clip.

    By default directly commits, replacing the existing clip at
    ``(track_name, section, clip_index)`` if one exists. Set ``as_proposal``
    to route the import through the accept/reject lifecycle instead.
    """
    st = get_state()
    sec = st.section_by_name(section)
    notes, _ = read_midi_notes(path)

    clip = Clip(
        track=track_name,
        section=section,
        notes=notes,
        start_bar=sec.start_bar,
        length_bars=sec.bars,
    )

    if as_proposal:
        prop = create_proposal(
            kind="import",
            section=section,
            track=track_name,
            clips=[clip],
            summary=f"imported {len(notes)} notes from {Path(path).name}",
            rationale=f"User-edited MIDI from {path}.",
        )
        return {"proposal_id": prop.id, "notes": len(notes), "summary": prop.summary}

    existing = st.tracks.get(track_name)
    tr = st.ensure_track(
        track_name, role=role or (existing.role if existing else "import")
    )
    in_section = [i for i, c in enumerate(tr.clips) if c.section == section]
    if in_section and clip_index < len(in_section):
        tr.clips[in_section[clip_index]] = clip
        replaced = True
    else:
        tr.clips.append(clip)
        replaced = False
    out_path = _rerender(clip)
    return {
        "ok": True,
        "track": track_name,
        "section": section,
        "notes": len(notes),
        "replaced": replaced,
        "written_midi": out_path,
    }


# Grid names accepted by ``quantize_clip``. Values are in quarter-note beats;
# 'T' marks a triplet grid (three notes in the space of two).
_QUANTIZE_GRIDS: dict[str, float] = {
    "1/4": 1.0,
    "1/8": 0.5,
    "1/8T": 1.0 / 3.0,
    "1/16": 0.25,
    "1/16T": 1.0 / 6.0,
    "1/32": 0.125,
}


def create_empty_clip(
    track_name: str,
    section: str,
    role: str | None = None,
) -> dict[str, Any]:
    """Create a blank clip on (track, section) so hand-composing can start
    without a generator running first. If the track doesn't exist, it's made
    with ``role`` (defaults to ``'midi'``). If a clip already exists on
    (track, section), the call is a no-op and returns ``existed=true``.
    """
    st = get_state()
    sec = st.section_by_name(section)
    tr = st.ensure_track(track_name, role=role or "midi")
    existing = [c for c in tr.clips if c.section == section]
    if existing:
        return {
            "ok": True,
            "existed": True,
            "track": track_name,
            "section": section,
            "clip_index": tr.clips.index(existing[0]),
            "note_count": len(existing[0].notes),
        }
    clip = Clip(
        track=track_name,
        section=section,
        notes=[],
        start_bar=sec.start_bar,
        length_bars=sec.bars,
    )
    tr.clips.append(clip)
    path = _rerender(clip)
    return {
        "ok": True,
        "existed": False,
        "track": track_name,
        "section": section,
        "clip_index": tr.clips.index(clip),
        "start_bar": clip.start_bar,
        "length_bars": clip.length_bars,
        "written_midi": path,
    }


def duplicate_clip(
    track_name: str,
    from_section: str,
    to_section: str,
    *,
    clip_index: int = 0,
    target_track_name: str | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Copy a clip's notes into another section.

    Notes keep their relative structure (same ``start_beat`` / duration /
    velocity / lyric); only the clip's ``section`` / ``start_bar`` /
    ``length_bars`` are re-targeted. When ``target_track_name`` is given the
    copy lands on a different track (created if missing, same ``role`` as the
    source). With ``replace=True`` an existing clip at the destination slot is
    overwritten; otherwise a second clip is appended.
    """
    src_track, src_clip = _find_clip(track_name, from_section, clip_index)
    st = get_state()
    to_sec = st.section_by_name(to_section)
    dest_track_name = target_track_name or track_name
    dest_track = st.ensure_track(dest_track_name, role=src_track.role)

    new_clip = Clip(
        track=dest_track_name,
        section=to_section,
        notes=[
            Note(
                pitch=n.pitch,
                start_beat=n.start_beat,
                duration_beats=n.duration_beats,
                velocity=n.velocity,
                lyric=n.lyric,
            )
            for n in src_clip.notes
        ],
        start_bar=to_sec.start_bar,
        length_bars=to_sec.bars,
        chord_symbol=src_clip.chord_symbol,
    )

    in_section = [i for i, c in enumerate(dest_track.clips) if c.section == to_section]
    if replace and in_section:
        dest_track.clips[in_section[0]] = new_clip
        replaced = True
    else:
        dest_track.clips.append(new_clip)
        replaced = False

    path = _rerender(new_clip)
    return {
        "ok": True,
        "source": {"track": track_name, "section": from_section, "clip_index": clip_index},
        "target": {
            "track": dest_track_name,
            "section": to_section,
            "clip_index": dest_track.clips.index(new_clip),
            "replaced": replaced,
        },
        "notes_copied": len(new_clip.notes),
        "written_midi": path,
    }


def quantize_clip(
    track_name: str,
    section: str,
    *,
    grid: str = "1/16",
    strength: float = 1.0,
    quantize_duration: bool = False,
    clip_index: int = 0,
) -> dict[str, Any]:
    """Snap note starts (and optionally durations) to ``grid``.

    ``strength`` ∈ [0, 1] interpolates between the original start and the
    fully-snapped position — 1.0 is hard quantize, 0.5 pulls halfway toward
    the grid (the classic "gentle" DAW feel). Durations snap only when
    ``quantize_duration=True`` and never go below one grid step (so very short
    ornaments aren't silently deleted).
    """
    if grid not in _QUANTIZE_GRIDS:
        raise ValueError(
            f"unknown grid {grid!r}; expected one of {sorted(_QUANTIZE_GRIDS)}"
        )
    s = max(0.0, min(1.0, float(strength)))
    step = _QUANTIZE_GRIDS[grid]

    _, clip = _find_clip(track_name, section, clip_index)
    moved = 0
    for n in clip.notes:
        snapped = round(n.start_beat / step) * step
        new_start = n.start_beat + (snapped - n.start_beat) * s
        if new_start != n.start_beat:
            moved += 1
        n.start_beat = new_start
        if quantize_duration:
            snapped_dur = round(n.duration_beats / step) * step
            n.duration_beats = max(step, snapped_dur)
    clip.notes.sort(key=lambda x: (x.start_beat, x.pitch))
    path = _rerender(clip)
    return {
        "ok": True,
        "track": track_name,
        "section": section,
        "grid": grid,
        "strength": s,
        "notes_moved": moved,
        "written_midi": path,
    }
