"""Regression tests for the songsmith feedback (2026-04-20):

1. ``build_song`` return payload must stay lean — no per-note arrays.
2. ``observe`` must default to a compact digest; raw notes only on
   ``verbose=true``.
3. Unknown ``style`` / ``contour`` / ``rhythm`` values must fail loudly
   instead of silently falling back to a default.
4. Composite tool schemas must enumerate their valid enum values so
   agents can discover them without opening source.
"""

from __future__ import annotations

import json

import pytest

from songsmith_mcp.arrangement import bass as bass_mod
from songsmith_mcp.arrangement import drums as drums_mod
from songsmith_mcp.lyrics.align import align_lyrics_to_rhythm
from songsmith_mcp.server import _dispatch, _list_tools
from songsmith_mcp.state import get_state, reset_state
from songsmith_mcp.theory import melody as melody_mod


def _call(name: str, **args):
    out = _dispatch(name, args)
    payload = json.loads(out[0].text)
    assert "error" not in payload, f"{name} errored: {payload}"
    return payload


def _call_raw(name: str, **args):
    return json.loads(_dispatch(name, args)[0].text)


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("SONGSMITH_OUT", str(tmp_path))
    import songsmith_mcp.reaper_bridge as rb

    rb._BRIDGE = None
    reset_state()
    yield tmp_path


# ---------------------------------------------------------------------------
# 3. Strict enum validation (no silent fallback)
# ---------------------------------------------------------------------------

def test_unknown_bass_style_raises():
    with pytest.raises(ValueError, match="unknown bass style 'root_eighths'"):
        bass_mod.write_bassline(
            chords_by_beat={0.0: [60, 64, 67]},
            section_name="v",
            track_name="Bass",
            key_str="C major",
            style="root_eighths",
            bars=1,
        )


def test_unknown_drum_style_raises():
    with pytest.raises(ValueError, match="unknown drum style"):
        drums_mod.write_drum_pattern(
            section_name="v", track_name="Drums", style="trap", bars=1
        )


def test_unknown_drum_intensity_raises():
    with pytest.raises(ValueError, match="unknown drum intensity"):
        drums_mod.write_drum_pattern(
            section_name="v",
            track_name="Drums",
            style="pop",
            intensity="nuclear",
            bars=1,
        )


def test_unknown_melody_contour_raises():
    with pytest.raises(ValueError, match="unknown melody contour"):
        melody_mod.propose_melody(
            key_str="C major",
            chords_by_beat={0.0: [60, 64, 67]},
            rhythm=[(0.0, 1.0)],
            contour="zigzag",
        )


def test_unknown_rhythm_raises():
    with pytest.raises(ValueError, match="unknown rhythm"):
        align_lyrics_to_rhythm("hello world", rhythm="double_dotted")


def test_write_bassline_rejects_unknown_style_via_mcp(_fresh):
    """The MCP handler must propagate the ValueError to the caller (as an
    error payload, not silently-ok)."""
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "v", "bars": 4}])
    _call("write_chords", section="v", roman_numerals=["I", "V", "vi", "IV"])
    # Accept the chords so bass has something to read.
    from songsmith_mcp.hitl import proposals as prop_mod

    for pid in list(get_state().proposals):
        prop_mod.accept_proposal(pid)

    # write_bassline catches ValueError and returns {"error": ...} — which is
    # exactly what the feedback wanted: no silent ok:true with wrong output.
    payload = _call_raw("write_bassline", section="v", style="root_eighths")
    assert "error" in payload
    assert "root_eighths" in payload["error"]


# ---------------------------------------------------------------------------
# 2. Compact observe
# ---------------------------------------------------------------------------

def _build_small_song():
    _call("new_song", key="C major", tempo=100)
    _call(
        "set_form",
        sections=[
            {"name": "intro", "bars": 4},
            {"name": "verse", "bars": 8},
        ],
    )
    _call(
        "build_song",
        sections=[
            {
                "section": "intro",
                "chords": {"roman_numerals": ["I", "IV", "V", "I"]},
                "drums": {"style": "pop"},
                "bass": {"style": "roots"},
            },
            {
                "section": "verse",
                "chords": {
                    "roman_numerals": ["I", "V", "vi", "IV", "I", "V", "vi", "IV"]
                },
                "drums": {"style": "pop"},
                "bass": {"style": "roots"},
                "melody": {"contour": "arch", "rhythm": "eighths"},
            },
        ],
        render_audio=False,
        render_score=False,
    )


