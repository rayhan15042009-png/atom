"""
Atom Cinematic Intro Sequence — Redesigned

4-screen intro with Iron Man HUD throughout:
- Boot: HUD-styled initialization with system checks, corner brackets, scan lines
- Optimus: Two PNG logos with dramatic animations (trace-in, glitch, slam, sweep)
- Welcome: Text reveal with audio and glow burst
- Dance: Ra.One particle assembly + video + HUD overlay
"""
import datetime as _datetime  # cached import for paintEvent hot paths
import math
import os
import sys
import time
import threading
import random
from pathlib import Path
from typing import Optional
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QRect, QPointF, QObject, QUrl
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QPen, QBrush,
    QRadialGradient, QLinearGradient, QRegion, QPixmap, QImage,
)
from PyQt6.QtWidgets import QWidget, QApplication, QDialog

from core.logger import get_logger
from ui.sprite_2d import Sprite2D as MinecraftSprite3D, CHARACTER_PROFILES

log = get_logger('intro')

ROOT = Path(__file__).parent.parent

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

class _DeferredCalls:
    """Single-shot timers that are cancelled with the widget they belong to.

    ``QTimer.singleShot(ms, bound_method)`` installs a timer with **no owner**.
    The intro closes each screen as it moves to the next one, and the screen
    object stays alive (``QDialog.close()`` only hides it), so every pending
    single-shot still fired afterwards:

    * the dance screen's 60 s safety timer kept running for the rest of the
      session, long after the intro was over;
    * a screen's completion signal could be emitted a second time — the boot
      screen's 300 ms delay fired ``boot_complete`` after the sequence had
      already advanced, which re-entered the next transition — and the dance
      screen could complete twice, once from ``EndOfMedia`` and once from a
      still-pending ``_complete``;
    * a callback could land on a screen Qt had already deleted, printing
      ``RuntimeError: wrapped C/C++ object ... has been deleted``.

    Timers created here are parented to ``owner`` (so they die with it) and are
    stopped from its ``closeEvent``. This build of PyQt6 has no
    ``QTimer.singleShot(ms, context, slot)`` overload, which is why this exists
    instead of the context-object form.
    """

    def __init__(self, owner: QWidget) -> None:
        self._owner = owner
        self._timers: list[QTimer] = []

    def start(self, ms: int, callback) -> QTimer:
        """Run *callback* once after *ms*, unless the owner is closed first."""
        timer = QTimer(self._owner)
        timer.setSingleShot(True)
        timer.timeout.connect(callback)
        timer.timeout.connect(lambda: self._discard(timer))
        self._timers.append(timer)
        timer.start(ms)
        return timer

    def _discard(self, timer: QTimer) -> None:
        if timer in self._timers:
            self._timers.remove(timer)

    def pending(self) -> int:
        """How many timers are still waiting (tests + diagnostics)."""
        return len(self._timers)

    def stop_all(self) -> None:
        """Cancel every pending call — called from ``closeEvent``."""
        for timer in list(self._timers):
            try:
                timer.stop()
            except Exception:
                pass
        self._timers.clear()


# ── System checks ──
# The asset list is shared with ui/intro_utils.py (see ASSET_PATHS) so the boot
# screen and the shared module cannot disagree about what has to be on disk.
from ui.intro_utils import ASSET_PATHS


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

def _check_assets() -> tuple:
    present = [a for a in ASSET_PATHS if a.exists()]
    if len(present) == len(ASSET_PATHS):
        return True, "all assets present"
    elif present:
        return True, f"{len(present)}/{len(ASSET_PATHS)} assets present"
    return True, "assets: none found (optional)"

def _check_internet() -> tuple:
    import socket
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

# ── HUD drawing utilities ──

def _draw_hud_frame(painter, w, h, color=HUD_CYAN, dim_color=HUD_CYAN_DIM, bracket_len=30, gap=5):
    """Draw Iron Man-style HUD corner brackets and outer frame."""
    # Outer thin frame
    painter.setPen(QPen(dim_color, 1))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRect(gap + 2, gap + 2, w - 2 * (gap + 2), h - 2 * (gap + 2))

    # Corner L-brackets
    painter.setPen(QPen(color, 2))
    corners = [
        (gap, gap, gap + bracket_len, gap, gap, gap + bracket_len),
        (w - gap - bracket_len, gap, w - gap, gap, w - gap, gap + bracket_len),
        (gap, h - gap, gap + bracket_len, h - gap, gap, h - gap - bracket_len),
        (w - gap - bracket_len, h - gap, w - gap, h - gap, w - gap, h - gap - bracket_len),
    ]
    for x1, y1, x2, y2, x3, y3 in corners:
        painter.drawLine(x1, y1, x2, y2)
        painter.drawLine(x1, y1, x3, y3)

    # Corner accent dots at bracket inner vertices
    bracket_inner_x = gap + bracket_len
    bracket_inner_y = gap + bracket_len
    accent_positions = [
        (bracket_inner_x, gap),
        (w - bracket_inner_x, gap),
        (bracket_inner_x, h - gap),
        (w - bracket_inner_x, h - gap),
        (gap, bracket_inner_y),
        (gap, h - bracket_inner_y),
        (w - gap, bracket_inner_y),
        (w - gap, h - bracket_inner_y),
    ]
    for ax, ay in accent_positions:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(QPointF(ax, ay), 2, 2)


def _draw_intro_sprite(painter, sprite, frame, x, y, pose='idle', emotion='neutral'):
    """Draw the animated character on an intro screen."""
    if sprite is None:
        return
    sprite.draw(
        painter, x, y,
        pose=pose,
        animation_frame=frame % 8,
        emotion=emotion,
        micro_interaction='',
        boredom_tier=0,
    )


def _draw_status_bar(painter, w, h, label, value, x, y, bar_width, bar_height, color=HUD_CYAN):
    """Draw a HUD-style status bar with label."""
    font = QFont("Courier New", 8)
    painter.setFont(font)
    painter.setPen(color)
    painter.drawText(x, y - 4, label)

    # Background track
    painter.fillRect(x, y, bar_width, bar_height, QColor(26, 26, 46))

    # Fill
    fill_w = int(bar_width * min(1.0, value))
    if fill_w > 0:
        painter.fillRect(x, y, fill_w, bar_height, color)

    # Percentage text
    painter.setPen(HUD_TEXT)
    pct = int(value * 100)
    painter.drawText(x + bar_width + 6, y + bar_height - 2, f"{pct}%")


# ════════════════════════════════════════════════════════════════════════════════
# SCREEN 1 — AtomBootScreen: HUD-styled initialization
# ════════════════════════════════════════════════════════════════════════════════

