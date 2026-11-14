#!/usr/bin/env python3
"""Beat-synchronized TTS synthesizer, timeline assembler, AAC muxer, and WebVTT generator.

Workflow:
  Pass 1 (Pre-render timings):
    Synthesize per-beat WAV files via offline Piper ONNX, an OpenAI-compatible HTTP
    `/v1/audio/speech` endpoint, or any CLI TTS from a JSON narration manifest and emit
    `beat_timings.json` so Manim scenes hold each beat for the exact spoken duration (`BeatTimer`).

  Pass 2 (Sample-accurate master mux, EBU R128 loudness & WebVTT):
    Place every beat WAV onto a sample-accurate 16-bit PCM master track, clone-pad the
    final video frame (`tpad=stop_mode=clone`) whenever trailing speech exceeds video
    length so zero words ever clip, optionally normalize loudness (`--loudnorm-lufs`),
    mux H.264 (`yuv420p`) + AAC (`160k`, `+faststart`), and emit clean `.vtt` subtitles.
"""

from __future__ import annotations

import argparse
from array import array
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import wave


DEFAULT_SAMPLE_RATE = 22050
DEFAULT_MIN_VISUAL_S = 1.5
DEFAULT_POST_PAD_S = 0.25
DEFAULT_INTER_BEAT_GAP_S = 0.20
DEFAULT_LEAD_IN_S = 0.15
DEFAULT_TAIL_PAD_S = 0.30
DEFAULT_HTTP_TTS_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_HTTP_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_HTTP_TTS_VOICE = "cedar"


@dataclass
class BeatTiming:
    """Computed timing and file metadata for a single narration beat."""

    id: str
    scene_id: str
    start_s: float
    end_s: float
    audio_duration_s: float
    min_visual_s: float
    recommended_wait_s: float
    slot_end_s: float
    text: str
    spoken_text: str
    wav_path: str


def format_vtt_timestamp(seconds: float) -> str:
    """Formats seconds as WebVTT `HH:MM:SS.mmm`."""
    clamped = max(0.0, seconds)
    total_ms = int(round(clamped * 1000.0))
    hours, rem_ms = divmod(total_ms, 3_600_000)
    minutes, rem_ms = divmod(rem_ms, 60_000)
    secs, millis = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def apply_phonetic_glossary(text: str, glossary: dict[str, str]) -> str:
    """Applies whole-token phonetic replacements for TTS synthesis."""
    result = text
    for term, phonetic in glossary.items():
        if not term:
            continue
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])")
        result = pattern.sub(phonetic, result)
    return result


