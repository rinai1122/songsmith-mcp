"""Regression tests for the timeout/blocking bug surfaced during the
2026-04-22 vocaloid-style session:

1. ``build_song(render_audio=true)`` used to run the synth inline, so a
   10-section song tripped the MCP client's 4-minute tool-call timeout and
   the caller lost the entire build result. The fix: always persist the
   build summary to ``out_dir/last_build.json`` first, then kick the synth
   to a daemon thread and return immediately with a sidecar pointer.

2. The inline render also blocked the asyncio event loop, so any tool call
   arriving during the render (even a trivial ``observe``) queued behind
   it. The fix: ``_call_tool`` now dispatches via ``asyncio.to_thread`` so
   the loop stays responsive.

These tests exercise both behaviors end-to-end through ``_dispatch`` (for
the sync parts) and through the real MCP stdio transport (for the
loop-responsiveness part), so they catch regressions at the user-facing
layer.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

from songsmith_mcp.server import _dispatch
from songsmith_mcp.state import reset_state


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("SONGSMITH_OUT", str(tmp_path))
    import songsmith_mcp.reaper_bridge as rb

    rb._BRIDGE = None
    reset_state()
    yield tmp_path


def _call(name: str, **args):
    out = _dispatch(name, args)
    payload = json.loads(out[0].text)
    assert "error" not in payload, f"{name} errored: {payload}"
    return payload


def test_build_song_persists_last_build_before_render(tmp_path: Path):
    """build_song must write last_build.json to out_dir so the caller can
    recover the build summary even if the render crashes, hangs, or is
    abandoned client-side."""
    _call("new_song", key="C major", tempo=120)
    _call("set_form", sections=[{"name": "verse", "bars": 4}])
    result = _call(
        "build_song",
        sections=[{
            "section": "verse",
            "chords": {"roman_numerals": ["I", "IV", "V", "I"]},
        }],
        auto_accept=True,
        render_audio=False,  # off: fast, no background thread
    )
    sidecar = Path(result["last_build_sidecar"])
    assert sidecar.exists(), "last_build.json must be written"
    parsed = json.loads(sidecar.read_text())
    # Proposal ids etc. must be recoverable from disk without re-running
    # the build.
    assert parsed["sections"][0]["section"] == "verse"
    assert parsed["sections"][0]["proposal_ids"]
    assert parsed["total_proposals"] >= 1


def test_build_song_with_render_audio_returns_immediately(tmp_path: Path):
    """The whole point of the fix: build_song(render_audio=true) must NOT
    block on the synth. It should return within a second or two with a
    'rendering_in_background' marker, and the sidecar should eventually
    show the completed render."""
    _call("new_song", key="C major", tempo=120)
    _call("set_form", sections=[{"name": "verse", "bars": 4}])

    t0 = time.monotonic()
    result = _call(
        "build_song",
        sections=[{
            "section": "verse",
            "chords": {"roman_numerals": ["I", "IV", "V", "I"]},
            "drums": {"style": "pop"},
            "bass": {"style": "roots"},
        }],
        auto_accept=True,
        render_audio=True,
    )
    elapsed = time.monotonic() - t0
    # Generous ceiling: even on a cold machine the return is bounded by
    # proposal-building, not by the synth. Four seconds is comfortable.
    assert elapsed < 4.0, f"build_song blocked for {elapsed:.1f}s — render is not backgrounded"

    audio = result["audio"]
    assert audio["status"] == "rendering_in_background"
    sidecar_path = Path(audio["result_sidecar"])
    assert sidecar_path.exists(), "sidecar must be written synchronously"

    # Wait up to 20s for the daemon thread to finalize. The tiny 4-chord
    # song renders in well under a second, but give headroom for slow CI.
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        parsed = json.loads(sidecar_path.read_text())
        if parsed.get("status") in {"done", "error"}:
            break
        time.sleep(0.05)
    else:
        pytest.fail("background render never finished within 20s")

    assert parsed["status"] == "done", f"render errored: {parsed}"
    assert parsed["ok"] is True
    assert Path(parsed["wav"]).exists(), "song.wav must land on disk"


def test_render_song_default_is_background(tmp_path: Path):
    """render_song defaults to background=true so big songs don't trip
    MCP timeouts. Test that the default response is a sidecar pointer."""
    _call("new_song", key="C major", tempo=120)
    _call("set_form", sections=[{"name": "verse", "bars": 4}])
    _call(
        "build_song",
        sections=[{
            "section": "verse",
            "chords": {"roman_numerals": ["I", "IV", "V", "I"]},
        }],
        auto_accept=True,
    )
    result = _call("render_song")  # no background arg — default should kick in
    assert result["status"] == "rendering_in_background"
    assert Path(result["result_sidecar"]).exists()


def test_render_song_sync_mode_still_works(tmp_path: Path):
    """For tests and short renders, background=false keeps the old
    synchronous contract (returns the full render result, not a sidecar
    pointer)."""
    _call("new_song", key="C major", tempo=120)
    _call("set_form", sections=[{"name": "verse", "bars": 4}])
    _call(
        "build_song",
        sections=[{
            "section": "verse",
            "chords": {"roman_numerals": ["I", "IV", "V", "I"]},
        }],
        auto_accept=True,
    )
    result = _call("render_song", background=False, emit_mp3=False)
    assert result["ok"] is True
    assert "wav" in result and Path(result["wav"]).exists()


@pytest.mark.asyncio
async def test_observe_stays_responsive_during_background_render(tmp_path: Path):
    """The original bug: a long render blocked every subsequent tool call,
    including trivial ones like observe. With dispatch offloaded to a
    worker thread AND the render running on a daemon thread, observe
    must return quickly even while a render is in flight."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "songsmith_mcp.server"],
        env={"SONGSMITH_OUT": str(tmp_path), "PYTHONPATH": "."},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            await client.call_tool("new_song", {"key": "C major", "tempo": 120})
            await client.call_tool("set_form", {"sections": [
                {"name": "verse", "bars": 4},
                {"name": "chorus", "bars": 4},
            ]})
            await client.call_tool("build_song", {
                "sections": [
                    {
                        "section": "verse",
                        "chords": {"roman_numerals": ["I", "IV", "V", "I"]},
                        "drums": {"style": "pop"},
                        "bass": {"style": "roots"},
                    },
                    {
                        "section": "chorus",
                        "chords": {"roman_numerals": ["vi", "IV", "I", "V"]},
                        "drums": {"style": "pop", "intensity": "heavy"},
                        "bass": {"style": "root_fifth"},
                    },
                ],
                "auto_accept": True,
                "render_audio": True,  # kicks off a background render
            })
            # observe should return well under a second — it's a dict
            # summary, not a synth render. If the loop were still blocked
            # (regression), this would hang for the render's full duration.
            t0 = time.monotonic()
            result = await asyncio.wait_for(
                client.call_tool("observe", {}),
                timeout=5.0,
            )
            elapsed = time.monotonic() - t0
            assert elapsed < 5.0, f"observe took {elapsed:.1f}s — dispatch is blocking"
            text = result.content[0].text
            assert "\"key\": \"C major\"" in text
