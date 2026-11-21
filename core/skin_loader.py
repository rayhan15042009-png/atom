"""
Minecraft Skin Loader — Phase 58

Parses standard Minecraft 64×64 PNG skin sheets into per-face textures
(16×16 QImages for 3D rendering) and dominant-color 2D palettes.

UV Map reference: s-plan.txt Minecraft Skin Format section.
"""
from pathlib import Path
from typing import Optional
from collections import Counter

from core.logger import get_logger

log = get_logger('skin')

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    from PyQt6.QtGui import QImage, QColor
    HAS_QT = True
except ImportError:
    HAS_QT = False


def legacy_to_modern(img):
    """Convert a 64×32 legacy skin to the 64×64 (1.8) layout.

    The two layouts agree for the top half — head, hat, body, right arm and
    right leg are at identical coordinates — but the legacy format has **no
    left arm or left leg**: renderers mirrored the right-hand limbs instead. The
    modern layout has real slots for them at (32,48) and (16,48), filled by
    mirroring the corresponding right-hand region.

    This matters because every UV lookup in this module (and in the glTF model)
    uses the modern layout. Previously a legacy skin was simply stretched from
    64×32 to 64×64, which put every texture at the wrong place — the character
    rendered with unrelated pixels for a face.
    """
    modern = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    modern.paste(img, (0, 0))
    flip = Image.Transpose.FLIP_LEFT_RIGHT
    # Left leg ← mirrored right leg, left arm ← mirrored right arm.
    modern.paste(img.crop((0, 16, 16, 32)).transpose(flip), (16, 48))
    modern.paste(img.crop((40, 16, 56, 32)).transpose(flip), (32, 48))
    return modern


# ── UV Map: Standard Minecraft Java skin (64×64 PNG) ──
# Format: (x, y, width, height) in pixels
UV_MAP = {
    "head": {
        "front": (8, 8, 8, 8), "back": (24, 8, 8, 8),
        "left": (0, 8, 8, 8), "right": (16, 8, 8, 8),
        "top": (8, 0, 8, 8), "bottom": (16, 0, 8, 8),
    },
    "hat": {
        "front": (40, 8, 8, 8),
    },
    "body": {
        "front": (20, 20, 8, 12), "back": (32, 20, 8, 12),
        "left": (16, 20, 4, 12), "right": (28, 20, 4, 12),
        "top": (20, 16, 8, 4), "bottom": (28, 16, 8, 4),
    },
    "arm_r": {
        "front": (44, 20, 4, 12), "back": (52, 20, 4, 12),
        "left": (40, 20, 4, 12), "right": (48, 20, 4, 12),
        "top": (44, 16, 4, 4), "bottom": (48, 16, 4, 4),
    },
    "arm_l": {
        "front": (36, 52, 4, 12), "back": (44, 52, 4, 12),
        "left": (32, 52, 4, 12), "right": (40, 52, 4, 12),
        "top": (36, 48, 4, 4), "bottom": (40, 48, 4, 4),
    },
    "leg_r": {
        "front": (4, 20, 4, 12), "back": (12, 20, 4, 12),
        "left": (0, 20, 4, 12), "right": (8, 20, 4, 12),
        "top": (4, 16, 4, 4), "bottom": (8, 16, 4, 4),
    },
    "leg_l": {
        "front": (20, 52, 4, 12), "back": (28, 52, 4, 12),
        "left": (16, 52, 4, 12), "right": (24, 52, 4, 12),
        "top": (20, 48, 4, 4), "bottom": (24, 48, 4, 4),
    },
}


