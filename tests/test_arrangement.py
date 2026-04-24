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


def test_drum_intensity_changes_note_density():
    """Regression: every section used to sound identical because intensity
    only nudged velocity. Light/normal/heavy must now produce materially
    different patterns."""
    light = drums_mod.write_drum_pattern("v", "Drums", style="pop", intensity="light", bars=2)
    normal = drums_mod.write_drum_pattern("v", "Drums", style="pop", intensity="normal", bars=2)
    heavy = drums_mod.write_drum_pattern("v", "Drums", style="pop", intensity="heavy", bars=2)
    assert len(heavy.notes) > len(normal.notes) > len(light.notes)
    # Heavy adds the open hi-hat voice that light/normal don't use.
    assert drums_mod.OPEN_HAT in {n.pitch for n in heavy.notes}
    assert drums_mod.OPEN_HAT not in {n.pitch for n in light.notes}
    # Light drops the crash.
    assert drums_mod.CRASH not in {n.pitch for n in light.notes}


def test_drum_fill_places_toms_in_last_bar():
    """fill=True should replace the last bar's second half with a tom fill
    — the only way sections can meaningfully lift into the next one."""
    no_fill = drums_mod.write_drum_pattern("v", "Drums", style="pop", bars=4, fill=False)
    with_fill = drums_mod.write_drum_pattern("v", "Drums", style="pop", bars=4, fill=True)
    # Toms only appear when fill is on.
    tom_pitches = {drums_mod.TOM_LO, drums_mod.TOM_HI}
    assert not (tom_pitches & {n.pitch for n in no_fill.notes})
    last_bar_notes = [n for n in with_fill.notes if n.start_beat >= 12.0]
    last_bar_pitches = {n.pitch for n in last_bar_notes}
    assert tom_pitches & last_bar_pitches, (
        f"fill=True produced no toms in last bar: {last_bar_pitches}"
    )
