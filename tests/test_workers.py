"""
Tests for Step 2 — Threading Skeleton
Workers: BrainThread, PerceptionWorker, VoiceThread
Tests: start/stop, signal emission, non-blocking behavior
"""

import time
import unittest
from unittest.mock import patch, MagicMock

from PyQt6.QtCore import QCoreApplication, QTimer
from PyQt6.QtTest import QSignalSpy

# Headless Qt (no display needed)
_app = QCoreApplication.instance()
if _app is None:
    _app = QCoreApplication([])

from core.brain import BrainThread, generate_fallback_response
from core.eyes import PerceptionWorker
from core.voice import VoiceThread


class TestBrainThread(unittest.TestCase):
    """BrainThread skeleton: start/stop, signal emission, queue."""

    def setUp(self):
        self.brain = BrainThread()

    def tearDown(self):
        if self.brain.isRunning():
            self.brain.stop()
        self.brain = None

    def test_not_running_on_init(self):
        self.assertFalse(self.brain.isRunning())

    def test_start_and_stop(self):
        self.brain.start()
        self.brain.msleep(100)  # Give thread time to start
        self.assertTrue(self.brain.isRunning())
        self.brain.stop()
        self.assertFalse(self.brain.isRunning())

    def test_start_stop_cycle(self):
        """Start → stop → start → stop should work cleanly."""
        self.brain.start()
        self.brain.msleep(100)
        self.brain.stop()
        self.assertFalse(self.brain.isRunning())

        self.brain.start()
        self.brain.msleep(100)
        self.brain.stop()
        self.assertFalse(self.brain.isRunning())

    def test_ask_emits_response_ready(self):
        """Queue a message and verify signal fires with fallback.

        Mocks ProviderRouter to return immediately instead of trying
        to connect to real LLM providers (Ollama/Gemini).
        """
        with patch('core.brain.ProviderRouter') as MockRouter:
            mock_instance = MagicMock()
            mock_instance.chat.return_value = "*tap* Hello from mock!"
            MockRouter.return_value = mock_instance

            brain = BrainThread()
            spy = QSignalSpy(brain.response_ready)
            brain.start()
            brain.msleep(100)
            brain.ask({"text": "hello", "mode": "tap"})
            brain.msleep(200)
            self.assertGreater(len(spy), 0)
            msg = spy[0][0]
            self.assertIsInstance(msg, str)
            self.assertIn("mock", msg.lower())
            brain.stop()

    def test_ask_empty_text_no_emission(self):
        """Empty text should not emit anything."""
        spy = QSignalSpy(self.brain.response_ready)
        self.brain.start()
        self.brain.msleep(100)
        self.brain.ask({"text": "", "mode": "tap"})
        self.brain.msleep(200)
        self.assertEqual(len(spy), 0)

    def test_clear_queue(self):
        """Clearing the queue should prevent pending items from processing."""
        spy = QSignalSpy(self.brain.response_ready)
        self.brain.start()
        self.brain.msleep(100)
        self.brain.ask({"text": "hello", "mode": "tap"})
        self.brain.clear_queue()
        self.brain.msleep(200)
        self.assertEqual(len(spy), 0)

    def test_double_stop_safe(self):
        """Calling stop() twice should not raise."""
        self.brain.start()
        self.brain.msleep(100)
        self.brain.stop()
        self.brain.stop()  # Second stop
        self.assertFalse(self.brain.isRunning())

    def test_process_emits_error_on_all_providers_fail(self):
        """Verify BrainThread emits error (not hangs) when router returns None.

        This guards against the QThread silent-exception bug: if router.chat()
        returns None, finalize_response(None, ...) crashes with AttributeError
        on None.lower() inside is_boring(). Prior to the fix, the exception was
        silently swallowed by QThread and response_ready never fired — the CLI
        hung forever on response_container['ready'].wait().
        """
        errors = []
        self.brain.error.connect(lambda msg: errors.append(msg))
        self.brain.start()
        self.brain.msleep(100)
        with patch.object(self.brain, '_router') as mock_router:
            mock_router.chat.return_value = None
            self.brain._process({"text": "hello", "mode": "tap"})
        self.assertEqual(len(errors), 1)
        self.assertIn("failed", errors[0].lower())


