"""Regression tests for the 3D character's materials, lighting and skin atlas.

The character used to render as a flat, unlit-looking block for three separate
reasons, all guarded here.

1. **The skin was flat.** ``atom_skin.png`` was a 64x32 legacy atlas with 19
   usable colours — one solid orange head, solid grey limbs. There was nothing
   for a shader to shade.

2. **The shader had no notion of material.** Every surface shared one hard-coded
   specular exponent, and the only lighting was three lights over a constant
   ambient term, so every face of every cube got the same value. There was also
   no way to use the skin's second layer, so hair could only ever be paint on
   the top of the head cube.

3. **A second GL context rendered black.** ``_SHADER_PROG``,
   ``_SHADER_UNIFORMS``, ``_LIGHTING_INITIALIZED`` and the texture manager are
   module globals, but GL names and uniform values only exist inside the context
   that created them. When the overlay rebuilt the renderer — stall recovery or
   the 2D/3D switch — ``_LIGHTING_INITIALIZED`` was still ``True``, so the light
   uniforms were never uploaded and everything drew at zero light. Measured on a
   real framebuffer: the second widget rendered **24 unique colours, luminance
   sd 0.88** against **231 colours, sd 26.9** for the first.
"""

from __future__ import annotations

import dataclasses
import json
import unittest
from pathlib import Path
from unittest import mock

ATOM_SKIN = Path(__file__).resolve().parent.parent / "assets" / "skins" / "atom_skin.png"
GLTF_PATH = Path(__file__).resolve().parent.parent / "assets" / "models" / "atom.gltf"
SKIN_DIR = Path(__file__).resolve().parent.parent / "assets" / "skins"

try:
    from tools import generate_skins as skin_gen
except Exception:  # pragma: no cover - generator is optional at runtime
    skin_gen = None

from ui import renderer_3d  # noqa: E402
from ui.renderer_3d import GLTFCharacterModel  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Material presets
# ═══════════════════════════════════════════════════════════════════════════

class TestMaterialPresets(unittest.TestCase):
    def test_every_preset_is_well_formed(self) -> None:
        for name, preset in renderer_3d.MATERIALS.items():
            with self.subTest(material=name):
                spec, shininess, rim, bump, emissive = preset
                self.assertTrue(0.0 <= spec <= 1.0, "specular out of range")
                self.assertTrue(2.0 <= shininess <= 128.0, "shininess out of range")
                self.assertTrue(0.0 <= rim <= 1.0, "rim out of range")
                self.assertTrue(0.0 <= bump <= 3.0, "bump out of range")
                self.assertEqual(len(emissive), 3)
                self.assertTrue(all(0 <= c <= 255 for c in emissive))

    def test_materials_are_visibly_different_from_each_other(self) -> None:
        """If they all shade the same, the presets are decorative."""
        signatures = {p[:4] for p in renderer_3d.MATERIALS.values()}
        self.assertGreater(len(signatures), 3)

    def test_metal_is_shinier_than_cloth(self) -> None:
        self.assertGreater(renderer_3d.MATERIALS["metal"][0], renderer_3d.MATERIALS["cloth"][0])
        self.assertGreater(renderer_3d.MATERIALS["metal"][1], renderer_3d.MATERIALS["cloth"][1])

    def test_cloth_has_more_relief_than_the_visor(self) -> None:
        """Fabric weave should catch the light; a visor should be smooth."""
        self.assertGreater(renderer_3d.MATERIALS["cloth"][3], renderer_3d.MATERIALS["visor"][3])

    def test_every_mesh_material_exists(self) -> None:
        for name in renderer_3d._MESH_MATERIALS:
            self.assertIn(name, renderer_3d.MATERIALS, f"unknown material {name}")

    def test_mesh_material_table_covers_every_mesh(self) -> None:
        self.assertEqual(len(renderer_3d._MESH_MATERIALS), len(renderer_3d._MESH_NAMES))

    def test_unknown_material_falls_back_instead_of_raising(self) -> None:
        with mock.patch.object(renderer_3d, "gl") as fake_gl:
            fake_gl.glUniform1f = mock.Mock()
            fake_gl.glUniform3f = mock.Mock()
            renderer_3d._shader_material("no_such_material")


