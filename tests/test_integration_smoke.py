"""Integration smoke test — verifies full application startup path.

Tests:
- Module imports succeed
- AtomApp can be instantiated
- All components (overlay, brain, eyes, voice, anim_machine, hands, chat_controller) are created
- Signal wiring is complete (no NameError on signal references)
- Workers can start() and stop()
- Basic API calls work (mode, pose, emotion, speech)

This test does NOT require a display or GPU — all Qt widget calls
are patched to avoid crashes on headless/CI systems.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path
from typing import Any, Optional

# ── Disable OpenGL surface format (must be BEFORE importing atom.py) ──
os.environ["QT_QPA_PLATFORM"] = "offscreen"

# Patch QSurfaceFormat before any atom imports
import PyQt6.QtGui as _qtgui
_orig_format = _qtgui.QSurfaceFormat


class _MockSurfaceFormat:
    # Enum class needed by atom.py: QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile
    class OpenGLContextProfile:
        CompatibilityProfile = 2
        CoreProfile = 0
        NoProfile = 1

    @staticmethod
    def setAlphaBufferSize(n: int) -> None: pass
    @staticmethod
    def setSamples(n: int) -> None: pass
    @staticmethod
    def setSwapInterval(n: int) -> None: pass
    @staticmethod
    def setDepthBufferSize(n: int) -> None: pass
    @staticmethod
    def setStencilBufferSize(n: int) -> None: pass
    @staticmethod
    def setProfile(p: Any) -> None: pass
    @staticmethod
    def setDefaultFormat(fmt: Any) -> None: pass


# Apply patch BEFORE atom.py is imported — atom.py does QSurfaceFormat setup at module level
_patcher = patch("PyQt6.QtGui.QSurfaceFormat", _MockSurfaceFormat)
_patcher.start()


# ═══════════════════════════════════════════════════════════════════════
# Helper: create a QApplication once per test session
# ═══════════════════════════════════════════════════════════════════════

def _ensure_qapp() -> Any:
    """Create QApplication if one doesn't exist (offscreen platform)."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1] if sys.argv else [""])
    return app


# ═══════════════════════════════════════════════════════════════════════
# Test classes
# ═══════════════════════════════════════════════════════════════════════

class TestImports(unittest.TestCase):
    """Verify all modules can be imported without errors."""

    def test_core_imports(self) -> None:
        """Import all core modules."""
        import core.config_manager
        import core.brain
        import core.eyes
        import core.voice
        import core.hands
        import core.logger
        import core.model_loader
        import core.installer
        import core.skin_loader
        import core.sandbox
        import core.web_tools
        import core.browser_brain
        self.assertTrue(callable(core.brain.get_system_prompt))

    def test_ui_imports(self) -> None:
        """Import all ui modules."""
        import ui.sprite_2d
        import ui.animation_machine
        import ui.hud
        import ui.overlay
        import ui.intro
        import ui.intro_utils
        import ui.renderer_3d
        self.assertTrue(hasattr(ui.sprite_2d, "Sprite2D"))
        self.assertTrue(hasattr(ui.animation_machine, "AnimationMachine"))

    def test_atom_import(self) -> None:
        """Import atom.py main module."""
        import atom
        self.assertTrue(hasattr(atom, "AtomApp"))
        self.assertTrue(hasattr(atom, "InputHandler"))
        self.assertTrue(hasattr(atom, "ChatController"))
        self.assertTrue(callable(atom.main))


