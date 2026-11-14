# Beat-Synchronized Voiceover, Explanatory Commentary & Piper TTS

## 1. The Golden Narration Rule: Process Commentary, Never Label Reading

Viewers can already read `"API Gateway"`, `"Token-Bucket Limiter"`, and `"Primary DB"` in 150 milliseconds.
A voiceover that recites box titles verbatim wastes cognitive bandwidth and feels robotic.

Write **causal engineering commentary** that answers three questions on every beat:
1. **Why** does this transition trigger right now?
2. **What invariant** or state guard holds across the boundary?
3. **What breaks or recovers** if the guard rejects or times out?

### Anti-Pattern vs High-Signal Commentary Examples

- **Beat: Ingress & Rate Limit Check**
  - *Bad (Label reading)*: `"The client sends a request to the API Gateway. Next, the arrow goes to the Token-Bucket Rate Limiter box, which has 100 tokens."`
  - *Good (Explanatory commentary)*: `"Every inbound write hits an atomic token-bucket check at the edge, absorbing sudden traffic spikes before they can exhaust downstream database connections."`
- **Beat: Read-Through Cache Miss**
  - *Bad (Label reading)*: `"The Read-Through Cache shows Cache Miss in yellow, so it calls Primary DB and gets data back."`
  - *Good (Explanatory commentary)*: `"On a cold key, the cache acquires a single-flight lock so fifty concurrent callers wait on one primary query instead of triggering a stampede."`
- **Beat: Distributed Lock & WAL Replication**
  - *Bad (Label reading)*: `"Now we see Write-Ahead Log Replication with Quorum Ack in green."`
  - *Good (Explanatory commentary)*: `"The leader only acknowledges the write once a majority of replicas flush the log entry to disk, preserving durability even if the primary node crashes mid-transaction."`

## 2. Two-Pass Beat-First Timing Architecture

Estimating speech duration by word count causes either awkward dead air or clipped sentences when animations outrun the narrator.
Eliminate drift completely with the two-pass Beat-First pipeline:

1. **Author `narration_manifest.json`**: Define ordered beats with `id`, `min_visual_s`, clean `text` (for `.vtt` subtitles), and optional `tts_phonetic_text` / `phonetic_glossary`.
2. **Pass 1 — Synthesize Beats & Measure Exact Durations**:
   Run `mux_narration.py --manifest narration_manifest.json --piper-bin piper --model <voice.onnx> --emit-timings beat_timings.json`.
   `mux_narration.py` synthesizes one `.wav` per beat, inspects exact PCM frame counts (`nframes / sample_rate`), and records `recommended_wait_s = max(min_visual_s, audio_duration_s + post_pad_s)` per `beat_id`.
3. **Render Manim Scene Driven by `BeatTimer`**:
   Inside `construct(self)`, load `timer = BeatTimer("beat_timings.json")`.
   Track animation `run_time` spent during a beat (`anim_s = 0.8`), then call `timer.hold(self, "act1_quota_check", consumed_anim_s=anim_s)`.
   Because every Manim `play()` + `wait()` sum matches `slot_end_s - start_s`, visual transitions land cleanly on beat boundaries across a 3-minute video.
4. **Pass 2 — Sample-Accurate Track Placement, Tail Guard & Mux**:
   Run `mux_narration.py --manifest narration_manifest.json --video raw_scene.mp4 --output act1_en.mp4`.
   Each beat's PCM samples are written at `round(start_s * sample_rate)` onto a 16-bit mono master buffer. If the final beat's audio extends past the rendered MP4 duration, `mux_narration.py` automatically appends `tpad=stop_mode=clone` so the final frame holds smoothly until speech finishes.

## 3. Phonetic Glossaries for Technical Acronyms

Offline neural TTS models (especially non-English voices or multi-lingual ONNX checkpoints) frequently mispronounce uppercase engineering acronyms (`WAL`, `gRPC`, `TTL`, `SQL`, `p99`, `CAS`, `JSON`) or read slashes/hyphens awkwardly.

Separate **viewer captions** (`text`) from **synthesizer input** (`tts_phonetic_text` or top-level `phonetic_glossary`):

```json
{
  "lead_in_s": 0.20,
  "inter_beat_gap_s": 0.25,
  "phonetic_glossary": {
    "gRPC": "G R P C",
    "WAL": "write-ahead log",
    "TTL": "T T L",
    "p99": "P ninety-nine",
    "HTTP 429": "H T T P four twenty-nine"
  },
  "scenes": [
    {
      "id": "act1",
      "beats": [
        {
          "id": "act1_ingress",
          "min_visual_s": 3.2,
          "text": "Inbound gRPC writes pass an atomic token-bucket check, keeping p99 latency bounded."
        },
        {
          "id": "act1_reject",
          "min_visual_s": 3.0,
          "text": "Once burst tokens hit zero, excess callers receive HTTP 429 immediately.",
          "tts_phonetic_text": "Once burst tokens hit zero, excess callers receive H T T P four twenty-nine immediately."
        }
      ]
    }
  ]
}
```

`mux_narration.py` feeds `tts_phonetic_text` (or `text` transformed by `phonetic_glossary`) to Piper / `--tts-cmd`, while writing only clean `text` into `.vtt` subtitles.

## 4. Offline Piper Voice Setup & Bilingual Walkthroughs

Piper runs 100% locally on CPU in milliseconds per sentence using `.onnx` + `.onnx.json` model pairs:

```bash
# Install piper-tts inside an isolated tool virtualenv
uv venv venv
uv pip install --python venv/bin/python manim piper-tts
```

Store downloaded `.onnx` and `.onnx.json` voice checkpoints in a gitignored user cache directory (such as `~/.cache/piper-voices/`) rather than committing binary weights into a repository.

For **bilingual walkthroughs** (e.g., English + Spanish or German):
- Maintain `narration_en.json` and `narration_alt.json` sharing identical `beat.id` keys.
- Run Pass 1 for each language (`beat_timings_en.json`, `beat_timings_alt.json`). Either render per-language video cuts for tightest pacing, or compute `max(en_wait, alt_wait)` per beat if sharing one base animation render across two muxed audio tracks (`act1_en.mp4` + `act1_en.vtt` and `act1_alt.mp4` + `act1_alt.vtt`).
- Wire both tracks into `PLAYER_CONFIG` inside `assets/video_player_template.html` so viewers can switch languages mid-playback while preserving timestamp position.

## 5. WebVTT Readability Guidelines

- Keep individual beat scripts to **1–2 crisp sentences** (`12–28` words, roughly `4–10` seconds of speech). If an architectural stage needs 20 seconds of explanation, split it into two visual sub-beats (`act2_miss_lock` and `act2_db_fill`) so both the animation and the subtitle cues progress naturally.
- Keep cue lines compact so native `<track kind="subtitles">` overlays stay within two lines at `1080p` and stay inside the reserved lower margin (`y < -3.05`).
