"""SongState → music21 Score → MusicXML (+ optional PNG via MuseScore).

MusicXML is always emitted (pure-Python, zero external deps). PNG is an
optional upgrade that requires MuseScore 4 installed; if it's not on PATH
and not in the common install locations, we skip PNG and still return the
MusicXML path so the user can open it in anything.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from music21 import (
    bar,
    chord as m21_chord,
    clef,
    duration as m21_duration,
    instrument,
    key as m21_key,
    meter,
    note as m21_note,
    stream,
    tempo as m21_tempo,
)

from ..state import Clip, SongState, Track


# ---------------------------------------------------------------------------
# MuseScore discovery
# ---------------------------------------------------------------------------

_MUSESCORE_CANDIDATE_PATHS = [
    r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
    r"C:\Program Files (x86)\MuseScore 4\bin\MuseScore4.exe",
    r"C:\Program Files\MuseScore 3\bin\MuseScore3.exe",
    "/Applications/MuseScore 4.app/Contents/MacOS/mscore",
    "/Applications/MuseScore 3.app/Contents/MacOS/mscore",
    "/usr/bin/musescore4",
    "/usr/bin/mscore",
]


def find_musescore() -> str | None:
    """Locate the MuseScore executable. Returns None if not installed."""
    env = os.environ.get("SONGSMITH_MUSESCORE")
    if env and Path(env).exists():
        return env
    for name in ("musescore4", "MuseScore4", "mscore", "musescore"):
        found = shutil.which(name)
        if found:
            return found
    for p in _MUSESCORE_CANDIDATE_PATHS:
        if Path(p).exists():
            return p
    return None


# ---------------------------------------------------------------------------
# SongState → music21
# ---------------------------------------------------------------------------

_INSTRUMENT_BY_ROLE = {
    "chords": instrument.Piano,
    "pad":    instrument.Piano,
    "melody": instrument.Vocalist,  # placeholder; real vocaloid replaces this
    "vocal":  instrument.Vocalist,
    "bass":   instrument.ElectricBass,
}


def _clip_to_part(clip: Clip, track: Track, beats_per_bar: float) -> stream.Part:
    """Convert one Clip's notes into a music21 Part fragment at its bar offset."""
    part = stream.Part()
    # Pad with rests up to clip.start_bar so everything aligns on one timeline.
    offset_ql = clip.start_bar * beats_per_bar  # assumes quarter=1 beat (TS with den=4)
    if offset_ql > 0:
        r = m21_note.Rest()
        r.duration = m21_duration.Duration(offset_ql)
        part.append(r)

    if track.role == "drums":
        # music21 percussion: route through an Unpitched-like shortcut — use
        # plain Notes on a PercussionClef so MusicXML still renders something.
        # Full GM-drum-name mapping is overkill for a preview; we just want
        # rhythmic shapes on the page.
        for n in clip.notes:
            nt = m21_note.Unpitched()
            nt.duration = m21_duration.Duration(max(n.duration_beats, 0.125))
            nt.offset = offset_ql + n.start_beat
            part.insert(nt.offset, nt)
        return part

    # Pitched: chords collapse simultaneous notes into m21.Chord objects.
    by_start: dict[float, list] = {}
    for n in clip.notes:
        by_start.setdefault(n.start_beat, []).append(n)

    for start, group in sorted(by_start.items()):
        abs_offset = offset_ql + start
        dur = max((n.duration_beats for n in group), default=0.25)
        if len(group) == 1:
            n = group[0]
            nt = m21_note.Note(midi=n.pitch)
            nt.duration = m21_duration.Duration(dur)
            if n.lyric:
                nt.lyric = n.lyric
            part.insert(abs_offset, nt)
        else:
            ch = m21_chord.Chord([g.pitch for g in group])
            ch.duration = m21_duration.Duration(dur)
            lyric = next((g.lyric for g in group if g.lyric), None)
            if lyric:
                ch.lyric = lyric
            part.insert(abs_offset, ch)
    return part


def build_score(state: SongState) -> stream.Score:
    """Return a music21 Score with one Part per Track."""
    score = stream.Score()
    beats_per_bar = state.time_sig[0] * (4 / state.time_sig[1])

    # Header: first part carries tempo / key / meter so they render at top.
    header_attached = False
    for track in state.tracks.values():
        if not track.clips:
            continue
        part = stream.Part()
        inst_cls = _INSTRUMENT_BY_ROLE.get(track.role, instrument.Piano)
        part.insert(0, inst_cls())
        part.partName = track.name

        if not header_attached:
            part.insert(0, m21_tempo.MetronomeMark(number=state.tempo))
            try:
                part.insert(0, m21_key.Key(state.key.split()[0], state.key.split()[1]))
            except Exception:
                pass  # tolerate free-form key strings
            part.insert(0, meter.TimeSignature(f"{state.time_sig[0]}/{state.time_sig[1]}"))
            header_attached = True

        if track.role == "bass":
            part.insert(0, clef.BassClef())
        elif track.role == "drums":
            part.insert(0, clef.PercussionClef())

        # Append each clip's contents, already offset to its bar position.
        for clip in sorted(track.clips, key=lambda c: c.start_bar):
            frag = _clip_to_part(clip, track, beats_per_bar)
            for el in frag.flatten().notesAndRests:
                part.insert(el.offset, el)

        # Trailing final barline so MuseScore closes the staff cleanly.
        total_ql = state.total_bars() * beats_per_bar
        part.insert(total_ql, bar.Barline("final"))

        score.append(part)

    return score


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_musicxml(state: SongState, out_path: Path) -> Path:
    """Write MusicXML. Always works (pure-Python)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    score = build_score(state)
    score.write("musicxml", fp=str(out_path))
    return out_path


def export_png(musicxml_path: Path, png_path: Path | None = None) -> Path | None:
    """Convert MusicXML → PNG by shelling out to MuseScore.

    Returns None if MuseScore isn't installed. MuseScore renders multi-page
    scores into ``page-N.png`` siblings when the score is longer than a page,
    so we return the first page.
    """
    mscore = find_musescore()
    if mscore is None:
        return None
    png_path = png_path or musicxml_path.with_suffix(".png")
    try:
        subprocess.run(
            [mscore, "-o", str(png_path), str(musicxml_path)],
            check=True,
            capture_output=True,
            timeout=60,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    if png_path.exists():
        return png_path
    # MuseScore renames to -1.png / -2.png for multi-page.
    candidate = png_path.with_name(png_path.stem + "-1.png")
    return candidate if candidate.exists() else None


def export_score(
    state: SongState,
    out_dir: Path,
    *,
    basename: str = "score",
    emit_png: bool = True,
) -> dict[str, str | None]:
    """Top-level: write musicxml + png, return their paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    xml_path = export_musicxml(state, out_dir / f"{basename}.musicxml")
    png_path = export_png(xml_path, out_dir / f"{basename}.png") if emit_png else None
    return {
        "musicxml": str(xml_path),
        "png": str(png_path) if png_path else None,
        "musescore_found": find_musescore(),
    }
