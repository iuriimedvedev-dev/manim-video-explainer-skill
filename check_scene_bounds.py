#!/usr/bin/env python3
"""Bounding-box and label-collision verifier for Manim Community scenes.

Validates that every visible mobject stays inside the safe 16:9 frame bounds
([-6.8, 6.8] x [-3.75, 3.75] by default) and that no two sibling text labels
collide on screen at any recorded beat checkpoint.

Can be used in two ways:
1. CLI check over a JSON checkpoint dump emitted during scene rendering:
     python3 check_scene_bounds.py --json media/bounds_checkpoints.json
2. CLI dry-run inspection of a Manim Scene class (requires `manim` installed):
     python3 check_scene_bounds.py --module scenes.py --scene RateLimiterWalkthrough
3. Imported inside a Manim scene via `record_checkpoint(scene, beat_id, store)`.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Iterable


DEFAULT_X_LIMIT = 6.8
DEFAULT_Y_LIMIT = 3.75
MIN_VISIBLE_OPACITY = 0.02
COLLISION_EPSILON = 0.02


@dataclass(frozen=True)
class BBoxItem:
    """Axis-aligned 2D bounding box for a single rendered visual element."""

    id: str
    kind: str  # "text" | "card" | "shape" | "arrow"
    left: float
    right: float
    bottom: float
    top: float
    opacity: float = 1.0
    group: str | None = None

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.top - self.bottom)


def _classify_mobject(mobj: Any) -> str:
    """Classifies a Manim mobject into 'text', 'arrow', 'card', or 'shape'."""
    cls_names = [cls.__name__.lower() for cls in type(mobj).__mro__]
    for name in cls_names:
        if any(tok in name for tok in ("text", "markup", "code", "paragraph", "tex")):
            return "text"
        if "arrow" in name or "line" in name:
            return "arrow"
        if "roundedrectangle" in name or "rectangle" in name:
            return "card"
    return "shape"


def _effective_opacity(mobj: Any) -> float:
    """Returns the maximum visible fill or stroke opacity of a Manim mobject."""
    opacities: list[float] = []
    for getter in ("get_fill_opacity", "get_stroke_opacity"):
        fn = getattr(mobj, getter, None)
        if callable(fn):
            try:
                val = float(fn())
                opacities.append(val)
            except (TypeError, ValueError):
                pass
    return max(opacities) if opacities else 1.0


def extract_scene_bboxes(scene: Any, beat_id: str = "checkpoint") -> dict[str, Any]:
    """Extracts leaf mobject bounding boxes from a live Manim Scene instance.

    Text/MarkupText/Code objects are treated as atomic leaf items ('text') so individual
    glyph submobjects are not falsely flagged as colliding with adjacent letters.
    """
    items: list[dict[str, Any]] = []
    counter = 0

    def visit(mobj: Any, group_id: str | None = None) -> None:
        nonlocal counter
        kind = _classify_mobject(mobj)
        opacity = _effective_opacity(mobj)

        # Treat text/code primitives as atomic leaves rather than descending into glyphs.
        submobjects = getattr(mobj, "submobjects", [])
        if kind != "text" and submobjects:
            next_group = group_id or f"{type(mobj).__name__}_{counter}"
            counter += 1
            for sub in submobjects:
                visit(sub, next_group)
            return

        try:
            left = float(mobj.get_left()[0])
            right = float(mobj.get_right()[0])
            bottom = float(mobj.get_bottom()[1])
            top = float(mobj.get_top()[1])
        except Exception:
            return

        if right - left <= 1e-4 and top - bottom <= 1e-4:
            return

        label = getattr(mobj, "name", None) or getattr(mobj, "text", None) or type(mobj).__name__
        clean_label = str(label).strip().replace("\n", " ")[:40]
        item_id = f"{type(mobj).__name__}[{counter}]:{clean_label}"
        counter += 1
        items.append(
            asdict(
                BBoxItem(
                    id=item_id,
                    kind=kind,
                    left=round(left, 4),
                    right=round(right, 4),
                    bottom=round(bottom, 4),
                    top=round(top, 4),
                    opacity=round(opacity, 4),
                    group=group_id,
                )
            )
        )

    for top_mobj in getattr(scene, "mobjects", []):
        visit(top_mobj, None)

    return {"beat_id": beat_id, "items": items}


def record_checkpoint(scene: Any, beat_id: str, store: list[dict[str, Any]]) -> dict[str, Any]:
    """Appends a bounding-box snapshot for `beat_id` to `store` and returns it."""
    snap = extract_scene_bboxes(scene, beat_id=beat_id)
    store.append(snap)
    return snap


def dump_checkpoints(checkpoints: list[dict[str, Any]], output_path: str | Path) -> Path:
    """Writes collected scene bounding-box checkpoints to a JSON file."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"checkpoints": checkpoints}, indent=2) + "\n", encoding="utf-8")
    return out


