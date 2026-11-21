"""
Atom Configuration Layer — Step 1 of the rebuild per master prompt §4.

Schema: config.json + memory.json, both strongly-typed with dataclasses.
Permissions block baked in from the start.
Loader/validator: fails loudly on schema violations, falls back to defaults only
on file-not-found or deserialization errors that would prevent the app from starting.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields, asdict
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = DATA_DIR / "config.json"
MEMORY_PATH = DATA_DIR / "memory.json"


# ──────────────────────────────────────────────────────────────────────────────
# Dict-like mixin (so consumers can do config.get("key", default) or config["key"])
# ──────────────────────────────────────────────────────────────────────────────

class _DictLike:
    """Mixin providing dict-style access with field alias and backward-compat support."""

    def _resolve(self, key: str) -> str | tuple[str, str] | None:
        """Resolve a key: alias → direct attr, or backward-compat → (sub, field).
        Returns None if unresolvable."""
        # 1. Direct alias
        if key in _FIELD_ALIASES:
            return _FIELD_ALIASES[key]
        # 2. Backward compat
        if key in _BACKWARD_COMPAT:
            return _BACKWARD_COMPAT[key]
        # 3. Direct attribute
        if hasattr(self, key):
            return key
        return None

    def _get_backward(self, spec: tuple[str, str], default: Any = None) -> Any:
        sub_name, field_name = spec
        if sub_name:
            sub = getattr(self, sub_name, None)
            return getattr(sub, field_name, default) if sub else default
        return getattr(self, field_name, default)

    def get(self, key: str, default: Any = None) -> Any:
        resolved = self._resolve(key)
        if resolved is None:
            return default
        if isinstance(resolved, tuple):
            return self._get_backward(resolved, default)
        return getattr(self, resolved, default)

    def __getitem__(self, key: str) -> Any:
        resolved = self._resolve(key)
        if resolved is None:
            raise KeyError(key)
        if isinstance(resolved, tuple):
            val = self._get_backward(resolved)
            if val is None:
                raise KeyError(key)
            return val
        return getattr(self, resolved)

    def setdefault(self, key: str, default: Any = None) -> Any:
        """``dict.setdefault`` semantics — ``memory.setdefault("facts", []).append(x)``
        has to work, since config and memory objects are used like dicts."""
        resolved = self._resolve(key)
        if isinstance(resolved, tuple):
            return self._get_backward(resolved, default)
        name = resolved or key
        current = getattr(self, name, None)
        if current is not None:
            return current
        setattr(self, name, default)
        return default

    def __setitem__(self, key: str, value: Any) -> None:
        resolved = self._resolve(key)
        if resolved is None:
            raise KeyError(key)
        if isinstance(resolved, tuple):
            sub_name, field_name = resolved
            if sub_name:
                sub = getattr(self, sub_name, None)
                if sub and hasattr(sub, field_name):
                    setattr(sub, field_name, value)
                    return
            setattr(self, field_name, value)
            return
        setattr(self, resolved, value)

    def __contains__(self, key: str) -> bool:
        return self._resolve(key) is not None

    def keys(self) -> list[str]:
        return [f.name for f in fields(self)]


# Aliases shared across all config types — key consumers can use '3d' to reach '_3d'
_FIELD_ALIASES: dict[str, str] = {
    "3d": "_3d",
}

# Backward-compat mapping: old flat field names → (sub_config_attr, sub_field_name)
# Allows consumers to still do config.get("confirmation_required") even though it's now
# under config.permissions.confirmation_required
_BACKWARD_COMPAT: dict[str, tuple[str, str]] = {
    "confirmation_required": ("permissions", "confirmation_required"),
    "sandbox_max_actions_per_minute": ("permissions", "max_actions_per_minute"),
    "sandbox_undo_stack_size": ("permissions", "undo_stack_size"),
    "sandbox_write_allowed_paths": ("permissions", "write_allowed_paths"),
    "sandbox_dir": ("permissions", "sandbox_dir"),
    "allow_outside_writes": ("permissions", "allow_outside_writes"),
    "intro_enabled": ("intro", "enabled"),
    "intro_skippable": ("intro", "skippable"),
    "intro_skip_delay_ms": ("intro", "skip_delay_ms"),
    "ocr_interval_seconds": ("ocr", "interval_seconds"),
    "ocr_interval_stalk_seconds": ("ocr", "interval_stalk_seconds"),
    "ocr_interval_whisper_seconds": ("ocr", "interval_whisper_seconds"),
    "ocr_interval_lullaby_seconds": ("ocr", "interval_lullaby_seconds"),
    "ocr_min_confidence": ("ocr", "min_confidence"),
    "ocr_urgency_threshold": ("ocr", "urgency_threshold"),
    "low_impact_mode": ("ocr", "low_impact_mode"),
    "low_impact_downscale": ("ocr", "low_impact_downscale"),
    "cpu_threshold_ocr_suspend": ("ocr", "cpu_threshold_suspend"),
    "internet_check_interval_seconds": ("network", "check_interval_seconds"),
    "internet_check_host": ("network", "check_host"),
    "internet_check_port": ("network", "check_port"),
    "internet_check_timeout": ("network", "check_timeout"),
    "provider_online_weights": ("network", "provider_online_weights"),
    "theme": ("theme", "name"),
    "theme_primary_glow": ("theme", "primary_glow"),
    "theme_accent": ("theme", "accent"),
    "theme_panel_bg": ("theme", "panel_bg"),
    "theme_panel_opacity": ("theme", "panel_opacity"),
    "skin_enabled": ("skins", "enabled"),
    "skin_hd_resize": ("skins", "hd_resize"),
    "skin_atom": ("skins", "atom"),
    "skin_pink": ("skins", "pink"),
    "skin_gamergirl": ("skins", "gamergirl"),
    "skin_steve": ("skins", "steve"),
    "skin_alex": ("skins", "alex"),
    "skin_ghost": ("skins", "ghost"),
    "model": ("", "model_chat"),  # empty sub → treat as top-level alias
    "model_primary": ("", "model_chat"),
    "model_fallback": ("", "model_chat"),
    "ollama_timeout_seconds": ("", "ollama_timeout_chat_seconds"),
}


def _from_dict(target: Any, data: dict) -> None:
    """Populate a dataclass instance from a flat dict, skipping unknown keys."""
    valid = {f.name for f in fields(target)}
    for k, v in data.items():
        if k in valid:
            setattr(target, k, v)
    # Also check aliases
    for alias, real in _FIELD_ALIASES.items():
        if alias in data and real in valid:
            setattr(target, real, data[alias])


def _sub_config(target: Any, data: dict | Any) -> None:
    """Populate a sub-dataclass from a nested dict, or do nothing if already set."""
    if isinstance(data, dict):
        _from_dict(target, data)


# ═══════════════════════════════════════════════════════════════════════════════
# Sub-config dataclasses
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ThreeDConfig(_DictLike):
    enabled: bool = False
    model_path: str = "assets/models/atom.gltf"
    camera_angle: str = "side"
    camera_distance: float = 5.0
    camera_height: float = 2.0
    head_tracking: bool = True
    eye_tracking: bool = False
    shadow_quality: str = "off"
    antialiasing: int = 2
    animation_fps: int = 60


@dataclass
class Roaming3DConfig(_DictLike):
    walk_speed: float = 1.2
    run_speed: float = 2.4
    rotation_speed: float = 360.0
    acceleration: float = 8.0
    deceleration: float = 10.0
    jump_height: float = 1.5
    gravity: float = 12.0
    direction_change_interval_ms: int = 3000
    jump_chance_percent: float = 0.5


@dataclass
class EmotionConfig(_DictLike):
    enabled: bool = True
    bored_timeout_seconds: int = 60
    sleepy_time_start_hour: int = 1


@dataclass
class BrowserAIConfig(_DictLike):
    url: str = "https://chat.deepseek.com/"
    show_browser: bool = False


@dataclass
class ChatConfig(_DictLike):
    minimize_enabled: bool = True
    float_icon_size: int = 48
    float_icon_opacity: float = 0.7
    float_icon_position: str = "bottom-left"
    keyboard_shortcut: str = "Ctrl+Shift+E"
    auto_restore_on_typing: bool = False


@dataclass
class IntroConfig(_DictLike):
    enabled: bool = True
    skippable: bool = True
    skip_delay_ms: int = 1000


@dataclass
class PermissionsConfig(_DictLike):
    """Security & sandbox permissions — locked down by default.

    File policy (Phase 163): ``sandbox_dir`` is the ONLY folder Atom may write
    to freely. Everything else on disk is read-only; a write outside needs the
    user's explicit per-action permission, and only when ``allow_outside_writes``
    is true (the default — set it false for a hard lockdown).
    """
    confirmation_required: str = "always"         # "always" | "risky" | "never"
    max_actions_per_minute: int = 10
    undo_stack_size: int = 5
    sandbox_dir: str = "sandbox"                    # relative to the project root
    # True  → a write outside ``sandbox_dir`` is allowed once the user approves
    #         that specific action (the default: permission is the gate).
    # False → outside writes are refused outright, approval or not (hard lockdown).
    allow_outside_writes: bool = True
    # Legacy: these roots used to grant free writes. They no longer do — writes
    # are confined to `sandbox_dir`. Kept so old configs still load cleanly.
    write_allowed_paths: list[str] = field(default_factory=lambda: ["Documents", "Desktop", "Downloads"])
    clipboard_access: bool = False
    network_access: bool = True                     # only to declared endpoints
    self_modify_config: bool = False                # LLM cannot change config without user confirmation
    escalation_tiers: bool = False                  # no escalating permission tiers


@dataclass
class OCROpts(_DictLike):
    interval_seconds: int = 8
    interval_stalk_seconds: int = 5
    interval_whisper_seconds: int = 15
    interval_lullaby_seconds: int = 0
    min_confidence: int = 60
    urgency_threshold: int = 5
    low_impact_mode: bool = True
    low_impact_downscale: str = "600x400"
    cpu_threshold_suspend: int = 70


@dataclass
class NetworkConfig(_DictLike):
    check_interval_seconds: int = 30
    check_host: str = "8.8.8.8"
    check_port: int = 53
    check_timeout: int = 1
    provider_online_weights: dict = field(default_factory=lambda: {"gemini": 0.5, "browser": 0.5})


@dataclass
class ThemeConfig(_DictLike):
    name: str = "atom_hud"
    primary_glow: str = "#00D4FF"
    accent: str = "#0077FF"
    panel_bg: str = "#0A0F1A"
    panel_opacity: float = 0.88


@dataclass
class SkinPaths(_DictLike):
    atom: str = "assets/skins/atom_skin.png"
    pink: str = "assets/skins/pink_skin.png"
    gamergirl: str = "assets/skins/gamergirl.png"
    steve: str = "assets/skins/steve_skin.png"
    alex: str = "assets/skins/alex_skin.png"
    ghost: str = "assets/skins/ghost_skin.png"
    # Off by default: the hand-authored CHARACTER_PROFILES are the two Echo
    # designs (Atom + Pink) plus Steve/Alex/Ghost Rider. Loading a skin PNG
    # overrides every colour and face texture, which is why Atom started
    # looking like an unrelated dark cyber skin instead of Echo's design.
    enabled: bool = False
    hd_resize: bool = True


# ═══════════════════════════════════════════════════════════════════════════════
# AppConfig — the single top-level config dataclass
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AppConfig(_DictLike):
    """
    Root configuration object.

    All fields have sensible defaults so the app can start from zero config.
    Loaded once at startup via load_config(), then passed by reference.
    """

    # ── LLM / Provider ───────────────────────────────────────────────────────
    llm_provider: str = "ollama"                    # "ollama" | "gemini" | "browser"

    # Chat model (small, fast) — phi3:mini, Fast & Focused params
    model_chat: str = "phi3:mini"  # Best balance of speed + context memory
    ollama_url: str = "http://localhost:11434"
    ollama_temperature: float = 0.7                 # Balances creativity & coherence — spicy tap mode personality
    ollama_max_tokens: int = 200                    # Fast & Focused: shorter = faster, enough for chat
    ollama_num_ctx: int = 4096                      # Keep 4K context for phi3:mini's memory strength
    ollama_repeat_penalty: float = 1.05             # Fast & Focused: minimal penalty = natural flow
    ollama_keep_alive: str = "30m"
    ollama_auto_detect: bool = True
    ollama_streaming: bool = True
    # A *ceiling*, not a delay: the happy path (model already resident) answers
    # in a few seconds. It has to be generous enough to survive the one-off
    # cost of a model (re)load on a CPU-only machine, otherwise a slow-but-
    # working answer is thrown away and the chain falls through to the browser
    # provider — which launches a whole headless Chromium.
    ollama_timeout_chat_seconds: int = 60
    ollama_timeout_generation_seconds: int = 45

    # Analysis model (bigger, slower — for proactive/analysis tasks)
    model_analysis: str = "dolphin-mistral:7b-v2.6-q4_K_M"
    ollama_max_tokens_analysis: int = 500           # §3: analysis token budget
    ollama_num_ctx_analysis: int = 4096             # §3: analysis context window
    # The analysis model is a 7B running on CPU — it needs far more headroom
    # than chat. This key used to be missing from the config entirely, so the
    # router's 25s fallback always applied and analysis timed out every time.
    ollama_timeout_analysis_seconds: int = 90

    # Gemini
    gemini_api_key: Optional[str] = None
    gemini_api_key_2: Optional[str] = None
    gemini_api_key_3: Optional[str] = None
    gemini_api_key_4: Optional[str] = None
    gemini_api_key_5: Optional[str] = None
    gemini_model: str = "gemini-2.5-flash"
    gemini_temperature: float = 0.75
    gemini_max_output_tokens: int = 1024
    gemini_timeout_seconds: int = 20  # Tuned: Gemini avg 1.8s, 2x safety margin
    gemini_fallback_to_ollama: bool = True
    gemini_streaming: bool = True

    # Fallback routing
    ollama_fallback_to_gemini: bool = True
    browser_fallback_to_ollama: bool = True

    # Provider settings
    browser_timeout_seconds: int = 30
    browser_retry_attempts: int = 3
    browser_retry_backoff_seconds: list[int] = field(default_factory=lambda: [1, 2, 4])

    # ── Mode / character ─────────────────────────────────────────────────────
    mode: str = "tap"                               # tap | stalk | whisper | lullaby | formal
    character: str = "atom"
    render_mode: str = "auto"                       # "auto" | "2d" | "3d"

    # ── Roaming (2D) ─────────────────────────────────────────────────────────
    roaming_box_width: int = 400                    # §3: 400×300px roaming bounds
    roaming_box_height: int = 300
    sprite_width: int = 96
    sprite_height: int = 148
    sprite_scale: int = 2
    start_x: int = 120
    start_y: int = 120

    # ── Proactive intervals ───────────────────────────────────────────────────
    proactive_tap_interval_min: int = 20            # §3: 20–40s randomized
    proactive_tap_interval_max: int = 40
    proactive_stalk_interval_min: int = 10          # §3: 10–15s
    proactive_stalk_interval_max: int = 15
    proactive_cooldown_after_interaction: int = 90
    proactive_trigger_chance: float = 0.15

    # ── TTS ───────────────────────────────────────────────────────────────────
    tts_enabled: bool = True
    tts_voice: str = "Zira"
    tts_speed: int = 200
    tts_volume: float = 0.8

    # ── Security ──────────────────────────────────────────────────────────────
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)

    # ── Sub-configs ──────────────────────────────────────────────────────────
    _3d: ThreeDConfig = field(default_factory=ThreeDConfig)
    roaming_3d: Roaming3DConfig = field(default_factory=Roaming3DConfig)
    emotion: EmotionConfig = field(default_factory=EmotionConfig)
    browser_ai: BrowserAIConfig = field(default_factory=BrowserAIConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    intro: IntroConfig = field(default_factory=IntroConfig)
    ocr: OCROpts = field(default_factory=OCROpts)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    theme: ThemeConfig = field(default_factory=ThemeConfig)
    skins: SkinPaths = field(default_factory=SkinPaths)

    # ── Internal ──────────────────────────────────────────────────────────────
    _first_launch_done: bool = False

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize the full config to a JSON-compatible dict."""
        d: dict[str, Any] = {}
        # Guard: sub-configs that must stay as dataclass instances
        _SUB_KEYS = frozenset({
            "_3d", "roaming_3d", "emotion", "browser_ai", "chat",
            "intro", "ocr", "network", "theme", "skins", "permissions",
        })
        for f in fields(self):
            k = f.name
            v = getattr(self, k)
            if k in _SUB_KEYS:
                try:
                    d[k] = asdict(v)
                except TypeError:
                    # Fallback if sub-config got replaced with a plain dict/primitive
                    d[k] = v if isinstance(v, (dict, list, str, int, float, bool)) else str(v)
            elif k.startswith("_"):
                d[k] = v
            else:
                d[k] = v
        # Apply alias '3d' → '_3d' for the JSON key
        if "_3d" in d:
            d["3d"] = d.pop("_3d")
        # Remove empty API keys
        for key in ("gemini_api_key", "gemini_api_key_2", "gemini_api_key_3",
                     "gemini_api_key_4", "gemini_api_key_5"):
            if key in d and not d[key]:
                del d[key]
        return d

    def save(self) -> None:
        """Write config atomically to disk."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(CONFIG_PATH)

    # ── Validation ────────────────────────────────────────────────────────────

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty = valid)."""
        errors: list[str] = []
        valid_modes = {"tap", "stalk", "whisper", "lullaby", "formal"}
        if self.mode not in valid_modes:
            errors.append(f"Invalid mode '{self.mode}'; must be one of {sorted(valid_modes)}")
        valid_providers = {"ollama", "gemini", "browser"}
        if self.llm_provider not in valid_providers:
            errors.append(f"Invalid llm_provider '{self.llm_provider}'")
        if self.roaming_box_width < 100 or self.roaming_box_height < 100:
            errors.append("Roaming box must be at least 100×100")
        if self.ollama_max_tokens < 1:
            errors.append("ollama_max_tokens must be ≥ 1")
        if self.ollama_num_ctx < 128:
            errors.append("ollama_num_ctx must be ≥ 128")
        if self.proactive_tap_interval_min < 5:
            errors.append("proactive_tap_interval_min must be ≥ 5")
        if self.proactive_stalk_interval_min < 3:
            errors.append("proactive_stalk_interval_min must be ≥ 3")
        return errors


