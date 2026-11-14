"""End-to-end unit and integration tests for manim-video-explainer bundled tools."""

from __future__ import annotations

from array import array
import http.server
import io
import json
import math
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import threading
from typing import Any
import wave

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))
sys.path.insert(0, str(SKILL_DIR / "assets"))

import check_scene_bounds  # noqa: E402
import lint  # noqa: E402
import mux_narration  # noqa: E402
from nord_manim_theme import BeatTimer, assert_all_orthogonal  # noqa: E402


def _make_sine_wav(path: Path, duration_s: float, sample_rate: int = 22050, freq_hz: float = 440.0) -> None:
    """Writes a deterministic 16-bit mono PCM sine-wave WAV file."""
    n_samples = int(round(duration_s * sample_rate))
    samples = array(
        "h",
        (
            int(12000 * math.sin(2.0 * math.pi * freq_hz * idx / sample_rate))
            for idx in range(n_samples)
        ),
    )
    mux_narration.write_pcm16_mono(path, samples, sample_rate)


def _make_test_mp4(path: Path, duration_s: float = 2.0, pix_fmt: str = "yuv420p") -> None:
    """Generates a tiny synthetic H.264 MP4 via ffmpeg lavfi color source."""
    ffmpeg_bin = shutil.which("ffmpeg")
    assert ffmpeg_bin is not None, "ffmpeg must be installed to run MP4 integration tests"
    cmd = [
        ffmpeg_bin,
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x2E3440:s=320x180:r=30:d={duration_s}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        pix_fmt,
        str(path),
    ]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    assert proc.returncode == 0
    assert path.exists() and path.stat().st_size > 0


# ---------------------------------------------------------------------------
# 1. check_scene_bounds.py tests
# ---------------------------------------------------------------------------


def test_scene_bounds_valid_card_and_labels(tmp_path: Path) -> None:
    payload = {
        "checkpoints": [
            {
                "beat_id": "act1_ingress",
                "items": [
                    {
                        "id": "GatewayCard",
                        "kind": "card",
                        "left": -5.8,
                        "right": -3.2,
                        "bottom": -0.6,
                        "top": 0.6,
                        "opacity": 1.0,
                    },
                    {
                        "id": "GatewayTitle",
                        "kind": "text",
                        "left": -5.4,
                        "right": -3.6,
                        "bottom": 0.05,
                        "top": 0.40,
                        "opacity": 1.0,
                    },
                    {
                        "id": "GatewaySubtitle",
                        "kind": "text",
                        "left": -5.4,
                        "right": -3.6,
                        "bottom": -0.40,
                        "top": -0.10,
                        "opacity": 1.0,
                    },
                    {
                        "id": "HiddenOffscreenHelper",
                        "kind": "text",
                        "left": -9.5,
                        "right": -8.0,
                        "bottom": 4.2,
                        "top": 4.8,
                        "opacity": 0.0,
                    },
                ],
            }
        ]
    }
    json_path = tmp_path / "valid_bounds.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    assert check_scene_bounds.main(["--json", str(json_path)]) == 0


def test_scene_bounds_detects_overflow_and_collision(tmp_path: Path) -> None:
    payload = {
        "checkpoints": [
            {
                "beat_id": "act2_collision",
                "items": [
                    {
                        "id": "OverflowCard",
                        "kind": "card",
                        "left": 4.8,
                        "right": 7.15,
                        "bottom": -3.95,
                        "top": -2.5,
                        "opacity": 1.0,
                    },
                    {
                        "id": "BadgeA",
                        "kind": "text",
                        "left": -1.0,
                        "right": 1.0,
                        "bottom": 1.2,
                        "top": 1.6,
                        "opacity": 1.0,
                    },
                    {
                        "id": "BadgeB",
                        "kind": "text",
                        "left": -0.3,
                        "right": 1.4,
                        "bottom": 1.3,
                        "top": 1.7,
                        "opacity": 1.0,
                    },
                ],
            }
        ]
    }
    json_path = tmp_path / "invalid_bounds.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")

    errors = check_scene_bounds.validate_checkpoint_payload(payload)
    assert any("Overflow RIGHT" in err for err in errors)
    assert any("Overflow BOTTOM" in err for err in errors)
    assert any("Text collision" in err for err in errors)
    assert check_scene_bounds.main(["--json", str(json_path)]) == 1


