"""Top-level orchestrator: SongState → song.wav (+ song.mp3 + per-role stems)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..state import SongState
from .encode import mix_stems, wav_to_mp3, write_wav
from .merge import flatten
from .synth import SAMPLE_RATE, RenderBackend, default_backend


def render_song(
    state: SongState,
    out_dir: Path,
    *,
    basename: str = "song",
    emit_stems: bool = True,
    emit_mp3: bool = True,
    backend: RenderBackend | None = None,
) -> dict[str, Any]:
    """Render the current SongState to ``{out_dir}/{basename}.wav`` (+ .mp3).

    Stems are written as ``{basename}__{role}.wav`` when ``emit_stems`` is true,
    so callers can swap individual tracks (e.g., replace ``__melody.wav`` with
    a real vocaloid render and re-mix) without touching the rest.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    backend = backend or default_backend()

    stems, duration_s = flatten(state)
    if not stems:
        return {
            "ok": False,
            "reason": "no clips in SongState — call build_song or propose_* first",
            "wav": None,
            "mp3": None,
        }

    role_buffers: dict[str, Any] = {}
    for stem in stems:
        buf = backend.render_stem(stem, duration_s)
        # Sum multiple tracks that share a role (e.g., two chord tracks) so
        # the mix bus gets one entry per role, not per track.
        if stem.role in role_buffers:
            existing = role_buffers[stem.role]
            if buf.size > existing.size:
                existing_padded = existing.copy()
                existing_padded.resize(buf.size, refcheck=False)
                role_buffers[stem.role] = existing_padded + buf
            else:
                buf_padded = buf.copy()
                buf_padded.resize(existing.size, refcheck=False)
                role_buffers[stem.role] = existing + buf_padded
        else:
            role_buffers[stem.role] = buf

    stem_paths: dict[str, str] = {}
    if emit_stems:
        for role, buf in role_buffers.items():
            p = out_dir / f"{basename}__{role}.wav"
            write_wav(p, buf, SAMPLE_RATE)
            stem_paths[role] = str(p)

    mix = mix_stems(list(role_buffers.items()))
    wav_path = out_dir / f"{basename}.wav"
    write_wav(wav_path, mix, SAMPLE_RATE)

    mp3_path: Path | None = None
    if emit_mp3:
        mp3_path = wav_to_mp3(wav_path)

    return {
        "ok": True,
        "wav": str(wav_path),
        "mp3": str(mp3_path) if mp3_path else None,
        "stems": stem_paths,
        "duration_s": round(duration_s, 3),
        "tracks_rendered": [s.track_name for s in stems],
        "backend": type(backend).__name__,
        "mp3_available": mp3_path is not None,
    }
