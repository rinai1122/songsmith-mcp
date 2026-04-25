"""Teacher-mode tools: analysis of committed material, curated lessons, and
contextual next-step suggestions.

These are the counterpart to ``explain`` (which works on proposals): they
answer "what is going on in my song right now?" and "what do I do next?"
without requiring the user to keep a proposal id handy.
"""

from __future__ import annotations

from typing import Any

from music21 import key as m21_key
from music21 import pitch as m21_pitch
from music21 import roman as m21_roman

from ..state import Clip, SongState, Track, get_state


# ---------------------------------------------------------------------------
# analyze_section — prose analysis of one section's committed tracks
# ---------------------------------------------------------------------------

def analyze_section(section_name: str) -> dict[str, Any]:
    """Walk committed clips in ``section_name`` and describe them in prose.

    Returns a dict with ``section``, a ``summary`` one-liner, per-role
    analysis under ``tracks``, and an ``observations`` list of pedagogical
    bullets (things to try, things worth noticing). Designed to be readable
    both by a human and as scaffolding the LLM can quote when coaching.
    """
    st = get_state()
    section = st.section_by_name(section_name)

    per_track: dict[str, dict[str, Any]] = {}
    observations: list[str] = []

    for tname, tr in st.tracks.items():
        clips = [c for c in tr.clips if c.section == section_name]
        if not clips:
            continue
        clip = clips[0]
        if tr.role == "chords":
            per_track[tname] = _analyze_chords(clip, st, observations)
        elif tr.role == "melody":
            per_track[tname] = _analyze_melody(clip, st, observations)
        elif tr.role == "bass":
            per_track[tname] = _analyze_bass(clip, observations)
        elif tr.role == "drums":
            per_track[tname] = _analyze_drums(clip, observations)
        else:
            per_track[tname] = {
                "role": tr.role,
                "note_count": len(clip.notes),
                "length_bars": clip.length_bars,
            }

    summary_parts = [
        f"{section_name} — {section.bars} bars in {st.key}",
    ]
    if any(t.get("role") == "chords" for t in per_track.values()):
        chord_info = next(t for t in per_track.values() if t.get("role") == "chords")
        if chord_info.get("roman_numerals"):
            summary_parts.append("progression " + " – ".join(chord_info["roman_numerals"]))
    if not per_track:
        observations.append(
            "No clips in this section yet. Try `propose_chord_progression` "
            "followed by `propose_melody` to lay down a first pass."
        )

    return {
        "section": section_name,
        "bars": section.bars,
        "start_bar": section.start_bar,
        "key": st.key,
        "tempo": st.tempo,
        "summary": "; ".join(summary_parts),
        "tracks": per_track,
        "observations": observations,
    }


def _analyze_chords(clip: Clip, st: SongState, observations: list[str]) -> dict[str, Any]:
    """Roman-numeral + functional analysis of a chord clip."""
    symbols = (clip.chord_symbol or "").split()
    # Group notes by start_beat to recover each chord's pitch set.
    groups: dict[float, list[int]] = {}
    for n in clip.notes:
        groups.setdefault(n.start_beat, []).append(n.pitch)
    chord_pitch_sets = [sorted(groups[b]) for b in sorted(groups)]

    k = _parse_key(st.key)
    roman_numerals: list[str] = []
    functions: list[str] = []
    for pitches in chord_pitch_sets:
        rn_str, func = _classify_chord(pitches, k)
        roman_numerals.append(rn_str)
        functions.append(func)

    # Beginner-friendly functional arc.
    if functions:
        if functions[-1] in ("tonic",):
            observations.append(
                "Progression resolves to tonic — feels grounded / at-rest at the end of the section."
            )
        elif functions[-1] == "dominant":
            observations.append(
                "Progression ends on the dominant — leaves the ear hanging, great going into the next section."
            )
    if functions.count("predominant") >= 2 and "dominant" in functions:
        observations.append(
            "Predominant → dominant → tonic motion is doing the classical cadence work here."
        )

    return {
        "role": "chords",
        "chord_symbols": symbols,
        "roman_numerals": roman_numerals,
        "functions": functions,
        "chord_count": len(chord_pitch_sets),
        "note_count": len(clip.notes),
    }


