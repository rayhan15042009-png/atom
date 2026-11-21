"""
Sprite 2D — block-style pixel art sprite system with character profiles,
Minecraft skin integration, and pose/emotion rendering.

Designed as a clean replacement for ui/sprite_3d.py — pure 2D drawing
with no dependency on the 3D renderer.
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QFontMetrics, QImage, QPainter,
    QPainterPath, QPen, QPixmap, QRadialGradient, QRegion,
)

ROOT = Path(__file__).resolve().parent.parent

# ── Optional skin loader (requires Pillow) ──
_HAS_SKIN_LOADER = False
try:
    from core.skin_loader import MinecraftSkinLoader
    _HAS_SKIN_LOADER = True
except ImportError:
    pass

# ── Skin paths ──
_SKIN_PATHS: dict[str, str] = {
    "atom": "assets/skins/atom_skin.png",
    "pink": "assets/skins/pink_skin.png",
    "gamergirl": "assets/skins/gamergirl.png",
    "steve": "assets/skins/steve_skin.png",
    "alex": "assets/skins/alex_skin.png",
    "ghost_rider": "assets/skins/ghost_skin.png",
}

# ── Minecraft Skin UV Map (from core/skin_loader.py) ──
# Defines which pixel region of the 64×64 skin PNG maps to each body part
_SKIN_UV: dict[str, tuple[int, int, int, int]] = {
    "head_front": (8, 8, 8, 8),      # Head face
    "head_top": (8, 0, 8, 8),        # Top of head (hair)
    "body_front": (20, 20, 8, 12),   # Body/torso
    "arm_front": (44, 20, 4, 12),    # Right arm (front view)
    "arm_side": (40, 20, 4, 12),     # Right arm (left/outer side)
    "leg_front": (4, 20, 4, 12),     # Right leg (front view)
    "leg_side": (0, 20, 4, 12),      # Right leg (left/outer side)
    "hat_front": (40, 8, 8, 8),      # Hat layer over head
}


# ═══════════════════════════════════════════════════════════════════════
#  Character profiles
# ═══════════════════════════════════════════════════════════════════════

CHARACTER_PROFILES: dict[str, dict] = {
    "atom": {
        "name": "Atom (Default)",
        "skin": (205, 155, 105),
        "hair": (70, 48, 38),
        "shirt": (74, 115, 205),
        "shirt_dark": (45, 73, 150),
        "pants": (42, 46, 60),
        "boot": (26, 28, 36),
        "cherry": (210, 44, 64),
        "horn": (235, 226, 186),
        "eye_color": (12, 12, 16),
        "eye_highlight": (180, 200, 240),
        "blush_color": (255, 140, 140),
    },
    "pink": {
        "name": "Pink (Gamer Girl)",
        "skin": (240, 200, 175),
        "hair": (235, 80, 150),
        "hair_light": (255, 180, 210),
        "shirt": (255, 130, 180),
        "shirt_dark": (220, 85, 140),
        "pants": (55, 45, 75),
        "pants_light": (80, 60, 105),
        "boot": (210, 160, 230),
        "boot_trim": (180, 120, 200),
        "cherry": (255, 65, 110),
        "horn": (255, 200, 215),
        "eye_color": (220, 60, 140),
        "eye_highlight": (255, 150, 200),
        "blush_color": (255, 120, 160),
        "accessory": "ribbon",
        "ribbon_color": (255, 100, 150),
        "sparkle_color": (255, 180, 230),
    },
    "steve": {
        "name": "Steve (Minecraft Classic)",
        # The classic skin: teal shirt, indigo jeans, tan skin, dark brown hair.
        "skin": (198, 164, 130),
        "hair": (54, 38, 26),
        "beard": (76, 54, 38),
        "shirt": (0, 170, 170),
        "shirt_dark": (0, 120, 125),
        "shirt_trim": (70, 205, 205),
        "pants": (60, 68, 170),
        "pants_light": (86, 94, 205),
        "boot": (58, 52, 48),
        "boot_trim": (40, 36, 32),
        "cherry": None,
        "horn": None,
        "eye_color": (40, 42, 55),
        "eye_highlight": (130, 150, 190),
        "blush_color": (200, 140, 120),
    },
    "alex": {
        "name": "Alex (Minecraft Default)",
        "skin": (225, 190, 158),
        "hair": (235, 168, 55),
        "hair_light": (255, 195, 90),
        "shirt": (80, 155, 78),
        "shirt_dark": (55, 115, 55),
        "shirt_trim": (100, 180, 100),
        "pants": (48, 52, 68),
        "pants_light": (68, 72, 88),
        "boot": (40, 40, 45),
        "boot_trim": (55, 55, 60),
        "cherry": None,
        "horn": None,
        "eye_color": (50, 65, 80),
        "eye_highlight": (100, 120, 160),
        "blush_color": (220, 165, 145),
        "accessory": "ponytail",
        "ponytail_color": (235, 168, 55),
        "ponytail_band_color": (160, 100, 30),
    },
    "ghost_rider": {
        "name": "Ghost Rider",
        "skin": (175, 125, 75),
        "hair": (12, 10, 8),
        "hair_light": (35, 28, 22),
        "shirt": (30, 28, 32),
        "shirt_dark": (20, 18, 22),
        "shirt_trim": (55, 48, 58),
        "pants": (24, 24, 30),
        "pants_light": (40, 38, 44),
        "boot": (20, 18, 22),
        "boot_trim": (38, 32, 38),
        "cherry": None,
        "horn": None,
        "eye_color": (255, 100, 20),
        "eye_highlight": (255, 220, 80),
        "blush_color": (200, 60, 20),
        "flame_inner": (255, 200, 50),
        "flame_mid": (255, 140, 40),
        "flame_outer": (200, 60, 10),
        "chain_color": (175, 165, 150),
        "chain_dark": (120, 115, 105),
        "jacket_red": (165, 28, 28),
        "accessory": "skull_flame",
        "sparkle_color": (255, 140, 40),
    },
}
# GamerGirl profile (uses gamergirl.png skin)
CHARACTER_PROFILES["gamergirl"] = CHARACTER_PROFILES["pink"].copy()
CHARACTER_PROFILES["gamergirl"]["name"] = "Gamer Girl"
# Legacy alias
CHARACTER_PROFILES["echo"] = CHARACTER_PROFILES["atom"]


# ═══════════════════════════════════════════════════════════════════════
#  Skin color loading
# ═══════════════════════════════════════════════════════════════════════

def _load_skin_colors(character: str, fallback_profile: dict) -> dict:
    """Attempt to load skin PNG and extract dominant colors per body region.

    Falls back to hardcoded profile if Pillow is missing or file not found.
    """
    if not _HAS_SKIN_LOADER:
        return fallback_profile

    skin_rel = _SKIN_PATHS.get(character)
    if not skin_rel:
        return fallback_profile

    full_path = ROOT / skin_rel
    if not full_path.exists():
        return fallback_profile

    try:
        loader = MinecraftSkinLoader(str(full_path), hd_resize=True)
        palette = loader.get_2d_palette()
        profile = dict(fallback_profile)

        hc = palette.get("head_color")
        if hc:
            profile["skin"] = (hc.red(), hc.green(), hc.blue())

        bc = palette.get("body_color")
        if bc:
            profile["shirt"] = (bc.red(), bc.green(), bc.blue())
            r, g, b = profile["shirt"]
            profile["shirt_dark"] = (max(0, r - 40), max(0, g - 40), max(0, b - 40))

        ac = palette.get("arm_color")
        if ac:
            profile["arm_color"] = (ac.red(), ac.green(), ac.blue())

        lc = palette.get("leg_color")
        if lc:
            profile["pants"] = (lc.red(), lc.green(), lc.blue())
            r, g, b = profile["pants"]
            profile["pants_light"] = (min(255, r + 20), min(255, g + 18), min(255, b + 15))

        boc = palette.get("boot_color")
        if boc:
            profile["boot"] = (boc.red(), boc.green(), boc.blue())

        hac = palette.get("hair_color")
        if hac:
            r, g, b = hac.red(), hac.green(), hac.blue()
            profile["hair"] = (r, g, b)
            profile["hair_light"] = (min(255, r + 30), min(255, g + 25), min(255, b + 20))

        return profile
    except Exception:
        return fallback_profile


# ═══════════════════════════════════════════════════════════════════════
#  Sprite2D
# ═══════════════════════════════════════════════════════════════════════

class Sprite2D:
    """Block-style pixel art sprite with character profiles and pose rendering.

    Canvas: 48×74 base (scaled by ``scale``), matching Echo's clean sprite approach.
    Drawing fits the full canvas — no offset needed.
    """

    POSES: list[str] = [
        "idle", "walk", "run", "talk", "listen", "bored", "kick", "yawn",
        "sit", "wave", "spin", "jump", "sleep", "dance", "blush", "think",
        "cheer", "stretch", "confused", "surprised", "laugh",
        "look_around", "head_scratch", "tilt_curious", "blink_slow", "sigh",
        "combat_idle", "point", "shrug", "salute",
    ]

    # Pre-computed color constants used across draw calls
    _EDGE_COLOR = (35, 35, 45)  # Echo's clean dark edge
    # Cached QPen/QBrush to avoid per-frame allocations in draw_block
    _EDGE_PEN = QPen(QColor(*_EDGE_COLOR), 1)
    _HIGHLIGHT_BRUSH = QBrush(QColor(255, 255, 255, 22))
    _RIM_BRUSH = QBrush(QColor(255, 255, 255, 16))
    _SHADOW_BRUSH = QBrush(QColor(0, 0, 0, 24))
    _BOTTOM_BRUSH = QBrush(QColor(0, 0, 0, 26))

    def __init__(self, scale: int = 2, character: str = "atom", use_skin_png: bool = True):
        self.scale = scale
        # When True a Minecraft skin PNG from assets/skins overrides the palette
        # and supplies textures. When False the hand-authored
        # CHARACTER_PROFILES colours are used — which is what the app passes, so
        # Atom and Pink render as Echo's designs instead of a repainted skin.
        self.use_skin_png = use_skin_png
        self.width = 48 * scale
        self.height = 74 * scale
        self._draw_offset_x = 0  # canvas matches Echo's 48-wide drawing directly
        self._draw_offset_y = 0

        self.character = character
        self.profile = CHARACTER_PROFILES.get(character, CHARACTER_PROFILES["atom"])
        self._skin_profile = None  # skin-loaded merged profile
        if self.use_skin_png:
            self._skin_profile = _load_skin_colors(character, CHARACTER_PROFILES.get(character, CHARACTER_PROFILES["atom"]))

        # Animation state
        self.animation_frame = 0
        self.mood_speed = 1.0
        self._blink_counter = 0
        self._blink_threshold = random.randint(35, 100)

        # Silhouette rects for post-process outline
        self._silhouette_rects: list[tuple[int, int, int, int]] = []

        # Reusable QRadialGradient for shadow (reduces allocations)
        self._shadow_grad = QRadialGradient(24, 66, 22)

        # ── Skin texture cache ──
        self._skin_textures: dict[str, QPixmap] = {}
        self._textures_loaded = False

    # ── Character management ──

    def set_character(self, character: str) -> None:
        """Switch character profile, loading skin PNG textures if available."""
        if character not in CHARACTER_PROFILES:
            return
        self.character = character
        base = CHARACTER_PROFILES[character]
        if self.use_skin_png:
            self._skin_profile = _load_skin_colors(character, base)
            self._load_skin_textures()
        else:
            self._skin_profile = None

    @property
    def profile(self) -> dict:
        return self._skin_profile or CHARACTER_PROFILES.get(self.character, CHARACTER_PROFILES["atom"])

    @profile.setter
    def profile(self, value: dict) -> None:
        self._skin_profile = value

    def set_mood_speed(self, speed: float) -> None:
        self.mood_speed = max(0.3, min(2.0, speed))

    # ── Pose normalisation ──

    @staticmethod
    def normalise_pose(pose: str) -> str:
        return pose if pose in Sprite2D.POSES else "idle"

    # ── Main draw entry point ──

    def draw(
        self,
        painter: QPainter,
        x: float, y: float,
        pose: str = "idle",
        animation_frame: int = 0,
        emotion: str = "neutral",
        micro_interaction: str = "",
        boredom_tier: int = 0,
    ) -> None:
        x, y = int(x), int(y)
        self.animation_frame = animation_frame % 8
        pose = self.normalise_pose(pose)
        emotion = (emotion or "neutral").lower()

        painter.save()
        painter.translate(x, y)
        painter.scale(self.scale, self.scale)
        painter.translate(self._draw_offset_x, self._draw_offset_y)

        mood_frame = int(animation_frame * self.mood_speed)

        self._silhouette_rects.clear()

        if pose == "walk":
            self._draw_body(painter, mood_frame, walk=True, emotion=emotion)
        elif pose == "run":
            self._draw_body(painter, mood_frame, walk=True, run=True, emotion=emotion)
        elif pose == "talk":
            self._draw_body(painter, mood_frame, talk=True, emotion=emotion)
        elif pose == "listen":
            self._draw_body(painter, mood_frame, listen=True, emotion=emotion)
        elif pose == "bored":
            self._draw_body(painter, mood_frame, bored=True, emotion=emotion)
        elif pose == "kick":
            self._draw_body(painter, mood_frame, kick=True, emotion=emotion)
        elif pose == "yawn":
            self._draw_body(painter, mood_frame, yawn=True, emotion=emotion)
        elif pose == "sit":
            self._draw_body(painter, mood_frame, sit=True, emotion=emotion)
        elif pose == "wave":
            self._draw_body(painter, mood_frame, wave=True, emotion=emotion)
        elif pose == "spin":
            self._draw_spin(painter, mood_frame, emotion)
        elif pose == "jump":
            self._draw_jump(painter, mood_frame, emotion)
        elif pose == "sleep":
            self._draw_body(painter, mood_frame, sleep=True, emotion=emotion)
        elif pose == "dance":
            self._draw_dance(painter, mood_frame, emotion)
        elif pose == "blush":
            self._draw_body(painter, mood_frame, blush=True, emotion=emotion)
        elif pose == "think":
            self._draw_body(painter, mood_frame, think=True, emotion=emotion)
        elif pose == "cheer":
            self._draw_body(painter, mood_frame, cheer=True, emotion=emotion)
        elif pose == "stretch":
            self._draw_body(painter, mood_frame, stretch=True, emotion=emotion)
        elif pose == "confused":
            self._draw_body(painter, mood_frame, confused=True, emotion=emotion)
        elif pose == "surprised":
            self._draw_body(painter, mood_frame, surprised=True, emotion="surprised")
        elif pose == "laugh":
            self._draw_body(painter, mood_frame, laugh=True, emotion="happy")
        elif pose == "combat_idle":
            self._draw_combat_idle(painter, mood_frame, emotion)
        elif pose == "point":
            self._draw_point(painter, mood_frame, emotion)
        elif pose == "shrug":
            self._draw_shrug(painter, mood_frame, emotion)
        elif pose == "salute":
            self._draw_salute(painter, mood_frame, emotion)
        elif pose in ("look_around", "head_scratch", "tilt_curious", "blink_slow", "sigh"):
            self._draw_body(painter, mood_frame, emotion=emotion, micro_interaction=pose)
        else:
            self._draw_body(painter, mood_frame, emotion=emotion)

        if boredom_tier > 0 and pose == "idle":
            self._draw_boredom_indicator(painter, mood_frame, boredom_tier)

        # Unified silhouette stroke
        if self._silhouette_rects:
            region = QRegion()
            for rx, ry, rw, rh in self._silhouette_rects:
                region = region.united(QRegion(rx, ry, rw, rh))
            if not region.isEmpty():
                path = QPainterPath()
                path.addRegion(region)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(15, 14, 18), 1))
                painter.drawPath(path)

        painter.restore()

    # ══════════════════════════════════════════════════════════════════
    #  Block drawing primitives
    # ══════════════════════════════════════════════════════════════════

    def _collect_rect(self, x: int, y: int, w: int, h: int) -> None:
        self._silhouette_rects.append((x - 1, y - 1, w + 2, h + 2))

    def draw_block(
        self, painter: QPainter,
        x: float, y: float, w: float, h: float,
        color: tuple[int, int, int] | None,
        edge: tuple[int, int, int] = (35, 35, 45),
        shade: bool = True,
    ) -> None:
        if color is None:
            return
        ix, iy, iw, ih = int(x), int(y), int(w), int(h)
        self._collect_rect(ix, iy, iw, ih)

        # Use cached edge pen when color is default, otherwise create fresh
        if edge == self._EDGE_COLOR:
            painter.setPen(self._EDGE_PEN)
        else:
            painter.setPen(QPen(QColor(*edge), 1))
        painter.setBrush(QBrush(QColor(*color)))
        painter.drawRect(ix, iy, iw, ih)

        if shade:
            painter.setPen(Qt.PenStyle.NoPen)
            # ── 3-tone pixel bevel (light from top-left) ──
            # Top highlight (12% of the block height)
            painter.setBrush(self._HIGHLIGHT_BRUSH)
            painter.drawRect(ix + 1, iy + 1, max(1, iw - 2), max(1, int(ih * 0.12)))
            # Left rim light — reads as "lit from the upper left", which is what
            # the ground shadow and the top highlight already imply.
            painter.setBrush(self._RIM_BRUSH)
            painter.drawRect(ix + 1, iy + 1, 1, max(1, ih - 2))
            # Right shadow (2px)
            painter.setBrush(self._SHADOW_BRUSH)
            painter.drawRect(ix + iw - 3, iy + 2, 2, max(1, ih - 3))
            # Bottom edge shadow — grounds the block against the one below it
            painter.setBrush(self._BOTTOM_BRUSH)
            painter.drawRect(ix + 2, iy + ih - 2, max(1, iw - 4), 1)

    def draw_block_no_edge(
        self, painter: QPainter,
        x: float, y: float, w: float, h: float,
        color: tuple[int, int, int] | None,
        alpha: int = 255,
    ) -> None:
        if color is None:
            return
        ix, iy, iw, ih = int(x), int(y), int(w), int(h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(*color, alpha)))
        painter.drawRect(ix, iy, iw, ih)

    def draw_glow(
        self, painter: QPainter,
        x: float, y: float, w: float, h: float,
        color: tuple[int, int, int],
        alpha: int,
    ) -> None:
        ix, iy, iw, ih = int(x), int(y), int(w), int(h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(*color, alpha)))
        painter.drawRect(ix, iy, iw, ih)

    # ══════════════════════════════════════════════════════════════════
    #  Skin texture loading
    # ══════════════════════════════════════════════════════════════════

    def _load_skin_textures(self) -> None:
        """
        Extract per-body-part pixel textures from the Minecraft skin PNG
        and cache them as QPixmaps for textured block rendering.

        Maps UV regions from skin_loader.py to the body parts drawn in
        _draw_body(). Falls back silently if Pillow is unavailable.
        """
        self._skin_textures.clear()
        self._textures_loaded = False

        if not _HAS_SKIN_LOADER:
            return

        skin_rel = _SKIN_PATHS.get(self.character)
        if not skin_rel:
            return

        full_path = ROOT / skin_rel
        if not full_path.exists():
            return

        try:
            from PIL import Image as _PILImage
            skin_img = _PILImage.open(str(full_path)).convert("RGBA")
            if skin_img.size != (64, 64):
                skin_img = skin_img.resize((64, 64), _PILImage.NEAREST)

            for part_key, (ux, uy, uw, uh) in _SKIN_UV.items():
                region = skin_img.crop((ux, uy, ux + uw, uy + uh))
                data = region.tobytes("raw", "RGBA")
                qimg = QImage(data, uw, uh, QImage.Format.Format_RGBA8888)
                self._skin_textures[part_key] = QPixmap.fromImage(qimg)

            self._textures_loaded = True
        except Exception:
            self._skin_textures.clear()
            self._textures_loaded = False

    def get_texture(self, part_key: str) -> QPixmap | None:
        """Get a cached skin texture for the given body part, or None."""
        if not self._textures_loaded:
            return None
        return self._skin_textures.get(part_key)

    # ══════════════════════════════════════════════════════════════════
    #  Textured block drawing primitives
    # ══════════════════════════════════════════════════════════════════

    def draw_block_textured(
        self, painter: QPainter,
        x: float, y: float, w: float, h: float,
        texture_key: str,
        edge: tuple[int, int, int] = (35, 35, 45),
    ) -> bool:
        """
        Draw a block filled with the cached skin texture for the given part.
        Returns True if the texture was found and drawn, False if falling
        back to flat color is needed.
        """
        tex = self.get_texture(texture_key)
        if tex is None or tex.isNull():
            return False

        ix, iy, iw, ih = int(x), int(y), int(w), int(h)
        self._collect_rect(ix, iy, iw, ih)

        # Scale the UV texture to fill the block (NEAREST for crisp pixel look)
        scaled = tex.scaled(
            iw, ih,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )

        painter.setPen(QPen(QColor(*edge), 1))
        painter.setBrush(QBrush(scaled))
        painter.drawRect(ix, iy, iw, ih)

        # ── 3-tone pixel bevel matching draw_block() ──
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255, 22)))
        painter.drawRect(ix + 1, iy + 1, max(1, iw - 2), max(1, int(ih * 0.12)))
        painter.setBrush(QBrush(QColor(255, 255, 255, 16)))
        painter.drawRect(ix + 1, iy + 1, 1, max(1, ih - 2))
        painter.setBrush(QBrush(QColor(0, 0, 0, 22)))
        painter.drawRect(ix + iw - 3, iy + 2, 2, max(1, ih - 3))
        painter.setBrush(QBrush(QColor(0, 0, 0, 26)))
        painter.drawRect(ix + 2, iy + ih - 2, max(1, iw - 4), 1)

        return True

    # ══════════════════════════════════════════════════════════════════
    #  Composite pose methods
    # ══════════════════════════════════════════════════════════════════

    def _draw_spin(self, painter: QPainter, frame: int, emotion: str) -> None:
        painter.translate(24, 37)
        painter.scale(max(0.35, abs(math.cos(frame * 0.9))), 1.0)
        painter.translate(-24, -37)
        self._draw_body(painter, frame, wave=True, emotion=emotion)

    def _draw_jump(self, painter: QPainter, frame: int, emotion: str) -> None:
        painter.translate(0, -10 - abs(math.sin(frame * 0.8)) * 6)
        self._draw_body(painter, frame, jump=True, emotion=emotion)

    def _draw_dance(self, painter: QPainter, frame: int, emotion: str) -> None:
        sway = math.sin(frame * 1.2) * 6
        bounce = abs(math.sin(frame * 2.0)) * 4
        painter.translate(sway, -bounce)
        painter.rotate(math.sin(frame * 1.2) * 4)
        self._draw_body(painter, frame, dance=True, emotion="excited")

    def _draw_combat_idle(self, painter: QPainter, frame: int, emotion: str) -> None:
        bob = abs(math.sin(frame * 0.6)) * 2
        painter.translate(0, -bob)
        self._draw_body(painter, frame, combat_idle=True, emotion="excited")

    def _draw_point(self, painter: QPainter, frame: int, emotion: str) -> None:
        painter.translate(2, -2)
        self._draw_body(painter, frame, point=True, emotion=emotion)

    def _draw_shrug(self, painter: QPainter, frame: int, emotion: str) -> None:
        tilt = math.sin(frame * 0.3) if frame > 0 else 6
        painter.rotate(tilt)
        self._draw_body(painter, frame, shrug=True, emotion="confused")

    def _draw_salute(self, painter: QPainter, frame: int, emotion: str) -> None:
        self._draw_body(painter, frame, salute=True, emotion=emotion)

    # ══════════════════════════════════════════════════════════════════
    #  Body drawing (the core)
    # ══════════════════════════════════════════════════════════════════

    def _draw_body(
        self, painter: QPainter, frame: int,
        walk: bool = False, run: bool = False, talk: bool = False,
        listen: bool = False, bored: bool = False, kick: bool = False,
        yawn: bool = False, sit: bool = False, wave: bool = False,
        jump: bool = False, sleep: bool = False, blush: bool = False,
        think: bool = False, cheer: bool = False, dance: bool = False,
        stretch: bool = False, confused: bool = False, surprised: bool = False,
        laugh: bool = False, combat_idle: bool = False, point: bool = False,
        shrug: bool = False, salute: bool = False,
        emotion: str = "neutral", micro_interaction: str = "",
    ) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        frame = frame % 8
        emotion = (emotion or "neutral").lower()
        p = self.profile
        db = self.draw_block
        dbt = self.draw_block_textured
        dbne = self.draw_block_no_edge
        dg = self.draw_glow

        # ── Helper: try texture first, fall back to flat color ──
        def dbx(x, y, w, h, texture_key, flat_color_key):
            """Draw block with texture if available, else flat color."""
            if not dbt(painter, x, y, w, h, texture_key):
                db(painter, x, y, w, h, p.get(flat_color_key))

        # ── Emotion-driven pose overrides ──
        explicit = walk or run or talk or listen or kick or yawn or sit or wave or jump or sleep or blush or think or cheer or dance or stretch or surprised or laugh or combat_idle or point or shrug or salute
        if not explicit:
            if emotion == "annoyed":
                bored = True
            if emotion == "sleepy":
                sleep = True
        if surprised:
            emotion = "surprised"
        if laugh:
            emotion = "happy"

        # ── Smoother bob, breath, transforms ──
        bob_scale = 2 if run else 1
        if emotion == "happy":
            bob_scale += 1
        elif emotion == "excited":
            bob_scale += 2
        elif emotion in ("sad", "sleepy"):
            bob_scale = 0

        idle_breath = 0
        if not sit and not walk and not run and not talk and not listen and not bored and not think and not sleep:
            idle_breath = abs(math.sin(frame * 0.25)) * 2
        # Smoother bob: use abs(sin) for softer rise/fall, phase offset for natural feel
        bob_raw = math.sin(frame * 0.9) * bob_scale
        bob_smooth = (bob_raw * 0.7 + math.sin(frame * 0.45) * bob_scale * 0.3)  # blend two frequencies
        bob = 0 if sleep or sit else int(bob_smooth) + idle_breath
        if bored or think:
            bob = 0

        # Subtle idle sway (barely perceptible weight shift)
        idle_sway = 0
        if not walk and not run and not sit and not sleep and not bored and not think:
            idle_sway = int(math.sin(frame * 0.35 + 1.2) * 1.2)  # ±1px sway

        is_ghost = self.character == "ghost_rider"

        # Pre-glow
        if is_ghost:
            flame_pulse = 0.5 + 0.5 * math.sin(frame * 1.2)
            glow_alpha = int(60 + 80 * flame_pulse)
            dg(painter, 8, 1, 32, 26, p.get("flame_mid", (255, 140, 40)), glow_alpha)
            dg(painter, 11, 4, 26, 20, p.get("flame_inner", (255, 200, 50)), glow_alpha - 20)

        # Emotion transforms
        if emotion == "happy":
            painter.translate(0, -abs(math.sin(frame * 0.9)) * 2)
        elif emotion == "excited" and not jump and not dance:
            painter.translate(0, -abs(math.sin(frame * 1.8)) * 3)
        elif emotion == "sad":
            painter.translate(0, 4)
            painter.rotate(-4)
        elif emotion == "flirty":
            painter.translate(math.sin(frame * 0.7) * 1.5, 0)
            painter.rotate(math.sin(frame * 0.7) * 3)
        elif emotion == "sleepy" and not sleep:
            painter.translate(0, 3)

        if dance:
            painter.translate(0, abs(math.sin(frame * 1.5)) * -2)

        if sleep:
            painter.translate(5, 18)
            painter.rotate(-12)
        elif listen:
            painter.rotate(-3)
        elif jump:
            painter.rotate(math.sin(frame * 0.7) * 4)
        elif micro_interaction == "look_around":
            painter.rotate(math.sin(frame * 1.5) * 6)
        elif micro_interaction == "tilt_curious":
            painter.rotate(12)
            painter.translate(2, 0)
        elif micro_interaction == "head_scratch":
            painter.rotate(5)
            painter.translate(1, -1)

        # Smoother leg/arm swing with phase offset for natural walk cycle
        leg_swing = 0
        arm_swing = 0
        if walk or run:
            swing_amp = 6 if run else 4
            leg_raw = math.sin(frame * 1.6)
            leg_smooth = leg_raw * 0.8 + math.sin(frame * 0.8 + 0.5) * 0.2
            leg_swing = int(leg_smooth * swing_amp)
            arm_swing = -leg_swing
        if kick:
            leg_swing = min(12, frame * 3)
        if sit or stretch:
            leg_swing = 0
            arm_swing = 0
        if dance:
            leg_swing = int(math.sin(frame * 1.5) * 3)
            arm_swing = int(math.sin(frame * 1.8) * 3)
        if cheer:
            leg_swing = 0
            arm_swing = 0

        # ── Shadow with ambient color tint (uses reusable gradient) ──
        painter.setPen(Qt.PenStyle.NoPen)
        shadow_alpha = 40 + (20 if is_ghost else 0)
        shadow_center_y = 66 if not sit and not stretch else 60
        shadow_tint = 0.15 + 0.08 * math.sin(frame * 0.4)
        # Update reusable gradient colors (avoids re-allocating QRadialGradient per frame)
        self._shadow_grad.setFocalPoint(24, shadow_center_y)
        self._shadow_grad.setCenter(24, shadow_center_y)
        self._shadow_grad.setRadius(22)
        inner_ct = QColor(int(20 * shadow_tint), int(30 * shadow_tint), int(60 * shadow_tint), min(160, shadow_alpha * 3))
        mid_ct = QColor(int(15 * shadow_tint), int(20 * shadow_tint), int(40 * shadow_tint), shadow_alpha)
        outer_ct = QColor(0, 0, 0, 0)
        self._shadow_grad.setColorAt(0.0, inner_ct)
        self._shadow_grad.setColorAt(0.5, mid_ct)
        self._shadow_grad.setColorAt(1.0, outer_ct)
        painter.setBrush(QBrush(self._shadow_grad))
        painter.drawEllipse(2, shadow_center_y - 8, 44, 16)

        # ── Position variables (cast to int for PyQt6 drawRect compatibility) ──
        leg_y = int(44 + bob)
        body_y = int(26 + bob + (8 if sit else 0) + (-2 if dance else 0))
        arm_y = body_y + 3
        head_y = int(7 + bob + (6 if sit else 0) + (-2 if dance else 0))
        if sleep:
            head_y += 3
        if stretch:
            head_y -= 1

        if combat_idle:
            db(painter, 14, leg_y + 2, 8, 16, p.get("pants"))
            db(painter, 26, leg_y + 2, 8, 16, p.get("pants"))
            db(painter, 13, leg_y + 17, 10, 5, p.get("boot"))
            db(painter, 25, leg_y + 17, 10, 5, p.get("boot"))
        elif salute:
            db(painter, 16, leg_y, 8, 18, p.get("pants"))
            db(painter, 24, leg_y, 8, 18, p.get("pants"))
            db(painter, 15, leg_y + 17, 10, 5, p.get("boot"))
            db(painter, 23, leg_y + 17, 10, 5, p.get("boot"))
        elif point or shrug:
            db(painter, 16, leg_y, 8, 18, p.get("pants"))
            db(painter, 24, leg_y, 8, 18, p.get("pants"))
            db(painter, 15, leg_y + 17, 10, 5, p.get("boot"))
            db(painter, 23, leg_y + 17, 10, 5, p.get("boot"))
        elif sit:
            db(painter, 15, 52, 8, 8, p.get("pants"))
            db(painter, 25, 52, 8, 8, p.get("pants"))
            db(painter, 14, 59, 10, 5, p.get("boot"))
            db(painter, 24, 59, 10, 5, p.get("boot"))
        elif stretch:
            db(painter, 13, 48, 8, 16, p.get("pants"))
            db(painter, 27, 46, 8, 18, p.get("pants"))
            db(painter, 12, 63, 10, 5, p.get("boot"))
            db(painter, 26, 63, 10, 5, p.get("boot"))
        else:
            dbx(15 + leg_swing + idle_sway, leg_y, 8, 18, "leg_front", "pants")
            dbx(25 - leg_swing - idle_sway, leg_y, 8, 18, "leg_front", "pants")
            dbx(14 + leg_swing + idle_sway, leg_y + 17, 10, 5, "leg_front", "boot")
            dbx(24 - leg_swing - idle_sway, leg_y + 17, 10, 5, "leg_front", "boot")

        # Ghost Rider chain
        if is_ghost:
            chain = p.get("chain_color", (175, 165, 150))
            chain_d = p.get("chain_dark", (120, 115, 105))
            chain_swing = int(math.sin(frame * 1.0) * 2)
            for cx in range(4):
                cy = 49 + chain_swing + (cx % 2) * 3 + int(math.sin(frame * 0.9 + cx) * 2)
                db(painter, 16 + cx * 4 + chain_swing, cy, 3, 2,
                   chain if cx % 2 == 0 else chain_d, shade=False)

        # ── Body (body_y already computed as int above) ──
        body_x = 13 + idle_sway // 2
        dbx(body_x, body_y, 22, 24 if not sit else 16, "body_front", "shirt")
        dbne(painter, body_x + 3, body_y + 2, 14, 5, p.get("shirt_dark"))

        if is_ghost:
            red = p.get("jacket_red", (160, 25, 25))
            dbne(painter, 13, body_y, 22, 2, red)
            seam = p.get("shirt_trim", (50, 45, 55))
            dbne(painter, 13, body_y + 10, 2, 12, seam)
            dbne(painter, 33, body_y + 10, 2, 12, seam)
            chain = p.get("chain_color", (175, 165, 150))
            chain_d = p.get("chain_dark", (120, 115, 105))
            chest_swing = int(math.sin(frame * 0.8) * 2)
            for cx in range(5):
                cy = body_y + 6 + (cx % 3) * 3 + int(math.sin(frame * 1.0 + cx) * 2)
                dbne(painter, 16 + cx * 3 + chest_swing, cy, 3, 2,
                     chain if cx % 2 == 0 else chain_d)
            spike = (60, 55, 60)
            dbne(painter, 9, body_y + 1, 4, 4, spike)
            dbne(painter, 35, body_y + 1, 4, 4, spike)
            dbne(painter, 8, body_y + 7, 3, 3, spike)
            dbne(painter, 37, body_y + 7, 3, 3, spike)

        if self.character in ("steve", "alex"):
            trim = p.get("shirt_trim", p["shirt"])
            dbne(painter, 13, body_y + 20, 22, 2, trim)

        # ── Arms ──
        arm_y = body_y + 3
        arm_sway_offset = idle_sway * 2  # weighted shift for arms
        if combat_idle:
            db(painter, 3, arm_y - 4, 8, 16, p.get("skin"))
            db(painter, 37, arm_y - 4, 8, 16, p.get("skin"))
            db(painter, 3, arm_y + 12, 10, 5, p.get("boot"))
            db(painter, 35, arm_y + 12, 10, 5, p.get("boot"))
        elif salute:
            db(painter, 4, arm_y + 2, 8, 17, p.get("skin"))
            db(painter, 36, arm_y - 9, 8, 10, p.get("skin"))
            dbne(painter, 37, arm_y - 12, 6, 4, p.get("skin"))
        elif point:
            db(painter, 4, arm_y + 2, 8, 17, p.get("skin"))
            db(painter, 38, arm_y, 12, 6, p.get("skin"))
            db(painter, 40, arm_y + 5, 6, 8, p.get("skin"))
        elif shrug:
            db(painter, 1, arm_y - 2, 8, 12, p.get("skin"))
            db(painter, 39, arm_y - 2, 8, 12, p.get("skin"))
            db(painter, 0, arm_y + 8, 9, 5, p.get("boot"))
            db(painter, 39, arm_y + 8, 9, 5, p.get("boot"))
        elif wave:
            db(painter, 4, arm_y + 1, 8, 18, p.get("skin"))
            db(painter, 36, arm_y - 11 - (frame % 3), 8, 18, p.get("skin"))
        elif listen:
            db(painter, 4, arm_y + 2, 8, 17, p.get("skin"))
            db(painter, 35, arm_y - 2, 8, 14, p.get("skin"))
            dbne(painter, 35, arm_y - 7, 6, 6, p.get("skin"))
        elif bored:
            db(painter, 5, arm_y + 9, 14, 6, p.get("skin"))
            db(painter, 29, arm_y + 9, 14, 6, p.get("skin"))
        elif think:
            db(painter, 4, arm_y + 2, 8, 17, p.get("skin"))
            db(painter, 36, arm_y - 2, 8, 10, p.get("skin"))
            dbne(painter, 36, arm_y - 5, 6, 6, p.get("skin"))
        elif confused:
            db(painter, 4, arm_y + 1, 8, 17, p.get("skin"))
            db(painter, 37, arm_y - 2, 8, 10, p.get("skin"))
            dbne(painter, 37, arm_y - 5, 6, 6, p.get("skin"))
        elif surprised:
            db(painter, 3, arm_y - 3, 8, 22, p.get("skin"))
            db(painter, 37, arm_y - 3, 8, 22, p.get("skin"))
        elif laugh:
            db(painter, 3, arm_y + 1, 8, 17, p.get("skin"))
            db(painter, 37, arm_y + 1, 8, 17, p.get("skin"))
            db(painter, 3, arm_y + 17, 9, 4, p.get("skin"))
            db(painter, 36, arm_y + 17, 9, 4, p.get("skin"))
        elif emotion == "flirty":
            db(painter, 4, arm_y + 4, 8, 17, p.get("skin"))
            db(painter, 36, arm_y + 10, 10, 6, p.get("skin"))
        elif emotion in ("happy", "excited") and not walk and not dance:
            lift = 3 if emotion == "happy" else 6
            db(painter, 4, arm_y - lift, 8, 19, p.get("skin"))
            db(painter, 36, arm_y - lift, 8, 19, p.get("skin"))
        elif cheer:
            db(painter, 2, arm_y - 7, 8, 22, p.get("skin"))
            db(painter, 38, arm_y - 7, 8, 22, p.get("skin"))
        elif dance:
            d_arm = int(math.sin(frame * 1.5) * 4)
            db(painter, 4, arm_y - 3 + d_arm, 8, 19, p.get("skin"))
            db(painter, 36, arm_y - 3 - d_arm, 8, 19, p.get("skin"))
        elif talk:
            talk_swing = int(math.sin(frame * 2.2) * 2)
            db(painter, 4 + talk_swing, arm_y, 8, 19, p.get("skin"))
            db(painter, 36 - talk_swing, arm_y, 8, 19, p.get("skin"))
        else:
            dbx(4 + arm_swing + arm_sway_offset, arm_y, 8, 19, "arm_front", "skin")
            dbx(36 - arm_swing + arm_sway_offset, arm_y, 8, 19, "arm_front", "skin")

        # Ghost Rider flame on arms
        if is_ghost:
            flame_intensity = 1.0
            if emotion in ("excited", "happy", "annoyed"):
                flame_intensity = 1.5
            elif emotion in ("sad", "sleepy"):
                flame_intensity = 0.3
            flame_c = p.get("flame_mid", (255, 140, 40))
            flame_alpha = int(80 * flame_intensity + 60 * flame_intensity * math.sin(frame * 1.5))
            dg(painter, 4, arm_y - 5, 8, 5, flame_c, flame_alpha)
            dg(painter, 36, arm_y - 5, 8, 5, flame_c, flame_alpha)
            dg(painter, 5, arm_y + 6, 6, 5, p.get("flame_outer", (200, 60, 10)), max(20, flame_alpha - 40))
            dg(painter, 37, arm_y + 6, 6, 5, p.get("flame_outer", (200, 60, 10)), max(20, flame_alpha - 40))

        # ── Head (head_y already computed as int above) ──
        head_sway = idle_sway // 2
        if is_ghost:
            skull_color = (200, 185, 165)
            if not dbt(painter, 12, head_y, 24, 20, "head_front"):
                db(painter, 12, head_y, 24, 20, skull_color)
            dbne(painter, 12, head_y, 24, 3, (180, 165, 145))
            crack = (160, 145, 125)
            dbne(painter, 20, head_y + 7, 3, 3, crack)
            dbne(painter, 26, head_y + 11, 3, 2, crack)
            dbne(painter, 12, head_y + 17, 24, 3, (185, 170, 150))
            # Skull fire hair
            flame_outer = p.get("flame_outer", (200, 60, 10))
            flame_mid = p.get("flame_mid", (255, 140, 40))
            flame_inner = p.get("flame_inner", (255, 200, 50))
            flame_tip = (255, 255, 150)
            flicker = int(math.sin(frame * 2.5) * 2)
            flicker2 = int(math.sin(frame * 3.2) * 2)
            dbne(painter, 10, head_y - 3 + flicker, 28, 5, flame_outer)
            dbne(painter, 12, head_y - 4 + flicker2, 24, 4, flame_mid)
            dbne(painter, 14, head_y - 5 + flicker, 20, 3, flame_inner)
            dbne(painter, 17, head_y - 6 + flicker2, 14, 2, flame_tip)
            # Side wisps
            for sx in (6, 34):
                wisp = int(math.sin(frame * 1.8 + sx) * 2)
                dbne(painter, sx, head_y + 2 + wisp, 5, 6, flame_outer)
                dbne(painter, sx + 1, head_y + 1 + wisp, 3, 4, flame_mid)
                dbne(painter, sx, head_y - 2 + wisp, 3, 3, flame_inner)
            # Skull horns
            dbne(painter, 12, head_y - 4, 5, 6, (190, 175, 155))
            dbne(painter, 31, head_y - 4, 5, 6, (190, 175, 155))
            dbne(painter, 13, head_y - 7, 4, 4, (210, 200, 180))
            dbne(painter, 32, head_y - 7, 4, 4, (210, 200, 180))
        else:
            db(painter, 12 + head_sway, head_y, 24, 20, p.get("skin"))
            dbne(painter, 12 + head_sway, head_y, 24, 5, p.get("hair"))

        # Hair sides (non-Ghost)
        if not is_ghost:
            dbne(painter, 9 + head_sway, head_y + 2, 5, 11, p.get("hair"))
            dbne(painter, 34 + head_sway, head_y + 2, 5, 11, p.get("hair"))

        # Character-specific accessories
        if self.character == "pink":
            dbne(painter, 7, head_y + 4, 5, 8, p.get("hair"))
            dbne(painter, 36, head_y + 5, 5, 7, p.get("hair"))
            if "ribbon_color" in p:
                dbne(painter, 18, head_y - 6, 10, 3, p["ribbon_color"])
                dbne(painter, 17, head_y - 4, 4, 3, p["ribbon_color"])
                dbne(painter, 27, head_y - 4, 4, 3, p["ribbon_color"])
            skirt = (p["shirt"][0] + 30, p["shirt"][1] + 20, p["shirt"][2] + 20)
            if not sit and not stretch:
                dbne(painter, 12, 46, 24, 6, skirt)
            elif sit:
                dbne(painter, 12, 54, 24, 5, skirt)
            dbne(painter, 14, 62, 2, 2, p.get("boot_trim", p["boot"]))
            dbne(painter, 32, 62, 2, 2, p.get("boot_trim", p["boot"]))
        elif self.character == "alex":
            pony = p.get("ponytail_color", p["hair"])
            band = p.get("ponytail_band_color", (160, 100, 30))
            dbne(painter, 35, head_y + 6, 5, 8, pony)
            dbne(painter, 36, head_y + 5, 4, 3, band)
            dbne(painter, 36, head_y + 12, 4, 5, pony)
            dbne(painter, 37, head_y + 16, 4, 4, pony)
            dbne(painter, 14, 62, 2, 2, p.get("boot_trim", p["boot"]))
            dbne(painter, 32, 62, 2, 2, p.get("boot_trim", p["boot"]))
        elif self.character == "steve":
            # ── Steve detail pass ──
            # Plain blocks read as "a default skin someone forgot to finish", so
            # he gets the classic teal/indigo palette plus the details that make
            # the Minecraft Steve recognisable: stubble, a collar, cuffs and a
            # belt.
            beard = p.get("beard", p.get("hair", (76, 54, 38)))
            trim = p.get("shirt_trim", p.get("shirt_dark", p["shirt"]))
            dbne(painter, 13, head_y + 16, 22, 3, beard)          # jaw stubble
            dbne(painter, 12, head_y + 9, 2, 8, beard)            # sideburn
            dbne(painter, 34, head_y + 9, 2, 8, beard)            # sideburn
            dbne(painter, 16, body_y + 1, 16, 2, trim)             # collar
            dbne(painter, 13, body_y + 21, 22, 2, p.get("boot", (58, 52, 48)))  # belt
            dbne(painter, 4, arm_y + 17, 8, 2, trim)               # left cuff
            dbne(painter, 36, arm_y + 17, 8, 2, trim)             # right cuff
            dbne(painter, 14, 62, 10, 4, p.get("boot_trim", p["boot"]))
            dbne(painter, 24, 62, 10, 4, p.get("boot_trim", p["boot"]))
        elif is_ghost:
            dbne(painter, 14, 60, 10, 4, p.get("boot_trim", p["boot"]))
            dbne(painter, 24, 60, 10, 4, p.get("boot_trim", p["boot"]))
            bs = (40, 38, 42)
            dbne(painter, 12, 59, 3, 3, bs)
            dbne(painter, 33, 59, 3, 3, bs)
            bf = p.get("flame_mid", (255, 140, 40))
            bf_flicker = int(math.sin(frame * 2.0) * 2)
            bf_alpha = 60 + (40 if walk or run else 0) + int(40 * math.sin(frame * 1.5))
            dg(painter, 13 + bf_flicker, 64, 5, 5, bf, bf_alpha)
            dg(painter, 30 - bf_flicker, 64, 5, 5, bf, bf_alpha)
            if walk or run:
                ta = int(30 + 30 * math.sin(frame * 2.0))
                tf = int(math.sin(frame * 1.5) * 3)
                tc = p.get("flame_outer", (200, 60, 10))
                dg(painter, 8 + tf, 65, 4, 3, tc, ta)
                dg(painter, 36 - tf, 65, 4, 3, tc, ta)

        # Horns and cherry
        if not is_ghost:
            horn = p.get("horn")
            cherry = p.get("cherry")
            if horn is not None:
                dbne(painter, 11 + head_sway, head_y - 4, 5, 5, horn)
                dbne(painter, 32 + head_sway, head_y - 4, 5, 5, horn)
            if cherry is not None:
                dbne(painter, 21 + head_sway // 2, head_y - 5, 6, 6, cherry)

        # ── Eyes ──
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(*p["eye_color"])))

        self._blink_counter += 1
        if not talk and not bored:
            if self._blink_counter >= self._blink_threshold:
                blink = True
                self._blink_threshold = random.randint(35, 100)
                self._blink_counter = 0
            else:
                blink = False
        else:
            blink = False
            self._blink_counter = 0

        # ── Eyebrows — adds facial definition the block face was missing.
        # Emotion drives the angle (sad = inner ends up, annoyed = flat, etc.).
        if not is_ghost and not sleep and emotion != "sleepy" and not blink \
                and micro_interaction != "blink_slow" and not bored:
            brow_c = p.get("hair", (70, 48, 38))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(
                max(0, brow_c[0] - 25), max(0, brow_c[1] - 25), max(0, brow_c[2] - 25))))
            if emotion in ("sad", "confused") or confused:
                painter.drawRect(16, head_y + 8, 4, 1)
                painter.drawRect(27, head_y + 9, 4, 1)
            elif emotion in ("annoyed", "excited") or surprised or laugh or cheer:
                painter.drawRect(16, head_y + 8, 4, 1)
                painter.drawRect(27, head_y + 8, 4, 1)
            else:
                painter.drawRect(16, head_y + 9, 4, 1)
                painter.drawRect(27, head_y + 9, 4, 1)

        if is_ghost:
            pulse = 0.7 + 0.3 * math.sin(frame * 2.0)
            r = int(p["eye_color"][0] * pulse + p.get("flame_inner", (255, 200, 50))[0] * (1 - pulse))
            g = int(p["eye_color"][1] * pulse + p.get("flame_inner", (255, 200, 50))[1] * (1 - pulse))
            b = int(p["eye_color"][2] * pulse + p.get("flame_inner", (255, 200, 50))[2] * (1 - pulse))
            painter.setBrush(QBrush(QColor(r, g, b)))
            painter.drawRect(16, head_y + 7, 5, 5)
            painter.drawRect(27, head_y + 7, 5, 5)
        elif sleep or emotion == "sleepy":
            painter.setPen(QPen(QColor(*p["eye_color"]), 1))
            painter.drawLine(16, head_y + 11, 20, head_y + 11)
            painter.drawLine(26, head_y + 11, 30, head_y + 11)
            painter.setPen(Qt.PenStyle.NoPen)
        elif bored:
            painter.drawRect(16, head_y + 11, 3, 1)
            painter.drawRect(27, head_y + 11, 3, 1)
        elif blush:
            painter.drawRect(16, head_y + 10, 3, 3)
            painter.drawRect(27, head_y + 10, 3, 3)
        elif blink:
            painter.setPen(QPen(QColor(*p["eye_color"]), 1))
            painter.drawLine(16, head_y + 11, 22, head_y + 11)
            painter.drawLine(24, head_y + 11, 30, head_y + 11)
            painter.setPen(Qt.PenStyle.NoPen)
        elif micro_interaction == "blink_slow":
            painter.setPen(QPen(QColor(*p["eye_color"]), 1))
            painter.drawLine(16, head_y + 11, 22, head_y + 11)
            painter.drawLine(24, head_y + 11, 30, head_y + 11)
            painter.setPen(Qt.PenStyle.NoPen)
        elif think:
            painter.drawRect(16, head_y + 9, 3, 3)
            painter.drawRect(27, head_y + 9, 3, 3)
        elif confused:
            painter.drawRect(16, head_y + 10, 3, 3)
            painter.drawRect(27, head_y + 9, 3, 4)
        elif surprised:
            painter.drawRect(15, head_y + 8, 5, 5)
            painter.drawRect(26, head_y + 8, 5, 5)
        elif laugh:
            painter.drawRect(16, head_y + 11, 4, 2)
            painter.drawRect(26, head_y + 11, 4, 2)
        elif emotion == "flirty":
            painter.drawRect(16, head_y + 10, 3, 3)
            painter.setPen(QPen(QColor(*p["eye_color"]), 1))
            painter.drawLine(27, head_y + 11, 32, head_y + 11)
            painter.setPen(Qt.PenStyle.NoPen)
        else:
            painter.drawRect(16, head_y + 10, 3, 3)
            painter.drawRect(27, head_y + 10, 3, 3)
            if "eye_highlight" in p:
                painter.setBrush(QBrush(QColor(*p["eye_highlight"])))
                painter.drawRect(17, head_y + 10, 1, 1)
                painter.drawRect(28, head_y + 10, 1, 1)
                painter.setBrush(QBrush(QColor(*p["eye_color"])))

        # Blush
        if blush or emotion == "flirty":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(*p["blush_color"], 120)))
            painter.drawEllipse(13, head_y + 14, 6, 4)
            painter.drawEllipse(28, head_y + 14, 6, 4)

        # Ghost Rider ember cheeks
        if is_ghost and emotion in ("annoyed", "excited", "happy"):
            ea = int(60 + 80 * math.sin(frame * 1.5))
            dg(painter, 13, head_y + 13, 6, 4, p.get("flame_outer", (200, 60, 10)), ea)
            dg(painter, 28, head_y + 13, 6, 4, p.get("flame_outer", (200, 60, 10)), ea)

        # ── Mouth ──
        if talk:
            mouth_h = 3 + int(abs(math.sin(frame * 1.8)) * 4)
        else:
            mouth_h = 2
        if yawn:
            mouth_h = 6
        if cheer:
            mouth_h = 4
        if blush:
            mouth_h = 1
        if laugh:
            mouth_h = 6
        if micro_interaction == "sigh":
            mouth_h = 3

        painter.setBrush(QBrush(QColor(75, 35, 45)))

        if is_ghost:
            mc = (30, 20, 15)
            painter.setBrush(QBrush(QColor(*mc)))
            if talk and frame % 2:
                painter.drawRect(20, head_y + 16, 10, 6)
            else:
                painter.drawRect(21, head_y + 16, 8, 4)
            painter.setPen(QPen(QColor(200, 185, 165), 1))
            for mx in (22, 25, 28):
                painter.drawLine(mx, head_y + 17, mx, head_y + 18)
            painter.setPen(Qt.PenStyle.NoPen)
        elif emotion == "happy" and not talk and not cheer and not laugh:
            painter.drawRect(21, head_y + 17, 8, 2)
            painter.setBrush(QBrush(QColor(*p["skin"])))
            painter.drawRect(22, head_y + 16, 6, 1)
        elif emotion == "sad" and not talk:
            painter.setPen(QPen(QColor(75, 35, 45), 1))
            painter.drawLine(22, head_y + 19, 25, head_y + 17)
            painter.drawLine(25, head_y + 17, 28, head_y + 19)
            painter.setPen(Qt.PenStyle.NoPen)
        elif laugh:
            painter.drawRect(20, head_y + 17, 10, 7)
            painter.setBrush(QBrush(QColor(*p["skin"])))
            painter.drawRect(22, head_y + 16, 6, 1)
        elif micro_interaction == "sigh":
            painter.drawRect(22, head_y + 16, 6, 4)
        elif cheer:
            painter.drawRect(21, head_y + 16, 8, 5)
        else:
            painter.drawRect(22, head_y + 17, 6, mouth_h)

        # Sigh breath
        if micro_interaction == "sigh":
            painter.setPen(QPen(QColor(200, 200, 220, 60), 1))
            for sx in range(3):
                painter.drawText(42 + sx * 4, head_y + 12 - sx * 2, "\u02e2")
            painter.setPen(Qt.PenStyle.NoPen)

        # Sleep Z's
        if sleep:
            painter.setPen(QPen(QColor(70, 90, 180), 1))
            painter.drawText(37, head_y - 5, "Z")
            painter.drawText(42, head_y - 10, "z")
            painter.setPen(Qt.PenStyle.NoPen)

        # Thinking bubble
        if think or confused:
            painter.setPen(QPen(QColor(200, 200, 200, 100), 1))
            painter.setBrush(QBrush(QColor(255, 255, 255, 80)))
            painter.drawEllipse(37, head_y - 6, 6, 6)
            painter.drawEllipse(42, head_y - 12, 8, 8)
            painter.setPen(Qt.PenStyle.NoPen)

        # Sparkle / fire effects
        show_fire = False
        fire_color = (255, 140, 40)
        if is_ghost:
            if emotion not in ("sad", "sleepy"):
                show_fire = True
                fire_color = p.get("sparkle_color", (255, 140, 40))
        elif self.character == "pink":
            if cheer or emotion == "excited" or dance:
                show_fire = True
                fire_color = p.get("sparkle_color", (255, 180, 230))
        elif cheer or emotion == "excited":
            show_fire = True
            fire_color = (255, 255, 150)

        if show_fire:
            painter.setPen(QPen(QColor(*fire_color), 1))
            for sx, sy in [(4, head_y - 6), (40, head_y - 4), (10, head_y - 14), (36, head_y - 16)]:
                if is_ghost and emotion != "excited" and not cheer:
                    painter.drawText(sx, sy + int(math.sin(frame * 3.0 + sx) * 4), "\U0001f525")
                else:
                    painter.drawText(sx, sy + int(math.sin(frame * 2.0 + sx) * 2), "*")
            painter.setPen(Qt.PenStyle.NoPen)

        # Ghost Rider fiery aura
        if is_ghost and emotion not in ("sad", "sleepy"):
            boost = 1.0
            if emotion in ("excited", "happy"):
                boost = 1.6
            elif emotion == "annoyed":
                boost = 1.3
            pulse = 0.5 + 0.5 * math.sin(frame * 0.7)
            alpha_mid = int((40 + 50 * pulse) * boost)
            alpha_inn = int((30 + 40 * pulse) * boost)
            dg(painter, 3, head_y - 6, 42, 35, p.get("flame_outer", (200, 60, 10)), alpha_mid)
            dg(painter, 6, head_y - 3, 36, 28, p.get("flame_mid", (255, 140, 40)), alpha_inn)
            dg(painter, 9, head_y, 30, 22, p.get("flame_inner", (255, 200, 50)), alpha_inn - 10)
            ground_alpha = int((20 + 30 * math.sin(frame * 0.5)) * boost)
            dg(painter, 4, 64, 40, 6, p.get("flame_outer", (200, 60, 10)), ground_alpha)
            dg(painter, 10, 66, 28, 4, p.get("flame_mid", (255, 140, 40)), max(5, ground_alpha // 2))

    # ══════════════════════════════════════════════════════════════════
    #  Boredom indicator
    # ══════════════════════════════════════════════════════════════════

    def _draw_boredom_indicator(self, painter: QPainter, frame: int, tier: int) -> None:
        if tier <= 1:
            return
        pulse = abs(math.sin(frame * 0.5 + tier))
        alpha = int(80 + 60 * pulse)

        if tier == 2:
            painter.setPen(QPen(QColor(180, 180, 190, alpha), 1))
            for i in range(3):
                painter.drawPoint(18 + i * 4, 70 + int(math.sin(frame * 0.7 + i) * 2))
            painter.setPen(Qt.PenStyle.NoPen)
        elif tier == 3:
            painter.setPen(QPen(QColor(180, 180, 190, alpha), 1))
            painter.drawText(38, 68 + int(math.sin(frame * 0.3) * 2), "\u2234")
            painter.setPen(Qt.PenStyle.NoPen)
        elif tier >= 4:
            painter.setPen(QPen(QColor(198, 58, 72, alpha), 1))
            painter.drawText(38, 68 + int(math.sin(frame * 0.5) * 3), "?")
            painter.setPen(Qt.PenStyle.NoPen)
