"""Tests for ``core.chat_history`` — the persisted chat transcript.

The GUI overlay never read ``data/chat_history.json``: the CLI wrote the
conversation there and ``atom.py`` opened an empty chat panel every launch, with
no memory of the last conversation. Both front ends now share this module, so
the format is worth pinning down — including the legacy ``{role, content}`` shape
the CLI has already written to disk.
"""

from __future__ import annotations

import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import chat_history as ch


class _TempHistory(unittest.TestCase):
    """Every test gets its own transcript file."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "chat_history.json"
        patcher = mock.patch.object(ch, "HISTORY_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, payload) -> None:
        self.path.write_text(json.dumps(payload), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Reading
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoad(_TempHistory):
    def test_missing_file_is_an_empty_transcript(self) -> None:
        self.assertEqual(ch.load_chat_history(), [])

    def test_round_trip(self) -> None:
        entries = [{"role": "user", "content": "hi"},
                   {"role": "assistant", "content": "hello"}]
        ch.save_chat_history(entries)
        self.assertEqual(ch.load_chat_history(), entries)

    def test_corrupt_json_is_an_empty_transcript(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(ch.load_chat_history(), [])

    def test_non_list_payload_is_an_empty_transcript(self) -> None:
        self.write({"role": "user", "content": "hi"})
        self.assertEqual(ch.load_chat_history(), [])

    def test_legacy_entries_without_a_timestamp_still_load(self) -> None:
        """atom_cli.py has been writing this shape since the beginning."""
        self.write([{"role": "user", "content": "old"},
                    {"role": "assistant", "content": "older"}])
        self.assertEqual(
            ch.load_chat_history(),
            [{"role": "user", "content": "old"},
             {"role": "assistant", "content": "older"}],
        )

    def test_timestamps_survive_a_round_trip(self) -> None:
        ch.save_chat_history([{"role": "user", "content": "hi",
                               "ts": "2026-09-25T07:31:02"}])
        self.assertEqual(ch.load_chat_history()[0]["ts"], "2026-09-25T07:31:02")

    def test_unusable_entries_are_dropped(self) -> None:
        self.write([
            {"role": "user", "content": "keep me"},
            "not a dict",
            {"role": "user"},
            {"content": "no role"},
            {"role": "user", "content": ""},
            {"role": "user", "content": "   "},
            {"role": "narrator", "content": "not a conversation role"},
            {"role": "user", "content": 42},
        ])
        self.assertEqual(ch.load_chat_history(),
                         [{"role": "user", "content": "keep me"}])

    def test_system_rows_are_not_part_of_the_transcript(self) -> None:
        """Panel status rows (greetings, action previews) are presentation."""
        self.write([{"role": "system", "content": "*tap* hello"}])
        self.assertEqual(ch.load_chat_history(), [])

    def test_only_the_newest_entries_are_returned(self) -> None:
        self.write([{"role": "user", "content": f"m{i}"} for i in range(10)])
        got = ch.load_chat_history(max_items=3)
        self.assertEqual([e["content"] for e in got], ["m7", "m8", "m9"])


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Writing
# ═══════════════════════════════════════════════════════════════════════════════

class TestSave(_TempHistory):
    def test_save_writes_a_json_list(self) -> None:
        ch.save_chat_history([{"role": "assistant", "content": "yo"}])
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")),
                         [{"role": "assistant", "content": "yo"}])

    def test_save_creates_the_data_directory(self) -> None:
        nested = Path(self._tmp.name) / "data" / "chat_history.json"
        ch.save_chat_history([{"role": "user", "content": "x"}], path=nested)
        self.assertTrue(nested.exists())

    def test_save_drops_unusable_entries(self) -> None:
        ch.save_chat_history([{"role": "system", "content": "no"},
                              {"role": "user", "content": "yes"}])
        self.assertEqual(ch.load_chat_history(),
                         [{"role": "user", "content": "yes"}])

    def test_save_keeps_the_newest_entries_only(self) -> None:
        ch.save_chat_history([{"role": "user", "content": f"m{i}"}
                              for i in range(ch.MAX_STORED + 25)])
        kept = ch.load_chat_history(max_items=1000)
        self.assertEqual(len(kept), ch.MAX_STORED)
        self.assertEqual(kept[-1]["content"], f"m{ch.MAX_STORED + 24}")

    def test_the_transcript_reaches_back_past_fifty_turns(self) -> None:
        """The panel used to lose everything older than the last 50 talks."""
        ch.save_chat_history([{"role": "user", "content": f"m{i}"}
                              for i in range(120)])
        self.assertEqual(len(ch.load_chat_history()), 120)
        self.assertGreaterEqual(ch.MAX_STORED, 200,
                                "the on-disk cap must allow scrolling well back")
        self.assertEqual(ch.MAX_RESTORED, ch.MAX_STORED,
                         "the panel should restore everything that is stored")

    def test_a_failed_write_does_not_raise(self) -> None:
        """A read-only disk must not take the UI thread down."""
        with mock.patch.object(Path, "write_text", side_effect=OSError("nope")):
            ch.save_chat_history([{"role": "user", "content": "x"}])  # must not raise


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Appending
# ═══════════════════════════════════════════════════════════════════════════════

class TestAppend(_TempHistory):
    def test_append_returns_the_grown_list_and_persists_it(self) -> None:
        entries = ch.append_chat_message([], "user", "hello")
        self.assertEqual(entries, [{"role": "user", "content": "hello",
                                    "ts": entries[0]["ts"]}])
        self.assertEqual(ch.load_chat_history(), entries)

    def test_append_records_a_timestamp(self) -> None:
        entries = ch.append_chat_message([], "assistant", "hi")
        parsed = ch.parse_timestamp(entries[0]["ts"])
        self.assertIsNotNone(parsed)
        self.assertLess(abs((datetime.datetime.now() - parsed).total_seconds()), 60)

    def test_append_accepts_an_explicit_timestamp(self) -> None:
        when = datetime.datetime(2026, 1, 2, 3, 4, 5)
        entries = ch.append_chat_message([], "user", "hi", ts=when)
        self.assertEqual(ch.parse_timestamp(entries[0]["ts"]), when)

    def test_append_ignores_non_conversation_roles(self) -> None:
        entries = ch.append_chat_message([], "system", "*tap* hello")
        self.assertEqual(entries, [])
        self.assertEqual(ch.load_chat_history(), [])

    def test_append_trims_to_the_cap(self) -> None:
        entries: list[dict] = []
        for i in range(ch.MAX_STORED + 10):
            entries = ch.append_chat_message(entries, "user", f"m{i}")
        self.assertEqual(len(entries), ch.MAX_STORED)
        self.assertEqual(entries[0]["content"], "m10")


class TestTimestampParsing(unittest.TestCase):
    def test_round_trip(self) -> None:
        when = datetime.datetime(2026, 9, 25, 7, 31, 2)
        self.assertEqual(ch.parse_timestamp(when.isoformat(timespec="seconds")), when)

    def test_missing_or_junk_values_are_none(self) -> None:
        for value in (None, "", 5, "not-a-date", "2026-13-45T99:99:99"):
            with self.subTest(value=value):
                self.assertIsNone(ch.parse_timestamp(value))


if __name__ == "__main__":
    unittest.main()
