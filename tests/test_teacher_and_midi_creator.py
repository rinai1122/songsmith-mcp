"""Covers the new teacher-mode tools and MIDI-creator helpers:

- explain() surviving accept_proposal
- create_empty_clip / duplicate_clip / quantize_clip
- analyze_section
- lesson
- suggest_next_step
"""

from __future__ import annotations

import json

import pytest

from songsmith_mcp import direct_edit as edit_mod
from songsmith_mcp.hitl import proposals as prop_mod
from songsmith_mcp.hitl.explain import explain as _explain
from songsmith_mcp.hitl.teacher import (
    LESSONS,
    analyze_section,
    lesson,
    suggest_next_step,
)
from songsmith_mcp.server import _dispatch
from songsmith_mcp.state import Note, get_state, reset_state


def _call(name: str, **args):
    out = _dispatch(name, args)
    payload = json.loads(out[0].text)
    assert "error" not in payload, f"{name} errored: {payload}"
    return payload


# ---------------------------------------------------------------------------
# explain() after accept
# ---------------------------------------------------------------------------

def test_explain_survives_accept_proposal():
    reset_state()
    _call("new_song", key="C major", tempo=100, explain_level="tutor")
    _call("set_form", sections=[{"name": "verse", "bars": 4}])
    prop = _call("write_chords", section="verse", roman_numerals=["I", "V", "vi", "IV"])
    pid = prop["proposal_id"]

    # Before accept: explain works.
    text_before = _explain(pid)
    assert "V" in text_before or "vi" in text_before or prop["summary"] in text_before

    # After accept: explain still works (previously raised KeyError).
    _call("accept_proposal", proposal_id=pid)
    text_after = _explain(pid)
    assert "accepted" in text_after.lower()
    # tutor paragraphs should still render.
    assert "chord" in text_after.lower() or "roman" in text_after.lower()


def test_bulk_accept_also_archives():
    reset_state()
    _call("new_song", key="A minor")
    _call("set_form", sections=[{"name": "intro", "bars": 2}])
    p1 = _call("write_chords", section="intro", roman_numerals=["i", "VII"])
    _call("bulk_accept_proposals")
    # After bulk accept, explain still resolves.
    text = _explain(p1["proposal_id"])
    assert "(This proposal was accepted" in text


def test_accepted_archive_bounded():
    # Hammering accept many times mustn't grow state unbounded.
    reset_state()
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "verse", "bars": 1}])
    for _ in range(140):
        p = _call("write_chords", section="verse", roman_numerals=["I"])
        _call("accept_proposal", proposal_id=p["proposal_id"])
    st = get_state()
    assert len(st.accepted_proposals) <= 128


# ---------------------------------------------------------------------------
# create_empty_clip
# ---------------------------------------------------------------------------

def test_create_empty_clip_opens_blank_canvas():
    reset_state()
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "verse", "bars": 4}])
    out = _call("create_empty_clip", track_name="Sketch", section="verse", role="pad")
    assert out["existed"] is False
    st = get_state()
    clip = st.tracks["Sketch"].clips[0]
    assert clip.notes == []
    assert clip.section == "verse"
    assert clip.length_bars == 4
    # add_note should now work on the blank clip.
    added = _call(
        "add_note",
        track_name="Sketch",
        section="verse",
        pitch=60,
        start_beat=0.0,
        duration_beats=1.0,
    )
    assert added["ok"] is True
    assert len(st.tracks["Sketch"].clips[0].notes) == 1


def test_create_empty_clip_is_idempotent():
    reset_state()
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "verse", "bars": 4}])
    _call("create_empty_clip", track_name="X", section="verse")
    again = _call("create_empty_clip", track_name="X", section="verse")
    assert again["existed"] is True


# ---------------------------------------------------------------------------
# duplicate_clip
# ---------------------------------------------------------------------------

def test_duplicate_clip_across_sections_copies_notes_and_reseats():
    reset_state()
    _call("new_song", key="C major")
    _call(
        "set_form",
        sections=[
            {"name": "verse", "bars": 4},
            {"name": "chorus", "bars": 4},
        ],
    )
    p = _call("write_chords", section="verse", roman_numerals=["I", "V", "vi", "IV"])
    _call("accept_proposal", proposal_id=p["proposal_id"])

    dup = _call(
        "duplicate_clip",
        track_name="Chords",
        from_section="verse",
        to_section="chorus",
    )
    st = get_state()
    # One chord track, two clips now.
    assert len(st.tracks["Chords"].clips) == 2
    verse_clip = [c for c in st.tracks["Chords"].clips if c.section == "verse"][0]
    chorus_clip = [c for c in st.tracks["Chords"].clips if c.section == "chorus"][0]
    # Same note count, same pitches, same relative timing.
    assert len(verse_clip.notes) == len(chorus_clip.notes)
    assert [n.pitch for n in verse_clip.notes] == [n.pitch for n in chorus_clip.notes]
    assert [n.start_beat for n in verse_clip.notes] == [n.start_beat for n in chorus_clip.notes]
    # Re-seated to chorus's start_bar.
    chorus_section = st.section_by_name("chorus")
    assert chorus_clip.start_bar == chorus_section.start_bar
    assert dup["notes_copied"] == len(verse_clip.notes)