class TestShaderMaterialUpload(unittest.TestCase):
    """``_shader_material`` must actually push the preset into the program."""

    # Distinct fake uniform locations, so the recorder can tell them apart.
    _KEYS = ("uSpecStrength", "uShininess", "uRimStrength", "uBumpScale", "uEmissive")

    def setUp(self) -> None:
        self._saved_prog = renderer_3d._SHADER_PROG
        self._saved_uniforms = dict(renderer_3d._SHADER_UNIFORMS)
        renderer_3d._SHADER_PROG = 1
        renderer_3d._SHADER_UNIFORMS.clear()
        for i, key in enumerate(self._KEYS):
            renderer_3d._SHADER_UNIFORMS[key] = i

    def tearDown(self) -> None:
        renderer_3d._SHADER_PROG = self._saved_prog
        renderer_3d._SHADER_UNIFORMS.clear()
        renderer_3d._SHADER_UNIFORMS.update(self._saved_uniforms)

    def _apply(self, material: str):
        floats: dict = {}
        vec3: dict = {}
        with mock.patch.object(renderer_3d, "gl") as fake_gl:
            fake_gl.glUniform1f = lambda loc, v: floats.__setitem__(self._KEYS[loc], v)
            fake_gl.glUniform3f = lambda loc, r, g, b: vec3.__setitem__(self._KEYS[loc], (r, g, b))
            renderer_3d._shader_material(material)
        return floats, vec3

    def test_preset_values_reach_the_driver(self) -> None:
        floats, _ = self._apply("metal")
        spec, shininess, rim, bump, _ = renderer_3d.MATERIALS["metal"]
        self.assertEqual(floats["uSpecStrength"], spec)
        self.assertEqual(floats["uShininess"], shininess)
        self.assertEqual(floats["uRimStrength"], rim)
        self.assertEqual(floats["uBumpScale"], bump)

    def test_switching_material_changes_the_values(self) -> None:
        cloth, _ = self._apply("cloth")
        metal, _ = self._apply("metal")
        self.assertNotEqual(cloth["uSpecStrength"], metal["uSpecStrength"])
        self.assertNotEqual(cloth["uBumpScale"], metal["uBumpScale"])

    def test_emissive_tint_is_normalised(self) -> None:
        _, vec3 = self._apply("visor")
        triplet = vec3["uEmissive"]
        self.assertTrue(all(0.0 <= c <= 1.0 for c in triplet), triplet)

    def test_default_material_leaves_no_emissive(self) -> None:
        _, vec3 = self._apply("default")
        self.assertEqual(vec3["uEmissive"], (0.0, 0.0, 0.0))


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Shader source actually implements the lighting model
# ═══════════════════════════════════════════════════════════════════════════

class TestShaderSource(unittest.TestCase):
    def test_declares_the_material_and_lighting_uniforms(self) -> None:
        src = renderer_3d._FRAG_SRC
        for name in ("uSkyColor", "uGroundColor", "uShininess", "uSpecStrength",
                     "uBumpScale", "uRimStrength", "uEmissive", "uAlphaCut",
                     "uAoStrength"):
            with self.subTest(uniform=name):
                self.assertIn(name, src)

    def test_hemisphere_ambient_replaces_the_flat_ambient(self) -> None:
        self.assertIn("mix(uGroundColor, uSkyColor, hemi)", renderer_3d._FRAG_SRC)

    def test_uses_texture_derived_relief(self) -> None:
        src = renderer_3d._FRAG_SRC
        self.assertIn("dFdx", src)
        self.assertIn("dFdy", src)

    def test_alpha_test_is_opt_in(self) -> None:
        """A stray transparent texel must not punch a hole in the body."""
        src = renderer_3d._FRAG_SRC
        self.assertIn("uAlphaCut > 0.0", src)

    def test_specular_is_scaled_by_the_material(self) -> None:
        self.assertIn("uLight0Spec * uSpecStrength", renderer_3d._FRAG_SRC)

    def test_the_shader_no_longer_hard_codes_a_specular_exponent(self) -> None:
        self.assertNotIn("pow(max(NdotH0, 0.0), 48.0)", renderer_3d._FRAG_SRC)


