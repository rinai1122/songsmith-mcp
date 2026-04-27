"""Tests for the vocal backend layer.

Coverage:
- Backend selection from env vars (auto / explicit / unknown).
- Formant backend produces audio with vowel-formant structure (not the
  saw-lead spectrum).
- External backend round-trip with a stub renderer (Python script that reads
  the JSON request and writes a wav).
- NNSVS backend gracefully reports unavailable when deps/bank missing.
- Render pipeline reports ``vocal_backend`` and uses formant by default.
- Render pipeline catches a backend exception and falls back to formant
  (so a broken external engine never breaks the mix).
"""

from __future__ import annotations

import os
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from songsmith_mcp.render.merge import AudioNote
from songsmith_mcp.render.pipeline import render_song
from songsmith_mcp.render.vocal import VocalRequest, select_vocal_backend
from songsmith_mcp.render.vocal.formant import (
    FormantBackend,
    _extract_vowel,
)
from songsmith_mcp.render.vocal.nnsvs_backend import NNSVSBackend, _build_sinsy_xml
from songsmith_mcp.render.vocal.external import ExternalBackend
from songsmith_mcp.render.vocal.saw import SawBackend
from songsmith_mcp.state import Clip, Note, Section, SongState, Track


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _melody_notes(lyrics: list[str], pitch: int = 67) -> list[AudioNote]:
    out: list[AudioNote] = []
    t = 0.0
    for word in lyrics:
        out.append(AudioNote(
            pitch=pitch, start_s=t, duration_s=0.4, velocity=100, lyric=word,
        ))
        t += 0.5
    return out


def _request(notes: list[AudioNote], duration: float = 3.0) -> VocalRequest:
    return VocalRequest(
        notes=notes, duration_s=duration, sample_rate=44100,
        tempo=120.0, key="C major",
    )


def _state_with_melody() -> SongState:
    st = SongState()
    st.tempo = 120.0
    st.sections = [Section(name="verse", bars=1, start_bar=0)]
    clip = Clip(
        track="Vox", section="verse", start_bar=0, length_bars=1,
        notes=[
            Note(pitch=67, start_beat=0.0, duration_beats=1.0, velocity=100, lyric="la"),
            Note(pitch=69, start_beat=1.0, duration_beats=1.0, velocity=100, lyric="lee"),
            Note(pitch=72, start_beat=2.0, duration_beats=1.0, velocity=100, lyric="lo"),
            Note(pitch=69, start_beat=3.0, duration_beats=1.0, velocity=100, lyric="loo"),
        ],
    )
    st.tracks["Vox"] = Track(name="Vox", role="vocal", clips=[clip])
    return st


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def test_select_default_is_formant_when_nothing_configured(monkeypatch):
    monkeypatch.delenv("SONGSMITH_VOCAL_BACKEND", raising=False)
    monkeypatch.delenv("SONGSMITH_VOCAL_BANK", raising=False)
    monkeypatch.delenv("SONGSMITH_VOCAL_RENDER_CMD", raising=False)
    backend = select_vocal_backend()
    assert backend.name == "formant"


def test_select_explicit_formant(monkeypatch):
    monkeypatch.setenv("SONGSMITH_VOCAL_BACKEND", "formant")
    backend = select_vocal_backend()
    assert backend.name == "formant"


def test_select_explicit_saw(monkeypatch):
    monkeypatch.setenv("SONGSMITH_VOCAL_BACKEND", "saw")
    backend = select_vocal_backend()
    assert backend.name == "saw"


def test_select_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("SONGSMITH_VOCAL_BACKEND", "totally-fake")
    with pytest.raises(ValueError, match="unknown SONGSMITH_VOCAL_BACKEND"):
        select_vocal_backend()


def test_select_auto_picks_external_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("SONGSMITH_VOCAL_BACKEND", "auto")
    monkeypatch.delenv("SONGSMITH_VOCAL_BANK", raising=False)
    monkeypatch.setenv(
        "SONGSMITH_VOCAL_RENDER_CMD",
        f"{sys.executable} -c 'pass' --in {{input}} --out {{output}}",
    )
    backend = select_vocal_backend()
    assert backend.name == "external"


