"""Measure point_at on generated screens (forms, tables, UI mocks) where the true target boxes are known.

Needs DEEPINFRA_API_KEY (loaded inside the program, like see_region). Usage:
    cd backend && uv run python scripts/guide_eval.py OUT_DIR
Each image is drawn at 2x (retina) for a fake region at (200, 150) in screen points; the shipped prompt and the shipped
conversion (guide.plan) turn the model's boxes into screen points, which are checked against the true boxes.
A step counts as a hit when its point lies inside the true box of the element its `target` text names; a step whose
target names no real element counts as a miss. `loose` also counts a point inside any real control.
"""

from __future__ import annotations

import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reach.tools import guide  # noqa: E402

S = 2  # capture scale
ORIGIN = (200.0, 150.0)
FONT = "/System/Library/Fonts/Supplemental/Arial.ttf"


def font(px):
    return ImageFont.truetype(FONT, px * S)


def form(title, fields, buttons, w=560, h=None):
    """fields: label names (inputs); buttons: names. Returns (image, elements[{name, box (points, region-relative)}])."""
    h = h or 90 + 60 * len(fields) + 70
    im = Image.new("RGB", (w * S, h * S), "white")
    d = ImageDraw.Draw(im)
    d.text((24 * S, 18 * S), title, fill="black", font=font(20))
    els = []
    y = 70
    for name in fields:
        d.text((24 * S, (y + 8) * S), name, fill=(60, 60, 60), font=font(14))
        box = (180, y, w - 30, y + 34)
        d.rectangle([v * S for v in box], outline=(150, 150, 150), width=S)
        els.append({"name": name, "box": box})
        y += 60
    x = 180
    for i, name in enumerate(buttons):
        bw = 30 + 9 * len(name)
        box = (x, y + 10, x + bw, y + 46)
        d.rounded_rectangle([v * S for v in box], radius=6 * S, fill=(37, 99, 235) if i == 0 else (225, 225, 225))
        d.text(((x + 15) * S, (y + 19) * S), name, fill="white" if i == 0 else "black", font=font(14))
        els.append({"name": name, "box": box})
        x += bw + 16
    return im, els


def table(title, cols, rows, action):
    w, rh = 640, 40
    h = 80 + rh * (len(rows) + 1) + 20
    im = Image.new("RGB", (w * S, h * S), "white")
    d = ImageDraw.Draw(im)
    d.text((20 * S, 16 * S), title, fill="black", font=font(20))
    cw = (w - 40 - 90) // len(cols)
    y = 60
    for j, c in enumerate(cols):
        d.text(((20 + j * cw + 6) * S, (y + 10) * S), c, fill="black", font=font(14))
    els = []
    for r in rows:
        y += rh
        d.line([20 * S, y * S, (w - 20) * S, y * S], fill=(220, 220, 220), width=S)
        for j, v in enumerate(r):
            d.text(((20 + j * cw + 6) * S, (y + 11) * S), v, fill=(40, 40, 40), font=font(14))
        box = (w - 100, y + 6, w - 30, y + 34)
        d.rounded_rectangle([v * S for v in box], radius=5 * S, outline=(37, 99, 235), width=S)
        d.text(((w - 92) * S, (y + 12) * S), action, fill=(37, 99, 235), font=font(13))
        els.append({"name": f"{action} {r[0]}", "box": box, "row": r[0]})
    return im, els


def toolbar(names, w=620):
    im = Image.new("RGB", (w * S, 300 * S), (245, 245, 247))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w * S, 44 * S], fill=(230, 230, 233))
    els, x = [], 12
    for n in names:
        bw = 24 + 8 * len(n)
        box = (x, 8, x + bw, 36)
        d.rounded_rectangle([v * S for v in box], radius=5 * S, fill="white", outline=(190, 190, 190), width=S)
        d.text(((x + 12) * S, 14 * S), n, fill="black", font=font(13))
        els.append({"name": n, "box": box})
        x += bw + 10
    d.text((30 * S, 120 * S), "Untitled document", fill=(120, 120, 120), font=font(22))
    return im, els