def test_observe_default_is_compact(_fresh):
    _build_small_song()
    obs = _call("observe")
    assert obs["verbose"] is False
    # Top-level keys the digest must include.
    assert "sections" in obs
    assert "tracks" in obs
    assert "pending_proposal_ids" in obs
    # Tracks must not leak raw notes — only counts.
    for tname, tinfo in obs["tracks"].items():
        assert "note_count" in tinfo
        assert "clip_count" in tinfo
        for c in tinfo["clips"]:
            assert set(c.keys()) == {
                "section",
                "start_bar",
                "length_bars",
                "note_count",
            }
    # "proposals" in the verbose-shape sense must NOT appear in compact mode.
    assert "proposals" not in obs


def test_observe_verbose_returns_full_state(_fresh):
    _build_small_song()
    obs = _call("observe", verbose=True)
    assert obs["verbose"] is True
    assert "tracks" in obs
    # At least one track should have at least one clip with raw `notes`
    # (list of dicts with pitch/start_beat/duration_beats/velocity/lyric).
    found_notes = False
    for tinfo in obs["tracks"].values():
        for clip in tinfo["clips"]:
            if clip.get("notes"):
                found_notes = True
                n = clip["notes"][0]
                assert "pitch" in n and "start_beat" in n
    assert found_notes, "verbose observe must include raw note arrays"


def test_observe_compact_payload_is_small(_fresh):
    """The whole point of the compact form: payload must be bounded by
    the number of tracks/clips, not by the total note count."""
    _build_small_song()
    compact = _dispatch("observe", {})[0].text
    verbose = _dispatch("observe", {"verbose": True})[0].text
    assert len(compact) < len(verbose)
    # The compact digest for a 12-bar 4-track song must stay well under 4 KB.
    assert len(compact) < 4000, f"compact observe too big: {len(compact)} bytes"


# ---------------------------------------------------------------------------
# 1. Lean build_song
# ---------------------------------------------------------------------------

def test_build_song_return_is_lean(_fresh):
    _build_small_song()
    # Re-run to grab the payload directly — _build_small_song discards it.
    reset_state()
    _call("new_song", key="C major")
    _call(
        "set_form",
        sections=[{"name": "intro", "bars": 4}, {"name": "verse", "bars": 8}],
    )
    payload = _call(
        "build_song",
        sections=[
            {
                "section": "intro",
                "chords": {"roman_numerals": ["I", "IV", "V", "I"]},
                "drums": {"style": "pop"},
                "bass": {"style": "roots"},
            },
            {
                "section": "verse",
                "chords": {
                    "roman_numerals": ["I", "V", "vi", "IV", "I", "V", "vi", "IV"]
                },
                "drums": {"style": "pop"},
                "bass": {"style": "roots"},
                "melody": {"contour": "arch", "rhythm": "eighths"},
            },
        ],
        render_audio=False,
        render_score=False,
    )
    assert payload["ok"] is True
    # No raw note arrays anywhere in the payload.
    def _walk(obj):
        if isinstance(obj, dict):
            # 'notes' should only ever appear as a top-level *count* field.
            if "notes" in obj and isinstance(obj["notes"], list):
                pytest.fail(
                    f"build_song payload leaked raw note list: {list(obj.keys())}"
                )
            for v in obj.values():
                _walk(v)
        elif isinstance(obj, list):
            for v in obj:
                _walk(v)

    _walk(payload)

    # Lean per-section summary — proposal_ids, kinds, chord symbols only.
    for s in payload["sections"]:
        assert {"section", "proposal_ids", "proposal_count", "kinds",
                "chord_symbols", "auto_accepted"} <= set(s.keys())

    # Digest is the compact form of SongState — counts, not notes.
    digest = payload["digest"]
    for tinfo in digest["tracks"].values():
        assert "note_count" in tinfo
        for c in tinfo["clips"]:
            assert "notes" not in c


def test_build_song_payload_size_is_bounded(_fresh):
    """A 40-bar 4-track song must still return a payload that fits
    comfortably inside MCP transport limits."""
    _call("new_song", key="C major", tempo=100)
    sections = [
        {"name": "intro", "bars": 4},
        {"name": "verse", "bars": 8},
        {"name": "chorus", "bars": 8},
        {"name": "verse2", "bars": 8},
        {"name": "chorus2", "bars": 8},
        {"name": "outro", "bars": 4},
    ]
    _call("set_form", sections=sections)

    specs = []
    for s in sections:
        specs.append({
            "section": s["name"],
            "chords": {"roman_numerals": ["I", "V", "vi", "IV"]},
            "drums": {"style": "pop"},
            "bass": {"style": "roots"},
            "melody": {"contour": "arch", "rhythm": "eighths"},
        })
    out = _dispatch(
        "build_song",
        {
            "sections": specs,
            "render_audio": False,
            "render_score": False,
        },
    )
    text = out[0].text
    # 40 bars * 4 tracks * notes-per-bar worth of verbose payload would be
    # >100 KB. The lean summary must stay well under 10 KB.
    assert len(text) < 10_000, f"build_song payload too big: {len(text)} bytes"
    payload = json.loads(text)
    assert payload["ok"] is True
    assert len(payload["sections"]) == 6


