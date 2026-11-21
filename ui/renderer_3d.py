"""
3D Block Character Renderer — PyOpenGL + QOpenGLWidget.

VBO-based cube rendering with procedural pixel textures,
GLSL per-pixel 3-light Blinn-Phong shading, and full
Minecraft-style block character with all 5 character profiles,
animation states, emotion effects, and Ghost Rider support.
"""

from __future__ import annotations

import math
import struct
import ctypes
import json
import base64
import time as _time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.logger import get_logger

log = get_logger("renderer_3d")

ROOT = Path(__file__).resolve().parent.parent

# ── OpenGL lazy import ──
_OPENGL_OK = False
_OPENGL_IMPORTED = False

QOpenGLWidget = None
QApplication = None
Qt = None
QTimer = None
pyqtSignal = None
QSurfaceFormat = None
QColor = None
QImage = None
QPainter = None
gl = None
glu = None


def _import_opengl() -> bool:
    global _OPENGL_OK, _OPENGL_IMPORTED
    global QOpenGLWidget, QApplication, Qt, QTimer, pyqtSignal
    global QSurfaceFormat, QColor, QImage, QPainter, gl, glu
    if _OPENGL_IMPORTED:
        return _OPENGL_OK
    _OPENGL_IMPORTED = True
    try:
        from PyQt6.QtOpenGLWidgets import QOpenGLWidget
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import Qt, QTimer, pyqtSignal
        from PyQt6.QtGui import QSurfaceFormat, QColor, QImage, QPainter
        import OpenGL.GL as gl
        import OpenGL.GLU as glu
        _OPENGL_OK = True
    except (ImportError, RuntimeError):
        _OPENGL_OK = False
    return _OPENGL_OK


# ── Constants ──
_TEX_SIZE = 64  # Minecraft skin resolution
_VERTEX_STRIDE = 32  # 8 floats × 4 bytes
_OUTLINE_COLOR = (20, 18, 22)

# ── Minecraft 64×64 skin UV pixel regions: (x, y, w, h) ──
# Standard Java Edition skin layout (64×64)
_SKIN_REGIONS = {
    # Head (8×8 on skin, centered at 8,8 in 64px grid)
    "head_front":  (8,  8,  8, 8),
    "head_back":   (24, 8,  8, 8),
    "head_top":    (8,  0,  8, 8),
    "head_bottom": (16, 0,  8, 8),
    "head_right":  (0,  8,  8, 8),
    "head_left":   (16, 8,  8, 8),
    # Hat/overlay
    "hat_front":   (40, 8,  8, 8),
    "hat_back":    (56, 8,  8, 8),
    "hat_top":     (40, 0,  8, 8),
    "hat_bottom":  (48, 0,  8, 8),
    "hat_right":   (32, 8,  8, 8),
    "hat_left":    (48, 8,  8, 8),
    # Body/Torso (8×12)
    "body_front":  (20, 20, 8, 12),
    "body_back":   (32, 20, 8, 12),
    "body_top":    (20, 16, 8, 4),
    "body_bottom": (28, 16, 8, 4),
    "body_right":  (16, 20, 4, 12),
    "body_left":   (24, 20, 4, 12),
    # Right Arm (4×12)
    "arm_r_front": (40, 20, 4, 12),
    "arm_r_back":  (52, 20, 4, 12),
    "arm_r_top":   (40, 16, 4, 4),
    "arm_r_bottom":(48, 16, 4, 4),
    "arm_r_right": (36, 20, 4, 12),
    "arm_r_left":  (44, 20, 4, 12),
    # Left Arm (4×12) — in newer skins, mirrored from right arm
    "arm_l_front": (36, 48, 4, 12),
    "arm_l_back":  (48, 48, 4, 12),
    "arm_l_top":   (36, 44, 4, 4),
    "arm_l_bottom":(44, 44, 4, 4),
    "arm_l_right": (32, 48, 4, 12),
    "arm_l_left":  (40, 48, 4, 12),
    # Right Leg (4×12)
    "leg_r_front": (4,  20, 4, 12),
    "leg_r_back":  (12, 20, 4, 12),
    "leg_r_top":   (4,  16, 4, 4),
    "leg_r_bottom":(8,  16, 4, 4),
    "leg_r_right": (0,  20, 4, 12),
    "leg_r_left":  (8,  20, 4, 12),
    # Left Leg (4×12)
    "leg_l_front": (20, 48, 4, 12),
    "leg_l_back":  (28, 48, 4, 12),
    "leg_l_top":   (20, 44, 4, 4),
    "leg_l_bottom":(24, 44, 4, 4),
    "leg_l_right": (16, 48, 4, 12),
    "leg_l_left":  (24, 48, 4, 12),
}


# ── Cube vertex data: interleaved (px,py,pz, nx,ny,nz, u,v) ──
# 36 vertices = 6 faces × 2 tris × 3 verts
_CUBE_VERTS_UV = [
    (-.5,-.5, .5, 0,1), ( .5,-.5, .5, 1,1), ( .5, .5, .5, 1,0),
    (-.5,-.5, .5, 0,1), ( .5, .5, .5, 1,0), (-.5, .5, .5, 0,0),
    ( .5,-.5,-.5, 0,1), (-.5,-.5,-.5, 1,1), (-.5, .5,-.5, 1,0),
    ( .5,-.5,-.5, 0,1), (-.5, .5,-.5, 1,0), ( .5, .5,-.5, 0,0),
    (-.5, .5, .5, 0,0), ( .5, .5, .5, 1,0), ( .5, .5,-.5, 1,1),
    (-.5, .5, .5, 0,0), ( .5, .5,-.5, 1,1), (-.5, .5,-.5, 0,1),
    (-.5,-.5,-.5, 0,0), ( .5,-.5,-.5, 1,0), ( .5,-.5, .5, 1,1),
    (-.5,-.5,-.5, 0,0), ( .5,-.5, .5, 1,1), (-.5,-.5, .5, 0,1),
    ( .5,-.5, .5, 0,1), ( .5,-.5,-.5, 1,1), ( .5, .5,-.5, 1,0),
    ( .5,-.5, .5, 0,1), ( .5, .5,-.5, 1,0), ( .5, .5, .5, 0,0),
    (-.5,-.5,-.5, 0,1), (-.5,-.5, .5, 1,1), (-.5, .5, .5, 1,0),
    (-.5,-.5,-.5, 0,1), (-.5, .5, .5, 1,0), (-.5, .5,-.5, 0,0),
]

_CUBE_NORMALS = [
    (0,0,1)]*6 + [(0,0,-1)]*6 + [(0,1,0)]*6 + [(0,-1,0)]*6 + [(1,0,0)]*6 + [(-1,0,0)]*6

_CUBE_EDGES = [
    (-.5,-.5,-.5),( .5,-.5,-.5),( .5,-.5,-.5),( .5,-.5, .5),
    ( .5,-.5, .5),(-.5,-.5, .5),(-.5,-.5, .5),(-.5,-.5,-.5),
    (-.5, .5,-.5),( .5, .5,-.5),( .5, .5,-.5),( .5, .5, .5),
    ( .5, .5, .5),(-.5, .5, .5),(-.5, .5, .5),(-.5, .5,-.5),
    (-.5,-.5,-.5),(-.5, .5,-.5),( .5,-.5,-.5),( .5, .5,-.5),
    ( .5,-.5, .5),( .5, .5, .5),(-.5,-.5, .5),(-.5, .5, .5),
]


def _build_cube_vbo_data() -> bytes:
    buf = bytearray()
    for (vx,vy,vz, u,v), (nx,ny,nz) in zip(_CUBE_VERTS_UV, _CUBE_NORMALS):
        buf.extend(struct.pack("ffffffff", vx,vy,vz, nx,ny,nz, u,v))
    return bytes(buf)


def _build_edge_vbo_data() -> bytes:
    buf = bytearray()
    for v in _CUBE_EDGES:
        buf.extend(struct.pack("fff", *v))
    return bytes(buf)


_CUBE_VBO_BYTES = _build_cube_vbo_data()
_EDGE_VBO_BYTES = _build_edge_vbo_data()


# ── GLSL shaders: per-pixel 3-light Blinn-Phong ──
_VERT_SRC = """
#version 120
varying vec3 vNormal;
varying vec2 vTexCoord;
varying vec3 vPosition;
void main() {
    vNormal = normalize(gl_NormalMatrix * gl_Normal);
    vTexCoord = gl_MultiTexCoord0.st;
    vPosition = (gl_ModelViewMatrix * gl_Vertex).xyz;
    gl_Position = ftransform();
}
"""

_FRAG_SRC = """
#version 120
varying vec3 vNormal;
varying vec2 vTexCoord;
varying vec3 vPosition;

uniform vec3 uColor;
uniform vec3 uLight0Pos, uLight0Diff, uLight0Spec;
uniform vec3 uLight1Pos, uLight1Diff, uLight1Spec;
uniform vec3 uLight2Pos, uLight2Diff;
uniform vec3 uAmbient;
uniform vec3 uSkyColor;
uniform vec3 uGroundColor;
uniform vec3 uRimColor;
uniform vec3 uEmissive;
uniform float uRimPower;
uniform float uRimStrength;
uniform float uShininess;
uniform float uSpecStrength;
uniform float uBumpScale;
uniform float uAoStrength;
uniform float uContactLevel;
uniform float uAlphaCut;
uniform float uTime;
uniform sampler2D uTexture;
uniform bool uUseTexture;

void main() {
    // Sample once, up front: the alpha test and the bump map both need it.
    vec4 texel = vec4(1.0);
    if (uUseTexture) {
        texel = texture2D(uTexture, vTexCoord);
    }
    // Only the hair shell enables this, so body-part textures that happen to
    // carry stray transparent texels are never punched full of holes.
    if (uAlphaCut > 0.0 && texel.a < uAlphaCut) discard;

    vec3 albedo = uColor * texel.rgb;
    vec3 N = normalize(vNormal);
    vec3 V = normalize(-vPosition);

    // Surface relief taken from the skin texture itself. The luminance
    // gradient is turned into a normal perturbation with screen-space
    // derivatives, so flat cubes still catch the light like fabric and skin
    // instead of reading as a single unbroken tone.
    if (uBumpScale > 0.0 && uUseTexture) {
        float h = dot(texel.rgb, vec3(0.299, 0.587, 0.114));
        vec3 dpdx = dFdx(vPosition);
        vec3 dpdy = dFdy(vPosition);
        vec3 n1 = cross(dpdy, N);
        vec3 n2 = cross(N, dpdx);
        float denom = dot(dpdx, n1);
        if (abs(denom) > 1e-6) {
            N = normalize(N - uBumpScale * (n1 * dFdx(h) + n2 * dFdy(h)) / denom);
        }
    }

    // Hemisphere ambient: cool sky above, warm bounce below.
    float hemi = 0.5 + 0.5 * N.y;
    vec3 color = mix(uGroundColor, uSkyColor, hemi) * albedo;
    color += uAmbient * albedo;

    // Light 0 — Warm key (front-right, top)
    vec3 L0 = normalize(uLight0Pos - vPosition);
    vec3 H0 = normalize(L0 + V);
    float NdotL0 = max(dot(N, L0), 0.0);
    float NdotH0 = max(dot(N, H0), 0.0);
    float spec0 = pow(max(NdotH0, 0.0), uShininess);
    color += uLight0Diff * albedo * NdotL0;
    color += uLight0Spec * uSpecStrength * spec0;

    // Light 1 — Cool fill (left-back, low)
    vec3 L1 = normalize(uLight1Pos - vPosition);
    vec3 H1 = normalize(L1 + V);
    float NdotL1 = max(dot(N, L1), 0.0);
    float NdotH1 = max(dot(N, H1), 0.0);
    float spec1 = pow(max(NdotH1, 0.0), uShininess * 0.35);
    color += uLight1Diff * albedo * NdotL1;
    color += uLight1Spec * uSpecStrength * spec1;

    // Light 2 — Rim back-light
    vec3 L2 = normalize(uLight2Pos - vPosition);
    float NdotL2 = max(dot(N, L2), 0.0);
    color += uLight2Diff * albedo * NdotL2;

    // Fresnel rim light — brightens edges facing the camera
    float fresnel = pow(1.0 - max(dot(N, V), 0.0), uRimPower);
    float rim_pulse = 1.0 + 0.15 * sin(uTime * 2.0);
    color += uRimColor * fresnel * uRimStrength * rim_pulse;

    // Subtle emissive pulse (for eyes / accents)
    float emissive_pulse = 0.03 + 0.02 * sin(uTime * 1.5 + vPosition.y * 5.0);
    color += albedo * emissive_pulse;
    color += uEmissive * (0.85 + 0.15 * sin(uTime * 2.0));

    // Contact shading. vPosition is view space and the camera has no roll, so
    // -Y points at the feet. uContactLevel is the view-space height of the
    // character's mid-body, supplied by the renderer from the live camera —
    // comparing against a fixed constant would darken the whole figure
    // uniformly, since every view-space Y is negative.
    float contact = clamp((uContactLevel - vPosition.y) * 0.8, 0.0, 1.0);
    color *= 1.0 - uAoStrength * contact;

    gl_FragColor = vec4(clamp(color, 0.0, 1.0), 1.0);
}
"""


# ═══════════════════════════════════════════════════════════════════════
#  Procedural texture generators
# ═══════════════════════════════════════════════════════════════════════

def _tex_buf(gen) -> bytes:
    buf = bytearray(_TEX_SIZE * _TEX_SIZE * 4)
    for y in range(_TEX_SIZE):
        for x in range(_TEX_SIZE):
            r, g, b = gen(x, y)
            i = (y * _TEX_SIZE + x) * 4
            buf[i] = max(0, min(255, r))
            buf[i+1] = max(0, min(255, g))
            buf[i+2] = max(0, min(255, b))
            buf[i+3] = 255
    return bytes(buf)


