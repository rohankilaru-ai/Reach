"""see_screen: look at the whole screen (or the frontmost window) when nothing is circled.

Captures with `/usr/sbin/screencapture -x` (no sound, never any keystroke or mouse event), downsizes to at most
MAX_SIDE px on the long side, saves `captures/screen-<ms>.png` under .reach/ with a sidecar JSON next to it, then sends
it to the same vision model and request builder as see_region (`vision.see`, which also logs latency and cost).

Sidecar `<stem>.json` (documented in docs/evidence/auto-screen.md; any capture consumer can read it with
`read_sidecar`):
    {"region": [x, y, w, h],   # screen rect the image covers, in points, top-left origin of the main display
     "scale": s,               # pixels of the SAVED image per point: point = (region.x + px / s, region.y + py / s)
     "display": "main",        # or "window:<id>"
     "created": <unix seconds>, "source": "see_screen", "target": "display" | "window", "app": <frontmost app>}

Privacy: the image goes only to the DeepInfra vision call. Captures older than RETENTION_S are deleted on server
start (`prune_captures`). Needs the Screen Recording permission for the process running the backend; a failed or
blank capture returns a 'needs you' message instead of an answer.
"""

from __future__ import annotations

import json
import shutil
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, Literal

from intern.tools.base import Tool, ToolContext, ToolOutput
from pydantic import BaseModel, Field

from ..paths import reach_dir

SCREENCAPTURE = "/usr/sbin/screencapture"
MAX_SIDE = 1568
RETENTION_S = 3600
CAPTURE_TIMEOUT_S = 10
BLANK_STDDEV = 2.0  # a capture whose pixels barely vary is black/blank: no permission or nothing drawn
OWN_APPS = ("Reach",)  # never capture Reach's own window as "the frontmost window"

Runner = Callable[[list[str], float], int]


class NeedsPermission(RuntimeError):
    pass


def host_app() -> str:
    """The app that must hold Screen Recording: the terminal that runs the backend, or the backend itself."""
    tp = os.environ.get("TERM_PROGRAM", "")
    return {"Apple_Terminal": "Terminal", "iTerm.app": "iTerm", "vscode": "Visual Studio Code"}.get(tp, tp) or \
        "the app running the Reach backend"


def needs_permission_message() -> str:
    return (f"needs you: allow Screen Recording for {host_app()} (System Settings > Privacy & Security > "
            "Screen & System Audio Recording), then ask again. I could not see the screen.")


def run_cmd(cmd: list[str], timeout: float) -> int:
    try:
        return subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout).returncode
    except (subprocess.TimeoutExpired, OSError):
        return -1


_JXA = r'''
ObjC.import("CoreGraphics"); ObjC.import("AppKit");
var f=$.NSScreen.mainScreen.frame; var out={screen:[f.size.width,f.size.height],scale:$.NSScreen.mainScreen.backingScaleFactor};
var front=$.NSWorkspace.sharedWorkspace.frontmostApplication; out.front=ObjC.unwrap(front.localizedName); var fp=front.processIdentifier;
var arr=ObjC.castRefToObject($.CGWindowListCopyWindowInfo($.kCGWindowListOptionOnScreenOnly|$.kCGWindowListExcludeDesktopElements,0));
out.window=null;
for (var i=0;i<arr.count;i++){var d=arr.objectAtIndex(i); if(ObjC.unwrap(d.objectForKey("kCGWindowLayer"))!==0) continue;
 var owner=ObjC.unwrap(d.objectForKey("kCGWindowOwnerName")); if(OWN.indexOf(owner)>=0) continue;
 if(ObjC.unwrap(d.objectForKey("kCGWindowOwnerPID"))!==fp && OWN.indexOf(out.front)<0) continue;
 var b=d.objectForKey("kCGWindowBounds");
 out.window={id:ObjC.unwrap(d.objectForKey("kCGWindowNumber")),owner:owner,b:[ObjC.unwrap(b.objectForKey("X")),ObjC.unwrap(b.objectForKey("Y")),ObjC.unwrap(b.objectForKey("Width")),ObjC.unwrap(b.objectForKey("Height"))]}; break;}
JSON.stringify(out)
'''


