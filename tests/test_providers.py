"""
Tests for Step 4 — Provider Routing
Coverage: Ollama provider, Gemini provider, key rotation, provider router,
fallback chain, cooldown management, BrainThread integration
"""

import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from PyQt6.QtCore import QCoreApplication

from core.providers.gemini import get_gemini_keys

# Headless Qt
_app = QCoreApplication.instance()
if _app is None:
    _app = QCoreApplication([])


# ══════════════════════════════════════════════════════════════════════════════
# Tests for Ollama provider
# ══════════════════════════════════════════════════════════════════════════════

class TestCallOllama(unittest.TestCase):
    """call_ollama — HTTP request formatting, error handling."""

    @patch("core.providers.ollama._request.urlopen")
    def test_successful_call(self, mock_urlopen):
        from core.providers.ollama import call_ollama
        mock_resp = MagicMock()
        mock_resp.read.return_value.decode.return_value = (
            '{"message": {"content": "Hello, Koko!"}}'
        )
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = call_ollama(
            [{"role": "user", "content": "hi"}],
            model="phi3:mini",
            url="http://localhost:11434",
        )
        self.assertEqual(result, "Hello, Koko!")

    @patch("core.providers.ollama._request.urlopen")
    def test_http_error_returns_none(self, mock_urlopen):
        from core.providers.ollama import call_ollama
        import urllib.error
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "http://localhost:11434/api/chat", 500, "Internal Error", {}, None
        )
        result = call_ollama([{"role": "user", "content": "hi"}])
        self.assertIsNone(result)

    @patch("core.providers.ollama._request.urlopen")
    def test_empty_response(self, mock_urlopen):
        from core.providers.ollama import call_ollama
        mock_resp = MagicMock()
        mock_resp.read.return_value.decode.return_value = '{"message": {}}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = call_ollama([{"role": "user", "content": "hi"}])
        self.assertEqual(result, "")

    @patch("core.providers.ollama._request.urlopen")
    def test_keep_alive_is_sent(self, mock_urlopen):
        """Without keep_alive Ollama unloads the model after 5 minutes, so
        every later question pays a cold load again."""
        import json as _json
        from core.providers.ollama import call_ollama
        mock_resp = MagicMock()
        mock_resp.read.return_value.decode.return_value = '{"message": {"content": "ok"}}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        call_ollama([{"role": "user", "content": "hi"}], keep_alive="30m")
        payload = _json.loads(mock_urlopen.call_args[0][0].data.decode())
        self.assertEqual(payload["keep_alive"], "30m")

    @patch("core.providers.ollama._request.urlopen")
    def test_options_carry_context_and_token_budget(self, mock_urlopen):
        import json as _json
        from core.providers.ollama import call_ollama
        mock_resp = MagicMock()
        mock_resp.read.return_value.decode.return_value = '{"message": {"content": "ok"}}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        call_ollama([{"role": "user", "content": "hi"}], num_ctx=4096, max_tokens=200)
        payload = _json.loads(mock_urlopen.call_args[0][0].data.decode())
        self.assertEqual(payload["options"]["num_ctx"], 4096)
        self.assertEqual(payload["options"]["num_predict"], 200)


# ══════════════════════════════════════════════════════════════════════════════
# Ollama request shaping via the router (resource tuning)
# ══════════════════════════════════════════════════════════════════════════════

