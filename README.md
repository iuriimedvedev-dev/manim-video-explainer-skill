# Manim Video Explainer Skill — Design Log

## Purpose

This skill equips AI agents to author deterministic, high-definition (`1080p60`) technical explainer videos, architecture walkthroughs, and narrated system tutorials using Manim Community (LaTeX-free), optional offline neural TTS voiceover (Piper ONNX or generic CLI TTS), WebVTT captions, and a zero-CDN HTML5 chapter player.
It solves four recurring failure modes in agent-generated technical animations: broken TeX environments, out-of-frame or colliding text labels, robotic label-reading voiceovers that drift out of sync or clip mid-sentence, and unplayable pixel formats in web browsers.

## Architecture

- **`SKILL.md` vs `references/` vs `assets/`**: `SKILL.md` stays compact (< 260 lines) with the end-to-end 4-stage workflow (Storyboard -> LaTeX-Free Manim -> Two-Pass Beat Voiceover -> Web Delivery & Linting). Deep coordinate grids and choreography hygiene live in `references/manim-layout-and-choreography.md`, while commentary writing standards and phonetic TTS recipes live in `references/voiceover-and-commentary.md`.
- **LaTeX-Free Nord Primitive & Orthogonal Schematic Kit (`assets/nord_manim_theme.py`)**: Provides Nord Polar/Frost/Aurora palette tokens, safe-frame limits (`[-6.6, 6.6] x [-3.5, 3.5]`), `BeatTimer`, Pango/Cairo mobject builders (`Text`, `MarkupText`, `RoundedRectangle`, `Arrow`, `DashedVMobject`), and port-anchored orthogonal connector primitives (`connect_h`, `connect_v`, `connect_elbow`, `animate_packet_along`, `assert_all_orthogonal`). `manim` is imported lazily inside mobject builders so `BeatTimer`, palette constants, and `assert_all_orthogonal` work in lightweight stdlib test runners.
- **Fast Geometry Gate (`check_scene_bounds.py`)**: Hooks `Scene.wait` in Manim `--dry_run` mode (or validates JSON checkpoint snapshots) to detect frame boundary violations outside `[-6.8, 6.8] x [-3.75, 3.75]` and 2D AABB collisions between sibling text labels in seconds—before spending CPU time encoding `1080p60` video.
- **Two-Pass Beat-First Audio Synchronization (`mux_narration.py`)**:
  1. *Pass 1* synthesizes individual beat WAVs (via offline Piper ONNX, any OpenAI-compatible `/v1/audio/speech` HTTP endpoint, or a custom CLI TTS), measures exact PCM sample durations, and writes `beat_timings.json` (`recommended_wait_s`) consumed by `BeatTimer` during Manim scene construction.
  2. *Pass 2* places each beat's 16-bit PCM samples at `round(start_s * sample_rate)` onto a master audio buffer, applies `ffmpeg` `tpad=stop_mode=clone` whenever final narration outlasts video frames, optionally normalizes loudness to EBU R128 (`--loudnorm-lufs`), muxes H.264 (`yuv420p`) + AAC (`+faststart`), and emits clean `.vtt` subtitles.
- **Deliverable Linter (`lint.py`)**: Stdlib verifier using `ffprobe` and `html.parser` to enforce `yuv420p` browser compatibility, audio/video tail alignment, monotonic WebVTT cues, offline CSP headers, zero external CDNs, and zero emojis.

## Alternatives Considered

- **Single monolithic TTS audio file vs Per-beat WAV synthesis + sample-accurate placement**:
  - *Evaluated*: Rendering an entire scene's script as one WAV file and guessing `self.wait(...)` durations from character or word counts.
  - *Rejected*: Speech cadence varies sharply across punctuation, numbers, and technical acronyms; errors accumulate across beats until visuals lag or lead by several seconds. Synthesizing per-beat WAVs and feeding exact durations into `BeatTimer` eliminates cumulative drift completely.