class TestAtomAppConstruction(unittest.TestCase):
    """Verify AtomApp can be instantiated and components are created."""

    @classmethod
    def setUpClass(cls) -> None:
        _ensure_qapp()

    def test_construction(self) -> None:
        """Test AtomApp construction creates all components.

        Uses patch on atom.AtomOverlay (not ui.overlay.AtomOverlay) because
        atom.py imports components by value (from ui.overlay import AtomOverlay).
        """
        from core.config_manager import AppConfig
        config = AppConfig()
        config.intro.enabled = False

        import atom as _atom_module

        with patch.object(_atom_module, "QApplication") as mock_qapp:
            mock_qapp_instance = MagicMock()
            mock_qapp.return_value = mock_qapp_instance
            mock_qapp_instance.primaryScreen.return_value = MagicMock()
            mock_qapp_instance.primaryScreen.return_value.geometry.return_value = MagicMock()

            with patch.object(_atom_module, "AtomOverlay") as mock_overlay:
                with patch.object(_atom_module, "BrainThread") as mock_brain:
                    with patch.object(_atom_module, "PerceptionWorker") as mock_eyes:
                        with patch.object(_atom_module, "VoiceThread") as mock_voice:
                            with patch.object(_atom_module, "AnimationMachine") as mock_anim:
                                with patch.object(_atom_module, "ActionExecutor") as mock_hands:
                                    with patch.object(_atom_module, "load_config") as mock_lc:
                                        with patch.object(_atom_module, "load_memory") as mock_lm:
                                            with patch.object(_atom_module, "save_memory"):
                                                mock_lc.return_value = config
                                                mock_lm.return_value = {"facts": []}

                                                app = _atom_module.AtomApp([""])

                                                # Verify components were created
                                                # overlay/hands are deferred to start() — None after __init__
                                                self.assertIsNone(app.overlay)
                                                self.assertIsNotNone(app.chat_controller)
                                                self.assertIsNotNone(app.anim_machine)
                                                self.assertIsNone(app.hands)
                                                self.assertIsNotNone(app.brain)
                                                self.assertIsNotNone(app.eyes)
                                                self.assertIsNotNone(app.voice)

                                                mock_lc.assert_called_once()
                                                mock_lm.assert_called_once()
                                                mock_overlay.assert_not_called()  # deferred to start()
                                                mock_anim.assert_called_once()
                                                mock_hands.assert_not_called()  # deferred to start()
                                                mock_brain.assert_called_once()
                                                mock_eyes.assert_called_once()
                                                mock_voice.assert_called_once()


class TestSignalWiring(unittest.TestCase):
    """Verify signals are properly connected between components."""

    @classmethod
    def setUpClass(cls) -> None:
        _ensure_qapp()

    def setUp(self) -> None:
        from core.config_manager import AppConfig
        self.config = AppConfig()
        self.config.intro.enabled = False
        self._patches: list = []
        self._pobjects: list = []

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._patches.clear()

    def _start_patch(self, target: str) -> MagicMock:
        """Start a patch that stays active until tearDown."""
        p = patch(target)
        mock_obj = p.start()
        self._patches.append(p)
        return mock_obj

    def _make_app_with_patches(self, mod: Any) -> tuple[Any, dict[str, MagicMock]]:
        """Create AtomApp with active patches that remain available after creation.

        Returns (app, mocks_dict) where mocks_dict contains the mock instances
        for verification.
        """
        from core.config_manager import AppConfig

        # Start patches that stay active until tearDown
        mock_qapp = self._start_patch(f"{mod.__name__}.QApplication")
        mock_overlay = self._start_patch(f"{mod.__name__}.AtomOverlay")
        mock_brain = self._start_patch(f"{mod.__name__}.BrainThread")
        mock_eyes = self._start_patch(f"{mod.__name__}.PerceptionWorker")
        mock_voice = self._start_patch(f"{mod.__name__}.VoiceThread")
        mock_anim = self._start_patch(f"{mod.__name__}.AnimationMachine")
        mock_hands = self._start_patch(f"{mod.__name__}.ActionExecutor")
        mock_lc = self._start_patch(f"{mod.__name__}.load_config")
        mock_lm = self._start_patch(f"{mod.__name__}.load_memory")
        self._start_patch(f"{mod.__name__}.save_memory")

        mock_lc.return_value = self.config
        mock_lm.return_value = {"facts": []}
        mock_qapp.return_value = MagicMock()

        app = mod.AtomApp([""])
        app.start()

        mocks = {
            "overlay": mock_overlay,
            "brain": mock_brain,
            "anim": mock_anim,
            "eyes": mock_eyes,
            "voice": mock_voice,
            "hands": mock_hands,
        }
        return app, mocks

    def test_overlay_to_chat_controller_signals(self) -> None:
        """Verify overlay signals are wired to chat_controller."""
        import atom as _atom_module

        app, mocks = self._make_app_with_patches(_atom_module)
        overlay_mock = mocks["overlay"]

        # overlay_mock is the patched class; overlay_mock.return_value is the instance
        # _setup_signals calls self.overlay.user_input.connect(...) on the instance
        self.assertTrue(overlay_mock.return_value.user_input.connect.called)
        self.assertTrue(overlay_mock.return_value.settings_changed.connect.called)
        self.assertTrue(overlay_mock.return_value.stop_requested.connect.called)

    def test_brain_signals(self) -> None:
        """Verify brain signals are connected."""
        import atom as _atom_module

        app, mocks = self._make_app_with_patches(_atom_module)
        brain_mock = mocks["brain"]

        self.assertTrue(brain_mock.return_value.response_ready.connect.called)
        self.assertTrue(brain_mock.return_value.error.connect.called)

    def test_animation_signals(self) -> None:
        """Verify animation machine signals are connected."""
        import atom as _atom_module

        app, mocks = self._make_app_with_patches(_atom_module)
        anim_mock = mocks["anim"]

        self.assertTrue(anim_mock.return_value.state_changed.connect.called)
        self.assertTrue(anim_mock.return_value.emotion_changed.connect.called)
        self.assertTrue(anim_mock.return_value.jump_triggered.connect.called)
        self.assertTrue(anim_mock.return_value.animation_started.connect.called)
        self.assertTrue(anim_mock.return_value.animation_finished.connect.called)


