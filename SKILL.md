---
name: manim-video-explainer
description: Render high-definition 1080p60 technical explainer videos, step-by-step system walkthroughs, and multi-act architecture animations with Manim Community (LaTeX-free), port-anchored orthogonal schematic connectors, optional beat-synchronized neural TTS voiceover (offline Piper, OpenAI-compatible /v1/audio/speech HTTP TTS, or CLI TTS) with EBU R128 loudness normalization and WebVTT captions, and an optional standalone HTML5 chapter player. Use when the user asks for an animated video walkthrough, Manim architecture/process animation, narrated video tutorial, or step-by-step visual explainer.
---

# Manim 1080p60 Technical Video Explainer & Synchronized Voiceover System

## Overview and Bundled Resources

Author crisp, deterministic `1920x1080 @ 60fps` technical explainer videos and architecture walkthroughs in pure Python using Manim Community without requiring `pdflatex` or `MacTeX`.
Pair animations optionally with sample-accurate neural voiceover (offline Piper ONNX, any OpenAI-compatible `/v1/audio/speech` HTTP endpoint, or custom CLI TTS), EBU R128 loudness normalization, synchronized `.vtt` WebVTT subtitles, and a zero-CDN offline HTML5 chapter player.

- `assets/nord_manim_theme.py`: Nord Polar/Frost/Aurora palette tokens, safe-frame layout constants, `BeatTimer` synchronization helper, LaTeX-free primitives (`make_header_banner`, `make_node_card`, `make_badge`, `animate_blocked_pulse`, `safe_fit_text`), and strict port-anchored orthogonal schematic connectors (`connect_h`, `connect_v`, `connect_elbow`, `animate_packet_along`, `assert_all_orthogonal`).
- `assets/video_player_template.html`: Standalone, zero-CDN Nord dark/light HTML5 Cinema Player with chapter tabs, clickable beat markers, playback rate (`0.75x..2.0x`), and optional bilingual audio/caption switching.
- `check_scene_bounds.py`: Pre-render layout verifier that checks Manim scenes or JSON checkpoints for `[-6.8, 6.8] x [-3.75, 3.75]` frame overflow and sibling text-label collisions.
- `mux_narration.py`: Stdlib Python CLI that synthesizes per-beat WAVs via Piper, an OpenAI-compatible HTTP `/v1/audio/speech` endpoint, or a custom TTS command, emits `beat_timings.json`, assembles a sample-accurate master WAV track, clone-pads trailing video frames (`tpad=stop_mode=clone`) so speech never clips, optionally normalizes loudness (`--loudnorm-lufs -18.0`), muxes H.264 + AAC (`+faststart`), and writes `.vtt` captions.
- `lint.py`: Output bundle verifier checking `ffprobe` H.264 `yuv420p` + audio streams, WebVTT header/cue monotonicity, and offline HTML5 player CSP/zero-CDN/zero-emoji invariants.
- `references/manim-layout-and-choreography.md`: Read when designing multi-node topologies, orthogonal bus/elbow routing, dynamic counters, bidirectional arrows, or multi-scene `ffmpeg` concatenation.
- `references/voiceover-and-commentary.md`: Read when authoring narration manifests, natural narrator prosody instructions, phonetic pronunciation glossaries, EBU R128 normalization, or bilingual voiceover tracks.

---

## 1. Storyboard & Multi-Act Narrative Design

Before writing Manim code, structure the explanation into discrete acts and a beat-by-beat storyboard table.
Keep one `Scene` class per Act so edits to Act 2 never require re-rendering Act 1 or Act 3:

- **Act 1 — Setup & Request Admission**: Introduce the topology, invariants, and entry guard (e.g., `Client -> API Gateway -> Token-Bucket Rate Limiter`).
- **Act 2 — Steady-State Execution**: Trace the primary data path and state transitions (e.g., `Read-Through Cache` single-flight lookup -> `Primary DB` hydration -> TTL writeback).
- **Act 3 — Failure Modes & Recovery**: Demonstrate what happens when limits trip or nodes fail (e.g., `HTTP 429` burst rejection or `Distributed Lock & Write-Ahead Log Replication` quorum promotion).

Map every beat explicitly before coding:

