"""Chat history must fit a character budget, not just a turn count.

The transcript is restored across restarts, so sixteen turns can easily be tens
of thousands of characters. Sent whole to a local model on CPU, prefill alone can
eat the request timeout — the answer arrives after the fallback instead of from
the model the user picked. History is now capped by characters as well, newest
turns first.
"""

from __future__ import annotations

import os
import sys
import types
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QListWidget, QListWidgetItem  # noqa: E402

import atom as atom_module  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])


class TestHistoryBudget(unittest.TestCase):
    def _app(self, turns: list[tuple[str, str]]) -> types.SimpleNamespace:
        lst = QListWidget()
        self.addCleanup(lst.deleteLater)
        for role, text in turns:
            item = QListWidgetItem()
            item.setText(text)
            item.setData(Qt.ItemDataRole.UserRole + 1, role)
            lst.addItem(item)
        return types.SimpleNamespace(
            overlay=types.SimpleNamespace(chat_list=lst),
            _HISTORY_CHAR_BUDGET=atom_module.AtomApp._HISTORY_CHAR_BUDGET,
        )

    def test_short_history_is_untouched(self) -> None:
        app = self._app([("user", "hi"), ("assistant", "hello")])
        self.assertEqual(
            atom_module.AtomApp._get_chat_history(app),
            [{"role": "user", "content": "hi"},
             {"role": "assistant", "content": "hello"}],
        )

    def test_long_history_is_trimmed_to_the_budget(self) -> None:
        turns = [("user", "q" * 500), ("assistant", "a" * 500)] * 8  # 8000 chars
        app = self._app(turns)
        history = atom_module.AtomApp._get_chat_history(app)
        total = sum(len(t["content"]) for t in history)
        self.assertLessEqual(
            total, atom_module.AtomApp._HISTORY_CHAR_BUDGET,
            "history still exceeds the character budget",
        )
        self.assertTrue(history, "the newest turn must always be kept")
        self.assertEqual(history[-1]["content"], "a" * 500, "newest turn was dropped")

    def test_at_least_the_newest_turn_survives(self) -> None:
        app = self._app([("user", "x" * 9000)])
        history = atom_module.AtomApp._get_chat_history(app)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["content"], "x" * 9000)

    def test_turn_count_is_still_capped(self) -> None:
        app = self._app([("user", "hi"), ("assistant", "hello")] * 20)
        self.assertLessEqual(len(atom_module.AtomApp._get_chat_history(app)), 16)

    def test_thinking_row_is_not_history(self) -> None:
        app = self._app([("user", "hi")])
        thinking = QListWidgetItem()
        thinking.setText("\u23f3  Atom is thinking...")
        thinking.setData(Qt.ItemDataRole.UserRole + 6, "thinking")
        app.overlay.chat_list.addItem(thinking)
        self.assertEqual(
            [t["content"] for t in atom_module.AtomApp._get_chat_history(app)], ["hi"]
        )


if __name__ == "__main__":
    unittest.main()
