# Manim Layout Grid, LaTeX-Free Primitives & Visual Choreography

## 1. 16:9 Coordinate System & Safe-Zone Reservations

Manim Community's default 16:9 canvas spans `[-7.11, 7.11]` horizontally (`14.22` units) and `[-4.0, 4.0]` vertically (`8.0` units).
Placing cards or labels near raw frame boundaries produces clipped strokes and collides with WebVTT subtitle overlays at playback time.

Enforce three vertical bands inside the safe authoring box `[-6.6, 6.6] x [-3.5, 3.5]`:

- **Header Band (`y in [2.65, 3.50]`)**: Act kicker (`font_size=16`, uppercase Frost Cyan) + Scene title (`font_size=28`, bold Snow Storm). Never run packet animations through the header band.
- **Primary Stage (`y in [-2.45, 2.45]`)**: System topology cards, state counters, request arrows, and transient badges.
  - **4-Column Horizontal Pipeline**: `x = -4.8, -1.6, +1.6, +4.8` with card width `<= 2.55` so inter-card arrow gaps stay `>= 0.65` units.
  - **3-Column Pipeline + Vertical Storage Tier**: Top row `y = +0.85` (`x = -4.2, 0.0, +4.2`), bottom row `y = -1.45` (`x = 0.0, +4.2`).
- **Caption-Clear Lower Margin (`y in [-3.50, -2.60]`)**: Reserve `y < -3.05` completely clear whenever `.vtt` subtitles are enabled so browser native WebVTT cue boxes never obscure active state labels. Place compact legend strips or invariants no lower than `y = -2.85`.

`check_scene_bounds.py` enforces hard outer walls at `[-6.8, 6.8] x [-3.75, 3.75]` and checks every pair of visible text elements for 2D bounding-box overlap.

## 2. Strictly LaTeX-Free Primitives

Never instantiate `Tex`, `MathTex`, `BulletedList`, or `Title` (which delegates to `Tex` internally). Those classes invoke `latex` / `dvisvgm` subprocesses and fail on machines without a multi-gigabyte TeX distribution.

Use Pango/Cairo native primitives exclusively:

- **Plain Typography**: `Text("Primary DB", font="Sans", font_size=21, weight=BOLD)`
- **Inline Styled Spans, Subscripts & Formulas**:
  ```python
  MarkupText(
      'Token refill: <span foreground="#88C0D0"><b>r = 500 tok/s</b></span>, '
      'burst cap <i>B</i><sub>max</sub> = 1000',
      font="Sans",
      font_size=18,
  )
  ```
- **Structured Config / SQL / Log Snippets**:
  ```python
  Code(
      code_string="SET lock:order:42 token NX PX 5000",
      language="text",
      background="rectangle",
      add_line_numbers=False,
  )
  ```
- **Topology Geometry**: `RoundedRectangle`, `Rectangle`, `Circle`, `Dot`, `Line`, `Arrow`, `CurvedArrow`, `DashedVMobject`, `SurroundingRectangle`.
- **Dynamic Counters**: Drive numeric readouts (such as token-bucket balance or WAL commit index) with a `ValueTracker` and `always_redraw` around `Text(f"Tokens: {int(tracker.get_value())}", ...)` rather than `DecimalNumber` (which defaults to `MathTex` unless configured carefully).

## 3. State Hygiene & Anti-Collision Choreography

Overlapping text labels are the single most common defect in generated Manim scenes. Follow four rules across every beat transition:

1. **One Transient Callout Per Anchor**: If Beat 2 places `"CACHE MISS"` above the Cache card (`badge.next_to(cache_card, UP, buff=0.18)`), explicitly `FadeOut(badge)` or `ReplacementTransform(miss_badge, fill_badge)` before Beat 3 attaches `"TTL = 60s"` to the same anchor.
2. **Clamp Dynamic Text Widths**: Always wrap newly built labels in `safe_fit_text(label, max_width=card_width - 0.34)` from `assets/nord_manim_theme.py` so long localized strings or technical identifiers shrink gracefully instead of overflowing card borders.
3. **Separate Edge Labels from Parallel Arrows**: For bidirectional request/response paths between Node A and Node B, offset forward arrows up by `+0.16 * UP` and return arrows down by `+0.16 * DOWN`. Place the request label above the top arrow and the response label below the bottom arrow.
4. **Dim Inactive Subgraphs During Focus Beats**: When explaining an edge-case failure on one branch (e.g., primary replica timeout and WAL promotion), animate unrelated upstream nodes to `opacity=0.32` (`upstream_group.animate.set_opacity(0.32)`) and restore `set_opacity(1.0)` when returning to steady-state flow.

## 4. Two-Speed Rendering & Multi-Scene Concatenation

### Fast Geometry Verification Before Full HD Render

Never iterate on layout at 1080p60. Run dry-run bounds verification first, then preview at 480p15 (`-ql`), and render 1080p60 (`-qh --fps 60`) once layout and beat timings are locked:

```bash
# 1. Dry-run bounding-box & text-collision check (< 3 seconds)
./venv/bin/python check_scene_bounds.py --module scenes.py --scene CacheWalkthrough

# 2. Optional quick visual preview (854x480 @ 15fps)
./venv/bin/manim -ql scenes.py CacheWalkthrough

# 3. Production 1080p60 render (1920x1080 @ 60fps)
./venv/bin/manim -qh --fps 60 scenes.py CacheWalkthrough
```

### Lossless Multi-Act Concatenation

Keep complex walkthroughs modular: author one `Scene` class per Act (`Act1Ingress`, `Act2SteadyState`, `Act3Failover`), mux each scene's narration independently so timing edits in Act 2 never shift Act 1 or require re-rendering Act 3, and concatenate matching H.264/AAC scenes into a master reel:

```bash
cat > tmp/concat_list.txt <<'EOF'
file '../output/act1_en.mp4'
file '../output/act2_en.mp4'
file '../output/act3_en.mp4'
EOF

ffmpeg -y -f concat -safe 0 -i tmp/concat_list.txt \
  -c:v libx264 -pix_fmt yuv420p -crf 21 -preset medium \
  -c:a aac -b:a 160k -movflags +faststart \
  output/master_en.mp4
```

Re-encoding during final master assembly with uniform `-r 60 -pix_fmt yuv420p` and `-c:a aac -b:a 160k -movflags +faststart` guarantees glitch-free PTS continuity across scene boundaries and fast HTTP `206 Partial Content` seeking in every browser.
