# No circle needed: `see_screen`

Intern tool `see_screen(question, target='display'|'window')` (backend/reach/tools/screen.py).

- Captures with `/usr/sbin/screencapture -x` (no sound; never a keystroke or mouse event). `display` = main display
  (`-m`); `window` = the frontmost app's frontmost on-screen window (`-o -l <id>`), found read-only through the
  CoreGraphics window list via `osascript -l JavaScript`; windows owned by `Reach` are skipped. If no window is
  found it captures the display and says so in the answer.
- Reach's own overlay: its lasso/halo windows use `sharingType = .none`, so they never appear. The panel may appear
  in a `display` capture; use `target='window'` to exclude it.
- Downsized to at most 1568 px on the long side, saved as `.reach/captures/screen-<ms>.png` (the full-size raw goes
  to a private temp dir and is deleted), then sent through `vision.see` (same request builder, `.reach` path check,
  and `vision` ledger entry with latency and cost as `see_region`).
- Failure (non-zero exit, no file, timeout) or a blank image (64x64 grayscale stddev < 2) returns
  `needs you: allow Screen Recording for <host app> ...`, never a crash. Note: without the permission macOS can also
  return wallpaper only, which the blank check does not catch.
- Privacy: the image goes only to the DeepInfra vision call. Captures (`*.png`, `*.json` directly in
  `.reach/captures/`) older than 1 hour are deleted when the server starts. The receipt carries
  `screen_captured: true` only when a capture was actually taken and sent.
- `REACH_AUTO_SCREEN=0` turns it off: the tool refuses and intent goes back to the "circle it" short-circuit.

## Sidecar format (for any capture consumer, e.g. point_at)

Next to each `screen-<ms>.png` there is `screen-<ms>.json`:

```json
{"region": [x, y, w, h], "scale": s, "display": "main" | "window:<id>", "created": 1794689590.83,
 "source": "see_screen", "target": "display" | "window", "app": "Google Chrome", "pixels": [1568, 1018]}
```

- `region`: the screen rect the image covers, in points, top-left origin of the main display.
- `scale`: pixels of the saved image per point. A pixel `(px, py)` in the image is the screen point
  `(region.x + px / scale, region.y + py / scale)`.
- Read it with `reach.tools.screen.read_sidecar(png)` (None when missing or malformed) and convert with
  `to_points(meta, px, py)`. Overlay circle captures have no sidecar: their region travels in the `selection`.

## Intent change (backend/reach/intent.py)

New action `see_screen`: no selection AND (`needs_screen_region` >= 0.7 OR kind == `screen_question`). The ask goes
ahead with a hint telling System 2 to call `see_screen`. `confirm` (risky) still wins over it. The old
`needs_selection` short-circuit runs only with `REACH_AUTO_SCREEN=0`.

## Real run (2026-11-14, own `reach serve`, Terminal has Screen Recording)

`reach ask "what is on my screen right now? one sentence"`

- tools `[see_screen]`; intent `screen_question` p=0.978, action `see_screen`, 273 ms; `screen_captured: true`.
- Answer: "Your screen is showing a Google search results page for "water scare," with an AI overview defining
  water scares, listing common causes, and a "Did you mean: water scarce" suggestion."
- Checked against the saved capture: correct (Chrome, Google results for "water scare", AI Overview, "Did you
  mean: water scarce").
- Vision: Qwen3-VL-235B, 4554 ms, 2368 in / 46 out tokens, $0.000514. Whole ask 14.4 s, System 2 $0.000089.
- Bug found and fixed on the way: `screencapture` exits 0 but refuses to write a dot-file name
  (`.raw-*.png`); the raw now goes to a temp dir.