class TestPerceptionWorker(unittest.TestCase):
    """PerceptionWorker skeleton: start/stop, signal emission."""

    def setUp(self):
        self.perception = PerceptionWorker()

    def tearDown(self):
        if self.perception.isRunning():
            self.perception.stop()
        self.perception = None

    def test_not_running_on_init(self):
        self.assertFalse(self.perception.isRunning())

    def test_start_and_stop(self):
        self.perception.start()
        self.perception.msleep(100)
        self.assertTrue(self.perception.isRunning())
        self.perception.stop()
        self.assertFalse(self.perception.isRunning())

    def test_double_stop_safe(self):
        self.perception.start()
        self.perception.msleep(100)
        self.perception.stop()
        self.perception.stop()
        self.assertFalse(self.perception.isRunning())


class TestVoiceThread(unittest.TestCase):
    """VoiceThread skeleton: start/stop, signal emission, queue."""

    def setUp(self):
        self.voice = VoiceThread()

    def tearDown(self):
        if self.voice.isRunning():
            self.voice.stop()
        self.voice = None

    def test_not_running_on_init(self):
        self.assertFalse(self.voice.isRunning())

    def test_start_and_stop(self):
        self.voice.start()
        self.voice.msleep(100)
        self.assertTrue(self.voice.isRunning())
        self.voice.stop()
        self.assertFalse(self.voice.isRunning())

    @patch("core.voice.VoiceThread._speak_with_system_speech", return_value=False)
    @patch("core.voice.VoiceThread._speak_with_pyttsx3", return_value=False)
    def test_speak_emits_signals(self, mock_pyttsx3, mock_system):
        """Speaking should emit speaking_started then speaking_finished.

        Mocks both TTS backends to return False immediately, forcing the
        silent fallback (msleep) — avoids PowerShell 45s timeout in test env.
        """
        started_spy = QSignalSpy(self.voice.speaking_started)
        finished_spy = QSignalSpy(self.voice.speaking_finished)
        self.voice.start()
        self.voice.msleep(100)
        self.voice.speak("*tap* Hello, Koko.")
        self.voice.msleep(500)  # Wait for the simulated speak
        self.assertGreater(len(started_spy), 0)
        self.assertGreater(len(finished_spy), 0)
        self.assertEqual(str(started_spy[0][0]), "*tap* Hello, Koko.")
        self.assertEqual(str(finished_spy[0][0]), "*tap* Hello, Koko.")

    def test_stop_speaking_clears_queue(self):
        """stop_speaking should clear queued items."""
        spy = QSignalSpy(self.voice.speaking_started)
        self.voice.start()
        self.voice.msleep(100)
        self.voice.speak("message 1")
        self.voice.speak("message 2")
        self.voice.speak("message 3")
        self.voice.stop_speaking()
        self.voice.msleep(500)
        # At most 1 message may have started before clear
        self.assertLessEqual(len(spy), 1)

    def test_is_speaking(self):
        self.voice.start()
        self.voice.msleep(100)
        self.assertFalse(self.voice.is_speaking())
        self.voice.speak("*tap* Hello")
        self.voice.msleep(50)
        # May or may not be speaking yet depending on timing
        # Just verify no crash
        _ = self.voice.is_speaking()

    def test_empty_text_no_op(self):
        """Empty text should not be queued."""
        spy = QSignalSpy(self.voice.speaking_started)
        spy_err = QSignalSpy(self.voice.error)
        self.voice.start()
        self.voice.msleep(100)
        self.voice.speak("")
        self.voice.speak("  ")
        self.voice.msleep(300)
        # Empty text is filtered by the voice.speak() method
        self.assertEqual(len(spy), 0)
        self.assertEqual(len(spy_err), 0)

    def test_speak_with_tts_disabled(self):
        """If config says tts_enabled=False, speak() should be no-op."""
        voice = VoiceThread({"tts_enabled": False})
        voice.start()
        voice.msleep(100)
        spy = QSignalSpy(voice.speaking_started)
        voice.speak("*tap* Test")
        voice.msleep(300)
        # speak() checks tts_enabled; if disabled, text is not queued
        self.assertEqual(len(spy), 0)
        voice.stop()

    def test_double_stop_safe(self):
        self.voice.start()
        self.voice.msleep(100)
        self.voice.stop()
        self.voice.stop()
        self.assertFalse(self.voice.isRunning())