class TestWorkerLifecycle(unittest.TestCase):
    """Verify workers can be started and stopped."""

    _threads: list = []

    @classmethod
    def setUpClass(cls) -> None:
        _ensure_qapp()

    def setUp(self) -> None:
        from core.config_manager import AppConfig
        self.config = AppConfig()
        self._threads = []

    def tearDown(self) -> None:
        """Stop any threads that might still be running."""
        for t in self._threads:
            if t.isRunning():
                try:
                    t.stop()
                    t.wait(1000)
                except Exception:
                    t.terminate()
                    t.wait(500)

    def _run_lifecycle(self, thread_cls: type) -> None:
        """Generic start/stop lifecycle test for a worker thread.

        QThread.wait(ms) waits for the thread to FINISH, not to start.
        After start(), isRunning() returns True immediately.
        """
        thread = thread_cls(self.config)
        self._threads.append(thread)
        self.assertFalse(thread.isRunning())
        thread.start()
        self.assertTrue(thread.isRunning())
        thread.stop()
        self.assertTrue(thread.wait(2000))
        self.assertFalse(thread.isRunning())

    def test_brain_thread_lifecycle(self) -> None:
        """Test BrainThread start/stop lifecycle."""
        from core.brain import BrainThread
        self._run_lifecycle(BrainThread)

    def test_voice_thread_lifecycle(self) -> None:
        """Test VoiceThread start/stop lifecycle."""
        from core.voice import VoiceThread
        self._run_lifecycle(VoiceThread)

    def test_perception_worker_lifecycle(self) -> None:
        """Test PerceptionWorker start/stop lifecycle."""
        from core.eyes import PerceptionWorker
        self._run_lifecycle(PerceptionWorker)


