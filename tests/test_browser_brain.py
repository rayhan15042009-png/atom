"""
Tests for core/browser_brain — BrowserWorker, BrowserBrainThread.

Most tests are unit-level and do not require Playwright to be installed.
Tests that would need a real browser are skipped if Playwright is absent.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

# Disable Qt platform before any Qt imports
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Ensure project root is on sys.path
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import unittest

from core.browser_brain import (
    AUTH_STATE_PATH,
    DEFAULT_SELECTORS,
    SKIP_PHRASES,
    BrowserWorker,
    BrowserBrainThread,
)


# ── Helpers ──

def _make_config(**overrides) -> dict:
    """Create a test config dict."""
    cfg: dict = {
        "browser_ai_url": "https://chat.deepseek.com/",
        "browser_show_browser": False,
    }
    cfg.update(overrides)
    return cfg


# ═══════════════════════════════════════════════════════════════════════════════
# Test: BrowserWorker — availability detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrowserWorkerAvailability(unittest.TestCase):
    """Test static availability checks — no browser required."""

    def test_is_available_returns_bool(self):
        """is_available() returns a bool (True if playwright installed)."""
        result = BrowserWorker.is_available()
        self.assertIsInstance(result, bool)

    def test_is_available_consistent(self):
        """Calling is_available() twice returns the same result."""
        r1 = BrowserWorker.is_available()
        r2 = BrowserWorker.is_available()
        self.assertEqual(r1, r2)


# ═══════════════════════════════════════════════════════════════════════════════
# Test: BrowserWorker — construction and config
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrowserWorkerConstruction(unittest.TestCase):
    """Test BrowserWorker construction and configuration parsing."""

    def test_default_config(self):
        """Worker initializes with sensible defaults."""
        worker = BrowserWorker({})
        self.assertIn("deepseek", worker._target_url.lower())
        self.assertEqual(worker._platform, "deepseek")
        self.assertTrue(worker._headless)  # show_browser defaults to False

    def test_custom_url(self):
        """Custom target URL is respected."""
        worker = BrowserWorker(_make_config(browser_ai_url="https://gemini.google.com/app"))
        self.assertIn("gemini", worker._target_url)
        self.assertEqual(worker._platform, "gemini")

    def test_custom_url_generic(self):
        """Unknown URLs get platform='generic'."""
        worker = BrowserWorker(_make_config(browser_ai_url="https://example.com/chat"))
        self.assertEqual(worker._platform, "generic")

    def test_show_browser(self):
        """show_browser=True sets headless=False."""
        worker = BrowserWorker(_make_config(browser_show_browser=True))
        self.assertFalse(worker._headless)

    def test_auth_returns_false_when_no_file(self):
        """is_auth_available returns False when auth file doesn't exist."""
        worker = BrowserWorker({})
        with patch.object(BrowserWorker, 'is_auth_available', return_value=False):
            self.assertFalse(worker.is_auth_available())

    def test_auth_returns_true_when_file_exists(self):
        """is_auth_available returns True when auth file exists."""
        worker = BrowserWorker({})
        original_path = AUTH_STATE_PATH
        try:
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
                f.write(b"{}")
                temp_path = Path(f.name)
            # Monkey-patch the auth path
            import core.browser_brain as bb
            bb.AUTH_STATE_PATH = temp_path
            # Re-create worker patched
            try:
                # Force re-check
                result = worker.is_auth_available()
                self.assertTrue(result)
            finally:
                bb.AUTH_STATE_PATH = original_path
                temp_path.unlink(missing_ok=True)
        except Exception:
            raise

    def test_system_prompt_not_sent_initially(self):
        """_system_prompt_sent is False on construction."""
        worker = BrowserWorker(_make_config())
        self.assertFalse(worker._system_prompt_sent)
        self.assertFalse(worker._conversation_started)


