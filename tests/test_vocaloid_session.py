"""Replay of the vocaloid-song session, with guards against the specific
bugs that surfaced last time:

* ``suggest_form`` rationales must not call a gap of tens-of-bars "slightly".
* ``propose_chord_progression`` must return ``n_candidates`` *distinct*
  progressions when the style pool + rotation space allows.
* The Aeolian-descent rationale must capitalise ``VI`` (major triad in
  natural minor), not ``vi``.
* Every ``write_chords`` call must return within a short budget — a proxy
  for "the reapy online-mode code isn't holding the main loop."
* The full section list (pop_standard with auto-disambiguated repeats like
  ``verse.2`` / ``chorus.3``) must accept chords end-to-end.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from songsmith_mcp.server import _dispatch
from songsmith_mcp.state import reset_state


# A single write_chords call should be well under a second; we give it 10s
# of slack because CI runners aren't fast, but anything near a minute means
# something is very wrong (this is the hang-regression guard).
WRITE_CHORDS_BUDGET_S = 10.0


def _call(name: str, **args):
    out = _dispatch(name, args)
    assert len(out) == 1
    payload = json.loads(out[0].text)
    assert "error" not in payload, f"{name} errored: {payload}"
    return payload


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("SONGSMITH_OUT", str(tmp_path))
    monkeypatch.setenv("SONGSMITH_DISABLE_REAPER", "1")
    import songsmith_mcp.reaper_bridge as rb
    rb._DISABLE_REAPER = True
    rb._BRIDGE = None
    reset_state()
    yield tmp_path


def test_suggest_form_does_not_lie_about_huge_deltas(_fresh):
    _call("new_song", key="A minor", tempo=172, style_hint="vocaloid")
    forms = _call("suggest_form", style="pop", target_duration_s=180)
    assert forms, "suggest_form returned nothing"
    for f in forms:
        gap = abs(f["total_bars"] - 129)  # target at 172bpm/180s ≈ 129 bars
        if gap > 16:
            assert "slightly" not in f["rationale"].lower(), (
                f"rationale for {f['name']} says 'slightly' but gap is {gap} bars: "
                f"{f['rationale']!r}"
            )


def test_propose_chord_progression_returns_distinct_candidates(_fresh):
    _call("new_song", key="A minor", tempo=172)
    _call("set_form", sections=[{"name": "intro", "bars": 4}])
    # Minor pop has 3 patterns; asking for 3 must give 3 distinct ones.
    out = _call("propose_chord_progression",
                section="intro", style="pop", length_bars=4, n_candidates=3)
    romans = [tuple(c["roman_numerals"]) for c in out["candidates"]]
    assert len(set(romans)) == len(romans), f"duplicate candidates: {romans}"

    # Even when the style pool is smaller than the request, we should dedup
    # via rotation rather than echoing the same pattern twice.
    out5 = _call("propose_chord_progression",
                 section="intro", style="rock", length_bars=4, n_candidates=5)
    romans5 = [tuple(c["roman_numerals"]) for c in out5["candidates"]]
    # At least 2 distinct — stronger than the rock pool's raw count of 2
    # because we rotate to pad; weaker than strict uniqueness because at
    # some point there genuinely are no more rotations.
    assert len(set(romans5)) >= 2


def test_aeolian_descent_rationale_uses_uppercase_VI(_fresh):
    _call("new_song", key="A minor", tempo=172)
    _call("set_form", sections=[{"name": "intro", "bars": 4}])
    out = _call("propose_chord_progression",
                section="intro", style="pop", length_bars=4, n_candidates=3, seed=0)
    aeolian = [c for c in out["candidates"]
               if tuple(c["roman_numerals"]) == ("i", "VI", "III", "VII")]
    assert aeolian, "expected the Aeolian-descent progression in the minor-pop pool"
    r = aeolian[0]["rationale"]
    assert "VI and III" in r, r
    # And make sure we didn't regress to the old lowercase 'vi' anywhere.
    assert " vi " not in f" {r} ", r


def test_full_vocaloid_song_end_to_end(_fresh):
    """Exercise every section of a pop_standard-shaped minor-key song,
    including auto-disambiguated repeats (verse.2, chorus.2, chorus.3)."""
    out_dir: Path = _fresh
    _call("new_song", key="A minor", tempo=172, style_hint="vocaloid",
          explain_level="tutor")

    _call("set_form", sections=[
        {"name": "intro",     "bars": 4},
        {"name": "verse",     "bars": 8},
        {"name": "prechorus", "bars": 4},
        {"name": "chorus",    "bars": 8},
        {"name": "verse",     "bars": 8},
        {"name": "prechorus", "bars": 4},
        {"name": "chorus",    "bars": 8},
        {"name": "bridge",    "bars": 8},
        {"name": "chorus",    "bars": 8},
        {"name": "outro",     "bars": 4},
    ])

    state = _call("observe")
    section_names = [s["name"] for s in state["sections"]]
    assert "verse.2" in section_names
    assert "chorus.3" in section_names

    # A progression per distinct section role, then materialized to *every*
    # instance of that role (including verse.2 etc.).
    plans = {
        "intro":       ["i", "VI", "III", "VII"],
        "verse":       ["i", "VII", "VI", "V"],
        "prechorus":   ["VI", "VII", "i", "V"],
        "chorus":      ["VI", "III", "VII", "i"],
        "bridge":      ["iv", "V", "i", "i"],
        "outro":       ["i", "VI", "i", "i"],
    }

    for sec in section_names:
        role = sec.split(".")[0]
        romans = plans[role]
        t0 = time.monotonic()
        chords = _call("write_chords", section=sec, roman_numerals=romans,
                       bars_per_chord=1,
                       rationale=f"{role}-appropriate vocaloid harmony")
        elapsed = time.monotonic() - t0
        assert elapsed < WRITE_CHORDS_BUDGET_S, (
            f"write_chords on {sec} took {elapsed:.1f}s "
            f"(budget {WRITE_CHORDS_BUDGET_S}s) — hang regression?"
        )
        _call("accept_proposal", proposal_id=chords["proposal_id"])

    # Drums + bass under every section.
    for sec in section_names:
        drums = _call("write_drum_pattern", section=sec,
                      style="edm", intensity="normal")
        _call("accept_proposal", proposal_id=drums["proposal_id"])
        bass = _call("write_bassline", section=sec, style="roots", seed=1)
        _call("accept_proposal", proposal_id=bass["proposal_id"])

    # One melody on the main chorus.
    melody = _call("propose_melody", section="chorus",
                   lyrics="echoes in the neon rain tonight",
                   contour="arch", rhythm="eighths", seed=1)
    _call("accept_proposal", proposal_id=melody["proposal_id"])

    final = _call("observe")
    assert final["pending_proposal_ids"] == []
    assert {"Chords", "Drums", "Bass", "Melody"}.issubset(final["tracks"].keys())

    # A .mid on disk for each accepted clip.
    mids = list(out_dir.glob("*.mid"))
    # 10 sections × (chords + drums + bass) × 2 writes per accept (draft+commit)
    # + 1 melody × 2 = at minimum 61 files; we just assert "lots".
    assert len(mids) >= 40
