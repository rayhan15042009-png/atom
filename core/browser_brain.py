"""
Browser AI brain — Playwright-based web chat with stealth, auth persistence,
configurable target URL, and multi-turn conversation support.

Architecture:
┌─────────────────────────────────────┐
│          BrowserWorker              │
│  Playwright + Headless Chromium     │
│  ┌───────────────────────────────┐  │
│  │  Web AI Chat (DeepSeek,       │  │
│  │  Gemini web, custom)          │  │
│  └───────────────────────────────┘  │
│  ┌───────────────────────────────┐  │
│  │  Session State (auth.json)    │  │
│  │  Cookies + localStorage       │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘

Usage:
    worker = BrowserWorker(config)
    worker.start()
    worker.navigate()
    worker.send("Hello!")
    response = worker.wait_response()
    worker.close()

Threaded variant for Qt:
    thread = BrowserBrainThread(config)
    thread.response_ready.connect(on_response)
    thread.start()
    thread.ask({"text": "Hello!"})
"""

from __future__ import annotations

import logging
import re as _re
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

log = logging.getLogger("atom.browser_brain")


# ── Re-export from canonical browser provider ──
# is_browser_available is available via BrowserWorker.is_available() static method

# ── Paths ──
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
AUTH_STATE_PATH = DATA_DIR / "browser_auth.json"

# ── Default URLs ──
DEEPSEEK_URL = "https://chat.deepseek.com/"
GEMINI_URL = "https://gemini.google.com/app"

# ── Default selectors per platform ──
DEFAULT_SELECTORS: dict[str, dict[str, str]] = {
    "deepseek": {
        "chat_input": (
            'textarea[placeholder*="Ask anything"], '
            'textarea[placeholder*="Type"], '
            'textarea[placeholder*="Message"], '
            'div[contenteditable="true"], '
            "#chat-input, "
            "textarea:not([disabled]), "
            'div[role="textbox"]'
        ),
        "send_button": (
            'button[type="submit"], '
            "button:has(svg), "
            "div.send-button, "
            'button:has-text("Send"), '
            'button[aria-label*="send"]'
        ),
        "response_container": (
            ".ds-markdown, "
            ".message-content, "
            '[class*="assistant"], '
            ".messages, "
            ".chat-messages"
        ),
        "stop_button": (
            'button:has-text("Stop"), '
            'button[aria-label*="stop"]'
        ),
        "login_indicator": (
            "textarea:not([disabled]), "
            'div[contenteditable="true"]'
        ),
        "new_chat_button": (
            'a:has-text("New Chat"), '
            'button:has-text("New chat")'
        ),
    },
    "gemini": {
        "chat_input": 'div[contenteditable="true"][role="textbox"], textarea',
        "send_button": 'button[aria-label*="Send"]',
        "response_container": ".conversation-container, main",
        "stop_button": 'button:has-text("Stop")',
        "login_indicator": 'div[contenteditable="true"][role="textbox"]',
    },
    "generic": {
        "chat_input": 'textarea, input[type="text"], div[contenteditable="true"]',
        "send_button": 'button[type="submit"], button:has-text("Send")',
        "response_container": "main, .chat, .messages",
        "stop_button": 'button:has-text("Stop")',
        "login_indicator": "textarea",
    },
}

# ── Phrases to skip when extracting response text ──
SKIP_PHRASES: list[str] = [
    "Ask anything", "Search", "Login", "Sign up", "Sign in",
    "Model", "Settings", "Profile", "History", "New chat",
    "Google", "Gemini", "DeepSeek", "Activity",
    "Help", "Terms", "Privacy",
]


# ═══════════════════════════════════════════════════════════════════════════════
# BrowserWorker — manages Playwright browser instance and AI chat interaction
# ═══════════════════════════════════════════════════════════════════════════════

