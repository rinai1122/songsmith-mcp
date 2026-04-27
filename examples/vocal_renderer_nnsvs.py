"""Reference external vocal renderer that wraps NNSVS.

Wire it into songsmith with::

    export SONGSMITH_VOCAL_BACKEND=external
    export SONGSMITH_VOCAL_RENDER_CMD='python examples/vocal_renderer_nnsvs.py \
        --bank /path/to/nnsvs/release-dir \
        --in {input} --out {output}'

Then ``render_song`` (or ``build_song`` with ``render_audio=true``) will
shell out to this script for the melody/vocal stem, and you'll hear the
output of your NNSVS voice bank in the mix.

Why a separate script instead of an in-process backend? The in-process
``nnsvs`` backend (see ``songsmith_mcp/render/vocal/nnsvs_backend.py``) is
the no-fuss path when a single voice bank works for you. This script is the
escape hatch for the common cases the in-process path doesn't cover:

- Different voice banks per song (pass ``--bank`` per song)
- Custom phoneme mapping (English lyrics → bank-specific phonemes)
- A renderer running in a separate venv (NNSVS pulls heavy deps)
- Caching, GPU offload, retry-on-OOM, anything else you want to wrap

Input JSON schema (written by songsmith to ``{input}``)::

    {
      "tempo": 120.0,
      "key": "A minor",
      "sample_rate": 44100,
      "duration_s": 38.0,
      "voice_id": null,
      "notes": [
        {"pitch": 67, "start_s": 4.0, "duration_s": 0.5,
         "velocity": 100, "lyric": "la"},
        ...
      ]
    }

Output: a 16-bit mono WAV at ``{output}``, sample_rate matching the request.
"""

from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np


def _build_sinsy_xml(req: dict) -> str:
    """Build a minimal sinsy MusicXML from the request JSON. Copy of the
    in-process logic so this script has zero songsmith imports."""
    from xml.etree.ElementTree import Element, SubElement, tostring

    pitch_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    sec_per_beat = 60.0 / req["tempo"]
    divisions = 240

    def beats_to_div(beats: float) -> int:
        return max(1, int(round(beats * divisions)))

    def midi_to_step_octave(pitch: int) -> tuple[str, int, int]:
        name = pitch_names[pitch % 12]
        octave = (pitch // 12) - 1
        if "#" in name:
            return name[0], 1, octave
        return name, 0, octave

    score = Element("score-partwise", {"version": "3.1"})
    pl = SubElement(score, "part-list")
    sp = SubElement(pl, "score-part", {"id": "P1"})
    SubElement(sp, "part-name").text = "Vocal"
    part = SubElement(score, "part", {"id": "P1"})
    measure = SubElement(part, "measure", {"number": "1"})
    attrs = SubElement(measure, "attributes")
    SubElement(attrs, "divisions").text = str(divisions)

    cursor_s = 0.0
    for note in sorted(req["notes"], key=lambda n: n["start_s"]):
        if note["start_s"] > cursor_s + 1e-4:
            gap = (note["start_s"] - cursor_s) / sec_per_beat
            r = SubElement(measure, "note")
            SubElement(r, "rest")
            SubElement(r, "duration").text = str(beats_to_div(gap))
            SubElement(r, "voice").text = "1"
            cursor_s = note["start_s"]
        n_el = SubElement(measure, "note")
        p_el = SubElement(n_el, "pitch")
        step, alter, octave = midi_to_step_octave(note["pitch"])
        SubElement(p_el, "step").text = step
        if alter:
            SubElement(p_el, "alter").text = str(alter)
        SubElement(p_el, "octave").text = str(octave)
        SubElement(n_el, "duration").text = str(
            beats_to_div(note["duration_s"] / sec_per_beat)
        )
        SubElement(n_el, "voice").text = "1"
        if note.get("lyric"):
            ly = SubElement(n_el, "lyric")
            SubElement(ly, "syllabic").text = "single"
            SubElement(ly, "text").text = note["lyric"]
        cursor_s = note["start_s"] + note["duration_s"]

    return tostring(score, encoding="unicode")


def _resample_linear(wav: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    if src_sr == dst_sr:
        return wav.astype(np.float32, copy=False)
    src_len = wav.shape[0]
    dst_len = int(round(src_len * dst_sr / src_sr))
    src_x = np.linspace(0.0, 1.0, src_len, dtype=np.float64)
    dst_x = np.linspace(0.0, 1.0, dst_len, dtype=np.float64)
    return np.interp(dst_x, src_x, wav).astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser(description="NNSVS reference renderer")
    parser.add_argument("--bank", required=True, help="path to NNSVS release directory")
    parser.add_argument("--in", dest="in_path", required=True)
    parser.add_argument("--out", dest="out_path", required=True)
    parser.add_argument("--device", default="cpu", choices=("cpu", "cuda"))
    args = parser.parse_args()

    req = json.loads(Path(args.in_path).read_text())

    # Lazy imports — keep startup fast when NNSVS isn't needed.
    try:
        import pysinsy  # noqa: F401
        from nnsvs.svs import SPSVS
    except ImportError as exc:
        print(f"NNSVS not installed: {exc}\n  pip install nnsvs pysinsy", file=sys.stderr)
        sys.exit(2)

    score_xml = _build_sinsy_xml(req)
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-8") as f:
        f.write(score_xml)
        xml_path = f.name

    try:
        try:
            labels = pysinsy.extract_fullcontext(xml_path)
        except AttributeError:
            labels = pysinsy.extract_fullcontext_label(xml_path)

        engine = SPSVS(args.bank, device=args.device)
        wav, engine_sr = engine.svs(labels)
    finally:
        Path(xml_path).unlink(missing_ok=True)

    wav = np.asarray(wav, dtype=np.float32).reshape(-1)
    target_sr = int(req["sample_rate"])
    wav = _resample_linear(wav, int(engine_sr), target_sr)

    pcm = (np.clip(wav, -1.0, 1.0) * 32767.0).astype(np.int16)
    Path(args.out_path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(args.out_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(target_sr)
        wf.writeframes(pcm.tobytes())


if __name__ == "__main__":
    main()