class AtomBootScreen(QDialog):
    """Iron Man HUD + GTA-style boot initialization sequence.

    Features:
    - Hex tech grid background with animated scan lines
    - Iron Man corner brackets with glowing accent dots
    - GTA-style chapter header: 'CHAPTER I: INITIALIZATION SEQUENCE'
    - Large 'ATOM' title with radial glow
    - Smooth animated loading bar with gradient fill and leading edge glow
    - Modular HUD data panels (left: system, right: modules)
    - Real system checks in background thread
    - Per-check status display with OK/FAIL icons
    - Bottom status bars (INIT, SYS)
    - Subtitle reveal after completion: 'SYSTEM READY'
    """

    boot_complete = pyqtSignal()
    # Thread-safe signals for background thread -> main thread communication
    check_progress = pyqtSignal(int, bool, str, str)  # idx, success, msg, label
    checks_done = pyqtSignal()

    def __init__(self, config, intro_sprite=None, sprite_frame_ref=None, sprite_pose_ref=None, sprite_emotion_ref=None):
        super().__init__()
        # Owns every deferred callback, so closing the screen cancels them all.
        self._deferred = _DeferredCalls(self)
        self.config = config
        self._intro_sprite = intro_sprite
        self._sprite_frame_ref = sprite_frame_ref or (lambda: 0)
        self._sprite_pose_ref = sprite_pose_ref or (lambda: 'idle')
        self._sprite_emotion_ref = sprite_emotion_ref or (lambda: 'neutral')

        self.setStyleSheet("background-color: #000000;")
        # Apply window flags immediately (QDialog + Tool flag safe for DWM)
        # Tick timer must be created BEFORE _apply_window_flags starts it
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._tick_update)
        self._apply_window_flags()

        # ── GTA-style chapter header ──
        self._chapter = "CHAPTER I: INITIALIZATION SEQUENCE"
        self._chapter_reveal = 0
        self._chapter_timer = None
        self._chapter_glow = 0.0

        # ── Large 'ATOM' title ──
        self._title_visible = False
        self._title_glow_radius = 0
        self._title_glow_growing = True

        # ── Sound wave / pulse ring ──
        self._pulse_rings = []  # list of (radius, alpha) expanding rings

        # ── Subtitle ──
        self._subtitle_visible = False

        # ── Loading bar edge spark particles ──
        self._edge_particles = []  # sparks from loading bar leading edge

        # ── Loading bar (smooth animated fill) ──
        self._segments_filled = 0
        self._segment_results: dict = {}
        self._status_text = ""
        self._status_label = ""
        self._current_check_idx = -1
        self._bar_anim_progress = 0.0
        self._bar_target_fill = 0.0

        # -- Immediate INITIALIZING overlay with pulsing loading bar --
        self._boot_init_alpha = 1.0
        self._boot_init_text = ">> INITIALIZING SYSTEM <<"
        self._boot_init_sub = "ATOM v7.0.0  \u2014  DESKTOP COMPANION"

        # ── Scan lines ──
        self._scan_y_primary = -2
        self._scan_y_secondary = 0
        self._scan_pass = 0

        # ── HUD data panels ──
        self._hud_left = [
            ("CORE TEMP", "38.2\u00b0C"), ("RAM USAGE", "2.4 GB"),
            ("CPU LOAD", "12%"), ("GPU", "ACTIVE"),
        ]
        self._hud_right = [
            ("NET", "SCANNING"), ("OCR ENGINE", "STANDBY"),
            ("LLM", "LOADING"), ("VISION", "OFFLINE"),
        ]

        # ── Glow pulse ──
        self._glow_pulse_t = math.pi / 2.0  # Start at sin(pi/2)=1.0 for max glow on frame 1  # Start with visible glow (not zero)

        # ── Video mode: true when playing video, skip HUD compositing ──
        self._video_bg_active = False

        # ── Timing ──
        self._elapsed_ms = 0
        self._start_time = time.time()
        self._boot_done = False
        self._skip_allowed = False

        # ── Checks thread ──
        self._check_thread = None
        self._stop_checks = False

        # ── Fonts ──
        self._chapter_font = QFont("Segoe UI", 14, QFont.Weight.Bold)
        self._title_font = QFont("Segoe UI", 64, QFont.Weight.Bold)
        self._status_font = QFont("Courier New", 11)
        self._hud_font = QFont("Courier New", 8)
        self._skip_font = QFont("Courier New", 10)

        # ── Hex grid ──
        self._grid_pixmap = QPixmap()
        self._grid_offset = 0

        # ── Floating particles ──
        self._particles = []
        sw = self.width() or 1920
        sh = self.height() or 1080
        for _ in range(40):
            self._particles.append({
                'x': random.randint(0, sw),
                'y': random.randint(0, sh),
                'speed': random.uniform(0.2, 1.0),
                'alpha': random.randint(15, 50),
                'size': random.uniform(0.5, 1.5),
            })

        # Connect thread-safe signals from background check thread
        self.check_progress.connect(self._on_check_progress)
        self.checks_done.connect(self._on_checks_done)

        log.info("AtomBootScreen shown (Iron Man HUD + GTA style)")

    def _start_chapter_subtitle(self):
        """Animate the GTA-style chapter subtitle fade-in."""
        self._chapter_sub = QTimer(self)
        self._chapter_sub.timeout.connect(self._tick_chapter_sub)
        self._chapter_sub.start(16)

    def _tick_chapter_sub(self):
        self._chapter_subtitle_alpha = min(1.0, self._chapter_subtitle_alpha + 0.04)
        if self._chapter_subtitle_alpha >= 1.0:
            self._chapter_sub.stop()
            self._chapter_sub = None
        self.update()

    def _start_chapter(self):
        """GTA-style: type out 'CHAPTER I: INITIALIZATION SEQUENCE' letter by letter."""
        self._chapter_timer = QTimer(self)
        self._chapter_timer.timeout.connect(self._reveal_chapter)
        self._chapter_timer.start(30)

    def _reveal_chapter(self):
        self._chapter_reveal += 1
        self._chapter_glow = 1.0
        if self._chapter_reveal >= len(self._chapter):
            self._chapter_timer.stop()
            self._chapter_timer = None
            # Show CHAPTER II subtitle with cinematic delay
            self._chapter_subtitle = True
            self._chapter_subtitle_alpha = 0.0
            self._deferred.start(100, self._start_chapter_subtitle)
            # After chapter text settles, show ATOM title
            self._deferred.start(200, self._show_title)
        self.update()

    def _show_title(self):
        self._title_visible = True
        self._title_glow_radius = 10
        self._title_glow_growing = True
        self.update()

    def _start_bar(self):
        """Run system checks in background thread with 6-second timeout."""
        self._check_thread = threading.Thread(target=self._run_checks, daemon=True)
        self._check_thread.start()
        # Force-complete after 6 seconds if checks aren't done
        self._deferred.start(6000, self._on_check_timeout)

    def _run_checks(self):
        for idx, (label, check_fn) in enumerate(CHECKS):
            if self._stop_checks:
                return
            try:
                success, msg = check_fn()
            except Exception as exc:
                success, msg = False, str(exc)[:60]
            # Thread-safe signal emit instead of QTimer.singleShot (not thread-safe!)
            self.check_progress.emit(idx, success, msg, label)
            if self._stop_checks:
                return
            time.sleep(random.uniform(0.3, 0.6))  # Natural varied timing for realistic loading feel
        if not self._stop_checks:
            # Thread-safe signal emit instead of QTimer.singleShot
            self.checks_done.emit()

    def _on_check_timeout(self):
        """Force-complete boot if checks take too long (prevents intro hang)."""
        if self._boot_done:
            return
        log.info("Boot check timeout — forcing completion")
        self._stop_checks = True
        for i in range(len(CHECKS)):
            if i not in self._segment_results:
                self._segment_results[i] = True
        self._segments_filled = len(CHECKS)
        self._complete()

    def _complete(self):
        if self._boot_done:
            return
        self._boot_done = True
        self._subtitle_visible = True
        self.update()
        self._deferred.start(500, self._emit_complete)

    def _emit_complete(self):
        self._tick.stop()
        self._deferred.start(100, lambda: self.boot_complete.emit())

    def _enable_skip(self):
        self._skip_allowed = True

    def _on_check_progress(self, idx, success, msg, label):
        """Handle check progress signal from background thread (runs on main thread)."""
        self._segment_results = {**self._segment_results, idx: success}
        self._status_text = msg
        self._status_label = label
        self._segments_filled = idx + 1
        self._current_check_idx = idx
        self.update()

    def _on_checks_done(self):
        """Handle checks complete signal from background thread (runs on main thread)."""
        # Small delay to let the last status message show
        self._deferred.start(400, self._complete)

    def _apply_window_flags(self) -> None:
        """Set window flags and show full-screen.
        Uses QDialog + FramelessWindowHint + WindowStaysOnTopHint + Tool
        with showFullScreen() to avoid DWM crashes."""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())
        self.show()
        self.raise_()
        self.activateWindow()
        # Start tick timer and post-init tasks
        self._tick.start(16)
        skip_delay = self.config.intro.skip_delay_ms if self.config.intro else 600
        self._deferred.start(100, self._start_chapter)
        self._deferred.start(200, self._start_bar)
        self._deferred.start(skip_delay, self._enable_skip)

    def _skip(self):
        if self._boot_done:
            return
        self._stop_checks = True
        if self._chapter_timer:
            self._chapter_timer.stop()
            self._chapter_timer = None
        for i in range(len(CHECKS)):
            if i not in self._segment_results:
                self._segment_results[i] = True
        self._segments_filled = len(CHECKS)
        self._status_text = ""
        self._boot_done = True
        self._subtitle_visible = True
        self._tick.stop()
        self._deferred.start(300, lambda: self.boot_complete.emit())

    def _ensure_hex_grid(self, force=False):
        """Cache hex grid once, only regenerate on resize."""
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        if force or self._grid_pixmap.isNull() or self._grid_pixmap.width() != w or self._grid_pixmap.height() != h:
            self._cache_hex_grid()

    def resizeEvent(self, event):
        """Invalidate hex grid cache on resize."""
        self._grid_pixmap = QPixmap()
        super().resizeEvent(event)

    def _cache_hex_grid(self):
        """Draw a hex grid pattern to QPixmap once, reuse every frame."""
        w = max(1, self.width())
        h = max(1, self.height())
        self._grid_pixmap = QPixmap(w, h)
        self._grid_pixmap.fill(Qt.GlobalColor.transparent)
        gp = QPainter(self._grid_pixmap)
        gp.setRenderHint(QPainter.RenderHint.Antialiasing)
        gp.setPen(QPen(QColor(14, 22, 40, 40), 1))
        # Hex grid: hexagons with radius 30
        r = 30
        hw = r * 1.5
        hh = r * 0.866
        for row in range(int(h / hh) + 2):
            for col in range(int(w / hw) + 2):
                cx = col * hw + (row % 2) * hw * 0.5
                cy = row * hh
                # Draw hexagon outline (simplified as 6 points)
                for side in range(6):
                    a1 = math.radians(60 * side - 30)
                    a2 = math.radians(60 * (side + 1) - 30)
                    x1 = cx + r * math.cos(a1)
                    y1 = cy + r * math.sin(a1)
                    x2 = cx + r * math.cos(a2)
                    y2 = cy + r * math.sin(a2)
                    gp.drawLine(int(x1), int(y1), int(x2), int(y2))
        gp.end()

    def _tick_update(self):
        self._elapsed_ms += 16

        # ── Decay INITIALIZING overlay (first 500ms) ──
        if self._boot_init_alpha > 0:
            self._boot_init_alpha = max(0.0, 1.0 - self._elapsed_ms / 500.0)

        # ── Animate ATOM title glow pulse ──
        if self._title_visible:
            if self._title_glow_growing:
                self._title_glow_radius += 0.8
                if self._title_glow_radius >= 35:
                    self._title_glow_growing = False
            else:
                self._title_glow_radius -= 0.4
                if self._title_glow_radius <= 15:
                    self._title_glow_growing = True

        # ── Pulse rings ──
        for pr in self._pulse_rings[:]:
            pr['radius'] += 1.5
            pr['alpha'] -= 4
            if pr['alpha'] <= 0:
                self._pulse_rings.remove(pr)
        if self._segments_filled > 0 and random.random() < 0.05:
            w = self.width() or 1920
            self._pulse_rings.append({
                'radius': 5,
                'alpha': 180,
                'x': random.randint(w // 4, w * 3 // 4),
                'y': random.randint(self.height() // 4, self.height() * 3 // 4),
            })

        # ── Update HUD data panels based on progress ──
        if self._segments_filled > 0:
            seg_pct = self._segments_filled / len(CHECKS) if len(CHECKS) > 0 else 0
            updates = {
                0: ("CONNECTED" if seg_pct > 0.2 else "SCANNING"),
                1: ("READY" if seg_pct > 0.3 else "STANDBY"),
                2: ("READY" if seg_pct > 0.4 else "LOADING"),
                3: ("ONLINE" if seg_pct > 0.6 else "OFFLINE"),
            }
            for ri, new_val in updates.items():
                if ri < len(self._hud_right):
                    old_label, _ = self._hud_right[ri]
                    self._hud_right[ri] = (old_label, new_val)

        # ── Scan lines ──
        if self._scan_pass < 2:
            self._scan_y_primary += max(1, self.height() // 60)
            if self._scan_y_primary > self.height():
                self._scan_y_primary = -2
                self._scan_pass += 1
        self._scan_y_secondary -= max(1, self.height() // 90)
        if self._scan_y_secondary < -2:
            self._scan_y_secondary = self.height() + 2

        # ── Grid animation ──
        self._grid_offset = (self._grid_offset + 1) % 100

        # ── Smooth loading bar ──
        target = self._segments_filled / len(CHECKS) if len(CHECKS) > 0 else 0
        diff = target - self._bar_anim_progress
        self._bar_anim_progress += diff * 0.15
        if abs(diff) < 0.001:
            self._bar_anim_progress = target

        # ── Glow pulse ──
        self._glow_pulse_t += 0.03

        # ── Chapter glow decay ──
        self._chapter_glow = max(0.0, self._chapter_glow - 0.02)

        # ── Loading bar edge spark particles ──
        if self._bar_anim_progress > 0 and self._bar_anim_progress < 1.0 and hasattr(self, '_edge_particles'):
            if random.random() < 0.3:
                bar_w = int(self.width() * 0.55) if self.width() > 0 else 800
                fill_w = int(bar_w * self._bar_anim_progress)
                bar_x = (self.width() - bar_w) // 2 if self.width() > 0 else 200
                bar_y = int(self.height() * 0.56) if self.height() > 0 else 400
                self._edge_particles.append({
                    'x': float(bar_x + fill_w), 'y': float(bar_y),
                    'vx': random.uniform(-0.5, 0.5), 'vy': random.uniform(-2.0, -0.5),
                    'life': random.randint(15, 35), 'max_life': 35,
                    'size': random.uniform(1.0, 2.5), 'alpha': random.randint(120, 220),
                })
        for ep in self._edge_particles[:]:
            ep['x'] += ep['vx']
            ep['y'] += ep['vy']
            ep['life'] -= 1
            ep['alpha'] = max(0, ep['alpha'] - 6)
            if ep['life'] <= 0:
                self._edge_particles.remove(ep)

        # ── Animate particles ──
        for p in self._particles:
            p['x'] -= p['speed']
            if p['x'] < -10:
                p['x'] = self.width() + 10 if self.width() > 0 else 1000
                p['y'] = random.randint(0, max(1, self.height()))

        self.update()
    def keyPressEvent(self, event):
        # Ctrl+C to abort the entire intro
        if event.key() == Qt.Key.Key_C and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._skip()
            return
        if self._skip_allowed and not self._boot_done:
            self._skip()
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if self._skip_allowed and not self._boot_done:
            self._skip()
        elif not self._skip_allowed:
            # Deliberately ignored: the configured skip delay exists so the
            # click that launched Atom cannot blow straight past the intro.
            # Logged so "I clicked and nothing happened" is diagnosable.
            log.debug("Skip click ignored — software skip delay still active")
        super().mousePressEvent(event)

    def closeEvent(self, event):
        self._stop_checks = True
        self._tick.stop()
        # Deferred callbacks would otherwise fire into a closed screen — the
        # 300 ms `boot_complete` used to re-enter the sequence's transition.
        self._deferred.stop_all()
        if self._chapter_timer:
            self._chapter_timer.stop()
        if hasattr(self, '_chapter_sub') and self._chapter_sub:
            self._chapter_sub.stop()
        event.accept()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            painter.end()
            return
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        self._ensure_hex_grid()
        if not self._grid_pixmap.isNull():
            painter.drawPixmap(0, 0, self._grid_pixmap)
        for p in self._particles:
            if 0 <= p['x'] <= w and 0 <= p['y'] <= h:
                painter.setPen(QPen(QColor(0, 212, 255, p['alpha']), max(1, int(p['size']))))
                painter.drawPoint(int(p['x']), int(p['y']))
        if self._scan_pass < 2 and 0 <= self._scan_y_primary <= h:
            painter.setOpacity(0.12)
            painter.fillRect(0, self._scan_y_primary, w, 2, QColor(255, 255, 255))
            painter.setOpacity(1.0)
        if 0 <= self._scan_y_secondary <= h:
            painter.setOpacity(0.06)
            painter.fillRect(0, self._scan_y_secondary, w, 1, QColor(0, 212, 255))
            painter.setOpacity(1.0)
        gap = 4
        bracket_len = 35
        painter.setPen(QPen(QColor(0, 212, 255), 2))
        painter.drawLine(gap, gap, gap + bracket_len, gap)
        painter.drawLine(gap, gap, gap, gap + bracket_len)
        painter.drawLine(w - gap - bracket_len, gap, w - gap, gap)
        painter.drawLine(w - gap, gap, w - gap, gap + bracket_len)
        painter.drawLine(gap, h - gap, gap + bracket_len, h - gap)
        painter.drawLine(gap, h - gap, gap, h - gap - bracket_len)
        painter.drawLine(w - gap - bracket_len, h - gap, w - gap, h - gap)
        painter.drawLine(w - gap, h - gap, w - gap, h - gap - bracket_len)
        painter.setPen(QPen(QColor(0, 212, 255, 40), 1))
        painter.drawRect(gap + 2, gap + 2, w - 2 * (gap + 2), h - 2 * (gap + 2))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 212, 255)))
        inner_x = gap + bracket_len
        inner_y = gap + bracket_len
        for ax, ay in [(inner_x, gap), (w - inner_x, gap), (inner_x, h - gap), (w - inner_x, h - gap), (gap, inner_y), (gap, h - inner_y), (w - gap, inner_y), (w - gap, h - inner_y)]:
            painter.drawEllipse(QPointF(ax, ay), 2, 2)
        painter.setFont(self._hud_font)
        painter.setPen(QColor(0, 212, 255, 150))
        painter.drawText(45, 16, 'ATOM.SYS v70')
        painter.drawText(45, 28, 'BOOT SEQUENCE')
        painter.drawText(w - 200, 16, _datetime.datetime.now().strftime('%H:%M:%S'))
        painter.drawText(w - 200, 28, 'SYSTEM INIT')
        if self._chapter_reveal > 0:
            painter.setFont(self._chapter_font)
            visible = self._chapter[:self._chapter_reveal]
            if self._chapter_glow > 0:
                painter.setPen(QColor(0, 212, 255, int(200 + 55 * self._chapter_glow)))
            else:
                painter.setPen(QColor(0, 212, 255, 200))
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(visible)
            painter.drawText((w - tw) // 2, int(h * 0.22), visible)
        if self._title_visible:
            painter.setFont(self._title_font)
            painter.setPen(QColor(0, 212, 255))
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance('ATOM')
            painter.drawText((w - tw) // 2, int(h * 0.42), 'ATOM')
            painter.setFont(QFont('Courier New', 10))
            painter.setPen(QColor(0, 212, 255, 100))
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance('v7.0.0 — DESKTOP COMPANION')
            painter.drawText((w - tw) // 2, int(h * 0.42) + 18, 'v7.0.0 — DESKTOP COMPANION')
        for pr in self._pulse_rings:
            painter.setPen(QPen(QColor(0, 212, 255, pr['alpha']), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(pr['x'], pr['y']), pr['radius'], pr['radius'] * 0.3)
        if self._segments_filled > 0:
            painter.setFont(QFont('Courier New', 10))
            panel_y = int(h * 0.47)
            line_h = 16
            panel_bg = QColor(12, 16, 28, 160)
            painter.fillRect(35, panel_y - 6, 200, line_h * 4 + 8, panel_bg)
            painter.setPen(QPen(QColor(0, 212, 255, 40), 1))
            painter.drawRect(35, panel_y - 6, 200, line_h * 4 + 8)
            painter.fillRect(w - 220, panel_y - 6, 195, line_h * 4 + 8, panel_bg)
            painter.setPen(QPen(QColor(0, 212, 255, 40), 1))
            painter.drawRect(w - 220, panel_y - 6, 195, line_h * 4 + 8)
            for li, (label, value) in enumerate(self._hud_left):
                ly = panel_y + li * line_h
                painter.setPen(QColor(0, 212, 255, 80))
                painter.drawText(45, ly, f'{label}:')
                painter.setPen(QColor(0, 255, 150) if 'OK' in str(value) or 'ACTIVE' in str(value) else QColor(0, 212, 255, 150))
                painter.drawText(170, ly, str(value))
            for ri, (label, value) in enumerate(self._hud_right):
                ry = panel_y + ri * line_h
                painter.setPen(QColor(0, 212, 255, 80))
                painter.drawText(w - 210, ry, f'{label}:')
                painter.setPen(QColor(0, 255, 150) if 'READY' in str(value) or 'ONLINE' in str(value) else QColor(0, 212, 255, 150))
                painter.drawText(w - 110, ry, str(value))
        bar_w = int(w * 0.55)
        bar_x = (w - bar_w) // 2
        bar_y = int(h * 0.56)
        seg_h = 8
        painter.fillRect(bar_x, bar_y, bar_w, seg_h, QColor(8, 10, 18))
        painter.setPen(QPen(QColor(0, 212, 255, 60), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(bar_x - 1, bar_y - 1, bar_w + 2, seg_h + 2, 3, 3)
        bar_fill = self._bar_anim_progress
        if bar_fill > 0.01:
            fill_w = int(bar_w * bar_fill)
            if fill_w > 0:
                painter.fillRect(bar_x, bar_y, fill_w, seg_h, QColor(0, 150, 255))
                if bar_fill < 0.99:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QBrush(QColor(200, 230, 255)))
                    painter.drawEllipse(QPointF(bar_x + fill_w, bar_y + seg_h // 2), 2, 2)
        pct = int((self._segments_filled / len(CHECKS)) * 100) if len(CHECKS) > 0 else 0
        painter.setFont(self._hud_font)
        painter.setPen(QColor(0, 212, 255, 150))
        dots = '.' * (1 + int(self._elapsed_ms * 0.003) % 3)
        if pct == 0:
            painter.drawText(bar_x, bar_y - 10, f'INITIALIZING{dots}')
        else:
            painter.drawText(bar_x, bar_y - 10, f'SYSTEM CHECK  [{pct}%]')
        if self._status_label and self._status_text:
            painter.setFont(self._status_font)
            painter.setPen(QColor(0, 212, 255))
            painter.drawText(QRect(0, bar_y + seg_h + 12, w, 20), Qt.AlignmentFlag.AlignCenter, f'> {self._status_label}')
            painter.setFont(QFont('Courier New', 9))
            painter.setPen(QColor(138, 173, 204, 160))
            painter.drawText(QRect(0, bar_y + seg_h + 30, w, 16), Qt.AlignmentFlag.AlignCenter, self._status_text)
        status_y = h - 28
        painter.setFont(self._hud_font)
        painter.setPen(QColor(100, 130, 160, 120))
        bar_progress = self._segments_filled / len(CHECKS) if len(CHECKS) > 0 else 0
        painter.fillRect(30, status_y, 80, 6, QColor(26, 26, 46))
        fill_w = int(80 * bar_progress)
        if fill_w > 0:
            painter.fillRect(30, status_y, fill_w, 6, QColor(0, 212, 255))
        painter.drawText(115, status_y + 5, 'INIT')
        painter.fillRect(180, status_y, 80, 6, QColor(26, 26, 46))
        painter.fillRect(180, status_y, int(80 * min(1.0, bar_progress * 1.2)), 6, QColor(0, 170, 85))
        painter.drawText(265, status_y + 5, 'SYS')
        if self._subtitle_visible:
            painter.setFont(QFont('Courier New', 12, QFont.Weight.Bold))
            painter.setPen(QColor(0, 212, 255, 200))
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance('>> SYSTEM READY <<')
            painter.drawText((w - tw) // 2, bar_y + seg_h + 60, '>> SYSTEM READY <<')
        if self._skip_allowed and not self._boot_done:
            painter.setFont(self._skip_font)
            painter.setPen(QColor(100, 130, 160, 120))
            painter.drawText(QRect(0, h - 45, w, 16), Qt.AlignmentFlag.AlignCenter, 'Press any key to skip  |  Ctrl+C to abort')
        if hasattr(self, '_intro_sprite') and self._intro_sprite is not None:
            _draw_intro_sprite(painter, self._intro_sprite,
                               self._sprite_frame_ref() if self._sprite_frame_ref else 0,
                               max(20, w - 280), max(20, h - 280),
                               pose=self._sprite_pose_ref() if self._sprite_pose_ref else 'idle',
                               emotion=self._sprite_emotion_ref() if self._sprite_emotion_ref else 'neutral')
        painter.end()

class AtomOptimusScreen(QDialog):
    """Full-screen animated logo reveal with HUD frame.

    3-step cinematic sequence:
    1. Fade-in reveal of v1 (optimus_v1.png) with glow buildup
    2. Cross-fade transition to v2 (optimus.png) with brief flash
    3. Hold v2 with underline sweep + "SYSTEM READY" + glow pulse → wipe out
    """

    splash_complete = pyqtSignal()

    def __init__(self, config, intro_sprite=None, sprite_frame_ref=None, sprite_pose_ref=None, sprite_emotion_ref=None):
        super().__init__()
        # Owns every deferred callback, so closing the screen cancels them all.
        self._deferred = _DeferredCalls(self)
        self.config = config
        self._intro_sprite = intro_sprite
        self._sprite_frame_ref = sprite_frame_ref or (lambda: 0)
        self._sprite_pose_ref = sprite_pose_ref or (lambda: 'idle')
        self._sprite_emotion_ref = sprite_emotion_ref or (lambda: 'neutral')
        self._start_time = time.time()

        self.setStyleSheet("background-color: #000000;")
        # Apply window flags immediately (QDialog + Tool flag safe for DWM)
        # Tick timer must be created BEFORE _apply_window_flags starts it
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._tick_update)
        self._apply_window_flags()

        # ── Asset loading ──
        v1_path = ROOT / 'assets' / 'koko-logo-v3.png'
        v2_path = ROOT / 'assets' / 'koko-logo-v5.png'
        self._pixmap_v1 = QPixmap(str(v1_path)) if v1_path.exists() else QPixmap()
        self._pixmap_v2 = QPixmap(str(v2_path)) if v2_path.exists() else QPixmap()
        self._has_v1 = not self._pixmap_v1.isNull()
        self._has_v2 = not self._pixmap_v2.isNull()
        # Log loaded assets with dimensions
        if self._has_v1:
            log.info("Loaded koko-logo-v3.png: %dx%dpx", self._pixmap_v1.width(), self._pixmap_v1.height())
        else:
            log.error("CRITICAL: koko-logo-v3.png not found at %s", v1_path)
        if self._has_v2:
            log.info("Loaded koko-logo-v5.png: %dx%dpx", self._pixmap_v2.width(), self._pixmap_v2.height())
        else:
            log.error("CRITICAL: koko-logo-v5.png not found at %s", v2_path)

        # ── Hex grid pixmap cache ──
        self._grid_pixmap = QPixmap()
        self._grid_offset = 0

        # ── Floating particles ──
        self._particles = [{
            'x': random.randint(0, 1920), 'y': random.randint(0, 1080),
            'speed': random.uniform(0.2, 1.0), 'alpha': random.randint(15, 50),
            'size': random.uniform(0.5, 1.5),
        } for _ in range(30)]

        # ── Phase state ──
        self._phase = 0  # 0=wait, 1=v1 trace-in+hold, 2=glitch transition, 3=v2+sweep, 4=wipe
        self._phase_elapsed = 0
        self._v1_opacity = 0.0
        self._v2_opacity = 0.0
        self._flash = 0.0
        self._sweep_progress = 0.0
        self._cursor_vis = True
        self._cursor_timer: Optional[QTimer] = None
        self._wipe_y = 0
        self._wipe_done = False

        # P5: Circuit traces
        self._circuit_traces = []  # list of dicts with angle, length, start_x, start_y, progress
        self._circuit_spawn_timer = 0

        # P5: Chromatic aberration glitch
        self._chromatic_aberration = 0.0

        # P5: Cyan sweep particles
        self._sweep_particles = []

        # -- 15042009koko custom animation state --
        self._koko_anim_active = False           # True during 15042009koko animation
        self._koko_anim_elapsed = 0               # ms since koko animation started
        self._koko_text_progress = 0              # 0-10: letter reveal for "15042009koko"
        self._koko_glitch_frames = []             # glitch displacement frames
        self._koko_sparks = []                    # spark particles for text reveal
        self._koko_sweep_done = False             # sweep line phase complete
        self._koko_sweep_ready = False             # koko eye flash done, ready to sweep
        self._koko_sweep_timer = 0                 # ms since koko ready
        self._koko_eye_flash = 0                  # brightness of eye flash on reveal

        # ── Persistent ambient particles across logo screens ──
        self._ambient_particles = []
        for _ in range(20):
            self._ambient_particles.append({
                'x': random.uniform(0, 1),
                'y': random.uniform(0, 1),
                'speed': random.uniform(0.001, 0.004),
                'alpha': random.randint(10, 30),
                'size': random.uniform(0.5, 1.5),
            })

        # ── Expanding ellipse clip for hologram trace-in ──
        self._clip_progress = 0.0  # 0→1 over 1400ms, clip radius grows from 0 to full image size

        # ── Glitch transition state (9-frame RA.ONE-style glitch) ──
        self._glitch_frame = 0        # 0-8, advances every 50ms
        self._glitch_slices = []      # list of dicts for current slice displacement frame
        self._glitch_slice_timer = 0  # ms accumulated toward next glitch frame
        self._white_noise_rects = []  # 200 random rects for frame 8
        self._white_noise_generated = False
        self._slam_v2_visible = False  # v2 shown behind white flash after glitch


        # ── Eye glow state ──
        self._v1_eye_elapsed = 0  # purple eye glow timer for koko-logo-v3
        self._v2_eye_elapsed = 0  # red eye glow timer for koko-logo-v5
        self._v2_eye_cycle = 0    # which color cycle (0,1,2)
        self._v2_glow_colors = [
            (0xFF, 0x22, 0x00),  # Cycle 0: red
            (0xFF, 0x66, 0x00),  # Cycle 1: orange-red
            (0xFF, 0x4F, 0xD8),  # Cycle 2: pink
        ]

        # Image rect
        self._img_rect = QRect()

        # ── Fonts ──
        self._hud_font = QFont("Courier New", 8)
        self._skip_font = QFont("Courier New", 10)

        # ── Skip ──
        self._skip_allowed = False
        self._deferred.start(1000, self._enable_skip)

        log.info("AtomOptimusScreen (v1=%s, v2=%s)", self._has_v1, self._has_v2)

    def _enable_skip(self):
        self._skip_allowed = True

    def _apply_window_flags(self) -> None:
        """Set window flags and show full-screen."""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())
        self.show()
        self.raise_()
        self.activateWindow()
        self._tick.start(16)
        self._deferred.start(300, lambda: setattr(self, "_phase", 1))

    def _skip(self):
        if self._phase >= 4:
            return
        self._tick.stop()
        if self._cursor_timer:
            self._cursor_timer.stop()
        self._phase = 4
        self._wipe_y = 0
        self._deferred.start(300, self._complete)

    def _complete(self):
        if self._wipe_done:
            return
        self._wipe_done = True
        self._tick.stop()
        self._deferred.start(100, lambda: self.splash_complete.emit())

    def _tick_update(self):
        self._phase_elapsed += 16

        # ── Eye glow timers ──
        if self._phase == 1 and self._v1_opacity >= 0.8:
            self._v1_eye_elapsed += 16
        elif self._phase == 3:
            self._v2_eye_elapsed += 16
            # Advance color cycle every ~1200ms (one pulse period)
            if self._v2_eye_elapsed > 0 and self._v2_eye_elapsed % 3600 < 16:
                self._v2_eye_cycle = min(self._v2_eye_cycle + 1, 2)

        if self._phase == 1:
            # Step 1 (0-1400ms): v1 trace-in with expanding ellipse clip
            # Step 2 (1400-2800ms): v1 hold with purple eye glow
            if self._phase_elapsed < 1400:
                # Expanding ellipse clip for hologram materializing effect
                self._clip_progress = min(1.0, self._phase_elapsed / 1400.0)
                self._v1_opacity = min(1.0, self._clip_progress * 1.2)
                # Circuit traces from image center
                self._circuit_spawn_timer += 16
                if self._circuit_spawn_timer >= 80 and self._v1_opacity > 0.1:
                    self._circuit_spawn_timer = 0
                    cx = self._img_rect.x() + self._img_rect.width() // 2
                    cy = self._img_rect.y() + self._img_rect.height() // 2
                    angle = random.uniform(0, math.pi * 2)
                    length = random.uniform(30, 80)
                    self._circuit_traces.append({
                        'sx': cx, 'sy': cy, 'angle': angle,
                        'length': length, 'progress': 0.0, 'alpha': 255,
                    })
            else:
                # v1 hold (1400-2800ms) — full opacity, eye glow active
                self._v1_opacity = 1.0
                self._clip_progress = 1.0
                # Continue circuit traces but dimmer
                self._circuit_spawn_timer += 16
                if self._circuit_spawn_timer >= 120 and self._phase_elapsed < 2200:
                    self._circuit_spawn_timer = 0
                    cx = self._img_rect.x() + self._img_rect.width() // 2
                    cy = self._img_rect.y() + self._img_rect.height() // 2
                    angle = random.uniform(0, math.pi * 2)
                    length = random.uniform(20, 50)
                    self._circuit_traces.append({
                        'sx': cx, 'sy': cy, 'angle': angle,
                        'length': length, 'progress': 0.0, 'alpha': 120,
                    })
            if self._phase_elapsed >= 2200:  # Reduced from 2800ms
                self._phase = 2
                self._phase_elapsed = 0
                self._clip_progress = 1.0
                self._glitch_frame = 0
                self._glitch_slice_timer = 0
                self._slam_v2_visible = False
                self._white_noise_generated = False
                # Generate first set of glitch slices
                self._generate_glitch_slices()

        elif self._phase == 2:
            # RA.ONE-style glitch transition: 9 frames at 50ms each (450ms total)
            # Then white flash slam: v2 appears behind flash over 250ms
            glitch_total = 450  # 9 frames × 50ms
            if self._phase_elapsed < glitch_total:
                # Advance glitch frames every 50ms
                self._glitch_slice_timer += 16
                if self._glitch_slice_timer >= 50:
                    self._glitch_slice_timer = 0
                    self._glitch_frame += 1
                    if self._glitch_frame <= 8:
                        if self._glitch_frame % 2 == 1:
                            # ODD frame: generate new slice displacement
                            self._generate_glitch_slices()
                        elif self._glitch_frame == 8:
                            # Frame 8: generate white noise
                            self._generate_white_noise()
                
                # Keep v1 at full opacity during glitch
                self._v1_opacity = 1.0
                
                # Decay chromatic aberration (used on even frames)
                if self._glitch_frame % 2 == 0 and self._glitch_frame < 8:
                    self._chromatic_aberration = 6.0
                else:
                    self._chromatic_aberration = max(0.0, self._chromatic_aberration - 0.5)
            else:
                # Post-glitch: white flash slam with v2 appearing behind
                if not self._slam_v2_visible:
                    self._slam_v2_visible = True
                    self._v2_opacity = 1.0  # v2 slams in immediately at full
                    self._v1_opacity = 0.0
                    self._flash = 1.0  # white flash starts at full
                # Decay flash over 250ms
                self._flash = max(0.0, self._flash - 0.064)  # ~250ms at 16ms ticks
                if self._flash <= 0:
                    self._phase = 3
                    self._phase_elapsed = 0
                    self._flash = 0.0
                    self._chromatic_aberration = 0.0
                    self._glitch_slices = []
                    self._white_noise_rects = []

        elif self._phase == 3:
            # Check koko animation completion (300ms delay after eye flash)
            if self._koko_sweep_ready and not self._koko_sweep_done:
                self._koko_sweep_timer += 16
                if self._koko_sweep_timer >= 300:
                    self._koko_sweep_done = True
            # Hold v2, do underline sweep
            sweep_duration = 800.0  # Reduced from 1000ms
            self._sweep_progress = min(1.0, self._phase_elapsed / sweep_duration)
            if self._sweep_progress >= 1.0 and self._phase_elapsed < sweep_duration + 50:
                self._spawn_sweep_particles()
            # Wait for koko animation to complete before transitioning
            koko_done = self._koko_sweep_done or self._phase_elapsed >= 3500
            if self._phase_elapsed >= sweep_duration + 1000 and koko_done:
                self._phase = 4
                self._phase_elapsed = 0
                if self._cursor_timer:
                    self._cursor_timer.stop()

        elif self._phase == 4:
            if not self._wipe_done:
                self._wipe_y += max(2, self.height() // 15)
                if self._wipe_y >= self.height():
                    self._complete()

        # Update floating particles
        pw = self.width() or 1920
        ph = self.height() or 1080
        for p in self._particles:
            p['x'] -= p['speed']
            if p['x'] < -10:
                p['x'] = pw + 10
                p['y'] = random.randint(0, max(1, ph))

        # Update circuit traces
        for ct in self._circuit_traces[:]:
            ct['progress'] = min(1.0, ct['progress'] + 0.04)
            ct['alpha'] = max(0, ct['alpha'] - 3)
            if ct['alpha'] <= 0:
                self._circuit_traces.remove(ct)

        # Update sweep particles
        for sp in self._sweep_particles[:]:
            sp['x'] += sp['vx']
            sp['y'] += sp['vy']
            sp['alpha'] = max(0, sp['alpha'] - 2)
            if sp['alpha'] <= 0:
                self._sweep_particles.remove(sp)

        # Update ambient particles
        for ap in self._ambient_particles:
            ap['y'] -= ap['speed']
            if ap['y'] < -0.05:
                ap['y'] = 1.05
                ap['x'] = random.uniform(0, 1)

        self.update()

    def _cache_hex_grid(self, w, h):
        """Draw a hex grid pattern to QPixmap once, reuse every frame."""
        w = max(1, w)
        h = max(1, h)
        self._grid_pixmap = QPixmap(w, h)
        self._grid_pixmap.fill(Qt.GlobalColor.transparent)
        gp = QPainter(self._grid_pixmap)
        gp.setRenderHint(QPainter.RenderHint.Antialiasing)
        gp.setPen(QPen(QColor(14, 22, 40, 40), 1))
        r = 30
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

    def _ensure_hex_grid(self, force=False):
        """Cache hex grid once, only regenerate on resize."""
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        if force or self._grid_pixmap.isNull() or self._grid_pixmap.width() != w or self._grid_pixmap.height() != h:
            self._cache_hex_grid(w, h)

    def resizeEvent(self, event):
        """Invalidate hex grid cache on resize."""
        self._grid_pixmap = QPixmap()
        super().resizeEvent(event)

    def _generate_glitch_slices(self):
        """Generate random horizontal slice displacement for RA.ONE glitch."""
        if not self._img_rect:
            return
        num_slices = random.randint(4, 7)
        ir = self._img_rect
        slice_h = ir.height() // num_slices
        self._glitch_slices = []
        for si in range(num_slices):
            sy = ir.y() + si * slice_h
            sh = slice_h if si < num_slices - 1 else ir.y() + ir.height() - sy
            x_off = random.randint(-25, 25)
            y_off = random.choice([0, 0, 0, -3, 3, -2, 2])
            opacity = random.uniform(0.6, 1.0)
            self._glitch_slices.append({
                'sy': sy, 'sh': sh,
                'x_off': x_off, 'y_off': y_off,
                'opacity': opacity,
            })

    def _generate_white_noise(self):
        """Generate 200 random 2x2 white rects for glitch frame 8."""
        w = self.width() or 1920
        h = self.height() or 1080
        self._white_noise_rects = []
        for _ in range(200):
            rx = random.randint(0, w - 2)
            ry = random.randint(0, h - 2)
            self._white_noise_rects.append((rx, ry))
        self._white_noise_generated = True

    def _spawn_sweep_particles(self):
        """Spawn 10 small cyan particles from the sweep end point."""
        if not self._img_rect:
            return
        sweep_x = self._img_rect.x() + int(self._img_rect.width() * 0.9) + 6
        sweep_y = self._img_rect.y() + int(self._img_rect.height() * 0.13)
        for _ in range(10):
            angle = random.uniform(-math.pi / 2, math.pi / 2)
            speed = random.uniform(1.0, 3.0)
            self._sweep_particles.append({
                'x': float(sweep_x), 'y': float(sweep_y),
                'vx': math.cos(angle) * speed,
                'vy': math.sin(angle) * speed,
                'alpha': 200,
            })

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            painter.end()
            return

        # ── Dark background ──
        painter.fillRect(self.rect(), QColor(0, 0, 0))

        # ── Hex grid (cached, only regenerated on resize) ──
        self._ensure_hex_grid()
        if not self._grid_pixmap.isNull():
            painter.drawPixmap(0, 0, self._grid_pixmap)

        # ── Background glow pulse ──
        pulse_op = 0.03 + 0.02 * math.sin((self._phase_elapsed if hasattr(self, '_phase_elapsed') else 0) * 0.002)
        glow_grad = QRadialGradient(w // 2, h // 2, max(w, h) * 0.5)
        glow_grad.setColorAt(0.0, QColor(0, 212, 255, int(255 * pulse_op)))
        glow_grad.setColorAt(0.4, QColor(0, 119, 255, int(80 * pulse_op)))
        glow_grad.setColorAt(1.0, QColor(0, 212, 255, 0))
        painter.setBrush(QBrush(glow_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(w // 2, h // 2), max(w, h) * 0.5, max(w, h) * 0.5)

        # ── Floating particles ──
        for p in self._particles:
            if 0 <= p['x'] <= w and 0 <= p['y'] <= h:
                painter.setPen(QPen(QColor(0, 212, 255, p['alpha']), max(1, int(p['size']))))
                painter.drawPoint(int(p['x']), int(p['y']))
        # Also draw ambient particles
        for ap in self._ambient_particles:
            px = int(ap['x'] * w)
            py = int(ap['y'] * h)
            painter.setPen(QPen(QColor(0, 212, 255, ap['alpha']), ap['size']))
            painter.drawPoint(px, py)

        # ── Subtle CRT scan line overlay ──
        screen_pulse = 0.03 + 0.02 * math.sin((self._phase_elapsed if hasattr(self, '_phase_elapsed') else 0) * 0.005)
        if screen_pulse > 0.04:
            painter.setOpacity(screen_pulse * 0.5)
            for sy in range(0, h, 4):
                painter.fillRect(0, sy, w, 1, QColor(0, 212, 255, 30))
            painter.setOpacity(1.0)

        # ── Iron Man HUD Corner Brackets ──
        gap = 4
        bracket_len = 35
        painter.setPen(QPen(QColor(0, 212, 255), 2))
        # Top-left
        painter.drawLine(gap, gap, gap + bracket_len, gap)
        painter.drawLine(gap, gap, gap, gap + bracket_len)
        # Top-right
        painter.drawLine(w - gap - bracket_len, gap, w - gap, gap)
        painter.drawLine(w - gap, gap, w - gap, gap + bracket_len)
        # Bottom-left
        painter.drawLine(gap, h - gap, gap + bracket_len, h - gap)
        painter.drawLine(gap, h - gap, gap, h - gap - bracket_len)
        # Bottom-right
        painter.drawLine(w - gap - bracket_len, h - gap, w - gap, h - gap)
        painter.drawLine(w - gap, h - gap, w - gap, h - gap - bracket_len)

        # Outer frame (dim)
        painter.setPen(QPen(QColor(0, 212, 255, 40), 1))
        painter.drawRect(gap + 2, gap + 2, w - 2 * (gap + 2), h - 2 * (gap + 2))

        # Accent dots at bracket inner vertices
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 212, 255)))
        inner_x = gap + bracket_len
        inner_y = gap + bracket_len
        for ax, ay in [
            (inner_x, gap), (w - inner_x, gap),
            (inner_x, h - gap), (w - inner_x, h - gap),
            (gap, inner_y), (gap, h - inner_y),
            (w - gap, inner_y), (w - gap, h - inner_y),
        ]:
            painter.drawEllipse(QPointF(ax, ay), 2, 2)

        # ── HUD corner texts ──
        painter.setFont(self._hud_font)
        painter.setPen(HUD_TEXT)
        painter.drawText(40, 18, "ATOM.SYS v70")
        painter.drawText(40, 30, "LOGO SEQUENCE")
        phase_names = {1: "REVEAL", 2: "TRANSITION", 3: "READY", 4: "DONE"}
        painter.drawText(w - 180, 18, f"PHASE: {phase_names.get(self._phase, 'WAIT')}")

        # ── Compute image rect ──
        img_h = int(h * 0.95)
        v2_pix = self._pixmap_v2 if self._has_v2 else self._pixmap_v1
        if not v2_pix.isNull():
            aspect = v2_pix.width() / v2_pix.height()
            img_w = int(img_h * aspect)
            if img_w > w:
                img_w = w
                img_h = int(img_w / aspect)
            img_x = (w - img_w) // 2
            img_y = int(h * 0.48) - img_h // 2
            self._img_rect = QRect(img_x, img_y, img_w, img_h)
        else:
            self._img_rect = QRect(w // 4, int(h * 0.2), w // 2, int(h * 0.6))

        # ── Draw v1 with fade and expanding ellipse clip ──
        if self._has_v1 and self._v1_opacity > 0:
            if self._phase == 1 and self._clip_progress < 1.0:
                # Expanding ellipse clip (hologram materializing effect)
                ir = self._img_rect
                cx = ir.x() + ir.width() // 2
                cy = ir.y() + ir.height() // 2
                cw = int(ir.width() * self._clip_progress)
                ch = int(ir.height() * self._clip_progress)
                if cw > 2 and ch > 2:
                    painter.save()
                    painter.setClipRegion(QRegion(cx - cw // 2, cy - ch // 2, cw, ch, QRegion.RegionType.Ellipse))
                    painter.setOpacity(self._v1_opacity)
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    painter.drawPixmap(self._img_rect, self._pixmap_v1)
                    painter.setClipping(False)
                    painter.restore()
                    # Clip boundary glow ring
                    if 0.1 < self._clip_progress < 1.0:
                        glow_r = max(cw, ch) // 2
                        boundary_grad = QRadialGradient(cx, cy, glow_r + 6)
                        glow_a = int(120 * (1.0 - self._clip_progress))
                        boundary_grad.setColorAt(0.85, QColor(0, 212, 255, 0))
                        boundary_grad.setColorAt(0.95, QColor(0, 212, 255, glow_a))
                        boundary_grad.setColorAt(1.0, QColor(0, 212, 255, 0))
                        painter.setBrush(QBrush(boundary_grad))
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.drawEllipse(QPointF(cx, cy), glow_r + 6, glow_r + 6)
                else:
                    # Too small to clip, draw normally
                    painter.setOpacity(self._v1_opacity)
                    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                    painter.drawPixmap(self._img_rect, self._pixmap_v1)
                    painter.setOpacity(1.0)
            else:
                painter.setOpacity(self._v1_opacity)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                painter.drawPixmap(self._img_rect, self._pixmap_v1)
                painter.setOpacity(1.0)

        # ── Purple eye glow on koko-logo-v3 (v1, phase 1 hold) ──
        if self._phase == 1 and self._v1_opacity >= 0.8 and self._img_rect:
            ir = self._img_rect
            v1_lx = ir.x() + int(ir.width() * 0.42)
            v1_rx = ir.x() + int(ir.width() * 0.58)
            v1_ey = ir.y() + int(ir.height() * 0.44)
            # Inner glow: sin wave 0.4→1.0, period 1100ms
            inner_op = 0.4 + 0.6 * abs(math.sin(self._v1_eye_elapsed * 0.0057))
            for ex in (v1_lx, v1_rx):
                ig = QRadialGradient(ex, v1_ey, 28)
                ig.setColorAt(0.0, QColor(0x9B, 0x59, 0xFF, int(180 * inner_op)))
                ig.setColorAt(0.5, QColor(0x9B, 0x59, 0xFF, int(60 * inner_op)))
                ig.setColorAt(1.0, QColor(0x9B, 0x59, 0xFF, 0))
                painter.setBrush(QBrush(ig))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QPointF(ex, v1_ey), 28, 28)
            # Outer glow: period 1400ms, offset π/2
            outer_op = 0.18 * (0.4 + 0.6 * abs(math.sin(self._v1_eye_elapsed * 0.0045 + math.pi / 2)))
            for ex in (v1_lx, v1_rx):
                og = QRadialGradient(ex, v1_ey, 58)
                og.setColorAt(0.0, QColor(0x9B, 0x59, 0xFF, int(255 * outer_op)))
                og.setColorAt(1.0, QColor(0x9B, 0x59, 0xFF, 0))
                painter.setBrush(QBrush(og))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QPointF(ex, v1_ey), 58, 58)

        # ── Draw v2 with fade ──
        if self._has_v2 and self._v2_opacity > 0:
            painter.setOpacity(self._v2_opacity)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(self._img_rect, self._pixmap_v2)
            painter.setOpacity(1.0)

        # ── Red eye glow on koko-logo-v5 (v2, phase 3 hold) ──
        if self._phase == 3 and self._img_rect:
            ir = self._img_rect
            v2_lx = ir.x() + int(ir.width() * 0.43)
            v2_rx = ir.x() + int(ir.width() * 0.57)
            v2_ey = ir.y() + int(ir.height() * 0.48)
            # Get current cycle color
            cycle_idx = min(self._v2_eye_cycle, len(self._v2_glow_colors) - 1)
            glow_col = self._v2_glow_colors[cycle_idx]
            # Inner glow: sin wave 0.35→1.0, period 1200ms
            inner_op = 0.35 + 0.65 * abs(math.sin(self._v2_eye_elapsed * 0.0052))
            for ex in (v2_lx, v2_rx):
                ig = QRadialGradient(ex, v2_ey, 32)
                ig.setColorAt(0.0, QColor(*glow_col, int(200 * inner_op)))
                ig.setColorAt(0.5, QColor(*glow_col, int(70 * inner_op)))
                ig.setColorAt(1.0, QColor(*glow_col, 0))
                painter.setBrush(QBrush(ig))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QPointF(ex, v2_ey), 32, 32)
            # Outer glow: period 1500ms
            outer_op = 0.2 * (0.35 + 0.65 * abs(math.sin(self._v2_eye_elapsed * 0.0042)))
            for ex in (v2_lx, v2_rx):
                og = QRadialGradient(ex, v2_ey, 70)
                og.setColorAt(0.0, QColor(*glow_col, int(255 * outer_op)))
                og.setColorAt(1.0, QColor(*glow_col, 0))
                painter.setBrush(QBrush(og))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QPointF(ex, v2_ey), 70, 70)
            # Screen tint: 2.5% opacity at glow peak
            if inner_op > 0.88:
                tint = QColor(*glow_col, 6)
                painter.fillRect(self.rect(), tint)

        # ── RA.ONE-style glitch transition (phase 2) ──
        if self._phase == 2:
            if self._glitch_frame < 8:
                if self._glitch_frame % 2 == 1:
                    # ODD frames: slice displacement
                    for gs in self._glitch_slices:
                        src_rect = QRect(0, gs['sy'] - self._img_rect.y(), self._img_rect.width(), gs['sh'])
                        dst_rect = QRect(
                            self._img_rect.x() + gs['x_off'],
                            gs['sy'] + gs['y_off'],
                            self._img_rect.width(),
                            gs['sh']
                        )
                        painter.save()
                        painter.setClipRect(dst_rect)
                        painter.setOpacity(gs['opacity'])
                        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                        painter.drawPixmap(dst_rect, self._pixmap_v1, src_rect)
                        painter.setOpacity(1.0)
                        painter.setClipping(False)
                        painter.restore()
                else:
                    # EVEN frames: RGB channel aberration
                    if self._chromatic_aberration > 1.0:
                        offset = int(self._chromatic_aberration)
                        painter.save()
                        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                        painter.setOpacity(0.35)
                        painter.drawPixmap(self._img_rect.adjusted(offset, 0, offset, 0), self._pixmap_v1)
                        painter.restore()
                        painter.save()
                        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
                        painter.setOpacity(0.35)
                        painter.drawPixmap(self._img_rect.adjusted(-offset, 0, -offset, 0), self._pixmap_v1)
                        painter.restore()
                # Frame 8 (even, last): white noise
                if self._glitch_frame == 8 and self._white_noise_generated:
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QBrush(QColor(255, 255, 255, 20)))
                    for rx, ry in self._white_noise_rects:
                        painter.drawRect(rx, ry, 2, 2)
            # During white flash slam: v2 is drawn normally (behind white flash)
            # v2 already drawn by the main draw call above

        # ── White flash at transition ──
        if self._flash > 0:
            painter.setOpacity(self._flash)
            painter.fillRect(self.rect(), QColor(255, 255, 255))
            painter.setOpacity(1.0)

        # ── Circuit traces (phase 1) ──
        for ct in self._circuit_traces:
            ct_len = ct['length'] * ct['progress']
            ex = ct['sx'] + math.cos(ct['angle']) * ct_len
            ey = ct['sy'] + math.sin(ct['angle']) * ct_len
            painter.setPen(QPen(QColor(0, 212, 255, ct['alpha']), 1))
            painter.drawLine(int(ct['sx']), int(ct['sy']), int(ex), int(ey))

        # ── Cyan sweep particles (phase 3) ──
        for sp in self._sweep_particles:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(0, 212, 255, sp['alpha'])))
            painter.drawEllipse(QPointF(sp['x'], sp['y']), 1.5, 1.5)

        # ── Phase 3: 15042009koko animation, then underline sweep + cursor + "SYSTEM READY" ──
        if self._phase == 3 and self._has_v2:
            # Run koko text reveal animation first
            self._draw_koko_animation(painter, w, h)
            # After koko animation completes, run underline sweep
            if self._koko_sweep_done:
                self._draw_sweep(painter, w, h)

        # ── Phase 4: wipe ──
        if self._phase == 4 and self._wipe_y < h:
            painter.fillRect(0, 0, w, self._wipe_y, HUD_DARK)

        # ── Bottom status ──
        painter.setFont(self._hud_font)
        painter.setPen(HUD_TEXT_DIM)
        progress = min(1.0, self._phase / 4.0)
        _draw_status_bar(painter, w, h, "LOAD", progress, 30, h - 28, 120, 6, HUD_CYAN)

        # ── Skip button (bottom-right corner) ──
        if self._skip_allowed and self._phase < 4:
            painter.setFont(self._skip_font)
            painter.setPen(HUD_TEXT_DIM)
            painter.drawText(QRect(0, h - 45, w, 16), Qt.AlignmentFlag.AlignCenter,
                             "Press any key to skip  |  Ctrl+C to abort")
            # Visible skip button in bottom-right
            btn_x = w - 100
            btn_y = h - 40
            btn_w = 85
            btn_h = 24
            btn_pulse = 0.3 + 0.2 * math.sin(self._phase_elapsed * 0.003)
            painter.fillRect(btn_x, btn_y, btn_w, btn_h, QColor(0, 212, 255, int(30 * btn_pulse)))
            painter.setPen(QPen(QColor(0, 212, 255, int(120 * btn_pulse)), 1))
            painter.drawRect(btn_x, btn_y, btn_w, btn_h)
            painter.setFont(QFont("Courier New", 9))
            painter.setPen(QColor(0, 212, 255, int(150 * btn_pulse)))
            painter.drawText(btn_x, btn_y, btn_w, btn_h, Qt.AlignmentFlag.AlignCenter, "[SKIP >>]")

        # Persistent animated character
        if hasattr(self, '_intro_sprite') and self._intro_sprite is not None:
            _draw_intro_sprite(painter, self._intro_sprite,
                               self._sprite_frame_ref() if self._sprite_frame_ref else 0,
                               w - 200, h - 240,
                               pose=self._sprite_pose_ref() if self._sprite_pose_ref else 'idle',
                               emotion=self._sprite_emotion_ref() if self._sprite_emotion_ref else 'neutral')

        painter.end()
    
    def _draw_koko_animation(self, painter, w, h):
        """15042009koko custom animation: letter reveal + glitch sparks + eye pulse.

        Plays during Phase 3 of the Optimus screen, before the underline sweep.
        Reveals "15042009koko" with a stylized glitch/spark effect per letter,
        culminating in a bright eye flash on the character.
        """
        if not self._img_rect or self._img_rect.isNull():
            return
        ir = self._img_rect
        # Start the animation when phase 3 begins
        if not self._koko_anim_active:
            self._koko_anim_active = True
            self._koko_anim_elapsed = 0
            self._koko_text_progress = 0
            self._koko_glitch_frames = []
            self._koko_sparks = []

        self._koko_anim_elapsed += 16

        # Letter-by-letter reveal: 13 letters, ~120ms each
        letters = len("15042009koko")
        letter_ms = 120
        self._koko_text_progress = min(letters, self._koko_anim_elapsed // letter_ms)

        # Generate sparks on each new letter reveal
        new_letters = self._koko_text_progress
        expected = self._koko_anim_elapsed // letter_ms
        if new_letters != expected:
            # Spawn glow sparks at random positions around the logo
            for _ in range(8):
                self._koko_sparks.append({
                    'x': ir.x() + random.randint(0, ir.width()),
                    'y': ir.y() + random.randint(0, int(ir.height() * 0.08)),
                    'vx': random.uniform(-1, 1),
                    'vy': random.uniform(0.3, 2),
                    'alpha': 200,
                    'size': random.uniform(1, 3),
                })

        # Draw letter reveal at top of image
        txt = "15042009koko"[:self._koko_text_progress]
        if txt:
            painter.setFont(QFont("Consolas", int(ir.height() * 0.035), QFont.Weight.Bold))
            fm = painter.fontMetrics()
            tw = fm.horizontalAdvance(txt)
            tx = ir.x() + (ir.width() - tw) // 2
            ty = ir.y() + int(ir.height() * 0.10)

            # Letter colors cycle through cyan/purple
            for li, ch in enumerate(txt):
                hue_shift = (li * 15) % 360
                r = int(255 * (0.5 + 0.5 * math.sin(hue_shift * 0.017)))
                g = int(255 * (0.5 + 0.5 * math.sin(hue_shift * 0.017 + 2.094)))
                b = int(255 * (0.5 + 0.5 * math.sin(hue_shift * 0.017 + 4.188)))
                lx = tx + fm.horizontalAdvance(txt[:li])
                painter.setPen(QColor(r, g, b, 200 + 55 * li // max(1, letters - 1)))
                painter.drawText(lx, ty, ch)

        # Draw sparks
        for sp in self._koko_sparks[:]:
            sp['x'] += sp['vx']
            sp['y'] += sp['vy']
            sp['alpha'] -= 4
            if sp['alpha'] <= 0:
                self._koko_sparks.remove(sp)
                continue
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(0, 212, 255, sp['alpha'])))
            painter.drawEllipse(QPointF(sp['x'], sp['y']), sp['size'], sp['size'])

        # After all letters revealed, do final eye flash
        if self._koko_text_progress >= letters and not self._koko_sweep_done:
            self._koko_eye_flash = min(1.0, self._koko_eye_flash + 0.02)
            if self._koko_eye_flash >= 1.0:
                # Eye flash burst
                cx = ir.x() + ir.width() // 2
                cy = ir.y() + ir.height() // 2
                flash_grad = QRadialGradient(cx, cy, 100)
                flash_grad.setColorAt(0.0, QColor(255, 255, 255, 80))
                flash_grad.setColorAt(0.3, QColor(200, 230, 255, 30))
                flash_grad.setColorAt(1.0, QColor(0, 212, 255, 0))
                painter.setBrush(QBrush(flash_grad))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QPointF(cx, cy), 100, 60)
                # Mark sweep ready - _tick_update will set _koko_sweep_done after delay
                self._koko_sweep_ready = True

    def _draw_sweep(self, painter, w, h):
        img_r = self._img_rect

        sweep_y = img_r.y() + int(img_r.height() * 0.13)  # under "15042009koko" text at top
        sweep_start = img_r.x() + int(img_r.width() * 0.09)
        sweep_end = img_r.x() + int(img_r.width() * 0.9)
        sweep_total = sweep_end - sweep_start
        progress = self._sweep_progress
        current_x = sweep_start + int(sweep_total * progress)

        if current_x > sweep_start:
            painter.fillRect(sweep_start, sweep_y, current_x - sweep_start, 2, QColor(0, 212, 255))

        if progress < 1.0:
            # Leading edge glow
            glow_grad = QLinearGradient(current_x - 25, sweep_y, current_x + 5, sweep_y)
            glow_grad.setColorAt(0.0, QColor(0, 212, 255, 0))
            glow_grad.setColorAt(0.7, QColor(0, 212, 255, 180))
            glow_grad.setColorAt(1.0, QColor(0, 212, 255, 255))
            painter.fillRect(current_x - 25, sweep_y - 2, 30, 6, QBrush(glow_grad))
        else:
            # Cursor blink
            if self._cursor_timer is None:
                self._cursor_timer = QTimer(self)
                self._cursor_timer.timeout.connect(lambda: (
                    setattr(self, '_cursor_vis', not self._cursor_vis),
                    self.update()
                ))
                self._cursor_timer.start(530)
            if self._cursor_vis:
                painter.setFont(QFont("Courier New", 16))
                painter.setPen(QColor(0, 212, 255))
                painter.drawText(sweep_end + 6, sweep_y - 2, "|")
                # "SYSTEM READY" text
                painter.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
                painter.setPen(QColor(0, 212, 255, 150))
                fm = painter.fontMetrics()
                ready_text = "SYSTEM READY"
                rw = fm.horizontalAdvance(ready_text)
                painter.drawText(sweep_end - rw - 10, sweep_y - 12, ready_text)

    def keyPressEvent(self, event):
        # Ctrl+C to abort the entire intro
        if event.key() == Qt.Key.Key_C and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._skip()
            return
        if self._skip_allowed and self._phase < 4:
            self._skip()
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if self._skip_allowed and self._phase < 4:
            self._skip()
        elif not self._skip_allowed:
            log.debug("Skip click ignored — skip delay still active")
        super().mousePressEvent(event)

    def closeEvent(self, event):
        self._tick.stop()
        self._deferred.stop_all()
        if self._cursor_timer:
            self._cursor_timer.stop()
        event.accept()


# ════════════════════════════════════════════════════════════════════════════════
# SCREEN 3 — AtomWelcomeScreen: "LET'S WELCOME ATOM" + Audio + Glow Burst
# ════════════════════════════════════════════════════════════════════════════════

class AtomWelcomeScreen(QDialog):
    """Welcome screen showing "LET'S WELCOME ATOM" with HUD frame.

    Features:
    - Video background (atom_intro.mp4) or solid black fallback
    - Letter-by-letter reveal for "LET'S WELCOME" and "ATOM"
    - Glow burst effect on final reveal
    - Arena audio playback
    - Iron Man HUD overlay frame
    """

    welcome_complete = pyqtSignal()

    def __init__(self, config, intro_sprite=None, sprite_frame_ref=None, sprite_pose_ref=None, sprite_emotion_ref=None):
        super().__init__()
        # Owns every deferred callback, so closing the screen cancels them all.
        self._deferred = _DeferredCalls(self)
        self.config = config
        self._intro_sprite = intro_sprite
        self._sprite_frame_ref = sprite_frame_ref or (lambda: 0)
        self._sprite_pose_ref = sprite_pose_ref or (lambda: 'idle')
        self._sprite_emotion_ref = sprite_emotion_ref or (lambda: 'neutral')

        self.setStyleSheet("background-color: #000000;")
        # Window flags + fullscreen deferred to avoid DWM crash
        self._deferred.start(0, self._apply_window_flags)

        # ── Hex grid ──
        self._grid_pixmap = QPixmap()
        self._grid_offset = 0

        # ── Floating particles ──
        self._particles = [{
            'x': random.randint(0, 1920), 'y': random.randint(0, 1080),
            'speed': random.uniform(0.2, 0.8), 'alpha': random.randint(15, 50),
            'size': random.uniform(0.5, 1.5),
        } for _ in range(30)]

        # ── Energy vortex particles (video fallback) ──
        self._energy_particles = []
        for _ in range(100):
            angle = random.uniform(0, math.pi * 2)
            radius = random.uniform(15, 300)
            self._energy_particles.append({
                'angle': angle, 'radius': radius,
                'speed': random.uniform(0.002, 0.015) * (1 if random.random() < 0.7 else -1),
                'orbital': random.uniform(0.005, 0.02),
                'size': random.uniform(1.0, 4.0), 'alpha': random.randint(20, 120),
                'phase': random.uniform(0, math.pi * 2),
                'base_alpha': random.randint(20, 120),
            })

        # ── Video background (QMediaPlayer) ──
        self._video_player = None
        self._video_widget = None
        self._use_video = False
        self._has_intro_video = False
        # ``_has_intro_video`` is cleared by _cleanup_intro_video() (skip, error,
        # teardown); this flag records that the clip *was* played, so the sequence
        # never mistakes a skipped video for "no video" and replays it on the
        # dance screen.
        self._played_intro_video = False
        self._try_init_intro_video()

        # ── Line reveal ──
        self._line1 = "LET'S WELCOME"
        self._line1_rev = 0
        self._line1_done = False
        self._line2 = "ATOM"
        self._line2_rev = 0
        self._line2_done = False
        # P6: Per-letter animation states
        self._line2_scales = {}  # idx → current scale (for slam animation)
        self._line2_slam_started = False
        self._line1_vibrate_timer = None
        self._line1_vibrate_frame = 0

        # ── Triple glow burst ──
        self._burst_elapsed = -1
        self._burst_steady = False
        self._burst_phases = []  # list of (radius, max_radius, duration, delay, max_opacity)

        # ── Skip ──
        self._skip_allowed = False
        self._deferred.start(1000, self._enable_skip)

        # ── State flags ──
        self._welcome_done = False

        # ── Fonts ──
        self._font_line1 = QFont("Courier New", 28)
        self._font_line2 = QFont("Courier New", 72, QFont.Weight.Bold)
        self._hud_font = QFont("Courier New", 8)

        # ── Start sequence ──
        self._deferred.start(50, self._start_line1)

        # ── Tick timer ──
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._tick_update)
        self._tick.start(16)

        # ── Safety timeout: 180s after welcome screen appears (lets long videos finish) ──
        self._welcome_safety_timer = QTimer(self)
        self._welcome_safety_timer.setSingleShot(True)
        self._welcome_safety_timer.timeout.connect(self._emit_complete)
        self._welcome_safety_timer.start(180000)

        log.info("AtomWelcomeScreen shown (video_bg=%s)", self._has_intro_video)

    def _apply_window_flags(self) -> None:
        """Set window flags and show full-screen."""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())
        self.show()
        self.raise_()
        self.activateWindow()

    def _try_init_intro_video(self):
        """Initialize intro video using QMediaPlayer (no OpenCV dependency).
        Falls back gracefully to solid black background."""
        video_path = ROOT / 'assets' / 'atom_intro.mp4'
        if not video_path.exists():
            log.info("Intro video not found at %s", video_path)
            return

        try:
            from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
            from PyQt6.QtMultimediaWidgets import QVideoWidget

            self._video_widget = QVideoWidget(self)
            self._video_widget.setGeometry(self.rect())
            self._video_widget.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatioByExpanding)
            self._video_widget.lower()
            # Clicks must reach the screen: this widget covers it edge to edge,
            # so without this a click on the video never became a skip.
            self._video_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._video_widget.show()

            self._video_player = QMediaPlayer(self)
            self._video_player.setVideoOutput(self._video_widget)
            self._video_audio = QAudioOutput()
            self._video_audio.setVolume(0.5)
            self._video_player.setAudioOutput(self._video_audio)
            self._video_player.setSource(QUrl.fromLocalFile(str(video_path.absolute())))
            self._video_player.mediaStatusChanged.connect(self._on_intro_video_status)
            self._video_player.errorOccurred.connect(self._on_intro_video_error)
            self._video_player.play()

            self._use_video = True
            self._has_intro_video = True
            self._played_intro_video = True
            log.info("Intro video started (QMediaPlayer)")
        except Exception as exc:
            log.warning("Intro video init failed: %s", exc)
            self._cleanup_intro_video()

    def _cleanup_intro_video(self):
        # Stop and release QMediaPlayer video
        if self._video_player:
            try:
                self._video_player.stop()
            except Exception:
                pass
            self._video_player = None
        if hasattr(self, '_video_audio') and self._video_audio:
            try:
                self._video_audio.deleteLater()
            except Exception:
                pass
            self._video_audio = None
        if self._video_widget:
            try:
                self._video_widget.hide()
                self._video_widget.deleteLater()
            except Exception:
                pass
            self._video_widget = None
        self._use_video = False
        self._has_intro_video = False

    def _on_intro_video_error(self, _error, error_string):
        """Handle intro video playback errors — fall back to dark background."""
        log.error("Intro video error: %s", error_string)
        self._cleanup_intro_video()

    def _on_intro_video_status(self, status):
        """Handle intro video status changes — log and ignore errors gracefully."""
        from PyQt6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.MediaStatus.InvalidMedia:
            log.warning("Intro video: invalid media (will use dark background)")
            self._cleanup_intro_video()
        elif status == QMediaPlayer.MediaStatus.NoMedia:
            log.warning("Intro video: no media loaded")
            self._cleanup_intro_video()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            log.info("Intro video ended — transitioning to next screen")
            # Video finished naturally — transition after a brief pause
            if hasattr(self, '_welcome_safety_timer') and self._welcome_safety_timer:
                self._welcome_safety_timer.stop()
            self._deferred.start(500, self._emit_complete)
            # Keep the last frame visible

    def _enable_skip(self):
        self._skip_allowed = True

    def _skip(self):
        if self._welcome_done:
            return
        log.info("Welcome screen skipped — video_played=%s", self._played_intro_video)
        # Clean up intro video on skip
        self._cleanup_intro_video()
        self._line1_rev = len(self._line1)
        self._line1_done = True
        self._line2_rev = len(self._line2)
        self._line2_done = True
        self._trigger_burst()
        self._deferred.start(500, self._on_trigger_done)

    def _start_line1(self):
        self._line1_timer = QTimer(self)
        self._line1_timer.timeout.connect(self._reveal_line1)
        self._line1_timer.start(25)  # Faster reveal (was 35ms)

    def _reveal_line1(self):
        self._line1_rev += 1
        if self._line1_rev >= len(self._line1):
            self._line1_done = True
            self._line1_timer.stop()
            # Start vibrate effect after line1 settles
            self._deferred.start(100, self._start_line1_vibrate)  # Reduced from 200ms
            self._deferred.start(350, self._start_line2)  # Reduced from 600ms
        self.update()

    def _start_line1_vibrate(self):
        self._line1_vibrating = True
        self._line1_vibrate_timer = QTimer(self)
        self._line1_vibrate_timer.timeout.connect(self._tick_vibrate)
        self._line1_vibrate_timer.start(30)
        # Stop after 200ms
        self._deferred.start(200, self._stop_line1_vibrate)

    def _tick_vibrate(self):
        self._line1_vibrate_frame = (self._line1_vibrate_frame + 1) % 4
        self.update()

    def _stop_line1_vibrate(self):
        self._line1_vibrating = False
        if self._line1_vibrate_timer:
            self._line1_vibrate_timer.stop()
            self._line1_vibrate_timer = None
        self.update()

    def _start_line2(self):
        self._line2_timer = QTimer(self)
        self._line2_timer.timeout.connect(self._reveal_line2)
        self._line2_timer.start(60)  # Faster letter reveal (was 80ms)

    def _reveal_line2(self):
        idx = self._line2_rev
        self._line2_scales[idx] = 200.0  # start at 200% scale, will slam to 100%
        self._line2_rev += 1
        if self._line2_rev >= len(self._line2):
            self._line2_done = True
            self._line2_timer.stop()
            self._trigger_burst()
        self.update()

    def _trigger_burst(self):
        """Trigger the glow burst animation for ATOM text reveal (no audio)."""
        self._burst_elapsed = 0
        # Set up triple concentric bursts
        self._burst_phases = [
            {'radius': 0, 'max_radius': 80, 'duration': 400, 'delay': 0, 'max_opacity': 1.0},
            {'radius': 0, 'max_radius': 140, 'duration': 600, 'delay': 100, 'max_opacity': 0.6},
            {'radius': 0, 'max_radius': 220, 'duration': 800, 'delay': 200, 'max_opacity': 0.3},
        ]

    def _draw_energy_field(self, painter, w, h):
        """Draw procedural particle vortex/energy field as video fallback."""
        if not hasattr(self, '_energy_particles') or not self._energy_particles:
            return
        cx, cy = w // 2, h // 2
        for ep in self._energy_particles:
            ep['angle'] += ep['orbital']
            ep['radius'] += math.sin(self._burst_elapsed * 0.002 + ep['phase']) * 0.3
            ep['radius'] = max(10, min(350, ep['radius']))
            px = cx + math.cos(ep['angle']) * ep['radius']
            py = cy + math.sin(ep['angle']) * ep['radius'] * 0.6
            pulse = 0.5 + 0.5 * math.sin(self._burst_elapsed * 0.004 + ep['phase'])
            alpha_mult = 0.3 + 0.7 * (self._line2_rev / max(1, len(self._line2)))
            alpha = int(ep['base_alpha'] * pulse * alpha_mult)
            if alpha > 5:
                painter.setPen(Qt.PenStyle.NoPen)
                col = QColor(0, 150 + int(105 * pulse), 255, alpha)
                painter.setBrush(QBrush(col))
                painter.drawEllipse(QPointF(px, py), ep['size'] * (0.5 + 0.5 * pulse), ep['size'] * (0.5 + 0.5 * pulse))
                # Glow trail
                trail_angle = ep['angle'] - ep['orbital'] * 3
                tx = cx + math.cos(trail_angle) * ep['radius']
                ty = cy + math.sin(trail_angle) * ep['radius'] * 0.6
                trail_col = QColor(0, 200, 255, alpha // 3)
                painter.setBrush(QBrush(trail_col))
                painter.drawEllipse(QPointF(tx, ty), ep['size'] * 0.3, ep['size'] * 0.3)
        # Central glow
        if self._burst_elapsed >= 0:
            center_alpha = int(min(60, self._burst_elapsed * 0.3))
            cg = QRadialGradient(cx, cy, 100)
            cg.setColorAt(0.0, QColor(0, 212, 255, center_alpha))
            cg.setColorAt(0.3, QColor(0, 150, 255, center_alpha // 2))
            cg.setColorAt(1.0, QColor(0, 212, 255, 0))
            painter.setBrush(QBrush(cg))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(cx, cy), 100, 60)

    def _on_trigger_done(self):
        """Burst animation complete — transition to next screen."""
        self._welcome_done = True
        self._deferred.start(1000, self._emit_complete)

    def _emit_complete(self):
        # Idempotent: EndOfMedia, the welcome safety timer and a skip all route
        # here, and each one used to emit again — which re-entered the intro
        # sequence and looked exactly like the intro restarting.
        if getattr(self, '_complete_sent', False):
            return
        self._complete_sent = True
        self._welcome_done = True
        if hasattr(self, '_welcome_safety_timer') and self._welcome_safety_timer:
            self._welcome_safety_timer.stop()
        if hasattr(self, '_tick') and self._tick:
            self._tick.stop()
        self.welcome_complete.emit()

    def _tick_update(self):
        # ── Video resize on each tick (handles screen changes) ──
        if self._video_widget:
            self._video_widget.setGeometry(self.rect())
        if self._burst_elapsed >= 0:
            self._burst_elapsed += 16
            if self._burst_elapsed > 1000:
                self._burst_steady = True
            # Update floating particles
        w = self.width() or 1920
        h = self.height() or 1080
        for p in self._particles:
            p['x'] -= p['speed']
            if p['x'] < -10:
                p['x'] = w + 10
                p['y'] = random.randint(0, h)

        # Update burst radii
            for bp in self._burst_phases:
                local_t = self._burst_elapsed - bp['delay']
                if 0 < local_t < bp['duration']:
                    t = local_t / bp['duration']
                    # ease-out
                    t_eased = 1.0 - (1.0 - t) * (1.0 - t)
                    bp['radius'] = bp['max_radius'] * t_eased
        # Animate letter scales (slam: 200% → 100% with easeOutBack)
        for idx in list(self._line2_scales.keys()):
            cur = self._line2_scales[idx]
            if cur > 100:
                # easeOutBack: overshoots then settles
                t = 1.0 - (cur - 100) / 100
                overshoot = 1.70158
                t_eased = 1.0 - (1.0 - t) * (1.0 - t) * (1.0 + overshoot * t)
                self._line2_scales[idx] = 100 + (100 - t_eased * 100)
                if abs(self._line2_scales[idx] - 100) < 0.5:
                    self._line2_scales[idx] = 100.0
        # Vibrate line1 when fully revealed
        if self._line1_done and hasattr(self, '_line1_vibrating') and self._line1_vibrating:
            self._line1_vibrate_frame = (self._line1_vibrate_frame + 1) % 4
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            painter.end()
            return

        # ── Dark background ──
        painter.fillRect(self.rect(), QColor(0, 0, 0))

        # ── Hex grid (cached, only regenerated on resize) ──
        self._ensure_hex_grid()
        if not self._grid_pixmap.isNull():
            painter.drawPixmap(0, 0, self._grid_pixmap)

        # ── Background glow pulse ──
        gp_phase = self._burst_elapsed if self._burst_elapsed >= 0 else 0
        pulse_op = 0.03 + 0.02 * math.sin(gp_phase * 0.002)
        glow_grad = QRadialGradient(w // 2, h // 2, max(w, h) * 0.5)
        glow_grad.setColorAt(0.0, QColor(0, 212, 255, int(255 * pulse_op)))
        glow_grad.setColorAt(0.4, QColor(0, 119, 255, int(80 * pulse_op)))
        glow_grad.setColorAt(1.0, QColor(0, 212, 255, 0))
        painter.setBrush(QBrush(glow_grad))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(w // 2, h // 2), max(w, h) * 0.5, max(w, h) * 0.5)

        # ── Floating particles ──
        for p in self._particles:
            if 0 <= p['x'] <= w and 0 <= p['y'] <= h:
                painter.setPen(QPen(QColor(0, 212, 255, p['alpha']), max(1, int(p['size']))))
                painter.drawPoint(int(p['x']), int(p['y']))

        # ── Subtle CRT scan line overlay ──
        gp_phase = self._burst_elapsed if self._burst_elapsed >= 0 else 0
        screen_pulse = 0.03 + 0.02 * math.sin(gp_phase * 0.005)
        if screen_pulse > 0.04:
            painter.setOpacity(screen_pulse * 0.5)
            for sy in range(0, h, 4):
                painter.fillRect(0, sy, w, 1, QColor(0, 212, 255, 30))
            painter.setOpacity(1.0)

        # ── Iron Man HUD Corner Brackets ──
        gap = 4
        bracket_len = 35
        painter.setPen(QPen(QColor(0, 212, 255), 2))
        painter.drawLine(gap, gap, gap + bracket_len, gap)
        painter.drawLine(gap, gap, gap, gap + bracket_len)
        painter.drawLine(w - gap - bracket_len, gap, w - gap, gap)
        painter.drawLine(w - gap, gap, w - gap, gap + bracket_len)
        painter.drawLine(gap, h - gap, gap + bracket_len, h - gap)
        painter.drawLine(gap, h - gap, gap, h - gap - bracket_len)
        painter.drawLine(w - gap - bracket_len, h - gap, w - gap, h - gap)
        painter.drawLine(w - gap, h - gap, w - gap, h - gap - bracket_len)

        painter.setPen(QPen(QColor(0, 212, 255, 40), 1))
        painter.drawRect(gap + 2, gap + 2, w - 2 * (gap + 2), h - 2 * (gap + 2))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(0, 212, 255)))
        inner_x = gap + bracket_len
        inner_y = gap + bracket_len
        for ax, ay in [
            (inner_x, gap), (w - inner_x, gap),
            (inner_x, h - gap), (w - inner_x, h - gap),
            (gap, inner_y), (gap, h - inner_y),
            (w - gap, inner_y), (w - gap, h - inner_y),
        ]:
            painter.drawEllipse(QPointF(ax, ay), 2, 2)

        # ── HUD text ──
        painter.setFont(self._hud_font)
        painter.setPen(HUD_TEXT)
        painter.drawText(40, 18, "ATOM.SYS v70")
        painter.drawText(40, 30, "WELCOME SEQUENCE")
        painter.drawText(w - 200, 18, _datetime.datetime.now().strftime("%H:%M:%S"))
        state = "COMPLETE" if self._line2_done else ("LINE2" if self._line1_done else "LINE1")
        painter.drawText(w - 200, 30, f"STATUS: {state}")

        # ── OpenCV video or energy field fallback ──
        if self._has_intro_video and hasattr(self, '_intro_video_pixmap') and self._intro_video_pixmap is not None:
            scaled = self._intro_video_pixmap.scaled(
                w, h,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            sw, sh = scaled.width(), scaled.height()
            sx = (sw - w) // 2 if sw > w else 0
            sy = (sh - h) // 2 if sh > h else 0
            painter.drawPixmap(0, 0, scaled, sx, sy, w, h)
        else:
            self._draw_energy_field(painter, w, h)



        # ── Line 1: "LET'S WELCOME" ──
        if self._line1_rev > 0:
            painter.setFont(self._font_line1)
            painter.setPen(HUD_WHITE)
            vib_x = 0
            vib_y = 0
            if getattr(self, '_line1_vibrating', False):
                vib_x = random.randint(-1, 1)
                vib_y = random.randint(-1, 1)
            painter.drawText(QRect(vib_x, int(h * 0.38) + vib_y, w, 40),
                             Qt.AlignmentFlag.AlignCenter,
                             self._line1[:self._line1_rev])

        # ── Line 2: "ATOM" ──
        if self._line2_rev > 0:
            painter.save()
            # Draw each letter with its own scale (slam animation)
            painter.setFont(self._font_line2)
            painter.setPen(HUD_CYAN)
            fm2 = QFontMetrics(self._font_line2)
            full_text = self._line2[:self._line2_rev]
            total_w = fm2.horizontalAdvance(full_text)
            start_x2 = (w - total_w) // 2
            for li, lch in enumerate(full_text):
                scale = self._line2_scales.get(li, 100.0) / 100.0
                if abs(scale - 1.0) > 0.01:
                    painter.save()
                    lx = start_x2 + fm2.horizontalAdvance(full_text[:li])
                    painter.translate(lx + fm2.horizontalAdvance(lch) // 2, int(h * 0.48) + 40)
                    painter.scale(scale, scale)
                    painter.drawText(-fm2.horizontalAdvance(lch) // 2, -fm2.descent() + 30, lch)
                    painter.restore()
                else:
                    lx = start_x2 + fm2.horizontalAdvance(full_text[:li])
                    painter.drawText(lx, int(h * 0.48) + 40, lch)
            painter.restore()

        # ── Triple concentric glow bursts ──
        if self._burst_elapsed >= 0 and not self._burst_steady:
            fm = QFontMetrics(self._font_line2)
            atom_w = fm.horizontalAdvance("ATOM")
            cx = (w - atom_w) // 2 + atom_w // 2
            cy = int(h * 0.48) + 35
            for bp in self._burst_phases:
                radius = bp.get('radius', 0)
                if radius > 5:
                    local_t = max(0, self._burst_elapsed - bp['delay'])
                    op = bp['max_opacity'] * (1.0 - local_t / bp['duration']) if bp['duration'] > 0 else 0
                    op = max(0.0, min(1.0, op))
                    gradient = QRadialGradient(cx, cy, max(1, radius))
                    gradient.setColorAt(0.0, QColor(0, 212, 255, int(op * 200)))
                    gradient.setColorAt(0.6, QColor(0, 212, 255, int(op * 60)))
                    gradient.setColorAt(1.0, QColor(0, 212, 255, 0))
                    painter.setBrush(QBrush(gradient))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawEllipse(QPointF(cx, cy), radius, radius * 0.5)

        # ── Steady glow ──
        if self._burst_steady and self._line2_done:
            fm = QFontMetrics(self._font_line2)
            atom_w = fm.horizontalAdvance("ATOM")
            cx = (w - atom_w) // 2 + atom_w // 2
            cy = int(h * 0.48) + 35
            gradient = QRadialGradient(cx, cy, 50)
            gradient.setColorAt(0.0, QColor(0, 212, 255, 89))
            gradient.setColorAt(1.0, QColor(0, 212, 255, 0))
            painter.setBrush(QBrush(gradient))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(cx, cy), 50, 25)

        # ── Skip button (bottom-right corner) ──
        if self._skip_allowed and not self._welcome_done:
            painter.setFont(QFont("Courier New", 10))
            painter.setPen(HUD_TEXT_DIM)
            painter.drawText(QRect(0, h - 40, w, 20), Qt.AlignmentFlag.AlignCenter,
                             "Press any key to skip  |  Ctrl+C to abort")
            # Visible skip button
            btn_x = w - 100
            btn_y = h - 36
            btn_w = 85
            btn_h = 22
            btn_pulse = 0.3 + 0.2 * math.sin(self._burst_elapsed * 0.003 if self._burst_elapsed >= 0 else 0)
            painter.fillRect(btn_x, btn_y, btn_w, btn_h, QColor(0, 212, 255, int(30 * btn_pulse)))
            painter.setPen(QPen(QColor(0, 212, 255, int(120 * btn_pulse)), 1))
            painter.drawRect(btn_x, btn_y, btn_w, btn_h)
            painter.setFont(QFont("Courier New", 9))
            painter.setPen(QColor(0, 212, 255, int(150 * btn_pulse)))
            painter.drawText(btn_x, btn_y, btn_w, btn_h, Qt.AlignmentFlag.AlignCenter, "[SKIP >>]")

        # Persistent animated character
        if hasattr(self, '_intro_sprite') and self._intro_sprite is not None:
            _draw_intro_sprite(painter, self._intro_sprite,
                               self._sprite_frame_ref() if self._sprite_frame_ref else 0,
                               w - 200, h - 240,
                               pose='cheer' if self._line2_done else 'idle',
                               emotion=self._sprite_emotion_ref() if self._sprite_emotion_ref else 'happy')

        painter.end()
    
    def keyPressEvent(self, event):
        # Ctrl+C to abort the entire intro
        if event.key() == Qt.Key.Key_C and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._skip()
            return
        if self._skip_allowed and not self._welcome_done:
            self._skip()
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if self._skip_allowed and not self._welcome_done:
            self._skip()
        elif not self._skip_allowed:
            log.debug("Skip click ignored — skip delay still active")
        super().mousePressEvent(event)

    def _ensure_hex_grid(self, force=False):
        """Cache hex grid once, only regenerate on resize."""
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        if force or self._grid_pixmap.isNull() or self._grid_pixmap.width() != w or self._grid_pixmap.height() != h:
            self._cache_hex_grid(w, h)

    def resizeEvent(self, event):
        """Invalidate hex grid cache on resize."""
        self._grid_pixmap = QPixmap()
        super().resizeEvent(event)

    def _cache_hex_grid(self, w, h):
        """Draw a hex grid pattern to QPixmap once, reuse every frame."""
        w = max(1, w)
        h = max(1, h)
        self._grid_pixmap = QPixmap(w, h)
        self._grid_pixmap.fill(Qt.GlobalColor.transparent)
        gp = QPainter(self._grid_pixmap)
        gp.setRenderHint(QPainter.RenderHint.Antialiasing)
        gp.setPen(QPen(QColor(14, 22, 40, 40), 1))
        r = 30
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

    def closeEvent(self, event):
        if hasattr(self, '_line1_timer') and self._line1_timer:
            self._line1_timer.stop()
        if hasattr(self, '_line2_timer') and self._line2_timer:
            self._line2_timer.stop()
        self._tick.stop()
        # The 1 s `_emit_complete` delay would otherwise fire after the sequence
        # had moved on, advancing the intro twice.
        self._deferred.stop_all()
        self._cleanup_intro_video()
        event.accept()


# ════════════════════════════════════════════════════════════════════════════════
# SCREEN 4 — AtomDanceScreen: Ra.One Particle Assembly + Video + HUD
# ════════════════════════════════════════════════════════════════════════════════

class RaOneParticle:
    """A single particle in the Ra.One assembly effect."""
    __slots__ = ('x', 'y', 'tx', 'ty', 'speed', 'alpha', 'size', 'phase',
                 'trail', 'color', 'body_part', 'born_time', 'arrival_flash')

    def __init__(self, x, y, tx, ty, body_part="none"):
        self.x = float(x)
        self.y = float(y)
        self.tx = float(tx)
        self.ty = float(ty)
        self.speed = random.uniform(0.03, 0.08)
        self.alpha = 0
        # Three sizes: 4x4 standard, 2x2 small, 6x6 large
        size_roll = random.random()
        if size_roll < 0.2:
            self.size = random.uniform(2.8, 3.5)  # large
        elif size_roll < 0.5:
            self.size = random.uniform(1.0, 1.8)  # small
        else:
            self.size = random.uniform(1.8, 2.8)  # standard
        self.phase = 0  # 0=flying in, 1=assembled, 2=fading
        self.trail = []
        self.body_part = body_part
        self.born_time = time.time()
        self.arrival_flash = 0  # >0 when particle arrives, counts down

        # Color based on body part
        part_colors = {
            "head": (255, 140, 40),
            "body": (0, 180, 255),
            "arm": (255, 80, 80),
            "leg": (0, 200, 100),
            "aura": (100, 200, 255),
            "none": (0, 212, 255),
        }
        self.color = part_colors.get(body_part, (0, 212, 255))


class AtomDanceScreen(QDialog):
    dance_complete = pyqtSignal()
    """Full-screen dance sequence with Ra.One particle assembly + video + HUD.

    Sequence:
    1. Ra.One particle assembly (nanobot construction effect)
    2. Full-screen video playback (atom_intro.mp4) with HUD overlay
    3. Fallback to Minecraft sprite animation if video unavailable
    """

    startup_complete = pyqtSignal()

    # ── Body part target positions (ratios relative to screen) ──
    BODY_PARTS = {
        "head": [(0.48, 0.25), (0.52, 0.25), (0.50, 0.20)],
        "body": [(0.50, 0.45), (0.45, 0.40), (0.55, 0.40)],
        "arm_l": [(0.30, 0.38), (0.28, 0.45)],
        "arm_r": [(0.70, 0.38), (0.72, 0.45)],
        "leg_l": [(0.42, 0.65), (0.40, 0.72)],
        "leg_r": [(0.58, 0.65), (0.60, 0.72)],
    }

    def __init__(self, config, intro_sprite=None, sprite_frame_ref=None, sprite_pose_ref=None, sprite_emotion_ref=None):
        super().__init__()
        # Owns every deferred callback, so closing the screen cancels them all.
        self._deferred = _DeferredCalls(self)
        self.config = config
        self._intro_sprite = intro_sprite
        self._sprite_frame_ref = sprite_frame_ref or (lambda: 0)
        self._sprite_pose_ref = sprite_pose_ref or (lambda: 'idle')
        self._sprite_emotion_ref = sprite_emotion_ref or (lambda: 'neutral')

        self.setStyleSheet("background-color: #000000;")
        # Window flags + fullscreen deferred to avoid DWM crash
        self._deferred.start(0, self._apply_window_flags)

        # ── Ra.One particles ──
        self._particles: list[RaOneParticle] = []
        self._assembly_phase = 0  # 0=init, 1=assembling, 2=done
        self._assembly_elapsed = 0
        self._assembly_duration = 2500  # 2.5 seconds for assembly (was 4000)
        self._assembly_tick_counter = 0  # throttle: updates every other tick

        # ── P7: HUD readout ──
        self._hud_readout_timer = 0
        self._hud_readouts = {
            'top_right': 'SYS: 0x0000',
            'bottom_left': 'PID: 0000 MEM: 0MB',
        }

        # ── P7: Sprite scan line reveal ──
        self._sprite_reveal_y = 0
        self._sprite_reveal_active = False

        # ── Video ──
        self._video_player = None
        self._video_widget = None
        self._use_video = False
        self._video_started = False

        # ── Sprite fallback ──
        self._frame_index = 0
        self._animation_elapsed = 0
        self._skin_pixmap = QPixmap()
        self._loaded_skin_name = None
        self._has_skin = False

        # ── Completion state ──
        self._dance_complete = False
        self._skip_allowed = False
        self._deferred.start(1000, self._enable_skip)

        # ── Initialize ──
        self._init_particles()
        self._try_init_video()
        if not self._use_video:
            self._init_sprite_fallback()

        # ── Tick timer (slower during video to reduce compositing overhead) ──
        self._tick = QTimer(self)
        self._tick.timeout.connect(self._tick_update)
        self._tick.start(33)
        self._video_mode_active = False
        self._video_paused_intentionally = True  # Video paused during assembly

        # ── Video health monitor (checks playback every 1s) ──
        self._video_health_timer = QTimer(self)
        self._video_health_timer.timeout.connect(self._check_video_health)
        self._video_health_timer.start(1000)

        # Video loading timeout: if not loaded in 3s, fall back to sprite
        self._video_load_timer = QTimer(self)
        self._video_load_timer.setSingleShot(True)
        self._video_load_timer.timeout.connect(self._on_video_load_timeout)
        self._video_load_timer.start(10000)

        # Safety completion (extended timeout — video can be 30-60s for a 100MB file)
        self._deferred.start(60000, self._complete)

        log.info("AtomDanceScreen shown (video=%s, particles=%d)", self._use_video, len(self._particles))
    def _init_particles(self):
        """Create Ra.One particles starting from screen edges."""
        w = self.width() or 1920
        h = self.height() or 1080
        self._particles = []
        idx = 0

        for part_name, targets in self.BODY_PARTS.items():
            for target in targets:
                tx = target[0] * w
                ty = target[1] * h
                # Spawn from random edge
                edge = idx % 4
                if edge == 0:
                    sx, sy = random.randint(0, w), -20
                elif edge == 1:
                    sx, sy = w + 20, random.randint(0, h)
                elif edge == 2:
                    sx, sy = random.randint(0, w), h + 20
                else:
                    sx, sy = -20, random.randint(0, h)
                self._particles.append(RaOneParticle(sx, sy, tx, ty, part_name))
                idx += 1

        # Add aura particles around center
        for _ in range(40):
            angle = random.uniform(0, math.pi * 2)
            dist = random.uniform(80, 200)
            tx = w * 0.50 + math.cos(angle) * dist
            ty = h * 0.42 + math.sin(angle) * dist * 0.6
            sx = random.randint(-20, w + 20)
            sy = random.randint(-20, h + 20)
            self._particles.append(RaOneParticle(sx, sy, tx, ty, "aura"))

        # Random order for natural feel
        random.shuffle(self._particles)

    def _try_init_video(self):
        video_path = ROOT / 'assets' / 'atom_intro.mp4'
        if not video_path.exists():
            log.info("Dance video not found — using sprite fallback")
            return

        try:
            from PyQt6.QtMultimedia import QMediaPlayer, QAudioOutput
            from PyQt6.QtMultimediaWidgets import QVideoWidget

            self._video_widget = QVideoWidget(self)
            self._video_widget.setGeometry(self.rect())
            self._video_widget.setAspectRatioMode(Qt.AspectRatioMode.KeepAspectRatioByExpanding)
            self._video_widget.lower()  # Keep behind QPainter drawing during assembly
            # This widget is *raised* over the screen while the video plays, so
            # without this the first click landed on the video and the next one
            # was needed to skip.
            self._video_widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self._video_widget.show()

            self._video_player = QMediaPlayer(self)
            self._video_player.setVideoOutput(self._video_widget)
            self._video_audio = QAudioOutput()
            self._video_audio.setVolume(0.7)
            self._video_player.setAudioOutput(self._video_audio)
            self._video_player.setSource(QUrl.fromLocalFile(str(video_path.absolute())))
            self._video_player.mediaStatusChanged.connect(self._on_video_status)
            self._video_player.errorOccurred.connect(self._on_video_error)
            self._video_player.play()
            self._video_player.pause()  # Pause until assembly is done

            self._use_video = True
            log.info("Dance video loaded (paused until assembly)")
        except Exception as exc:
            log.warning("Video init failed: %s", exc)
            self._cleanup_video()

    def _on_video_error(self, _error, error_string):
        """Handle video playback errors gracefully — always completes."""
        log.error("Dance video error: %s", error_string)
        self._cleanup_video()
        self._init_sprite_fallback()
        # Always complete regardless of phase (don't get stuck)
        self._deferred.start(500, self._complete)

    def _stop_video_load_timer(self):
        """Stop the video-load watchdog, if it is still alive.

        ``_cleanup_video()`` drops the reference, but Qt keeps delivering
        ``mediaStatusChanged`` after the player has been stopped, so this must
        never assume the timer still exists.
        """
        timer = self.__dict__.get('_video_load_timer')
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass

    def _on_video_status(self, status):
        from PyQt6.QtMultimedia import QMediaPlayer
        # Qt delivers status updates asynchronously — including after
        # ``_cleanup_video()`` has torn the player down (skipping the dance
        # screen is enough to trigger one). The screen is gone by then, so a
        # late status must be ignored rather than acted on: ``_video_load_timer``
        # is None at that point and the old code raised AttributeError here.
        if self.__dict__.get('_video_player') is None:
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            log.info("Dance video ended")
            self._deferred.start(500, self._complete)
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            log.error("Dance video: invalid media file (codec or format issue)")
            self._cleanup_video()
            self._init_sprite_fallback()
            self._deferred.start(300, self._complete)
        elif status == QMediaPlayer.MediaStatus.NoMedia:
            log.warning("Dance video: no media loaded")
            self._cleanup_video()
            self._init_sprite_fallback()
            self._deferred.start(300, self._complete)
        elif status == QMediaPlayer.MediaStatus.LoadedMedia:
            log.info("Dance video: media loaded and ready")
            self._stop_video_load_timer()
        elif status == QMediaPlayer.MediaStatus.BufferingMedia:
            log.info("Dance video: buffering")
            self._stop_video_load_timer()
        elif status == QMediaPlayer.MediaStatus.StalledMedia:
            log.warning("Dance video: stalled (buffering or stream issue)")
            # The periodic health monitor will handle recovery
        elif hasattr(QMediaPlayer.MediaStatus, 'PausedMedia') and status == QMediaPlayer.MediaStatus.PausedMedia:
            log.debug("Dance video: paused")

    def _on_video_load_timeout(self):
        """Video failed to load within timeout — fall back to sprite and complete."""
        log.warning("Dance video: load timeout (10s) — falling back to sprite")
        self._cleanup_video()
        self._init_sprite_fallback()
        if not self._dance_complete:
            self._complete()

    def _on_video_skip_tick(self):
        """Slow timer for updating skip hint visibility during video."""
        if self._video_mode_active:
            self.update()

    def _check_video_health(self):
        """Periodic video health check — detects stalls, pauses, media errors early."""
        if not self._use_video or not self._video_player or self._dance_complete:
            return
        state = self._video_player.playbackState()
        # 0=StoppedState, 1=PlayingState, 2=PausedState
        from PyQt6.QtMultimedia import QMediaPlayer
        status = self._video_player.mediaStatus()
        if state == 0:  # Stopped unexpectedly
            log.warning("Video stopped unexpectedly — checking status")
            if status in (QMediaPlayer.MediaStatus.EndOfMedia,):
                log.info("Video naturally ended — completing")
                self._deferred.start(500, self._complete)
            elif status in (QMediaPlayer.MediaStatus.InvalidMedia, QMediaPlayer.MediaStatus.NoMedia):
                log.warning("Video media invalid — falling back to sprite")
                self._cleanup_video()
                self._init_sprite_fallback()
        elif state == 2:  # Paused unexpectedly (Windows compositor switch, stall recovery)
            log.warning("Video paused unexpectedly — attempting recovery")
            if status in (QMediaPlayer.MediaStatus.EndOfMedia,):
                log.info("Video ended (paused at end) — completing")
                self._deferred.start(500, self._complete)
            elif status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferingMedia):
                self._video_player.play()
                log.info("Attempted to resume paused video")
            elif status in (QMediaPlayer.MediaStatus.InvalidMedia, QMediaPlayer.MediaStatus.NoMedia):
                log.warning("Video media invalid — falling back to sprite")
                self._cleanup_video()
                self._init_sprite_fallback()
            else:
                self._video_player.play()

    def _cleanup_video(self):
        """Clean up video player, audio, and widget. Safe to call multiple times."""
        # Stop health monitor if running
        if hasattr(self, '_video_health_timer') and self._video_health_timer:
            try:
                self._video_health_timer.stop()
            except Exception:
                pass
            self._video_health_timer = None

        # Single-shot load timer: it was left pending, so it fired 10 s after the
        # screen had already been closed and kicked off a sprite fallback on a
        # hidden widget.
        load_timer = self.__dict__.get('_video_load_timer')
        if load_timer:
            try:
                load_timer.stop()
            except Exception:
                pass
        self._video_load_timer = None

        if self._video_player:
            # Disconnect before stopping: ``stop()`` emits ``mediaStatusChanged``
            # synchronously, and any late emit would otherwise reach
            # ``_on_video_status`` after the player/timers below are released.
            try:
                self._video_player.mediaStatusChanged.disconnect(self._on_video_status)
            except Exception:
                pass
            try:
                self._video_player.errorOccurred.disconnect(self._on_video_error)
            except Exception:
                pass
            try:
                self._video_player.stop()
                self._video_player.deleteLater()
            except Exception:
                pass
            self._video_player = None
        if hasattr(self, '_video_audio') and self._video_audio:
            try:
                self._video_audio.deleteLater()
            except Exception:
                pass
            self._video_audio = None
        if self._video_widget:
            try:
                self._video_widget.hide()
                self._video_widget.deleteLater()
            except Exception:
                pass
            self._video_widget = None
        self._use_video = False

    def _init_sprite_fallback(self):
        skins_dir = ROOT / 'assets' / 'skins'
        skin_files = ['atom_skin.png', 'pink_skin.png', 'steve_skin.png',
                      'alex_skin.png', 'ghost_skin.png', 'gamergirl.png']
        for skin_file in skin_files:
            skin_path = skins_dir / skin_file
            if skin_path.exists():
                pix = QPixmap(str(skin_path))
                if not pix.isNull():
                    self._skin_pixmap = pix
                    self._loaded_skin_name = skin_file.replace('_skin.png', '').replace('.png', '')
                    self._has_skin = True
                    log.info("Loaded skin: %s", self._loaded_skin_name)
                    break

    def _enable_skip(self):
        self._skip_allowed = True

    def _skip(self):
        self._complete()

    def _tick_update(self):
        # Slow down tick during video playback (reduces compositor overhead significantly)
        if self._video_mode_active:
            self._animation_elapsed += 100  # 100ms ticks during video
            self._frame_index = int((self._animation_elapsed / 100) % 8)
            self._hud_readout_timer += 100
            if self._hud_readout_timer >= 2000:
                self._hud_readout_timer = 0
                self._hud_readouts['top_right'] = f'SYS: 0x{random.randint(0x1000, 0xFFFF):04X}'
                pid = random.randint(1000, 9999)
                mem = random.randint(200, 2000)
                self._hud_readouts['bottom_left'] = f'PID: {pid} MEM: {mem}MB'
            # Only update every 3rd tick during video (reduces paint calls by 66%)
            if self._animation_elapsed % 300 != 0:
                return
            self.update()
            return

        self._animation_elapsed += 33
        self._frame_index = int((self._animation_elapsed / 100) % 8)

        # ── P7: HUD readout refresh every 2s ──
        self._hud_readout_timer += 33
        if self._hud_readout_timer >= 2000:
            self._hud_readout_timer = 0
            self._hud_readouts['top_right'] = f'SYS: 0x{random.randint(0x1000, 0xFFFF):04X}'
            pid = random.randint(1000, 9999)
            mem = random.randint(200, 2000)
            self._hud_readouts['bottom_left'] = f'PID: {pid} MEM: {mem}MB'

        # ── Ra.One particle assembly ──
        if self._assembly_phase < 2:
            self._assembly_elapsed += 33
            assembly_progress = self._assembly_elapsed / self._assembly_duration

            if assembly_progress >= 1.0:
                self._assembly_phase = 2
                # Start video if available
                if self._video_player and self._use_video:
                    # Switch to video mode
                    self._video_widget.show()
                    self._video_widget.raise_()
                    self._video_player.play()
                    # CRITICAL: Stop tick timer completely during video.
                    # QPainter compositing over QVideoWidget kills frame rate in Qt 6.
                    # The video plays independently via the media player.
                    # A separate 1-second timer handles the skip hint overlay.
                    self._tick.stop()
                    self._video_mode_active = True
                    self._video_skip_timer = QTimer(self)
                    self._video_skip_timer.timeout.connect(self._on_video_skip_tick)
                    self._video_skip_timer.start(1000)
                # Start sprite scan-line reveal
                self._sprite_reveal_active = True
                self._sprite_reveal_y = 0
                log.info("Ra.One assembly complete")
            else:
                self._assembly_phase = 1

            # Update particles (throttled to every other tick to reduce CPU burst)
            self._assembly_tick_counter += 1
            if self._assembly_tick_counter % 2 == 0:
                for p in self._particles:
                    if p.phase == 0:
                        # Flying in
                        dx = p.tx - p.x
                        dy = p.ty - p.y
                        p.x += dx * p.speed
                        p.y += dy * p.speed
                        p.alpha = min(255, p.alpha + 8)

                        # Check if arrived
                        if abs(dx) < 5 and abs(dy) < 5:
                            p.phase = 1
                            p.x = p.tx
                            p.y = p.ty
                            p.arrival_flash = 20  # 20 ticks of flash

                        # Comet trail (3-tick history for speed)
                        if len(p.trail) > 3:
                            p.trail.pop(0)
                        p.trail.append((p.x, p.y, p.alpha))

                    elif p.phase == 1:

                        # Assembled — pulse

                        p.alpha = 200 + int(55 * math.sin(self._animation_elapsed * 0.005 + hash(p) % 100))

                        # Arrival flash countdown

                        if p.arrival_flash > 0:

                            p.arrival_flash -= 1


                        # After assembly complete, start fading aura particles

                        if self._assembly_phase == 2 and p.body_part == "aura":

                            p.phase = 2


                    elif p.phase == 2:

                        # Fading out (aura particles)

                        p.alpha = max(0, p.alpha - 5)


        # ── P7: Sprite scan line reveal ──
        if self._sprite_reveal_active:
            step = max(2, (self.height() or 1080) // 50)
            self._sprite_reveal_y += step
            if self._sprite_reveal_y >= (self.height() or 1080):
                self._sprite_reveal_active = False

        # Throttle self.update() to every other tick during assembly (matches particle throttle)
        if self._assembly_tick_counter % 2 == 0:
            self.update()

    def _stop_video_skip_timer(self):
        """Stop the 1 s video-mode repaint timer. Idempotent.

        ``__dict__.get`` rather than ``getattr`` because QObjects built with
        ``__new__`` (as tests do) raise on a missing attribute.
        """
        timer = self.__dict__.get('_video_skip_timer')
        if timer:
            timer.stop()
        self._video_skip_timer = None

    def _complete(self):
        if self._dance_complete:
            return
        self._dance_complete = True
        self._tick.stop()
        self._stop_video_skip_timer()
        self._video_mode_active = False
        self._cleanup_video()
        self._deferred.start(100, lambda: self.startup_complete.emit())

    def _draw_hud(self, painter, w, h):
        """Draw the Iron Man HUD overlay."""
        _draw_hud_frame(painter, w, h)

        painter.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        painter.setPen(QColor(0, 212, 255, 180))
        label = self._loaded_skin_name.upper() if self._loaded_skin_name else "ATOM"
        painter.drawText(40, 18, f"{label}.SYS v70")

        painter.setFont(QFont("Courier New", 9))
        painter.setPen(QColor(0, 212, 255, 140))

        if self._assembly_phase < 2:
            status_text = "RA.ONE ASSEMBLY"
        elif self._use_video:
            status_text = "DANCE MODE"
        else:
            status_text = "DISPLAY MODE"
        painter.drawText(w - 220, 18, status_text)

        painter.drawText(w - 220, 30, _datetime.datetime.now().strftime("%H:%M:%S"))

        # ── P7: HUD animated readouts ──
        painter.setFont(QFont("Courier New", 8))
        painter.setPen(QColor(0, 212, 255, 127))
        # Top-right data readout
        if hasattr(self, '_hud_readouts') and 'top_right' in self._hud_readouts:
            painter.drawText(w - 180, h - 45, self._hud_readouts['top_right'])
        # Bottom-left data readout
        if hasattr(self, '_hud_readouts') and 'bottom_left' in self._hud_readouts:
            painter.drawText(40, h - 38, self._hud_readouts['bottom_left'])

        # HUD status bars
        _draw_status_bar(painter, w, h, "SYS", 1.0, 30, h - 28, 80, 6, HUD_CYAN)
        _draw_status_bar(painter, w, h, "VID", 0.85, 130, h - 28, 80, 6, HUD_GREEN)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            painter.end()
            return

        # ── Black background ──
        painter.fillRect(self.rect(), QColor(0, 0, 0))

        # ── Ra.One particle assembly phase ──
        if self._assembly_phase < 2:
            self._draw_particle_assembly(painter, w, h)

        # ── Video is playing or sprite fallback ──
        is_video_active = self._use_video and self._assembly_phase == 2
        if is_video_active:
            if self._video_widget:
                self._video_widget.setGeometry(self.rect())
            # VIDEO MODE: Minimal overlay — skip full HUD to avoid compositing overhead
            # This is critical: QPainter + QVideoWidget compositing kills frame rate
            pass
        elif not self._use_video and self._assembly_phase == 2:
            # Sprite fallback after assembly
            self._draw_sprite_fallback(painter, w, h)
            # Full HUD for sprite mode (no QVideoWidget to compete with)
            self._draw_hud(painter, w, h)
        else:
            # Assembly phase: full HUD
            self._draw_hud(painter, w, h)

        # ── Video mode: no HUD drawing (QPainter renders behind QVideoWidget)
        # The video widget is a child that composites on top, so HUD text here
        # would be invisible. Only draw the skip hint which renders via Qt compositing.
        if is_video_active:
            # Single scan line (very lightweight, barely visible but shows system is alive)
            scan_y = int(time.time() * 30) % h
            painter.fillRect(0, scan_y, w, 1, QColor(0, 212, 255, 8))

        # ── Skip hint ──
        if self._skip_allowed and not self._dance_complete:
            painter.setFont(QFont("Courier New", 9))
            painter.setPen(HUD_TEXT_DIM)
            painter.drawText(QRect(0, h - 45, w, 16), Qt.AlignmentFlag.AlignCenter,
                            "Press any key to skip")

        painter.end()

    def _draw_particle_assembly(self, painter, w, h):
        """Draw the Ra.One particle assembly effect."""
        # Glow center
        center_x = w * 0.50
        center_y = h * 0.42

        # Central glow builds as particles arrive
        arrival_ratio = min(1.0, self._assembly_elapsed / self._assembly_duration)
        glow_radius = 30 + arrival_ratio * 100

        # Draw comet trails first (behind particles)
        for p in self._particles:
            if len(p.trail) > 1 and p.phase == 0:
                # Draw trail with decreasing opacity (comet tail effect)
                trail_len = len(p.trail)
                for i in range(1, trail_len):
                    tx, ty, ta = p.trail[i - 1]
                    nx, ny, na = p.trail[i]
                    # Brightness fades along the trail
                    fade = i / trail_len  # 0=oldest, 1=newest
                    trail_alpha = int(na * fade * 0.5)
                    trail_width = p.size * (0.3 + 0.7 * fade)
                    if trail_alpha > 2:
                        painter.setPen(QPen(QColor(*p.color, trail_alpha), max(0.5, trail_width)))
                        painter.drawLine(QPointF(tx, ty), QPointF(nx, ny))

        # Draw particles
        for p in self._particles:
            if p.alpha > 5:
                alpha = p.alpha
                if p.body_part == "aura":
                    alpha = int(alpha * (1.0 - arrival_ratio * 0.8))

                # Arrival flash (white spark when particle hits target)
                if p.arrival_flash > 0:
                    flash_alpha = int(200 * (p.arrival_flash / 20.0))
                    painter.setPen(QPen(QColor(255, 255, 255, flash_alpha), 1.5))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawEllipse(QPointF(p.x, p.y), p.size * 2, p.size * 2)

                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(*p.color, alpha)))
                painter.drawEllipse(QPointF(p.x, p.y), p.size, p.size)

                # Glow around assembled particles
                if p.phase == 1 and p.body_part != "aura":
                    glow = QRadialGradient(p.x, p.y, p.size * 3)
                    glow.setColorAt(0.0, QColor(*p.color, int(alpha * 0.3)))
                    glow.setColorAt(1.0, QColor(*p.color, 0))
                    painter.setBrush(QBrush(glow))
                    painter.drawEllipse(QPointF(p.x, p.y), p.size * 3, p.size * 3)

        # Central glow
        if arrival_ratio > 0.1:
            gradient = QRadialGradient(center_x, center_y, glow_radius)
            glow_a = int(40 * arrival_ratio)
            gradient.setColorAt(0.0, QColor(0, 212, 255, glow_a))
            gradient.setColorAt(0.5, QColor(0, 212, 255, int(glow_a * 0.5)))
            gradient.setColorAt(1.0, QColor(0, 212, 255, 0))
            painter.setBrush(QBrush(gradient))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(center_x, center_y), glow_radius, glow_radius * 0.6)

        # Assembly progress text
        if arrival_ratio < 1.0:
            pct = int(arrival_ratio * 100)
            painter.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
            painter.setPen(QColor(0, 212, 255, 80 + int(100 * (1 - arrival_ratio))))
            painter.drawText(QRect(0, int(h * 0.75), w, 30), Qt.AlignmentFlag.AlignCenter,
                             f"RA.ONE NANOBOT ASSEMBLY  [{pct}%]")

            # Animated progress bar
            bar_w = int(w * 0.4)
            bar_x = (w - bar_w) // 2
            bar_y = int(h * 0.78)
            painter.fillRect(bar_x, bar_y, bar_w, 3, QColor(16, 20, 36))
            fill = int(bar_w * arrival_ratio)
            if fill > 0:
                painter.fillRect(bar_x, bar_y, fill, 3, HUD_CYAN)

    def _draw_sprite_fallback(self, painter, w, h):
        """Draw Minecraft sprite as fallback after assembly (with scan-line reveal)."""
        if not self._has_skin:
            return

        scale = 3
        sprite_w = 64 * scale
        sprite_h = 64 * scale
        sprite_x = (w - sprite_w) // 2
        sprite_y = (h - sprite_h) // 2 - 50

        # Bob animation
        bob = int(math.sin(self._animation_elapsed / 500) * 15)
        sprite_y += bob

        scaled = self._skin_pixmap.scaledToWidth(sprite_w,
                                                 Qt.TransformationMode.SmoothTransformation)

        # P7: Scan-line sprite reveal
        if self._sprite_reveal_active and self._assembly_phase == 2:
            reveal_y = self._sprite_reveal_y - sprite_y
            if reveal_y > 0:
                # Draw only the revealed portion
                clip = QRegion(QRect(sprite_x, sprite_y, sprite_w, min(sprite_h, reveal_y)))
                painter.setClipRegion(clip)
                painter.drawPixmap(sprite_x, sprite_y, scaled)
                painter.setClipping(False)
                # Draw the scan line
                if reveal_y < sprite_h:
                    scan_line_y = sprite_y + reveal_y
                    painter.setPen(QPen(QColor(0, 212, 255, 180), 2))
                    painter.drawLine(sprite_x, scan_line_y, sprite_x + sprite_w, scan_line_y)
            else:
                # Not revealed yet
                pass
        else:
            # Normal full draw
            painter.drawPixmap(sprite_x, sprite_y, scaled)

        # Pulsing glow
        glow_pulse = 0.5 + 0.5 * math.sin(self._animation_elapsed / 300)
        glow_alpha = int(60 + 60 * glow_pulse)
        painter.setPen(QPen(QColor(0, 212, 255, glow_alpha), 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(sprite_x - 20, sprite_y - 20, sprite_w + 40, sprite_h + 40)

    def resizeEvent(self, event):
        if self._video_widget:
            self._video_widget.setGeometry(self.rect())
        super().resizeEvent(event)

    def keyPressEvent(self, event):
        if self._skip_allowed and not self._dance_complete:
            self._skip()
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if self._skip_allowed and not self._dance_complete:
            self._skip()
        elif not self._skip_allowed:
            log.debug("Skip click ignored — skip delay still active")
        super().mousePressEvent(event)

    def closeEvent(self, event):
        self._tick.stop()
        # Only _complete() used to stop this, so closing the screen early (e.g.
        # AtomIntroSequence.stop(), or the bootstrap timeout) left a 1 s timer
        # repainting a hidden widget for the rest of the session.
        self._stop_video_skip_timer()
        # The 60 s safety `_complete` was the worst offender: it kept the timer
        # alive for a minute after the intro had finished.
        self._deferred.stop_all()
        self._cleanup_video()
        event.accept()


# ════════════════════════════════════════════════════════════════════════════════
# AtomIntroSequence: Chains All 4 Screens
# ════════════════════════════════════════════════════════════════════════════════


    def _apply_window_flags(self) -> None:
        """Set window flags and show full-screen."""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())
        self.show()
        self.raise_()
        self.activateWindow()
        self._tick.start(33)

class _FadeOverlay(QWidget):
    """Full-screen black overlay for smooth cross-fade between intro screens."""
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setStyleSheet("background-color: #000000;")
        # Purely visual: it covers the whole screen during a cross-fade, and a
        # click arriving then must still reach the screen underneath.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        screen = QApplication.primaryScreen()
        if screen:
            self.setGeometry(screen.geometry())
        self.show()
        self.raise_()
        self.activateWindow()
        self._opacity = 0.0
        self._target = 0.0
        self._step = 0.0
        self._on_finished = None

    def paintEvent(self, event):
        painter = QPainter(self)
        alpha = int(255 * self._opacity)
        painter.fillRect(self.rect(), QColor(0, 0, 0, alpha))
        painter.end()

    def animate_to(self, target: float, duration_ms: int = 200, on_finished=None):
        start = self._opacity
        diff = target - start
        self._step = diff * (16.0 / duration_ms) if duration_ms > 0 else diff
        self._target = target
        self._on_finished = on_finished

    def tick(self) -> bool:
        remaining = self._target - self._opacity
        if abs(remaining) < 0.01:
            self._opacity = self._target
            self.update()
            if self._on_finished:
                fn = self._on_finished
                self._on_finished = None
                fn()
            return True
        self._opacity += self._step
        if (self._step > 0 and self._opacity > self._target) or (self._step < 0 and self._opacity < self._target):
            self._opacity = self._target
        self.update()
        if abs(self._target - self._opacity) < 0.01:
            self._opacity = self._target
            if self._on_finished:
                fn = self._on_finished
                self._on_finished = None
                fn()
            return True
        return False


class AtomIntroSequence(QObject):
    """Chains the 4 intro screens with a persistent animated character."""

    all_done = pyqtSignal()

    def _cleanup_sprite(self):
        """Stop the sprite animation timer."""
        if self._sprite_timer:
            self._sprite_timer.stop()
            self._sprite_timer = None

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self._fade_overlay: Optional[_FadeOverlay] = None
        self._fade_timer: Optional[QTimer] = None
        # Persistent animated character across all intro screens
        self._intro_sprite = None
        self._sprite_frame = 0
        self._sprite_timer = None
        self._sprite_pose = 'idle'
        self._sprite_emotion = 'neutral'
        # Initialised here as well as in start(): stop() is a teardown method and
        # must be safe to call even if the sequence never started.
        self._current_screen = None
        self._init_intro_sprite()

    def _init_intro_sprite(self):
        """Initialize the persistent animated character."""
        try:
            char = getattr(self.config, 'character', None) or 'atom'
            self._intro_sprite = MinecraftSprite3D(scale=2, character=char)
            self._sprite_timer = QTimer(self)
            self._sprite_timer.timeout.connect(self._tick_sprite)
            self._sprite_timer.start(33)
            log.info("Intro sprite: %s", char)
        except Exception as e:
            log.warning("Intro sprite failed: %s", e)
            self._intro_sprite = None

    def _tick_sprite(self):
        """Advance the sprite animation frame."""
        self._sprite_frame = (self._sprite_frame + 1) % 60
        if self._sprite_frame % 40 == 0:
            self._sprite_pose = random.choice(['wave', 'cheer', 'idle', 'look_around'])
        if self._sprite_frame % 60 == 0:
            self._sprite_emotion = random.choice(['neutral', 'happy', 'excited'])
        # Screen's own tick timer handles repaint; no need to call update() here

    def start(self):
        self._current_screen = None
        self._show_boot()

    def _fade_transition(self, next_creator, connect_fn=None):
        old_screen = self._current_screen
        self._fade_overlay = _FadeOverlay()
        self._fade_overlay.show()
        self._fade_overlay.raise_()
        self._fade_overlay.animate_to(1.0, 200,
            on_finished=lambda: self._on_fade_out_done(next_creator, old_screen, connect_fn))
        self._fade_timer = QTimer(self)
        self._fade_timer.timeout.connect(self._on_fade_tick)
        self._fade_timer.start(16)

    def _on_fade_tick(self):
        if self._fade_overlay:
            done = self._fade_overlay.tick()
            if done and self._fade_timer:
                self._fade_timer.stop()
                self._fade_timer = None

    def _on_fade_out_done(self, next_creator, old_screen, connect_fn):
        if old_screen:
            old_screen.hide()
            old_screen.close()
        self._current_screen = next_creator()
        self._current_screen.show()
        self._fade_overlay.raise_()
        self._fade_overlay.animate_to(0.0, 200,
            on_finished=lambda: self._on_fade_in_done(connect_fn))
        self._fade_timer = QTimer(self)
        self._fade_timer.timeout.connect(self._on_fade_tick)
        self._fade_timer.start(16)

    def _on_fade_in_done(self, connect_fn=None):
        # Connect signal now that screen is fully visible
        if connect_fn:
            try:
                connect_fn()
            except Exception:
                pass
        if self._fade_overlay:
            self._fade_overlay.hide()
            self._fade_overlay.close()
            self._fade_overlay = None
        if self._fade_timer:
            self._fade_timer.stop()
            self._fade_timer = None

    def _show_boot(self):
        self._current_screen = AtomBootScreen(self.config, intro_sprite=self._intro_sprite,
                                              sprite_frame_ref=lambda: self._sprite_frame,
                                              sprite_pose_ref=lambda: self._sprite_pose,
                                              sprite_emotion_ref=lambda: self._sprite_emotion)
        self._current_screen.boot_complete.connect(self._show_optimus)
        self._current_screen.show()

    def _show_optimus(self):
        self._fade_transition(
            lambda: AtomOptimusScreen(self.config, intro_sprite=self._intro_sprite,
                                        sprite_frame_ref=lambda: self._sprite_frame,
                                        sprite_pose_ref=lambda: self._sprite_pose,
                                        sprite_emotion_ref=lambda: self._sprite_emotion),
            connect_fn=lambda: self._current_screen.splash_complete.connect(self._show_welcome)
        )

    def _show_welcome(self):
        self._fade_transition(
            lambda: AtomWelcomeScreen(self.config, intro_sprite=self._intro_sprite,
                                        sprite_frame_ref=lambda: self._sprite_frame,
                                        sprite_pose_ref=lambda: self._sprite_pose,
                                        sprite_emotion_ref=lambda: self._sprite_emotion),
            connect_fn=lambda: self._current_screen.welcome_complete.connect(self._on_welcome_done)
        )

    def _on_welcome_done(self):
        """After the welcome screen: load Atom, or (without video) dance first.

        The welcome and dance screens both play ``assets/atom_intro.mp4``. When
        the welcome screen already played it, starting the dance screen replayed
        the whole video from the beginning instead of loading Atom — so with a
        video the sequence ends here.
        """
        screen = self._current_screen
        # ``_played_intro_video`` survives the cleanup that a skip performs;
        # ``_has_intro_video`` is the live flag and is checked as a fallback.
        played_video = bool(
            getattr(screen, "_played_intro_video", False)
            or getattr(screen, "_has_intro_video", False)
        )
        if played_video:
            log.info("Intro video finished — loading Atom (skipping the dance replay)")
            self._on_all_done()
            return
        self._show_dance()

    def _show_dance(self):
        self._fade_transition(
            lambda: AtomDanceScreen(self.config, intro_sprite=self._intro_sprite,
                                      sprite_frame_ref=lambda: self._sprite_frame,
                                      sprite_pose_ref=lambda: self._sprite_pose,
                                      sprite_emotion_ref=lambda: self._sprite_emotion),
            connect_fn=lambda: self._current_screen.startup_complete.connect(self._on_all_done)
        )

    def _on_all_done(self):
        if self._current_screen:
            self._current_screen.hide()
            self._current_screen.close()
            self._current_screen = None
        if self._fade_overlay:
            self._fade_overlay.hide()
            self._fade_overlay.close()
            self._fade_overlay = None
        self._cleanup_sprite()
        self.all_done.emit()

    def stop(self):
        if self._fade_timer:
            self._fade_timer.stop()
            self._fade_timer = None
        if self._fade_overlay:
            self._fade_overlay.hide()
            self._fade_overlay.close()
            self._fade_overlay = None
        if self._current_screen:
            self._current_screen.close()
            self._current_screen = None
        # _on_all_done() cleaned this up, but stop() is the other teardown path
        # (bootstrap timeout, early exit) and used to leave the 33 ms sprite
        # timer running for the rest of the session.
        self._cleanup_sprite()
