"""HUD overlay — frameless transparent window with Iron Man HUD, chat panel,
speech bubbles, and settings dialog.

Uses core/config_manager.py for config and ui/sprite_2d.py for the sprite.
"""

from __future__ import annotations

import datetime
import math
import random
import time as _time
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import (
    Qt, QEvent, QTimer, pyqtSignal,
    QPoint, QPointF, QRect, QRectF, QSize,
    QPropertyAnimation, QEasingCurve,
    QObject,
)
from PyQt6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QImage, QLinearGradient, QRadialGradient,
    QPainter, QPainterPath, QPen, QBrush, QRegion, QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QLineEdit, QPushButton, QLabel,
    QFrame, QHBoxLayout, QVBoxLayout, QDialog,
    QSlider, QCheckBox, QComboBox, QPlainTextEdit,
    QFormLayout, QGroupBox, QListWidget, QListWidgetItem,
    QAbstractItemView, QStyledItemDelegate, QStyleOptionViewItem,
    QMenu, QTabWidget, QGraphicsOpacityEffect,
)

from core.chat_history import (
    HISTORY_PATH,
    append_chat_message,
    load_chat_history,
    parse_timestamp,
    save_chat_history,
)
from core.config_manager import AppConfig
from core.logger import get_logger
from ui.sprite_2d import Sprite2D

log = get_logger("overlay")
# Backward compat: MinecraftSprite3D was renamed to Sprite2D in Step 5
MinecraftSprite3D = Sprite2D

ROOT = Path(__file__).resolve().parent.parent

# ── 3D renderer watchdog tuning ──
# The renderer deliberately throttles to ~4 FPS when idle/neutral, so the
# "too slow" threshold has to sit below that. Genuine trouble is caught by the
# stall watchdog (no frames at all for _3D_STALL_SECONDS) rather than by FPS.
_3D_MIN_FPS = 1.5
_3D_STALL_SECONDS = 5.0
_3D_STALL_MAX_RETRIES = 2
# How long a renderer may go without painting while its animation timer is still
# ticking before that is treated as a real failure. A live timer means the GL
# context is alive — the compositor just is not delivering paints (the character
# is behind another window, the display went to sleep, the machine is busy).
_3D_COMPOSITOR_GRACE_SECONDS = 20.0
# How long the 3D window may stay hidden before the overlay draws the 2D sprite
# as a stand-in, so the character is never off-screen.
_3D_HIDDEN_PLACEHOLDER_SECONDS = 0.6


# ════════════════════════════════════════════════════════════════════════════════
# StatusHudWidget — persistent mode/status indicator
# ════════════════════════════════════════════════════════════════════════════════

_MODE_COLORS = {
    "TAP": QColor(0, 212, 255),
    "STALK": QColor(255, 170, 0),
    "WHISPER": QColor(0, 255, 136),
    "LULLABY": QColor(170, 102, 255),
    "FORMAL": QColor(255, 255, 255),
}


