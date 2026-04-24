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


def test_syllabify_splits_short_common_words():
    # pyphen alone misses these; the vowel-group fallback catches them.
    assert count_syllables("city") == 2
    assert count_syllables("tonight") == 2
    assert count_syllables("many") == 2
    assert count_syllables("any") == 2
    # Terminal silent 'e' must not get counted as its own syllable.
    assert count_syllables("lone") == 1
    assert count_syllables("fire") == 1


def test_syllabify_splits_hiatus_vowel_pairs():
    """Regression: 'neon' used to come through as one syllable because
    the vowel-group scanner treated 'eo' as a single run. Same story for
    'lion' (io), 'create' (ea is a diphthong in some words but in 'create'
    it's hiatus; covered by 'ie' / 'io' family), 'piano' (ia + io), etc."""
    assert count_syllables("neon") == 2
    assert count_syllables("lion") == 2
    assert count_syllables("create") == 2
    assert count_syllables("piano") == 3
    assert count_syllables("radio") == 3
    # Diphthongs must stay single-syllable — 'rain' (ai), 'boy' (oy).
    assert count_syllables("rain") == 1
    assert count_syllables("boy") == 1
    # Previous guarantees still hold.
    assert count_syllables("beautiful") >= 3
    assert count_syllables("lone") == 1


def test_align_distributes_phrases_across_section():
    """Regression: multi-line lyrics in an 8-bar section used to pack all
    syllables contiguously at the start, leaving dead silence at the end.
    With bars_hint given, each phrase should get its own bar window."""
    lyrics = (
        "neon lights are glowing tonight\n"
        "rhythms pulse across the sky\n"
        "feel the beat and never let go\n"
        "we are electric hearts"
    )
    aligned = align_lyrics_to_rhythm(lyrics, bars_hint=8, rhythm="eighths")
    assert aligned.notes, "should produce notes"
    # At least one note must land in the second half of the 8-bar section.
    second_half_starts = [n.start_beat for n in aligned.notes if n.start_beat >= 16.0]
    assert second_half_starts, (
        "no notes in the second half of the 8-bar window — the aligner "
        "front-loaded the whole lyric again"
    )
    # Phrase boundaries should create detectable gaps (>= 0.5 beats of silence)
    # somewhere between phrases.
    starts = sorted(n.start_beat for n in aligned.notes)
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert any(g >= 1.0 for g in gaps), f"expected phrase gaps, got {gaps}"


def test_align_single_phrase_still_contiguous():
    """Single-phrase input must behave exactly like before — no surprise
    distribution, chronological order, one slot per syllable."""
    aligned = align_lyrics_to_rhythm(
        "tell me what you know about love", bars_hint=4, rhythm="eighths"
    )
    starts = [n.start_beat for n in aligned.notes]
    assert starts == sorted(starts)
    # Eighths contiguously: starts spaced 0.5 apart.
    for a, b in zip(starts, starts[1:]):
        assert b - a == 0.5


def test_align_preserves_chronological_order_syncopated():
    """Regression: syncopated slot-swap used to flip 'lone' and 'ly' in time,
    so the clip sang 'ly-lone' instead of 'lone-ly'."""
    aligned = align_lyrics_to_rhythm(
        "the city hums a lonely song tonight", rhythm="syncopated"
    )
    starts = [n.start_beat for n in aligned.notes]
    assert starts == sorted(starts), "syllables must be in chronological order"
    lyrics = [n.lyric for n in aligned.notes]
    # 'lone' precedes 'ly' in time.
    assert lyrics.index("lone") < lyrics.index("ly")
    # 'to' precedes 'night' in time.
    assert lyrics.index("to") < lyrics.index("night")
