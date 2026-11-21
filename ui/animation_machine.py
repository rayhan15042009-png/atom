"""
Animation machine — state transitions, emotions, micro-interactions, boredom tiers.

Part of the 2D rendering layer. Ticked by the main app timer.
Moved from core/animation_machine.py to ui/ for Step 5 consolidation.
"""

from __future__ import annotations

import datetime
import enum
import math
import random
import time
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


class AnimationState(enum.Enum):
    IDLE = "idle"
    WALK = "walk"
    RUN = "run"
    LISTEN = "listen"
    TALK = "talk"
    BORED = "bored"
    WAVE = "wave"
    SPIN = "spin"
    JUMP = "jump"
    SLEEP = "sleep"


class Emotion(enum.Enum):
    NEUTRAL = "neutral"
    HAPPY = "happy"
    SAD = "sad"
    EXCITED = "excited"
    ANNOYED = "annoyed"
    FLIRTY = "flirty"
    SLEEPY = "sleepy"


class AnimationMachine(QObject):
    """Manages Atom's animation state machine, emotions, and idle behaviours.

    Signal flow:
      state_changed(prev, cur)  → overlay updates pose
      animation_started(name)   → overlay triggers micro/boredom animation
      animation_finished(name)  → overlay cleans up
      emotion_changed(emotion)  → overlay updates sprite mood speed
      jump_triggered()          → overlay applies jump physics
      bored_triggered()         → overlay may trigger a response
    """

    state_changed = pyqtSignal(AnimationState, AnimationState)
    animation_started = pyqtSignal(str)
    animation_finished = pyqtSignal(str)
    emotion_changed = pyqtSignal(Emotion)
    jump_triggered = pyqtSignal()
    bored_triggered = pyqtSignal()
    roam_position_changed = pyqtSignal(float, float)  # (roam_offset_x, velocity)

    _MICRO_INTERACTIONS: list[str] = [
        "look_around", "head_scratch", "stretch",
        "sigh", "tilt_curious", "blink_slow",
    ]
    _BOREDOM_THRESHOLDS: list[int] = [10, 25, 40, 55]

    # Clock. _TICK_INTERVAL_SECONDS must match _tick_timer's interval, and
    # _MAX_DT stops a suspend/stall from dumping an hour of simulated time into
    # one tick.
    _TICK_INTERVAL_SECONDS: float = 0.080
    _MAX_DT: float = 0.25

    def __init__(self) -> None:
        super().__init__()

        # ── State ──
        self.current_state: AnimationState = AnimationState.IDLE
        self.previous_state: AnimationState = AnimationState.IDLE
        self._idle_timer: float = 0.0
        self._last_action_time: float = 0.0
        self._last_tick_time: float = time.monotonic()

        # ── Emotion ──
        self.current_emotion: Emotion = Emotion.NEUTRAL
        self._last_emotion: Emotion = Emotion.NEUTRAL
        self._emotion_timer = QTimer(self)
        self._emotion_timer.setSingleShot(True)
        self._emotion_timer.timeout.connect(self._clear_emotion)

        # Emotion-driven visual overrides (read by overlay)
        self.emotion_speed_scale: float = 1.0
        self.emotion_bounce_amplitude: float = 0.0
        self.emotion_posture: list[float] = [0.0, 0.0, 0.0]

        # ── Flags ──
        self._listen_flag: bool = False
        self._respond_flag: bool = False
        self._speech_done_flag: bool = False
        self._wave_flag: bool = False

        # ── 2D roaming ──
        self.should_roam: bool = False
        self.velocity: float = 0.0
        self.target_velocity: float = 0.0
        self._move_time: float = 0.0
        self.x: float = 0.0
        self.home_x: float = 0.0         # Screen-anchored home position
        self.roam_box_width: float = 400.0  # Roaming bound width
        self.flip_sprite: bool = False    # Direction facing
        self.is_jumping: bool = False
        self._jump_time: float = 0.0

        # ── Blink ──
        self._blink_time: float = 0.0
        self._blink_interval: float = random.uniform(3.0, 6.0)
        self.is_blinking: bool = False

        # ── Micro-interactions ──
        self._micro_active: Optional[str] = None
        self._micro_end: float = 0.0
        self._last_micro: float = 0.0
        self._micro_cooldown: float = 4.0
        self._micro_timer = QTimer(self)
        self._micro_timer.timeout.connect(self._try_micro)
        self._micro_timer.start(12000)  # Micro-interactions every 12s (was 8s — saves CPU)

        # ── Boredom ──
        self._boredom_tier: int = 0
        self._boredom_played: set[int] = set()

        # ── Update timer ──
        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        # 12.5 FPS tick (was 50ms/20 FPS — saves CPU). Keep this in step with
        # _TICK_INTERVAL_SECONDS or _tick()'s clock drifts again.
        self._tick_timer.start(int(self._TICK_INTERVAL_SECONDS * 1000))

    # ══════════════════════════════════════════════════════════════════
    #  Tick
    # ══════════════════════════════════════════════════════════════════

    def _tick(self) -> None:
        # Advance on REAL elapsed time. This used to be a hard-coded
        # ``0.016 * emotion_speed_scale`` while _tick_timer fires every 80 ms, so
        # the machine's clock ran at ~17-20% of real time (measured: 0.336
        # simulated seconds per 2.0 real seconds) and every duration below —
        # blink 3-6 s, boredom 10/25/40/55 s, micro cooldown 4 s, direction
        # change 2-4 s — took roughly six times longer than written.
        now = time.monotonic()
        dt = now - self._last_tick_time
        self._last_tick_time = now
        if dt <= 0.0:
            dt = self._TICK_INTERVAL_SECONDS  # clock unchanged, or a synthetic tick
        dt = min(dt, self._MAX_DT)            # never lurch after a suspend/stall

        self._idle_timer += dt
        self._last_action_time += dt
        self._check_transitions()
        self._update_blink(dt)
        self._check_micro_expired()

        if self.current_emotion != self._last_emotion:
            self._apply_emotion()
            self._last_emotion = self.current_emotion

        # Run roaming update whenever roaming is enabled, regardless of current state.
        # This allows velocity to build up and triggers the WALK transition.
        if self.should_roam:
            # Pass the raw dt. _update_roaming applies emotion_speed_scale itself
            # (accel / decel / walk speed), so scaling dt here as well applied it
            # twice — three times over once dt carried the scale too.
            self._update_roaming(dt)

        self._update_jump(dt)
        self._update_boredom(dt)

    # ══════════════════════════════════════════════════════════════════
    #  Transitions
    # ══════════════════════════════════════════════════════════════════

    def _check_transitions(self) -> None:
        cur = self.current_state
        idle = self._idle_timer
        vel = abs(self.velocity)
        blocked = {
            AnimationState.LISTEN, AnimationState.TALK,
            AnimationState.WAVE, AnimationState.SPIN,
            AnimationState.JUMP, AnimationState.SLEEP,
        }

        if cur == AnimationState.WALK and vel > 2.0:
            self._transition(AnimationState.RUN)
            return
        if cur == AnimationState.RUN and vel <= 2.0:
            self._transition(AnimationState.WALK)
            return
        if cur in (AnimationState.WALK, AnimationState.RUN) and vel < 0.1 and idle > 3.0:
            self._transition(AnimationState.IDLE)
            return
        if self._listen_flag and cur not in blocked:
            self._transition(AnimationState.LISTEN)
            self._listen_flag = False
            return
        if cur == AnimationState.LISTEN and self._respond_flag:
            self._transition(AnimationState.TALK)
            self._respond_flag = False
            return
        if cur == AnimationState.TALK and self._speech_done_flag:
            self._transition(AnimationState.IDLE)
            self._speech_done_flag = False
            return
        if self.should_roam and vel > 0.1 and cur not in blocked:
            self._transition(AnimationState.WALK)
            return
        if self._wave_flag and cur not in {AnimationState.LISTEN, AnimationState.TALK, AnimationState.SLEEP}:
            self._transition(AnimationState.WAVE)
            self._wave_flag = False
            return
        if cur == AnimationState.WAVE and idle > 0.5:
            self._transition(AnimationState.SPIN)
            return
        if cur == AnimationState.SPIN and idle > 0.8:
            self._transition(AnimationState.IDLE)
            self.animation_finished.emit("spin")
            return
        if self.should_roam and cur == AnimationState.WALK and not self.is_jumping:
            if random.random() < 0.005:
                self.trigger_jump()

    def _transition(self, new_state: AnimationState) -> None:
        if new_state == self.current_state:
            return
        self.previous_state = self.current_state
        self.current_state = new_state
        self._idle_timer = 0.0
        self.state_changed.emit(self.previous_state, new_state)
        self.animation_started.emit(new_state.value)
        if new_state == AnimationState.BORED:
            self.bored_triggered.emit()
        self._boredom_tier = 0
        self._boredom_played.clear()

    # ══════════════════════════════════════════════════════════════════
    #  Signal helpers — called by the overlay
    # ══════════════════════════════════════════════════════════════════

    def signal_listening(self) -> None:
        self._listen_flag = True
        self._respond_flag = False
        self._speech_done_flag = False
        self._last_action_time = 0.0

    def signal_responding(self) -> None:
        self._respond_flag = True
        self._listen_flag = False

    def signal_speech_finished(self) -> None:
        self._speech_done_flag = True

    def signal_wave(self) -> None:
        self._wave_flag = True
        self._last_action_time = 0.0

    # ══════════════════════════════════════════════════════════════════
    #  Emotion
    # ══════════════════════════════════════════════════════════════════

    def set_emotion(self, emotion: Emotion, duration: float = 3.0) -> None:
        self.current_emotion = emotion
        self.emotion_changed.emit(emotion)
        if duration > 0:
            self._emotion_timer.stop()
            self._emotion_timer.start(int(duration * 1000))

    def _clear_emotion(self) -> None:
        self.set_emotion(Emotion.NEUTRAL, duration=0)

    def _apply_emotion(self) -> None:
        self.emotion_speed_scale = 1.0
        self.emotion_bounce_amplitude = 0.0
        self.emotion_posture = [0.0, 0.0, 0.0]

        e = self.current_emotion
        if e == Emotion.HAPPY:
            self.emotion_speed_scale = 1.15
            self.emotion_bounce_amplitude = 0.2
            self.emotion_posture = [0.0, -0.3, 0.0]
        elif e == Emotion.EXCITED:
            self.emotion_speed_scale = 1.5
            self.emotion_bounce_amplitude = 0.4
            self.emotion_posture = [0.0, -0.5, 0.0]
        elif e == Emotion.SAD:
            self.emotion_speed_scale = 0.7
            self.emotion_bounce_amplitude = -0.1
            self.emotion_posture = [2.0, 0.0, 0.0]
        elif e == Emotion.ANNOYED:
            self.emotion_speed_scale = 2.0
            self.emotion_bounce_amplitude = 0.1
            self.emotion_posture = [0.0, 0.0, 1.0]
        elif e == Emotion.FLIRTY:
            self.emotion_speed_scale = 1.0
            self.emotion_bounce_amplitude = 0.15
            self.emotion_posture = [0.0, -0.2, 2.0]
        elif e == Emotion.SLEEPY:
            self.emotion_speed_scale = 0.6
            self.emotion_bounce_amplitude = -0.2
            self.emotion_posture = [3.0, 0.0, 0.0]

    def parse_emotion_from_text(self, text: str) -> None:
        tl = text.lower()
        if any(p in tl for p in ["good girl", "thanks", "thank you", "love you"]):
            self.set_emotion(Emotion.HAPPY, duration=3.0)
        elif any(p in tl for p in ["do this", "click", "type", "do it"]):
            self.set_emotion(Emotion.EXCITED, duration=2.0)
        elif any(p in tl for p in ["error", "fail", "wrong", "can't"]):
            self.set_emotion(Emotion.ANNOYED, duration=2.0)
        elif any(p in tl for p in ["sexy", "kiss", "love", "beautiful", "hot"]):
            self.set_emotion(Emotion.FLIRTY, duration=3.0)
        elif any(p in tl for p in ["bye", "goodbye", "see you", "later"]):
            self.set_emotion(Emotion.SAD, duration=4.0)
        if datetime.datetime.now().hour >= 1 and random.random() < 0.3:
            self.set_emotion(Emotion.SLEEPY, duration=5.0)

    def parse_interaction_command(self, text: str) -> Optional[str]:
        tl = text.lower().strip()
        if "tap" in tl or "wave" in tl:
            self.signal_wave()
            self.set_emotion(Emotion.EXCITED, duration=2.0)
            return "wave"
        if "spin" in tl:
            self._transition(AnimationState.SPIN)
            return "spin"
        if "jump" in tl:
            self.trigger_jump()
            return "jump"
        if "dance" in tl:
            self._transition(AnimationState.SPIN)
            return "dance"
        return None

    # ══════════════════════════════════════════════════════════════════
    #  Roaming (2D only, no Z-axis)
    # ══════════════════════════════════════════════════════════════════

    def set_roaming(self, enabled: bool) -> None:
        self.should_roam = enabled
        if not enabled:
            self.target_velocity = 0.0

    def set_roam_direction(self, velocity: float) -> None:
        self.target_velocity = velocity

    def _update_roaming(self, dt: float) -> None:
        """Update roaming position with random direction changes every 2-4 seconds.

        Direction choices: left, right, stop, turn_around.
        Smooth acceleration toward target velocity, bounce at boundaries.
        """
        accel = 8.0 * self.emotion_speed_scale
        decel = 10.0 * self.emotion_speed_scale

        # ── Random direction change every 2-4 seconds ──
        self._move_time += dt
        if self._move_time >= random.uniform(2.0, 4.0):
            self._move_time = 0.0
            walk_speed = 60.0 * self.emotion_speed_scale  # pixels/sec
            choice = random.choice(["left", "right", "stop", "turn_around"])
            if choice == "left":
                self.target_velocity = -walk_speed
            elif choice == "right":
                self.target_velocity = walk_speed
            elif choice == "stop":
                self.target_velocity = 0.0
            elif choice == "turn_around":
                self.target_velocity = -self.velocity
            # Clamp target velocity
            max_speed = 120.0 * self.emotion_speed_scale
            self.target_velocity = max(-max_speed, min(max_speed, self.target_velocity))

        # ── Smooth acceleration/deceleration ──
        if abs(self.target_velocity - self.velocity) > 0.01:
            direction = 1.0 if self.target_velocity > self.velocity else -1.0
            a = accel if direction > 0 else decel
            self.velocity += direction * a * dt
            if abs(self.velocity) > abs(self.target_velocity):
                self.velocity = self.target_velocity
        else:
            self.velocity = self.target_velocity

        # ── Apply movement ──
        self.x += self.velocity * dt

        # ── Boundary collision (bounce at roaming box edges) ──
        half_box = self.roam_box_width / 2.0
        if self.x < -half_box:
            self.x = -half_box
            self.velocity = abs(self.velocity) * 0.8  # Bounce right with some energy loss
            self.target_velocity = abs(self.target_velocity)
        elif self.x > half_box:
            self.x = half_box
            self.velocity = -abs(self.velocity) * 0.8  # Bounce left
            self.target_velocity = -abs(self.target_velocity)

        self.flip_sprite = self.velocity < -0.1
        self.roam_position_changed.emit(self.x, self.velocity)

    # ══════════════════════════════════════════════════════════════════
    #  Jump
    # ══════════════════════════════════════════════════════════════════

    def trigger_jump(self) -> None:
        if self.is_jumping:
            return
        self.is_jumping = True
        self._jump_time = 0.0
        self._transition(AnimationState.JUMP)
        self.jump_triggered.emit()

    def _update_jump(self, dt: float) -> None:
        if not self.is_jumping:
            return
        self._jump_time += dt
        progress = min(1.0, self._jump_time / 0.8)
        if progress >= 1.0:
            self.is_jumping = False
            if self.current_state == AnimationState.JUMP:
                self._transition(AnimationState.IDLE)

    # ══════════════════════════════════════════════════════════════════
    #  Blink
    # ══════════════════════════════════════════════════════════════════

    def _update_blink(self, dt: float) -> None:
        self._blink_time += dt
        if self._blink_time >= self._blink_interval:
            self.is_blinking = True
            self._blink_time = 0.0
            self._blink_interval = random.uniform(3.0, 6.0)
        if self.is_blinking and self._blink_time > 0.1:
            self.is_blinking = False

    # ══════════════════════════════════════════════════════════════════
    #  Micro-interactions
    # ══════════════════════════════════════════════════════════════════

    def _try_micro(self) -> None:
        if self.current_state != AnimationState.IDLE:
            return
        if self._micro_active is not None:
            return
        if self._idle_timer - self._last_micro < self._micro_cooldown:
            return
        if self.current_emotion in (Emotion.SLEEPY, Emotion.SAD):
            return
        if random.random() < 0.2:
            micro = random.choice(self._MICRO_INTERACTIONS)
            duration = random.uniform(0.8, 2.0)
            self._micro_active = micro
            self._micro_end = self._idle_timer + duration
            self._last_micro = self._idle_timer
            self.animation_started.emit(f"micro_{micro}")

    def get_micro_interaction(self) -> str:
        return self._micro_active or ""

    def _check_micro_expired(self) -> None:
        if self._micro_active is not None and self._idle_timer >= self._micro_end:
            self._micro_active = None
            self.animation_finished.emit("micro_end")

    # ══════════════════════════════════════════════════════════════════
    #  Boredom
    # ══════════════════════════════════════════════════════════════════

    def _update_boredom(self, dt: float) -> None:
        if self.current_state != AnimationState.IDLE:
            self._boredom_tier = 0
            self._boredom_played.clear()
            return
        if self._micro_active is not None:
            return
        if self.current_emotion in (Emotion.EXCITED, Emotion.HAPPY, Emotion.FLIRTY):
            self._boredom_tier = 0
            return
        self._check_micro_expired()
        # Also tick micro expiry during boredom check
        if self._micro_active is not None:
            return

        new_tier = 0
        for i, th in enumerate(self._BOREDOM_THRESHOLDS):
            if self._idle_timer >= th:
                new_tier = i + 1
        self._boredom_tier = new_tier

        if new_tier >= 1 and 1 not in self._boredom_played:
            self._boredom_played.add(1)
            self.animation_started.emit("bored_tier1")
        if new_tier >= 2 and 2 not in self._boredom_played:
            self._boredom_played.add(2)
            self.animation_started.emit("bored_tier2")
        if new_tier >= 3 and 3 not in self._boredom_played:
            self._boredom_played.add(3)
            self.animation_started.emit("bored_tier3")
        if new_tier >= 4 and 4 not in self._boredom_played:
            self._boredom_played.add(4)
            self.bored_triggered.emit()

    def get_boredom_tier(self) -> int:
        return self._boredom_tier

    # ══════════════════════════════════════════════════════════════════
    #  Cleanup
    # ══════════════════════════════════════════════════════════════════

    def cleanup(self) -> None:
        self._tick_timer.stop()
        self._emotion_timer.stop()
        self._micro_timer.stop()
