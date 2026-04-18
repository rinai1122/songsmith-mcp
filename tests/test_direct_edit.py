from pathlib import Path

from songsmith_mcp import direct_edit as edit_mod
from songsmith_mcp.arrangement import form as form_mod
from songsmith_mcp.hitl import proposals as prop_mod
from songsmith_mcp.state import reset_state
from songsmith_mcp.theory.chords import build_chord_clip, candidate_from_romans


def _fresh_song_with_chords(tmp_path, monkeypatch):
    monkeypatch.setenv("SONGSMITH_OUT", str(tmp_path))
    import songsmith_mcp.reaper_bridge as rb

    rb._BRIDGE = None

    st = reset_state()
    form_mod.apply_form(st, [("verse", 2)])
    cand = candidate_from_romans(["I", "V"], "C major")
    clip = build_chord_clip(cand, "verse", "Chords", (4, 4), 0, 1)
    prop = prop_mod.create_proposal("chords", "verse", "Chords", [clip], "I V")
    prop_mod.accept_proposal(prop.id)
    return st


def test_edit_note_mutates_and_rerenders(tmp_path, monkeypatch):
    st = _fresh_song_with_chords(tmp_path, monkeypatch)
    original = st.tracks["Chords"].clips[0].notes[0].pitch

    res = edit_mod.edit_note(
        "Chords", "verse", note_index=0, pitch=original + 12, velocity=110
    )
    assert res["ok"]
    assert res["note"]["pitch"] == original + 12
    assert res["note"]["velocity"] == 110
    assert Path(res["written_midi"]).exists()
    # State actually changed.
    assert st.tracks["Chords"].clips[0].notes[0].pitch == original + 12


def test_add_and_delete_note(tmp_path, monkeypatch):
    st = _fresh_song_with_chords(tmp_path, monkeypatch)
    before = len(st.tracks["Chords"].clips[0].notes)

    added = edit_mod.add_note(
        "Chords", "verse", pitch=72, start_beat=0.5, duration_beats=0.5, velocity=80
    )
    assert added["ok"]
    assert len(st.tracks["Chords"].clips[0].notes) == before + 1

    edit_mod.delete_note("Chords", "verse", note_index=added["note_index"])
    assert len(st.tracks["Chords"].clips[0].notes) == before


def test_import_midi_round_trip(tmp_path, monkeypatch):
    st = _fresh_song_with_chords(tmp_path, monkeypatch)
    clip = st.tracks["Chords"].clips[0]
    # The accept path wrote 'draft__Chords__verse.mid' into tmp_path.
    midi_path = tmp_path / "draft__Chords__verse.mid"
    assert midi_path.exists()

    notes_before = len(clip.notes)
    res = edit_mod.import_midi(str(midi_path), "Chords", "verse")
    assert res["ok"]
    assert res["replaced"] is True
    # Round-trip should preserve note count.
    assert res["notes"] == notes_before


def test_import_midi_as_proposal_creates_proposal(tmp_path, monkeypatch):
    st = _fresh_song_with_chords(tmp_path, monkeypatch)
    midi_path = tmp_path / "draft__Chords__verse.mid"

    res = edit_mod.import_midi(
        str(midi_path), "Chords", "verse", as_proposal=True
    )
    assert "proposal_id" in res
    assert res["proposal_id"] in st.proposals
