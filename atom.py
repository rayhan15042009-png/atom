"""
Atom — Desktop Companion

Self-healing entry point: if dependencies are missing, auto-installs them
on first run, then starts normally.

Architecture (stabilized with Echo patterns):
- AtomApp: main lifecycle, worker creation, signal wiring
- ChatController: message routing, brain/voice coordination, memory
- InputHandler: local commands, keyboard shortcuts
- Familiar: 2D sprite roaming with direction changes
- Config: AppConfig dataclass (loaded once, passed by reference)
"""

from __future__ import annotations

import atexit
import subprocess
import sys
import time as _time
from pathlib import Path
from typing import Optional

# ── Phase 0: Self-healing — auto-install Python packages if missing ──
# This runs at module level so it catches ImportError BEFORE any PyQt6
# imports. If PyQt6 is missing, we pip install requirements.txt then restart.
# Only restarts on SUCCESS to avoid infinite loops.
try:
    import PyQt6
except ImportError:
    print("=" * 55)
    print("  ATOM — Auto-Installer")
    print("  Dependencies not found. Installing requirements...")
    print("=" * 55)
    _install_ok = False
    try:
        _req_path = Path(__file__).parent / "requirements.txt"
        _ret = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(_req_path)],
            timeout=300
        )
        _install_ok = _ret.returncode == 0
    except Exception as _exc:
        print(f"[Atom] Auto-install error: {_exc}")
    
    # Verify pip install actually made PyQt6 importable before restarting
    if _install_ok:
        _verify = subprocess.run(
            [sys.executable, "-c", "import PyQt6; print('OK')"],
            capture_output=True, timeout=10
        )
        if _verify.returncode == 0 and b'OK' in _verify.stdout:
            print("[Atom] Dependencies installed! Restarting...")
            subprocess.run([sys.executable] + sys.argv)
        else:
            print("[Atom] Pip completed but PyQt6 still not importable.")
            print("[Atom] Try: restart your terminal, or check Python environment.")
    else:
        print()
        print("=" * 55)
        print("  Failed to install dependencies.")
        print("  Please install manually:")
        print(f"    {sys.executable} -m pip install -r requirements.txt")
        print("=" * 55)
    sys.exit(0)

from PyQt6.QtGui import QSurfaceFormat
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

from core.config_manager import load_config, load_memory, save_memory
from core.eyes import PerceptionWorker
from core.brain import BrainThread
from core.voice import VoiceThread
from core.hands import ActionExecutor
from core.familiar import Familiar
from ui.animation_machine import AnimationMachine, AnimationState, Emotion
from ui.overlay import AtomOverlay
from ui.intro import AtomIntroSequence
from core.logger import get_logger

log = get_logger("atom")

# ── OpenGL surface format (must be set BEFORE QApplication) ──
_fmt = QSurfaceFormat()
_fmt.setAlphaBufferSize(8)
_fmt.setSamples(4)
_fmt.setSwapInterval(0)
_fmt.setDepthBufferSize(24)
_fmt.setStencilBufferSize(8)
_fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
QSurfaceFormat.setDefaultFormat(_fmt)

ROOT = Path(__file__).resolve().parent


# ════════════════════════════════════════════════════════════════════════════════
# InputHandler
# ════════════════════════════════════════════════════════════════════════════════