def test_extract_scene_bboxes_with_mock_mobjects() -> None:
    class FakeText:
        def __init__(self, text: str, x0: float, x1: float, y0: float, y1: float) -> None:
            self.text = text
            self._box = (x0, x1, y0, y1)
            # Simulate glyph children that should NOT be traversed separately
            self.submobjects = [object(), object()]

        def get_left(self) -> list[float]:
            return [self._box[0], 0.0, 0.0]

        def get_right(self) -> list[float]:
            return [self._box[1], 0.0, 0.0]

        def get_bottom(self) -> list[float]:
            return [0.0, self._box[2], 0.0]

        def get_top(self) -> list[float]:
            return [0.0, self._box[3], 0.0]

        def get_fill_opacity(self) -> float:
            return 1.0

    class FakeScene:
        mobjects = [
            FakeText("Token-Bucket Limiter", -2.0, 0.5, 0.2, 0.6),
            FakeText("Read-Through Cache", 1.2, 3.6, 0.2, 0.6),
        ]

    snap = check_scene_bounds.extract_scene_bboxes(FakeScene(), beat_id="mock_beat")
    assert snap["beat_id"] == "mock_beat"
    assert len(snap["items"]) == 2
    assert check_scene_bounds.validate_bboxes(snap["items"]) == []


# ---------------------------------------------------------------------------
# 2. BeatTimer & mux_narration.py tests
# ---------------------------------------------------------------------------


