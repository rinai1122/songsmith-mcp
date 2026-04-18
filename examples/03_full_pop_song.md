# Example 3 — blank → finished pop song

One chat thread. End state: an A-minor ballad with verse/chorus/verse/
chorus/bridge/chorus, all four core tracks (chords, bass, drums, melody),
lyrics on the melody track.

## Script the LLM follows

```python
new_song(key="A minor", tempo=92, style_hint="ballad", explain_level="tutor")

# Form
set_form(sections=[
    {"name": "intro", "bars": 4},
    {"name": "verse", "bars": 8},
    {"name": "chorus", "bars": 8},
    {"name": "verse", "bars": 8},   # becomes "verse.2"
    {"name": "chorus", "bars": 8},  # becomes "chorus.2"
    {"name": "bridge", "bars": 8},
    {"name": "chorus", "bars": 8},  # becomes "chorus.3"
    {"name": "outro", "bars": 4},
])

# Verse: chords, lyrics+melody, bass, drums (light).
write_chords(section="verse", roman_numerals=["i","VI","iv","V"], bars_per_chord=2); accept
propose_melody(section="verse", lyrics="tell me what you know about love and rain",
               contour="arch", seed=1); accept
write_bassline(section="verse", style="root_fifth"); accept
write_drum_pattern(section="verse", style="ballad", intensity="light"); accept

# Chorus: brighter IV-based progression, fuller drums.
write_chords(section="chorus", roman_numerals=["VI","III","VII","i"], bars_per_chord=2); accept
propose_melody(section="chorus", lyrics="carry me home, carry me home to you",
               contour="arch", range_hi=79); accept
write_bassline(section="chorus", style="roots"); accept
write_drum_pattern(section="chorus", style="pop", intensity="normal"); accept

# Copy verse/chorus to verse.2/chorus.2 by re-running the same writes with those sections.
# Bridge: key change feel via bVII - IV - i.
write_chords(section="bridge", roman_numerals=["VII","III","iv","V"], bars_per_chord=2); accept
propose_melody(section="bridge", contour="descending", range_hi=74); accept
write_drum_pattern(section="bridge", style="halftime", intensity="normal"); accept

# Final chorus: heavy drums, re-voice chords for drop2 intensity.
write_chords(section="chorus.3", roman_numerals=["VI","III","VII","i"], bars_per_chord=2); accept
revoice(section="chorus.3", style="drop2")
write_bassline(section="chorus.3", style="root_fifth"); accept
write_drum_pattern(section="chorus.3", style="pop", intensity="heavy"); accept

# Outro: fade on i chord.
write_chords(section="outro", roman_numerals=["i","iv","i","i"], bars_per_chord=1); accept
```

## What the user sees

- Region markers at every section boundary.
- A `_proposals` folder in REAPER briefly, then real tracks once each
  proposal is accepted.
- `explain(proposal_id)` on any proposal returns a beginner-readable
  rationale — copy it into the track's notes and it stays there.