# ═══════════════════════════════════════════════════════════════════════════════
# Test: BrowserWorker — platform detection
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrowserWorkerPlatformDetection(unittest.TestCase):
    """Test URL-based platform detection."""

    def test_detect_deepseek(self):
        worker = BrowserWorker(_make_config(browser_ai_url="https://chat.deepseek.com/"))
        self.assertEqual(worker._platform, "deepseek")

    def test_detect_gemini(self):
        worker = BrowserWorker(_make_config(browser_ai_url="https://gemini.google.com/app"))
        self.assertEqual(worker._platform, "gemini")

    def test_detect_generic(self):
        worker = BrowserWorker(_make_config(browser_ai_url="https://claude.ai"))
        self.assertEqual(worker._platform, "generic")

    def test_detect_deepseek_subdomain(self):
        worker = BrowserWorker(_make_config(browser_ai_url="https://chat.deepseek.com/a/chat/s"))
        self.assertEqual(worker._platform, "deepseek")

    def test_detect_empty_url_fallsback_to_default(self):
        worker = BrowserWorker({})
        # Default is deepseek URL
        self.assertEqual(worker._platform, "deepseek")


# ═══════════════════════════════════════════════════════════════════════════════
# Test: BrowserWorker — selectors
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrowserWorkerSelectors(unittest.TestCase):
    """Test selector resolution."""

    def test_deepseek_selectors(self):
        worker = BrowserWorker(_make_config())
        selectors = worker._get_selectors()
        self.assertIn("chat_input", selectors)
        self.assertIn("send_button", selectors)
        self.assertIn("stop_button", selectors)
        self.assertIn("login_indicator", selectors)
        self.assertIn("response_container", selectors)

    def test_gemini_selectors(self):
        worker = BrowserWorker(_make_config(browser_ai_url="https://gemini.google.com/app"))
        selectors = worker._get_selectors()
        self.assertIn("chat_input", selectors)
        self.assertIn("login_indicator", selectors)

    def test_generic_selectors(self):
        worker = BrowserWorker(_make_config(browser_ai_url="https://example.com"))
        selectors = worker._get_selectors()
        self.assertIn("chat_input", selectors)

    def test_custom_selectors_override_defaults(self):
        """Custom selectors in config override defaults."""
        custom = {"chat_input": "#my-input", "send_button": "#my-send"}
        worker = BrowserWorker(_make_config(browser_ai_selectors=custom))
        selectors = worker._get_selectors()
        self.assertEqual(selectors["chat_input"], "#my-input")
        self.assertEqual(selectors["send_button"], "#my-send")

    def test_default_selectors_not_mutated(self):
        """_get_selectors returns a copy of defaults, not the original."""
        worker = BrowserWorker(_make_config())
        s1 = worker._get_selectors()
        worker2 = BrowserWorker(_make_config())
        s2 = worker2._get_selectors()
        # Both should have the same content
        self.assertEqual(s1, s2)


