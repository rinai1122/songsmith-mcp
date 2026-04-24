"""Stereo mixdown, .wav writing, and optional .mp3 transcode via ffmpeg."""

from __future__ import annotations

import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np


def mix_stems(stems: list[tuple[str, np.ndarray]]) -> np.ndarray:
    """Sum per-role buffers into a single mono float32 track and normalize.

    ``stems`` is a list of ``(role, samples)``. We apply per-role bus gains so
    drums don't drown the melody, then normalize to -1 dBFS so the output is
    loud without clipping.
    """
    bus_gain = {
        "melody": 1.15,
        "vocal":  1.15,
        "chords": 0.85,
        "pad":    0.80,
        "bass":   0.95,
        "drums":  0.90,
    }
    if not stems:
        return np.zeros(0, dtype=np.float32)

    max_len = max(buf.size for _, buf in stems)
    mix = np.zeros(max_len, dtype=np.float32)
    for role, buf in stems:
        gain = bus_gain.get(role, 0.85)
        mix[:buf.size] += buf * gain

    peak = float(np.max(np.abs(mix))) if mix.size else 0.0
    if peak > 0.0:
        mix = mix * (0.89 / peak)  # -1 dBFS headroom
    return mix


def write_wav(path: Path, samples: np.ndarray, sample_rate: int = 44100) -> Path:
    """Write a 16-bit mono WAV. ``samples`` is float32 in roughly [-1, 1]."""
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(samples, -1.0, 1.0)
    pcm = (clipped * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return path


def wav_to_mp3(wav_path: Path, mp3_path: Path | None = None) -> Path | None:
    """Transcode WAV→MP3 with ffmpeg. Returns None if ffmpeg isn't on PATH."""
    if shutil.which("ffmpeg") is None:
        return None
    mp3_path = mp3_path or wav_path.with_suffix(".mp3")
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", str(wav_path),
                "-codec:a", "libmp3lame", "-qscale:a", "2",
                str(mp3_path),
            ],
            check=True,
            timeout=60,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return mp3_path
