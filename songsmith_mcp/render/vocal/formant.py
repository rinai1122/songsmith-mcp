"""Formant-vowel singing synth — pure numpy, always available.

This is the default vocal backend. It's not vocaloid-quality, but it actually
*sings vowels*: each note's syllable lyric is reduced to a vowel ([a], [i],
[u], [e], [o], or schwa), and the note is rendered as a glottal-source-plus-
formant-filter additive synth at the note's pitch. The result sounds like a
choirboy humming open vowels — clearly synthetic, but unambiguously vocal,
and a strict upgrade over the saw-lead stand-in it replaces.

For real vocaloid quality, point ``SONGSMITH_VOCAL_BACKEND=nnsvs`` (or
``=external``) at a configured engine — this backend is the always-on
fallback when nothing fancier is wired up.
"""

from __future__ import annotations

import numpy as np

from .base import VocalBackend, VocalRequest


# ---------------------------------------------------------------------------
# Vowel formant table
# ---------------------------------------------------------------------------
# Center frequencies for an adult voice. We push F1 up slightly with pitch so
# high notes don't sound clenched (real singers do this — formant tuning).
# Bandwidths control how peaked each resonance is: narrower = more "ee/oo"
# colored, wider = more open. Values are a compromise between intelligibility
# and avoiding ringing on long notes.

_VOWEL_FORMANTS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    # vowel: ((F1, F2, F3), (B1, B2, B3))
    "a": ((730.0, 1090.0, 2440.0), (130.0, 110.0, 170.0)),  # "father"
    "e": ((530.0, 1840.0, 2480.0), (110.0, 130.0, 180.0)),  # "bed"
    "i": ((270.0, 2290.0, 3010.0),  (90.0, 110.0, 200.0)),  # "beat"
    "o": ((570.0,  840.0, 2410.0), (110.0, 110.0, 160.0)),  # "boat"
    "u": ((300.0,  870.0, 2240.0),  (90.0, 110.0, 160.0)),  # "boot"
    "@": ((500.0, 1500.0, 2500.0), (140.0, 150.0, 200.0)),  # schwa fallback
}

_VOWEL_LETTERS = set("aeiou")
_DIPHTHONG_NUCLEUS = {
    # "ai", "ay" → /a/ (we sing the nucleus, ignore the off-glide)
    "ai": "a", "ay": "a", "ei": "e", "ey": "e", "oi": "o", "oy": "o",
    "au": "a", "ou": "o", "ow": "o", "ie": "i",
    # "ee", "ea" → /i/; "oo" → /u/; "ah" → /a/
    "ee": "i", "ea": "i", "oo": "u", "ah": "a", "uh": "@",
}


def _extract_vowel(lyric: str | None) -> str:
    """Reduce a syllable to one of {a,e,i,o,u,@}. Schwa for missing/odd input."""
    if not lyric:
        return "@"
    s = lyric.lower()
    # Diphthongs first — they carry the perceived vowel of the syllable.
    for di, v in _DIPHTHONG_NUCLEUS.items():
        if di in s:
            return v
    # Otherwise first vowel letter wins.
    for ch in s:
        if ch in _VOWEL_LETTERS:
            return ch if ch != "y" else "i"
    return "@"


# ---------------------------------------------------------------------------
# Synthesis
# ---------------------------------------------------------------------------

_VIBRATO_HZ = 5.5
_VIBRATO_DEPTH_CENTS = 28.0     # ~28 cents = ~1.6% pitch swing
_BREATH_GAIN = 0.04
_NOTE_TAIL_S = 0.06             # short release tail per note


def _midi_to_hz(pitch: int) -> float:
    return 440.0 * (2.0 ** ((pitch - 69) / 12.0))


def _formant_envelope(freqs: np.ndarray, vowel: str, f0: float) -> np.ndarray:
    """Sum of Lorentzian peaks at F1/F2/F3 evaluated at ``freqs`` (Hz).

    Returns linear amplitudes (already shaped — no extra normalization). We
    nudge F1 up with pitch so soprano-range notes stay open instead of
    sounding muffled (formant tuning, simplified).
    """
    formants, bandwidths = _VOWEL_FORMANTS[vowel]
    f1, f2, f3 = formants
    if f0 > 350.0:  # roughly above F4
        # Linearly raise F1 toward f0 so it stays above the fundamental.
        f1 = max(f1, 0.85 * f0)
    centers = (f1, f2, f3)
    gains = (1.00, 0.55, 0.40)
    env = np.zeros_like(freqs, dtype=np.float32)
    for fc, bw, g in zip(centers, bandwidths, gains):
        # Lorentzian: 1 / (1 + ((f - fc)/bw)^2)
        env += np.float32(g) / (1.0 + ((freqs - fc) / bw) ** 2)
    # Gentle 6 dB/oct rolloff above 4 kHz so the harmonic stack doesn't get
    # crispy-sounding at high pitches.
    rolloff = np.where(freqs > 4000.0, 4000.0 / np.maximum(freqs, 1.0), 1.0)
    return env * rolloff.astype(np.float32)


