"""Chord progression generation + realization as MIDI block chords.

We work in Roman numerals so the same progression reads sensibly in any key,
and we lean on ``music21`` for analysis, transposition, and voice leading.

Each public function returns plain Python data (no music21 objects in the
API surface) so the MCP tool schema stays simple.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

from music21 import chord as m21_chord
from music21 import key as m21_key
from music21 import pitch as m21_pitch
from music21 import roman

from ..state import Clip, Note


# ---------------------------------------------------------------------------
# Style catalog — curated, theory-sound progressions per mood / genre
# ---------------------------------------------------------------------------

# Major-key progressions (Roman numerals as music21 understands them).
_STYLE_MAJOR: dict[str, list[list[str]]] = {
    "pop":      [["I", "V", "vi", "IV"],
                 ["vi", "IV", "I", "V"],
                 ["I", "vi", "IV", "V"]],
    "ballad":   [["I", "iii", "IV", "V"],
                 ["I", "V/vi", "vi", "IV"],
                 ["IMaj7", "iii7", "vi7", "IV"]],
    "rock":     [["I", "bVII", "IV", "I"],
                 ["I", "V", "IV", "I"]],
    "folk":     [["I", "IV", "V", "I"],
                 ["I", "vi", "ii", "V"]],
    "jazz":     [["IMaj7", "vi7", "ii7", "V7"],
                 ["IMaj7", "VI7", "ii7", "V7"]],
    "rnb":      [["IMaj7", "iii7", "vi7", "V7"],
                 ["I", "iii", "vi", "V"]],
    "edm":      [["vi", "IV", "I", "V"],
                 ["I", "V", "vi", "IV"]],
    "vocaloid": [["IV", "V", "iii", "vi"],           # royal road
                 ["IV", "V", "I", "vi"],
                 ["vi", "IV", "I", "V"],
                 ["IV", "V", "vi", "I"]],
    "default":  [["I", "V", "vi", "IV"],
                 ["vi", "IV", "I", "V"],
                 ["I", "IV", "V", "vi"]],
}

# Minor-key progressions (natural/harmonic minor — the V is major by default).
_STYLE_MINOR: dict[str, list[list[str]]] = {
    "pop":      [["i", "VI", "III", "VII"],
                 ["i", "VII", "VI", "V"],
                 ["i", "iv", "VII", "III"]],
    "ballad":   [["i", "VI", "iv", "V"],
                 ["i", "III", "VII", "VI"]],
    "rock":     [["i", "VII", "VI", "V"],
                 ["i", "iv", "v", "i"]],
    "folk":     [["i", "VII", "VI", "VII"]],
    "jazz":     [["i7", "iv7", "VII7", "IIIMaj7"],
                 ["i7", "VI7", "ii°7", "V7"]],
    "rnb":      [["i7", "iv7", "VI", "V7"],
                 ["i", "VI", "III", "V"]],
    "edm":      [["i", "VI", "III", "VII"],
                 ["i", "VII", "VI", "VII"],
                 ["i", "v", "VI", "iv"]],
    "sad":      [["i", "VI", "iv", "V"],
                 ["i", "iv", "i", "V"]],
    "vocaloid": [["VI", "VII", "i", "i"],            # minor-key 'descending cadence' vocaloid feel
                 ["i", "VII", "VI", "V"],            # Andalusian — very common in vocaloid
                 ["iv", "V", "III", "VI"],           # a minor royal-road
                 ["i", "VI", "III", "VII"],
                 ["i", "iv", "VII", "III"]],
    "default":  [["i", "VI", "III", "VII"],
                 ["i", "VII", "VI", "V"],
                 ["i", "iv", "VII", "III"]],
}


# Accept common spelling variants so "future-pop" / "future pop" / "vocaloid-style"
# all route to the vocaloid catalog rather than falling through to "default" and
# producing repeated candidates.
_STYLE_ALIASES: dict[str, str] = {
    "future-pop": "vocaloid",
    "future pop": "vocaloid",
    "futurepop": "vocaloid",
    "vocaloid-style": "vocaloid",
    "anime": "vocaloid",
    "jpop": "vocaloid",
    "j-pop": "vocaloid",
    "kpop": "pop",
    "k-pop": "pop",
    "dance": "edm",
    "electronic": "edm",
    "house": "edm",
    "trance": "edm",
    "blues": "rock",
    "country": "folk",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class ChordCandidate:
    roman_numerals: list[str]
    chord_symbols: list[str]      # realized in the current key (e.g. ["Am", "F", "C", "G"])
    pitches_by_chord: list[list[int]]  # MIDI note numbers per chord
    rationale: str


def propose_chord_progression(
    key_str: str,
    style: str = "pop",
    length_bars: int = 4,
    n_candidates: int = 3,
    seed: int | None = None,
) -> list[ChordCandidate]:
    """Return ``n_candidates`` progressions appropriate for (key, style)."""

    rng = random.Random(seed)
    k = _parse_key(key_str)
    is_minor = k.mode == "minor"
    catalog = _STYLE_MINOR if is_minor else _STYLE_MAJOR
    style_key = style.lower().strip()
    style_key = _STYLE_ALIASES.get(style_key, style_key)
    pool = catalog.get(style_key, catalog["default"])

    # Build a candidate list, looping/truncating the pattern to length_bars.
    # Dedup across the primary pick and the rotation fallback: users asking for
    # 3 candidates expect 3 *distinct* progressions.
    chosen: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()

    pool_copy = list(pool)
    rng.shuffle(pool_copy)
    for pattern in pool_copy:
        fitted = _fit_pattern(pattern, length_bars)
        key = tuple(fitted)
        if key in seen:
            continue
        chosen.append(fitted)
        seen.add(key)
        if len(chosen) >= n_candidates:
            break

    if len(chosen) < n_candidates:
        # Pad with non-identity rotations of the pool.
        rotations: list[list[str]] = []
        for base in pool:
            for rot in range(1, len(base)):
                rotations.append(base[rot:] + base[:rot])
        rng.shuffle(rotations)
        for rot_pat in rotations:
            fitted = _fit_pattern(rot_pat, length_bars)
            key = tuple(fitted)
            if key in seen:
                continue
            chosen.append(fitted)
            seen.add(key)
            if len(chosen) >= n_candidates:
                break

    # Final fallback: draw from the cross-style pool so we don't duplicate.
    if len(chosen) < n_candidates:
        cross_pool: list[list[str]] = []
        for patterns in catalog.values():
            cross_pool.extend(patterns)
        rng.shuffle(cross_pool)
        for pattern in cross_pool:
            fitted = _fit_pattern(pattern, length_bars)
            key = tuple(fitted)
            if key in seen:
                continue
            chosen.append(fitted)
            seen.add(key)
            if len(chosen) >= n_candidates:
                break

    # Absolute last-resort: return what we have rather than silently duplicate.
    # Callers should handle len(result) < n_candidates, but given the cross-pool
    # fallback above this branch should be unreachable in practice.

    out: list[ChordCandidate] = []
    for pattern in chosen:
        symbols, pitches = _realize_pattern(pattern, k)
        out.append(
            ChordCandidate(
                roman_numerals=pattern,
                chord_symbols=symbols,
                pitches_by_chord=pitches,
                rationale=_rationale_for(pattern, k, style),
            )
        )
    return out


def build_chord_clip(
    candidate: ChordCandidate,
    section_name: str,
    track_name: str,
    time_sig: tuple[int, int],
    start_bar: int,
    bars_per_chord: int = 1,
) -> Clip:
    """Materialize a ChordCandidate into block-chord MIDI notes on a single clip."""
    beats_per_bar = time_sig[0] * (4 / time_sig[1])
    notes: list[Note] = []
    for i, pitches in enumerate(candidate.pitches_by_chord):
        start_beat = i * bars_per_chord * beats_per_bar
        dur = bars_per_chord * beats_per_bar
        for p in pitches:
            notes.append(Note(pitch=p, start_beat=start_beat, duration_beats=dur, velocity=80))
    length = len(candidate.pitches_by_chord) * bars_per_chord
    return Clip(
        track=track_name,
        section=section_name,
        notes=notes,
        start_bar=start_bar,
        length_bars=length,
        chord_symbol=" ".join(candidate.chord_symbols),
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _parse_key(key_str: str) -> m21_key.Key:
    """Accept 'C', 'Am', 'C major', 'A minor', 'F#m', 'Bb major' etc."""
    s = key_str.strip()
    if " " in s:
        tonic, mode = s.split(None, 1)
        m = mode.strip().lower()
        mode = "minor" if m.startswith("min") else "major"
    elif s.lower().endswith("min"):
        tonic, mode = s[:-3], "minor"
    elif s.endswith("m") and len(s) > 1 and s[-2] not in "bB":
        tonic, mode = s[:-1], "minor"
    else:
        tonic, mode = s, "major"
    tonic = tonic.strip()
    # music21 uses '-' for flats; accept 'Bb', 'Eb', …
    if len(tonic) >= 2 and tonic[1] == "b":
        tonic = tonic[0] + "-" + tonic[2:]
    return m21_key.Key(tonic, mode)


def _fit_pattern(pattern: list[str], length_bars: int) -> list[str]:
    if length_bars <= 0:
        return list(pattern)
    out: list[str] = []
    i = 0
    while len(out) < length_bars:
        out.append(pattern[i % len(pattern)])
        i += 1
    return out


def candidate_from_romans(
    roman_numerals: list[str],
    key_str: str,
    rationale: str = "",
) -> ChordCandidate:
    """Build a ChordCandidate from user-supplied Roman numerals."""
    k = _parse_key(key_str)
    symbols, pitches = _realize_pattern(list(roman_numerals), k)
    return ChordCandidate(
        roman_numerals=list(roman_numerals),
        chord_symbols=symbols,
        pitches_by_chord=pitches,
        rationale=rationale or f"{' '.join(roman_numerals)} in {key_str}.",
    )


def _realize_pattern(
    pattern: list[str], k: m21_key.Key
) -> tuple[list[str], list[list[int]]]:
    symbols: list[str] = []
    pitches_all: list[list[int]] = []
    for rn in pattern:
        try:
            r = roman.RomanNumeral(rn, k)
        except Exception:
            # Fall back to simple triad on the scale degree.
            r = roman.RomanNumeral("I", k)
        symbols.append(_chord_symbol(r))
        pitches_all.append(_voice_root_position(list(r.pitches), root_range=(48, 60)))
    return symbols, pitches_all


def _voice_root_position(
    m21_pitches: list[m21_pitch.Pitch],
    root_range: tuple[int, int] = (48, 60),
) -> list[int]:
    """Voice a chord with the root on the bottom and all other tones stacked
    within one octave above it.

    Prior to this, each pitch was clamped to [48, 72] independently, which
    could leave a chord tone (e.g. the 5th of F#m, C#5 = 73) one octave
    *below* the root (F#4 = 66). A bassline that assumes the chord's lowest
    note is the root would then play the wrong note. Keeping root-on-bottom
    makes chord[0] a safe root reference for bass.py and melody.py.
    """
    if not m21_pitches:
        return []
    root_midi = m21_pitches[0].midi
    lo, hi = root_range
    while root_midi < lo:
        root_midi += 12
    while root_midi > hi:
        root_midi -= 12

    out: list[int] = [root_midi]
    for p in m21_pitches[1:]:
        midi = p.midi
        # Pull into the octave immediately above the root.
        while midi <= root_midi:
            midi += 12
        while midi - root_midi > 12:
            midi -= 12
        if midi <= root_midi:
            midi += 12
        out.append(midi)
    return out


def _chord_symbol(r: roman.RomanNumeral) -> str:
    """Concise chord symbol — root + quality (+ 7 / maj7 / m7 etc.)."""
    root = r.root().name.replace("-", "b")
    quality = r.commonName.lower()
    if "seventh" in quality or r.seventh:
        if r.quality == "major" and r.seventh:
            tag = "maj7"
        elif r.quality == "minor" and r.seventh:
            tag = "m7"
        elif r.quality == "dominant":
            tag = "7"
        elif r.quality == "diminished":
            tag = "dim7"
        else:
            tag = "7"
    else:
        tag_map = {"major": "", "minor": "m", "diminished": "dim", "augmented": "aug"}
        tag = tag_map.get(r.quality, "")
    return f"{root}{tag}"


def _octave_pitch(p: m21_pitch.Pitch, base_octave: int = 3) -> int:
    """Place a music21 pitch in a usable chord-comping octave."""
    midi = p.midi
    # Center block-chord voicings around C3–C5.
    while midi < 48:
        midi += 12
    while midi > 72:
        midi -= 12
    return midi


def _rationale_for(pattern: list[str], k: m21_key.Key, style: str) -> str:
    """A short paragraph explaining *why* this progression works here."""
    head = ", ".join(pattern[:4])
    mode = "minor" if k.mode == "minor" else "major"
    tonic = k.tonic.name.replace("-", "b")
    base = f"Progression {head}… in {tonic} {mode}."
    notes: list[str] = []
    # Tag common well-known moves.
    if pattern[:4] == ["I", "V", "vi", "IV"] or pattern[:4] == ["vi", "IV", "I", "V"]:
        notes.append("This is the famous 'axis' pop progression — lands on a bright tonic and turns around through the relative minor.")
    if pattern[:4] == ["i", "VI", "III", "VII"]:
        notes.append("Aeolian descent through VI and III (both major triads in natural minor) — a staple of minor-key pop and modern film cues.")
    if "ii7" in pattern and "V7" in pattern:
        notes.append("ii–V motion sets up strong dominant resolution; the 7ths add jazz voicing color.")
    if "bVII" in pattern or "VII" in pattern:
        notes.append("Borrowed bVII adds rock/modal colour (Mixolydian flavour).")
    if style == "ballad":
        notes.append("Chosen density is sparse — one chord per bar gives the vocal room.")
    return " ".join([base, *notes]).strip()
