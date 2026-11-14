"""Reach's secret-path policy: an Intern `tool_call` hook that refuses any call reaching for keys.

It runs before Intern's own guard and needs no System 1 call. It fails closed: any error inside the
check refuses the call (Intern's hooks fail open on an exception, so the check catches its own).

Two layers:
- an allowlist for file tools (read, grep, find): the real path (symlinks resolved) must lie inside the
  agent workspace or a `.reach/` folder Reach shows the agent (captures, browser output);
- a denylist over every path-like or command-like argument of every tool, for secret-bearing names
  (.env, .intern-home, ~/.ssh, ~/.aws, *.pem, id_rsa, keychain, env dumps, /proc/*/environ, .reach/run).

A refused call comes back to the model as a refused tool result with a plain reason, and is logged
in the Drex log as type `policy`.
"""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Any, ClassVar

from intern.hooks import Extension, Refuse, RunContext
from intern.messages import ToolCall

from .paths import reach_dir

FILE_TOOLS = frozenset({"read", "grep", "find", "write", "edit"})
SHELL_TOOLS = frozenset({"bash", "process", "shell"})
# Reach tools whose arguments are only free text the user sees; their text is not a path.
TEXT_ONLY_TOOLS = frozenset({"note_append", "shelf_to_note", "open_source", "evidence_search", "reach_now"})
# Do mode (EXPERIMENTAL): Finder tools keep paths inside the home folder themselves; only the secret check applies here.
HOME_PATH_TOOLS = frozenset({"finder_list", "finder_open", "finder_reveal"})
ALLOWED_REACH_SUBDIRS = ("workspace", "captures", "browser")

REASON = "Reach does not let the assistant read keys, secret files or environment dumps."
NEXT = "Answer without it. Never ask for or show keys; tell the user this is blocked for safety."

_SECRET_PATTERNS = [
    (r"(^|[/\s'\"=:@`(]|\.\./|\.\.)\.env(\b|$|[.\w*?\[])", "a .env file"),
    (r"\.intern-home", "the Intern key home"),
    (r"\.ssh(/|\b)", "~/.ssh"),
    (r"\.aws(/|\b)", "~/.aws"),
    (r"\.gnupg|\.netrc|\.npmrc|\.pypirc|\.git-credentials|\.docker/config", "a credentials file"),
    (r"\.pem\b|\.key\b|\.p12\b|\.pfx\b", "a key file"),
    (r"\bid_(rsa|dsa|ecdsa|ed25519)\b", "a private key"),
    (r"keychain|\bsecurity\s+(find|dump|export)", "the keychain"),
    (r"/proc/[^/\s]+/environ", "a process environment"),
    (r"(^|[;&|`(\s])(printenv|env|set|export\s+-p|declare\s+-x)(\s*$|\s*[;&|`)>]|\s+-0?\s*$)", "an environment dump"),
    (r"os\.environ|process\.env\b|getenv\(|ENV\[|\$ENV\{", "an environment dump"),
    (r"\.reach/run\b|/run/token\b|server\.json", "Reach's session token"),
    (r"\b(sk|rk|pk)-|api[_-]?key|secret|token|password", "key-shaped text"),
]
_SECRET_RE = [(re.compile(p, re.IGNORECASE), what) for p, what in _SECRET_PATTERNS]
# For "key-shaped text" we only scan shell/file tool arguments, not Reach tools' text (a note may say "token").
_KEYWORD_WHAT = "key-shaped text"


def workspace_dir() -> Path:
    return reach_dir() / "workspace"


def ensure_workspace() -> Path:
    ws = workspace_dir()
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def allowed_roots() -> list[Path]:
    base = reach_dir()
    return [(base / sub).resolve() for sub in ALLOWED_REACH_SUBDIRS]


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list | tuple):
        return [s for v in value for s in _strings(v)]
    return []


def _secret_hit(text: str, *, keywords: bool) -> str | None:
    for rx, what in _SECRET_RE:
        if what == _KEYWORD_WHAT and not keywords:
            continue
        if rx.search(text):
            return what
    return None


def _real(path: str, cwd: Path) -> Path:
    p = Path(os.path.expandvars(path)).expanduser()
    if not p.is_absolute():
        p = cwd / p
    return Path(os.path.realpath(p))  # follows every symlink, dangling ones included, and folds ..


