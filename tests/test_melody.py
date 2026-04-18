from songsmith_mcp.theory.melody import humanize, propose_melody
from songsmith_mcp.state import Note


def _rhythm(n=8):
    return [(i * 0.5, 0.5) for i in range(n)]


def test_melody_has_one_note_per_rhythm_slot():
    chords = {0.0: [60, 64, 67], 2.0: [65, 69, 72]}
    cand = propose_melody("C major", chords, _rhythm(8), contour="arch", seed=1)
    assert len(cand.notes) == 8


def test_melody_respects_vocal_range():
    chords = {0.0: [60, 64, 67]}
    cand = propose_melody("C major", chords, _rhythm(16), vocal_range=(60, 72), seed=0)
    for n in cand.notes:
        assert 60 <= n.pitch <= 72


def test_melody_strong_beats_prefer_chord_tones():
    chords = {0.0: [60, 64, 67]}  # C major chord only
    # Strong beats = every beat on a whole integer.
    cand = propose_melody("C major", chords, _rhythm(8), contour="flat", seed=0)
    strong = [n.pitch % 12 for n in cand.notes if abs(n.start_beat - round(n.start_beat)) < 1e-6]
    # majority of strong-beat pitches should be in {C=0, E=4, G=7}
    chord_pcs = {0, 4, 7}
    hits = sum(1 for p in strong if p in chord_pcs)
    assert hits >= len(strong) // 2


def test_humanize_preserves_pitch_and_note_count():
    notes = [Note(pitch=60 + i, start_beat=i * 0.5, duration_beats=0.5, velocity=90) for i in range(8)]
    out = humanize(notes, timing_jitter_ticks=8, velocity_jitter=5, seed=2)
    assert len(out) == len(notes)
    for a, b in zip(notes, out):
        assert a.pitch == b.pitch
        # timing moves slightly
        assert abs(a.start_beat - b.start_beat) < 0.05
        # velocity stays in-range
        assert 1 <= b.velocity <= 127