| `beat_id` | `visual_action` | `min_visual_sec` | `commentary_script` (if narrated) |
| :--- | :--- | :--- | :--- |
| `act1_ingress` | Render 4 Nord topology cards + header; animate request packet into Gateway | `3.2` | Inbound writes pass an edge gateway before touching stateful storage. |
| `act1_bucket` | Decrement token counter `100 -> 99`; flash green `ADMITTED` badge | `3.8` | An atomic token-bucket check absorbs traffic bursts while bounding downstream concurrency. |
| `act1_reject` | Trigger `animate_blocked_pulse` (`429 RATE LIMITED`) when tokens hit `0` | `3.5` | Once burst capacity drains, excess callers fail fast with HTTP 429 instead of queuing infinitely. |

---

## 2. LaTeX-Free Manim Community Authoring

### Isolated Environment Setup

Provision Manim (and optional `piper-tts`) inside an isolated virtual environment; verify `ffmpeg` and `ffprobe` are on `PATH`:

```bash
uv venv venv
uv pip install --python venv/bin/python manim piper-tts
```

### Strict LaTeX-Free & Orthogonal Schematic Rules

1. **Zero TeX Subprocesses**: Never use `Tex`, `MathTex`, `Title`, or `DecimalNumber` (which invoke `pdflatex`/`dvisvgm`). Use `Text`, `MarkupText` (`<b>`, `<i>`, `<sub>`, `<sup>`, `<span foreground="...">`), `Code`, `RoundedRectangle`, `Arrow`, `DashedVMobject`, and `MoveAlongPath`.
2. **Golden Schematic Rule (Port-Anchored Orthogonal Wires)**: **NEVER call `Arrow(card_a, card_b)` directly on two `Mobject`s** when building architecture block diagrams—Manim aims rays between bounding-box centers and produces crooked diagonal arrows whenever card widths or Y-positions differ. Route every edge via `connect_h(card_from, card_to, y=...)`, `connect_v(card_from, card_to, x=...)`, or `connect_elbow(points)`, animate packets with `animate_packet_along(self, edge, ...)`, and validate wire geometry with `assert_all_orthogonal(connectors, cards)`.
3. **Safe-Frame Coordinate Grid**: Manim's 16:9 canvas spans `[-7.11, 7.11] x [-4.0, 4.0]`. Keep all authored content inside `[-6.6, 6.6] x [-3.5, 3.5]`, and keep `y < -3.05` empty whenever WebVTT subtitles are displayed.
4. **Import `assets/nord_manim_theme.py`**: Set `self.camera.background_color = BG_DARK` and construct cards, orthogonal connectors, packet animations, and beat holds consistently:

```python
from pathlib import Path
import sys

SKILL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL_DIR))
sys.path.insert(0, str(SKILL_DIR / "assets"))

from manim import Create, FadeIn, Scene, VGroup
from check_scene_bounds import dump_checkpoints
from nord_manim_theme import (
    ACCENT_BLUE, ACCENT_CYAN, BG_DARK, STATUS_ERR, STATUS_OK,
    BeatTimer, animate_blocked_pulse, animate_packet_along,
    assert_all_orthogonal, connect_h, make_header_banner, make_node_card,
)


class Act1RateLimiter(Scene):
    def construct(self) -> None:
        self.camera.background_color = BG_DARK
        timer = BeatTimer("tmp/beat_timings.json")
        checkpoints: list[dict] = []

        header = make_header_banner(
            "Act 1 / Request Admission",
            "API Gateway & Token-Bucket Rate Limiter",
        )
        gw = make_node_card("API Gateway", "TLS + Route Match", width=2.5, accent=ACCENT_CYAN).move_to([-4.5, 0.2, 0])
        limiter = make_node_card("Rate Limiter", "Bucket: 100 tok", width=2.5, accent=ACCENT_BLUE).move_to([-1.5, 0.2, 0])
        cache = make_node_card("Read Cache", "Single-Flight TTL", width=2.5, accent=ACCENT_CYAN).move_to([1.5, 0.2, 0])
        db = make_node_card("Primary DB", "WAL Quorum", width=2.5, accent=STATUS_OK).move_to([4.5, 0.2, 0])

        e_gw_lim = connect_h(gw, limiter)
        e_lim_cache = connect_h(limiter, cache)
        e_cache_db = connect_h(cache, db, dashed=True)
        assert_all_orthogonal([e_gw_lim, e_lim_cache, e_cache_db], [gw, limiter, cache, db])
        edges = VGroup(e_gw_lim, e_lim_cache, e_cache_db)

        self.play(FadeIn(header), FadeIn(gw), FadeIn(limiter), FadeIn(cache), FadeIn(db), Create(edges), run_time=1.1)
        spent = animate_packet_along(self, e_gw_lim, label="POST /orders", run_time=0.7)
        timer.hold(self, "act1_ingress", consumed_anim_s=1.1 + spent, fallback_s=3.2, checkpoints=checkpoints)

        spent = animate_packet_along(self, e_lim_cache, label="token=99", color=STATUS_OK, run_time=0.7)
        timer.hold(self, "act1_bucket", consumed_anim_s=spent, fallback_s=3.8, checkpoints=checkpoints)

        spent = animate_blocked_pulse(self, limiter, reason_label="429 RATE LIMITED", color=STATUS_ERR, run_time=0.95)
        timer.hold(self, "act1_reject", consumed_anim_s=spent, fallback_s=3.5, checkpoints=checkpoints)

        dump_checkpoints(checkpoints, "tmp/act1_bounds.json")
```

