from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import turn_anchor  # noqa: E402


def attachment(
    uuid: str,
    parent: str | None,
    content: object,
    *,
    hook_type: str = "hook_additional_context",
) -> dict[str, object]:
    return {
        "type": "attachment",
        "uuid": uuid,
        "parentUuid": parent,
        "attachment": {
            "type": hook_type,
            "hookEvent": "UserPromptSubmit",
            "content": content,
        },
    }


class TurnAnchorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.transcript = self.root / "session.jsonl"
        self.backups = self.root / "backups"
        self.anchor = self.root / "anchor.md"
        self.anchor.write_text("Keep the established role and tone.\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def write_lines(self, lines: list[object | str]) -> None:
        rendered = [
            item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            for item in lines
        ]
        self.transcript.write_text("\n".join(rendered) + "\n", encoding="utf-8")

    def read_objects(self) -> list[dict[str, object]]:
        return [
            json.loads(line)
            for line in self.transcript.read_text(encoding="utf-8").splitlines()
            if line.startswith("{")
        ]

    def test_prunes_only_marked_hook_attachments_and_preserves_links(self) -> None:
        lines: list[object | str] = [
            {
                "type": "user",
                "uuid": "u1",
                "parentUuid": None,
                "message": {
                    "role": "user",
                    "content": "The literal text <TURN_ANCHOR> is part of my message.",
                },
            },
            attachment(
                "a1",
                "u1",
                ["<TURN_ANCHOR>\nold one\n</TURN_ANCHOR>"],
            ),
            attachment("a2", "a1", ["unrelated context"]),
            attachment(
                "a3",
                "a2",
                ["<TURN_ANCHOR>\nold two\n</TURN_ANCHOR>"],
            ),
            attachment("a4", "a3", [], hook_type="hook_additional_context"),
            {
                "type": "assistant",
                "uuid": "s1",
                "parentUuid": "a4",
                "message": {"role": "assistant", "content": "done"},
            },
        ]
        self.write_lines(lines)
        original = self.transcript.read_text(encoding="utf-8")
        links_before = [
            (item.get("uuid"), item.get("parentUuid"))
            for item in self.read_objects()
        ]

        count = turn_anchor.prune_transcript(
            self.transcript, backup_root=self.backups
        )

        self.assertEqual(count, 2)
        objects = self.read_objects()
        self.assertEqual(len(objects), len(lines))
        self.assertIn(
            "<TURN_ANCHOR>",
            objects[0]["message"]["content"],  # type: ignore[index]
        )
        self.assertEqual(objects[1]["attachment"]["content"], [])  # type: ignore[index]
        self.assertEqual(
            objects[2]["attachment"]["content"],  # type: ignore[index]
            ["unrelated context"],
        )
        self.assertEqual(objects[3]["attachment"]["content"], [])  # type: ignore[index]
        links_after = [
            (item.get("uuid"), item.get("parentUuid")) for item in objects
        ]
        self.assertEqual(links_after, links_before)

        backups = list(self.backups.glob("session.jsonl.*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), original)

    def test_malformed_physical_line_is_preserved(self) -> None:
        malformed = '{"type":"partial"'
        self.write_lines(
            [
                malformed,
                attachment(
                    "a1",
                    None,
                    ["<TURN_ANCHOR>\nold\n</TURN_ANCHOR>"],
                ),
            ]
        )

        self.assertEqual(
            turn_anchor.prune_transcript(
                self.transcript, backup_root=self.backups
            ),
            1,
        )
        self.assertEqual(
            self.transcript.read_text(encoding="utf-8").splitlines()[0],
            malformed,
        )

    def test_second_prune_is_idempotent_and_writes_no_new_backup(self) -> None:
        self.write_lines(
            [
                attachment(
                    "a1",
                    None,
                    ["<TURN_ANCHOR>\nold\n</TURN_ANCHOR>"],
                )
            ]
        )
        self.assertEqual(
            turn_anchor.prune_transcript(
                self.transcript, backup_root=self.backups
            ),
            1,
        )
        snapshot = self.transcript.read_bytes()
        self.assertEqual(
            turn_anchor.prune_transcript(
                self.transcript, backup_root=self.backups
            ),
            0,
        )
        self.assertEqual(self.transcript.read_bytes(), snapshot)
        self.assertEqual(len(list(self.backups.glob("*.bak"))), 1)

    def test_backup_rotation_keeps_latest_three(self) -> None:
        for index in range(5):
            self.write_lines(
                [
                    attachment(
                        f"a{index}",
                        None,
                        [f"<TURN_ANCHOR>\nold {index}\n</TURN_ANCHOR>"],
                    )
                ]
            )
            self.assertEqual(
                turn_anchor.prune_transcript(
                    self.transcript, backup_root=self.backups
                ),
                1,
            )
        self.assertEqual(
            len(list(self.backups.glob("session.jsonl.*.bak"))),
            turn_anchor.BACKUP_KEEP,
        )

    def test_process_payload_prunes_then_returns_fresh_anchor(self) -> None:
        self.write_lines(
            [
                attachment(
                    "a1",
                    None,
                    ["<TURN_ANCHOR>\nold\n</TURN_ANCHOR>"],
                )
            ]
        )
        environment = {
            "CLAUDE_CONFIG_DIR": str(self.root / "config"),
            "CLAUDE_TURN_ANCHOR_FILE": str(self.anchor),
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            output = turn_anchor.process_payload(
                {"transcript_path": str(self.transcript)}
            )

        self.assertIsNotNone(output)
        context = output["hookSpecificOutput"]["additionalContext"]  # type: ignore[index]
        self.assertEqual(
            context,
            "<TURN_ANCHOR>\nKeep the established role and tone.\n</TURN_ANCHOR>",
        )
        self.assertEqual(
            self.read_objects()[0]["attachment"]["content"],  # type: ignore[index]
            [],
        )

    def test_missing_transcript_still_returns_anchor(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CLAUDE_TURN_ANCHOR_FILE": str(self.anchor)},
            clear=False,
        ):
            output = turn_anchor.process_payload(
                {"transcript_path": str(self.root / "not-created.jsonl")}
            )
        self.assertIsNotNone(output)

    def test_disable_switch_neither_prunes_nor_injects(self) -> None:
        self.write_lines(
            [
                attachment(
                    "a1",
                    None,
                    ["<TURN_ANCHOR>\nold\n</TURN_ANCHOR>"],
                )
            ]
        )
        original = self.transcript.read_bytes()
        with mock.patch.dict(
            os.environ,
            {
                "CLAUDE_TURN_ANCHOR_DISABLED": "1",
                "CLAUDE_TURN_ANCHOR_FILE": str(self.anchor),
            },
            clear=False,
        ):
            output = turn_anchor.process_payload(
                {"transcript_path": str(self.transcript)}
            )
        self.assertIsNone(output)
        self.assertEqual(self.transcript.read_bytes(), original)

    def test_prune_failure_does_not_add_another_anchor(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"CLAUDE_TURN_ANCHOR_FILE": str(self.anchor)},
            clear=False,
        ):
            output = turn_anchor.process_payload(
                {"transcript_path": str(self.root)}
            )
        self.assertIsNone(output)

    def test_anchor_file_must_not_include_marker_tags(self) -> None:
        self.anchor.write_text("<TURN_ANCHOR>duplicate</TURN_ANCHOR>\n")
        with self.assertRaises(ValueError):
            turn_anchor.load_anchor(self.anchor)


if __name__ == "__main__":
    unittest.main()