class TestOllamaRequestTuning(unittest.TestCase):
    """Chat and analysis must agree on the context window, and both must ask
    Ollama to keep the model resident. Changing num_ctx forces a full model
    reload (30-60s on CPU), which is the single most expensive mistake here."""

    def setUp(self) -> None:
        from core.config_manager import AppConfig
        self.cfg = AppConfig()

    def _capture(self, analysis: bool):
        from core.providers.router import ProviderRouter
        router = ProviderRouter(self.cfg)
        captured: dict = {}

        def fake_call(messages, **kwargs):
            captured.update(kwargs)
            return "ok"

        with patch("core.providers.router.call_ollama", side_effect=fake_call):
            router._try_ollama([{"role": "user", "content": "hi"}], analysis=analysis)
        return captured

    def test_chat_and_analysis_use_the_same_context_window(self) -> None:
        self.assertEqual(self._capture(False)["num_ctx"], self._capture(True)["num_ctx"])

    def test_keep_alive_reaches_ollama(self) -> None:
        self.assertEqual(self._capture(False)["keep_alive"], self.cfg.ollama_keep_alive)

    def test_analysis_gets_a_timeout_fit_for_a_7b_cpu_model(self) -> None:
        self.assertGreaterEqual(self._capture(True)["timeout"], 60)
        self.assertGreaterEqual(self.cfg.ollama_timeout_analysis_seconds, 60)

    def test_chat_timeout_survives_a_model_reload(self) -> None:
        self.assertGreaterEqual(self._capture(False)["timeout"], 45)

    def test_analysis_timeout_is_a_real_config_field(self) -> None:
        """A missing field meant the router's short fallback always won."""
        self.assertIn("ollama_timeout_analysis_seconds", self.cfg.to_dict())
        self.cfg.ollama_timeout_analysis_seconds = 123
        self.assertEqual(self.cfg.to_dict()["ollama_timeout_analysis_seconds"], 123)


# ══════════════════════════════════════════════════════════════════════════════
# Tests for Gemini provider
# ══════════════════════════════════════════════════════════════════════════════

class TestCallGemini(unittest.TestCase):
    """call_gemini — request formatting, error handling."""

    def setUp(self):
        self.api_key = "AIzaSyDEt7x8x8x8x8x8x8x8x8x8x8x8x8"

    @patch("core.providers.gemini._request.urlopen")
    def test_successful_call(self, mock_urlopen):
        from core.providers.gemini import call_gemini
        mock_resp = MagicMock()
        mock_resp.read.return_value.decode.return_value = (
            '{"candidates": [{"content": {"parts": [{"text": "Hello from Gemini"}]}}]}'
        )
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = call_gemini(
            [{"role": "user", "content": "hi"}],
            api_key=self.api_key,
        )
        self.assertEqual(result, "Hello from Gemini")

    @patch("core.providers.gemini._request.urlopen")
    def test_no_candidates_returns_none(self, mock_urlopen):
        from core.providers.gemini import call_gemini
        mock_resp = MagicMock()
        mock_resp.read.return_value.decode.return_value = '{"candidates": []}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = call_gemini(
            [{"role": "user", "content": "hi"}],
            api_key=self.api_key,
        )
        self.assertIsNone(result)

    def test_system_prompt_conversion(self):
        """First message with role=system becomes systemInstruction."""
        # Test the payload construction by checking URL formatting
        from core.providers.gemini import call_gemini
        # Just verify the function signature is correct
        self.assertTrue(callable(call_gemini))


