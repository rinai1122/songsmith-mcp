"""Central in-memory song state.

We keep a canonical symbolic representation here and sync it to REAPER on
``accept``. This lets every tool work headless (unit tests, no DAW) and also
makes proposals trivially diff-able.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


PPQ = 480  # MIDI pulses per quarter note — our canonical symbolic tick grid


@dataclass
class Section:
    name: str
    bars: int
    start_bar: int = 0  # filled by form.recompute()


@dataclass
class Note:
    pitch: int        # MIDI number
    start_beat: float # absolute beats from song start
    duration_beats: float
    velocity: int = 90
    lyric: str | None = None


@dataclass
class Clip:
    """A symbolic MIDI clip, living on a track, spanning whole bars."""
    track: str
    section: str
    notes: list[Note] = field(default_factory=list)
    start_bar: int = 0
    length_bars: int = 0
    chord_symbol: str | None = None  # optional text label (e.g. "Am7")


@dataclass
class Track:
    name: str
    role: str        # "chords" | "melody" | "bass" | "drums" | "pad" | "vocal" | ...
    vst: str | None = None
    color: str | None = None
    # Clips on this track are indexed by (section, start_bar)
    clips: list[Clip] = field(default_factory=list)


@dataclass
class Proposal:
    """A pending change the LLM has suggested but the human hasn't accepted."""
    id: str
    kind: str                     # "chords" | "melody" | "bass" | "drums" | ...
    section: str
    track: str
    clips: list[Clip]
    summary: str                  # short human-readable description
    rationale: str = ""           # long-form "why" (pedagogy)
    created_at: float = field(default_factory=time.time)


@dataclass
class SongState:
    key: str = "C major"
    tempo: float = 100.0
    time_sig: tuple[int, int] = (4, 4)
    style_hint: str = ""
    sections: list[Section] = field(default_factory=list)
    tracks: dict[str, Track] = field(default_factory=dict)
    proposals: dict[str, Proposal] = field(default_factory=dict)
    # Proposals keep after accept_proposal pops them from `proposals`. This is
    # what keeps `explain(prop_id)` working after a proposal is committed —
    # pedagogy shouldn't vanish the moment the user says "yes". Bounded so a
    # multi-hour session doesn't grow unbounded state.
    accepted_proposals: dict[str, Proposal] = field(default_factory=dict)
    project_path: str | None = None
    explain_level: str = "normal"  # "silent" | "normal" | "tutor"

    # --- derived queries -------------------------------------------------

    def total_bars(self) -> int:
        return sum(s.bars for s in self.sections)

    def section_by_name(self, name: str) -> Section:
        for s in self.sections:
            if s.name == name:
                return s
        raise KeyError(f"no such section: {name!r}")

    def ensure_track(self, name: str, role: str, **kwargs: Any) -> Track:
        if name not in self.tracks:
            self.tracks[name] = Track(name=name, role=role, **kwargs)
        return self.tracks[name]

    def new_proposal_id(self) -> str:
        return f"prop_{uuid.uuid4().hex[:8]}"

    # --- persistence -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "tempo": self.tempo,
            "time_sig": list(self.time_sig),
            "style_hint": self.style_hint,
            "sections": [asdict(s) for s in self.sections],
            "tracks": {n: asdict(t) for n, t in self.tracks.items()},
            "proposals": {i: asdict(p) for i, p in self.proposals.items()},
            "accepted_proposals": {
                i: asdict(p) for i, p in self.accepted_proposals.items()
            },
            "project_path": self.project_path,
            "explain_level": self.explain_level,
        }

    def summary(self) -> dict[str, Any]:
        """Compact, MCP-safe digest — no per-note data.

        Use this anywhere you would otherwise dump ``to_dict``. A 40-bar
        4-track song expands ``to_dict`` into ~150 KB of JSON (every note of
        every clip), which blows past most MCP payload limits; this digest
        stays under ~2 KB regardless of song length by reporting counts
        only. Callers who genuinely need the raw notes should reach for
        ``view_clip`` or ``to_dict`` directly.
        """
        tracks_digest: dict[str, Any] = {}
        for name, tr in self.tracks.items():
            total_notes = sum(len(c.notes) for c in tr.clips)
            tracks_digest[name] = {
                "role": tr.role,
                "clip_count": len(tr.clips),
                "note_count": total_notes,
                "clips": [
                    {
                        "section": c.section,
                        "start_bar": c.start_bar,
                        "length_bars": c.length_bars,
                        "note_count": len(c.notes),
                    }
                    for c in tr.clips
                ],
            }
        proposals_digest = [
            {
                "id": p.id,
                "kind": p.kind,
                "section": p.section,
                "track": p.track,
                "summary": p.summary,
            }
            for p in self.proposals.values()
        ]
        return {
            "key": self.key,
            "tempo": self.tempo,
            "time_sig": list(self.time_sig),
            "style_hint": self.style_hint,
            "explain_level": self.explain_level,
            "project_path": self.project_path,
            "sections": [asdict(s) for s in self.sections],
            "tracks": tracks_digest,
            "pending_proposals": proposals_digest,
            "pending_proposal_count": len(self.proposals),
            "total_bars": self.total_bars(),
        }

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2))


# A module-level singleton. The MCP server is single-session so this is fine.
_STATE = SongState()


def get_state() -> SongState:
    return _STATE


def reset_state() -> SongState:
    global _STATE
    _STATE = SongState()
    return _STATE
