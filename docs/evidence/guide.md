# Guide on screen (`point_at`): box convention and pointing accuracy

Measured 2026-11-14 with `Qwen/Qwen3-VL-235B-A22B-Instruct` on DeepInfra's OpenAI-compatible endpoint.

## Box convention (measured, not assumed)

Probe: three generated images of different, non-square sizes (1600x700, 640x900, 2800x1800) with one blue button
each near the bottom or right, asking for its box as `[x1, y1, x2, y2]`.

| image | true box (px) | returned | returned / true, x | returned / true, y | 1000/W | 1000/H |
|---|---|---|---|---|---|---|
| 1600x700 | 1250,560,1520,640 | 779,795,949,912 | 0.623 | 1.42 | 0.625 | 1.429 |
| 640x900 | 60,760,400,840 | 86,843,626,935 | 1.43-1.57 | 1.11 | 1.562 | 1.111 |
| 2800x1800 | 2300,1500,2700,1650 | 820,831,965,915 | 0.357 | 0.555 | 0.357 | 0.556 |

The x ratio matches 1000/W and the y ratio matches 1000/H on every image. So the boxes are normalised to 0-1000 on
each axis of the image sent, x first, regardless of the pixel size (not absolute pixels, and not one shared factor).
`guide.to_screen` therefore does: normalised -> image pixels -> screen points (divide by the capture scale,
image px width / region width, which is 2.0 for a retina capture) -> add the region origin. Boxes more than 2% outside
0-1000, inverted or non-finite are dropped; points outside the region are dropped.

## Accuracy on generated screens

`backend/scripts/guide_eval.py`: 10 generated screens drawn at 2x (6 forms, 2 tables with a per-row action button,
2 toolbars), 3 questions each (30 asks), fake region at (200, 150) in screen points. It runs the shipped prompt and the
shipped conversion (`guide.plan`). A step is a hit only when its screen point lies inside the true box of the element
its `target` names (the input box for a field, not its caption); a step naming no real element counts as a miss.

| asks | steps returned | hits | hit rate | first step hit | asks with no steps | cost |
|---|---|---|---|---|---|---|
| 30 | 54 | 54 | **100%** | 30/30 | 0 | $0.0079 |

The score checks the element each step names; to rule out a step that names the wrong field, the 24 one-step answers were checked against the question by eye: all 24 named the element the question asked for (e.g. 'show me how to open the Initech invoice' -> Open on the INV-103 row).

The real overlay shows these pointers: `Model.swift` calls `onPointer` for every `pointer` with a task id (no task filter there), so steps arriving after the receipt still move the ghost pointer. Follow-up: the ghost window is 160 pt wide at 11 pt, so a label like '5. Click Pay now to submit' may be cut off.

Read this honestly: the screens are clean, high-contrast, generated mock-ups with distinct labels, so this shows the
coordinate pipeline (convention, retina scale, origin) is right and that the model picks the right control on simple
layouts. It is not a measure on real, dense apps; expect lower there. Step quality is also not perfect: for
"show me how to fill this in" on the sign-up form it returned 5 steps ending with "Click to cancel", which points
correctly but is not a step anyone needs.

Example (screen 4, "walk me through paying"): 6 steps, e.g. "Enter CVC" at (554.2, 353.8), true input box
x 380-730, y 340-374 (screen points).

## What a wrong pointing looks like

None of the 54 steps missed, so the failure modes are shown from the probe and the design:

- Wrong convention: reading the normalised box 779,795,949,912 as pixels on the 1600x700 image gives a point at
  (864, 853), below the image's bottom edge; it would land 150+ px under the real button (or off the region and be
  dropped). This is the error the probe rules out.
- Caption instead of control: pointing at the "Email" caption left of the field instead of the input box. The
  scorer counts that as a miss because the true box is the input box only; the prompt asks for the input box itself.
- Invented target: a step whose `target` is not visible on the screen counts as a miss; the prompt tells the model to
  return fewer steps (or none) rather than guess, and `point_at` shows no pointer when nothing is returned.

## Gate (real run)

A `role: perception` client sent `select {region: [200, 150, 560, 400], image_path: .reach/captures/gate-payment.png}`
(the generated payment form, 2x PNG), then unmodified `reach ask "show me how to fill this in"` ran against a local
`reach serve` while a fake `role: overlay` client recorded. Receipt `tools: ["point_at"]`, status ok. Pointers, in
order, 3.5 s apart (t = seconds since the select):

| t | label | x | y | true box (screen points) |
|---|---|---|---|---|
| 24.54 | 1. Enter card number | 554.5 | 236.0 | x 380-730, y 220-254 |
| 28.04 | 2. Enter expiry date | 554.5 | 295.4 | x 380-730, y 280-314 |
| 31.54 | 3. Enter CVC code | 554.5 | 355.0 | x 380-730, y 340-374 |
| 35.04 | 4. Enter cardholder name | 554.5 | 415.4 | x 380-730, y 400-434 |
| 38.54 | 5. Click Pay now to submit | 425.7 | 486.4 | x 380-473, y 470-506 |
| 42.04 | 6. Click Cancel to exit | 531.2 | 486.4 | x 489-573, y 470-506 |

The answer explained the six steps in words and said: "The pointer only points — I didn't click or type anything".
An earlier run (before the prompt said to call point_at directly) used `see_region` first, then `point_at`.
Drex intent was unavailable in that server ("no Drex key" in the worktree), so the `screen_question` hint itself was
not exercised in the gate; the prompt sentence in `bridge.prompt_for` was.