class TestNonBlocking(unittest.TestCase):
    """Verify that workers don't block the Qt event loop."""

    def test_brain_does_not_block_ui(self):
        """Start BrainThread and verify the main thread still processes events."""
        brain = BrainThread()
        brain.start()

        # Measure: time to process a single event
        start = time.monotonic()
        for _ in range(10):
            QCoreApplication.processEvents()
            time.sleep(0.001)
        elapsed = time.monotonic() - start

        brain.stop()
        # Should take ~10ms, not seconds
        self.assertLess(elapsed, 1.0)

    def test_perception_does_not_block_ui(self):
        perception = PerceptionWorker()
        perception.start()
        start = time.monotonic()
        for _ in range(10):
            QCoreApplication.processEvents()
            time.sleep(0.001)
        elapsed = time.monotonic() - start
        perception.stop()
        self.assertLess(elapsed, 1.0)

    def test_voice_does_not_block_ui(self):
        voice = VoiceThread()
        voice.start()
        start = time.monotonic()
        for _ in range(10):
            QCoreApplication.processEvents()
            time.sleep(0.001)
        elapsed = time.monotonic() - start
        voice.stop()
        self.assertLess(elapsed, 1.0)


class TestUtilityFunctions(unittest.TestCase):
    """Utility functions that survived the skeleton cut — must still work."""

    def test_get_system_prompt_atom(self):
        from core.brain import get_system_prompt
        prompt = get_system_prompt("tap")
        self.assertIn("Atom", prompt)
        self.assertIn("Koko", prompt)

    def test_get_system_prompt_formal(self):
        from core.brain import get_system_prompt
        prompt = get_system_prompt("formal")
        self.assertIn("Atom", prompt)
        self.assertIn("formal mode", prompt)

    def test_strip_preamble(self):
        from core.brain import strip_preamble
        self.assertEqual(strip_preamble("Sure, here is the info"), "here is the info")
        self.assertEqual(strip_preamble("I think we should go"), "we should go")

    def test_clean_response_adds_tap(self):
        from core.brain import clean_response
        result = clean_response("hello world")
        self.assertTrue(result.startswith("*tap*"))

    def test_validate_gemini_key_valid(self):
        from core.brain import validate_gemini_key
        self.assertIsNone(validate_gemini_key("AIzaSyDEt7x8x8x8x8x8x8x8x8x8x8x8x8"))

    def test_validate_gemini_key_oauth(self):
        from core.brain import validate_gemini_key
        # ya29.* tokens are real OAuth 2.0 tokens, not Gemini API keys
        err = validate_gemini_key("ya29.a0AfH6xxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        self.assertIsNotNone(err)
        self.assertIn("OAuth", err)

    def test_enforce_formal_tone(self):
        from core.brain import enforce_formal_tone
        result = enforce_formal_tone("don't worry about it :) lol")
        self.assertNotIn("lol", result.lower())
        self.assertIn("do not", result.lower())

    def test_generate_fallback_response(self):
        from core.brain import generate_fallback_response
        result = generate_fallback_response("hello", "tap")
        self.assertTrue(result.startswith("*tap*"))


if __name__ == "__main__":
    unittest.main()
