"""MCP stdio server exposing the Songsmith composition toolkit.

Run standalone: ``python -m songsmith_mcp.server``
Or wire into Claude Desktop / Claude Code via an ``mcpServers`` config entry
pointing at this module.
"""

from __future__ import annotations

import asyncio
import copy
import json
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .arrangement import bass as bass_mod
from .arrangement import drums as drums_mod
from .arrangement import form as form_mod
from . import direct_edit as edit_mod
from .hitl import proposals as prop_mod
from .hitl.explain import explain as _explain
from .lyrics.align import DEFAULT_RHYTHMS, RHYTHM_TEMPLATES, align_lyrics_to_rhythm, as_rhythm_template
from .lyrics.syllabify import count_syllables, syllabify
from .reaper_bridge import get_bridge
from .render import render_song as _render_song
from .render.playback import open_with_default_app
from .render.score import export_score as _export_score, find_musescore
from .state import SongState, get_state, reset_state
from .theory import chords as chords_mod
from .theory import melody as melody_mod
from .theory import voice_leading as vl_mod


# ---------------------------------------------------------------------------
# Nested schemas shared between build_song and render_section.
#
# Keeping these enums here means unknown styles fail at the MCP-schema layer
# before ever reaching Python, and agents see the valid values in the tool
# description — no more guessing which style strings are implemented.
# ---------------------------------------------------------------------------

def _drums_subschema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "Same fields as write_drum_pattern.",
        "properties": {
            "style": {"type": "string", "enum": list(drums_mod.DRUM_STYLES)},
            "intensity": {
                "type": "string",
                "enum": list(drums_mod.DRUM_INTENSITIES),
                "default": "normal",
            },
            "track_name": {"type": "string", "default": "Drums"},
            "fill": {"type": "boolean", "default": False},
            "add_crash": {"type": "boolean"},
        },
    }


def _bass_subschema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "Same fields as write_bassline.",
        "properties": {
            "style": {"type": "string", "enum": list(bass_mod.BASS_STYLES)},
            "track_name": {"type": "string", "default": "Bass"},
            "seed": {"type": "integer"},
        },
    }


def _melody_subschema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "Same fields as propose_melody.",
        "properties": {
            "lyrics": {"type": "string", "default": ""},
            "contour": {
                "type": "string",
                "enum": list(melody_mod.MELODY_CONTOURS),
                "default": "arch",
            },
            "rhythm": {
                "type": "string",
                "enum": list(RHYTHM_TEMPLATES),
                "default": "eighths",
            },
            "range_lo": {"type": "integer", "default": 57},
            "range_hi": {"type": "integer", "default": 76},
            "max_leap": {"type": "integer", "default": 7},
            "seed": {"type": "integer"},
            "track_name": {"type": "string", "default": "Melody"},
        },
    }


server = Server("songsmith-mcp")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json(obj: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(obj, indent=2, default=str))]


# ---------------------------------------------------------------------------
# Background rendering
#
# The pure-numpy synth is fast per-note but scales linearly with total song
# duration — a 10-section multi-track song can easily exceed the MCP client's
# per-tool-call timeout (~60s-4min depending on client). Running it inline
# from build_song means the client gives up on the call, the user never sees
# the build result, and any subsequent tool call queues behind the still-in-
# flight render.
#
# Solution: kick synth/score work onto a daemon thread, write the result (or
# error) to a JSON sidecar in out_dir, and return from the tool call
# immediately. The caller gets the build summary right away, plus a pointer
# to the sidecar file they can poll (via ``observe`` or ``play_song``).
# ---------------------------------------------------------------------------

_BG_RENDER_LOCK = threading.Lock()
_BG_RENDERS: dict[str, threading.Thread] = {}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON to ``path`` via a tmp file + rename so readers never see a
    half-written file mid-render."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    tmp.replace(path)


def _start_background_render(
    state: SongState,
    out_dir: Path,
    *,
    basename: str,
    emit_stems: bool,
    emit_mp3: bool,
    sidecar_name: str,
) -> dict[str, Any]:
    """Snapshot ``state`` and run ``render_song`` in a daemon thread.

    The snapshot is vital: once this call returns, the caller may mutate the
    SongState (add clips, accept proposals, re-build) while the synth is
    still grinding on its copy. Without the deepcopy, those mutations would
    corrupt the in-flight render.

    Returns a status dict the caller can drop straight into their tool
    response. No ``ok=True`` yet — that lands in the sidecar once the render
    finishes.
    """
    snapshot = copy.deepcopy(state)
    sidecar_path = out_dir / sidecar_name
    # Mark as in-progress so stale results from a prior render don't confuse
    # callers that poll the sidecar.
    _write_json_atomic(sidecar_path, {
        "ok": False,
        "status": "rendering",
        "basename": basename,
    })

    def _run() -> None:
        try:
            result = _render_song(
                snapshot,
                out_dir,
                basename=basename,
                emit_stems=emit_stems,
                emit_mp3=emit_mp3,
            )
            _write_json_atomic(sidecar_path, {
                "ok": bool(result.get("ok")),
                "status": "done",
                **result,
            })
        except Exception as e:  # noqa: BLE001 — must always finalize sidecar
            _write_json_atomic(sidecar_path, {
                "ok": False,
                "status": "error",
                "error": str(e),
                "type": type(e).__name__,
                "basename": basename,
            })
        finally:
            with _BG_RENDER_LOCK:
                _BG_RENDERS.pop(basename, None)

    t = threading.Thread(target=_run, daemon=True, name=f"songsmith-render-{basename}")
    with _BG_RENDER_LOCK:
        _BG_RENDERS[basename] = t
    t.start()

    return {
        "ok": True,
        "status": "rendering_in_background",
        "basename": basename,
        "target_wav": str(out_dir / f"{basename}.wav"),
        "target_mp3": str(out_dir / f"{basename}.mp3"),
        "result_sidecar": str(sidecar_path),
        "hint": (
            "poll the sidecar or call play_song once status flips to 'done'. "
            "the render is running in a daemon thread and will NOT block "
            "other tool calls."
        ),
    }


def _start_background_score(
    state: SongState,
    out_dir: Path,
    *,
    basename: str,
    emit_png: bool,
) -> dict[str, Any]:
    """Same pattern as ``_start_background_render`` but for MusicXML / PNG
    score export. MuseScore PNG rendering can stall on cold start, so we
    treat it as a potentially-slow op even though XML alone is fast."""
    snapshot = copy.deepcopy(state)
    sidecar_path = out_dir / f"{basename}.score_result.json"
    _write_json_atomic(sidecar_path, {
        "ok": False,
        "status": "exporting",
        "basename": basename,
    })

    def _run() -> None:
        try:
            result = _export_score(
                snapshot,
                out_dir,
                basename=basename,
                emit_png=emit_png,
            )
            _write_json_atomic(sidecar_path, {
                "ok": True,
                "status": "done",
                **result,
            })
        except Exception as e:  # noqa: BLE001
            _write_json_atomic(sidecar_path, {
                "ok": False,
                "status": "error",
                "error": str(e),
                "type": type(e).__name__,
            })

    threading.Thread(target=_run, daemon=True, name=f"songsmith-score-{basename}").start()

    return {
        "ok": True,
        "status": "exporting_in_background",
        "basename": basename,
        "target_musicxml": str(out_dir / f"{basename}.musicxml"),
        "target_png": str(out_dir / f"{basename}.png"),
        "result_sidecar": str(sidecar_path),
    }


