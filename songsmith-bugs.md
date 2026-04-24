# Songsmith Bug Log

Running log of issues surfaced by automated `/test-songsmith` iterations (cron job `*/20 * * * *`).

Each entry: title, tool, compact inputs, observed vs expected, severity, scenario #, first-seen timestamp.
Dedupe rule: same tool + substantively same symptom = duplicate.

## Scenario Run Ledger

| # | Scenario | Runs |
|---|----------|------|
| 1 | Baseline pop E2E | 0 |
| 2 | Edge key & tempo | 0 |
| 3 | HITL proposal flow | 0 |
| 4 | Lyrics alignment stress | 0 |
| 5 | All bass styles | 0 |
| 6 | All drum styles & intensities | 0 |
| 7 | Render & score output | 1 |
| 8 | Humanize + revoice | 0 |
| 9 | MIDI import | 0 |
| 10 | Form & transpose | 0 |
| 11 | Observe / explain UX | 0 |
| 12 | Bulk proposal ops | 0 |

## Meta notes

- 2026-04-22: automation switched from parallel subagents to main-thread execution — subagent tool-availability for `mcp__songsmith__*` is flaky (observed: 2 of 3 parallel agents reported "no tools available"; the 3rd called them fine). Main-thread execution is reliable, so future iterations run one scenario per cron fire from main.

## 2026-04-22

- ~~**build_song requires set_form first; error is cryptic** (ux)~~ — fixed 2026-04-24
  - tool: `mcp__songsmith__build_song`
  - inputs: `sections=[verse,chorus]` with no prior `set_form` call
  - observed: `{"error":"not found: \"no such section: 'verse'\""}`
  - expected: either auto-create section markers from the `sections` array, or return guidance like "call set_form first"
  - scenario: 7
  - first seen: iteration 1 (subagent run, pre-pivot)
  - fix: `build_song` auto-derives the form from the sections array when no `set_form` call has landed (each section sized to `len(roman_numerals) * bars_per_chord`). If a form *is* committed but names don't match, returns a targeted `UnknownSection` error naming both the missing and the existing section names. Regressions in `tests/test_feedback_fixes.py::test_build_song_auto_derives_form_when_no_set_form_call` (+ 2 neighbors).

- **render_section produces no audio artifact despite its name** (incorrect)
  - tool: `mcp__songsmith__render_section`
  - inputs: `section="verse"` with chords/drums/bass, `auto_accept=true`
  - observed: returns only proposal_ids and summaries; no wav/mid/mp3 path. `observe` shows `project_path=null` and no render path field on tracks/clips
  - expected: an audio/MIDI output path, or explicit "no renderer available" message
  - scenario: 7
  - first seen: iteration 1

- **REAPER bridge silently unavailable; no audio export path** (incorrect)
  - tool: `mcp__songsmith__observe`
  - inputs: n/a
  - observed: `reaper.reapy_installed=false`, `reaper_connected=false`, `out_dir` exists but no audio produced; no surfaced tool for audio rendering
  - expected: a dedicated render/export tool, or a clear error when audio is requested while REAPER is offline
  - scenario: 7
  - first seen: iteration 1

- **No score-export tool exposed** (ux)
  - tool: (none)
  - inputs: enumerated available tool list
  - observed: no `export_score` / musicxml / pdf tool surfaced
  - expected: if notation export is a feature, expose a tool; otherwise don't imply one
  - scenario: 7
  - first seen: iteration 1

- **explain fails on auto-accepted proposal IDs** (ux)
  - tool: `mcp__songsmith__explain`
  - inputs: `proposal_id="prop_68947828"` just returned from `render_section` with `auto_accept=true`
  - observed: `{"error":"not found: 'unknown proposal: prop_68947828'"}`
  - expected: explain should work on accepted proposals, or the error should say "proposal was accepted and purged"
  - scenario: 7
  - first seen: iteration 1

- **new_song response dominated by noisy purge list** (ux)
  - tool: `mcp__songsmith__new_song`
  - inputs: `title="IterTest-Render", key="G", tempo=110`
  - observed: response includes a 44-entry `purged_proposal_files` array from a prior session, dominating the payload
  - expected: purge silently, or return a count only (`purged: 44`), not full filenames as primary data
  - scenario: 7
  - first seen: iteration 1