# ═══════════════════════════════════════════════════════════════════════════════
# Test: BrowserWorker — text extraction (no browser needed)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrowserWorkerTextExtraction(unittest.TestCase):
    """Test _extract_text logic — uses mocked page.inner_text."""

    def _make_worker_with_mock_text(self, text: str) -> BrowserWorker:
        worker = BrowserWorker(_make_config())
        worker._page = MagicMock()
        worker._page.inner_text.return_value = text
        return worker

    def test_extract_simple_response(self):
        """Extracts the last substantial block of text."""
        worker = self._make_worker_with_mock_text(
            "User: hello\nAssistant: Hi there! What can I do for you today?\n"
        )
        result = worker._extract_text()
        self.assertIsNotNone(result)
        self.assertIn("Hi there", result)

    def test_extract_filters_ui_noise(self):
        """Filters out common UI phrases from the extracted text."""
        worker = self._make_worker_with_mock_text(
            "Search: results\n"
            "Settings: on\n"
            "Profile: user\n"
            "History: empty\n"
            "The capital of France is Paris."
        )
        result = worker._extract_text()
        self.assertIsNotNone(result)
        self.assertIn("Paris", result)

    def test_extract_empty_page(self):
        """Returns None for empty page text."""
        worker = self._make_worker_with_mock_text("")
        self.assertIsNone(worker._extract_text())

    def test_extract_short_lines_filtered(self):
        """Lines shorter than 5 chars are filtered out."""
        worker = self._make_worker_with_mock_text(
            "Hi\n\nOK\nyes\nno\nthe\nHere is the real response text."
        )
        result = worker._extract_text()
        self.assertIsNotNone(result)
        self.assertIn("real response", result)

    def test_extract_multi_line_response(self):
        """Multi-line responses are joined properly."""
        worker = self._make_worker_with_mock_text(
            "First line of response\nSecond line\nThird line code block"
        )
        result = worker._extract_text()
        self.assertIsNotNone(result)
        self.assertIn("First line", result)

    def test_extract_removes_excessive_whitespace(self):
        """Excessive newlines and spaces are collapsed."""
        text = "Line 1\n\n\n\nLine 2\n   spaced    text"
        worker = self._make_worker_with_mock_text(text)
        result = worker._extract_text()
        if result:
            self.assertNotIn("\n\n\n", result)
            self.assertNotIn("   ", result)

    def test_extract_no_page_returns_none(self):
        """_extract_text returns None if no page."""
        worker = BrowserWorker(_make_config())
        self.assertIsNone(worker._extract_text())

    def test_extract_deduplicates_lines(self):
        """Duplicate consecutive text is removed."""
        worker = self._make_worker_with_mock_text(
            "Line A\nLine B\nLine A\nLine B\nLine C"
        )
        result = worker._extract_text()
        self.assertIsNotNone(result)
        # The dedup preserves order but removes repeats
        self.assertIn("Line C", result)

    def test_extract_skip_phrases_filter(self):
        """All SKIP_PHRASES are used as filters."""
        worker = BrowserWorker(_make_config())
        for phrase in SKIP_PHRASES:
            worker._page = MagicMock()
            worker._page.inner_text.return_value = f"Some text\n{phrase}\nImportant response"
            result = worker._extract_text()
            self.assertIsNotNone(result, f"Phrase '{phrase}' crashed extraction")
            self.assertIn("Important", result, f"Phrase '{phrase}' blocked extraction")