def _tex_img(buf: bytes) -> QImage:
    img = QImage(buf, _TEX_SIZE, _TEX_SIZE, QImage.Format.Format_RGBA8888)
    return img.copy()


# ── Procedural material textures ──
# These are multiplied by the per-block colour (glColor / uColor), so they are
# authored near-white: a mid-grey base used to darken every character into
# mud. The patterns are deliberately very subtle — at 64×64 over a small cube
# any strong noise reads as dirt rather than fabric.
def _mat_texture(base: int, pattern, tint=(0, 0, 0)) -> QImage:
    """Build a near-white material texture; *pattern* returns an offset."""
    def gen(x, y):
        n = pattern(x, y)
        v = max(0, min(255, base + n))
        return (max(0, min(255, v + tint[0])),
                max(0, min(255, v + tint[1])),
                max(0, min(255, v + tint[2])))
    return _tex_img(_tex_buf(gen))


# Flat (255) materials. Every character colour comes from the profile, and
# the lighting rig supplies the shading, so a plain texture is what makes the
# 3D blocks read as clean Minecraft geometry instead of camouflage.
def _make_skin_tex() -> QImage:
    return _mat_texture(255, lambda x, y: 0, (0, -1, -2))


def _make_cloth_tex() -> QImage:
    return _mat_texture(252, lambda x, y: -3 if x % 8 == 0 else 0, (0, 0, -1))


def _make_denim_tex() -> QImage:
    return _mat_texture(250, lambda x, y: -4 if (x % 8 == 0 or y % 8 == 0) else 0, (0, -1, 2))


def _make_hair_tex() -> QImage:
    return _mat_texture(252, lambda x, y: 2 if x % 8 == 0 else 0, (0, -1, -3))


def _make_leather_tex() -> QImage:
    return _mat_texture(249, lambda x, y: -4 if y % 12 == 0 else 0, (0, -1, -4))


def _make_metal_tex() -> QImage:
    return _mat_texture(252, lambda x, y: 3 if y % 12 == 0 else 0, (2, 0, -1))


def _make_fire_tex() -> QImage:
    def gen(x, y):
        dx, dy = x-8, y-8
        d = math.sqrt(dx*dx+dy*dy)/8.0
        f = max(0, 1-d)*255
        n = int(math.sin(x*9.1+y*7.3)*25 + math.cos(x*11.7-y*5.9)*18 + math.sin(x*5.3+y*3.7)*12)
        v = int(f) + n
        return (min(255, max(60, v+20)), min(220, max(40, v-30)), min(100, max(5, v-90)))
    return _tex_img(_tex_buf(gen))


def _make_bone_tex() -> QImage:
    def gen(x, y):
        n = int(math.sin(x*6.3+y*4.1)*8 + math.cos(x*8.7-y*3.9)*6 + math.sin(x*3.1+y*5.7)*4)
        c = 0
        if y in (4,11) and 3<x<12: c = -30
        if x==7 and 5<y<10: c = -25
        v = 198 + n + c
        return (v, max(165,v-5), max(145,v-18))
    return _tex_img(_tex_buf(gen))


def _make_flat_tex() -> QImage:
    return _tex_img(bytes([128, 128, 128, 255]) * _TEX_SIZE * _TEX_SIZE)


def _c(color, default=(128, 128, 128)):
    if color is None:
        return default
    return tuple(int(c) for c in color[:3])


def _dc(color, amount=35):
    return tuple(max(0, int(c)-amount) for c in _c(color))


# ── Profile loader ──
def _load_profile(character: str) -> dict:
    try:
        from ui.sprite_2d import CHARACTER_PROFILES
        return CHARACTER_PROFILES.get(character, CHARACTER_PROFILES.get("atom", {}))
    except Exception:
        return {}


# ═══════════════════════════════════════════════════════════════════════
#  TextureManager
# ═══════════════════════════════════════════════════════════════════════

class TextureManager:
    """Manages OpenGL textures, VBOs, and display lists."""

    _GENERATORS = {
        "skin": _make_skin_tex, "cloth": _make_cloth_tex, "denim": _make_denim_tex,
        "hair": _make_hair_tex, "leather": _make_leather_tex, "metal": _make_metal_tex,
        "fire": _make_fire_tex, "bone": _make_bone_tex, "flat": _make_flat_tex,
    }

    def __init__(self):
        self._tex: dict[str, int] = {}
        self._cube_vbo = 0
        self._edge_vbo = 0
        self._cube_dl: list[int] = []   # 3 display lists (top/bot, sides)
        self._cube_full_dl = 0
        self._edge_dl = 0
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    def initialize(self) -> None:
        if self._ready:
            return

        # Cube VBO
        self._cube_vbo = gl.glGenBuffers(1)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._cube_vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, len(_CUBE_VBO_BYTES),
                        _CUBE_VBO_BYTES, gl.GL_STATIC_DRAW)

        # Edge VBO
        self._edge_vbo = gl.glGenBuffers(1)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._edge_vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, len(_EDGE_VBO_BYTES),
                        _EDGE_VBO_BYTES, gl.GL_STATIC_DRAW)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)

        # Procedural textures
        for name, fn in self._GENERATORS.items():
            img = fn().convertToFormat(QImage.Format.Format_RGBA8888)
            data = bytes(img.bits().asstring(img.sizeInBytes()))
            tid = gl.glGenTextures(1)
            gl.glBindTexture(gl.GL_TEXTURE_2D, tid)
            gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA,
                            _TEX_SIZE, _TEX_SIZE, 0,
                            gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, data)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_REPEAT)
            gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_REPEAT)
            self._tex[name] = tid

        # ── Compile display lists ──
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._cube_vbo)
        gl.glVertexPointer(3, gl.GL_FLOAT, _VERTEX_STRIDE, ctypes.c_void_p(0))
        gl.glNormalPointer(gl.GL_FLOAT, _VERTEX_STRIDE, ctypes.c_void_p(12))
        gl.glTexCoordPointer(2, gl.GL_FLOAT, _VERTEX_STRIDE, ctypes.c_void_p(24))
        gl.glEnableClientState(gl.GL_VERTEX_ARRAY)
        gl.glEnableClientState(gl.GL_NORMAL_ARRAY)
        gl.glEnableClientState(gl.GL_TEXTURE_COORD_ARRAY)

        # 3 cube part display lists: front+back+top (0-17), bottom (18-23), right+left (24-35)
        for start, count in [(0, 18), (18, 6), (24, 12)]:
            dl = gl.glGenLists(1)
            gl.glNewList(dl, gl.GL_COMPILE)
            gl.glDrawArrays(gl.GL_TRIANGLES, start, count)
            gl.glEndList()
            self._cube_dl.append(dl)

        self._cube_full_dl = gl.glGenLists(1)
        gl.glNewList(self._cube_full_dl, gl.GL_COMPILE)
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 36)
        gl.glEndList()

        # Edge display list
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._edge_vbo)
        gl.glVertexPointer(3, gl.GL_FLOAT, 0, ctypes.c_void_p(0))
        gl.glDisableClientState(gl.GL_NORMAL_ARRAY)
        gl.glDisableClientState(gl.GL_TEXTURE_COORD_ARRAY)
        self._edge_dl = gl.glGenLists(1)
        gl.glNewList(self._edge_dl, gl.GL_COMPILE)
        gl.glDrawArrays(gl.GL_LINES, 0, 24)
        gl.glEndList()

        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)
        self._ready = True

    def bind_cube_ptr(self) -> None:
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._cube_vbo)
        gl.glVertexPointer(3, gl.GL_FLOAT, _VERTEX_STRIDE, ctypes.c_void_p(0))
        gl.glNormalPointer(gl.GL_FLOAT, _VERTEX_STRIDE, ctypes.c_void_p(12))
        gl.glTexCoordPointer(2, gl.GL_FLOAT, _VERTEX_STRIDE, ctypes.c_void_p(24))

    def bind_edge_ptr(self) -> None:
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._edge_vbo)
        gl.glVertexPointer(3, gl.GL_FLOAT, 0, ctypes.c_void_p(0))

    def bind_tex(self, name: str) -> None:
        tid = self._tex.get(name, self._tex.get("flat", 0))
        gl.glBindTexture(gl.GL_TEXTURE_2D, tid)

    def replace_tex_from_qimage(self, name: str, img: QImage) -> None:
        """Replace a procedural texture with one from a QImage (skin face texture)."""
        if not self._ready:
            return
        scaled = img.scaled(_TEX_SIZE, _TEX_SIZE,
                            Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.FastTransformation)
        scaled = scaled.convertToFormat(QImage.Format.Format_RGBA8888)
        data = bytes(scaled.bits().asstring(scaled.sizeInBytes()))
        tid = gl.glGenTextures(1)
        gl.glBindTexture(gl.GL_TEXTURE_2D, tid)
        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGBA,
                        _TEX_SIZE, _TEX_SIZE, 0,
                        gl.GL_RGBA, gl.GL_UNSIGNED_BYTE, data)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_NEAREST)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_NEAREST)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)
        old = self._tex.get(name)
        if old is not None:
            gl.glDeleteTextures(1, [old])
        self._tex[name] = tid

    def forget(self) -> None:
        """Drop all GL object names *without* touching the driver.

        Called when a brand new context is about to initialise: the names refer
        to objects in the context that just went away, so they must not be
        reused (binding a stale texture id samples nothing) and they must not be
        handed to ``glDelete*`` either.
        """
        self._tex.clear()
        self._cube_vbo = 0
        self._edge_vbo = 0
        self._cube_dl.clear()
        self._cube_full_dl = 0
        self._edge_dl = 0
        self._ready = False

    def cleanup(self) -> None:
        for tid in self._tex.values():
            gl.glDeleteTextures(1, [tid])
        self._tex.clear()
        if self._cube_vbo:
            gl.glDeleteBuffers(1, [self._cube_vbo])
            self._cube_vbo = 0
        if self._edge_vbo:
            gl.glDeleteBuffers(1, [self._edge_vbo])
            self._edge_vbo = 0
        for dl in self._cube_dl:
            gl.glDeleteLists(dl, 1)
        self._cube_dl.clear()
        if self._cube_full_dl:
            gl.glDeleteLists(self._cube_full_dl, 1)
            self._cube_full_dl = 0
        if self._edge_dl:
            gl.glDeleteLists(self._edge_dl, 1)
            self._edge_dl = 0
        self._ready = False


_TEX_MGR = TextureManager()

# ═══════════════════════════════════════════════════════════════════════
#  GLTFCharacterModel — loads atom.gltf and creates per-mesh VBOs
# ═══════════════════════════════════════════════════════════════════════

_MESH_NAMES = ["Head", "Body", "LeftArm", "RightArm", "LeftLeg", "RightLeg", "Hat"]


def _normalise_skin_atlas(img: "QImage") -> "QImage":
    """Return a 64×64 modern-layout skin atlas from any skin image.

    A legacy 64×32 skin has no left arm / left leg slots, so it must be
    *remapped* into the 1.8 layout (see ``core.skin_loader.legacy_to_modern``)
    before any UV lookup. Stretching it — which is what the renderer used to do
    — placed every texture in the wrong spot and the model rendered with
    unrelated pixels, while HD skins (128×128 and up) genuinely do just need
    downscaling.
    """
    if img.width() == 64 and img.height() == 32:
        modern = QImage(64, 64, QImage.Format.Format_ARGB32)
        modern.fill(Qt.GlobalColor.transparent)
        painter = QPainter(modern)
        painter.drawImage(0, 0, img)
        flip = Qt.Orientation.Horizontal
        painter.drawImage(16, 48, img.copy(0, 16, 16, 16).flipped(flip))
        painter.drawImage(32, 48, img.copy(40, 16, 16, 16).flipped(flip))
        painter.end()
        return modern
    if img.width() == 64 and img.height() == 64:
        return img
    return img.scaled(64, 64, Qt.AspectRatioMode.IgnoreAspectRatio,
                      Qt.TransformationMode.FastTransformation)


class _MeshInfo:
    """Per-mesh draw info extracted from glTF."""
    __slots__ = ('name', 'vertex_byte_offset', 'vertex_count',
                 'index_byte_offset', 'index_count', 'component_type')
    def __init__(self, name, vertex_byte_offset, vertex_count,
                 index_byte_offset, index_count, component_type):
        self.name = name
        self.vertex_byte_offset = vertex_byte_offset
        self.vertex_count = vertex_count
        self.index_byte_offset = index_byte_offset
        self.index_count = index_count
        self.component_type = component_type