- **`manim-voiceover` plugin vs Standalone `mux_narration.py` + `BeatTimer`**:
  - *Evaluated*: Coupling scenes to third-party Manim audio plugins (`manim-voiceover` + `sox`/PortAudio dependencies).
  - *Rejected*: External plugins frequently pin conflicting Manim/NumPy/SciPy versions, require native audio libraries (`sox`), and complicate bilingual re-muxing or phonetic vs WebVTT text separation. A 100% stdlib `wave` + `ffmpeg` pipeline works everywhere `manim` runs.
- **`MathTex` / `Tex` formulas vs Pango `MarkupText` + `Code`**:
  - *Evaluated*: Using standard Manim `Tex`/`MathTex` for subscripts and equations.
  - *Rejected*: Installing `MacTeX` / `TeX Live` adds gigabytes of setup friction and often fails in headless CI or developer sandboxes. `MarkupText` (`<sub>`, `<sup>`, `<span foreground="...">`) covers system architecture typography cleanly with zero TeX dependencies.
- **`Arrow(card_a, card_b)` center-ray connectors vs Port-anchored `connect_h` / `connect_v` / `connect_elbow`**:
  - *Evaluated*: Passing two card `Mobject`s directly to `Arrow(card_a, card_b)`.
  - *Rejected*: Manim aims `Arrow(mobj_a, mobj_b)` along the line connecting bounding-box centers, producing crooked diagonal arrows whenever two cards have different widths, heights, or Y-offsets. Port-anchored `connect_h`, `connect_v`, and `connect_elbow` lock coordinates along a shared axis and expose `.wire_points` verified by pure-stdlib `assert_all_orthogonal`.
- **`ffmpeg -shortest` during audio muxing vs `tpad=stop_mode=clone` video extension**:
  - *Evaluated*: Passing `-shortest` when combining video and master WAV streams.
  - *Rejected*: `-shortest` silently chops off the final spoken words if an animation ends slightly early. Cloning the final frame (`tpad=stop_mode=clone:stop_duration=...`) guarantees zero clipped words while keeping visual continuity intact.

## Implementation Notes

- **Fast PCM buffer assembly**: `mux_narration.py` manipulates 16-bit mono PCM audio via Python's stdlib `array('h')` slice assignment rather than per-sample Python loops, assembling multi-minute master tracks in milliseconds with zero third-party packages.
- **Zero-dependency `/v1/audio/speech` HTTP TTS & EBU R128 loudness**: `synthesize_with_http_tts()` uses stdlib `urllib.request` to synthesize speech from any OpenAI-compatible `/v1/audio/speech` endpoint (including `instructions` for natural prosody), and `--loudnorm-lufs` applies `loudnorm=I=<target>:TP=-1.5:LRA=11` at `48 kHz` AAC so multi-chapter bundles maintain uniform loudness.
- **Clean caption vs spoken phonetic separation**: `narration_manifest.json` supports both beat-level `tts_phonetic_text` and manifest-level whole-token `phonetic_glossary` regex substitution for the TTS synthesizer while preserving unmodified `text` in generated `.vtt` cues.
- **Glyph-safe collision checking**: In `check_scene_bounds.py`, `extract_scene_bboxes()` treats `Text`, `MarkupText`, and `Code` mobjects as atomic leaf boxes instead of recursing into per-character glyph submobjects, preventing false-positive intra-word kerning collisions while catching real label-on-label overlaps.

## Useful Context

- Keep `assets/video_player_template.html` 100% self-contained (inline CSS, inline monochrome `<symbol>` SVGs, inline script, strict offline CSP) so `lint.py` passes without modification when copied into output bundles.
- When serving generated HTML5 players locally, prefer HTTP `206` Range-capable static servers so browser `<video>` scrubbing and `<track>` subtitle loading work reliably.

## Attribution and License

The code, templates, and documentation in this skill are released under the MIT license.