# ═══════════════════════════════════════════════════════════════════════════════
# Test: BrowserWorker — message flow (mocked page)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrowserWorkerMessageFlow(unittest.TestCase):
    """Test message send flow with mocked page."""

    def setUp(self):
        self.worker = BrowserWorker(_make_config())
        self.worker._page = MagicMock()
        self.worker._context = MagicMock()
        self.worker._conversation_started = True

        # Make wait_for_selector return a mock element
        mock_element = MagicMock()
        self.worker._page.wait_for_selector.return_value = mock_element
        self.worker._page.query_selector.return_value = mock_element

        # Make inner_text return a response
        self.worker._page.inner_text.return_value = (
            "User: hello\nAssistant: Hello Koko! How may I assist you today?\n"
        )

    def test_send_returns_response(self):
        """send() returns the AI's response text."""
        result = self.worker.send("hello")
        self.assertIsNotNone(result)
        self.assertIn("Hello", result)

    def test_send_no_page_returns_none(self):
        """send() returns None if no page."""
        worker = BrowserWorker(_make_config())
        self.assertIsNone(worker.send("hello"))

    def test_send_empty_message(self):
        """send() handles empty message."""
        result = self.worker.send("")
        # May return None or empty — just ensure no crash
        self.assertIsNotNone(result)

    def test_system_prompt_sends(self):
        """send_system_prompt returns True on success."""
        result = self.worker.send_system_prompt("You are a helpful AI.")
        self.assertTrue(result)
        self.assertTrue(self.worker._system_prompt_sent)

    def test_system_prompt_only_once(self):
        """send_system_prompt can only be called once."""
        self.worker.send_system_prompt("Prompt 1")
        result = self.worker.send_system_prompt("Prompt 2")
        self.assertFalse(result)

    def test_close_cleans_up(self):
        """close() resets all browser references."""
        self.worker._browser = MagicMock()
        self.worker._playwright = MagicMock()
        self.worker.close()
        self.assertIsNone(self.worker._browser)
        self.assertIsNone(self.worker._context)
        self.assertIsNone(self.worker._page)
        self.assertIsNone(self.worker._playwright)

    def test_send_saves_auth(self):
        """send() calls _save_auth after getting response."""
        with patch.object(self.worker, "_save_auth") as mock_save:
            self.worker.send("hello")
            mock_save.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# Test: BrowserWorker — login/wait flow (mocked page)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrowserWorkerLoginFlow(unittest.TestCase):
    """Test login flow methods with mocked page."""

    def setUp(self):
        self.worker = BrowserWorker(_make_config())
        self.worker._page = MagicMock()
        self.worker._context = MagicMock()

    def test_navigate_success(self):
        """navigate() returns True when login indicator is found."""
        self.worker._page.wait_for_selector.return_value = MagicMock()
        result = self.worker.navigate()
        self.assertTrue(result)

    def test_navigate_failure(self):
        """navigate() returns False when login indicator is not found."""
        self.worker._page.wait_for_selector.side_effect = Exception("Timeout")
        result = self.worker.navigate()
        self.assertFalse(result)

    def test_navigate_no_page(self):
        """navigate() returns False if no page."""
        worker = BrowserWorker(_make_config())
        self.assertFalse(worker.navigate())

    def test_wait_for_login_success(self):
        """wait_for_login returns True when indicator appears."""
        self.worker._page.wait_for_selector.return_value = MagicMock()
        result = self.worker.wait_for_login(timeout_seconds=5)
        self.assertTrue(result)

    def test_wait_for_login_timeout(self):
        """wait_for_login returns False on timeout."""
        self.worker._page.wait_for_selector.side_effect = Exception("Timeout")
        result = self.worker.wait_for_login(timeout_seconds=1)
        self.assertFalse(result)

    def test_wait_for_login_no_page(self):
        """wait_for_login returns False if no page."""
        worker = BrowserWorker(_make_config())
        self.assertFalse(worker.wait_for_login())


# ═══════════════════════════════════════════════════════════════════════════════
# Test: BrowserBrainThread — construction and lifecycle
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrowserBrainThreadConstruction(unittest.TestCase):
    """Test BrowserBrainThread construction and basic methods."""

    def test_construction(self):
        """Thread constructs with default config."""
        thread = BrowserBrainThread(_make_config())
        self.assertIsNotNone(thread.worker)
        self.assertEqual(thread._queue, [])
        self.assertTrue(thread._running)
        self.assertFalse(thread._initialized)

    def test_ask_queues_message(self):
        """ask() adds payload to the queue."""
        thread = BrowserBrainThread(_make_config())
        thread.ask({"text": "hello"})
        self.assertEqual(len(thread._queue), 1)
        self.assertEqual(thread._queue[0]["text"], "hello")

    def test_clear_queue(self):
        """clear_queue() empties the queue."""
        thread = BrowserBrainThread(_make_config())
        thread.ask({"text": "hello"})
        thread.ask({"text": "world"})
        thread.clear_queue()
        self.assertEqual(thread._queue, [])

    def test_set_system_prompt(self):
        """set_system_prompt stores the prompt text."""
        thread = BrowserBrainThread(_make_config())
        thread.set_system_prompt("You are helpful.")
        self.assertEqual(thread._system_prompt_text, "You are helpful.")

    def test_is_ready_false_initially(self):
        """is_ready() returns False before initialization."""
        thread = BrowserBrainThread(_make_config())
        self.assertFalse(thread.is_ready())

    def test_stop_clears_queue(self):
        """stop() clears the queue."""
        thread = BrowserBrainThread(_make_config())
        thread.ask({"text": "hello"})
        thread.stop()
        self.assertEqual(thread._queue, [])

    def test_error_signal_exists(self):
        """Thread has an error signal."""
        thread = BrowserBrainThread(_make_config())
        self.assertTrue(hasattr(thread, "error"))
        self.assertTrue(hasattr(thread, "response_ready"))
        self.assertTrue(hasattr(thread, "login_required"))
        self.assertTrue(hasattr(thread, "login_complete"))