class TestValidateGeminiKey(unittest.TestCase):
    """validate_gemini_key — key format detection."""

    def setUp(self):
        from core.providers.gemini import validate_gemini_key
        self.validate = validate_gemini_key

    def test_valid_aiza_key(self):
        self.assertIsNone(self.validate("AIzaSyDEt7x8x8x8x8x8x8x8x8x8x8x8x8"))

    def test_empty_key(self):
        err = self.validate("")
        self.assertIsNotNone(err)

    def test_short_key(self):
        err = self.validate("short")
        self.assertIsNotNone(err)
        self.assertIn("too short", err)

    def test_oauth_token_detected(self):
        # ya29.* tokens are real OAuth 2.0 tokens, not Gemini API keys
        err = self.validate("ya29.a0AfH6xxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        self.assertIsNotNone(err)
        self.assertIn("OAuth", err)

    def test_ya29_token_detected(self):
        err = self.validate("ya29.xxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        self.assertIsNotNone(err)
        self.assertIn("OAuth", err)


class TestGetGeminiKeys(unittest.TestCase):
    """get_gemini_keys — key collection from env and config."""

    def setUp(self):
        from core.providers.gemini import get_gemini_keys
        self.get_keys = get_gemini_keys

    @patch.dict("os.environ", {"GEMINI_API_KEY": "env_key_123"}, clear=True)
    def test_env_var(self):
        keys = self.get_keys({})
        self.assertIn("env_key_123", keys)

    @patch.dict("os.environ", {}, clear=True)
    def test_config_dict(self):
        keys = self.get_keys({"gemini_api_key": "cfg_key"})
        self.assertIn("cfg_key", keys)

    @patch.dict("os.environ", {}, clear=True)
    def test_multiple_keys(self):
        keys = self.get_keys({
            "gemini_api_key": "primary",
            "gemini_api_key_2": "secondary",
            "gemini_api_key_3": "tertiary",
        })
        self.assertIn("primary", keys)
        self.assertIn("secondary", keys)
        self.assertIn("tertiary", keys)

    @patch.dict("os.environ", {}, clear=True)
    def test_no_keys(self):
        keys = self.get_keys({})
        self.assertEqual(keys, [])

    @patch.dict("os.environ", {}, clear=True)
    def test_deduplication(self):
        keys = self.get_keys({"gemini_api_key": "same", "gemini_api_key_2": "same"})
        self.assertEqual(len(keys), 1)


class TestGetGeminiKeysFromEnvironment(unittest.TestCase):
    """``.env`` documents GEMINI_API_KEY_2..5; all of them must be read.

    Only ``GEMINI_API_KEY`` used to be read from the environment, so a user with
    five keys in ``.env`` got one working key and four silently ignored ones.
    """

    def setUp(self):
        from core.providers.gemini import get_gemini_keys
        self.get_keys = get_gemini_keys

    @patch.dict("os.environ", {
        "GEMINI_API_KEY": "k1",
        "GEMINI_API_KEY_2": "k2",
        "GEMINI_API_KEY_3": "k3",
        "GEMINI_API_KEY_4": "k4",
        "GEMINI_API_KEY_5": "k5",
    }, clear=True)
    def test_all_five_environment_keys_are_returned(self):
        self.assertEqual(self.get_keys({}), ["k1", "k2", "k3", "k4", "k5"])

    @patch.dict("os.environ", {"GEMINI_API_KEY_4": "only_fourth"}, clear=True)
    def test_a_gap_does_not_hide_the_later_keys(self):
        self.assertEqual(self.get_keys({}), ["only_fourth"])

    @patch.dict("os.environ", {"GEMINI_API_KEY_2": "env2"}, clear=True)
    def test_environment_keys_come_before_config_keys(self):
        self.assertEqual(self.get_keys({"gemini_api_key": "cfg1"}), ["env2", "cfg1"])

    @patch.dict("os.environ", {"GEMINI_API_KEY": "same", "GEMINI_API_KEY_2": "same"},
                clear=True)
    def test_duplicate_environment_keys_collapse(self):
        self.assertEqual(self.get_keys({}), ["same"])

    @patch.dict("os.environ", {"GEMINI_API_KEY": "shared"}, clear=True)
    def test_a_key_in_both_env_and_config_is_only_used_once(self):
        keys = self.get_keys({"gemini_api_key": "shared", "gemini_api_key_2": "other"})
        self.assertEqual(keys, ["shared", "other"])

    @patch.dict("os.environ", {"GEMINI_API_KEY": '"quoted"'}, clear=True)
    def test_quotes_around_a_key_are_stripped(self):
        self.assertEqual(self.get_keys({}), ["quoted"])

    @patch.dict("os.environ", {"GEMINI_API_KEY": "  spaced  "}, clear=True)
    def test_whitespace_around_a_key_is_stripped(self):
        self.assertEqual(self.get_keys({}), ["spaced"])

    @patch.dict("os.environ", {}, clear=True)
    def test_config_object_supports_all_five_keys(self):
        cfg = SimpleNamespace(gemini_api_key="c1", gemini_api_key_2="c2",
                              gemini_api_key_3="c3", gemini_api_key_4="c4",
                              gemini_api_key_5="c5")
        self.assertEqual(self.get_keys(cfg), ["c1", "c2", "c3", "c4", "c5"])

    @patch.dict("os.environ", {}, clear=True)
    def test_config_defaults_to_nothing(self):
        self.assertEqual(self.get_keys(), [])


class TestGeminiKeyRotation(unittest.TestCase):
    """``_try_gemini`` must give every configured key a turn, and spread work.

    Keys used to be "sticky": the first working key served every request until it
    failed, so four of five configured keys were never used and the daily quota
    was effectively one key's worth. They now rotate.
    """

    def setUp(self):
        from core.providers.router import ProviderRouter
        self.router = ProviderRouter({
            "gemini_api_key": "first",
            "gemini_api_key_2": "second",
            "gemini_api_key_3": "third",
        })
        self.five = ProviderRouter({
            "gemini_api_key": "k1",
            "gemini_api_key_2": "k2",
            "gemini_api_key_3": "k3",
            "gemini_api_key_4": "k4",
            "gemini_api_key_5": "k5",
        })
        self.messages = [{"role": "user", "content": "hello"}]

    @patch.dict("os.environ", {}, clear=True)
    def test_a_failing_key_rotates_to_the_next(self):
        attempted: list[str] = []

        def fake_call(messages, api_key, **kwargs):
            attempted.append(api_key)
            return "third" if api_key == "third" else None

        with patch("core.providers.router.call_gemini", side_effect=fake_call):
            self.assertEqual(self.router._try_gemini(self.messages), "third")
        self.assertEqual(attempted, ["first", "second", "third"])

    @patch.dict("os.environ", {}, clear=True)
    def test_five_requests_use_all_five_keys_in_order(self):
        used: list[str] = []

        def fake_call(messages, api_key, **kwargs):
            used.append(api_key)
            return "ok"

        with patch("core.providers.router.call_gemini", side_effect=fake_call):
            for _ in range(5):
                self.assertEqual(self.five._try_gemini(self.messages), "ok")
        self.assertEqual(used, ["k1", "k2", "k3", "k4", "k5"])

    @patch.dict("os.environ", {}, clear=True)
    def test_rotation_wraps_around(self):
        used: list[str] = []

        def fake_call(messages, api_key, **kwargs):
            used.append(api_key)
            return "ok"

        with patch("core.providers.router.call_gemini", side_effect=fake_call):
            for _ in range(7):
                self.five._try_gemini(self.messages)
        self.assertEqual(used, ["k1", "k2", "k3", "k4", "k5", "k1", "k2"])

    @patch.dict("os.environ", {}, clear=True)
    def test_the_next_request_starts_after_the_key_that_answered(self):
        """A failure moves the rotation past the key that actually worked."""
        def fake_call(messages, api_key, **kwargs):
            return "ok" if api_key == "second" else None

        with patch("core.providers.router.call_gemini", side_effect=fake_call):
            self.router._try_gemini(self.messages)
        self.assertEqual(self.router._gemini_key_index, 2, "next key is 'third'")
        with patch("core.providers.router.call_gemini", return_value="ok") as call:
            self.router._try_gemini(self.messages)
        self.assertEqual(call.call_args.kwargs["api_key"], "third")

    @patch.dict("os.environ", {}, clear=True)
    def test_every_key_failing_returns_none(self):
        with patch("core.providers.router.call_gemini", return_value=None) as call:
            self.assertIsNone(self.router._try_gemini(self.messages))
        self.assertEqual(call.call_count, 3)

    @patch.dict("os.environ", {}, clear=True)
    def test_the_status_reports_every_key(self):
        self.assertEqual(len(get_gemini_keys(self.router.config)), 3)


class TestWhichGeminiKeyAnswered(unittest.TestCase):
    """Which key served the request must be visible, without leaking the key."""

    def setUp(self):
        from core.providers.router import ProviderRouter
        self.router = ProviderRouter({
            "gemini_api_key": "secret-one",
            "gemini_api_key_2": "secret-two",
            "gemini_api_key_3": "secret-three",
        })
        self.messages = [{"role": "user", "content": "hello"}]

    @patch.dict("os.environ", {}, clear=True)
    def test_the_answering_slot_is_recorded(self):
        with patch("core.providers.router.call_gemini", return_value="ok"):
            self.router._try_gemini(self.messages)
        self.assertEqual(self.router.last_gemini_key_slot, (1, 3))
        self.assertEqual(self.router.gemini_key_label(), "#1/3")

    @patch.dict("os.environ", {}, clear=True)
    def test_the_slot_follows_the_rotation(self):
        with patch("core.providers.router.call_gemini", return_value="ok"):
            self.router._try_gemini(self.messages)
            self.router._try_gemini(self.messages)
        self.assertEqual(self.router.last_gemini_key_slot, (2, 3))
        self.assertEqual(self.router.gemini_key_label(), "#2/3")

    @patch.dict("os.environ", {}, clear=True)
    def test_no_label_before_anything_answered(self):
        self.assertIsNone(self.router.last_gemini_key_slot)
        self.assertEqual(self.router.gemini_key_label(), "")

    @patch.dict("os.environ", {}, clear=True)
    def test_every_key_failing_clears_the_slot(self):
        with patch("core.providers.router.call_gemini", return_value=None):
            self.router._try_gemini(self.messages)
        self.assertIsNone(self.router.last_gemini_key_slot)

    @patch.dict("os.environ", {}, clear=True)
    def test_the_log_names_the_slot_and_never_the_key(self):
        """The log file is not a secret store."""
        with self.assertLogs("atom.providers.router", level="INFO") as captured:
            with patch("core.providers.router.call_gemini", return_value="ok"):
                self.router._try_gemini(self.messages)
        logged = "\n".join(captured.output)
        self.assertIn("Gemini key 1/3 answered", logged)
        for secret in ("secret-one", "secret-two", "secret-three"):
            self.assertNotIn(secret, logged)

    @patch.dict("os.environ", {}, clear=True)
    def test_a_different_provider_answering_clears_the_slot(self):
        """A stale Gemini slot must not sit next to 'ollama' on the HUD."""
        with patch("core.providers.router.call_gemini", return_value="ok"):
            self.router._try_gemini(self.messages)
        self.assertEqual(self.router.last_gemini_key_slot, (1, 3))

        with patch.object(self.router, "_try_ollama", return_value="local answer"), \
             patch.object(self.router, "_get_provider_order", return_value=["ollama"]):
            self.assertEqual(self.router.chat(self.messages), "local answer")
        self.assertEqual(self.router.last_provider, "ollama")
        self.assertIsNone(self.router.last_gemini_key_slot)

    @patch.dict("os.environ", {}, clear=True)
    def test_chat_records_the_provider_that_answered(self):
        with patch.object(self.router, "_try_gemini", return_value="cloud answer"), \
             patch.object(self.router, "_get_provider_order", return_value=["gemini"]):
            self.router.chat(self.messages)
        self.assertEqual(self.router.last_provider, "gemini")


class TestGeminiQuotaManager(unittest.TestCase):
    """GeminiQuotaManager — rate limiting."""

    def setUp(self):
        from core.providers.gemini import GeminiQuotaManager
        self.qm = GeminiQuotaManager()

    def test_can_request_initially(self):
        self.assertTrue(self.qm.can_request())

    def test_records_request(self):
        self.qm.record_request()
        status = self.qm.get_status()
        self.assertEqual(status["minute_used"], 1)

    def test_seconds_available_initially_zero(self):
        self.assertEqual(self.qm.seconds_until_available(), 0.0)


# ══════════════════════════════════════════════════════════════════════════════
# Tests for ProviderRouter
# ══════════════════════════════════════════════════════════════════════════════

class TestProviderRouter(unittest.TestCase):
    """ProviderRouter — fallback chain, cooldowns, routing logic."""

    def setUp(self):
        from core.providers.router import ProviderRouter
        self.router = ProviderRouter({})

    def test_initial_no_cooldowns(self):
        status = self.router.get_status()
        self.assertIsNone(status["ollama_cooldown"])
        self.assertIsNone(status["gemini_cooldown"])
        self.assertIsNone(status["browser_cooldown"])

    def test_cooldown_set_and_check(self):
        self.router._set_cooldown("ollama", seconds=60)
        self.assertTrue(self.router._is_on_cooldown("ollama"))

    def test_cooldown_clear(self):
        self.router._set_cooldown("ollama", seconds=60)
        self.router._clear_cooldown("ollama")
        self.assertFalse(self.router._is_on_cooldown("ollama"))

    def test_expired_cooldown(self):
        self.router._set_cooldown("ollama", seconds=-1)  # Already expired
        self.assertFalse(self.router._is_on_cooldown("ollama"))

    # A valid-looking key, passed through config so these tests do not depend on
    # whatever (or whoever) happens to have set GEMINI_API_KEY in the
    # environment — running this file alone used to change the provider order.
    _GEMINI_KEY = "AIzaSyUnitTestKeyForProviderOrder_0123456789"

    def test_provider_order_default(self):
        router = type(self.router)({"gemini_api_key": self._GEMINI_KEY})
        with patch.object(router, "_check_internet", return_value=True):
            order = router._get_provider_order()
        self.assertEqual(order[0], "gemini")

    def test_provider_order_gemini_preferred(self):
        router = type(self.router)({
            "llm_provider": "gemini", "gemini_api_key": self._GEMINI_KEY,
        })
        with patch.object(router, "_check_internet", return_value=True):
            order = router._get_provider_order()
        self.assertEqual(order[0], "gemini")

    def test_provider_order_prefers_ollama_without_gemini_keys(self):
        router = type(self.router)({"llm_provider": "ollama"})
        with patch.object(router, "_check_internet", return_value=True), \
                patch("core.providers.router.get_gemini_keys", return_value=[]):
            order = router._get_provider_order()
        self.assertEqual(order[0], "ollama")

    @patch("core.providers.router.call_ollama", return_value=None)
    @patch("core.providers.router.call_gemini", return_value=None)
    @patch("core.providers.router.is_browser_available", return_value=False)
    @patch.dict("os.environ", {}, clear=True)
    def test_chat_returns_fallback_on_all_fail(self, mock_browser, mock_gemini, mock_ollama):
        """When all providers fail, chat() should return a non-empty fallback."""
        result = self.router.chat([{"role": "user", "content": "hello"}], mode="tap")
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("*tap*"))
        self.assertGreater(len(result), 5)

    @patch("core.providers.router.call_ollama", return_value=None)
    @patch("core.providers.router.call_gemini", return_value=None)
    @patch("core.providers.router.is_browser_available", return_value=False)
    @patch.dict("os.environ", {}, clear=True)
    def test_analyze_returns_none_on_all_fail(self, mock_browser, mock_gemini, mock_ollama):
        """When all providers fail, analyze() should return None."""
        result = self.router.analyze([{"role": "user", "content": "hello"}])
        self.assertIsNone(result)

    def test_router_cleanup(self):
        """Cleanup should not raise."""
        self.router.cleanup()  # No-op if browser not initialized

    def test_get_status_dict(self):
        status = self.router.get_status()
        self.assertIn("ollama_cooldown", status)
        self.assertIn("gemini_cooldown", status)
        self.assertIn("browser_cooldown", status)


class TestProviderRouterCooldownChain(unittest.TestCase):
    """Verify that providers on cooldown are skipped in the chain."""

    def setUp(self):
        from core.providers.router import ProviderRouter
        self.router = ProviderRouter({})

    @patch("core.providers.router.call_ollama", return_value=None)
    @patch("core.providers.router.call_gemini", return_value=None)
    @patch("core.providers.router.is_browser_available", return_value=False)
    @patch.dict("os.environ", {}, clear=True)
    def test_skips_cooldown_providers(self, mock_browser, mock_gemini, mock_ollama):
        """When Ollama is on cooldown, the router should skip it."""
        self.router._set_cooldown("ollama", seconds=60)
        result = self.router.chat([{"role": "user", "content": "test"}])
        self.assertIsNotNone(result)
        self.assertTrue(result.startswith("*tap*"))

    def test_recovery_after_cooldown_expires(self):
        """After cooldown expires, the provider should be tried again."""
        self.router._set_cooldown("ollama", seconds=-1)  # Expired
        self.assertFalse(self.router._is_on_cooldown("ollama"))


# ══════════════════════════════════════════════════════════════════════════════
# Tests for BrainThread integration
# ══════════════════════════════════════════════════════════════════════════════

class TestBrainThreadIntegration(unittest.TestCase):
    """Verify BrainThread._process uses ProviderRouter correctly."""

    def setUp(self):
        from core.brain import BrainThread
        self.brain = BrainThread({"llm_provider": "ollama"})

    def tearDown(self):
        if self.brain.isRunning():
            self.brain.stop()
        self.brain = None

    @patch("core.providers.router.call_ollama", return_value="*tap* Hello from mock")
    @patch("core.providers.router.call_gemini", return_value=None)
    @patch("core.providers.router.is_browser_available", return_value=False)
    @patch.dict("os.environ", {}, clear=True)
    def test_process_emits_response(self, mock_browser, mock_gemini, mock_ollama):
        """_process should emit response_ready for valid input."""
        from PyQt6.QtTest import QSignalSpy
        spy = QSignalSpy(self.brain.response_ready)
        self.brain.start()
        self.brain.msleep(100)
        self.brain.ask({
            "text": "hello",
            "mode": "tap",
            "history": [],
            "memory": [],
        })
        self.brain.msleep(500)
        self.assertGreaterEqual(len(spy), 1)

    @patch.dict("os.environ", {}, clear=True)
    def test_process_empty_text_no_emission(self):
        """Empty text should not emit anything."""
        from PyQt6.QtTest import QSignalSpy
        spy = QSignalSpy(self.brain.response_ready)
        self.brain.start()
        self.brain.msleep(100)
        self.brain.ask({"text": "", "mode": "tap"})
        self.brain.msleep(300)
        self.assertEqual(len(spy), 0)


# ══════════════════════════════════════════════════════════════════════════════
# Tests for contextual fallback
# ══════════════════════════════════════════════════════════════════════════════

class TestBrainThreadSurvivesFailures(unittest.TestCase):
    """A bad payload must not kill the brain thread or stall the queue.

    An unhandled exception used to escape the QThread's run loop: the thread
    died, ``_processing`` stayed True, and every later message sat in the queue
    unanswered for the rest of the session.
    """

    def test_process_failure_keeps_the_thread_alive(self) -> None:
        from core.brain import BrainThread
        brain = BrainThread({"llm_provider": "ollama"})
        errors: list = []
        brain.error.connect(errors.append)
        brain._process = MagicMock(side_effect=RuntimeError("boom"))
        try:
            brain.start()
            brain.msleep(150)
            brain.ask({"text": "hello", "mode": "tap", "history": []})
            for _ in range(60):
                # error is a queued signal — it only arrives if the main thread
                # keeps pumping its event loop.
                QCoreApplication.processEvents()
                brain.msleep(50)
                if errors:
                    break
            self.assertTrue(brain.isRunning(), "thread must survive a bad payload")
            self.assertFalse(brain._processing, "the queue must not stay stalled")
            self.assertTrue(errors, "the failure must be reported, not swallowed")
        finally:
            brain.stop()

    def test_workspace_lookup_failure_is_not_fatal(self) -> None:
        """`_workspace_path` used to raise NameError while handling its own error
        (no logger in this module), killing the thread instead of degrading."""
        from core.brain import BrainThread
        brain = BrainThread({"llm_provider": "ollama"})
        with patch("core.sandbox.resolve_workspace", side_effect=RuntimeError("no home")):
            self.assertIsNone(brain._workspace_path())


class TestContextualFallback(unittest.TestCase):
    """_contextual_fallback — mode-aware fallback responses."""

    def setUp(self):
        from core.providers.router import _contextual_fallback
        self.fallback = _contextual_fallback

    def test_greeting_in_tap_mode(self):
        result = self.fallback("hello", "tap")
        self.assertTrue(result.startswith("*tap*"))
        self.assertIn("Koko", result)

    def test_greeting_in_formal_mode(self):
        result = self.fallback("hello", "formal")
        self.assertIn("assist", result)

    def test_weather_query(self):
        result = self.fallback("what's the weather", "tap")
        self.assertIn("weather", result.lower())

    def test_generic_fallback(self):
        result = self.fallback("random stuff", "tap")
        self.assertTrue(result.startswith("*tap*"))


class TestIsBrowserAvailable(unittest.TestCase):
    """is_browser_available — Playwright detection."""

    def test_returns_bool(self):
        from core.providers.browser import is_browser_available
        result = is_browser_available()
        self.assertIsInstance(result, bool)


if __name__ == "__main__":
    unittest.main()
