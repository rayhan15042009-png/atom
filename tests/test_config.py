"""
Tests for core.config_manager — Step 1 of the rebuild
Covers: creation, serialization, save/load cycle, validation, backward compat, memory
"""

import json
import os
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

# We test against a temp dir so we never touch the real data/
import core.config_manager as cfgmod


class TestDefaults(unittest.TestCase):
    """AppConfig defaults must match master prompt §3 specifications."""

    def test_defaults_created(self):
        cfg = cfgmod.AppConfig()
        self.assertEqual(cfg.mode, "tap")
        self.assertEqual(cfg.llm_provider, "ollama")
        self.assertEqual(cfg.character, "atom")

    def test_chat_token_budget_per_spec(self):
        """§3: Chat — Fast & Focused: 200 max_tokens / 4096 context"""
        cfg = cfgmod.AppConfig()
        self.assertEqual(cfg.ollama_max_tokens, 200)
        self.assertEqual(cfg.ollama_num_ctx, 4096)

    def test_analysis_token_budget_per_spec(self):
        """§3: Analysis = 400 max_tokens / 4096 context window"""
        cfg = cfgmod.AppConfig()
        self.assertEqual(cfg.ollama_max_tokens_analysis, 500)
        self.assertEqual(cfg.ollama_num_ctx_analysis, 4096)

    def test_roaming_bounds_per_spec(self):
        """§3: 400×300px roaming bounds"""
        cfg = cfgmod.AppConfig()
        self.assertEqual(cfg.roaming_box_width, 400)
        self.assertEqual(cfg.roaming_box_height, 300)

    def test_proactive_tap_interval_per_spec(self):
        """§3: Tap proactive 20–40s randomized"""
        cfg = cfgmod.AppConfig()
        self.assertEqual(cfg.proactive_tap_interval_min, 20)
        self.assertEqual(cfg.proactive_tap_interval_max, 40)

    def test_proactive_stalk_interval_per_spec(self):
        """§3: Stalk proactive 10–15s"""
        cfg = cfgmod.AppConfig()
        self.assertEqual(cfg.proactive_stalk_interval_min, 10)
        self.assertEqual(cfg.proactive_stalk_interval_max, 15)

    def test_models_per_spec(self):
        """Default chat model (phi3:mini, tuned Jul 2026) + analysis"""
        cfg = cfgmod.AppConfig()
        self.assertEqual(cfg.model_chat, "phi3:mini")
        self.assertEqual(cfg.model_analysis, "dolphin-mistral:7b-v2.6-q4_K_M")


class TestDictLike(unittest.TestCase):
    """The _DictLike mixin must provide ergonomic access."""

    def setUp(self):
        self.cfg = cfgmod.AppConfig()

    def test_get_existing(self):
        self.assertEqual(self.cfg.get("mode"), "tap")

    def test_get_missing_default(self):
        self.assertEqual(self.cfg.get("nonexistent", "fallback"), "fallback")

    def test_dict_style_getitem(self):
        self.assertEqual(self.cfg["mode"], "tap")

    def test_dict_style_setitem(self):
        self.cfg["mode"] = "stalk"
        self.assertEqual(self.cfg.mode, "stalk")

    def test_contains(self):
        self.assertIn("mode", self.cfg)
        self.assertNotIn("nonexistent", self.cfg)

    def test_alias_3d(self):
        """'3d' key should resolve to '_3d' sub-config"""
        self.assertIn("3d", self.cfg)
        d3 = self.cfg["3d"]
        self.assertEqual(d3.camera_angle, "side")

    def test_keys(self):
        keys = self.cfg.keys()
        self.assertIn("mode", keys)
        self.assertIn("llm_provider", keys)

    def test_backward_compat_confirmation_required(self):
        """Old top-level field name resolves to permissions.confirmation_required"""
        self.assertIn("confirmation_required", self.cfg)
        self.assertEqual(self.cfg.get("confirmation_required"), "always")

    def test_backward_compat_set(self):
        self.cfg["confirmation_required"] = "risky"
        self.assertEqual(self.cfg.permissions.confirmation_required, "risky")

    def test_backward_compat_intro_enabled(self):
        self.assertIn("intro_enabled", self.cfg)
        self.assertEqual(self.cfg.get("intro_enabled"), True)

    def test_backward_compat_model_alias(self):
        """Old 'model' key → model_chat"""
        self.assertIn("model", self.cfg)
        self.assertEqual(self.cfg.get("model"), "phi3:mini")


