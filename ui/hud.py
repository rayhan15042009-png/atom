"""
HUD components — shared UI widgets for the Atom overlay.

Extracted from ui/overlay.py for Step 5 consolidation.
"""

from __future__ import annotations

import datetime
import math
from typing import Any, Optional

from PyQt6.QtCore import Qt, QEvent, QPointF, QRect, QRectF, QSize, QObject
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QLinearGradient,
    QPainter, QPainterPath, QPen, QPixmap, QRadialGradient,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QStyledItemDelegate, QStyleOptionViewItem,
    QAbstractItemView,
)

from core.config_manager import AppConfig

# ═══════════════════════════════════════════════════════════════════════
#  Mode colours
# ═══════════════════════════════════════════════════════════════════════

_MODE_COLORS: dict[str, QColor] = {
    "TAP": QColor(0, 212, 255),
    "STALK": QColor(255, 170, 0),
    "WHISPER": QColor(0, 255, 136),
    "LULLABY": QColor(170, 102, 255),
    "FORMAL": QColor(255, 255, 255),
}


# ═══════════════════════════════════════════════════════════════════════
#  StatusHudWidget — persistent mode/status indicator
# ═══════════════════════════════════════════════════════════════════════

class StatusHudWidget(QWidget):
    """Persistent HUD indicator showing current mode, provider, and model.

    Positioned at bottom-left of the overlay. Click opens Settings.
    """

    WIDTH = 175
    HEIGHT = 28
    MARGIN = 8

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._mode = config.mode.upper()
        self._provider = "?"
        self._model = "?"
        self._connectivity = "unknown"
        self._mode_color = _MODE_COLORS.get(self._mode, _MODE_COLORS["TAP"])
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_mode(self, mode: str) -> None:
        mode = mode.upper()
        self._mode = mode
        self._mode_color = _MODE_COLORS.get(mode, _MODE_COLORS["TAP"])
        self.update()

    def set_provider(self, provider: str, model: str = "", connectivity: str = "unknown") -> None:
        self._provider = provider
        self._model = model
        self._connectivity = connectivity
        self.update()

    def set_key_warning(self, warning: str) -> None:
        if warning:
            self._connectivity = "warning"
            self.setToolTip(warning)
        else:
            self._connectivity = "unknown"
            self.setToolTip("")
        self.update()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # Background panel
        painter.setBrush(QBrush(QColor(10, 15, 26, 220)))
        painter.setPen(QPen(QColor(0, 212, 255, 50), 1))
        painter.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

        # Connectivity dot
        dot_color = {
            "online": QColor(0, 255, 136),
            "offline": QColor(255, 68, 68),
            "unknown": QColor(255, 184, 0),
            "warning": QColor(255, 140, 0),
        }.get(self._connectivity, QColor(255, 184, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(dot_color))
        painter.drawEllipse(QPointF(10, h // 2), 4, 4)

        # Mode badge
        painter.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        painter.setPen(self._mode_color)
        painter.drawText(22, 2, w - 24, h, Qt.AlignmentFlag.AlignVCenter, self._mode)

        # Provider/model (right side, elided)
        painter.setFont(QFont("Courier New", 7))
        label = self._provider
        if self._model:
            label += f" \u2022 {self._model}"
        painter.setPen(QColor(138, 173, 204, 150))
        fm = painter.fontMetrics()
        max_w = w - 80
        elided = fm.elidedText(label, Qt.TextElideMode.ElideRight, max_w)
        lw = fm.horizontalAdvance(elided)
        painter.drawText(w - lw - 6, 2, lw, h, Qt.AlignmentFlag.AlignVCenter, elided)
        painter.end()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            parent = self.parent()
            while parent and not hasattr(parent, "settings_changed"):
                parent = parent.parent()
            if parent and hasattr(parent, "_open_settings"):
                parent._open_settings()  # type: ignore[union-attr]
        super().mousePressEvent(event)


# ═══════════════════════════════════════════════════════════════════════
#  ChatBubbleDelegate — styled chat bubbles with tails
# ═══════════════════════════════════════════════════════════════════════

class ChatBubbleDelegate(QStyledItemDelegate):
    """Styled chat bubbles with tails, hover timestamps, and gradient fills."""

    _FONT_BUBBLE = QFont("Segoe UI", 11)
    _FONT_TIME = QFont("Segoe UI", 8)

    USER_BG_A = QColor(0, 60, 200, 225)
    USER_BG_B = QColor(0, 100, 255, 235)
    ASSISTANT_BG = QColor(12, 18, 30, 248)
    ASSISTANT_BORDER = QColor(0, 212, 255, 76)
    TEXT_COLOR = QColor(240, 242, 248, 250)
    TIME_COLOR = QColor(160, 162, 172, 150)
    TAIL_SIZE = 8

    _MARGIN = 6
    _PAD = 12
    _RADIUS = 8
    _WIDTH_RATIO = 0.78

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._hovered_index: Optional[int] = None
        if parent is not None and hasattr(parent, "viewport"):
            parent.viewport().setMouseTracking(True)
            parent.viewport().installEventFilter(self)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        parent = self.parent()
        if parent is not None and hasattr(parent, "viewport") and obj is parent.viewport():
            if event.type() == QEvent.Type.MouseMove:
                pos = event.position().toPoint()  # type: ignore[union-attr]
                item = parent.itemAt(pos)
                if item:
                    new_idx = parent.indexFromItem(item).row()
                    if new_idx != self._hovered_index:
                        self._hovered_index = new_idx
                        obj.update()
            elif event.type() == QEvent.Type.Leave:
                self._hovered_index = None
                obj.update()
        return super().eventFilter(obj, event)

    @staticmethod
    def _role(index: QAbstractItemView.ModelIndex, offset: int = 1) -> Any:
        return index.data(Qt.ItemDataRole.UserRole + offset)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QAbstractItemView.ModelIndex) -> None:  # type: ignore[override]
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        role = self._role(index, 1) or "assistant"
        text = self._role(index, 0) or ""
        ts = self._role(index, 2) or ""
        if not text:
            return

        is_user = role == "user"
        w = option.rect.width()
        max_w = int(w * self._WIDTH_RATIO)

        painter.setFont(self._FONT_BUBBLE)
        tr = painter.boundingRect(QRectF(0, 0, max_w - self._PAD * 2, 0),
                                   Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, text)
        bw = max(80, min(max_w, int(tr.width()) + self._PAD * 2 + 4))
        th = 14 if ts else 0
        bh = max(36, int(tr.height()) + self._PAD * 2 + th + 4)
        hovered = self._hovered_index is not None and index.row() == self._hovered_index

        bx = w - bw - self._MARGIN - self.TAIL_SIZE if is_user else self._MARGIN + self.TAIL_SIZE
        by = option.rect.y() + 3
        r = QRectF(bx, by, bw, bh)

        # Tail path
        tail = QPainterPath()
        if is_user:
            tail.moveTo(r.right(), r.y() + r.height() * 0.25)
            tail.lineTo(r.right() + self.TAIL_SIZE, r.y() + self.TAIL_SIZE * 0.6)
            tail.lineTo(r.right(), r.y() + self.TAIL_SIZE * 1.2)
        else:
            tail.moveTo(r.left(), r.y() + r.height() * 0.25)
            tail.lineTo(r.left() - self.TAIL_SIZE, r.y() + self.TAIL_SIZE * 0.6)
            tail.lineTo(r.left(), r.y() + self.TAIL_SIZE * 1.2)
        tail.closeSubpath()

        # Bubble rect
        path = QPainterPath()
        path.addRoundedRect(r, self._RADIUS, self._RADIUS)

        if is_user:
            grad = QLinearGradient(r.topLeft(), r.bottomRight())
            grad.setColorAt(0.0, self.USER_BG_A)
            grad.setColorAt(1.0, self.USER_BG_B)
            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(path)
            painter.setBrush(QBrush(grad))
            painter.drawPath(tail)
        else:
            grad = QLinearGradient(r.topLeft(), r.bottomLeft())
            grad.setColorAt(0.0, QColor(20, 32, 50, 242))
            grad.setColorAt(1.0, self.ASSISTANT_BG)
            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(path)
            painter.setBrush(QBrush(grad))
            painter.setPen(QPen(self.ASSISTANT_BORDER, 1))
            painter.drawPath(tail)

        # Text
        painter.setPen(self.TEXT_COLOR)
        text_rect = QRectF(r.x() + self._PAD, r.y() + self._PAD,
                           r.width() - self._PAD * 2, r.height() - self._PAD * 2 - th)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, text)

        # Timestamp (hover only)
        if ts and hovered:
            painter.setFont(self._FONT_TIME)
            painter.setPen(self.TIME_COLOR)
            align = Qt.AlignmentFlag.AlignRight if is_user else Qt.AlignmentFlag.AlignLeft
            painter.drawText(QRectF(r.x() + self._PAD, r.bottom() - self._PAD - 12,
                                     r.width() - self._PAD * 2, 12), align, ts)

    def sizeHint(self, option: QStyleOptionViewItem, index: QAbstractItemView.ModelIndex) -> QSize:  # type: ignore[override]
        text = self._role(index, 0) or ""
        ts = self._role(index, 2) or ""
        th = 14 if ts else 0
        max_w = int(360 * self._WIDTH_RATIO)
        fm = QFontMetrics(QFont("Segoe UI", 11))
        tr = fm.boundingRect(QRect(0, 0, max_w - self._PAD * 2, 0),
                              Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, text)
        bh = max(36, int(tr.height()) + self._PAD * 2 + th + 8)
        return QSize(360, bh)


# ═══════════════════════════════════════════════════════════════════════
#  HudRenderer — static HUD frame painting utilities
# ═══════════════════════════════════════════════════════════════════════

_HUD_CYAN = QColor(0, 212, 255)
_HUD_CYAN_DIM = QColor(0, 212, 255, 60)


class HudRenderer:
    """Helper for drawing Iron Man-style HUD frame elements.

    Stateless — call paint_* methods from your widget's paintEvent.
    """

    @staticmethod
    def paint_frame(
        painter: QPainter,
        width: int, height: int,
        bracket_len: int = 30,
        gap: int = 5,
        color: QColor = _HUD_CYAN,
        dim_color: QColor = _HUD_CYAN_DIM,
    ) -> None:
        """Draw Iron Man-style corner brackets and outer frame."""
        painter.setPen(QPen(dim_color, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(gap + 2, gap + 2, width - 2 * (gap + 2), height - 2 * (gap + 2))

        painter.setPen(QPen(color, 2))
        corners = [
            (gap, gap, gap + bracket_len, gap, gap, gap + bracket_len),
            (width - gap - bracket_len, gap, width - gap, gap, width - gap, gap + bracket_len),
            (gap, height - gap, gap + bracket_len, height - gap, gap, height - gap - bracket_len),
            (width - gap - bracket_len, height - gap, width - gap, height - gap, width - gap, height - gap - bracket_len),
        ]
        for x1, y1, x2, y2, x3, y3 in corners:
            painter.drawLine(x1, y1, x2, y2)
            painter.drawLine(x1, y1, x3, y3)

        # Accent dots
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        inner_x = gap + bracket_len
        inner_y = gap + bracket_len
        for ax, ay in [
            (inner_x, gap), (width - inner_x, gap),
            (inner_x, height - gap), (width - inner_x, height - gap),
            (gap, inner_y), (gap, height - inner_y),
            (width - gap, inner_y), (width - gap, height - inner_y),
        ]:
            painter.drawEllipse(QPointF(ax, ay), 2, 2)

    @staticmethod
    def paint_scan_line(
        painter: QPainter,
        width: int, height: int,
        time_ms: float,
        color: QColor = _HUD_CYAN,
    ) -> None:
        """Draw an animated scan line across the screen."""
        scan_y = int(time_ms / 1000 * 30) % height
        scan_alpha = int(18 + 12 * math.sin(time_ms / 1000 * 4))
        painter.fillRect(0, scan_y, width, 2, QColor(color.red(), color.green(), color.blue(), scan_alpha))

    @staticmethod
    def paint_corner_text(
        painter: QPainter,
        width: int, height: int,
        left_label: str = "ATOM.SYS v70",
        right_label: str | None = None,
    ) -> None:
        """Draw HUD corner text labels."""
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        painter.setFont(QFont("Courier New", 9))
        painter.setPen(QColor(0, 212, 255, 120))
        painter.drawText(45, 16, left_label)
        painter.drawText(width - 160, height - 20, now_str)

    @staticmethod
    def paint_status_bar(
        painter: QPainter,
        x: int, y: int,
        label: str,
        value: float,
        bar_width: int = 80,
        bar_height: int = 6,
        color: QColor = _HUD_CYAN,
    ) -> None:
        """Draw a HUD-style status bar with label."""
        painter.setFont(QFont("Courier New", 8))
        painter.setPen(color)
        painter.drawText(x, y - 4, label)
        painter.fillRect(x, y, bar_width, bar_height, QColor(26, 26, 46))
        fill_w = int(bar_width * min(1.0, value))
        if fill_w > 0:
            painter.fillRect(x, y, fill_w, bar_height, color)
        painter.setPen(QColor(138, 173, 204))
        painter.drawText(x + bar_width + 6, y + bar_height - 2, f"{int(value * 100)}%")

    @staticmethod
    def make_hud_pixmap(
        width: int, height: int,
        frame: int = 0,
        label: str = "ATOM.SYS v70",
    ) -> QPixmap:
        """Render a cached HUD background pixmap with frame, brackets, and labels."""
        pix = QPixmap(width, height)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        glow = QColor(0, 212, 255)
        dim = QColor(0, 212, 255, 60)
        bl = 30
        gap = 4
        painter.setPen(QPen(glow, 2))
        lines = [
            (gap, gap, gap + bl, gap), (gap, gap, gap, gap + bl),
            (width - gap - bl, gap, width - gap, gap), (width - gap, gap, width - gap, gap + bl),
            (gap, height - gap, gap + bl, height - gap), (gap, height - gap, gap, height - gap - bl),
            (width - gap - bl, height - gap, width - gap, height - gap), (width - gap, height - gap, width - gap, height - gap - bl),
        ]
        for x1, y1, x2, y2 in lines:
            painter.drawLine(x1, y1, x2, y2)

        painter.setPen(QPen(dim, 1))
        painter.drawRect(gap + 2, gap + 2, width - 2 * (gap + 2), height - 2 * (gap + 2))
        painter.setFont(QFont("Courier New", 9))
        painter.setPen(QColor(0, 212, 255, 120))
        painter.drawText(gap + 35, gap + 14, label)
        painter.drawText(width - 160, height - gap - 8, datetime.datetime.now().strftime("%H:%M:%S"))
        painter.end()
        return pix