# ---------------------------------------------------------------------------
# Vowel extraction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "lyric,expected",
    [
        ("la",     "a"),
        ("lee",    "i"),
        ("loo",    "u"),
        ("oh",     "o"),
        ("hey",    "e"),
        ("",       "@"),
        (None,     "@"),
        ("hmm",    "@"),     # no vowel letter → schwa
        ("light",  "i"),
        ("sound",  "o"),
        ("rain",   "a"),     # diphthong "ai" → /a/ nucleus
    ],
)
def test_vowel_extraction(lyric, expected):
    assert _extract_vowel(lyric) == expected


# ---------------------------------------------------------------------------
# Formant backend
# ---------------------------------------------------------------------------

def test_formant_backend_renders_padded_buffer():
    backend = FormantBackend()
    req = _request(_melody_notes(["la", "lee", "lo"]))
    buf = backend.render(req)
    expected = int(req.duration_s * req.sample_rate) + req.sample_rate
    assert buf.shape == (expected,)
    assert buf.dtype == np.float32
    assert np.max(np.abs(buf)) > 0.05, "formant render should be audible"


def test_formant_backend_is_deterministic():
    """Same request → bit-identical output (rng seeded inside backend)."""
    req = _request(_melody_notes(["la", "la"]))
    a = FormantBackend().render(req)
    b = FormantBackend().render(req)
    assert np.array_equal(a, b)


def test_formant_backend_different_vowels_produce_different_audio():
    """[a] and [i] have very different formant centers → buffers must differ."""
    a_buf = FormantBackend().render(_request(_melody_notes(["la"])))
    i_buf = FormantBackend().render(_request(_melody_notes(["lee"])))
    assert a_buf.shape == i_buf.shape
    diff = float(np.mean(np.abs(a_buf - i_buf)))
    assert diff > 0.005, f"vowel difference should be perceptible (got {diff})"


def test_formant_backend_handles_empty_notes():
    buf = FormantBackend().render(_request([], duration=1.0))
    assert buf.size > 0
    assert float(np.max(np.abs(buf))) == 0.0  # silence


# ---------------------------------------------------------------------------
# Saw backend (legacy)
# ---------------------------------------------------------------------------

def test_saw_backend_still_renders():
    """Parity check for users opting into the legacy saw lead."""
    buf = SawBackend().render(_request(_melody_notes(["la", "la"])))
    assert buf.size > 0
    assert float(np.max(np.abs(buf))) > 0.05


# ---------------------------------------------------------------------------
# NNSVS backend (no real bank — just availability + sinsy XML shape)
# ---------------------------------------------------------------------------

def test_nnsvs_backend_unavailable_without_bank(monkeypatch):
    monkeypatch.delenv("SONGSMITH_VOCAL_BANK", raising=False)
    backend = NNSVSBackend()
    ready, reason = backend.is_available()
    assert ready is False
    assert "SONGSMITH_VOCAL_BANK" in reason


def test_nnsvs_backend_unavailable_when_bank_path_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SONGSMITH_VOCAL_BANK", str(tmp_path / "nope"))
    backend = NNSVSBackend()
    ready, reason = backend.is_available()
    assert ready is False
    assert "not found" in reason


def test_sinsy_xml_includes_pitch_duration_and_lyric():
    notes = [
        AudioNote(pitch=60, start_s=0.0, duration_s=0.5, velocity=100, lyric="la"),
        AudioNote(pitch=62, start_s=0.5, duration_s=0.5, velocity=100, lyric="lee"),
    ]
    xml = _build_sinsy_xml(_request(notes, duration=2.0))
    assert "<step>C</step>" in xml
    assert "<step>D</step>" in xml
    assert "<text>la</text>" in xml
    assert "<text>lee</text>" in xml


def test_sinsy_xml_handles_sharps():
    notes = [AudioNote(pitch=61, start_s=0.0, duration_s=0.5, velocity=100, lyric="la")]
    xml = _build_sinsy_xml(_request(notes, duration=1.0))
    assert "<step>C</step>" in xml
    assert "<alter>1</alter>" in xml


# ---------------------------------------------------------------------------
# External backend (subprocess plug-in)
# ---------------------------------------------------------------------------

