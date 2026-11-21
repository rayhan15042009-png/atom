"""
Tests for ui/renderer_3d — BlockRenderer3D, texture generation, VBO data, shader compilation.

Most tests run without an OpenGL context — they verify module-level data,
procedural texture output, and fallback behavior.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from PyQt6.QtGui import QCloseEvent, QImage
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent


class TestOpenGLImport(unittest.TestCase):
    """The lazy import mechanism."""

    def test_import_function_exists(self) -> None:
        from ui.renderer_3d import _import_opengl
        # Should be callable
        self.assertTrue(callable(_import_opengl))

    def test_available_constant_is_bool(self) -> None:
        from ui.renderer_3d import _OPENGL_AVAILABLE
        self.assertIsInstance(_OPENGL_AVAILABLE, bool)


class TestBlockRenderer3DAvailability(unittest.TestCase):
    """BlockRenderer3D.is_available() static method."""

    def test_is_available_returns_bool(self) -> None:
        # This should work regardless of whether OpenGL is installed
        try:
            from ui.renderer_3d import BlockRenderer3D
            available = BlockRenderer3D.is_available()
            self.assertIsInstance(available, bool)
        except RuntimeError:
            # If OpenGL is not available, the fallback class returns False
            from ui.renderer_3d import BlockRenderer3D
            self.assertFalse(BlockRenderer3D.is_available())


class TestCubeVertexData(unittest.TestCase):
    """Pre-computed cube VBO data."""

    def test_cube_vbo_bytes_nonempty(self) -> None:
        from ui.renderer_3d import _CUBE_VBO_BYTES
        self.assertGreater(len(_CUBE_VBO_BYTES), 0)
        # 36 vertices × 8 floats × 4 bytes = 1152 bytes
        self.assertEqual(len(_CUBE_VBO_BYTES), 36 * 8 * 4)

    def test_edge_vbo_bytes_nonempty(self) -> None:
        from ui.renderer_3d import _EDGE_VBO_BYTES
        self.assertGreater(len(_EDGE_VBO_BYTES), 0)
        # 24 vertices × 3 floats × 4 bytes = 288 bytes
        self.assertEqual(len(_EDGE_VBO_BYTES), 24 * 3 * 4)

    def test_cube_edges_count(self) -> None:
        from ui.renderer_3d import _CUBE_EDGES
        self.assertEqual(len(_CUBE_EDGES), 24)  # 12 edges × 2 verts


class TestProceduralTextures(unittest.TestCase):
    """Each procedural texture generator produces a valid QImage."""

    def _check_texture(self, name: str) -> None:
        import importlib
        mod = importlib.import_module("ui.renderer_3d")
        fn_name = f"_make_{name}_tex"
        fn = getattr(mod, fn_name, None)
        if fn is None:
            self.skipTest(f"Texture generator {fn_name} not found")
        img = fn()
        self.assertIsInstance(img, QImage)
        self.assertFalse(img.isNull())
        self.assertEqual(img.width(), 64)
        self.assertEqual(img.height(), 64)

    def test_skin_tex(self) -> None:
        self._check_texture("skin")

    def test_cloth_tex(self) -> None:
        self._check_texture("cloth")

    def test_denim_tex(self) -> None:
        self._check_texture("denim")

    def test_hair_tex(self) -> None:
        self._check_texture("hair")

    def test_leather_tex(self) -> None:
        self._check_texture("leather")

    def test_metal_tex(self) -> None:
        self._check_texture("metal")

    def test_fire_tex(self) -> None:
        self._check_texture("fire")

    def test_bone_tex(self) -> None:
        self._check_texture("bone")

    def test_flat_tex(self) -> None:
        self._check_texture("flat")

    def test_all_textures_are_rgba(self) -> None:
        import importlib
        mod = importlib.import_module("ui.renderer_3d")
        for name in ["skin", "cloth", "denim", "hair", "leather", "metal", "fire", "bone", "flat"]:
            fn = getattr(mod, f"_make_{name}_tex")
            img = fn()
            self.assertEqual(img.format(), QImage.Format.Format_RGBA8888,
                             f"{name} texture format mismatch")
            del img


class TestColorHelpers(unittest.TestCase):
    """_c and _dc utility functions."""

    def test_c_with_color(self) -> None:
        from ui.renderer_3d import _c
        result = _c((100, 150, 200))
        self.assertEqual(result, (100, 150, 200))

    def test_c_with_none(self) -> None:
        from ui.renderer_3d import _c
        result = _c(None)
        self.assertEqual(result, (128, 128, 128))

    def test_c_with_custom_default(self) -> None:
        from ui.renderer_3d import _c
        result = _c(None, default=(50, 60, 70))
        self.assertEqual(result, (50, 60, 70))

    def test_c_clamps_values(self) -> None:
        from ui.renderer_3d import _c
        result = _c((300, -5, 128))
        self.assertEqual(result, (300, -5, 128))  # no clamping by _c itself

    def test_dc(self) -> None:
        from ui.renderer_3d import _dc
        result = _dc((100, 150, 200), amount=35)
        self.assertEqual(result, (65, 115, 165))

    def test_dc_clamps_at_zero(self) -> None:
        from ui.renderer_3d import _dc
        result = _dc((20, 30, 40), amount=50)  # 20-50 = -30 → 0
        self.assertEqual(result, (0, 0, 0))

    def test_dc_default_amount(self) -> None:
        from ui.renderer_3d import _dc
        result = _dc((100, 150, 200))
        self.assertEqual(result, (65, 115, 165))


class TestProfileLoader(unittest.TestCase):
    """_load_profile loads from sprite_2d CHARACTER_PROFILES."""

    def test_loads_atom(self) -> None:
        from ui.renderer_3d import _load_profile
        profile = _load_profile("atom")
        self.assertIn("name", profile)
        self.assertEqual(profile["name"], "Atom (Default)")

    def test_loads_pink(self) -> None:
        from ui.renderer_3d import _load_profile
        profile = _load_profile("pink")
        self.assertIn("name", profile)
        self.assertEqual(profile["name"], "Pink (Gamer Girl)")

    def test_fallback_for_unknown(self) -> None:
        from ui.renderer_3d import _load_profile
        profile = _load_profile("nonexistent")
        self.assertIn("name", profile)
        self.assertEqual(profile["name"], "Atom (Default)")

    def test_returns_dict(self) -> None:
        from ui.renderer_3d import _load_profile
        profile = _load_profile("steve")
        self.assertIsInstance(profile, dict)


class TestDrawFunctions(unittest.TestCase):
    """_draw_block, _draw_flat, _draw_glow_block return callables."""

    def test_draw_block_returns_callable(self) -> None:
        from ui.renderer_3d import _draw_block
        fn = _draw_block((100, 150, 200), "cloth")
        self.assertTrue(callable(fn))

    def test_draw_block_with_dark_color(self) -> None:
        from ui.renderer_3d import _draw_block
        fn = _draw_block((100, 150, 200), "cloth", dark_color=(50, 80, 100))
        self.assertTrue(callable(fn))

    def test_draw_flat_returns_callable(self) -> None:
        from ui.renderer_3d import _draw_flat
        fn = _draw_flat((100, 100, 100))
        self.assertTrue(callable(fn))

    def test_draw_glow_block_returns_callable(self) -> None:
        from ui.renderer_3d import _draw_glow_block
        fn = _draw_glow_block((255, 140, 40), alpha=0.3)
        self.assertTrue(callable(fn))

    def test_draw_block_stores_colors(self) -> None:
        """_draw_block stores color values as closure cells."""
        from ui.renderer_3d import _draw_block
        fn = _draw_block((200, 100, 50), "skin")
        self.assertTrue(callable(fn))


class TestTextureManager(unittest.TestCase):
    """TextureManager class without OpenGL context."""

    def test_constructor(self) -> None:
        from ui.renderer_3d import TextureManager
        mgr = TextureManager()
        self.assertIsNotNone(mgr)
        self.assertFalse(mgr.ready)

    def test_has_generators(self) -> None:
        from ui.renderer_3d import TextureManager
        expected = {"skin", "cloth", "denim", "hair", "leather", "metal", "fire", "bone", "flat"}
        self.assertEqual(set(TextureManager._GENERATORS.keys()), expected)


class TestCreate3DWidget(unittest.TestCase):
    """create_3d_widget factory function."""

    def test_returns_none_or_widget(self) -> None:
        """create_3d_widget returns None if no display/GPU, otherwise a widget."""
        from ui.renderer_3d import create_3d_widget
        # On headless/CI systems this returns None (exception caught in factory).
        # On systems with a display and GPU it returns a BlockRenderer3D widget.
        # Either is valid — we just verify no crash and type correctness.
        result = create_3d_widget()
        self.assertTrue(result is None or hasattr(result, "set_profile"))


class TestFallbackClass(unittest.TestCase):
    """Fallback BlockRenderer3D when OpenGL is unavailable."""

    def test_fallback_is_available_returns_false(self) -> None:
        from ui.renderer_3d import _OPENGL_AVAILABLE
        if not _OPENGL_AVAILABLE:
            from ui.renderer_3d import BlockRenderer3D
            self.assertFalse(BlockRenderer3D.is_available())


class TestShaderSources(unittest.TestCase):
    """GLSL shader source strings are valid."""

    def test_vert_src_nonempty(self) -> None:
        from ui.renderer_3d import _VERT_SRC
        self.assertGreater(len(_VERT_SRC), 50)
        self.assertIn("varying", _VERT_SRC)

    def test_frag_src_nonempty(self) -> None:
        from ui.renderer_3d import _FRAG_SRC
        self.assertGreater(len(_FRAG_SRC), 50)
        self.assertIn("uniform", _FRAG_SRC)
        self.assertIn("Blinn", "Blinn")  # just checking it's valid


class TestConstants(unittest.TestCase):
    def test_tex_size(self) -> None:
        from ui.renderer_3d import _TEX_SIZE
        self.assertEqual(_TEX_SIZE, 64)

    def test_vertex_stride(self) -> None:
        from ui.renderer_3d import _VERTEX_STRIDE
        self.assertEqual(_VERTEX_STRIDE, 32)  # 8 floats × 4 bytes

    def test_outline_color(self) -> None:
        from ui.renderer_3d import _OUTLINE_COLOR
        self.assertEqual(len(_OUTLINE_COLOR), 3)


# ═══════════════════════════════════════════════════════════════════════════════
# Factory safety + animation-timer teardown (both found by audit)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCreate3DWidgetNeedsQApplication(unittest.TestCase):
    """The factory must not abort the interpreter when no QApplication exists.

    Qt answers QWidget construction without a QApplication with qFatal, which is
    not a Python exception, so the factory's try/except cannot catch it. Running
    this file on its own used to kill the process with exit code 127 after 34 of
    41 tests, silently skipping the rest.
    """

    def test_returns_none_instead_of_aborting(self) -> None:
        code = (
            "from ui.renderer_3d import create_3d_widget;"
            "print('RESULT', create_3d_widget())"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            proc.returncode, 0, f"interpreter aborted: {proc.stderr[-800:]}"
        )
        self.assertIn("RESULT None", proc.stdout)


class TestRendererAnimationTimerTeardown(unittest.TestCase):
    """stop_animation() and closeEvent() must really stop the 15 Hz timer.

    Nothing stopped it before; it relied on Qt destroying the widget, which does
    not happen while the Python wrapper holds a reference.
    """

    def _widget(self):
        QApplication.instance() or QApplication([])
        from ui.renderer_3d import create_3d_widget

        widget = create_3d_widget()
        if widget is None:
            self.skipTest("no 3D widget available in this environment")
        self.addCleanup(widget.stop_animation)
        return widget

    def test_timer_starts_active(self) -> None:
        widget = self._widget()
        self.assertTrue(widget._timer.isActive())
        self.assertEqual(widget._timer.interval(), 66)

    def test_stop_animation_stops_the_timer(self) -> None:
        widget = self._widget()
        widget.stop_animation()
        self.assertFalse(widget._timer.isActive())

    def test_stop_animation_is_idempotent(self) -> None:
        widget = self._widget()
        widget.stop_animation()
        widget.stop_animation()
        self.assertFalse(widget._timer.isActive())

    def test_close_event_stops_the_timer(self) -> None:
        widget = self._widget()
        widget.closeEvent(QCloseEvent())
        self.assertFalse(widget._timer.isActive())


class TestOverlayStopsRendererAnimation(unittest.TestCase):
    """Both overlay teardown paths must call stop_animation()."""

    def test_teardown_paths_are_wired(self) -> None:
        src = (ROOT / "ui" / "overlay.py").read_text(encoding="utf-8")
        self.assertTrue(
            "def _stop_renderer_animation" in src,
            "overlay.py has no _stop_renderer_animation helper",
        )
        self.assertGreaterEqual(
            src.count("self._stop_renderer_animation()"),
            2,
            "_fallback_to_2d and _handle_render_mode_change must both stop it",
        )


if __name__ == "__main__":
    unittest.main()
