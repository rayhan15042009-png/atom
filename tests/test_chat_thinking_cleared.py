"""A finished reply must clear the "Atom is thinking…" row.

The placeholder was only removed on errors or by the next request, so it stayed
on screen underneath every answer.
"""

from __future__ import annotations

import unittest

from atom import ChatController


class _Overlay:
    def __init__(self) -> None:
        self.thinking_calls: list[bool] = []
        self.messages: list[tuple[str, str]] = []

    def set_thinking(self, active: bool) -> None:
        self.thinking_calls.append(active)

    def add_chat_message(self, role: str, text: str, animate: bool = True) -> None:
        self.messages.append((role, text))


class _App:
    def __init__(self) -> None:
        self.overlay = _Overlay()
        self.anim_machine = None
        self.voice = None
        self.config = type("Cfg", (), {"tts_enabled": False})()


class TestThinkingCleared(unittest.TestCase):
    def test_reply_clears_the_placeholder_before_displaying(self) -> None:
        app = _App()
        controller = ChatController.__new__(ChatController)
        controller.app = app
        controller._processing = True
        controller._display_response("*tap* here is the answer")
        self.assertEqual(app.overlay.thinking_calls, [False])
        self.assertEqual(app.overlay.messages[-1], ("assistant", "*tap* here is the answer"))

    def test_empty_reply_leaves_state_alone(self) -> None:
        app = _App()
        controller = ChatController.__new__(ChatController)
        controller.app = app
        controller._display_response("")
        self.assertEqual(app.overlay.thinking_calls, [])


if __name__ == "__main__":
    unittest.main()