def screen_info(timeout: float = 5) -> dict[str, Any]:
    """Main display size (points) and scale, the frontmost app, and its frontmost window not owned by Reach.
    Read-only (CoreGraphics window list via osascript JXA); {} when it fails."""
    src = f"var OWN={json.dumps(list(OWN_APPS))};" + _JXA
    try:
        r = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-e", src], capture_output=True, text=True,
                           timeout=timeout)
        return json.loads(r.stdout.strip()) if r.returncode == 0 else {}
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return {}


def is_blank(png: Path) -> bool:
    """True when the image is (near) one flat colour: what macOS returns without Screen Recording, or a black screen."""
    from PIL import Image, ImageStat

    with Image.open(png) as im:
        small = im.convert("L").resize((64, 64))
        return ImageStat.Stat(small).stddev[0] < BLANK_STDDEV


def downsize(src: Path, dst: Path, max_side: int = MAX_SIDE) -> tuple[int, int, int, int]:
    """Write `src` to `dst` as PNG with the long side at most `max_side`. Returns (orig_w, orig_h, new_w, new_h)."""
    from PIL import Image

    with Image.open(src) as im:
        w, h = im.size
        f = min(1.0, max_side / max(w, h))
        nw, nh = max(1, round(w * f)), max(1, round(h * f))
        out = im.convert("RGB")
        if (nw, nh) != (w, h):
            out = out.resize((nw, nh), Image.LANCZOS)
        out.save(dst, "PNG")
    return w, h, nw, nh


def sidecar_path(png: Path) -> Path:
    return png.with_suffix(".json")


def write_sidecar(png: Path, region: list[float], scale: float, display: str, **extra: Any) -> dict[str, Any]:
    meta = {"region": [round(float(v), 2) for v in region], "scale": round(float(scale), 6), "display": display,
            "created": round(time.time(), 3), **extra}
    sidecar_path(png).write_text(json.dumps(meta))
    return meta


def read_sidecar(png: str | Path) -> dict[str, Any] | None:
    """The sidecar of a capture PNG, or None (overlay circle captures have none: their region is in the selection)."""
    try:
        meta = json.loads(sidecar_path(Path(png)).read_text())
    except (OSError, ValueError):
        return None
    r = meta.get("region")
    if not (isinstance(r, list) and len(r) == 4 and isinstance(meta.get("scale"), (int, float)) and meta["scale"] > 0):
        return None
    return meta


def to_points(meta: dict[str, Any], px: float, py: float) -> tuple[float, float]:
    """A pixel in the saved image -> a screen point (top-left origin), using the sidecar."""
    x, y = meta["region"][0], meta["region"][1]
    return x + px / meta["scale"], y + py / meta["scale"]


def captures_dir(root: Path | None = None) -> Path:
    return (root or reach_dir()) / "captures"


