"""Proposal lifecycle.

Every generative tool that would modify the song produces a ``Proposal``
first. Nothing is committed until ``accept_proposal`` is called. This is the
"human-in-the-loop" contract promised in the plan.
"""

from __future__ import annotations

from typing import Any

from ..reaper_bridge import get_bridge
from ..state import Clip, Proposal, SongState, get_state


# Keep the last N accepted proposals queryable (for `explain` after accept).
# A heavy `/test-songsmith` session can easily produce 40+ accepts; 128 is
# generous without bloating the state dump in `observe(verbose=true)`.
_ACCEPTED_ARCHIVE_MAX = 128


def create_proposal(
    kind: str,
    section: str,
    track: str,
    clips: list[Clip],
    summary: str,
    rationale: str = "",
) -> Proposal:
    st = get_state()
    prop = Proposal(
        id=st.new_proposal_id(),
        kind=kind,
        section=section,
        track=track,
        clips=clips,
        summary=summary,
        rationale=rationale,
    )
    st.proposals[prop.id] = prop
    # Render a draft .mid file (+ REAPER preview if connected).
    bridge = get_bridge()
    for clip in clips:
        bridge.insert_clip(clip, st, proposal_id=prop.id)
    return prop


def accept_proposal(proposal_id: str) -> dict[str, Any]:
    st = get_state()
    if proposal_id not in st.proposals:
        raise KeyError(f"unknown proposal: {proposal_id}")
    prop = st.proposals.pop(proposal_id)

    tr = st.ensure_track(prop.track, role=prop.kind)
    for clip in prop.clips:
        tr.clips.append(clip)

    # Re-materialize as real (non-prop) MIDI items.
    bridge = get_bridge()
    paths: list[str] = []
    for clip in prop.clips:
        path = bridge.insert_clip(clip, st, proposal_id=None)
        paths.append(path)

    # Archive so `explain` / `diff_proposal` keep working after accept.
    # FIFO-evict the oldest entry once we exceed the cap.
    st.accepted_proposals[proposal_id] = prop
    while len(st.accepted_proposals) > _ACCEPTED_ARCHIVE_MAX:
        oldest_id = next(iter(st.accepted_proposals))
        st.accepted_proposals.pop(oldest_id, None)

    return {
        "accepted": proposal_id,
        "track": prop.track,
        "clips_added": len(prop.clips),
        "track_total_clips": len(tr.clips),
        "written_midi": paths,
    }


def reject_proposal(proposal_id: str) -> dict[str, Any]:
    st = get_state()
    if proposal_id not in st.proposals:
        raise KeyError(f"unknown proposal: {proposal_id}")
    prop = st.proposals.pop(proposal_id)
    # (In online mode we'd also remove the _proposals track items here.)
    return {"rejected": proposal_id, "kind": prop.kind, "track": prop.track}


def diff_proposal(proposal_id: str) -> dict[str, Any]:
    st = get_state()
    prop = st.proposals.get(proposal_id) or st.accepted_proposals.get(proposal_id)
    if not prop:
        raise KeyError(f"unknown proposal: {proposal_id}")
    bars_touched: set[int] = set()
    note_count = 0
    for clip in prop.clips:
        for b in range(clip.start_bar, clip.start_bar + max(1, clip.length_bars)):
            bars_touched.add(b)
        note_count += len(clip.notes)
    return {
        "id": prop.id,
        "kind": prop.kind,
        "section": prop.section,
        "track": prop.track,
        "bars_touched": sorted(bars_touched),
        "note_count": note_count,
        "chord_symbols": " ".join(
            c.chord_symbol for c in prop.clips if c.chord_symbol
        ) or None,
        "summary": prop.summary,
    }


def list_proposals() -> list[dict[str, Any]]:
    st = get_state()
    return [diff_proposal(pid) for pid in st.proposals]


def bulk_accept(proposal_ids: list[str] | None = None) -> dict[str, Any]:
    """Accept many proposals in one call.

    If ``proposal_ids`` is falsy, accepts every currently pending proposal in
    insertion order. Missing IDs are collected into ``not_found`` rather than
    raising, so a partial batch still makes progress.
    """
    st = get_state()
    ids = list(proposal_ids) if proposal_ids else list(st.proposals.keys())
    accepted: list[dict[str, Any]] = []
    not_found: list[str] = []
    for pid in ids:
        if pid not in st.proposals:
            not_found.append(pid)
            continue
        accepted.append(accept_proposal(pid))
    return {"accepted": accepted, "not_found": not_found, "count": len(accepted)}


def bulk_reject(proposal_ids: list[str] | None = None) -> dict[str, Any]:
    """Reject many proposals in one call. Same semantics as ``bulk_accept``."""
    st = get_state()
    ids = list(proposal_ids) if proposal_ids else list(st.proposals.keys())
    rejected: list[dict[str, Any]] = []
    not_found: list[str] = []
    for pid in ids:
        if pid not in st.proposals:
            not_found.append(pid)
            continue
        rejected.append(reject_proposal(pid))
    return {"rejected": rejected, "not_found": not_found, "count": len(rejected)}
