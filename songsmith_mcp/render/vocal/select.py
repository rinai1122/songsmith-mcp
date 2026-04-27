"""Vocal backend selection.

``SONGSMITH_VOCAL_BACKEND`` controls which engine renders melody/vocal stems:

- ``auto`` (default) — try ``nnsvs`` if configured, then ``external`` if
  configured, then fall back to ``formant``. Never fails.
- ``formant`` — vowel-shaped formant additive synth. Always available.
- ``nnsvs``   — real neural SVS via NNSVS + ``SONGSMITH_VOCAL_BANK``.
- ``external``— subprocess plug-in via ``SONGSMITH_VOCAL_RENDER_CMD``.
- ``saw``     — legacy saw lead, for parity with pre-vocal-pipeline output.

The non-``auto`` choices error loudly if their dependencies aren't met, so
mis-configured production setups don't silently degrade. ``auto`` is the
right default for development and casual use.
"""

from __future__ import annotations

import os
import sys

from .base import VocalBackend
from .external import ExternalBackend
from .formant import FormantBackend
from .nnsvs_backend import NNSVSBackend
from .saw import SawBackend


_NAME_TO_CLS: dict[str, type[VocalBackend]] = {
    "formant":  FormantBackend,
    "nnsvs":    NNSVSBackend,
    "external": ExternalBackend,
    "saw":      SawBackend,
}


def _try_backend(cls: type[VocalBackend]) -> VocalBackend | None:
    backend = cls()
    ready, _ = backend.is_available()
    return backend if ready else None


def select_vocal_backend(name: str | None = None) -> VocalBackend:
    """Resolve a ``VocalBackend`` instance.

    Resolution order:
    1. Explicit ``name`` argument (used by tests).
    2. ``SONGSMITH_VOCAL_BACKEND`` env var.
    3. ``"auto"``.
    """
    requested = (name or os.environ.get("SONGSMITH_VOCAL_BACKEND") or "auto").strip().lower()

    if requested == "auto":
        for cls in (NNSVSBackend, ExternalBackend):
            picked = _try_backend(cls)
            if picked is not None:
                return picked
        return FormantBackend()

    if requested not in _NAME_TO_CLS:
        valid = ", ".join(sorted(_NAME_TO_CLS) + ["auto"])
        raise ValueError(
            f"unknown SONGSMITH_VOCAL_BACKEND={requested!r}; expected one of {valid}"
        )

    backend = _NAME_TO_CLS[requested]()
    ready, reason = backend.is_available()
    if not ready:
        # Surface the reason so misconfig is obvious — don't silently downgrade
        # when the user explicitly asked for a specific engine.
        print(
            f"[songsmith] vocal backend {requested!r} not ready: {reason}",
            file=sys.stderr,
        )
    return backend