class TestAnimationMachineAPI(unittest.TestCase):
    """Verify AnimationMachine public API works."""

    def setUp(self) -> None:
        _ensure_qapp()
        from ui.animation_machine import AnimationMachine
        self.am = AnimationMachine()

    def test_initial_state(self) -> None:
        """Verify initial state is IDLE with NEUTRAL emotion."""
        from ui.animation_machine import AnimationState, Emotion
        self.assertEqual(self.am.current_state, AnimationState.IDLE)
        self.assertEqual(self.am.current_emotion, Emotion.NEUTRAL)

    def test_set_emotion(self) -> None:
        """Verify emotion setting works.

        Note: _apply_emotion() is called from _tick(), not from set_emotion().
        We call _tick() manually to trigger the speed scale update.
        """
        from ui.animation_machine import Emotion
        self.am.set_emotion(Emotion.HAPPY, duration=2.0)
        self.assertEqual(self.am.current_emotion, Emotion.HAPPY)
        # Tick triggers _apply_emotion() which updates emotion_speed_scale
        self.am._tick()
        self.assertAlmostEqual(self.am.emotion_speed_scale, 1.15, places=2)

    def test_signal_wave(self) -> None:
        """Verify wave signal triggers state transition."""
        self.am.signal_wave()
        # After tick, wave flag should cause transition
        self.am._tick_timer.timeout.disconnect()  # stop auto-tick
        self.am._tick()  # manual tick
        self.assertNotEqual(self.am.current_state.value, "idle")

    @patch("ui.animation_machine.datetime")
    @patch("ui.animation_machine.random", MagicMock())
    def test_parse_emotion_from_text(self, mock_dt: MagicMock) -> None:
        """Verify emotional text parsing works.

        Mocks datetime to return hour=0 (midnight, before 1AM cutoff where
        hour >= 1) and random to avoid the sleepy late-night trigger.
        """
        from ui.animation_machine import Emotion
        # hour=0 ensures `0 >= 1` is False, bypassing the sleepy trigger
        mock_dt.datetime.now.return_value.hour = 0
        self.am.parse_emotion_from_text("thanks good girl")
        self.assertEqual(self.am.current_emotion, Emotion.HAPPY)

    def test_boredom_tracking(self) -> None:
        """Verify boredom tiers escalate with idle time."""
        self.am._idle_timer = 999  # Force idle time high
        self.am._tick()
        self.assertGreaterEqual(self.am.get_boredom_tier(), 1)

    def test_micro_interactions(self) -> None:
        """Verify micro-interactions can be triggered."""
        self.am._idle_timer = 999
        self.am._last_micro = -999
        self.am._tick()
        # Force micro attempt
        self.am._try_micro()
        micro = self.am.get_micro_interaction()
        self.assertIn(micro, [""] + self.am._MICRO_INTERACTIONS)


class TestSprite2DAPI(unittest.TestCase):
    """Verify Sprite2D public API works."""

    def setUp(self) -> None:
        _ensure_qapp()

    def test_sprite_creation(self) -> None:
        """Verify Sprite2D can be created with default character."""
        from ui.sprite_2d import Sprite2D
        sprite = Sprite2D(scale=2, character="atom")
        self.assertEqual(sprite.character, "atom")
        self.assertEqual(sprite.width, 96)   # 48 * 2
        self.assertEqual(sprite.height, 148) # 74 * 2

    def test_character_switch(self) -> None:
        """Verify character switching works for all profiles."""
        from ui.sprite_2d import Sprite2D, CHARACTER_PROFILES
        sprite = Sprite2D(scale=2, character="atom")
        for char in CHARACTER_PROFILES:
            sprite.set_character(char)
            self.assertEqual(sprite.character, char)

    def test_mood_speed(self) -> None:
        """Verify mood speed clamping."""
        from ui.sprite_2d import Sprite2D
        sprite = Sprite2D()
        sprite.set_mood_speed(0.5)
        self.assertAlmostEqual(sprite.mood_speed, 0.5)
        sprite.set_mood_speed(5.0)
        self.assertAlmostEqual(sprite.mood_speed, 2.0)  # Clamped
        sprite.set_mood_speed(0.1)
        self.assertAlmostEqual(sprite.mood_speed, 0.3)  # Clamped

    def test_pose_normalization(self) -> None:
        """Verify unknown poses fall back to 'idle'."""
        from ui.sprite_2d import Sprite2D
        self.assertEqual(Sprite2D.normalise_pose("nonexistent"), "idle")
        self.assertEqual(Sprite2D.normalise_pose("walk"), "walk")
        self.assertEqual(Sprite2D.normalise_pose("idle"), "idle")


