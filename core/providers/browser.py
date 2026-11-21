"""
Browser AI provider — Playwright-based web AI chat.

Features:
- Headless Chromium with stealth patches to bypass anti-bot
- Session persistence via Playwright storage_state
- Configurable target URL (DeepSeek, Gemini web, etc.)
- System prompt sent on first interaction
- Multi-turn conversation support
- Login flow with manual browser for auth

Marked as "best-effort" per master prompt §4 — may be unreliable
depending on target website changes and anti-bot measures.
"""

from __future__ import annotations

import json
import logging
import os
import re as _re
from pathlib import Path
from typing import Optional

log = logging.getLogger("atom.providers.browser")

ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = ROOT / "data"
AUTH_STATE_PATH = DATA_DIR / "browser_auth.json"

DEFAULT_URL = "https://chat.deepseek.com/"

# ── Selectors per platform ──
DEFAULT_SELECTORS = {
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
    },
    "gemini": {
        "chat_input": 'div[contenteditable="true"][role="textbox"], textarea',
        "send_button": 'button[aria-label*="Send"]',
        "response_container": ".conversation-container, main",
        "stop_button": 'button:has-text("Stop")',
        "login_indicator": 'div[contenteditable="true"][role="textbox"]',
    },
    "generic": {
        "chat_input": "textarea, input[type=\"text\"], div[contenteditable=\"true\"]",
        "send_button": 'button[type="submit"], button:has-text("Send")',
        "response_container": "main, .chat, .messages",
        "stop_button": 'button:has-text("Stop")',
        "login_indicator": "textarea",
    },
}


def is_browser_available() -> bool:
    """Check if Playwright is installed."""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


