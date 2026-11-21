"""Regression tests for the glTF character model and skin-atlas normalisation.

Two defects made the 3D character look nothing like its design.

1. ``GLTFCharacterModel`` stored *buffer-absolute* byte offsets and then used
   them as offsets into the index buffer — which is a separate GL buffer
   holding only the index slice. Every ``glDrawElements`` read past the end of
   that buffer, and the vertex array was never re-pointed per mesh, so all six
   meshes drew the same vertices. Measured from a real framebuffer, the
   assembled figure rendered as a 38x35px sliver in a 200x280 window (12% of the
   frame height, pinned to the top edge) instead of a 78x193px figure (69%).
   After the fix all animated poses also fit without clipping.

2. A legacy 64x32 skin was stretched to 64x64 instead of being remapped to the
   (1.8) layout. The legacy format has no left-arm/left-leg slots, so every UV
   lookup — in the 2D sprite *and* the glTF model — sampled unrelated pixels.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from core.skin_loader import legacy_to_modern  # noqa: E402
from ui import renderer_3d  # noqa: E402
from ui.renderer_3d import GLTFCharacterModel, _normalise_skin_atlas  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GLTF_PATH = ROOT / "assets" / "models" / "atom.gltf"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Buffer offsets are relative to the buffer view they live in
# ═══════════════════════════════════════════════════════════════════════════════

class TestGltfBufferOffsets(unittest.TestCase):
    def setUp(self) -> None:
        self.model = GLTFCharacterModel()
        self.assertTrue(self.model.parse_gltf(str(GLTF_PATH)), "glTF failed to parse")
        with open(GLTF_PATH, encoding="utf-8") as f:
            self.gltf = json.load(f)

    def test_index_offsets_stay_inside_the_index_buffer(self) -> None:
        """The regression: offsets were buffer-absolute (4608 + n)."""
        limit = self.model._idx_byte_length
        self.assertGreater(limit, 0)
        for m in self.model._meshes:
            self.assertLess(
                m.index_byte_offset, limit,
                f"{m.name} index offset {m.index_byte_offset} is past the "
                f"{limit}-byte index buffer",
            )

    def test_index_offsets_match_the_file(self) -> None:
        for mesh, m in zip(self.gltf["meshes"], self.model._meshes):
            acc = self.gltf["accessors"][mesh["primitives"][0]["indices"]]
            self.assertEqual(m.index_byte_offset, acc["byteOffset"])

    def test_vertex_offsets_match_the_file(self) -> None:
        for mesh, m in zip(self.gltf["meshes"], self.model._meshes):
            acc = self.gltf["accessors"][mesh["primitives"][0]["attributes"]["POSITION"]]
            self.assertEqual(m.vertex_byte_offset, acc["byteOffset"])

    def test_each_mesh_has_its_own_vertex_slice(self) -> None:
        offsets = [m.vertex_byte_offset for m in self.model._meshes]
        self.assertEqual(len(set(offsets)), len(offsets), "meshes share a vertex slice")
        self.assertEqual(offsets, sorted(offsets))

    def test_buffer_sizes_come_from_the_file_not_constants(self) -> None:
        """Hard-coded 4608/5040 broke as soon as the model was regenerated."""
        self.assertEqual(
            self.model._vtx_byte_length,
            self.gltf["bufferViews"][0]["byteLength"],
        )
        self.assertEqual(
            self.model._idx_byte_offset,
            self.gltf["bufferViews"][1]["byteOffset"],
        )
        # Derived from the file, since the model gained a hair-layer mesh: a
        # hard-coded 6 here is exactly the bug this test exists to catch.
        self.assertEqual(self.model._vtx_byte_length, self.model.mesh_count * 24 * 32)


class TestDrawMeshRepointsTheVertexArray(unittest.TestCase):
    """All six meshes used to draw the *first* mesh's vertices."""

    def setUp(self) -> None:
        self.model = GLTFCharacterModel()
        self.model.parse_gltf(str(GLTF_PATH))
        self.model._loaded = True  # skip VBO creation (needs a GL context)

    @staticmethod
    def _ptr(ptr) -> int:
        """c_void_p(0).value is None, not 0."""
        if hasattr(ptr, "value"):
            return ptr.value or 0
        return ptr or 0

    def _record(self, mesh_idx: int) -> dict:
        calls: dict = {}

        def vertex_pointer(size, type_, stride, ptr):
            calls["vertex"] = self._ptr(ptr)

        def normal_pointer(type_, stride, ptr):
            calls["normal"] = self._ptr(ptr)

        def tex_pointer(size, type_, stride, ptr):
            calls["tex"] = self._ptr(ptr)

        def draw(mode, count, type_, ptr):
            calls["index"] = self._ptr(ptr)

        with mock.patch.object(renderer_3d.gl, "glVertexPointer", vertex_pointer), \
             mock.patch.object(renderer_3d.gl, "glNormalPointer", normal_pointer), \
             mock.patch.object(renderer_3d.gl, "glTexCoordPointer", tex_pointer), \
             mock.patch.object(renderer_3d.gl, "glDrawElements", draw):
            self.model.draw_mesh(mesh_idx)
        return calls

    def test_vertex_pointer_uses_the_mesh_offset(self) -> None:
        for idx in range(self.model.mesh_count):
            expected = self.model._meshes[idx].vertex_byte_offset
            calls = self._record(idx)
            self.assertEqual(calls["vertex"], expected, f"mesh {idx} vertex base")
            self.assertEqual(calls["normal"], expected + 12, f"mesh {idx} normal base")
            self.assertEqual(calls["tex"], expected + 24, f"mesh {idx} texcoord base")

    def test_index_pointer_is_the_mesh_index_offset(self) -> None:
        for idx in range(self.model.mesh_count):
            expected = self.model._meshes[idx].index_byte_offset
            self.assertEqual(self._record(idx)["index"], expected)

    def test_out_of_range_draw_is_ignored(self) -> None:
        self.assertEqual(self._record(99), {})


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Legacy 64×32 skins are remapped, not stretched
# ═══════════════════════════════════════════════════════════════════════════════

