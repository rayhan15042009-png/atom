"""The Settings → Login to DeepSeek button must actually open a browser.

`browser_login_requested` was emitted into the void: nothing connected it, so
Browser AI could never be set up and the provider always failed with
"chat input not detected".
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from atom import AtomApp


class _Overlay:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.banners: list[str] = []

    def add_chat_message(self, role: str, text: str, animate: bool = True) -> None:
        self.messages.append(text)

    def show_message(self, text: str, duration: int = 4000) -> None:
        self.banners.append(text)


def _app() -> AtomApp:
    app = AtomApp.__new__(AtomApp)
    app.overlay = _Overlay()
    app._config_to_dict = lambda: {"browser_ai_url": "https://chat.deepseek.com/"}
    return app


class _FakeWorker:
    instances: list["_FakeWorker"] = []
    login_result = True

    def __init__(self, config=None) -> None:
        self.config = config
        self.headless = None
        self.closed = False
        _FakeWorker.instances.append(self)

    def start(self, headless=None) -> bool:
        self.headless = headless
        return True

    def navigate(self) -> bool:
        return False  # the sign-in page has no chat input; expected pre-login

    def wait_for_login(self, timeout_seconds: int = 300) -> bool:
        self.timeout = timeout_seconds
        return _FakeWorker.login_result

    def close(self) -> None:
        self.closed = True


class TestBrowserLoginFlow(unittest.TestCase):
    def setUp(self) -> None:
        _FakeWorker.instances = []
        _FakeWorker.login_result = True
        self._patch = patch("core.providers.browser.BrowserWorker", _FakeWorker)
        self._patch.start()

    def tearDown(self) -> None:
        self._patch.stop()

    def test_success_opens_a_visible_browser_and_reports(self) -> None:
        app = _app()
        app._run_browser_login()
        worker = _FakeWorker.instances[0]
        self.assertFalse(worker.headless, "login must be visible, not headless")
        self.assertEqual(worker.timeout, 300)
        self.assertTrue(worker.closed)
        self.assertTrue(any("logged in" in m.lower() for m in app.overlay.messages))

    def test_timeout_reports_plainly(self) -> None:
        _FakeWorker.login_result = False
        app = _app()
        app._run_browser_login()
        self.assertTrue(any("wasn't completed" in m.lower() for m in app.overlay.messages))

    def test_second_click_does_not_open_a_second_browser(self) -> None:
        app = _app()
        alive = unittest.mock.MagicMock()
        alive.is_alive.return_value = True
        app._browser_login_thread = alive
        app._on_browser_login()
        self.assertEqual(_FakeWorker.instances, [])
        self.assertTrue(app.overlay.banners)

    def test_logout_removes_the_saved_session(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            auth = Path(tmp) / "browser_auth.json"
            auth.write_text("{}", encoding="utf-8")
            app = _app()
            with patch("core.providers.browser.AUTH_STATE_PATH", auth):
                app._on_browser_logout()
            self.assertFalse(auth.exists())


if __name__ == "__main__":
    unittest.main()
