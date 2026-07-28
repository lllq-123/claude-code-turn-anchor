#!/usr/bin/env python3
"""Keep one fresh turn-level anchor in a Claude Code session.

The UserPromptSubmit hook does two things in one process:

1. Replace older hook attachments containing <TURN_ANCHOR> with content=[].
2. Return the current anchor as additionalContext for the new prompt.

The transcript entry itself is retained so uuid/parentUuid links stay intact.
Only this project's marker is touched. All other messages and hook attachments
are preserved.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


MARKER_OPEN = "<TURN_ANCHOR>"
MARKER_CLOSE = "</TURN_ANCHOR>"
HOOK_EVENT = "UserPromptSubmit"
BACKUP_KEEP = 3
DISABLED_VALUES = {"1", "true", "yes", "on"}


def config_dir() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override).expanduser() if override else Path.home() / ".claude"


def anchor_file() -> Path:
    override = os.environ.get("CLAUDE_TURN_ANCHOR_FILE")
    return (
        Path(override).expanduser()
        if override
        else Path(__file__).resolve().with_name("anchor.md")
    )


def load_anchor(path: Path | None = None) -> str:
    """Read the user-editable anchor and wrap it in the private marker."""
    source = path or anchor_file()
    body = source.read_text(encoding="utf-8").strip()
    if not body:
        return ""
    if MARKER_OPEN in body or MARKER_CLOSE in body:
        raise ValueError(
            f"{source} must contain anchor text only; marker tags are added automatically"
        )
    return f"{MARKER_OPEN}\n{body}\n{MARKER_CLOSE}"


def _contains_marker(content: Any) -> bool:
    if isinstance(content, str):
        return MARKER_OPEN in content
    if isinstance(content, list):
        return any(isinstance(item, str) and MARKER_OPEN in item for item in content)
    return False


def _prune_object(obj: Any) -> bool:
    """Mutate one parsed JSONL object when it is one of our old attachments."""
    if not isinstance(obj, dict) or obj.get("type") != "attachment":
        return False
    attachment = obj.get("attachment")
    if not isinstance(attachment, dict):
        return False
    if attachment.get("type") != "hook_additional_context":
        return False
    if not _contains_marker(attachment.get("content")):
        return False
    attachment["content"] = []
    return True


def _backup_path(transcript: Path, backup_root: Path) -> Path:
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    stamp = time.time_ns()
    return backup_root / f"{transcript.name}.{stamp}.bak"


def _write_backup(transcript: Path, original: str, backup_root: Path) -> Path:
    destination = _backup_path(transcript, backup_root)
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(original)
        handle.flush()
        os.fsync(handle.fileno())
    destination.chmod(0o600)
    return destination


def _rotate_backups(transcript: Path, backup_root: Path) -> None:
    candidates = sorted(
        backup_root.glob(f"{transcript.name}.*.bak"),
        key=lambda item: item.stat().st_mtime_ns,
    )
    for old in candidates[:-BACKUP_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass


def _rewrite_lines(original: str) -> tuple[str, int]:
    """Return a minimally changed JSONL string and the number of pruned entries."""
    output: list[str] = []
    pruned = 0

    for physical_line in original.splitlines(keepends=True):
        body = physical_line.rstrip("\r\n")
        ending = physical_line[len(body) :]
        if not body.strip():
            output.append(physical_line)
            continue
        try:
            obj = json.loads(body)
        except (TypeError, ValueError):
            output.append(physical_line)
            continue
        if _prune_object(obj):
            output.append(
                json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + ending
            )
            pruned += 1
        else:
            output.append(physical_line)

    return "".join(output), pruned


def prune_transcript(
    transcript: Path,
    *,
    backup_root: Path | None = None,
) -> int:
    """Prune old anchors in-place while retaining the transcript inode.

    A full private backup is written before mutation. If rewriting fails, the
    original in-memory content is restored before the exception is re-raised.
    """
    backups = backup_root or config_dir() / "turn-anchor-backups"

    with transcript.open("r+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            original = handle.read()
            rewritten, pruned = _rewrite_lines(original)
            if pruned == 0:
                return 0

            backup = _write_backup(transcript, original, backups)
            try:
                handle.seek(0)
                handle.write(rewritten)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())

                expected_size = len(rewritten.encode("utf-8"))
                if os.fstat(handle.fileno()).st_size != expected_size:
                    raise OSError("transcript size verification failed")
            except Exception:
                handle.seek(0)
                handle.write(original)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
                raise

            if not backup.exists():
                raise OSError("backup disappeared after transcript rewrite")
            _rotate_backups(transcript, backups)
            return pruned
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def hook_output(anchor: str) -> dict[str, Any]:
    return {
        "suppressOutput": True,
        "hookSpecificOutput": {
            "hookEventName": HOOK_EVENT,
            "additionalContext": anchor,
        },
    }


def process_payload(payload: Any) -> dict[str, Any] | None:
    """Process one Claude Code hook payload.

    Fail closed: if an existing transcript cannot be pruned safely, do not add
    another anchor copy during that turn.
    """
    if not isinstance(payload, dict):
        return None
    if os.environ.get("CLAUDE_TURN_ANCHOR_DISABLED", "").lower() in DISABLED_VALUES:
        return None

    transcript_value = payload.get("transcript_path")
    if transcript_value:
        transcript = Path(str(transcript_value)).expanduser()
        if transcript.exists():
            try:
                prune_transcript(transcript)
            except Exception:
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