def test_mux_narration_phonetics_tail_clone_pad_and_beat_timer(tmp_path: Path) -> None:
    # Build a tiny helper script to act as a deterministic offline TTS CLI (--tts-cmd).
    # It writesspoken text to a log file and generates a 1.6s WAV per beat.
    spoken_log = tmp_path / "spoken_inputs.jsonl"
    synth_script = tmp_path / "fake_tts.py"
    synth_script.write_text(
        "import json, math, struct, sys, wave\n"
        "from pathlib import Path\n"
        "out_wav = Path(sys.argv[1])\n"
        "log_file = Path(sys.argv[2])\n"
        "spoken = sys.stdin.read().strip()\n"
        "with log_file.open('a', encoding='utf-8') as f:\n"
        "    f.write(json.dumps({'wav': out_wav.name, 'spoken': spoken}) + '\\n')\n"
        "rate = 22050\n"
        "frames = b''.join(struct.pack('<h', int(9000 * math.sin(2*math.pi*330*i/rate))) for i in range(int(rate * 1.6)))\n"
        "with wave.open(str(out_wav), 'wb') as wf:\n"
        "    wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(rate); wf.writeframes(frames)\n",
        encoding="utf-8",
    )

    manifest = {
        "lead_in_s": 0.20,
        "inter_beat_gap_s": 0.25,
        "tail_pad_s": 0.30,
        "phonetic_glossary": {
            "WAL": "write-ahead log",
            "TTL": "T T L",
        },
        "scenes": [
            {
                "id": "act1",
                "beats": [
                    {
                        "id": "beat_cache",
                        "min_visual_s": 2.2,
                        "text": "Cold lookups acquire a single-flight lock before setting TTL.",
                    },
                    {
                        "id": "beat_quorum",
                        "min_visual_s": 1.4,
                        "text": "Primary storage commits only after WAL quorum succeeds.",
                    },
                    {
                        "id": "beat_reject",
                        "min_visual_s": 1.4,
                        "text": "Excess burst callers receive HTTP 429 immediately.",
                        "tts_phonetic_text": "Excess burst callers receive H T T P four twenty-nine immediately.",
                    },
                ],
            }
        ],
    }
    manifest_path = tmp_path / "narration.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    wav_dir = tmp_path / "beats_wav"
    timings_path = tmp_path / "beat_timings.json"
    tts_cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(synth_script))} {{wav}} {shlex.quote(str(spoken_log))}"

    # Pass 1: Synthesize per-beat WAVs & emit beat_timings.json
    rc_pass1 = mux_narration.main(
        [
            "--manifest",
            str(manifest_path),
            "--scene-id",
            "act1",
            "--wav-dir",
            str(wav_dir),
            "--tts-cmd",
            tts_cmd,
            "--emit-timings",
            str(timings_path),
        ]
    )
    assert rc_pass1 == 0
    assert timings_path.exists()

    # Verify phonetic glossary & tts_phonetic_text went to synthesizer
    logged = [json.loads(line) for line in spoken_log.read_text(encoding="utf-8").splitlines()]
    assert len(logged) == 3
    assert "T T L" in logged[0]["spoken"]
    assert "write-ahead log" in logged[1]["spoken"]
    assert "H T T P four twenty-nine" in logged[2]["spoken"]

    # Verify BeatTimer reads emitted timings accurately
    timer = BeatTimer.from_file(timings_path)
    assert timer.slot_duration("beat_cache") == pytest.approx(2.2, abs=0.02)
    assert timer.remaining_wait("beat_cache", consumed_anim_s=0.9) == pytest.approx(1.3, abs=0.02)
    assert timer.remaining_wait("unknown_beat", consumed_anim_s=0.5, fallback_s=2.0) == pytest.approx(1.5, abs=0.02)

    waited: list[float] = []

    class DummyScene:
        mobjects: list = []

        def wait(self, duration: float) -> None:
            waited.append(duration)

    checkpoints: list[dict] = []
    timer.hold(DummyScene(), "beat_quorum", consumed_anim_s=0.5, checkpoints=checkpoints)
    assert len(waited) == 1 and waited[0] > 1.2
    assert len(checkpoints) == 1 and checkpoints[0]["beat_id"] == "beat_quorum"

    # Pass 2: Intentionally feed a SHORT 2.0s video while 3 beats span ~6.2s.
    # mux_narration.py must extend the video tail with tpad=stop_mode=clone so 0 words clip!
    short_video = tmp_path / "raw_short.mp4"
    _make_test_mp4(short_video, duration_s=2.0, pix_fmt="yuv420p")

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    narrated_mp4 = bundle_dir / "act1_en.mp4"
    narrated_vtt = bundle_dir / "act1_en.vtt"

    rc_pass2 = mux_narration.main(
        [
            "--manifest",
            str(manifest_path),
            "--scene-id",
            "act1",
            "--wav-dir",
            str(wav_dir),
            "--video",
            str(short_video),
            "--output",
            str(narrated_mp4),
            "--vtt",
            str(narrated_vtt),
        ]
    )
    assert rc_pass2 == 0
    assert narrated_mp4.exists()
    assert narrated_vtt.exists()

    # Verify output video was extended past 5.8s via tpad=stop_mode=clone
    out_probe = mux_narration.probe_video_stream(narrated_mp4)
    assert out_probe["duration_s"] >= 5.8
    assert out_probe["pix_fmt"] == "yuv420p"

    # Verify WebVTT contains clean display text, NOT phonetic overrides
    vtt_text = narrated_vtt.read_text(encoding="utf-8")
    assert vtt_text.startswith("WEBVTT\n")
    assert "WAL quorum" in vtt_text
    assert "HTTP 429" in vtt_text
    assert "four twenty-nine" not in vtt_text

    # Copy HTML5 Cinema Player template into bundle_dir and run lint.py over entire bundle
    player_copy = bundle_dir / "index.html"
    shutil.copyfile(SKILL_DIR / "assets" / "video_player_template.html", player_copy)
    assert lint.main([str(bundle_dir), "--require-audio"]) == 0


def test_mux_narration_shifts_overlapping_explicit_start_times(tmp_path: Path) -> None:
    wav_dir = tmp_path / "wavs"
    wav_dir.mkdir()
    _make_sine_wav(wav_dir / "b1.wav", duration_s=1.8)
    _make_sine_wav(wav_dir / "b2.wav", duration_s=1.2)

    manifest = {
        "beats": [
            {"id": "b1", "start_s": 0.2, "text": "First transition acquires the distributed lease."},
            {"id": "b2", "start_s": 0.8, "text": "Second transition flushes the commit record."},
        ]
    }
    timings, buffers, rate = mux_narration.resolve_and_measure_beats(
        manifest,
        manifest_dir=tmp_path,
        wav_dir=wav_dir,
    )
    assert timings[0].start_s == pytest.approx(0.2, abs=0.01)
    assert timings[0].end_s == pytest.approx(2.0, abs=0.02)
    # b2 requested 0.8s (which would talk over b1), so it must shift past 2.0s
    assert timings[1].start_s >= timings[0].end_s
    master = mux_narration.assemble_master_track(timings, buffers, rate)
    assert len(master) >= int(timings[1].end_s * rate)


