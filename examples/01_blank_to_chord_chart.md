# Example 1 — blank project to chord chart

Goal: start a session, pick a form, commit a chord progression for the
verse. End state: REAPER has four bars of block chords in a "Chords" track,
with region markers for each section.

## Transcript (user ↔ LLM)

> **user:** I want to sketch a sad ballad in A minor, around 90 BPM.

**LLM calls** `new_song(key="A minor", tempo=90, style_hint="ballad", explain_level="tutor")`.

> **LLM:** Started. What form do you want — one you pick, or should I suggest?

> **user:** Suggest.

**LLM calls** `suggest_form(style="pop", target_duration_s=150)`.
It picks `pop_short` (intro/verse/chorus/verse/chorus/bridge/chorus/outro)
and calls `set_form(...)`.

> **LLM:** Form set — 52 bars total. Now let me draft chords for the verse.

**LLM calls** `propose_chord_progression(section="verse", style="ballad", length_bars=4, n_candidates=3)`.

Candidates come back:
1. `i – VI – iv – V`  → `Am | F | Dm | E`
2. `i – III – VII – VI`  → `Am | C | G | F`
3. `i – iv – i – V`  → `Am | Dm | Am | E`

> **LLM:** I'll go with option 1 — the iv before V gives the ballad its
> classic pull. Writing it now.

**LLM calls** `write_chords(section="verse", roman_numerals=["i","VI","iv","V"], bars_per_chord=2)`
→ returns `proposal_id = prop_abc123`.

> **LLM:** Proposal `prop_abc123` — `Am | F | Dm | E`. Accept?

> **user:** yes

**LLM calls** `accept_proposal(proposal_id="prop_abc123")`.

The chord clip lands on track "Chords" spanning bars 4–11.

## Why this is useful

- The LLM **proposed** before committing; the user said yes before anything
  moved in the DAW.
- Every step left an audit trail: the `rationale` field records *why* this
  progression was picked, not just *what* notes it contains.
- A beginner in `tutor` mode gets a paragraph on chord function alongside
  the Roman numerals.