class InputHandler:
    """Parse user input: local commands vs. LLM-bound messages."""

    CMD_PREFIX = "/"

    def __init__(self, app: "AtomApp"):
        self.app = app

    def handle(self, text: str) -> Optional[str]:
        """Route input. Returns response text or None if async."""
        if not text or not text.strip():
            return None
        stripped = text.strip()
        if stripped.startswith(self.CMD_PREFIX):
            return self._handle_command(stripped)
        self.app._send_to_brain(stripped)
        return None

    def _handle_command(self, cmd: str) -> Optional[str]:
        parts = cmd[1:].strip().split(maxsplit=1)
        command = parts[0].lower() if parts else ""
        arg = parts[1] if len(parts) > 1 else ""
        handler = self._cmd_map().get(command)
        if handler:
            return handler(arg)
        self.app._send_to_brain(cmd)
        return None

    def _cmd_map(self):
        return {
            "help": lambda a: self._help(),
            "hello": lambda a: self._hello(),
            "hi": lambda a: self._hello(),
            "clear": lambda a: self._clear(),
            "cls": lambda a: self._clear(),
            "save": lambda a: self._save(),
            "mode": lambda a: self._mode(a),
            "remember": lambda a: self._remember(a),
            "forget": lambda a: self._forget(),
            "memory": lambda a: self._show_memory(),
            "mute": lambda a: self._mute(),
            "unmute": lambda a: self._unmute(),
            "tts": lambda a: self._tts(a),
            "voice": lambda a: self._tts(a),
            "speed": lambda a: self._speed(a),
            "status": lambda a: self._status(),
            "stats": lambda a: self._status(),
            "emoji": lambda a: self._emoji(),
            "pose": lambda a: self._pose(a),
            "emotion": lambda a: self._emotion(a),
            "wave": lambda a: self._wave(),
            "jump": lambda a: self._jump(),
            "spin": lambda a: self._spin(),
            "sleep": lambda a: self._sleep(),
            "wake": lambda a: self._wake(),
            "quit": lambda a: self._quit(),
            "exit": lambda a: self._quit(),
            "bye": lambda a: self._quit(),
            "roam": lambda a: self._roam(a),
            "settings": lambda a: self._settings(),
            "config": lambda a: self._settings(),
            "intro": lambda a: self._intro(),
            "browser": lambda a: self._browser(),
            "token": lambda a: self._token(),
            "time": lambda a: self._time(),
            "date": lambda a: self._time(),
            "allow": lambda a: self._allow(),
            "deny": lambda a: self._deny(),
            "workspace": lambda a: self._workspace(),
            "sandbox": lambda a: self._workspace(),
        }

    def _help(self) -> str:
        cmds = [
            "/help", "/clear", "/save", "/mode", "/remember",
            "/forget", "/memory", "/mute", "/unmute", "/speed",
            "/status", "/pose", "/emotion", "/wave", "/jump",
            "/spin", "/sleep", "/wake", "/roam", "/settings",
            "/intro", "/browser", "/time", "/workspace",
            "/allow", "/deny", "/quit",
        ]
        return "*tap* Commands: " + ", ".join(cmds)

    def _allow(self) -> Optional[str]:
        """Approve the action waiting for permission (same as typing `yes`)."""
        if self.app.hands and self.app.hands.pending is not None:
            self.app.hands.confirm()
            return None
        return "*tap* Nothing is waiting for permission."

    def _deny(self) -> Optional[str]:
        """Reject the action waiting for permission (same as typing `no`)."""
        if self.app.hands and self.app.hands.pending is not None:
            self.app.hands.cancel()
            return None
        return "*tap* Nothing is waiting for permission."

    def _workspace(self) -> str:
        """Show the single folder Atom may write to freely."""
        if not self.app.hands:
            return "*tap* Sandbox not ready yet."
        perms = getattr(self.app.config, 'permissions', None)
        outside = bool(getattr(perms, 'allow_outside_writes', True))
        tail = ("I ask you first before touching anything else"
                if outside else "writes out there are locked off")
        return (
            f"*tap* My writable sandbox is: {self.app.hands.workspace}\n"
            f"Everything else on disk is read-only — {tail}.\n"
            f"Commands: `/allow` / `/deny` for a pending action."
        )

    def _hello(self) -> str:
        return "*tap* Hey Koko. What's up?"

    def _clear(self) -> None:
        self.app.overlay.clear_chat()
        return None

    def _save(self) -> str:
        self.app._save_state()
        return "*tap* Saved, Koko."

    def _mode(self, arg: str) -> str:
        valid = ["tap", "stalk", "whisper", "lullaby", "formal"]
        if arg not in valid:
            return f"*tap* Valid modes: {', '.join(valid)}"
        self.app.config.mode = arg
        self.app._apply_mode()
        self.app._save_config()
        return f"*tap* Switched to {arg} mode."

    def _remember(self, arg: str) -> str:
        if not arg:
            return "*tap* What should I remember, Koko?"
        self.app.memory.setdefault("facts", []).append(arg)
        if len(self.app.memory["facts"]) > 50:
            self.app.memory["facts"] = self.app.memory["facts"][-50:]
        save_memory(self.app.memory)
        return "*tap* Got it, Koko."

    def _forget(self) -> str:
        self.app.memory["facts"] = []
        save_memory(self.app.memory)
        return "*tap* Memory wiped."

    def _show_memory(self) -> str:
        facts = self.app.memory.get("facts", [])
        if not facts:
            return "*tap* I don't remember anything yet."
        lines = ["*tap* Here's what I remember:"]
        for i, f in enumerate(facts[-10:], 1):
            lines.append(f"  {i}. {f}")
        return "\n".join(lines)

    def _mute(self) -> str:
        self.app.config.tts_enabled = False
        self.app._save_config()
        return "*tap* Muted."

    def _unmute(self) -> str:
        self.app.config.tts_enabled = True
        self.app._save_config()
        return "*tap* Unmuted."

    def _tts(self, arg: str) -> str:
        on = arg in ("on", "1", "yes", "true", "enable")
        off = arg in ("off", "0", "no", "false", "disable")
        if on:
            self.app.config.tts_enabled = True
        elif off:
            self.app.config.tts_enabled = False
        else:
            return "*tap* Usage: /tts on|off"
        self.app._save_config()
        return f"*tap* TTS {'enabled' if self.app.config.tts_enabled else 'disabled'}."

    def _speed(self, arg: str) -> str:
        try:
            speed = max(50, min(300, int(arg)))
            self.app.config.tts_speed = speed
            self.app._save_config()
            return f"*tap* TTS speed set to {speed}."
        except (ValueError, TypeError):
            return "*tap* Usage: /speed <50-300>"

    def _status(self) -> str:
        c = self.app.config
        perms = getattr(c, 'permissions', None)
        outside = getattr(perms, 'allow_outside_writes', True)
        sandbox = str(self.app.hands.workspace) if self.app.hands else 'not ready'
        return (
            f"*tap* Mode: {c.mode} | Char: {c.character} | "
            f"TTS: {'ON' if c.tts_enabled else 'OFF'} | "
            f"Brain: {'running' if self.app.brain and self.app.brain.isRunning() else 'stopped'}\n"
            f"Sandbox: {sandbox} (outside writes: {'with permission' if outside else 'blocked'})"
        )

    def _emoji(self) -> str:
        return "*tap* ⌐◨-◨"

    def _pose(self, arg: str) -> str:
        if not arg:
            return "*tap* Usage: /pose <idle|walk|wave|spin|jump|sleep>"
        valid = ["idle", "walk", "wave", "spin", "jump", "sleep"]
        if arg not in valid:
            return f"*tap* Valid poses: {', '.join(valid)}"
        if self.app.anim_machine:
            try:
                self.app.anim_machine._transition(AnimationState(arg))
            except ValueError:
                self.app.overlay.set_pose(arg)
        return f"*tap* Pose set to {arg}."

    def _emotion(self, arg: str) -> str:
        if not arg:
            return "*tap* Usage: /emotion <happy|sad|excited|annoyed|flirty|neutral>"
        valid = ["neutral", "happy", "sad", "excited", "annoyed", "flirty", "sleepy"]
        if arg not in valid:
            return f"*tap* Valid emotions: {', '.join(valid)}"
        if self.app.anim_machine:
            try:
                self.app.anim_machine.set_emotion(Emotion(arg), duration=5.0)
            except ValueError:
                log.debug("Invalid emotion '%s' for animation_machine, using overlay fallback", arg)
        self.app.overlay.set_emotion(arg)
        return f"*tap* Emotion set to {arg}."

    def _wave(self) -> str:
        if self.app.anim_machine:
            self.app.anim_machine.signal_wave()
        return "*tap* *waves*"

    def _jump(self) -> str:
        if self.app.anim_machine:
            self.app.anim_machine.trigger_jump()
        return "*tap* *jumps*"

    def _spin(self) -> str:
        if self.app.anim_machine:
            self.app.anim_machine._transition(AnimationState.SPIN)
        return "*tap* *spins*"

    def _sleep(self) -> str:
        if self.app.anim_machine:
            self.app.anim_machine._transition(AnimationState.SLEEP)
        self.app.overlay.set_pose("sleep")
        return "*tap* Goodnight, Koko..."

    def _wake(self) -> str:
        if self.app.anim_machine:
            self.app.anim_machine._transition(AnimationState.IDLE)
        self.app.overlay.set_pose("idle")
        return "*tap* I'm awake!"

    def _quit(self) -> str:
        QTimer.singleShot(100, self.app._shutdown)
        return "*tap* Shutting down..."

    def _roam(self, arg: str) -> str:
        # Roaming handled by animation_machine, familiar removed in Step 10
        if arg in ("on", "1", "yes", "true", "enable"):
            if self.app.anim_machine:
                self.app.anim_machine.should_roam = True
            return "*tap* Roaming enabled."
        elif arg in ("off", "0", "no", "false", "disable"):
            if self.app.anim_machine:
                self.app.anim_machine.should_roam = False
            return "*tap* Roaming disabled."
        roaming_state = self.app.anim_machine.should_roam if self.app.anim_machine else False
        return "*tap* Roaming is {}.".format("ON" if roaming_state else "OFF")

    def _settings(self) -> None:
        self.app.overlay._open_settings()
        return None

    def _intro(self) -> str:
        return "*tap* Intro already played at startup. Restart Atom to see it again."

    def _browser(self) -> str:
        self.app.overlay.browser_login_requested.emit()
        return "*tap* Opening browser login..."

    def _token(self) -> str:
        return "*tap* ⌐◨-◨  TAP  ◨-◨"

    def _time(self) -> str:
        return f"*tap* {_time.strftime('%A, %B %d, %Y — %I:%M %p')}"


