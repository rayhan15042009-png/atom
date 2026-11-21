"""
Tests for ui/intro_utils.py — shared intro HUD utilities, system checks, hex grid, particles.
"""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap, QPainter, QFont
from PyQt6.QtWidgets import QApplication

# Ensure QApplication exists for QPainter operations
_APP = QApplication.instance() or QApplication([])

from ui.intro_utils import (
    ASSET_PATHS,
    HUD_CYAN, HUD_CYAN_DIM, HUD_RED, HUD_AMBER, HUD_GREEN,
    HUD_WHITE, HUD_DARK, HUD_SURFACE, HUD_TEXT, HUD_TEXT_DIM,
    CHECKS, SKIP_DELAY_MS,
    draw_hud_frame, draw_hud_corner_text,
    draw_hex_grid_pixmap, HexGridCache,
    draw_background_glow, draw_crt_scanlines,
    draw_floating_particles, create_particles, update_particles,
    draw_skip_button, draw_status_bar,
    _check_packages, _check_ollama_connection, _check_ollama_models,
    _check_tesseract, _check_config, _check_memory, _check_assets, _check_internet,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Test: Module constants
# ═══════════════════════════════════════════════════════════════════════════════

class TestConstants(unittest.TestCase):
    """Test module-level constants."""

    def test_skip_delay_1000ms(self):
        """SKIP_DELAY_MS is 1000 (1 second per build order)."""
        self.assertEqual(SKIP_DELAY_MS, 1000)

    def test_chceks_non_empty(self):
        """CHECKS is a non-empty list of (label, callable) tuples."""
        self.assertTrue(len(CHECKS) > 0)
        for label, fn in CHECKS:
            self.assertIsInstance(label, str)
            self.assertTrue(callable(fn))

    def test_hud_colors(self):
        """All HUD color constants are QColor instances."""
        colors = [HUD_CYAN, HUD_CYAN_DIM, HUD_RED, HUD_AMBER,
                  HUD_GREEN, HUD_WHITE, HUD_DARK, HUD_SURFACE,
                  HUD_TEXT, HUD_TEXT_DIM]
        for c in colors:
            self.assertIsInstance(c, QColor)


# ═══════════════════════════════════════════════════════════════════════════════
# Test: System check functions
# ═══════════════════════════════════════════════════════════════════════════════

class TestSystemChecks(unittest.TestCase):
    """Test all 8 system check functions."""

    def test_check_packages(self):
        """_check_packages returns a (bool, str) tuple."""
        ok, msg = _check_packages()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(msg, str)
        self.assertGreater(len(msg), 0)

    def test_check_ollama_connection(self):
        """_check_ollama_connection returns a (bool, str) tuple."""
        ok, msg = _check_ollama_connection()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(msg, str)

    def test_check_ollama_models(self):
        """_check_ollama_models returns a (bool, str) tuple."""
        ok, msg = _check_ollama_models()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(msg, str)

    def test_check_tesseract(self):
        """_check_tesseract returns a (bool, str) tuple."""
        ok, msg = _check_tesseract()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(msg, str)

    def test_check_config(self):
        """_check_config returns a (bool, str) tuple."""
        ok, msg = _check_config()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(msg, str)

    def test_check_memory(self):
        """_check_memory returns a (bool, str) tuple."""
        ok, msg = _check_memory()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(msg, str)

    def test_check_assets(self):
        """_check_assets returns a (bool, str) tuple."""
        ok, msg = _check_assets()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(msg, str)

    def test_every_listed_asset_exists_on_disk(self):
        """A listed-but-absent asset is a permanent warning on the boot screen.

        ``assets/atom_arena.mp3`` was on this list and does not exist (nothing
        plays it), so the boot screen reported "4/5 assets present" every launch.
        """
        missing = [p.name for p in ASSET_PATHS if not p.exists()]
        self.assertEqual(missing, [], f"boot screen would flag missing assets: {missing}")

    def test_the_boot_screen_reports_all_assets_present(self):
        ok, msg = _check_assets()
        self.assertTrue(ok)
        self.assertEqual(msg, "all assets present")

    def test_the_shipped_character_skins_are_covered(self):
        """A character whose skin went missing should be noticed at boot."""
        names = {p.name for p in ASSET_PATHS}
        for skin in ("atom_skin.png", "pink_skin.png", "steve_skin.png",
                     "alex_skin.png", "ghost_skin.png", "gamergirl.png"):
            with self.subTest(skin=skin):
                self.assertIn(skin, names)

    def test_both_copies_of_the_check_agree(self):
        """ui/intro.py used to keep its own copy of ASSET_PATHS and drifted."""
        from ui.intro import _check_assets as live_check
        self.assertEqual(live_check(), _check_assets())

    def test_the_package_check_covers_opencv(self):
        """cv2 is a real dependency (core/eyes.py OCR) and was missing here."""
        import ui.intro_utils as iu
        src = inspect.getsource(iu._check_packages)
        self.assertIn("'cv2'", src)

    def test_check_internet(self):
        """_check_internet returns a (bool, str) tuple."""
        ok, msg = _check_internet()
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(msg, str)


# ═══════════════════════════════════════════════════════════════════════════════
# Test: HUD drawing functions
# ═══════════════════════════════════════════════════════════════════════════════

class TestHudDrawing(unittest.TestCase):
    """Test HUD drawing utility functions (render to QPixmap, no crash)."""

    def setUp(self):
        self.pix = QPixmap(200, 200)
        self.pix.fill(Qt.GlobalColor.black)
        self.painter = QPainter(self.pix)

    def tearDown(self):
        self.painter.end()

    def test_draw_hud_frame(self):
        """draw_hud_frame does not crash."""
        draw_hud_frame(self.painter, 200, 200)
        self.assertFalse(self.pix.isNull())

    def test_draw_hud_frame_custom_colors(self):
        """draw_hud_frame works with custom colors."""
        draw_hud_frame(self.painter, 200, 200, color=HUD_RED, dim_color=QColor(255, 0, 0, 30))
        self.assertFalse(self.pix.isNull())

    def test_draw_hud_corner_text(self):
        """draw_hud_corner_text does not crash."""
        draw_hud_corner_text(self.painter, 200, 200)
        self.assertFalse(self.pix.isNull())

    def test_draw_hud_corner_text_custom(self):
        """draw_hud_corner_text works with custom titles."""
        draw_hud_corner_text(self.painter, 200, 200,
                            left_title="TEST.SYS", left_sub="TESTING",
                            right_title="12:00", right_sub="READY")

    def test_draw_background_glow(self):
        """draw_background_glow does not crash."""
        draw_background_glow(self.painter, 200, 200)

    def test_draw_crt_scanlines(self):
        """draw_crt_scanlines does not crash."""
        draw_crt_scanlines(self.painter, 200, 200, elapsed_ms=100)

    def test_draw_floating_particles(self):
        """draw_floating_particles does not crash with empty list."""
        draw_floating_particles(self.painter, 200, 200, [])

    def test_draw_floating_particles_with_data(self):
        """draw_floating_particles renders visible particles."""
        particles = [{'x': 100, 'y': 100, 'alpha': 100, 'size': 2}]
        draw_floating_particles(self.painter, 200, 200, particles)
        self.assertFalse(self.pix.isNull())

    def test_draw_skip_button(self):
        """draw_skip_button does not crash."""
        draw_skip_button(self.painter, 200, 200, 100)

    def test_draw_skip_button_not_allowed(self):
        """draw_skip_button is no-op when not allowed."""
        draw_skip_button(self.painter, 200, 200, 100, skip_allowed=False)

    def test_draw_status_bar(self):
        """draw_status_bar does not crash."""
        draw_status_bar(self.painter, 10, 180, "TEST", 0.5)
        self.assertFalse(self.pix.isNull())

    def test_draw_status_bar_full(self):
        """draw_status_bar at 100% fill."""
        draw_status_bar(self.painter, 10, 180, "TEST", 1.0)

    def test_draw_status_bar_zero(self):
        """draw_status_bar at 0% fill."""
        draw_status_bar(self.painter, 10, 180, "TEST", 0.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Test: Hex grid
# ═══════════════════════════════════════════════════════════════════════════════

class TestHexGrid(unittest.TestCase):
    """Test hex grid generation and caching."""

    def test_draw_hex_grid_pixmap(self):
        """draw_hex_grid_pixmap returns a non-null QPixmap."""
        pix = draw_hex_grid_pixmap(200, 200)
        self.assertIsInstance(pix, QPixmap)
        self.assertFalse(pix.isNull())

    def test_draw_hex_grid_pixmap_size(self):
        """draw_hex_grid_pixmap returns correct dimensions."""
        pix = draw_hex_grid_pixmap(300, 400)
        self.assertEqual(pix.width(), 300)
        self.assertEqual(pix.height(), 400)

    def test_hex_grid_cache_initial_null(self):
        """HexGridCache starts with a null pixmap."""
        cache = HexGridCache()
        self.assertTrue(cache._pixmap.isNull())

    def test_hex_grid_cache_generates(self):
        """HexGridCache.get() generates a pixmap on first call."""
        cache = HexGridCache()
        pix = cache.get(200, 200)
        self.assertFalse(pix.isNull())

    def test_hex_grid_cache_reuses(self):
        """HexGridCache reuses pixmap for same size."""
        cache = HexGridCache()
        pix1 = cache.get(200, 200)
        pix2 = cache.get(200, 200)
        self.assertIs(pix1, pix2)

    def test_hex_grid_cache_invalidates(self):
        """HexGridCache.invalidate() resets the cache."""
        cache = HexGridCache()
        cache.get(200, 200)
        cache.invalidate()
        self.assertTrue(cache._pixmap.isNull())
        self.assertEqual(cache._last_size, (0, 0))


# ═══════════════════════════════════════════════════════════════════════════════
# Test: Particles
# ═══════════════════════════════════════════════════════════════════════════════

class TestParticles(unittest.TestCase):
    """Test particle creation and update."""

    def test_create_particles_count(self):
        """create_particles returns the requested count."""
        particles = create_particles(40)
        self.assertEqual(len(particles), 40)

    def test_create_particles_default_count(self):
        """create_particles defaults to 30."""
        particles = create_particles()
        self.assertEqual(len(particles), 30)

    def test_create_particles_structure(self):
        """Each particle has the required fields."""
        particles = create_particles(5)
        required = {'x', 'y', 'speed', 'alpha', 'size'}
        for p in particles:
            self.assertTrue(required.issubset(p.keys()))

    def test_update_particles_no_crash(self):
        """update_particles handles empty list."""
        update_particles([], 200, 200)

    def test_update_particles_moves(self):
        """update_particles moves particles left."""
        particles = [{'x': 100, 'y': 100, 'speed': 0.5, 'alpha': 50, 'size': 1}]
        update_particles(particles, 200, 200)
        self.assertLess(particles[0]['x'], 100)

    def test_update_particles_wraps(self):
        """update_particles wraps around when offscreen."""
        particles = [{'x': 5, 'y': 100, 'speed': 20, 'alpha': 50, 'size': 1}]
        update_particles(particles, 200, 200)
        self.assertGreater(particles[0]['x'], 100)


# ═══════════════════════════════════════════════════════════════════════════════
# Test: Check functions return consistent types
# ═══════════════════════════════════════════════════════════════════════════════

class TestCheckReturnTypes(unittest.TestCase):
    """All check functions return consistent types."""

    def test_all_checks_return_tuple(self):
        """All CHECKS functions return (bool, str)."""
        for _, fn in CHECKS:
            ok, msg = fn()
            self.assertIsInstance(ok, bool)
            self.assertIsInstance(msg, str)
            self.assertGreater(len(msg), 0)


if __name__ == "__main__":
    unittest.main()