def capture(target: str = "display", run: Runner = run_cmd, info: Callable[[], dict] = screen_info,
            root: Path | None = None) -> tuple[Path, dict[str, Any], str]:
    """Capture, check, downsize and save. Returns (png, sidecar, note). Raises NeedsPermission on failure/blank."""
    d = captures_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    ms = int(time.time() * 1000)
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="reach-screen-"))  # full-size raw never lands in captures/
    raw = tmp / f"raw-{ms}.png"  # screencapture refuses dot-file names
    si = info() or {}
    note = ""
    win = si.get("window") if target == "window" else None
    if target == "window" and not win:
        note = "could not find the frontmost window, so I looked at the whole display"
    if win:
        cmd, region, display = [SCREENCAPTURE, "-x", "-o", "-t", "png", "-l", str(win["id"]), str(raw)], \
            list(win["b"]), f"window:{win['id']}"
    else:
        sw, sh = (si.get("screen") or [0, 0])[:2]
        cmd, region, display = [SCREENCAPTURE, "-x", "-m", "-t", "png", str(raw)], [0, 0, sw, sh], "main"
    try:
        rc = run(cmd, CAPTURE_TIMEOUT_S)
        if rc != 0 or not raw.is_file() or raw.stat().st_size == 0:
            raise NeedsPermission(needs_permission_message())
        if is_blank(raw):
            raise NeedsPermission(needs_permission_message())
        png = d / f"screen-{ms}.png"
        ow, oh, nw, nh = downsize(raw, png)
    finally:
        raw.unlink(missing_ok=True)
        shutil.rmtree(tmp, ignore_errors=True)
    if not region[2] or not region[3]:  # no screen info: assume the capture's own pixel size at scale 1
        region = [0, 0, ow, oh]
    scale = nw / float(region[2])
    meta = write_sidecar(png, region, scale, display, source="see_screen", target="window" if win else "display",
                         app=(win or {}).get("owner") or si.get("front"), pixels=[nw, nh])
    return png, meta, note


def prune_captures(root: Path | None = None, max_age_s: float = RETENTION_S, now: float | None = None) -> int:
    """Delete regular .png/.json files directly in captures/ older than max_age_s. Returns how many were removed."""
    d = captures_dir(root)
    if not d.is_dir():
        return 0
    now = time.time() if now is None else now
    n = 0
    for p in d.iterdir():
        try:
            if p.is_symlink() or not p.is_file() or p.suffix.lower() not in (".png", ".json"):
                continue
            if now - p.stat().st_mtime > max_age_s:
                p.unlink()
                n += 1
        except OSError:
            continue
    return n


def enabled() -> bool:
    return os.environ.get("REACH_AUTO_SCREEN", "1") != "0"


def see_screen(question: str, target: str = "display", run: Runner = run_cmd, info: Callable[[], dict] = screen_info,
               send: Callable | None = None, root: Path | None = None) -> tuple[str, Path]:
    from . import vision

    png, _meta, note = capture(target, run=run, info=info, root=root)
    answer = vision.see(str(png), question, **({"send": send} if send else {}))
    return (f"({note}) " if note else "") + answer, png


class SeeScreenArgs(BaseModel):
    question: str = Field(description="What to find out about what is on the user's screen")
    target: Literal["display", "window"] = Field(
        default="display", description="'display' = the whole main display, 'window' = only the frontmost app window")


class SeeScreen(Tool):
    name: ClassVar[str] = "see_screen"
    description: ClassVar[str] = (
        "Take a screenshot of the user's screen (whole main display, or only the frontmost window) and answer a "
        "question about it with a vision model. Use it when the user asks about something on their screen ('what is "
        "this', 'what's on my screen', 'explain this error') and nothing is selected or circled. If a region is "
        "selected, use see_region instead. The screenshot is sent only to the vision model."
    )
    Args: ClassVar[type[BaseModel]] = SeeScreenArgs
    effects: ClassVar[frozenset[str]] = frozenset({"read", "network"})

    async def run(self, args: Any, ctx: ToolContext) -> ToolOutput:
        import asyncio

        if not enabled():
            return ToolOutput(status="error", content="see_screen is turned off (REACH_AUTO_SCREEN=0); ask the user "
                              "to circle the thing on screen instead.")
        try:
            answer, png = await asyncio.to_thread(see_screen, args.question, args.target)
        except NeedsPermission as e:
            return ToolOutput(status="error", content=str(e))
        except (PermissionError, FileNotFoundError, ValueError) as e:
            return ToolOutput(status="error", content=str(e))
        except Exception as e:
            msg = str(e) if str(e).startswith(("HTTP", "DEEPINFRA")) else type(e).__name__
            return ToolOutput(status="error", content=f"vision call failed: {msg}")
        from .vision import MODEL

        return ToolOutput(content=f"screen_captured=true image={png.name}\nVision model ({MODEL}) says:\n{answer}")
