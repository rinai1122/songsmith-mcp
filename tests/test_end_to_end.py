"""End-to-end symbolic composition: blank → full pop song, headless.

No REAPER required; we verify the MIDI files materialize and the song state
looks sensible after each step.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from songsmith_mcp.server import _dispatch
from songsmith_mcp.state import reset_state


def _call(name: str, **args):
    out = _dispatch(name, args)
    assert len(out) == 1
    payload = json.loads(out[0].text)
    assert "error" not in payload, f"{name} errored: {payload}"
    return payload


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("SONGSMITH_OUT", str(tmp_path))
    import songsmith_mcp.reaper_bridge as rb
    rb._BRIDGE = None
    reset_state()
    yield tmp_path


def test_full_pop_song_end_to_end(_fresh):
    out_dir: Path = _fresh

    # 1. Bootstrap.
    _call("new_song", key="A minor", tempo=92, style_hint="ballad", explain_level="tutor")

    # 2. Form.
    forms = _call("suggest_form", style="pop", target_duration_s=150)
    assert any(f["sections"] for f in forms)
    _call("set_form", sections=[
        {"name": "intro", "bars": 4},
        {"name": "verse", "bars": 8},
        {"name": "chorus", "bars": 8},
        {"name": "outro", "bars": 4},
    ])

    # 3. Chords in the verse.
    chord_cands = _call("propose_chord_progression", section="verse", style="ballad", length_bars=4, n_candidates=3)
    assert len(chord_cands["candidates"]) == 3
    best = chord_cands["candidates"][0]
    chords = _call(
        "write_chords",
        section="verse",
        roman_numerals=best["roman_numerals"],
        bars_per_chord=2,
        rationale=best["rationale"],
    )
    verse_chord_proposal = chords["proposal_id"]
    _call("accept_proposal", proposal_id=verse_chord_proposal)

    # 4. Lyrics → melody for the verse.
    lyric_line = "tell me what you know about love and rain"
    melody = _call(
        "propose_melody",
        section="verse",
        lyrics=lyric_line,
        contour="arch",
        rhythm="eighths",
        seed=1,
    )
    _call("accept_proposal", proposal_id=melody["proposal_id"])

    # 5. Bass + drums under the verse.
    bass = _call("write_bassline", section="verse", style="root_fifth", seed=2)
    _call("accept_proposal", proposal_id=bass["proposal_id"])
    drums = _call("write_drum_pattern", section="verse", style="ballad", intensity="normal")
    _call("accept_proposal", proposal_id=drums["proposal_id"])

    # 6. Observe.
    state = _call("observe")
    assert state["key"] == "A minor"
    tracks = set(state["tracks"].keys())
    assert {"Chords", "Melody", "Bass", "Drums"}.issubset(tracks)

    # 7. MIDI files should exist on disk.
    mids = list(out_dir.glob("*.mid"))
    assert len(mids) >= 8  # proposal previews + accepted drafts per track


def test_explain_produces_text_in_tutor_mode(_fresh):
    _call("new_song", key="C major", explain_level="tutor")
    _call("set_form", sections=[{"name": "verse", "bars": 4}])
    cands = _call("propose_chord_progression", section="verse", style="pop", length_bars=4)
    chords = _call(
        "write_chords",
        section="verse",
        roman_numerals=cands["candidates"][0]["roman_numerals"],
        rationale=cands["candidates"][0]["rationale"],
    )
    text = _call("explain", proposal_id=chords["proposal_id"])["text"]
    assert "chord chart" in text.lower() or "chord" in text.lower()
    assert "**" in text, "tutor mode should include markdown-bolded headers"


def test_propose_melody_without_chords_errors_gracefully(_fresh):
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "verse", "bars": 2}])
    # With no chord track, melody generator should still work with empty
    # chord map — strong beats fall back to scale tones, but it shouldn't crash.
    out = _dispatch("propose_melody", {"section": "verse", "rhythm": "quarters"})
    payload = json.loads(out[0].text)
    # Either a clean proposal or a clear error — no crash.
    assert "proposal_id" in payload or "error" in payload