class TestPermissions(unittest.TestCase):
    """Permissions block must be locked down by default."""

    def setUp(self):
        self.cfg = cfgmod.AppConfig()

    def test_confirmation_always(self):
        self.assertEqual(self.cfg.permissions.confirmation_required, "always")

    def test_clipboard_access_off(self):
        self.assertFalse(self.cfg.permissions.clipboard_access)

    def test_no_self_modify(self):
        self.assertFalse(self.cfg.permissions.self_modify_config)

    def test_no_escalation(self):
        self.assertFalse(self.cfg.permissions.escalation_tiers)

    def test_rate_limit(self):
        self.assertEqual(self.cfg.permissions.max_actions_per_minute, 10)


class TestSubConfigs(unittest.TestCase):
    """Nested sub-configs must initialize correctly."""

    def setUp(self):
        self.cfg = cfgmod.AppConfig()

    def test_3d(self):
        self.assertFalse(self.cfg._3d.enabled)
        self.assertEqual(self.cfg._3d.camera_angle, "side")

    def test_emotion(self):
        self.assertTrue(self.cfg.emotion.enabled)
        self.assertEqual(self.cfg.emotion.bored_timeout_seconds, 60)

    def test_theme(self):
        self.assertEqual(self.cfg.theme.name, "atom_hud")
        self.assertEqual(self.cfg.theme.primary_glow, "#00D4FF")

    def test_skins(self):
        # Off by default: the hand-authored Echo designs (Atom + Pink) are the
        # intended look, and loading a skin PNG over them repainted both
        # characters into unrecognisable palette dumps. The paths stay so the
        # setting still works when a user turns it on.
        self.assertFalse(self.cfg.skins.enabled)
        self.assertIn("atom", self.cfg.skins.atom)
        self.assertIn("pink", self.cfg.skins.pink)

    def test_ocr(self):
        self.assertEqual(self.cfg.ocr.interval_seconds, 8)
        self.assertEqual(self.cfg.ocr.min_confidence, 60)

    def test_network(self):
        self.assertEqual(self.cfg.network.check_host, "8.8.8.8")

    def test_intro(self):
        self.assertTrue(self.cfg.intro.enabled)
        self.assertTrue(self.cfg.intro.skippable)

    def test_chat(self):
        self.assertTrue(self.cfg.chat.minimize_enabled)
        self.assertEqual(self.cfg.chat.float_icon_position, "bottom-left")


class TestSerialization(unittest.TestCase):
    """Serialization round-trip must preserve all data."""

    def setUp(self):
        self.cfg = cfgmod.AppConfig()
        self.cfg.mode = "stalk"
        self.cfg.llm_provider = "gemini"
        self.cfg.permissions.confirmation_required = "risky"
        self.cfg._3d.enabled = True
        self.cfg.skins.enabled = False
        self.cfg.ocr.interval_seconds = 12
        self.cfg.theme.name = "dark"
        self.d = self.cfg.to_dict()

    def test_serializes_mode(self):
        self.assertEqual(self.d["mode"], "stalk")

    def test_serializes_provider(self):
        self.assertEqual(self.d["llm_provider"], "gemini")

    def test_serializes_permissions(self):
        self.assertIn("permissions", self.d)
        self.assertEqual(self.d["permissions"]["confirmation_required"], "risky")

    def test_serializes_3d_as_alias(self):
        self.assertIn("3d", self.d)
        self.assertNotIn("_3d", self.d)
        self.assertTrue(self.d["3d"]["enabled"])

    def test_serializes_skins(self):
        self.assertIn("skins", self.d)
        self.assertFalse(self.d["skins"]["enabled"])

    def test_serializes_ocr(self):
        self.assertIn("ocr", self.d)
        self.assertEqual(self.d["ocr"]["interval_seconds"], 12)

    def test_serializes_theme(self):
        self.assertIn("theme", self.d)
        self.assertEqual(self.d["theme"]["name"], "dark")

    def test_omits_empty_api_keys(self):
        self.assertNotIn("gemini_api_key", self.d)

    def test_includes_nonempty_api_key(self):
        self.cfg.gemini_api_key = "AIzaSyTest"
        d = self.cfg.to_dict()
        self.assertEqual(d["gemini_api_key"], "AIzaSyTest")


