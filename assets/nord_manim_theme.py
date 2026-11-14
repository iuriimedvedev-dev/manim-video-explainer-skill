"""Nord palette tokens, safe-frame layout helpers, LaTeX-free primitives, and BeatTimer.

Designed for 1080p60 (16:9) Manim Community scenes without `pdflatex` / `MacTeX`.
Palette constants and `BeatTimer` use only the Python standard library; Manim
mobject builders lazily import `manim` when called inside a scene script.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Sequence


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


def _extract_card_bbox(card: Any, index: int = 0) -> tuple[str, float, float, float, float]:
    """Extracts `(card_id, left, right, bottom, top)` in pure Python without requiring `manim`."""
    if isinstance(card, tuple):
        if len(card) == 4:
            return (f"card_{index}", float(card[0]), float(card[1]), float(card[2]), float(card[3]))
        if len(card) == 5:
            return (str(card[0]), float(card[1]), float(card[2]), float(card[3]), float(card[4]))
        raise ValueError(
            f"Card tuple at index {index} must have 4 `(left, right, bottom, top)` "
            f"or 5 `(id, left, right, bottom, top)` elements."
        )

    if isinstance(card, dict):
        cid = str(card.get("id", f"card_{index}"))
        return (
            cid,
            float(card["left"]),
            float(card["right"]),
            float(card["bottom"]),
            float(card["top"]),
        )

    if all(hasattr(card, attr) for attr in ("left", "right", "bottom", "top")):
        cid = str(getattr(card, "id", getattr(card, "name", f"card_{index}")))
        return (
            cid,
            float(card.left),
            float(card.right),
            float(card.bottom),
            float(card.top),
        )

    if all(callable(getattr(card, fn, None)) for fn in ("get_left", "get_right", "get_bottom", "get_top")):
        cid = str(getattr(card, "name", f"card_{index}"))
        return (
            cid,
            float(card.get_left()[0]),
            float(card.get_right()[0]),
            float(card.get_bottom()[1]),
            float(card.get_top()[1]),
        )

    raise TypeError(
        f"Unsupported card representation at index {index}: {type(card).__name__}. "
        "Pass a Manim Mobject, `(left, right, bottom, top)` tuple, or bbox dict."
    )


def _extract_wire_points(connector: Any, index: int = 0) -> list[tuple[float, float]]:
    """Extracts ordered 2D `(x, y)` waypoints from a connector or sequence in pure Python."""
    raw_pts: Any = None
    if hasattr(connector, "wire_points"):
        raw_pts = connector.wire_points
    elif isinstance(connector, dict) and "wire_points" in connector:
        raw_pts = connector["wire_points"]
    elif isinstance(connector, (list, tuple)):
        raw_pts = connector
    elif callable(getattr(connector, "get_start", None)) and callable(
        getattr(connector, "get_end", None)
    ):
        s = connector.get_start()
        e = connector.get_end()
        raw_pts = [(float(s[0]), float(s[1])), (float(e[0]), float(e[1]))]
    else:
        raise TypeError(
            f"Connector #{index} ({type(connector).__name__}) lacks `.wire_points`. "
            "Build edges with `connect_h`, `connect_v`, or `connect_elbow`."
        )

    pts = [(float(pt[0]), float(pt[1])) for pt in raw_pts]
    if len(pts) < 2:
        raise AssertionError(f"Connector #{index} must contain at least 2 waypoints, got {len(pts)}.")
    return pts


def assert_all_orthogonal(
    connectors: Iterable[Any],
    cards: Iterable[Any] = (),
    *,
    tol: float = 1e-3,
    interior_inset: float = 1e-2,
) -> None:
    """Pure-stdlib schematic verifier enforcing 100% horizontal/vertical/90-deg wires.

    Verifies two invariants without requiring Manim installed:
      1. Every segment `(x0, y0) -> (x1, y1)` in each connector's `.wire_points` is strictly
         horizontal (`|dy| <= tol`) or strictly vertical (`|dx| <= tol`) and non-degenerate.
      2. No wire segment cuts through the interior of any card bounding box in `cards`.
    """
    parsed_cards = [_extract_card_bbox(c, idx) for idx, c in enumerate(cards)]
    connector_list = list(connectors)

    for c_idx, conn in enumerate(connector_list):
        pts = _extract_wire_points(conn, c_idx)
        for s_idx in range(len(pts) - 1):
            x0, y0 = pts[s_idx]
            x1, y1 = pts[s_idx + 1]
            dx = abs(x1 - x0)
            dy = abs(y1 - y0)

            if dx <= tol and dy <= tol:
                raise AssertionError(
                    f"Connector #{c_idx} segment #{s_idx} is degenerate zero-length at ({x0:.3f}, {y0:.3f})."
                )
            if dx > tol and dy > tol:
                raise AssertionError(
                    f"Connector #{c_idx} segment #{s_idx} ({x0:.3f}, {y0:.3f}) -> ({x1:.3f}, {y1:.3f}) "
                    f"is diagonal (dx={dx:.3f}, dy={dy:.3f}). Never call Arrow(card_a, card_b) directly; "
                    "route via connect_h, connect_v, or connect_elbow."
                )

            is_horizontal = dy <= tol
            for cid, left, right, bottom, top in parsed_cards:
                ix0 = left + interior_inset
                ix1 = right - interior_inset
                iy0 = bottom + interior_inset
                iy1 = top - interior_inset
                if ix0 >= ix1 or iy0 >= iy1:
                    continue

                if is_horizontal:
                    y_mid = 0.5 * (y0 + y1)
                    sx0, sx1 = min(x0, x1), max(x0, x1)
                    if iy0 < y_mid < iy1 and max(sx0, ix0) < min(sx1, ix1):
                        raise AssertionError(
                            f"Connector #{c_idx} horizontal segment ({x0:.3f}, {y0:.3f}) -> "
                            f"({x1:.3f}, {y1:.3f}) crosses interior of card '{cid}' "
                            f"[{left:.2f}, {right:.2f}] x [{bottom:.2f}, {top:.2f}]."
                        )
                else:
                    x_mid = 0.5 * (x0 + x1)
                    sy0, sy1 = min(y0, y1), max(y0, y1)
                    if ix0 < x_mid < ix1 and max(sy0, iy0) < min(sy1, iy1):
                        raise AssertionError(
                            f"Connector #{c_idx} vertical segment ({x0:.3f}, {y0:.3f}) -> "
                            f"({x1:.3f}, {y1:.3f}) crosses interior of card '{cid}' "
                            f"[{left:.2f}, {right:.2f}] x [{bottom:.2f}, {top:.2f}]."
                        )


def connect_h(
    card_from: Any,
    card_to: Any,
    y: float | None = None,
    *,
    buff: float = 0.10,
    color: str = BORDER_SUBTLE,
    dashed: bool = False,
    stroke_width: float = 2.8,
    bidirectional: bool = False,
) -> Any:
    """Connects two cards with a strictly horizontal (`dy = 0`) port-anchored arrow."""
    m = _require_manim()
    _, left_a, right_a, bot_a, top_a = _extract_card_bbox(card_from, 0)
    _, left_b, right_b, bot_b, top_b = _extract_card_bbox(card_to, 1)

    if y is None:
        ov_low = max(bot_a, bot_b)
        ov_high = min(top_a, top_b)
        if ov_low > ov_high:
            raise ValueError(
                "connect_h requires cards with overlapping vertical spans or an explicit y=... port; "
                "use connect_elbow(...) to connect offset vertical tiers."
            )
        y_port = 0.5 * (ov_low + ov_high)
    else:
        y_port = float(y)

    cx_a = 0.5 * (left_a + right_a)
    cx_b = 0.5 * (left_b + right_b)
    if cx_b >= cx_a:
        x_start = right_a + buff
        x_end = left_b - buff
    else:
        x_start = left_a - buff
        x_end = right_b + buff

    start_pt = [x_start, y_port, 0.0]
    end_pt = [x_end, y_port, 0.0]
    arrow_cls = m.DoubleArrow if bidirectional else m.Arrow
    arrow = arrow_cls(
        start=start_pt,
        end=end_pt,
        buff=0.0,
        stroke_width=stroke_width,
        max_tip_length_to_length_ratio=0.14,
        color=color,
    )
    conn = m.DashedVMobject(arrow, num_dashes=12) if dashed else arrow
    conn.wire_points = [(round(x_start, 6), round(y_port, 6)), (round(x_end, 6), round(y_port, 6))]
    conn.endpoints = (card_from, card_to)
    return conn


def connect_v(
    card_from: Any,
    card_to: Any,
    x: float | None = None,
    *,
    buff: float = 0.10,
    color: str = BORDER_SUBTLE,
    dashed: bool = False,
    stroke_width: float = 2.8,
    bidirectional: bool = False,
) -> Any:
    """Connects two cards with a strictly vertical (`dx = 0`) port-anchored arrow."""
    m = _require_manim()
    _, left_a, right_a, bot_a, top_a = _extract_card_bbox(card_from, 0)
    _, left_b, right_b, bot_b, top_b = _extract_card_bbox(card_to, 1)

    if x is None:
        ov_left = max(left_a, left_b)
        ov_right = min(right_a, right_b)
        if ov_left > ov_right:
            raise ValueError(
                "connect_v requires cards with overlapping horizontal spans or an explicit x=... port; "
                "use connect_elbow(...) to connect offset horizontal columns."
            )
        x_port = 0.5 * (ov_left + ov_right)
    else:
        x_port = float(x)

    cy_a = 0.5 * (bot_a + top_a)
    cy_b = 0.5 * (bot_b + top_b)
    if cy_b >= cy_a:
        y_start = top_a + buff
        y_end = bot_b - buff
    else:
        y_start = bot_a - buff
        y_end = top_b + buff

    start_pt = [x_port, y_start, 0.0]
    end_pt = [x_port, y_end, 0.0]
    arrow_cls = m.DoubleArrow if bidirectional else m.Arrow
    arrow = arrow_cls(
        start=start_pt,
        end=end_pt,
        buff=0.0,
        stroke_width=stroke_width,
        max_tip_length_to_length_ratio=0.14,
        color=color,
    )
    conn = m.DashedVMobject(arrow, num_dashes=12) if dashed else arrow
    conn.wire_points = [(round(x_port, 6), round(y_start, 6)), (round(x_port, 6), round(y_end, 6))]
    conn.endpoints = (card_from, card_to)
    return conn


def connect_elbow(
    points: Sequence[Sequence[float]],
    *,
    color: str = BORDER_SUBTLE,
    dashed: bool = False,
    stroke_width: float = 2.8,
    tip_length: float = 0.18,
    tol: float = 1e-3,
) -> Any:
    """Connects an ordered sequence of `(x, y)` waypoints with 90-degree orthogonal segments."""
    pts_2d = [(float(pt[0]), float(pt[1])) for pt in points]
    if len(pts_2d) < 2:
        raise ValueError("connect_elbow requires at least 2 `(x, y)` waypoints.")

    for idx in range(len(pts_2d) - 1):
        x0, y0 = pts_2d[idx]
        x1, y1 = pts_2d[idx + 1]
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        if dx <= tol and dy <= tol:
            raise ValueError(f"connect_elbow segment #{idx} is zero-length at ({x0:.3f}, {y0:.3f}).")
        if dx > tol and dy > tol:
            raise ValueError(
                f"connect_elbow segment #{idx} ({x0:.3f}, {y0:.3f}) -> ({x1:.3f}, {y1:.3f}) "
                f"is diagonal (dx={dx:.3f}, dy={dy:.3f}); insert an intermediate (x, y) corner."
            )

    m = _require_manim()
    pts_3d = [[x, y, 0.0] for x, y in pts_2d]
    final_leg = m.Arrow(
        start=pts_3d[-2],
        end=pts_3d[-1],
        buff=0.0,
        stroke_width=stroke_width,
        tip_length=tip_length,
        max_tip_length_to_length_ratio=0.35,
        color=color,
    )
    if len(pts_3d) == 2:
        base_conn = final_leg
    else:
        prefix = m.VMobject(color=color, stroke_width=stroke_width)
        prefix.set_points_as_corners(pts_3d[:-1])
        base_conn = m.VGroup(prefix, final_leg)

    conn = m.DashedVMobject(base_conn, num_dashes=16) if dashed else base_conn
    conn.wire_points = [(round(x, 6), round(y, 6)) for x, y in pts_2d]
    return conn


def make_edge_arrow(
    source_mobj: Any,
    target_mobj: Any,
    *,
    color: str = BORDER_SUBTLE,
    dashed: bool = False,
    buff: float = 0.14,
) -> Any:
    """Connects two cards orthogonally via `connect_h`, `connect_v`, or a 90-degree `connect_elbow`."""
    _, left_a, right_a, bot_a, top_a = _extract_card_bbox(source_mobj, 0)
    _, left_b, right_b, bot_b, top_b = _extract_card_bbox(target_mobj, 1)
    cx_a, cy_a = 0.5 * (left_a + right_a), 0.5 * (bot_a + top_a)
    cx_b, cy_b = 0.5 * (left_b + right_b), 0.5 * (bot_b + top_b)
    dx = cx_b - cx_a
    dy = cy_b - cy_a

    if abs(dx) >= abs(dy) and max(bot_a, bot_b) <= min(top_a, top_b):
        return connect_h(source_mobj, target_mobj, buff=buff, color=color, dashed=dashed)
    if abs(dy) > abs(dx) and max(left_a, left_b) <= min(right_a, right_b):
        return connect_v(source_mobj, target_mobj, buff=buff, color=color, dashed=dashed)

    x_start = (right_a + buff) if dx >= 0 else (left_a - buff)
    y_end = (bot_b - buff) if dy >= 0 else (top_b + buff)
    return connect_elbow(
        [(x_start, cy_a), (cx_b, cy_a), (cx_b, y_end)],
        color=color,
        dashed=dashed,
    )


def _animate_packet_on_path(
    scene: Any,
    path: Any,
    start_point: Any,
    *,
    label: str = "",
    color: str = ACCENT_CYAN,
    run_time: float = 0.75,
    font: str = "Sans",
) -> float:
    """Internal helper that animates a glowing packet + optional label along `path`."""
    m = _require_manim()
    core = m.Dot(point=start_point, radius=0.085, color=color)
    halo = m.Circle(
        radius=0.17,
        stroke_color=color,
        stroke_width=2.0,
        fill_color=color,
        fill_opacity=0.22,
    )
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


def animate_packet_along(
    scene: Any,
    connector: Any,
    *,
    label: str = "",
    color: str = ACCENT_CYAN,
    run_time: float = 0.80,
    reverse: bool = False,
    font: str = "Sans",
) -> float:
    """Animates a glowing packet strictly along `connector.wire_points` (including 90-deg elbows).

    Returns total animation seconds consumed (`run_time + 0.20`) for `BeatTimer`.
    """
    m = _require_manim()
    pts_2d = _extract_wire_points(connector, 0)
    if reverse:
        pts_2d = list(reversed(pts_2d))
    pts_3d = [[x, y, 0.0] for x, y in pts_2d]
    path = m.VMobject()
    path.set_points_as_corners(pts_3d)
    return _animate_packet_on_path(
        scene,
        path,
        pts_3d[0],
        label=label,
        color=color,
        run_time=run_time,
        font=font,
    )


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
    """Animates a glowing request/event packet along a line segment and cleans it up cleanly.

    Returns total animation seconds consumed (`run_time + 0.20`) for `BeatTimer`.
    """
    m = _require_manim()
    path = m.Line(start_point, end_point)
    return _animate_packet_on_path(
        scene,
        path,
        start_point,
        label=label,
        color=color,
        run_time=run_time,
        font=font,
    )


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
