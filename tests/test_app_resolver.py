"""`[open: …]` must resolve loose names instead of failing in the shell."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import app_resolver


class TestResolve(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.learned = Path(self.tmp.name) / "learned.json"
        self._patch = patch.object(app_resolver, "LEARNED_PATH", self.learned)
        self._patch.start()
        app_resolver._learned_cache = None

    def tearDown(self) -> None:
        self._patch.stop()
        app_resolver._learned_cache = None
        self.tmp.cleanup()

    def test_url_passthrough(self) -> None:
        self.assertEqual(app_resolver.resolve("https://example.com")["kind"], "url")

    def test_known_site(self) -> None:
        result = app_resolver.resolve("google", allow_web_lookup=False)
        self.assertEqual(result["kind"], "url")
        self.assertEqual(result["target"], "https://www.google.com")

    def test_windows_app_alias(self) -> None:
        result = app_resolver.resolve("notepad", allow_web_lookup=False)
        self.assertEqual(result["kind"], "app")
        self.assertEqual(result["target"], "notepad.exe")

    def test_protocol_alias(self) -> None:
        self.assertEqual(app_resolver.resolve("settings", allow_web_lookup=False)["kind"], "uri")

    def test_unknown_without_lookup_returns_none(self) -> None:
        self.assertIsNone(app_resolver.resolve("zz-not-a-real-app-zz", allow_web_lookup=False))

    def test_learning_is_persisted_and_reused(self) -> None:
        app_resolver.remember("someapp", "url", "https://someapp.example", "web")
        self.assertTrue(self.learned.exists())
        saved = json.loads(self.learned.read_text(encoding="utf-8"))
        self.assertEqual(saved["someapp"]["target"], "https://someapp.example")

        app_resolver._learned_cache = None  # force a fresh read
        with patch.object(app_resolver, "_web_guess") as web:
            result = app_resolver.resolve("someapp")
        self.assertEqual(result["target"], "https://someapp.example")
        web.assert_not_called()

    def test_web_guess_is_learned(self) -> None:
        with patch.object(app_resolver, "_web_guess", return_value="https://learned.example"):
            result = app_resolver.resolve("brand-new-thing")
        self.assertEqual(result["source"], "web")
        app_resolver._learned_cache = None
        self.assertEqual(app_resolver.load_learned()["brand-new-thing"]["kind"], "url")


class TestLearningFromFailures(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.learned = Path(self.tmp.name) / "learned.json"
        self._patch = patch.object(app_resolver, "LEARNED_PATH", self.learned)
        self._patch.start()
        app_resolver._learned_cache = None

    def tearDown(self) -> None:
        self._patch.stop()
        app_resolver._learned_cache = None
        self.tmp.cleanup()

    def test_obvious_typo_is_learned_without_a_web_lookup(self) -> None:
        with patch.object(app_resolver, "_web_guess") as web:
            result = app_resolver.resolve("gogle")
        self.assertEqual(result["target"], "https://www.google.com")
        self.assertEqual(result["source"], "fuzzy")
        web.assert_not_called()
        self.assertEqual(app_resolver.load_learned()["gogle"]["kind"], "url")

    def test_loose_typo_uses_the_web_once_then_is_instant(self) -> None:
        with patch.object(app_resolver, "_web_guess", return_value=None) as web:
            first = app_resolver.resolve("googel")
        self.assertEqual(first["target"], "https://www.google.com")
        self.assertEqual(web.call_count, 1)

        with patch.object(app_resolver, "_web_guess") as second:
            again = app_resolver.resolve("googel")
        second.assert_not_called()
        self.assertEqual(again["target"], "https://www.google.com")

    def test_repeated_failure_stops_asking_the_web(self) -> None:
        with patch.object(app_resolver, "_web_guess", return_value=None) as web:
            self.assertIsNone(app_resolver.resolve("zz-not-a-real-app-zz"))
            self.assertIsNone(app_resolver.resolve("zz-not-a-real-app-zz"))
            self.assertEqual(web.call_count, app_resolver.NEGATIVE_SKIP_AFTER)
            self.assertIsNone(app_resolver.resolve("zz-not-a-real-app-zz"))
            self.assertEqual(web.call_count, app_resolver.NEGATIVE_SKIP_AFTER)

        entry = app_resolver.load_learned()["zz-not-a-real-app-zz"]
        self.assertEqual(entry["kind"], app_resolver.UNRESOLVED)
        self.assertGreaterEqual(entry["attempts"], app_resolver.NEGATIVE_SKIP_AFTER)
        self.assertTrue(app_resolver.is_locally_blacklisted("zz-not-a-real-app-zz"))

    def test_failed_name_heals_when_it_resolves_locally(self) -> None:
        app_resolver.record_failure("coolapp")
        with patch.object(app_resolver, "find_start_menu_shortcut",
                          return_value=r"C:\Start Menu\Cool App.lnk"):
            result = app_resolver.resolve("coolapp")
        self.assertEqual(result["kind"], "path")
        self.assertEqual(app_resolver.load_learned()["coolapp"]["kind"], "path")

    def test_learned_entry_counts_hits(self) -> None:
        app_resolver.remember("site", "url", "https://site.example", "web")
        app_resolver.resolve("site")
        app_resolver.resolve("site")
        self.assertEqual(app_resolver.load_learned()["site"]["hits"], 3)

    def test_suggest_offers_close_names(self) -> None:
        self.assertIn("google", app_resolver.suggest("googel"))
        self.assertIn("notepad", app_resolver.suggest("notpad"))
        self.assertEqual(app_resolver.suggest("zzzzzzzzzz"), [])


class TestHandsOpenFeedback(unittest.TestCase):
    def test_unknown_name_reports_suggestions(self) -> None:
        from core.hands import ActionExecutor

        executor = ActionExecutor.__new__(ActionExecutor)
        with patch("core.app_resolver.resolve", return_value=None), \
                patch("core.app_resolver.suggest", return_value=["google"]):
            with self.assertRaises(ValueError) as ctx:
                executor._execute_open({"target": "googel"})
        self.assertIn("google", str(ctx.exception))

    def test_launch_failure_forgets_the_mapping(self) -> None:
        from core.hands import ActionExecutor

        executor = ActionExecutor.__new__(ActionExecutor)
        with patch("core.app_resolver.resolve",
                   return_value={"kind": "path", "target": "C:/gone.exe", "source": "learned"}), \
                patch("core.app_resolver.launch", side_effect=OSError("missing")), \
                patch("core.app_resolver.record_failure") as record:
            with self.assertRaises(OSError):
                executor._execute_open({"target": "gone"})
        record.assert_called_once_with("gone")


if __name__ == "__main__":
    unittest.main()