class GLTFCharacterModel:
    """Loads atom.gltf and renders Minecraft-proportioned character meshes with skin UV."""

    _GLTF_PATH = ROOT / "assets" / "models" / "atom.gltf"

    def __init__(self):
        self._vbo = 0
        self._ibo = 0
        self._meshes: list = []
        self._loaded = False
        self._buffer_data: bytes = b""
        # Sizes come from the file's buffer views, not from hard-coded constants:
        # regenerating the model with different proportions must not silently
        # upload the wrong slice.
        self._vtx_byte_length = 0
        self._idx_byte_offset = 0
        self._idx_byte_length = 0

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def mesh_count(self) -> int:
        """Number of meshes the loaded file actually carries.

        Callers must not assume the six original body parts: the model gained a
        seventh (the hair/hat layer), and future regenerations may add more.
        """
        return len(self._meshes)

    def parse_gltf(self, gltf_path):
        # Called again whenever the context is recreated; without this the mesh
        # list grew on every rebuild.
        self._meshes.clear()
        try:
            with open(gltf_path, "r", encoding="utf-8") as f:
                gltf_data = json.load(f)

            buffers = gltf_data.get("buffers", [])
            if not buffers:
                log.warning("GLTF has no buffers")
                return False
            uri = buffers[0].get("uri", "")
            if not uri.startswith("data:application/octet-stream;base64,"):
                log.warning("GLTF buffer is not base64-embedded")
                return False

            b64_data = uri.split("base64,")[1]
            self._buffer_data = base64.b64decode(b64_data)

            accessors = gltf_data.get("accessors", [])
            meshes = gltf_data.get("meshes", [])
            buffer_views = gltf_data.get("bufferViews", [])

            if len(buffer_views) < 2:
                log.warning("GLTF needs at least 2 buffer views")
                return False

            vtx_bv = buffer_views[0]
            idx_bv = buffer_views[1]
            idx_base = idx_bv.get("byteOffset", vtx_bv.get("byteLength", 0))
            self._vtx_byte_length = vtx_bv.get("byteLength", 0)
            self._idx_byte_offset = idx_base
            self._idx_byte_length = idx_bv.get("byteLength", 0)

            for i, mesh in enumerate(meshes):
                primitives = mesh.get("primitives", [])
                if not primitives:
                    continue
                prim = primitives[0]
                attrs = prim.get("attributes", {})
                pos_idx = attrs.get("POSITION")
                idx_idx = prim.get("indices")
                if pos_idx is None or idx_idx is None:
                    continue

                pos_acc = accessors[pos_idx]
                idx_acc = accessors[idx_idx]

                vtx_count = pos_acc.get("count", 0)
                idx_count = idx_acc.get("count", 0)
                # Offsets must be relative to the *buffer view* they live in,
                # because each is uploaded to its own GL buffer. Adding the buffer
                # view's own byteOffset here (as this used to) pointed every
                # draw call past the end of the index buffer, so the character
                # rendered as a sliver of garbage instead of the model.
                vtx_offset = pos_acc.get("byteOffset", 0)
                idx_offset = idx_acc.get("byteOffset", 0)
                name = mesh.get("name", _MESH_NAMES[i] if i < len(_MESH_NAMES) else f"Mesh_{i}")
                comp_type = idx_acc.get("componentType", 5123)

                self._meshes.append(_MeshInfo(
                    name=name, vertex_byte_offset=vtx_offset,
                    vertex_count=vtx_count, index_byte_offset=idx_offset,
                    index_count=idx_count, component_type=comp_type))

            log.info("GLTF parsed: %d meshes, %d bytes", len(self._meshes), len(self._buffer_data))
            return True
        except Exception as e:
            log.warning("Failed to parse glTF: %s", e)
            return False

    def create_vbos(self):
        if not self._buffer_data:
            return
        vtx_len = min(self._vtx_byte_length or len(self._buffer_data), len(self._buffer_data))
        idx_data = self._buffer_data[self._idx_byte_offset:
                                     self._idx_byte_offset + self._idx_byte_length]

        self._vbo = gl.glGenBuffers(1)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._vbo)
        gl.glBufferData(gl.GL_ARRAY_BUFFER, vtx_len, self._buffer_data[:vtx_len], gl.GL_STATIC_DRAW)

        self._ibo = gl.glGenBuffers(1)
        gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, self._ibo)
        gl.glBufferData(gl.GL_ELEMENT_ARRAY_BUFFER, len(idx_data), idx_data, gl.GL_STATIC_DRAW)

        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)
        gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, 0)
        self._loaded = True
        log.info("GLTF VBOs created: %d bytes vertex, %d bytes index", vtx_len, len(idx_data))

    def bind(self):
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self._vbo)
        gl.glVertexPointer(3, gl.GL_FLOAT, 32, ctypes.c_void_p(0))
        gl.glNormalPointer(gl.GL_FLOAT, 32, ctypes.c_void_p(12))
        gl.glTexCoordPointer(2, gl.GL_FLOAT, 32, ctypes.c_void_p(24))
        gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, self._ibo)

    def draw_mesh(self, mesh_idx):
        if not self._loaded or mesh_idx < 0 or mesh_idx >= len(self._meshes):
            return
        m = self._meshes[mesh_idx]
        # The file stores each mesh's indices relative to its own first vertex,
        # so the vertex array has to be re-pointed at that mesh's slice before
        # drawing. Without this every mesh drew the first mesh's vertices.
        base = m.vertex_byte_offset
        gl.glVertexPointer(3, gl.GL_FLOAT, 32, ctypes.c_void_p(base))
        gl.glNormalPointer(gl.GL_FLOAT, 32, ctypes.c_void_p(base + 12))
        gl.glTexCoordPointer(2, gl.GL_FLOAT, 32, ctypes.c_void_p(base + 24))
        gl.glDrawElements(gl.GL_TRIANGLES, m.index_count,
                          m.component_type, ctypes.c_void_p(m.index_byte_offset))

    def cleanup(self):
        if self._vbo:
            gl.glDeleteBuffers(1, [self._vbo])
            self._vbo = 0
        if self._ibo:
            gl.glDeleteBuffers(1, [self._ibo])
            self._ibo = 0
        self._meshes.clear()
        self._loaded = False
        self._buffer_data = b""


# Map character names to skin PNG file paths.
#
# This must agree with ``ui.sprite_2d._SKIN_PATHS``, which is the atlas the same
# character is coloured from and the one ``config_manager.skin_<name>`` points
# at. Gamer Girl used to be textured with Pink's sheet here while her palette
# was read from ``gamergirl.png``, so the 2D and 3D versions of the character
# showed different people. ``tests/test_materials.py`` now asserts they agree.
_SKIN_FILES = {
    "atom": "atom_skin.png",
    "pink": "pink_skin.png",
    "steve": "steve_skin.png",
    "alex": "alex_skin.png",
    "ghost_rider": "ghost_skin.png",
    "echo": "atom_skin.png",
    "gamergirl": "gamergirl.png",
}

# ── Shader state ──
_SHADER_PROG = 0
_SHADER_UNIFORMS: dict[str, int] = {}


def _compile_shader(src: str, stype: int) -> int:
    try:
        s = gl.glCreateShader(stype)
        gl.glShaderSource(s, src)
        gl.glCompileShader(s)
        if not gl.glGetShaderiv(s, gl.GL_COMPILE_STATUS):
            gl.glDeleteShader(s)
            return 0
        return s
    except Exception:
        return 0


def _link_program(vs: int, fs: int) -> int:
    try:
        p = gl.glCreateProgram()
        gl.glAttachShader(p, vs)
        gl.glAttachShader(p, fs)
        gl.glLinkProgram(p)
        if not gl.glGetProgramiv(p, gl.GL_LINK_STATUS):
            gl.glDeleteProgram(p)
            return 0
        return p
    except Exception:
        return 0


def _init_shaders() -> bool:
    """Compile and link the shading program.

    Resets the program handle and the uniform cache first. GL object names and
    uniform values only mean anything inside the context that created them, but
    ``_SHADER_PROG``/``_LIGHTING_INITIALIZED`` are module globals — so when the
    overlay rebuilt the renderer (stall recovery, 2D/3D switch) the new context
    was handed the *old* state, ``_LIGHTING_INITIALIZED`` still said True, and
    the light uniforms were never uploaded. The character rendered black.
    """
    global _SHADER_PROG, _SHADER_UNIFORMS, _LIGHTING_INITIALIZED
    _SHADER_PROG = 0
    _SHADER_UNIFORMS.clear()
    _LIGHTING_INITIALIZED = False
    try:
        vs = _compile_shader(_VERT_SRC, gl.GL_VERTEX_SHADER)
        if not vs:
            return False
        fs = _compile_shader(_FRAG_SRC, gl.GL_FRAGMENT_SHADER)
        if not fs:
            gl.glDeleteShader(vs)
            return False
        prog = _link_program(vs, fs)
        gl.glDeleteShader(vs)
        gl.glDeleteShader(fs)
        if not prog:
            return False
        _SHADER_PROG = prog
        for name in ["uColor", "uLight0Pos", "uLight0Diff", "uLight0Spec",
                      "uLight1Pos", "uLight1Diff", "uLight1Spec",
                      "uLight2Pos", "uLight2Diff",
                      "uAmbient", "uSkyColor", "uGroundColor",
                      "uRimColor", "uRimPower", "uRimStrength",
                      "uEmissive", "uShininess", "uSpecStrength",
                      "uBumpScale", "uAoStrength", "uContactLevel",
                      "uAlphaCut", "uTime",
                      "uTexture", "uUseTexture"]:
            _SHADER_UNIFORMS[name] = gl.glGetUniformLocation(prog, name)
        log.info("3D shaders compiled (id=%d)", prog)
        return True
    except Exception:
        log.warning("Shader initialization failed - using fixed-function fallback")
        return False


def _reset_gl_state() -> None:
    """Forget GL object names that belonged to a destroyed context.

    The texture manager, the shader program and the uniform cache are all
    module-level singletons, but GL names are only valid inside the context
    that created them. When the overlay rebuilds the renderer the new context
    inherited the old names, so the character came back untextured and unlit.
    """
    _TEX_MGR.forget()


def _cleanup_shaders() -> None:
    global _SHADER_PROG, _SHADER_UNIFORMS, _LIGHTING_INITIALIZED
    if _SHADER_PROG:
        gl.glDeleteProgram(_SHADER_PROG)
        _SHADER_PROG = 0
        _SHADER_UNIFORMS.clear()
    _LIGHTING_INITIALIZED = False


_LIGHTING_INITIALIZED = False


# Material presets.
#
# (specular strength, shininess, rim strength, bump scale, emissive tint).
# The bump scale is what gives each surface its own relief response: cloth
# weave catches highlights differently from denim, which differs from the soft
# falloff of skin.
MATERIALS: dict[str, tuple[float, float, float, float, tuple[int, int, int]]] = {
    "default": (0.10, 16.0, 0.25, 0.75, (0, 0, 0)),
    "skin":    (0.10, 20.0, 0.30, 0.85, (0, 0, 0)),
    "visor":   (0.55, 64.0, 0.45, 0.35, (0, 20, 30)),
    "cloth":   (0.05,  8.0, 0.20, 1.10, (0, 0, 0)),
    "denim":   (0.08, 12.0, 0.24, 1.30, (0, 0, 0)),
    "leather": (0.20, 22.0, 0.32, 1.05, (0, 0, 0)),
    "metal":   (0.70, 60.0, 0.55, 0.55, (0, 0, 0)),
    "hair":    (0.24, 34.0, 0.42, 1.00, (0, 0, 0)),
}

# Which material each glTF mesh is made of. Index matches _MESH_NAMES.
_MESH_MATERIALS = ["skin", "cloth", "cloth", "cloth", "denim", "denim", "hair"]

# View-space Y far below any real fragment, so contact shading is inert until
# paintGL supplies a reference height from the live camera.
_CONTACT_DEFAULT_VIEW_Y = -1000.0


def _bind_shaders() -> None:
    """Bind shader program and set the static uniforms (lights + time).

    Only the light rig is uploaded once — it never changes. Per-material and
    per-draw uniforms (uAlphaCut, uShininess, ...) are set at their draw sites
    by :func:`_shader_material` / :func:`_shader_alpha_cut`.
    """
    global _LIGHTING_INITIALIZED
    if not _SHADER_PROG:
        return
    gl.glUseProgram(_SHADER_PROG)
    if not _LIGHTING_INITIALIZED:
        # Key: warm, high and to the camera's right.
        gl.glUniform3f(_SHADER_UNIFORMS["uLight0Pos"], 7.5, 11.0, 8.0)
        gl.glUniform3f(_SHADER_UNIFORMS["uLight0Diff"], .95, .84, .66)
        gl.glUniform3f(_SHADER_UNIFORMS["uLight0Spec"], .62, .55, .44)
        # Fill: cool, low, opposite side — stops the shadow side going black.
        gl.glUniform3f(_SHADER_UNIFORMS["uLight1Pos"], -7.0, 3.0, 6.5)
        gl.glUniform3f(_SHADER_UNIFORMS["uLight1Diff"], .24, .30, .44)
        gl.glUniform3f(_SHADER_UNIFORMS["uLight1Spec"], .16, .19, .28)
        # Rim: cool, behind and above — separates the silhouette from the bg.
        gl.glUniform3f(_SHADER_UNIFORMS["uLight2Pos"], -3.0, 8.0, -9.0)
        gl.glUniform3f(_SHADER_UNIFORMS["uLight2Diff"], .30, .36, .52)
        gl.glUniform3f(_SHADER_UNIFORMS["uAmbient"], .07, .07, .09)
        gl.glUniform3f(_SHADER_UNIFORMS["uSkyColor"], .34, .38, .45)
        gl.glUniform3f(_SHADER_UNIFORMS["uGroundColor"], .09, .08, .07)
        gl.glUniform3f(_SHADER_UNIFORMS["uRimColor"], .45, .68, 1.0)
        gl.glUniform1f(_SHADER_UNIFORMS["uRimPower"], 3.0)
        gl.glUniform1f(_SHADER_UNIFORMS["uAoStrength"], 0.35)
        gl.glUniform1f(_SHADER_UNIFORMS["uContactLevel"], _CONTACT_DEFAULT_VIEW_Y)
        gl.glUniform1i(_SHADER_UNIFORMS["uTexture"], 0)
        # Defaults so the procedural path (which never sets a material) and the
        # very first frame are well defined.
        gl.glUniform1f(_SHADER_UNIFORMS["uAlphaCut"], 0.0)
        spec, shin, rim, bump, emis = MATERIALS["default"]
        gl.glUniform1f(_SHADER_UNIFORMS["uSpecStrength"], spec)
        gl.glUniform1f(_SHADER_UNIFORMS["uShininess"], shin)
        gl.glUniform1f(_SHADER_UNIFORMS["uRimStrength"], rim)
        gl.glUniform1f(_SHADER_UNIFORMS["uBumpScale"], bump)
        gl.glUniform3f(_SHADER_UNIFORMS["uEmissive"], 0.0, 0.0, 0.0)
        _LIGHTING_INITIALIZED = True
    gl.glUniform1f(_SHADER_UNIFORMS["uTime"], _time.time())


