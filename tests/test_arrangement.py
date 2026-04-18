from songsmith_mcp.arrangement import bass as bass_mod
from songsmith_mcp.arrangement import drums as drums_mod
from songsmith_mcp.arrangement import form as form_mod
from songsmith_mcp.state import reset_state


def test_suggest_form_returns_at_least_one():
    cands = form_mod.suggest_form("pop", 180, tempo=100)
    assert cands
    # total_bars matches the section list sum
    for c in cands:
        assert sum(b for _, b in c.sections) == c.total_bars


def test_apply_form_populates_sections_with_start_bars():
    st = reset_state()
    form_mod.apply_form(st, [("intro", 4), ("verse", 8), ("verse", 8), ("chorus", 8)])
    assert [s.name for s in st.sections] == ["intro", "verse", "verse.2", "chorus"]
    assert [s.start_bar for s in st.sections] == [0, 4, 12, 20]


def test_drum_pattern_has_kick_and_snare():
    clip = drums_mod.write_drum_pattern("verse", "Drums", style="pop", bars=2)
    pitches = {n.pitch for n in clip.notes}
    assert drums_mod.KICK in pitches
    assert drums_mod.SNARE in pitches


def test_bass_roots_places_root_at_each_chord_change():
    chords = {0.0: [60, 64, 67], 4.0: [65, 69, 72], 8.0: [57, 60, 64], 12.0: [55, 59, 62]}
    clip = bass_mod.write_bassline(chords, "verse", "Bass", "C major", style="roots", bars=4)
    # one bass note per chord for 'roots'
    assert len(clip.notes) == 4
    # Root of first chord (C) — note should be a C somewhere in bass octaves.
    assert clip.notes[0].pitch % 12 == 0


def test_walking_bass_has_four_notes_per_bar():
    chords = {0.0: [60, 64, 67], 4.0: [65, 69, 72]}
    clip = bass_mod.write_bassline(chords, "verse", "Bass", "C major", style="walking", bars=2)
    assert len(clip.notes) == 8  # 2 chords * 4 quarter-notes