### Verify Bounds & Zero Collisions Before 1080p60 Render

Always verify layout geometry in seconds before launching a full `1080p60` render:

```bash
# Option A: Live dry-run hook over every Scene.wait() call (< 3s, zero MP4 encoding)
./venv/bin/python .agents/skills/manim-video-explainer/check_scene_bounds.py \
  --module scenes.py --scene Act1RateLimiter

# Option B: Inspect emitted checkpoint JSON after a fast -ql render
python3 .agents/skills/manim-video-explainer/check_scene_bounds.py \
  --json tmp/act1_bounds.json
```

Read `references/manim-layout-and-choreography.md` for multi-tier coordinate templates, dynamic `ValueTracker` counters, and anti-collision cleanup rules.

---

## 3. Optional Beat-Synchronized Voiceover Commentary (`mux_narration.py`)

### The Golden Narration Rule

**NEVER read on-screen box titles, arrows, or badge strings verbatim.**
Viewers already see `"API Gateway -> Rate Limiter"` immediately. Write **explanatory process commentary** that states *why* each transition happens, *what invariant* protects the system, and *what causes* rejection or recovery.

### Beat-First Two-Pass Workflow

1. **Write `narration_manifest.json`** separating clean subtitle `text` from phonetic overrides (`tts_phonetic_text` or `phonetic_glossary`) so technical acronyms (`WAL`, `gRPC`, `TTL`, `p99`, `HTTP 429`) are pronounced naturally without polluting `.vtt` captions:

```json
{
  "lead_in_s": 0.20,
  "inter_beat_gap_s": 0.25,
  "phonetic_glossary": {
    "WAL": "write-ahead log",
    "TTL": "T T L",
    "HTTP 429": "H T T P four twenty-nine"
  },
  "scenes": [
    {
      "id": "act1",
      "beats": [
        {
          "id": "act1_ingress",
          "min_visual_s": 3.2,
          "text": "Inbound writes terminate at the gateway before touching stateful storage."
        },
        {
          "id": "act1_bucket",
          "min_visual_s": 3.8,
          "text": "An atomic token-bucket check absorbs burst traffic while bounding database concurrency."
        },
        {
          "id": "act1_reject",
          "min_visual_s": 3.5,
          "text": "When burst tokens drain to zero, excess callers fail fast with HTTP 429."
        }
      ]
    }
  ]
}
```

2. **Pass 1 — Synthesize Per-Beat WAVs & Emit Exact Timings**:

```bash
# Option A: Offline Piper ONNX neural TTS (zero network calls)
python3 .agents/skills/manim-video-explainer/mux_narration.py \
  --manifest tmp/narration_manifest.json \
  --scene-id act1 \
  --wav-dir cache/beats_en \
  --piper-bin ./venv/bin/piper \
  --model ~/.cache/piper-voices/en_US-lessac-medium.onnx \
  --emit-timings tmp/beat_timings.json

# Option B: OpenAI-compatible HTTP neural TTS (/v1/audio/speech, stdlib urllib)
python3 .agents/skills/manim-video-explainer/mux_narration.py \
  --manifest tmp/narration_manifest.json \
  --scene-id act1 \
  --wav-dir cache/beats_en \
  --http-tts-url https://api.openai.com/v1/audio/speech \
  --http-tts-key-env OPENAI_API_KEY \
  --http-tts-model gpt-4o-mini-tts \
  --http-tts-voice cedar \
  --http-tts-instructions "Calm, clear technical documentary narrator; brief pauses at clauses." \
  --emit-timings tmp/beat_timings.json
```