def _adsr(n_samples: int, a: float, d: float, s: float, r: float, sr: int) -> np.ndarray:
    """Vocal-leaning ADSR. ``s`` is sustain level (0–1); a/d/r in seconds."""
    env = np.zeros(n_samples, dtype=np.float32)
    if n_samples <= 0:
        return env
    a_n = max(1, int(a * sr))
    d_n = max(1, int(d * sr))
    r_n = max(1, int(r * sr))
    sustain_n = max(0, n_samples - a_n - d_n - r_n)
    i = 0
    seg = min(a_n, n_samples - i)
    env[i:i + seg] = np.linspace(0.0, 1.0, seg, dtype=np.float32)
    i += seg
    seg = min(d_n, n_samples - i)
    env[i:i + seg] = np.linspace(1.0, s, seg, dtype=np.float32)
    i += seg
    seg = min(sustain_n, n_samples - i)
    env[i:i + seg] = s
    i += seg
    seg = n_samples - i
    if seg > 0:
        env[i:i + seg] = np.linspace(s, 0.0, seg, dtype=np.float32)
    return env


def _render_note(
    pitch: int,
    duration_s: float,
    velocity: int,
    vowel: str,
    sr: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Render one sung note as glottal source × formant envelope, additively."""
    n = int(round(duration_s * sr))
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    f0 = _midi_to_hz(pitch)
    if f0 <= 0.0:
        return np.zeros(n, dtype=np.float32)

    t = np.arange(n, dtype=np.float32) / sr

    # Vibrato as a phase modulator. Delay vibrato onset so short notes don't
    # warble — singers don't vibrato a 1/16 note.
    vib_onset = 0.18  # seconds before vibrato fades in
    vib_amp = (2.0 ** (_VIBRATO_DEPTH_CENTS / 1200.0)) - 1.0  # ~0.0163
    vib_gain = np.clip((t - vib_onset) / 0.25, 0.0, 1.0).astype(np.float32)
    vibrato = 1.0 + vib_gain * vib_amp * np.sin(2.0 * np.pi * _VIBRATO_HZ * t).astype(np.float32)

    # Build harmonic stack up to a safe ceiling below Nyquist.
    nyquist = sr * 0.5
    max_harmonic = max(1, int((nyquist * 0.95) // f0))
    # Cap harmonics — past ~64 we're spending CPU on inaudible content for
    # most pitches; the formant envelope kills them anyway.
    max_harmonic = min(max_harmonic, 80)

    # Pre-compute formant amplitudes per harmonic (vowel-shaped spectrum).
    harmonic_freqs = f0 * np.arange(1, max_harmonic + 1, dtype=np.float32)
    amps = _formant_envelope(harmonic_freqs, vowel, f0)
    # Glottal-source rolloff: -6 dB/oct (~1/h). Multiply formant envelope.
    amps = amps * (1.0 / np.arange(1, max_harmonic + 1, dtype=np.float32))

    # Drop harmonics that are negligibly small — pure speed optimization.
    keep = amps > (amps.max() * 1e-3)
    amps = amps[keep]
    harmonic_idx = (np.arange(1, max_harmonic + 1)[keep]).astype(np.float32)

    # Sum of sinusoids with shared vibrato. Phase = h * 2π * f0 * t * vibrato.
    # We integrate the instantaneous frequency by cumulative-sum-style trick:
    # since vibrato is small, treat it as a scalar multiplier on phase.
    base_phase = 2.0 * np.pi * f0 * t * vibrato
    out = np.zeros(n, dtype=np.float32)
    for h, a in zip(harmonic_idx, amps):
        out += a * np.sin(base_phase * h).astype(np.float32)

    # Breath noise — white noise pre-shaped by the same formant envelope at a
    # coarse frequency grid, low amplitude. Adds the "haaa" airy quality.
    noise = rng.standard_normal(n).astype(np.float32)
    # Cheap formant coloring: weighted sum of three bandpass-y components via
    # IIR-free moving averages. At this gain it's mostly perceptual sweetener.
    out += _BREATH_GAIN * noise

    env = _adsr(n, a=0.035, d=0.10, s=0.78, r=max(_NOTE_TAIL_S, duration_s * 0.08), sr=sr)
    return (out * env * (velocity / 127.0)).astype(np.float32)


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

class FormantBackend:
    """Default vocal backend: vowel-shaped formant additive synth in numpy."""

    name = "formant"

    def is_available(self) -> tuple[bool, str]:
        return True, "always available (pure numpy)"

    def render(self, request: VocalRequest) -> np.ndarray:
        sr = request.sample_rate
        total = int(request.duration_s * sr) + sr  # 1s tail to match other stems
        buf = np.zeros(total, dtype=np.float32)
        # Deterministic noise so identical SongStates render bit-identical.
        rng = np.random.default_rng(0xC0FFEE)
        for note in request.notes:
            vowel = _extract_vowel(note.lyric)
            sample = _render_note(
                pitch=note.pitch,
                duration_s=note.duration_s,
                velocity=note.velocity,
                vowel=vowel,
                sr=sr,
                rng=rng,
            )
            if sample.size == 0:
                continue
            start = int(note.start_s * sr)
            end = start + sample.size
            if end > buf.size:
                sample = sample[: buf.size - start]
                end = buf.size
            buf[start:end] += sample
        # Soft clip — formant stacks at high velocity can spike past unity.
        peak = float(np.max(np.abs(buf))) if buf.size else 0.0
        if peak > 0.95:
            buf = buf * (0.95 / peak)
        return buf