class StatusHudWidget(QWidget):
    """Persistent HUD widget showing current mode, provider status, and model.

    Positioned at bottom-left corner of the overlay. Click opens Settings.
    """

    WIDTH = 175
    HEIGHT = 28
    MARGIN = 8

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._mode = parent._config.mode.upper() if hasattr(parent, '_config') else "TAP"
        self._provider = "?"
        self._model = "?"
        self._note = ""  # e.g. the Gemini key slot that answered ("#3/5")
        self._warning = ""  # e.g. an invalid-key message
        self._connectivity = "unknown"  # "online", "offline", "unknown"
        self._mode_color = _MODE_COLORS.get(self._mode, _MODE_COLORS["TAP"])
        self.setFixedSize(self.WIDTH, self.HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_mode(self, mode: str) -> None:
        mode = mode.upper()
        self._mode = mode
        self._mode_color = _MODE_COLORS.get(mode, _MODE_COLORS["TAP"])
        self.update()

    def set_provider(self, provider: str, model: str = "", connectivity: str = "unknown",
                     note: str = "") -> None:
        """Show the active provider, its model, and an optional short note.

        ``note`` carries the Gemini key slot that answered (``"#3/5"``). It sits
        directly after the provider name, because the model half of the label is
        elided on this HUD — that keeps "which key is serving me" readable.
        """
        self._provider = provider
        self._model = model
        self._note = note
        self._connectivity = connectivity
        self._refresh_tooltip()
        self.update()

    def set_key_warning(self, warning: str) -> None:
        """Set a warning message (e.g. invalid API key).
        When set, the connectivity dot turns amber and a tooltip shows the warning.
        """
        self._warning = warning or ""
        if warning:
            self._connectivity = "warning"
        self._refresh_tooltip()
        self.update()

    def _refresh_tooltip(self) -> None:
        """Compose one tooltip from whatever is known — neither part may clobber
        the other (a key warning used to erase the answering-key note)."""
        parts = []
        if self._provider and self._provider != "?":
            text = self._provider
            if self._model and self._model != "?":
                text += f" \u2022 {self._model}"
            if self._note and self._note != "?":
                text += f" \u00b7 key {self._note.lstrip('#')}"
            parts.append(text)
        if self._warning:
            parts.append(self._warning)
        self.setToolTip("\n".join(parts))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()

        # Background panel
        bg = QColor(10, 15, 26, 220)
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(QColor(0, 212, 255, 50), 1))
        painter.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

        # Connectivity dot (left side)
        dot_color = {
            "online": QColor(0, 255, 136),
            "offline": QColor(255, 68, 68),
            "unknown": QColor(255, 184, 0),
            "warning": QColor(255, 140, 0),
        }.get(self._connectivity, QColor(255, 184, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(dot_color))
        painter.drawEllipse(QPointF(10, h // 2), 4, 4)

        # Mode badge (colored text)
        painter.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        painter.setPen(self._mode_color)
        painter.drawText(22, 2, w - 24, h, Qt.AlignmentFlag.AlignVCenter, self._mode)

        # Provider/model on right side with elision for overflow
        painter.setFont(QFont("Courier New", 7))
        label = self._provider
        if self._note:
            label += self._note
        if self._model:
            label += f" \u2022 {self._model}"
        painter.setPen(QColor(138, 173, 204, 150))
        fm = painter.fontMetrics()
        max_label_w = w - 80  # Leave room for mode text + dot
        elided = fm.elidedText(label, Qt.TextElideMode.ElideRight, max_label_w)
        label_w = fm.horizontalAdvance(elided)
        label_x = w - label_w - 6
        painter.drawText(label_x, 2, label_w, h, Qt.AlignmentFlag.AlignVCenter, elided)

        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            parent = self.parent()
            while parent and not hasattr(parent, '_open_settings'):
                parent = parent.parent()
            if parent and hasattr(parent, '_open_settings'):
                parent._open_settings()
        super().mousePressEvent(event)


def _qcolor_from_hex(hex_str: str, alpha: int = 255) -> QColor:
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return QColor(int(h[:2], 16), int(h[2:4], 16), int(h[4:6], 16), alpha)


# ════════════════════════════════════════════════════════════════════════════════
# ChatBubbleDelegate
# ════════════════════════════════════════════════════════════════════════════════

class ChatBubbleDelegate(QStyledItemDelegate):
    """Styled chat bubbles with tails, hover timestamps."""

    _FONT_BUBBLE = QFont("Segoe UI", 12)
    _FONT_TIME = QFont("Segoe UI", 8)
    _FONT_NAME = QFont("Segoe UI", 9, QFont.Weight.DemiBold)

    USER_BG = QColor(0, 80, 220, 220)
    ASSISTANT_BG = QColor(20, 28, 46, 245)
    ASSISTANT_BORDER = QColor(0, 180, 255, 50)
    TEXT_COLOR = QColor(240, 243, 250, 250)
    TIME_COLOR = QColor(150, 160, 180, 160)
    NAME_COLOR = QColor(0, 212, 255, 200)

    _MARGIN = 6
    _PAD = 10
    _RADIUS = 8
    _WIDTH_RATIO = 0.78

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

    @staticmethod
    def _role(index, role_offset: int = 1) -> Any:
        return index.data(Qt.ItemDataRole.UserRole + role_offset)

    @staticmethod
    def _text(index) -> str:
        """The message body: ``setText`` (DisplayRole), then UserRole+0.

        Reading UserRole+0 alone returned None for every item created with
        ``setText()``, so paint() bailed out on the empty string and the chat
        box rendered as a blank panel while the messages sat in the model.
        """
        return index.data(Qt.ItemDataRole.DisplayRole) or index.data(Qt.ItemDataRole.UserRole) or ""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QAbstractItemView.ModelIndex) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        role = self._role(index, 1) or "assistant"
        text = self._text(index)
        ts = self._role(index, 2) or ""
        is_grouped = self._role(index, 4) or False  # 4 = grouped flag

        # ── Date separator: render as centered text with horizontal divider lines ──
        if self._role(index, 5):  # date_separator flag
            w = option.rect.width()
            y_mid = option.rect.y() + option.rect.height() // 2
            painter.setFont(QFont("Segoe UI", 9))
            painter.setPen(QColor(110, 130, 160, 140))
            fm = painter.fontMetrics()
            text_w = fm.horizontalAdvance(text) + 24
            line_x1 = (w - text_w) // 2
            line_x2 = line_x1 + text_w
            # Left line
            painter.setPen(QPen(QColor(110, 130, 160, 60), 1))
            painter.drawLine(20, y_mid, line_x1, y_mid)
            # Right line
            painter.drawLine(line_x2, y_mid, w - 20, y_mid)
            # Date text
            painter.setPen(QColor(130, 150, 180, 180))
            painter.drawText(QRect(line_x1, option.rect.y(), text_w, option.rect.height()),
                             Qt.AlignmentFlag.AlignCenter, text)
            return

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

        margin = self._MARGIN
        bx = w - bw - margin if is_user else margin
        by = option.rect.y() + (1 if is_grouped else 2)
        r = QRectF(bx, by, bw, bh)

        # -- Bubble body (simplified, no tail, no glow) --
        if is_user:
            painter.setBrush(QBrush(self.USER_BG))
            painter.setPen(Qt.PenStyle.NoPen)
        else:
            if is_grouped:
                painter.setBrush(QBrush(QColor(16, 22, 40, 245)))
            else:
                painter.setBrush(QBrush(self.ASSISTANT_BG))
            painter.setPen(QPen(self.ASSISTANT_BORDER, 1))

        path = QPainterPath()
        path.addRoundedRect(r, self._RADIUS, self._RADIUS)
        painter.drawPath(path)

        # -- Body text --
        painter.setFont(self._FONT_BUBBLE)
        painter.setPen(self.TEXT_COLOR)
        text_rect = QRectF(r.x() + self._PAD, r.y() + self._PAD,
                           r.width() - self._PAD * 2, r.height() - self._PAD * 2 - th)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, text)

        # -- Timestamp (always visible, bottom-right, like Echo) --
        if ts:
            painter.setFont(self._FONT_TIME)
            painter.setPen(self.TIME_COLOR)
            align = Qt.AlignmentFlag.AlignRight if is_user else Qt.AlignmentFlag.AlignLeft
            painter.drawText(QRectF(r.x() + self._PAD, r.bottom() - self._PAD - 12,
                                     r.width() - self._PAD * 2, 12), align, ts)

    #: Scratch painter used to measure wrapped text exactly as paint() will.
    _measure_img: Optional[QImage] = None
    _measure_painter: Optional[QPainter] = None

    def row_height(self, text: str, width: int, with_ts: bool = True) -> int:
        """Height of one bubble at *width* pixels of list space.

        ``with_ts`` matches paint(): a row without a timestamp (the transient
        thinking indicator) has no timestamp strip, and reserving one ate so
        much of the bubble that the text was clipped in half.
        """
        if not text:
            return 32
        if ChatBubbleDelegate._measure_painter is None:
            ChatBubbleDelegate._measure_img = QImage(8, 8, QImage.Format.Format_ARGB32)
            ChatBubbleDelegate._measure_painter = QPainter(ChatBubbleDelegate._measure_img)
        painter = ChatBubbleDelegate._measure_painter
        painter.setFont(self._FONT_BUBBLE)
        wrap_w = max(40, int(int(width * self._WIDTH_RATIO) - self._PAD * 2))
        rect = painter.boundingRect(
            QRectF(0, 0, wrap_w, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap,
            text,
        )
        return max(36, int(rect.height()) + self._PAD * 2 + (14 if with_ts else 0) + 4)

    def sizeHint(self, option: QStyleOptionViewItem, index: QAbstractItemView.ModelIndex) -> QSize:
        """Row size for the *current* list width.

        Qt calls this during layout, so the width is correct even for messages
        added before the panel was shown — unlike a stored hint, which is frozen
        at whatever width happened to exist when the message arrived.
        """
        width = 360
        view = self.parent()
        try:
            width = view.viewport().width() or 360
        except Exception:
            pass
        if self._role(index, 5):  # date separator
            return QSize(max(300, width - 4), 28)
        has_ts = bool(self._role(index, 2))
        return QSize(max(300, width - 4),
                     self.row_height(self._text(index), width, has_ts) + 3)


# ════════════════════════════════════════════════════════════════════════════════
# AtomOverlay
# ════════════════════════════════════════════════════════════════════════════════

class AtomOverlay(QWidget):
    """Frameless transparent HUD overlay — sprite, speech bubbles, chat, settings."""

    user_input = pyqtSignal(str)
    action_requested = pyqtSignal(dict)
    confirm_action = pyqtSignal()
    cancel_action = pyqtSignal()
    stop_requested = pyqtSignal()
    chat_toggled = pyqtSignal(bool)
    settings_changed = pyqtSignal(dict)
    voice_toggle_requested = pyqtSignal(bool)
    mode_change_requested = pyqtSignal(str)
    tts_speed_requested = pyqtSignal(int)
    tts_volume_requested = pyqtSignal(float)
    character_changed = pyqtSignal(str)
    browser_login_requested = pyqtSignal()
    browser_logout_requested = pyqtSignal()
    sprite_drag_finished = pyqtSignal(float, float)

    _EMOTION_SPEED_MAP = {
        "neutral": 1.0, "happy": 1.15, "excited": 1.3,
        "sad": 0.7, "sleepy": 0.5, "annoyed": 1.1,
        "flirty": 1.1, "surprised": 1.2, "confused": 0.9,
    }

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Window flags + geometry set by _init_window_flags() — called after intro to avoid Qt crash
        self.setWindowTitle("Atom")

        # ── Sprite ──
        scale = max(1, config.sprite_scale)
        self._sprite = MinecraftSprite3D(
            scale=scale, character=config.character,
            use_skin_png=bool(getattr(config.skins, "enabled", False)),
        )

        self._sprite_x = float(config.start_x)
        self._sprite_y = float(config.start_y)
        self._dragging = False
        self._drag_offset: Optional[QPoint] = None

        # ── State ──
        self.speech_text = ""
        self.status_text = ""
        self._screen_context = ""
        self._preview_rect: Optional[QRect] = None
        self.current_pose = "idle"
        self.current_emotion = "neutral"
        self.current_micro_interaction = ""
        self.current_boredom_tier = 0
        self._animation_tick = 0
        self._thinking_dots = 0
        self._thinking_timer: Optional[QTimer] = None
        self._last_chat_date: Optional[str] = None

        # Typing animation
        self._typing_timer: Optional[QTimer] = None
        self._typing_target = ""
        self._typing_pos = 0
        self._typing_speed = 15
        self._typing_item: Optional[QListWidgetItem] = None
        self._typing_skip_recalc = 0

        # Speech bubble timer
        self._speech_timer = QTimer(self)
        self._speech_timer.setSingleShot(True)
        self._speech_timer.timeout.connect(lambda: self.set_speech(""))

        # ── Click-through mode (idle → mouse passthrough) ──
        self._click_through_active = False
        self._idle_click_timer = QTimer(self)
        self._idle_click_timer.setSingleShot(True)
        self._idle_click_timer.timeout.connect(self._enable_click_through)
        self._idle_click_timer.start(8000)  # 8s idle → click-through

        # Idle click-through makes the window ignore mouse events, so watch the
        # cursor to restore interaction when the user reaches for the character.
        self._cursor_watch_timer = QTimer(self)
        self._cursor_watch_timer.setInterval(150)
        self._cursor_watch_timer.timeout.connect(self._watch_cursor)

        # ── Animation helpers ──
        self._chat_anim: Optional[QPropertyAnimation] = None
        # Safety net for the chat-slide animation: some platforms (or a stalled
        # event loop) never deliver `finished`, which used to leave a
        # "minimized" panel on screen.
        self._chat_out_timer: Optional[QTimer] = None
        self._speech_fade_anim: Optional[QPropertyAnimation] = None
        self._float_fade_anim: Optional[QPropertyAnimation] = None

        # Panels
        self._chat_minimized = False
        self._chat_panel = self._build_chat_panel()
        self._float_button = self._build_float_button()
        self._layout_chat_panel()

        # ── Transcript ──
        # The conversation on disk is replayed into the panel so a restart
        # continues the chat instead of opening an empty one.
        self._chat_log: list[dict] = []
        self._restore_chat_history()

        # Caches
        self._hud_cache: Optional[QPixmap] = None
        self._hud_cache_size = (0, 0)
        self._hud_cache_frame = 0
        self._last_mask_key: tuple = ()
        self._last_tick_pose: Optional[str] = None
        self._last_tick_pos = (0.0, 0.0)
        self._idle_tick_count = 0

        # Step 3: Gemini key warning state
        self._gemini_key_warning: Optional[str] = None

        # Step 4: FPS monitoring for 3D→2D auto-fallback
        # NOTE: this measures the *renderer's* real frame_rendered rate. It used
        # to count overlay update-timer ticks (fixed 10 Hz), which is always
        # below the 15 FPS threshold — so 3D was force-downgraded to 2D after
        # ~6s on every run, which is why the 3D model "disappeared".
        self._fps_frame_count = 0                      # last seen renderer frame count
        self._fps_time_start: float = _time.time()  # CRITICAL: must be current time, not 0
        self._fps_current: float = 60.0
        self._fps_low_start: Optional[float] = None
        self._fps_low_threshold = _3D_MIN_FPS
        self._fps_low_duration = 8.0
        self._fps_fallback_done = False

        # 3D renderer
        self._render_win = None
        self.renderer_3d = None
        self._3d_fallback_timer: Optional[QTimer] = None
        self._render_win_raise_counter = 0
        self._3d_init_time: Optional[float] = None     # when 3D was last initialized
        self._3d_frames_rendered = 0                    # frame_rendered signal count
        self._3d_broken_detected = False                # true if 3D is alive but broken
        self._3d_raise_timer: Optional[QTimer] = None   # periodic raise to prevent burying
        self._3d_retry_count = 0                         # number of 3D re-init attempts
        self._3d_reshow_count = 0                        # number of 3D window re-show attempts
        # Render-stall watchdog (model vanishing mid-session)
        self._3d_last_frame_count = 0                    # frame count at last health check
        self._3d_last_frame_time: float = _time.time()   # when frames last advanced
        self._3d_stall_count = 0                         # consecutive stall recoveries
        # Compositor stalls (no frames, but the renderer is still animating) are
        # tolerated for a grace period instead of tearing the renderer down.
        self._3d_compositor_stall_since: Optional[float] = None
        # When the 3D window was first seen hidden (drives the 2D placeholder).
        self._render_win_hidden_since: Optional[float] = None
        # Session-wide stall-recovery budget. This one is deliberately NOT reset
        # by a re-init: a renderer that never paints (no GPU, remote desktop,
        # a lost GL context) used to be rebuilt every few seconds forever,
        # burning CPU/GPU instead of falling back to the 2D sprite.
        self._3d_stall_recoveries = 0

        # Status HUD widget (persistent mode indicator, bottom-left)
        self._status_hud = StatusHudWidget(self)
        # Don't position yet — geometry is 0x0 until _init_window_flags is called
        self._status_hud.hide()

        # Timers
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick_animation)
        self._anim_timer.start(80)

        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(self._tick_update)
        self._update_timer.start(100)  # 10 FPS — smooth enough, saves ~33% CPU vs 66ms

        # update_input_mask not called yet — geometry is 0x0 until _init_window_flags

    def _init_window_flags(self) -> None:
        """Set window flags and geometry for overlay visibility.

        Called after the intro sequence to avoid a Qt/Windows DWM crash
        (STATUS_STACK_BUFFER_OVERRUN 0xC0000409) that occurs when creating
        a hidden FramelessWindowHint + WindowStaysOnTopHint + WA_TranslucentBackground
        window BEFORE showing a fullscreen dialog.
        """
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())
        # Position status HUD now that geometry is known
        self._reposition_status_hud()
        self._layout_chat_panel()
        # Ensure chat panel is visible (Qt children auto-show, but explicit call prevents edge cases)
        self._chat_panel.show()
        self.update_input_mask()

        # ── Initialize 3D renderer based on config ──
        self._try_init_3d()

    def _reposition_status_hud(self) -> None:
        """Position the status HUD widget at bottom-left corner.
        Called after geometry is known (from _init_window_flags or resizeEvent).
        """
        if hasattr(self, '_status_hud') and self._status_hud:
            self._status_hud.move(
                StatusHudWidget.MARGIN,
                max(0, self.height() - StatusHudWidget.HEIGHT - StatusHudWidget.MARGIN),
            )
            self._status_hud.show()

    # ══════════════════════════════════════════════════════════════════
    # 3D renderer
    # ══════════════════════════════════════════════════════════════════

    def _try_init_3d(self) -> None:
        """Check config and init 3D if render_mode allows.
        Safe to call multiple times — guards against double-init.
        """
        if self.renderer_3d is not None:
            return  # Already running
        try:
            from ui.renderer_3d import BlockRenderer3D
            mode = self._config.render_mode
            if mode == "3d" or (mode == "auto" and BlockRenderer3D.is_available()):
                self._init_3d()
        except Exception:
            pass

    def _init_3d(self) -> None:
        if self.renderer_3d is not None:
            return  # Already running — prevent duplicate windows
        try:
            from ui.renderer_3d import create_3d_widget, BlockRenderer3D
            if not BlockRenderer3D.is_available():
                return
            self._3d_retry_count = 0  # Reset retry counter on success
            self._create_3d_window()
        except Exception:
            pass

    def _create_3d_window(self) -> None:
        from PyQt6.QtWidgets import QMainWindow
        from ui.renderer_3d import create_3d_widget

        self._render_win = QMainWindow()
        self._render_win.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput
        )
        self._render_win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._render_win.setStyleSheet("background: transparent;")

        widget = create_3d_widget(
            self._render_win,
            use_skin_png=bool(getattr(self._config.skins, "enabled", False)),
        )
        if widget is None:
            self._render_win.deleteLater()
            self._render_win = None
            return

        self.renderer_3d = widget
        self._render_win.setCentralWidget(widget)
        self._sync_render_win_pos()
        self._render_win.show()
        self.renderer_3d.set_profile(self._config.character)

        # ── Reset re-show / watchdog state on fresh 3D init ──
        self._3d_reshow_count = 0
        self._3d_stall_count = 0
        self._3d_compositor_stall_since = None
        self._render_win_hidden_since = None
        # ── Track 3D health ──
        self._3d_init_time = _time.time()
        self._3d_frames_rendered = 0
        self._3d_broken_detected = False
        self._3d_last_frame_count = 0
        self._3d_last_frame_time = _time.time()
        # Give the new renderer a clean measurement window.
        self._fps_frame_count = 0
        self._fps_time_start = _time.time()
        self._fps_low_start = None
        self._fps_fallback_done = False
        try:
            self.renderer_3d.frame_rendered.connect(self._on_3d_frame_rendered)
        except Exception:
            pass

        # ── Periodic raise timer (every 2s) to prevent window from getting buried ──
        self._3d_raise_timer = QTimer(self)
        self._3d_raise_timer.timeout.connect(self._on_3d_raise_tick)
        self._3d_raise_timer.start(2000)

    def _3d_window_size(self) -> tuple[int, int]:
        """Render-window size, scaled with the configured sprite scale.

        Previously hard-coded at 200x280 — a larger window gives the model more
        pixels, which is the cheapest 3D quality win available.
        """
        try:
            scale = int(getattr(self._config, "sprite_scale", 2) or 2)
        except (TypeError, ValueError):
            scale = 2
        scale = max(2, min(4, scale))
        return (100 * scale, 140 * scale)

    def _sync_render_win_pos(self) -> None:
        if self._render_win is None or self.renderer_3d is None:
            return
        w, h = self._3d_window_size()
        pos = self.mapToGlobal(QPoint(int(self._sprite_x), int(self._sprite_y)))
        if self._render_win.width() != w or self._render_win.height() != h:
            self._render_win.setGeometry(pos.x(), pos.y(), w, h)
        else:
            self._render_win.move(pos.x(), pos.y())
        self._render_win.raise_()

    def _stop_renderer_animation(self) -> None:
        """Stop the 3D widget's animation timer before dropping it.

        The timer used to rely on Qt destroying the widget, which is not
        guaranteed: ``deleteLater()`` leaves it running while the Python wrapper
        still holds a reference, so it kept repainting a discarded widget.
        """
        renderer = self.renderer_3d
        if renderer is None:
            return
        try:
            renderer.stop_animation()
        except Exception:
            pass

    def _fallback_to_2d(self) -> None:
        if self._3d_fallback_timer:
            try:
                self._3d_fallback_timer.stop()
            except Exception:
                pass
            self._3d_fallback_timer = None
        if self._3d_raise_timer:
            try:
                self._3d_raise_timer.stop()
            except Exception:
                pass
            self._3d_raise_timer = None
        self._3d_init_time = None
        self._3d_frames_rendered = 0
        self._stop_renderer_animation()
        self.renderer_3d = None
        if self._render_win is not None:
            try:
                self._render_win.hide()
                self._render_win.deleteLater()
            except Exception:
                pass
            self._render_win = None
        self.update_input_mask()
        self.update()

    def use_2d(self) -> None:
        """Switch to the 2D sprite (context-menu action)."""
        if self.renderer_3d is None:
            return
        self._fallback_to_2d()
        self.show_message("*tap* Switched to the 2D sprite.", duration=3000)

    def restore_3d(self) -> None:
        """Bring the 3D renderer back after a fallback (context-menu action).

        The automatic fallback used to be permanent for the session, so one
        transient stall — or a display that was asleep — left the character in
        2D until Atom was restarted. This clears every latch that keeps 3D off
        and tries again.
        """
        if self.renderer_3d is not None:
            return
        self._fps_fallback_done = False
        self._fps_low_start = None
        self._fps_time_start = _time.time()
        self._fps_frame_count = self._3d_frames_rendered
        self._3d_retry_count = 0
        self._3d_stall_count = 0
        self._3d_stall_recoveries = 0
        self._3d_compositor_stall_since = None
        self._render_win_hidden_since = None
        self._init_3d()
        if self.renderer_3d is None:
            self.show_message("*tap* 3D isn't available on this display.", duration=5000)
        else:
            self.show_message("*tap* Back in 3D.", duration=3000)
        self.update_input_mask()
        self.update()

    def _check_3d_valid(self) -> None:
        """Check if the 3D window is still visible.

        If hidden (Windows can drop or bury a top-most frameless window), re-show
        it instead of falling back to 2D. Only ``_3D_STALL_MAX_RETRIES + 1``
        consecutive failures trigger the 2D fallback.
        """
        if self.renderer_3d is None:
            return
        win = self._render_win
        if win is None:
            return
        if win.isVisible():
            self._3d_reshow_count = 0  # healthy — reset the consecutive counter
            return
        self._3d_reshow_count += 1
        if self._3d_reshow_count > 3:
            log.warning("3D window hidden after 3 re-show attempts — falling back to 2D")
            self._fallback_to_2d()
            return
        try:
            self._sync_render_win_pos()
            win.show()
            win.raise_()
            log.info("3D window was hidden — re-showing (attempt %d/3)", self._3d_reshow_count)
        except Exception as exc:
            log.warning("3D re-show failed: %s", exc)
            self._fallback_to_2d()

    def _renderer_animation_running(self) -> bool:
        """True if the 3D renderer's animation timer is still ticking.

        Distinguishes a broken renderer (dead timer) from one that is simply not
        being painted right now. Absent/unknown implementations report False so
        the watchdog keeps its old, conservative behaviour.
        """
        renderer = self.renderer_3d
        if renderer is None:
            return False
        check = getattr(renderer, "is_animation_running", None)
        if not callable(check):
            return False
        try:
            result = check()
        except Exception:
            return False
        # Require a real bool. An object that merely happens to answer the call
        # (a mock, a wrapper) is unknown, and treating it as "still animating"
        # would silently disable the watchdog for the whole session.
        return result is True

    def _on_3d_frame_rendered(self) -> None:
        """Increment frame counter for 3D health tracking."""
        self._3d_frames_rendered += 1

    def _on_3d_raise_tick(self) -> None:
        """Keep the 3D window on top and babysit its health every 2s.

        Re-raises the window (without stealing focus) and delegates to the stall
        watchdog. Without this the model could silently stop rendering for the
        rest of the session.
        """
        if self.renderer_3d is None or self._render_win is None:
            return
        self._sync_render_win_pos()
        self._check_3d_health()

    def _check_3d_health(self) -> None:
        """Detect a 3D renderer that stopped producing frames and recover."""
        if self.renderer_3d is None:
            return

        now = _time.time()
        if self._3d_frames_rendered != self._3d_last_frame_count:
            # Frames are advancing — renderer is alive, so the recovery budget is
            # spent on this run only if it ever actually breaks again.
            self._3d_last_frame_count = self._3d_frames_rendered
            self._3d_last_frame_time = now
            self._3d_stall_count = 0
            self._3d_stall_recoveries = 0
            self._3d_compositor_stall_since = None
            return

        stalled = now - self._3d_last_frame_time
        if stalled < _3D_STALL_SECONDS:
            return

        # Cheapest recovery first: the window may just be hidden/buried. If the
        # re-show path ran, give it a full interval to start painting again.
        was_hidden = self._render_win is not None and not self._render_win.isVisible()
        self._check_3d_valid()
        if self.renderer_3d is None:
            return
        if was_hidden:
            self._3d_last_frame_time = now
            return

        # A renderer whose animation timer is still ticking has not crashed: the
        # window is visible but the platform stopped delivering paints. Re-init
        # used to run here, which discarded a perfectly good GL context and —
        # after a couple of occurrences — downgraded the whole session to 2D.
        # That is what made the 3D character vanish on its own after a while.
        # Only a dead timer means a broken renderer; a live one gets a grace
        # period (and the 2D placeholder keeps the character on screen meanwhile).
        if self._renderer_animation_running():
            # __dict__ (not attribute access) — QObject subclasses raise on missing
            # attributes, and callers may build an overlay without __init__().
            since = self.__dict__.get("_3d_compositor_stall_since")
            if since is None:
                since = now
                self._3d_compositor_stall_since = since
                log.debug(
                    "3D produced no frames for %.1fs but is still animating — "
                    "treating as a compositor stall", stalled,
                )
            if now - since < _3D_COMPOSITOR_GRACE_SECONDS:
                self._3d_last_frame_time = now
                return
            log.warning(
                "3D has not painted for %.0fs despite an active timer — re-initialising",
                now - since,
            )
        self._3d_compositor_stall_since = None

        self._3d_stall_count += 1
        # __dict__ (not getattr) — QObject subclasses raise on missing attributes.
        self._3d_stall_recoveries = self.__dict__.get("_3d_stall_recoveries", 0) + 1
        if self._3d_stall_recoveries <= _3D_STALL_MAX_RETRIES:
            log.warning("3D renderer stalled for %.1fs — re-initialising (attempt %d/%d)",
                        stalled, self._3d_stall_recoveries, _3D_STALL_MAX_RETRIES)
            self._reinit_3d()
        else:
            log.warning(
                "3D renderer stalled repeatedly (%d recoveries failed to paint) — "
                "staying on the 2D sprite", self._3d_stall_recoveries,
            )
            self._fallback_to_2d()

    def _reinit_3d(self) -> None:
        """Rebuild the 3D window from scratch, keeping 2D as the fallback."""
        retries = self._3d_retry_count + 1
        if self._3d_raise_timer:
            try:
                self._3d_raise_timer.stop()
            except Exception:
                pass
            self._3d_raise_timer = None
        self.renderer_3d = None
        if self._render_win is not None:
            try:
                self._render_win.hide()
                self._render_win.deleteLater()
            except Exception:
                pass
            self._render_win = None
        self._3d_frames_rendered = 0
        self._3d_last_frame_count = 0
        self._3d_last_frame_time = _time.time()
        self._init_3d()
        # _init_3d() resets the counter on a fresh widget — restore our budget.
        self._3d_retry_count = retries

    # ══════════════════════════════════════════════════════════════════
    # Input mask
    # ══════════════════════════════════════════════════════════════════

    def update_input_mask(self) -> None:
        """Fit the window mask around the interactive widgets.

        Two bugs used to live here:

        * The sprite was dropped from the region whenever click-through was
          active. ``QWidget.setMask`` clips **painting** as well as input, so the
          2D character was not merely click-through in that state — it vanished
          completely, and could not be woken by hovering it either, because mouse
          events no longer reached the window there.
        * The change-detection key was sprite position/size plus a few booleans,
          so it did not notice a resize or a chat-panel re-layout. The panel is
          anchored bottom-right, so after any window size change its new rect fell
          outside the stale mask and the chat panel silently disappeared (and
          stopped accepting clicks).

        The key is now derived from the rects themselves, the sprite is always
        included, and click-through input is handled by the window attribute in
        :meth:`_enable_click_through` instead of by cutting a hole in the mask.
        """
        rects = [self._sprite_rect().adjusted(-8, -8, 8, 8)]
        if self._chat_minimized:
            rects.append(self._float_button.geometry().adjusted(-4, -4, 4, 4))
        else:
            rects.append(self._chat_panel.geometry().adjusted(-2, -2, 2, 2))
        # Status HUD is always clickable
        rects.append(self._status_hud.geometry().adjusted(-2, -2, 2, 2))
        if self.speech_text:
            bx, by, bw, bh = self._speech_bubble_rect()
            rects.append(QRect(bx, by, bw, bh).adjusted(-2, -2, 2, 2))

        key = tuple((r.x(), r.y(), r.width(), r.height()) for r in rects)
        if key == self._last_mask_key:
            return
        self._last_mask_key = key

        region = QRegion()
        for r in rects:
            region = region.united(QRegion(r))
        self.setMask(region)

    # ══════════════════════════════════════════════════════════════════
    # Mouse events
    # ══════════════════════════════════════════════════════════════════

    def mousePressEvent(self, event) -> None:
        self._reset_idle_timer()
        if event.button() == Qt.MouseButton.LeftButton and self._sprite_rect().contains(event.pos()):
            self._dragging = True
            self._drag_offset = event.pos() - self._sprite_rect().topLeft()
            self.set_pose("listen")

    def mouseMoveEvent(self, event) -> None:
        self._reset_idle_timer()
        if self._dragging and self._drag_offset is not None:
            pos = event.pos() - self._drag_offset
            self._sprite_x = max(0.0, min(float(pos.x()), float(self.width() - self._sprite.width)))
            self._sprite_y = max(0.0, min(float(pos.y()), float(self.height() - self._sprite.height)))
            self.update_input_mask()
            self.update()  # Force repaint so sprite follows cursor immediately

    def mouseReleaseEvent(self, event) -> None:
        self._dragging = False
        self.set_pose("idle")
        self.sprite_drag_finished.emit(self._sprite_x, self._sprite_y)
        self._reset_idle_timer()

    # ══════════════════════════════════════════════════════════════════
    # Paint
    # ══════════════════════════════════════════════════════════════════

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # ── Cached HUD (static background layer) ──
        stale = (w, h) != self._hud_cache_size or self._hud_cache is None
        if stale or self._hud_cache_frame >= 60:
            self._hud_cache_frame = 0
            self._hud_cache = QPixmap(w, h)
            self._hud_cache.fill(Qt.GlobalColor.transparent)
            cp = QPainter(self._hud_cache)
            cp.setRenderHint(QPainter.RenderHint.Antialiasing)
            gap = 4
            bl = 30
            # Dim outer border (base layer)
            cp.setPen(QPen(QColor(0, 212, 255, 40), 1))
            cp.drawRect(gap + 2, gap + 2, w - 2 * (gap + 2), h - 2 * (gap + 2))
            # Dim bracket hints (animated bright overlay comes on top)
            dim = QColor(0, 212, 255, 25)
            cp.setPen(QPen(dim, 1))
            for x1, y1, x2, y2 in [
                (gap, gap, gap + bl, gap), (gap, gap, gap, gap + bl),
                (w - gap - bl, gap, w - gap, gap), (w - gap, gap, w - gap, gap + bl),
                (gap, h - gap, gap + bl, h - gap), (gap, h - gap, gap, h - gap - bl),
                (w - gap - bl, h - gap, w - gap, h - gap), (w - gap, h - gap, w - gap, h - gap - bl),
            ]:
                cp.drawLine(x1, y1, x2, y2)
            # Version + timestamp
            cp.setFont(QFont("Courier New", 9))
            cp.setPen(QColor(0, 212, 255, 120))
            cp.drawText(gap + 35, gap + 14, "ATOM.SYS v70")
            cp.drawText(w - 160, h - gap - 8, datetime.datetime.now().strftime("%H:%M:%S"))
            cp.end()
            self._hud_cache_size = (w, h)
        else:
            self._hud_cache_frame += 1

        if self._hud_cache and not self._hud_cache.isNull():
            painter.drawPixmap(0, 0, self._hud_cache)

        # ── Animated corner bracket glow (Iron Man breathing effect) ──
        t_now = _time.time()
        breath = 0.5 + 0.5 * math.sin(t_now * 1.8)  # 0-1 cycle, ~0.3 Hz
        breath_alpha = int(60 + 195 * breath)        # 60-255 range

        bl = 30
        gap = 4
        bracket_coords = [
            (gap, gap, gap + bl, gap), (gap, gap, gap, gap + bl),
            (w - gap - bl, gap, w - gap, gap), (w - gap, gap, w - gap, gap + bl),
            (gap, h - gap, gap + bl, h - gap), (gap, h - gap, gap, h - gap - bl),
            (w - gap - bl, h - gap, w - gap, h - gap), (w - gap, h - gap, w - gap, h - gap - bl),
        ]

        # Single-pass bright brackets (removed outer glow halo — saves 8 drawLine/frame)
        _BRACKET_PEN = QPen(QColor(0, 212, 255, breath_alpha), 2, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap)
        painter.setPen(_BRACKET_PEN)
        for x1, y1, x2, y2 in bracket_coords:
            painter.drawLine(x1, y1, x2, y2)

        # Corner accent dots (pulse with breathing)
        dot_radius = 2 + int(1 * breath)
        dot_alpha = int(120 + 135 * breath)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 212, 255, dot_alpha)))
        for cx, cy in [(gap, gap), (w - gap, gap), (gap, h - gap), (w - gap, h - gap)]:
            painter.drawEllipse(QPointF(cx, cy), dot_radius, dot_radius)

        # ── Radar arc sweep at top-left + bottom-right corners only ──
        # (was 4 corners — 50% cost reduction, same visual feel)
        radar_angle = (t_now * 0.6) % (2 * math.pi)
        arc_radius = 14
        _RADAR_PEN = QPen(QColor(0, 212, 255, 70), 1.5)
        _BLIP_BRUSH = QBrush(QColor(0, 255, 255, 200))
        for cx, cy, offset in [(gap, gap, 0), (w - gap, h - gap, math.pi)]:
            sweep = radar_angle + offset
            painter.save()
            painter.translate(cx, cy)
            painter.rotate(math.degrees(sweep))

            # Swept arc (45° → +50° sector)
            arc_path = QPainterPath()
            arc_path.arcMoveTo(-arc_radius, -arc_radius, arc_radius * 2, arc_radius * 2, -25)
            arc_path.arcTo(-arc_radius, -arc_radius, arc_radius * 2, arc_radius * 2, -25, 50)
            painter.strokePath(arc_path, _RADAR_PEN)

            # Blip dot at the leading edge of the arc
            blip_angle_deg = 25
            bx = arc_radius * math.cos(math.radians(blip_angle_deg))
            by = arc_radius * math.sin(math.radians(blip_angle_deg))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_BLIP_BRUSH)
            painter.drawEllipse(QPointF(bx, by), 2, 2)

            painter.restore()

        # ── Enhanced scan line with trailing glow ──
        scan_y = int(t_now * 30) % h
        # Trailing glow lines (3 trails instead of 8 — saves 5 fillRect/frame)
        for i in range(3):
            trail_y = scan_y - i * 2
            if 0 <= trail_y < h:
                trail_alpha = max(1, int(22 * (1 - i / 3) ** 2))
                painter.fillRect(0, trail_y, w, 1 if i > 0 else 2,
                                 QColor(0, 160, 255, trail_alpha))
        # Main bright scan line with pulsing alpha
        main_alpha = int(18 + 14 * math.sin(t_now * 4))
        painter.fillRect(0, scan_y, w, 2, QColor(0, 212, 255, main_alpha))

        # Status bar
        if self.status_text:
            painter.setFont(QFont("Courier New", 8))
            painter.setPen(QColor(0, 212, 255, 100))
            sw = painter.fontMetrics().horizontalAdvance(self.status_text) + 20
            sx = (w - sw) // 2
            painter.fillRect(sx, h - 22, sw, 16, QColor(0, 212, 255, 15))
            painter.drawText(sx + 10, h - 11, self.status_text)

        # Sprite
        self._draw_sprite(painter)

        # Speech bubble
        self._draw_speech(painter)

        # Action preview rect
        if self._preview_rect:
            painter.setPen(QPen(QColor(255, 0, 0, 180), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(self._preview_rect)

        painter.end()

    def _draw_sprite(self, painter: QPainter) -> None:
        if self.renderer_3d is not None:
            # ── 3D is active: re-raise window and sync position ──
            # The 3D window stays alive until Atom shuts down — no auto-fallback
            if self._render_win and not self._render_win.isVisible():
                self._draw_sprite_2d_placeholder(painter)
                try:
                    self._sync_render_win_pos()
                    self._render_win.show()
                    self._render_win.raise_()
                    log.info("3D window was hidden — re-showing")
                except Exception as exc:
                    log.warning("3D re-show failed: %s", exc)
                    # Only fallback to 2D if 3D is truly broken (exception on re-show)
                    self._fallback_to_2d()
                return

            self._render_win_hidden_since = None
            # Sync position every few frames so 3D window follows the sprite
            self._render_win_raise_counter += 1
            if self._render_win_raise_counter >= 15:
                self._render_win_raise_counter = 0
                if self._render_win:
                    try:
                        # raise_() only — never activateWindow(): stealing focus
                        # from the user's active app on a timer is hostile.
                        self._sync_render_win_pos()
                    except Exception:
                        pass
            return

        # ── Fallback to 2D sprite ──
        self._draw_sprite_2d(painter)

    def _draw_sprite_2d_placeholder(self, painter: QPainter) -> None:
        """Draw the 2D sprite while the 3D window is missing.

        The 3D branch of :meth:`_draw_sprite` used to return without painting
        anything, so whenever the 3D window was hidden or buried the overlay
        rendered an empty frame — the character simply was not on screen. It now
        shows the 2D sprite as a stand-in, but only once the window has stayed
        hidden for ``_3D_HIDDEN_PLACEHOLDER_SECONDS``: a routine re-show is a
        single frame, and painting the sprite for it would flash behind the model.
        """
        now = _time.time()
        since = self.__dict__.get("_render_win_hidden_since")
        if since is None:
            self._render_win_hidden_since = now
            return
        if now - since < _3D_HIDDEN_PLACEHOLDER_SECONDS:
            return
        self._draw_sprite_2d(painter)

    def _draw_sprite_2d(self, painter: QPainter) -> None:
        self._sprite.draw(
            painter, int(self._sprite_x), int(self._sprite_y),
            pose=self.current_pose, animation_frame=self._animation_tick,
            emotion=self.current_emotion, micro_interaction=self.current_micro_interaction,
            boredom_tier=self.current_boredom_tier,
        )

    # ══════════════════════════════════════════════════════════════════
    # Speech bubble
    # ══════════════════════════════════════════════════════════════════

    def _speech_bubble_rect(self) -> tuple[int, int, int, int]:
        sprite = self._sync_sprite_rect()
        bw = min(300, max(200, len(self.speech_text) * 5))
        bx = max(10, min(sprite.center().x() - bw // 2, self.width() - bw - 10))
        by = max(10, sprite.top() - 100)
        return bx, by, bw, 100

    def _sync_sprite_rect(self) -> QRect:
        if self.renderer_3d is not None:
            w, h = self._3d_window_size()
            return QRect(int(self._sprite_x), int(self._sprite_y), w, h)
        return QRect(int(self._sprite_x), int(self._sprite_y), self._sprite.width, self._sprite.height)

    def _draw_speech(self, painter: QPainter) -> None:
        text = self.speech_text
        if not text:
            return
        sprite = self._sync_sprite_rect()
        bubble_width = min(300, max(200, len(text) * 5))
        painter.setFont(QFont("Segoe UI", 10))
        fm = painter.fontMetrics()
        tr = fm.boundingRect(QRect(0, 0, int(bubble_width - 20), 0),
                              Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, text)
        bubble_height = max(60, int(tr.height()) + 24)
        x = max(10, min(sprite.center().x() - bubble_width // 2, self.width() - bubble_width - 10))
        y = max(10, sprite.top() - bubble_height - 14)
        rect = QRectF(x, y, bubble_width, bubble_height)

        path = QPainterPath()
        path.addRoundedRect(rect, 12, 12)
        bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
        bg.setColorAt(0.0, QColor(10, 15, 26, 240))
        bg.setColorAt(1.0, QColor(16, 20, 32, 235))
        painter.setBrush(QBrush(bg))
        painter.setPen(QPen(QColor(0, 212, 255, 76), 1.5))
        painter.drawPath(path)

        # Tail
        tail_size = 10
        tail_path = QPainterPath()
        tail_path.moveTo(sprite.center().x() - tail_size, rect.bottom())
        tail_path.lineTo(sprite.center().x(), rect.bottom() + tail_size)
        tail_path.lineTo(sprite.center().x() + tail_size, rect.bottom())
        tail_path.closeSubpath()
        painter.setBrush(QBrush(QColor(10, 15, 26, 240)))
        painter.setPen(QPen(QColor(0, 212, 255, 76), 1.5))
        painter.drawPath(tail_path)

        painter.setFont(QFont("Segoe UI", 10))
        painter.setPen(QColor(235, 237, 242, 250))
        painter.drawText(QRectF(x + 10, y + 8, bubble_width - 20, bubble_height - 16),
                          Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, text)

    # ══════════════════════════════════════════════════════════════════
    # Ticks
    # ══════════════════════════════════════════════════════════════════

    def _tick_animation(self) -> None:
        self._animation_tick = (self._animation_tick + 1) % 8

    def _tick_update(self) -> None:
        pos = (self._sprite_x, self._sprite_y)
        pose = self.current_pose
        changed = pos != self._last_tick_pos or pose != self._last_tick_pose

        if not changed:
            self._idle_tick_count += 1
            if self._idle_tick_count > 10:
                self._idle_tick_count = 10
                self.update()
                return
        else:
            self._idle_tick_count = 0

        self._last_tick_pos = pos
        self._last_tick_pose = pose
        if changed:
            self.update_input_mask()
        if self.renderer_3d and pos != self._last_tick_pos:
            self._sync_render_win_pos()
        # Step 4: FPS monitoring (only when 3D is active)
        self._update_fps()
        self.update()

    def _sprite_rect(self) -> QRect:
        if self.renderer_3d is not None:
            w, h = self._3d_window_size()
            return QRect(int(self._sprite_x), int(self._sprite_y), w, h)
        return QRect(int(self._sprite_x), int(self._sprite_y), self._sprite.width, self._sprite.height)

    # ══════════════════════════════════════════════════════════════════
    # Chat panel
    # ══════════════════════════════════════════════════════════════════

    def _build_float_button(self) -> QPushButton:
        btn = QPushButton("\U0001F4AC", self)
        btn.setToolTip("Restore chat (Ctrl+M) — right-click the sprite for more")
        btn.setFixedSize(44, 44)
        btn.setStyleSheet(
            "QPushButton { background: rgba(10,15,26,218); border: 2px solid rgba(0,212,255,102);"
            " border-radius: 22px; font-size: 18px; color: rgba(0,212,255,200); }"
            "QPushButton:hover { background: rgba(0,212,255,51); border-color: rgba(0,212,255,200); color: white; }"
        )
        btn.hide()
        btn.clicked.connect(self._restore_chat)
        return btn

    def _build_chat_panel(self) -> QFrame:
        panel = QFrame(self)
        panel.setObjectName("chatPanel")
        panel.setStyleSheet(
            "QFrame#chatPanel { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            " stop:0 rgba(8,12,22,235), stop:1 rgba(12,16,28,240));"
            " border: 1px solid rgba(0,212,255,50); border-radius: 16px; }"
            "QLabel { color: rgba(200,202,212,230); background: transparent; font-family: 'Courier New'; }"
            "QLineEdit { color: #e4e6ea; background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 #060C18, stop:1 #0A1020);"
            " border: 1px solid rgba(0,212,255,80); border-radius: 12px; padding: 8px 14px;"
            " font-size: 12px; font-family: 'Courier New';"
            " selection-background-color: rgba(0,119,255,220); }"
            "QLineEdit:focus { border: 1px solid rgba(0,212,255,204); background: rgba(4,8,18,248); }"
            "QPushButton { color: #8AADCC; background: transparent;"
            " border: 1px solid rgba(0,212,255,51); border-radius: 8px; padding: 4px 8px;"
            " font-size: 11px; font-family: 'Courier New'; }"
            "QPushButton:hover { color: white; background: rgba(0,212,255,51);"
            " border-color: rgba(0,212,255,127); }"
            "QScrollBar:vertical { width: 5px; background: rgba(0,212,255,10); border-radius: 3px;"
            " margin: 2px 0; }"
            "QScrollBar::handle:vertical { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            " stop:0 rgba(0,180,255,90), stop:1 rgba(0,212,255,120));"
            " border-radius: 3px; min-height: 24px; }"
            "QScrollBar::handle:vertical:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            " stop:0 rgba(0,180,255,170), stop:1 rgba(0,212,255,200)); }"
        )

        # Header
        header = QFrame(panel)
        header.setStyleSheet("QFrame { background: transparent; border: none; }")
        hdr = QHBoxLayout(header)
        hdr.setContentsMargins(8, 2, 8, 2)

        self.status_label = QLabel("ATOM", header)
        self.status_label.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        self.status_label.setStyleSheet("color: rgba(0,212,255,220);")
        hdr.addWidget(self.status_label, 1)

        self._mode_label = QLabel("TAP", header)
        self._mode_label.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._mode_label.setStyleSheet("color: rgba(0,212,255,200); padding: 1px 8px;"
                                        " border: 1px solid rgba(0,212,255,80); border-radius: 8px;"
                                        " background: rgba(0,212,255,25);")
        hdr.addWidget(self._mode_label, 0)

        self.msg_count_label = QLabel("0 msgs", header)
        self.msg_count_label.setFont(QFont("Courier New", 8))
        self.msg_count_label.setStyleSheet("color: rgba(138,173,204,120);")
        hdr.addWidget(self.msg_count_label, 0)

        # Minimize (collapse) — a labelled button. The old glyph-only "–" button
        # was easy to miss and font-dependent, which is why the minimise feature
        # kept being reported as missing. Ctrl+M, Esc and the sprite's context
        # menu all do the same thing.
        _hdr_btn_style = (
            "QPushButton { color: rgba(0,212,255,200); background: transparent;"
            " border: 1px solid rgba(0,212,255,60); border-radius: 8px;"
            " font-size: 9px; font-weight: bold; padding: 0; }"
            "QPushButton:hover { color: white; background: rgba(0,212,255,60);"
            " border-color: rgba(0,212,255,160); }"
        )

        self._min_btn = QPushButton("MIN", header)
        self._min_btn.setFixedSize(34, 22)
        self._min_btn.setToolTip("Minimize chat (Ctrl+M or Esc)")
        self._min_btn.setAccessibleName("Minimize chat")
        self._min_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._min_btn.setStyleSheet(_hdr_btn_style)
        self._min_btn.clicked.connect(self._minimize_chat)
        hdr.addWidget(self._min_btn, 0)

        self._clear_btn = QPushButton("CLR", header)
        self._clear_btn.setFixedSize(34, 22)
        self._clear_btn.setToolTip("Clear chat history")
        self._clear_btn.setAccessibleName("Clear chat history")
        self._clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_btn.setStyleSheet(_hdr_btn_style)
        self._clear_btn.clicked.connect(self.clear_chat)
        hdr.addWidget(self._clear_btn, 0)

        # Chat list
        self.chat_list = QListWidget(panel)
        self.chat_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.chat_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chat_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._delegate = ChatBubbleDelegate(self.chat_list)
        self.chat_list.setItemDelegate(self._delegate)
        self.chat_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.chat_list.setSpacing(2)
        self.chat_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.chat_list.customContextMenuRequested.connect(self._show_chat_context_menu)
        self.chat_list.setStyleSheet("QListWidget { background: transparent; border: none; }")

        # Input
        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(4)
        self.input = QLineEdit(panel)
        self.input.setPlaceholderText("ask atom anything...")
        self.input.returnPressed.connect(self._on_input_entered)
        # Override placeholder styling via QSS — inline style already handles it

        send_btn = QPushButton("\u2192", panel)
        send_btn.setFixedSize(30, 28)
        send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        send_btn.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            " stop:0 rgba(0,130,220,200), stop:1 rgba(0,180,255,220));"
            " border: none; border-radius: 14px; font-size: 14px; color: white;"
            " font-weight: bold; }"
            "QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            " stop:0 rgba(0,150,240,220), stop:1 rgba(0,200,255,240)); }"
            "QPushButton:pressed { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,"
            " stop:0 rgba(0,100,190,200), stop:1 rgba(0,150,220,220)); }"
        )
        send_btn.clicked.connect(self._on_input_entered)

        settings_btn = QPushButton("\u2699", panel)
        settings_btn.setFixedSize(28, 28)
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setStyleSheet(
            "QPushButton { color: rgba(138,173,204,160); background: transparent;"
            " border: 1px solid rgba(0,212,255,40); border-radius: 14px;"
            " font-size: 12px; }"
            "QPushButton:hover { color: white; background: rgba(0,212,255,40);"
            " border-color: rgba(0,212,255,120); }"
        )
        settings_btn.clicked.connect(self._open_settings)

        stop_btn = QPushButton("\u23F9", panel)
        stop_btn.setFixedSize(28, 28)
        stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        stop_btn.setStyleSheet(
            "QPushButton { color: rgba(255,100,100,160); background: transparent;"
            " border: 1px solid rgba(255,80,80,40); border-radius: 14px;"
            " font-size: 11px; }"
            "QPushButton:hover { color: white; background: rgba(255,80,80,50);"
            " border-color: rgba(255,80,80,120); }"
        )
        stop_btn.clicked.connect(self.stop_requested.emit)

        input_row.addWidget(self.input, 1)
        input_row.addWidget(send_btn)
        input_row.addWidget(settings_btn)
        input_row.addWidget(stop_btn)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 6, 10, 8)
        layout.setSpacing(4)
        layout.addWidget(header)
        sep = QFrame(panel)
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: rgba(0,212,255,25); max-height: 1px;")
        layout.addWidget(sep)
        layout.addWidget(self.chat_list, 1)
        layout.addLayout(input_row)
        return panel

    def _layout_chat_panel(self) -> None:
        margin = 16
        # Wide enough for a full answer with its timestamp, tall enough to keep
        # a real conversation visible without scrolling on every turn.
        pw = min(680, max(420, int(self.width() * 0.42)))
        ph = min(760, max(380, int(self.height() * 0.68)))
        # Always position chat panel at bottom-right corner
        self._chat_panel.setGeometry(
            max(margin, self.width() - pw - margin),
            max(margin, self.height() - ph - margin), pw, ph,
        )
        # Float button at bottom-left (only visible when minimized)
        btn_sz = 48
        self._float_button.setGeometry(margin, self.height() - btn_sz - margin, btn_sz, btn_sz)
        # Row heights were computed for the previous panel width.
        QTimer.singleShot(0, self._refresh_bubble_sizes)

    def _minimize_chat(self) -> None:
        self._chat_minimized = True
        self._animate_chat_out()
        self.chat_toggled.emit(False)

    def _restore_chat(self) -> None:
        self._chat_minimized = False
        self._animate_chat_in()
        self.input.setFocus()
        self.chat_toggled.emit(True)
        # Scroll to bottom after animation completes
        QTimer.singleShot(350, lambda: self.chat_list.scrollToBottom())

    def toggle_chat(self) -> None:
        if self._chat_minimized:
            self._restore_chat()
        else:
            self._minimize_chat()

    # ══════════════════════════════════════════════════════════════════
    # Chat messages
    # ══════════════════════════════════════════════════════════════════

    def _add_date_separator(self, dt: datetime.datetime) -> None:
        """Add a date separator item to the chat list."""
        item = QListWidgetItem()
        date_str = dt.strftime("%B %d, %Y")
        item.setText(date_str)
        item.setData(Qt.ItemDataRole.UserRole + 1, "system")     # role
        item.setData(Qt.ItemDataRole.UserRole + 5, True)          # date_separator flag
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setSizeHint(QSize(max(300, self.chat_list.width() - 4), 28))
        self.chat_list.addItem(item)
        self.chat_list.scrollToBottom()

    def add_chat_message(self, role: str, text: str, animate: bool = False,
                         persist: bool = True, ts: Optional[datetime.datetime] = None) -> None:
        """Append a message to the panel.

        ``persist`` writes user/assistant turns to the transcript file (see
        ``core.chat_history``); restoring the transcript passes ``persist=False``
        so replaying it does not rewrite it. ``ts`` overrides the timestamp, used
        when restoring so the bubble shows when it was actually said.
        """
        if not text:
            return
        self._stop_typing()
        now = ts or datetime.datetime.now()
        today = now.strftime("%Y-%m-%d")

        # ── Date separator (if day changed from last message) ──
        if self._last_chat_date is not None and self._last_chat_date != today:
            self._add_date_separator(now)
        self._last_chat_date = today

        # ── Message grouping: check if last message was same author ──
        is_grouped = False
        last_item = None
        for i in range(self.chat_list.count() - 1, -1, -1):
            li = self.chat_list.item(i)
            if li and li.data(Qt.ItemDataRole.UserRole + 1) is not None:
                last_item = li
                break
        if last_item is not None:
            last_role = last_item.data(Qt.ItemDataRole.UserRole + 1)
            last_is_sep = last_item.data(Qt.ItemDataRole.UserRole + 5) or False  # 5 = date_separator
            if last_role == role and not last_is_sep:
                is_grouped = True

        item = QListWidgetItem()
        item.setText(text)
        item.setData(Qt.ItemDataRole.UserRole + 1, role)
        item.setData(Qt.ItemDataRole.UserRole + 2, now.strftime("%H:%M"))
        item.setData(Qt.ItemDataRole.UserRole + 3, today)
        item.setData(Qt.ItemDataRole.UserRole + 4, is_grouped)  # 4 = grouped flag
        item.setData(Qt.ItemDataRole.UserRole + 5, False)       # 5 = date_separator flag
        # No stored size hint: ChatBubbleDelegate.sizeHint() measures for the
        # real list width, which keeps the row and the painted bubble identical.

        if animate and role == "assistant" and len(text) > 5:
            self._start_typing(text, item)
        else:
            self.chat_list.addItem(item)
            QTimer.singleShot(30, self.chat_list.scrollToBottom)
        if persist:
            self._chat_log = append_chat_message(self._chat_log, role, text, ts=now)
        self._update_msg_count()

    def _refresh_bubble_sizes(self) -> None:
        """Drop any stored row sizes and re-lay the list for the current width.

        Rows are sized by ``ChatBubbleDelegate.sizeHint`` now; a stored hint from
        an earlier (narrower) panel width is what left text clipped or floating
        in empty space after a restore or a resize.
        """
        for i in range(self.chat_list.count()):
            item = self.chat_list.item(i)
            if item is None or item.data(Qt.ItemDataRole.UserRole + 5):
                continue
            if item in (self._typing_item,):
                continue
            item.setSizeHint(QSize(-1, -1))
        self.chat_list.scheduleDelayedItemsLayout()

    # ── Transcript persistence ──

    def _restore_chat_history(self) -> None:
        """Replay the persisted transcript into the panel.

        Silent and un-animated (the typing effect is for live replies), and
        ``persist=False`` so restoring does not rewrite the file.
        """
        entries = load_chat_history()
        if not entries:
            return
        for entry in entries:
            self.add_chat_message(
                entry["role"], entry["content"],
                animate=False, persist=False, ts=parse_timestamp(entry.get("ts")),
            )
        self._chat_log = [dict(e) for e in entries]
        self._last_chat_date = None  # a fresh message starts its own day group
        log.info("Restored %d chat message(s) from %s", len(entries), HISTORY_PATH.name)
        QTimer.singleShot(0, self._refresh_bubble_sizes)
        self.chat_list.scrollToBottom()

    def _update_msg_count(self) -> None:
        count = sum(1 for i in range(self.chat_list.count())
                    if self.chat_list.item(i)
                    and self.chat_list.item(i).data(Qt.ItemDataRole.UserRole + 6) != "thinking"
                    and not self.chat_list.item(i).data(Qt.ItemDataRole.UserRole + 5))
        self.msg_count_label.setText(f"{count} msgs")

    # ══════════════════════════════════════════════════════════════════
    # Typing animation
    # ══════════════════════════════════════════════════════════════════

    def _start_typing(self, text: str, item: QListWidgetItem) -> None:
        self._stop_typing()
        self._typing_target = text
        self._typing_pos = 0
        self._typing_item = item
        self._typing_item.setText("")
        self.chat_list.addItem(self._typing_item)
        self.chat_list.scrollToBottom()

        text_len = max(1, len(text))
        target_ms = max(1500, min(3000, text_len * 20))
        ticks_needed = text_len / 4
        self._typing_speed = max(8, min(30, int(target_ms / ticks_needed)))

        self._typing_timer = QTimer(self)
        self._typing_timer.timeout.connect(self._typing_tick)
        self._typing_timer.start(self._typing_speed)

    def _typing_tick(self) -> None:
        if self._typing_item is None or self._typing_pos >= len(self._typing_target):
            self._finish_typing()
            return
        chunk = min(random.randint(3, 6), len(self._typing_target) - self._typing_pos)
        self._typing_pos += chunk
        visible = self._typing_target[:self._typing_pos]
        display = visible + "\u258C" if self._typing_pos < len(self._typing_target) else visible
        self._typing_item.setText(display)
        self.chat_list.scrollToBottom()

    def _finish_typing(self) -> None:
        item = self._typing_item
        target = self._typing_target
        if self._typing_timer:
            try:
                self._typing_timer.stop()
            except Exception:
                pass
            self._typing_timer = None
        self._typing_target = ""
        self._typing_pos = 0
        self._typing_item = None
        if item and target:
            try:
                if item.listWidget() is not None:
                    item.setText(target)
                    self.chat_list.scheduleDelayedItemsLayout()
                    self.chat_list.scrollToBottom()
            except RuntimeError:
                pass

    def _stop_typing(self, finalize: bool = True) -> None:
        # Finish the reply that is mid-type before dropping it: the timer used
        # to be stopped without writing the remaining text, so whenever another
        # message arrived the previous answer stayed frozen at a few characters.
        if finalize and self._typing_item is not None and self._typing_target:
            try:
                if self._typing_item.listWidget() is not None:
                    self._typing_item.setText(self._typing_target)
                    self.chat_list.scheduleDelayedItemsLayout()
            except RuntimeError:
                pass
        if self._typing_timer:
            try:
                self._typing_timer.stop()
            except Exception:
                pass
            self._typing_timer = None
        self._typing_target = ""
        self._typing_pos = 0
        self._typing_item = None

    # ══════════════════════════════════════════════════════════════════
    # Thinking indicator
    # ══════════════════════════════════════════════════════════════════

    # ══════════════════════════════════════════════════════════════════
    # Click-through mode
    # ══════════════════════════════════════════════════════════════════

    def _reset_idle_timer(self) -> None:
        """Reset the idle timer. If click-through is active, disable it.
        Called on any user interaction (mouse move, key press, etc.).
        """
        if self._click_through_active:
            self._disable_click_through()
        self._idle_click_timer.stop()
        self._idle_click_timer.start(8000)

    def _enable_click_through(self) -> None:
        """Let clicks fall through to whatever is underneath, while staying visible.

        Done with ``WA_TransparentForMouseEvents`` rather than by removing the
        sprite from the window mask: the mask also clips painting, so the old
        approach made the character disappear and left no way to wake it.
        """
        if not self._click_through_active:
            self._click_through_active = True
            try:
                self.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents, True
                )
            except Exception:
                pass
            # The window no longer receives mouse events, so poll the cursor to
            # notice when the user reaches for the character again.
            self._cursor_watch_timer.start()
            self.update_input_mask()

    def _disable_click_through(self) -> None:
        """Disable click-through mode — sprite is interactive again."""
        if self._click_through_active:
            self._click_through_active = False
            try:
                self.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents, False
                )
            except Exception:
                pass
            self._cursor_watch_timer.stop()
            self.update_input_mask()

    def _watch_cursor(self) -> None:
        """Re-enable interaction as soon as the cursor reaches an interactive area."""
        if not self._click_through_active:
            return
        pos = self.mapFromGlobal(QCursor.pos())
        targets = [self._sprite_rect().adjusted(-8, -8, 8, 8)]
        targets.append(
            self._float_button.geometry() if self._chat_minimized
            else self._chat_panel.geometry()
        )
        targets.append(self._status_hud.geometry())
        for rect in targets:
            if rect.contains(pos):
                self._disable_click_through()
                return

    # ══════════════════════════════════════════════════════════════════
    # Smooth animations (QPropertyAnimation)
    # ══════════════════════════════════════════════════════════════════

    def _animate_chat_in(self) -> None:
        """Animate chat panel sliding in from the right with opacity fade."""
        self._stop_chat_anim()
        self._stop_chat_out_timer()
        self._float_button.hide()
        self._chat_panel.show()

        # Set opacity effect for fade
        opacity_effect = QGraphicsOpacityEffect(self._chat_panel)
        opacity_effect.setOpacity(0.0)
        self._chat_panel.setGraphicsEffect(opacity_effect)

        # Start position: slide from right (100px off-screen)
        target_geo = self._chat_panel.geometry()
        start_geo = QRect(target_geo.x() + 100, target_geo.y(),
                          target_geo.width(), target_geo.height())
        self._chat_panel.setGeometry(start_geo)

        # Opacity animation
        fade_anim = QPropertyAnimation(opacity_effect, b"opacity")
        fade_anim.setDuration(250)
        fade_anim.setStartValue(0.0)
        fade_anim.setEndValue(1.0)
        fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Position animation
        pos_anim = QPropertyAnimation(self._chat_panel, b"geometry")
        pos_anim.setDuration(300)
        pos_anim.setStartValue(start_geo)
        pos_anim.setEndValue(target_geo)
        pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Group animations via parallel timers
        fade_anim.start()
        pos_anim.start()
        self._chat_anim = pos_anim
        fade_anim.finished.connect(self._on_chat_anim_done)

    def _animate_chat_out(self) -> None:
        """Animate chat panel sliding out to the right with opacity fade."""
        self._stop_chat_anim()

        opacity_effect = QGraphicsOpacityEffect(self._chat_panel)
        opacity_effect.setOpacity(1.0)
        self._chat_panel.setGraphicsEffect(opacity_effect)

        target_geo = self._chat_panel.geometry()
        end_geo = QRect(target_geo.x() + 100, target_geo.y(),
                        target_geo.width(), target_geo.height())

        # Opacity animation
        fade_anim = QPropertyAnimation(opacity_effect, b"opacity")
        fade_anim.setDuration(200)
        fade_anim.setStartValue(1.0)
        fade_anim.setEndValue(0.0)
        fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Position animation
        pos_anim = QPropertyAnimation(self._chat_panel, b"geometry")
        pos_anim.setDuration(250)
        pos_anim.setStartValue(target_geo)
        pos_anim.setEndValue(end_geo)
        pos_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _on_out_done():
            # Idempotent — the fade signal and the safety timer may both fire.
            if not self._chat_minimized:
                return  # restored in the meantime: never hide a live panel
            self._chat_panel.hide()
            self._chat_panel.setGraphicsEffect(None)
            self._float_button.show()
            self.update_input_mask()

        fade_anim.finished.connect(_on_out_done)
        fade_anim.start()
        pos_anim.start()
        self._chat_anim = pos_anim
        # Guarantee the end state even if the animation never reports finished.
        self._stop_chat_out_timer()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(_on_out_done)
        timer.start(400)
        self._chat_out_timer = timer

    def _stop_chat_out_timer(self) -> None:
        """Cancel the chat-minimize safety timer, if one is pending."""
        if self._chat_out_timer is not None:
            try:
                self._chat_out_timer.stop()
            except Exception:
                pass
            self._chat_out_timer = None

    def _stop_chat_anim(self) -> None:
        if self._chat_anim:
            try:
                self._chat_anim.stop()
            except Exception:
                pass
            self._chat_anim = None
        # Also clean up the graphics effect to prevent orphan animations
        try:
            self._chat_panel.setGraphicsEffect(None)
        except Exception:
            pass

    def _on_chat_anim_done(self) -> None:
        self._chat_panel.setGraphicsEffect(None)
        self.update_input_mask()

    # ══════════════════════════════════════════════════════════════════
    # Right-click context menu
    # ══════════════════════════════════════════════════════════════════

    def contextMenuEvent(self, event) -> None:
        """Global right-click context menu for the overlay."""
        self._build_context_menu().exec(event.globalPos())

    def _build_context_menu(self) -> QMenu:
        """Build (without showing) the overlay context menu.

        Split out of :meth:`contextMenuEvent` so the menu's contents can be
        asserted in tests — ``exec()`` blocks, so the old code was untestable.
        """
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu { background: rgba(10,15,26,240); border: 1px solid rgba(0,212,255,60);"
            " border-radius: 8px; padding: 4px; font-family: 'Courier New'; font-size: 12px; }"
            "QMenu::item { color: rgba(200,212,230,220); padding: 6px 24px 6px 12px;"
            " border-radius: 4px; }"
            "QMenu::item:selected { background: rgba(0,212,255,40); color: white; }"
            "QMenu::separator { height: 1px; background: rgba(0,212,255,30);"
            " margin: 4px 8px; }"
            "QMenu::icon { margin-right: 8px; }"
        )

        # ── Chat toggle ──
        toggle_chat_action = menu.addAction(
            "\U0001F4AC  Toggle Chat" if self._chat_minimized else "\U0001F4AC  Minimize Chat"
        )
        toggle_chat_action.triggered.connect(self.toggle_chat)

        menu.addSeparator()

        # ── Renderer submenu ──
        # Until now there was no way back from the automatic 3D→2D fallback.
        render_menu = menu.addMenu("\U0001F5A5  Renderer")
        render_menu.setStyleSheet(menu.styleSheet())
        act_2d = render_menu.addAction("2D sprite")
        act_3d = render_menu.addAction("3D model")
        for act in (act_2d, act_3d):
            act.setCheckable(True)
        if self.renderer_3d is not None:
            act_3d.setChecked(True)
            act_2d.setToolTip("Drop the 3D window and use the 2D sprite")
        else:
            act_2d.setChecked(True)
            act_3d.setToolTip("Try the 3D model again")
        act_2d.triggered.connect(self.use_2d)
        act_3d.triggered.connect(self.restore_3d)

        menu.addSeparator()

        # ── Mode submenu ──
        mode_menu = menu.addMenu("\U0001F3AF  Switch Mode")
        mode_menu.setStyleSheet(menu.styleSheet())
        current_mode = self._config.mode.upper()
        for mode_name in ("TAP", "STALK", "WHISPER", "LULLABY", "FORMAL"):
            action = mode_menu.addAction(mode_name)
            if mode_name == current_mode:
                action.setCheckable(True)
                action.setChecked(True)
            action.triggered.connect(
                lambda checked=False, m=mode_name.lower(): self.mode_change_requested.emit(m)
            )

        # ── Character submenu ──
        char_menu = menu.addMenu("\U0001F9D1  Switch Character")
        char_menu.setStyleSheet(menu.styleSheet())
        current_char = self._config.character
        from ui.sprite_2d import CHARACTER_PROFILES
        for char_name in CHARACTER_PROFILES:
            action = char_menu.addAction(char_name.capitalize())
            if char_name == current_char:
                action.setCheckable(True)
                action.setChecked(True)
            action.triggered.connect(
                lambda checked=False, c=char_name: self._change_character(c)
            )

        menu.addSeparator()

        # ── TTS toggle ──
        tts_enabled = self._config.get("tts_enabled", True)
        tts_action = menu.addAction(
            "\U0001F50A  Disable TTS" if tts_enabled else "\U0001F507  Enable TTS"
        )
        tts_action.triggered.connect(
            lambda: self.voice_toggle_requested.emit(not tts_enabled)
        )

        menu.addSeparator()

        # ── Settings ──
        settings_action = menu.addAction("\u2699  Settings")
        settings_action.triggered.connect(self._open_settings)

        menu.addSeparator()

        # ── Quit ──
        quit_action = menu.addAction("\u2716  Quit Atom")
        quit_action.triggered.connect(QApplication.quit)

        return menu

    def _change_character(self, name: str) -> None:
        """Change character and emit signal."""
        self._sprite.set_character(name)
        if self.renderer_3d:
            self.renderer_3d.set_profile(name)
        self.character_changed.emit(name)

    def set_thinking(self, active: bool) -> None:
        self._remove_thinking_items()
        if self._thinking_timer:
            self._thinking_timer.stop()
            self._thinking_timer = None
        if active:
            self._thinking_dots = 0
            self._add_thinking_item()  # Show thinking indicator immediately
            self._thinking_timer = QTimer(self)
            self._thinking_timer.timeout.connect(self._animate_thinking)
            self._thinking_timer.start(500)

    def _remove_thinking_items(self) -> None:
        """Drop every thinking placeholder.

        The marker lives at ``UserRole + 6`` (see ``_add_thinking_item``).
        ``_animate_thinking`` used to look for it at ``UserRole``, found nothing,
        and added another placeholder every 500 ms — a long answer left a column
        of "Atom is thinking…" bubbles above the reply.
        """
        for i in range(self.chat_list.count() - 1, -1, -1):
            item = self.chat_list.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole + 6) == "thinking":
                self.chat_list.takeItem(i)

    def _add_thinking_item(self) -> None:
        dots = "." * (self._thinking_dots + 1)
        text = f"\u23f3  Atom is thinking{dots}"
        item = QListWidgetItem()
        item.setText(text)
        item.setData(Qt.ItemDataRole.UserRole + 6, "thinking")
        item.setData(Qt.ItemDataRole.UserRole + 1, "system")
        item.setForeground(QColor(150, 170, 200, 200))
        item.setFont(QFont("Segoe UI", 10))
        # No stored size hint: a hard-coded 36px row was shorter than the bubble
        # the delegate paints (~54px), so the indicator was cut in half at the
        # bottom of the panel. Let the delegate measure it like any other row.
        self.chat_list.addItem(item)
        # Scroll after the row is laid out: scrolling right after addItem() uses
        # the previous extents and leaves the indicator hanging off the bottom.
        QTimer.singleShot(30, self.chat_list.scrollToBottom)

    def _animate_thinking(self) -> None:
        self._thinking_dots = (self._thinking_dots + 1) % 3
        self._remove_thinking_items()
        self._add_thinking_item()

    # ══════════════════════════════════════════════════════════════════
    # Context menu
    # ══════════════════════════════════════════════════════════════════

    def _show_chat_context_menu(self, pos) -> None:
        item = self.chat_list.itemAt(pos)
        if not item:
            return
        text = item.text()
        if not text:
            return
        menu = QMenu(self.chat_list)
        copy_action = menu.addAction("Copy text")
        action = menu.exec(self.chat_list.viewport().mapToGlobal(pos))
        if action == copy_action:
            QApplication.clipboard().setText(text)

    # ══════════════════════════════════════════════════════════════════
    # Input
    # ══════════════════════════════════════════════════════════════════

    def _on_input_entered(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self.user_input.emit(text)

    # ══════════════════════════════════════════════════════════════════
    # Settings
    # ══════════════════════════════════════════════════════════════════

    def _open_settings(self) -> None:
        dialog = AtomSettingsDialog(self._config, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            changes = dialog.get_changes()
            if changes:
                self.settings_changed.emit(changes)
                if "tts_enabled" in changes:
                    self.voice_toggle_requested.emit(changes["tts_enabled"])
                if "tts_speed" in changes:
                    self.tts_speed_requested.emit(changes["tts_speed"])
                if "tts_volume" in changes:
                    self.tts_volume_requested.emit(changes["tts_volume"])
                if "mode" in changes:
                    self.mode_change_requested.emit(changes["mode"])
                if "character" in changes:
                    self._sprite.set_character(changes["character"])
                    if self.renderer_3d:
                        self.renderer_3d.set_profile(changes["character"])
                    self.character_changed.emit(changes["character"])
                if "render_mode" in changes:
                    self._handle_render_mode_change(changes["render_mode"])

    def _handle_render_mode_change(self, new_mode: str) -> None:
        if new_mode == "2d" and self.renderer_3d:
            self._stop_renderer_animation()
            self.renderer_3d.hide()
            self.renderer_3d.deleteLater()
            self.renderer_3d = None
            # _fallback_to_2d() tore this container down but switching render
            # mode did not, leaving an empty frameless always-on-top window.
            render_win = getattr(self, "_render_win", None)
            if render_win is not None:
                try:
                    render_win.hide()
                    render_win.deleteLater()
                except Exception:
                    pass
                self._render_win = None
            self.update_input_mask()
        elif new_mode in ("3d", "auto") and self.renderer_3d is None:
            self._init_3d()
        self.update()

    # ══════════════════════════════════════════════════════════════════
    # Public setters
    # ══════════════════════════════════════════════════════════════════

    def set_speech(self, text: str) -> None:
        self.speech_text = text
        if text:
            self._speech_timer.stop()
            self._reset_idle_timer()  # Speech bubble appears → keep sprite clickable
        self.update_input_mask()
        self.update()

    def set_status(self, text: str) -> None:
        self.status_text = text
        self.status_label.setText(text or "")
        self.update()

    def set_pose(self, pose: str) -> None:
        self.current_pose = pose
        if self.renderer_3d:
            self.renderer_3d.set_pose(pose)

    def set_emotion(self, emotion: str) -> None:
        self.current_emotion = emotion or "neutral"
        speed = self._EMOTION_SPEED_MAP.get(self.current_emotion, 1.0)
        self._sprite.set_mood_speed(speed)
        if self.renderer_3d:
            self.renderer_3d.set_emotion(self.current_emotion)
        self.update()

    def set_micro_interaction(self, name: str) -> None:
        self.current_micro_interaction = name or ""
        self.update()

    def set_boredom_tier(self, tier: int) -> None:
        self.current_boredom_tier = tier
        self.update()

    # ══════════════════════════════════════════════════════════════════
    # Step 3: Gemini key warning
    # ══════════════════════════════════════════════════════════════════

    def set_gemini_key_warning(self, warning: str) -> None:
        """Show or clear a Gemini API key warning on the overlay."""
        self._gemini_key_warning = warning
        self.update()

    # ══════════════════════════════════════════════════════════════════
    # Step 4: FPS monitoring for 3D→2D auto-fallback
    # ══════════════════════════════════════════════════════════════════

    def _update_fps(self) -> None:
        """Measure the 3D renderer's *real* frame rate and downgrade if hopeless.

        Uses the renderer's ``frame_rendered`` counter, not the overlay update
        timer. The timer runs at a fixed 10 Hz, so counting timer ticks meant the
        measured "FPS" was always ~10 — permanently below the old 15 FPS
        threshold. Every session got silently downgraded from 3D to 2D a few
        seconds after startup, which read as "the model disappears".
        """
        if self.renderer_3d is None or self._fps_fallback_done:
            return

        now = _time.time()
        elapsed = now - self._fps_time_start
        if elapsed < 4.0:
            return

        frames = self._3d_frames_rendered - self._fps_frame_count
        self._fps_current = frames / elapsed if elapsed > 0 else 0.0
        self._fps_frame_count = self._3d_frames_rendered
        self._fps_time_start = now

        if self._fps_current < self._fps_low_threshold:
            if self._fps_low_start is None:
                self._fps_low_start = now
            elif now - self._fps_low_start > self._fps_low_duration:
                self._fps_fallback_done = True
                log.info(
                    "3D renderer at %.2f FPS for %ss — auto 3D→2D fallback",
                    self._fps_current, self._fps_low_duration,
                )
                self._fallback_to_2d()
                self.show_message(
                    "*tap* 3D mode was too slow. Switched to 2D.",
                    duration=5000,
                )
        else:
            self._fps_low_start = None

    def set_mode_indicator(self, mode: str) -> None:
        mode = mode.upper()
        colors = {
            "TAP": "#00D4FF", "STALK": "#FFAA00",
            "WHISPER": "#00FF88", "LULLABY": "#AA66FF", "FORMAL": "#FFFFFF",
        }
        color = colors.get(mode, "#00D4FF")
        c = QColor(color)
        self._mode_label.setText(mode)
        self._mode_label.setStyleSheet(
            f"color: rgba({c.red()},{c.green()},{c.blue()},220); padding: 1px 8px;"
            f" border: 1px solid rgba({c.red()},{c.green()},{c.blue()},120);"
            f" border-radius: 8px; background: rgba({c.red()},{c.green()},{c.blue()},30);"
        )
        # Also update the persistent HUD widget
        if hasattr(self, '_status_hud'):
            self._status_hud.set_mode(mode)

    def show_message(self, text: str, duration: Optional[int] = None) -> None:
        self.set_speech(text)
        if duration is not None:
            self._speech_timer.start(duration)

    def clear_chat(self) -> None:
        self._stop_typing()
        if self._thinking_timer:
            self._thinking_timer.stop()
            self._thinking_timer = None
        self.chat_list.clear()
        self._last_chat_date = None
        # Clearing is the user saying "I don't want this history" — it must not
        # come back on the next launch.
        self._chat_log = []
        save_chat_history([])
        self._update_msg_count()

    def set_screen_context(self, window: str, text: str) -> None:
        self._screen_context = f"[{window}] {text[:300]}" if text else ""

    def get_context(self) -> str:
        return getattr(self, "_screen_context", "")

    def show_action_preview(self, action: dict) -> None:
        desc = action.get("description", action.get("action", "Action"))
        if "x" in action and "y" in action:
            self._preview_rect = QRect(int(action["x"]) - 25, int(action["y"]) - 5, 50, 10)
        else:
            self._preview_rect = QRect(int(self._sprite_x) - 10, int(self._sprite_y) - 10, 36, 36)
        self.set_speech(f"*tap* I'll {desc}. Proceed?")
        self.update()

    def clear_action_preview(self) -> None:
        self._preview_rect = None
        self.update()

    def resizeEvent(self, event) -> None:
        self._layout_chat_panel()
        self.update_input_mask()
        if self._render_win:
            self._sync_render_win_pos()
        self._reposition_status_hud()
        super().resizeEvent(event)

    def keyPressEvent(self, event) -> None:
        self._reset_idle_timer()
        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & Qt.KeyboardModifier.ShiftModifier)
        # Ctrl+M (and the legacy Ctrl+Shift+E) collapse/restore the chat panel.
        if ctrl and key == Qt.Key.Key_M:
            self.toggle_chat()
            event.accept()
            return
        if ctrl and shift and key == Qt.Key.Key_E:
            self.toggle_chat()
            event.accept()
            return
        # Escape collapses the chat while typing (does not quit anything).
        if key == Qt.Key.Key_Escape and not self._chat_minimized:
            self._minimize_chat()
            event.accept()
            return
        super().keyPressEvent(event)


# ════════════════════════════════════════════════════════════════════════════════
# Settings dialog
# ════════════════════════════════════════════════════════════════════════════════

_TAB_STYLE = """
    QGroupBox { color: #00D4FF; font-weight: 600; border: 1px solid rgba(0,212,255,40);
        border-radius: 6px; margin-top: 10px; padding-top: 14px; }
    QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left;
        padding: 2px 8px; color: #00D4FF; }
"""


class _SettingsTab(QWidget):
    def get_changes(self) -> dict[str, Any]:
        return {}


class GeneralSettingsTab(_SettingsTab):
    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config = config
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 12, 16, 12)

        mode_g = QGroupBox("Mode")
        mode_g.setStyleSheet(_TAB_STYLE)
        mode_l = QFormLayout(mode_g)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["tap", "stalk", "whisper", "lullaby", "formal"])
        idx = self.mode_combo.findText(config.mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        mode_l.addRow("Active mode:", self.mode_combo)
        layout.addWidget(mode_g)

        char_g = QGroupBox("Character")
        char_g.setStyleSheet(_TAB_STYLE)
        char_l = QFormLayout(char_g)
        self.char_combo = QComboBox()
        self.char_combo.addItems(["Atom (Default)", "Pink (Gamer Girl)", "Steve (Minecraft)",
                                   "Alex (Minecraft)", "Ghost Rider"])
        self._char_values = ["atom", "pink", "steve", "alex", "ghost_rider"]
        if config.character in self._char_values:
            self.char_combo.setCurrentIndex(self._char_values.index(config.character))
        char_l.addRow("Sprite:", self.char_combo)
        layout.addWidget(char_g)

        render_g = QGroupBox("Rendering")
        render_g.setStyleSheet(_TAB_STYLE)
        render_l = QFormLayout(render_g)
        self.render_combo = QComboBox()
        self.render_combo.addItems(["2D (Pixel sprite)", "3D (OpenGL)", "Auto"])
        self._render_values = ["2d", "3d", "auto"]
        if config.render_mode in self._render_values:
            self.render_combo.setCurrentIndex(self._render_values.index(config.render_mode))
        render_l.addRow("Render mode:", self.render_combo)
        layout.addWidget(render_g)

        intro_g = QGroupBox("Intro")
        intro_g.setStyleSheet(_TAB_STYLE)
        intro_l = QVBoxLayout(intro_g)
        self.intro_check = QCheckBox("Show intro on startup")
        self.intro_check.setChecked(config.intro.enabled)
        intro_l.addWidget(self.intro_check)
        layout.addWidget(intro_g)
        layout.addStretch()

    def get_changes(self) -> dict[str, Any]:
        c: dict[str, Any] = {}
        nm = self.mode_combo.currentText()
        if nm != self._config.mode:
            c["mode"] = nm
        ci = self.char_combo.currentIndex()
        if ci < len(self._char_values):
            nc = self._char_values[ci]
            if nc != self._config.character:
                c["character"] = nc
        ri = self.render_combo.currentIndex()
        if ri < len(self._render_values):
            nr = self._render_values[ri]
            if nr != self._config.render_mode:
                c["render_mode"] = nr
        if self.intro_check.isChecked() != self._config.intro.enabled:
            c["intro_enabled"] = self.intro_check.isChecked()
        return c


class VoiceSettingsTab(_SettingsTab):
    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config = config
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 12, 16, 12)

        voice_g = QGroupBox("Voice Output")
        voice_g.setStyleSheet(_TAB_STYLE)
        vl = QVBoxLayout(voice_g)
        self.voice_check = QCheckBox("Enable text-to-speech")
        self.voice_check.setChecked(config.tts_enabled)
        vl.addWidget(self.voice_check)

        sp_l = QHBoxLayout()
        sp_l.addWidget(QLabel("Speed:"))
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(50, 300)
        self.speed_slider.setValue(config.tts_speed)
        self.speed_label = QLabel(str(config.tts_speed))
        self.speed_label.setFixedWidth(36)
        self.speed_slider.valueChanged.connect(lambda v: self.speed_label.setText(str(v)))
        sp_l.addWidget(self.speed_slider, 1)
        sp_l.addWidget(self.speed_label)
        vl.addLayout(sp_l)

        vo_l = QHBoxLayout()
        vo_l.addWidget(QLabel("Volume:"))
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(int(config.tts_volume * 100))
        self.vol_label = QLabel(f"{int(config.tts_volume * 100)}%")
        self.vol_label.setFixedWidth(36)
        self.vol_slider.valueChanged.connect(lambda v: self.vol_label.setText(f"{v}%"))
        vo_l.addWidget(self.vol_slider, 1)
        vo_l.addWidget(self.vol_label)
        vl.addLayout(vo_l)
        layout.addWidget(voice_g)
        layout.addStretch()

    def get_changes(self) -> dict[str, Any]:
        c: dict[str, Any] = {}
        if self.voice_check.isChecked() != self._config.tts_enabled:
            c["tts_enabled"] = self.voice_check.isChecked()
        ns = self.speed_slider.value()
        if ns != self._config.tts_speed:
            c["tts_speed"] = ns
        nv = self.vol_slider.value() / 100.0
        if abs(nv - self._config.tts_volume) > 0.01:
            c["tts_volume"] = nv
        return c


