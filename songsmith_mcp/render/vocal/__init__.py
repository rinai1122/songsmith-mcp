"""Pluggable singing-voice synthesis backends for the melody/vocal role.

The default ``NumpyBackend`` in ``render/synth.py`` ships a saw-lead patch as
a stand-in for vocals. This package replaces that with real vowel-shaped
synthesis (formant filter driven by syllable lyrics) by default, and lets
users opt into a true neural SVS engine (NNSVS, or an arbitrary external
renderer) when they have one installed and a voice bank configured.

Selection happens in ``select.select_vocal_backend()`` based on the
``SONGSMITH_VOCAL_BACKEND`` env var.
"""

from .base import VocalBackend, VocalRequest
from .select import select_vocal_backend

__all__ = ["VocalBackend", "VocalRequest", "select_vocal_backend"]