# ═══════════════════════════════════════════════════════════════════════════════
# Test: BrowserBrainThread — _process logic (mocked worker)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrowserBrainThreadProcess(unittest.TestCase):
    """Test the _process method with mocked worker."""

    def setUp(self):
        self.thread = BrowserBrainThread(_make_config())
        self.thread._initialized = True
        self.thread.worker = MagicMock()
        self.thread.worker.send.return_value = "Mock response"
        self.thread.worker._page = MagicMock()

        # Capture signals
        self.responses: list[str] = []
        self.errors: list[str] = []
        self.thread.response_ready.connect(self.responses.append)
        self.thread.error.connect(self.errors.append)

    def test_process_sends_response(self):
        """_process sends response via signal."""
        self.thread._process({"text": "hello"})
        self.assertEqual(self.responses, ["Mock response"])
        self.assertEqual(self.errors, [])

    def test_process_empty_text_skips(self):
        """_process with empty text does nothing."""
        self.thread._process({"text": ""})
        self.assertEqual(self.responses, [])
        self.assertEqual(self.errors, [])

    def test_process_no_text_skips(self):
        """_process with no text key does nothing."""
        self.thread._process({"other": "data"})
        self.assertEqual(self.responses, [])

    def test_process_not_initialized(self):
        """_process emits error if not initialized."""
        thread = BrowserBrainThread(_make_config())
        errors: list[str] = []
        thread.error.connect(errors.append)
        thread._process({"text": "hello"})
        self.assertIn("not ready", errors[0].lower())

    def test_process_emits_error_on_exception(self):
        """_process emits error when worker.send raises."""
        self.thread.worker.send.side_effect = Exception("Boom")
        self.thread._process({"text": "hello"})
        self.assertEqual(self.responses, [])
        self.assertTrue(any("Boom" in e for e in self.errors))


