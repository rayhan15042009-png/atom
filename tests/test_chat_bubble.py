"""Regression tests for the chat panel: messages must be *visible*.

The delegate used to read the body text from ``UserRole + 0``, but every item is
created with ``setText()`` (DisplayRole), so paint() got an empty string, bailed
out, and the whole chat panel rendered blank while the model sat there full of
messages. These tests paint a real bubble and look at the pixels.
"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication, QListWidget, QListWidgetItem, QStyleOptionViewItem  # noqa: E402

from ui.overlay import ChatBubbleDelegate  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])


class TestChatBubbleRendering(unittest.TestCase):
    def setUp(self) -> None:
        self.list = QListWidget()
        self.list.resize(600, 400)
        self.delegate = ChatBubbleDelegate(self.list)
        self.list.setItemDelegate(self.delegate)
        self.list.show()

    def tearDown(self) -> None:
        self.list.close()
        self.list.deleteLater()

    def _item(self, text: str, role: str, ts: str = "12:00") -> QListWidgetItem:
        item = QListWidgetItem()
        item.setText(text)
        item.setData(Qt.ItemDataRole.UserRole + 1, role)
        item.setData(Qt.ItemDataRole.UserRole + 2, ts)
        item.setData(Qt.ItemDataRole.UserRole + 4, False)
        item.setData(Qt.ItemDataRole.UserRole + 5, False)
        self.list.addItem(item)
        return item

    def _paint(self, item: QListWidgetItem, width: int = 600) -> QImage:
        option = QStyleOptionViewItem()
        option.rect = self.list.visualItemRect(item)
        if option.rect.height() <= 0:
            option.rect.setRect(0, 0, width, 80)
        option.rect.setWidth(width)
        img = QImage(width, option.rect.height(), QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        painter = QPainter(img)
        self.delegate.paint(painter, option, self.list.model().index(self.list.row(item), 0))
        painter.end()
        return img

    def test_text_is_read_from_the_item(self) -> None:
        item = self._item("hello there", "assistant")
        index = self.list.model().index(self.list.row(item), 0)
        self.assertEqual(ChatBubbleDelegate._text(index), "hello there")

    def test_painted_bubble_has_pixels(self) -> None:
        item = self._item("a reply that must be visible", "assistant")
        img = self._paint(item)
        opaque = sum(
            1 for y in range(img.height()) for x in range(img.width())
            if img.pixelColor(x, y).alpha() > 0
        )
        self.assertGreater(opaque, 500, "the assistant bubble painted nothing")

    def test_user_bubble_is_right_aligned(self) -> None:
        item = self._item("question", "user")
        img = self._paint(item)
        left_half = sum(
            1 for y in range(img.height()) for x in range(min(80, img.width()))
            if img.pixelColor(x, y).alpha() > 0
        )
        right_half = sum(
            1 for y in range(img.height())
            for x in range(max(0, img.width() - 80), img.width())
            if img.pixelColor(x, y).alpha() > 0
        )
        self.assertGreater(right_half, left_half, "user bubble is not on the right")

    def test_row_height_grows_with_text(self) -> None:
        short = self.delegate.row_height("short", 600)
        long = self.delegate.row_height("word " * 400, 600)
        self.assertGreater(long, short)

    def test_thinking_placeholders_do_not_pile_up(self) -> None:
        """Animation used to look for the marker at the wrong role and add
        another placeholder every 500 ms, leaving a column of "thinking" rows."""
        from ui.overlay import AtomOverlay

        stub = type("Stub", (), {})()
        stub.chat_list = QListWidget()
        stub.chat_list.scrollToBottom = lambda: None
        stub._thinking_dots = 0
        stub._remove_thinking_items = lambda: AtomOverlay._remove_thinking_items(stub)
        stub._add_thinking_item = lambda: AtomOverlay._add_thinking_item(stub)
        for _ in range(5):
            AtomOverlay._animate_thinking(stub)
        markers = [
            stub.chat_list.item(i) for i in range(stub.chat_list.count())
            if stub.chat_list.item(i).data(Qt.ItemDataRole.UserRole + 6) == "thinking"
        ]
        self.assertEqual(len(markers), 1)

    def test_thinking_row_is_not_clipped(self) -> None:
        """The placeholder row carried a hard-coded 36px hint while the delegate
        painted a ~54px bubble, so "Atom is thinking" was cut in half."""
        from ui.overlay import AtomOverlay

        stub = type("Stub", (), {})()
        stub.chat_list = self.list
        stub._thinking_dots = 0
        AtomOverlay._add_thinking_item(stub)
        item = self.list.item(self.list.count() - 1)
        index = self.list.model().index(self.list.row(item), 0)
        hint = self.delegate.sizeHint(QStyleOptionViewItem(), index)
        self.assertGreaterEqual(
            hint.height(),
            self.delegate.row_height(item.text(), 600, with_ts=False),
            "the thinking row is shorter than the bubble it paints",
        )

    def _paint_ink_span(self, text: str, role: str, ts: str) -> tuple[int, int]:
        """Paint one bubble at its size hint; return (height, rows of text ink)."""
        item = self._item(text, role, ts=ts)
        index = self.list.model().index(self.list.row(item), 0)
        option = QStyleOptionViewItem()
        hint = self.delegate.sizeHint(option, index)
        option.rect = QRect(0, 0, 600, hint.height())
        img = QImage(600, hint.height(), QImage.Format.Format_ARGB32)
        img.fill(QColor(0, 0, 0, 0))
        painter = QPainter(img)
        self.delegate.paint(painter, option, index)
        painter.end()
        ink = [
            y for y in range(img.height())
            if any(
                img.pixelColor(x, y).red() > 200 and img.pixelColor(x, y).green() > 200
                for x in range(img.width())
            )
        ]
        self.assertTrue(ink, "the bubble painted no text")
        return hint.height(), ink[-1] - ink[0] + 1

    def test_text_without_timestamp_is_not_clipped(self) -> None:
        """paint() reserved 14px for the timestamp even when there was none,
        leaving too little room for the wrapped text: the thinking row was cut
        in half and long bodies lost their last line."""
        text = "word " * 120
        with_ts_hint, with_ts_ink = self._paint_ink_span(text, "assistant", "12:00")
        plain_hint, plain_ink = self._paint_ink_span(text, "system", "")
        self.assertEqual(with_ts_hint - plain_hint, 14)
        self.assertGreaterEqual(
            plain_ink, with_ts_ink - 4,
            "text is clipped when the bubble has no timestamp",
        )

    def test_long_reply_is_not_clipped(self) -> None:
        """The measured row must be at least as tall as the painted bubble."""
        text = "This is a long answer that wraps. " * 40
        item = self._item(text, "assistant")
        index = self.list.model().index(self.list.row(item), 0)
        option = QStyleOptionViewItem()
        hint = self.delegate.sizeHint(option, index)
        self.assertGreater(hint.height(), self.delegate.row_height(text, 600))


if __name__ == "__main__":
    unittest.main()
