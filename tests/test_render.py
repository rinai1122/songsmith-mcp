"""Tests for the audio render pipeline.

Focus on the parts we can assert cheaply: stem flattening (bar-offset math),
graceful behavior on an empty state, and wav-file existence after a real
end-to-end render. We don't spectrum-analyze the audio — if the WAV writes
and is non-trivially sized, the synth math ran.
"""

from __future__ import annotations

import shutil
import wave
from pathlib import Path

import pytest

from songsmith_mcp.render.merge import flatten
from songsmith_mcp.render.pipeline import render_song
from songsmith_mcp.state import Clip, Note, Section, SongState, Track


def _mk_state_with_one_clip() -> SongState:
    st = SongState()
    st.tempo = 120.0            # 2 beats / sec
    st.time_sig = (4, 4)
    st.sections = [
        Section(name="intro", bars=2, start_bar=0),
        Section(name="verse", bars=4, start_bar=2),
    ]
    # One melody clip in "verse" at start_bar=2 (= beat 8, = 4 seconds at 120bpm)
    clip = Clip(
        track="Melody",
        section="verse",
        start_bar=2,
        length_bars=2,
        notes=[
            Note(pitch=60, start_beat=0.0, duration_beats=1.0, velocity=100),
            Note(pitch=64, start_beat=1.0, duration_beats=1.0, velocity=100),
        ],
    )
    tr = Track(name="Melody", role="melody", clips=[clip])
    st.tracks["Melody"] = tr
    return st


def test_flatten_applies_section_bar_offset():
    st = _mk_state_with_one_clip()
    stems, duration = flatten(st)
    assert len(stems) == 1
    stem = stems[0]
    # verse starts at bar 2 → beat 8 → second 4.0 at 120 BPM, 4/4.
    assert stem.notes[0].start_s == pytest.approx(4.0)
    assert stem.notes[1].start_s == pytest.approx(4.5)
    # Song is 6 bars total → 24 beats → 12 s; duration includes a half-second tail.
    assert duration >= 12.0


def test_flatten_empty_state_returns_empty():
    stems, duration = flatten(SongState())
    assert stems == []
    assert duration >= 0.5


def test_render_song_writes_wav(tmp_path: Path):
    st = _mk_state_with_one_clip()
    result = render_song(st, tmp_path, emit_stems=True, emit_mp3=False)
    assert result["ok"] is True
    wav = Path(result["wav"])
    assert wav.exists() and wav.stat().st_size > 1000, "wav should be non-trivially sized"
    # Readable as valid WAV.
    with wave.open(str(wav), "rb") as wf:
        assert wf.getframerate() == 44100
        assert wf.getnchannels() == 1
        assert wf.getnframes() > 0
    # Stem for the melody role was emitted.
    assert "melody" in result["stems"]
    assert Path(result["stems"]["melody"]).exists()


def test_render_song_empty_state_no_crash(tmp_path: Path):
    result = render_song(SongState(), tmp_path, emit_mp3=False)
    assert result["ok"] is False
    assert "no clips" in result["reason"].lower()


def test_render_song_mp3_optional(tmp_path: Path):
    """mp3 emission is best-effort: ffmpeg present → .mp3 exists; absent → None."""
    st = _mk_state_with_one_clip()
    result = render_song(st, tmp_path, emit_mp3=True)
    if shutil.which("ffmpeg") is None:
        assert result["mp3"] is None
        assert result["mp3_available"] is False
    else:
        assert result["mp3_available"] is True
        assert Path(result["mp3"]).exists()


def test_drum_stem_renders_without_pitched_voice(tmp_path: Path):
    st = SongState()
    st.tempo = 120.0
    st.sections = [Section(name="intro", bars=1, start_bar=0)]
    st.tracks["Drums"] = Track(
        name="Drums",
        role="drums",
        clips=[Clip(
            track="Drums", section="intro", start_bar=0, length_bars=1,
            notes=[
                Note(pitch=36, start_beat=0.0, duration_beats=0.25, velocity=110),  # kick
                Note(pitch=38, start_beat=1.0, duration_beats=0.25, velocity=110),  # snare
                Note(pitch=42, start_beat=0.5, duration_beats=0.25, velocity=80),   # closed hat
            ],
        )],
    )
    result = render_song(st, tmp_path, emit_mp3=False)
    assert result["ok"] is True
    assert "drums" in result["stems"]
