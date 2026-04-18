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
