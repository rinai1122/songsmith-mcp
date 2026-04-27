"""External-process vocal backend — universal escape hatch.

Lets the user wire any singing-voice engine (DiffSinger, NEUTRINO, OpenUtau
once it ships a CLI, a custom HTTP service, …) without us taking a
dependency on it. The contract is:

1. We write a request JSON to a temp file with notes + lyrics + tempo + key.
2. We run ``$SONGSMITH_VOCAL_RENDER_CMD``, expanding ``{input}`` and
   ``{output}`` placeholders to the JSON path and the expected wav path.
3. We read the wav back and splice it into the mix.

A reference renderer that wraps NNSVS lives at ``examples/vocal_renderer.py``.

Example::

    export SONGSMITH_VOCAL_BACKEND=external
    export SONGSMITH_VOCAL_RENDER_CMD='python examples/vocal_renderer.py --in {input} --out {output}'

The renderer is responsible for: reading the JSON, synthesizing audio at the
requested sample rate, writing a mono WAV at ``{output}``. Anything printed
to stderr is forwarded for diagnostics.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import wave
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .base import VocalBackend, VocalRequest


_RENDER_TIMEOUT_S = 300  # 5 min — neural SVS on CPU is slow


def _request_to_json(request: VocalRequest) -> dict:
    return {
        "tempo": request.tempo,
        "key": request.key,
        "sample_rate": request.sample_rate,
        "duration_s": request.duration_s,
        "voice_id": request.voice_id,
        "notes": [asdict(n) for n in request.notes],
    }


def _read_wav_mono(path: Path, expected_sr: int) -> np.ndarray:
    """Read a 16-bit PCM mono WAV. Renderer is expected to honor expected_sr;
    if it doesn't we still load the audio but warn — the mix will be off-pitch."""
    with wave.open(str(path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        sr = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sampwidth != 2:
        raise RuntimeError(
            f"external renderer wrote {sampwidth*8}-bit wav; expected 16-bit PCM"
        )
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
    if n_channels == 2:
        pcm = pcm.reshape(-1, 2).mean(axis=1)
    elif n_channels != 1:
        raise RuntimeError(f"unexpected channel count {n_channels}")

    if sr != expected_sr:
        print(
            f"[songsmith] external renderer wrote {sr} Hz wav, expected {expected_sr} — "
            "mix will be at wrong speed/pitch",
            file=sys.stderr,
        )
    return pcm


class ExternalBackend:
    """Subprocess plug-in. Uses ``SONGSMITH_VOCAL_RENDER_CMD`` from env."""

    name = "external"

    def __init__(self) -> None:
        self._cmd_template = os.environ.get("SONGSMITH_VOCAL_RENDER_CMD", "").strip()
        self._voice_id = os.environ.get("SONGSMITH_VOCAL_VOICE_ID", "").strip() or None

    def is_available(self) -> tuple[bool, str]:
        if not self._cmd_template:
            return False, "set SONGSMITH_VOCAL_RENDER_CMD='your-renderer --in {input} --out {output}'"
        if "{input}" not in self._cmd_template or "{output}" not in self._cmd_template:
            return False, "command template must include both {input} and {output} placeholders"
        return True, f"external renderer: {self._cmd_template}"

    def render(self, request: VocalRequest) -> np.ndarray:
        ready, reason = self.is_available()
        if not ready:
            raise RuntimeError(f"external backend unavailable: {reason}")

        if request.voice_id is None and self._voice_id:
            request = VocalRequest(
                notes=request.notes,
                duration_s=request.duration_s,
                sample_rate=request.sample_rate,
                tempo=request.tempo,
                key=request.key,
                voice_id=self._voice_id,
            )

        with tempfile.TemporaryDirectory(prefix="songsmith_vocal_") as tmpdir:
            in_path = Path(tmpdir) / "request.json"
            out_path = Path(tmpdir) / "vocal.wav"
            in_path.write_text(json.dumps(_request_to_json(request), indent=2))

            cmd_str = self._cmd_template.replace("{input}", str(in_path)).replace(
                "{output}", str(out_path)
            )
            # ``shell=True`` is intentional: the user owns SONGSMITH_VOCAL_RENDER_CMD
            # in their own environment, so there's no untrusted-input risk, and
            # delegating quoting to the platform shell (cmd.exe on Windows,
            # /bin/sh elsewhere) avoids cross-platform shlex/backslash pitfalls.
            try:
                result = subprocess.run(
                    cmd_str,
                    shell=True,
                    capture_output=True,
                    timeout=_RENDER_TIMEOUT_S,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"external renderer timed out after {_RENDER_TIMEOUT_S}s") from exc

            if result.stderr:
                sys.stderr.write(result.stderr.decode(errors="replace"))
            if result.returncode != 0:
                raise RuntimeError(
                    f"external renderer exited {result.returncode} (cmd: {cmd_str})"
                )
            if not out_path.exists():
                raise RuntimeError(f"external renderer didn't write {out_path}")

            wav = _read_wav_mono(out_path, request.sample_rate)

        total = int(request.duration_s * request.sample_rate) + request.sample_rate
        out = np.zeros(total, dtype=np.float32)
        end = min(wav.size, total)
        out[:end] = wav[:end]
        return out