def _legacy_skin() -> QImage:
    """A 64×32 skin with each region painted a distinct colour."""
    img = QImage(64, 32, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    for x in range(0, 64, 8):
        for y in range(0, 32, 8):
            img.fill(QColor((x * 3) % 256, (y * 7) % 256, 128))
    # Right arm region (40,16)-(56,32) and right leg (0,16)-(16,32) get markers.
    for x in range(40, 56):
        for y in range(16, 32):
            img.setPixelColor(x, y, QColor(200, 10, 10, 255))
    for x in range(0, 16):
        for y in range(16, 32):
            img.setPixelColor(x, y, QColor(10, 200, 10, 255))
    return img


class TestLegacySkinAtlas(unittest.TestCase):
    def test_renderer_converts_64x32_to_64x64(self) -> None:
        atlas = _normalise_skin_atlas(_legacy_skin())
        self.assertEqual((atlas.width(), atlas.height()), (64, 64))

    def test_left_limb_slots_are_populated(self) -> None:
        """The legacy layout has no left arm/leg — they must be generated."""
        atlas = _normalise_skin_atlas(_legacy_skin())
        left_arm = atlas.copy(32, 48, 16, 16)
        left_leg = atlas.copy(16, 48, 16, 16)
        for region, name in ((left_arm, "left arm"), (left_leg, "left leg")):
            opaque = sum(
                1
                for x in range(region.width())
                for y in range(region.height())
                if region.pixelColor(x, y).alpha() > 0
            )
            self.assertGreater(opaque, 0, f"{name} slot was left empty")

    def test_top_half_is_preserved(self) -> None:
        src = _legacy_skin()
        atlas = _normalise_skin_atlas(src)
        for x in range(0, 64, 7):
            for y in range(0, 32, 5):
                self.assertEqual(atlas.pixelColor(x, y), src.pixelColor(x, y))

    def test_modern_skin_is_returned_untouched(self) -> None:
        modern = QImage(64, 64, QImage.Format.Format_ARGB32)
        modern.fill(QColor(1, 2, 3, 255))
        out = _normalise_skin_atlas(modern)
        self.assertEqual((out.width(), out.height()), (64, 64))
        self.assertEqual(out.pixelColor(50, 60), QColor(1, 2, 3, 255))

    def test_hd_skin_is_downscaled(self) -> None:
        hd = QImage(128, 128, QImage.Format.Format_ARGB32)
        hd.fill(QColor(9, 9, 9, 255))
        self.assertEqual((_normalise_skin_atlas(hd).width(), _normalise_skin_atlas(hd).height()),
                         (64, 64))

    def test_pil_loader_uses_the_same_layout(self) -> None:
        """core.skin_loader feeds the 2D palette, so it must agree."""
        pil = legacy_to_modern(_pil_legacy_skin())
        self.assertEqual(pil.size, (64, 64))
        self.assertEqual(pil.getpixel((16, 48))[3], 255, "left leg slot empty")
        self.assertEqual(pil.getpixel((32, 48))[3], 255, "left arm slot empty")


def _pil_legacy_skin():
    from PIL import Image

    img = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
    for x in range(40, 56):
        for y in range(16, 32):
            img.putpixel((x, y), (200, 10, 10, 255))
    for x in range(0, 16):
        for y in range(16, 32):
            img.putpixel((x, y), (10, 200, 10, 255))
    return img


# ═══════════════════════════════════════════════════════════════════════════════
# 3. The hair layer is a real mesh, not paint on the head cube
# ═══════════════════════════════════════════════════════════════════════════════

class TestHairLayerMesh(unittest.TestCase):
    """The second skin layer has to exist as geometry for hair to have volume."""

    _HEAD_MESH = 0
    _HAIR_MESH = 6

    def setUp(self) -> None:
        with open(GLTF_PATH, encoding="utf-8") as f:
            self.gltf = json.load(f)
        self.meshes = self.gltf["meshes"]

    def test_model_has_a_hair_mesh(self) -> None:
        self.assertGreaterEqual(len(self.meshes), 7)
        self.assertEqual(self.meshes[self._HAIR_MESH]["name"], "Hat")

    def test_the_original_six_body_parts_are_unchanged(self) -> None:
        """Appending a mesh must not renumber the ones the renderer draws."""
        names = [m["name"] for m in self.meshes[:6]]
        self.assertEqual(
            names,
            ["Head", "Body", "LeftArm", "RightArm", "LeftLeg", "RightLeg"],
        )

    def test_hair_layer_is_larger_than_the_head(self) -> None:
        def extent(idx: int) -> float:
            pos = self.gltf["accessors"][self.meshes[idx]["primitives"][0]["attributes"]["POSITION"]]
            return pos["max"][0] - pos["min"][0]

        self.assertGreater(extent(self._HAIR_MESH), extent(self._HEAD_MESH))

    def test_hair_layer_maps_to_the_second_layer_uvs(self) -> None:
        """Sampling the head's UVs would just repaint the face at a larger size."""
        def uvs(idx: int):
            acc = self.gltf["accessors"][self.meshes[idx]["primitives"][0]["attributes"]["TEXCOORD_0"]]
            view = self.gltf["bufferViews"][acc["bufferView"]]
            return acc["byteOffset"] + view.get("byteOffset", 0)

        def read_pair(offset: int) -> tuple[float, float]:
            import struct
            uri = self.gltf["buffers"][0]["uri"]
            import base64
            data = base64.b64decode(uri.split("base64,")[1])
            return struct.unpack_from("<ff", data, offset)

        hair_u, _ = read_pair(uvs(self._HAIR_MESH))
        head_u, _ = read_pair(uvs(self._HEAD_MESH))
        # Head front starts at x=8/64, hat front at x=40/64.
        self.assertAlmostEqual(head_u, 8 / 64, places=4)
        self.assertAlmostEqual(hair_u, 40 / 64, places=4)

    def test_renderer_skips_the_hair_mesh_when_it_is_absent(self) -> None:
        """An older/custom model without layer 2 must still render."""
        model = GLTFCharacterModel()
        model._loaded = True
        model._meshes = []
        self.assertEqual(model.mesh_count, 0)


class TestShippedSkinsAreLoadable(unittest.TestCase):
    def test_every_shipped_skin_normalises(self) -> None:
        skins_dir = ROOT / "assets" / "skins"
        if not skins_dir.exists():
            self.skipTest("no skins directory")
        for path in sorted(skins_dir.glob("*.png")):
            with self.subTest(skin=path.name):
                atlas = _normalise_skin_atlas(QImage(str(path)))
                self.assertEqual((atlas.width(), atlas.height()), (64, 64))


if __name__ == "__main__":
    unittest.main()
