# Songsmith MCP

An MCP server that lets an LLM compose a full song — lyrics, chords, melody,
bass, drums, arrangement — by **driving REAPER through the tools humans
already use**, with a human-in-the-loop proposal/accept workflow.

Built because the existing `reaper-mcp` covers engineering primitives (make
a track, insert a note, render) but there is no composition-level layer:
theory-aware chord proposing, voice leading, prosody-aware lyric-to-rhythm
alignment, or proposal/diff/explain primitives. See `../.claude/plans/` for
the design doc.

## Design in one paragraph

The agent works on a canonical symbolic song state (`state.py`) and every
generator emits a **Proposal** — a set of MIDI clips landing in an
`_proposals` folder (or a `.mid` file, offline). The user (or the LLM on the
user's behalf) calls `accept_proposal` or `reject_proposal`. Nothing
overwrites existing work without consent. If REAPER is running, proposals
show up live via `python-reapy`; if not, everything still works as MIDI
files you can open in REAPER/MuseScore/Logic.

## Install

```bash
# Python ≥ 3.10 required (mcp SDK requirement).
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -e .
# Optional — needed only when REAPER is actually running:
pip install -e ".[reaper]"
```

In REAPER: Options → Preferences → Plug-ins → ReaScript → enable Python.
Then run `reapy.configure_reaper()` once (see
[python-reapy docs](https://github.com/RomeoDespres/reapy#installation)).

## Wire into Claude Desktop / Code

Add to your MCP config:

```json
{
  "mcpServers": {
    "songsmith": {
      "command": "python",
      "args": ["-m", "songsmith_mcp.server"],
      "env": { "SONGSMITH_OUT": "./out" }
    }
  }
}
```

> **Windows gotcha.** If you use an absolute Windows path for
> `SONGSMITH_OUT`, either use forward slashes (`"C:/Users/me/out"`) or
> escape every backslash (`"C:\\Users\\me\\out"`). A raw
> `"C:\Users\me\out"` will have its `\U` / `\m` / `\o` silently stripped
> by the JSON parser and you'll get files in `./C:Usersmeout/` instead.
> The server now detects this mangled form and falls back to `./out`
> with a warning on stderr.

If you want engineering-level control in the same session (FX, mixer,
render) also install
[itsuzef/reaper-mcp](https://github.com/itsuzef/reaper-mcp) — the two
servers are designed to live side by side; Songsmith handles *composition*,
`reaper-mcp` handles *production*.

## Tool surface

| Phase       | Tools |
|-------------|-------|
| Session     | `new_song`, `observe`, `set_explain_level`, `reaper_status` |
| Form        | `suggest_form`, `set_form` |
| Harmony     | `propose_chord_progression`, `write_chords`, `revoice` |
| Lyrics      | `syllabify`, `align_lyrics_to_rhythm` |
| Melody      | `propose_melody`, `humanize` |
| Arrangement | `write_bassline`, `write_drum_pattern` |
| Batch       | `render_section` (one section, all layers), `build_song` (whole song in one call) |
| HITL        | `list_proposals`, `diff_proposal`, `accept_proposal`, `reject_proposal`, `bulk_accept_proposals`, `bulk_reject_proposals`, `explain` |
| Direct edit | `import_midi`, `edit_note`, `add_note`, `delete_note`, `edit_notes` (batch), `transpose_clip` |
| Inspect     | `view_clip` (readable note list), `list_clips` |

See `examples/` for step-by-step transcripts.

## Cutting tool-use count: `build_song`

Composing a multi-section song by calling `write_chords` / `write_bassline`
/ `write_drum_pattern` / `propose_melody` individually costs 4 MCP calls
per section plus an accept per layer — 40+ calls for a 5-section song,
which burns through agent tool-use budgets.

`build_song` takes the whole song as one payload:

```jsonc
{
  "sections": [
    {
      "section": "intro",
      "chords": { "roman_numerals": ["VI", "VII", "i", "i"] }
    },
    {
      "section": "verse",
      "chords": { "roman_numerals": ["i", "VII", "VI", "V",
                                      "i", "VII", "VI", "V"] },
      "melody": { "contour": "wave", "seed": 11 }
    },
    {
      "section": "chorus",
      "chords": { "roman_numerals": ["iv", "V", "III", "VI",
                                      "iv", "V", "III", "VI"] },
      "drums":  { "style": "edm", "intensity": "heavy" },
      "bass":   { "style": "arp" },
      "melody": { "contour": "arch", "seed": 22 }
    }
  ],
  "default_drums": { "style": "edm" },
  "default_bass":  { "style": "roots" },
  "auto_accept": true
}
```

`default_drums` / `default_bass` apply to any section that doesn't
override them. With `auto_accept: true` every proposal is accepted
inline so bass/melody generators can read the chords back from state.
Returns per-section `proposal_ids` and a flat `total_proposals` count.

## Editing a song inside Claude Desktop (no DAW needed)

You can inspect and mutate notes entirely through the LLM — no MuseScore
or REAPER required.

```
you:    view_clip({ track_name: "Melody", section: "chorus" })
agent:  (returns every note with index + pitch name + start beat + velocity)

you:    "raise the last 4 notes by an octave and make the first note G5 instead of G4"
agent:  edit_notes({
          track_name: "Melody", section: "chorus",
          edits: [
            { note_index: 0,  pitch: 79 },
            { note_index: 60, pitch: … }, …
          ]
        })
```

Useful patterns:
- `view_clip` *without* `section` → lists all clips on the track.
- `list_clips` → one-line index across every track.
- `transpose_clip` → shift an entire clip by a semitone offset.
- `edit_notes` → batch of edits, single `.mid` re-render at the end.
- `import_midi` remains available if you'd rather round-trip through a
  DAW.

## Direct edits

The proposal workflow is for generator output. For hand-edits, skip it:

- `edit_note(track, section, note_index, pitch=…, velocity=…, …)` — tweak one note.
- `add_note` / `delete_note` — insert or remove a single note.
- `import_midi(path, track, section)` — re-import a clip after editing it in
  REAPER / MuseScore / any DAW. Defaults to direct commit; pass
  `as_proposal=true` to review the diff first.

Every edit re-renders the affected clip's `.mid` file in `SONGSMITH_OUT`.

## Pedagogy mode

`set_explain_level` takes `silent` / `normal` / `tutor`. In `tutor` mode
every proposal carries a multi-paragraph rationale ("Using ii–V–I because
the previous section ended on vi; this is a standard relative-major
pivot"), so beginners learn theory by watching the agent work.

## Theory notes

- **Chord voicing.** Block chords are voiced root-position: the root sits
  on the bottom and the other chord tones stack within one octave above
  it. This means `chord[0]` is always the root, which keeps
  `write_bassline` honest (bass.py reads `chord[0]` as the root).
- **Chord candidates.** `propose_chord_progression` guarantees
  `n_candidates` *distinct* progressions: it shuffles the style pool,
  dedupes by Roman-numeral tuple, falls back to pool rotations, and
  finally to a cross-style draw. Unknown styles like `"vocaloid"`,
  `"future-pop"`, `"j-pop"`, `"anime"`, `"house"` are aliased to the
  nearest in-catalog style rather than silently falling into a tiny
  default pool.

## Tests

Run headless — no REAPER required:

```bash
pytest
```

41 tests cover theory, lyrics, melody, arrangement, the proposal
lifecycle, MCP stdio dispatch, and a full `blank → accepted full pop
song` integration path.

## Licence

MIT.