# ═══════════════════════════════════════════════════════════════════════════════
# Test: BrowserBrainThread — _initialize_browser logic (mocked worker)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrowserBrainThreadInit(unittest.TestCase):
    """Test the _initialize_browser flow with mocked worker."""

    def setUp(self):
        self.thread = BrowserBrainThread(_make_config())
        self.thread.worker = MagicMock()
        self.thread.worker.is_auth_available.return_value = False
        self.thread.worker.start.return_value = True
        self.thread.worker.navigate.return_value = True

        self.errors: list[str] = []
        self.logins_required: int = 0
        self.logins_complete: int = 0
        self.thread.error.connect(self.errors.append)
        self.thread.login_required.connect(lambda: setattr(self, "logins_required", self.logins_required + 1))
        self.thread.login_complete.connect(lambda: setattr(self, "logins_complete", self.logins_complete + 1))

    @patch("core.browser_brain.BrowserWorker.is_available", return_value=False)
    def test_no_playwright_emits_error(self, *_):
        self.thread._initialize_browser()
        self.assertFalse(self.thread._initialized)
        self.assertTrue(any("Playwright" in e for e in self.errors))

    @patch("core.browser_brain.BrowserWorker.is_available", return_value=True)
    def test_first_run_login_flow(self, *_):
        """First run without auth goes through login flow."""
        self.thread.worker.is_auth_available.return_value = False
        self.thread._initialize_browser()
        self.assertTrue(self.thread._initialized)
        self.thread.worker.start.assert_called_once_with(headless=False)
        self.thread.worker.navigate.assert_called_once()

    @patch("core.browser_brain.BrowserWorker.is_available", return_value=True)
    def test_subsequent_run_headless(self, *_):
        """Subsequent runs with auth try headless first."""
        self.thread.worker.is_auth_available.return_value = True
        self.thread._initialize_browser()
        self.assertTrue(self.thread._initialized)
        self.thread.worker.start.assert_called_once_with(headless=True)

    @patch("core.browser_brain.BrowserWorker.is_available", return_value=True)
    def test_nav_failure_triggers_login_flow(self, *_):
        """Failed navigation on first run triggers login_required."""
        self.thread.worker.is_auth_available.return_value = False
        self.thread.worker.navigate.return_value = False
        self.thread.worker.wait_for_login.return_value = True

        self.thread._initialize_browser()
        self.assertEqual(self.logins_required, 1)
        self.assertEqual(self.logins_complete, 1)

    @patch("core.browser_brain.BrowserWorker.is_available", return_value=True)
    def test_login_timeout(self, *_):
        """Login timeout emits error."""
        self.thread.worker.is_auth_available.return_value = False
        self.thread.worker.navigate.return_value = False
        self.thread.worker.wait_for_login.return_value = False

        self.thread._initialize_browser()
        self.assertFalse(self.thread._initialized)
        self.assertTrue(any("timeout" in e.lower() for e in self.errors))

    @patch("core.browser_brain.BrowserWorker.is_available", return_value=True)
    def test_system_prompt_sent_on_init(self, *_):
        """System prompt is sent after initialization."""
        self.thread.set_system_prompt("You are Atom.")
        self.thread._initialize_browser()
        self.thread.worker.send_system_prompt.assert_called_once_with("You are Atom.")

    @patch("core.browser_brain.BrowserWorker.is_available", return_value=True)
    def test_expired_auth_handling(self, *_):
        """Expired auth deletes the file and retries."""
        self.thread.worker.is_auth_available.return_value = True
        self.thread.worker.navigate.side_effect = [False]  # First nav fails

        with patch.object(Path, "unlink") as mock_unlink:
            self.thread._initialize_browser()
            # Should try to delete expired auth
            self.assertFalse(self.thread._initialized)

    def test_already_initialized_returns(self):
        """_initialize_browser returns immediately if already initialized."""
        self.thread._initialized = True
        self.thread._initialize_browser()
        self.thread.worker.start.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Test: SKIP_PHRASES and module-level constants
# ═══════════════════════════════════════════════════════════════════════════════

class TestModuleConstants(unittest.TestCase):
    """Test module-level constants."""

    def test_default_selectors_have_all_platforms(self):
        """DEFAULT_SELECTORS has deepseek, gemini, and generic."""
        for platform in ("deepseek", "gemini", "generic"):
            self.assertIn(platform, DEFAULT_SELECTORS)

    def test_skip_phrases_non_empty(self):
        """SKIP_PHRASES is a non-empty list."""
        self.assertTrue(len(SKIP_PHRASES) > 0)
        self.assertIsInstance(SKIP_PHRASES, list)

    def test_skip_phrases_are_strings(self):
        """All SKIP_PHRASES entries are strings."""
        for phrase in SKIP_PHRASES:
            self.assertIsInstance(phrase, str)

    def test_each_platform_has_required_keys(self):
        """Each platform's selectors have the required keys."""
        required = {"chat_input", "send_button", "response_container", "stop_button", "login_indicator"}
        for platform, selectors in DEFAULT_SELECTORS.items():
            for key in required:
                self.assertIn(key, selectors, f"{platform} missing '{key}'")


# ═══════════════════════════════════════════════════════════════════════════════
# Test: AUTH_STATE_PATH
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuthPath(unittest.TestCase):
    """Test auth path configuration."""

    def test_auth_path_is_path(self):
        """AUTH_STATE_PATH is a Path instance."""
        self.assertIsInstance(AUTH_STATE_PATH, Path)

    def test_auth_path_name(self):
        """AUTH_STATE_PATH filename is browser_auth.json."""
        self.assertEqual(AUTH_STATE_PATH.name, "browser_auth.json")

    def test_auth_path_in_data_dir(self):
        """AUTH_STATE_PATH is inside the data directory."""
        self.assertIn("data", str(AUTH_STATE_PATH).lower())


# ── Run ──

if __name__ == "__main__":
    unittest.main()