# ════════════════════════════════════════════════════════════════════════════════
# ChatController
# ════════════════════════════════════════════════════════════════════════════════

class ChatController(QObject):
    """Routes messages between overlay, brain, and voice."""

    def __init__(self, app: "AtomApp"):
        super().__init__()
        self.app = app
        self._processing = False

    # Plain words that approve / deny a pending action.
    _ALLOW_WORDS = frozenset({
        "yes", "y", "yeah", "yep", "ok", "okay", "sure", "allow", "approve",
        "proceed", "confirm", "do it", "go ahead", "run it",
    })
    _DENY_WORDS = frozenset({
        "no", "n", "nope", "deny", "cancel", "stop", "abort",
        "don't", "dont", "never mind", "nevermind",
    })

    def _handle_permission_reply(self, stripped: str) -> bool:
        """Answer a pending action-permission prompt with yes/no.

        Returns True when the input was consumed as a permission answer.
        """
        hands = self.app.hands
        if hands is None or hands.pending is None:
            return False
        if stripped in self._ALLOW_WORDS:
            self._display_response("*tap* Allowed — running it now.")
            hands.confirm()
            return True
        if stripped in self._DENY_WORDS:
            self._display_response("*tap* Denied. Dropping it.")
            hands.cancel()
            return True
        return False

    def on_user_input(self, text: str) -> None:
        if not text.strip():
            return
        # Signal listening state to animation machine
        if self.app.anim_machine:
            self.app.anim_machine.signal_listening()
        self.app.overlay.add_chat_message("user", text)
        self.app.overlay.set_speech("")
        stripped = text.strip().lower()
        # An action waiting for permission is answered by yes / no before anything
        # else — otherwise the words would just be chat.
        if self._handle_permission_reply(stripped):
            return
        # Check for exit/quit as standalone words (no / prefix needed)
        if stripped in ('exit', 'quit', 'goodbye', 'bye', 'good night'):
            self.app.overlay.add_chat_message("system", "*tap* Goodbye, Koko.", animate=False)
            QTimer.singleShot(500, self.app._shutdown)
            return
        # Check for formal! toggle (any number of ! marks, no / prefix needed)
        if stripped.startswith('formal') and len(stripped) > 6 and all(c == '!' for c in stripped[6:]):
            current = self.app.config.mode
            new_mode = 'formal' if current != 'formal' else 'tap'
            self.app.config.mode = new_mode
            self.app._apply_mode()
            self.app._save_config()
            self._display_response(f"*tap* Switched to {new_mode} mode.")
            return
        handler = InputHandler(self.app)
        result = handler.handle(text)
        if result is not None:
            self._display_response(result)

    def _display_response(self, text: str) -> None:
        if not text:
            return
        # The reply is here, so the "Atom is thinking…" row must go. It used to
        # be cleared only on errors and on the next request, leaving a stale
        # placeholder sitting under every finished answer.
        self.app.overlay.set_thinking(False)
        self.app.overlay.add_chat_message("assistant", text, animate=True)
        if self.app.anim_machine:
            self.app.anim_machine.parse_emotion_from_text(text)
        if self.app.voice and self.app.config.tts_enabled:
            self.app.voice.speak(text)
        self._processing = False

    def _on_brain_response(self, text: str) -> None:
        self._display_response(text)

    def on_save_memory(self, text: str = "") -> None:
        if text:
            self.app.memory.setdefault("facts", []).append(text)
            save_memory(self.app.memory)

    def forget_memory(self) -> None:
        self.app.memory["facts"] = []
        save_memory(self.app.memory)

    def on_settings_changed(self, changes: dict) -> None:
        from core.config_manager import _BACKWARD_COMPAT
        # Sub-config names that must remain as dataclass instances
        _CONFIG_SUB_KEYS = frozenset({
            "_3d", "roaming_3d", "emotion", "browser_ai", "chat",
            "intro", "ocr", "network", "theme", "skins", "permissions",
        })
        for key, value in changes.items():
            # NEVER replace a sub-config dataclass with a raw value
            if key in _CONFIG_SUB_KEYS:
                continue
            # Direct attribute on config
            if hasattr(self.app.config, key):
                setattr(self.app.config, key, value)
                continue
            # Backward-compat key → resolve to sub-config path
            if key in _BACKWARD_COMPAT:
                spec = _BACKWARD_COMPAT[key]
                if len(spec) == 2 and spec[0]:
                    sub_name, field_name = spec
                    sub = getattr(self.app.config, sub_name, None)
                    if sub is not None and hasattr(sub, field_name):
                        setattr(sub, field_name, value)
                elif len(spec) == 2 and not spec[0]:
                    # Top-level alias: ("", "field")
                    if hasattr(self.app.config, spec[1]):
                        setattr(self.app.config, spec[1], value)
                elif len(spec) == 1:
                    # Top-level field rename: ("new_name",)
                    if hasattr(self.app.config, spec[0]):
                        setattr(self.app.config, spec[0], value)
        if "mode" in changes:
            self.app._apply_mode()
        if "character" in changes:
            self.app.overlay.set_pose("idle")
            self.app.overlay.set_emotion("neutral")
        self.app._save_config()