class TestContactShadingIsCameraRelative(unittest.TestCase):
    """Contact shading must be relative to the character, not to the view origin.

    Every fragment of the figure sits at a *negative* view-space Y (the eye is
    ~2.9 units above the look-at point), so `clamp(0.55 - vPosition.y, 0, 1)`
    evaluates to 1 for the whole model and simply dims it by a flat 35% instead
    of shading the legs.
    """

    def setUp(self) -> None:
        self._saved = (renderer_3d._SHADER_PROG, dict(renderer_3d._SHADER_UNIFORMS))
        renderer_3d._SHADER_PROG = 1
        renderer_3d._SHADER_UNIFORMS.clear()
        renderer_3d._SHADER_UNIFORMS["uContactLevel"] = 0

    def tearDown(self) -> None:
        prog, uniforms = self._saved
        renderer_3d._SHADER_PROG = prog
        renderer_3d._SHADER_UNIFORMS.clear()
        renderer_3d._SHADER_UNIFORMS.update(uniforms)

    def test_level_is_where_the_body_sits_in_view_space(self) -> None:
        sent: dict = {}
        with mock.patch.object(renderer_3d, "gl") as fake_gl:
            fake_gl.glUniform1f = lambda loc, v: sent.__setitem__("v", v)
            renderer_3d._shader_contact_level(eye_y=2.9, mid_body_y=0.95)
        self.assertAlmostEqual(sent["v"], 0.95 - 2.9, places=5)

    def test_shader_reads_the_uniform_instead_of_a_constant(self) -> None:
        src = renderer_3d._FRAG_SRC
        self.assertIn("uContactLevel", src)
        self.assertNotIn("0.55 - vPosition.y", src)

    def test_static_default_leaves_shading_untouched(self) -> None:
        self.assertLess(renderer_3d._CONTACT_DEFAULT_VIEW_Y, -100.0)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. GL state must not leak between contexts
# ═══════════════════════════════════════════════════════════════════════════