def _boxes_overlap(a: BBoxItem, b: BBoxItem, epsilon: float = COLLISION_EPSILON) -> bool:
    """Returns True if two axis-aligned bounding boxes overlap by more than `epsilon`."""
    overlap_x = min(a.right, b.right) - max(a.left, b.left)
    overlap_y = min(a.top, b.top) - max(a.bottom, b.bottom)
    return overlap_x > epsilon and overlap_y > epsilon


def validate_bboxes(
    items: Iterable[BBoxItem | dict[str, Any]],
    *,
    beat_id: str = "checkpoint",
    x_limit: float = DEFAULT_X_LIMIT,
    y_limit: float = DEFAULT_Y_LIMIT,
    epsilon: float = COLLISION_EPSILON,
) -> list[str]:
    """Checks frame overflow and sibling text-label collisions for a single checkpoint."""
    parsed: list[BBoxItem] = []
    for raw in items:
        if isinstance(raw, BBoxItem):
            parsed.append(raw)
        else:
            parsed.append(
                BBoxItem(
                    id=str(raw["id"]),
                    kind=str(raw.get("kind", "shape")),
                    left=float(raw["left"]),
                    right=float(raw["right"]),
                    bottom=float(raw["bottom"]),
                    top=float(raw["top"]),
                    opacity=float(raw.get("opacity", 1.0)),
                    group=raw.get("group"),
                )
            )

    errors: list[str] = []
    visible = [
        it
        for it in parsed
        if it.opacity >= MIN_VISIBLE_OPACITY and (it.width > 1e-4 or it.height > 1e-4)
    ]

    for item in visible:
        if item.left < -x_limit:
            errors.append(
                f"[{beat_id}] Overflow LEFT: {item.id} ({item.kind}) left={item.left:.2f} < {-x_limit:.2f}"
            )
        if item.right > x_limit:
            errors.append(
                f"[{beat_id}] Overflow RIGHT: {item.id} ({item.kind}) right={item.right:.2f} > {x_limit:.2f}"
            )
        if item.bottom < -y_limit:
            errors.append(
                f"[{beat_id}] Overflow BOTTOM: {item.id} ({item.kind}) bottom={item.bottom:.2f} < {-y_limit:.2f}"
            )
        if item.top > y_limit:
            errors.append(
                f"[{beat_id}] Overflow TOP: {item.id} ({item.kind}) top={item.top:.2f} > {y_limit:.2f}"
            )

    text_items = [it for it in visible if it.kind == "text"]
    for idx, first in enumerate(text_items):
        for second in text_items[idx + 1 :]:
            if _boxes_overlap(first, second, epsilon=epsilon):
                errors.append(
                    f"[{beat_id}] Text collision: '{first.id}' "
                    f"[{first.left:.2f},{first.bottom:.2f}..{first.right:.2f},{first.top:.2f}] "
                    f"overlaps '{second.id}' "
                    f"[{second.left:.2f},{second.bottom:.2f}..{second.right:.2f},{second.top:.2f}]"
                )

    return errors


def validate_checkpoint_payload(
    payload: dict[str, Any] | list[dict[str, Any]],
    *,
    x_limit: float = DEFAULT_X_LIMIT,
    y_limit: float = DEFAULT_Y_LIMIT,
    epsilon: float = COLLISION_EPSILON,
) -> list[str]:
    """Validates a full checkpoint payload (dict with `checkpoints` or single snapshot)."""
    if isinstance(payload, list):
        checkpoints = payload
    elif "checkpoints" in payload:
        checkpoints = payload["checkpoints"]
    elif "items" in payload:
        checkpoints = [payload]
    else:
        raise ValueError("Expected JSON with 'checkpoints' list or single {'beat_id', 'items'} object.")

    all_errors: list[str] = []
    for index, cp in enumerate(checkpoints):
        beat_id = str(cp.get("beat_id", f"beat_{index:02d}"))
        items = cp.get("items", [])
        all_errors.extend(
            validate_bboxes(
                items,
                beat_id=beat_id,
                x_limit=x_limit,
                y_limit=y_limit,
                epsilon=epsilon,
            )
        )
    return all_errors