class TestBrainAPI(unittest.TestCase):
    """Verify brain module public API works."""

    def test_get_system_prompt(self) -> None:
        """Verify system prompt selection by mode."""
        from core.brain import get_system_prompt
        default = get_system_prompt("tap")
        formal = get_system_prompt("formal")
        self.assertIn("alakazam bitch", default.lower())
        self.assertIn("*tap*", formal)

    def test_strip_preamble(self) -> None:
        """Verify preamble stripping removes AI boilerplate."""
        from core.brain import strip_preamble
        text = strip_preamble("Sure, here's how to do that")
        self.assertNotIn("Sure,", text)

    def test_is_boring(self) -> None:
        """Verify boring detection catches corporate speak."""
        from core.brain import is_boring
        self.assertTrue(is_boring("As an AI language model, I cannot do that"))
        self.assertFalse(is_boring("*tap* Let's hack some stuff!"))

    def test_validate_gemini_key(self) -> None:
        """Verify Gemini key validation."""
        from core.brain import validate_gemini_key
        self.assertIsNotNone(validate_gemini_key(""))  # Empty
        self.assertIsNotNone(validate_gemini_key("ya29.abc"))  # OAuth
        self.assertIsNone(validate_gemini_key("AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ123456"))  # Valid

    def test_get_gemini_keys(self) -> None:
        """Verify Gemini key extraction works with dict config."""
        from core.brain import get_gemini_keys
        cfg = {"gemini_api_key": "key1", "gemini_api_key_2": "key2", "gemini_api_key_5": "key5"}
        keys = get_gemini_keys(cfg)
        self.assertIn("key1", keys)
        self.assertIn("key2", keys)
        self.assertIn("key5", keys)

    def test_fallback_response(self) -> None:
        """Verify fallback response generation."""
        from core.brain import generate_fallback_response
        resp = generate_fallback_response("hello", mode="tap")
        self.assertTrue(resp.startswith("*tap*"))
        resp_f = generate_fallback_response("hello", mode="formal")
        self.assertTrue(resp_f.startswith("*tap*"))


class TestConfigManagerAPI(unittest.TestCase):
    """Verify config_manager public API works."""

    def test_app_config_defaults(self) -> None:
        """Verify AppConfig has sensible defaults."""
        from core.config_manager import AppConfig
        cfg = AppConfig()
        self.assertEqual(cfg.mode, "tap")
        self.assertEqual(cfg.character, "atom")
        self.assertTrue(cfg.tts_enabled)
        self.assertEqual(cfg.llm_provider, "ollama")

    def test_app_config_dict_access(self) -> None:
        """Verify dict-style access works on AppConfig."""
        from core.config_manager import AppConfig
        cfg = AppConfig()
        self.assertEqual(cfg.get("mode"), "tap")
        self.assertEqual(cfg.get("nonexistent", "default"), "default")
        self.assertIn("mode", cfg)

    def test_app_config_validation(self) -> None:
        """Verify validation catches bad values."""
        from core.config_manager import AppConfig
        cfg = AppConfig()
        errors = cfg.validate()
        self.assertEqual(len(errors), 0)  # Defaults should be valid
        cfg.mode = "invalid_mode"
        errors = cfg.validate()
        self.assertGreater(len(errors), 0)

    def test_config_round_trip(self) -> None:
        """Verify to_dict -> from_dict round trip."""
        from core.config_manager import AppConfig
        cfg = AppConfig()
        cfg.mode = "stalk"
        cfg.character = "ghost_rider"

        # Use the actual serialization via to_dict + load_config's internal path
        d = cfg.to_dict()
        self.assertEqual(d.get("mode"), "stalk")
        self.assertEqual(d.get("character"), "ghost_rider")

    def test_memory_default(self) -> None:
        """Verify AppMemory defaults."""
        from core.config_manager import AppMemory
        mem = AppMemory()
        self.assertEqual(mem.facts, [])
        self.assertEqual(mem.session_count, 0)


class TestBrowserBrainAPI(unittest.TestCase):
    """Verify browser_brain module public API works."""

    def test_browser_worker_creation(self) -> None:
        """Verify BrowserWorker can be created with config."""
        from core.browser_brain import BrowserWorker
        worker = BrowserWorker({"browser_ai_url": "https://chat.deepseek.com/"})
        self.assertIsNotNone(worker)

    def test_platform_detection(self) -> None:
        """Verify URL-based platform detection."""
        from core.browser_brain import BrowserWorker
        worker = BrowserWorker({"browser_ai_url": "https://chat.deepseek.com/"})
        self.assertEqual(worker._detect_platform(), "deepseek")
        worker2 = BrowserWorker({"browser_ai_url": "https://gemini.google.com/app"})
        self.assertEqual(worker2._detect_platform(), "gemini")
        worker3 = BrowserWorker({"browser_ai_url": "https://example.com/chat"})
        self.assertEqual(worker3._detect_platform(), "generic")

    def test_selectors_by_platform(self) -> None:
        """Verify selector retrieval per platform."""
        from core.browser_brain import BrowserWorker, DEFAULT_SELECTORS
        worker = BrowserWorker({"browser_ai_url": "https://chat.deepseek.com/"})
        selectors = worker._get_selectors()
        self.assertIn("chat_input", selectors)
        self.assertIn("send_button", selectors)
        self.assertIn("deepseek", DEFAULT_SELECTORS)
        self.assertIn("gemini", DEFAULT_SELECTORS)
        self.assertIn("generic", DEFAULT_SELECTORS)

    def test_is_available_no_playwright(self) -> None:
        """Verify is_available returns bool even without playwright."""
        from core.browser_brain import BrowserWorker
        result = BrowserWorker.is_available()
        self.assertIsInstance(result, bool)

    def test_auth_path_exists(self) -> None:
        """Verify AUTH_STATE_PATH is a pathlib Path."""
        from core.browser_brain import AUTH_STATE_PATH
        from pathlib import Path
        self.assertIsInstance(AUTH_STATE_PATH, Path)
        self.assertIn("browser_auth.json", AUTH_STATE_PATH.name)