class TestContextRecreation(unittest.TestCase):
    """The regression that made the character render black on a rebuild."""

    def setUp(self) -> None:
        self._saved = (
            renderer_3d._SHADER_PROG,
            dict(renderer_3d._SHADER_UNIFORMS),
            renderer_3d._LIGHTING_INITIALIZED,
        )
        mgr = renderer_3d._TEX_MGR
        self._saved_mgr = (dict(mgr._tex), mgr._ready, mgr._cube_vbo)

    def tearDown(self) -> None:
        prog, uniforms, lit = self._saved
        renderer_3d._SHADER_PROG = prog
        renderer_3d._SHADER_UNIFORMS.clear()
        renderer_3d._SHADER_UNIFORMS.update(uniforms)
        renderer_3d._LIGHTING_INITIALIZED = lit
        mgr = renderer_3d._TEX_MGR
        tex, ready, vbo = self._saved_mgr
        mgr._tex.clear()
        mgr._tex.update(tex)
        mgr._ready = ready
        mgr._cube_vbo = vbo

    def test_init_shaders_forgets_the_previous_context(self) -> None:
        renderer_3d._SHADER_PROG = 7
        renderer_3d._SHADER_UNIFORMS["uColor"] = 3
        renderer_3d._LIGHTING_INITIALIZED = True

        # Make compilation fail so the function returns early: the reset must
        # happen *before* any driver call, not after a successful link.
        with mock.patch.object(renderer_3d, "gl") as fake_gl:
            fake_gl.glCreateShader.side_effect = RuntimeError("no context")
            ok = renderer_3d._init_shaders()

        self.assertFalse(ok)
        self.assertEqual(renderer_3d._SHADER_PROG, 0)
        self.assertEqual(renderer_3d._SHADER_UNIFORMS, {})
        self.assertFalse(renderer_3d._LIGHTING_INITIALIZED)

    def test_reset_gl_state_drops_stale_texture_names(self) -> None:
        mgr = renderer_3d._TEX_MGR
        mgr._tex["skin"] = 42
        mgr._cube_vbo = 7
        mgr._ready = True

        renderer_3d._reset_gl_state()

        self.assertFalse(mgr._ready)
        self.assertEqual(mgr._tex, {})
        self.assertEqual(mgr._cube_vbo, 0)

    def test_reset_does_not_call_into_the_dead_context(self) -> None:
        """Deleting names that belong to a destroyed context is undefined."""
        mgr = renderer_3d._TEX_MGR
        mgr._tex["skin"] = 42
        mgr._ready = True
        with mock.patch.object(renderer_3d, "gl") as fake_gl:
            renderer_3d._reset_gl_state()
            fake_gl.glDeleteTextures.assert_not_called()
            fake_gl.glDeleteBuffers.assert_not_called()
            fake_gl.glDeleteLists.assert_not_called()

    def test_reparsing_the_model_does_not_duplicate_meshes(self) -> None:
        """initializeGL re-parses; the mesh list used to grow every rebuild."""
        model = GLTFCharacterModel()
        self.assertTrue(model.parse_gltf(str(GLTF_PATH)))
        first = model.mesh_count
        self.assertGreater(first, 0)
        self.assertTrue(model.parse_gltf(str(GLTF_PATH)))
        self.assertEqual(model.mesh_count, first)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. The shipped skin atlas is detailed and safe to alpha-test
# ═══════════════════════════════════════════════════════════════════════════

# Every region the renderer samples, per layer.
_BODY_REGIONS = [
    (8, 8, 8, 8), (16, 8, 8, 8), (0, 8, 8, 8), (24, 8, 8, 8),
    (8, 0, 8, 8), (16, 0, 8, 8),
    (20, 20, 8, 12), (32, 20, 8, 12), (16, 20, 4, 12), (28, 20, 4, 12),
    (20, 16, 8, 4), (28, 16, 8, 4),
    (44, 20, 4, 12), (52, 20, 4, 12), (40, 20, 4, 12), (48, 20, 4, 12),
    (36, 52, 4, 12), (44, 52, 4, 12), (32, 52, 4, 12), (40, 52, 4, 12),
    (4, 20, 4, 12), (12, 20, 4, 12), (0, 20, 4, 12), (8, 20, 4, 12),
    (20, 52, 4, 12), (28, 52, 4, 12), (16, 52, 4, 12), (24, 52, 4, 12),
]

_HAT_FRONT = (40, 8, 8, 8)


