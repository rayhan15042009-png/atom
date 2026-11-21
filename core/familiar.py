"""Atom Roaming System — restored from Echo's stable familiar.py.

Handles 2D sprite roaming with direction changes, special poses,
and collision detection. Clean, simple, and reliable.
"""

import random
from PyQt6.QtCore import QObject, QTimer


class Familiar(QObject):
    """2D sprite roaming with direction changes and special poses."""

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.mode = config.get('mode', 'tap') if isinstance(config, dict) else getattr(config, 'mode', 'tap')
        self.sprite_width = self._get_cfg('sprite_width', 96)
        self.sprite_height = self._get_cfg('sprite_height', 148)
        self.home_x = float(self._get_cfg('start_x', 120))
        self.home_y = float(self._get_cfg('start_y', 120))
        self.x = self.home_x
        self.y = self.home_y
        self.vx = 0
        self.vy = 0
        self.pose = 'idle'
        self.animation_frame = 0
        self.roaming = True
        self.pose_counter = 0

        self.roam_timer = QTimer()
        self.roam_timer.timeout.connect(self.choose_direction)
        self.roam_timer.start(2000)

    def _get_cfg(self, key, default):
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    def choose_direction(self):
        if not self.roaming:
            return
        self.roam_timer.start(random.randint(1000, 3500))

        self.pose_counter = (self.pose_counter + 1) % 8
        if self.pose_counter == 0:
            choice = random.choice(['idle', 'walk', 'kick', 'yawn', 'sit', 'wave', 'jump'])
            if choice != 'idle':
                self.pose = choice
                if choice in ('sit', 'yawn'):
                    self.vx = 0
                    self.vy = 0
                    return

        choice = random.choice(['stop', 'left', 'right', 'up', 'down',
                                'upleft', 'upright', 'downleft', 'downright'])
        self.mode = self._get_cfg('mode', 'tap')
        max_speed = 5 if self.mode == 'stalk' else 3
        speed = random.choice([0, 1, 2, 3, max_speed])
        if choice == 'stop' or speed == 0:
            self.vx = 0
            self.vy = 0
        elif choice == 'left':
            self.vx = -speed; self.vy = 0
        elif choice == 'right':
            self.vx = speed; self.vy = 0
        elif choice == 'up':
            self.vx = 0; self.vy = -speed
        elif choice == 'down':
            self.vx = 0; self.vy = speed
        elif choice == 'upleft':
            self.vx = -speed; self.vy = -speed
        elif choice == 'upright':
            self.vx = speed; self.vy = -speed
        elif choice == 'downleft':
            self.vx = -speed; self.vy = speed
        elif choice == 'downright':
            self.vx = speed; self.vy = speed

    def update_position(self, bounds):
        if not self.roaming:
            return
        self.x += self.vx
        self.y += self.vy
        left, top, right, bottom = bounds
        if self.x < left:
            self.x = left; self.vx = abs(self.vx)
        if self.x > right:
            self.x = right; self.vx = -abs(self.vx)
        if self.y < top:
            self.y = top; self.vy = abs(self.vy)
        if self.y > bottom:
            self.y = bottom; self.vy = -abs(self.vy)

    def set_home(self, x, y):
        self.home_x = x
        self.home_y = y
        self.x = x
        self.y = y

    def freeze(self):
        self.roaming = False
        self.vx = 0
        self.vy = 0

    def resume(self):
        self.roaming = True

    def set_pose(self, pose):
        self.pose = pose
        if pose in ('listen', 'talk', 'sleep', 'bored', 'sit', 'yawn'):
            self.freeze()
        elif pose == 'idle':
            self.resume()

    def stop(self):
        self.roam_timer.stop()
