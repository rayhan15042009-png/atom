"""
Shared intro sequence utilities — HUD drawing, hex grid, particles, system checks.

Extracted from ui/intro.py to reduce ~40KB of code duplication across 4 screens.
"""

from __future__ import annotations

import math
import random
import socket
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QPointF, QRect, QTimer
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QPen, QBrush,
    QRadialGradient, QLinearGradient, QPixmap,
)
from PyQt6.QtWidgets import QWidget, QApplication

from core.logger import get_logger

log = get_logger("intro_utils")

ROOT = Path(__file__).resolve().parent.parent

# ── HUD color scheme ──
HUD_CYAN = QColor(0, 212, 255)
HUD_CYAN_DIM = QColor(0, 212, 255, 60)
HUD_RED = QColor(255, 68, 68)
HUD_AMBER = QColor(255, 184, 0)
HUD_GREEN = QColor(0, 200, 100)
HUD_WHITE = QColor(255, 255, 255)
HUD_DARK = QColor(8, 10, 18)
HUD_SURFACE = QColor(12, 16, 28)
HUD_TEXT = QColor(138, 173, 204)
HUD_TEXT_DIM = QColor(100, 130, 160, 120)

# ── System check functions ──

def _check_packages() -> tuple:
    required = ['PyQt6', 'pyttsx3', 'pytesseract', 'cv2', 'mss', 'pyautogui']
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if not missing:
        return True, "all packages ok"
    return True, f"{len(required) - len(missing)}/{len(required)} packages ok"


def _check_ollama_connection() -> tuple:
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:11434", method='GET')
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                return True, "ollama running"
            return False, f"ollama returned {resp.status}"
    except Exception:
        return False, "ollama not running (will use gemini/browser)"


def _check_ollama_models() -> tuple:
    try:
        import json, urllib.request
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            models = [m['name'] for m in data.get('models', [])]
            if any(m in models for m in ['phi3:mini', 'dolphin-mistral:7b']):
                return True, f"{len(models)} model(s) available"
            return True, "models found (may need pulling)"
    except Exception:
        return False, "could not check models"


def _check_tesseract() -> tuple:
    try:
        import pytesseract
        version = pytesseract.get_tesseract_version()
        return True, f"tesseract {version}"
    except Exception:
        return False, "tesseract not found"


def _check_config() -> tuple:
    cfg = ROOT / 'data' / 'config.json'
    if cfg.exists():
        return True, "config ok"
    return False, "config missing (will create defaults)"


def _check_memory() -> tuple:
    mem = ROOT / 'data' / 'memory.json'
    if not mem.exists():
        try:
            mem.parent.mkdir(parents=True, exist_ok=True)
            mem.write_text('{"facts": []}', encoding='utf-8')
            return True, "memory created"
        except Exception:
            return False, "memory error"
    return True, "memory ok"


#: Every asset the intro actually loads, shared with ``ui.intro`` so the two
#: copies of the boot check cannot drift apart again.
#:
#: ``atom_arena.mp3`` used to be listed here. It does not exist and **no code
#: path plays it** — its only reference in the repository was this list — so the
#: boot screen reported "4/5 assets present" on every single launch for nothing.
#: ``tests/test_intro_utils.py`` now asserts that every entry exists on disk, so
#: a listed-but-absent asset cannot come back unnoticed.
ASSET_PATHS: tuple[Path, ...] = (
    ROOT / 'assets' / 'koko-logo-v3.png',
    ROOT / 'assets' / 'koko-logo-v5.png',
    ROOT / 'assets' / 'atom_intro.mp4',
    *sorted((ROOT / 'assets' / 'skins').glob('*.png')),
)


def _check_assets() -> tuple:
    present = [a for a in ASSET_PATHS if a.exists()]
    if len(present) == len(ASSET_PATHS):
        return True, "all assets present"
    elif present:
        return True, f"{len(present)}/{len(ASSET_PATHS)} assets present"
    return True, "assets: none found (optional)"


def _check_internet() -> tuple:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        sock.connect(("8.8.8.8", 53))
        sock.close()
        return True, "internet connected"
    except Exception:
        return False, "no internet (offline mode)"


