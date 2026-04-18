from songsmith_mcp.lyrics.align import align_lyrics_to_rhythm
from songsmith_mcp.lyrics.syllabify import count_syllables, syllabify


def test_syllabify_counts_match_expectations():
    # Canonical single/multi-syllable words.
    assert count_syllables("cat") == 1
    assert count_syllables("water") == 2
    assert count_syllables("beautiful") >= 3


def test_syllabify_marks_stress():
    syls = syllabify("beautiful morning")
    assert any(s.stress >= 2 for s in syls), "at least one primary stress expected"


def test_align_one_note_per_syllable():
    line = "tell me what you know about love"
    n = count_syllables(line)
    aligned = align_lyrics_to_rhythm(line, time_sig=(4, 4), rhythm="eighths")
    assert len(aligned.notes) == n
    for note, syl in zip(aligned.notes, aligned.syllables):
        assert note.lyric == syl.text


def test_align_distinct_rhythm_templates_differ():
    line = "the quick brown fox jumps over the lazy dog"
    a = align_lyrics_to_rhythm(line, rhythm="eighths")
    b = align_lyrics_to_rhythm(line, rhythm="quarters")
    # Same syllable count.
    assert len(a.notes) == len(b.notes)
    # Quarter-note alignment takes more total time.
    assert b.bars_used >= a.bars_used