class PromptSettingsTab(_SettingsTab):
    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config = config
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 12, 16, 12)

        prompt_g = QGroupBox("System Prompts")
        prompt_g.setStyleSheet(_TAB_STYLE)
        prompt_l = QVBoxLayout(prompt_g)
        prompt_l.addWidget(QLabel("Default:"))
        self.default_edit = QPlainTextEdit()
        self.default_edit.setPlainText(config.get("custom_default_prompt", ""))
        self.default_edit.setPlaceholderText("Custom default prompt")
        self.default_edit.setMinimumHeight(130)
        prompt_l.addWidget(self.default_edit)
        prompt_l.addWidget(QLabel("Formal:"))
        self.formal_edit = QPlainTextEdit()
        self.formal_edit.setPlainText(config.get("custom_formal_prompt", ""))
        self.formal_edit.setPlaceholderText("Custom formal prompt")
        self.formal_edit.setMinimumHeight(130)
        prompt_l.addWidget(self.formal_edit)
        layout.addWidget(prompt_g, 1)

    def get_changes(self) -> dict[str, Any]:
        c: dict[str, Any] = {}
        dp = self.default_edit.toPlainText().strip()
        if dp != self._config.get("custom_default_prompt", ""):
            c["system_prompt_default"] = dp
        fp = self.formal_edit.toPlainText().strip()
        if fp != self._config.get("custom_formal_prompt", ""):
            c["system_prompt_formal"] = fp
        return c