# ---------------------------------------------------------------------------
# 3. lint.py validation tests
# ---------------------------------------------------------------------------


def test_lint_shipped_html_player_template() -> None:
    template_path = SKILL_DIR / "assets" / "video_player_template.html"
    assert lint.lint_html(template_path) == []
    assert lint.main([str(template_path)]) == 0


def test_lint_catches_bad_mp4_pix_fmt_and_missing_audio(tmp_path: Path) -> None:
    bad_mp4 = tmp_path / "wrong_fmt.mp4"
    _make_test_mp4(bad_mp4, duration_s=1.0, pix_fmt="yuv444p")

    errs = lint.lint_mp4(bad_mp4, require_audio=True)
    assert any("yuv420p" in e for e in errs)
    assert any("silent" in e for e in errs)

    silent_ok = tmp_path / "silent_walkthrough.mp4"
    _make_test_mp4(silent_ok, duration_s=1.0, pix_fmt="yuv420p")
    assert lint.lint_mp4(silent_ok, require_audio=False, allow_silent=True) == []


def test_lint_catches_invalid_vtt_and_unsafe_html(tmp_path: Path) -> None:
    bad_vtt = tmp_path / "broken.vtt"
    bad_vtt.write_text(
        "NOT_WEBVTT\n\n1\n00:00:04.000 --> 00:00:02.000\nInverted timestamps\n",
        encoding="utf-8",
    )
    assert any("WEBVTT" in e for e in lint.lint_vtt(bad_vtt))

    inverted_vtt = tmp_path / "inverted.vtt"
    inverted_vtt.write_text(
        "WEBVTT\n\n1\n00:00:05.000 --> 00:00:03.000\nCue ends before start\n",
        encoding="utf-8",
    )
    assert any("must exceed start" in e for e in lint.lint_vtt(inverted_vtt))

    unsafe_html = tmp_path / "unsafe_player.html"
    unsafe_html.write_text(
        "<!doctype html><html><head>"
        "<script src=\"https://cdn.example.com/lib.js\"></script>"
        "</head><body><p>Missing video tag, missing CSP, external CDN, emoji \U0001f680, "
        "and internal host db.prod.internal</p></body></html>",
        encoding="utf-8",
    )
    html_errs = lint.lint_html(unsafe_html)
    assert any("Content-Security-Policy" in e for e in html_errs)
    assert any("External CDN" in e for e in html_errs)
    assert any("Emoji" in e for e in html_errs)
    assert any("<video>" in e for e in html_errs)
    assert any("Private/internal" in e for e in html_errs)


# ---------------------------------------------------------------------------
# 4. Orthogonal schematic connector tests (pure stdlib, zero Manim required)
# ---------------------------------------------------------------------------


class _FakeWire:
    def __init__(self, wire_points: list[tuple[float, float]]) -> None:
        self.wire_points = wire_points


def test_assert_all_orthogonal_accepts_hv_and_elbows_and_rejects_diagonals() -> None:
    cards = [
        ("gw", -5.75, -3.25, -0.42, 0.82),
        ("limiter", -2.75, -0.25, -0.42, 0.82),
        ("cache", 0.25, 2.75, -0.42, 0.82),
        ("dlq", 0.25, 2.75, -2.35, -1.15),
    ]
    valid_connectors = [
        _FakeWire([(-3.15, 0.20), (-2.85, 0.20)]),  # horizontal
        _FakeWire([(1.50, -0.52), (1.50, -1.05)]),  # vertical
        _FakeWire([(-1.50, -0.52), (-1.50, -1.75), (0.15, -1.75)]),  # 90-degree elbow
    ]
    assert_all_orthogonal(valid_connectors, cards)

    diagonal_connector = _FakeWire([(-3.15, 0.35), (-2.85, -0.10)])
    with pytest.raises(AssertionError, match="diagonal"):
        assert_all_orthogonal([diagonal_connector], cards)


