"""
Generate detailed 64x64 Minecraft-layout skin atlases for every character.

Why this exists
---------------
The shipped atlases were flat. ``atom_skin.png`` was a 64x32 legacy skin with
**19 usable colours**, most of them solid fills: one orange block for the head,
solid grey blocks for the torso and limbs. ``steve_skin.png`` was also 64x32, and
its face region was painted with *hair* colours, so the 2D sprite gave Steve a
near-black face. ``gamergirl.png`` had the same defect. Mapped onto the glTF
character, a flat atlas produces a flat, unlit-looking cube.

This tool paints each character at the modern 64x64 layout, with:

* a real **face** — brow ridge, an eye band whose texels line up with the
  glowing eye quads the renderer overlays, nose shadow and mouth,
* a **hair (second) layer** so the 3D renderer can show hair volume instead of
  painting the top of a cube,
* **baked ambient occlusion** — darker one-pixel borders, under-collar and
  under-belt shading, so the surface reads as volumetric even before lighting,
* **material break-up** — panel lines, seams, stitching, cuff trim, boot soles,
  chest straps, a chain, headphones, flames, and a deterministic micro-noise
  pass that gives the shader's bump mapping something to bite on.

Every region keeps its base tone in the majority, because
``core.skin_loader.MinecraftSkinLoader.get_2d_palette`` takes the *most common*
colour of a region as that body part's colour for the 2D sprite. If a base tone
stopped being the mode, the 2D character would silently change colour. Each
character therefore declares an ``EXPECTED_PALETTE`` and the generator fails
loudly on drift.

Characters
----------
``atom`` keeps its own hand-written template set and palette: that atlas was
already regenerated and is byte-for-byte unchanged by this tool. The other five
share one semantic template *family* (see ``B_*``) — the same face construction,
AO borders, trims, cuffs and boots — and differ through a per-character palette
plus small identity **overrides** (Steve's full sleeves, Alex's ponytail, Pink's
ribbon, Gamer Girl's headphones, Ghost Rider's skull/flames/chain).

Two shipped palettes are corrected, in the same way Atom's was: where the face
region was dominated by *hair* texels, the region now resolves to a real skin
tone instead of a hair colour. Those two are marked ``CORRECTED`` below.

Usage::

    python tools/generate_skins.py              # write + verify every atlas
    python tools/generate_skins.py --only pink  # just one character
    python tools/generate_skins.py --check      # verify the shipped files, write nothing
    python tools/generate_skins.py --report     # per-region detail stats
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SKIN_DIR = ROOT / "assets" / "skins"

RGB = tuple[int, int, int]
Palette = dict[str, "RGB | None"]
Faces = dict[str, dict[str, list[str]]]


# ══════════════════════════════════════════════════════════════════════════════
# Atom — unchanged
# ══════════════════════════════════════════════════════════════════════════════
# Continuing the published atom design: charcoal suit, orange hair, cyan eyes
# and trim, orange-brown boots. Shading tones are derived but written out so the
# templates stay readable.

ATOM_PALETTE: Palette = {
    ".": (217, 94, 0),      # hair
    "#": (150, 60, 0),      # hair, root/shadow
    "H": (246, 150, 52),    # hair, light
    "h": (255, 198, 120),   # hair, highlight
    "f": (58, 58, 62),      # face
    "d": (38, 38, 42),      # face, shadow
    "p": (20, 20, 23),      # face, deep shadow
    "l": (80, 80, 86),      # face, light
    "u": (112, 112, 120),   # face, highlight
    "e": (0, 190, 235),     # eye / visor
    "E": (185, 244, 255),   # eye, bright core
    "y": (0, 100, 145),     # eye, rim
    "s": (38, 38, 38),      # suit
    "z": (24, 24, 26),      # suit, shadow
    "Z": (12, 12, 14),      # suit, deep shadow
    "S": (62, 62, 68),      # suit, light
    "w": (96, 96, 104),     # suit, highlight
    "t": (0, 154, 209),     # trim (blue)
    "T": (96, 214, 255),    # trim, light
    "r": (0, 88, 128),      # trim, dark
    "b": (186, 100, 2),     # boot / bracer
    "B": (222, 138, 34),    # boot, light
    "c": (140, 70, 0),      # boot, sole
    "-": None,              # transparent
}

A_HEAD_TOP = [
    "#..HH..#",
    "#.HHHH.#",
    "..HhhH..",
    ".HhhhhH.",
    ".HhhhhH.",
    "..HhhH..",
    "#..HH..#",
    "##....##",
]

A_HEAD_BOTTOM = [
    "pppppppp",
    "p######p",
    "p#ffff#p",
    "p#fppf#p",
    "p#fppf#p",
    "p#ffff#p",
    "p######p",
    "pppppppp",
]

A_HEAD_RIGHT = [
    "#.HHHH.#",
    ".HHhhHH.",
    ".HhhhhH.",
    "..HhhH..",
    "dddfffdd",
    "dfffffdd",
    "dffddffd",
    "pddddddp",
]

# The face. Eye texels sit at x=1..2 and x=5..6, rows 3..4 — the same footprint
# as the glowing eye quads in ui/renderer_3d.py, so the glow lands on the eyes.
A_HEAD_FRONT = [
    "...HH...",
    "#......#",
    "dffffffd",   # brow ridge
    "fEeffeEf",   # eye band
    "fyelleyf",   # lower lid + nose bridge
    "ffluulff",   # cheek / nose highlight
    "ffffdfff",   # nose tip
    "pffdddfp",   # mouth + jaw shading
]

A_HEAD_BACK = [
    "........",
    "..HHHH..",
    ".HhhhhH.",
    ".HhhhhH.",
    ".HhhhhH.",
    ".HhhhhH.",
    "..HHHH..",
    "#......#",
]

A_HAT_TOP = [
    "........",
    "..HhhH..",
    ".HhhhhH.",
    ".HhhhhH.",
    ".HhhhhH.",
    ".HhhhhH.",
    "..HhhH..",
    "........",
]

A_HAT_BOTTOM = ["--------"] * 8

A_HAT_RIGHT = [
    "#......#",
    ".HHhhHH.",
    ".HhhhhH.",
    "..HhhH..",
    "--------",
    "--------",
    "--------",
    "--------",
]

A_HAT_FRONT = [
    "...HH...",
    "#......#",
    "--------",
    "--------",
    "--------",
    "--------",
    "--------",
    "--------",
]

A_HAT_BACK = [
    "........",
    "..HHHH..",
    ".HhhhhH.",
    ".HhhhhH.",
    ".HhhhhH.",
    ".HhhhhH.",
    "..HHHH..",
    "#......#",
]

A_BODY_TOP = [
    "zzzzzzzz",
    "zssssssz",
    "zsSSSSsz",
    "zzzzzzzz",
]

A_BODY_BOTTOM = [
    "ZZZZZZZZ",
    "ZzzzzzzZ",
    "ZzzzzzzZ",
    "ZZZZZZZZ",
]

A_BODY_FRONT = [
    "ZZzzzzZZ",
    "Zzsssszz",
    "zssttssz",
    "ssSssSss",
    "zsswwssz",
    "zsswwssz",
    "tssttsst",
    "zsSwSszz",
    "zssssssz",
    "zssssssz",
    "ZzsssszZ",
    "ZZzsszzZ",
]

A_BODY_BACK = [
    "ZZzzzzZZ",
    "ZzsssszZ",
    "zssssssz",
    "sszsszss",
    "zssssssz",
    "zsswwssz",
    "zssssssz",
    "zssssssz",
    "zssssssz",
    "zzsssszz",
    "ZzzsszzZ",
    "ZZZzzzZZ",
]

A_BODY_SIDE = [
    "Zzzz",
    "zssz",
    "zsSz",
    "zssz",
    "zssz",
    "zssz",
    "zssz",
    "zssz",
    "zssz",
    "zssz",
    "ZzzZ",
    "ZZZZ",
]

A_ARM_TOP = [
    "zzzz",
    "zssz",
    "zsSz",
    "zzzz",
]

A_ARM_BOTTOM = [
    "ZZZZ",
    "ZzzZ",
    "ZzzZ",
    "ZZZZ",
]

A_ARM_FRONT = [
    "zzzz",
    "sssz",
    "sssz",
    "sSsz",
    "sssz",
    "sssz",
    "sssz",
    "tttt",
    "bbBb",
    "cbbb",
    "cbbb",
    "ccbc",
]

A_ARM_RIGHT = [
    "zzzz",
    "zsss",
    "zSss",
    "zsss",
    "zsss",
    "zsss",
    "zsss",
    "tttt",
    "bBbb",
    "bbcb",
    "bbcb",
    "cbbc",
]

A_ARM_BACK = [
    "zzzz",
    "zsss",
    "zsss",
    "zsss",
    "zsss",
    "zsss",
    "zsss",
    "tttt",
    "bbbb",
    "bbbb",
    "cbbc",
    "cccc",
]

A_LEG_TOP = [
    "zzzz",
    "zssz",
    "zsSz",
    "zzzz",
]

A_LEG_BOTTOM = [
    "cccc",
    "cbbc",
    "cbbc",
    "cccc",
]

A_LEG_FRONT = [
    "zzzz",
    "sssz",
    "sssz",
    "sSsz",
    "sssz",
    "sssz",
    "sssz",
    "tttt",
    "bbbb",
    "bBbb",
    "bbbc",
    "cccc",
]

A_LEG_RIGHT = [
    "zzzz",
    "zsss",
    "zSss",
    "zsss",
    "zsss",
    "zsss",
    "zsss",
    "tttt",
    "bbbb",
    "bbbB",
    "bbbc",
    "cccc",
]

A_LEG_BACK = [
    "zzzz",
    "zsss",
    "zsss",
    "zsss",
    "zsss",
    "zsss",
    "zsss",
    "tttt",
    "bbbb",
    "bbbb",
    "cbbc",
    "cccc",
]

ATOM_FACES: Faces = {
    "head": {
        "top": A_HEAD_TOP, "bottom": A_HEAD_BOTTOM, "right": A_HEAD_RIGHT,
        "front": A_HEAD_FRONT, "back": A_HEAD_BACK,
    },
    "hat": {
        "top": A_HAT_TOP, "bottom": A_HAT_BOTTOM, "right": A_HAT_RIGHT,
        "front": A_HAT_FRONT, "back": A_HAT_BACK,
    },
    "body": {
        "top": A_BODY_TOP, "bottom": A_BODY_BOTTOM, "right": A_BODY_SIDE,
        "front": A_BODY_FRONT, "back": A_BODY_BACK,
    },
    "arm_r": {
        "top": A_ARM_TOP, "bottom": A_ARM_BOTTOM, "right": A_ARM_RIGHT,
        "front": A_ARM_FRONT, "back": A_ARM_BACK,
    },
    "arm_l": {
        "top": A_ARM_TOP, "bottom": A_ARM_BOTTOM, "right": A_ARM_RIGHT,
        "front": A_ARM_FRONT, "back": A_ARM_BACK,
    },
    "leg_r": {
        "top": A_LEG_TOP, "bottom": A_LEG_BOTTOM, "right": A_LEG_RIGHT,
        "front": A_LEG_FRONT, "back": A_LEG_BACK,
    },
    "leg_l": {
        "top": A_LEG_TOP, "bottom": A_LEG_BOTTOM, "right": A_LEG_RIGHT,
        "front": A_LEG_FRONT, "back": A_LEG_BACK,
    },
}

ATOM_EXPECTED: dict[str, RGB] = {
    "head_color": (58, 58, 62),    # the face, not the orange hair
    "hair_color": (217, 94, 0),
    "body_color": (38, 38, 38),
    "arm_color": (38, 38, 38),
    "leg_color": (38, 38, 38),
    "boot_color": (186, 100, 2),
}


# ══════════════════════════════════════════════════════════════════════════════
# Atlas layout (standard Java 1.8 / 64x64) — coordinates only
# ══════════════════════════════════════════════════════════════════════════════
# ``(x, y)`` of each face's top-left corner. Face sizes come from the template:
# head/hat faces are 8x8, torso faces 8x12 (front/back) or 4x12 (sides), limbs
# 4x12, and the top/bottom caps are 8x4 (torso) or 4x4 (limbs).
#
# A missing face is mirrored from "right": that is how the modern layout stores
# the left half of a symmetric part. Limbs work the same way — their inner faces
# are the mirrored outer texture — which keeps both sides identical, and keeps
# Atom's already-shipped atlas byte-for-byte reproducible.

LAYOUT: dict[str, dict[str, tuple[int, int]]] = {
    "head": {
        "top": (8, 0), "bottom": (16, 0), "right": (0, 8),
        "front": (8, 8), "left": (16, 8), "back": (24, 8),
    },
    "hat": {
        "top": (40, 0), "bottom": (48, 0), "right": (32, 8),
        "front": (40, 8), "left": (48, 8), "back": (56, 8),
    },
    "body": {
        "top": (20, 16), "bottom": (28, 16), "right": (16, 20),
        "front": (20, 20), "left": (28, 20), "back": (32, 20),
    },
    "arm_r": {
        "top": (44, 16), "bottom": (48, 16), "right": (40, 20),
        "front": (44, 20), "left": (48, 20), "back": (52, 20),
    },
    "arm_l": {
        "top": (36, 48), "bottom": (40, 48), "right": (32, 52),
        "front": (36, 52), "left": (40, 52), "back": (44, 52),
    },
    "leg_r": {
        "top": (4, 16), "bottom": (8, 16), "right": (0, 20),
        "front": (4, 20), "left": (8, 20), "back": (12, 20),
    },
    "leg_l": {
        "top": (20, 48), "bottom": (24, 48), "right": (16, 52),
        "front": (20, 52), "left": (24, 52), "back": (28, 52),
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# Shared template family (Steve / Alex / Pink / Gamer Girl / Ghost Rider)
# ══════════════════════════════════════════════════════════════════════════════
# Semantic keys, so one pattern can be re-skinned per character:
#
#   hair      a base   b shadow  c light   d highlight
#   face      f base   e shadow  g deep    h light   i highlight
#
# ``f`` carries the face because that is the tone the shared templates use most
# in a head region — ``get_2d_palette`` reports the *mode*, so the base tone has
# to be the most common texel or the 2D sprite changes colour.
#   eyes      j dark   k bright  l rim
#   top       m base   n shadow  o deep    p light   q highlight
#   trim      r base   s light   t dark
#   pants     u base   v shadow  w light
#   boots     x base   y light   z sole
#   extra     A..C bone/tone steps, D/E chain, F..H flame, K accessory accent
#   -         transparent

B_HEAD_TOP = [
    "bbbbbbbb",
    "baaaaaab",
    "baaaaaab",
    "baacaaab",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "bbbbbbbb",
]

B_HEAD_BOTTOM = [
    "bbbbbbbb",
    "baaaaaab",
    "baaeeaab",
    "baeeeeab",
    "baeeeeab",
    "baaeeaab",
    "baaaaaab",
    "bbbbbbbb",
]

B_HEAD_RIGHT = [
    "bbbbbbbb",
    "baaaaaab",
    "baacaaab",
    "baaaaaab",
    "ffffhfff",
    "ffggefff",
    "ffffhhff",
    "gffffffg",
]

B_HEAD_FRONT = [
    "aaaccaaa",   # fringe
    "baaaaaab",   # hairline
    "ffffffff",   # brow ridge
    "fkjffjkf",   # eye band — matches the renderer's glow quads
    "fljeejlf",   # lower lid
    "ffhiihff",   # cheek / nose highlight
    "ffffgfff",   # nose tip
    "gffgggfg",   # mouth + jaw shading
]

B_HEAD_BACK = [
    "bbbbbbbb",
    "baaaaaab",
    "baaaaaab",
    "baacaaab",
    "baaaaaab",
    "baaaaaab",
    "bbbbbbbb",
    "bbbbbbbb",
]

B_HAT_TOP = [
    "bbbbbbbb",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "bbbbbbbb",
]

B_HAT_BOTTOM = ["--------"] * 8

B_HAT_RIGHT = [
    "bbbbbbbb",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "--------",
    "--------",
    "--------",
    "--------",
]

B_HAT_FRONT = [
    "aaaccaaa",
    "baaaaaab",
    "--------",
    "--------",
    "--------",
    "--------",
    "--------",
    "--------",
]

B_HAT_BACK = [
    "bbbbbbbb",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "bbbbbbbb",
]

B_BODY_TOP = [
    "nnnnnnnn",
    "nmmmmmmn",
    "nmmmmmmn",
    "nnnnnnnn",
]

B_BODY_BOTTOM = [
    "oooooooo",
    "onnnnnno",
    "onnnnnno",
    "oooooooo",
]

B_BODY_SIDE = [
    "nnnn",
    "nmmn",
    "nmmn",
    "nmmn",
    "nmmn",
    "nmmn",
    "nmmn",
    "nmmn",
    "nmmn",
    "nmmn",
    "nnnn",
    "oooo",
]

B_BODY_FRONT = [
    "oonnnnoo",   # collar
    "onmmmmno",
    "onmmmmon",
    "mmmmmmmm",   # chest
    "nmmqqmmn",
    "nmmqqmmn",
    "nmmmmmmn",
    "nmmmmmmn",
    "rmmmmmmr",   # side trim
    "rrrrrrrr",   # belt
    "onmmmmno",
    "oonnnnoo",
]

B_BODY_BACK = [
    "oonnnnoo",
    "onmmmmno",
    "onmmmmno",
    "mmmmmmmm",
    "nmmmmmmn",
    "nmmmmmmn",
    "nmmmmmmn",
    "nmmmmmmn",
    "nmmmmmmn",
    "nmmmmmmn",
    "onmmmmno",
    "oonnnnoo",
]

B_ARM_TOP = [
    "nnnn",
    "nmmn",
    "nmmn",
    "nnnn",
]

B_ARM_BOTTOM = [
    "gggg",
    "gffg",
    "gffg",
    "gggg",
]

# Short sleeve, cuff, then bare forearm.
B_ARM_FRONT = [
    "nnnn",
    "mmmn",
    "mmmn",
    "mmpn",
    "mmmn",
    "mmmn",
    "mmmn",
    "mmmn",
    "rrrr",
    "tfff",
    "ffff",
    "geeg",
]

B_ARM_OUTER = [
    "nnnn",
    "mmmn",
    "mmmn",
    "mmmn",
    "mmmn",
    "mmmn",
    "mmmn",
    "mmmn",
    "rrrr",
    "tfff",
    "ffff",
    "gggg",
]

B_ARM_BACK = [
    "nnnn",
    "nmmm",
    "nmmm",
    "nmmm",
    "nmmm",
    "nmmm",
    "nmmm",
    "nmmm",
    "rrrr",
    "ffff",
    "ffff",
    "gggg",
]

B_LEG_TOP = [
    "vvvv",
    "vuuv",
    "vuuv",
    "vvvv",
]

B_LEG_BOTTOM = [
    "zzzz",
    "zxxz",
    "zxxz",
    "zzzz",
]

# Trousers, boot seam, boot with a lit toe cap, dark sole.
B_LEG_FRONT = [
    "vvvv",
    "uuuv",
    "uuuv",
    "uuwv",
    "uuuv",
    "uuuv",
    "uuuv",
    "uuuv",
    "zzzz",
    "xxxy",
    "xxxx",
    "zzzz",
]

B_LEG_OUTER = [
    "vvvv",
    "vuuu",
    "vuuu",
    "vuuu",
    "vuuu",
    "vuuu",
    "vuuu",
    "vuuu",
    "zzzz",
    "xxxx",
    "xxxy",
    "zzzz",
]

B_LEG_BACK = [
    "vvvv",
    "vuuu",
    "vuuu",
    "vuuu",
    "vuuu",
    "vuuu",
    "vuuu",
    "vuuu",
    "zzzz",
    "xxxx",
    "xxxy",
    "zzzz",
]


def _base_faces() -> Faces:
    """A fresh copy of the shared template family."""
    return {
        "head": {
            "top": B_HEAD_TOP, "bottom": B_HEAD_BOTTOM, "right": B_HEAD_RIGHT,
            "front": B_HEAD_FRONT, "back": B_HEAD_BACK,
        },
        "hat": {
            "top": B_HAT_TOP, "bottom": B_HAT_BOTTOM, "right": B_HAT_RIGHT,
            "front": B_HAT_FRONT, "back": B_HAT_BACK,
        },
        "body": {
            "top": B_BODY_TOP, "bottom": B_BODY_BOTTOM, "right": B_BODY_SIDE,
            "front": B_BODY_FRONT, "back": B_BODY_BACK,
        },
        "arm_r": {
            "top": B_ARM_TOP, "bottom": B_ARM_BOTTOM, "right": B_ARM_OUTER,
            "front": B_ARM_FRONT, "back": B_ARM_BACK,
        },
        "arm_l": {
            "top": B_ARM_TOP, "bottom": B_ARM_BOTTOM, "right": B_ARM_OUTER,
            "front": B_ARM_FRONT, "back": B_ARM_BACK,
        },
        "leg_r": {
            "top": B_LEG_TOP, "bottom": B_LEG_BOTTOM, "right": B_LEG_OUTER,
            "front": B_LEG_FRONT, "back": B_LEG_BACK,
        },
        "leg_l": {
            "top": B_LEG_TOP, "bottom": B_LEG_BOTTOM, "right": B_LEG_OUTER,
            "front": B_LEG_FRONT, "back": B_LEG_BACK,
        },
    }


# ── Steve: full sleeves on a classic blocky frame ──────────────────────────
S_ARM_FRONT = [
    "nnnn",
    "mmmn",
    "mmmn",
    "mmpn",
    "mmmn",
    "mmmn",
    "mmmn",
    "mmmn",
    "rrrr",
    "mmmn",
    "mmpn",
    "nnnn",
]
S_ARM_OUTER = [
    "nnnn",
    "mmmn",
    "mmmn",
    "mmmn",
    "mmmn",
    "mmmn",
    "mmmn",
    "mmmn",
    "rrrr",
    "mmmn",
    "mmmn",
    "nnnn",
]

S_ARM_BACK = [
    "nnnn",
    "nmmm",
    "nmmm",
    "nmmm",
    "nmmm",
    "nmmm",
    "nmmm",
    "nmmm",
    "rrrr",
    "nmmm",
    "nmmm",
    "nnnn",
]

# ── Alex: short sleeves, bare forearms, long side hair + ponytail ──────────
X_ARM_FRONT = [
    "nnnn",
    "mmmn",
    "mmmn",
    "rrrr",
    "tfff",
    "fhff",
    "ffhe",
    "ffff",
    "fhff",
    "ffff",
    "ffhe",
    "geeg",
]
X_ARM_OUTER = [
    "nnnn",
    "mmmn",
    "mmmn",
    "rrrr",
    "tfff",
    "fhff",
    "ffff",
    "ffhe",
    "ffff",
    "fhff",
    "ffff",
    "geeg",
]

X_ARM_BACK = [
    "nnnn",
    "nmmm",
    "nmmm",
    "rrrr",
    "tfff",
    "ffff",
    "ffff",
    "ffhe",
    "ffff",
    "ffff",
    "ffhe",
    "geeg",
]

X_HAT_FRONT = [
    "aaaccaaa",
    "baaaaaab",
    "--------",
    "--------",
    "--------",
    "--------",
    "--------",
    "--------",
]
X_HAT_TOP = [
    "bbbbbbbb",
    "baaaaaab",
    "baacaaab",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "bbbbbbbb",
]
# Hair falls past the jaw on both sides, with the ponytail band at the nape.
X_HAT_RIGHT = [
    "bbbbbbbb",
    "baaaaaab",
    "baaaaaab",
    "aaaaaaaa",
    "abbbbbba",
    "aabbbbaa",
    "aaabbaaa",
    "--------",
]
X_HAT_BACK = [
    "bbbbbbbb",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "bbbKKbbb",
    "bbKKKKbb",
]

# ── Pink: pastel hair shell with a ribbon on the crown ─────────────────────
P_HAT_TOP = [
    "aaaaaaaa",
    "aaccccaa",
    "aacrrcaa",
    "aacrrcaa",
    "aaccccaa",
    "aaaaaaaa",
    "aaaaaaaa",
    "bbbbbbbb",
]
P_HAT_RIGHT = [
    "aaaaaaaa",
    "aaccccaa",
    "aaaaaaaa",
    "aaaaaaaa",
    "aabbbbaa",
    "--------",
    "--------",
    "--------",
]
P_HAT_BACK = [
    "aaaaaaaa",
    "aaccccaa",
    "aaaaaaaa",
    "aaaaaaaa",
    "aaaaaaaa",
    "aaaaaaaa",
    "abbbbbba",
    "bbbbbbbb",
]

# ── Gamer Girl: headphone band with ear cups ──────────────────────────────
G_HAT_TOP = [
    "nnnnnnnn",
    "nmmmmmmn",
    "nmmmmmmn",
    "nmmmmmmn",
    "nmmmmmmn",
    "nmmmmmmn",
    "nmmmmmmn",
    "nnnnnnnn",
]
G_HAT_FRONT = [
    "nnnnnnnn",
    "baaaaaab",
    "--------",
    "--------",
    "--------",
    "--------",
    "--------",
    "--------",
]
G_HAT_RIGHT = [
    "--------",
    "--------",
    "nnnnnnnn",
    "nmmppmmn",
    "nmpyypmn",
    "nmpyypmn",
    "nmmppmmn",
    "nnnnnnnn",
]
G_HAT_BACK = [
    "nnnnnnnn",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "baaaaaab",
    "bbbbbbbb",
]

# ── Ghost Rider: skull, flames on the hair layer, chained leathers ─────────
R_HEAD_FRONT = [
    "ffffffff",
    "feeeeeef",   # temple shading
    "fjjffjjf",   # eye sockets
    "fkjffjkf",   # sockets lit from within
    "ffffffff",
    "fffjjfff",   # nasal cavity
    "ffffffff",
    "fejjjjef",   # clenched jaw
]

R_HAT_TOP = [
    "FFGGHHGG",
    "FGHHHHGF",
    "GHHHHHHG",
    "GHHHHHHG",
    "FGHHHHGF",
    "FFGGHHGG",
    "FGGHHGGF",
    "FFGGGGFF",
]
R_HAT_FRONT = [
    "FFGHHGFF",
    "FGGHHGGF",
    "--------",
    "--------",
    "--------",
    "--------",
    "--------",
    "--------",
]
R_HAT_RIGHT = [
    "FFGGHHGG",
    "FGHHHHGF",
    "FFGHHGFF",
    "FFGGGGFF",
    "FFFGGFFF",
    "FFFGGFFF",
    "FFFFGFFF",
    "FFFFFFFF",
]
R_HAT_BACK = [
    "FFGGGGFF",
    "FGHHHHGF",
    "FGHHHHGF",
    "FFGHHGFF",
    "FFGGGGFF",
    "FFGGGGFF",
    "FFFGGFFF",
    "FFFFFFFF",
]

R_BODY_FRONT = [
    "oonnnnoo",
    "onmmmmno",
    "onmDmmno",
    "mmmDDmmm",
    "nmmmDDmn",
    "nmmmmDDn",
    "rmmmmDDm",
    "nmmmmmDn",
    "nmmmmmmn",
    "rrrrrrrr",
    "onmmmmno",
    "oonnnnoo",
]

R_ARM_FRONT = [
    "nnnn",
    "mmmn",
    "mmmn",
    "mmpn",
    "mmmn",
    "mmmn",
    "mmmn",
    "mmmn",
    "rrrr",
    "mmmn",
    "fffg",
    "gggg",
]
R_ARM_OUTER = [
    "nnnn",
    "mmmn",
    "mmmn",
    "mmmn",
    "mmmn",
    "mmmn",
    "mmmn",
    "mmmn",
    "rrrr",
    "mmmn",
    "mmmn",
    "gggg",
]

R_ARM_BACK = [
    "nnnn",
    "nmmm",
    "nmmm",
    "nmmm",
    "nmmm",
    "nmmm",
    "nmmm",
    "nmmm",
    "rrrr",
    "nmmm",
    "ffff",
    "gggg",
]


def _faces(overrides: Faces | None = None) -> Faces:
    """Shared family with per-character identity overrides merged in."""
    faces = _base_faces()
    for part, part_faces in (overrides or {}).items():
        faces[part].update(part_faces)
    return faces


# ══════════════════════════════════════════════════════════════════════════════
# Character specifications
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SkinSpec:
    key: str
    title: str
    filename: str
    palette: Palette
    faces: Faces
    expected: dict[str, RGB]
    note: str = ""


STEVE_PALETTE: Palette = {
    # hair — near-black brown, as the shipped atlas had it
    "a": (12, 8, 2), "b": (6, 4, 1), "c": (34, 24, 12), "d": (58, 42, 22),
    # face — CORRECTED: the 64x32 atlas painted the whole face region with hair
    "f": (210, 170, 130), "e": (186, 146, 106), "g": (150, 114, 80),
    "h": (226, 190, 152), "i": (240, 210, 180),
    "j": (48, 52, 86), "k": (235, 235, 240), "l": (168, 132, 98),
    # shirt — the shipped teal
    "m": (0, 133, 122), "n": (0, 108, 99), "o": (0, 84, 77),
    "p": (0, 158, 145), "q": (20, 186, 172),
    "r": (0, 96, 88), "s": (0, 120, 110), "t": (0, 66, 60),
    # trousers
    "u": (29, 22, 70), "v": (20, 15, 50), "w": (46, 36, 100),
    # boots — shipped dark indigo, the profile's grey as the toe cap
    "x": (29, 23, 62), "y": (60, 55, 48), "z": (16, 12, 36),
    "K": (60, 55, 48),
    "-": None,
}

ALEX_PALETTE: Palette = {
    "a": (210, 123, 44), "b": (176, 98, 32), "c": (232, 150, 72), "d": (250, 178, 104),
    "f": (227, 190, 152), "e": (202, 166, 130), "g": (166, 132, 100),
    "h": (240, 208, 176), "i": (250, 226, 200),
    "j": (60, 64, 84), "k": (238, 238, 242), "l": (176, 144, 112),
    "m": (71, 116, 70), "n": (55, 94, 55), "o": (40, 72, 40),
    "p": (92, 140, 90), "q": (110, 160, 108),
    "r": (120, 175, 115), "s": (150, 200, 145), "t": (55, 94, 55),
    "u": (47, 36, 29), "v": (33, 25, 20), "w": (66, 52, 42),
    "x": (49, 49, 52), "y": (68, 68, 72), "z": (30, 30, 32),
    "K": (160, 100, 30),
    "-": None,
}

PINK_PALETTE: Palette = {
    "a": (250, 239, 225), "b": (226, 212, 196), "c": (255, 248, 240), "d": (255, 255, 255),
    "f": (255, 223, 194), "e": (236, 200, 170), "g": (205, 168, 140),
    "h": (255, 238, 214), "i": (255, 247, 232),
    "j": (196, 64, 132), "k": (255, 255, 255), "l": (168, 128, 148),
    "m": (252, 249, 240), "n": (228, 222, 210), "o": (198, 192, 180),
    "p": (255, 255, 252), "q": (255, 255, 255),
    "r": (255, 130, 180), "s": (255, 170, 205), "t": (210, 80, 140),
    "u": (251, 161, 211), "v": (226, 132, 186), "w": (255, 190, 228),
    "x": (251, 161, 211), "y": (255, 200, 235), "z": (210, 120, 175),
    "K": (255, 100, 150),
    "-": None,
}

GAMERGIRL_PALETTE: Palette = {
    "a": (222, 143, 84), "b": (186, 112, 62), "c": (245, 175, 116), "d": (255, 205, 155),
    # face — CORRECTED: the shipped atlas let orange hair dominate this region
    "f": (247, 190, 151), "e": (222, 166, 130), "g": (186, 134, 102),
    "h": (255, 212, 178), "i": (255, 230, 205),
    "j": (58, 58, 92), "k": (245, 245, 250), "l": (176, 130, 100),
    "m": (20, 32, 72), "n": (14, 22, 52), "o": (8, 14, 36),
    "p": (34, 50, 100), "q": (52, 72, 130),
    "r": (16, 20, 59), "s": (34, 50, 100), "t": (8, 14, 36),
    "u": (34, 22, 17), "v": (24, 15, 11), "w": (56, 38, 30),
    "x": (34, 22, 17), "y": (205, 194, 181), "z": (18, 11, 8),
    "K": (205, 194, 181),
    "-": None,
}

GHOST_PALETTE: Palette = {
    # the cranium reads as bone; the fire lives on the hair layer
    "a": (211, 197, 172), "b": (176, 162, 138), "c": (230, 215, 187), "d": (245, 234, 214),
    "f": (230, 215, 187), "e": (211, 197, 172), "g": (170, 158, 136),
    "h": (245, 234, 214), "i": (255, 248, 232),
    "j": (0, 0, 0), "k": (255, 96, 16), "l": (120, 40, 12),
    "m": (0, 0, 0), "n": (14, 14, 16), "o": (30, 28, 32),
    "p": (44, 42, 48), "q": (70, 66, 74),
    "r": (165, 28, 28), "s": (210, 50, 50), "t": (110, 18, 18),
    "u": (0, 0, 0), "v": (17, 17, 19), "w": (40, 40, 46),
    "x": (255, 37, 26), "y": (255, 120, 40), "z": (150, 20, 12),
    "A": (245, 234, 214), "B": (211, 197, 172), "C": (170, 158, 136),
    "D": (175, 165, 150), "E": (120, 115, 105),
    "F": (200, 60, 10), "G": (255, 140, 40), "H": (255, 200, 50),
    "K": (255, 140, 40),
    "-": None,
}


SKINS: dict[str, SkinSpec] = {
    "atom": SkinSpec(
        key="atom",
        title="Atom",
        filename="atom_skin.png",
        palette=ATOM_PALETTE,
        faces=ATOM_FACES,
        expected=ATOM_EXPECTED,
        note="charcoal suit, orange hair, cyan visor",
    ),
    "steve": SkinSpec(
        key="steve",
        title="Steve",
        filename="steve_skin.png",
        palette=STEVE_PALETTE,
        faces=_faces({
            "arm_r": {"front": S_ARM_FRONT, "right": S_ARM_OUTER, "back": S_ARM_BACK},
            "arm_l": {"front": S_ARM_FRONT, "right": S_ARM_OUTER, "back": S_ARM_BACK},
        }),
        expected={
            "head_color": (210, 170, 130),   # CORRECTED: was (14, 9, 3), a hair tone
            "hair_color": (12, 8, 2),
            "body_color": (0, 133, 122),
            "arm_color": (0, 133, 122),      # full sleeves; unused by the 2D sprite
            "leg_color": (29, 22, 70),
            "boot_color": (29, 23, 62),
        },
        note="classic blocky frame, full teal sleeves, denim legs",
    ),
    "alex": SkinSpec(
        key="alex",
        title="Alex",
        filename="alex_skin.png",
        palette=ALEX_PALETTE,
        faces=_faces({
            "hat": {"front": X_HAT_FRONT, "top": X_HAT_TOP,
                    "right": X_HAT_RIGHT, "back": X_HAT_BACK},
            "arm_r": {"front": X_ARM_FRONT, "right": X_ARM_OUTER, "back": X_ARM_BACK},
            "arm_l": {"front": X_ARM_FRONT, "right": X_ARM_OUTER, "back": X_ARM_BACK},
        }),
        expected={
            "head_color": (227, 190, 152),
            "hair_color": (210, 123, 44),
            "body_color": (71, 116, 70),
            "arm_color": (227, 190, 152),    # short sleeves, bare forearms
            "leg_color": (47, 36, 29),
            "boot_color": (49, 49, 52),
        },
        note="orange hair + ponytail, short-sleeved green tunic",
    ),
    "pink": SkinSpec(
        key="pink",
        title="Pink",
        filename="pink_skin.png",
        palette=PINK_PALETTE,
        faces=_faces({
            "hat": {"top": P_HAT_TOP, "right": P_HAT_RIGHT, "back": P_HAT_BACK},
        }),
        expected={
            "head_color": (255, 223, 194),
            "hair_color": (250, 239, 225),
            "body_color": (252, 249, 240),
            "arm_color": (252, 249, 240),
            "leg_color": (251, 161, 211),
            "boot_color": (251, 161, 211),
        },
        note="pastel outfit, ribbon on the crown",
    ),
    "gamergirl": SkinSpec(
        key="gamergirl",
        title="Gamer Girl",
        filename="gamergirl.png",
        palette=GAMERGIRL_PALETTE,
        faces=_faces({
            "hat": {"top": G_HAT_TOP, "front": G_HAT_FRONT,
                    "right": G_HAT_RIGHT, "back": G_HAT_BACK},
        }),
        expected={
            "head_color": (247, 190, 151),   # CORRECTED: was (222, 143, 84), a hair tone
            "hair_color": (222, 143, 84),
            "body_color": (20, 32, 72),
            "arm_color": (20, 32, 72),
            "leg_color": (34, 22, 17),
            "boot_color": (34, 22, 17),
        },
        note="navy outfit, orange hair, headphone band",
    ),
    "ghost_rider": SkinSpec(
        key="ghost_rider",
        title="Ghost Rider",
        filename="ghost_skin.png",
        palette=GHOST_PALETTE,
        faces=_faces({
            "head": {"front": R_HEAD_FRONT},
            "hat": {"top": R_HAT_TOP, "front": R_HAT_FRONT,
                    "right": R_HAT_RIGHT, "back": R_HAT_BACK},
            "body": {"front": R_BODY_FRONT},
            "arm_r": {"front": R_ARM_FRONT, "right": R_ARM_OUTER, "back": R_ARM_BACK},
            "arm_l": {"front": R_ARM_FRONT, "right": R_ARM_OUTER, "back": R_ARM_BACK},
        }),
        expected={
            "head_color": (230, 215, 187),
            "hair_color": (211, 197, 172),
            "body_color": (0, 0, 0),
            "arm_color": (0, 0, 0),
            "leg_color": (0, 0, 0),
            "boot_color": (255, 37, 26),
        },
        note="bone skull, flame hair layer, red-trimmed leathers",
    ),
}

# Backwards-compatible module aliases (the shipped atom atlas is already correct).
EXPECTED_PALETTE = ATOM_EXPECTED


# ── Micro-noise ────────────────────────────────────────────────────────────
# Applied in a 1-in-4 stipple so each base tone keeps ~75% of its texels — well
# above any neighbouring shade, which is what keeps the 2D palette extraction
# picking the intended colour.
NOISE_STEP = 4
NOISE_AMPLITUDE = 7


def _mirror(rows: list[str]) -> list[str]:
    return [row[::-1] for row in rows]


def _validate(spec: SkinSpec) -> None:
    """Fail loudly on a mistyped template rather than painting garbage."""
    known = set(spec.palette)
    for part, part_faces in spec.faces.items():
        if part not in LAYOUT:
            raise SystemExit(f"{spec.key}: unknown part {part!r}")
        for face, rows in part_faces.items():
            if face not in LAYOUT[part]:
                raise SystemExit(f"{spec.key}: unknown face {part}.{face}")
            where = f"{spec.key}:{part}.{face}"
            widths = {len(r) for r in rows}
            if len(widths) != 1:
                raise SystemExit(f"template {where} has ragged rows: {sorted(widths)}")
            unknown = {c for r in rows for c in r} - known
            if unknown:
                raise SystemExit(f"template {where} uses unknown keys {unknown}")

    # Every region the renderer samples must be painted, and only the hair layer
    # may carry cut-out texels (the body is drawn without alpha test).
    for part, faces in LAYOUT.items():
        for face in faces:
            rows = spec.faces.get(part, {}).get(face)
            if rows is None:
                if face == "left" and spec.faces.get(part, {}).get("right") is not None:
                    continue          # mirrored
                raise SystemExit(f"{spec.key}: region {part}.{face} has no template")
            if part != "hat" and any("-" in r for r in rows):
                raise SystemExit(
                    f"{spec.key}: region {part}.{face} is transparent; the body "
                    "is drawn without an alpha test"
                )

    hat_front = spec.faces.get("hat", {}).get("front")
    if hat_front is not None and not any("-" in r for r in hat_front):
        raise SystemExit(f"{spec.key}: the hair layer has no transparent texels")


def _stipple(x: int, y: int) -> bool:
    """Deterministic 1-in-N stipple so the noise is reproducible across runs."""
    return ((x * 73_856_093) ^ (y * 19_349_663)) % NOISE_STEP == 0


def _jitter(x: int, y: int) -> int:
    h = (x * 73_856_093) ^ (y * 19_349_663)
    h = (h ^ (h >> 13)) * 1_274_126_177
    return ((h ^ (h >> 16)) % (2 * NOISE_AMPLITUDE + 1)) - NOISE_AMPLITUDE


def _build(spec: SkinSpec) -> Image.Image:
    """Paint one atlas from its templates."""
    _validate(spec)
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    px = img.load()

    for part, faces in LAYOUT.items():
        for face, (ox, oy) in faces.items():
            rows = spec.faces.get(part, {}).get(face)
            if rows is None:
                rows = _mirror(spec.faces[part]["right"])

            for ry, row in enumerate(rows):
                for rx, key in enumerate(row):
                    colour = spec.palette[key]
                    if colour is None:
                        continue
                    r, g, b = colour
                    if _stipple(rx + ox, ry + oy):
                        j = _jitter(rx + ox, ry + oy)
                        r, g, b = (max(0, min(255, c + j)) for c in (r, g, b))
                    px[ox + rx, oy + ry] = (r, g, b, 255)

    return img


def build(key: str = "atom") -> Image.Image:
    """Paint the atlas for ``key`` (``"atom"`` by default)."""
    if key not in SKINS:
        raise KeyError(f"unknown skin {key!r}; known: {sorted(SKINS)}")
    return _build(SKINS[key])


def dominant(img: Image.Image, box: tuple[int, int, int, int]) -> "RGB | None":
    """Most common opaque colour in a region — what ``get_2d_palette`` returns."""
    x, y, w, h = box
    pixels = [
        img.getpixel((xx, yy))[:3]
        for yy in range(y, y + h)
        for xx in range(x, x + w)
        if img.getpixel((xx, yy))[3] > 128
    ]
    if not pixels:
        return None
    return Counter(pixels).most_common(1)[0][0]


def region_stats(img: Image.Image) -> dict[str, dict]:
    """Colour count, alpha coverage and resolved mode per region."""
    from core.skin_loader import UV_MAP

    stats: dict[str, dict] = {}
    for part, faces in UV_MAP.items():
        for face, box in faces.items():
            x, y, w, h = box
            colours = {
                img.getpixel((xx, yy))[:3]
                for yy in range(y, y + h)
                for xx in range(x, x + w)
                if img.getpixel((xx, yy))[3] > 128
            }
            stats[f"{part}_{face}"] = {
                "colours": len(colours),
                "mode": dominant(img, box),
            }
    stats["_unique_total"] = {
        "colours": len({img.getpixel((x, y)) for y in range(64) for x in range(64)}),
        "mode": None,
    }
    return stats


def resolved_palette(img: Image.Image, key: str = "atom") -> dict[str, "RGB | None"]:
    """What ``get_2d_palette`` will return once this atlas is shipped."""
    from core.skin_loader import MinecraftSkinLoader

    tmp = SKIN_DIR / f".{key}.tmp.png"
    img.convert("RGBA").save(tmp)
    try:
        loader = MinecraftSkinLoader(str(tmp), hd_resize=True)
        pal = loader.get_2d_palette()
        return {
            k: (v.red(), v.green(), v.blue()) if v else None
            for k, v in pal.items()
        }
    finally:
        tmp.unlink(missing_ok=True)


def _process(spec: SkinSpec, *, write: bool, report: bool) -> tuple[list[str], Image.Image]:
    """Build, verify and (optionally) save one atlas.

    Returns the drifted palette keys and the freshly built image (so a
    ``--check`` run does not have to build the same atlas twice).
    """
    img = _build(spec)
    out = SKIN_DIR / spec.filename

    if write:
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out)
        print(f"{spec.key:12s} wrote {out.name} ({img.width}x{img.height})")
    else:
        print(f"{spec.key:12s} {out.name} ({img.width}x{img.height})")

    stats = region_stats(img)
    if report:
        for name, info in sorted(stats.items()):
            if name.startswith("_"):
                continue
            print(f"    {name:22s} {info['colours']:3d} colours  mode {info['mode']}")
    print(f"    unique colours in atlas: {stats['_unique_total']['colours']}")

    got = resolved_palette(img, spec.key)
    failures = []
    for name, expected in spec.expected.items():
        actual = got.get(name)
        mark = "ok " if actual == expected else "!! "
        print(f"    {mark}{name:12s} {actual}  (expected {expected})")
        if actual != expected:
            failures.append(name)

    if failures:
        print(f"    PALETTE DRIFT: {', '.join(failures)}")
        print("    The 2D sprite colours come from these values — fix the template.")
    return failures, img


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--only", action="append", default=[],
                    help="generate only these characters (default: all)")
    ap.add_argument("--report", action="store_true",
                    help="print per-region colour counts and modes")
    ap.add_argument("--check", action="store_true",
                    help="verify the shipped atlases match the templates; write nothing")
    args = ap.parse_args()

    keys = args.only or list(SKINS)
    unknown = [k for k in keys if k not in SKINS]
    if unknown:
        ap.error(f"unknown character(s) {unknown}; known: {sorted(SKINS)}")

    drift: list[str] = []
    stale: list[str] = []
    for key in keys:
        spec = SKINS[key]
        failures, built = _process(spec, write=not args.check, report=args.report)
        drift.extend(f"{key}.{name}" for name in failures)

        if args.check:
            out = SKIN_DIR / spec.filename
            if not out.exists():
                stale.append(f"{key}: {out.name} is missing")
                continue
            shipped = Image.open(str(out)).convert("RGBA")
            if shipped.size != built.size or shipped.tobytes() != built.tobytes():
                stale.append(f"{key}: {out.name} does not match its templates")

    if stale:
        print("\nSTALE ATLASES:")
        for line in stale:
            print(f"  {line}")
        return 1

    if drift:
        return 1

    print("\nall atlases match their templates; every 2D palette is stable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