class AdvancedSettingsTab(_SettingsTab):
    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config = config
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 12, 16, 12)

        # Theme
        theme_g = QGroupBox("Theme")
        theme_g.setStyleSheet(_TAB_STYLE)
        tl = QFormLayout(theme_g)
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["atom_hud", "midnight", "cyber"])
        ct = config.theme.name
        ti = ["atom_hud", "midnight", "cyber"]
        if ct in ti:
            self.theme_combo.setCurrentIndex(ti.index(ct))
        tl.addRow("Theme:", self.theme_combo)
        glow_l = QHBoxLayout()
        glow_l.addWidget(QLabel("Glow:"))
        self.glow_edit = QLineEdit(config.theme.primary_glow)
        self.glow_edit.setMaxLength(7)
        self.glow_edit.setFixedWidth(80)
        glow_l.addWidget(self.glow_edit)
        glow_l.addStretch()
        tl.addRow("", glow_l)
        layout.addWidget(theme_g)

        # Browser AI
        browser_g = QGroupBox("Browser AI (DeepSeek)")
        browser_g.setStyleSheet(_TAB_STYLE)
        bl = QVBoxLayout(browser_g)
        auth_p = Path(__file__).resolve().parent.parent / "data" / "browser_auth.json"
        logged_in = auth_p.exists()
        self.browser_status = QLabel(
            "\u2713  Logged in" if logged_in else "\u2717  Not logged in"
        )
        self.browser_status.setStyleSheet(
            "color: #4caf50; font-weight: 600;" if logged_in else "color: #ff9800; font-weight: 600;"
        )
        bl.addWidget(self.browser_status)
        login_btn = QPushButton("Login to DeepSeek")
        login_btn.clicked.connect(self._on_login)
        bl.addWidget(login_btn)
        logout_btn = QPushButton("Logout")
        logout_btn.clicked.connect(self._on_logout)
        bl.addWidget(logout_btn)
        layout.addWidget(browser_g)
        layout.addStretch()

    def _on_login(self) -> None:
        p = self.parent()
        while p and not hasattr(p, "browser_login_requested"):
            p = p.parent()
        if p and hasattr(p, "browser_login_requested"):
            p.browser_login_requested.emit()

    def _on_logout(self) -> None:
        p = self.parent()
        while p and not hasattr(p, "browser_logout_requested"):
            p = p.parent()
        if p and hasattr(p, "browser_logout_requested"):
            p.browser_logout_requested.emit()
        auth_p = Path(__file__).resolve().parent.parent / "data" / "browser_auth.json"
        if not auth_p.exists():
            self.browser_status.setText("\u2717  Not logged in")
            self.browser_status.setStyleSheet("color: #ff9800; font-weight: 600;")

    def get_changes(self) -> dict[str, Any]:
        c: dict[str, Any] = {}
        ti = ["atom_hud", "midnight", "cyber"]
        nt = ti[self.theme_combo.currentIndex()] if self.theme_combo.currentIndex() < len(ti) else "atom_hud"
        if nt != self._config.theme.name:
            c["theme"] = nt
        ng = self.glow_edit.text().strip()
        if ng and ng != self._config.theme.primary_glow:
            c["theme_primary_glow"] = ng
        return c