# ════════════════════════════════════════════════════════════════════════════════
# AtomApp
# ════════════════════════════════════════════════════════════════════════════════

class AtomApp:
    """Main application — creates and wires all components.

    Created AFTER the intro sequence completes (GTA V-style boot: intro first,
    then load the game). The intro is handled entirely by AtomIntroSequence
    in main() — AtomApp never deals with intro screens.
    """

    def __init__(self, argv: list[str], qt_app: Optional[QApplication] = None):
        if qt_app is not None:
            self.app = qt_app
        else:
            self.app = QApplication(argv)
            self.app.setApplicationName("Atom")
            self.app.setQuitOnLastWindowClosed(False)

        self.config = load_config()
        self.memory = load_memory()

        self.overlay: Optional[AtomOverlay] = None
        self.chat_controller: Optional[ChatController] = None
        self.brain: Optional[BrainThread] = None
        self.eyes: Optional[PerceptionWorker] = None
        self.voice: Optional[VoiceThread] = None
        self.anim_machine: Optional[AnimationMachine] = None
        self.hands: Optional[ActionExecutor] = None
        self.familiar: Optional[Familiar] = None
        # True while the brain is waiting on a yes/no for a tool it requested.
        self._awaiting_action_result = False

        self._familiar_timer = QTimer()

        # Build non-overlay components immediately (brain, eyes, voice, etc.)
        # Overlay is created later in start() to avoid FramelessWindowHint conflict
        self._build_components()
        self._setup_signals()
        self._load_env()

        self._roam_home_x = float(self.config.start_x)
        self._roam_home_y = float(self.config.start_y)

        # _shutdown is registered with atexit *and* called from the quit paths,
        # so it must be safe to run more than once (it used to log twice, stop
        # workers twice and save state twice on every exit).
        self._shutting_down = False
        atexit.register(self._shutdown)
        log.info("AtomApp initialized (mode=%s, char=%s)", self.config.mode, self.config.character)

    def start(self) -> None:
        """Create overlay, start workers, show.
        Called after AtomIntroSequence completes (or immediately if no intro).
        Overlay/hands creation is deferred here to avoid FramelessWindowHint conflict
        with intro's full-screen QDialogs."""
        self._init_overlay()
        self.overlay.set_mode_indicator(self.config.mode)
        self.overlay.set_status("Atom ready")
        self._update_status_hud_provider()
        self._check_gemini_key_on_startup()
        self.overlay._init_window_flags()
        self.overlay.show()
        self._start_workers()
        log.info("AtomApp started (mode=%s, char=%s)", self.config.mode, self.config.character)

    def _build_components(self) -> None:
        """Create all components. Overlay/hands are deferred if intro is enabled
        (to prevent DWM crash from multiple FramelessWindowHint windows)."""
        self.overlay = None
        self.hands = None
        self.anim_machine = AnimationMachine()
        self.chat_controller = ChatController(self)

        # Initialize Familiar for 2D sprite roaming
        cfg_dict = self._config_to_dict()
        self.familiar = Familiar(cfg_dict)

        self.brain = BrainThread(self.config, self.memory)
        self.eyes = PerceptionWorker(cfg_dict)
        self.voice = VoiceThread(cfg_dict)

    def _init_overlay(self) -> None:
        """Create overlay, hands, wire overlay/hands signals, and start timers.
        Called after intro completes or immediately if intro is disabled."""
        self.overlay = AtomOverlay(self.config)
        self.hands = ActionExecutor(self.overlay)
        self.overlay.familiar = self.familiar

        # Wire overlay and hands signals
        self.overlay.user_input.connect(self.chat_controller.on_user_input)
        self.overlay.settings_changed.connect(self.chat_controller.on_settings_changed)
        self.overlay.stop_requested.connect(self._on_stop_requested)
        self.overlay.sprite_drag_finished.connect(self._on_sprite_drag_finished)
        self.overlay.browser_login_requested.connect(self._on_browser_login)
        self.overlay.browser_logout_requested.connect(self._on_browser_logout)

        if self.hands:
            self.overlay.confirm_action.connect(self.hands.confirm)
            self.overlay.cancel_action.connect(self.hands.cancel)
            self.hands.confirmed.connect(self._on_action_confirmed)
            self.hands.cancelled.connect(self._on_action_cancelled)

        # Sync familiar position to overlay
        if self.familiar:
            self.familiar.set_home(self.overlay._sprite_x, self.overlay._sprite_y)

        # Start familiar roaming timer
        self._familiar_timer = QTimer()
        self._familiar_timer.timeout.connect(self._update_familiar)
        self._familiar_timer.start(200)  # 5 FPS — roaming doesn't need 10 updates/s, saves ~50% CPU

    # ── Browser AI login ──

    def _on_browser_login(self) -> None:
        """Open a real browser window so Koko can log into the web AI once.

        The headless provider reuses the saved session, so this login is what
        makes Browser AI usable at all. It runs on a thread because waiting for
        a manual login can take minutes.
        """
        if getattr(self, "_browser_login_thread", None) is not None \
                and self._browser_login_thread.is_alive():
            self.overlay.show_message("*tap* The login window is already open.", duration=4000)
            return
        self.overlay.add_chat_message(
            "system",
            "*tap* Opening a browser window — log into DeepSeek there. "
            "I'll wait up to 5 minutes and remember the session.",
            animate=False,
        )
        import threading

        self._browser_login_thread = threading.Thread(
            target=self._run_browser_login, daemon=True, name="browser-login",
        )
        self._browser_login_thread.start()

    def _run_browser_login(self) -> None:
        """Worker body for `_on_browser_login` (never touches widgets directly
        except through the overlay's thread-safe message helpers)."""
        from core.providers.browser import AUTH_STATE_PATH, BrowserWorker

        worker = None
        ok = False
        try:
            worker = BrowserWorker(self._config_to_dict())
            if not worker.start(headless=False):
                raise RuntimeError("the browser could not be launched")
            # navigate() logs "chat input not detected" on the sign-in page;
            # that is the expected state before logging in.
            worker.navigate()
            ok = worker.wait_for_login(timeout_seconds=300)
        except Exception as exc:
            log.warning("Browser login failed: %s", exc)
        finally:
            if worker is not None:
                try:
                    worker.close()
                except Exception:
                    pass

        if ok:
            log.info("Browser AI login saved to %s", AUTH_STATE_PATH.name)
            self.overlay.add_chat_message(
                "system",
                "*tap* Browser AI is logged in — the provider can answer now.",
                animate=False,
            )
            self.overlay.show_message("*tap* Browser AI logged in.", duration=5000)
        else:
            log.warning("Browser AI login was not completed")
            self.overlay.add_chat_message(
                "system",
                "*tap* Login wasn't completed (timed out or closed). "
                "Settings → Advanced → Login to DeepSeek to try again.",
                animate=False,
            )

    def _on_browser_logout(self) -> None:
        """Forget the saved browser session."""
        from core.providers.browser import AUTH_STATE_PATH

        try:
            AUTH_STATE_PATH.unlink(missing_ok=True)
            log.info("Browser AI session cleared")
            self.overlay.show_message("*tap* Browser session cleared.", duration=4000)
        except Exception as exc:
            log.warning("Could not clear the browser session: %s", exc)

    def _config_to_dict(self) -> dict:
        """Flatten AppConfig into a flat dict, expanding sub-configs.

        Sub-configs like `self.config.ocr`, `self.config.intro`, etc.
        are expanded so workers get flat keys like 'ocr_interval_seconds',
        'intro_enabled', 'confirmation_required', etc.
        """
        d = {}
        for attr in dir(self.config):
            if attr.startswith("_") or callable(getattr(self.config, attr)):
                continue
            val = getattr(self.config, attr)
            # Expand sub-config dataclasses into their fields
            if hasattr(val, "keys") and callable(val.keys):
                try:
                    for sub_key in val.keys():
                        sub_val = getattr(val, sub_key) if hasattr(val, sub_key) else None
                        d[sub_key] = sub_val
                except Exception:
                    d[attr] = val
            else:
                d[attr] = val
        # Also add backward-compat keys so consumers using old-style names find values
        from core.config_manager import _BACKWARD_COMPAT
        for old_key, spec in _BACKWARD_COMPAT.items():
            if old_key not in d:
                if len(spec) == 2 and spec[0]:
                    # Sub-config path: ("sub_name", "field_name")
                    sub = getattr(self.config, spec[0], None)
                    if sub and hasattr(sub, spec[1]):
                        d[old_key] = getattr(sub, spec[1])
                elif len(spec) == 2 and not spec[0]:
                    # Top-level alias with empty sub-name: ("", "field")
                    if hasattr(self.config, spec[1]):
                        d[old_key] = getattr(self.config, spec[1])
                elif len(spec) == 1:
                    # Top-level field rename: ("new_name",)
                    if hasattr(self.config, spec[0]):
                        d[old_key] = getattr(self.config, spec[0])
        return d

    def _setup_signals(self) -> None:
        """Wire non-overlay signals. Overlay/hands signals are wired in _init_overlay()."""
        if self.brain:
            self.brain.response_ready.connect(self.chat_controller._on_brain_response)
            self.brain.error.connect(self._on_brain_error)
            self.brain.action_requested.connect(self._on_brain_action)
            self.brain.provider_used.connect(self._on_provider_used)
            self.brain.fact_learned.connect(self.chat_controller.on_save_memory)

        if self.eyes:
            self.eyes.ocr_ready.connect(self._on_ocr_ready)
            self.eyes.ocr_interesting.connect(self._on_ocr_interesting)
            self.eyes.active_window.connect(self._on_active_window)
            self.eyes.error.connect(self._on_perception_error)

        if self.voice:
            self.voice.speaking_started.connect(self._on_speaking_started)
            self.voice.speaking_finished.connect(self._on_speaking_finished)

        if self.anim_machine:
            self.anim_machine.state_changed.connect(self._on_animation_state)
            self.anim_machine.emotion_changed.connect(self._on_emotion_changed)
            self.anim_machine.jump_triggered.connect(self._on_jump_triggered)
            self.anim_machine.animation_started.connect(self._on_animation_event)
            self.anim_machine.animation_finished.connect(self._on_animation_event)
            self.anim_machine.roam_position_changed.connect(self._on_roam_position)


    def _start_workers(self) -> None:
        if self.brain:
            self.brain.start()
        if self.eyes:
            self.eyes.start()
        if self.voice:
            self.voice.start()

    def _check_gemini_key_on_startup(self) -> None:
        """Check Gemini API key validity and show warning on the HUD if invalid."""
        provider = self.config.get('llm_provider', 'ollama')
        if provider != 'gemini':
            return
        from core.brain import validate_gemini_key, get_gemini_keys
        keys = get_gemini_keys(self.config)
        for key in keys:
            err = validate_gemini_key(key)
            if err:
                warning = f"Gemini key issue: {err}"
                log.warning(warning)
                if hasattr(self.overlay, '_status_hud'):
                    self.overlay._status_hud.set_key_warning(err)
                return

    def _on_roam_position(self, roam_x: float, velocity: float) -> None:
        """Update sprite screen position from animation machine roaming."""
        if not self.overlay or not self.anim_machine:
            return
        if self.overlay._dragging:
            return
        # Compute absolute screen x from home + roam offset
        abs_x = self._roam_home_x + roam_x
        # Clamp to screen bounds
        sprite_w = self.overlay._sprite.width if hasattr(self.overlay, '_sprite') else 80
        max_x = self.overlay.width() - sprite_w - 10
        abs_x = max(10.0, min(float(max_x), abs_x))
        self.overlay._sprite_x = abs_x
        # Set pose based on velocity
        if abs(velocity) > 0.5:
            self.overlay.set_pose("walk" if abs(velocity) < 2.0 else "run")
        self.overlay.update_input_mask()

    def _update_familiar(self) -> None:
        """Update Familiar roaming position and sync to overlay.

        Only syncs position and pose when the overlay has no locked/overridden
        pose (listen, talk, sleep, wave, etc.) to avoid overwriting state set
        by the brain/voice handlers.
        """
        if not self.familiar or not self.overlay:
            return
        if self.overlay._dragging:
            return
        bounds = (
            0, 0,
            self.overlay.width() - self.familiar.sprite_width,
            self.overlay.height() - self.familiar.sprite_height,
        )
        self.familiar.update_position(bounds)
        # Sync familiar position to overlay sprite position
        self.overlay._sprite_x = float(self.familiar.x)
        self.overlay._sprite_y = float(self.familiar.y)
        # Sync pose only for basic poses — don't override special poses
        POSE_LOCKED = ('listen', 'talk', 'sleep', 'wave', 'spin', 'jump', 'bored', 'sit')
        if self.overlay.current_pose not in POSE_LOCKED:
            moving = abs(self.familiar.vx) + abs(self.familiar.vy)
            if moving:
                self.overlay.current_pose = 'run' if moving >= 5 else 'walk'
            else:
                self.overlay.current_pose = 'idle'

    def _load_env(self) -> None:
        from core.brain import load_dotenv as _load_dotenv
        _load_dotenv(str(ROOT / ".env"))

    def _update_status_hud_provider(self, note: str = '') -> None:
        """Update the persistent status HUD with current provider/model info.

        ``note`` is the Gemini key slot that answered (``"#3/5"``); it is cleared
        whenever the configured provider changes, so a stale slot never lingers.
        """
        if hasattr(self.overlay, '_status_hud'):
            provider = self.config.get('llm_provider', 'ollama')
            model = self.config.get('model', 'phi3:mini')
            if provider == 'gemini':
                model = self.config.get('gemini_model', 'gemini-2.5-flash')
            elif provider == 'browser':
                model = 'DeepSeek'
            self.overlay._status_hud.set_provider(provider, model, 'unknown', note)

    def _on_provider_used(self, provider: str, note: str) -> None:
        """The brain reports what actually answered — trust it over the config."""
        if not hasattr(self.overlay, '_status_hud'):
            return
        if provider and provider != '?':
            model = self.config.get('model', 'phi3:mini')
            if provider == 'gemini':
                model = self.config.get('gemini_model', 'gemini-2.5-flash')
            elif provider == 'browser':
                model = 'DeepSeek'
            self.overlay._status_hud.set_provider(provider, model, 'online', note)
        else:
            self._update_status_hud_provider(note)

    def _apply_mode(self) -> None:
        self.overlay.set_mode_indicator(self.config.mode)
        # Auto-enable/disable roaming based on mode
        roam_enabled = self.config.mode in ("tap", "stalk")
        # Use Familiar for 2D roaming; disable animation_machine roaming to avoid conflict
        if self.anim_machine:
            self.anim_machine.should_roam = False
            if not roam_enabled:
                self.anim_machine.target_velocity = 0.0
                self.anim_machine.velocity = 0.0
        if self.familiar:
            self.familiar.roaming = roam_enabled
            if not roam_enabled:
                self.familiar.freeze()
            else:
                self.familiar.resume()
        log.info("Mode set to: %s (roaming=%s)", self.config.mode, roam_enabled)

    # ── Brain ──

    #: Characters of chat history handed to the model per request.
    _HISTORY_CHAR_BUDGET = 6000

    def _send_to_brain(self, text: str) -> None:
        if not self.brain or not self.brain.isRunning():
            self.chat_controller._display_response("*tap* My brain's not online yet, Koko.")
            return
        history = self._get_chat_history()
        # The overlay already appended the current user message to the chat list
        # (ChatController.on_user_input runs first). Drop it so the LLM does not
        # receive the same question twice — duplicated turns confuse the model.
        if history and history[-1]["role"] == "user" \
                and history[-1]["content"].strip() == text.strip():
            history = history[:-1]
        payload = {
            "text": text,
            "mode": self.config.mode,
            "screen_context": self.overlay.get_context(),
            "history": history,
            "memory": self.memory.get("facts", []),
        }
        self.overlay.set_thinking(True)
        self.brain.ask(payload)

    def _get_chat_history(self) -> list[dict]:
        """Build the LLM message history from the visible chat log.

        Uses the chat item's custom role data (``UserRole + 1``) rather than the
        display/decoration roles — reading the wrong role made every prior turn
        look like a user message, which broke multi-turn question answering.
        Date separators and the transient "thinking" row are skipped.
        """
        history: list[dict] = []
        if not hasattr(self.overlay, "chat_list"):
            return history
        for i in range(self.overlay.chat_list.count()):
            item = self.overlay.chat_list.item(i)
            if item is None:
                continue
            if item.data(Qt.ItemDataRole.UserRole + 6) == "thinking":
                continue
            if item.data(Qt.ItemDataRole.UserRole + 5):
                continue  # date separator row
            raw_role = item.data(Qt.ItemDataRole.UserRole + 1)
            text = (item.text() or "").strip()
            if not text:
                continue
            # Atom's own output is logged as "system" — that is an assistant turn.
            role = "assistant" if raw_role in ("assistant", "system") else "user"
            history.append({"role": role, "content": text})
        # The transcript survives restarts, so sixteen turns can be tens of
        # thousands of characters. A local model on CPU then spends the whole
        # request timeout on prefill and answers late or not at all, so keep the
        # newest turns that fit a character budget (plus the 16-turn cap).
        budget = self._HISTORY_CHAR_BUDGET
        kept: list[dict] = []
        used = 0
        for turn in reversed(history[-16:]):
            used += len(turn["content"])
            if kept and used > budget:
                break
            kept.append(turn)
        kept.reverse()
        return kept
    def _on_brain_error(self, msg: str) -> None:
        log.warning("Brain error: %s", msg)
        self.overlay.set_thinking(False)
        self.chat_controller._display_response(f"*tap* Error: {msg[:200]}")

    # ── Perception ──

    def _on_ocr_ready(self, data: dict) -> None:
        text = data.get("ocr_text", "")
        window = data.get("active_window", "")
        if text:
            self.overlay.set_screen_context(window, text)

    def _on_ocr_interesting(self, data: dict) -> None:
        reason = data.get("reason", "unknown")
        text = data.get("ocr_text", "")[:100]
        log.info("Interesting OCR: %s — %s", reason, text)
        if self.config.mode == "stalk" and text and not self.chat_controller._processing:
            self._send_to_brain(f"[Screen noticed: {reason}] {text}")

    def _on_active_window(self, title: str) -> None:
        pass

    def _on_perception_error(self, msg: str) -> None:
        log.warning("Perception error: %s", msg)

    # ── Voice ──

    def _on_speaking_started(self, text: str) -> None:
        if self.anim_machine:
            self.anim_machine.signal_responding()
        self.overlay.set_speech(text)
        self.overlay.set_pose("talk")

    def _on_speaking_finished(self, text: str) -> None:
        if self.anim_machine:
            self.anim_machine.signal_speech_finished()
        QTimer.singleShot(2000, lambda: self.overlay.set_speech(""))
        self.overlay.set_pose("idle")

    # ── Animation ──

    def _on_animation_state(self, old: AnimationState, new: AnimationState) -> None:
        self.overlay.set_pose(new.value)
        if new == AnimationState.SLEEP:
            self.overlay.set_emotion("sleepy")
        elif new == AnimationState.IDLE:
            self.overlay.set_pose("idle")
            self.overlay.set_emotion("neutral")

    def _on_emotion_changed(self, emotion: Emotion) -> None:
        self.overlay.set_emotion(emotion.value)
        speed_map = {
            Emotion.NEUTRAL: 1.0, Emotion.HAPPY: 1.15,
            Emotion.EXCITED: 1.3, Emotion.SAD: 0.7,
            Emotion.SLEEPY: 0.5, Emotion.ANNOYED: 1.1,
            Emotion.FLIRTY: 1.1,
        }
        self.overlay._sprite.set_mood_speed(speed_map.get(emotion, 1.0))

    def _on_jump_triggered(self) -> None:
        """Trigger jump animation — brief pose then return to idle."""
        if self.overlay:
            self.overlay.set_pose("jump")
            QTimer.singleShot(600, lambda: self.overlay.set_pose("idle"))

    def _on_animation_event(self, name: str) -> None:
        if name.startswith("micro_"):
            self.overlay.set_micro_interaction(name[6:])
            QTimer.singleShot(1500, lambda: self.overlay.set_micro_interaction(""))
        elif name.startswith("bored_"):
            self.overlay.set_boredom_tier(int(name[-1]))
            QTimer.singleShot(2000, lambda: self.overlay.set_boredom_tier(0))

    # ── Actions ──

    def _describe_action(self, action: dict) -> str:
        """Short human-readable summary of a tool call, for the permission prompt."""
        act = action.get("action", "action")
        path = action.get("path") or action.get("file") or action.get("target") or ""
        if act == "write_file":
            return f"write {len(str(action.get('text', '')))} chars to {path}"
        if act == "append_file":
            return f"append to {path}"
        if act == "make_dir":
            return f"create folder {path}"
        if act == "delete_file":
            return f"delete {path}"
        if act == "shell":
            return f"run shell: {action.get('command', '')}"
        if act == "type":
            return f"type: {str(action.get('text', ''))[:60]}"
        if act == "open":
            return f"open {path or action.get('target', '')}"
        if act == "click":
            return f"click at ({action.get('x')}, {action.get('y')})"
        if act == "chain":
            steps = action.get("steps") or []
            shown = "; ".join(self._describe_action(s) for s in steps[:4])
            extra = f" (+{len(steps) - 4} more)" if len(steps) > 4 else ""
            return f"run {len(steps)} steps: {shown}{extra}"
        return f"{act} {path}".strip()

    def _on_action_confirmed(self, result: str) -> None:
        log.info("Action confirmed: %s", result[:100])
        if self._awaiting_action_result:
            # Let the model answer with the real tool output instead of a canned line.
            self._awaiting_action_result = False
            if self.brain:
                self.brain.feed_action_result(result)
            return
        self.overlay.show_message("*tap* Done.", duration=4000)

    def _on_action_cancelled(self, msg: str) -> None:
        log.info("Action cancelled: %s", msg)
        # Always surface the outcome immediately — when a write is refused or
        # denied, waiting for the model's explanation would look like a freeze.
        self.overlay.show_message(f"*tap* {msg}", duration=6000)
        if self._awaiting_action_result:
            self._awaiting_action_result = False
            if self.brain:
                self.brain.feed_action_result(
                    f"Koko denied permission or the action failed: {msg}"
                )

    def _on_brain_action(self, action: dict) -> None:
        """Run a tool the brain asked for — asking Koko first when it is not safe.

        Read-only tools and writes inside the sandbox workspace run immediately.
        Writes anywhere else, plus shell/type/click/open, stop here and wait for
        an explicit yes/no from the user.
        """
        if not self.hands:
            log.warning("No ActionExecutor available for brain action")
            return
        try:
            if self.hands.pending is not None:
                self.overlay.add_chat_message(
                    "system",
                    "*tap* Still waiting on `yes`/`no` for the last action — ignoring this one.",
                    animate=False,
                )
                return
            # Refuse up-front when no approval could change the answer, so Koko
            # is never asked for a permission that cannot be honoured.
            blocked = self.hands.blocked_reason(action)
            if blocked:
                self.overlay.set_thinking(False)
                self._awaiting_action_result = False
                message = f"*tap* Blocked — {blocked}"
                self.overlay.add_chat_message("system", message, animate=False)
                if self.brain:
                    self.brain.feed_action_result(
                        f"REFUSED by the sandbox policy: {blocked} "
                        f"Tell Koko plainly, in one short line, and offer the "
                        f"alternative of writing inside {self.hands.workspace}."
                    )
                return
            if self.hands.requires_permission(action):
                desc = action.get("description") or self._describe_action(action)
                self.overlay.set_thinking(False)
                self._awaiting_action_result = True
                self.hands.request_action(action)
                self.overlay.add_chat_message(
                    "system",
                    f"*tap* Permission needed — {desc}\n"
                    f"Reply `yes` to allow or `no` to deny. "
                    f"My workspace ({self.hands.workspace}) is always writable; "
                    f"everything else needs your ok.",
                    animate=False,
                )
                return
            result = self.hands.execute(action)
            if self.brain:
                self.brain.feed_action_result(result)
        except Exception as exc:
            log.warning("Brain action failed: %s", exc)
            if self.brain:
                self.brain.feed_action_result(f"Action failed: {exc}")

    def _on_sprite_drag_finished(self, x: float, y: float) -> None:
        """When user finishes dragging the sprite, update roaming home
        and sync familiar position to prevent snap-back on next tick."""
        self._roam_home_x = x
        self._roam_home_y = y
        if self.anim_machine:
            self.anim_machine.home_x = x
            self.anim_machine.x = 0.0
            self.anim_machine.velocity = 0.0
            self.anim_machine.target_velocity = 0.0
        # Sync familiar position so _update_familiar doesn't snap sprite back
        if self.familiar:
            self.familiar.x = x
            self.familiar.y = y
            self.familiar.vx = 0.0
            self.familiar.vy = 0.0

    def _on_stop_requested(self) -> None:
        if self.brain:
            self.brain.clear_queue()
        if self.voice:
            self.voice.stop_speaking()
        self.overlay.set_thinking(False)

    # Familiar roaming removed in Step 10 — roaming handled by animation_machine

    # ── Save / Shutdown ──

    def _save_state(self) -> None:
        self.config.save()
        save_memory(self.memory)

    def _save_config(self) -> None:
        self.config.save()

    def _shutdown(self) -> None:
        """Shut down all workers and save state.

        Idempotent: both the quit paths and ``atexit`` call it, and the second
        pass used to stop every worker and write state again.
        """
        if getattr(self, "_shutting_down", False):
            return
        self._shutting_down = True
        log.info("Shutting down...")
        if self.brain and self.brain.isRunning():
            self.brain.stop()
        if self.eyes and self.eyes.isRunning():
            self.eyes.stop()
        if self.voice and self.voice.isRunning():
            self.voice.stop()
        if self.anim_machine:
            self.anim_machine.cleanup()
        if self.familiar:
            self.familiar.stop()
        if hasattr(self, '_familiar_timer') and self._familiar_timer:
            self._familiar_timer.stop()
        self._save_state()
        QApplication.quit()

    def run(self) -> int:
        return self.app.exec()


