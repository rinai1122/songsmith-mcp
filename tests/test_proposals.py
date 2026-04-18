from pathlib import Path

from songsmith_mcp.arrangement import form as form_mod
from songsmith_mcp.hitl import proposals as prop_mod
from songsmith_mcp.reaper_bridge import get_bridge
from songsmith_mcp.state import reset_state
from songsmith_mcp.theory.chords import build_chord_clip, candidate_from_romans


def test_propose_accept_round_trip_writes_midi(tmp_path, monkeypatch):
    monkeypatch.setenv("SONGSMITH_OUT", str(tmp_path))
    # Force a fresh bridge pointed at tmp_path.
    import songsmith_mcp.reaper_bridge as rb
    rb._BRIDGE = None

    st = reset_state()
    form_mod.apply_form(st, [("verse", 4)])

    c = candidate_from_romans(["I", "V", "vi", "IV"], "C major")
    clip = build_chord_clip(c, "verse", "Chords", (4, 4), 0, 1)
    prop = prop_mod.create_proposal("chords", "verse", "Chords", [clip], "I V vi IV")

    assert prop.id in st.proposals
    # Proposal should have written a preview .mid file.
    out = list(tmp_path.glob(f"{prop.id}__*.mid"))
    assert out, "proposal should write a preview midi"

    prop_mod.accept_proposal(prop.id)

    assert prop.id not in st.proposals
    assert "Chords" in st.tracks
    assert len(st.tracks["Chords"].clips) == 1

    # Accept should have written a 'draft'-prefixed midi file too.
    drafts = list(tmp_path.glob("draft__*.mid"))
    assert drafts


def test_reject_removes_proposal():
    st = reset_state()
    form_mod.apply_form(st, [("verse", 4)])
    c = candidate_from_romans(["I"], "C major")
    clip = build_chord_clip(c, "verse", "Chords", (4, 4), 0, 1)
    prop = prop_mod.create_proposal("chords", "verse", "Chords", [clip], "I")
    prop_mod.reject_proposal(prop.id)
    assert prop.id not in st.proposals


def test_diff_proposal_reports_bars_and_notes():
    st = reset_state()
    form_mod.apply_form(st, [("verse", 4)])
    c = candidate_from_romans(["I", "V"], "C major")
    clip = build_chord_clip(c, "verse", "Chords", (4, 4), 0, 2)
    prop = prop_mod.create_proposal("chords", "verse", "Chords", [clip], "I V")
    d = prop_mod.diff_proposal(prop.id)
    assert d["kind"] == "chords"
    assert d["note_count"] >= 6
    assert len(d["bars_touched"]) >= 4
