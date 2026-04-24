"""Pure-numpy additive synth.

Zero binary deps. Each track role gets a distinct timbre so the mix is
intelligible without any VST/SoundFont. Quality is "chiptune demo" — good
enough to tell whether a melody works, not good enough to ship.

The ``RenderBackend`` protocol exists so FluidSynth / OpenUtau / DiffSinger
can plug in later: a new backend just needs to implement
``render_stem(stem, sample_rate, duration_s) -> np.ndarray``.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np

from .merge import AudioNote, Stem

SAMPLE_RATE = 44100


class RenderBackend(Protocol):
    def render_stem(self, stem: Stem, duration_s: float) -> np.ndarray: ...


# ---------------------------------------------------------------------------
# Oscillators & envelopes
# ---------------------------------------------------------------------------

def _midi_to_hz(pitch: int) -> float:
    return 440.0 * (2.0 ** ((pitch - 69) / 12.0))


def _saw(freq: float, n_samples: int, sr: int = SAMPLE_RATE) -> np.ndarray:
    # Band-limited saw via naive aliasing — fine for our purposes at 44.1kHz.
    t = np.arange(n_samples) / sr
    return 2.0 * (t * freq - np.floor(0.5 + t * freq))


def _square(freq: float, n_samples: int, sr: int = SAMPLE_RATE) -> np.ndarray:
    t = np.arange(n_samples) / sr
    return np.sign(np.sin(2 * np.pi * freq * t))


def _triangle(freq: float, n_samples: int, sr: int = SAMPLE_RATE) -> np.ndarray:
    t = np.arange(n_samples) / sr
    return 2.0 * np.abs(2.0 * (t * freq - np.floor(t * freq + 0.5))) - 1.0


def _sine(freq: float, n_samples: int, sr: int = SAMPLE_RATE) -> np.ndarray:
    t = np.arange(n_samples) / sr
    return np.sin(2 * np.pi * freq * t)


def _adsr(n_samples: int, a: float, d: float, s: float, r: float, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Standard linear ADSR. ``s`` is the sustain level (0–1); a/d/r are seconds."""
    env = np.zeros(n_samples, dtype=np.float32)
    a_n = max(1, int(a * sr))
    d_n = max(1, int(d * sr))
    r_n = max(1, int(r * sr))
    sustain_n = max(0, n_samples - a_n - d_n - r_n)
    i = 0
    # attack
    seg = min(a_n, n_samples - i)
    env[i:i + seg] = np.linspace(0.0, 1.0, seg, dtype=np.float32)
    i += seg
    # decay
    seg = min(d_n, n_samples - i)
    env[i:i + seg] = np.linspace(1.0, s, seg, dtype=np.float32)
    i += seg
    # sustain
    seg = min(sustain_n, n_samples - i)
    env[i:i + seg] = s
    i += seg
    # release
    seg = n_samples - i
    if seg > 0:
        env[i:i + seg] = np.linspace(s, 0.0, seg, dtype=np.float32)
    return env


# ---------------------------------------------------------------------------
# Per-role voice patches
# ---------------------------------------------------------------------------

def _melody_voice(note: AudioNote) -> np.ndarray:
    """Bright saw lead — stand-in for future vocal synthesis."""
    n = int(note.duration_s * SAMPLE_RATE)
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    f = _midi_to_hz(note.pitch)
    wave = 0.55 * _saw(f, n) + 0.25 * _square(f, n) + 0.20 * _sine(f * 2, n)
    env = _adsr(n, a=0.012, d=0.08, s=0.75, r=0.15)
    return (wave * env).astype(np.float32) * (note.velocity / 127.0)


def _chord_voice(note: AudioNote) -> np.ndarray:
    """Soft triangle pad — fat but non-distracting harmonic bed."""
    n = int(note.duration_s * SAMPLE_RATE)
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    f = _midi_to_hz(note.pitch)
    wave = 0.7 * _triangle(f, n) + 0.3 * _sine(f, n)
    env = _adsr(n, a=0.04, d=0.1, s=0.85, r=0.25)
    return (wave * env).astype(np.float32) * (note.velocity / 127.0) * 0.55


def _bass_voice(note: AudioNote) -> np.ndarray:
    """Square sub — punchy low end."""
    n = int(note.duration_s * SAMPLE_RATE)
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    f = _midi_to_hz(note.pitch)
    wave = 0.6 * _square(f, n) + 0.4 * _sine(f, n)
    env = _adsr(n, a=0.005, d=0.06, s=0.7, r=0.08)
    return (wave * env).astype(np.float32) * (note.velocity / 127.0) * 0.9


# Drum pitches follow GM drum map (channel 10).
_GM_KICK = {35, 36}
_GM_SNARE = {38, 40}
_GM_CLOSED_HAT = {42, 44}
_GM_OPEN_HAT = {46}
_GM_CRASH = {49, 57}
_GM_RIDE = {51, 59}
_GM_TOM = {41, 43, 45, 47, 48, 50}