class MinecraftSkinLoader:
    """Load and parse a Minecraft skin PNG into per-face textures and color palettes."""

    def __init__(self, skin_path: str, hd_resize: bool = True):
        if not HAS_PIL:
            raise ImportError("Pillow is required for skin loading. Install: pip install Pillow")

        self._path = Path(skin_path)
        if not self._path.exists():
            raise FileNotFoundError(f"Skin file not found: {skin_path}")

        self._img = Image.open(skin_path).convert("RGBA")

        if self._img.size == (64, 32):
            # Legacy layout — remap, do not stretch. Only 64×32 is treated this
            # way; larger sizes (128×128 HD skins) still downscale below.
            log.info("Converting legacy 64×32 skin to the 1.8 layout: %s", skin_path)
            self._img = legacy_to_modern(self._img)
        elif self._img.size != (64, 64):
            # Resize HD skins (e.g., 128×128) to 64×64
            if hd_resize:
                log.info("Resizing skin %s from %s to 64×64", skin_path, self._img.size)
                self._img = self._img.resize((64, 64), Image.NEAREST)
            else:
                raise ValueError(f"Skin must be 64×64. Got {self._img.size}. Set hd_resize=True to auto-resize.")

        self._hd_resize = hd_resize

    @property
    def pil_image(self):
        return self._img

    def crop_region(self, part: str, face: str = "front") -> Optional[Image.Image]:
        """Crop a specific part-face region from the skin."""
        if part not in UV_MAP:
            raise ValueError(f"Unknown part: {part}. Valid: {list(UV_MAP.keys())}")
        if face not in UV_MAP[part]:
            raise ValueError(f"Unknown face '{face}' for part '{part}'. Valid: {list(UV_MAP[part].keys())}")

        x, y, w, h = UV_MAP[part][face]
        return self._img.crop((x, y, x + w, y + h))

    def get_face_texture(self, part: str, face: str = "front", size: int = 16):
        """Get a specific face region scaled to size×size as a QImage (for 3D texturing)."""
        if not HAS_QT:
            raise ImportError("PyQt6 is required for QImage output. Install: pip install PyQt6")

        cropped = self.crop_region(part, face)
        scaled = cropped.resize((size, size), Image.NEAREST)
        data = scaled.tobytes("raw", "RGBA")
        return QImage(data, size, size, QImage.Format.Format_RGBA8888).copy()

    def get_dominant_color(self, part: str, face: str = "front") -> Optional["QColor"]:
        """Get the most common non-transparent pixel color in a region."""
        if not HAS_QT:
            return None

        region = self.crop_region(part, face)
        # get_flattened_data replaces deprecated getdata() in Pillow 12.1+
        try:
            pixels = [(r, g, b) for r, g, b, a in region.get_flattened_data() if a > 128]
        except AttributeError:
            pixels = [(r, g, b) for r, g, b, a in region.getdata() if a > 128]
        if not pixels:
            return QColor(128, 128, 128)
        r, g, b = Counter(pixels).most_common(1)[0][0]
        return QColor(r, g, b)

    def get_2d_palette(self) -> dict:
        """Get a 6-key 2D color palette from the skin for sprite rendering."""
        return {
            "head_color": self.get_dominant_color("head", "front"),
            "hair_color": self.get_dominant_color("head", "top"),
            "body_color": self.get_dominant_color("body", "front"),
            "arm_color": self.get_dominant_color("arm_r", "front"),
            "leg_color": self.get_dominant_color("leg_r", "front"),
            "boot_color": self._get_boot_color(),
        }

    def _get_boot_color(self):
        """Get dominant color from the bottom 3 rows of the right leg."""
        region = self.crop_region("leg_r", "front")
        # Bottom 3 rows of the 12-row leg
        boot_region = region.crop((0, region.height - 3, region.width, region.height))
        # get_flattened_data replaces deprecated getdata() in Pillow 12.1+
        try:
            pixels = [(r, g, b) for r, g, b, a in boot_region.get_flattened_data() if a > 128]
        except AttributeError:
            pixels = [(r, g, b) for r, g, b, a in boot_region.getdata() if a > 128]
        if not pixels:
            return self.get_dominant_color("leg_r", "front")
        # Counter imported at module level
        # pixels already filtered for alpha > 128
        r, g, b = Counter(pixels).most_common(1)[0][0]
        return QColor(r, g, b)

    def get_head_preview(self, size: int = 16):
        """Get the head front face as a QPixmap-friendly QImage for previews."""
        return self.get_face_texture("head", "front", size)


def load_skin_for_character(config, character: str) -> Optional[MinecraftSkinLoader]:
    """Load the skin for a given character name from config paths."""
    skin_key = f"skin_{character}"
    if not hasattr(config, skin_key):
        return None

    skin_path = getattr(config, skin_key)
    if not skin_path:
        return None

    hd_resize = getattr(config, 'skin_hd_resize', True)
    full_path = Path(__file__).parent.parent / skin_path
    if not full_path.exists():
        log.debug("Skin file not found for %s: %s", character, full_path)
        return None

    try:
        return MinecraftSkinLoader(str(full_path), hd_resize=hd_resize)
    except Exception as exc:
        log.warning("Failed to load skin for %s: %s", character, exc)
        return None
