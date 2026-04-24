# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -e ".[dev]"            # dev deps (pytest, pytest-asyncio)
pip install -e ".[reaper]"         # add python-reapy for live REAPER integration
pytest                             # all tests, headless (no REAPER required)
pytest tests/test_melody.py::test_name  # one test
python -m songsmith_mcp.server     # run the MCP stdio server standalone
```

The full project surface is described in `README.md`; user-facing tool examples live in `examples/`.

## Architecture

### Single-process MCP server with a canonical symbolic song state

Everything pivots on **`state.SongState`**, a module-level singleton (`get_state()` / `reset_state()`). All generators read and mutate this in-memory object; rendering MIDI/audio/score is a projection of it. Tests, the MCP server, and the REAPER bridge all share that same singleton — there is no per-session isolation.

Time uses two systems in parallel: **PPQ=480 ticks** in MIDI files, and **absolute beats from song start** as floats inside `Note` / `Clip`. Section boundaries are bar-counted; `start_bar` is filled by `form.recompute()` after `set_form`.

### Proposal/accept lifecycle is the contract for *generators*

Generators (`propose_chord_progression`, `propose_melody`, `write_bassline`, `write_drum_pattern`, …) **never mutate `SongState.tracks` directly**. They construct a `Proposal` via `hitl.proposals.create_proposal(…)`, which:

1. Stores it in `state.proposals[id]`.
2. Calls `bridge.insert_clip(clip, state, proposal_id=prop.id)` to write a draft `prop_<id>__<track>__<section>.mid` into `out_dir` (and to the REAPER `_proposals` folder track if connected).

Only `accept_proposal` (or `bulk_accept_proposals`) pops the proposal, appends its clips to the actual track via `state.ensure_track`, and re-emits the MIDI without the `prop_` prefix. `reject_proposal` just discards. **`build_song` with `auto_accept=true` is the standard path** — without it, layered generators (bass, melody) can't see chords yet committed and will fail.

The opposite side of the contract is **`direct_edit.py`** (`edit_note`, `add_note`, `delete_note`, `edit_notes`, `transpose_clip`, `import_midi` default mode). These are user-driven hand-edits that bypass the proposal lifecycle and re-render the affected clip's `.mid` immediately via `_rerender → bridge.insert_clip(proposal_id=None)`.

### REAPER bridge: online ⇄ offline with no caller awareness

`reaper_bridge.ReaperBridge` (singleton via `get_bridge()`) wraps `python-reapy`. The connect attempt runs in a daemon thread with a 2-second timeout because `reapy.Project()` has been observed to hang on Windows when REAPER isn't running. Any failure during a live call triggers `_go_offline()` so we don't keep paying the cost. Set `SONGSMITH_DISABLE_REAPER=1` to force offline even if reapy is installed.

`SONGSMITH_OUT` controls where `.mid` and rendered audio land (default `./out`). `_sanitize_out_dir` actively detects the classic Claude Desktop config trap of an unescaped Windows path (`C:\Users\me\out` → JSON parser eats `\U`/`\m`/`\o` → caller passes `"C:Usersmeout"`) and falls back to `./out` with a stderr warning.

### Server dispatch (`server.py`)

`@server.call_tool()` dispatches by `name` through a long if-chain (`name == "..."`). Tool schemas are declared once in `@server.list_tools()`. Style enums (`DRUM_STYLES`, `BASS_STYLES`, `MELODY_CONTOURS`, `RHYTHM_TEMPLATES`) are pulled from the implementation modules into the JSON schema so unknown styles fail at the schema layer, not deep in Python.

`SongState.summary()` is a compact digest used by `observe` (default) so a 40-bar song doesn't blow MCP payload limits with per-note JSON. Use `view_clip` / `list_clips` for targeted note inspection; only fall back to `to_dict()` / `verbose=true` when you actually need raw notes.

### Module layout

- **`theory/`** — `chords.py` (Roman-numeral progressions via `music21`, style catalog with alias normalization for unknown styles like `vocaloid`/`anime`), `melody.py` (rules-based generator: chord tones on strong beats, contour bias, max-leap clamp), `voice_leading.py`. Block chords are voiced **root-position** so `chord[0]` is always the root — `bass.py` depends on this invariant.
- **`arrangement/`** — `form.py` (templates + `recompute()` to assign `start_bar`), `bass.py` (styles: roots/root_fifth/walking/syncopated/arp), `drums.py` (GM drum-map patterns).
- **`lyrics/`** — `syllabify.py` (`pyphen` + cheap stress heuristic), `align.py` (one syllable per note, with `DEFAULT_RHYTHMS` templates).
- **`render/`** — three-stage `merge → synth → encode` audio pipeline producing per-role stems + mixed `.wav` (+ `.mp3` when ffmpeg is on PATH). `synth.py` is a pure-numpy additive synth — zero binary deps. `score.py` exports MusicXML (always) and PNG via MuseScore (best-effort, looks at `SONGSMITH_MUSESCORE` env or common install paths).
- **`hitl/`** — `proposals.py` (lifecycle), `explain.py` (silent/normal/tutor pedagogy commentary).

## Conventions worth knowing before editing

- **Sections must exist before per-section tools are called.** `_chords_by_beat_for_section` and friends look up `section_by_name`; if `set_form` (or the equivalent in `build_song`) hasn't run, callers get a `KeyError` that bubbles up as a cryptic MCP error. New tools that take a `section` arg should validate up front.
- **Stale-proposal hazard.** `accept_proposal` pops from `state.proposals`, so `explain(prop_id)` on an already-accepted proposal raises `unknown proposal`. Treat the proposal dict as ephemeral.
- **Tests pin `SONGSMITH_OUT`.** `tests/conftest.py` sets it to `tests/_out` so test runs don't pollute the repo's `./out`. New tests don't need to do this themselves; just don't override it.
- **`new_song` purges `prop_*__*.mid` from `out_dir`** so stale previews don't confuse later sessions. The returned `purged_proposal_files` list can be long after a heavy session — that's expected, not a bug.

## Operational loops

- **`/test-songsmith`** runs one of 12 numbered scenarios (see `.claude/commands/test-songsmith.md`) end-to-end through the MCP tools, deduplicates findings, and appends to `songsmith-bugs.md`. It maintains a Scenario Run Ledger and picks the lowest-run-count scenario each iteration. Critical: it executes from the **main thread** — subagents do not reliably inherit the `mcp__songsmith__*` tools (see the meta note in `songsmith-bugs.md`).
- `songsmith-bugs.md` is the canonical bug log; check it before opening a new investigation in case the symptom is already tracked.
