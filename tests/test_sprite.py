"""
Tests for ui/sprite_2d — Sprite2D, character profiles, block drawing.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QPixmap
from PyQt6.QtWidgets import QApplication


# ── QApplication singleton (required for QPixmap/QPainter) ──
_APP = QApplication.instance() or QApplication([])


# ── Sprite2D imports ──
from ui.sprite_2d import (
    Sprite2D,
    CHARACTER_PROFILES,
    _HAS_SKIN_LOADER,
)


class TestCharacterProfiles(unittest.TestCase):
    """CHARACTER_PROFILES dict integrity."""

    def test_has_required_characters(self) -> None:
        for name in ("atom", "pink", "steve", "alex", "ghost_rider"):
            self.assertIn(name, CHARACTER_PROFILES)

    def test_each_profile_has_required_keys(self) -> None:
        required = {"name", "skin", "hair", "shirt", "pants", "boot", "eye_color"}
        for name, profile in CHARACTER_PROFILES.items():
            if name == "echo":
                continue  # alias
            with self.subTest(character=name):
                for key in required:
                    self.assertIn(key, profile, f"{name} missing key: {key}")

    def test_echo_is_alias(self) -> None:
        self.assertIs(CHARACTER_PROFILES["echo"], CHARACTER_PROFILES["atom"])

    def test_colors_are_3_tuples(self) -> None:
        color_keys = ["skin", "hair", "shirt", "pants", "boot", "eye_color"]
        for name, profile in CHARACTER_PROFILES.items():
            if name == "echo":
                continue
            with self.subTest(character=name):
                for key in color_keys:
                    val = profile.get(key)
                    if val is not None:
                        self.assertIsInstance(val, tuple)
                        self.assertEqual(len(val), 3)
                        for c in val:
                            self.assertTrue(0 <= c <= 255)


class TestSprite2DConstruction(unittest.TestCase):
    """Sprite2D creation and basic properties."""

    def test_default_scale(self) -> None:
        sprite = Sprite2D()
        self.assertEqual(sprite.scale, 2)
        self.assertEqual(sprite.width, 96)   # 48 * 2
        self.assertEqual(sprite.height, 148) # 74 * 2

    def test_custom_scale(self) -> None:
        sprite = Sprite2D(scale=3)
        self.assertEqual(sprite.scale, 3)
        self.assertEqual(sprite.width, 144)  # 48 * 3
        self.assertEqual(sprite.height, 222) # 74 * 3

    def test_default_character(self) -> None:
        sprite = Sprite2D()
        self.assertEqual(sprite.character, "atom")

    def test_custom_character(self) -> None:
        sprite = Sprite2D(character="steve")
        self.assertEqual(sprite.character, "steve")

    def test_invalid_character_falls_back(self) -> None:
        sprite = Sprite2D(character="nonexistent")
        self.assertEqual(sprite.character, "nonexistent")  # stored as-is
        self.assertIn("name", sprite.profile)  # fallback to atom profile

    def test_set_character(self) -> None:
        sprite = Sprite2D(character="atom")
        sprite.set_character("pink")
        self.assertEqual(sprite.character, "pink")

    def test_set_invalid_character_ignored(self) -> None:
        sprite = Sprite2D(character="atom")
        sprite.set_character("invalid")
        self.assertEqual(sprite.character, "atom")

    def test_mood_speed_clamping(self) -> None:
        sprite = Sprite2D()
        sprite.set_mood_speed(0.1)
        self.assertEqual(sprite.mood_speed, 0.3)
        sprite.set_mood_speed(3.0)
        self.assertEqual(sprite.mood_speed, 2.0)
        sprite.set_mood_speed(1.0)
        self.assertEqual(sprite.mood_speed, 1.0)


class TestSprite2DPoses(unittest.TestCase):
    """Pose normalisation and POSES list."""

    def test_positions(self) -> None:
        expected_poses = {
            "idle", "walk", "run", "talk", "listen", "bored", "kick", "yawn",
            "sit", "wave", "spin", "jump", "sleep", "dance", "blush", "think",
            "cheer", "stretch", "confused", "surprised", "laugh",
            "look_around", "head_scratch", "tilt_curious", "blink_slow", "sigh",
        }
        for pose in expected_poses:
            self.assertIn(pose, Sprite2D.POSES)

    def test_normalise_valid(self) -> None:
        self.assertEqual(Sprite2D.normalise_pose("walk"), "walk")
        self.assertEqual(Sprite2D.normalise_pose("dance"), "dance")

    def test_normalise_invalid(self) -> None:
        self.assertEqual(Sprite2D.normalise_pose("invalid"), "idle")

    def test_normalise_empty(self) -> None:
        self.assertEqual(Sprite2D.normalise_pose(""), "idle")


class TestSprite2DSkinLoading(unittest.TestCase):
    """Skin loading integration — uses real path checks."""

    def test_every_character_takes_its_colours_from_its_own_atlas(self) -> None:
        """``set_character`` recolours the sprite from the atlas, so a regenerated
        atlas that no longer resolves to its intended tones would recolour the
        sprite without any drawing code changing."""
        from ui.sprite_2d import _SKIN_PATHS, ROOT
        from core.skin_loader import MinecraftSkinLoader

        for character, rel_path in _SKIN_PATHS.items():
            with self.subTest(character=character):
                loader = MinecraftSkinLoader(str(ROOT / rel_path), hd_resize=True)
                head = loader.get_2d_palette()["head_color"]
                sprite = Sprite2D(character=character)
                sprite.set_character(character)
                self.assertEqual(
                    sprite.profile["skin"], (head.red(), head.green(), head.blue()),
                )

    def test_skin_paths_exist(self) -> None:
        from ui.sprite_2d import _SKIN_PATHS, ROOT
        for character, rel_path in _SKIN_PATHS.items():
            full = ROOT / rel_path
            # Not all skins may exist in dev; this is a soft check
            if not full.exists():
                self.skipTest(f"Skin file not found: {full}")

    @unittest.skipIf(_HAS_SKIN_LOADER, "PIL/loader is available — skin loading will work, not fallback")
    def test_load_skin_colors_fallback_no_pil(self) -> None:
        """If PIL is not available, fallback returns the fallback_profile."""
        from ui.sprite_2d import _load_skin_colors
        fallback = {"name": "test", "skin": (100, 100, 100)}
        result = _load_skin_colors("atom", fallback)
        self.assertIs(result, fallback)


class TestSprite2DProfileProperty(unittest.TestCase):
    """profile property getter/setter."""

    def test_profile_getter_uses_skin_or_character_profiles(self) -> None:
        sprite = Sprite2D(character="atom")
        p = sprite.profile
        self.assertIn("name", p)

    def test_profile_setter(self) -> None:
        sprite = Sprite2D(character="atom")
        custom = {"name": "custom", "skin": (255, 0, 0)}
        sprite.profile = custom
        self.assertIs(sprite.profile, custom)


class TestSprite2DPixmap(unittest.TestCase):
    """Lightweight pixmap-based tests for draw method.

    Uses a single reusable pixmap per test to avoid QPainter creation pressure
    (known Windows/PyQt6 stability issue with many QPainter instances).
    """

    def _render(self, sprite: Sprite2D, **kwargs) -> QPixmap:
        """Render sprite at given kwargs into a new pixmap."""
        pix = QPixmap(sprite.width + 10, sprite.height + 10)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        try:
            sprite.draw(painter, 0, 0, **kwargs)
        finally:
            painter.end()
        return pix

    def test_draw_idle_pose_creates_content(self) -> None:
        pix = self._render(Sprite2D(scale=1), pose="idle")
        image = pix.toImage()
        has_content = any(
            image.pixelColor(x, y).alpha() > 0
            for y in range(min(50, image.height()))
            for x in range(min(50, image.width()))
        )
        self.assertTrue(has_content, "Idle pose should produce visible pixels")

    def test_draw_pose_roundtrip(self) -> None:
        """Smoke test: all basic poses render without crash."""
        for pose in ("idle", "walk", "talk", "sit", "wave", "dance", "spin", "jump"):
            pix = self._render(Sprite2D(scale=1), pose=pose)
            self.assertFalse(pix.isNull())
            del pix

    def test_draw_emotion_roundtrip(self) -> None:
        """Smoke test: all emotions render without crash."""
        for emotion in ("neutral", "happy", "sad", "excited", "annoyed", "flirty", "sleepy"):
            pix = self._render(Sprite2D(scale=1), pose="idle", emotion=emotion)
            self.assertFalse(pix.isNull())
            del pix

    def test_draw_all_characters(self) -> None:
        """Smoke test: every character renders without crash."""
        for char in ("atom", "echo", "pink", "gamergirl", "steve", "alex", "ghost_rider"):
            pix = self._render(Sprite2D(scale=1, character=char), pose="idle")
            self.assertFalse(pix.isNull())
            del pix

    def test_draw_all_features(self) -> None:
        """Smoke test: boredom tiers, micro-interactions, advanced poses render."""
        s = Sprite2D(scale=1)
        for tier in range(5):
            pix = self._render(s, pose="idle", boredom_tier=tier)
            self.assertFalse(pix.isNull())
            del pix
        for micro in ("look_around", "head_scratch", "tilt_curious", "sigh"):
            pix = self._render(s, pose="idle", micro_interaction=micro)
            self.assertFalse(pix.isNull())
            del pix
        for pose in ("combat_idle", "point", "shrug", "salute"):
            pix = self._render(s, pose=pose, animation_frame=3)
            self.assertFalse(pix.isNull())
            del pix

    def test_draw_unknown_emotion_falls_back(self) -> None:
        pix = self._render(Sprite2D(scale=1), pose="idle", emotion="nonexistent")
        self.assertFalse(pix.isNull())

    def test_draw_outside_bounds(self) -> None:
        """Drawing at negative position should not crash."""
        pix = QPixmap(10, 10)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        try:
            Sprite2D(scale=2).draw(painter, -100, -100, pose="idle")
        finally:
            painter.end()


class TestDrawBlockPrimitives(unittest.TestCase):
    """Direct tests for draw_block, draw_block_no_edge, draw_glow."""

    def _render_block(self, method_name: str, *args) -> QPixmap:
        sprite = Sprite2D(scale=1)
        pix = QPixmap(100, 100)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        try:
            getattr(sprite, method_name)(painter, *args)
        finally:
            painter.end()
        return pix

    def test_draw_block(self) -> None:
        pix = self._render_block("draw_block", 10, 10, 20, 20, (100, 150, 200))
        self.assertTrue(pix.toImage().pixelColor(20, 20).alpha() > 0)

    def test_draw_block_none_color(self) -> None:
        pix = self._render_block("draw_block", 10, 10, 20, 20, None)
        self.assertEqual(pix.toImage().pixelColor(20, 20).alpha(), 0)

    def test_draw_block_no_edge(self) -> None:
        pix = self._render_block("draw_block_no_edge", 10, 10, 20, 20, (200, 100, 50))
        self.assertTrue(pix.toImage().pixelColor(20, 20).alpha() > 0)

    def test_draw_block_no_edge_none_color(self) -> None:
        pix = self._render_block("draw_block_no_edge", 10, 10, 20, 20, None)
        self.assertEqual(pix.toImage().pixelColor(20, 20).alpha(), 0)

    def test_draw_glow(self) -> None:
        pix = self._render_block("draw_glow", 10, 10, 20, 20, (0, 200, 255), 100)
        self.assertTrue(pix.toImage().pixelColor(20, 20).alpha() > 0)

    def test_silhouette_rects_collected(self) -> None:
        sprite = Sprite2D(scale=1)
        pix = QPixmap(100, 100)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        try:
            sprite.draw_block(painter, 10, 10, 20, 20, (100, 100, 100))
        finally:
            painter.end()
        self.assertGreater(len(sprite._silhouette_rects), 0)

    def test_mood_speed_normal(self) -> None:
        sprite = Sprite2D()
        sprite.set_mood_speed(1.0)
        self.assertEqual(sprite.mood_speed, 1.0)

    def test_scale_negative_ignored(self) -> None:
        sprite = Sprite2D(scale=-1)
        self.assertEqual(sprite.scale, -1)  # stored as-is, used in multiplication


class TestSpriteBevelShading(unittest.TestCase):
    """3-tone pixel bevel: top highlight, left rim, right shadow, bottom shadow."""

    def test_cached_bevel_brushes_exist(self) -> None:
        for name in ("_HIGHLIGHT_BRUSH", "_RIM_BRUSH", "_SHADOW_BRUSH", "_BOTTOM_BRUSH"):
            self.assertTrue(hasattr(Sprite2D, name), f"missing {name}")

    def test_bottom_edge_is_darker_than_centre(self) -> None:
        sprite = Sprite2D(scale=1)
        pix = QPixmap(100, 100)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        try:
            sprite.draw_block(painter, 10, 10, 40, 40, (200, 200, 200))
        finally:
            painter.end()
        img = pix.toImage()
        centre = img.pixelColor(30, 25)
        bottom = img.pixelColor(30, 48)
        self.assertLess(bottom.red(), centre.red())

    def test_left_rim_is_lighter_than_right_edge(self) -> None:
        sprite = Sprite2D(scale=1)
        pix = QPixmap(100, 100)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        try:
            sprite.draw_block(painter, 10, 10, 40, 40, (120, 120, 120))
        finally:
            painter.end()
        img = pix.toImage()
        left = img.pixelColor(11, 30)
        right = img.pixelColor(47, 30)
        self.assertGreaterEqual(left.red(), right.red())

    def test_shade_false_skips_bevel(self) -> None:
        sprite = Sprite2D(scale=1)
        pix = QPixmap(100, 100)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        try:
            sprite.draw_block(painter, 10, 10, 40, 40, (120, 120, 120), shade=False)
        finally:
            painter.end()
        img = pix.toImage()
        self.assertEqual(img.pixelColor(30, 25).red(), 120)


class TestSpriteEyebrows(unittest.TestCase):
    """Eyebrows add facial definition above the eyes for non-ghost characters."""

    def _render(self, character: str, emotion: str = "neutral") -> QPixmap:
        sprite = Sprite2D(scale=1, character=character)
        pix = QPixmap(sprite.width + 10, sprite.height + 10)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        try:
            sprite.draw(painter, 0, 0, pose="idle", emotion=emotion)
        finally:
            painter.end()
        return pix

    def test_all_profiles_render_with_eyebrows(self) -> None:
        for char in ("atom", "pink", "steve", "alex", "ghost_rider"):
            with self.subTest(character=char):
                pix = self._render(char)
                self.assertFalse(pix.isNull())
                del pix

    def test_emotions_render_with_eyebrows(self) -> None:
        for emotion in ("neutral", "sad", "confused", "annoyed", "excited", "happy"):
            with self.subTest(emotion=emotion):
                pix = self._render("atom", emotion)
                self.assertFalse(pix.isNull())
                del pix

    def test_brow_pixels_present_above_eyes(self) -> None:
        """A darker-than-skin pixel must appear in the brow row for Atom."""
        pix = self._render("atom")
        img = pix.toImage()
        skin = (205, 155, 105)
        found = False
        for y in range(0, 40):
            for x in range(10, 40):
                c = img.pixelColor(x, y)
                if c.alpha() > 0 and c.red() < skin[0] - 10:
                    found = True
                    break
            if found:
                break
        self.assertTrue(found, "expected darker brow pixels near the head")


if __name__ == "__main__":
    unittest.main()
