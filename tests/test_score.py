"""Tests for the score/playback pipeline.

MusicXML export must always work (pure-Python, no deps). PNG is best-effort
— the test asserts that when MuseScore is detected the PNG exists, and that
when it isn't the pipeline still returns the MusicXML without raising.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from songsmith_mcp.render.score import build_score, export_score, find_musescore
from songsmith_mcp.state import Clip, Note, Section, SongState, Track


def _mk_two_track_state() -> SongState:
    st = SongState()
    st.tempo = 120.0
    st.time_sig = (4, 4)
    st.sections = [Section(name="verse", bars=2, start_bar=0)]
    # Chord track
    st.tracks["Chords"] = Track(
        name="Chords", role="chords",
        clips=[Clip(
            track="Chords", section="verse", start_bar=0, length_bars=2,
            notes=[
                Note(pitch=60, start_beat=0.0, duration_beats=4.0),
                Note(pitch=64, start_beat=0.0, duration_beats=4.0),
                Note(pitch=67, start_beat=0.0, duration_beats=4.0),
            ],
        )],
    )
    # Melody with lyrics
    st.tracks["Melody"] = Track(
        name="Melody", role="melody",
        clips=[Clip(
            track="Melody", section="verse", start_bar=0, length_bars=2,
            notes=[
                Note(pitch=72, start_beat=0.0, duration_beats=1.0, lyric="la"),
                Note(pitch=74, start_beat=1.0, duration_beats=1.0, lyric="di"),
            ],
        )],
    )
    return st


def test_build_score_has_one_part_per_track():
    st = _mk_two_track_state()
    score = build_score(st)
    # music21 wraps parts in its Score object; count top-level Parts.
    parts = list(score.parts)
    assert len(parts) == 2
    names = sorted(p.partName for p in parts)
    assert names == ["Chords", "Melody"]


def test_build_score_attaches_lyrics():
    st = _mk_two_track_state()
    score = build_score(st)
    lyrics_found: list[str] = []
    for p in score.parts:
        for n in p.flatten().notes:
            if n.lyric:
                lyrics_found.append(n.lyric)
    assert "la" in lyrics_found and "di" in lyrics_found


def test_export_score_writes_musicxml(tmp_path: Path):
    st = _mk_two_track_state()
    result = export_score(st, tmp_path, emit_png=False)
    xml = Path(result["musicxml"])
    assert xml.exists() and xml.stat().st_size > 500
    assert "<?xml" in xml.read_text(encoding="utf-8")[:200]


def test_export_score_png_when_musescore_present(tmp_path: Path):
    st = _mk_two_track_state()
    result = export_score(st, tmp_path, emit_png=True)
    assert result["musicxml"]
    if find_musescore() is None:
        assert result["png"] is None
    else:
        assert result["png"] is not None, "MuseScore is installed but PNG export returned None"
        assert Path(result["png"]).exists()


def test_export_score_empty_state_no_crash(tmp_path: Path):
    """Score of an empty state should still produce a (tiny) MusicXML without raising."""
    result = export_score(SongState(), tmp_path, emit_png=False)
    assert Path(result["musicxml"]).exists()


def test_playback_find_musescore_is_idempotent():
    """Detection should be deterministic across calls."""
    a = find_musescore()
    b = find_musescore()
    assert a == b