def _chords_by_beat_for_section(section_name: str) -> tuple[dict[float, list[int]], int, int]:
    """Find the most recent chord clip in a section and turn it into a
    ``{beat: chord_pitches}`` dict the melody/bass use."""
    st = get_state()
    section = st.section_by_name(section_name)
    beats_per_bar = st.time_sig[0] * (4 / st.time_sig[1])
    for tr in st.tracks.values():
        if tr.role != "chords":
            continue
        for clip in tr.clips:
            if clip.section != section_name:
                continue
            groups: dict[float, list[int]] = {}
            for n in clip.notes:
                groups.setdefault(n.start_beat, []).append(n.pitch)
            return groups, section.start_bar, int(beats_per_bar * section.bars)
    # No chord track yet.
    return {}, section.start_bar, int(beats_per_bar * section.bars)


# ---------------------------------------------------------------------------
# Shared builders — used by individual tool handlers AND render_section
# ---------------------------------------------------------------------------

def _build_chords_proposal(st: SongState, args: dict[str, Any]) -> tuple[Any, Any]:
    section = st.section_by_name(args["section"])
    roman = list(args["roman_numerals"])
    cand = chords_mod.candidate_from_romans(roman, st.key, args.get("rationale", ""))
    bars_per_chord = int(args.get("bars_per_chord", 1))
    clip = chords_mod.build_chord_clip(
        cand, section.name, args.get("track_name", "Chords"),
        st.time_sig, section.start_bar, bars_per_chord,
    )
    prop = prop_mod.create_proposal(
        kind="chords",
        section=section.name,
        track=args.get("track_name", "Chords"),
        clips=[clip],
        summary=f"{' | '.join(cand.chord_symbols)}  ({' '.join(cand.roman_numerals)})",
        rationale=cand.rationale,
    )
    return prop, cand


def _build_melody_proposal(st: SongState, args: dict[str, Any]) -> Any:
    section_name = args["section"]
    chords_by_beat_abs, section_start_bar, _ = _chords_by_beat_for_section(section_name)
    beats_per_bar = st.time_sig[0] * (4 / st.time_sig[1])
    chords_local: dict[float, list[int]] = {
        b - section_start_bar * beats_per_bar: v for b, v in chords_by_beat_abs.items()
    }

    lyrics = args.get("lyrics", "") or ""
    if lyrics.strip():
        aligned = align_lyrics_to_rhythm(
            lyrics,
            time_sig=st.time_sig,
            bars_hint=st.section_by_name(section_name).bars,
            rhythm=args.get("rhythm", "eighths"),
        )
        rhythm = as_rhythm_template(aligned)
        lyric_syls = [n.lyric for n in aligned.notes]
    else:
        bars = st.section_by_name(section_name).bars
        rhythm_name = args.get("rhythm", "eighths")
        if rhythm_name not in DEFAULT_RHYTHMS:
            raise ValueError(
                f"unknown rhythm {rhythm_name!r}; "
                f"expected one of {list(DEFAULT_RHYTHMS.keys())}"
            )
        one_bar = DEFAULT_RHYTHMS[rhythm_name]
        rhythm = []
        for b in range(bars):
            off = b * beats_per_bar
            rhythm.extend([(s + off, d) for s, d in one_bar])
        lyric_syls = [None] * len(rhythm)

    cand = melody_mod.propose_melody(
        key_str=st.key,
        chords_by_beat=chords_local,
        rhythm=rhythm,
        contour=args.get("contour", "arch"),
        vocal_range=(int(args.get("range_lo", 57)), int(args.get("range_hi", 76))),
        max_leap=int(args.get("max_leap", 7)),
        seed=args.get("seed"),
    )
    for note, lyr in zip(cand.notes, lyric_syls):
        note.lyric = lyr

    section = st.section_by_name(section_name)
    clip = melody_mod.build_melody_clip(
        cand,
        section_name=section_name,
        track_name=args.get("track_name", "Melody"),
        start_bar=section.start_bar,
        length_bars=section.bars,
    )
    return prop_mod.create_proposal(
        kind="melody",
        section=section_name,
        track=args.get("track_name", "Melody"),
        clips=[clip],
        summary=cand.summary,
        rationale=(
            f"Contour: {args.get('contour', 'arch')}. "
            f"Pitches on strong beats are chord tones; off-beats are scale tones. "
            f"Max leap: {args.get('max_leap', 7)} semitones."
        ),
    )


def _build_bass_proposal(st: SongState, args: dict[str, Any]) -> Any:
    section = st.section_by_name(args["section"])
    chords_by_beat_abs, _, _ = _chords_by_beat_for_section(section.name)
    if not chords_by_beat_abs:
        raise ValueError(f"no chord track in section {section.name!r}; write chords first")
    clip = bass_mod.write_bassline(
        chords_by_beat=chords_by_beat_abs,
        section_name=section.name,
        track_name=args.get("track_name", "Bass"),
        key_str=st.key,
        style=args.get("style", "roots"),
        time_sig=st.time_sig,
        start_bar=section.start_bar,
        bars=section.bars,
        seed=args.get("seed"),
    )
    return prop_mod.create_proposal(
        kind="bass",
        section=section.name,
        track=args.get("track_name", "Bass"),
        clips=[clip],
        summary=f"{args.get('style', 'roots')} bass over {section.bars} bars",
        rationale="Bass follows chord roots; approach tones and fifths as the style requires.",
    )


def _build_drums_proposal(st: SongState, args: dict[str, Any]) -> Any:
    section = st.section_by_name(args["section"])
    fill = bool(args.get("fill", False))
    add_crash = args.get("add_crash")
    clip = drums_mod.write_drum_pattern(
        section_name=section.name,
        track_name=args.get("track_name", "Drums"),
        style=args.get("style", "pop"),
        intensity=args.get("intensity", "normal"),
        bars=section.bars,
        start_bar=section.start_bar,
        time_sig=st.time_sig,
        fill=fill,
        add_crash=None if add_crash is None else bool(add_crash),
    )
    flags = []
    if fill:
        flags.append("fill")
    flag_str = f" [{', '.join(flags)}]" if flags else ""
    return prop_mod.create_proposal(
        kind="drums",
        section=section.name,
        track=args.get("track_name", "Drums"),
        clips=[clip],
        summary=(
            f"{args.get('style', 'pop')} drums "
            f"({args.get('intensity', 'normal')}), {section.bars} bars{flag_str}"
        ),
        rationale=(
            "Kick on 1/3, snare on 2/4 is the backbeat. Hats subdivide the beat to set the feel."
        ),
    )


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