def test_duplicate_clip_replace_overwrites_existing():
    reset_state()
    _call("new_song", key="C major")
    _call(
        "set_form",
        sections=[
            {"name": "verse", "bars": 4},
            {"name": "chorus", "bars": 4},
        ],
    )
    pv = _call("write_chords", section="verse", roman_numerals=["I", "V", "vi", "IV"])
    _call("accept_proposal", proposal_id=pv["proposal_id"])
    pc = _call("write_chords", section="chorus", roman_numerals=["IV", "V", "I", "vi"])
    _call("accept_proposal", proposal_id=pc["proposal_id"])

    _call(
        "duplicate_clip",
        track_name="Chords",
        from_section="verse",
        to_section="chorus",
        replace=True,
    )
    st = get_state()
    chorus_clips = [c for c in st.tracks["Chords"].clips if c.section == "chorus"]
    assert len(chorus_clips) == 1  # replaced, not appended
    # Should now contain the verse's notes (I-V-vi-IV → 4 chord blocks).
    assert chorus_clips[0].chord_symbol is not None


# ---------------------------------------------------------------------------
# quantize_clip
# ---------------------------------------------------------------------------

def test_quantize_clip_snaps_to_eighth_grid():
    reset_state()
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "verse", "bars": 2}])
    _call("create_empty_clip", track_name="Lead", section="verse")
    # Add a note slightly off the grid — 0.47 should snap to 0.5 on 1/8.
    _call(
        "add_note",
        track_name="Lead",
        section="verse",
        pitch=60,
        start_beat=0.47,
        duration_beats=0.5,
    )
    _call(
        "add_note",
        track_name="Lead",
        section="verse",
        pitch=62,
        start_beat=1.03,
        duration_beats=0.5,
    )
    out = _call("quantize_clip", track_name="Lead", section="verse", grid="1/8")
    assert out["ok"] is True
    assert out["notes_moved"] == 2
    st = get_state()
    starts = sorted(n.start_beat for n in st.tracks["Lead"].clips[0].notes)
    assert starts == pytest.approx([0.5, 1.0])


def test_quantize_clip_strength_partial():
    reset_state()
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "verse", "bars": 1}])
    _call("create_empty_clip", track_name="L", section="verse")
    _call(
        "add_note",
        track_name="L",
        section="verse",
        pitch=60,
        start_beat=0.4,
        duration_beats=0.25,
    )
    _call("quantize_clip", track_name="L", section="verse", grid="1/4", strength=0.5)
    st = get_state()
    # 0.4 → snapped target is 0 (0.4 rounds to 0 on 1/4). Halfway = 0.2.
    assert st.tracks["L"].clips[0].notes[0].start_beat == pytest.approx(0.2)


def test_quantize_clip_rejects_unknown_grid():
    reset_state()
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "verse", "bars": 1}])
    _call("create_empty_clip", track_name="L", section="verse")
    # _dispatch propagates ValueError; the MCP wrapper (_call_tool) is what
    # turns it into an {"error": ...} payload for clients.
    with pytest.raises(ValueError, match="unknown grid"):
        _dispatch("quantize_clip", {"track_name": "L", "section": "verse", "grid": "bogus"})


# ---------------------------------------------------------------------------
# analyze_section
# ---------------------------------------------------------------------------

def test_analyze_section_classifies_chord_functions():
    reset_state()
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "verse", "bars": 4}])
    p = _call("write_chords", section="verse", roman_numerals=["I", "IV", "V", "I"])
    _call("accept_proposal", proposal_id=p["proposal_id"])

    result = analyze_section("verse")
    assert result["section"] == "verse"
    chords_info = next(
        t for t in result["tracks"].values() if t.get("role") == "chords"
    )
    # Should see tonic + predominant + dominant functional zones.
    funcs = chords_info["functions"]
    assert "tonic" in funcs
    assert "dominant" in funcs
    # Progression ends on tonic → should note 'resolves to tonic'.
    assert any("tonic" in obs.lower() for obs in result["observations"])


