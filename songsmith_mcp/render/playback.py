"""Cross-platform "open this file in the default app" shim.

Used by the ``play_song`` and ``view_score`` MCP tools to pop an audio or
score file into the OS default handler (Windows Media Player, Quick Look,
xdg-open, MuseScore — whatever's registered).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def open_with_default_app(path: Path) -> bool:
    """Return True if we launched something, False if we gave up.

    On Windows we use ``os.startfile`` which goes through the shell's default
    verb. On macOS ``open`` and on Linux ``xdg-open`` are used.
    """
    path = Path(path)
    if not path.exists():
        return False
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
            return True
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        if shutil.which(opener) is None:
            return False
        subprocess.Popen([opener, str(path)])
        return True
    except OSError:
        return False