def _shader_color(color, alpha=1.0) -> None:
    if _SHADER_PROG:
        c = _c(color)
        gl.glUniform3f(_SHADER_UNIFORMS["uColor"], c[0]/255, c[1]/255, c[2]/255)


def _shader_tex(use: bool) -> None:
    if _SHADER_PROG:
        gl.glUniform1i(_SHADER_UNIFORMS["uUseTexture"], 1 if use else 0)


def _shader_material(name: str) -> None:
    """Switch the active surface material.

    With shaders this drives per-pixel specular / rim / relief / emissive. On
    the fixed-function fallback the same preset is applied through the
    material state so the two paths look as close as the API allows.
    """
    spec, shin, rim, bump, emis = MATERIALS.get(name, MATERIALS["default"])
    if _SHADER_PROG:
        gl.glUniform1f(_SHADER_UNIFORMS["uSpecStrength"], spec)
        gl.glUniform1f(_SHADER_UNIFORMS["uShininess"], shin)
        gl.glUniform1f(_SHADER_UNIFORMS["uRimStrength"], rim)
        gl.glUniform1f(_SHADER_UNIFORMS["uBumpScale"], bump)
        gl.glUniform3f(_SHADER_UNIFORMS["uEmissive"],
                       emis[0] / 255, emis[1] / 255, emis[2] / 255)
    else:
        try:
            gl.glMaterialfv(gl.GL_FRONT_AND_BACK, gl.GL_SPECULAR,
                            [spec, spec, spec, 1.0])
            gl.glMaterialf(gl.GL_FRONT_AND_BACK, gl.GL_SHININESS, min(128.0, shin))
        except Exception:
            pass


def _shader_alpha_cut(cut: float) -> None:
    """0.0 disables the alpha test; 0.5 makes it a Minecraft-style cut-out."""
    if _SHADER_PROG:
        gl.glUniform1f(_SHADER_UNIFORMS["uAlphaCut"], cut)


def _shader_contact_level(eye_y: float, mid_body_y: float = 0.95) -> None:
    """Tell the shader where the character's mid-body sits in view space.

    Contact shading has to be relative to the character, and view-space Y is
    only meaningful once the camera is known: the whole figure sits at a
    *negative* view-space height, so a hard-coded threshold clamps to full
    darkening for every fragment and just dims the model by a flat percentage.
    """
    if _SHADER_PROG:
        gl.glUniform1f(_SHADER_UNIFORMS["uContactLevel"], mid_body_y - eye_y)


# ═══════════════════════════════════════════════════════════════════════
#  Drawing primitives (display-list accelerated)
# ═══════════════════════════════════════════════════════════════════════

def _draw_block(color, tex_type="flat", dark_color=None):
    """Return a display-list draw function for a textured block."""
    c = _c(color)
    dc = _c(dark_color) if dark_color is not None else _dc(color)
    oc = _OUTLINE_COLOR

    def draw(x, y, z, w, h, d):
        _TEX_MGR.bind_tex(tex_type)
        # The procedural texture names line up with the material presets
        # (skin / cloth / denim / hair / leather / metal); "flat" falls back to
        # the default material.
        _shader_material(tex_type)
        if _SHADER_PROG:
            _shader_tex(True)
        gl.glPushMatrix()
        gl.glTranslatef(x, y, z)
        gl.glScalef(w, h, d)

        if _SHADER_PROG:
            _shader_color(c)
        else:
            gl.glColor3f(c[0]/255, c[1]/255, c[2]/255)
        gl.glCallList(_TEX_MGR._cube_dl[0])  # front+back+top

        if _SHADER_PROG:
            _shader_color(dc)
        else:
            gl.glColor3f(dc[0]/255, dc[1]/255, dc[2]/255)
        gl.glCallList(_TEX_MGR._cube_dl[1])  # bottom

        if _SHADER_PROG:
            _shader_color(c)
        else:
            gl.glColor3f(c[0]/255, c[1]/255, c[2]/255)
        gl.glCallList(_TEX_MGR._cube_dl[2])  # right+left

        gl.glPopMatrix()

        if _SHADER_PROG:
            _shader_tex(False)
        # Outline
        oc_ = oc
        if _SHADER_PROG:
            _shader_color(oc_)
        else:
            gl.glColor3f(oc_[0]/255, oc_[1]/255, oc_[2]/255)
        gl.glDisable(gl.GL_LIGHTING)
        gl.glLineWidth(1.2)
        gl.glPushMatrix()
        gl.glTranslatef(x, y, z)
        gl.glScalef(w, h, d)
        gl.glCallList(_TEX_MGR._edge_dl)
        gl.glPopMatrix()
        gl.glEnableClientState(gl.GL_TEXTURE_COORD_ARRAY)
        gl.glEnable(gl.GL_LIGHTING)
    return draw


def _draw_flat(color):
    """Return a display-list draw function for flat solid blocks."""
    c = _c(color)
    oc = _OUTLINE_COLOR

    def draw(x, y, z, w, h, d):
        gl.glDisableClientState(gl.GL_TEXTURE_COORD_ARRAY)
        gl.glPushMatrix()
        gl.glTranslatef(x, y, z)
        gl.glScalef(w, h, d)
        if _SHADER_PROG:
            _shader_color(c)
        else:
            gl.glColor3f(c[0]/255, c[1]/255, c[2]/255)
        gl.glCallList(_TEX_MGR._cube_full_dl)
        gl.glPopMatrix()
        gl.glEnableClientState(gl.GL_TEXTURE_COORD_ARRAY)

        if _SHADER_PROG:
            _shader_color(oc)
        else:
            gl.glColor3f(oc[0]/255, oc[1]/255, oc[2]/255)
        gl.glDisable(gl.GL_LIGHTING)
        gl.glLineWidth(1)
        gl.glPushMatrix()
        gl.glTranslatef(x, y, z)
        gl.glScalef(w, h, d)
        gl.glCallList(_TEX_MGR._edge_dl)
        gl.glPopMatrix()
        gl.glEnable(gl.GL_LIGHTING)
    return draw


def _draw_glow_block(color, alpha=0.3):
    """Return a display-list draw function for a glow block (no outline)."""
    c = _c(color)
    def draw(x, y, z, w, h, d):
        gl.glDisable(gl.GL_LIGHTING)
        gl.glDisableClientState(gl.GL_NORMAL_ARRAY)
        gl.glDisableClientState(gl.GL_TEXTURE_COORD_ARRAY)
        gl.glPushMatrix()
        gl.glTranslatef(x, y, z)
        gl.glScalef(w, h, d)
        gl.glColor4f(c[0]/255, c[1]/255, c[2]/255, alpha)
        gl.glCallList(_TEX_MGR._cube_full_dl)
        gl.glPopMatrix()
        gl.glEnable(gl.GL_LIGHTING)
        gl.glEnableClientState(gl.GL_NORMAL_ARRAY)
        gl.glEnableClientState(gl.GL_TEXTURE_COORD_ARRAY)
    return draw


def _draw_ellipse(rx, ry, y, color, alpha=1.0, segs=24):
    """Immediate-mode filled ellipse (caller unbound shader)."""
    gl.glBegin(gl.GL_TRIANGLE_FAN)
    gl.glColor4f(color[0]/255, color[1]/255, color[2]/255, alpha)
    gl.glVertex3f(0, y, 0)
    for i in range(segs + 1):
        a = 2 * math.pi * i / segs
        gl.glVertex3f(rx * math.cos(a), y, ry * math.sin(a))
    gl.glEnd()


def _draw_tri_fan(center, points, color, alpha):
    """Immediate-mode triangle fan (caller unbound shader)."""
    gl.glBegin(gl.GL_TRIANGLE_FAN)
    gl.glColor4f(color[0]/255, color[1]/255, color[2]/255, alpha)
    gl.glVertex3f(*center)
    for p in points:
        gl.glVertex3f(*p)
    gl.glEnd()


# ═══════════════════════════════════════════════════════════════════════
#  BlockRenderer3D
# ═══════════════════════════════════════════════════════════════════════

_OPENGL_AVAILABLE = _import_opengl()