@server.list_tools()
async def _list_tools() -> list[Tool]:
    return [
        Tool(
            name="new_song",
            description="Start a fresh song. Sets key, tempo, time signature, style hint; clears prior state.",
            inputSchema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "default": "C major"},
                    "tempo": {"type": "number", "default": 100},
                    "time_sig_num": {"type": "integer", "default": 4},
                    "time_sig_den": {"type": "integer", "default": 4},
                    "style_hint": {"type": "string", "default": ""},
                    "explain_level": {"type": "string", "enum": ["silent", "normal", "tutor"], "default": "normal"},
                },
            },
        ),
        Tool(
            name="observe",
            description=(
                "Return the current song state. By default returns a compact "
                "digest (key, tempo, form, per-track clip+note counts, pending "
                "proposal summaries, REAPER status) that stays under a couple "
                "KB regardless of song length. Set verbose=true to dump every "
                "note of every clip — a 40-bar song can easily blow past MCP "
                "payload limits in verbose mode, so prefer view_clip / "
                "list_clips for targeted note inspection."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "verbose": {
                        "type": "boolean",
                        "default": False,
                        "description": "Include raw per-note data. Off by default.",
                    }
                },
            },
        ),
        Tool(
            name="suggest_form",
            description="Propose candidate song forms (intro/verse/chorus/…) sized to a target duration.",
            inputSchema={
                "type": "object",
                "properties": {
                    "style": {"type": "string", "default": "pop"},
                    "target_duration_s": {"type": "number", "default": 180},
                },
            },
        ),
        Tool(
            name="set_form",
            description="Commit a specific form to the song as section markers. Overwrites prior form.",
            inputSchema={
                "type": "object",
                "properties": {
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}, "bars": {"type": "integer"}},
                            "required": ["name", "bars"],
                        },
                    }
                },
                "required": ["sections"],
            },
        ),
        Tool(
            name="propose_chord_progression",
            description="Return 1–5 candidate chord progressions (with Roman-numeral analysis) suitable for the key + style.",
            inputSchema={
                "type": "object",
                "properties": {
                    "section": {"type": "string"},
                    "style": {"type": "string", "default": "pop"},
                    "length_bars": {"type": "integer", "default": 4},
                    "n_candidates": {"type": "integer", "default": 3},
                    "seed": {"type": "integer"},
                },
                "required": ["section"],
            },
        ),
        Tool(
            name="write_chords",
            description="Commit one chord-progression candidate as a proposal (materialized as MIDI block-chord clip).",
            inputSchema={
                "type": "object",
                "properties": {
                    "section": {"type": "string"},
                    "roman_numerals": {"type": "array", "items": {"type": "string"}},
                    "bars_per_chord": {"type": "integer", "default": 1},
                    "track_name": {"type": "string", "default": "Chords"},
                    "rationale": {"type": "string", "default": ""},
                },
                "required": ["section", "roman_numerals"],
            },
        ),
        Tool(
            name="revoice",
            description="Re-voice an existing chord clip for smoother voice leading. style ∈ close/open/drop2/drop3/spread.",
            inputSchema={
                "type": "object",
                "properties": {
                    "section": {"type": "string"},
                    "track_name": {"type": "string", "default": "Chords"},
                    "style": {"type": "string", "default": "close"},
                },
                "required": ["section"],
            },
        ),
        Tool(
            name="syllabify",
            description="Split a lyric line into syllables with stress markers.",
            inputSchema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        ),
        Tool(
            name="align_lyrics_to_rhythm",
            description="Produce a per-syllable rhythm (one note per syllable). rhythm ∈ eighths/quarters/dotted/syncopated/waltz.",
            inputSchema={
                "type": "object",
                "properties": {
                    "lyrics": {"type": "string"},
                    "rhythm": {"type": "string", "default": "eighths"},
                    "bars_hint": {"type": "integer"},
                },
                "required": ["lyrics"],
            },
        ),
        Tool(
            name="propose_melody",
            description="Generate a melody candidate. Requires chords already written in the section; uses lyric rhythm if given.",
            inputSchema={
                "type": "object",
                "properties": {
                    "section": {"type": "string"},
                    "lyrics": {"type": "string", "default": ""},
                    "contour": {
                        "type": "string",
                        "enum": list(melody_mod.MELODY_CONTOURS),
                        "default": "arch",
                    },
                    "rhythm": {
                        "type": "string",
                        "enum": list(RHYTHM_TEMPLATES),
                        "default": "eighths",
                    },
                    "range_lo": {"type": "integer", "default": 57},
                    "range_hi": {"type": "integer", "default": 76},
                    "max_leap": {"type": "integer", "default": 7},
                    "seed": {"type": "integer"},
                    "track_name": {"type": "string", "default": "Melody"},
                },
                "required": ["section"],
            },
        ),
        Tool(
            name="write_bassline",
            description="Write a bass part under the chord track in a section.",
            inputSchema={
                "type": "object",
                "properties": {
                    "section": {"type": "string"},
                    "style": {
                        "type": "string",
                        "enum": list(bass_mod.BASS_STYLES),
                        "default": "roots",
                    },
                    "track_name": {"type": "string", "default": "Bass"},
                    "seed": {"type": "integer"},
                },
                "required": ["section"],
            },
        ),
        Tool(
            name="write_drum_pattern",
            description=(
                "Write a drum clip in a section. "
                "intensity reshapes the pattern (light thins hats, heavy adds open hats + ghost snares). "
                "Set fill=true on the bar before a chorus/drop — the last bar's second half becomes a tom fill."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "section": {"type": "string"},
                    "style": {
                        "type": "string",
                        "enum": list(drums_mod.DRUM_STYLES),
                        "default": "pop",
                    },
                    "intensity": {
                        "type": "string",
                        "enum": list(drums_mod.DRUM_INTENSITIES),
                        "default": "normal",
                    },
                    "track_name": {"type": "string", "default": "Drums"},
                    "fill": {
                        "type": "boolean",
                        "default": False,
                        "description": "Replace the last bar's second half with a tom fill (for pre-chorus lifts).",
                    },
                    "add_crash": {
                        "type": "boolean",
                        "description": "Force a crash on bar 1 on/off. Defaults to true unless intensity=light.",
                    },
                },
                "required": ["section"],
            },
        ),
        Tool(
            name="build_song",
            description=(
                "Compose an entire song in a single MCP call. Takes a list of "
                "per-section specs, each naming a section and its chords "
                "(required) plus optional drums/bass/melody. Equivalent to "
                "calling render_section once per section — but one tool call "
                "can lay down a whole multi-section song, drastically cutting "
                "agent tool-use count. Set auto_accept=false to review each "
                "section's chords before bass/melody layers are computed from "
                "them. "
                "If no form has been committed yet (set_form was not called), "
                "the form is auto-derived from the sections array — each "
                "section is sized to len(roman_numerals) * bars_per_chord. "
                "Repeated names are disambiguated ('verse','verse' → "
                "'verse','verse.2'); the resolved names show up in the "
                "per-section response entries. "
                "Returns a lean summary (proposal_ids + kinds + chord symbols "
                "per section, a compact digest of the resulting song, and "
                "only the output file paths for audio/score) — no raw note "
                "arrays. Call observe (compact) or view_clip for inspection."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "section": {"type": "string"},
                                "chords": {
                                    "type": "object",
                                    "properties": {
                                        "roman_numerals": {"type": "array", "items": {"type": "string"}},
                                        "bars_per_chord": {"type": "integer", "default": 1},
                                        "track_name": {"type": "string", "default": "Chords"},
                                        "rationale": {"type": "string", "default": ""},
                                    },
                                    "required": ["roman_numerals"],
                                },
                                "drums": _drums_subschema(),
                                "bass": _bass_subschema(),
                                "melody": _melody_subschema(),
                            },
                            "required": ["section", "chords"],
                        },
                    },
                    "auto_accept": {"type": "boolean", "default": True},
                    "default_drums": {
                        **_drums_subschema(),
                        "description": "Applied to every section that doesn't set its own drums. Same fields as write_drum_pattern.",
                    },
                    "default_bass": {
                        **_bass_subschema(),
                        "description": "Applied to every section that doesn't set its own bass. Same fields as write_bassline.",
                    },
                    "render_audio": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "After all sections are built+accepted, kick off a "
                            "background render of song.wav/song.mp3 (+ stems) "
                            "under out_dir. Requires auto_accept=true. The "
                            "render runs on a daemon thread so this call "
                            "returns immediately — poll the result_sidecar "
                            "path in the audio field (status flips from "
                            "'rendering' to 'done') or just call play_song "
                            "later. Partial state is persisted to "
                            "last_build.json before rendering starts, so "
                            "a render crash can never lose the composition."
                        ),
                    },
                    "render_score": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "After building, kick off a background score "
                            "export (score.musicxml always; score.png if "
                            "MuseScore 4 is installed). Requires "
                            "auto_accept=true. Like render_audio, this returns "
                            "immediately and writes a result sidecar the "
                            "caller can poll."
                        ),
                    },
                    "open_after_build": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Legacy one-shot flag. Ignored when render_audio "
                            "or render_score is true (the render is now "
                            "backgrounded, so there's nothing to open yet). "
                            "Call play_song / view_score after the sidecar "
                            "flips to status=done."
                        ),
                    },
                },
                "required": ["sections"],
            },
        ),
        Tool(
            name="view_clip",
            description=(
                "Return a human-readable listing of every note in one clip "
                "(pitch name + MIDI number, start beat, duration, velocity, "
                "lyric). Use this before edit_note / delete_note to discover "
                "note indices — it replaces needing to open the .mid file in "
                "MuseScore. If section is omitted, lists all clips on the "
                "track with summaries."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "track_name": {"type": "string"},
                    "section": {"type": "string"},
                    "clip_index": {"type": "integer", "default": 0},
                },
                "required": ["track_name"],
            },
        ),
        Tool(
            name="list_clips",
            description="List every clip across every track with one-line summaries — useful as an index before calling view_clip.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="edit_notes",
            description=(
                "Batch-edit multiple notes in one call. Pass a list of edits, "
                "each with note_index and any of pitch/start_beat/duration_beats/"
                "velocity/lyric. Single .mid re-render at the end instead of "
                "one per edit."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "track_name": {"type": "string"},
                    "section": {"type": "string"},
                    "clip_index": {"type": "integer", "default": 0},
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "note_index": {"type": "integer"},
                                "pitch": {"type": "integer"},
                                "start_beat": {"type": "number"},
                                "duration_beats": {"type": "number"},
                                "velocity": {"type": "integer"},
                                "lyric": {"type": "string"},
                            },
                            "required": ["note_index"],
                        },
                    },
                },
                "required": ["track_name", "section", "edits"],
            },
        ),
        Tool(
            name="transpose_clip",
            description="Shift every note in one clip by a semitone offset. Positive = up, negative = down. Single re-render.",
            inputSchema={
                "type": "object",
                "properties": {
                    "track_name": {"type": "string"},
                    "section": {"type": "string"},
                    "semitones": {"type": "integer"},
                    "clip_index": {"type": "integer", "default": 0},
                },
                "required": ["track_name", "section", "semitones"],
            },
        ),
        Tool(
            name="render_section",
            description=(
                "Composite tool: lay down chords (required) plus any combination of "
                "drums / bass / melody for one section in a single call, with optional "
                "auto_accept. Designed to cut agent tool-count when building a "
                "multi-section song — one render_section call replaces up to eight "
                "individual write_* + accept_proposal calls. Returns the proposal ids "
                "created and (if auto_accept) the accept results."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "section": {"type": "string"},
                    "chords": {
                        "type": "object",
                        "description": "Required. { roman_numerals: [str], bars_per_chord?, track_name?, rationale? }",
                        "properties": {
                            "roman_numerals": {"type": "array", "items": {"type": "string"}},
                            "bars_per_chord": {"type": "integer", "default": 1},
                            "track_name": {"type": "string", "default": "Chords"},
                            "rationale": {"type": "string", "default": ""},
                        },
                        "required": ["roman_numerals"],
                    },
                    "drums": _drums_subschema(),
                    "bass": _bass_subschema(),
                    "melody": _melody_subschema(),
                    "auto_accept": {"type": "boolean", "default": True},
                },
                "required": ["section", "chords"],
            },
        ),
        Tool(
            name="humanize",
            description="Apply micro-timing and velocity drift to a track's clips (mutates in place).",
            inputSchema={
                "type": "object",
                "properties": {
                    "track_name": {"type": "string"},
                    "timing_jitter_ticks": {"type": "number", "default": 8},
                    "velocity_jitter": {"type": "integer", "default": 8},
                    "seed": {"type": "integer"},
                },
                "required": ["track_name"],
            },
        ),
        Tool(
            name="list_proposals",
            description="List pending proposals with a one-line diff each.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="diff_proposal",
            description="Show bars touched, note count, and summary for one proposal.",
            inputSchema={
                "type": "object",
                "properties": {"proposal_id": {"type": "string"}},
                "required": ["proposal_id"],
            },
        ),
        Tool(
            name="accept_proposal",
            description="Commit a proposal: move clips onto the real track, re-render .mid.",
            inputSchema={
                "type": "object",
                "properties": {"proposal_id": {"type": "string"}},
                "required": ["proposal_id"],
            },
        ),
        Tool(
            name="reject_proposal",
            description="Discard a proposal.",
            inputSchema={
                "type": "object",
                "properties": {"proposal_id": {"type": "string"}},
                "required": ["proposal_id"],
            },
        ),
        Tool(
            name="bulk_accept_proposals",
            description=(
                "Accept many proposals in one call. Pass a list of proposal_ids, "
                "or omit to accept every currently pending proposal. Returns "
                "accepted results and any ids that were not found."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "proposal_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        ),
        Tool(
            name="bulk_reject_proposals",
            description=(
                "Reject many proposals in one call. Pass a list of proposal_ids, "
                "or omit to reject every currently pending proposal."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "proposal_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        ),
        Tool(
            name="explain",
            description="Return a human-readable rationale for a proposal (verbosity depends on explain_level).",
            inputSchema={
                "type": "object",
                "properties": {"proposal_id": {"type": "string"}},
                "required": ["proposal_id"],
            },
        ),
        Tool(
            name="set_explain_level",
            description="Set verbosity of explanations. silent = one-liner; normal = summary+rationale; tutor = beginner-friendly.",
            inputSchema={
                "type": "object",
                "properties": {"level": {"type": "string", "enum": ["silent", "normal", "tutor"]}},
                "required": ["level"],
            },
        ),
        Tool(
            name="reaper_status",
            description="Is REAPER connected? Where are .mid files being written?",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="import_midi",
            description=(
                "Read a .mid file back into the song as a clip on (track, section). "
                "Use this after hand-editing a clip in REAPER / MuseScore. "
                "Defaults to direct commit, replacing any existing clip at that slot; "
                "set as_proposal=true to review first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "track_name": {"type": "string"},
                    "section": {"type": "string"},
                    "as_proposal": {"type": "boolean", "default": False},
                    "role": {"type": "string"},
                    "clip_index": {"type": "integer", "default": 0},
                },
                "required": ["path", "track_name", "section"],
            },
        ),
        Tool(
            name="edit_note",
            description=(
                "Directly mutate one note in an existing clip. Any of pitch / "
                "start_beat / duration_beats / velocity / lyric can be changed; "
                "omitted fields are left alone. Re-renders the .mid file."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "track_name": {"type": "string"},
                    "section": {"type": "string"},
                    "note_index": {"type": "integer"},
                    "pitch": {"type": "integer"},
                    "start_beat": {"type": "number"},
                    "duration_beats": {"type": "number"},
                    "velocity": {"type": "integer"},
                    "lyric": {"type": "string"},
                    "clip_index": {"type": "integer", "default": 0},
                },
                "required": ["track_name", "section", "note_index"],
            },
        ),
        Tool(
            name="add_note",
            description="Add one note to an existing clip. Re-renders the .mid file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "track_name": {"type": "string"},
                    "section": {"type": "string"},
                    "pitch": {"type": "integer"},
                    "start_beat": {"type": "number"},
                    "duration_beats": {"type": "number"},
                    "velocity": {"type": "integer", "default": 90},
                    "lyric": {"type": "string"},
                    "clip_index": {"type": "integer", "default": 0},
                },
                "required": [
                    "track_name",
                    "section",
                    "pitch",
                    "start_beat",
                    "duration_beats",
                ],
            },
        ),
        Tool(
            name="delete_note",
            description="Remove one note from an existing clip by index. Re-renders the .mid file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "track_name": {"type": "string"},
                    "section": {"type": "string"},
                    "note_index": {"type": "integer"},
                    "clip_index": {"type": "integer", "default": 0},
                },
                "required": ["track_name", "section", "note_index"],
            },
        ),
        Tool(
            name="view_score",
            description=(
                "Export the current SongState as engraved sheet music. Always "
                "writes {out}/score.musicxml (openable in any notation app). "
                "Also writes {out}/score.png when MuseScore 4 is installed — "
                "detected automatically. Set open=true (default) to pop the "
                "PNG/MusicXML in the OS default viewer so the user sees it "
                "immediately without hunting in the out folder."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "basename": {"type": "string", "default": "score"},
                    "emit_png": {"type": "boolean", "default": True},
                    "open": {"type": "boolean", "default": True},
                },
            },
        ),
        Tool(
            name="play_song",
            description=(
                "Open {out}/song.mp3 in the OS default media player (falls "
                "back to song.wav if mp3 wasn't rendered). Call after "
                "build_song / render_song to hear the result without manually "
                "hunting in the out folder."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "basename": {"type": "string", "default": "song"},
                },
            },
        ),
        Tool(
            name="render_song",
            description=(
                "Render the current SongState to listenable audio. Emits "
                "{out}/song.wav (always) and {out}/song.mp3 (if ffmpeg is "
                "installed) plus per-role stems ({basename}__melody.wav, "
                "__chords.wav, __bass.wav, __drums.wav) so individual tracks "
                "can be swapped out later (e.g., replace the melody stem with "
                "a vocaloid render and re-mix). Uses a built-in numpy synth "
                "by default — zero install friction. "
                "Defaults to background=true — the synth runs on a daemon "
                "thread and this call returns immediately with a sidecar "
                "pointer, because a 10-section song can easily exceed the "
                "MCP client's per-call timeout. Set background=false only "
                "when you want to block and wait (tests, short jingles)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "basename": {"type": "string", "default": "song"},
                    "emit_stems": {"type": "boolean", "default": True},
                    "emit_mp3": {"type": "boolean", "default": True},
                    "background": {
                        "type": "boolean",
                        "default": True,
                        "description": (
                            "When true (default), kick the render onto a "
                            "daemon thread and return a sidecar pointer "
                            "immediately. When false, block until the "
                            "render completes and return the full result."
                        ),
                    },
                },
            },
        ),
    ]