CASES = [
    (lambda: form("Create account", ["Email", "Password", "Confirm password"], ["Sign up", "Cancel"]),
     ["show me how to fill this in", "where do I type my password?", "where do I click to finish signing up?"]),
    (lambda: form("Shipping address", ["Full name", "Street", "City", "Postcode"], ["Continue", "Back"]),
     ["how do I fill this in?", "where does the city go?", "where do I click to go on to the next page?"]),
    (lambda: form("Contact us", ["Name", "Email", "Subject", "Message"], ["Send", "Clear"]),
     ["show me how to send a message", "where do I put my email?", "which button clears the form?"]),
    (lambda: form("Log in", ["Username", "Password"], ["Log in", "Forgot password"], w=480),
     ["show me how to log in", "where do I enter my username?", "where do I click if I forgot my password?"]),
    (lambda: form("Payment", ["Card number", "Expiry", "CVC", "Name on card"], ["Pay now", "Cancel"]),
     ["walk me through paying", "where does the CVC go?", "where is the button to pay?"]),
    (lambda: table("Invoices", ["Number", "Customer", "Amount"],
                   [["INV-101", "Acme", "$120"], ["INV-102", "Globex", "$80"], ["INV-103", "Initech", "$45"]], "Open"),
     ["where do I click to open invoice INV-102?", "show me how to open the Initech invoice", "where do I open INV-101?"]),
    (lambda: table("Team", ["Name", "Role", "Status"],
                   [["Ana", "Design", "Active"], ["Ben", "Eng", "Away"], ["Cy", "Ops", "Active"], ["Dee", "Eng", "Active"]], "Edit"),
     ["where do I edit Ben?", "show me how to edit Dee's entry", "where do I click to change Cy?"]),
    (lambda: toolbar(["New", "Open", "Save", "Share", "Print", "Help"]),
     ["where do I click to save?", "show me how to share this document", "where is print?"]),
    (lambda: toolbar(["Bold", "Italic", "Underline", "Link", "Image", "Undo"]),
     ["how do I make text bold?", "where do I add an image?", "where do I click to undo?"]),
    (lambda: form("Newsletter settings", ["Display name", "Email", "Frequency"], ["Save changes", "Unsubscribe"]),
     ["show me how to update my settings", "where do I change how often I get emails?", "where do I unsubscribe?"]),
]


def norm(s):
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in s).split())


def match(target, label, els):
    t = norm(target) + " | " + norm(label)
    best = None
    for e in sorted(els, key=lambda e: -len(e["name"])):  # longest name first ("Confirm password" before "Password")
        keys = [norm(e["name"])] + ([norm(e["row"])] if e.get("row") else [])
        if any(k and k in t for k in keys):
            best = e
            break
    return best


def inside(x, y, box):
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def run_one(args):
    i, q, png, region, els = args
    cost = [0.0]

    def send(body):
        from reach.tools import vision

        r = vision.call(body)
        cost[0] += (r.get("usage") or {}).get("estimated_cost") or 0.0
        return r

    steps = guide.plan(png, region, q, 6, send=send)
    rows = []
    for s in steps:
        rx, ry = s["x"] - region[0], s["y"] - region[1]  # region-relative points
        e = match(s.get("target", ""), s["label"], els)
        hit = bool(e and inside(rx, ry, e["box"]))
        loose = any(inside(rx, ry, x["box"]) for x in els)
        rows.append({"label": s["label"], "target": s.get("target"), "x": s["x"], "y": s["y"],
                     "matched": e["name"] if e else None, "hit": hit, "loose": loose,
                     "true_box": [b + o for b, o in zip(e["box"], region[:2] * 2)] if e else None})
    return {"image": i, "question": q, "steps": rows, "cost": cost[0]}


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jobs = []
    for i, (make, qs) in enumerate(CASES):
        im, els = make()
        b = io.BytesIO()
        im.save(b, "PNG")
        (out / f"screen-{i}.png").write_bytes(b.getvalue())
        region = [ORIGIN[0], ORIGIN[1], im.width / S, im.height / S]
        for q in qs:
            jobs.append((i, q, b.getvalue(), region, els))
    with ThreadPoolExecutor(5) as ex:
        results = list(ex.map(run_one, jobs))
    steps = [s for r in results for s in r["steps"]]
    summary = {
        "asks": len(results), "steps": len(steps), "hits": sum(s["hit"] for s in steps),
        "loose_hits": sum(s["loose"] for s in steps), "asks_with_no_steps": sum(not r["steps"] for r in results),
        "cost_usd": round(sum(r["cost"] for r in results), 5),
    }
    summary["hit_rate"] = round(summary["hits"] / max(1, summary["steps"]), 3)
    summary["first_step_hit_rate"] = round(sum(bool(r["steps"]) and r["steps"][0]["hit"] for r in results) / len(results), 3)
    (out / "results.json").write_text(json.dumps({"summary": summary, "results": results}, indent=1))
    print(json.dumps(summary))
    for r in results:
        for s in r["steps"]:
            if not s["hit"]:
                print("MISS", r["image"], repr(r["question"]), json.dumps(s))


if __name__ == "__main__":
    main(sys.argv[1])
