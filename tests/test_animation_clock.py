"""
Regression tests for the animation machine's clock.

The defect (found by audit, measured empirically): ``_tick()`` advanced its clock
by a hard-coded ``0.016 * emotion_speed_scale`` seconds while ``_tick_timer``
fires every **80 ms**. The machine therefore ran at ~17% of real time — measured
at 0.336 simulated seconds per 2.0 real seconds — and every duration written in
the class (blink 3-6 s, boredom 10/25/40/55 s, micro cooldown 4 s, direction
change 2-4 s, jump 0.8 s) took roughly six times longer than intended.

A second defect: ``_tick`` multiplied dt by ``emotion_speed_scale`` and then
multiplied again when calling ``_update_roaming`` — which already scales its own
accel/decel/walk-speed by the same factor. Emotion was applied three times over
to roaming.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

import ui.animation_machine as anim  # noqa: E402
from ui.animation_machine import AnimationMachine, Emotion  # noqa: E402


class _MachineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = AnimationMachine()
        self.machine._tick_timer.stop()
        self.addCleanup(self.machine.cleanup)

    def tick_after(self, elapsed: float) -> None:
        """Run one tick pretending `elapsed` seconds passed since the last one."""
        base = self.machine._last_tick_time
        with patch.object(anim.time, "monotonic", return_value=base + elapsed):
            self.machine._tick()


# ═══════════════════════════════════════════════════════════════════════════════
# Clock
# ═══════════════════════════════════════════════════════════════════════════════

class TestAnimationClock(_MachineTest):
    def test_clock_advances_by_real_elapsed_time(self) -> None:
        before = self.machine._idle_timer
        self.tick_after(0.25)
        self.assertAlmostEqual(self.machine._idle_timer - before, 0.25, places=3)

    def test_nominal_dt_matches_the_timer_interval(self) -> None:
        """The constant and the QTimer interval must not drift apart again."""
        self.assertAlmostEqual(
            self.machine._TICK_INTERVAL_SECONDS,
            self.machine._tick_timer.interval() / 1000.0,
            places=3,
        )

    def test_zero_elapsed_falls_back_to_one_interval(self) -> None:
        before = self.machine._idle_timer
        self.tick_after(0.0)
        self.assertAlmostEqual(
            self.machine._idle_timer - before,
            self.machine._TICK_INTERVAL_SECONDS,
            places=3,
        )

    def test_long_stall_is_clamped(self) -> None:
        """A suspended laptop must not dump an hour into a single tick."""
        before = self.machine._idle_timer
        self.tick_after(3600.0)
        self.assertAlmostEqual(
            self.machine._idle_timer - before, self.machine._MAX_DT, places=3
        )

    def test_the_old_hard_coded_dt_is_gone(self) -> None:
        """0.016 s was the old dt regardless of the 80 ms interval."""
        before = self.machine._idle_timer
        self.tick_after(0.080)
        advanced = self.machine._idle_timer - before
        self.assertAlmostEqual(advanced, 0.080, places=3)
        self.assertNotAlmostEqual(advanced, 0.016, places=3)

    def test_boredom_uses_real_seconds(self) -> None:
        """10 s of real idle time must cross the first boredom threshold."""
        ticks = 0
        while self.machine.get_boredom_tier() == 0 and ticks < 200:
            self.tick_after(0.080)
            ticks += 1
        self.assertLess(ticks * 0.080, 15.0, "boredom tier 1 should arrive near 10 s")
        self.assertGreaterEqual(self.machine.get_boredom_tier(), 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Emotion scaling applied exactly once
# ═══════════════════════════════════════════════════════════════════════════════

class TestRoamingEmotionScaling(_MachineTest):
    def setUp(self) -> None:
        super().setUp()
        self.machine.should_roam = True

    def test_roaming_receives_raw_dt_with_neutral_emotion(self) -> None:
        base = self.machine._last_tick_time
        with patch.object(self.machine, "_update_roaming") as roam, patch.object(
            anim.time, "monotonic", return_value=base + 0.080
        ):
            self.machine._tick()
        self.assertEqual(roam.call_count, 1)
        self.assertAlmostEqual(roam.call_args[0][0], 0.080, places=3)

    def test_roaming_receives_raw_dt_when_excited(self) -> None:
        """emotion_speed_scale is 1.5 when EXCITED; dt must not be pre-scaled."""
        self.machine.set_emotion(Emotion.EXCITED, duration=0)
        self.tick_after(0.080)  # applies the emotion override
        self.assertAlmostEqual(self.machine.emotion_speed_scale, 1.5, places=2)

        base = self.machine._last_tick_time
        with patch.object(self.machine, "_update_roaming") as roam, patch.object(
            anim.time, "monotonic", return_value=base + 0.080
        ):
            self.machine._tick()
        self.assertAlmostEqual(
            roam.call_args[0][0],
            0.080,
            places=3,
            msg="dt must not be pre-multiplied by emotion_speed_scale",
        )

    def test_idle_clock_is_not_emotion_scaled(self) -> None:
        """An excited Atom should not get bored faster in wall-clock terms."""
        self.machine.set_emotion(Emotion.EXCITED, duration=0)
        self.tick_after(0.080)  # apply override
        before = self.machine._idle_timer
        self.tick_after(0.080)
        self.assertAlmostEqual(self.machine._idle_timer - before, 0.080, places=3)


if __name__ == "__main__":
    unittest.main()
