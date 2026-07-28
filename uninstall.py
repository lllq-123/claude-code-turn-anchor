#!/usr/bin/env python3
"""Precise, recoverable uninstaller for claude-code-turn-anchor."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import install


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Uninstall the turn-anchor UserPromptSubmit hook."
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
    config = (args.config_dir or install.default_config_dir()).expanduser().resolve()
    settings_path = config / "settings.json"
    install_dir = config / "hooks" / install.INSTALL_DIR_NAME
    backups = config / "turn-anchor-backups"
    command = install.hook_command(config)

    try:
        current = install.load_settings(settings_path)
        updated, settings_changed = install.remove_hook(current, command)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Cannot prepare uninstall: {exc}", file=sys.stderr)
        return 1

    print("Uninstall plan:")
    print(
        "  settings: "
        + ("remove exact hook command" if settings_changed else "hook not registered")
    )
    print(
        "  files:    "
        + (
            f"move {install_dir} into {backups}"
            if install_dir.exists()
            else "install directory not found"
        )
    )
    print("  backups:  keep transcript and settings backups")

    if args.dry_run:
        print("Dry run only; no files changed.")
        return 0
    if not args.yes:
        answer = input("Continue? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Cancelled.")
            return 0

    try:
        if settings_changed:
            backup = install.backup_settings(settings_path, backups)
            install.atomic_write_settings(settings_path, updated)
            if backup:
                print(f"Settings backup: {backup}")

        if install_dir.exists():
            backups.mkdir(mode=0o700, parents=True, exist_ok=True)
            archive = backups / f"uninstalled-files.{time.time_ns()}"
            shutil.move(str(install_dir), archive)
            print(f"Installed files moved to: {archive}")
    except OSError as exc:
        print(f"Uninstall failed: {exc}", file=sys.stderr)
        return 1

    print("Uninstalled. Restart Claude Code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
