from songsmith_mcp.theory.chords import build_chord_clip, candidate_from_romans
from songsmith_mcp.theory.voice_leading import harmonize_line, revoice_clip
from songsmith_mcp.state import Note


def test_revoice_smooths_motion_between_chords():
    c = candidate_from_romans(["I", "V", "vi", "IV"], "C major")
    clip = build_chord_clip(c, "verse", "Chords", (4, 4), 0, 1)
    revoiced = revoice_clip(clip, style="close")

    def chord_at(notes, t):
        return sorted(n.pitch for n in notes if abs(n.start_beat - t) < 1e-6)

    def total_movement(notes):
        chords = [chord_at(notes, t) for t in (0.0, 4.0, 8.0, 12.0)]
        total = 0
        for a, b in zip(chords, chords[1:]):
            # pair each pitch to its nearest partner
            paired = 0
            for p in a:
                paired += min(abs(p - q) for q in b)
            total += paired
        return total

    assert total_movement(revoiced.notes) <= total_movement(clip.notes) + 1


def test_harmonize_line_returns_expected_voices():
    melody = [Note(pitch=72, start_beat=0.0, duration_beats=1.0, velocity=90)]
    chord_at_beat = {0.0: [60, 64, 67]}
    harmony = harmonize_line(melody, chord_at_beat, voices=2)
    assert len(harmony) == 2
    for voice in harmony:
        assert len(voice) == 1
        # backing voices sit below the melody
        assert voice[0].pitch <= melody[0].pitch
