from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import install  # noqa: E402
import uninstall  # noqa: E402


class InstallerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.config = Path(self.tempdir.name) / ".claude"
        self.command = install.hook_command(self.config.resolve())

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_add_hook_preserves_existing_settings_and_is_idempotent(self) -> None:
        original = {
            "model": "claude-example",
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python3 /opt/existing.py",
                                "timeout": 5,
                            }
                        ]
                    }
                ],
                "PostToolUse": [{"hooks": [{"type": "command", "command": "lint"}]}],
            },
        }
        untouched = copy.deepcopy(original)

        once, changed = install.add_hook(original, self.command)
        twice, changed_twice = install.add_hook(once, self.command)

        self.assertTrue(changed)
        self.assertFalse(changed_twice)
        self.assertEqual(original, untouched)
        self.assertEqual(once, twice)
        self.assertEqual(once["model"], "claude-example")
        self.assertEqual(
            once["hooks"]["PostToolUse"], original["hooks"]["PostToolUse"]
        )
        commands = [
            handler["command"]
            for group in once["hooks"]["UserPromptSubmit"]
            for handler in group["hooks"]
        ]
        self.assertEqual(commands, ["python3 /opt/existing.py", self.command])

    def test_remove_hook_is_exact_and_keeps_other_handlers_in_group(self) -> None:
        settings = {
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {"type": "command", "command": self.command},
                            {
                                "type": "command",
                                "command": self.command + " --different",
                            },
                        ]
                    },
                    {"hooks": [{"type": "command", "command": "keep-me"}]},
                ]
            }
        }

        updated, changed = install.remove_hook(settings, self.command)

        self.assertTrue(changed)
        commands = [
            handler["command"]
            for group in updated["hooks"]["UserPromptSubmit"]
            for handler in group["hooks"]
        ]
        self.assertEqual(commands, [self.command + " --different", "keep-me"])

    def test_install_reinstall_and_uninstall_round_trip(self) -> None:
        buffer = StringIO()
        with redirect_stdout(buffer):
            self.assertEqual(
                install.main(
                    ["--yes", "--config-dir", str(self.config)]
                ),
                0,
            )

        settings_path = self.config / "settings.json"
        install_dir = self.config / "hooks" / install.INSTALL_DIR_NAME
        anchor_path = install_dir / "anchor.md"
        self.assertTrue((install_dir / "turn_anchor.py").exists())
        self.assertTrue(anchor_path.exists())
        first_settings = json.loads(settings_path.read_text(encoding="utf-8"))

        anchor_path.write_text("my custom anchor\n", encoding="utf-8")
        with redirect_stdout(StringIO()):
            self.assertEqual(
                install.main(
                    ["--yes", "--config-dir", str(self.config)]
                ),
                0,
            )
        self.assertEqual(anchor_path.read_text(encoding="utf-8"), "my custom anchor\n")
        second_settings = json.loads(settings_path.read_text(encoding="utf-8"))
        self.assertEqual(second_settings, first_settings)

        with redirect_stdout(StringIO()):
            self.assertEqual(
                uninstall.main(
                    ["--yes", "--config-dir", str(self.config)]
                ),
                0,
            )
        final_settings = json.loads(settings_path.read_text(encoding="utf-8"))
        _, still_registered = install.remove_hook(final_settings, self.command)
        self.assertFalse(still_registered)
        self.assertFalse(install_dir.exists())
        archives = list(
            (self.config / "turn-anchor-backups").glob("uninstalled-files.*")
        )
        self.assertEqual(len(archives), 1)
        self.assertEqual(
            (archives[0] / "anchor.md").read_text(encoding="utf-8"),
            "my custom anchor\n",
        )

    def test_dry_run_creates_nothing(self) -> None:
        with redirect_stdout(StringIO()):
            self.assertEqual(
                install.main(
                    ["--dry-run", "--config-dir", str(self.config)]
                ),
                0,
            )
        self.assertFalse(self.config.exists())


if __name__ == "__main__":
    unittest.main()
