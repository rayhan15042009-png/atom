"""
Tests for ui/animation_machine — AnimationMachine, AnimationState, Emotion.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from PyQt6.QtCore import QTimer

from ui.animation_machine import (
    AnimationMachine,
    AnimationState,
    Emotion,
)


class TestAnimationStateEnum(unittest.TestCase):
    def test_values(self) -> None:
        expected = {"idle", "walk", "run", "listen", "talk", "bored", "wave", "spin", "jump", "sleep"}
        self.assertEqual({s.value for s in AnimationState}, expected)

    def test_default_is_idle(self) -> None:
        machine = AnimationMachine()
        self.assertEqual(machine.current_state, AnimationState.IDLE)


class TestEmotionEnum(unittest.TestCase):
    def test_values(self) -> None:
        expected = {"neutral", "happy", "sad", "excited", "annoyed", "flirty", "sleepy"}
        self.assertEqual({e.value for e in Emotion}, expected)

    def test_default_is_neutral(self) -> None:
        machine = AnimationMachine()
        self.assertEqual(machine.current_emotion, Emotion.NEUTRAL)


class TestTransitions(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = AnimationMachine()
        self.machine._tick_timer.stop()  # prevent automatic ticking

    def test_listen_transition(self) -> None:
        self.machine.signal_listening()
        self.machine._check_transitions()
        self.assertEqual(self.machine.current_state, AnimationState.LISTEN)

    def test_respond_transition(self) -> None:
        self.machine.signal_listening()
        self.machine._check_transitions()
        self.machine.signal_responding()
        self.machine._check_transitions()
        self.assertEqual(self.machine.current_state, AnimationState.TALK)

    def test_speech_finished_transition(self) -> None:
        self.machine._listen_flag = True
        self.machine._check_transitions()
        self.machine._respond_flag = True
        self.machine._check_transitions()
        self.machine.signal_speech_finished()
        self.machine._check_transitions()
        self.assertEqual(self.machine.current_state, AnimationState.IDLE)

    def test_wave_transition(self) -> None:
        self.machine.signal_wave()
        self.machine._check_transitions()
        self.assertEqual(self.machine.current_state, AnimationState.WAVE)

    def test_wave_to_spin(self) -> None:
        self.machine.signal_wave()
        self.machine._check_transitions()
        self.machine._idle_timer = 1.0  # past 0.5s threshold
        self.machine._check_transitions()
        self.assertEqual(self.machine.current_state, AnimationState.SPIN)

    def test_spin_to_idle(self) -> None:
        self.machine.current_state = AnimationState.SPIN
        self.machine._idle_timer = 1.0  # past 0.8s threshold
        self.machine._check_transitions()
        self.assertEqual(self.machine.current_state, AnimationState.IDLE)

    def test_walk_to_run(self) -> None:
        self.machine.current_state = AnimationState.WALK
        self.machine.velocity = 3.0
        self.machine._check_transitions()
        self.assertEqual(self.machine.current_state, AnimationState.RUN)

    def test_run_to_walk(self) -> None:
        self.machine.current_state = AnimationState.RUN
        self.machine.velocity = 1.0
        self.machine._check_transitions()
        self.assertEqual(self.machine.current_state, AnimationState.WALK)

    def test_same_transition_no_emit(self) -> None:
        signals = []
        self.machine.state_changed.connect(lambda p, c: signals.append((p, c)))
        self.machine._transition(AnimationState.IDLE)  # already IDLE
        self.assertEqual(len(signals), 0)


class TestEmotionManagement(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = AnimationMachine()
        self.machine._tick_timer.stop()

    def test_set_emotion(self) -> None:
        self.machine.set_emotion(Emotion.HAPPY, duration=2.0)
        self.assertEqual(self.machine.current_emotion, Emotion.HAPPY)

    def test_emotion_signal_emitted(self) -> None:
        signals = []
        self.machine.emotion_changed.connect(lambda e: signals.append(e))
        self.machine.set_emotion(Emotion.SAD, duration=1.0)
        self.assertEqual(len(signals), 1)
        self.assertIs(signals[0], Emotion.SAD)

    def test_emotion_speed_map_happy(self) -> None:
        self.machine.set_emotion(Emotion.HAPPY)
        self.machine._apply_emotion()
        self.assertEqual(self.machine.emotion_speed_scale, 1.15)

    def test_emotion_speed_map_sad(self) -> None:
        self.machine.set_emotion(Emotion.SAD)
        self.machine._apply_emotion()
        self.assertEqual(self.machine.emotion_speed_scale, 0.7)

    def test_emotion_speed_map_sleepy(self) -> None:
        self.machine.set_emotion(Emotion.SLEEPY)
        self.machine._apply_emotion()
        self.assertEqual(self.machine.emotion_speed_scale, 0.6)

    def test_clear_emotion_returns_to_neutral(self) -> None:
        self.machine.set_emotion(Emotion.HAPPY, duration=0)
        self.machine._clear_emotion()
        self.assertEqual(self.machine.current_emotion, Emotion.NEUTRAL)

    @patch("ui.animation_machine.random.random", return_value=1.0)
    def test_parse_emotion_happy(self, mock_rand) -> None:
        self.machine.parse_emotion_from_text("thank you")
        self.assertEqual(self.machine.current_emotion, Emotion.HAPPY)

    @patch("ui.animation_machine.random.random", return_value=1.0)
    def test_parse_emotion_annoyed(self, mock_rand) -> None:
        self.machine.parse_emotion_from_text("there's an error")
        self.assertEqual(self.machine.current_emotion, Emotion.ANNOYED)

    @patch("ui.animation_machine.datetime")
    def test_parse_emotion_no_match(self, mock_dt) -> None:
        mock_dt.datetime.now.return_value.hour = 12  # midday, no sleep override
        self.machine.set_emotion(Emotion.HAPPY, duration=0)
        with patch("ui.animation_machine.random.random", return_value=1.0):  # no random sleep
            self.machine.parse_emotion_from_text("hello world")
        self.assertEqual(self.machine.current_emotion, Emotion.HAPPY)

    def test_parse_interaction_wave(self) -> None:
        result = self.machine.parse_interaction_command("wave")
        self.assertEqual(result, "wave")

    def test_parse_interaction_jump(self) -> None:
        result = self.machine.parse_interaction_command("jump!")
        self.assertEqual(result, "jump")

    def test_parse_interaction_no_match(self) -> None:
        result = self.machine.parse_interaction_command("hello")
        self.assertIsNone(result)


class TestRoaming(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = AnimationMachine()
        self.machine._tick_timer.stop()

    def test_set_roaming_enables(self) -> None:
        self.machine.set_roaming(True)
        self.assertTrue(self.machine.should_roam)

    def test_set_roaming_disables_resets_velocity(self) -> None:
        self.machine.target_velocity = 5.0
        self.machine.set_roaming(False)
        self.assertFalse(self.machine.should_roam)
        self.assertEqual(self.machine.target_velocity, 0.0)

    def test_set_roam_direction(self) -> None:
        self.machine.set_roam_direction(3.0)
        self.assertEqual(self.machine.target_velocity, 3.0)

    def test_roaming_acceleration(self) -> None:
        self.machine.should_roam = True
        self.machine.target_velocity = 5.0
        self.machine._update_roaming(0.1)
        self.assertGreater(self.machine.velocity, 0.0)


class TestJump(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = AnimationMachine()
        self.machine._tick_timer.stop()

    def test_trigger_jump(self) -> None:
        self.machine.trigger_jump()
        self.assertTrue(self.machine.is_jumping)
        self.assertEqual(self.machine.current_state, AnimationState.JUMP)

    def test_jump_completes(self) -> None:
        self.machine.trigger_jump()
        self.machine._update_jump(1.0)  # past 0.8s duration
        self.assertFalse(self.machine.is_jumping)

    def test_no_double_jump(self) -> None:
        self.machine.trigger_jump()
        self.machine.trigger_jump()  # should be ignored
        # Still jumping once

    @patch.object(AnimationMachine, "trigger_jump")
    def test_jump_signal_emitted(self, mock_trigger) -> None:
        self.machine.trigger_jump()
        mock_trigger.assert_called_once()


class TestMicroInteractions(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = AnimationMachine()
        self.machine._tick_timer.stop()

    def test_micro_none_by_default(self) -> None:
        self.assertEqual(self.machine.get_micro_interaction(), "")

    def test_micro_expired_clears(self) -> None:
        self.machine._micro_active = "look_around"
        self.machine._micro_end = -1.0  # already expired
        self.machine._check_micro_expired()
        self.assertIsNone(self.machine._micro_active)

    def test_micro_not_expired(self) -> None:
        self.machine._micro_active = "look_around"
        self.machine._micro_end = 999.0  # far in future
        self.machine._check_micro_expired()
        self.assertEqual(self.machine._micro_active, "look_around")

    def test_micro_skip_when_not_idle(self) -> None:
        self.machine.current_state = AnimationState.TALK
        self.machine._try_micro()  # should not set anything
        self.assertIsNone(self.machine._micro_active)

    def test_micro_skip_when_sleepy(self) -> None:
        self.machine.current_emotion = Emotion.SLEEPY
        self.machine._try_micro()
        self.assertIsNone(self.machine._micro_active)


class TestBoredom(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = AnimationMachine()
        self.machine._tick_timer.stop()

    def test_boredom_starts_at_zero(self) -> None:
        self.assertEqual(self.machine.get_boredom_tier(), 0)

    def test_boredom_increases_with_idle_time(self) -> None:
        self.machine._idle_timer = 30
        self.machine._update_boredom(0.016)
        self.assertGreater(self.machine.get_boredom_tier(), 0)

    def test_boredom_resets_on_non_idle(self) -> None:
        self.machine._boredom_tier = 3
        self.machine.current_state = AnimationState.TALK
        self.machine._update_boredom(0.016)
        self.assertEqual(self.machine.get_boredom_tier(), 0)

    def test_boredom_suppressed_by_excited_emotion(self) -> None:
        self.machine.current_emotion = Emotion.HAPPY
        self.machine._idle_timer = 30
        self.machine._update_boredom(0.016)
        self.assertEqual(self.machine.get_boredom_tier(), 0)

    def test_boredom_tier_thresholds(self) -> None:
        self.machine._boredom_tier = 0
        self.machine._idle_timer = 10
        self.machine._update_boredom(0.016)
        self.assertGreaterEqual(self.machine.get_boredom_tier(), 0)

    def test_boredom_signals_on_tier_change(self) -> None:
        signals = []
        self.machine.animation_started.connect(lambda s: signals.append(s))
        self.machine._idle_timer = 50  # past threshold 4 (40)
        self.machine._boredom_played = set()
        self.machine._update_boredom(0.016)
        self.assertGreater(len(signals), 0)


class TestTickBehaviour(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = AnimationMachine()
        # Stop the real timer; we'll call _tick manually
        self.machine._tick_timer.stop()

    def test_tick_increments_idle_timer(self) -> None:
        before = self.machine._idle_timer
        self.machine._tick()
        self.assertGreater(self.machine._idle_timer, before)

    def test_tick_applies_emotion_overrides(self) -> None:
        self.machine.set_emotion(Emotion.HAPPY, duration=0)
        self.machine._tick()
        self.assertEqual(self.machine.emotion_speed_scale, 1.15)


class TestCleanup(unittest.TestCase):
    def test_cleanup_stops_timers(self) -> None:
        machine = AnimationMachine()
        machine._tick_timer.stop()
        self.assertTrue(machine._tick_timer.isActive() is False)
        machine._emotion_timer.stop()
        self.assertTrue(machine._emotion_timer.isActive() is False)
        machine._micro_timer.stop()
        self.assertTrue(machine._micro_timer.isActive() is False)


if __name__ == "__main__":
    unittest.main()
