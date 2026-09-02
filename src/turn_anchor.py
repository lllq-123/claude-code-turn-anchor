#!/usr/bin/env python3
"""Append a short turn anchor at a sparse, stable cadence.

The hook never reads or rewrites the Claude Code transcript.  Each session gets
an external counter; the first prompt and then every Nth prompt receive the
anchor as fresh additionalContext.  Historical messages remain append-only,
which is required by preserved-thinking models such as Claude Fable 5.1.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


MARKER_OPEN = "<TURN_ANCHOR>"
MARKER_CLOSE = "</TURN_ANCHOR>"
HOOK_EVENT = "UserPromptSubmit"
DEFAULT_INTERVAL = 15
DISABLED_VALUES = {"1", "true", "yes", "on"}


def config_dir() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override).expanduser() if override else Path.home() / ".claude"


def anchor_file() -> Path:
    override = os.environ.get("CLAUDE_TURN_ANCHOR_FILE")
    return Path(override).expanduser() if override else Path(__file__).resolve().with_name("anchor.md")


def state_dir() -> Path:
    override = os.environ.get("CLAUDE_TURN_ANCHOR_STATE_DIR")
    return Path(override).expanduser() if override else config_dir() / "turn-anchor-state"


def interval() -> int:
    raw = os.environ.get("CLAUDE_TURN_ANCHOR_INTERVAL", str(DEFAULT_INTERVAL))
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_INTERVAL
    return value if value > 0 else DEFAULT_INTERVAL


def load_anchor(path: Path | None = None) -> str:
    source = path or anchor_file()
    body = source.read_text(encoding="utf-8").strip()
    if not body:
        return ""
    if MARKER_OPEN in body or MARKER_CLOSE in body:
        raise ValueError(f"{source} must contain anchor text only; marker tags are added automatically")
    return f"{MARKER_OPEN}\n{body}\n{MARKER_CLOSE}"


def _safe_session_id(value: Any) -> str:
    session_id = str(value or "").strip()
    if not session_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in session_id):
        return ""
    return session_id


def advance_counter(session_id: str, *, root: Path | None = None, every: int | None = None) -> bool:
    """Atomically advance one session counter and return whether to emit."""
    target_root = root or state_dir()
    cadence = every or interval()
    target_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    state = target_root / f"{session_id}.count"
    lock = target_root / f"{session_id}.lock"

    with lock.open("a+", encoding="utf-8") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            try:
                count = int(state.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                count = 0
            count += 1

            descriptor, temporary_name = tempfile.mkstemp(prefix=f".{state.name}.", suffix=".tmp", dir=target_root)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(f"{count}\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                temporary.chmod(0o600)
                os.replace(temporary, state)
            finally:
                if temporary.exists():
                    temporary.unlink()
            return (count - 1) % cadence == 0
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def hook_output(anchor: str) -> dict[str, Any]:
    return {
        "suppressOutput": True,
        "hookSpecificOutput": {"hookEventName": HOOK_EVENT, "additionalContext": anchor},
    }


def process_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    if os.environ.get("CLAUDE_TURN_ANCHOR_DISABLED", "").lower() in DISABLED_VALUES:
        return None

    session_id = _safe_session_id(payload.get("session_id"))
    if not session_id:
        return None
    try:
        emit = advance_counter(session_id)
    except OSError:
        return None
    if not emit:
        return None

    try:
        anchor = load_anchor()
    except (OSError, ValueError):
        return None
    return hook_output(anchor) if anchor else None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (TypeError, ValueError):
        return 0
    output = process_payload(payload)
    if output is not None:
        json.dump(output, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