(Any custom CLI synthesizer can also be used via `--tts-cmd "my-tts --out {wav} --text {text}"`, or pre-rendered `.wav` files can be placed directly in `--wav-dir`.)

3. **Render 1080p60 Manim Video Driven by `tmp/beat_timings.json`**:

```bash
./venv/bin/manim -qh --fps 60 scenes.py Act1RateLimiter
```

4. **Pass 2 — Sample-Accurate Master Track Assembly, Loudness Normalization, Tail Guard & AAC Mux**:

```bash
python3 .agents/skills/manim-video-explainer/mux_narration.py \
  --manifest tmp/narration_manifest.json \
  --scene-id act1 \
  --wav-dir cache/beats_en \
  --video media/videos/scenes/1080p60/Act1RateLimiter.mp4 \
  --loudnorm-lufs -18.0 \
  --output output/act1_en.mp4 \
  --vtt output/act1_en.vtt
```

`mux_narration.py` places every beat WAV at `round(start_s * sample_rate)` on a 16-bit PCM master track, extends the final video frame via `tpad=stop_mode=clone` whenever final speech exceeds video duration (zero clipped words), optionally applies EBU R128 loudness normalization (`--loudnorm-lufs -18.0`) so volume stays consistent across chapters, encodes `-c:a aac -b:a 160k -movflags +faststart`, and writes `output/act1_en.vtt`.

Read `references/voiceover-and-commentary.md` for Bad-vs-Good commentary transformations, non-English phonetic overrides, and bilingual audio workflows.

---

## 4. Web Encoding, Multi-Scene Concatenation & Offline HTML5 Player

### Web-Compatible Encoding & Master Reel Concatenation

For silent walkthroughs (without `mux_narration.py`), ensure universal browser/QuickTime compatibility (`yuv420p` + moved `moov` atom):

```bash
ffmpeg -y -i media/videos/scenes/1080p60/Act1RateLimiter.mp4 \
  -c:v libx264 -pix_fmt yuv420p -crf 23 -movflags +faststart \
  output/act1_silent.mp4
```

To assemble multiple narrated act MP4s into a continuous master reel:

```bash
printf "file 'act1_en.mp4'\nfile 'act2_en.mp4'\nfile 'act3_en.mp4'\n" > output/concat.txt
ffmpeg -y -f concat -safe 0 -i output/concat.txt \
  -c:v libx264 -pix_fmt yuv420p -crf 21 -c:a aac -b:a 160k -movflags +faststart \
  output/master_en.mp4
```

### Optional Standalone HTML5 Cinema Player

When delivering a multi-chapter walkthrough or bilingual explainer package:
1. Copy `assets/video_player_template.html` to `output/index.html`.
2. Customize `PLAYER_CONFIG` (`chapters`, `sources`, and `markers` matching beat timestamps from `beat_timings.json`). If delivering a single language, hide or omit the secondary language button in `#langGroup`.
3. Keep all styles, monochrome SVG icons, and scripts 100% inline so the bundle works offline without external CDNs.
4. **HTTP `206 Partial Content` Serving Note**: Browsers require HTTP Byte-Range (`206`) responses to scrub `<video>` timelines and load `<track>` `.vtt` files cleanly (`file://` blocks WebVTT tracks via CORS, and Python's basic `http.server` lacks `Range` header support in older versions). Serve local previews with a Range-capable static server such as `npx serve output` or nginx/Caddy.

### Mandatory Bundle Verification (`lint.py`)

Run `lint.py` across the output directory (or individual `.mp4`, `.vtt`, and `.html` artifacts) before finishing:

```bash
# Narrated bundle (verifies yuv420p video, AAC audio alignment, WebVTT cues, and HTML CSP)
python3 .agents/skills/manim-video-explainer/lint.py output/ --require-audio

# Silent video deliverable
python3 .agents/skills/manim-video-explainer/lint.py output/act1_silent.mp4 --allow-silent
```
