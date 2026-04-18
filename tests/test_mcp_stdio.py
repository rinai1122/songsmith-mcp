"""Smoke-test the real MCP stdio transport: spawn the server, do the
handshake, list tools, call one. If this passes, the server is wireable
into Claude Desktop / Claude Code without surprises.
"""

from __future__ import annotations

import asyncio
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_server_lists_tools_and_runs_new_song(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "songsmith_mcp.server"],
        env={"SONGSMITH_OUT": str(tmp_path), "PYTHONPATH": "."},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = await client.list_tools()
            names = {t.name for t in tools.tools}
            expected = {
                "new_song", "observe", "suggest_form", "set_form",
                "propose_chord_progression", "write_chords", "revoice",
                "syllabify", "align_lyrics_to_rhythm",
                "propose_melody", "write_bassline", "write_drum_pattern",
                "humanize", "list_proposals", "diff_proposal",
                "accept_proposal", "reject_proposal", "explain",
                "set_explain_level", "reaper_status",
            }
            assert expected.issubset(names), f"missing: {expected - names}"

            result = await client.call_tool("new_song", {"key": "D major", "tempo": 110})
            # Content should be one text block with JSON inside.
            assert result.content
            text = result.content[0].text
            assert "\"key\": \"D major\"" in text
            assert "\"tempo\": 110" in text


if __name__ == "__main__":
    asyncio.run(test_server_lists_tools_and_runs_new_song(__file__))