class TestDetailedSkinAtlas(unittest.TestCase):
    """The look is mostly the texture, so its quality is a regression surface."""

    def setUp(self) -> None:
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover
            self.skipTest("Pillow not installed")
        if not ATOM_SKIN.exists():
            self.skipTest("atom skin missing")
        self.img = Image.open(str(ATOM_SKIN)).convert("RGBA")

    def _colours(self, regions) -> set:
        out = set()
        for (x, y, w, h) in regions:
            for yy in range(y, y + h):
                for xx in range(x, x + w):
                    r, g, b, a = self.img.getpixel((xx, yy))
                    if a >= 128:
                        out.add((r, g, b))
        return out

    def _mode(self, region) -> tuple[int, int, int]:
        from collections import Counter
        x, y, w, h = region
        return Counter(
            self.img.getpixel((xx, yy))[:3]
            for yy in range(y, y + h)
            for xx in range(x, x + w)
            if self.img.getpixel((xx, yy))[3] >= 128
        ).most_common(1)[0][0]

    def test_atlas_is_the_modern_layout(self) -> None:
        """A 64x32 legacy atlas has no left-limb slots and must be remapped."""
        self.assertEqual(self.img.size, (64, 64))

    def test_is_substantially_more_detailed_than_the_flat_original(self) -> None:
        """The old atlas carried 19 colours across the whole body."""
        colours = self._colours(_BODY_REGIONS)
        self.assertGreater(
            len(colours), 100,
            f"only {len(colours)} body colours — the skin has gone flat again",
        )

    def test_body_regions_are_opaque(self) -> None:
        """The hair layer is alpha-tested; the body under it must not be."""
        for region in _BODY_REGIONS:
            x, y, w, h = region
            with self.subTest(region=region):
                clear = [
                    (xx, yy)
                    for yy in range(y, y + h)
                    for xx in range(x, x + w)
                    if self.img.getpixel((xx, yy))[3] < 128
                ]
                self.assertEqual(clear, [], f"transparent texels at {x},{y}")

    def test_face_and_hair_are_different_tones(self) -> None:
        """They were both the same orange, which made the head a flat blob."""
        face = self._mode((8, 8, 8, 8))
        hair = self._mode((8, 0, 8, 8))
        self.assertNotEqual(face, hair)
        self.assertGreater(
            sum(abs(a - b) for a, b in zip(face, hair)), 60,
            f"face {face} and hair {hair} are too close to read apart",
        )

    def test_face_is_the_body_part_colour_not_the_hair(self) -> None:
        """``get_2d_palette`` takes the mode of the face region as the skin tone."""
        from collections import Counter
        from PyQt6.QtGui import QImage

        pixels = [
            self.img.getpixel((xx, yy))[:3]
            for yy in range(8, 16)
            for xx in range(8, 16)
            if self.img.getpixel((xx, yy))[3] >= 128
        ]
        mode = Counter(pixels).most_common(1)[0][0]
        self.assertLess(sum(mode), 300, "the face resolved to a bright colour, likely hair")

    def test_hair_layer_can_be_alpha_tested(self) -> None:
        """The second layer needs cut-out texels or it would hide the face."""
        x, y, w, h = _HAT_FRONT
        alpha = [
            self.img.getpixel((xx, yy))[3]
            for yy in range(y, y + h)
            for xx in range(x, x + w)
        ]
        self.assertIn(0, alpha, "hat layer has no transparent texels")
        self.assertTrue(any(a >= 250 for a in alpha), "hat layer has no hair texels")

    def test_atlas_normalises_without_being_scaled(self) -> None:
        """A 64x64 atlas must pass through untouched, not be resized."""
        from PyQt6.QtGui import QImage
        atlas = renderer_3d._normalise_skin_atlas(QImage(str(ATOM_SKIN)))
        self.assertEqual((atlas.width(), atlas.height()), (64, 64))


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Skin generation is reproducible
# ═══════════════════════════════════════════════════════════════════════════

class TestSkinGeneratorReproducible(unittest.TestCase):
    def setUp(self) -> None:
        try:
            import tools.generate_skins as _  # noqa: F401
        except Exception as exc:  # pragma: no cover
            self.skipTest(f"generator unavailable: {exc}")

    def test_regenerating_matches_the_shipped_atlas(self) -> None:
        """Catches a hand-edited atlas that no longer matches its template."""
        from PIL import Image
        import tools.generate_skins as gen

        if not ATOM_SKIN.exists():
            self.skipTest("atom skin missing")
        rebuilt = gen.build()
        shipped = Image.open(str(ATOM_SKIN)).convert("RGBA")
        self.assertEqual(rebuilt.size, shipped.size)
        self.assertEqual(rebuilt.tobytes(), shipped.tobytes())

    def test_expected_palette_matches_the_generator_templates(self) -> None:
        """The 2D sprite colours are derived from these; drift changes sprites."""
        import tools.generate_skins as gen

        rebuilt = gen.build()
        self.assertEqual(gen.resolved_palette(rebuilt), gen.EXPECTED_PALETTE)


