---
description: Run one songsmith MCP test scenario from the main thread and append any new bugs to songsmith-bugs.md
---

You are running ONE iteration of an automated songsmith test loop. The user is AFK. Be terse. No preamble.

CRITICAL: Run the scenario yourself from the main thread using `mcp__songsmith__*` tools. Do NOT spawn subagents — they do not inherit MCP tools. Do NOT read source files. Do NOT edit any code.

## Step 1 — Pick a scenario

Read `songsmith-bugs.md`. Find the `## Scenario Run Ledger` section. Pick the scenario with the LOWEST run count; tie-break by lowest number. If the ledger is missing, initialize it with all 12 entries at 0 and pick scenario 1.

## Step 2 — Run the scenario

Execute the steps for the chosen scenario below using `mcp__songsmith__*` tools, one after another. After each call, note its result briefly in your own working memory (not on disk). If a tool errors, record the exact error string and continue where sensible.

---

### 1 · Baseline pop E2E
1. `new_song` title="Iter-Pop", key="C", tempo=120, time_signature="4/4"
2. `set_form` form=[verse, chorus, verse, chorus]
3. `build_song` with 4 sections. verse: roman_numerals=["I","V","vi","IV"], bars_per_chord=1, lyrics="simple test of the song maker", drums style="pop" intensity="normal", bass style="roots". chorus: roman_numerals=["I","V","vi","IV"], bars_per_chord=1, lyrics="sing along if you can hear", drums style="pop" intensity="heavy", bass style="root_fifth". Second verse & chorus identical. auto_accept=true.
4. `observe`
5. `list_clips`
6. `view_clip` on one clip id from step 5
7. `reaper_status`

### 2 · Edge key & tempo
1. `new_song` title="Iter-EdgeLow", key="F# minor", tempo=55, time_signature="4/4"
2. `build_song` verse: roman_numerals=["i","VI","III","VII"], lyrics="slow and dark at fifty five bpm", drums style="ballad" if present else "pop" intensity="light", bass style="walking". auto_accept=true.
3. `observe`
4. `new_song` title="Iter-EdgeHigh", key="Eb", tempo=190, time_signature="4/4"
5. `build_song` chorus: roman_numerals=["I","bVII","IV","I"], lyrics="fast fast fast", drums style="rock" if present else "pop" intensity="heavy", bass style="syncopated". auto_accept=true.
6. `observe`

### 3 · HITL proposal flow
1. `new_song` title="Iter-HITL", key="A minor", tempo=100
2. `propose_chord_progression` section="verse" roman_numerals=["i","VI","III","VII"] bars_per_chord=1 → capture proposal_id
3. `list_proposals`
4. `diff_proposal` id=<above>
5. `accept_proposal` id=<above>
6. `list_proposals`
7. `propose_melody` section="verse" lyrics="test melody line" seed=42 → capture id
8. `diff_proposal` id=<melody>
9. `reject_proposal` id=<melody>
10. `list_proposals`
11. Try `accept_proposal` id="proposal_doesnotexist" — verify clear error
12. Try `accept_proposal` on the already-rejected melody id — verify clear error
13. `bulk_accept_proposals` ids=[] — observe
14. `bulk_reject_proposals` ids=["bogus_1","bogus_2"] — observe

### 4 · Lyrics alignment stress
1. `new_song` title="Iter-Lyrics", key="D", tempo=120
2. `syllabify` text="don't we'll can't shouldn't"
3. `syllabify` text="Hello, world! It's a... test?"
4. `syllabify` text="café naïve résumé jalapeño"
5. `syllabify` text="supercalifragilisticexpialidocious is a pneumonoultramicroscopicsilicovolcanoconiosis word"
6. `align_lyrics_to_rhythm` with the same four inputs against a simple default rhythm template (let the tool pick if possible).

### 5 · All bass styles
1. `new_song` title="Iter-Bass", key="G", tempo=100
2. `build_song` single verse: roman_numerals=["I","IV","V","I"], bars_per_chord=1, auto_accept=true (no bass yet).
3. For each style in [roots, root_fifth, walking, syncopated, arp]: call `write_bassline` section="verse" style=<style>. Capture output notes; confirm non-empty and reasonable pitch range.

