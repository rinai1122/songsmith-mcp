"""Vocal backend Protocol and request type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

from ..merge import AudioNote


@dataclass
class VocalRequest:
    """Everything a singing-voice engine needs to render one melody stem."""
    notes: list[AudioNote]   # absolute-time notes; ``lyric`` populated when set
    duration_s: float
    sample_rate: int
    tempo: float
    key: str                 # e.g. "A minor" — some engines key-sensitive
    voice_id: str | None = None  # backend-specific (NNSVS bank dir, OpenUtau singer, …)


class VocalBackend(Protocol):
    """Render one melody/vocal Stem to a mono float32 buffer.

    Backends MUST return audio padded to ``int(request.duration_s *
    sample_rate) + sample_rate`` samples (one-second tail) so the mix bus can
    sum it alongside other role buffers without size juggling.
    """

    name: str

    def is_available(self) -> tuple[bool, str]:
        """``(ready, reason)``. ``reason`` is shown to the user when not ready."""
        ...

    def render(self, request: VocalRequest) -> np.ndarray:
        ...