@server.call_tool()
async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Offload the sync dispatch onto a worker thread.

    Previously ``_dispatch`` ran directly on the asyncio event loop, which
    meant a long ``render_song`` (or ``build_song`` with ``render_audio=true``)
    blocked *every* subsequent tool call — even a near-instant ``observe`` —
    until the synth finished. With ``to_thread`` the loop stays responsive and
    other tool calls proceed in parallel. Slow renders snapshot the state up
    front (see ``_start_background_render``) so a concurrent mutation on the
    SongState singleton can't corrupt an in-flight render.
    """
    try:
        return await asyncio.to_thread(_dispatch, name, arguments or {})
    except KeyError as e:
        return _json({"error": f"not found: {e}"})
    except Exception as e:
        return _json({"error": str(e), "type": type(e).__name__})


def _dispatch(name: str, args: dict[str, Any]) -> list[TextContent]:
    st = get_state()

    if name == "new_song":
        st = reset_state()
        st.key = args.get("key", "C major")
        st.tempo = float(args.get("tempo", 100))
        st.time_sig = (int(args.get("time_sig_num", 4)), int(args.get("time_sig_den", 4)))
        st.style_hint = args.get("style_hint", "")
        st.explain_level = args.get("explain_level", "normal")
        bridge = get_bridge()
        purged = bridge.purge_proposal_files()
        bridge.set_tempo(st.tempo)
        bridge.set_time_sig(*st.time_sig)
        return _json({
            "ok": True,
            **st.to_dict(),
            "reaper": bridge.status(),
            "purged_proposal_files": purged,
        })

    if name == "observe":
        bridge = get_bridge()
        if bool(args.get("verbose", False)):
            d = st.to_dict()
            d["reaper"] = bridge.status()
            d["pending_proposal_ids"] = list(st.proposals.keys())
            d["verbose"] = True
            return _json(d)
        d = st.summary()
        d["reaper"] = bridge.status()
        d["pending_proposal_ids"] = list(st.proposals.keys())
        d["verbose"] = False
        return _json(d)

    if name == "suggest_form":
        cands = form_mod.suggest_form(
            style=args.get("style", "pop"),
            target_duration_s=float(args.get("target_duration_s", 180)),
            tempo=st.tempo,
        )
        return _json([asdict(c) for c in cands])

    if name == "set_form":
        sections = [(s["name"], int(s["bars"])) for s in args["sections"]]
        form_mod.apply_form(st, sections)
        bridge = get_bridge()
        for s in st.sections:
            bridge.set_region(s.start_bar, s.start_bar + s.bars, s.name)
        return _json({"ok": True, "sections": [asdict(s) for s in st.sections]})

    if name == "propose_chord_progression":
        cands = chords_mod.propose_chord_progression(
            key_str=st.key,
            style=args.get("style", "pop"),
            length_bars=int(args.get("length_bars", 4)),
            n_candidates=int(args.get("n_candidates", 3)),
            seed=args.get("seed"),
        )
        out = [asdict(c) for c in cands]
        return _json({"section": args["section"], "candidates": out})

    if name == "write_chords":
        prop, cand = _build_chords_proposal(st, args)
        return _json({"proposal_id": prop.id, "summary": prop.summary, "chord_symbols": cand.chord_symbols})

    if name == "revoice":
        section_name = args["section"]
        track_name = args.get("track_name", "Chords")
        tr = st.tracks.get(track_name)
        if not tr:
            raise KeyError(track_name)
        changed = 0
        for clip in tr.clips:
            if clip.section != section_name:
                continue
            new_clip = vl_mod.revoice_clip(clip, style=args.get("style", "close"))
            clip.notes = new_clip.notes
            changed += 1
        return _json({"ok": True, "clips_revoiced": changed})

    if name == "syllabify":
        out = [asdict(s) for s in syllabify(args["text"])]
        return _json({"syllables": out, "count": count_syllables(args["text"])})

    if name == "align_lyrics_to_rhythm":
        aligned = align_lyrics_to_rhythm(
            args["lyrics"],
            time_sig=st.time_sig,
            bars_hint=args.get("bars_hint"),
            rhythm=args.get("rhythm", "eighths"),
        )
        return _json(
            {
                "slots": [(n.start_beat, n.duration_beats, n.lyric) for n in aligned.notes],
                "bars_used": aligned.bars_used,
                "syllable_count": len(aligned.notes),
            }
        )

    if name == "propose_melody":
        prop = _build_melody_proposal(st, args)
        return _json({"proposal_id": prop.id, "summary": prop.summary})

    if name == "write_bassline":
        try:
            prop = _build_bass_proposal(st, args)
        except ValueError as e:
            return _json({"error": str(e)})
        return _json({"proposal_id": prop.id, "summary": prop.summary})

    if name == "write_drum_pattern":
        prop = _build_drums_proposal(st, args)
        return _json({"proposal_id": prop.id, "summary": prop.summary})

    if name == "build_song":
        auto_accept = bool(args.get("auto_accept", True))
        default_drums = args.get("default_drums") or None
        default_bass = args.get("default_bass") or None
        sections_arg = args["sections"]

        # Auto-derive the form if no set_form call has landed yet. Without
        # this, a vanilla new_song → build_song sequence fails with a cryptic
        # `no such section: 'Intro'` from deep inside _build_chords_proposal,
        # because generators look up sections by name. Size each section to
        # len(roman_numerals) * bars_per_chord — that's exactly the span the
        # chord clip will occupy, so nothing hangs off the end.
        # When the caller has *already* committed a form, we don't touch it;
        # but if their spec names don't match that form at all, surface a
        # targeted error instead of letting section_by_name raise KeyError.
        missing = [
            s["section"] for s in sections_arg
            if not any(existing.name == s["section"] for existing in st.sections)
        ]
        resolved_names: list[str] = [s["section"] for s in sections_arg]
        if missing and not st.sections:
            derived: list[tuple[str, int]] = []
            for spec in sections_arg:
                chords_spec = spec.get("chords") or {}
                roman = chords_spec.get("roman_numerals") or []
                bpc = int(chords_spec.get("bars_per_chord", 1))
                bars = max(1, len(roman) * bpc)
                derived.append((spec["section"], bars))
            form_mod.apply_form(st, derived)
            bridge = get_bridge()
            for s in st.sections:
                bridge.set_region(s.start_bar, s.start_bar + s.bars, s.name)
            # apply_form disambiguates repeats (verse, verse → verse, verse.2),
            # so the dispatch below must use the resolved names — otherwise
            # both iterations would look up "verse" and clobber the first clip.
            resolved_names = [s.name for s in st.sections]
        elif missing:
            return _json({
                "error": (
                    f"sections {missing!r} not in current form "
                    f"{[s.name for s in st.sections]!r}. "
                    f"Call set_form first, or start fresh with new_song "
                    f"so build_song can auto-derive the form from this "
                    f"sections array."
                ),
                "type": "UnknownSection",
                "missing": missing,
                "existing_sections": [s.name for s in st.sections],
            })

        out_sections: list[dict[str, Any]] = []
        section_errors: list[dict[str, Any]] = []
        for idx, spec in enumerate(sections_arg):
            section_name = resolved_names[idx]
            render_args: dict[str, Any] = {
                "section": section_name,
                "chords": spec["chords"],
                "auto_accept": auto_accept,
            }
            if spec.get("drums") is not None:
                render_args["drums"] = spec["drums"]
            elif default_drums is not None:
                render_args["drums"] = default_drums
            if spec.get("bass") is not None:
                render_args["bass"] = spec["bass"]
            elif default_bass is not None:
                render_args["bass"] = default_bass
            if spec.get("melody") is not None:
                render_args["melody"] = spec["melody"]
            # Reuse the render_section handler logic.
            rendered = _dispatch("render_section", render_args)
            parsed = json.loads(rendered[0].text)
            if isinstance(parsed, dict) and "error" in parsed:
                section_errors.append({"section": spec["section"], **parsed})
                continue
            # Trim the per-section record to what callers actually need: id
            # list, kind list, chord symbols, top-line summaries. The full
            # SongState never leaves this function.
            out_sections.append({
                "section": parsed.get("section"),
                "proposal_ids": parsed.get("proposal_ids", []),
                "proposal_count": len(parsed.get("proposal_ids", [])),
                "kinds": sorted(parsed.get("summaries", {}).keys()),
                "chord_symbols": parsed.get("chord_symbols", []),
                "auto_accepted": parsed.get("auto_accepted", auto_accept),
            })
        audio: dict[str, Any] = {}
        score: dict[str, Any] = {}
        out_dir = get_bridge().out_dir

        # Persist the build result BEFORE starting any render. If the synth
        # hangs or the client gives up on the response, the user can still
        # recover every proposal_id / chord_symbol / section summary from
        # out_dir/last_build.json without rebuilding the song.
        build_record = {
            "ok": not section_errors,
            "sections": out_sections,
            "section_errors": section_errors,
            "total_proposals": sum(s.get("proposal_count", 0) for s in out_sections),
            "digest": st.summary(),
        }
        try:
            _write_json_atomic(out_dir / "last_build.json", build_record)
        except OSError:
            # Non-fatal — the sidecar is a recovery aid, not a hard contract.
            pass

        want_audio = auto_accept and bool(args.get("render_audio", False))
        want_score = auto_accept and bool(args.get("render_score", False))
        open_after = bool(args.get("open_after_build", False))

        if want_audio:
            audio = _start_background_render(
                st,
                out_dir,
                basename="song",
                emit_stems=True,
                emit_mp3=True,
                sidecar_name="song.render_result.json",
            )
        if want_score:
            score = _start_background_score(st, out_dir, basename="score", emit_png=True)

        # open_after_build only makes sense for synchronous renders — the
        # target file doesn't exist yet when we return. Surface that instead
        # of silently dropping the flag.
        opened: dict[str, str | None] = {}
        if open_after and (want_audio or want_score):
            opened["note"] = (
                "open_after_build ignored: render is running in the background. "
                "call play_song / view_score once the sidecar flips to status=done."
            )

        return _json({
            **build_record,
            "audio": audio,
            "score": score,
            "opened": opened,
            "last_build_sidecar": str(out_dir / "last_build.json"),
        })

    if name == "render_song":
        basename = args.get("basename", "song")
        emit_stems = bool(args.get("emit_stems", True))
        emit_mp3 = bool(args.get("emit_mp3", True))
        out_dir = get_bridge().out_dir
        # Default to background so a slow render doesn't trip the MCP client
        # timeout. Callers that want to block (tests, short renders) opt out.
        if bool(args.get("background", True)):
            return _json(_start_background_render(
                st,
                out_dir,
                basename=basename,
                emit_stems=emit_stems,
                emit_mp3=emit_mp3,
                sidecar_name=f"{basename}.render_result.json",
            ))
        result = _render_song(
            st,
            out_dir,
            basename=basename,
            emit_stems=emit_stems,
            emit_mp3=emit_mp3,
        )
        return _json(result)

    if name == "view_score":
        basename = args.get("basename", "score")
        emit_png = bool(args.get("emit_png", True))
        do_open = bool(args.get("open", True))
        out_dir = get_bridge().out_dir
        result = _export_score(st, out_dir, basename=basename, emit_png=emit_png)
        opened: str | None = None
        if do_open:
            target = result["png"] or result["musicxml"]
            if target and open_with_default_app(Path(target)):
                opened = target
        return _json({**result, "opened": opened})

    if name == "play_song":
        basename = args.get("basename", "song")
        out_dir = get_bridge().out_dir
        mp3 = out_dir / f"{basename}.mp3"
        wav = out_dir / f"{basename}.wav"
        target = mp3 if mp3.exists() else wav if wav.exists() else None
        if target is None:
            return _json({
                "ok": False,
                "error": f"no {basename}.mp3 or {basename}.wav in {out_dir} — run render_song / build_song first",
            })
        launched = open_with_default_app(target)
        return _json({"ok": launched, "played": str(target)})

    if name == "view_clip":
        track_name = args["track_name"]
        tr = st.tracks.get(track_name)
        if not tr:
            raise KeyError(track_name)
        if "section" not in args:
            return _json({
                "track": track_name,
                "role": tr.role,
                "clips": [
                    {
                        "section": c.section,
                        "start_bar": c.start_bar,
                        "length_bars": c.length_bars,
                        "note_count": len(c.notes),
                        "chord_symbol": c.chord_symbol,
                    }
                    for c in tr.clips
                ],
            })
        section = args["section"]
        clip_index = int(args.get("clip_index", 0))
        matches = [c for c in tr.clips if c.section == section]
        if not matches:
            raise KeyError(f"no clip on {track_name!r} in section {section!r}")
        if clip_index >= len(matches):
            raise IndexError(f"clip_index {clip_index} out of range ({len(matches)} clips)")
        clip = matches[clip_index]
        from music21 import pitch as _mpitch
        note_rows: list[dict[str, Any]] = []
        for i, n in enumerate(clip.notes):
            note_rows.append({
                "index": i,
                "pitch": n.pitch,
                "name": _mpitch.Pitch(midi=n.pitch).nameWithOctave,
                "start_beat": round(n.start_beat, 4),
                "duration_beats": round(n.duration_beats, 4),
                "velocity": n.velocity,
                "lyric": n.lyric,
            })
        return _json({
            "track": track_name,
            "section": section,
            "clip_index": clip_index,
            "start_bar": clip.start_bar,
            "length_bars": clip.length_bars,
            "chord_symbol": clip.chord_symbol,
            "note_count": len(clip.notes),
            "notes": note_rows,
        })

    if name == "list_clips":
        rows: list[dict[str, Any]] = []
        for tname, tr in st.tracks.items():
            for c in tr.clips:
                rows.append({
                    "track": tname,
                    "role": tr.role,
                    "section": c.section,
                    "start_bar": c.start_bar,
                    "length_bars": c.length_bars,
                    "notes": len(c.notes),
                    "chord_symbol": c.chord_symbol,
                })
        return _json({"clips": rows, "count": len(rows)})

    if name == "edit_notes":
        track_name = args["track_name"]
        section = args["section"]
        clip_index = int(args.get("clip_index", 0))
        tr = st.tracks.get(track_name)
        if not tr:
            raise KeyError(track_name)
        matches = [c for c in tr.clips if c.section == section]
        if not matches:
            raise KeyError(f"no clip on {track_name!r} in section {section!r}")
        if clip_index >= len(matches):
            raise IndexError(f"clip_index {clip_index} out of range ({len(matches)} clips)")
        clip = matches[clip_index]
        applied = 0
        errors: list[dict[str, Any]] = []
        for e in args.get("edits", []):
            idx = int(e["note_index"])
            if idx < 0 or idx >= len(clip.notes):
                errors.append({"note_index": idx, "error": "out of range"})
                continue
            n = clip.notes[idx]
            if "pitch" in e and e["pitch"] is not None:
                n.pitch = int(e["pitch"])
            if "start_beat" in e and e["start_beat"] is not None:
                n.start_beat = float(e["start_beat"])
            if "duration_beats" in e and e["duration_beats"] is not None:
                n.duration_beats = float(e["duration_beats"])
            if "velocity" in e and e["velocity"] is not None:
                n.velocity = int(e["velocity"])
            if "lyric" in e:
                n.lyric = e["lyric"] or None
            applied += 1
        path = get_bridge().insert_clip(clip, st, proposal_id=None)
        return _json({
            "ok": True,
            "applied": applied,
            "errors": errors,
            "written_midi": path,
        })

    if name == "transpose_clip":
        track_name = args["track_name"]
        section = args["section"]
        semitones = int(args["semitones"])
        clip_index = int(args.get("clip_index", 0))
        tr = st.tracks.get(track_name)
        if not tr:
            raise KeyError(track_name)
        matches = [c for c in tr.clips if c.section == section]
        if not matches:
            raise KeyError(f"no clip on {track_name!r} in section {section!r}")
        if clip_index >= len(matches):
            raise IndexError(f"clip_index {clip_index} out of range ({len(matches)} clips)")
        clip = matches[clip_index]
        for n in clip.notes:
            n.pitch = max(0, min(127, n.pitch + semitones))
        path = get_bridge().insert_clip(clip, st, proposal_id=None)
        return _json({
            "ok": True,
            "notes_transposed": len(clip.notes),
            "semitones": semitones,
            "written_midi": path,
        })

    if name == "render_section":
        section_name = args["section"]
        chords_args = dict(args["chords"])
        chords_args["section"] = section_name
        chord_prop, chord_cand = _build_chords_proposal(st, chords_args)
        proposal_ids: list[str] = [chord_prop.id]
        summaries: dict[str, str] = {"chords": chord_prop.summary}

        # Chord proposal must be accepted before bass/melody can read it back
        # via _chords_by_beat_for_section, so if auto_accept is off, skip bass
        # and melody (they'd see no harmony).
        auto_accept = bool(args.get("auto_accept", True))
        if auto_accept:
            prop_mod.accept_proposal(chord_prop.id)

        if args.get("drums"):
            drum_args = dict(args["drums"])
            drum_args["section"] = section_name
            drum_prop = _build_drums_proposal(st, drum_args)
            proposal_ids.append(drum_prop.id)
            summaries["drums"] = drum_prop.summary
            if auto_accept:
                prop_mod.accept_proposal(drum_prop.id)

        if auto_accept and args.get("bass"):
            bass_args = dict(args["bass"])
            bass_args["section"] = section_name
            try:
                bass_prop = _build_bass_proposal(st, bass_args)
            except ValueError as e:
                summaries["bass_error"] = str(e)
            else:
                proposal_ids.append(bass_prop.id)
                summaries["bass"] = bass_prop.summary
                prop_mod.accept_proposal(bass_prop.id)

        if auto_accept and args.get("melody"):
            mel_args = dict(args["melody"])
            mel_args["section"] = section_name
            mel_prop = _build_melody_proposal(st, mel_args)
            proposal_ids.append(mel_prop.id)
            summaries["melody"] = mel_prop.summary
            prop_mod.accept_proposal(mel_prop.id)

        return _json({
            "section": section_name,
            "proposal_ids": proposal_ids,
            "summaries": summaries,
            "auto_accepted": auto_accept,
            "chord_symbols": chord_cand.chord_symbols,
        })

    if name == "humanize":
        tr = st.tracks.get(args["track_name"])
        if not tr:
            raise KeyError(args["track_name"])
        for clip in tr.clips:
            clip.notes = melody_mod.humanize(
                clip.notes,
                timing_jitter_ticks=float(args.get("timing_jitter_ticks", 8)),
                velocity_jitter=int(args.get("velocity_jitter", 8)),
                seed=args.get("seed"),
            )
        return _json({"ok": True, "track": tr.name, "clips": len(tr.clips)})

    if name == "list_proposals":
        return _json(prop_mod.list_proposals())

    if name == "diff_proposal":
        return _json(prop_mod.diff_proposal(args["proposal_id"]))

    if name == "accept_proposal":
        return _json(prop_mod.accept_proposal(args["proposal_id"]))

    if name == "reject_proposal":
        return _json(prop_mod.reject_proposal(args["proposal_id"]))

    if name == "bulk_accept_proposals":
        return _json(prop_mod.bulk_accept(args.get("proposal_ids")))

    if name == "bulk_reject_proposals":
        return _json(prop_mod.bulk_reject(args.get("proposal_ids")))

    if name == "explain":
        return _json({"text": _explain(args["proposal_id"])})

    if name == "set_explain_level":
        st.explain_level = args["level"]
        return _json({"ok": True, "explain_level": st.explain_level})

    if name == "reaper_status":
        return _json(get_bridge().status())

    if name == "import_midi":
        return _json(
            edit_mod.import_midi(
                path=args["path"],
                track_name=args["track_name"],
                section=args["section"],
                as_proposal=bool(args.get("as_proposal", False)),
                role=args.get("role"),
                clip_index=int(args.get("clip_index", 0)),
            )
        )

    if name == "edit_note":
        return _json(
            edit_mod.edit_note(
                track_name=args["track_name"],
                section=args["section"],
                note_index=int(args["note_index"]),
                pitch=args.get("pitch"),
                start_beat=args.get("start_beat"),
                duration_beats=args.get("duration_beats"),
                velocity=args.get("velocity"),
                lyric=args.get("lyric"),
                clip_index=int(args.get("clip_index", 0)),
            )
        )

    if name == "add_note":
        return _json(
            edit_mod.add_note(
                track_name=args["track_name"],
                section=args["section"],
                pitch=int(args["pitch"]),
                start_beat=float(args["start_beat"]),
                duration_beats=float(args["duration_beats"]),
                velocity=int(args.get("velocity", 90)),
                lyric=args.get("lyric"),
                clip_index=int(args.get("clip_index", 0)),
            )
        )

    if name == "delete_note":
        return _json(
            edit_mod.delete_note(
                track_name=args["track_name"],
                section=args["section"],
                note_index=int(args["note_index"]),
                clip_index=int(args.get("clip_index", 0)),
            )
        )

    return _json({"error": f"unknown tool: {name}"})


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def _run() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    import asyncio
    asyncio.run(_run())


if __name__ == "__main__":
    main()