class TestRenderer3DAPI(unittest.TestCase):
    """Verify renderer_3d module public API works."""

    @classmethod
    def setUpClass(cls) -> None:
        _ensure_qapp()

    def test_import_opengl(self) -> None:
        """Verify _import_opengl returns bool without crashing."""
        from ui.renderer_3d import _import_opengl
        result = _import_opengl()
        self.assertIsInstance(result, bool)

    def test_create_widget(self) -> None:
        """Verify create_3d_widget returns either None or a widget.

        On headless/CI systems (QT_QPA_PLATFORM=offscreen), OpenGL context
        creation is unavailable — skip test gracefully.
        """
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            self.skipTest("No display available for OpenGL widget (offscreen)")
        from ui.renderer_3d import create_3d_widget
        try:
            result = create_3d_widget()
            self.assertTrue(result is None or hasattr(result, "set_profile"))
        except Exception:
            self.skipTest("OpenGL widget creation failed on this system")

    def test_texture_data_generation(self) -> None:
        """Verify VBO data generation produces bytes."""
        from ui.renderer_3d import _build_cube_vbo_data, _build_edge_vbo_data
        cube_data = _build_cube_vbo_data()
        edge_data = _build_edge_vbo_data()
        self.assertIsInstance(cube_data, bytes)
        self.assertIsInstance(edge_data, bytes)
        self.assertGreater(len(cube_data), 0)
        self.assertGreater(len(edge_data), 0)

    def test_tex_buf_images(self) -> None:
        """Verify procedural texture generators produce valid results."""
        import ui.renderer_3d as r3d
        _ensure_qapp()  # QImage needs QApp
        # Test the generators exist
        for name in ["skin", "cloth", "denim", "hair", "leather", "metal", "fire", "bone", "flat"]:
            fn_name = f"_make_{name}_tex"
            self.assertTrue(hasattr(r3d, fn_name), f"Missing {fn_name}")
            fn = getattr(r3d, fn_name)
            img = fn()
            self.assertIsNotNone(img)


class TestIntroUtilsAPI(unittest.TestCase):
    """Verify intro_utils module public API."""

    def test_hex_grid_cache(self) -> None:
        """Verify HexGridCache returns non-null pixmap."""
        from ui.intro_utils import HexGridCache
        cache = HexGridCache()
        pix = cache.get(800, 600)
        self.assertIsNotNone(pix)
        # Second call should return cached version
        pix2 = cache.get(800, 600)
        self.assertIsNotNone(pix2)

    def test_create_and_update_particles(self) -> None:
        """Verify particle creation and animation."""
        from ui.intro_utils import create_particles, update_particles
        particles = create_particles(20, 800, 600)
        self.assertEqual(len(particles), 20)
        for p in particles:
            self.assertIn("x", p)
            self.assertIn("y", p)
            self.assertIn("speed", p)
        # Update should not crash
        before = [p["x"] for p in particles]
        update_particles(particles, 800, 600)
        # Particles drift left by their own speed and wrap to the right edge.
        # The old assertion (``0 <= x <= 810 or x < -10``) rejected the burst
        # window where a particle starting just right of 0 drifts slightly
        # negative — roughly a 1-in-100 run, with no fixed seed. It also allowed
        # the impossible ``x < -10`` case, since one update can move at most 1 px.
        for original, p in zip(before, particles):
            expected = original - p["speed"]
            if expected < -10:
                self.assertEqual(p["x"], 810, "should wrap to the right edge")
            else:
                self.assertAlmostEqual(p["x"], expected, places=6)
            self.assertGreaterEqual(p["x"], -10)
            self.assertLessEqual(p["x"], 810)

    def test_hud_functions_exist(self) -> None:
        """Verify all HUD drawing functions are importable."""
        from ui.intro_utils import (
            draw_hud_frame, draw_hud_corner_text,
            draw_background_glow, draw_crt_scanlines,
            draw_floating_particles, draw_skip_button, draw_status_bar,
            CHECKS, SKIP_DELAY_MS, HUD_CYAN, HUD_CYAN_DIM,
        )
        self.assertGreater(len(CHECKS), 0)
        self.assertEqual(SKIP_DELAY_MS, 1000)
        self.assertIsNotNone(HUD_CYAN)