class TestSaveLoadCycle(unittest.TestCase):
    """Save config to disk, load it back, verify round-trip."""

    def setUp(self):
        # Save original paths, redirect to temp
        self._orig_data = cfgmod.DATA_DIR
        self._orig_cfg = cfgmod.CONFIG_PATH
        self._tmpdir = Path(tempfile.mkdtemp())
        cfgmod.DATA_DIR = self._tmpdir
        cfgmod.CONFIG_PATH = self._tmpdir / "config.json"

    def tearDown(self):
        cfgmod.DATA_DIR = self._orig_data
        cfgmod.CONFIG_PATH = self._orig_cfg
        import shutil
        shutil.rmtree(str(self._tmpdir), ignore_errors=True)

    def test_save_creates_file(self):
        cfg = cfgmod.AppConfig()
        cfg.mode = "whisper"
        cfg.save()
        self.assertTrue(cfgmod.CONFIG_PATH.exists())
        raw = json.loads(cfgmod.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(raw["mode"], "whisper")

    def test_load_round_trip(self):
        cfg = cfgmod.AppConfig()
        cfg.mode = "lullaby"
        cfg.llm_provider = "browser"
        cfg.permissions.confirmation_required = "never"
        cfg._3d.enabled = True
        cfg.save()

        loaded = cfgmod.load_config()
        self.assertEqual(loaded.mode, "lullaby")
        self.assertEqual(loaded.llm_provider, "browser")
        self.assertEqual(loaded.permissions.confirmation_required, "never")
        self.assertTrue(loaded._3d.enabled)

    def test_load_missing_file_returns_defaults(self):
        # File doesn't exist in temp dir
        cfg = cfgmod.load_config()
        self.assertEqual(cfg.mode, "tap")
        self.assertEqual(cfg.ollama_max_tokens, 200)

    def test_save_keeps_api_key(self):
        cfg = cfgmod.AppConfig()
        cfg.gemini_api_key = "AIzaSyTest123"
        cfg.save()
        loaded = cfgmod.load_config()
        self.assertEqual(loaded.gemini_api_key, "AIzaSyTest123")


class TestValidation(unittest.TestCase):
    """AppConfig.validate() must catch invalid states."""

    def setUp(self):
        self.cfg = cfgmod.AppConfig()

    def test_valid_config_returns_empty(self):
        errors = self.cfg.validate()
        self.assertEqual(errors, [])

    def test_invalid_mode(self):
        self.cfg.mode = "invalid"
        errors = self.cfg.validate()
        self.assertTrue(any("mode" in e for e in errors))

    def test_invalid_provider(self):
        self.cfg.llm_provider = "chatgpt"
        errors = self.cfg.validate()
        self.assertTrue(any("llm_provider" in e for e in errors))

    def test_small_roaming_box(self):
        self.cfg.roaming_box_width = 50
        errors = self.cfg.validate()
        self.assertTrue(any("Roaming" in e for e in errors))

    def test_negative_tokens(self):
        self.cfg.ollama_max_tokens = 0
        errors = self.cfg.validate()
        self.assertTrue(any("max_tokens" in e for e in errors))

    def test_small_context(self):
        self.cfg.ollama_num_ctx = 64
        errors = self.cfg.validate()
        self.assertTrue(any("num_ctx" in e for e in errors))

    def test_too_fast_proactive_tap(self):
        self.cfg.proactive_tap_interval_min = 2
        errors = self.cfg.validate()
        self.assertTrue(any("proactive_tap_interval_min" in e for e in errors))

    def test_too_fast_proactive_stalk(self):
        self.cfg.proactive_stalk_interval_min = 1
        errors = self.cfg.validate()
        self.assertTrue(any("proactive_stalk_interval_min" in e for e in errors))


class TestBackwardCompatibility(unittest.TestCase):
    """Loading old-format config.json (flat layout) must map to new schema."""

    def setUp(self):
        self._orig_data = cfgmod.DATA_DIR
        self._orig_cfg = cfgmod.CONFIG_PATH
        self._tmpdir = Path(tempfile.mkdtemp())
        cfgmod.DATA_DIR = self._tmpdir
        cfgmod.CONFIG_PATH = self._tmpdir / "config.json"

    def tearDown(self):
        cfgmod.DATA_DIR = self._orig_data
        cfgmod.CONFIG_PATH = self._orig_cfg
        import shutil
        shutil.rmtree(str(self._tmpdir), ignore_errors=True)

    def _write_old_config(self, overrides: dict | None = None):
        """Write a config in the OLD flat format (pre-rebuild)."""
        old = {
            "model": "stablelm-zephyr:3b",
            "llm_provider": "browser",
            "mode": "tap",
            "character": "atom",
            "render_mode": "2d",
            "confirmation_required": "always",
            "sandbox_max_actions_per_minute": 10,
            "sandbox_undo_stack_size": 5,
            "sandbox_write_allowed_paths": ["Documents", "Desktop"],
            "intro_enabled": True,
            "intro_skippable": True,
            "intro_skip_delay_ms": 1000,
            "ocr_interval_seconds": 8,
            "ocr_min_confidence": 60,
            "low_impact_mode": True,
            "cpu_threshold_ocr_suspend": 70,
            "theme": "cyber",
            "theme_primary_glow": "#00D4FF",
            "skin_enabled": True,
            "skin_atom": "assets/skins/atom_skin.png",
            "skin_pink": "assets/skins/pink_skin.png",
            "internet_check_host": "8.8.8.8",
            "provider_online_weights": {"gemini": 0.7, "browser": 0.3},
            "3d": {"enabled": False, "camera_angle": "side"},
            "roaming_box_width": 300,
            "roaming_box_height": 200,
            "gemini_api_key": "",
        }
        if overrides:
            old.update(overrides)
        cfgmod.CONFIG_PATH.write_text(
            json.dumps(old, indent=2), encoding="utf-8"
        )

    def test_loads_old_format_mode(self):
        self._write_old_config({"mode": "stalk"})
        cfg = cfgmod.load_config()
        self.assertEqual(cfg.mode, "stalk")

    def test_loads_old_format_provider(self):
        self._write_old_config({"llm_provider": "gemini"})
        cfg = cfgmod.load_config()
        self.assertEqual(cfg.llm_provider, "gemini")

    def test_old_model_maps_to_model_chat(self):
        self._write_old_config({"model": "stablelm-zephyr:3b"})
        cfg = cfgmod.load_config()
        self.assertEqual(cfg.model_chat, "stablelm-zephyr:3b")

    def test_old_confirmation_maps_to_permissions(self):
        self._write_old_config({"confirmation_required": "risky"})
        cfg = cfgmod.load_config()
        self.assertEqual(cfg.permissions.confirmation_required, "risky")

    def test_old_theme_maps_to_theme_name(self):
        self._write_old_config({"theme": "dark"})
        cfg = cfgmod.load_config()
        self.assertEqual(cfg.theme.name, "dark")

    def test_old_ocr_maps_to_subconfig(self):
        self._write_old_config({"ocr_interval_seconds": 15, "ocr_min_confidence": 75})
        cfg = cfgmod.load_config()
        self.assertEqual(cfg.ocr.interval_seconds, 15)
        self.assertEqual(cfg.ocr.min_confidence, 75)

    def test_old_3d_subconfig_still_works(self):
        self._write_old_config({"3d": {"enabled": False, "camera_angle": "side"}})
        cfg = cfgmod.load_config()
        self.assertFalse(cfg._3d.enabled)
        self.assertEqual(cfg._3d.camera_angle, "side")

    def test_old_roaming_bounds(self):
        self._write_old_config({"roaming_box_width": 500, "roaming_box_height": 400})
        cfg = cfgmod.load_config()
        self.assertEqual(cfg.roaming_box_width, 500)
        self.assertEqual(cfg.roaming_box_height, 400)

    def test_old_skin_fields(self):
        self._write_old_config({"skin_atom": "custom/skin.png", "skin_enabled": False})
        cfg = cfgmod.load_config()
        self.assertEqual(cfg.skins.atom, "custom/skin.png")
        self.assertFalse(cfg.skins.enabled)

    def test_empty_gemini_key_omitted(self):
        self._write_old_config({"gemini_api_key": ""})
        cfg = cfgmod.load_config()
        self.assertIsNone(cfg.gemini_api_key)

    def test_corrupt_config_returns_defaults(self):
        cfgmod.CONFIG_PATH.write_text("not json", encoding="utf-8")
        cfg = cfgmod.load_config()
        self.assertEqual(cfg.mode, "tap")


class TestMemory(unittest.TestCase):
    """AppMemory and load/save memory."""

    def setUp(self):
        self._orig_data = cfgmod.DATA_DIR
        self._orig_mem = cfgmod.MEMORY_PATH
        self._tmpdir = Path(tempfile.mkdtemp())
        cfgmod.DATA_DIR = self._tmpdir
        cfgmod.MEMORY_PATH = self._tmpdir / "memory.json"

    def tearDown(self):
        cfgmod.DATA_DIR = self._orig_data
        cfgmod.MEMORY_PATH = self._orig_mem
        import shutil
        shutil.rmtree(str(self._tmpdir), ignore_errors=True)

    def test_default_memory(self):
        mem = cfgmod.AppMemory()
        self.assertEqual(mem.facts, [])
        self.assertEqual(mem.session_count, 0)

    def test_save_and_load_memory(self):
        mem = cfgmod.AppMemory()
        mem.facts = ["Koko likes blue", "Atom taps"]
        mem.session_count = 5
        cfgmod.save_memory(mem)
        loaded = cfgmod.load_memory()
        self.assertEqual(loaded.facts, ["Koko likes blue", "Atom taps"])
        self.assertEqual(loaded.session_count, 5)

    def test_save_dict_works(self):
        cfgmod.save_memory({"facts": ["test"], "session_count": 3})
        loaded = cfgmod.load_memory()
        self.assertEqual(loaded.facts, ["test"])
        self.assertEqual(loaded.session_count, 3)

    def test_load_missing_returns_empty(self):
        mem = cfgmod.load_memory()
        self.assertEqual(mem.facts, [])

    def test_load_corrupt_returns_empty(self):
        cfgmod.MEMORY_PATH.write_text("garbage", encoding="utf-8")
        mem = cfgmod.load_memory()
        self.assertEqual(mem.facts, [])


if __name__ == "__main__":
    unittest.main()
