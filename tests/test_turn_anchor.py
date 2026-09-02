from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import turn_anchor  # noqa: E402


class TurnAnchorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.anchor = self.root / "anchor.md"
        self.anchor.write_text("Keep the established role and tone.\n", encoding="utf-8")
        self.env = {
            "CLAUDE_CONFIG_DIR": str(self.root / "config"),
            "CLAUDE_TURN_ANCHOR_FILE": str(self.anchor),
            "CLAUDE_TURN_ANCHOR_STATE_DIR": str(self.root / "state"),
            "CLAUDE_TURN_ANCHOR_INTERVAL": "15",
        }

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def call(self, session_id: str = "session-1"):
        with mock.patch.dict(os.environ, self.env, clear=False):
            return turn_anchor.process_payload({"session_id": session_id})

    def test_first_and_sixteenth_prompt_emit(self) -> None:
        self.assertIsNotNone(self.call())
        for _ in range(14):
            self.assertIsNone(self.call())
        self.assertIsNotNone(self.call())

    def test_sessions_have_independent_counters(self) -> None:
        self.assertIsNotNone(self.call("one"))
        self.assertIsNone(self.call("one"))
        self.assertIsNotNone(self.call("two"))

    def test_counter_survives_process_calls(self) -> None:
        self.assertIsNotNone(self.call())
        state = self.root / "state" / "session-1.count"
        self.assertEqual(state.read_text(encoding="utf-8"), "1\n")
        self.assertIsNone(self.call())
        self.assertEqual(state.read_text(encoding="utf-8"), "2\n")

    def test_custom_interval(self) -> None:
        self.env["CLAUDE_TURN_ANCHOR_INTERVAL"] = "3"
        self.assertIsNotNone(self.call())
        self.assertIsNone(self.call())
        self.assertIsNone(self.call())
        self.assertIsNotNone(self.call())

    def test_invalid_interval_falls_back_to_fifteen(self) -> None:
        self.env["CLAUDE_TURN_ANCHOR_INTERVAL"] = "nope"
        self.assertIsNotNone(self.call())
        self.assertIsNone(self.call())

    def test_transcript_is_never_read_or_written(self) -> None:
        transcript = self.root / "session.jsonl"
        original = b'{"type":"attachment","attachment":{"content":["<TURN_ANCHOR>old</TURN_ANCHOR>"]}}\n'
        transcript.write_bytes(original)
        with mock.patch.dict(os.environ, self.env, clear=False):
            output = turn_anchor.process_payload({"session_id": "session-1", "transcript_path": str(transcript)})
        self.assertIsNotNone(output)
        self.assertEqual(transcript.read_bytes(), original)

    def test_disable_switch_is_silent_and_does_not_count(self) -> None:
        self.env["CLAUDE_TURN_ANCHOR_DISABLED"] = "1"
        self.assertIsNone(self.call())
        self.assertFalse((self.root / "state").exists())

    def test_missing_or_unsafe_session_id_is_silent(self) -> None:
        with mock.patch.dict(os.environ, self.env, clear=False):
            self.assertIsNone(turn_anchor.process_payload({}))
            self.assertIsNone(turn_anchor.process_payload({"session_id": "../bad"}))

    def test_anchor_file_must_not_include_marker_tags(self) -> None:
        self.anchor.write_text("<TURN_ANCHOR>duplicate</TURN_ANCHOR>\n")
        with self.assertRaises(ValueError):
            turn_anchor.load_anchor(self.anchor)


if __name__ == "__main__":
    unittest.main()
