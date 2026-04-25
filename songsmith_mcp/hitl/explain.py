"""Explain-mode commentary generation.

For beginners: given a proposal, render a multi-paragraph rationale a human
can read. For pros: the ``silent`` level short-circuits to the one-liner.
"""

from __future__ import annotations

from ..state import Proposal, get_state


def explain(proposal_id: str) -> str:
    st = get_state()
    prop = st.proposals.get(proposal_id)
    accepted = False
    if not prop:
        prop = st.accepted_proposals.get(proposal_id)
        accepted = prop is not None
    if not prop:
        raise KeyError(f"unknown proposal: {proposal_id}")

    level = st.explain_level
    if level == "silent":
        return prop.summary

    body = [prop.summary]
    if accepted:
        body.append("(This proposal was accepted and is part of the song.)")
    if prop.rationale:
        body.append("")
        body.append(prop.rationale)

    if level == "tutor":
        body.append("")
        body.extend(_tutor_paragraphs(prop, st))

    return "\n".join(body)


def _tutor_paragraphs(prop: Proposal, st) -> list[str]:
    out: list[str] = []
    if prop.kind == "chords":
        out.append(
            "**How to read a chord chart.** The Roman numerals capture the "
            "chord's *function* in the key (I = home, V = tension, vi = "
            "relative-minor pivot). The chord symbols show the same thing "
            "spelled in the current key so you can play them on a keyboard."
        )
        out.append(
            "**Try this.** Press play on just the chord track, then sing any "
            "syllable on top. If it feels stable, you're on a chord tone; if "
            "it pulls, you're on a tension — that's where melody gets "
            "expressive."
        )
    elif prop.kind == "melody":
        out.append(
            "**Why these pitches.** On the strong beats (1 and 3 of each "
            "bar) the melody snaps to a chord tone — this is what makes it "
            "feel 'in the song'. Off-beats use any scale note, which is "
            "where the ear hears motion."
        )
    elif prop.kind == "bass":
        out.append(
            "**Bass role.** The bass defines the harmonic foundation. Root "
            "notes on beat 1 anchor the chord; fifths and approach tones "
            "keep the line moving without changing the harmony."
        )
    elif prop.kind == "drums":
        out.append(
            "**Drum feel.** Kick on 1 and 3, snare on 2 and 4 → 'backbeat'. "
            "Hats divide the beat; their subdivision (8ths vs. 16ths) sets "
            "the tempo feel more than the BPM does."
        )
    elif prop.kind == "form":
        out.append(
            "**Form shapes energy.** Verse tells the story; chorus is the "
            "emotional payoff; bridge breaks the pattern so the final chorus "
            "feels earned."
        )
    return out