if _OPENGL_AVAILABLE:

    class BlockRenderer3D(QOpenGLWidget):
        """QOpenGLWidget that renders a Minecraft-style block character.

        Signals:
            frame_rendered — emitted after each paintGL
        """

        frame_rendered = pyqtSignal()

        def __init__(self, parent=None, use_skin_png: bool = False):
            super().__init__(parent)
            # Skin PNGs (assets/skins/*.png) override the hand-authored profile
            # colours and provide face textures. Off by default so the two Echo
            # designs — Atom and Pink — render as authored.
            self.use_skin_png = bool(use_skin_png)
            # Keep this below the overlay's 3D window size (200x280 at scale 2)
            # so the widget never fights the window geometry.
            self.setMinimumSize(120, 168)

            # Camera. The distance is set so every animated pose stays inside the
            # frame: at 5.5 the jump pose's raised arms were clipped by the top
            # edge (measured on a real framebuffer — the figure is 193px tall in a
            # 280px window, leaving too little head room).
            self.cam_distance = 6.4
            self.cam_elevation = 22.0
            self.cam_rotation = 45.0

            # State
            self.pose = "idle"
            self.animation_frame = 0
            self.emotion = "neutral"
            self.character = "atom"
            self.profile = _load_profile("atom")

            # Cached draw functions (procedural fallback)
            self._cache: dict = {}
            self._rebuild_cache()

            # glTF model loader
            self._gltf_model = GLTFCharacterModel()
            self._gltf_loaded = False

            # Animation timer
            self._timer = QTimer(self)
            self._timer.timeout.connect(self._animate)
            self._timer.start(66)  # 15 FPS — block character doesn't need more, saves ~25% CPU vs 20 FPS
            self._idle_frames = 0
            self._full_speed = True

        def stop_animation(self) -> None:
            """Stop the animation timer. Idempotent.

            Nothing stopped this timer before — it relied on Qt destroying the
            widget, but ``deleteLater()`` leaves it ticking at 15 Hz whenever the
            Python wrapper still holds a reference (verified), so it kept
            repainting a widget the overlay had already discarded.
            """
            timer = self.__dict__.get("_timer")
            if timer is not None:
                timer.stop()

        def is_animation_running(self) -> bool:
            """True while the animation timer is still ticking.

            The overlay's stall watchdog uses this to tell a *broken* renderer
            (dead timer) apart from one that simply is not being painted right
            now (window covered, display asleep, machine busy). Re-initialising a
            renderer that is still animating throws away a working GL context.
            """
            timer = self.__dict__.get("_timer")
            if timer is None:
                return False
            try:
                return bool(timer.isActive())
            except Exception:
                return False

        def closeEvent(self, event) -> None:
            self.stop_animation()
            super().closeEvent(event)

        # ── Public API ──

        @staticmethod
        def is_available() -> bool:
            return True

        def set_profile(self, character: str) -> None:
            self.character = character
            self.profile = _load_profile(character)
            self._rebuild_cache()
            if self.use_skin_png:
                self._load_skin_textures(character)  # Also calls _load_skin_png_texture internally
            self.update()

        def set_pose(self, pose: str) -> None:
            self.pose = pose
            self.update()

        def set_emotion(self, emotion: str) -> None:
            self.emotion = emotion or "neutral"
            self.update()

        # ── Cache rebuild ──

        def _rebuild_cache(self) -> None:
            p = self.profile
            skin = _c(p.get("skin"))
            hair = _c(p.get("hair"), skin)
            hl = _c(p.get("hair_light"), (min(255, hair[0]+40), min(255, hair[1]+30), min(255, hair[2]+20)))
            shirt = _c(p.get("shirt"))
            sd = _c(p.get("shirt_dark"), _dc(p.get("shirt")))
            pants = _c(p.get("pants"))
            pl = _c(p.get("pants_light"), (min(255, pants[0]+20), min(255, pants[1]+18), min(255, pants[2]+15)))
            boot = _c(p.get("boot"))
            bt = _c(p.get("boot_trim"), (max(0, boot[0]-10), max(0, boot[1]-10), max(0, boot[2]-10)))

            # Check if skin PNG textures were loaded — use them if available
            # otherwise fall back to procedural textures
            _has_skin_tex = _TEX_MGR.ready and "skin_head_front" in _TEX_MGR._tex

            # Head textures: use skin PNG face or procedural
            head_tex = "skin_head_front" if _has_skin_tex else "skin"
            head_side_tex = "skin_head_side" if _has_skin_tex else "skin"
            head_top_tex = "skin_head_top" if _has_skin_tex else "skin"
            head_back_tex = "skin_head_back" if _has_skin_tex else "skin"

            # Body textures
            body_tex = "skin_body" if _has_skin_tex else "cloth"
            body_side_tex = "skin_body_side" if _has_skin_tex else "cloth"
            body_top_tex = "skin_body_top" if _has_skin_tex else "cloth"

            # Arm textures
            arm_tex = "skin_arm" if _has_skin_tex else "skin"
            arm_side_tex = "skin_arm_side" if _has_skin_tex else "skin"
            arm_top_tex = "skin_arm_top" if _has_skin_tex else "skin"

            # Leg textures
            leg_tex = "skin_leg" if _has_skin_tex else "denim"
            leg_side_tex = "skin_leg_side" if _has_skin_tex else "denim"
            leg_top_tex = "skin_leg_top" if _has_skin_tex else "denim"

            self._cache = {
                "skin_color": skin, "hair_color": hair, "hl_color": hl,
                "shirt_color": shirt, "sd_color": sd,
                "pants_color": pants, "pl_color": pl,
                "boot_color": boot, "bt_color": bt,
                # Head: front/back/top/left/right
                "b_head_front": _draw_block(skin, head_tex),
                "b_head_side": _draw_block(skin, head_side_tex),
                "b_head_top": _draw_block(skin, head_top_tex),
                "b_head_back": _draw_block(skin, head_back_tex),
                # Body: front/sides/top
                "b_body": _draw_block(shirt, body_tex, sd),
                "b_body_side": _draw_block(shirt, body_side_tex, sd),
                "b_body_top": _draw_block(shirt, body_top_tex),
                # Arms
                "b_arm": _draw_block(skin, arm_tex),
                "b_arm_side": _draw_block(skin, arm_side_tex),
                "b_arm_top": _draw_block(skin, arm_top_tex),
                # Legs
                "b_leg": _draw_block(pants, leg_tex),
                "b_leg_side": _draw_block(pants, leg_side_tex),
                "b_leg_top": _draw_block(pants, leg_top_tex),
                # Boots (lower leg)
                "b_boot": _draw_block(boot, "leather"),
                "b_boot_top": _draw_block(bt, "leather"),
                # Fallback procedural draw functions
                "bs": _draw_block(skin, "skin"),
                "bh": _draw_block(hair, "hair"),
                "bhl": _draw_block(hl, "hair"),
                "bsh": _draw_block(shirt, "cloth", sd),
                "bshl": _draw_block(shirt, "cloth"),
                "bp": _draw_block(pants, "denim"),
                "bpl": _draw_block(pl, "denim"),
                "bb": _draw_block(boot, "leather"),
                "bbt": _draw_block(bt, "leather"),
            }

        # ── OpenGL events ──

        def initializeGL(self):
            # Everything below is per-context: discard whatever a previous
            # context left in the module-level caches before rebuilding.
            _reset_gl_state()
            gl.glClearColor(0, 0, 0, 0)
            gl.glEnable(gl.GL_DEPTH_TEST)
            gl.glDepthFunc(gl.GL_LEQUAL)
            gl.glEnable(gl.GL_CULL_FACE)
            gl.glCullFace(gl.GL_BACK)
            gl.glEnable(gl.GL_BLEND)
            gl.glBlendFunc(gl.GL_SRC_ALPHA, gl.GL_ONE_MINUS_SRC_ALPHA)
            gl.glEnable(gl.GL_LINE_SMOOTH)
            gl.glHint(gl.GL_LINE_SMOOTH_HINT, gl.GL_NICEST)
            # Smooth shading + hardware multisampling for cleaner edges. The
            # surface format already requests 4x samples (set in atom.py).
            try:
                gl.glShadeModel(gl.GL_SMOOTH)
                gl.glEnable(gl.GL_MULTISAMPLE)
                gl.glHint(gl.GL_POLYGON_SMOOTH_HINT, gl.GL_NICEST)
            except Exception:
                log.debug("Multisample/smooth-shading unavailable on this driver")

            _TEX_MGR.initialize()

            if _init_shaders():
                log.info("Using GLSL shaders (per-pixel 3-light)")
            else:
                log.info("Shader fallback — fixed-function lighting")
                gl.glEnable(gl.GL_LIGHTING)
                for i in range(3):
                    gl.glEnable(getattr(gl, f"GL_LIGHT{i}"))
                gl.glEnable(gl.GL_COLOR_MATERIAL)
                gl.glColorMaterial(gl.GL_FRONT_AND_BACK, gl.GL_AMBIENT_AND_DIFFUSE)
                # Mirrors the GLSL rig: warm key, cool fill, cool back rim, and
                # a dim ambient so the shadow side does not go pure black.
                gl.glLightModelfv(gl.GL_LIGHT_MODEL_AMBIENT, [.10, .10, .13, 1])
                gl.glLightfv(gl.GL_LIGHT0, gl.GL_POSITION, [7.5, 11.0, 8.0, 1])
                gl.glLightfv(gl.GL_LIGHT0, gl.GL_DIFFUSE, [.95, .84, .66, 1])
                gl.glLightfv(gl.GL_LIGHT0, gl.GL_SPECULAR, [.62, .55, .44, 1])
                gl.glLightfv(gl.GL_LIGHT1, gl.GL_POSITION, [-7.0, 3.0, 6.5, 1])
                gl.glLightfv(gl.GL_LIGHT1, gl.GL_DIFFUSE, [.24, .30, .44, 1])
                gl.glLightfv(gl.GL_LIGHT1, gl.GL_SPECULAR, [.16, .19, .28, 1])
                gl.glLightfv(gl.GL_LIGHT2, gl.GL_POSITION, [-3.0, 8.0, -9.0, 1])
                gl.glLightfv(gl.GL_LIGHT2, gl.GL_DIFFUSE, [.30, .36, .52, 1])
                gl.glLightfv(gl.GL_LIGHT2, gl.GL_SPECULAR, [0.0, 0.0, 0.0, 1])
                _shader_material("default")

            # Load glTF model (disabled — see the docstring in _load_gltf_model)
            self._load_gltf_model()

            # Load skin textures only when the PNG skins are switched on
            if self.use_skin_png:
                self._load_skin_textures(self.character)
                self._load_skin_png_texture(self.character)

        def resizeGL(self, w, h):
            gl.glViewport(0, 0, w, h)
            gl.glMatrixMode(gl.GL_PROJECTION)
            gl.glLoadIdentity()
            glu.gluPerspective(32, w / max(h, 1), 0.1, 50)
            gl.glMatrixMode(gl.GL_MODELVIEW)

        def paintGL(self):
            gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
            gl.glLoadIdentity()

            gl.glEnableClientState(gl.GL_VERTEX_ARRAY)
            gl.glEnableClientState(gl.GL_NORMAL_ARRAY)
            gl.glEnableClientState(gl.GL_TEXTURE_COORD_ARRAY)
            gl.glEnable(gl.GL_TEXTURE_2D)

            if _SHADER_PROG:
                _bind_shaders()  # uTime updated inside (saves one glUniform call)
            else:
                gl.glEnable(gl.GL_LIGHTING)

            p = self.profile
            frame = self.animation_frame % 60
            t = frame / 60.0
            is_ghost = self.character == "ghost_rider"

            # Camera orbit with emotion sway + subtle auto-rotation when idle
            sway = 0.0
            if self.emotion in ("happy", "excited"):
                sway = math.sin(t * 2 * math.pi) * 2
            elif self.emotion == "flirty":
                sway = math.sin(t * 1.5 * math.pi) * 3
            elif self.emotion == "neutral" and self.pose == "idle":
                # Gentle auto-rotation: ~0.17°/tick → full orbit every ~144s at 15 FPS
                self.cam_rotation += 0.17
                if self.cam_rotation > 360:
                    self.cam_rotation -= 360

            rot = math.radians(self.cam_rotation + sway)
            el = math.radians(self.cam_elevation)
            cx = self.cam_distance * math.cos(el) * math.sin(rot)
            cy = self.cam_distance * math.sin(el)
            cz = self.cam_distance * math.cos(el) * math.cos(rot)
            ty = 0.6 if self.emotion == "sad" else 0.5
            glu.gluLookAt(cx, cy + ty, cz, 0, ty, 0, 0, 1, 0)
            # Contact shading is defined relative to this camera.
            _shader_contact_level(cy + ty)

            # Shadow
            sh = 0.35
            if self.pose in ("walk", "run"):
                sh = 0.42
            if self.emotion in ("excited", "happy"):
                sh = 0.38
            if is_ghost:
                sh += 0.05
            self._draw_shadow(is_ghost, sh)

            gl.glPushMatrix()
            self._apply_transform(p, frame, t, is_ghost)
            self._draw_character(p, frame, t, is_ghost)
            gl.glPopMatrix()

            gl.glDisableClientState(gl.GL_VERTEX_ARRAY)
            gl.glDisableClientState(gl.GL_NORMAL_ARRAY)
            gl.glDisableClientState(gl.GL_TEXTURE_COORD_ARRAY)
            gl.glDisable(gl.GL_TEXTURE_2D)
            gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)
            self.frame_rendered.emit()

        # ── Shadow ──

        _SHADOW_RINGS = [
            (0.98, 0.78, 0.04), (0.90, 0.70, 0.07), (0.78, 0.60, 0.10),
            (0.65, 0.50, 0.13), (0.52, 0.40, 0.16), (0.38, 0.30, 0.19),
            (0.24, 0.18, 0.22), (0.12, 0.08, 0.25),
        ]

        def _ensure_shadow_dl(self, is_ghost: bool) -> int:
            """Return cached display list for shadow ellipse rings."""
            sc = (120, 110, 100) if is_ghost else (8, 8, 12)
            key = "ghost" if is_ghost else "normal"
            if not hasattr(self, '_shadow_dls'):
                self._shadow_dls = {}
            if key in self._shadow_dls:
                return self._shadow_dls[key]
            dl = gl.glGenLists(1)
            gl.glNewList(dl, gl.GL_COMPILE)
            for rx_mul, ry_mul, alpha in self._SHADOW_RINGS:
                _draw_ellipse(rx_mul, ry_mul, -0.88, sc, alpha, 22)
            gl.glEndList()
            self._shadow_dls[key] = dl
            return dl

        def _draw_shadow(self, is_ghost: bool, size: float) -> None:
            gl.glDisable(gl.GL_LIGHTING)
            gl.glDisable(gl.GL_DEPTH_TEST)
            if _SHADER_PROG:
                gl.glUseProgram(0)
            dl = self._ensure_shadow_dl(is_ghost)
            gl.glPushMatrix()
            gl.glScalef(size, 1.0, size)
            gl.glCallList(dl)
            gl.glPopMatrix()
            if _SHADER_PROG:
                gl.glUseProgram(_SHADER_PROG)
                gl.glUniform1f(_SHADER_UNIFORMS["uTime"], _time.time())
            gl.glEnable(gl.GL_DEPTH_TEST)
            gl.glEnable(gl.GL_LIGHTING)

        # ── Animation transform ──

        def _apply_transform(self, p, frame, t, is_ghost) -> None:
            bob = 0.0
            sway = 0.0
            e = self.emotion
            if e == "happy":
                bob = abs(math.sin(t * 3 * math.pi)) * 0.06
            elif e == "excited":
                bob = abs(math.sin(t * 5 * math.pi)) * 0.10
            elif e == "sad":
                bob = -0.04
                sway = math.sin(t * 1.5 * math.pi) * 0.02
            elif e == "flirty":
                bob = abs(math.sin(t * 2.5 * math.pi)) * 0.04
                sway = math.sin(t * 2 * math.pi) * 0.03
            elif e == "sleepy":
                bob = -0.02
                sway = math.sin(t * math.pi) * 0.04

            if self.pose in ("walk", "run"):
                spd = 2 if self.pose == "run" else 1
                bob = abs(math.sin(frame * 0.25 * spd)) * 0.12
            elif self.pose == "jump":
                bob = abs(math.sin(frame * 0.12)) * 0.30
            elif self.pose == "sleep":
                bob = -0.08
                gl.glRotatef(-12, 0, 0, 1)
            elif self.pose == "wave":
                bob = abs(math.sin(frame * 0.1)) * 0.04

            if is_ghost and e not in ("sad", "sleepy"):
                bob += abs(math.sin(t * 4 * math.pi)) * 0.03

            gl.glTranslatef(sway, bob, 0)
            if self.pose == "run":
                gl.glRotatef(5, 1, 0, 0)
            elif self.pose == "walk":
                gl.glRotatef(2, 1, 0, 0)

        # ── Character drawing ──

        def _draw_character(self, p, frame, t, is_ghost) -> None:
            if self._gltf_loaded:
                self._draw_character_gltf(p, frame, t, is_ghost)
                return
            self._draw_character_procedural(p, frame, t, is_ghost)

        def _draw_character_procedural(self, p, frame, t, is_ghost) -> None:
            """Original procedural block drawing — kept as fallback when glTF is unavailable."""
            f = frame % 60
            c = self._cache
            bs = c["bs"]
            bh = c["bh"]
            bhl = c["bhl"]
            bsh = c["bsh"]
            bp = c["bp"]
            bb = c["bb"]
            bbt = c["bbt"]
            sd = c["sd_color"]
            hair = c["hair_color"]
            shirt = c["shirt_color"]

            # Animation parameters
            leg_swing = 0.0
            arm_swing = 0.0
            wave_arm = 0.0
            pose = self.pose
            e = self.emotion

            if pose in ("walk", "run"):
                spd = 2.5 if pose == "run" else 1.2
                leg_swing = math.sin(f * 0.12 * spd) * 0.25
                arm_swing = -leg_swing
            elif pose == "wave":
                wave_arm = math.sin(f * 0.2) * 0.3 + 0.4
            elif pose == "listen":
                leg_swing = math.sin(f * 0.08) * 0.08
            elif pose == "talk":
                arm_swing = math.sin(f * 0.3) * 0.08
            elif pose == "bored":
                leg_swing = math.sin(f * 0.05) * 0.06
            elif pose in ("think", "confused"):
                arm_swing = math.sin(f * 0.08) * 0.05
            elif pose in ("surprised", "laugh"):
                arm_swing = math.sin(f * 0.15) * 0.10
            elif pose == "jump":
                leg_swing = math.sin(f * 0.1) * 0.10
                arm_swing = math.sin(f * 0.1) * 0.10
            elif e == "flirty":
                s = math.sin(t * 3 * math.pi)
                leg_swing = s * 0.06
                arm_swing = -s * 0.06

            # Ghost Rider runtime closures
            if is_ghost:
                fo = _c(p.get("flame_outer"), (200, 60, 10))
                fm = _c(p.get("flame_mid"), (255, 140, 40))
                fi = _c(p.get("flame_inner"), (255, 200, 50))
                chain = _c(p.get("chain_color"), (175, 165, 150))
                cd = _c(p.get("chain_dark"), (120, 115, 105))
                jr = _c(p.get("jacket_red"), (160, 25, 25))
                ef = (255, 100, 20)
                eh = (255, 220, 80)
                bch = _draw_block(chain, "metal")
                bcd = _draw_block(cd, "metal")
                bj = _draw_block(jr, "leather")
                bsk = _draw_block((200, 185, 165), "bone")
                bfo = _draw_block(fo, "fire")
                bfm = _draw_block(fm, "fire")
                bfi = _draw_block(fi, "fire")

            # ── Body proportions (Minecraft-accurate) ──
            # Head 8px, Body 12px, Arms 12px, Legs 12px (in block units)
            # Scale: 1 block unit = 8px in Minecraft skin
            head_w, head_h, head_d = 0.48, 0.48, 0.48
            bw, bh_body, bd = 0.48, 0.64, 0.24
            body_cy = -0.04
            arm_w, arm_h, arm_d = 0.20, 0.56, 0.20
            leg_w, leg_h, leg_d = 0.22, 0.48, 0.22

            # ── Body ──
            # Coordinates are block centres (the cube geometry is centred on
            # the origin), so the torso sits at x=0. It used to be passed as
            # -bw/2 (a left-edge value), which shifted the whole figure half a
            # body to the left and left the right arm floating in space.
            c["b_body"](0, body_cy, 0, bw, bh_body, bd)
            _draw_flat(sd)(0, body_cy + 0.04, 0.10, bw - 0.08, 0.04, 0.04)

            if is_ghost:
                bj(0, body_cy, 0.10, bw, bh_body, 0.04)
                seam = _c(p.get("shirt_trim"), (50, 45, 55))
                _draw_flat(seam)(-bw/2 - 0.04, body_cy - 0.06, 0, 0.04, 0.08, 0.04)
                _draw_flat(seam)(bw/2, body_cy - 0.06, 0, 0.04, 0.08, 0.04)
                cs2 = math.sin(f * 0.06) * 0.03
                for cx in range(5):
                    bch(-0.12 + cx * 0.06, body_cy - 0.02 + (cx % 3) * 0.04 + math.sin(f * 0.08 + cx) * 0.03 + cs2, 0, 0.05, 0.03, 0.05)
                _draw_flat((60, 55, 60))(-bw/2 - 0.04, body_cy - 0.10, 0, 0.06, 0.06, 0.06)
                _draw_flat((60, 55, 60))(bw/2 - 0.02, body_cy - 0.10, 0, 0.06, 0.06, 0.06)
            elif self.character in ("steve", "alex"):
                trim = _c(p.get("shirt_trim"), shirt)
                _draw_flat(trim)(-bw/2, body_cy + bh_body/2 - 0.02, -bd/2 + 0.02, bw, 0.03, bd - 0.04)

            # ── Legs ──
            llx = -leg_w/2 - 0.02 + leg_swing * 0.12
            lrx = leg_w/2 + 0.02 - leg_swing * 0.12
            lo = abs(leg_swing) * 0.04
            leg_cy = body_cy - bh_body/2 - leg_h/2
            c["b_leg"](llx, leg_cy + lo, 0, leg_w, leg_h, leg_d)
            c["b_leg"](lrx, leg_cy, 0, leg_w, leg_h, leg_d)
            c["b_boot"](llx, leg_cy - leg_h/2 - 0.04 + lo, 0, leg_w + 0.02, 0.08, leg_d + 0.02)
            c["b_boot"](lrx, leg_cy - leg_h/2 - 0.04, 0, leg_w + 0.02, 0.08, leg_d + 0.02)

            if is_ghost:
                cs = math.sin(f * 0.08) * 0.04
                for cx in range(3):
                    bch(-0.08 + cx * 0.05, body_cy - bh_body/2 + cs + (cx % 2) * 0.04, 0, 0.05, 0.03, 0.05)

            # ── Arms ──
            alx = -bw/2 - arm_w/2
            arx = bw/2 + arm_w/2
            arm_cy = body_cy + bh_body/2 - arm_h/2  # shoulder joint (block centre)
            idle_b = abs(math.sin(f * 0.12)) * 0.012 if pose == "idle" else 0

            def _simp_arm(ax, swing):
                c["b_arm"](ax + swing * 0.08, arm_cy + abs(swing) * 0.02, 0, arm_w, arm_h, arm_d)

            if pose == "wave":
                _simp_arm(alx, 0)
                c["b_arm"](arx + 0.02, arm_cy + wave_arm * 0.18 + idle_b, 0, arm_w + 0.02, arm_h * 1.1, arm_d + 0.02)
            elif pose == "listen":
                _simp_arm(alx, 0)
                c["b_arm"](arx, arm_cy + 0.10 + idle_b, 0, arm_w, arm_h * 0.8, arm_d)
            elif pose == "bored":
                c["b_arm"](alx + 0.06, arm_cy - 0.04 + idle_b, 0.06, arm_w, arm_h * 0.6, arm_d)
                c["b_arm"](arx - 0.06, arm_cy - 0.04 + idle_b, 0.06, arm_w, arm_h * 0.6, arm_d)
            elif pose in ("think", "confused"):
                _simp_arm(alx, 0)
                c["b_arm"](arx, arm_cy + 0.10 + idle_b, 0, arm_w, arm_h * 0.7, arm_d)
            elif pose in ("surprised", "laugh"):
                lift = 0.08
                _simp_arm(alx, 0)
                c["b_arm"](arx, arm_cy + lift + idle_b, 0, arm_w, arm_h, arm_d)
            elif e in ("happy", "excited") and pose not in ("walk", "run"):
                lift = 0.06 if e == "happy" else 0.10
                _simp_arm(alx, 0)
                bs(arx, arm_cy + lift + idle_b, 0, arm_w, arm_h, arm_d)
            else:
                _simp_arm(alx, arm_swing + idle_b)
                _simp_arm(arx, -arm_swing - idle_b)

            # Ghost Rider arm flames
            if is_ghost and e not in ("sad", "sleepy"):
                fi2 = 1.0
                if e in ("excited", "happy", "annoyed"):
                    fi2 = 1.5
                fa = min(0.35, 0.12 * fi2 + 0.08 * fi2 * abs(math.sin(t * 12 * math.pi)))
                if _SHADER_PROG:
                    gl.glUseProgram(0)
                for ax in [alx, arx]:
                    pts = [(ax + (0.06 + 0.02 * math.sin(t * 10 * math.pi + i * 0.8)) * math.cos(2 * math.pi * i / 8),
                            arm_cy + (0.06 + 0.02 * math.sin(t * 10 * math.pi + i * 0.8)) * math.sin(2 * math.pi * i / 8),
                            (0.06 + 0.02 * math.sin(t * 10 * math.pi + i * 0.8)) * 0.5) for i in range(8)]
                    _draw_tri_fan((ax, arm_cy, 0), pts, fm, fa)
                if _SHADER_PROG:
                    _bind_shaders()

            # ── Head ──
            hb = abs(math.sin(f * 0.12)) * 0.006 if pose == "idle" else idle_b * 0.5
            hy = body_cy + bh_body / 2 + head_h / 2 + hb

            if is_ghost:
                bsk(-head_w/2, hy, -head_d/2, head_w, head_h, head_d)
                _draw_flat((180, 165, 145))(-head_w/2, hy, -head_d/2 + 0.03, head_w, 0.03, head_d - 0.06)
                _draw_flat((160, 145, 125))(-0.04, hy + head_h * 0.15, head_d/2, 0.08, 0.04, 0.04)
                _draw_flat((185, 170, 150))(-head_w/2, hy + head_h/2 - 0.04, -head_d/2, head_w, 0.06, head_d)
            else:
                # Draw head with per-face textures from skin PNG (or procedural fallback)
                # Front face + back + sides
                c["b_head_front"](0, hy, 0, head_w, head_h, head_d)
                # ── Hair: a real cap, not a hairline ──
                # The 2D design has the top ~5px of the head as hair. Drawing
                # only a thin strip left the 3D model looking bald.
                hair_v = c["hair_color"]
                hl_v = c["hl_color"]
                _draw_flat(hair_v)(0, hy + head_h/2 - 0.10, head_d/2 - 0.01, head_w, 0.10, 0.03)   # fringe
                _draw_flat(hair_v)(0, hy + head_h/2 - 0.01, 0, head_w + 0.02, 0.06, head_d + 0.02)  # cap
                _draw_flat(hair_v)(0, hy, -head_d/2 - 0.01, head_w + 0.02, head_h, 0.03)          # back
                _draw_flat(hair_v)(-(head_w/2 + 0.01), hy + 0.03, 0, 0.03, head_h * 0.6, head_d)  # left side
                _draw_flat(hair_v)(head_w/2 + 0.01, hy + 0.03, 0, 0.03, head_h * 0.6, head_d)     # right side
                _draw_flat(hl_v)(0, hy + head_h/2 + 0.03, 0, head_w * 0.6, 0.02, head_d * 0.5)    # highlight
                _draw_flat(hair_v)(-head_w/2 - 0.02, hy - 0.02, -head_d/4, 0.04, 0.10, head_d/2)
                _draw_flat(hair)(head_w/2, hy - 0.02, -head_d/4, 0.04, 0.10, head_d/2)

            # Ghost Rider fire hair
            if is_ghost:
                fl = abs(math.sin(t * 18 * math.pi)) * 0.03
                fl2 = abs(math.sin(t * 20 * math.pi)) * 0.03
                bfo(-0.22, hy + 0.30 + fl, -0.12, 0.44, 0.08, 0.24)
                bfm(-0.18, hy + 0.32 + fl2, -0.08, 0.36, 0.06, 0.16)
                bfi(-0.10, hy + 0.34 + fl, -0.04, 0.20, 0.04, 0.08)
                if _SHADER_PROG:
                    gl.glUseProgram(0)
                for side in [-1, 1]:
                    wx = side * 0.22
                    for w in range(2):
                        wy = 0.32 + w * 0.08 + math.sin(t * 14 * math.pi + side * w) * 0.03
                        wa = 0.15 - w * 0.04
                        pts = [(wx + (0.04 + 0.01 * math.sin(t * 12 * math.pi + side + w + i)) * math.cos(2 * math.pi * i / 8),
                                wy + (0.04 + 0.01 * math.sin(t * 12 * math.pi + side + w + i)) * 0.5 * math.sin(2 * math.pi * i / 8),
                                (0.04 + 0.01 * math.sin(t * 12 * math.pi + side + w + i)) * math.sin(2 * math.pi * i / 8)) for i in range(8)]
                        _draw_tri_fan((wx, wy, 0), pts, fm, wa)
                if _SHADER_PROG:
                    _bind_shaders()

            # Character-specific accessories
            if self.character == "pink":
                pt = _c(p.get("hair_light"), (255, 185, 215))
                _draw_flat(pt)(-0.34, hy + 0.02, 0, 0.12, 0.20, 0.12)
                _draw_flat(pt)(-0.32, hy + 0.20, 0, 0.10, 0.12, 0.10)
                _draw_flat(pt)(0.22, hy + 0.04, 0, 0.12, 0.18, 0.12)
                _draw_flat(pt)(0.24, hy + 0.20, 0, 0.10, 0.12, 0.10)
                if "ribbon_color" in p:
                    rb = _c(p["ribbon_color"])
                    _draw_flat(rb)(-0.08, hy + 0.32, 0, 0.18, 0.04, 0.08)
                    _draw_flat(rb)(-0.12, hy + 0.30, 0, 0.08, 0.03, 0.06)
                    _draw_flat(rb)(0.04, hy + 0.30, 0, 0.08, 0.03, 0.06)
            elif self.character == "alex":
                pn = _c(p.get("ponytail_color"), hair)
                _draw_flat(pn)(0.26, hy + 0.04, 0, 0.12, 0.20, 0.12)
                _draw_flat(_c(p.get("ponytail_band_color"), (160, 100, 30)))(0.26, hy + 0.02, 0, 0.12, 0.04, 0.12)
                _draw_flat(pn)(0.28, hy + 0.22, 0, 0.10, 0.16, 0.10)
                _draw_flat(pn)(0.29, hy + 0.36, 0, 0.08, 0.12, 0.08)

            # Horns + cherry
            if not is_ghost:
                horn = p.get("horn")
                if horn is not None and self.character in ("atom", "echo"):
                    hc = _c(horn)
                    _draw_flat(hc)(-0.22, hy + 0.26, 0, 0.12, 0.14, 0.12)
                    _draw_flat(hc)(0.10, hy + 0.26, 0, 0.12, 0.14, 0.12)
                    _draw_flat((245, 236, 210))(-0.20, hy + 0.36, 0, 0.08, 0.04, 0.08)
                    _draw_flat((245, 236, 210))(0.12, hy + 0.36, 0, 0.08, 0.04, 0.08)
                ch = p.get("cherry")
                if ch is not None and self.character in ("atom", "echo"):
                    cc = _c(ch)
                    _draw_flat(cc)(-0.06, hy + 0.34, 0, 0.12, 0.12, 0.12)
                    _draw_flat((60, 120, 40))(-0.02, hy + 0.44, 0, 0.04, 0.04, 0.04)
                    _draw_flat((255, 200, 200, 80))(-0.03, hy + 0.36, 0.08, 0.04, 0.04, 0.03)

            if is_ghost:
                _draw_flat((190, 175, 155))(-0.20, hy + 0.28, 0, 0.10, 0.12, 0.10)
                _draw_flat((190, 175, 155))(0.10, hy + 0.28, 0, 0.10, 0.12, 0.10)
                _draw_flat((210, 200, 180))(-0.18, hy + 0.38, 0, 0.06, 0.04, 0.06)
                _draw_flat((210, 200, 180))(0.12, hy + 0.38, 0, 0.06, 0.04, 0.06)

            # ── Eyes ──
            if is_ghost:
                pu = 0.7 + 0.3 * math.sin(t * 12 * math.pi)
                r = int(eh[0] * pu + fi[1] * (1 - pu))
                g2 = int(eh[1] * pu + fi[1] * (1 - pu))
                b2 = int(eh[2] * pu + fi[1] * (1 - pu))
                if _SHADER_PROG:
                    gl.glUseProgram(0)
                ga = 0.25 * pu
                gl.glColor4f(eh[0]/255, eh[1]/255, eh[2]/255, ga)
                for ex in [-0.10, 0.10]:
                    pts = [(ex + 0.05 * math.cos(2*math.pi*i/8), hy + 0.10 + 0.05 * math.sin(2*math.pi*i/8), 0.20) for i in range(8)]
                    _draw_tri_fan((ex, hy + 0.10, 0.20), pts, ef, ga)
                if _SHADER_PROG:
                    _bind_shaders()
                _draw_flat((r, g2, b2))(-0.12, hy + 0.10, 0.22, 0.10, 0.08, 0.06)
                _draw_flat((r, g2, b2))(0.02, hy + 0.10, 0.22, 0.10, 0.08, 0.06)
                _draw_flat(eh)(-0.10, hy + 0.11, 0.22, 0.04, 0.04, 0.04)
                _draw_flat(eh)(0.04, hy + 0.11, 0.22, 0.04, 0.04, 0.04)
            else:
                ec = _c(p.get("eye_color"), (20, 20, 28))
                ehc = _c(p.get("eye_highlight"), (180, 200, 240))
                _draw_flat((240, 240, 248))(-0.14, hy + 0.09, 0.22, 0.14, 0.10, 0.06)
                _draw_flat((240, 240, 248))(0, hy + 0.09, 0.22, 0.14, 0.10, 0.06)
                _draw_flat(ec)(-0.12, hy + 0.10, 0.23, 0.10, 0.08, 0.04)
                _draw_flat(ec)(0.02, hy + 0.10, 0.23, 0.10, 0.08, 0.04)
                _draw_flat(ehc)(-0.10, hy + 0.11, 0.24, 0.04, 0.04, 0.03)
                _draw_flat(ehc)(0.04, hy + 0.11, 0.24, 0.04, 0.04, 0.03)

            # ── Eyebrows — mirrors the 2D sprite so both render paths read the
            # emotion the same way. Sits just above the eye whites.
            if not is_ghost:
                _bv = _c(p.get("hair"), (70, 48, 38))
                _brow = (max(0, _bv[0] - 25), max(0, _bv[1] - 25), max(0, _bv[2] - 25))
                if e in ("sad", "confused"):
                    _draw_flat(_brow)(-0.14, hy + 0.135, 0.22, 0.14, 0.02, 0.06)
                    _draw_flat(_brow)(0.00, hy + 0.148, 0.22, 0.14, 0.02, 0.06)
                elif e in ("annoyed", "excited"):
                    _draw_flat(_brow)(-0.14, hy + 0.126, 0.22, 0.14, 0.02, 0.06)
                    _draw_flat(_brow)(0.00, hy + 0.126, 0.22, 0.14, 0.02, 0.06)
                else:
                    _draw_flat(_brow)(-0.14, hy + 0.140, 0.22, 0.14, 0.02, 0.06)
                    _draw_flat(_brow)(0.00, hy + 0.140, 0.22, 0.14, 0.02, 0.06)

            # ── Mouth ──
            mh = 0.04 if pose == "talk" and int(f / 4) % 2 == 0 else 0.02
            if e == "happy":
                _draw_flat((75, 35, 45))(0, hy + 0.18, 0.22, 0.10, 0.02, 0.03)
                _draw_flat((80, 36, 48))(0, hy + 0.17, 0.23, 0.06, 0.02, 0.02)
            elif e == "sad":
                gl.glDisable(gl.GL_LIGHTING)
                if _SHADER_PROG:
                    gl.glUseProgram(0)
                gl.glColor3f(0.3, 0.14, 0.18)
                gl.glBegin(gl.GL_LINES)
                gl.glVertex3f(-0.05, hy + 0.20, 0.23)
                gl.glVertex3f(0, hy + 0.18, 0.23)
                gl.glVertex3f(0, hy + 0.18, 0.23)
                gl.glVertex3f(0.05, hy + 0.20, 0.23)
                gl.glEnd()
                if _SHADER_PROG:
                    _bind_shaders()
                gl.glEnable(gl.GL_LIGHTING)
            elif is_ghost:
                _draw_flat((30, 20, 15))(-0.05, hy + 0.18, 0.22, 0.10, 0.03, 0.03)
                gl.glDisable(gl.GL_LIGHTING)
                if _SHADER_PROG:
                    gl.glUseProgram(0)
                gl.glColor3f(0.78, 0.72, 0.65)
                gl.glBegin(gl.GL_LINES)
                gl.glVertex3f(-0.03, hy + 0.19, 0.23)
                gl.glVertex3f(-0.03, hy + 0.20, 0.23)
                gl.glVertex3f(0.03, hy + 0.19, 0.23)
                gl.glVertex3f(0.03, hy + 0.20, 0.23)
                gl.glEnd()
                if _SHADER_PROG:
                    _bind_shaders()
                gl.glEnable(gl.GL_LIGHTING)
            else:
                _draw_flat((75, 35, 45))(0, hy + 0.18, 0.22, 0.10, mh, 0.03)
                if pose == "talk" and int(f / 4) % 2 == 0:
                    _draw_flat((55, 25, 35))(0, hy + 0.18, 0.23, 0.06, 0.03, 0.02)

            # Blush
            if e in ("flirty", "happy") or pose == "blush":
                bl = _c(p.get("blush_color"), (255, 150, 150))
                if _SHADER_PROG:
                    gl.glUseProgram(0)
                for bx in [-0.18, 0.18]:
                    pts = [(bx + 0.05 * math.cos(2*math.pi*i/8), hy + 0.15 + 0.03 * math.sin(2*math.pi*i/8), 0.20) for i in range(8)]
                    _draw_tri_fan((bx, hy + 0.15, 0.20), pts, bl, 0.45)
                if _SHADER_PROG:
                    _bind_shaders()

            # Ghost Rider fiery aura
            if is_ghost and e not in ("sad", "sleepy"):
                eb = 1.0
                if e in ("excited", "happy"):
                    eb = 1.6
                elif e == "annoyed":
                    eb = 1.3
                au = 0.5 + 0.5 * math.sin(t * 3 * math.pi)
                am = min(0.15, (0.03 + 0.04 * au) * eb)
                ai = min(0.10, (0.02 + 0.03 * au) * eb)
                if _SHADER_PROG:
                    gl.glUseProgram(0)
                _draw_ellipse(0.55, 0.55, 0, fo, am, 20)
                _draw_ellipse(0.35, 0.35, 0.05, fm, ai, 16)
                if _SHADER_PROG:
                    _bind_shaders()
                ga2 = max(0.03, (0.02 + 0.03 * math.sin(t * 2 * math.pi)) * eb)
                if _SHADER_PROG:
                    gl.glUseProgram(0)
                _draw_ellipse(0.45 + 0.05 * math.sin(t * 3 * math.pi),
                              0.33 + 0.04 * math.sin(t * 3 * math.pi), -0.88, fo, ga2, 18)
                if _SHADER_PROG:
                    _bind_shaders()

            # Pink skirt
            if self.character == "pink":
                sc2 = (shirt[0] + 40, shirt[1] + 25, shirt[2] + 25)
                skirt_y = body_cy - bh_body / 2 - 0.04
                _draw_flat(sc2)(0, skirt_y, 0, bw - 0.04, 0.10, bd)
                _draw_flat((sc2[0]+20, sc2[1]+15, sc2[2]+15))(0, skirt_y + 0.02, 0, bw - 0.08, 0.04, bd - 0.04)

        # ── glTF-based character drawing ──

        def _draw_character_gltf(self, p, frame, t, is_ghost) -> None:
            """Draw character using loaded glTF meshes with Minecraft skin textures."""
            f = frame % 60
            c = self._cache
            leg_swing = 0.0
            arm_swing = 0.0
            wave_arm = 0.0
            pose = self.pose
            e = self.emotion
            if pose in ("walk", "run"):
                spd = 2.5 if pose == "run" else 1.2
                leg_swing = math.sin(f * 0.12 * spd) * 0.25
                arm_swing = -leg_swing
            elif pose == "wave":
                wave_arm = math.sin(f * 0.2) * 0.3 + 0.4
            elif pose == "listen":
                leg_swing = math.sin(f * 0.08) * 0.08
            elif pose == "talk":
                arm_swing = math.sin(f * 0.3) * 0.08
            elif pose == "bored":
                leg_swing = math.sin(f * 0.05) * 0.06
            elif pose in ("think", "confused"):
                arm_swing = math.sin(f * 0.08) * 0.05
            elif pose in ("surprised", "laugh"):
                arm_swing = math.sin(f * 0.15) * 0.10
            elif pose == "jump":
                leg_swing = math.sin(f * 0.1) * 0.10
                arm_swing = math.sin(f * 0.1) * 0.10
            elif e == "flirty":
                s = math.sin(t * 3 * math.pi)
                leg_swing = s * 0.06
                arm_swing = -s * 0.06
            if is_ghost:
                fm = _c(p.get("flame_mid"), (255, 140, 40))
                chain = _c(p.get("chain_color"), (175, 165, 150))
                jr = _c(p.get("jacket_red"), (160, 25, 25))
                bch = _draw_block(chain, "metal")
                bj = _draw_block(jr, "leather")
                bfo = _draw_block(fm, "fire")
            gl.glEnableClientState(gl.GL_VERTEX_ARRAY)
            gl.glEnableClientState(gl.GL_NORMAL_ARRAY)
            gl.glEnableClientState(gl.GL_TEXTURE_COORD_ARRAY)
            gl.glEnable(gl.GL_TEXTURE_2D)
            self._gltf_model.bind()
            if _SHADER_PROG:
                _bind_shaders()
            _TEX_MGR.bind_tex("skin")
            if "gltf_skin" in _TEX_MGR._tex:
                gl.glBindTexture(gl.GL_TEXTURE_2D, _TEX_MGR._tex.get("gltf_skin", 0))
            if _SHADER_PROG:
                _shader_tex(True)
                _shader_color((255, 255, 255))
            body_y = 0.75
            _shader_material(_MESH_MATERIALS[1])
            self._gltf_model.draw_mesh(1)
            if is_ghost:
                bj(-0.22, body_y - 0.32, -0.14, 0.44, 0.42, 0.06)
            hb = abs(math.sin(f * 0.12)) * 0.006 if pose == "idle" else 0
            head_y = body_y + 0.55 + hb
            # Atom's face is a dark visor, so it gets the glossier material.
            head_material = "visor" if self.character in ("atom", "echo") else _MESH_MATERIALS[0]
            gl.glPushMatrix()
            gl.glTranslatef(0, head_y, 0)
            _shader_material(head_material)
            self._gltf_model.draw_mesh(0)
            gl.glPopMatrix()
            # ── Hair layer (skin layer 2) — alpha-tested so the face shows through ──
            if self._gltf_model.mesh_count > 6:
                gl.glPushMatrix()
                gl.glTranslatef(0, head_y, 0)
                _shader_material(_MESH_MATERIALS[6])
                _shader_alpha_cut(0.5)
                self._gltf_model.draw_mesh(6)
                _shader_alpha_cut(0.0)
                gl.glPopMatrix()
            # ── Eye glow (disable shader for immediate-mode glow quads) ──
            eye_bright = 0.6 + 0.4 * abs(math.sin(t * 2 * math.pi))
            eye_col = (int(80 + 175 * eye_bright), int(200 + 55 * eye_bright), 255)
            gl.glPushMatrix()
            gl.glTranslatef(0, head_y, 0)
            if _SHADER_PROG:
                gl.glUseProgram(0)
            gl.glDisable(gl.GL_LIGHTING)
            gl.glDisableClientState(gl.GL_NORMAL_ARRAY)
            gl.glDisableClientState(gl.GL_TEXTURE_COORD_ARRAY)
            # Left eye (bright core)
            gl.glColor4f(eye_col[0]/255, eye_col[1]/255, eye_col[2]/255, 0.6 + 0.4 * eye_bright)
            gl.glBegin(gl.GL_QUADS)
            gl.glVertex3f(-0.18, 0.06, 0.201)
            gl.glVertex3f(-0.06, 0.06, 0.201)
            gl.glVertex3f(-0.06, -0.04, 0.201)
            gl.glVertex3f(-0.18, -0.04, 0.201)
            # Right eye
            gl.glVertex3f(0.06, 0.06, 0.201)
            gl.glVertex3f(0.18, 0.06, 0.201)
            gl.glVertex3f(0.18, -0.04, 0.201)
            gl.glVertex3f(0.06, -0.04, 0.201)
            gl.glEnd()
            # Eye glow aura (larger, semi-transparent)
            gl.glColor4f(0.2, 0.5, 1.0, 0.12 * eye_bright)
            gl.glBegin(gl.GL_QUADS)
            gl.glVertex3f(-0.22, 0.10, 0.202)
            gl.glVertex3f(-0.02, 0.10, 0.202)
            gl.glVertex3f(-0.02, -0.08, 0.202)
            gl.glVertex3f(-0.22, -0.08, 0.202)
            gl.glVertex3f(0.02, 0.10, 0.202)
            gl.glVertex3f(0.22, 0.10, 0.202)
            gl.glVertex3f(0.22, -0.08, 0.202)
            gl.glVertex3f(0.02, -0.08, 0.202)
            gl.glEnd()
            gl.glEnable(gl.GL_LIGHTING)
            gl.glEnableClientState(gl.GL_NORMAL_ARRAY)
            gl.glEnableClientState(gl.GL_TEXTURE_COORD_ARRAY)
            if _SHADER_PROG:
                _bind_shaders()
                gl.glUniform1f(_SHADER_UNIFORMS["uTime"], _time.time())
            gl.glPopMatrix()
            lo = abs(leg_swing) * 0.04
            leg_y = body_y - 0.45
            gl.glPushMatrix()
            gl.glTranslatef(-0.125, leg_y + lo, 0)
            gl.glTranslatef(0, 0.30, 0)
            gl.glRotatef(leg_swing * 60, 0, 0, 1)
            gl.glTranslatef(0, -0.30, 0)
            _shader_material(_MESH_MATERIALS[4])
            self._gltf_model.draw_mesh(4)
            gl.glPopMatrix()
            gl.glPushMatrix()
            gl.glTranslatef(0.125, leg_y, 0)
            gl.glTranslatef(0, 0.30, 0)
            gl.glRotatef(-leg_swing * 60, 0, 0, 1)
            gl.glTranslatef(0, -0.30, 0)
            _shader_material(_MESH_MATERIALS[5])
            self._gltf_model.draw_mesh(5)
            gl.glPopMatrix()
            mid_b = abs(math.sin(f * 0.12)) * 0.012 if pose == "idle" else 0
            gl.glPushMatrix()
            gl.glTranslatef(-0.375, body_y, 0)
            gl.glTranslatef(0, 0.30, 0)
            if pose == "wave":
                gl.glRotatef(-30 + wave_arm * 45, 0, 0, 1)
            else:
                gl.glRotatef((arm_swing + mid_b) * 30, 0, 0, 1)
            gl.glTranslatef(0, -0.30, 0)
            _shader_material(_MESH_MATERIALS[2])
            self._gltf_model.draw_mesh(2)
            gl.glPopMatrix()
            gl.glPushMatrix()
            gl.glTranslatef(0.375, body_y, 0)
            gl.glTranslatef(0, 0.30, 0)
            if pose == "wave":
                gl.glRotatef(-30 + wave_arm * 45, 0, 0, 1)
            elif pose == "listen":
                gl.glRotatef(20, 0, 0, 1)
            elif e in ("happy", "excited"):
                gl.glRotatef(-15, 0, 0, 1)
            else:
                gl.glRotatef((-arm_swing - mid_b) * 30, 0, 0, 1)
            gl.glTranslatef(0, -0.30, 0)
            _shader_material(_MESH_MATERIALS[3])
            self._gltf_model.draw_mesh(3)
            gl.glPopMatrix()
            gl.glBindBuffer(gl.GL_ARRAY_BUFFER, 0)
            gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, 0)
            if _SHADER_PROG:
                _shader_tex(False)
            if is_ghost and e not in ("sad", "sleepy"):
                fi2 = 1.0
                if e in ("excited", "happy", "annoyed"):
                    fi2 = 1.5
                fa = min(0.35, 0.12 * fi2 + 0.08 * fi2 * abs(math.sin(t * 12 * math.pi)))
                if _SHADER_PROG:
                    gl.glUseProgram(0)
                for ax in [-0.375, 0.375]:
                    pts = [(ax + (0.06 + 0.02 * math.sin(t * 10 * math.pi + i * 0.8)) * math.cos(2 * math.pi * i / 8),
                            body_y + (0.06 + 0.02 * math.sin(t * 10 * math.pi + i * 0.8)) * math.sin(2 * math.pi * i / 8),
                            (0.06 + 0.02 * math.sin(t * 10 * math.pi + i * 0.8)) * 0.5) for i in range(8)]
                    _draw_tri_fan((ax, body_y, 0), pts, fm, fa)
                if _SHADER_PROG:
                    _bind_shaders()
            if is_ghost:
                bfo(-0.22, head_y + 0.25, -0.12, 0.44, 0.08, 0.24)
                if _SHADER_PROG:
                    gl.glUseProgram(0)
                for side in [-1, 1]:
                    wx = side * 0.22
                    for w in range(2):
                        wy = 0.32 + w * 0.08 + math.sin(t * 14 * math.pi + side * w) * 0.03
                        wa = 0.15 - w * 0.04
                        pts = [(wx + (0.04 + 0.01 * math.sin(t * 12 * math.pi + side + w + i)) * math.cos(2 * math.pi * i / 8),
                                body_y + wy + (0.04 + 0.01) * 0.5 * math.sin(2 * math.pi * i / 8),
                                (0.04 + 0.01 * math.sin(t * 12 * math.pi + side + w + i)) * math.sin(2 * math.pi * i / 8)) for i in range(8)]
                        _draw_tri_fan((wx, body_y + wy, 0), pts, fm, wa)
                if _SHADER_PROG:
                    _bind_shaders()
            if self.character == "pink":
                pt = _c(p.get("hair_light"), (255, 185, 215))
                _draw_flat(pt)(-0.34, head_y + 0.02, 0, 0.12, 0.20, 0.12)
                _draw_flat(pt)(-0.32, head_y + 0.20, 0, 0.10, 0.12, 0.10)
                _draw_flat(pt)(0.22, head_y + 0.04, 0, 0.12, 0.18, 0.12)
                _draw_flat(pt)(0.24, head_y + 0.20, 0, 0.10, 0.12, 0.10)
                if "ribbon_color" in p:
                    rb = _c(p["ribbon_color"])
                    _draw_flat(rb)(-0.08, head_y + 0.32, 0, 0.18, 0.04, 0.08)
                    _draw_flat(rb)(-0.12, head_y + 0.30, 0, 0.08, 0.03, 0.06)
                    _draw_flat(rb)(0.04, head_y + 0.30, 0, 0.08, 0.03, 0.06)
            elif self.character == "alex":
                hair_c = _c(p.get("hair"), c.get("hair_color", (80, 55, 40)))
                pn = _c(p.get("ponytail_color"), hair_c)
                _draw_flat(pn)(0.26, head_y + 0.04, 0, 0.12, 0.20, 0.12)
                _draw_flat(_c(p.get("ponytail_band_color"), (160, 100, 30)))(0.26, head_y + 0.02, 0, 0.12, 0.04, 0.12)
                _draw_flat(pn)(0.28, head_y + 0.22, 0, 0.10, 0.16, 0.10)
                _draw_flat(pn)(0.29, head_y + 0.36, 0, 0.08, 0.12, 0.08)
            if not is_ghost:
                horn = p.get("horn")
                if horn is not None and self.character in ("atom", "echo"):
                    hc = _c(horn)
                    _draw_flat(hc)(-0.22, head_y + 0.26, 0, 0.12, 0.14, 0.12)
                    _draw_flat(hc)(0.10, head_y + 0.26, 0, 0.12, 0.14, 0.12)
                    _draw_flat((245, 236, 210))(-0.20, head_y + 0.36, 0, 0.08, 0.04, 0.08)
                    _draw_flat((245, 236, 210))(0.12, head_y + 0.36, 0, 0.08, 0.04, 0.08)
                ch = p.get("cherry")
                if ch is not None and self.character in ("atom", "echo"):
                    cc = _c(ch)
                    _draw_flat(cc)(-0.06, head_y + 0.34, 0, 0.12, 0.12, 0.12)
                    _draw_flat((60, 120, 40))(-0.02, head_y + 0.44, 0, 0.04, 0.04, 0.04)
                    _draw_flat((255, 200, 200))(-0.02, head_y + 0.48, 0, 0.04, 0.04, 0.04)

        def _load_gltf_model(self) -> None:
            """Load the atom.gltf model: parse JSON + create VBOs.

            Disabled on purpose: ``assets/models/atom.gltf`` has a cyclic node
            graph (Body lists itself as a child) and its UVs do not line up with
            the skin atlas, so drawing it produced a shredded figure — head with
            the torso texture, black torso, arms floating beside the body. The
            procedural renderer below builds the character from the same skin
            atlas with per-face textures, so it is both correct and richer
            (character accessories, ghost-rider flame). Keep the parser for the
            glTF tests, but never draw from it.
            """
            self._gltf_loaded = False

        def _load_skin_png_texture(self, character: str) -> None:
            """Load Minecraft skin PNG and create per-body-part textures from it.

            Extracts each body region from the 64×64 skin atlas and creates
            separate OpenGL textures so the block renderer can map them
            correctly.
            """
            if not _TEX_MGR.ready:
                return
            skin_file = _SKIN_FILES.get(character, "atom_skin.png")
            skin_path = ROOT / "assets" / "skins" / skin_file
            if not skin_path.exists():
                log.debug("Skin PNG not found: %s -- using procedural skin", skin_path)
                return
            try:
                full_img = QImage(str(skin_path))
                if full_img.isNull():
                    log.warning("Failed to load skin PNG: %s", skin_path)
                    return

                skin_64 = _normalise_skin_atlas(full_img)

                # Keep full skin for glTF path
                _TEX_MGR.replace_tex_from_qimage("gltf_skin", skin_64)

                # Extract each body region as a separate texture
                # Group regions by body part for simpler texture names
                body_tex_map = {
                    # Head uses the 8x8 face texture
                    "skin_head_front": "head_front",
                    "skin_head_side": "head_right",   # right = left side on display
                    "skin_head_top": "head_top",
                    "skin_head_back": "head_back",
                    # Body uses the 8×12 torso area
                    "skin_body": "body_front",
                    "skin_body_side": "body_right",
                    "skin_body_top": "body_top",
                    # Arms (4×12)
                    "skin_arm": "arm_r_front",
                    "skin_arm_side": "arm_r_right",
                    "skin_arm_top": "arm_r_top",
                    # Legs (4×12)
                    "skin_leg": "leg_r_front",
                    "skin_leg_side": "leg_r_right",
                    "skin_leg_top": "leg_r_top",
                }

                for tex_name, region_name in body_tex_map.items():
                    region = _SKIN_REGIONS.get(region_name)
                    if region is None:
                        continue
                    rx, ry, rw, rh = region
                    region_img = skin_64.copy(rx, ry, rw, rh)
                    if not region_img.isNull():
                        _TEX_MGR.replace_tex_from_qimage(tex_name, region_img)

                log.debug("Loaded %d body-part textures from %s", len(body_tex_map), skin_path)
            except Exception as e:
                log.warning("Error loading skin PNG textures: %s", e)

        def _animate(self) -> None:
            if self.pose == "sleep":
                self._idle_frames += 1
                if self._idle_frames % 7 != 0:
                    return
            elif self.pose == "idle" and self.emotion in ("neutral",):
                self._idle_frames += 1
                if self._idle_frames % 2 == 0 and self._full_speed:
                    self._full_speed = False
                elif not self._full_speed and self._idle_frames % 4 != 0:
                    return
            else:
                self._idle_frames = 0
                self._full_speed = True

            self.animation_frame = (self.animation_frame + 1) % 60
            self.update()

        # ── Skin texture loading ──

        def _load_skin_textures(self, character: str) -> None:
            """Extract dominant colors from skin PNG and update character profile.

            Also replaces procedural textures with actual skin face textures
            extracted from the PNG using MinecraftSkinLoader.
            """
            if not _TEX_MGR.ready:
                return
            from ui.sprite_2d import _SKIN_PATHS, CHARACTER_PROFILES as CP
            skin_rel = _SKIN_PATHS.get(character)
            if not skin_rel:
                return
            skin_path = ROOT / skin_rel
            if not skin_path.exists():
                return
            try:
                # Load skin PNG textures first (always)
                self._load_skin_png_texture(character)

                from core.skin_loader import MinecraftSkinLoader
                loader = MinecraftSkinLoader(str(skin_path), hd_resize=True)
                palette = loader.get_2d_palette()
                base = CP.get(character, {})
                if palette.get("head_color"):
                    base["skin"] = (palette["head_color"].red(), palette["head_color"].green(), palette["head_color"].blue())
                if palette.get("body_color"):
                    base["shirt"] = (palette["body_color"].red(), palette["body_color"].green(), palette["body_color"].blue())
                if palette.get("leg_color"):
                    base["pants"] = (palette["leg_color"].red(), palette["leg_color"].green(), palette["leg_color"].blue())
                if palette.get("hair_color"):
                    base["hair"] = (palette["hair_color"].red(), palette["hair_color"].green(), palette["hair_color"].blue())
                if palette.get("boot_color"):
                    base["boot"] = (palette["boot_color"].red(), palette["boot_color"].green(), palette["boot_color"].blue())
                self.profile = base
                self._rebuild_cache()
                log.debug("Loaded skin colors for %s", character)
            except Exception as exc:
                log.debug("Skin texture loading failed: %s", exc)