def test_assert_all_orthogonal_rejects_wire_crossing_card_interior() -> None:
    cards = [
        ("gw", -5.5, -3.5, -0.5, 0.5),
        ("middle_blocker", -1.2, 1.2, -0.5, 0.5),
        ("db", 3.5, 5.5, -0.5, 0.5),
    ]
    # Horizontal wire from gw directly to db cuts straight through middle_blocker
    cutting_wire = _FakeWire([(-3.4, 0.0), (3.4, 0.0)])
    with pytest.raises(AssertionError, match="crosses interior"):
        assert_all_orthogonal([cutting_wire], cards)

    # Routing around middle_blocker via a 90-degree U-elbow passes cleanly
    routed_elbow = _FakeWire([(-3.4, 0.0), (-3.4, 1.2), (3.4, 1.2), (3.4, 0.0)])
    assert_all_orthogonal([routed_elbow], cards)


def test_connect_h_v_elbow_and_animate_packet_along_with_stub_manim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import types
    import nord_manim_theme

    class StubMobj:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.args = args
            self.kwargs = kwargs
            self.corners: list[list[float]] = []
            self.updaters: list[Any] = []
            self.width = 0.5
            self.height = 0.2

        def set_points_as_corners(self, pts: list[list[float]]) -> "StubMobj":
            self.corners = pts
            return self

        def move_to(self, pt: Any) -> "StubMobj":
            return self

        def next_to(self, target: Any, direction: Any, buff: float = 0.0) -> "StubMobj":
            return self

        def add_updater(self, fn: Any) -> None:
            self.updaters.append(fn)

        def remove_updater(self, fn: Any) -> None:
            if fn in self.updaters:
                self.updaters.remove(fn)

        def __getitem__(self, idx: int) -> "StubMobj":
            return self.args[idx]

    stub_manim = types.SimpleNamespace(
        Arrow=StubMobj,
        DoubleArrow=StubMobj,
        DashedVMobject=lambda mobj, num_dashes=12: StubMobj(mobj, num_dashes=num_dashes),
        VMobject=StubMobj,
        VGroup=StubMobj,
        Dot=StubMobj,
        Circle=StubMobj,
        Text=StubMobj,
        MoveAlongPath=lambda mob, path: ("MoveAlongPath", mob, path),
        FadeOut=lambda mob: ("FadeOut", mob),
        BOLD="BOLD",
        UP=[0.0, 1.0, 0.0],
        smooth=lambda t: t,
    )
    monkeypatch.setattr(nord_manim_theme, "_require_manim", lambda: stub_manim)

    card_a = ("gw", -5.5, -3.5, -0.6, 0.6)
    card_b = ("limiter", -2.0, 0.0, -0.4, 0.8)  # different height/center, overlapping Y
    card_c = ("storage", -2.0, 0.0, -2.6, -1.4)

    eh = nord_manim_theme.connect_h(card_a, card_b, buff=0.10)
    ev = nord_manim_theme.connect_v(card_b, card_c, buff=0.10, dashed=True)
    ee = nord_manim_theme.connect_elbow([(-3.4, 0.1), (-2.1, 0.1)], dashed=False)
    assert_all_orthogonal([eh, ev, ee], [card_a, card_b, card_c])

    # Verify connect_elbow rejects diagonal input immediately
    with pytest.raises(ValueError, match="diagonal"):
        nord_manim_theme.connect_elbow([(-3.0, 0.0), (-1.0, 0.5)])

    played: list[tuple[Any, ...]] = []

    class StubScene:
        def add(self, *mobs: Any) -> None:
            return

        def play(self, *anims: Any, **kwargs: Any) -> None:
            played.append((anims, kwargs))

    consumed = nord_manim_theme.animate_packet_along(StubScene(), eh, label="POST /v1", run_time=0.65)
    assert consumed == pytest.approx(0.85, abs=1e-4)
    assert len(played) == 2


# ---------------------------------------------------------------------------
# 5. OpenAI-compatible HTTP TTS (/v1/audio/speech) & EBU R128 loudnorm tests
# ---------------------------------------------------------------------------


def _build_wav_bytes(duration_s: float = 1.25, sample_rate: int = 24000, freq_hz: float = 360.0) -> bytes:
    n_samples = int(round(duration_s * sample_rate))
    samples = array(
        "h",
        (
            int(10000 * math.sin(2.0 * math.pi * freq_hz * idx / sample_rate))
            for idx in range(n_samples)
        ),
    )
    if sys.byteorder != "little":
        samples.byteswap()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


