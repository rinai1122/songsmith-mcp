"""NNSVS singing-voice-synthesis backend (opt-in).

Requires the ``nnsvs`` and ``pysinsy`` packages plus a downloaded NNSVS
release directory (model checkpoints + config) pointed to by
``SONGSMITH_VOCAL_BANK``. NNSVS itself is a heavy dep (PyTorch + audio libs),
so we import it lazily inside ``render`` — having ``nnsvs`` installed costs
nothing at import time, and not having it just falls back to the formant
backend.

This backend is *real* singing voice synthesis: notes + lyrics → sinsy XML →
HTS full-context labels → NNSVS acoustic model → vocoder → audio. Quality
matches what NNSVS produces in standalone usage.

Voice-bank-specific phoneme mapping is the failure mode you'll hit first:
most public NNSVS banks expect Japanese romaji lyrics (``"a"``, ``"ka"``,
``"mi"`` …). English lyrics will sing as gibberish unless the bank was
trained with an English phoneme set. The fix is to either (a) author lyrics
in romaji, or (b) point ``SONGSMITH_VOCAL_BANK`` at an English-trained bank.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring

import numpy as np

from .base import VocalBackend, VocalRequest


_PITCH_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _midi_to_step_octave(pitch: int) -> tuple[str, int, int]:
    """Return (step, alter, octave) for MusicXML <pitch>."""
    name = _PITCH_NAMES[pitch % 12]
    octave = (pitch // 12) - 1
    if "#" in name:
        return name[0], 1, octave
    return name, 0, octave


def _build_sinsy_xml(request: VocalRequest) -> str:
    """Build a minimal sinsy-flavored MusicXML score from the request.

    Sinsy expects a single-part score with notes carrying both pitch and
    lyric text. Rests fill gaps so timing matches the requested ``start_s``.
    Divisions are set so we can express any duration cleanly in the
    quarter-note grid (240 = 16th-note resolution at quarter=4).
    """
    sec_per_beat = 60.0 / request.tempo
    divisions = 240  # 240 ticks per quarter — generous for fine durations

    def beats_to_div(beats: float) -> int:
        return max(1, int(round(beats * divisions)))

    score = Element(
        "score-partwise", {"version": "3.1"},
    )
    part_list = SubElement(score, "part-list")
    score_part = SubElement(part_list, "score-part", {"id": "P1"})
    SubElement(score_part, "part-name").text = "Vocal"

    part = SubElement(score, "part", {"id": "P1"})
    measure = SubElement(part, "measure", {"number": "1"})

    attrs = SubElement(measure, "attributes")
    SubElement(attrs, "divisions").text = str(divisions)

    cursor_s = 0.0
    for note in sorted(request.notes, key=lambda n: n.start_s):
        if note.start_s > cursor_s + 1e-4:
            gap_beats = (note.start_s - cursor_s) / sec_per_beat
            rest_el = SubElement(measure, "note")
            SubElement(rest_el, "rest")
            SubElement(rest_el, "duration").text = str(beats_to_div(gap_beats))
            SubElement(rest_el, "voice").text = "1"
            cursor_s = note.start_s

        note_el = SubElement(measure, "note")
        pitch_el = SubElement(note_el, "pitch")
        step, alter, octave = _midi_to_step_octave(note.pitch)
        SubElement(pitch_el, "step").text = step
        if alter:
            SubElement(pitch_el, "alter").text = str(alter)
        SubElement(pitch_el, "octave").text = str(octave)
        SubElement(note_el, "duration").text = str(beats_to_div(note.duration_s / sec_per_beat))
        SubElement(note_el, "voice").text = "1"
        if note.lyric:
            lyric_el = SubElement(note_el, "lyric")
            SubElement(lyric_el, "syllabic").text = "single"
            SubElement(lyric_el, "text").text = note.lyric
        cursor_s = note.start_s + note.duration_s

    return tostring(score, encoding="unicode")


def _resample_linear(wav: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    """Linear resampling — adequate for the speech-band content NNSVS emits.

    We avoid pulling in ``librosa``/``scipy`` just for resample; if the user
    cares about pristine sample-rate conversion they can post-process the
    rendered wav externally.
    """
    if src_sr == dst_sr:
        return wav.astype(np.float32, copy=False)
    src_len = wav.shape[0]
    dst_len = int(round(src_len * dst_sr / src_sr))
    if dst_len <= 0:
        return np.zeros(0, dtype=np.float32)
    src_x = np.linspace(0.0, 1.0, src_len, dtype=np.float64)
    dst_x = np.linspace(0.0, 1.0, dst_len, dtype=np.float64)
    return np.interp(dst_x, src_x, wav).astype(np.float32)


class NNSVSBackend:
    """Wraps NNSVS's ``SPSVS`` for in-process neural singing synthesis."""

    name = "nnsvs"

    def __init__(self) -> None:
        self._engine = None  # lazy
        self._engine_sr: int | None = None
        self._bank_path = os.environ.get("SONGSMITH_VOCAL_BANK", "").strip()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def is_available(self) -> tuple[bool, str]:
        if not self._bank_path:
            return False, "set SONGSMITH_VOCAL_BANK to an NNSVS release directory"
        if not Path(self._bank_path).exists():
            return False, f"voice bank not found at {self._bank_path}"
        try:
            import nnsvs  # noqa: F401
        except ImportError:
            return False, "install nnsvs: `pip install nnsvs pysinsy`"
        try:
            import pysinsy  # noqa: F401
        except ImportError:
            return False, "install pysinsy: `pip install pysinsy`"
        return True, f"nnsvs bank: {self._bank_path}"

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def _ensure_engine(self) -> None:
        if self._engine is not None:
            return
        from nnsvs.svs import SPSVS  # imported lazily — heavy dep
        self._engine = SPSVS(self._bank_path, device="cpu")
        # NNSVS engines expose ``sample_rate`` (or ``sr``) on the loaded config.
        sr = getattr(self._engine, "sample_rate", None) or getattr(self._engine, "sr", None)
        self._engine_sr = int(sr) if sr else 44100

    def render(self, request: VocalRequest) -> np.ndarray:
        ready, reason = self.is_available()
        if not ready:
            raise RuntimeError(f"NNSVS backend unavailable: {reason}")

        import pysinsy

        score_xml = _build_sinsy_xml(request)
        # pysinsy reads from disk in some versions; write to a temp path to be safe.
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-8") as f:
            f.write(score_xml)
            xml_path = f.name
        try:
            labels = pysinsy.extract_fullcontext(xml_path)
        except AttributeError:
            # Older API name
            labels = pysinsy.extract_fullcontext_label(xml_path)
        finally:
            try:
                os.unlink(xml_path)
            except OSError:
                pass

        self._ensure_engine()
        assert self._engine is not None and self._engine_sr is not None

        try:
            wav, _engine_sr = self._engine.svs(labels)
        except Exception as exc:  # noqa: BLE001 — engine failures are surfaced verbatim
            print(f"[songsmith] NNSVS render failed: {exc}", file=sys.stderr)
            raise

        wav = np.asarray(wav, dtype=np.float32).reshape(-1)
        wav = _resample_linear(wav, self._engine_sr, request.sample_rate)

        total = int(request.duration_s * request.sample_rate) + request.sample_rate
        out = np.zeros(total, dtype=np.float32)
        end = min(wav.size, total)
        out[:end] = wav[:end]
        return out
