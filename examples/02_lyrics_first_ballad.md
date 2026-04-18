# Example 2 — lyrics-first melody

Goal: the user has a lyric line; we want a singable melody that fits it.

## Key idea

Most generative tools write melody and then try to squeeze lyrics on top.
We invert that: **the lyric prosody dictates the rhythm** (one syllable per
note, stressed syllables on strong beats), and the melody generator then
picks pitches to match.

## Transcript

> **user:** I have a verse lyric. Set it to music.
> ```
> tell me what you know about love and rain
> what you felt when the morning came
> ```

**LLM calls** `syllabify(text="tell me what you know about love and rain ...")`
to see what it's dealing with:

```
["tell"(s=0), "me"(s=0), "what"(s=0), "you"(s=0), "know"(s=2),
 "a"(s=0), "bout"(s=2), "love"(s=2), "and"(s=0), "rain"(s=2), …]
```

**LLM calls** `align_lyrics_to_rhythm(lyrics=..., rhythm="eighths")`.
The aligner swaps slots to place the stressed "know", "bout", "love",
"rain" on beats 1 and 3.

**LLM calls** `propose_melody(section="verse", lyrics=<same>, contour="arch", seed=1)`.

The generator:
1. Reads the chord clip already on the "Chords" track for that section.
2. Places each syllable as one MIDI note, using the aligned rhythm.
3. On strong beats, snaps to a chord tone of whichever chord is active.
4. Off-beats use any scale tone of A minor; leaps capped at a 7th.
5. The ``arch`` contour pushes the melody up toward the middle of the line
   and down at the end.

Returns `proposal_id = prop_mel42`.

> **LLM:** Drafted a melody (`arch contour, 19 notes, range A3–D5`).
> `diff_proposal prop_mel42` shows it touches bars 4–11, 19 notes total.

> **user:** Looks good. Accept, then thicken it with a simple bassline.

**LLM calls**:
- `accept_proposal(proposal_id="prop_mel42")`
- `write_bassline(section="verse", style="root_fifth")` → `prop_bass7`
- `accept_proposal(proposal_id="prop_bass7")`

## Why this is useful

- "Lyrics drove rhythm" is how real songwriters actually work — Songsmith
  makes that a first-class operation rather than an afterthought.
- The alignment is inspectable: you can call `align_lyrics_to_rhythm`
  directly to see which beat each syllable sits on before committing.