class ProvidersSettingsTab(_SettingsTab):
    """Settings tab for LLM provider configuration."""
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(16, 12, 16, 12)

        # Primary provider
        primary_g = QGroupBox("Primary Provider")
        primary_g.setStyleSheet(_TAB_STYLE)
        primary_l = QFormLayout(primary_g)
        self.provider_combo = QComboBox()
        self.provider_combo.addItems(["ollama", "gemini", "browser"])
        idx = self.provider_combo.findText(getattr(config, 'llm_provider', 'ollama'))
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        primary_l.addRow("Primary:", self.provider_combo)
        desc = QLabel("Primary provider for LLM inference. Others act as fallback.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: rgba(180,182,192,160); font-size: 10px; font-style: italic;")
        primary_l.addRow(desc)
        layout.addWidget(primary_g)

        # Provider toggles
        toggle_g = QGroupBox("Enabled Providers")
        toggle_g.setStyleSheet(_TAB_STYLE)
        toggle_l = QVBoxLayout(toggle_g)
        toggle_l.setSpacing(4)

        from core.providers.gemini import get_gemini_keys as _get_keys
        from core.providers.browser import is_browser_available as _browser_avail

        self.gemini_check = QCheckBox("Gemini fallback (Google API)")
        gemini_fallback = getattr(config, 'ollama_fallback_to_gemini', None)
        if gemini_fallback is None:
            gemini_fallback = len(_get_keys(config)) > 0
        self.gemini_check.setChecked(gemini_fallback)
        toggle_l.addWidget(self.gemini_check)
        
        # Gemini key status
        self.gemini_status = QLabel()
        keys = _get_keys(config)
        if keys:
            self.gemini_status.setText(f'✅ {len(keys)} key(s) found')
            self.gemini_status.setStyleSheet("color: rgba(0,255,136,180); font-size: 10px; padding-left: 24px;")
        else:
            self.gemini_status.setText("⚠ No key - set GEMINI_API_KEY in .env")
            self.gemini_status.setStyleSheet("color: rgba(255,170,0,200); font-size: 10px; padding-left: 24px;")
        toggle_l.addWidget(self.gemini_status)

        self.browser_check = QCheckBox("Browser AI fallback (Playwright)")
        browser_fallback = getattr(config, 'browser_fallback_to_ollama', True)
        self.browser_check.setChecked(browser_fallback)
        toggle_l.addWidget(self.browser_check)

        if _browser_avail():
            browser_stat = QLabel("✅ Playwright ready")
            browser_stat.setStyleSheet("color: rgba(0,255,136,180); font-size: 10px; padding-left: 24px;")
        else:
            browser_stat = QLabel("⚠ Playwright not installed")
            browser_stat.setStyleSheet("color: rgba(255,170,0,200); font-size: 10px; padding-left: 24px;")
        toggle_l.addWidget(browser_stat)

        layout.addWidget(toggle_g)

        # ── Gemini key management ──
        # Five keys, editable here, written to .env (git-ignored). They are never
        # stored in data/config.json, which is committed to the repository.
        keys_g = QGroupBox("Gemini API keys")
        keys_g.setStyleSheet(_TAB_STYLE)
        kg = QVBoxLayout(keys_g)
        kg.setSpacing(4)
        note = QLabel("Saved to .env only. Leave a slot empty to remove that key.")
        note.setStyleSheet("color: rgba(170,180,200,180); font-size: 10px;")
        kg.addWidget(note)

        from core.providers.gemini import get_gemini_keys, save_gemini_keys, validate_gemini_key
        from core.providers.gemini import ENV_KEY_NAMES

        existing = get_gemini_keys(config)
        self._key_edits: list[QLineEdit] = []
        self._key_status: list[QLabel] = []
        for i, env_name in enumerate(ENV_KEY_NAMES):
            row = QHBoxLayout()
            row.setSpacing(6)
            label = QLabel(f"Key {i + 1}")
            label.setFixedWidth(46)
            edit = QLineEdit()
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.setPlaceholderText("not set" if i >= len(existing) else "configured")
            if i < len(existing):
                edit.setText(existing[i])
            status = QLabel("—")
            status.setFixedWidth(22)
            status.setStyleSheet("color: rgba(160,170,190,200);")
            edit.editingFinished.connect(lambda idx=i: self._check_key(idx, validate_gemini_key))
            self._key_edits.append(edit)
            self._key_status.append(status)
            row.addWidget(label)
            row.addWidget(edit, 1)
            row.addWidget(status)
            kg.addLayout(row)

        save_row = QHBoxLayout()
        save_row.addStretch()
        save_keys_btn = QPushButton("Save keys to .env")
        save_result = QLabel("")
        save_result.setStyleSheet("color: rgba(0,255,136,190); font-size: 10px;")

        def _save_keys() -> None:
            values = {
                ENV_KEY_NAMES[i]: edit.text().strip()
                for i, edit in enumerate(self._key_edits)
            }
            bad = []
            for i, edit in enumerate(self._key_edits):
                text = edit.text().strip()
                err = validate_gemini_key(text) if text else None
                if text and err:
                    bad.append(f"key {i + 1}: {err}")
            if bad:
                save_result.setStyleSheet("color: rgba(255,150,120,220); font-size: 10px;")
                save_result.setText("; ".join(bad))
                return
            save_gemini_keys(values)
            active = sum(1 for v in values.values() if v)
            save_result.setStyleSheet("color: rgba(0,255,136,190); font-size: 10px;")
            save_result.setText(f"Saved — {active} key(s) active")
            for idx in range(len(self._key_edits)):
                self._check_key(idx, validate_gemini_key)

        save_keys_btn.clicked.connect(_save_keys)
        save_row.addWidget(save_result)
        save_row.addWidget(save_keys_btn)
        kg.addLayout(save_row)
        layout.addWidget(keys_g)

        for idx in range(len(self._key_edits)):
            self._check_key(idx, validate_gemini_key)

        layout.addStretch()

    def _check_key(self, idx: int, validator) -> None:
        """Mark one key slot ✅ / ⚠ by format (no request, no quota)."""
        text = self._key_edits[idx].text().strip()
        if not text:
            self._key_status[idx].setText("—")
            self._key_status[idx].setToolTip("")
            return
        err = validator(text)
        if err:
            self._key_status[idx].setText("⚠")
            self._key_status[idx].setToolTip(err)
        else:
            self._key_status[idx].setText("✅")
            self._key_status[idx].setToolTip("Looks like a valid Gemini API key")

    def get_changes(self) -> dict:
        return {
            "llm_provider": self.provider_combo.currentText(),
            "ollama_fallback_to_gemini": self.gemini_check.isChecked(),
            "browser_fallback_to_ollama": self.browser_check.isChecked(),
        }