def _analyze_melody(clip: Clip, st: SongState, observations: list[str]) -> dict[str, Any]:
    """Range, contour, chord-tone hit rate on strong beats."""
    if not clip.notes:
        return {"role": "melody", "note_count": 0}

    pitches = [n.pitch for n in clip.notes]
    lo, hi = min(pitches), max(pitches)
    range_semitones = hi - lo
    # Contour classification by linear trend of pitch vs. time.
    n = len(pitches)
    if n >= 2:
        first_third = sum(pitches[: max(1, n // 3)]) / max(1, n // 3)
        last_third = sum(pitches[-max(1, n // 3):]) / max(1, n // 3)
        middle_third = sum(pitches[n // 3 : 2 * n // 3]) / max(1, (2 * n // 3) - (n // 3))
        if last_third > first_third + 1 and middle_third < max(first_third, last_third) - 1:
            shape = "wave"
        elif abs(middle_third - first_third) <= 1 and abs(middle_third - last_third) <= 1:
            shape = "flat"
        elif middle_third > first_third + 1 and middle_third > last_third + 1:
            shape = "arch"
        elif last_third > first_third + 2:
            shape = "ascending"
        elif last_third < first_third - 2:
            shape = "descending"
        else:
            shape = "wave"
    else:
        shape = "flat"

    # Chord-tone hit rate on strong beats (whole-beat positions).
    chord_pitches_by_beat = _chord_pc_sets_by_beat(clip.section, st)
    strong_notes = [note for note in clip.notes if abs(note.start_beat - round(note.start_beat)) < 1e-6]
    hits = 0
    for note in strong_notes:
        # Find the chord in effect at note.start_beat (largest key <= start).
        active = None
        for cb in sorted(chord_pitches_by_beat):
            if cb <= note.start_beat:
                active = chord_pitches_by_beat[cb]
        if active is None:
            continue
        if (note.pitch % 12) in active:
            hits += 1
    hit_rate = round(hits / max(1, len(strong_notes)), 2)

    if hit_rate >= 0.7:
        observations.append(
            f"Melody lands on chord tones {int(hit_rate * 100)}% of the time on strong beats — "
            "a very 'in-the-song' feel. For more tension, aim 55–70%."
        )
    elif hit_rate <= 0.35:
        observations.append(
            f"Only {int(hit_rate * 100)}% of strong-beat notes are chord tones — the melody is "
            "wandering off-harmony. Great for jazz/impressionistic tension; grounding a few beats "
            "on chord tones will make it sound more settled."
        )

    if range_semitones > 19:  # > octave + fifth
        observations.append(
            f"Vocal range is {range_semitones} semitones — wide. Consider whether a single "
            "singer can cover it or splitting it across two parts/octaves."
        )

    return {
        "role": "melody",
        "note_count": len(clip.notes),
        "range": [_midi_name(lo), _midi_name(hi)],
        "range_semitones": range_semitones,
        "shape": shape,
        "strong_beat_count": len(strong_notes),
        "chord_tone_hit_rate": hit_rate,
    }


def _analyze_bass(clip: Clip, observations: list[str]) -> dict[str, Any]:
    if not clip.notes:
        return {"role": "bass", "note_count": 0}
    pitches = [n.pitch for n in clip.notes]
    lo, hi = min(pitches), max(pitches)
    return {
        "role": "bass",
        "note_count": len(clip.notes),
        "range": [_midi_name(lo), _midi_name(hi)],
        "average_note_duration": round(sum(n.duration_beats for n in clip.notes) / len(clip.notes), 2),
    }


def _analyze_drums(clip: Clip, observations: list[str]) -> dict[str, Any]:
    if not clip.notes:
        return {"role": "drums", "note_count": 0}
    by_pitch: dict[int, int] = {}
    for n in clip.notes:
        by_pitch[n.pitch] = by_pitch.get(n.pitch, 0) + 1
    kick = by_pitch.get(36, 0)
    snare = by_pitch.get(38, 0)
    hat = by_pitch.get(42, 0) + by_pitch.get(46, 0)
    feel: str
    if hat >= 12:
        feel = "16th-note hats — driving, dance feel"
    elif hat >= 6:
        feel = "8th-note hats — standard backbeat feel"
    elif hat:
        feel = "quarter-note hats — half-time / open feel"
    else:
        feel = "no hats — kick-and-snare only"
    observations.append(f"Drum feel: {feel}.")
    return {
        "role": "drums",
        "note_count": len(clip.notes),
        "kick": kick,
        "snare": snare,
        "hat": hat,
        "feel": feel,
    }


def _chord_pc_sets_by_beat(section_name: str, st: SongState) -> dict[float, set[int]]:
    """Pitch-class sets of each chord onset, for chord-tone hit-rate analysis."""
    out: dict[float, set[int]] = {}
    for tr in st.tracks.values():
        if tr.role != "chords":
            continue
        for clip in tr.clips:
            if clip.section != section_name:
                continue
            groups: dict[float, set[int]] = {}
            for n in clip.notes:
                groups.setdefault(n.start_beat, set()).add(n.pitch % 12)
            out.update(groups)
    return out


def _classify_chord(pitches: list[int], k: m21_key.Key) -> tuple[str, str]:
    """Best-effort Roman numeral + functional label for a pitch set."""
    if not pitches:
        return ("?", "unknown")
    try:
        # Let music21 figure out the Roman numeral.
        from music21 import chord as m21_chord
        c = m21_chord.Chord([int(p) for p in pitches])
        rn = m21_roman.romanNumeralFromChord(c, k)
        rn_str = rn.figure
        # Functional zone: tonic / predominant / dominant.
        deg = rn.scaleDegree
        if deg in (1, 6):
            func = "tonic"
        elif deg in (2, 4):
            func = "predominant"
        elif deg in (5, 7):
            func = "dominant"
        else:
            func = "other"
        return rn_str, func
    except Exception:
        return ("?", "unknown")


def _parse_key(key_str: str) -> m21_key.Key:
    s = key_str.strip()
    if " " in s:
        tonic, mode_txt = s.split(None, 1)
        mode = "minor" if mode_txt.strip().lower().startswith("min") else "major"
    elif s.lower().endswith("min"):
        tonic, mode = s[:-3], "minor"
    elif s.endswith("m") and len(s) > 1 and s[-2] not in "bB":
        tonic, mode = s[:-1], "minor"
    else:
        tonic, mode = s, "major"
    tonic = tonic.strip()
    if len(tonic) >= 2 and tonic[1] == "b":
        tonic = tonic[0] + "-" + tonic[2:]
    return m21_key.Key(tonic, mode)


def _midi_name(m: int) -> str:
    return m21_pitch.Pitch(midi=m).nameWithOctave


# ---------------------------------------------------------------------------
# lesson — curated pedagogical explanations
# ---------------------------------------------------------------------------

# Lessons are plain prose with concrete examples embedded inline. Kept short
# (200-400 words) so an LLM client can surface them directly in chat without
# chunking. Edit these to improve teaching quality — they're the canonical
# pedagogy surface of the project.
LESSONS: dict[str, str] = {
    "chord_function": (
        "Every chord in a key has a *function* — its role in creating or "
        "releasing tension.\n\n"
        "* **Tonic (I, vi)** — home base. The ear hears rest.\n"
        "* **Predominant (ii, IV)** — leaves home, builds energy.\n"
        "* **Dominant (V, vii°)** — peak tension, wants to resolve back to I.\n\n"
        "Most Western pop moves tonic → predominant → dominant → tonic. The "
        "classic I–IV–V–I (C–F–G–C in C major) is literally that arc. When you "
        "swap ii for IV you get the jazz/pop ii–V–I (Dm–G–C in C). Same function, "
        "different colour.\n\n"
        "Try this: pick a four-chord loop and identify the function of each. If "
        "two predominants land in a row (IV, ii, IV) the section *pushes* longer; "
        "if dominant resolves quickly (V–I) the section feels decisive."
    ),
    "voice_leading": (
        "Voice leading is how the individual notes of successive chords move.\n\n"
        "Two rules cover 80% of the good-sounding cases:\n"
        "1. **Keep common tones.** If two chords share a note (C and Am both "
        "contain C and E), hold those notes; only move the ones that must change.\n"
        "2. **Move by the smallest step.** A voice jumping a fifth sounds "
        "disjointed; a voice moving a tone or semitone sounds smooth.\n\n"
        "In practice, 'revoice' your chords so the top notes trace a little "
        "melody of their own. C–Am–F–G voiced with all roots on the bottom "
        "but tops on G–E–F–G (a neighbour motion) will sound more connected "
        "than the same chords voiced identically."
    ),
    "melodic_contour": (
        "Contour is the *shape* a melody draws in time.\n\n"
        "Common shapes:\n"
        "* **Arch** — rise, peak, fall. Natural, speech-like; most folk songs "
        "use this.\n"
        "* **Descending** — starts high, comes down. Feels resolving, wistful "
        "(think 'Mary Had a Little Lamb').\n"
        "* **Ascending** — starts low, climbs. Builds energy; great for "
        "pre-choruses.\n"
        "* **Wave** — up-down-up-down. Conversational, never quite lands.\n\n"
        "The strong-beat pitches are what the ear remembers — even if the "
        "melody has 30 notes, track where they hit on beats 1 and 3. That's "
        "your contour. Everything in between is ornament."
    ),
    "song_form": (
        "Form shapes energy over time. A pop song typically runs:\n\n"
        "**intro → verse → pre-chorus → chorus → verse → pre-chorus → chorus → "
        "bridge → chorus → outro**\n\n"
        "Each section has a job:\n"
        "* **Verse** — tells the story, stays fairly flat energy.\n"
        "* **Pre-chorus** — lifts. Often uses predominant chords (IV, ii) to "
        "set up the chorus.\n"
        "* **Chorus** — the hook. Lands on tonic, repeats the title.\n"
        "* **Bridge** — breaks the pattern. New chords (often iii or vi), new "
        "melodic register. Makes the final chorus feel earned.\n\n"
        "When a chorus repeats for the third time the ear is tired of it; the "
        "bridge exists to refresh the listener's attention."
    ),
    "syncopation": (
        "Syncopation is rhythm that emphasises weak beats.\n\n"
        "In 4/4, beats 1 and 3 are strong; 2 and 4 are weak. Landing notes on "
        "the weak beats — or *between* them (the 'and' of 2) — creates "
        "syncopation. Funk, reggae, and most pop melodies live here.\n\n"
        "A minimal example: play four quarter notes (1 2 3 4) vs. four notes "
        "on the *ands* (1& 2& 3& 4&). The second feels like it's leaning "
        "forward. Mix both in one bar and you get the classic pop lilt: "
        "'1 __ 2& __ 4' is the skeleton of half the radio hits of the last "
        "thirty years.\n\n"
        "Rule of thumb: the kick lands on strong beats, the snare on weak; "
        "syncopation is where the *melody* pulls against that grid."
    ),
    "chord_tones_vs_tensions": (
        "Every chord splits the 12 chromatic notes into two groups: *chord "
        "tones* (the notes of the chord itself) and *tensions* (everything "
        "else in the scale).\n\n"
        "For C major (C-E-G) in the key of C, chord tones are C, E, G; tensions "
        "are D, F, A, B. A melody singing C-E-G over the chord will sound "
        "completely at rest — maybe too much. A melody singing F or B will "
        "feel like it wants to move — F pulls to E, B pulls to C.\n\n"
        "The pop-writer's secret: put chord tones on strong beats, tensions on "
        "off-beats. The strong beats anchor the harmony; the off-beats give "
        "motion. If you flip it — tension on 1, chord tone on the 'and' — you "
        "get a sophisticated, jazz-inflected sound. Both work; pick the one "
        "that matches the feel you want."
    ),
    "secondary_dominant": (
        "A secondary dominant is 'the V of something that isn't the tonic'.\n\n"
        "In C major, the V chord is G. The V chord of *vi* (Am) would be E — "
        "because E is the dominant of A. We write this as V/vi.\n\n"
        "Why bother? V/vi in C major introduces a G#, a note not in C major, "
        "and that *surprise* colour makes the following vi chord feel special. "
        "You hear this constantly: 'Something' by the Beatles uses V7/vi "
        "before the vi chord. 'Stand by Me' uses V/ii.\n\n"
        "Formula: to add a secondary dominant before any chord (other than I), "
        "take that chord's root and build a major (or dominant 7) chord a "
        "fifth above it. Insert for one beat before the target. Done."
    ),
    "modal_interchange": (
        "Modal interchange is borrowing a chord from the parallel minor (or "
        "major) of the current key.\n\n"
        "Key of C major. The parallel minor is C minor. C minor contains bIII "
        "(Eb), bVI (Ab), and bVII (Bb). Drop any of those into C major and you "
        "get an instant colour shift — darker, more wistful.\n\n"
        "Concrete moves:\n"
        "* **I → bVII → IV** — C → Bb → F. Rock staple, Mixolydian feel.\n"
        "* **I → bVI → bVII → I** — C → Ab → Bb → C. Cinematic lift.\n"
        "* **iv in major** — F minor in C major. Very classic; paired with a "
        "IV right before it (F → Fm → C) sounds like a sigh.\n\n"
        "Use sparingly: one borrowed chord per section is usually enough. More "
        "than that and the key feels unclear."
    ),
    "cadences": (
        "A cadence is the chord move that ends a phrase.\n\n"
        "* **Authentic (V → I)** — the strongest resolution; 'period at the "
        "end of a sentence'.\n"
        "* **Plagal (IV → I)** — 'amen' cadence; softer than authentic, very "
        "common in gospel.\n"
        "* **Deceptive (V → vi)** — sets up I, delivers vi instead. A gentle "
        "surprise; perfect for an inner-verse ending.\n"
        "* **Half cadence (… → V)** — ends on the dominant. Feels unfinished; "
        "use it to push into the next section.\n\n"
        "Different cadence for each section-end is a great way to structure a "
        "longer song — e.g. half cadence at the end of verse 1, authentic at "
        "the end of chorus 1, deceptive at the end of verse 2 to set up the "
        "bridge."
    ),
    "rhythm_templates": (
        "Songsmith's lyric-to-rhythm aligner supports five templates:\n\n"
        "* **quarters** — one syllable per beat. Slow, speech-like. Use for "
        "hymns, folk ballads, declamatory verses.\n"
        "* **eighths** — two syllables per beat. Default pop rate.\n"
        "* **dotted** — alternating long-short-long-short. Lopes, reggae feel.\n"
        "* **syncopated** — leans on the & of 2; the classic radio-pop pulse.\n"
        "* **waltz** — 3/4 grouping; strong-weak-weak.\n\n"
        "Picking the right template first saves a lot of editing. If you write "
        "your lyrics naturally before thinking about rhythm, read them aloud "
        "and feel where the natural stresses land — that tells you which "
        "template will fit."
    ),
}


def lesson(topic: str | None = None) -> dict[str, Any]:
    """Return a curated lesson on ``topic``, or the topic list if omitted."""
    if not topic:
        return {
            "topics": sorted(LESSONS.keys()),
            "hint": "call lesson(topic='chord_function') to see any single topic.",
        }
    key = topic.strip().lower().replace("-", "_").replace(" ", "_")
    if key not in LESSONS:
        return {
            "error": f"no lesson for {topic!r}",
            "available_topics": sorted(LESSONS.keys()),
        }
    return {"topic": key, "text": LESSONS[key]}


# ---------------------------------------------------------------------------
# suggest_next_step — contextual coaching
# ---------------------------------------------------------------------------

def suggest_next_step() -> dict[str, Any]:
    """Look at the current SongState and return up to 3 ordered suggestions.

    Each suggestion names a tool to call, the args to call it with, and a
    one-line reason. The list is ordered by priority — the first entry is
    the single most valuable thing to do next.
    """
    st = get_state()
    suggestions: list[dict[str, Any]] = []

    # 0. No song started.
    if not st.sections and not st.tracks:
        suggestions.append({
            "tool": "new_song",
            "args": {"key": "C major", "tempo": 100, "style_hint": "pop"},
            "reason": "no song state — start a fresh song.",
        })
        return {"suggestions": suggestions, "state_summary": "empty"}

    # 1. No form committed.
    if not st.sections:
        suggestions.append({
            "tool": "suggest_form",
            "args": {"style": st.style_hint or "pop"},
            "reason": "no sections yet — pick a form so generators can target specific sections.",
        })
        return {"suggestions": suggestions, "state_summary": "no_form"}

    # 2. Which sections lack which parts?
    sections_with_chords = _sections_with_role(st, "chords")
    sections_with_melody = _sections_with_role(st, "melody")
    sections_with_bass = _sections_with_role(st, "bass")
    sections_with_drums = _sections_with_role(st, "drums")

    missing_chords = [s.name for s in st.sections if s.name not in sections_with_chords]
    missing_melody = [s.name for s in st.sections if s.name not in sections_with_melody]
    missing_bass = [s.name for s in st.sections if s.name not in sections_with_bass]
    missing_drums = [s.name for s in st.sections if s.name not in sections_with_drums]

    if missing_chords:
        target = missing_chords[0]
        suggestions.append({
            "tool": "propose_chord_progression",
            "args": {"section": target, "style": st.style_hint or "pop"},
            "reason": f"section {target!r} has no harmony yet — chords anchor every other layer.",
        })
    if missing_melody and not missing_chords:
        target = missing_melody[0]
        suggestions.append({
            "tool": "propose_melody",
            "args": {"section": target, "contour": "arch"},
            "reason": f"section {target!r} has chords but no melody — a vocal line is usually the biggest single-tool upgrade.",
        })
    if missing_bass and not missing_chords:
        target = missing_bass[0]
        suggestions.append({
            "tool": "write_bassline",
            "args": {"section": target, "style": "roots"},
            "reason": f"section {target!r} has no bass — bass roots lock the harmony to the floor.",
        })
    if missing_drums:
        target = missing_drums[0]
        suggestions.append({
            "tool": "write_drum_pattern",
            "args": {"section": target, "style": "pop", "intensity": "normal"},
            "reason": f"section {target!r} has no drums — even a simple kick/snare pattern establishes tempo for the listener.",
        })

    # 3. Everything exists — recommend polish.
    if not (missing_chords or missing_melody or missing_bass or missing_drums):
        suggestions.append({
            "tool": "humanize",
            "args": {"track_name": "Melody", "timing_jitter_ticks": 6, "velocity_jitter": 8},
            "reason": "all core parts are in place — add small timing/velocity variation to break out of the grid feel.",
        })
        suggestions.append({
            "tool": "render_song",
            "args": {},
            "reason": "render to wav/mp3 and listen — the fastest way to hear what's actually working.",
        })
        suggestions.append({
            "tool": "view_score",
            "args": {},
            "reason": "export notation to verify voice leading visually.",
        })

    # Cap to top 3 so callers can surface this cleanly.
    return {
        "suggestions": suggestions[:3],
        "state_summary": {
            "sections": [s.name for s in st.sections],
            "missing_chords": missing_chords,
            "missing_melody": missing_melody,
            "missing_bass": missing_bass,
            "missing_drums": missing_drums,
        },
    }


def _sections_with_role(st: SongState, role: str) -> set[str]:
    out: set[str] = set()
    for tr in st.tracks.values():
        if tr.role != role:
            continue
        for c in tr.clips:
            if c.notes:  # skip empty placeholder clips
                out.add(c.section)
    return out
