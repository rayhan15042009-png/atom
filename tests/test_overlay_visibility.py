"""
Regression tests for overlay visibility (sprite + chat panel).

Two defects, both of which made parts of the UI vanish:

1. ``update_input_mask()`` dropped the sprite from the window region whenever
   click-through was active. ``QWidget.setMask`` clips **painting** as well as
   input, so the 2D character did not merely become click-through — it
   disappeared, and could not be woken by hovering it either, because mouse
   events no longer reached the window there.

2. The mask's change-detection key was sprite position/size plus a few booleans,
   so it ignored resizes and chat-panel re-layouts. The panel is anchored
   bottom-right, so after any size change its new rect fell outside the stale
   mask and the chat panel silently disappeared and stopped accepting clicks.

The minimise control is also covered: it existed but was an unlabelled "–" glyph
that was easy to miss, which is why it kept being reported as missing.
"""

from __future__ import annotations

import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

_APP = QApplication.instance() or QApplication([])

from core import chat_history  # noqa: E402
from core.config_manager import AppConfig  # noqa: E402
from ui.overlay import AtomOverlay, _3D_COMPOSITOR_GRACE_SECONDS  # noqa: E402


class _FakeRenderer:
    """Minimal stand-in for BlockRenderer3D (no GL context needed)."""

    def __init__(self, running: bool = True) -> None:
        self._running = running
        self.frames = 0

    def is_animation_running(self) -> bool:
        return self._running

    def stop_animation(self) -> None:
        pass

    def set_profile(self, name: str) -> None:
        pass


class _OverlayTest(unittest.TestCase):
    def setUp(self) -> None:
        # The overlay now reads (and writes) data/chat_history.json, so every
        # test in this module gets its own transcript instead of the real one.
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.history_path = Path(self._tmp.name) / "chat_history.json"
        patcher = mock.patch.object(chat_history, "HISTORY_PATH", self.history_path)
        patcher.start()
        self.addCleanup(patcher.stop)

        cfg = AppConfig()
        cfg.render_mode = "2d"
        self.overlay = AtomOverlay(cfg)
        self.overlay.resize(1280, 720)
        self.overlay._layout_chat_panel()
        self.overlay.update_input_mask()
        self.addCleanup(self._teardown)

    def _teardown(self) -> None:
        for name in ("_anim_timer", "_update_timer", "_idle_click_timer",
                     "_cursor_watch_timer", "_speech_timer"):
            timer = self.overlay.__dict__.get(name)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass
        try:
            self.overlay.close()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
# 1. The sprite is never masked away
# ═══════════════════════════════════════════════════════════════════════════════

class TestSpriteAlwaysInMask(_OverlayTest):
    def test_idle_mask_covers_sprite(self) -> None:
        self.assertTrue(self.overlay.mask().contains(self.overlay._sprite_rect().center()))

    def test_click_through_keeps_sprite_in_mask(self) -> None:
        """The regression: click-through used to clip the character away."""
        self.overlay._reset_idle_timer()
        self.overlay._enable_click_through()
        self.assertTrue(self.overlay._click_through_active)
        self.assertTrue(
            self.overlay.mask().contains(self.overlay._sprite_rect().center()),
            "sprite must stay painted while click-through is active",
        )

    def test_click_through_uses_window_attribute_not_mask(self) -> None:
        self.overlay._enable_click_through()
        self.assertTrue(
            self.overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        )
        self.overlay._disable_click_through()
        self.assertFalse(
            self.overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        )

    def test_disable_click_through_restores_interaction(self) -> None:
        self.overlay._enable_click_through()
        self.overlay._disable_click_through()
        self.assertFalse(self.overlay._click_through_active)
        self.assertTrue(self.overlay.mask().contains(self.overlay._sprite_rect().center()))

    def test_cursor_watch_wakes_when_reaching_the_sprite(self) -> None:
        """The wake path cannot rely on mouse events — the window ignores them."""
        self.overlay._enable_click_through()
        # Simulate the cursor arriving on the sprite by patching the global pos.
        from PyQt6.QtGui import QCursor
        centre = self.overlay._sprite_rect().center()
        global_pos = self.overlay.mapToGlobal(centre)
        original = QCursor.pos
        try:
            QCursor.pos = staticmethod(lambda: global_pos)  # type: ignore[assignment]
            self.overlay._watch_cursor()
        finally:
            QCursor.pos = original  # type: ignore[assignment]
        self.assertFalse(self.overlay._click_through_active)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. The mask follows layout changes