class BrowserWorker:
    """Manages a Playwright browser instance for web-based AI chat.

    This is a best-effort provider. It may require manual login on first use
    and can break if the target website changes its DOM. Designed to be called
    from BrowserBrainThread or used directly in synchronous code.
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._target_url = self.config.get("browser_ai_url", DEEPSEEK_URL)
        self._platform = self._detect_platform()
        self._headless = not self.config.get("browser_show_browser", False)
        self._system_prompt_sent = False
        self._conversation_started = False

    # ── Platform detection ──

    def _detect_platform(self) -> str:
        url = self._target_url.lower()
        if "deepseek" in url:
            return "deepseek"
        if "gemini" in url:
            return "gemini"
        return "generic"

    def _get_selectors(self) -> dict:
        platform_selectors = self.config.get("browser_ai_selectors", {})
        if platform_selectors:
            return platform_selectors
        return dict(DEFAULT_SELECTORS.get(self._platform, DEFAULT_SELECTORS["generic"]))

    # ── Auth ──

    def is_auth_available(self) -> bool:
        """Check if saved auth state exists on disk."""
        return AUTH_STATE_PATH.exists()

    # ── Browser lifecycle ──

    def start(self, headless: bool | None = None) -> bool:
        """Start Playwright and launch Chromium with stealth evasions.

        Args:
            headless: Override headless mode. If None, uses config.

        Returns:
            True if browser started successfully.
        """
        if headless is not None:
            self._headless = headless
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()

            # Chrome launch args to hide automation signals
            stealth_args = [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--window-size=1280,800",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-extensions",
                "--no-default-browser-check",
            ]

            launch_opts = {
                "headless": self._headless,
                "args": stealth_args,
                "ignore_default_args": ["--enable-automation"],
            }

            # Context options for realistic fingerprint
            context_opts = {
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "viewport": {"width": 1280, "height": 800},
                "locale": "en-US",
                "timezone_id": "America/New_York",
                "permissions": ["clipboard-read", "clipboard-write"],
                "color_scheme": "dark",
            }

            # Load saved auth state if available
            if self.is_auth_available():
                context_opts["storage_state"] = str(AUTH_STATE_PATH)

            self._browser = self._playwright.chromium.launch(**launch_opts)
            self._context = self._browser.new_context(**context_opts)
            self._page = self._context.new_page()
            self._page.set_default_timeout(45000)

            # Apply stealth patches (non-fatal)
            self._apply_stealth()

            log.info("Browser started (headless=%s, platform=%s)", self._headless, self._platform)
            return True

        except ImportError as exc:
            log.error("Playwright not installed: %s", exc)
            return False
        except Exception as exc:
            log.error("Failed to start browser: %s", exc)
            return False

    def _apply_stealth(self) -> None:
        """Apply JavaScript patches to hide Playwright automation signals.

        Catches all exceptions since stealth is non-critical.
        """
        if not self._page:
            return
        try:
            self._page.add_init_script("""
            // Hide webdriver
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

            // Realistic chrome object
            window.chrome = {
                runtime: {
                    getManifest: () => ({ version: '124.0.0.0' }),
                    connect: () => ({}),
                    sendMessage: () => {},
                    onMessage: { addListener: () => {}, removeListener: () => {} },
                    onConnect: { addListener: () => {}, removeListener: () => {} },
                },
                loadTimes: () => ({}),
                csi: () => ({}),
                app: { isInstalled: false, getDetails: () => null },
            };

            // Fake plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                    { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin' },
                ]
            });

            // Realistic languages
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
            Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });

            // Remove Playwright-specific property
            if (window.cdc_adoQpoasnfa76pfcZLmcfl) {
                delete window.cdc_adoQpoasnfa76pfcZLmcfl;
            }
            """)
        except Exception:
            log.warning("Stealth injection failed (non-critical)")
            pass

    # ── Navigation ──

    def navigate(self) -> bool:
        """Navigate to the AI chat URL and wait for the page to load.

        Returns:
            True if chat input was detected (logged in).
        """
        if not self._page:
            return False
        try:
            self._page.goto(self._target_url, wait_until="domcontentloaded", timeout=45000)
            self._page.wait_for_timeout(3000)

            selectors = self._get_selectors()
            indicator = selectors.get("login_indicator", "textarea")

            try:
                self._page.wait_for_selector(indicator, timeout=15000)
                log.info("Chat input detected — logged in")
            except Exception:
                log.warning("Chat input not detected — may need login")
                return False

            self._save_auth()
            return True
        except Exception as exc:
            log.error("Navigate error: %s", exc)
            return False

    def wait_for_login(self, timeout_seconds: int = 300) -> bool:
        """Wait for user to manually log in via visible browser.

        Args:
            timeout_seconds: Max seconds to wait (default 5 min).

        Returns:
            True if login detected.
        """
        if not self._page:
            return False
        selectors = self._get_selectors()
        indicator = selectors.get("login_indicator", "textarea")
        try:
            self._page.wait_for_selector(indicator, timeout=timeout_seconds * 1000)
            self._save_auth()
            return True
        except Exception as exc:
            log.warning("Login wait failed: %s", exc)
            return False

    # ── Message send / receive ──

    def send(self, message: str) -> Optional[str]:
        """Send a message to the AI chat and return the response.

        Args:
            message: The user message text.

        Returns:
            The AI's response text, or None on failure.
        """
        if not self._page:
            return None
        if not self._conversation_started:
            self._start_new_chat()
        return self._send_message(message)

    def _start_new_chat(self) -> None:
        """Start a new chat conversation (click new chat button if available)."""
        if not self._page:
            return
        selectors = self._get_selectors()
        btn = selectors.get("new_chat_button")
        if btn:
            try:
                el = self._page.query_selector(btn)
                if el:
                    el.click()
                    self._page.wait_for_timeout(2000)
            except Exception:
                log.warning("Failed to clear chat input")
        self._conversation_started = True

    def _send_message(self, message: str) -> Optional[str]:
        """Internal: type message, send, wait for response."""
        if not self._page:
            return None
        try:
            selectors = self._get_selectors()
            input_sel = selectors.get("chat_input", "textarea")

            # 1. Find chat input
            chat_input = self._page.wait_for_selector(input_sel, timeout=10000)
            if not chat_input:
                return None
            chat_input.click()
            self._page.wait_for_timeout(500)

            # 2. Clear existing text
            try:
                chat_input.fill("")
            except Exception:
                self._page.keyboard.press("Control+a")
                self._page.keyboard.press("Delete")

            # 3. Type the message
            self._page.keyboard.type(message, delay=10)
            self._page.wait_for_timeout(500)

            # 4. Send — try Enter first, then send button
            try:
                self._page.keyboard.press("Enter")
                self._page.wait_for_timeout(1000)
            except Exception:
                send_sel = selectors.get("send_button")
                if send_sel:
                    btn = self._page.query_selector(send_sel)
                    if btn:
                        btn.click()
                        self._page.wait_for_timeout(1000)

            # 5. Wait for and extract response
            response = self._wait_for_response(selectors)
            self._save_auth()
            return response

        except Exception as exc:
            log.error("Send message error: %s", exc)
            return None

    def _wait_for_response(self, selectors: dict) -> Optional[str]:
        """Wait for the AI to finish generating and extract the response text."""
        if not self._page:
            return None
        try:
            stop_sel = selectors.get("stop_button")
            if stop_sel:
                try:
                    # Wait for stop button to appear (generation starts)
                    self._page.wait_for_selector(stop_sel, timeout=60000)
                    # Wait for it to disappear (generation done)
                    self._page.wait_for_selector(stop_sel, state="hidden", timeout=120000)
                except Exception:
                    self._page.wait_for_timeout(3000)

            return self._extract_text()
        except Exception as exc:
            log.error("Wait error: %s", exc)
            return None

    def _extract_text(self) -> Optional[str]:
        """Extract the AI's response text from the page.

        Uses platform-agnostic extraction: get all page text, filter UI noise,
        return the last substantial block of text.
        """
        if not self._page:
            return None
        try:
            page_text = self._page.inner_text("body")
            if not page_text:
                return None

            lines = [l.strip() for l in page_text.split("\n") if l.strip() and len(l.strip()) > 5]
            if not lines:
                return None

            # Filter out UI noise (word-boundary matching to avoid false positives)
            filtered = [
                l for l in lines
                if not any(_re.search(r'\b' + _re.escape(p) + r'\b', l, _re.IGNORECASE) for p in SKIP_PHRASES)
            ]

            # Take last ~20 lines as the response
            response_lines = filtered[-min(len(filtered), 20):]
            # Remove duplicates (preserve order)
            unique: list[str] = []
            for l in response_lines:
                if l not in unique:
                    unique.append(l)

            text = "\n".join(unique[-8:]) if unique else None
            if text:
                text = _re.sub(r"\n{3,}", "\n\n", text)
                text = _re.sub(r" {2,}", " ", text)
            return text
        except Exception:
            return None

    # ── System prompt ──

    def send_system_prompt(self, system_prompt: str) -> bool:
        """Send a system prompt as the first message in a new conversation.

        Args:
            system_prompt: The system prompt text.

        Returns:
            True if sent successfully.
        """
        if not self._page or self._system_prompt_sent:
            return False
        self._start_new_chat()
        result = self._send_message(system_prompt)
        if result:
            self._system_prompt_sent = True
            self._conversation_started = True
            return True
        return False

    # ── Auth persistence ──

    def _save_auth(self) -> None:
        """Save browser storage state (cookies, localStorage) to disk."""
        if not self._context:
            return
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._context.storage_state(path=str(AUTH_STATE_PATH))
        except Exception:
            log.debug("Browser close failed (expected during shutdown)")
            pass

    # ── Cleanup ──

    def close(self) -> None:
        """Close the browser and clean up resources."""
        try:
            self._save_auth()
            if self._browser:
                try:
                    self._browser.close()
                except Exception:
                    log.debug("Browser cleanup")
                self._browser = None
            if self._context:
                try:
                    self._context.close()
                except Exception:
                    log.debug("Context cleanup")
                self._context = None
            if self._playwright:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
            self._page = None
        except Exception as exc:
            log.warning("Browser close error: %s", exc)

    # ── Utilities ──

    @staticmethod
    def is_available() -> bool:
        """Check if Playwright is installed."""
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False


# backward-compat alias so ``from core.browser_brain import BrowserAI`` works
BrowserAI = BrowserWorker


# ═══════════════════════════════════════════════════════════════════════════════
# BrowserBrainThread — QThread wrapper for async browser AI interaction
# ═══════════════════════════════════════════════════════════════════════════════

class BrowserBrainThread(QThread):
    """QThread wrapper that drives BrowserWorker in a background thread.

    Handles the async nature of browser-based AI interaction by running
    Playwright operations in a background thread. Supports queue-based
    message processing, auto-login flow, and periodic auth saving.

    Signals:
        response_ready(str): Emitted when the AI responds.
        error(str): Emitted on failures.
        login_required(): Emitted when the user needs to log in manually.
        login_complete(): Emitted when login is detected.
    """

    response_ready = pyqtSignal(str)
    error = pyqtSignal(str)
    login_required = pyqtSignal()
    login_complete = pyqtSignal()

    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self.worker = BrowserWorker(config)
        self._queue: list[dict] = []
        self._running = True
        self._initialized = False
        self._system_prompt_text = ""

    def run(self) -> None:
        """Main thread loop — processes queue items."""
        while self._running:
            if not self._initialized:
                self._initialize_browser()

            if self._queue and self._initialized:
                item = self._queue.pop(0)
                self._process(item)

            self.msleep(100)

    def _initialize_browser(self) -> None:
        """Initialize the browser and handle login flow."""
        if self._initialized:
            return

        log.info("Initializing browser AI...")

        if not BrowserWorker.is_available():
            self.error.emit("Playwright not installed. Run: pip install playwright && playwright install chromium")
            self.msleep(5000)
            return

        has_auth = self.worker.is_auth_available()

        if not has_auth:
            # First run: visible browser for login
            log.info("No auth state — launching visible browser for login")
            success = self.worker.start(headless=False)
            if not success:
                self.error.emit("Failed to start browser")
                self.msleep(10000)
                return

            nav_ok = self.worker.navigate()
            if not nav_ok:
                self.login_required.emit()
                login_ok = self.worker.wait_for_login(timeout_seconds=300)
                if not login_ok:
                    self.error.emit("Login timeout — please try again")
                    self.worker.close()
                    return

            self.login_complete.emit()
        else:
            # Has saved auth: try headless
            success = self.worker.start(headless=True)
            if not success:
                success = self.worker.start(headless=False)
            if not success:
                self.error.emit("Failed to start browser")
                self.msleep(10000)
                return

            nav_ok = self.worker.navigate()
            if not nav_ok:
                # Auth expired
                log.warning("Auth expired — need re-login")
                self.worker.close()
                try:
                    AUTH_STATE_PATH.unlink(missing_ok=True)
                except Exception:
                    log.warning("Browser loop iteration error")
                self.msleep(1000)
                return

        # Send system prompt if configured
        if self._system_prompt_text:
            self.worker.send_system_prompt(self._system_prompt_text)

        self._initialized = True
        log.info("Browser AI initialized successfully")

    def set_system_prompt(self, text: str) -> None:
        """Set the system prompt to send on initialization."""
        self._system_prompt_text = text

    def ask(self, payload: dict) -> None:
        """Queue a message for processing.

        Args:
            payload: Dict with at least "text" key (user message).
        """
        self._queue.append(payload)

    def clear_queue(self) -> None:
        """Clear pending messages."""
        self._queue.clear()

    def _process(self, payload: dict) -> None:
        """Process a single message through the browser AI."""
        text = payload.get("text", "")
        if not text:
            return
        if not self._initialized:
            self.error.emit("Browser AI not ready yet")
            return

        log.info("Sending message: %.50s", text)
        try:
            response = self.worker.send(text)
            if response:
                self.response_ready.emit(response)
            else:
                self.error.emit("Web AI didn't return a response")
        except Exception as exc:
            log.error("Browser AI error: %s", exc)
            self.error.emit(f"Browser AI error: {exc}")

    def is_ready(self) -> bool:
        """Check if the browser AI is initialized and ready."""
        return self._initialized and self.worker._page is not None

    def stop(self) -> None:
        """Stop the thread and clean up."""
        self._running = False
        self._queue.clear()
        try:
            self.worker.close()
        except Exception:
            log.debug("Browser worker stop")
        self.quit()
        if self.isRunning() and not self.wait(2000):
            self.terminate()
            self.wait(500)
