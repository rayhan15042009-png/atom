"""Atom must search for current facts instead of guessing."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from core.brain import BrainThread


class TestTimeSensitiveQuestions(unittest.TestCase):
    def test_prices_and_news_trigger_a_search(self) -> None:
        for question in (
            "what is the current price of bitcoin",
            "any news about the election today?",
            "how much does a 4090 cost right now",
            "latest python release version",
        ):
            self.assertTrue(BrainThread._looks_time_sensitive(question), question)

    def test_ordinary_chat_does_not(self) -> None:
        for question in ("tell me a joke", "write a haiku about rain", "explain recursion"):
            self.assertFalse(BrainThread._looks_time_sensitive(question), question)

    def test_commands_are_ignored(self) -> None:
        self.assertFalse(BrainThread._looks_time_sensitive("/news"))
        self.assertFalse(BrainThread._looks_time_sensitive("exit"))


class TestToolResultCondensing(unittest.TestCase):
    def test_short_results_pass_through(self) -> None:
        brain = BrainThread.__new__(BrainThread)
        brain._router = None
        self.assertEqual(brain._condense_tool_result("short text", "q"), "short text")

    def test_long_results_use_the_analysis_model(self) -> None:
        brain = BrainThread.__new__(BrainThread)
        brain.config = {}
        router = unittest.mock.MagicMock()
        router.analyze.return_value = "- fact one\n- fact two"
        brain._router = router
        out = brain._condense_tool_result("x" * 5000, "what happened?")
        self.assertIn("fact one", out)
        router.analyze.assert_called_once()

    def test_analysis_failure_falls_back_to_raw_text(self) -> None:
        brain = BrainThread.__new__(BrainThread)
        brain.config = {}
        router = unittest.mock.MagicMock()
        router.analyze.return_value = None
        brain._router = router
        out = brain._condense_tool_result("y" * 5000, "q")
        self.assertTrue(out.startswith("y" * 10))


class TestRememberAction(unittest.TestCase):
    def test_remember_is_parsed(self) -> None:
        brain = BrainThread.__new__(BrainThread)
        actions = brain._parse_actions("sure [remember: Koko likes tea]")
        self.assertEqual(actions, [{"action": "remember", "fact": "Koko likes tea"}])

    def test_fact_is_stored_once(self) -> None:
        brain = BrainThread.__new__(BrainThread)
        brain.memory = {"facts": []}
        brain.fact_learned = unittest.mock.MagicMock()
        brain._remember_fact("Bitcoin was 81k on Oct 23 2026")
        brain._remember_fact("Bitcoin was 81k on Oct 23 2026")
        self.assertEqual(len(brain.memory["facts"]), 1)
        brain.fact_learned.emit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
