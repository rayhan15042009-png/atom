"""
Regression tests for intro-sequence lifecycle defects found by auditing the
intro + 3D render paths (static AST analysis + headless construction).

Each test maps to a defect that was actually shipping:

1. ``RaOneParticle`` — a pure ``__slots__`` data class — had a stray
   ``_apply_window_flags`` method pasted into it by an automated patch. It could
   never run: the class has no ``_tick`` slot and no Qt window methods, so
   calling it raises ``AttributeError``. Nothing called it.
2. ``AtomDanceScreen._check_video_stalled()`` was never referenced by anything
   while ``_video_health_timer`` drove ``_check_video_health()``. Its logic was
   also wrong: it fell back to the sprite whenever ``playbackState != Playing``,
   which is exactly the state during the intentional pre-assembly pause.
3. ``_on_video_skip_tick()`` was orphaned — the 1 s video timer was connected to
   a bare ``lambda: self.update()`` instead.
4. ``closeEvent()`` stopped neither ``_video_skip_timer`` (1 s) nor
   ``_video_load_timer`` (10 s single-shot), so both outlived the screen and
   repainted / ran a fallback on a hidden widget.
5. ``AtomIntroSequence.stop()`` never stopped the 33 ms sprite timer, and read
   ``self._current_screen``, which ``__init__`` never initialised.
6. ``AtomDanceScreen._on_video_status()`` dereferenced ``_video_load_timer``
   unconditionally, but ``_cleanup_video()`` sets it to ``None`` *before* the
   player is stopped. Qt delivers ``mediaStatusChanged`` asynchronously, so
   skipping the dance screen printed ``AttributeError: 'NoneType' object has no
   attribute 'stop'`` from ``ui/intro.py`` into the console on every skipped
   intro. The handler is now a no-op once the player is gone, the load timer is
   stopped through a None-safe helper, and ``_cleanup_video()`` disconnects the
   player's signals before releasing it.
7. Every screen scheduled work with an ownerless ``QTimer.singleShot``, and the
   sequence closes each screen as it moves on (``close()`` only hides a dialog,
   so the object stays alive). The deferred callbacks therefore still ran: the
   dance screen's 60 s safety timer outlived the intro, and a screen's completion
   signal could fire a second time and re-enter the transition. They are now
   parented single-shot timers in ``_DeferredCalls`` and cancelled by
   ``closeEvent``.
8. A click during the video did not reach the screen: ``QVideoWidget`` covers it
   edge to edge and the cross-fade overlay covers it for the first frames. Both
   are now ``WA_TransparentForMouseEvents``, so the first click after the skip
   delay always skips.
"""

from __future__ import annotations

import ast
import re
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget

_APP = QApplication.instance() or QApplication([])

import ui.intro as intro  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
INTRO_PATH = ROOT / "ui" / "intro.py"
INTRO_SRC = INTRO_PATH.read_text(encoding="utf-8")


def _class_node(name: str) -> ast.ClassDef:
    for node in ast.parse(INTRO_SRC).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"class {name} not found in ui/intro.py")


def _word_count(haystack: str, needle: str) -> int:
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(needle) + r"(?![A-Za-z0-9_])"
    return len(re.findall(pattern, haystack))


# ═══════════════════════════════════════════════════════════════════════════════
# 1. RaOneParticle must stay a pure data class
# ═══════════════════════════════════════════════════════════════════════════════

