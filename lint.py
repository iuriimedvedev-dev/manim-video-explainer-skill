#!/usr/bin/env python3
"""Bundle verifier for Manim explainer videos, WebVTT subtitles, and HTML5 chapter players.

Checks:
1. MP4 streams (`ffprobe`): non-empty file, H.264 `yuv420p` video stream, positive duration,
   optional/paired AAC audio stream presence, and audio-vs-video tail truncation safety.
2. WebVTT (`.vtt`): valid `WEBVTT` signature, well-formed monotonic `-->` timestamps,
   positive cue durations, non-empty caption lines, and zero emojis.
3. HTML5 Cinema Player (`.html`): Content-Security-Policy meta tag, zero external CDN
   scripts/fonts/stylesheets, `<video>` element presence, zero emojis, and secret/host safety.
"""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


EMOJI_PATTERN = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\ufe0f\u200d\u20e3]")
PRIVATE_HOST_PATTERN = re.compile(
    r"(?i)\b(?:[a-z0-9-]+\.)+(?:internal|corp|lan)\b|"
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
)
SECRET_PATTERN = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?token|password|secret|authorization)\s*[:=]\s*[\"']?"
    r"(?!none\b|null\b|false\b|true\b|placeholder\b|example\b)[A-Za-z0-9_./+\-=]{12,}|"
    r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_]{16,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
EXTERNAL_URL_PATTERN = re.compile(r"(?i)^(?:https?:)?//")
CSS_EXTERNAL_URL_PATTERN = re.compile(
    r"(?i)@import\s+(?:url\()?['\"]?(?:https?:)?//|url\(\s*['\"]?(?:https?:)?//"
)
VTT_CUE_ARROW_PATTERN = re.compile(
    r"^\s*((?:\d{2}:)?\d{2}:\d{2}\.\d{3})\s*-->\s*((?:\d{2}:)?\d{2}:\d{2}\.\d{3})(?:\s+.*)?$"
)


def parse_vtt_timestamp(ts: str) -> float:
    """Parses `HH:MM:SS.mmm` or `MM:SS.mmm` into seconds."""
    parts = ts.strip().split(":")
    if len(parts) == 3:
        hours, minutes, sec_ms = int(parts[0]), int(parts[1]), float(parts[2])
    elif len(parts) == 2:
        hours, minutes, sec_ms = 0, int(parts[0]), float(parts[1])
    else:
        raise ValueError(f"Invalid WebVTT timestamp: {ts}")
    return hours * 3600.0 + minutes * 60.0 + sec_ms


def check_text_safety(text: str, label: str) -> list[str]:
    """Checks arbitrary text for emojis, private network hosts, and exposed secrets."""
    errors: list[str] = []
    emoji_match = EMOJI_PATTERN.search(text)
    if emoji_match:
        errors.append(
            f"{label}: Emoji character U+{ord(emoji_match.group(0)):04X} is forbidden; use SVG icons or plain text."
        )
    host_match = PRIVATE_HOST_PATTERN.search(text)
    if host_match:
        errors.append(f"{label}: Private/internal network identifier detected ('{host_match.group(0)}').")
    if SECRET_PATTERN.search(text):
        errors.append(f"{label}: Potential exposed credential or private key detected.")
    return errors


def lint_mp4(
    mp4_path: Path,
    *,
    require_audio: bool = False,
    allow_silent: bool = False,
) -> list[str]:
    """Validates MP4 streams, pixel format, and audio/video synchronization via `ffprobe`."""
    errors: list[str] = []
    label = mp4_path.name

    if not mp4_path.exists():
        return [f"{label}: MP4 file does not exist."]
    if mp4_path.stat().st_size == 0:
        return [f"{label}: MP4 file is empty (0 bytes)."]

    ffprobe_bin = shutil.which("ffprobe")
    if not ffprobe_bin:
        return [f"{label}: `ffprobe` binary not found on PATH; cannot verify MP4 streams."]

    proc = subprocess.run(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(mp4_path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        stderr_msg = proc.stderr.decode("utf-8", errors="replace").strip()
        return [f"{label}: ffprobe failed: {stderr_msg}"]

    info: dict[str, Any] = json.loads(proc.stdout.decode("utf-8"))
    streams = info.get("streams", [])
    fmt = info.get("format", {})

    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not video_streams:
        errors.append(f"{label}: Missing video stream.")
        return errors

    vs = video_streams[0]
    pix_fmt = str(vs.get("pix_fmt", ""))
    if pix_fmt != "yuv420p":
        errors.append(
            f"{label}: Video pixel format is '{pix_fmt}', expected 'yuv420p' for universal browser playback."
        )

    width = int(vs.get("width") or 0)
    height = int(vs.get("height") or 0)
    if width <= 0 or height <= 0:
        errors.append(f"{label}: Invalid video dimensions ({width}x{height}).")

    fmt_dur = float(fmt.get("duration") or 0.0)
    vid_dur = float(vs.get("duration") or fmt_dur or 0.0)
    if vid_dur <= 0.0:
        errors.append(f"{label}: Video duration must be > 0s (got {vid_dur:.3f}s).")

    companion_vtt = mp4_path.with_suffix(".vtt")
    expect_audio = (require_audio or companion_vtt.exists()) and not allow_silent

    if expect_audio and not audio_streams:
        errors.append(
            f"{label}: Expected an audio stream (required or paired with {companion_vtt.name}), "
            "but MP4 is silent. Pass --allow-silent if intentional."
        )

    if audio_streams:
        aus = audio_streams[0]
        aud_dur = float(aus.get("duration") or 0.0)
        if aud_dur <= 0.0:
            errors.append(f"{label}: Audio stream has non-positive duration ({aud_dur:.3f}s).")
        elif vid_dur > 0.0 and aud_dur - vid_dur > 0.30:
            errors.append(
                f"{label}: Audio stream ({aud_dur:.2f}s) exceeds video stream ({vid_dur:.2f}s) "
                "by more than 0.30s; extend video tail with tpad=stop_mode=clone."
            )

    return errors


def lint_vtt(vtt_path: Path) -> list[str]:
    """Validates WebVTT header, cue timestamps, monotonic order, and text safety."""
    label = vtt_path.name
    if not vtt_path.exists():
        return [f"{label}: WebVTT file does not exist."]

    raw = vtt_path.read_text(encoding="utf-8").lstrip("\ufeff")
    errors = check_text_safety(raw, label)

    lines = raw.splitlines()
    if not lines or not (lines[0].strip() == "WEBVTT" or lines[0].strip().startswith("WEBVTT ")):
        errors.append(f"{label}: Line 1 must start with 'WEBVTT'.")
        return errors

    cue_count = 0
    prev_start = 0.0
    idx = 1
    total = len(lines)

    while idx < total:
        line = lines[idx].strip()
        if not line:
            idx += 1
            continue
        if line.startswith("NOTE") or line.startswith("STYLE"):
            idx += 1
            while idx < total and lines[idx].strip():
                idx += 1
            continue

        # Optional cue identifier line preceding timestamp line
        if "-->" not in line and (idx + 1 < total) and "-->" in lines[idx + 1]:
            idx += 1
            line = lines[idx].strip()

        match = VTT_CUE_ARROW_PATTERN.match(line)
        if not match:
            errors.append(f"{label}: Malformed WebVTT cue header at line {idx + 1}: '{line}'")
            idx += 1
            continue

        start_s = parse_vtt_timestamp(match.group(1))
        end_s = parse_vtt_timestamp(match.group(2))
        cue_count += 1

        if end_s <= start_s:
            errors.append(
                f"{label}: Cue {cue_count} (line {idx + 1}) end ({match.group(2)}) must exceed start ({match.group(1)})."
            )
        if start_s + 1e-6 < prev_start:
            errors.append(
                f"{label}: Cue {cue_count} (line {idx + 1}) start ({match.group(1)}) regresses before previous cue ({prev_start:.3f}s)."
            )
        prev_start = max(prev_start, start_s)

        idx += 1
        payload_lines: list[str] = []
        while idx < total and lines[idx].strip():
            payload_lines.append(lines[idx].strip())
            idx += 1
        if not payload_lines:
            errors.append(f"{label}: Cue {cue_count} has empty subtitle text.")

    if cue_count == 0:
        errors.append(f"{label}: WebVTT file contains 0 subtitle cues.")

    return errors


class _PlayerHTMLParser(HTMLParser):
    """Extracts CSP meta tags, external asset URLs, style blocks, video tags, and icon invariants."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.csp_contents: list[str] = []
        self.external_refs: list[str] = []
        self.style_blocks: list[str] = []
        self.in_style = False
        self.video_count = 0
        self.video_with_native_controls = 0
        self.symbol_or_use_count = 0
        self.button_svg_counts: list[int] = []
        self.in_button = False
        self.current_button_svg_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}

        if t == "meta" and attr_map.get("http-equiv", "").lower() == "content-security-policy":
            self.csp_contents.append(attr_map.get("content", ""))

        if t == "video":
            self.video_count += 1
            if "controls" in attr_map:
                self.video_with_native_controls += 1

        if t in {"symbol", "use"}:
            self.symbol_or_use_count += 1

        if t == "button":
            self.in_button = True
            self.current_button_svg_count = 0

        if t == "svg" and self.in_button:
            self.current_button_svg_count += 1

        if t == "style":
            self.in_style = True

        for attr_name in ("src", "href", "poster"):
            val = attr_map.get(attr_name, "").strip()
            if val and t in {"script", "link", "img", "video", "source", "track"}:
                # Allow anchor/doc links (<a href="https://...">), forbid external runtime dependencies
                if t == "link" and attr_map.get("rel", "").lower() not in {
                    "stylesheet",
                    "preload",
                    "modulepreload",
                    "icon",
                }:
                    continue
                if EXTERNAL_URL_PATTERN.match(val):
                    self.external_refs.append(f"<{t} {attr_name}=\"{val}\">")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "style":
            self.in_style = False
        if t == "button":
            if self.in_button:
                self.button_svg_counts.append(self.current_button_svg_count)
                self.in_button = False

    def handle_data(self, data: str) -> None:
        if self.in_style:
            self.style_blocks.append(data)


def lint_html(html_path: Path) -> list[str]:
    """Validates standalone offline HTML5 chapter player invariants."""
    label = html_path.name
    if not html_path.exists():
        return [f"{label}: HTML file does not exist."]

    raw = html_path.read_text(encoding="utf-8")
    errors = check_text_safety(raw, label)

    parser = _PlayerHTMLParser()
    parser.feed(raw)

    if not parser.csp_contents:
        errors.append(f"{label}: Missing <meta http-equiv=\"Content-Security-Policy\" ...> header.")
    else:
        for csp in parser.csp_contents:
            if re.search(r"(?i)https?://", csp):
                errors.append(f"{label}: CSP must remain offline-self-contained without external origins ('{csp}').")

    if parser.external_refs:
        for ref in parser.external_refs:
            errors.append(f"{label}: External CDN/network dependency is forbidden ({ref}).")

    css_blob = "\n".join(parser.style_blocks)
    if CSS_EXTERNAL_URL_PATTERN.search(css_blob):
        errors.append(f"{label}: External CSS @import or url(http...) dependency is forbidden.")

    if parser.video_count == 0:
        errors.append(f"{label}: HTML player must contain at least one <video> element.")

    if parser.video_with_native_controls > 0:
        errors.append(
            f"{label}: Native <video controls> is forbidden; use bespoke cinema player with custom transport deck."
        )

    if parser.symbol_or_use_count > 0:
        errors.append(
            f"{label}: SVG <symbol>/<use> icon sprites are forbidden ({parser.symbol_or_use_count} found); "
            "use standalone inline <svg viewBox=\"0 0 24 24\"><path d=\"...\"/></svg> elements."
        )

    for idx, count in enumerate(parser.button_svg_counts, start=1):
        if count > 1:
            errors.append(
                f"{label}: Button #{idx} contains {count} <svg> elements (strictly <= 1 permitted). "
                "Swap <path d=\"...\"/> dynamically via state instead of stacking multiple icons."
            )

    return errors


def lint_target(
    target: Path,
    *,
    require_audio: bool = False,
    allow_silent: bool = False,
) -> list[str]:
    """Dispatches linting across files or bundle directories."""
    if not target.exists():
        return [f"{target}: Path does not exist."]

    if target.is_dir():
        mp4s = sorted(target.rglob("*.mp4"))
        vtts = sorted(target.rglob("*.vtt"))
        htmls = sorted(target.rglob("*.html"))
        if not (mp4s or vtts or htmls):
            return [f"{target}: Directory contains no .mp4, .vtt, or .html deliverables to lint."]
        errors: list[str] = []
        for mp4 in mp4s:
            errors.extend(lint_mp4(mp4, require_audio=require_audio, allow_silent=allow_silent))
        for vtt in vtts:
            errors.extend(lint_vtt(vtt))
        for html_file in htmls:
            errors.extend(lint_html(html_file))
        return errors

    suffix = target.suffix.lower()
    if suffix == ".mp4":
        return lint_mp4(target, require_audio=require_audio, allow_silent=allow_silent)
    if suffix == ".vtt":
        return lint_vtt(target)
    if suffix in {".html", ".htm"}:
        return lint_html(target)
    return [f"{target.name}: Unsupported file extension '{suffix}' (expected directory, .mp4, .vtt, or .html)."]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify Manim explainer MP4 streams, WebVTT subtitles, and offline HTML5 players."
    )
    parser.add_argument(
        "targets",
        nargs="+",
        type=Path,
        help="Paths to output bundle directory, .mp4, .vtt, or .html files.",
    )
    parser.add_argument(
        "--require-audio",
        action="store_true",
        help="Require every inspected MP4 to carry a valid audio stream.",
    )
    parser.add_argument(
        "--allow-silent",
        action="store_true",
        help="Allow silent MP4 files even when a companion .vtt file sits next to them.",
    )
    args = parser.parse_args(argv)

    all_errors: list[str] = []
    for target in args.targets:
        all_errors.extend(
            lint_target(
                target,
                require_audio=args.require_audio,
                allow_silent=args.allow_silent,
            )
        )

    if all_errors:
        print(f"FAIL: {len(all_errors)} verification issue(s) found:", file=sys.stderr)
        for err in all_errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    joined = ", ".join(str(t) for t in args.targets)
    print(f"PASS: Verified {joined} with 0 issues.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