else:
    # ── Fallback when PyOpenGL is not installed ──
    class BlockRenderer3D:  # type: ignore[no-redef]
        frame_rendered = None  # Will be set by Qt if available

        @staticmethod
        def is_available() -> bool:
            return False

        def __init__(self, parent=None):
            raise RuntimeError("PyOpenGL is not installed")

        def set_profile(self, character: str) -> None:
            pass

        def set_pose(self, pose: str) -> None:
            pass

        def set_emotion(self, emotion: str) -> None:
            pass

        def stop_animation(self) -> None:
            """No timer exists in the no-OpenGL fallback."""

        def is_animation_running(self) -> bool:
            """No timer exists in the no-OpenGL fallback."""
            return False

        def hide(self) -> None:
            pass

        def deleteLater(self) -> None:
            pass

        def setGeometry(self, x: int, y: int, w: int, h: int) -> None:
            pass

        def show(self) -> None:
            pass

        def update(self) -> None:
            pass


def create_3d_widget(parent=None, use_skin_png: bool = False) -> Optional["BlockRenderer3D"]:
    """Create a BlockRenderer3D widget, or None if it cannot be created."""
    if not _OPENGL_AVAILABLE:
        return None
    # A QWidget cannot exist before its QApplication, and Qt answers that with a
    # qFatal abort rather than a Python exception — so the try/except below can
    # never catch it. Bail out explicitly instead: this used to kill the
    # interpreter with exit code 127, silently skipping every test after the one
    # that called the factory.
    from PyQt6.QtWidgets import QApplication as _QA
    if _QA.instance() is None:
        log.debug(
            "create_3d_widget: no QApplication instance yet — not creating a 3D widget"
        )
        return None
    try:
        return BlockRenderer3D(parent, use_skin_png=use_skin_png)
    except Exception:
        return None
