#!/usr/bin/env python3
"""Safe, idempotent installer for claude-code-turn-anchor."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SOURCE_SCRIPT = ROOT / "src" / "turn_anchor.py"
EXAMPLE_ANCHOR = ROOT / "examples" / "anchor.md"
INSTALL_DIR_NAME = "claude-code-turn-anchor"
HOOK_TIMEOUT_SECONDS = 10


def default_config_dir() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override).expanduser() if override else Path.home() / ".claude"


def installed_script(config: Path) -> Path:
    return config / "hooks" / INSTALL_DIR_NAME / "turn_anchor.py"


def hook_command(config: Path) -> str:
    return f"python3 {shlex.quote(str(installed_script(config)))}"


def load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def add_hook(
    settings: dict[str, Any], command: str
) -> tuple[dict[str, Any], bool]:
    """Append our hook group without changing existing groups or handlers."""
    result = copy.deepcopy(settings)
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("settings.hooks must be a JSON object")
    groups = hooks.setdefault("UserPromptSubmit", [])
    if not isinstance(groups, list):
        raise ValueError("settings.hooks.UserPromptSubmit must be a JSON array")

    for group in groups:
        if not isinstance(group, dict):
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            continue
        for handler in handlers:
            if isinstance(handler, dict) and handler.get("command") == command:
                return result, False

    groups.append(
        {
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                    "timeout": HOOK_TIMEOUT_SECONDS,
                }
            ]
        }
    )
    return result, True


def remove_hook(
    settings: dict[str, Any], command: str
) -> tuple[dict[str, Any], bool]:
    """Remove only handlers whose command exactly matches this installation."""
    result = copy.deepcopy(settings)
    hooks = result.get("hooks")
    if not isinstance(hooks, dict):
        return result, False
    groups = hooks.get("UserPromptSubmit")
    if not isinstance(groups, list):
        return result, False

    changed = False
    kept_groups: list[Any] = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            kept_groups.append(group)
            continue
        kept_handlers = [
            handler
            for handler in group["hooks"]
            if not (
                isinstance(handler, dict) and handler.get("command") == command
            )
        ]
        if len(kept_handlers) != len(group["hooks"]):
            changed = True
        if kept_handlers:
            kept_group = copy.deepcopy(group)
            kept_group["hooks"] = kept_handlers
            kept_groups.append(kept_group)

    if changed:
        if kept_groups:
            hooks["UserPromptSubmit"] = kept_groups
        else:
            hooks.pop("UserPromptSubmit", None)
        if not hooks:
            result.pop("hooks", None)
    return result, changed


def backup_settings(settings_path: Path, backup_root: Path) -> Path | None:
    if not settings_path.exists():
        return None
    backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = backup_root / f"settings.json.{time.time_ns()}.bak"
    shutil.copy2(settings_path, destination)
    destination.chmod(0o600)
    return destination


def atomic_write_settings(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    payload = json.dumps(settings, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def copy_runtime_files(config: Path) -> tuple[Path, bool]:
    destination_dir = config / "hooks" / INSTALL_DIR_NAME
    destination_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

    destination_script = destination_dir / "turn_anchor.py"
    shutil.copy2(SOURCE_SCRIPT, destination_script)
    destination_script.chmod(0o755)

    destination_anchor = destination_dir / "anchor.md"
    anchor_created = not destination_anchor.exists()
    if anchor_created:
        shutil.copy2(EXAMPLE_ANCHOR, destination_anchor)
        destination_anchor.chmod(0o600)
    return destination_script, anchor_created


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install the turn-anchor UserPromptSubmit hook."
    )
    parser.add_argument("--dry-run", action="store_true", help="show the plan only")
    parser.add_argument("--yes", action="store_true", help="skip confirmation")
    parser.add_argument(
        "--config-dir",
        type=Path,
        help="Claude config directory (default: CLAUDE_CONFIG_DIR or ~/.claude)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = (args.config_dir or default_config_dir()).expanduser().resolve()
    settings_path = config / "settings.json"
    install_dir = config / "hooks" / INSTALL_DIR_NAME
    backups = config / "turn-anchor-backups"
    command = hook_command(config)

    try:
        current = load_settings(settings_path)
        updated, settings_changed = add_hook(current, command)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot prepare installation: {exc}", file=sys.stderr)
        return 1

    anchor_path = install_dir / "anchor.md"
    print("Installation plan:")
    print(f"  runtime:  {install_dir / 'turn_anchor.py'}")
    print(
        f"  anchor:   {anchor_path}"
        + (" (preserve existing file)" if anchor_path.exists() else "")
    )
    print(f"  settings: {settings_path}")
    print(
        "  hook:     "
        + ("already registered" if not settings_changed else "append UserPromptSubmit")
    )
    print(f"  backups:  {backups}")

    if args.dry_run:
        print("Dry run only; no files changed.")
        return 0
    if not args.yes:
        answer = input("Continue? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Cancelled.")
            return 0

    try:
        copy_runtime_files(config)
        if settings_changed:
            backup = backup_settings(settings_path, backups)
            atomic_write_settings(settings_path, updated)
            if backup:
                print(f"Settings backup: {backup}")
    except OSError as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        return 1

    print(f"Installed. Edit your anchor at: {anchor_path}")
    print("Restart Claude Code before testing the hook.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