# ═══════════════════════════════════════════════════════════════════════════════
# Memory — typed schema for persistent facts / session data
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class AppMemory(_DictLike):
    """Persistent memory that grows across sessions."""
    facts: list[str] = field(default_factory=list)
    session_count: int = 0
    first_seen: str = ""                            # ISO timestamp
    last_seen: str = ""                             # ISO timestamp
    user_name: str = ""
    known_facts: dict[str, str] = field(default_factory=dict)
    running_jokes: list[str] = field(default_factory=list)
    mood_history: list[dict[str, Any]] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# Loaders
# ═══════════════════════════════════════════════════════════════════════════════

# Keys that are now sub-configs — skip flat assignment when loading old format
_SUB_CONFIG_KEYS = frozenset({
    "3d", "roaming_3d", "emotion", "browser_ai", "chat",
    "intro", "ocr", "network", "theme", "skins", "permissions",
})


def load_config() -> AppConfig:
    """Load config from disk. Falls back to clean defaults if file is missing."""
    if not CONFIG_PATH.exists():
        cfg = AppConfig()
        cfg.save()
        return cfg

    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cfg = AppConfig()

        # ── Map top-level fields ──
        for k, v in raw.items():
            if k == "3d" and isinstance(v, dict):
                _from_dict(cfg._3d, v)
            elif k == "roaming_3d" and isinstance(v, dict):
                _from_dict(cfg.roaming_3d, v)
            elif k == "emotion" and isinstance(v, dict):
                _from_dict(cfg.emotion, v)
            elif k == "browser_ai" and isinstance(v, dict):
                _from_dict(cfg.browser_ai, v)
            elif k == "chat" and isinstance(v, dict):
                _from_dict(cfg.chat, v)
            elif k == "intro" and isinstance(v, dict):
                _from_dict(cfg.intro, v)
            elif k == "ocr" and isinstance(v, dict):
                _from_dict(cfg.ocr, v)
            elif k == "network" and isinstance(v, dict):
                _from_dict(cfg.network, v)
            elif k == "theme" and isinstance(v, dict):
                _from_dict(cfg.theme, v)
            elif k == "skins" and isinstance(v, dict):
                _from_dict(cfg.skins, v)
            elif k == "permissions" and isinstance(v, dict):
                _from_dict(cfg.permissions, v)
            elif k in _SUB_CONFIG_KEYS:
                # Old-format value for a sub-config field (e.g., "theme": "cyber" as string)
                # Skip — compat_map handles these
                pass
            elif k.startswith("gemini_api_key") and not v:
                # Empty API key → None
                setattr(cfg, k, None)
            elif hasattr(cfg, k):
                setattr(cfg, k, v)

        # ── Backward compat: map old top-level field names to new locations ──
        compat_map = {
            "confirmation_required": ("permissions", "confirmation_required"),
            "sandbox_max_actions_per_minute": ("permissions", "max_actions_per_minute"),
            "sandbox_undo_stack_size": ("permissions", "undo_stack_size"),
            "sandbox_write_allowed_paths": ("permissions", "write_allowed_paths"),
            "sandbox_dir": ("permissions", "sandbox_dir"),
            "allow_outside_writes": ("permissions", "allow_outside_writes"),
            "intro_enabled": ("intro", "enabled"),
            "intro_skippable": ("intro", "skippable"),
            "intro_skip_delay_ms": ("intro", "skip_delay_ms"),
            "ocr_interval_seconds": ("ocr", "interval_seconds"),
            "ocr_interval_stalk_seconds": ("ocr", "interval_stalk_seconds"),
            "ocr_interval_whisper_seconds": ("ocr", "interval_whisper_seconds"),
            "ocr_interval_lullaby_seconds": ("ocr", "interval_lullaby_seconds"),
            "ocr_min_confidence": ("ocr", "min_confidence"),
            "ocr_urgency_threshold": ("ocr", "urgency_threshold"),
            "low_impact_mode": ("ocr", "low_impact_mode"),
            "low_impact_downscale": ("ocr", "low_impact_downscale"),
            "cpu_threshold_ocr_suspend": ("ocr", "cpu_threshold_suspend"),
            "internet_check_interval_seconds": ("network", "check_interval_seconds"),
            "internet_check_host": ("network", "check_host"),
            "internet_check_port": ("network", "check_port"),
            "internet_check_timeout": ("network", "check_timeout"),
            "provider_online_weights": ("network", "provider_online_weights"),
            "theme": ("theme", "name"),
            "theme_primary_glow": ("theme", "primary_glow"),
            "theme_accent": ("theme", "accent"),
            "theme_panel_bg": ("theme", "panel_bg"),
            "theme_panel_opacity": ("theme", "panel_opacity"),
            "skin_enabled": ("skins", "enabled"),
            "skin_hd_resize": ("skins", "hd_resize"),
            "skin_atom": ("skins", "atom"),
            "skin_pink": ("skins", "pink"),
            "skin_gamergirl": ("skins", "gamergirl"),
            "skin_steve": ("skins", "steve"),
            "skin_alex": ("skins", "alex"),
            "skin_ghost": ("skins", "ghost"),
            "model": ("model_chat",),               # old 'model' → model_chat
            "model_primary": ("model_chat",),
            "model_fallback": ("model_chat",),
            "ollama_timeout_seconds": ("ollama_timeout_chat_seconds",),
            "ollama_timeout_chat_seconds": None,    # already exists in new schema
            "ollama_max_tokens": None,              # already exists
            "ollama_num_ctx": None,                 # already exists
        }
        for old_key, target in compat_map.items():
            if old_key not in raw:
                continue
            if target is None:
                continue  # already handled by direct field mapping
            if len(target) == 1:
                # Top-level field rename
                if hasattr(cfg, target[0]):
                    setattr(cfg, target[0], raw[old_key])
            elif len(target) == 2:
                # Sub-config field
                sub = getattr(cfg, target[0], None)
                if sub is not None and hasattr(sub, target[1]):
                    setattr(sub, target[1], raw[old_key])

        return cfg
    except Exception as exc:
        # Corrupt config — start fresh with defaults
        import logging
        logging.getLogger("atom.config").warning("Config load failed (%s); using defaults", exc)
        return AppConfig()


def save_memory(memory: AppMemory | dict) -> None:
    """Persist memory to disk."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if isinstance(memory, dict):
            data = memory
        elif isinstance(memory, AppMemory):
            data = asdict(memory)
        else:
            data = {"facts": []}
        MEMORY_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        import logging
        logging.getLogger("atom.config").warning("Failed to save memory: %s", exc)


def load_memory() -> AppMemory:
    """Load memory from disk. Returns empty AppMemory on any error."""
    if not MEMORY_PATH.exists():
        return AppMemory()
    try:
        raw = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
        mem = AppMemory()
        if isinstance(raw, dict):
            for k, v in raw.items():
                if hasattr(mem, k):
                    setattr(mem, k, v)
        return mem
    except Exception:
        return AppMemory()