class TestHudModuleAPI(unittest.TestCase):
    """Verify hud module public API."""

    def test_hud_renderer_methods(self) -> None:
        """Verify HudRenderer static methods exist."""
        from ui.hud import HudRenderer
        self.assertTrue(callable(HudRenderer.paint_frame))
        self.assertTrue(callable(HudRenderer.paint_scan_line))
        self.assertTrue(callable(HudRenderer.paint_corner_text))
        self.assertTrue(callable(HudRenderer.paint_status_bar))
        self.assertTrue(callable(HudRenderer.make_hud_pixmap))


class TestOverlayAPI(unittest.TestCase):
    """Verify overlay module public API (no widget creation)."""

    @classmethod
    def setUpClass(cls) -> None:
        _ensure_qapp()

    def test_mode_colors_defined(self) -> None:
        """Verify mode color constants exist."""
        import ui.overlay as ov
        from PyQt6.QtGui import QColor
        for mode in ["TAP", "STALK", "WHISPER", "LULLABY", "FORMAL"]:
            self.assertIn(mode, ov._MODE_COLORS)
            self.assertIsInstance(ov._MODE_COLORS[mode], QColor)

    def test_chat_bubble_delegate_creation(self) -> None:
        """Verify ChatBubbleDelegate can be created."""
        from ui.overlay import ChatBubbleDelegate
        delegate = ChatBubbleDelegate()
        self.assertIsNotNone(delegate)

    def test_settings_tabs_exist(self) -> None:
        """Verify settings dialog tab classes exist."""
        import ui.overlay as ov
        self.assertTrue(hasattr(ov, "GeneralSettingsTab"))
        self.assertTrue(hasattr(ov, "VoiceSettingsTab"))
        self.assertTrue(hasattr(ov, "PromptSettingsTab"))
        self.assertTrue(hasattr(ov, "AdvancedSettingsTab"))
        self.assertTrue(hasattr(ov, "AtomSettingsDialog"))


class TestActionsAPI(unittest.TestCase):
    """Verify action execution module works."""

    def test_action_executor_creation(self) -> None:
        """Verify ActionExecutor can be created with mock overlay."""
        from core.hands import ActionExecutor
        executor = ActionExecutor(MagicMock())
        self.assertIsNotNone(executor)
        self.assertIsNone(executor.pending)

    def test_action_executor_confirm_cancel(self) -> None:
        """Verify confirm/cancel flow without pending action."""
        from core.hands import ActionExecutor
        executor = ActionExecutor(MagicMock())
        # Cancel with no pending should emit cancelled
        signals = []
        executor.cancelled.connect(lambda msg: signals.append(msg))
        executor.cancel()
        self.assertEqual(len(signals), 1)
        self.assertIn("canceled", signals[0].lower())


class TestCoreInit(unittest.TestCase):
    """Verify core/__init__.py exports."""

    def test_all_exports(self) -> None:
        """Verify __all__ exports are accessible."""
        import core
        for name in core.__all__:
            self.assertTrue(hasattr(core, name), f"Missing export: {name}")


# ═══════════════════════════════════════════════════════════════════════
# Clean up patcher
# ═══════════════════════════════════════════════════════════════════════

def tearDownModule() -> None:
    _patcher.stop()


if __name__ == "__main__":
    unittest.main()
