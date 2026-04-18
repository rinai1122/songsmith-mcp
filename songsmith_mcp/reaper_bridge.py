"""Thin wrapper around ``python-reapy`` with graceful offline degradation.

We want every Songsmith tool to succeed in one of two modes:

* **Online** — REAPER is running with ``python-reapy`` connected. Proposals
  land as real MIDI items in a ``_proposals`` folder track; accepts move them
  into real tracks; form changes materialize as region markers.
* **Offline** — no REAPER. Proposals still build symbolically in
  ``state.SongState`` and get written to MIDI files under ``./out/``.

The bridge hides this distinction so tool implementations don't have to care.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .state import Clip, Note, Section, SongState, Track, get_state

try:  # pragma: no cover — exercised only with REAPER running
    import reapy  # type: ignore
    _HAVE_REAPY = True
except Exception:
    reapy = None  # type: ignore
    _HAVE_REAPY = False

# Operator escape hatch: force offline mode even if reapy is installed + REAPER
# is running. Useful when the REAPER bridge hangs (observed intermittently on
# Windows) and we'd rather just get .mid files out.
_DISABLE_REAPER = os.environ.get("SONGSMITH_DISABLE_REAPER", "").strip().lower() in {"1", "true", "yes"}


class ReaperBridge:
    """Façade over python-reapy. Silently no-ops when not connected."""

    def __init__(self, out_dir: str | Path | None = None) -> None:
        raw_out = out_dir or os.environ.get("SONGSMITH_OUT", "./out")
        self.out_dir = _sanitize_out_dir(str(raw_out))
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._project = None
        self._connected = False
        if _HAVE_REAPY and not _DISABLE_REAPER:
            try:
                self._project = reapy.Project()  # type: ignore[union-attr]
                self._connected = True
            except Exception:
                self._connected = False

    # ----- status -------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    def status(self) -> dict[str, Any]:
        return {
            "reapy_installed": _HAVE_REAPY,
            "reaper_connected": self._connected,
            "reaper_disabled_by_env": _DISABLE_REAPER,
            "out_dir": str(self.out_dir.resolve()),
        }

    def purge_proposal_files(self) -> list[str]:
        """Delete any ``prop_*__*.mid`` preview files in out_dir.

        Called on ``new_song`` so stale previews from prior songs don't pollute
        the output directory and confuse the user about what's still pending.
        """
        removed: list[str] = []
        for p in self.out_dir.glob("prop_*__*.mid"):
            try:
                p.unlink()
                removed.append(p.name)
            except OSError:
                pass
        return removed

    # ----- project-level ops -------------------------------------------

    def set_tempo(self, bpm: float) -> None:
        if not self._connected:
            return
        self._project.bpm = bpm  # type: ignore[union-attr]

    def set_time_sig(self, num: int, den: int) -> None:
        if not self._connected:
            return
        # reapy exposes this via set_time_signature on the project time selection
        # or via a marker at bar 1; the simplest is through the master tempo env.
        try:
            reapy.reascript_api.SetTempoTimeSigMarker(  # type: ignore[union-attr]
                self._project.id, -1, 0.0, -1, -1, self._project.bpm, num, den, True
            )
        except Exception:
            self._go_offline()

    def set_region(self, start_bar: int, end_bar: int, name: str) -> None:
        if not self._connected:
            return
        try:
            start_t = self._bar_to_time(start_bar)
            end_t = self._bar_to_time(end_bar)
            reapy.reascript_api.AddProjectMarker2(  # type: ignore[union-attr]
                self._project.id, True, start_t, end_t, name, -1, 0
            )
        except Exception:
            self._go_offline()

    def _go_offline(self) -> None:
        """Called when a reapy call raises; stay offline for the rest of the
        session so we don't pay the cost (or hit a possible hang) again.

        .mid files still materialize via the offline path — we just stop
        poking REAPER from this process.
        """
        self._connected = False
        self._project = None

    # ----- track / clip ops --------------------------------------------

    def ensure_track(self, name: str, folder: str | None = None) -> Any:
        """Return (or create) a REAPER track by name. Offline: returns None."""
        if not self._connected:
            return None
        for tr in self._project.tracks:  # type: ignore[union-attr]
            if tr.name == name:
                return tr
        tr = self._project.add_track(name=name)  # type: ignore[union-attr]
        if folder:
            # We don't enforce folder parenting here — that's a follow-up.
            pass
        return tr

    def write_clip_midi(self, clip: Clip, state: SongState, proposal_id: str | None = None) -> Path:
        """Write a symbolic ``Clip`` to a standalone .mid file and return the path.

        This file can be imported into REAPER via Insert Media, or inserted as
        an item by ``insert_clip``. We always write the file so offline users
        still get something they can open in MuseScore / a DAW.
        """
        import mido

        mid = mido.MidiFile(type=0, ticks_per_beat=480)
        tr = mido.MidiTrack()
        mid.tracks.append(tr)

        tr.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(state.tempo), time=0))
        tr.append(mido.MetaMessage(
            "time_signature",
            numerator=state.time_sig[0],
            denominator=state.time_sig[1],
            time=0,
        ))
        if clip.chord_symbol:
            tr.append(mido.MetaMessage("marker", text=clip.chord_symbol, time=0))

        events: list[tuple[int, mido.Message | mido.MetaMessage]] = []
        for n in clip.notes:
            start_t = int(n.start_beat * 480)
            end_t = int((n.start_beat + n.duration_beats) * 480)
            events.append((start_t, mido.Message("note_on", note=n.pitch, velocity=n.velocity)))
            events.append((end_t, mido.Message("note_off", note=n.pitch, velocity=0)))
            if n.lyric:
                events.append((start_t, mido.MetaMessage("lyrics", text=n.lyric)))
        events.sort(key=lambda e: e[0])

        abs_t = 0
        for t, msg in events:
            msg.time = max(0, t - abs_t)
            abs_t = t
            tr.append(msg)
        tr.append(mido.MetaMessage("end_of_track", time=0))

        prefix = proposal_id or "draft"
        safe = clip.track.replace(" ", "_").replace("/", "_")
        p = self.out_dir / f"{prefix}__{safe}__{clip.section}.mid"
        mid.save(str(p))
        return p

    def insert_clip(self, clip: Clip, state: SongState, proposal_id: str | None = None) -> str:
        """Materialize ``clip`` into REAPER if connected; always write a .mid.

        Returns the path of the written MIDI file.
        """
        path = self.write_clip_midi(clip, state, proposal_id=proposal_id)

        if not self._connected:
            return str(path)

        # Online path: put the track inside a _proposals folder while pending,
        # then ``accept`` moves it to the real track.
        tr_name = clip.track if proposal_id is None else f"_prop:{proposal_id}:{clip.track}"
        try:
            tr = self.ensure_track(tr_name)
            start_t = self._bar_to_time(clip.start_bar)
            reapy.reascript_api.InsertMedia(str(path), 0)  # type: ignore[union-attr]
            # Best-effort: move the newly inserted item to the desired track & pos.
            if tr and self._project.items:  # type: ignore[union-attr]
                last = self._project.items[-1]  # type: ignore[union-attr]
                last.position = start_t
                last.track = tr  # type: ignore[attr-defined]
        except Exception:
            self._go_offline()
        return str(path)

    # ----- utils --------------------------------------------------------

    def _bar_to_time(self, bar: int) -> float:
        """Convert bar number (0-based) to seconds, using current tempo/TS."""
        st = get_state()
        beats_per_bar = st.time_sig[0] * (4 / st.time_sig[1])
        seconds_per_beat = 60.0 / st.tempo
        return bar * beats_per_bar * seconds_per_beat


def _sanitize_out_dir(raw: str) -> Path:
    r"""Catch a classic Claude Desktop config trap: SONGSMITH_OUT set to a
    Windows path in unescaped JSON. When the user writes
    ``"SONGSMITH_OUT": "C:\\Users\\me\\out"`` as ``"C:\Users\me\out"`` (no
    double backslashes), the JSON parser drops ``\U``/``\m``/``\o`` as
    invalid escapes and hands us ``"C:Usersmeout"`` — a single token with no
    path separators. That "directory" then gets created relative to CWD,
    with every tool silently writing files to a surprising location.

    Heuristic: if the string starts with a drive letter (``X:``) but has no
    subsequent ``/``/``\`` separator, it's almost certainly mangled. Fall
    back to ``./out`` and print a warning to stderr so the user sees it.
    """
    import re
    import sys

    if re.match(r"^[A-Za-z]:[^/\\]+$", raw):
        print(
            f"[songsmith] SONGSMITH_OUT={raw!r} looks mangled — "
            "did Claude Desktop config forget to escape backslashes? "
            "Falling back to ./out. Use forward slashes or '\\\\' in JSON.",
            file=sys.stderr,
        )
        return Path("./out")
    return Path(raw)


_BRIDGE: ReaperBridge | None = None


def get_bridge() -> ReaperBridge:
    global _BRIDGE
    if _BRIDGE is None:
        _BRIDGE = ReaperBridge()
    return _BRIDGE
