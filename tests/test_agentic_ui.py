"""
Tests for the agentic / question-answering and UI-robustness improvements.

Covers:
- core/brain: answer-preserving response pipeline, multi-action tool parsing,
  action-result feedback that replays the original question.
- core/providers/router: cached internet probe.
- ui/overlay: 3D render watchdog (the "model disappears" bug) and the
  chat minimize shortcuts.
"""

from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QWidget

# ── QApplication singleton (required for widget classes) ──
_APP = QApplication.instance() or QApplication([])

from core.brain import (  # noqa: E402
    BrainThread,
    MAX_HISTORY_TURNS,
    MAX_TOOL_ROUNDS,
    finalize_response,
    is_boring,
    strip_boring_phrases,
)
from core.config_manager import AppConfig  # noqa: E402
from core.providers import router as router_mod  # noqa: E402
from core.providers.router import ProviderRouter  # noqa: E402
from ui.overlay import (  # noqa: E402
    AtomOverlay,
    _3D_MIN_FPS,
    _3D_STALL_MAX_RETRIES,
    _3D_STALL_SECONDS,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Brain — question answering
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnswerPreservingPipeline(unittest.TestCase):
    """finalize_response must not throw away legitimate answers."""

    def test_answer_mentioning_banned_phrase_survives(self) -> None:
        raw = "I cannot do that in one line, but here is the idea: use a dict lookup."
        out = finalize_response(raw, "how do I do X", "tap")
        self.assertIn("dict", out.lower())
        self.assertNotIn("All models failed", out)

    def test_pure_disclaimer_falls_back(self) -> None:
        out = finalize_response("As an AI, I cannot do that", "test", "tap")
        self.assertTrue(out.startswith("*tap*"))

    def test_none_and_empty_response_do_not_crash(self) -> None:
        for value in (None, "", "   "):
            out = finalize_response(value, "hello", "tap")
            self.assertTrue(out)

    def test_strip_boring_phrases_pure_disclaimer_is_empty(self) -> None:
        self.assertEqual(strip_boring_phrases("As an AI, I cannot do that"), "")

    def test_strip_boring_phrases_keeps_substance(self) -> None:
        out = strip_boring_phrases("I am an AI. Water boils at 100C at sea level.")
        self.assertIn("Water boils", out)

    def test_is_boring_still_detects_corporate_speak(self) -> None:
        self.assertTrue(is_boring("As an AI language model, I cannot do that"))
        self.assertFalse(is_boring("*tap* Let's hack some stuff!"))


class TestAnswerRulesInPrompt(unittest.TestCase):
    """The persona prompts now carry explicit answering rules."""

    def test_tap_prompt_has_answer_rules(self) -> None:
        from core.brain import get_system_prompt
        prompt = get_system_prompt("tap")
        self.assertIn("ANSWER IT FIRST", prompt)
        # persona must survive alongside the rules
        self.assertIn("alakazam bitch", prompt.lower())

    def test_formal_prompt_has_answer_rules(self) -> None:
        from core.brain import get_system_prompt
        prompt = get_system_prompt("formal")
        self.assertIn("ANSWER IT FIRST", prompt)
        self.assertIn("*tap*", prompt)


class TestToolActionParsing(unittest.TestCase):
    """Robust, multi-action tool-command parsing."""

    def setUp(self) -> None:
        self.brain = BrainThread()

    def test_single_search(self) -> None:
        actions = self.brain._parse_actions("sure [search: quantum physics] ok")
        self.assertEqual(actions, [{"action": "search", "query": "quantum physics"}])

    def test_legacy_single_action_still_works(self) -> None:
        action = self.brain._parse_action("[open: notepad]")
        self.assertEqual(action, {"action": "open", "target": "notepad"})

    def test_multiple_actions_in_order(self) -> None:
        text = "[search: rust async] then [scrape: https://example.com/a]"
        actions = self.brain._parse_actions(text)
        self.assertEqual([a["action"] for a in actions], ["search", "scrape"])
        self.assertEqual(actions[1]["url"], "https://example.com/a")

    def test_action_order_follows_text_not_table(self) -> None:
        """A chain must run in the order the model wrote it."""
        text = "[scrape: https://example.com/a] then [search: rust async]"
        actions = self.brain._parse_actions(text)
        self.assertEqual([a["action"] for a in actions], ["scrape", "search"])

    def test_click_parses_integers(self) -> None:
        action = self.brain._parse_action("[click: 120, 45]")
        self.assertEqual(action, {"action": "click", "x": 120, "y": 45})

    def test_malformed_click_is_skipped(self) -> None:
        self.assertEqual(self.brain._parse_actions("[click: abc, def]"), [])

    def test_empty_argument_is_skipped(self) -> None:
        self.assertEqual(self.brain._parse_actions("[search:   ]"), [])

    def test_no_actions_returns_empty(self) -> None:
        self.assertEqual(self.brain._parse_actions("just a normal answer"), [])

    def test_none_text_is_safe(self) -> None:
        self.assertEqual(self.brain._parse_actions(None), [])


class TestActionResultFeedback(unittest.TestCase):
    """Tool results are replayed together with the original question."""

    def setUp(self) -> None:
        self.brain = BrainThread()
        self.queued: list[dict] = []
        self.brain.ask = self.queued.append  # type: ignore[assignment]

    def test_feedback_replays_original_question(self) -> None:
        self.brain._pending_action = {
            "text": "what's the weather",
            "_action_name": "search",
            "_original_question": "what's the weather in Tokyo",
        }
        self.brain.feed_action_result("Tokyo: 24C, clear")
        self.assertEqual(len(self.queued), 1)
        payload = self.queued[0]
        self.assertIn("24C", payload["text"])
        self.assertIn("what's the weather in Tokyo", payload["text"])
        self.assertTrue(payload["_action_result_feedback"])

    def test_feedback_without_original_question_is_safe(self) -> None:
        self.brain._pending_action = {"text": "x", "_action_name": "search"}
        self.brain.feed_action_result("result")
        self.assertEqual(len(self.queued), 1)

    def test_feedback_with_no_pending_action_is_noop(self) -> None:
        self.brain._pending_action = None
        self.brain.feed_action_result("result")
        self.assertEqual(self.queued, [])


class TestMultiRoundToolLoop(unittest.TestCase):
    """A tool result may lead to another tool, bounded by MAX_TOOL_ROUNDS."""

    def _brain_with(self, replies: list[str]):
        brain = BrainThread({})
        captured: list[dict] = []
        seen: list[dict] = []

        class _FakeRouter:
            def chat(self, messages, mode="tap"):
                seen.append(messages)
                return replies[min(len(seen) - 1, len(replies) - 1)]

            def cleanup(self):
                pass

        brain._router = _FakeRouter()  # type: ignore[assignment]
        brain.action_requested.connect(captured.append)
        return brain, captured, seen

    def test_second_round_can_request_another_tool(self) -> None:
        brain, actions, seen = self._brain_with([
            "[ls: .]",                       # round 1 → a tool
            "[read: notes.txt]",             # round 2 → another tool
            "Notes say hello.",              # round 3 → the answer
        ])
        answers: list[str] = []
        brain.response_ready.connect(answers.append)

        brain._process({"text": "what's in my notes?", "mode": "tap", "history": []})
        self.assertEqual(actions[0]["action"], "list_dir")
        self.assertEqual(brain._pending_action["_tool_round"], 1)

        brain.feed_action_result("notes.txt\nother.txt")
        brain._process(brain._queue.pop(0))
        self.assertEqual(actions[1]["action"], "read_file")
        self.assertEqual(brain._pending_action["_tool_round"], 2)
        self.assertEqual(brain._pending_action["_original_question"], "what's in my notes?")

        brain.feed_action_result("hello")
        brain._process(brain._queue.pop(0))
        self.assertEqual(len(actions), 2, "no third tool call expected")
        self.assertTrue(answers[-1])

    def test_original_question_survives_every_round(self) -> None:
        brain, _, seen = self._brain_with(["[ls: .]", "Here: files."])
        brain._process({"text": "list my workspace", "mode": "tap", "history": []})
        brain.feed_action_result("a.txt")
        brain._process(brain._queue.pop(0))
        last_user = [m for m in seen[-1] if m["role"] == "user"][-1]["content"]
        self.assertIn("list my workspace", last_user)
        self.assertIn("a.txt", last_user)

    def test_tool_rounds_are_capped(self) -> None:
        """A model stuck in a tool loop must be forced to answer."""
        brain, actions, seen = self._brain_with([
            "[ls: .]", "[ls: .]", "[ls: .]", "[ls: .]", "Final answer.",
        ])
        answers: list[str] = []
        brain.response_ready.connect(answers.append)

        brain._process({"text": "go", "mode": "tap", "history": []})
        for _ in range(MAX_TOOL_ROUNDS + 2):
            brain.feed_action_result("listing")
            if not brain._queue:
                break
            brain._process(brain._queue.pop(0))

        self.assertLessEqual(len(actions), MAX_TOOL_ROUNDS)
        self.assertTrue(answers[-1], "the model must still answer Koko")

    def test_leftover_markup_is_stripped_when_out_of_rounds(self) -> None:
        brain, _, _ = self._brain_with(["[ls: .]"] * (MAX_TOOL_ROUNDS + 1))
        answers: list[str] = []
        brain.response_ready.connect(answers.append)

        brain._process({"text": "go", "mode": "tap", "history": []})
        for _ in range(MAX_TOOL_ROUNDS + 2):
            brain.feed_action_result("listing")
            if not brain._queue:
                break
            brain._process(brain._queue.pop(0))
        self.assertNotIn("[ls:", answers[-1])

    def test_round_counter_defaults_to_zero(self) -> None:
        brain, _, _ = self._brain_with(["[ls: .]"])
        brain._process({"text": "go", "mode": "tap", "history": []})
        self.assertEqual(brain._pending_action["_tool_round"], 1)


class TestHistoryWindow(unittest.TestCase):
    """History is bounded by MAX_HISTORY_TURNS and roles are preserved."""

    def test_history_turns_constant_is_sane(self) -> None:
        self.assertGreaterEqual(MAX_HISTORY_TURNS, 8)

    def test_history_roles_reach_messages(self) -> None:
        brain = BrainThread({})
        captured: dict = {}

        class _FakeRouter:
            def chat(self, messages, mode="tap"):
                captured["messages"] = messages
                return "ok"

            def cleanup(self):
                pass

        brain._router = _FakeRouter()  # type: ignore[assignment]
        brain._process({
            "text": "turn 3",
            "mode": "tap",
            "history": [
                {"role": "user", "content": "turn 1"},
                {"role": "assistant", "content": "reply 1"},
                {"role": "user", "content": "turn 2"},
                {"role": "assistant", "content": "reply 2"},
            ],
        })
        roles = [m["role"] for m in captured["messages"]]
        self.assertEqual(
            roles,
            ["system", "user", "assistant", "user", "assistant", "user"],
        )


# ═══════════════════════════════════════════════════════════════════════════════
# ProviderRouter — cached internet probe
# ═══════════════════════════════════════════════════════════════════════════════

class TestInternetCache(unittest.TestCase):
    def setUp(self) -> None:
        self.router = ProviderRouter({})
        # Reset the module cache so tests are order-independent.
        router_mod._INTERNET_CACHE["ok"] = True
        router_mod._INTERNET_CACHE["ts"] = 0.0

    def test_second_call_uses_cache(self) -> None:
        with patch("socket.socket") as mock_socket:
            mock_socket.return_value.connect.return_value = None
            first = self.router._check_internet(force=True)
            calls_after_first = mock_socket.call_count
            second = self.router._check_internet()
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(mock_socket.call_count, calls_after_first,
                         "cached call must not open another socket")

    def test_force_bypasses_cache(self) -> None:
        with patch("socket.socket") as mock_socket:
            mock_socket.return_value.connect.return_value = None
            self.router._check_internet(force=True)
            self.router._check_internet(force=True)
            self.assertEqual(mock_socket.call_count, 2)

    def test_offline_result_is_cached(self) -> None:
        with patch("socket.socket") as mock_socket:
            mock_socket.return_value.connect.side_effect = OSError("no net")
            self.assertFalse(self.router._check_internet(force=True))
            self.assertFalse(self.router._check_internet())

    def test_expired_cache_refreshes(self) -> None:
        with patch("socket.socket") as mock_socket:
            mock_socket.return_value.connect.return_value = None
            self.router._check_internet(force=True)
            router_mod._INTERNET_CACHE["ts"] = time.time() - (
                router_mod._INTERNET_TTL_SECONDS + 1)
            self.router._check_internet()
            self.assertEqual(mock_socket.call_count, 2)


# ═══════════════════════════════════════════════════════════════════════════════
# Overlay — 3D watchdog + chat minimize
# ═══════════════════════════════════════════════════════════════════════════════

def _bare_overlay(scale: int = 2) -> AtomOverlay:
    """Build an AtomOverlay without constructing the real window.

    The 3D watchdog and window-size helpers only touch plain attributes, so a
    bare instance keeps the tests fast and headless.
    """
    ov = AtomOverlay.__new__(AtomOverlay)
    cfg = AppConfig()
    cfg.sprite_scale = scale
    ov._config = cfg
    return ov


class Test3DWindowSize(unittest.TestCase):
    def test_default_scale_size(self) -> None:
        self.assertEqual(_bare_overlay(2)._3d_window_size(), (200, 280))

    def test_larger_scale_gives_more_pixels(self) -> None:
        self.assertEqual(_bare_overlay(3)._3d_window_size(), (300, 420))

    def test_scale_is_clamped(self) -> None:
        self.assertEqual(_bare_overlay(99)._3d_window_size(), (400, 560))
        self.assertEqual(_bare_overlay(1)._3d_window_size(), (200, 280))

    def test_invalid_scale_falls_back(self) -> None:
        ov = _bare_overlay(2)
        ov._config.sprite_scale = "nonsense"
        self.assertEqual(ov._3d_window_size(), (200, 280))


class Test3DWatchdog(unittest.TestCase):
    """The renderer used to silently stop painting (model 'disappearing')."""

    def _watchdog_overlay(self) -> AtomOverlay:
        ov = _bare_overlay()
        ov.renderer_3d = MagicMock()
        ov._render_win = MagicMock()
        ov._render_win.isVisible.return_value = True
        ov._3d_frames_rendered = 10
        ov._3d_last_frame_count = 10
        ov._3d_last_frame_time = time.time()
        ov._3d_stall_count = 0
        ov._3d_stall_recoveries = 0
        ov._3d_reshow_count = 0
        ov._3d_retry_count = 0
        ov._3d_fallback_timer = None
        ov._3d_raise_timer = None
        ov._3d_init_time = time.time()
        ov.update_input_mask = MagicMock()
        ov.update = MagicMock()
        return ov

    def test_advancing_frames_are_healthy(self) -> None:
        ov = self._watchdog_overlay()
        ov._3d_frames_rendered = 25
        ov._check_3d_health()
        self.assertIsNotNone(ov.renderer_3d)
        self.assertEqual(ov._3d_last_frame_count, 25)
        self.assertEqual(ov._3d_stall_count, 0)

    def test_short_pause_is_tolerated(self) -> None:
        ov = self._watchdog_overlay()
        ov._3d_last_frame_time = time.time() - (_3D_STALL_SECONDS / 2)
        ov._check_3d_health()
        self.assertIsNotNone(ov.renderer_3d)
        self.assertEqual(ov._3d_stall_count, 0)

    def test_long_stall_reinitialises_before_going_2d(self) -> None:
        ov = self._watchdog_overlay()
        ov._3d_last_frame_time = time.time() - (_3D_STALL_SECONDS + 1)
        ov._reinit_3d = MagicMock()
        ov._check_3d_health()
        ov._reinit_3d.assert_called_once()
        self.assertEqual(ov._3d_stall_count, 1)

    def test_repeated_stalls_fall_back_to_2d(self) -> None:
        ov = self._watchdog_overlay()
        ov._3d_stall_recoveries = _3D_STALL_MAX_RETRIES  # budget already spent
        ov._3d_last_frame_time = time.time() - (_3D_STALL_SECONDS + 1)
        ov._check_3d_health()
        self.assertIsNone(ov.renderer_3d)
        ov.update.assert_called()

    def test_hidden_window_is_reshown_not_killed(self) -> None:
        ov = self._watchdog_overlay()
        ov._render_win.isVisible.return_value = False
        ov._3d_last_frame_time = time.time() - (_3D_STALL_SECONDS + 1)
        ov._sync_render_win_pos = MagicMock()
        ov._check_3d_health()
        self.assertIsNotNone(ov.renderer_3d)
        ov._render_win.show.assert_called()

    def test_recovery_budget_survives_a_re_init(self) -> None:
        """A renderer that never paints must stop being rebuilt.

        _create_3d_window() resets the *consecutive* stall counter, which made a
        broken 3D setup rebuild a GL context every few seconds forever. The
        session-wide budget is what ends that loop.
        """
        ov = self._watchdog_overlay()
        ov._3d_stall_recoveries = 0
        ov._reinit_3d = MagicMock()
        ov._fallback_to_2d = MagicMock()

        for _ in range(3):
            ov.renderer_3d = MagicMock()
            ov._3d_last_frame_time = time.time() - (_3D_STALL_SECONDS + 1)
            ov._3d_frames_rendered = ov._3d_last_frame_count
            # Simulate what a fresh window does to the consecutive counter.
            ov._3d_stall_count = 0
            ov._check_3d_health()

        self.assertEqual(ov._reinit_3d.call_count, 2, "only the budget is rebuilt")
        ov._fallback_to_2d.assert_called_once()

    def test_healthy_frames_refill_the_budget(self) -> None:
        ov = self._watchdog_overlay()
        ov._3d_stall_recoveries = 2
        ov._3d_stall_count = 2
        ov._3d_frames_rendered = ov._3d_last_frame_count + 5
        ov._check_3d_health()
        self.assertEqual(ov._3d_stall_recoveries, 0)
        self.assertEqual(ov._3d_stall_count, 0)

    def test_fps_threshold_below_idle_throttle(self) -> None:
        # Renderer idles at ~4 FPS; the "too slow" threshold must sit under it.
        self.assertLess(_3D_MIN_FPS, 4.0)


class TestChatMinimizeShortcuts(unittest.TestCase):
    def _key_overlay(self) -> AtomOverlay:
        ov = _bare_overlay()
        ov._chat_minimized = False
        ov._click_through_active = False
        ov._idle_click_timer = MagicMock()
        ov.toggle_chat = MagicMock()
        ov._minimize_chat = MagicMock()
        return ov

    @staticmethod
    def _key(key, mods=Qt.KeyboardModifier.NoModifier) -> QKeyEvent:
        return QKeyEvent(QEvent.Type.KeyPress, key, mods)

    def _send(self, ov: AtomOverlay, event: QKeyEvent) -> None:
        # The bare instance bypasses QWidget.__init__, so stub out the base
        # implementation that AtomOverlay falls back to for unhandled keys.
        with patch.object(QWidget, "keyPressEvent"):
            ov.keyPressEvent(event)

    def test_ctrl_m_toggles_chat(self) -> None:
        ov = self._key_overlay()
        self._send(ov, self._key(Qt.Key.Key_M, Qt.KeyboardModifier.ControlModifier))
        ov.toggle_chat.assert_called_once()

    def test_ctrl_shift_e_still_toggles(self) -> None:
        ov = self._key_overlay()
        self._send(ov, self._key(
            Qt.Key.Key_E,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
        ))
        ov.toggle_chat.assert_called_once()

    def test_escape_minimizes_when_open(self) -> None:
        ov = self._key_overlay()
        self._send(ov, self._key(Qt.Key.Key_Escape))
        ov._minimize_chat.assert_called_once()

    def test_escape_does_nothing_when_already_minimized(self) -> None:
        ov = self._key_overlay()
        ov._chat_minimized = True
        self._send(ov, self._key(Qt.Key.Key_Escape))
        ov._minimize_chat.assert_not_called()

    def test_plain_key_is_not_hijacked(self) -> None:
        ov = self._key_overlay()
        self._send(ov, self._key(Qt.Key.Key_A))
        ov.toggle_chat.assert_not_called()
        ov._minimize_chat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