class AtomSettingsDialog(QDialog):
    browser_login_requested = pyqtSignal()
    browser_logout_requested = pyqtSignal()

    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._config = config
        self._changes: dict[str, Any] = {}
        self._tabs: list[_SettingsTab] = []
        self.setWindowTitle("Atom Settings")
        self.setFixedSize(560, 480)
        self.setStyleSheet(
            "QDialog { background: rgba(16,18,24,245); color: #e8eaee;"
            " border: 1px solid rgba(255,255,255,35); border-radius: 10px; }"
            "QLabel { color: #d8dae0; font-size: 12px; }"
            "QLineEdit { color: #e0e2e8; background: rgba(255,255,255,30);"
            " border: 1px solid rgba(255,255,255,40); border-radius: 4px; padding: 4px 8px; }"
            "QLineEdit:focus { border-color: rgba(0,212,255,150); }"
            "QPlainTextEdit { color: #e0e2e8; background: rgba(255,255,255,22);"
            " border: 1px solid rgba(255,255,255,38); border-radius: 4px;"
            " selection-background-color: rgba(0,170,220,180); }"
            "QCheckBox { color: #d8dae0; spacing: 8px; }"
            "QCheckBox::indicator { width: 16px; height: 16px; border-radius: 3px;"
            " border: 1px solid rgba(255,255,255,80); background: rgba(255,255,255,15); }"
            "QCheckBox::indicator:checked { background: rgba(0,212,255,230); }"
            "QSlider::groove:horizontal { height: 3px; background: rgba(255,255,255,30);"
            " border-radius: 2px; }"
            "QSlider::handle:horizontal { width: 12px; height: 12px; margin: -4px 0;"
            " border-radius: 6px; background: rgba(0,212,255,230); }"
            "QSlider::sub-page:horizontal { background: rgba(0,212,255,160); border-radius: 2px; }"
            "QComboBox { color: #e0e2e8; background: rgba(255,255,255,25);"
            " border: 1px solid rgba(255,255,255,40); border-radius: 4px; padding: 3px 6px; }"
            "QPushButton { color: white; background: rgba(0,170,220,210);"
            " border: none; border-radius: 5px; padding: 5px 14px; font-weight: 600; }"
            "QPushButton:hover { background: rgba(0,190,240,235); }"
            "QTabWidget::pane { border: none; background: transparent; }"
            "QTabBar::tab { color: rgba(200,202,212,160); background: transparent;"
            " border: none; padding: 8px 16px; font-size: 11px; font-weight: 600; }"
            "QTabBar::tab:selected { color: #e8eaee; border-bottom: 2px solid rgba(0,212,255,220); }"
            "QTabBar::tab:hover { color: rgba(220,222,230,200); }"
        )
        self._build_ui()

    def _build_ui(self) -> None:
        tabs = QTabWidget(self)
        general = GeneralSettingsTab(self._config, self)
        voice = VoiceSettingsTab(self._config, self)
        prompts = PromptSettingsTab(self._config, self)
        advanced = AdvancedSettingsTab(self._config, self)
        providers = ProvidersSettingsTab(self._config, self)
        self._tabs = [general, voice, prompts, advanced, providers]
        tabs.addTab(general, "General")
        tabs.addTab(voice, "Voice")
        tabs.addTab(prompts, "Prompts")
        tabs.addTab(advanced, "Advanced")
        tabs.addTab(providers, "Providers")

        btn_l = QHBoxLayout()
        btn_l.addStretch()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_l.addWidget(save_btn)
        btn_l.addWidget(cancel_btn)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 10)
        outer.addWidget(tabs, 1)
        outer.addLayout(btn_l)

    def _save(self) -> None:
        self._changes = {}
        for tab in self._tabs:
            self._changes.update(tab.get_changes())
        self.accept()

    def get_changes(self) -> dict[str, Any]:
        return self._changes