# ═══════════════════════════════════════════════════════════════════════════════
# 4b. Every character's atlas, not just Atom's
# ═══════════════════════════════════════════════════════════════════════════════

@unittest.skipIf(skin_gen is None, "skin generator unavailable")
class TestEveryCharacterSkinAtlas(unittest.TestCase):
    """One flat atlas was a bug; five more of them would be five more bugs.

    Steve shipped as a 64x32 legacy atlas whose face region was painted with
    hair colours (its 2D skin tone was ``(14, 9, 3)``), and Gamer Girl's face
    region was dominated by orange hair the same way. Both are checked here.
    """

    @classmethod
    def setUpClass(cls) -> None:
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover
            raise unittest.SkipTest("Pillow not installed")
        cls.images = {}
        for key, spec in skin_gen.SKINS.items():
            path = SKIN_DIR / spec.filename
            if path.exists():
                cls.images[key] = Image.open(str(path)).convert("RGBA")

    def _colours(self, img, regions) -> set:
        out = set()
        for (x, y, w, h) in regions:
            for yy in range(y, y + h):
                for xx in range(x, x + w):
                    r, g, b, a = img.getpixel((xx, yy))
                    if a >= 128:
                        out.add((r, g, b))
        return out

    def _mode(self, img, region) -> tuple[int, int, int]:
        from collections import Counter

        x, y, w, h = region
        return Counter(
            img.getpixel((xx, yy))[:3]
            for yy in range(y, y + h)
            for xx in range(x, x + w)
            if img.getpixel((xx, yy))[3] >= 128
        ).most_common(1)[0][0]

    def test_the_generator_covers_every_shipped_character(self) -> None:
        self.assertEqual(set(self.images), set(skin_gen.SKINS))

    def test_every_atlas_is_the_modern_64x64_layout(self) -> None:
        for key, img in self.images.items():
            with self.subTest(character=key):
                self.assertEqual(img.size, (64, 64), "a legacy 64x32 atlas shipped")

    def test_every_atlas_is_detailed(self) -> None:
        """The flat atlases carried 20-53 colours across the whole body."""
        for key, img in self.images.items():
            with self.subTest(character=key):
                colours = self._colours(img, _BODY_REGIONS)
                self.assertGreater(
                    len(colours), 100,
                    f"only {len(colours)} body colours — the skin has gone flat",
                )

    def test_body_regions_are_opaque(self) -> None:
        """Only the hair layer is alpha-tested; the body underneath is not."""
        for key, img in self.images.items():
            for region in _BODY_REGIONS:
                x, y, w, h = region
                clear = [
                    (xx, yy)
                    for yy in range(y, y + h)
                    for xx in range(x, x + w)
                    if img.getpixel((xx, yy))[3] < 128
                ]
                with self.subTest(character=key, region=region):
                    self.assertEqual(clear, [], f"transparent texels at {x},{y}")

    def test_face_and_hair_are_different_tones(self) -> None:
        """The threshold is 40 rather than Atom's 60: Pink is deliberately a
        pale character, where cream hair on pale skin is only ~52 apart."""
        for key, img in self.images.items():
            with self.subTest(character=key):
                face = self._mode(img, (8, 8, 8, 8))
                hair = self._mode(img, (8, 0, 8, 8))
                self.assertNotEqual(face, hair)
                self.assertGreater(
                    sum(abs(a - b) for a, b in zip(face, hair)), 40,
                    f"face {face} and hair {hair} are too close to read apart",
                )

    def test_the_shipped_palette_matches_the_declared_one(self) -> None:
        """The 2D sprite colours come from these values, so drift is a look change."""
        for key, spec in skin_gen.SKINS.items():
            with self.subTest(character=key):
                self.assertEqual(
                    skin_gen.resolved_palette(self.images[key], key), spec.expected,
                )

    def test_hair_layer_can_be_alpha_tested(self) -> None:
        """The second layer needs cut-out texels or it would hide the face."""
        x, y, w, h = _HAT_FRONT
        for key, img in self.images.items():
            alpha = [
                img.getpixel((xx, yy))[3]
                for yy in range(y, y + h)
                for xx in range(x, x + w)
            ]
            with self.subTest(character=key):
                self.assertIn(0, alpha, "no transparent texels in the hair layer")
                self.assertTrue(any(a >= 250 for a in alpha), "no hair texels")

    def test_regenerating_matches_every_shipped_atlas(self) -> None:
        """Catches a hand-edited atlas that no longer matches its templates."""
        for key, img in self.images.items():
            with self.subTest(character=key):
                rebuilt = skin_gen.build(key)
                self.assertEqual(rebuilt.size, img.size)
                self.assertEqual(rebuilt.tobytes(), img.tobytes())


