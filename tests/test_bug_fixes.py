"""Regression tests for the three songsmith bugs reported 2026-04-18:

1. ``new_song`` leaving stale proposal .mid files in out_dir.
2. No way to accept/reject multiple proposals in one call.
3. Tool-count explosion when building a full multi-section song.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from songsmith_mcp.server import _dispatch
from songsmith_mcp.state import get_state, reset_state


def _call(name: str, **args):
    out = _dispatch(name, args)
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


def test_new_song_purges_stale_proposal_files(_fresh):
    out_dir: Path = _fresh
    _call("new_song", key="C major", tempo=100)
    _call("set_form", sections=[{"name": "verse", "bars": 4}])
    r = _call("write_chords", section="verse", roman_numerals=["I", "V", "vi", "IV"])
    pid = r["proposal_id"]

    # Confirm the preview file exists before we reset.
    assert list(out_dir.glob(f"{pid}__*.mid")), "preview should be written"

    # new_song must wipe stale preview files.
    purge = _call("new_song", key="D major", tempo=120)
    assert any(name.startswith(pid) for name in purge["purged_proposal_files"])
    assert not list(out_dir.glob(f"{pid}__*.mid")), "stale preview should be gone"


def test_bulk_accept_closes_all_pending(_fresh):
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "v1", "bars": 4}, {"name": "v2", "bars": 4}])
    p1 = _call("write_chords", section="v1", roman_numerals=["I", "V"])
    p2 = _call("write_chords", section="v2", roman_numerals=["vi", "IV"])
    assert len(get_state().proposals) == 2

    r = _call("bulk_accept_proposals")  # no ids => accept all pending
    assert r["count"] == 2
    assert not get_state().proposals
    accepted_ids = {a["accepted"] for a in r["accepted"]}
    assert accepted_ids == {p1["proposal_id"], p2["proposal_id"]}


def test_bulk_accept_with_explicit_ids_and_missing(_fresh):
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "v", "bars": 4}])
    p1 = _call("write_chords", section="v", roman_numerals=["I"])

    r = _call(
        "bulk_accept_proposals",
        proposal_ids=[p1["proposal_id"], "prop_doesnotexist"],
    )
    assert r["count"] == 1
    assert r["not_found"] == ["prop_doesnotexist"]


def test_bulk_reject_closes_all_pending(_fresh):
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "v", "bars": 4}])
    _call("write_chords", section="v", roman_numerals=["I", "V", "vi", "IV"])
    _call("write_drum_pattern", section="v", style="pop")
    assert len(get_state().proposals) == 2

    r = _call("bulk_reject_proposals")
    assert r["count"] == 2
    assert not get_state().proposals


def test_render_section_composite_auto_accepts_everything(_fresh):
    _call("new_song", key="A minor", tempo=120)
    _call("set_form", sections=[{"name": "chorus", "bars": 4}])

    r = _call(
        "render_section",
        section="chorus",
        chords={"roman_numerals": ["i", "VI", "III", "VII"]},
        drums={"style": "pop"},
        bass={"style": "roots"},
        melody={"contour": "arch", "rhythm": "eighths"},
        auto_accept=True,
    )
    # Four proposals in one call — but because auto_accept=True, none remain.
    assert len(r["proposal_ids"]) == 4
    assert {"chords", "drums", "bass", "melody"}.issubset(r["summaries"].keys())
    assert not get_state().proposals, "auto_accept should clear all proposals"

    # All four tracks should now exist in state.
    tracks = get_state().tracks
    assert "Chords" in tracks
    assert "Drums" in tracks
    assert "Bass" in tracks
    assert "Melody" in tracks


def test_render_section_without_auto_accept_skips_bass_and_melody(_fresh):
    """When auto_accept=False the chord clip isn't on a real track yet, so
    bass/melody (which read back from tracks) can't run. They're skipped
    rather than failing."""
    _call("new_song", key="C major")
    _call("set_form", sections=[{"name": "v", "bars": 4}])

    r = _call(
        "render_section",
        section="v",
        chords={"roman_numerals": ["I", "V", "vi", "IV"]},
        drums={"style": "pop"},
        bass={"style": "roots"},
        melody={"contour": "arch"},
        auto_accept=False,
    )
    # Chords + drums are independent, so we get both. Bass + melody are
    # skipped because they'd have nothing to read.
    assert len(r["proposal_ids"]) == 2
    assert "chords" in r["summaries"]
    assert "drums" in r["summaries"]
    assert "bass" not in r["summaries"]
    assert "melody" not in r["summaries"]
    # Both still pending.
    assert len(get_state().proposals) == 2