def extract_manifest_beats(
    manifest: dict[str, Any],
    *,
    scene_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Extracts ordered beat records from either `scenes -> beats` or top-level `beats`."""
    glossary = dict(manifest.get("phonetic_glossary") or {})
    collected: list[dict[str, Any]] = []

    if "scenes" in manifest:
        for scene in manifest["scenes"]:
            scene_id = str(scene.get("id", "default"))
            if scene_filter and scene_id != scene_filter:
                continue
            for beat in scene.get("beats", []):
                entry = dict(beat)
                entry.setdefault("scene_id", scene_id)
                entry["_glossary"] = glossary
                collected.append(entry)
    elif "beats" in manifest:
        for beat in manifest["beats"]:
            entry = dict(beat)
            entry.setdefault("scene_id", "default")
            entry["_glossary"] = glossary
            collected.append(entry)
    else:
        raise ValueError("Manifest must contain either a 'scenes' list or a 'beats' list.")

    if not collected:
        raise ValueError(
            f"No beats found in manifest"
            + (f" for scene '{scene_filter}'." if scene_filter else ".")
        )
    return collected


def synthesize_with_piper(
    spoken_text: str,
    out_wav: Path,
    *,
    piper_bin: str,
    model_path: Path,
    speaker: int | None = None,
    length_scale: float | None = None,
) -> None:
    """Synthesizes `spoken_text` to `out_wav` via offline Piper ONNX CLI."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [piper_bin, "--model", str(model_path), "--output_file", str(out_wav)]
    if speaker is not None:
        cmd.extend(["--speaker", str(speaker)])
    if length_scale is not None:
        cmd.extend(["--length_scale", f"{length_scale:.3f}"])

    proc = subprocess.run(
        cmd,
        input=(spoken_text.strip() + "\n").encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0 or not out_wav.exists():
        stderr_msg = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Piper TTS failed (exit {proc.returncode}): {stderr_msg}")


def synthesize_with_template(
    spoken_text: str,
    out_wav: Path,
    *,
    tts_cmd_template: str,
) -> None:
    """Synthesizes `spoken_text` using a tokenized CLI template (`{wav}` and `{text}`)."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    raw_tokens = shlex.split(tts_cmd_template)
    if not raw_tokens:
        raise ValueError("--tts-cmd template cannot be empty.")

    used_text_placeholder = False
    cmd: list[str] = []
    for tok in raw_tokens:
        if "{text}" in tok:
            used_text_placeholder = True
        cmd.append(tok.replace("{wav}", str(out_wav)).replace("{text}", spoken_text))

    stdin_bytes = None if used_text_placeholder else (spoken_text.strip() + "\n").encode("utf-8")
    proc = subprocess.run(
        cmd,
        input=stdin_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0 or not out_wav.exists():
        stderr_msg = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"TTS command failed (exit {proc.returncode}): {stderr_msg}")


def _resolve_speech_endpoint(url: str) -> str:
    """Normalizes a base `/v1` URL or returns an explicit `/v1/audio/speech` URL unchanged."""
    cleaned = url.strip()
    if not cleaned:
        raise ValueError("--http-tts-url cannot be empty.")
    stripped = cleaned.rstrip("/")
    if stripped.endswith("/v1"):
        return f"{stripped}/audio/speech"
    return cleaned


def synthesize_with_http_tts(
    spoken_text: str,
    out_wav: Path,
    *,
    url: str,
    api_key: str | None = None,
    api_key_env: str = DEFAULT_HTTP_TTS_KEY_ENV,
    model: str = DEFAULT_HTTP_TTS_MODEL,
    voice: str = DEFAULT_HTTP_TTS_VOICE,
    instructions: str | None = None,
    response_format: str = "wav",
    speed: float | None = None,
    timeout_s: float = 60.0,
) -> None:
    """Synthesizes `spoken_text` via any OpenAI-compatible HTTP `/v1/audio/speech` endpoint."""
    endpoint = _resolve_speech_endpoint(url)
    parsed = urllib.parse.urlparse(endpoint)
    hostname = (parsed.hostname or "").lower()
    is_loopback = hostname in {"127.0.0.1", "localhost", "::1"}

    token = api_key if api_key is not None else os.environ.get(api_key_env, "").strip()
    if not token and not is_loopback:
        raise RuntimeError(
            f"HTTP TTS API key environment variable '{api_key_env}' is not set or empty."
        )

    payload: dict[str, Any] = {
        "model": model,
        "voice": voice,
        "input": spoken_text.strip(),
        "response_format": response_format,
    }
    if instructions and instructions.strip():
        payload["instructions"] = instructions.strip()
    if speed is not None:
        payload["speed"] = float(speed)

    headers = {
        "Content-Type": "application/json",
        "Accept": "audio/wav, application/octet-stream",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            audio_bytes = resp.read()
    except urllib.error.HTTPError as exc:
        err_body = ""
        try:
            err_body = exc.read(512).decode("utf-8", errors="replace").strip()
        except Exception:
            err_body = str(exc.reason)
        raise RuntimeError(
            f"HTTP TTS request failed with status {exc.code} from {endpoint}: {err_body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HTTP TTS connection failed for {endpoint}: {exc.reason}") from exc

    if not audio_bytes:
        raise RuntimeError(f"HTTP TTS endpoint {endpoint} returned an empty audio body.")

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    if len(audio_bytes) >= 12 and audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
        out_wav.write_bytes(audio_bytes)
        return

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError(
            f"HTTP TTS endpoint returned non-RIFF audio ({len(audio_bytes)} bytes), "
            "and `ffmpeg` is not on PATH to transcode it to WAV."
        )

    with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as tmp_in:
        tmp_in.write(audio_bytes)
        tmp_in_path = Path(tmp_in.name)
    try:
        cmd = [
            ffmpeg_bin,
            "-y",
            "-v",
            "error",
            "-i",
            str(tmp_in_path),
            "-ac",
            "1",
            "-sample_fmt",
            "s16",
            str(out_wav),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode != 0 or not out_wav.exists():
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"ffmpeg failed decoding HTTP TTS response: {err}")
    finally:
        tmp_in_path.unlink(missing_ok=True)


def read_pcm16_mono(wav_path: Path, target_rate: int | None = None) -> tuple[array, int]:
    """Reads a WAV file as 16-bit mono PCM samples (`array('h')`) at `target_rate`.

    Uses Python's `wave` module directly when the file is already 16-bit mono PCM at
    the desired sample rate; falls back to `ffmpeg` resampling otherwise.
    """
    with wave.open(str(wav_path), "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        comptype = wf.getcomptype()
        nframes = wf.getnframes()

        desired_rate = target_rate or framerate
        if channels == 1 and sampwidth == 2 and comptype == "NONE" and framerate == desired_rate:
            raw = wf.readframes(nframes)
            samples = array("h")
            samples.frombytes(raw)
            if sys.byteorder != "little":
                samples.byteswap()
            return samples, framerate

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError(
            f"WAV file {wav_path} requires conversion (ch={channels}, width={sampwidth}, "
            f"rate={framerate} -> {desired_rate}), but `ffmpeg` is not on PATH."
        )

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        cmd = [
            ffmpeg_bin,
            "-y",
            "-v",
            "error",
            "-i",
            str(wav_path),
            "-ac",
            "1",
            "-ar",
            str(desired_rate),
            "-sample_fmt",
            "s16",
            str(tmp_path),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"ffmpeg WAV normalization failed for {wav_path}: {err}")
        return read_pcm16_mono(tmp_path, target_rate=desired_rate)
    finally:
        tmp_path.unlink(missing_ok=True)


def write_pcm16_mono(wav_path: Path, samples: array, sample_rate: int) -> None:
    """Writes 16-bit mono PCM samples (`array('h')`) to `wav_path`."""
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    out_samples = samples
    if sys.byteorder != "little":
        out_samples = array("h", samples)
        out_samples.byteswap()
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(out_samples.tobytes())


def probe_video_stream(video_path: Path) -> dict[str, Any]:
    """Probes duration, codec, and pixel format of `video_path` via `ffprobe`."""
    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        raise RuntimeError("`ffprobe` is required for video probing but was not found on PATH.")

    cmd = [
        ffprobe_bin,
        "-v",
        "error",
        "-show_format",
        "-show_streams",
        "-of",
        "json",
        str(video_path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffprobe failed on {video_path}: {err}")

    info = json.loads(proc.stdout.decode("utf-8"))
    video_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    if not video_streams:
        raise RuntimeError(f"No video stream found inside {video_path}")

    vs = video_streams[0]
    fmt = info.get("format", {})
    duration_str = vs.get("duration") or fmt.get("duration") or "0"
    return {
        "duration_s": float(duration_str),
        "codec_name": str(vs.get("codec_name", "")),
        "pix_fmt": str(vs.get("pix_fmt", "")),
    }


def resolve_and_measure_beats(
    manifest: dict[str, Any],
    *,
    manifest_dir: Path,
    wav_dir: Path,
    scene_filter: str | None = None,
    piper_bin: str | None = None,
    model_path: Path | None = None,
    piper_speaker: int | None = None,
    piper_length_scale: float | None = None,
    tts_cmd: str | None = None,
    http_tts_url: str | None = None,
    http_tts_key_env: str = DEFAULT_HTTP_TTS_KEY_ENV,
    http_tts_model: str = DEFAULT_HTTP_TTS_MODEL,
    http_tts_voice: str = DEFAULT_HTTP_TTS_VOICE,
    http_tts_instructions: str | None = None,
    force_synth: bool = False,
    target_sample_rate: int | None = None,
) -> tuple[list[BeatTiming], list[array], int]:
    """Synthesizes or loads every beat WAV and computes non-overlapping beat timings."""
    raw_beats = extract_manifest_beats(manifest, scene_filter=scene_filter)
    lead_in_s = float(manifest.get("lead_in_s", DEFAULT_LEAD_IN_S))
    inter_gap_s = float(manifest.get("inter_beat_gap_s", DEFAULT_INTER_BEAT_GAP_S))
    default_min_vis = float(manifest.get("min_visual_s", DEFAULT_MIN_VISUAL_S))
    default_post_pad = float(manifest.get("post_pad_s", DEFAULT_POST_PAD_S))
    manifest_instructions = manifest.get("tts_instructions")
    chosen_rate: int | None = target_sample_rate or (
        int(manifest["sample_rate"]) if "sample_rate" in manifest else None
    )

    timings: list[BeatTiming] = []
    beat_pcm_buffers: list[array] = []
    cursor_s = lead_in_s
    prev_audio_end_s = 0.0

    for index, beat in enumerate(raw_beats):
        beat_id = str(beat.get("id") or f"beat_{index + 1:02d}")
        scene_id = str(beat.get("scene_id", "default"))
        display_text = str(beat.get("text", "")).strip()
        if not display_text:
            raise ValueError(f"Beat '{beat_id}' is missing non-empty 'text'.")

        glossary = beat.get("_glossary") or {}
        raw_phonetic = beat.get("tts_phonetic_text")
        spoken_text = (
            str(raw_phonetic).strip()
            if raw_phonetic
            else apply_phonetic_glossary(display_text, glossary)
        )

        if beat.get("wav_path"):
            candidate = Path(str(beat["wav_path"]))
            wav_path = candidate if candidate.is_absolute() else (manifest_dir / candidate)
        else:
            wav_path = wav_dir / f"{beat_id}.wav"

        needs_synth = force_synth or not wav_path.exists()
        if needs_synth:
            if piper_bin and model_path:
                synthesize_with_piper(
                    spoken_text,
                    wav_path,
                    piper_bin=piper_bin,
                    model_path=model_path,
                    speaker=piper_speaker,
                    length_scale=piper_length_scale,
                )
            elif http_tts_url:
                beat_instructions = (
                    beat.get("tts_instructions")
                    or manifest_instructions
                    or http_tts_instructions
                )
                synthesize_with_http_tts(
                    spoken_text,
                    wav_path,
                    url=http_tts_url,
                    api_key_env=http_tts_key_env,
                    model=http_tts_model,
                    voice=http_tts_voice,
                    instructions=str(beat_instructions) if beat_instructions else None,
                )
            elif tts_cmd:
                synthesize_with_template(spoken_text, wav_path, tts_cmd_template=tts_cmd)
            else:
                raise FileNotFoundError(
                    f"Beat WAV not found at {wav_path}. Pass --piper-bin & --model, "
                    f"--http-tts-url, --tts-cmd, or pre-render beat WAVs into --wav-dir."
                )

        samples, rate = read_pcm16_mono(wav_path, target_rate=chosen_rate)
        if chosen_rate is None:
            chosen_rate = rate

        audio_dur_s = len(samples) / float(chosen_rate)
        min_visual_s = float(beat.get("min_visual_s", default_min_vis))
        post_pad_s = float(beat.get("post_pad_s", default_post_pad))
        recommended_wait_s = max(min_visual_s, audio_dur_s + post_pad_s)

        if "start_s" in beat and beat["start_s"] is not None:
            requested_start_s = float(beat["start_s"])
            if requested_start_s < prev_audio_end_s:
                placed_start_s = prev_audio_end_s + 0.05
                print(
                    f"WARN: Beat '{beat_id}' requested start_s={requested_start_s:.2f}s overlaps "
                    f"previous speech ending at {prev_audio_end_s:.2f}s; shifted to {placed_start_s:.2f}s.",
                    file=sys.stderr,
                )
            else:
                placed_start_s = requested_start_s
        else:
            placed_start_s = cursor_s

        end_s = placed_start_s + audio_dur_s
        slot_end_s = placed_start_s + recommended_wait_s
        prev_audio_end_s = end_s
        cursor_s = slot_end_s + inter_gap_s

        timings.append(
            BeatTiming(
                id=beat_id,
                scene_id=scene_id,
                start_s=round(placed_start_s, 4),
                end_s=round(end_s, 4),
                audio_duration_s=round(audio_dur_s, 4),
                min_visual_s=round(min_visual_s, 4),
                recommended_wait_s=round(recommended_wait_s, 4),
                slot_end_s=round(slot_end_s, 4),
                text=display_text,
                spoken_text=spoken_text,
                wav_path=str(wav_path),
            )
        )
        beat_pcm_buffers.append(samples)

    assert chosen_rate is not None
    return timings, beat_pcm_buffers, chosen_rate


def assemble_master_track(
    timings: list[BeatTiming],
    beat_pcm_buffers: list[array],
    sample_rate: int,
    *,
    min_total_duration_s: float = 0.0,
) -> array:
    """Places per-beat PCM buffers onto a sample-accurate master track without clipping."""
    max_sample_index = int(round(min_total_duration_s * sample_rate))
    placements: list[tuple[int, array]] = []

    for bt, pcm in zip(timings, beat_pcm_buffers):
        start_idx = max(0, int(round(bt.start_s * sample_rate)))
        end_idx = start_idx + len(pcm)
        if end_idx > max_sample_index:
            max_sample_index = end_idx
        placements.append((start_idx, pcm))

    master = array("h", [0]) * max(1, max_sample_index)
    for start_idx, pcm in placements:
        master[start_idx : start_idx + len(pcm)] = pcm
    return master


def build_webvtt(timings: list[BeatTiming]) -> str:
    """Renders a clean WebVTT subtitle file from `timings` using display `text`."""
    lines = ["WEBVTT", ""]
    for idx, bt in enumerate(timings, start=1):
        start_ts = format_vtt_timestamp(bt.start_s)
        # Ensure minimum 0.5s subtitle visibility even for ultra-short cues.
        cue_end = max(bt.end_s, bt.start_s + 0.5)
        if idx < len(timings):
            cue_end = min(cue_end, max(bt.end_s, timings[idx].start_s - 0.02))
        end_ts = format_vtt_timestamp(cue_end)
        lines.append(f"{idx}")
        lines.append(f"{start_ts} --> {end_ts}")
        lines.append(bt.text)
        lines.append("")
    return "\n".join(lines)


def write_timings_json(
    timings: list[BeatTiming],
    sample_rate: int,
    output_path: Path,
) -> None:
    """Writes `beat_timings.json` with both O(1) `beats` map and ordered `timeline`."""
    total_audio_end = max((bt.end_s for bt in timings), default=0.0)
    total_slot_end = max((bt.slot_end_s for bt in timings), default=0.0)
    payload = {
        "sample_rate": sample_rate,
        "total_audio_end_s": round(total_audio_end, 4),
        "total_recommended_duration_s": round(total_slot_end, 4),
        "beats": {bt.id: asdict(bt) for bt in timings},
        "timeline": [asdict(bt) for bt in timings],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def mux_video_and_audio(
    video_path: Path,
    master_wav_path: Path,
    output_mp4_path: Path,
    *,
    required_duration_s: float,
    reencode_video: bool = False,
    loudnorm_lufs: float | None = None,
) -> dict[str, Any]:
    """Muxes video + master WAV into web-ready H.264 (`yuv420p`) + AAC MP4 (`+faststart`).

    If `required_duration_s` exceeds the input video's length, extends the final video
    frame seamlessly using `tpad=stop_mode=clone` so trailing speech never clips.
    Optionally applies EBU R128 loudness normalization (`loudnorm_lufs`, e.g. `-18.0`).
    """
    if loudnorm_lufs is not None and not (-70.0 <= float(loudnorm_lufs) <= -5.0):
        raise ValueError(
            f"--loudnorm-lufs must lie within [-70.0, -5.0] LUFS (e.g. -18.0), got {loudnorm_lufs}."
        )

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("`ffmpeg` is required for MP4 muxing but was not found on PATH.")

    probe = probe_video_stream(video_path)
    video_dur_s = probe["duration_s"]
    pad_s = max(0.0, required_duration_s - video_dur_s)

    output_mp4_path.parent.mkdir(parents=True, exist_ok=True)
    needs_filter_pad = pad_s > 0.02
    needs_reencode = (
        reencode_video
        or needs_filter_pad
        or probe["codec_name"] != "h264"
        or probe["pix_fmt"] != "yuv420p"
    )

    cmd = [
        ffmpeg_bin,
        "-y",
        "-v",
        "error",
        "-i",
        str(video_path),
        "-i",
        str(master_wav_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
    ]
    if needs_filter_pad:
        cmd.extend(["-vf", f"tpad=stop_mode=clone:stop_duration={pad_s:.3f}"])

    if needs_reencode:
        cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "medium"])
    else:
        cmd.extend(["-c:v", "copy"])

    if loudnorm_lufs is not None:
        cmd.extend(
            [
                "-af",
                f"loudnorm=I={float(loudnorm_lufs):.1f}:TP=-1.5:LRA=11",
                "-ar",
                "48000",
            ]
        )

    cmd.extend(
        [
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(output_mp4_path),
        ]
    )
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg mux failed: {err}")

    return {
        "input_video_duration_s": round(video_dur_s, 4),
        "cloned_tail_pad_s": round(pad_s if needs_filter_pad else 0.0, 4),
        "reencoded_video": needs_reencode,
        "loudnorm_lufs": round(float(loudnorm_lufs), 2) if loudnorm_lufs is not None else None,
        "output_mp4": str(output_mp4_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synthesize beat narration WAVs, emit beat timings, mux H.264+AAC MP4, and write WebVTT captions."
    )
    parser.add_argument("--manifest", type=Path, required=True, help="Path to narration JSON manifest.")
    parser.add_argument("--scene-id", type=str, help="Optional scene ID filter in multi-scene manifests.")
    parser.add_argument(
        "--wav-dir",
        type=Path,
        help="Directory for per-beat WAV files (defaults to <manifest_dir>/cache/beats).",
    )
    parser.add_argument("--piper-bin", type=str, help="Path or binary name for offline `piper` TTS.")
    parser.add_argument("--model", type=Path, help="Path to Piper `.onnx` voice model.")
    parser.add_argument("--piper-speaker", type=int, help="Optional speaker ID for multi-speaker Piper models.")
    parser.add_argument("--piper-length-scale", type=float, help="Optional Piper speaking rate length_scale.")
    parser.add_argument(
        "--http-tts-url",
        type=str,
        help="OpenAI-compatible `/v1/audio/speech` endpoint URL for HTTP neural TTS.",
    )
    parser.add_argument(
        "--http-tts-key-env",
        type=str,
        default=DEFAULT_HTTP_TTS_KEY_ENV,
        help=f"Environment variable holding HTTP TTS Bearer key (default: {DEFAULT_HTTP_TTS_KEY_ENV}).",
    )
    parser.add_argument(
        "--http-tts-model",
        type=str,
        default=DEFAULT_HTTP_TTS_MODEL,
        help=f"HTTP TTS model name (default: {DEFAULT_HTTP_TTS_MODEL}).",
    )
    parser.add_argument(
        "--http-tts-voice",
        type=str,
        default=DEFAULT_HTTP_TTS_VOICE,
        help=f"HTTP TTS voice identifier, e.g. cedar or marin (default: {DEFAULT_HTTP_TTS_VOICE}).",
    )
    parser.add_argument(
        "--http-tts-instructions",
        type=str,
        help="Optional natural narrator prosody/style instructions sent to `/v1/audio/speech`.",
    )
    parser.add_argument(
        "--tts-cmd",
        type=str,
        help="Generic TTS CLI template with {wav} and optional {text} placeholders.",
    )
    parser.add_argument(
        "--force-synth",
        action="store_true",
        help="Re-synthesize beat WAVs even when cached WAV files already exist.",
    )
    parser.add_argument("--sample-rate", type=int, help="Override master PCM sample rate (Hz).")
    parser.add_argument("--emit-timings", type=Path, help="Output JSON path for computed beat timings.")
    parser.add_argument("--master-wav", type=Path, help="Optional path to save assembled master WAV track.")
    parser.add_argument("--video", type=Path, help="Input rendered MP4 video to mux narration onto.")
    parser.add_argument("--output", type=Path, help="Output narrated MP4 path (required when --video is set).")
    parser.add_argument("--vtt", type=Path, help="Output WebVTT (.vtt) subtitle path.")
    parser.add_argument("--no-vtt", action="store_true", help="Skip automatic .vtt emission next to --output.")
    parser.add_argument(
        "--tail-pad",
        type=float,
        default=DEFAULT_TAIL_PAD_S,
        help=f"Breathing room after final spoken word in seconds (default: {DEFAULT_TAIL_PAD_S}).",
    )
    parser.add_argument(
        "--loudnorm-lufs",
        type=float,
        help="Optional EBU R128 integrated loudness target in LUFS (e.g. -18.0) during MP4 muxing.",
    )
    parser.add_argument(
        "--reencode-video",
        action="store_true",
        help="Force H.264 yuv420p video re-encoding even when no tail padding is needed.",
    )
    args = parser.parse_args(argv)

    if args.video and not args.output:
        parser.error("--output <narrated.mp4> is required when --video is provided.")
    if bool(args.piper_bin) ^ bool(args.model):
        parser.error("--piper-bin and --model must be provided together.")

    try:
        manifest_path = args.manifest.resolve()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_dir = manifest_path.parent
        wav_dir = (args.wav_dir or (manifest_dir / "cache" / "beats")).resolve()
        wav_dir.mkdir(parents=True, exist_ok=True)

        timings, pcm_buffers, sample_rate = resolve_and_measure_beats(
            manifest,
            manifest_dir=manifest_dir,
            wav_dir=wav_dir,
            scene_filter=args.scene_id,
            piper_bin=args.piper_bin,
            model_path=args.model,
            piper_speaker=args.piper_speaker,
            piper_length_scale=args.piper_length_scale,
            tts_cmd=args.tts_cmd,
            http_tts_url=args.http_tts_url,
            http_tts_key_env=args.http_tts_key_env,
            http_tts_model=args.http_tts_model,
            http_tts_voice=args.http_tts_voice,
            http_tts_instructions=args.http_tts_instructions,
            force_synth=args.force_synth,
            target_sample_rate=args.sample_rate,
        )

        if args.emit_timings:
            write_timings_json(timings, sample_rate, args.emit_timings)
            print(f"Wrote beat timings ({len(timings)} beats) -> {args.emit_timings}")

        vtt_path = args.vtt
        if vtt_path is None and args.output and not args.no_vtt:
            vtt_path = args.output.with_suffix(".vtt")

        if vtt_path is not None:
            vtt_path.parent.mkdir(parents=True, exist_ok=True)
            vtt_path.write_text(build_webvtt(timings), encoding="utf-8")
            print(f"Wrote WebVTT captions ({len(timings)} cues) -> {vtt_path}")

        last_speech_end_s = max((bt.end_s for bt in timings), default=0.0)
        required_total_s = last_speech_end_s + max(0.0, args.tail_pad)

        if args.video:
            probe = probe_video_stream(args.video)
            target_track_s = max(probe["duration_s"], required_total_s)
            master_samples = assemble_master_track(
                timings,
                pcm_buffers,
                sample_rate,
                min_total_duration_s=target_track_s,
            )
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                temp_master_path = Path(tmp_wav.name)
            try:
                master_out = args.master_wav or temp_master_path
                write_pcm16_mono(master_out, master_samples, sample_rate)
                mux_summary = mux_video_and_audio(
                    args.video,
                    master_out,
                    args.output,
                    required_duration_s=target_track_s,
                    reencode_video=args.reencode_video,
                    loudnorm_lufs=args.loudnorm_lufs,
                )
                print(
                    f"Muxed narrated MP4 -> {mux_summary['output_mp4']} "
                    f"(video_in={mux_summary['input_video_duration_s']:.2f}s, "
                    f"tail_clone_pad={mux_summary['cloned_tail_pad_s']:.2f}s)"
                )
            finally:
                temp_master_path.unlink(missing_ok=True)
        elif args.master_wav:
            master_samples = assemble_master_track(
                timings,
                pcm_buffers,
                sample_rate,
                min_total_duration_s=required_total_s,
            )
            write_pcm16_mono(args.master_wav, master_samples, sample_rate)
            print(f"Wrote master WAV ({required_total_s:.2f}s @ {sample_rate}Hz) -> {args.master_wav}")

    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
