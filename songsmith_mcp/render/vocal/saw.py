"""Legacy saw-lead vocal "backend" — preserves the pre-vocal-pipeline output.

Kept so users who explicitly set ``SONGSMITH_VOCAL_BACKEND=saw`` (or who
script against the prior synth output) get bit-identical audio.
"""

from __future__ import annotations

import numpy as np

from ..synth import _melody_voice  # type: ignore[attr-defined]
from .base import VocalBackend, VocalRequest


class SawBackend:
    """Wraps ``synth._melody_voice`` so the old saw lead is selectable."""

    name = "saw"

    def is_available(self) -> tuple[bool, str]:
        return True, "legacy saw lead (pre-vocal-pipeline behavior)"

    def render(self, request: VocalRequest) -> np.ndarray:
        sr = request.sample_rate
        total = int(request.duration_s * sr) + sr
        buf = np.zeros(total, dtype=np.float32)
        for note in request.notes:
            sample = _melody_voice(note)
            if sample.size == 0:
                continue
            start = int(note.start_s * sr)
            end = start + sample.size
            if end > buf.size:
                sample = sample[: buf.size - start]
                end = buf.size
            buf[start:end] += sample
        return buf