# ════════════════════════════════════════════════════════════════════════════════
# Entry point
# ════════════════════════════════════════════════════════════════════════════════

def main():
    """GTA V-style boot: intro first → THEN load the game.

    Sequence:
    0. Run dependency installer (Ollama, Tesseract, models, assets)
       BEFORE QApplication to avoid GUI freeze during model pulls
    1. Create QApplication (required for any GUI)
    2. Load config
    3. If intro enabled: Play AtomIntroSequence (Boot → Optimus → Welcome → Dance)
       - No Atom components exist yet — only the intro's own QDialogs
       - After all 4 screens complete → create AtomApp + show overlay
    4. If intro disabled: Create AtomApp + show overlay immediately
    """
    # Phase 0: Run full dependency installer BEFORE QApplication creation
    # This prevents GUI freeze during long operations (model pulls can take minutes)
    # The installer reads config.json internally for model names
    try:
        from core.installer import run_setup
        run_setup(pull_model=True)
    except Exception as _exc:
        print(f"[Atom] Dependency check: {_exc}")

    QSurfaceFormat.setDefaultFormat(_fmt)
    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName("Atom")
    qt_app.setQuitOnLastWindowClosed(False)

    config = load_config()

    if config.intro.enabled:
        log.info("Atom boot: intro first (GTA V-style)")
        intro = AtomIntroSequence(config)
        _intro_done = False  # Guard flag: intro completion vs the bootstrap timeout race

        def on_intro_done():
            nonlocal _intro_done
            if _intro_done:
                return
            _intro_done = True
            intro.stop()
            intro.deleteLater()
            log.info("Intro complete — starting Atom")
            atom = AtomApp(sys.argv, qt_app=qt_app)
            atom.start()

        def on_intro_error():
            nonlocal _intro_done
            if _intro_done:
                return
            _intro_done = True
            log.warning("Intro timeout — starting Atom directly")
            # Tear the intro down first: without this the frameless full-screen
            # intro window stayed visible on top of Atom, with its timers live.
            intro.stop()
            intro.deleteLater()
            atom = AtomApp(sys.argv, qt_app=qt_app)
            atom.start()

        intro.all_done.connect(on_intro_done)
        # Safety timeout: start Atom even if intro hangs (4 min for long videos)
        QTimer.singleShot(240000, on_intro_error)
        intro.start()
    else:
        log.info("Atom boot: direct (intro disabled)")
        atom = AtomApp(sys.argv, qt_app=qt_app)
        atom.start()

    sys.exit(qt_app.exec())


if __name__ == "__main__":
    sys.exit(main())
