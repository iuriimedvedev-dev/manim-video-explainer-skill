"""Nord palette tokens, safe-frame layout helpers, LaTeX-free primitives, and BeatTimer.

Designed for 1080p60 (16:9) Manim Community scenes without `pdflatex` / `MacTeX`.
Palette constants and `BeatTimer` use only the Python standard library; Manim
mobject builders lazily import `manim` when called inside a scene script.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# Nord Polar Night (surfaces & dark backgrounds)
NORD0 = "#2E3440"
NORD1 = "#3B4252"
NORD2 = "#434C5E"
NORD3 = "#4C566A"

# Nord Snow Storm (high-contrast typography)
NORD4 = "#D8DEE9"
NORD5 = "#E5E9F0"
NORD6 = "#ECEFF4"

# Nord Frost (primary data flow, active highlights, links)
NORD7 = "#8FBCBB"
NORD8 = "#88C0D0"
NORD9 = "#81A1C1"
NORD10 = "#5E81AC"

# Nord Aurora (semantic status signals)
NORD11 = "#BF616A"  # Error / Rejected / Circuit Open
NORD12 = "#D08770"  # Retry / Backpressure
NORD13 = "#EBCB8B"  # Warning / Cache Miss / Degraded
NORD14 = "#A3BE8C"  # Healthy / Quorum Ack / Cache Hit
NORD15 = "#B48EAD"  # Control Plane / Async Event

# Semantic aliases
BG_DARK = NORD0
SURFACE_CARD = NORD1
SURFACE_ELEVATED = NORD2
BORDER_SUBTLE = NORD3
TEXT_MUTED = NORD4
TEXT_PRIMARY = NORD6
ACCENT_TEAL = NORD7
ACCENT_CYAN = NORD8
ACCENT_STEEL = NORD9
ACCENT_BLUE = NORD10
STATUS_ERR = NORD11
STATUS_RETRY = NORD12
STATUS_WARN = NORD13
STATUS_OK = NORD14
ACCENT_PURPLE = NORD15

# 16:9 Manim frame is [-7.11, 7.11] x [-4.0, 4.0].
# Author inside SAFE limits so nothing clips and WebVTT captions stay unobstructed.
SAFE_X_LIMIT = 6.6
SAFE_Y_LIMIT = 3.5
HARD_X_LIMIT = 6.8
HARD_Y_LIMIT = 3.75
CAPTION_SAFE_BOTTOM_Y = -3.05


class BeatTimer:
    """Synchronizes Manim `scene.wait(...)` calls with `beat_timings.json` durations.

    When `timings_path` is None or does not exist yet (e.g. silent render or layout
    dry-run), falls back cleanly to each call's `fallback_s` value.
    """

    def __init__(self, timings_path: str | Path | None = None) -> None:
        self.beats: dict[str, dict[str, Any]] = {}
        if timings_path is not None:
            path = Path(timings_path)
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.beats = dict(payload.get("beats") or {})

    @classmethod
    def from_file(cls, timings_path: str | Path | None) -> "BeatTimer":
        return cls(timings_path=timings_path)

    def slot_duration(self, beat_id: str, fallback_s: float = 2.0) -> float:
        """Returns total recommended visual hold duration for `beat_id`."""
        entry = self.beats.get(beat_id)
        if not entry:
            return float(fallback_s)
        return float(entry.get("recommended_wait_s", fallback_s))

    def remaining_wait(
        self,
        beat_id: str,
        *,
        consumed_anim_s: float = 0.0,
        fallback_s: float = 2.0,
        min_hold_s: float = 0.15,
    ) -> float:
        """Returns remaining `scene.wait(...)` seconds after subtracting `consumed_anim_s`."""
        target_s = self.slot_duration(beat_id, fallback_s=fallback_s)
        return round(max(min_hold_s, target_s - max(0.0, consumed_anim_s)), 4)

    def hold(
        self,
        scene: Any,
        beat_id: str,
        *,
        consumed_anim_s: float = 0.0,
        fallback_s: float = 2.0,
        min_hold_s: float = 0.15,
        checkpoints: list[dict[str, Any]] | None = None,
    ) -> float:
        """Optionally records a layout checkpoint, waits the remaining beat duration, and returns wait seconds."""
        if checkpoints is not None:
            from check_scene_bounds import record_checkpoint

            record_checkpoint(scene, beat_id, checkpoints)
        wait_s = self.remaining_wait(
            beat_id,
            consumed_anim_s=consumed_anim_s,
            fallback_s=fallback_s,
            min_hold_s=min_hold_s,
        )
        scene.wait(wait_s)
        return wait_s


def _require_manim() -> Any:
    try:
        import manim  # type: ignore

        return manim
    except ImportError as exc:
        raise RuntimeError(
            "Manim Community (`manim`) is required to construct scene mobjects. "
            "Install it inside an isolated virtualenv (`uv pip install manim`)."
        ) from exc


def safe_fit_text(mobj: Any, max_width: float, max_height: float | None = None) -> Any:
    """Scales `mobj` down proportionally if it exceeds `max_width` or `max_height`."""
    if getattr(mobj, "width", 0.0) > max_width and max_width > 0:
        mobj.scale_to_fit_width(max_width)
    if max_height is not None and getattr(mobj, "height", 0.0) > max_height and max_height > 0:
        mobj.scale_to_fit_height(max_height)
    return mobj


def make_header_banner(
    act_kicker: str,
    title: str,
    *,
    accent: str = ACCENT_CYAN,
    font: str = "Sans",
) -> Any:
    """Builds a top-anchored act kicker + title header within the safe upper band."""
    m = _require_manim()
    kicker_txt = safe_fit_text(
        m.Text(act_kicker.upper(), font=font, font_size=16, weight=m.BOLD, color=accent),
        max_width=12.4,
    )
    title_txt = safe_fit_text(
        m.Text(title, font=font, font_size=28, weight=m.BOLD, color=TEXT_PRIMARY),
        max_width=12.4,
    )
    header = m.VGroup(kicker_txt, title_txt).arrange(m.DOWN, buff=0.10, aligned_edge=m.LEFT)
    header.to_edge(m.UP, buff=0.35).to_edge(m.LEFT, buff=0.65)
    return header


def make_node_card(
    title: str,
    subtitle: str = "",
    *,
    width: float = 2.75,
    height: float = 1.25,
    accent: str = ACCENT_CYAN,
    fill: str = SURFACE_CARD,
    font: str = "Sans",
) -> Any:
    """Creates a Nord-styled architecture node card without LaTeX dependencies."""
    m = _require_manim()
    box = m.RoundedRectangle(
        corner_radius=0.14,
        width=width,
        height=height,
        fill_color=fill,
        fill_opacity=0.96,
        stroke_color=accent,
        stroke_width=2.4,
    )
    inner_max_w = max(0.4, width - 0.34)
    title_mobj = safe_fit_text(
        m.Text(title, font=font, font_size=21, weight=m.BOLD, color=TEXT_PRIMARY),
        max_width=inner_max_w,
    )

    if subtitle:
        sub_mobj = safe_fit_text(
            m.Text(subtitle, font=font, font_size=15, color=TEXT_MUTED),
            max_width=inner_max_w,
        )
        labels = m.VGroup(title_mobj, sub_mobj).arrange(m.DOWN, buff=0.11)
    else:
        labels = m.VGroup(title_mobj)

    safe_fit_text(labels, max_width=inner_max_w, max_height=max(0.3, height - 0.28))
    labels.move_to(box.get_center())
    card = m.VGroup(box, labels)
    card.name = f"Card:{title}"
    return card


def make_badge(
    label: str,
    *,
    color: str = STATUS_OK,
    text_color: str = NORD0,
    font: str = "Sans",
) -> Any:
    """Creates a compact status pill badge (e.g. 'CACHE HIT', '429 REJECTED', 'QUORUM ACK')."""
    m = _require_manim()
    txt = safe_fit_text(
        m.Text(label, font=font, font_size=14, weight=m.BOLD, color=text_color),
        max_width=3.6,
    )
    pill = m.RoundedRectangle(
        corner_radius=0.12,
        width=txt.width + 0.34,
        height=txt.height + 0.20,
        fill_color=color,
        fill_opacity=1.0,
        stroke_width=0.0,
    )
    txt.move_to(pill.get_center())
    badge = m.VGroup(pill, txt)
    badge.name = f"Badge:{label}"
    return badge


def make_edge_arrow(
    source_mobj: Any,
    target_mobj: Any,
    *,
    color: str = BORDER_SUBTLE,
    dashed: bool = False,
    buff: float = 0.14,
) -> Any:
    """Connects two cards horizontally or vertically with solid or dashed arrow styling."""
    m = _require_manim()
    src_center = source_mobj.get_center()
    dst_center = target_mobj.get_center()
    dx = dst_center[0] - src_center[0]
    dy = dst_center[1] - src_center[1]

    if abs(dx) >= abs(dy):
        start = source_mobj.get_right() if dx >= 0 else source_mobj.get_left()
        end = target_mobj.get_left() if dx >= 0 else target_mobj.get_right()
    else:
        start = source_mobj.get_top() if dy >= 0 else source_mobj.get_bottom()
        end = target_mobj.get_bottom() if dy >= 0 else target_mobj.get_top()

    arrow = m.Arrow(
        start=start,
        end=end,
        buff=buff,
        stroke_width=2.8,
        max_tip_length_to_length_ratio=0.14,
        color=color,
    )
    if dashed:
        return m.DashedVMobject(arrow, num_dashes=12)
    return arrow


def animate_packet(
    scene: Any,
    start_point: Any,
    end_point: Any,
    *,
    label: str = "",
    color: str = ACCENT_CYAN,
    run_time: float = 0.75,
    font: str = "Sans",
) -> float:
    """Animates a glowing request/event packet along a path and cleans it up cleanly.

    Returns total animation seconds consumed (`run_time + 0.20`) for `BeatTimer`.
    """
    m = _require_manim()
    path = m.Line(start_point, end_point)
    core = m.Dot(point=start_point, radius=0.085, color=color)
    halo = m.Circle(radius=0.17, stroke_color=color, stroke_width=2.0, fill_color=color, fill_opacity=0.22)
    halo.move_to(start_point)

    if label:
        tag = safe_fit_text(
            m.Text(label, font=font, font_size=13, weight=m.BOLD, color=color),
            max_width=2.2,
        )
        tag.next_to(halo, m.UP, buff=0.08)
        packet = m.VGroup(halo, core, tag)
        updater = lambda mob: mob[2].next_to(mob[0], m.UP, buff=0.08)
        packet.add_updater(updater)
    else:
        packet = m.VGroup(halo, core)
        updater = None

    scene.add(packet)
    if updater is not None:
        scene.play(
            m.MoveAlongPath(halo, path),
            m.MoveAlongPath(core, path),
            run_time=run_time,
            rate_func=m.smooth,
        )
        packet.remove_updater(updater)
    else:
        scene.play(m.MoveAlongPath(packet, path), run_time=run_time, rate_func=m.smooth)

    scene.play(m.FadeOut(packet), run_time=0.20)
    return round(run_time + 0.20, 4)


def animate_blocked_pulse(
    scene: Any,
    target_mobj: Any,
    *,
    reason_label: str = "429 RATE LIMITED",
    color: str = STATUS_ERR,
    run_time: float = 0.95,
    font: str = "Sans",
) -> float:
    """Flashes an Aurora alert ring around `target_mobj` with a transient reason badge.

    Automatically fades the transient callout out so subsequent beats never overlap it.
    Returns total animation seconds consumed for `BeatTimer`.
    """
    m = _require_manim()
    ring = m.SurroundingRectangle(
        target_mobj,
        buff=0.10,
        corner_radius=0.16,
        color=color,
        stroke_width=3.5,
    )
    badge = make_badge(reason_label, color=color, text_color=TEXT_PRIMARY, font=font)
    badge.next_to(target_mobj, m.UP, buff=0.20)

    scene.play(m.Create(ring), m.FadeIn(badge, shift=m.UP * 0.08), run_time=run_time * 0.45)
    scene.play(ring.animate.scale(1.05).set_stroke(opacity=0.35), run_time=run_time * 0.30)
    scene.play(m.FadeOut(ring), m.FadeOut(badge), run_time=run_time * 0.25)
    return round(run_time, 4)
