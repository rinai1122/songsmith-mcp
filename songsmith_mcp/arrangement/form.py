"""Song-form management: section list + region markers."""

from __future__ import annotations

from dataclasses import dataclass

from ..state import Section, SongState


# Canonical templates by style & target duration (in bars, 4/4 @ ~100bpm).
# Each tuple is (section_name, bars).
_TEMPLATES: dict[str, list[tuple[str, int]]] = {
    "pop_short":   [("intro", 4), ("verse", 8), ("chorus", 8), ("verse", 8),
                    ("chorus", 8), ("bridge", 4), ("chorus", 8), ("outro", 4)],
    "pop_standard":[("intro", 4), ("verse", 8), ("prechorus", 4), ("chorus", 8),
                    ("verse", 8), ("prechorus", 4), ("chorus", 8),
                    ("bridge", 8), ("chorus", 8), ("chorus", 8), ("outro", 4)],
    "ballad":      [("intro", 4), ("verse", 8), ("chorus", 8), ("verse", 8),
                    ("chorus", 8), ("bridge", 6), ("chorus", 8), ("outro", 6)],
    "AABA":        [("A", 8), ("A", 8), ("B", 8), ("A", 8)],
    "loop":        [("loop", 16)],
}


@dataclass
class FormCandidate:
    name: str
    sections: list[tuple[str, int]]
    total_bars: int
    rationale: str


def suggest_form(style: str = "pop", target_duration_s: float = 180.0, tempo: float = 100.0) -> list[FormCandidate]:
    """Return candidate forms sized so their total bars ≈ target duration."""
    beats_per_bar = 4
    bars_needed = max(8, int(target_duration_s * (tempo / 60.0) / beats_per_bar))

    out: list[FormCandidate] = []
    for name, template in _TEMPLATES.items():
        if style.lower() == "pop" and not name.startswith("pop"):
            continue
        if style.lower() == "ballad" and name != "ballad":
            continue
        if style.lower() in {"jazz", "standard"} and name != "AABA":
            continue
        sections = list(template)
        total = sum(b for _, b in sections)
        out.append(
            FormCandidate(
                name=name,
                sections=sections,
                total_bars=total,
                rationale=_rationale(name, total, bars_needed),
            )
        )
    # Always include the raw pop_standard as a safe default.
    if not out:
        template = _TEMPLATES["pop_standard"]
        out.append(
            FormCandidate(
                name="pop_standard",
                sections=list(template),
                total_bars=sum(b for _, b in template),
                rationale="Default verse–prechorus–chorus structure; standard pop framing.",
            )
        )
    return out


def apply_form(state: SongState, sections: list[tuple[str, int]]) -> None:
    """Replace ``state.sections`` and recompute ``start_bar``."""
    # Disambiguate repeats (verse, verse → verse, verse.2) so section lookup
    # by name stays stable without us having to re-index.
    counts: dict[str, int] = {}
    resolved: list[Section] = []
    bar = 0
    for name, bars in sections:
        counts[name] = counts.get(name, 0) + 1
        unique = name if counts[name] == 1 else f"{name}.{counts[name]}"
        resolved.append(Section(name=unique, bars=bars, start_bar=bar))
        bar += bars
    state.sections = resolved


def recompute(state: SongState) -> None:
    """Re-derive each section's ``start_bar`` after edits."""
    bar = 0
    for s in state.sections:
        s.start_bar = bar
        bar += s.bars


def _rationale(name: str, total: int, target: int) -> str:
    delta = total - target
    abs_delta = abs(delta)
    if abs_delta <= 4:
        fit = "sized almost exactly to your target."
    elif abs_delta <= 16:
        if delta < 0:
            fit = f"slightly shorter than target (by {abs_delta} bars) — tighter radio-friendly length."
        else:
            fit = f"slightly longer than target (by {abs_delta} bars) — room for extended chorus play-outs."
    else:
        direction = "shorter" if delta < 0 else "longer"
        fit = (
            f"much {direction} than target (by {abs_delta} bars); "
            f"the target assumes 4-bar groupings at your tempo, so treat this more as a sketch of section order than a duration match."
        )
    mapping = {
        "pop_short":    "Compact pop form: two verse/chorus rotations and a short bridge.",
        "pop_standard": "Industry-standard pop: verse → prechorus → chorus with a full bridge.",
        "ballad":       "Ballad form: slower sections, longer chorus, emotional bridge.",
        "AABA":         "32-bar AABA jazz standard form.",
        "loop":         "Single 16-bar loop — good for instrumental beat sketches.",
    }
    return f"{mapping.get(name, 'Custom form.')} {fit}"
