"""Audio rendering pipeline: SongState → .wav / .mp3.

Three stages, each factored so future backends (FluidSynth, OpenUtau,
DiffSinger) slot in without touching callers:

1. ``merge`` — flatten every Clip across every Track into stems of
   ``(role, notes_with_absolute_timing)``.
2. ``synth`` — turn each stem into a mono float32 audio buffer. Default
   backend is a pure-numpy additive synth so the pipeline runs with zero
   binary deps; swap in FluidSynth by setting a different backend.
3. ``encode`` — mix stems down, write .wav, and (if ffmpeg is on PATH)
   transcode to .mp3.
"""

from .pipeline import render_song

__all__ = ["render_song"]