# ---------------------------------------------------------------------------
# 4. Enums exposed in composite schemas
# ---------------------------------------------------------------------------

def test_build_song_auto_derives_form_when_no_set_form_call(_fresh):
    """Regression: build_song used to fail with a cryptic 'no such section'
    when called right after new_song without set_form. The docstring pitches
    it as a one-call composer, so it now auto-derives the form from the
    sections array — each section sized to len(roman_numerals) *
    bars_per_chord."""
    _call("new_song", key="C major")
    payload = _call(
        "build_song",
        sections=[
            {
                "section": "Intro",
                "chords": {"roman_numerals": ["I", "V", "vi", "IV"]},
            },
            {
                "section": "Verse",
                "chords": {
                    "roman_numerals": ["I", "V", "vi", "IV"],
                    "bars_per_chord": 2,
                },
            },
        ],
        render_audio=False,
        render_score=False,
    )
    assert payload["ok"] is True
    assert not payload["section_errors"]
    # Form was derived: Intro = 4 bars (4 romans × 1), Verse = 8 bars (4 × 2).
    st = get_state()
    assert [(s.name, s.bars, s.start_bar) for s in st.sections] == [
        ("Intro", 4, 0),
        ("Verse", 8, 4),
    ]


def test_build_song_auto_derive_disambiguates_repeats(_fresh):
    """Repeated section names in the sections array must not collapse into
    one — apply_form renames 'verse','verse' → 'verse','verse.2' and the
    per-iteration dispatch must use those resolved names."""
    _call("new_song", key="C major")
    payload = _call(
        "build_song",
        sections=[
            {"section": "verse", "chords": {"roman_numerals": ["I", "V"]}},
            {"section": "verse", "chords": {"roman_numerals": ["vi", "IV"]}},
        ],
        render_audio=False,
        render_score=False,
    )
    st = get_state()
    assert [s.name for s in st.sections] == ["verse", "verse.2"]
    # Each section should have its own chord clip at the right bar.
    chords = st.tracks["Chords"].clips
    assert {c.section for c in chords} == {"verse", "verse.2"}
    starts = {c.section: c.start_bar for c in chords}
    assert starts == {"verse": 0, "verse.2": 2}


def test_build_song_unknown_section_returns_targeted_error(_fresh):
    """If the caller already committed a form and then passes a section name
    that isn't in it, return a helpful error instead of the old cryptic
    KeyError. Auto-derivation is intentionally off in this branch so we
    don't silently trample their form."""
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "verse", "bars": 4}])
    payload = _call_raw(
        "build_song",
        sections=[
            {"section": "chorus", "chords": {"roman_numerals": ["I", "V"]}}
        ],
        render_audio=False,
        render_score=False,
    )
    assert "error" in payload
    assert payload.get("type") == "UnknownSection"
    assert payload.get("missing") == ["chorus"]
    assert "set_form" in payload["error"]


def test_build_song_schema_lists_style_enums():
    import asyncio

    tools = asyncio.run(_list_tools())
    build_song = next(t for t in tools if t.name == "build_song")
    section_items = build_song.inputSchema["properties"]["sections"]["items"]
    drums_props = section_items["properties"]["drums"]["properties"]
    bass_props = section_items["properties"]["bass"]["properties"]
    melody_props = section_items["properties"]["melody"]["properties"]
    assert set(drums_props["style"]["enum"]) == set(drums_mod.DRUM_STYLES)
    assert set(drums_props["intensity"]["enum"]) == set(drums_mod.DRUM_INTENSITIES)
    assert set(bass_props["style"]["enum"]) == set(bass_mod.BASS_STYLES)
    assert set(melody_props["contour"]["enum"]) == set(melody_mod.MELODY_CONTOURS)
    # Rhythm enum must include the documented template names.
    from songsmith_mcp.lyrics.align import RHYTHM_TEMPLATES

    assert set(melody_props["rhythm"]["enum"]) == set(RHYTHM_TEMPLATES)


def test_render_section_schema_lists_style_enums():
    import asyncio

    tools = asyncio.run(_list_tools())
    render_section = next(t for t in tools if t.name == "render_section")
    props = render_section.inputSchema["properties"]
    assert "enum" in props["drums"]["properties"]["style"]
    assert "enum" in props["bass"]["properties"]["style"]
    assert "enum" in props["melody"]["properties"]["contour"]
    assert "enum" in props["melody"]["properties"]["rhythm"]