# ═══════════════════════════════════════════════════════════════════════════════
# 4c. The template validator is the safety net for hand-written templates
# ═══════════════════════════════════════════════════════════════════════════════

@unittest.skipIf(skin_gen is None, "skin generator unavailable")
class TestGeneratorRejectsBrokenTemplates(unittest.TestCase):
    """A mistyped row should raise, not silently paint a broken character."""

    def _spec(self, part: str, face: str, rows):
        faces = {p: dict(f) for p, f in skin_gen.SKINS["pink"].faces.items()}
        faces[part][face] = rows
        return dataclasses.replace(skin_gen.SKINS["pink"], faces=faces)

    def test_ragged_rows_are_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            skin_gen._validate(self._spec("head", "front", ["ffffffff", "fff"]))

    def test_unknown_palette_keys_are_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            skin_gen._validate(self._spec("head", "front", ["QQQQQQQQ"] * 8))

    def test_a_hole_in_the_body_is_rejected(self) -> None:
        """The body is drawn without an alpha test, so a hole stays a hole."""
        with self.assertRaises(SystemExit):
            skin_gen._validate(self._spec("body", "front", ["--------"] * 12))

    def test_an_opaque_hair_layer_is_rejected(self) -> None:
        """An opaque second layer would cover the face entirely."""
        with self.assertRaises(SystemExit):
            skin_gen._validate(self._spec("hat", "front", ["aaaaaaaa"] * 8))

    def test_a_missing_region_is_rejected(self) -> None:
        faces = {p: dict(f) for p, f in skin_gen.SKINS["pink"].faces.items()}
        del faces["leg_l"]["front"]
        spec = dataclasses.replace(skin_gen.SKINS["pink"], faces=faces)
        with self.assertRaises(SystemExit):
            skin_gen._validate(spec)

    def test_the_sprite_and_renderer_agree_on_every_skin_file(self) -> None:
        """They are two hand-written tables for the same characters.

        Gamer Girl was textured from ``pink_skin.png`` in the 3D renderer while
        her palette came from ``gamergirl.png``, so the 2D sprite and the 3D
        figure showed different characters.
        """
        from ui.sprite_2d import _SKIN_PATHS

        for character, rel in _SKIN_PATHS.items():
            with self.subTest(character=character):
                self.assertEqual(
                    renderer_3d._SKIN_FILES[character], Path(rel).name,
                )

    def test_every_spec_declares_a_palette_check(self) -> None:
        for key, spec in skin_gen.SKINS.items():
            with self.subTest(character=key):
                self.assertEqual(
                    set(spec.expected),
                    {"head_color", "hair_color", "body_color",
                     "arm_color", "leg_color", "boot_color"},
                )


if __name__ == "__main__":
    unittest.main()