def inspect_manim_scene(
    module_path: Path,
    scene_name: str,
    *,
    x_limit: float = DEFAULT_X_LIMIT,
    y_limit: float = DEFAULT_Y_LIMIT,
    epsilon: float = COLLISION_EPSILON,
    dump_json: Path | None = None,
) -> list[str]:
    """Runs a Manim Scene in dry-run mode and captures bounding boxes at every `wait()`."""
    try:
        from manim import tempconfig  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "Manim is not installed in the active Python interpreter. "
            "Run inside your Manim virtualenv or validate a JSON dump with --json."
        ) from exc

    spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load Python module from {module_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_path.stem] = mod
    spec.loader.exec_module(mod)

    scene_cls = getattr(mod, scene_name, None)
    if scene_cls is None:
        raise RuntimeError(f"Scene class '{scene_name}' not found in {module_path}")

    checkpoints: list[dict[str, Any]] = []
    wait_index = 0
    orig_wait = scene_cls.wait

    def hooked_wait(self: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal wait_index
        wait_index += 1
        record_checkpoint(self, f"wait_{wait_index:02d}", checkpoints)
        return orig_wait(self, *args, **kwargs)

    scene_cls.wait = hooked_wait
    try:
        with tempconfig({"dry_run": True, "quality": "low_quality", "preview": False}):
            scene = scene_cls()
            scene.render()
            record_checkpoint(scene, "scene_end", checkpoints)
    finally:
        scene_cls.wait = orig_wait

    if dump_json is not None:
        dump_checkpoints(checkpoints, dump_json)

    return validate_checkpoint_payload(
        {"checkpoints": checkpoints},
        x_limit=x_limit,
        y_limit=y_limit,
        epsilon=epsilon,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check Manim scene bounding boxes for safe-frame overflow and text collisions."
    )
    parser.add_argument("--json", type=Path, help="Path to JSON bounding-box checkpoint file.")
    parser.add_argument("--module", type=Path, help="Path to Manim Python file for live dry-run check.")
    parser.add_argument("--scene", type=str, help="Scene class name inside --module.")
    parser.add_argument(
        "--dump-json",
        type=Path,
        help="Optional output JSON path when inspecting --module / --scene.",
    )
    parser.add_argument(
        "--x-limit",
        type=float,
        default=DEFAULT_X_LIMIT,
        help=f"Half-width safe bound in Manim units (default: {DEFAULT_X_LIMIT}).",
    )
    parser.add_argument(
        "--y-limit",
        type=float,
        default=DEFAULT_Y_LIMIT,
        help=f"Half-height safe bound in Manim units (default: {DEFAULT_Y_LIMIT}).",
    )
    parser.add_argument(
        "--epsilon",
        type=float,
        default=COLLISION_EPSILON,
        help=f"Allowed overlap tolerance in Manim units (default: {COLLISION_EPSILON}).",
    )
    args = parser.parse_args(argv)

    if not args.json and not args.module:
        parser.error("Provide either --json <checkpoints.json> or --module <file.py> --scene <ClassName>.")

    try:
        if args.json:
            payload = json.loads(args.json.read_text(encoding="utf-8"))
            errors = validate_checkpoint_payload(
                payload,
                x_limit=args.x_limit,
                y_limit=args.y_limit,
                epsilon=args.epsilon,
            )
        else:
            if not args.scene:
                parser.error("--scene <ClassName> is required when using --module.")
            errors = inspect_manim_scene(
                args.module,
                args.scene,
                x_limit=args.x_limit,
                y_limit=args.y_limit,
                epsilon=args.epsilon,
                dump_json=args.dump_json,
            )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if errors:
        print(f"FAIL: {len(errors)} layout violation(s) detected:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    target = args.json if args.json else f"{args.module}:{args.scene}"
    print(f"PASS: {target} satisfied bounds [-{args.x_limit}, {args.x_limit}] x [-{args.y_limit}, {args.y_limit}] with 0 text collisions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