def test_synthesize_with_http_tts_and_loudnorm_muxing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_requests: list[dict[str, Any]] = []
    synthetic_wav = _build_wav_bytes(duration_s=1.35, sample_rate=24000)

    class MockSpeechHandler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length)
            captured_requests.append(
                {
                    "path": self.path,
                    "auth": self.headers.get("Authorization", ""),
                    "body": json.loads(raw_body.decode("utf-8")),
                }
            )
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(synthetic_wav)))
            self.end_headers()
            self.wfile.write(synthetic_wav)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), MockSpeechHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        monkeypatch.setenv("MOCK_TTS_API_TOKEN", "test-secret-token-xyz")
        base_v1_url = f"http://127.0.0.1:{port}/v1"

        # Direct unit call check (verifying /v1 -> /v1/audio/speech normalization too)
        direct_wav = tmp_path / "direct_http.wav"
        mux_narration.synthesize_with_http_tts(
            "Atomic quorum replication keeps writes durable.",
            direct_wav,
            url=base_v1_url,
            api_key_env="MOCK_TTS_API_TOKEN",
            model="gpt-4o-mini-tts",
            voice="marin",
            instructions="Warm, measured documentary cadence.",
        )
        assert direct_wav.exists() and direct_wav.stat().st_size > 1000
        assert captured_requests[0]["path"] == "/v1/audio/speech"
        assert captured_requests[0]["auth"] == "Bearer test-secret-token-xyz"
        assert captured_requests[0]["body"]["model"] == "gpt-4o-mini-tts"
        assert captured_requests[0]["body"]["voice"] == "marin"
        assert captured_requests[0]["body"]["response_format"] == "wav"
        assert captured_requests[0]["body"]["instructions"] == "Warm, measured documentary cadence."

        # End-to-end CLI check: Pass 1 HTTP synthesis + Pass 2 mux with --loudnorm-lufs -18.0
        manifest = {
            "lead_in_s": 0.15,
            "inter_beat_gap_s": 0.20,
            "phonetic_glossary": {"WAL": "write-ahead log"},
            "beats": [
                {
                    "id": "http_b1",
                    "min_visual_s": 1.8,
                    "text": "Leader commits only after WAL quorum acknowledges.",
                },
                {
                    "id": "http_b2",
                    "min_visual_s": 1.8,
                    "text": "Followers replay committed segments in strict offset order.",
                },
            ],
        }
        manifest_path = tmp_path / "http_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        wav_dir = tmp_path / "http_wavs"
        timings_out = tmp_path / "http_timings.json"
        raw_mp4 = tmp_path / "raw_input.mp4"
        _make_test_mp4(raw_mp4, duration_s=2.5, pix_fmt="yuv420p")
        out_mp4 = tmp_path / "narrated_loudnorm.mp4"

        rc = mux_narration.main(
            [
                "--manifest",
                str(manifest_path),
                "--wav-dir",
                str(wav_dir),
                "--http-tts-url",
                f"http://127.0.0.1:{port}/v1/audio/speech",
                "--http-tts-key-env",
                "MOCK_TTS_API_TOKEN",
                "--http-tts-model",
                "gpt-4o-mini-tts",
                "--http-tts-voice",
                "cedar",
                "--http-tts-instructions",
                "Calm technical narrator; crisp clause boundaries.",
                "--emit-timings",
                str(timings_out),
                "--video",
                str(raw_mp4),
                "--loudnorm-lufs",
                "-18.0",
                "--output",
                str(out_mp4),
            ]
        )
        assert rc == 0
        assert len(captured_requests) == 3
        assert "write-ahead log" in captured_requests[1]["body"]["input"]
        assert captured_requests[1]["body"]["voice"] == "cedar"
        assert out_mp4.exists()
        assert lint.lint_mp4(out_mp4, require_audio=True) == []
    finally:
        server.shutdown()
        server.server_close()


def test_synthesize_with_http_tts_requires_key_for_remote_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISSING_REMOTE_TTS_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MISSING_REMOTE_TTS_KEY"):
        mux_narration.synthesize_with_http_tts(
            "Unauthenticated remote call must fail before network.",
            tmp_path / "unused.wav",
            url="https://api.example.com/v1/audio/speech",
            api_key_env="MISSING_REMOTE_TTS_KEY",
        )