def test_analyze_section_reports_melody_range_and_hit_rate():
    reset_state()
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "verse", "bars": 2}])
    p = _call("write_chords", section="verse", roman_numerals=["I", "V"])
    _call("accept_proposal", proposal_id=p["proposal_id"])
    pm = _call(
        "propose_melody",
        section="verse",
        rhythm="eighths",
        contour="arch",
        seed=7,
    )
    _call("accept_proposal", proposal_id=pm["proposal_id"])

    result = analyze_section("verse")
    melody_info = next(
        t for t in result["tracks"].values() if t.get("role") == "melody"
    )
    assert melody_info["note_count"] > 0
    assert isinstance(melody_info["chord_tone_hit_rate"], float)
    assert 0.0 <= melody_info["chord_tone_hit_rate"] <= 1.0
    assert melody_info["shape"] in ("arch", "ascending", "descending", "wave", "flat")


def test_analyze_section_empty_section_nudges_user():
    reset_state()
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "verse", "bars": 4}])
    result = analyze_section("verse")
    assert result["tracks"] == {}
    assert any("propose_chord_progression" in o for o in result["observations"])


# ---------------------------------------------------------------------------
# lesson
# ---------------------------------------------------------------------------

def test_lesson_returns_known_topic():
    out = lesson("chord_function")
    assert out["topic"] == "chord_function"
    assert "tonic" in out["text"].lower()


def test_lesson_normalizes_topic_key():
    assert lesson("Chord-Function")["topic"] == "chord_function"
    assert lesson("modal interchange")["topic"] == "modal_interchange"


def test_lesson_lists_when_omitted():
    out = lesson(None)
    assert "topics" in out
    assert set(out["topics"]) == set(LESSONS.keys())


def test_lesson_unknown_topic_returns_available_list():
    out = lesson("non_existent_xyz")
    assert "error" in out
    assert "available_topics" in out


# ---------------------------------------------------------------------------
# suggest_next_step
# ---------------------------------------------------------------------------

def test_suggest_next_step_empty_state_recommends_new_song():
    reset_state()
    out = suggest_next_step()
    assert out["suggestions"][0]["tool"] == "new_song"


def test_suggest_next_step_after_form_recommends_chords():
    reset_state()
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "verse", "bars": 4}, {"name": "chorus", "bars": 4}])
    out = suggest_next_step()
    tools = [s["tool"] for s in out["suggestions"]]
    assert tools[0] == "propose_chord_progression"


def test_suggest_next_step_after_chords_recommends_melody_or_bass():
    reset_state()
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "verse", "bars": 4}])
    p = _call("write_chords", section="verse", roman_numerals=["I", "V", "vi", "IV"])
    _call("accept_proposal", proposal_id=p["proposal_id"])
    out = suggest_next_step()
    tools = [s["tool"] for s in out["suggestions"]]
    # Chords are present in all sections → should move on to melody/bass/drums.
    assert "propose_melody" in tools or "write_bassline" in tools or "write_drum_pattern" in tools
    assert "propose_chord_progression" not in tools


def test_suggest_next_step_complete_song_recommends_polish():
    reset_state()
    _call("new_song", key="C major")
    _call(
        "build_song",
        sections=[
            {
                "section": "verse",
                "chords": {"roman_numerals": ["I", "V", "vi", "IV"]},
                "bass": {"style": "roots"},
                "drums": {"style": "pop"},
                "melody": {"contour": "arch", "seed": 1},
            }
        ],
        auto_accept=True,
    )
    out = suggest_next_step()
    tools = [s["tool"] for s in out["suggestions"]]
    # All parts in place → recommend polish moves.
    assert any(t in tools for t in ("humanize", "render_song", "view_score"))


# ---------------------------------------------------------------------------
# Server round-trip: the 6 new tools are registered and callable via MCP
# ---------------------------------------------------------------------------

def test_server_lists_all_new_tools():
    import asyncio
    from songsmith_mcp.server import _list_tools

    tools = asyncio.run(_list_tools())
    names = {t.name for t in tools}
    for expected in (
        "create_empty_clip",
        "duplicate_clip",
        "quantize_clip",
        "analyze_section",
        "lesson",
        "suggest_next_step",
    ):
        assert expected in names


def test_server_dispatch_lesson_end_to_end():
    out = _dispatch("lesson", {"topic": "chord_function"})
    payload = json.loads(out[0].text)
    assert payload["topic"] == "chord_function"


def test_server_dispatch_suggest_next_step_end_to_end():
    reset_state()
    out = _dispatch("suggest_next_step", {})
    payload = json.loads(out[0].text)
    assert "suggestions" in payload