# ═══════════════════════════════════════════════════════════════════════════════

class TestMaskFollowsLayout(_OverlayTest):
    def test_panel_is_in_the_mask_initially(self) -> None:
        self.assertTrue(
            self.overlay.mask().contains(self.overlay._chat_panel.geometry().center())
        )

    def test_mask_is_recomputed_after_a_resize(self) -> None:
        """The stale-mask regression: the panel is anchored bottom-right."""
        before = self.overlay.mask()
        self.overlay.resize(1920, 1080)
        self.overlay._layout_chat_panel()
        self.overlay.update_input_mask()
        after = self.overlay.mask()

        self.assertNotEqual(before, after, "mask was not recomputed after resize")
        self.assertTrue(
            after.contains(self.overlay._chat_panel.geometry().center()),
            "chat panel fell outside the mask after a resize",
        )
        self.assertTrue(after.contains(self.overlay._sprite_rect().center()))

    def test_mask_tracks_the_float_button_when_minimised(self) -> None:
        self.overlay._chat_minimized = True
        self.overlay.update_input_mask()
        self.assertTrue(
            self.overlay.mask().contains(self.overlay._float_button.geometry().center())
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. The minimise control is discoverable and works
# ═══════════════════════════════════════════════════════════════════════════════

class TestMinimizeControl(_OverlayTest):
    """Minimise is a real round trip: the panel hides, the bubble restores it."""

    def _settle(self) -> None:
        """Run the slide/fade animations to their end state."""
        QTest.qWait(600)

    def test_minimize_button_exists_with_a_text_label(self) -> None:
        btn = self.overlay._min_btn
        self.assertIsInstance(btn, QPushButton)
        self.assertEqual(btn.text(), "MIN")
        self.assertIn("Minimize", btn.toolTip())

    def test_minimize_button_is_inside_the_panel(self) -> None:
        self.assertTrue(self.overlay._chat_panel.rect().contains(self.overlay._min_btn.geometry()))

    def test_clicking_minimize_minimises(self) -> None:
        self.overlay._min_btn.click()
        self.assertTrue(self.overlay._chat_minimized)

    # ``isHidden`` rather than ``isVisible``: the overlay window itself is never
    # shown in these tests, and a child of a hidden window reports
    # ``isVisible() == False`` no matter what its own show/hide state is.

    def test_clicking_minimize_hides_the_panel_and_shows_the_bubble(self) -> None:
        self.overlay._chat_panel.show()
        self.overlay._min_btn.click()
        self._settle()
        self.assertTrue(self.overlay._chat_panel.isHidden(),
                        "the chat panel stayed on screen after minimising")
        self.assertFalse(self.overlay._float_button.isHidden(),
                         "nothing was left to restore the chat with")

    def test_the_bubble_restores_the_panel(self) -> None:
        self.overlay._chat_panel.show()
        self.overlay._min_btn.click()
        self._settle()
        self.overlay._float_button.click()
        self._settle()
        self.assertFalse(self.overlay._chat_minimized)
        self.assertFalse(self.overlay._chat_panel.isHidden(), "chat did not come back")
        self.assertTrue(self.overlay._float_button.isHidden())

    def test_a_restore_during_the_hide_animation_wins(self) -> None:
        """The hide callback must never hide a panel that came back meanwhile."""
        self.overlay._chat_panel.show()
        self.overlay._min_btn.click()
        self.overlay.toggle_chat()
        self._settle()
        self.assertFalse(self.overlay._chat_panel.isHidden())

    def test_toggle_round_trip(self) -> None:
        self.overlay._min_btn.click()
        self.overlay.toggle_chat()
        self.assertFalse(self.overlay._chat_minimized)

    def test_clear_button_exists(self) -> None:
        self.assertEqual(self.overlay._clear_btn.text(), "CLR")


# ═══════════════════════════════════════════════════════════════════════════════
# 3b. The chat panel remembers the previous conversation
# ═══════════════════════════════════════════════════════════════════════════════

class TestChatTranscript(_OverlayTest):
    """The panel used to open empty on every launch, with the conversation on disk."""

    def _roles(self, overlay) -> list:
        return [overlay.chat_list.item(i).data(Qt.ItemDataRole.UserRole + 1)
                for i in range(overlay.chat_list.count())]

    def _texts(self, overlay) -> list:
        return [overlay.chat_list.item(i).text()
                for i in range(overlay.chat_list.count())]

    def _second_overlay(self) -> AtomOverlay:
        second = AtomOverlay(self.overlay._config)
        self.addCleanup(second.close)
        return second

    def test_a_stored_conversation_is_replayed_into_a_fresh_panel(self) -> None:
        self.overlay.add_chat_message("user", "who are you?")
        self.overlay.add_chat_message("assistant", "*tap* Atom.", animate=False)

        second = self._second_overlay()

        self.assertEqual(self._roles(second), ["user", "assistant"])
        self.assertEqual(self._texts(second), ["who are you?", "*tap* Atom."])

    def test_restoring_does_not_rewrite_or_duplicate_the_transcript(self) -> None:
        self.overlay.add_chat_message("user", "one")
        before = self.history_path.read_text(encoding="utf-8")
        second = self._second_overlay()
        self.assertEqual(len(self._texts(second)), 1)
        self.assertEqual(self.history_path.read_text(encoding="utf-8"), before)

    def test_a_message_sent_after_a_restore_is_appended(self) -> None:
        self.overlay.add_chat_message("user", "first")
        second = self._second_overlay()
        second.add_chat_message("user", "second")
        self.assertEqual([e["content"] for e in chat_history.load_chat_history()],
                         ["first", "second"])

    def test_status_rows_are_not_remembered(self) -> None:
        self.overlay.add_chat_message("system", "*tap* Goodbye, Koko.")
        self.assertEqual(chat_history.load_chat_history(), [])

    def test_restored_messages_keep_their_timestamp(self) -> None:
        self.overlay.add_chat_message("user", "earlier")
        second = self._second_overlay()
        item = second.chat_list.item(0)
        # 3 = the date the bubble groups under, 2 = the hover time
        self.assertEqual(item.data(Qt.ItemDataRole.UserRole + 3),
                         self.overlay.chat_list.item(0).data(Qt.ItemDataRole.UserRole + 3))

    def test_clearing_the_chat_forgets_the_transcript(self) -> None:
        self.overlay.add_chat_message("user", "forget me")
        self.overlay.clear_chat()
        self.assertEqual(chat_history.load_chat_history(), [])
        self.assertEqual(self._texts(self._second_overlay()), [])

    def test_a_corrupt_transcript_does_not_stop_the_overlay(self) -> None:
        self.history_path.write_text("{ broken", encoding="utf-8")
        self.assertEqual(self._texts(self._second_overlay()), [])


# ═══════════════════════════════════════════════════════════════════════════════
# 4. A live-but-unpainted renderer is not mistaken for a broken one
# ═══════════════════════════════════════════════════════════════════════════════

class TestStatusHudShowsTheAnsweringKey(_OverlayTest):
    """Five configured Gemini keys are only useful if you can see which one ran.

    The live widget is ``ui.overlay.StatusHudWidget`` — note that ``ui/hud.py``
    carries a near-identical copy that only the tests import.
    """

    def test_the_slot_is_shown_with_the_provider(self) -> None:
        self.overlay._status_hud.set_provider("gemini", "gemini-2.5-flash",
                                              "online", "#3/5")
        hud = self.overlay._status_hud
        self.assertEqual(hud._note, "#3/5")
        self.assertIn("key 3/5", hud.toolTip())

    def test_a_key_warning_does_not_erase_the_slot(self) -> None:
        hud = self.overlay._status_hud
        hud.set_provider("gemini", "gemini-2.5-flash", "online", "#2/5")
        hud.set_key_warning("Gemini key issue: too short")
        self.assertIn("key 2/5", hud.toolTip())
        self.assertIn("too short", hud.toolTip())

    def test_another_provider_clears_the_slot(self) -> None:
        hud = self.overlay._status_hud
        hud.set_provider("gemini", "gemini-2.5-flash", "online", "#4/5")
        hud.set_provider("ollama", "phi3:mini", "online")
        self.assertEqual(hud._note, "")
        self.assertNotIn("key", hud.toolTip())

    def test_the_app_shows_the_answering_key(self) -> None:
        """The brain reports what answered; the HUD has to pick it up."""
        import atom as atom_module

        fake_app = types.SimpleNamespace(
            overlay=self.overlay,
            config={"gemini_model": "gemini-2.5-flash", "model": "phi3:mini"},
        )
        atom_module.AtomApp._on_provider_used(fake_app, "gemini", "#3/5")

        hud = self.overlay._status_hud
        self.assertEqual(hud._provider, "gemini")
        self.assertEqual(hud._note, "#3/5")
        self.assertIn("key 3/5", hud.toolTip())

    def test_an_unknown_provider_falls_back_to_the_config(self) -> None:
        import atom as atom_module

        fake_app = types.SimpleNamespace(
            overlay=self.overlay,
            config={"llm_provider": "ollama", "model": "phi3:mini"},
        )
        # The unknown-provider path re-reads the config, so wire that method up.
        fake_app._update_status_hud_provider = lambda note="": (
            atom_module.AtomApp._update_status_hud_provider(fake_app, note)
        )
        atom_module.AtomApp._on_provider_used(fake_app, "?", "")
        self.assertEqual(self.overlay._status_hud._provider, "ollama")

    def test_the_note_survives_labelling(self) -> None:
        """The note sits next to the provider so elision cannot hide it."""
        import inspect
        from ui.overlay import StatusHudWidget
        source = inspect.getsource(StatusHudWidget.paintEvent)
        self.assertLess(source.index("self._note"), source.index("self._model"),
                        "the key slot must be drawn before the elided model name")


class Test3dStallWatchdog(_OverlayTest):
    """The watchdog used to tear down a renderer that was merely not painted.

    ``_check_3d_health()`` treated "no frames for 5s" as breakage and
    re-initialised the renderer, discarding a working GL context. Two of those
    in a row permanently downgraded the session to 2D, which is what made the
    3D character vanish on its own once the window was buried (or the display
    slept / the machine was busy). Only a dead animation timer is a real failure.
    """

    def setUp(self) -> None:
        super().setUp()
        self.reinit: list = []
        self.fallback: list = []
        self.overlay._reinit_3d = lambda: self.reinit.append(1)
        self.overlay._fallback_to_2d = lambda: self.fallback.append(1)
        self.overlay._sync_render_win_pos = lambda: None
        self.win = QWidget()
        self.win.show()
        self.addCleanup(self.win.close)

    def _stall(self, *, running: bool) -> None:
        self.overlay.renderer_3d = _FakeRenderer(running)
        self.overlay._render_win = self.win
        self.overlay._3d_frames_rendered = 100
        self.overlay._3d_last_frame_count = 100
        self.overlay._3d_last_frame_time = time.time() - 10.0
        self.overlay._check_3d_health()

    def test_live_timer_with_no_frames_is_tolerated(self) -> None:
        self.overlay._3d_compositor_stall_since = None
        self._stall(running=True)
        self.assertEqual(self.reinit, [], "a still-animating renderer was re-initialised")
        self.assertEqual(self.fallback, [])

    def test_dead_timer_still_reinitialises(self) -> None:
        self._stall(running=False)
        self.assertEqual(self.reinit, [1], "a genuinely dead renderer must be recovered")

    def test_grace_period_is_bounded(self) -> None:
        self.overlay._3d_compositor_stall_since = time.time() - _3D_COMPOSITOR_GRACE_SECONDS - 1
        self._stall(running=True)
        self.assertEqual(self.reinit, [1], "a never-painting renderer must eventually be rebuilt")

    def test_frames_advancing_clears_the_compositor_latch(self) -> None:
        self.overlay._3d_compositor_stall_since = 123.0
        self.overlay.renderer_3d = _FakeRenderer(True)
        self.overlay._render_win = self.win
        self.overlay._3d_frames_rendered = 101
        self.overlay._3d_last_frame_count = 100
        self.overlay._check_3d_health()
        self.assertIsNone(self.overlay._3d_compositor_stall_since)


class TestPlaceholderWhileWindowHidden(_OverlayTest):
    """The 3D branch returned without painting, leaving an empty overlay."""

    def setUp(self) -> None:
        super().setUp()
        self.drawn: list = []
        self.overlay._draw_sprite_2d = lambda painter: self.drawn.append(1)

    def test_no_flash_on_the_first_hidden_frame(self) -> None:
        self.overlay._render_win_hidden_since = None
        self.overlay._draw_sprite_2d_placeholder(None)
        self.assertEqual(self.drawn, [], "placeholder flashed on the first hidden frame")

    def test_placeholder_appears_once_the_window_stays_hidden(self) -> None:
        self.overlay._render_win_hidden_since = time.time() - 2.0
        self.overlay._draw_sprite_2d_placeholder(None)
        self.assertEqual(self.drawn, [1], "character was left off-screen")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. There is a way back from the automatic 3D→2D fallback
# ═══════════════════════════════════════════════════════════════════════════════

class TestRendererSwitch(_OverlayTest):
    def test_restore_3d_clears_every_latch(self) -> None:
        self.overlay._fps_fallback_done = True
        self.overlay._3d_stall_recoveries = 5
        self.overlay._3d_compositor_stall_since = 123.0
        self.overlay._render_win_hidden_since = 123.0
        calls: list = []
        self.overlay._init_3d = lambda: calls.append(1)

        self.overlay.restore_3d()

        self.assertEqual(calls, [1])
        self.assertFalse(self.overlay._fps_fallback_done)
        self.assertEqual(self.overlay._3d_stall_recoveries, 0)
        self.assertIsNone(self.overlay._3d_compositor_stall_since)
        self.assertIsNone(self.overlay._render_win_hidden_since)

    def test_restore_3d_is_a_noop_when_already_in_3d(self) -> None:
        self.overlay.renderer_3d = _FakeRenderer(True)
        calls: list = []
        self.overlay._init_3d = lambda: calls.append(1)
        self.overlay.restore_3d()
        self.assertEqual(calls, [])

    def test_use_2d_drops_the_renderer(self) -> None:
        self.overlay.renderer_3d = _FakeRenderer(True)
        dropped: list = []
        self.overlay._fallback_to_2d = lambda: dropped.append(1)
        self.overlay.use_2d()
        self.assertEqual(dropped, [1])

    def test_menu_offers_both_renderers(self) -> None:
        menu = self.overlay._build_context_menu()
        labels = [a.text() for m in menu.findChildren(type(menu)) for a in m.actions()]
        self.assertIn("2D sprite", labels)
        self.assertIn("3D model", labels)

    def test_menu_checks_3d_when_the_renderer_is_live(self) -> None:
        self.overlay.renderer_3d = _FakeRenderer(True)
        menu = self.overlay._build_context_menu()
        checked = {
            a.text() for m in menu.findChildren(type(menu)) for a in m.actions() if a.isChecked()
        }
        self.assertIn("3D model", checked)


if __name__ == "__main__":
    unittest.main()