def _expand(word: str, cwd: Path) -> list[str]:
    import glob

    word = word.lstrip("@<>")
    if not word or word.startswith("-"):
        return []
    if any(c in word for c in "*?["):
        base = os.path.expanduser(os.path.expandvars(word))
        pattern = base if os.path.isabs(base) else str(cwd / base)
        return glob.glob(pattern, include_hidden=True)[:200]
    return [word]


SECRET_NAMES = (".env", ".env.local", ".intern-home", "id_rsa", "id_ed25519", ".ssh", ".aws")


def _glob_could_match_secret(word: str) -> bool:
    """`.e*`, `.[e]nv`, `*env`: a glob that would match a secret name wherever the shell happens to be."""
    import fnmatch

    if not any(c in word for c in "*?["):
        return False
    tail = os.path.basename(word.rstrip("/")) or word
    return any(fnmatch.fnmatchcase(n, tail) for n in SECRET_NAMES if tail not in ("*", "**"))


def _inside(p: Path, roots: list[Path]) -> bool:
    return any(p == r or r in p.parents for r in roots)


def _path_reason(path: str, cwd: Path, roots: list[Path]) -> str | None:
    real = _real(path, cwd)
    hit = _secret_hit(str(real), keywords=False) or _secret_hit(path, keywords=False)
    if hit:
        return hit
    if not _inside(real, roots):
        return "a file outside Reach's workspace"
    return None


def check(name: str, args: dict[str, Any], cwd: Path | str, roots: list[Path] | None = None) -> str | None:
    """Why this call is refused, or None to let it through. Pure apart from resolving paths."""
    cwd = Path(cwd)
    roots = roots if roots is not None else allowed_roots()
    name = (name or "").lower()
    strings = _strings(args)
    if name in SHELL_TOOLS:
        for s in strings:
            hit = _secret_hit(s, keywords=True)
            if hit:
                return hit
            try:
                words = shlex.split(s)
            except ValueError:
                words = s.split()
            for w in words:  # each word as a path too (globs expanded): catches a symlink named innocently
                if _glob_could_match_secret(w):
                    return "a .env file"
                for cand in _expand(w, cwd):
                    if hit := _secret_hit(str(_real(cand, cwd)), keywords=False):
                        return hit
        return None
    if name in FILE_TOOLS:
        for s in strings:
            if hit := _secret_hit(s, keywords=False):
                return hit
        for key in ("path", "file_path", "directory"):
            if isinstance(args.get(key), str):
                if reason := _path_reason(args[key], cwd, roots):
                    return reason
        if name in ("grep", "find") and "path" not in args:
            if reason := _path_reason(".", cwd, roots):
                return reason
        return None
    if name in TEXT_ONLY_TOOLS:
        return None
    # Other tools (browser_task, see_region, future ones): path and URL arguments must not name secrets.
    for key, value in args.items():
        if not isinstance(value, str):
            continue
        if hit := _secret_hit(value, keywords=False):
            return hit
        low = value.lower()
        if low.startswith("file:"):
            return "a local file through the browser"
        if key.endswith("path") and value and name not in HOME_PATH_TOOLS:
            if reason := _path_reason(value, cwd, roots):
                return reason
    return None


def log_refusal(name: str, reason: str) -> None:
    from .ledger import append_tool_entry

    try:
        append_tool_entry("policy", tool=name, decision="refused", reason=reason)
    except Exception:
        pass  # the refusal stands even if the log cannot be written


class SecretPolicy(Extension):
    """Refuses tool calls that reach for keys or secret files, before Intern's guard runs."""

    name: ClassVar[str] = "reach-policy"

    async def tool_call(self, call: ToolCall, ctx: RunContext) -> Refuse | None:
        try:
            reason = check(call.name, dict(call.arguments or {}), ctx.cwd)
        except Exception as e:  # fail closed: a broken check never lets a call through
            reason = f"the safety check could not run ({type(e).__name__})"
        if reason is None:
            return None
        log_refusal(call.name, reason)
        return Refuse(reason=f"Refused by Reach's policy: this call reaches for {reason}. {REASON}", next_action=NEXT)
