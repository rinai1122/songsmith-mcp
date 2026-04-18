from songsmith_mcp.theory.chords import (
    ChordCandidate,
    build_chord_clip,
    candidate_from_romans,
    propose_chord_progression,
)


def test_propose_returns_requested_count():
    cands = propose_chord_progression("C major", style="pop", length_bars=4, n_candidates=3, seed=1)
    assert len(cands) == 3
    for c in cands:
        assert isinstance(c, ChordCandidate)
        assert len(c.roman_numerals) == 4
        assert len(c.chord_symbols) == 4
        assert len(c.pitches_by_chord) == 4
        assert c.rationale


def test_minor_key_resolves_i_as_minor_triad():
    c = candidate_from_romans(["i", "VI", "III", "VII"], "A minor")
    # i in A minor = Am = A C E
    root_note_pcs = {p % 12 for p in c.pitches_by_chord[0]}
    assert 9 in root_note_pcs        # A
    assert 0 in root_note_pcs        # C (minor third)


def test_chord_symbols_use_flat_sign():
    c = candidate_from_romans(["I"], "Bb major")
    assert c.chord_symbols[0].startswith("Bb")


def test_build_chord_clip_has_block_chords():
    c = candidate_from_romans(["I", "V", "vi", "IV"], "C major")
    clip = build_chord_clip(c, "verse", "Chords", (4, 4), start_bar=0, bars_per_chord=1)
    assert clip.length_bars == 4
    # 4 chords × ≥3 notes each
    assert len(clip.notes) >= 12
    # Notes at beat 0 are all the I-chord voicing.
    first_chord = [n for n in clip.notes if n.start_beat == 0.0]
    assert len(first_chord) >= 3


def test_style_pop_uses_axis_progression_family():
    cands = propose_chord_progression("C major", style="pop", length_bars=4, seed=0)
    flat = {tuple(c.roman_numerals) for c in cands}
    axis = {("I", "V", "vi", "IV"), ("vi", "IV", "I", "V"), ("I", "vi", "IV", "V")}
    assert flat & axis