# A tiny Python script that produces a 0.5-second 220Hz sine and writes it
# as 16-bit mono WAV. Acts as a stand-in vocaloid renderer for tests.
_STUB_RENDERER = '''
import json, sys, wave
import numpy as np

inp = out = None
i = 1
while i < len(sys.argv):
    if sys.argv[i] == "--in":
        inp = sys.argv[i+1]; i += 2
    elif sys.argv[i] == "--out":
        out = sys.argv[i+1]; i += 2
    else:
        i += 1

req = json.load(open(inp))
sr = req["sample_rate"]
dur = 0.5
n = int(sr * dur)
t = np.arange(n) / sr
wave_data = (0.5 * np.sin(2 * np.pi * 220.0 * t) * 32767).astype(np.int16)
with wave.open(out, "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(sr)
    wf.writeframes(wave_data.tobytes())
'''


def test_external_backend_unavailable_without_cmd(monkeypatch):
    monkeypatch.delenv("SONGSMITH_VOCAL_RENDER_CMD", raising=False)
    backend = ExternalBackend()
    ready, reason = backend.is_available()
    assert ready is False
    assert "SONGSMITH_VOCAL_RENDER_CMD" in reason


def test_external_backend_rejects_template_without_placeholders(monkeypatch):
    monkeypatch.setenv("SONGSMITH_VOCAL_RENDER_CMD", "bin/render-no-placeholders")
    backend = ExternalBackend()
    ready, reason = backend.is_available()
    assert ready is False
    assert "{input}" in reason and "{output}" in reason


def test_external_backend_round_trips_with_stub_renderer(monkeypatch, tmp_path):
    stub = tmp_path / "stub_render.py"
    stub.write_text(_STUB_RENDERER)
    monkeypatch.setenv(
        "SONGSMITH_VOCAL_RENDER_CMD",
        f'"{sys.executable}" "{stub}" --in {{input}} --out {{output}}',
    )
    backend = ExternalBackend()
    ready, _ = backend.is_available()
    assert ready
    buf = backend.render(_request(_melody_notes(["la"])))
    expected = int(3.0 * 44100) + 44100
    assert buf.shape == (expected,)
    # Stub renders 0.5 s of audible sine → first 22050 samples are non-zero.
    assert float(np.max(np.abs(buf[:22050]))) > 0.1


def test_external_backend_surfaces_renderer_failure(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "SONGSMITH_VOCAL_RENDER_CMD",
        f'"{sys.executable}" -c "import sys; sys.exit(7)" --in {{input}} --out {{output}}',
    )
    backend = ExternalBackend()
    with pytest.raises(RuntimeError, match="exited 7"):
        backend.render(_request(_melody_notes(["la"])))


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------

def test_render_song_reports_vocal_backend(monkeypatch, tmp_path):
    monkeypatch.delenv("SONGSMITH_VOCAL_BACKEND", raising=False)
    monkeypatch.delenv("SONGSMITH_VOCAL_BANK", raising=False)
    monkeypatch.delenv("SONGSMITH_VOCAL_RENDER_CMD", raising=False)
    result = render_song(_state_with_melody(), tmp_path, emit_mp3=False)
    assert result["ok"] is True
    assert result["vocal_backend"] == "formant"
    # Vocal stem was emitted via the new pipeline (role is "vocal" in this state).
    assert "vocal" in result["stems"]


def test_render_song_with_explicit_saw_backend(monkeypatch, tmp_path):
    monkeypatch.setenv("SONGSMITH_VOCAL_BACKEND", "saw")
    result = render_song(_state_with_melody(), tmp_path, emit_mp3=False)
    assert result["vocal_backend"] == "saw"


def test_render_song_falls_back_when_vocal_backend_raises(tmp_path, capsys):
    """A broken vocal engine must not break the whole render — fall back to formant."""

    class _Boom:
        name = "boom"
        def is_available(self):  # noqa: D401
            return True, "always"
        def render(self, request):
            raise RuntimeError("simulated engine crash")

    result = render_song(
        _state_with_melody(), tmp_path, emit_mp3=False, vocal_backend=_Boom(),
    )
    assert result["ok"] is True
    assert result["vocal_backend"] == "boom"  # what the user asked for
    captured = capsys.readouterr()
    assert "simulated engine crash" in captured.err
    assert "falling back to formant" in captured.err
    # Wav still got written.
    wav_path = Path(result["wav"])
    assert wav_path.exists() and wav_path.stat().st_size > 1000