CHECKS = [
    ("checking python packages...", _check_packages),
    ("checking ollama connection...", _check_ollama_connection),
    ("checking ollama models...", _check_ollama_models),
    ("checking tesseract...", _check_tesseract),
    ("checking config...", _check_config),
    ("checking memory...", _check_memory),
    ("checking assets...", _check_assets),
    ("checking internet...", _check_internet),
]

# ── SKIP_DELAY_MS: How long before the user can skip (1s per build order) ──
SKIP_DELAY_MS = 1000

# ── HUD drawing utilities ──


def draw_hud_frame(painter: QPainter, w: int, h: int,
                   color: QColor = HUD_CYAN,
                   dim_color: QColor = HUD_CYAN_DIM,
                   bracket_len: int = 35, gap: int = 4,
                   elapsed_ms: int = 0) -> None:
    """Draw Iron Man-style HUD corner brackets and outer frame.

    Features animated multi-layer corner dot glow with color cycling
    (cyan → purple → magenta → cyan), bracket outer glow, and
    pulsing frame when elapsed_ms > 0.
    """
    t = elapsed_ms * 0.001  # convert to seconds
    breath = 1.0
    if elapsed_ms > 0:
        breath = 0.6 + 0.4 * math.sin(t * 1.8)

    # ── Outer frame ──
    painter.setPen(QPen(QColor(dim_color.red(), dim_color.green(), dim_color.blue(),
                               int(dim_color.alpha() * breath)), 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(gap + 2, gap + 2, w - 2 * (gap + 2), h - 2 * (gap + 2))

    # ── Corner L-brackets with optional outer glow ──
    corners = [
        (gap, gap, gap + bracket_len, gap, gap, gap + bracket_len),
        (w - gap - bracket_len, gap, w - gap, gap, w - gap, gap + bracket_len),
        (gap, h - gap, gap + bracket_len, h - gap, gap, h - gap - bracket_len),
        (w - gap - bracket_len, h - gap, w - gap, h - gap, w - gap, h - gap - bracket_len),
    ]

    # Outer glow behind brackets (thick, faint)
    if elapsed_ms > 0:
        glow_breath = int(15 + 30 * (0.5 + 0.5 * math.sin(t * 2.2)))
        painter.setPen(QPen(QColor(0, 180, 255, glow_breath), 4, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap))
        for x1, y1, x2, y2, x3, y3 in corners:
            painter.drawLine(x1, y1, x2, y2)
            painter.drawLine(x1, y1, x3, y3)

    # Main bracket lines
    painter.setPen(QPen(color, 2))
    for x1, y1, x2, y2, x3, y3 in corners:
        painter.drawLine(x1, y1, x2, y2)
        painter.drawLine(x1, y1, x3, y3)

    # ── Corner accent dots — multi-layer glow with color cycling ──
    inner_x = gap + bracket_len
    inner_y = gap + bracket_len
    painter.setPen(Qt.PenStyle.NoPen)

    # Color cycles through cyan → purple → magenta → cyan
    if elapsed_ms > 0:
        hue_phase = (t * 0.3) % (2 * math.pi)
        # RGB color cycling without QColor HSV conversion
        r = int(0 + 200 * (0.5 + 0.5 * math.sin(hue_phase)))
        g = int(212 * (0.5 + 0.5 * math.sin(hue_phase + 2.094)))
        b = int(200 + 55 * (0.5 + 0.5 * math.sin(hue_phase + 4.188)))
        # Clamp to valid range
        r = max(0, min(255, r))
        g = max(0, min(255, g))
        b = max(0, min(255, b))
        accent_color = QColor(r, g, b, 255)
    else:
        accent_color = color

    dot_positions = [
        (inner_x, gap), (w - inner_x, gap),
        (inner_x, h - gap), (w - inner_x, h - gap),
        (gap, inner_y), (gap, h - inner_y),
        (w - gap, inner_y), (w - gap, h - inner_y),
    ]

    for idx, (ax, ay) in enumerate(dot_positions):
        if elapsed_ms > 0:
            phase_offset = idx * 0.8  # stagger each dot's animation
            # Outer glow halo (largest, faintest)
            outer_size = 4.0 + 1.5 * math.sin(t * 1.2 + phase_offset)
            glow_alpha = int(30 + 30 * (0.5 + 0.5 * math.sin(t * 1.5 + phase_offset)))
            painter.setBrush(QBrush(QColor(r, g, b, glow_alpha)))
            painter.drawEllipse(QPointF(ax, ay), outer_size, outer_size)

            # Mid glow ring
            mid_size = 3.0 + 1.0 * math.sin(t * 1.8 + phase_offset + 0.5)
            mid_alpha = int(80 + 60 * (0.5 + 0.5 * math.sin(t * 2.0 + phase_offset)))
            painter.setBrush(QBrush(QColor(0, 212, 255, mid_alpha)))
            painter.drawEllipse(QPointF(ax, ay), mid_size, mid_size)

            # Inner bright dot
            inner_size = 2.0 + 0.6 * math.sin(t * 2.5 + phase_offset + 1.0)
            painter.setBrush(QBrush(accent_color))
            painter.drawEllipse(QPointF(ax, ay), inner_size, inner_size)
        else:
            painter.setBrush(QBrush(accent_color))
            painter.drawEllipse(QPointF(ax, ay), 2.0, 2.0)


def draw_hud_corner_text(painter: QPainter, w: int, h: int,
                          left_title: str = "ATOM.SYS v70",
                          left_sub: str = "BOOT SEQUENCE",
                          right_title: Optional[str] = None,
                          right_sub: str = "SYSTEM INIT",
                          font: Optional[QFont] = None) -> None:
    """Draw HUD corner text (left + right titles and subtitles)."""
    if font is None:
        font = QFont("Courier New", 8)
    painter.setFont(font)
    c = QColor(0, 212, 255, 150)
    painter.setPen(c)
    painter.drawText(45, 16, left_title)
    painter.drawText(45, 28, left_sub)
    painter.drawText(w - 200, 16, right_title or time.strftime("%H:%M:%S"))
    painter.drawText(w - 200, 28, right_sub)


def draw_hex_grid_pixmap(w: int, h: int, r: int = 30) -> QPixmap:
    """Draw a hex grid pattern to a QPixmap for reuse."""
    w = max(1, w)
    h = max(1, h)
    pix = QPixmap(w, h)
    pix.fill(Qt.GlobalColor.transparent)
    gp = QPainter(pix)
    gp.setRenderHint(QPainter.RenderHint.Antialiasing)
    gp.setPen(QPen(QColor(14, 22, 40, 40), 1))
    hw = r * 1.5
    hh = r * 0.866
    for row in range(int(h / hh) + 2):
        for col in range(int(w / hw) + 2):
            cx = col * hw + (row % 2) * hw * 0.5
            cy = row * hh
            for side in range(6):
                a1 = math.radians(60 * side - 30)
                a2 = math.radians(60 * (side + 1) - 30)
                x1 = cx + r * math.cos(a1)
                y1 = cy + r * math.sin(a1)
                x2 = cx + r * math.cos(a2)
                y2 = cy + r * math.sin(a2)
                gp.drawLine(int(x1), int(y1), int(x2), int(y2))
    gp.end()
    return pix


class HexGridCache:
    """Cached hex grid pixmap that only regenerates on resize."""

    def __init__(self) -> None:
        self._pixmap: QPixmap = QPixmap()
        self._last_size = (0, 0)

    def get(self, w: int, h: int) -> QPixmap:
        if w <= 0 or h <= 0:
            return self._pixmap
        if self._pixmap.isNull() or self._last_size != (w, h):
            self._pixmap = draw_hex_grid_pixmap(w, h)
            self._last_size = (w, h)
        return self._pixmap

    def invalidate(self) -> None:
        self._pixmap = QPixmap()
        self._last_size = (0, 0)


def draw_background_glow(painter: QPainter, w: int, h: int, elapsed_ms: int = 0) -> None:
    """Draw subtle multi-layered radial glow pulse at screen center.

    Uses dual concentric glows (cyan inner + purple outer) for depth.
    """
    pulse_op = 0.04 + 0.02 * math.sin(elapsed_ms * 0.003)
    # Inner cyan glow
    inner_radius = max(w, h) * 0.35
    glow_grad = QRadialGradient(w // 2, h // 2, inner_radius)
    glow_grad.setColorAt(0.0, QColor(0, 212, 255, int(200 * pulse_op)))
    glow_grad.setColorAt(0.3, QColor(0, 119, 255, int(80 * pulse_op)))
    glow_grad.setColorAt(0.7, QColor(80, 40, 180, int(40 * pulse_op)))
    glow_grad.setColorAt(1.0, QColor(0, 212, 255, 0))
    painter.setBrush(QBrush(glow_grad))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QPointF(w // 2, h // 2), inner_radius, inner_radius)

    # Outer purple halo (subtle)
    outer_radius = max(w, h) * 0.65
    outer_grad = QRadialGradient(w // 2, h // 2, outer_radius)
    outer_grad.setColorAt(0.5, QColor(100, 30, 180, int(25 * pulse_op)))
    outer_grad.setColorAt(0.8, QColor(60, 20, 120, int(15 * pulse_op)))
    outer_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.setBrush(QBrush(outer_grad))
    painter.drawEllipse(QPointF(w // 2, h // 2), outer_radius, outer_radius)


def draw_crt_scanlines(painter: QPainter, w: int, h: int, elapsed_ms: int = 0) -> None:
    """Draw subtle CRT scan line overlay with animated color cycling.

    Colors cycle through cyan → purple → pink → cyan for a more dynamic
    retro-futuristic HUD feel. Two interlaced scan line sets with
    independent speeds create depth.
    """
    t = elapsed_ms * 0.001
    screen_pulse = 0.03 + 0.02 * math.sin(t * 5.0)
    if screen_pulse > 0.04:
        painter.setOpacity(screen_pulse * 0.5)

        # Primary scan lines (4px spacing) — slow color cycle
        hue = (t * 0.3) % (2 * math.pi)
        r = int(80 + 120 * (0.5 + 0.5 * math.sin(hue)))
        g = int(80 + 140 * (0.5 + 0.5 * math.sin(hue + 2.094)))
        b = int(140 + 115 * (0.5 + 0.5 * math.sin(hue + 4.188)))
        r = max(0, min(255, r))
        g = max(0, min(255, g))
        b = max(0, min(255, b))
        for sy in range(0, h, 4):
            line_alpha = 20 + 12 * math.sin(t * 3.0 + sy * 0.01)
            painter.fillRect(0, sy, w, 1, QColor(r, g, b, int(line_alpha)))

        # Secondary scan lines (8px spacing, offset by 2px) — faster cycle
        hue2 = (t * 0.6 + 1.0) % (2 * math.pi)
        r2 = int(60 + 80 * (0.5 + 0.5 * math.sin(hue2 + 0.5)))
        g2 = int(60 + 100 * (0.5 + 0.5 * math.sin(hue2 + 2.6)))
        b2 = int(100 + 100 * (0.5 + 0.5 * math.sin(hue2 + 4.7)))
        r2 = max(0, min(255, r2))
        g2 = max(0, min(255, g2))
        b2 = max(0, min(255, b2))
        for sy in range(2, h, 8):
            line_alpha = 8 + 6 * math.sin(t * 5.0 + sy * 0.02)
            painter.fillRect(0, sy, w, 1, QColor(r2, g2, b2, int(line_alpha)))

        painter.setOpacity(1.0)


def draw_floating_particles(painter: QPainter, w: int, h: int, particles: list) -> None:
    """Draw floating ambient particles."""
    for p in particles:
        if 0 <= p['x'] <= w and 0 <= p['y'] <= h:
            painter.setPen(QPen(QColor(0, 212, 255, p['alpha']), max(1, int(p['size']))))
            painter.drawPoint(int(p['x']), int(p['y']))


def create_particles(count: int = 30, sw: int = 1920, sh: int = 1080) -> list:
    """Create floating particle state list."""
    return [{
        'x': random.randint(0, sw),
        'y': random.randint(0, sh),
        'speed': random.uniform(0.2, 1.0),
        'alpha': random.randint(15, 50),
        'size': random.uniform(0.5, 1.5),
    } for _ in range(count)]


def update_particles(particles: list, w: int, h: int) -> None:
    """Animate floating particles (drift left, wrap around)."""
    for p in particles:
        p['x'] -= p['speed']
        if p['x'] < -10:
            p['x'] = w + 10
            p['y'] = random.randint(0, max(1, h))


def draw_skip_button(painter: QPainter, w: int, h: int, elapsed_ms: int,
                     skip_allowed: bool = True) -> None:
    """Draw pulsing skip button in bottom-right corner."""
    if not skip_allowed:
        return
    btn_x = w - 100
    btn_y = h - 40
    btn_w = 85
    btn_h = 24
    btn_pulse = 0.3 + 0.2 * math.sin(elapsed_ms * 0.003)
    painter.fillRect(btn_x, btn_y, btn_w, btn_h, QColor(0, 212, 255, int(30 * btn_pulse)))
    painter.setPen(QPen(QColor(0, 212, 255, int(120 * btn_pulse)), 1))
    painter.drawRect(btn_x, btn_y, btn_w, btn_h)
    painter.setFont(QFont("Courier New", 9))
    painter.setPen(QColor(0, 212, 255, int(150 * btn_pulse)))
    painter.drawText(btn_x, btn_y, btn_w, btn_h, Qt.AlignmentFlag.AlignCenter, "[SKIP >>]")


def draw_status_bar(painter: QPainter, x: int, y: int,
                    label: str, value: float, bar_width: int = 80,
                    bar_height: int = 6, color: QColor = HUD_CYAN) -> None:
    """Draw a HUD-style status bar with label."""
    font = QFont("Courier New", 8)
    painter.setFont(font)
    painter.setPen(color)
    painter.drawText(x, y - 4, label)
    painter.fillRect(x, y, bar_width, bar_height, QColor(26, 26, 46))
    fill_w = int(bar_width * min(1.0, value))
    if fill_w > 0:
        painter.fillRect(x, y, fill_w, bar_height, color)
    painter.setPen(HUD_TEXT)
    pct = int(value * 100)
    painter.drawText(x + bar_width + 6, y + bar_height - 2, f"{pct}%")


# ── Base intro screen with shared boilerplate ──

class IntroBaseScreen(QWidget):
    """Base class for intro screens with shared HUD lifecycle."""

    def __init__(self, config, title: str = "ATOM") -> None:
        super().__init__()
        self.config = config
        self._title = title
        self._elapsed_ms = 0
        self._skip_allowed = False
        self._skip_done = False
        self._grid_cache = HexGridCache()
        self._particles = create_particles(30)

        self.setStyleSheet("background-color: #080A12;")
        # Window flags + fullscreen deferred to avoid Windows DWM crash
        QTimer.singleShot(0, self._apply_window_flags)

        # Master tick
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start(16)

        # Enable skip after 1s delay (build order requirement)
        QTimer.singleShot(SKIP_DELAY_MS, self._enable_skip)

    def _apply_window_flags(self) -> None:
        """Apply window flags and show fullscreen (deferred to avoid DWM crash)."""
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.geometry()
            self.setGeometry(geo)
        else:
            self.setGeometry(0, 0, 1920, 1080)
        self.setWindowFlags(
            Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.show()

    def _enable_skip(self) -> None:
        self._skip_allowed = True

    def _on_tick(self) -> None:
        self._elapsed_ms += 16
        self._tick_screen()

    def _tick_screen(self) -> None:
        """Override in subclass — called every 16ms."""
        self.update()

    def _skip(self) -> None:
        """Override in subclass — handle skip behavior."""
        self._skip_done = True

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_C and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._skip()
            return
        if self._skip_allowed and not self._skip_done:
            self._skip()
        super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:
        if self._skip_allowed and not self._skip_done:
            self._skip()
        super().mousePressEvent(event)

    def closeEvent(self, event) -> None:
        self._tick.stop()
        event.accept()
