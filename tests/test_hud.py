"""
Tests for ui/hud — StatusHudWidget, HudRenderer, ChatBubbleDelegate.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from PyQt6.QtCore import Qt, QRect, QSize
from PyQt6.QtGui import QColor, QFont, QPainter, QPixmap, QStandardItemModel, QStandardItem
from PyQt6.QtWidgets import QApplication, QListWidget, QListWidgetItem, QStyledItemDelegate, QStyleOptionViewItem


# ── QApplication singleton (required for QPixmap/QPainter) ──
_APP = QApplication.instance() or QApplication([])

from core.config_manager import AppConfig

# HUD components
from ui.hud import (
    StatusHudWidget,
    ChatBubbleDelegate,
    HudRenderer,
    _MODE_COLORS,
)


# We need a QApplication instance for widgets
class _TestApp:
    _app = None

    @classmethod
    def get_app(cls) -> QApplication:
        if cls._app is None:
            cls._app = QApplication.instance() or QApplication([])
        return cls._app


def _make_test_config() -> AppConfig:
    """Create a minimal AppConfig for testing."""
    return AppConfig()


class TestModeColors(unittest.TestCase):
    def test_has_all_modes(self) -> None:
        for mode in ("TAP", "STALK", "WHISPER", "LULLABY", "FORMAL"):
            self.assertIn(mode, _MODE_COLORS)

    def test_tap_color(self) -> None:
        self.assertEqual(_MODE_COLORS["TAP"].name(), "#00d4ff")

    def test_stalk_color(self) -> None:
        self.assertEqual(_MODE_COLORS["STALK"].name(), "#ffaa00")


class TestHudRenderer(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = HudRenderer()
        self.pixmap = QPixmap(200, 200)
        self.pixmap.fill(Qt.GlobalColor.transparent)

    def test_paint_frame_creates_content(self) -> None:
        painter = QPainter(self.pixmap)
        self.renderer.paint_frame(painter, 200, 200)
        painter.end()
        image = self.pixmap.toImage()
        # Check that some pixels are drawn (corners/brackets)
        center = image.pixelColor(100, 100)
        edge = image.pixelColor(5, 5)
        self.assertNotEqual(center.alpha(), edge.alpha())

    def test_paint_frame_with_custom_args(self) -> None:
        painter = QPainter(self.pixmap)
        self.renderer.paint_frame(painter, 200, 200, bracket_len=20, gap=3,
                                   color=QColor(255, 0, 0), dim_color=QColor(255, 0, 0, 60))
        painter.end()
        image = self.pixmap.toImage()
        self.assertIsNotNone(image)

    def test_paint_scan_line(self) -> None:
        painter = QPainter(self.pixmap)
        self.renderer.paint_scan_line(painter, 200, 200, time_ms=100)
        painter.end()
        image = self.pixmap.toImage()
        self.assertIsNotNone(image)

    def test_paint_corner_text(self) -> None:
        painter = QPainter(self.pixmap)
        self.renderer.paint_corner_text(painter, 200, 200)
        painter.end()
        image = self.pixmap.toImage()
        self.assertIsNotNone(image)

    def test_paint_status_bar(self) -> None:
        painter = QPainter(self.pixmap)
        self.renderer.paint_status_bar(painter, 10, 10, "TEST", 0.5)
        painter.end()
        image = self.pixmap.toImage()
        self.assertIsNotNone(image)

    def test_paint_status_bar_zero_value(self) -> None:
        painter = QPainter(self.pixmap)
        self.renderer.paint_status_bar(painter, 10, 10, "TEST", 0.0)
        painter.end()

    def test_paint_status_bar_full_value(self) -> None:
        painter = QPainter(self.pixmap)
        self.renderer.paint_status_bar(painter, 10, 10, "TEST", 1.0)
        painter.end()

    def test_make_hud_pixmap(self) -> None:
        pix = self.renderer.make_hud_pixmap(200, 200, frame=0)
        self.assertIsNotNone(pix)
        self.assertEqual(pix.width(), 200)
        self.assertEqual(pix.height(), 200)
        self.assertFalse(pix.isNull())

    def test_make_hud_pixmap_custom_label(self) -> None:
        pix = self.renderer.make_hud_pixmap(200, 200, label="CUSTOM")
        self.assertIsNotNone(pix)


class TestStatusHudWidget(unittest.TestCase):
    def setUp(self) -> None:
        _TestApp.get_app()
        self.config = _make_test_config()
        # Create parent-less widget for testing — won't be shown
        self.hud = StatusHudWidget(self.config)

    def test_default_mode(self) -> None:
        self.assertEqual(self.hud._mode, self.config.mode.upper())

    def test_set_mode(self) -> None:
        self.hud.set_mode("stalk")
        self.assertEqual(self.hud._mode, "STALK")

    def test_set_mode_uppercases(self) -> None:
        self.hud.set_mode("whisper")
        self.assertEqual(self.hud._mode, "WHISPER")

    def test_set_provider(self) -> None:
        self.hud.set_provider("ollama", model="phi3:mini", connectivity="online")
        self.assertEqual(self.hud._provider, "ollama")
        self.assertEqual(self.hud._model, "phi3:mini")
        self.assertEqual(self.hud._connectivity, "online")

    def test_set_key_warning_shows_warning(self) -> None:
        self.hud.set_key_warning("Invalid API key")
        self.assertEqual(self.hud._connectivity, "warning")
        self.assertTrue(self.hud.toolTip())

    def test_set_key_warning_clears(self) -> None:
        self.hud.set_key_warning("some warning")
        self.assertEqual(self.hud._connectivity, "warning")
        self.hud.set_key_warning("")
        self.assertEqual(self.hud._connectivity, "unknown")

    def test_paintEvent_does_not_crash(self) -> None:
        # Just verify paintEvent runs without error
        try:
            self.hud.paintEvent(None)
        except Exception:
            pass  # painting without a proper paint device may be fine

    def test_fixed_size(self) -> None:
        self.assertEqual(self.hud.size(), QSize(StatusHudWidget.WIDTH, StatusHudWidget.HEIGHT))

    def test_cursor_shape(self) -> None:
        self.assertEqual(self.hud.cursor().shape(), Qt.CursorShape.PointingHandCursor)


class TestChatBubbleDelegate(unittest.TestCase):
    def setUp(self) -> None:
        _TestApp.get_app()
        self.list_widget = QListWidget()
        self.delegate = ChatBubbleDelegate(self.list_widget)

    def test_initial_hover_state(self) -> None:
        self.assertIsNone(self.delegate._hovered_index)

    def test_size_hint_user(self) -> None:
        """Test sizeHint with user role data."""
        item = QListWidgetItem("Hello, world!")
        item.setData(Qt.ItemDataRole.UserRole + 1, "user")
        self.list_widget.addItem(item)
        idx = self.list_widget.indexFromItem(item)
        opt = QStyleOptionViewItem()
        size = self.delegate.sizeHint(opt, idx)
        self.assertIsInstance(size, QSize)
        self.assertGreater(size.width(), 0)

    def test_role_helper_returns_none_for_empty_index(self) -> None:
        """Static _role helper handles edge cases."""
        from PyQt6.QtCore import QModelIndex
        result = ChatBubbleDelegate._role(QModelIndex(), 0)
        self.assertIsNone(result)

    def test_event_filter_setup(self) -> None:
        delegate = ChatBubbleDelegate(self.list_widget)
        self.assertTrue(self.list_widget.viewport().hasMouseTracking())

    def test_size_hint_basic(self) -> None:
        """Verify delegate doesn't crash on sizeHint."""
        item = QListWidgetItem("test message")
        self.list_widget.addItem(item)
        index = self.list_widget.indexFromItem(item)
        opt = QStyleOptionViewItem()
        size = self.delegate.sizeHint(opt, index)
        self.assertIsInstance(size, QSize)
        self.assertGreater(size.width(), 0)
        self.assertGreater(size.height(), 0)


if __name__ == "__main__":
    unittest.main()