### 6 · All drum styles & intensities
1. `new_song` title="Iter-Drums", key="C", tempo=120
2. `build_song` single verse: roman_numerals=["I","I","I","I"], bars_per_chord=1, auto_accept=true.
3. For each drum style in the server's `DRUM_STYLES` enum (list what `write_drum_pattern`'s schema or error exposes) × intensities [light, normal, heavy]: call `write_drum_pattern`. If some combination errors, note it.

### 7 · Render & score output
1. `new_song` title="Iter-Render", key="G", tempo=110
2. `build_song` 2 sections. verse: roman_numerals=["I","IV","V","I"], drums style="pop", bass style="roots". chorus: roman_numerals=["vi","IV","I","V"], drums style="pop" intensity="heavy", bass style="root_fifth". auto_accept=true.
3. `render_section` section="verse" (request audio)
4. `render_section` section="chorus"
5. `observe`
6. `view_clip` on any clip
7. Note any file paths returned and whether the tool claims success vs. actual existence (you can confirm via `observe` re-reporting them, not via filesystem tools).

### 8 · Humanize + revoice
1. `new_song` title="Iter-Hum", key="F", tempo=100
2. `build_song` verse: roman_numerals=["I","IV","V","I"], auto_accept=true, with melody and bass.
3. `view_clip` on melody clip → record note count + first few onset timings
4. `humanize` on melody clip
5. `view_clip` on melody clip → compare: note count must equal; timings should differ slightly
6. `revoice` on chord clip
7. `view_clip` on chord clip → compare voicing

### 9 · MIDI import
1. `new_song` title="Iter-MIDI", key="C", tempo=120
2. `import_midi` path="C:\\does\\not\\exist.mid" — expect a clear error
3. `import_midi` path="C:\\Windows\\System32\\drivers\\etc\\hosts" — a non-MIDI file, expect a clear error
4. Note whether errors distinguish "file not found" from "not a MIDI file"

### 10 · Form & transpose
1. `new_song` title="Iter-Form", key="C", tempo=120
2. `suggest_form`
3. `set_form` form=[intro, verse, chorus, verse, chorus, bridge, chorus, outro]
4. `build_song` verse: roman_numerals=["I","V","vi","IV"], auto_accept=true.
5. `list_clips`
6. `transpose_clip` clip_id=<a chord or melody clip> semitones=5
7. `view_clip` on it — verify pitches shifted up by 5
8. `transpose_clip` same clip semitones=-7 — verify it lands 2 semitones below the original
9. `transpose_clip` semitones=13 — check wrap/overflow handling

### 11 · Observe / explain UX
1. `new_song` title="Iter-Explain", key="C", tempo=120
2. `build_song` verse: roman_numerals=["I","V","vi","IV"], auto_accept=true.
3. `set_explain_level` level="brief" (or equivalent minimum)
4. `explain` on the latest change
5. `set_explain_level` level="verbose" (or maximum)
6. `explain` on the same change — compare; verbose should say more than brief
7. `observe` with compact output
8. `observe` with full output if the flag exists

### 12 · Bulk proposal ops
1. `new_song` title="Iter-Bulk", key="A minor", tempo=100
2. `propose_chord_progression` section="verse" roman_numerals=["i","VI","III","VII"] → id_a
3. `propose_chord_progression` section="chorus" roman_numerals=["VI","VII","i","i"] → id_b
4. `propose_melody` section="verse" lyrics="hello" seed=1 → id_c
5. `bulk_accept_proposals` ids=[id_a, id_c]
6. `list_proposals` — verify both accepted, id_b still pending
7. `bulk_reject_proposals` ids=[id_b, "bogus_xyz"] — mixed valid/invalid, note behavior

---

## Step 3 — Dedupe & append

1. Re-read `songsmith-bugs.md`.
2. For each issue surfaced in Step 2:
   - Compare against existing entries. Treat as duplicate if the same tool AND substantively the same symptom is already logged.
   - For a duplicate, optionally update its `seen` count on the existing line (do not append a new one).
   - For a new issue, append under today's date heading (create if missing). Format:
     ```
     - **<title>** (severity)
       - tool: `<mcp tool>`
       - inputs: `<compact>`
       - observed: <one line>
       - expected: <one line>
       - scenario: <#>
       - first seen: <HH:MM>
     ```
3. Increment the run count for the chosen scenario in the Scenario Run Ledger.

## Step 4 — One-line summary

End with exactly one line, nothing after:
`iteration complete: scenario=<name>, N new, M duplicates`

No preamble before Step 1. Go.