class BrowserWorker:
    """Manages a Playwright browser instance for web-based AI chat.

    This is a best-effort provider. It may require manual login on first use
    and can break if the target website changes its DOM.
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._target_url = self.config.get("browser_ai_url", DEFAULT_URL)
        self._platform = self._detect_platform()
        self._headless = not self.config.get("browser_show_browser", False)
        self._system_prompt_sent = False
        self._conversation_started = False

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
        return DEFAULT_SELECTORS.get(self._platform, DEFAULT_SELECTORS["generic"])

    def is_auth_available(self) -> bool:
        return AUTH_STATE_PATH.exists()

    # ── Browser lifecycle ──

    def start(self, headless: bool | None = None) -> bool:
        """Start Playwright and launch Chromium."""
        if headless is not None:
            self._headless = headless
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
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
            if self.is_auth_available():
                context_opts["storage_state"] = str(AUTH_STATE_PATH)

            self._browser = self._playwright.chromium.launch(**launch_opts)
            self._context = self._browser.new_context(**context_opts)
            self._page = self._context.new_page()
            self._page.set_default_timeout(45000)
            self._apply_stealth()
            log.info("Browser started (headless=%s, platform=%s)", self._headless, self._platform)
            return True
        except ImportError as exc:
            log.error("Playwright not installed: %s", exc)
            return False
        except Exception as exc:
            log.error("Failed to start browser: %s", exc)
            return False

    def _apply_stealth(self):
        """Apply JavaScript patches to hide automation signals."""
        if not self._page:
            return
        try:
            self._page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = {
                runtime: { getManifest: () => ({ version: '124.0.0.0' }) },
                loadTimes: () => ({}),
                csi: () => ({}),
                app: { isInstalled: false },
            };
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
                    { name: 'Native Client', filename: 'internal-nacl-plugin' },
                ]
            });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
            """)
        except Exception:
            log.warning("Browser init failed")
            pass

    # ── Navigation ──

    def navigate(self) -> bool:
        """Navigate to the AI chat URL."""
        if not self._page:
            return False
        try:
            self._page.goto(self._target_url, wait_until="domcontentloaded", timeout=45000)
            self._page.wait_for_timeout(3000)
            selectors = self._get_selectors()
            indicator = selectors.get("login_indicator", "textarea")
            try:
                self._page.wait_for_selector(indicator, timeout=15000)
            except Exception:
                log.warning("Chat input not detected — may need login")
                return False
            self._save_auth()
            return True
        except Exception as exc:
            log.error("Navigate error: %s", exc)
            return False

    def wait_for_login(self, timeout_seconds: int = 300) -> bool:
        """Wait for user to manually log in (shows browser)."""
        if not self._page:
            return False
        selectors = self._get_selectors()
        indicator = selectors.get("login_indicator", "textarea")
        try:
            self._page.wait_for_selector(indicator, timeout=timeout_seconds * 1000)
            self._save_auth()
            return True
        except Exception:
            log.warning("Navigation failed")
            return False

    # ── Message send ──

    def send(self, message: str) -> Optional[str]:
        """Send a message and return the AI's response."""
        if not self._page:
            return None
        if not self._conversation_started:
            self._start_new_chat()
        return self._send_message(message)

    def _start_new_chat(self):
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
        if not self._page:
            return None
        try:
            selectors = self._get_selectors()
            input_sel = selectors.get("chat_input", "textarea")
            chat_input = self._page.wait_for_selector(input_sel, timeout=10000)
            if not chat_input:
                return None
            chat_input.click()
            self._page.wait_for_timeout(500)
            try:
                chat_input.fill("")
            except Exception:
                self._page.keyboard.press("Control+a")
                self._page.keyboard.press("Delete")
            self._page.keyboard.type(message, delay=10)
            self._page.wait_for_timeout(500)
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
            response = self._wait_for_response(selectors)
            self._save_auth()
            return response
        except Exception as exc:
            log.error("Send message error: %s", exc)
            return None

    def _wait_for_response(self, selectors: dict) -> Optional[str]:
        if not self._page:
            return None
        try:
            stop_sel = selectors.get("stop_button")
            if stop_sel:
                try:
                    self._page.wait_for_selector(stop_sel, timeout=60000)
                    self._page.wait_for_selector(stop_sel, state="hidden", timeout=120000)
                except Exception:
                    self._page.wait_for_timeout(3000)
            text = self._extract_text()
            return text
        except Exception as exc:
            log.error("Wait error: %s", exc)
            return None

    def _extract_text(self) -> Optional[str]:
        if not self._page:
            return None
        try:
            page_text = self._page.inner_text("body")
            if not page_text:
                return None
            lines = [l.strip() for l in page_text.split("\n") if l.strip() and len(l.strip()) > 5]
            if not lines:
                return None
            skip_phrases = [
                "Ask anything", "Search", "Login", "Sign up", "Model",
                "Settings", "Profile", "History",
            ]
            filtered = [l for l in lines if not any(p in l for p in skip_phrases)]
            response_lines = filtered[-min(len(filtered), 20):]
            unique = []
            for l in response_lines:
                if l not in unique:
                    unique.append(l)
            text = "\n".join(unique[-8:]) if unique else None
            if text:
                text = _re.sub(r"\n{3,}", "\n\n", text)
                text = _re.sub(r" {2,}", " ", text)
            return text
        except Exception:
            log.warning("Failed to extract page text")
            return None

    # ── Auth persistence ──

    def _save_auth(self):
        if not self._context:
            return
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            self._context.storage_state(path=str(AUTH_STATE_PATH))
        except Exception:
            log.warning("Failed to save browser auth state")
            pass

    def send_system_prompt(self, system_prompt: str) -> bool:
        """Send system prompt as the first message."""
        if not self._page or self._system_prompt_sent:
            return False
        self._start_new_chat()
        result = self._send_message(system_prompt)
        if result:
            self._system_prompt_sent = True
            self._conversation_started = True
            return True
        return False

    # ── Cleanup ──

    def close(self):
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
                    log.debug("Playwright cleanup")
                self._playwright = None
            self._page = None
        except Exception as exc:
            log.warning("Browser close error: %s", exc)