def _drum_hit(pitch: int, velocity: int) -> np.ndarray:
    """One-shot drum sound built from sine + filtered noise."""
    v = velocity / 127.0
    if pitch in _GM_KICK:
        n = int(0.25 * SAMPLE_RATE)
        t = np.arange(n) / SAMPLE_RATE
        # Pitch sweep from 110 Hz to 45 Hz — classic kick thump.
        freq = 110.0 * np.exp(-t * 20.0) + 45.0
        phase = np.cumsum(2 * np.pi * freq / SAMPLE_RATE)
        env = np.exp(-t * 12.0)
        return (np.sin(phase) * env).astype(np.float32) * v * 1.2
    if pitch in _GM_SNARE:
        n = int(0.20 * SAMPLE_RATE)
        rng = np.random.default_rng(pitch)
        noise = rng.standard_normal(n).astype(np.float32)
        # Body tone ~180 Hz + noise burst.
        t = np.arange(n) / SAMPLE_RATE
        body = np.sin(2 * np.pi * 180.0 * t) * np.exp(-t * 25.0)
        env = np.exp(-t * 18.0)
        return ((0.6 * noise + 0.4 * body) * env).astype(np.float32) * v * 0.9
    if pitch in _GM_CLOSED_HAT:
        n = int(0.06 * SAMPLE_RATE)
        rng = np.random.default_rng(pitch + 1)
        noise = rng.standard_normal(n).astype(np.float32)
        t = np.arange(n) / SAMPLE_RATE
        env = np.exp(-t * 60.0)
        return (noise * env).astype(np.float32) * v * 0.35
    if pitch in _GM_OPEN_HAT:
        n = int(0.22 * SAMPLE_RATE)
        rng = np.random.default_rng(pitch + 2)
        noise = rng.standard_normal(n).astype(np.float32)
        t = np.arange(n) / SAMPLE_RATE
        env = np.exp(-t * 10.0)
        return (noise * env).astype(np.float32) * v * 0.4
    if pitch in _GM_CRASH:
        n = int(0.9 * SAMPLE_RATE)
        rng = np.random.default_rng(pitch + 3)
        noise = rng.standard_normal(n).astype(np.float32)
        t = np.arange(n) / SAMPLE_RATE
        env = np.exp(-t * 3.0)
        return (noise * env).astype(np.float32) * v * 0.5
    if pitch in _GM_RIDE:
        n = int(0.4 * SAMPLE_RATE)
        rng = np.random.default_rng(pitch + 4)
        noise = rng.standard_normal(n).astype(np.float32)
        t = np.arange(n) / SAMPLE_RATE
        body = np.sin(2 * np.pi * 3000.0 * t) * np.exp(-t * 5.0)
        env = np.exp(-t * 6.0)
        return ((0.5 * noise + 0.5 * body) * env).astype(np.float32) * v * 0.4
    if pitch in _GM_TOM:
        n = int(0.30 * SAMPLE_RATE)
        t = np.arange(n) / SAMPLE_RATE
        # Tune tom by pitch in the tom range.
        f0 = 80.0 + (pitch - 41) * 12.0
        env = np.exp(-t * 8.0)
        return (np.sin(2 * np.pi * f0 * t) * env).astype(np.float32) * v * 0.8
    # Unknown drum pitch — short click so it's still audible.
    n = int(0.05 * SAMPLE_RATE)
    rng = np.random.default_rng(pitch + 99)
    return (rng.standard_normal(n) * 0.3).astype(np.float32) * v


# ---------------------------------------------------------------------------
# Stem rendering
# ---------------------------------------------------------------------------

_VOICE_BY_ROLE = {
    "melody": _melody_voice,
    "vocal":  _melody_voice,
    "chords": _chord_voice,
    "pad":    _chord_voice,
    "bass":   _bass_voice,
}


def _render_pitched_stem(stem: Stem, duration_s: float) -> np.ndarray:
    voice = _VOICE_BY_ROLE.get(stem.role, _chord_voice)
    total = int(duration_s * SAMPLE_RATE) + SAMPLE_RATE  # 1s tail for releases
    buf = np.zeros(total, dtype=np.float32)
    for note in stem.notes:
        sample = voice(note)
        if sample.size == 0:
            continue
        start = int(note.start_s * SAMPLE_RATE)
        end = start + sample.size
        if end > buf.size:
            sample = sample[:buf.size - start]
            end = buf.size
        buf[start:end] += sample
    return buf


def _render_drum_stem(stem: Stem, duration_s: float) -> np.ndarray:
    total = int(duration_s * SAMPLE_RATE) + SAMPLE_RATE
    buf = np.zeros(total, dtype=np.float32)
    for note in stem.notes:
        sample = _drum_hit(note.pitch, note.velocity)
        start = int(note.start_s * SAMPLE_RATE)
        end = start + sample.size
        if end > buf.size:
            sample = sample[:buf.size - start]
            end = buf.size
        buf[start:end] += sample
    return buf


class NumpyBackend:
    """Default backend. Always available, no external deps."""

    def render_stem(self, stem: Stem, duration_s: float) -> np.ndarray:
        if stem.role == "drums":
            return _render_drum_stem(stem, duration_s)
        return _render_pitched_stem(stem, duration_s)


def default_backend() -> RenderBackend:
    return NumpyBackend()
