"""
VoiceThread — Windows TTS with dual-backend (pyttsx3 + PowerShell System.Speech).

Ported from Echo's stable voice system with:
- Primary: pyttsx3 (SAPI5)
- Fallback: PowerShell System.Speech
- Auto-recovery on engine failure
- Queue-based async speech (non-blocking)
- Mode-aware TTS (strip *tap*, clean text)
- Configurable voice, speed, and volume
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
from collections import deque
from threading import Lock
from typing import Any, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from core.logger import get_logger

log = get_logger('voice')

# ── Compiled regex for TTS text cleaning ──
_TTS_MARKDOWN_RE = re.compile(r"[*_~`#]")
_TTS_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE00-\uFE0F\u200D]")
_ACTION_BLOCK_RE = re.compile(r"```action\s*\{.*?\}\s*```", re.S | re.I)
_ACTION_LINE_RE = re.compile(r"ACTION:\s*\{.*?\}", re.S | re.I)
_FENCE_RE = re.compile(r"```.*?```", re.S)
_TTS_SPECIAL_RE = re.compile(r"[*_`#>\[\]{}]")


class VoiceThread(QThread):
    """Background thread for TTS with dual-backend support.

    Primary: pyttsx3 (SAPI5)
    Fallback: PowerShell System.Speech
    Auto-recovery: re-initializes engine on failure

    Processes speech requests from a queue, speaking one at a time.
    """

    speaking_started = pyqtSignal(str)
    speaking_finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, config: dict | Any = None):
        super().__init__()
        self.config = config if isinstance(config, dict) else (config or {})
        self.queue: deque = deque()
        self.lock = Lock()
        self._running = True
        self._current_text: Optional[str] = None
        self._engine = None
        self._engine_available = False
        self._stop_requested = False
        self._com_initialized = False

    # ── Thread lifecycle ──

    def run(self):
        """Main loop — drain speech queue through preferred backend.

        Engine is initialized here (in the voice thread) for SAPI COM
        compatibility. If init fails, the silent msleep fallback handles
        speech simulation.
        """
        self._init_com()
        try:
            self._init_engine()
            while self._running:
                if self._stop_requested:
                    self._stop_requested = False
                    self.msleep(50)
                    continue
                text = None
                with self.lock:
                    if self.queue:
                        text = self.queue.popleft()
                if text and not self._stop_requested:
                    self._speak_text(text)
                self.msleep(50)
        finally:
            self._shutdown_engine()
            self._uninit_com()

    # ── COM initialization (for Windows SAPI voices) ──

    def _init_com(self):
        """Initialize COM in this thread (required by SAPI on Windows)."""
        try:
            import pythoncom
            pythoncom.CoInitialize()
            self._com_initialized = True
        except Exception:
            log.warning("COM init failed", exc_info=True)
            self._com_initialized = False

    def _uninit_com(self):
        if not self._com_initialized:
            return
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:
            log.debug("COM uninit failed (expected during shutdown)")
        self._com_initialized = False

    # ── Engine init ──

    def _init_engine(self) -> bool:
        """Initialize pyttsx3 engine with voice and rate settings."""
        try:
            import pyttsx3
            self._engine = pyttsx3.init()
            self._engine_available = True

            speed = self._get_cfg("tts_speed", 150)
            self._engine.setProperty('rate', speed)

            volume = float(self._get_cfg("tts_volume", 0.8))
            self._engine.setProperty('volume', volume)

            preferred = self._get_cfg("tts_voice", "Zira")
            voices = self._engine.getProperty('voices')
            found = False
            for voice in voices:
                if preferred and preferred.lower() in voice.name.lower():
                    self._engine.setProperty('voice', voice.id)
                    found = True
                    break
            if not found:
                for voice in voices:
                    if 'female' in voice.name.lower() or 'zira' in voice.name.lower():
                        self._engine.setProperty('voice', voice.id)
                        found = True
                        break
            if not found and voices:
                self._engine.setProperty('voice', voices[0].id)

            log.info(
                "TTS initialized (pyttsx3, voice=%s, rate=%d, volume=%.1f)",
                preferred if found else "default",
                speed, volume,
            )
            return True
        except Exception as exc:
            log.warning("TTS engine init failed: %s", exc)
            self._engine = None
            self._engine_available = False
            return False

    def _recover_engine(self):
        """Re-initialize engine after a failure."""
        self._shutdown_engine()
        self._init_engine()

    def _shutdown_engine(self):
        try:
            if self._engine:
                self._engine.stop()
        except Exception:
            log.debug("TTS engine shutdown failed (expected during shutdown)")
        self._engine = None

    # ── Config helpers ──

    def _get_cfg(self, key: str, default: Any = None) -> Any:
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default) if hasattr(self.config, key) else default

    # ── Speaking ──

    def _speak_text(self, text: str):
        """Speak a single utterance through the preferred backend, with silent fallback."""
        self._current_text = text
        self.speaking_started.emit(text)
        try:
            success = self._speak_with_preferred_backend(text)
            if not success:
                # Silent fallback: simulate speaking duration
                char_count = len(text)
                ms = max(100, min(3000, char_count * 15))
                self.msleep(ms)
        except Exception as exc:
            log.warning("TTS speak error: %s", exc)
            char_count = len(text)
            ms = max(100, min(3000, char_count * 15))
            self.msleep(ms)
            self._recover_engine()
        finally:
            self.speaking_finished.emit(text)
            self._current_text = None

    def _speak_with_preferred_backend(self, text: str) -> bool:
        """Try backends in order: auto → pyttsx3 (primary) → System.Speech (fallback on Windows)."""
        clean = self._clean_text(text)
        if not clean:
            self.msleep(100)
            return True

        backend = (self._get_cfg("tts_backend", "auto") or "auto").lower()

        # Try System.Speech if configured or auto on Windows
        if backend in ('auto', 'system_speech') and platform.system() == 'Windows':
            if self._speak_with_system_speech(clean):
                return True
            if backend == 'system_speech':
                return False  # Don't fallback to pyttsx3 if explicitly system_speech

        # Try pyttsx3
        return self._speak_with_pyttsx3(clean)

    def _speak_with_pyttsx3(self, text: str) -> bool:
        """Speak using pyttsx3 engine."""
        if not self._engine and not self._init_engine():
            return False
        if self._stop_requested:
            return False
        try:
            self._engine.say(text)
            self._engine.runAndWait()
            return not self._stop_requested
        except Exception as exc:
            log.warning("pyttsx3 speak error: %s", exc)
            return False

    def _speak_with_system_speech(self, text: str) -> bool:
        """Speak using PowerShell System.Speech (Windows only)."""
        try:
            powershell = self._get_cfg("tts_powershell_path", "powershell.exe")
            rate = int((int(self._get_cfg("tts_speed", 150)) - 150) / 25)
            rate = max(-10, min(10, rate))
            volume = int(float(self._get_cfg("tts_volume", 0.8)) * 100)
            volume = max(0, min(100, volume))
            preferred = self._get_cfg("tts_voice", "Zira")
            timeout = float(self._get_cfg("tts_timeout_seconds", 45))

            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.Rate = {rate}; $s.Volume = {volume}; "
                f"$v = '{preferred}'.ToLowerInvariant(); "
                "if ($v) { "
                "  $voice = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Name.ToLowerInvariant().Contains($v) } | Select-Object -First 1; "
                "  if ($voice) { $s.SelectVoice($voice.VoiceInfo.Name) } "
                "} "
                "$s.Speak($env:ATOM_TTS_TEXT); "
                "$s.Dispose();"
            )
            env = os.environ.copy()
            env['ATOM_TTS_TEXT'] = text
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
            result = subprocess.run(
                [powershell, '-NoProfile', '-NonInteractive', '-Command', script],
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                creationflags=flags,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or '').strip()[:160]
                log.warning("System.Speech failed: %s", detail)
                return False
            return True
        except Exception as exc:
            log.warning("System.Speech error: %s", exc)
            return False

    # ── Text cleaning ──

    @staticmethod
    def _clean_text(text: str) -> str:
        """Clean text for TTS: strip action blocks, markdown, emoji."""
        text = _ACTION_BLOCK_RE.sub('', text or '')
        text = _ACTION_LINE_RE.sub('', text)
        text = _FENCE_RE.sub('', text)
        text = _TTS_SPECIAL_RE.sub('', text)
        text = _TTS_EMOJI_RE.sub('', text)
        text = re.sub(r'\s+', ' ', text).strip()
        # Strip *tap* prefix
        if text.lower().startswith('*tap*'):
            text = text[5:].strip()
        # Speak the opening sentences only. A full search answer can run to
        # thousands of characters and took minutes to read out loud; the whole
        # reply stays visible in the chat box instead.
        limit = 240
        if len(text) > limit:
            cut = text[:limit]
            boundary = max(cut.rfind('. '), cut.rfind('! '), cut.rfind('? '))
            if boundary >= 100:
                text = cut[:boundary + 1].strip()
            else:
                text = cut.rsplit(' ', 1)[0].rstrip() + '...'
        return text

    # ── Public API ──

    def speak(self, text: str) -> None:
        """Queue text for speech."""
        if not text or not text.strip() or not self._running:
            self.speaking_finished.emit('')
            return
        enabled = self._get_cfg("tts_enabled", True)
        if not enabled:
            self.speaking_finished.emit('')
            return
        with self.lock:
            self.queue.append(text)

    def stop_speaking(self) -> None:
        """Immediately stop speaking and clear the queue."""
        self._stop_requested = True
        with self.lock:
            self.queue.clear()
        try:
            if self._engine:
                self._engine.stop()
        except Exception:
            log.warning("Failed to check TTS speaking state")

    def is_speaking(self) -> bool:
        """Check if currently speaking."""
        return self._current_text is not None

    def update_voice(self, voice_name: str) -> None:
        """Change voice (applied to next speech)."""
        if not self._engine or not self._engine_available:
            return
        try:
            voices = self._engine.getProperty('voices')
            for v in voices:
                if voice_name.lower() in v.name.lower():
                    self._engine.setProperty('voice', v.id)
                    break
        except Exception:
            log.warning("TTS run loop exception")

    def update_speed(self, speed: int) -> None:
        """Change speech speed (applied to next speech)."""
        if not self._engine or not self._engine_available:
            return
        try:
            self._engine.setProperty('rate', speed)
        except Exception:
            log.warning("Failed to update TTS speed")

    def stop(self) -> None:
        """Stop the thread gracefully."""
        self._running = False
        self.stop_speaking()
        self._shutdown_engine()
        self.quit()
        if not self.wait(2000):
            self.terminate()
            self.wait(500)