class TestRaOneParticleIsADataClass(unittest.TestCase):
    def test_source_defines_only_init(self) -> None:
        methods = [
            n.name
            for n in _class_node("RaOneParticle").body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        self.assertEqual(
            methods,
            ["__init__"],
            "RaOneParticle is a __slots__ data holder; it must not grow methods "
            "(a stray _apply_window_flags was pasted into it once already)",
        )

    def test_runtime_exposes_no_private_methods(self) -> None:
        private = [
            m
            for m in dir(intro.RaOneParticle)
            if m.startswith("_") and not m.startswith("__")
        ]
        self.assertEqual(private, [])

    def test_slots_do_not_gain_ui_attributes(self) -> None:
        self.assertNotIn("_tick", intro.RaOneParticle.__slots__)


# ═══════════════════════════════════════════════════════════════════════════════
# 2 + 3. Video handler wiring
# ═══════════════════════════════════════════════════════════════════════════════

class TestVideoHandlerWiring(unittest.TestCase):
    def test_unwired_stalled_checker_is_gone(self) -> None:
        self.assertNotIn("_check_video_stalled", INTRO_SRC)

    def test_health_timer_is_wired_to_the_health_check(self) -> None:
        self.assertIn(
            "self._video_health_timer.timeout.connect(self._check_video_health)",
            INTRO_SRC,
        )

    def test_skip_timer_is_wired_to_its_handler(self) -> None:
        """It used to be `connect(lambda: self.update())`, orphaning the method."""
        self.assertIn(
            "self._video_skip_timer.timeout.connect(self._on_video_skip_tick)",
            INTRO_SRC,
        )

    def test_no_orphaned_private_methods_in_intro(self) -> None:
        """Generic guard so a future patch cannot leave a handler unreferenced."""
        corpus = "\n".join(
            p.read_text(encoding="utf-8", errors="replace")
            for p in (
                ROOT / "ui" / "intro.py",
                ROOT / "ui" / "intro_utils.py",
                ROOT / "atom.py",
            )
            if p.exists()
        )
        orphans = []
        for node in ast.walk(ast.parse(INTRO_SRC)):
            if not isinstance(node, ast.ClassDef):
                continue
            for fn in node.body:
                if not isinstance(fn, ast.FunctionDef):
                    continue
                name = fn.name
                if name.startswith("__") or not name.startswith("_"):
                    continue
                hits = _word_count(corpus, name)
                definitions = _word_count(corpus, f"def {name}")
                if hits - definitions <= 0:
                    orphans.append(f"{node.name}.{name}")
        self.assertEqual(orphans, [], f"orphaned private methods: {orphans}")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. AtomDanceScreen teardown
# ═══════════════════════════════════════════════════════════════════════════════

class TestDanceScreenTeardown(unittest.TestCase):
    def _make_screen(self) -> "intro.AtomDanceScreen":
        # Patch out media loading and window showing: neither is under test and
        # both are slow / would flash a full-screen window.
        with patch.object(intro.AtomDanceScreen, "_try_init_video", lambda self: None), \
             patch.object(intro.AtomDanceScreen, "_apply_window_flags", lambda self: None):
            screen = intro.AtomDanceScreen(config=None)
        self.addCleanup(screen.closeEvent, QCloseEvent())
        return screen

    def test_close_event_stops_the_skip_timer(self) -> None:
        screen = self._make_screen()
        timer = QTimer()
        timer.start(1000)
        screen._video_skip_timer = timer

        screen.closeEvent(QCloseEvent())

        self.assertFalse(timer.isActive())
        self.assertIsNone(screen._video_skip_timer)

    def test_close_event_stops_the_video_load_timer(self) -> None:
        screen = self._make_screen()
        self.assertTrue(screen._video_load_timer.isActive(), "precondition")

        screen.closeEvent(QCloseEvent())

        self.assertIsNone(screen._video_load_timer)

    def test_close_event_stops_the_tick_timer(self) -> None:
        screen = self._make_screen()
        screen.closeEvent(QCloseEvent())
        self.assertFalse(screen._tick.isActive())

    def test_stop_video_skip_timer_is_idempotent(self) -> None:
        screen = self._make_screen()
        screen.closeEvent(QCloseEvent())
        screen._stop_video_skip_timer()  # must not raise on the second call
        self.assertIsNone(screen._video_skip_timer)

    def test_stop_video_skip_timer_survives_a_missing_attribute(self) -> None:
        """QObjects built with __new__ have no __dict__ entries — no AttributeError."""
        screen = intro.AtomDanceScreen.__new__(intro.AtomDanceScreen)
        screen._stop_video_skip_timer()
        self.assertIsNone(screen._video_skip_timer)

    def test_stop_video_load_timer_is_safe_once_cleaned_up(self) -> None:
        screen = self._make_screen()
        screen.closeEvent(QCloseEvent())
        screen._stop_video_load_timer()  # was `self._video_load_timer.stop()`
        self.assertIsNone(screen._video_load_timer)

    def test_stop_video_load_timer_survives_a_missing_attribute(self) -> None:
        screen = intro.AtomDanceScreen.__new__(intro.AtomDanceScreen)
        screen._stop_video_load_timer()
        self.assertIsNone(screen.__dict__.get('_video_load_timer'))


# ═══════════════════════════════════════════════════════════════════════════════
# 6. A late media status must not touch a torn-down dance screen
# ═══════════════════════════════════════════════════════════════════════════════

class TestLateVideoStatusAfterCleanup(unittest.TestCase):
    """The skipped-intro crash.

    ``_complete()`` -> ``_cleanup_video()`` releases ``_video_load_timer``; the
    player then emits ``mediaStatusChanged`` once more and the handler blew up on
    the released timer. Reproduced here without any real playback.
    """

    def _make_screen(self) -> "intro.AtomDanceScreen":
        with patch.object(intro.AtomDanceScreen, "_try_init_video", lambda self: None), \
             patch.object(intro.AtomDanceScreen, "_apply_window_flags", lambda self: None):
            screen = intro.AtomDanceScreen(config=None)
        self.addCleanup(screen.closeEvent, QCloseEvent())
        return screen

    def _status(self, name: str):
        from PyQt6.QtMultimedia import QMediaPlayer
        return getattr(QMediaPlayer.MediaStatus, name)

    def test_status_after_cleanup_is_ignored(self) -> None:
        """The exact crash: handler runs after the timer was released."""
        screen = self._make_screen()
        screen._complete()
        self.assertIsNone(screen._video_load_timer, "precondition")
        for name in ("LoadedMedia", "BufferingMedia", "EndOfMedia", "NoMedia"):
            screen._on_video_status(self._status(name))  # must not raise

    def test_cleanup_disconnects_the_player_signals(self) -> None:
        """Emitting after teardown must not reach the handler at all."""
        from PyQt6.QtMultimedia import QMediaPlayer
        screen = self._make_screen()
        player = QMediaPlayer(screen)
        player.mediaStatusChanged.connect(screen._on_video_status)
        player.errorOccurred.connect(screen._on_video_error)
        screen._video_player = player

        with patch.object(intro.AtomDanceScreen, "_init_sprite_fallback", lambda self: None):
            screen._cleanup_video()

        # The handler logs "Dance video: media loaded and ready" on this status;
        # with no handler attached nothing may be logged.
        with self.assertNoLogs("atom.intro", level="INFO"):
            player.mediaStatusChanged.emit(QMediaPlayer.MediaStatus.LoadedMedia)

    def test_cleanup_is_idempotent_with_a_late_status_in_between(self) -> None:
        screen = self._make_screen()
        screen._cleanup_video()
        screen._on_video_status(self._status("LoadedMedia"))
        screen._cleanup_video()  # must not raise either
        self.assertIsNone(screen._video_player)

    def test_the_handler_never_dereferences_the_timer_directly(self) -> None:
        """Source guard: use the None-safe helper, not the raw attribute."""
        self.assertNotIn("self._video_load_timer.stop()", INTRO_SRC)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Nothing may be left scheduled after a screen closes
# ═══════════════════════════════════════════════════════════════════════════════

#: The four screens the sequence chains, with the signal each emits when it is
#: finished with.
SCREENS: tuple[tuple[str, str], ...] = (
    ("AtomBootScreen", "boot_complete"),
    ("AtomOptimusScreen", "splash_complete"),
    ("AtomWelcomeScreen", "welcome_complete"),
    ("AtomDanceScreen", "startup_complete"),
)


def _make_screen(name: str) -> "intro.AtomDanceScreen":
    """Build a screen without showing it or touching the media stack.

    Only the methods the screen actually has are patched — the four screens have
    different (and differently named) setup helpers.
    """
    cls = getattr(intro, name)
    patched = [("_apply_window_flags", lambda self: None)]
    for method in ("_try_init_video", "_try_init_intro_video"):
        if hasattr(cls, method):
            patched.append((method, lambda self: None))
    if hasattr(cls, "_init_particles"):
        patched.append(("_init_particles",
                        lambda self: setattr(self, "_particles", [])))

    with ExitStack() as stack:
        for method, replacement in patched:
            stack.enter_context(patch.object(cls, method, replacement))
        screen = cls(config=None)
    screen.resize(800, 600)
    return screen


class TestDeferredCalls(unittest.TestCase):
    """``_DeferredCalls`` is what replaced the ownerless ``QTimer.singleShot``."""

    def setUp(self) -> None:
        self.owner = QWidget()
        self.addCleanup(self.owner.close)
        self.bag = intro._DeferredCalls(self.owner)

    def test_a_pending_call_runs(self) -> None:
        ran: list = []
        self.bag.start(10, lambda: ran.append(1))
        QTest.qWait(120)
        self.assertEqual(ran, [1])

    def test_a_finished_call_is_forgotten(self) -> None:
        self.bag.start(10, lambda: None)
        self.assertEqual(self.bag.pending(), 1, "precondition")
        QTest.qWait(120)
        self.assertEqual(self.bag.pending(), 0)

    def test_stop_all_cancels_pending_calls(self) -> None:
        ran: list = []
        self.bag.start(60, lambda: ran.append(1))
        self.bag.stop_all()
        self.assertEqual(self.bag.pending(), 0)
        QTest.qWait(200)
        self.assertEqual(ran, [], "a cancelled callback still ran")

    def test_stop_all_is_safe_when_nothing_is_pending(self) -> None:
        self.bag.stop_all()
        self.bag.stop_all()
        self.assertEqual(self.bag.pending(), 0)

    def test_timers_are_parented_so_they_die_with_the_owner(self) -> None:
        self.bag.start(60_000, lambda: None)
        timer = self.owner.findChild(QTimer)
        self.assertIsNotNone(timer, "the timer must be a child of its owner")


class TestNoTimerOutlivesItsScreen(unittest.TestCase):
    """Closing a screen must leave nothing scheduled behind it."""

    def test_each_screen_cancels_its_pending_calls_on_close(self) -> None:
        for name, _signal in SCREENS:
            with self.subTest(screen=name):
                screen = _make_screen(name)
                ran: list = []
                screen._deferred.start(60, lambda: ran.append(1))
                self.assertGreater(screen._deferred.pending(), 0, "precondition")

                screen.closeEvent(QCloseEvent())

                self.assertEqual(screen._deferred.pending(), 0,
                                 f"{name} closed with callbacks still scheduled")
                QTest.qWait(200)
                self.assertEqual(ran, [], f"{name} ran a callback after closing")

class TestNoOwnerlessSingleShotRemains(unittest.TestCase):
    def test_intro_uses_the_deferred_bag(self) -> None:
        """``QTimer.singleShot(ms, bound_method)`` has no owner: it survives the
        screen. No *call* to it may come back (the docstring explains why, so the
        check looks at the syntax tree rather than at the text)."""
        offenders: list[int] = []
        for node in ast.walk(ast.parse(INTRO_SRC)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "singleShot"
                    and isinstance(func.value, ast.Name) and func.value.id == "QTimer"):
                offenders.append(node.lineno)
        self.assertEqual(offenders, [],
                         f"ownerless single-shot timers came back at {offenders}")
        self.assertIn("self._deferred.start(", INTRO_SRC)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. One click skips — and nothing on top of the screen eats it
# ═══════════════════════════════════════════════════════════════════════════════

class TestOneClickSkips(unittest.TestCase):
    """After the skip delay a single click must finish the screen, once."""

    def test_a_single_click_completes_each_screen_exactly_once(self) -> None:
        for name, signal_name in SCREENS:
            with self.subTest(screen=name):
                screen = _make_screen(name)
                fired: list = []
                getattr(screen, signal_name).connect(lambda *a: fired.append(1))
                screen._skip_allowed = True  # the delay has elapsed

                QTest.mouseClick(screen, Qt.MouseButton.LeftButton,
                                 pos=screen.rect().center())
                QTest.qWait(1600)

                self.assertEqual(len(fired), 1,
                                 f"one click made {name} complete {len(fired)} times")

    def test_a_screen_closed_mid_skip_does_not_signal_afterwards(self) -> None:
        """The sequence closes each screen as it advances. A completion that was
        already scheduled used to fire anyway and re-enter the transition, which
        is how a second full-screen intro window could appear over the first.
        """
        for name, signal_name in SCREENS:
            with self.subTest(screen=name):
                screen = _make_screen(name)
                fired: list = []
                getattr(screen, signal_name).connect(lambda *a: fired.append(1))
                screen._skip_allowed = True

                QTest.mouseClick(screen, Qt.MouseButton.LeftButton,
                                 pos=screen.rect().center())
                screen.closeEvent(QCloseEvent())      # the sequence moves on
                QTest.qWait(1600)

                self.assertEqual(fired, [],
                                 f"{name} signalled after it was closed")

    def test_a_click_inside_the_skip_delay_is_ignored_by_design(self) -> None:
        """The delay is what stops the double-click that launched Atom from
        skipping the intro; a click in that window is dropped on purpose."""
        screen = _make_screen("AtomDanceScreen")
        self.assertFalse(screen._skip_allowed, "precondition")

        QTest.mouseClick(screen, Qt.MouseButton.LeftButton,
                         pos=screen.rect().center())
        QTest.qWait(100)

        self.assertFalse(screen._dance_complete)

    def test_the_video_widget_lets_clicks_through(self) -> None:
        """It covers the screen edge to edge, so a click must be transparent."""
        for name in ("AtomWelcomeScreen", "AtomDanceScreen"):
            with self.subTest(screen=name):
                cls = getattr(intro, name)
                with patch.object(cls, "_apply_window_flags", lambda self: None):
                    screen = cls(config=None)      # real video init
                self.addCleanup(screen.closeEvent, QCloseEvent())
                if screen._video_widget is None:
                    self.skipTest("no video backend available")
                self.assertTrue(
                    screen._video_widget.testAttribute(
                        Qt.WidgetAttribute.WA_TransparentForMouseEvents),
                    f"{name}'s video widget would swallow the skip click",
                )

    def test_the_cross_fade_overlay_lets_clicks_through(self) -> None:
        """It is raised above the screen for the first frames of a transition."""
        overlay = intro._FadeOverlay()
        self.addCleanup(overlay.close)
        self.assertTrue(
            overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 5. AtomIntroSequence teardown
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntroSequenceTeardown(unittest.TestCase):
    def _make_sequence(self) -> "intro.AtomIntroSequence":
        seq = intro.AtomIntroSequence(SimpleNamespace(character="atom"))
        self.addCleanup(seq.stop)
        return seq

    def test_current_screen_is_initialised_so_stop_is_safe(self) -> None:
        seq = self._make_sequence()
        self.assertIsNone(seq._current_screen)
        seq.stop()  # must not raise AttributeError before start()

    def test_stop_cleans_up_the_sprite_timer(self) -> None:
        seq = self._make_sequence()
        seq._sprite_timer = QTimer()
        seq._sprite_timer.start(33)

        seq.stop()

        self.assertIsNone(seq._sprite_timer)

    def test_stop_is_idempotent(self) -> None:
        seq = self._make_sequence()
        seq.stop()
        seq.stop()
        self.assertIsNone(seq._sprite_timer)

    def test_cleanup_sprite_stops_a_live_timer(self) -> None:
        seq = self._make_sequence()
        timer = QTimer()
        timer.start(33)
        seq._sprite_timer = timer

        seq._cleanup_sprite()

        self.assertFalse(timer.isActive())


if __name__ == "__main__":
    unittest.main()
