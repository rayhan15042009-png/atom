"""
ProviderRouter — Orchestrates the LLM provider chain.

Chain order: Ollama → Gemini → Browser AI → Hardcoded fallback

Features:
- Cooldown tracking (failed providers aren't retried immediately)
- Mode-aware routing (chat model vs analysis model)
- Key rotation for Gemini (tries keys 1..N in order)
- Graceful degradation when all providers fail
"""

from __future__ import annotations

import logging
import random
import socket
import time as _time
from typing import Optional

from core.providers.ollama import (
    call_ollama,
    largest_installed_model,
    resolve_chat_model,
)
from core.providers.gemini import call_gemini, get_gemini_keys
from core.providers.browser import BrowserWorker, is_browser_available

log = logging.getLogger("atom.providers.router")

# ── Cooldown: don't retry a failed provider for this many seconds ──
COOLDOWN_SECONDS = 30

# ── Internet reachability cache ──
# _check_internet() opens a TCP socket with a 2s timeout. It used to run on
# every chat/analysis request, adding up to 2s of blocking latency to the
# (single) brain thread. Cache the answer for a short window instead.
_INTERNET_TTL_SECONDS = 30.0
_INTERNET_CACHE: dict[str, float | bool] = {"ok": True, "ts": 0.0}

# ── Fallback responses ──
FALLBACK_POOL: list[str] = [
    "*tap* All models failed. Your move, Koko.",
    "*tap* Brain's taking a nap. Give me a sec, Koko.",
    "*tap* Connection's being weird. Try again?",
    "*tap* Huh. Silence from the void. Koko, say that again?",
    "*tap* Error city. Population: Me. Try again, Koko?",
]


def _contextual_fallback(user_text: str, mode: str = "tap") -> str:
    """Generate a contextually appropriate fallback response."""
    user_lower = user_text.lower().strip()
    if mode == "formal":
        if any(w in user_lower for w in ["hello", "hey", "hi", "thanks", "thank"]):
            return "*tap* Greetings, Koko. How may I assist?"
        return "*tap* I apologize, Koko. I'm experiencing a connection issue."
    if any(w in user_lower for w in ["hello", "hey", "hi", "yo", "sup"]):
        return "*tap* Hey Koko. You rang?"
    if "weather" in user_lower:
        return "*tap* Weather's busted. Either the API hates me or I need to try harder."
    return random.choice(FALLBACK_POOL)


# ═══════════════════════════════════════════════════════════════════════════════
# ProviderRouter
# ═══════════════════════════════════════════════════════════════════════════════

class ProviderRouter:
    """Routes LLM requests through the provider chain with fallback and cooldown.

    Usage:
        router = ProviderRouter(config)
        response = router.chat(messages, mode="tap")
        analysis = router.analyze(messages)
    """

    def __init__(self, config):
        self.config = config
        self._cooldowns: dict[str, float] = {}  # provider_name -> time when cooldown ends
        # The key the NEXT request starts from. Advances on every success, so
        # consecutive requests spread across all configured keys instead of
        # hammering the first one.
        self._gemini_key_index: int = 0
        #: ``(slot, total)`` of the Gemini key that answered most recently — 1
        #: based, for the log line and the status HUD. ``None`` when a different
        #: provider answered, or when every key failed.
        self.last_gemini_key_slot: Optional[tuple[int, int]] = None
        #: Provider that produced the most recent successful response.
        self.last_provider: Optional[str] = None
        self._browser_worker: Optional[BrowserWorker] = None
        self._browser_initialized: bool = False

    def gemini_key_label(self) -> str:
        """``"#3/5"`` for the key that answered last, or ``""`` if none did."""
        if not self.last_gemini_key_slot:
            return ""
        slot, total = self.last_gemini_key_slot
        return f"#{slot}/{total}"

    # ── Cooldown management ──

    def _is_on_cooldown(self, provider: str) -> bool:
        """Check if a provider is on cooldown."""
        end_time = self._cooldowns.get(provider, 0.0)
        return _time.time() < end_time

    def _set_cooldown(self, provider: str, seconds: float = COOLDOWN_SECONDS):
        """Put a provider on cooldown."""
        self._cooldowns[provider] = _time.time() + seconds
        log.info("Provider '%s' on cooldown for %.0fs", provider, seconds)

    def _clear_cooldown(self, provider: str):
        """Clear a provider's cooldown."""
        self._cooldowns.pop(provider, None)

    # ── Config helpers ──

    def _get_cfg(self, key: str, default=None):
        if isinstance(self.config, dict):
            return self.config.get(key, default)
        return getattr(self.config, key, default)

    # ── Chat routing ──

    def chat(self, messages: list[dict], mode: str = "tap") -> Optional[str]:
        """Route a chat request through the provider chain.

        Args:
            messages: List of ``{"role": ..., "content": ...}`` dicts.
            mode: Current mode (affects routing and fallback tone).

        Returns:
            Response text or a contextual fallback string.
        """
        provider_order = self._get_provider_order()
        log.info("Routing chat request — provider_order=%s, mode=%s", provider_order, mode)

        for provider in provider_order:
            if self._is_on_cooldown(provider):
                log.debug("Skipping provider '%s' (on cooldown)", provider)
                continue

            log.info("Trying provider '%s' for chat...", provider)
            response = self._try_provider(provider, messages, mode)
            if response:
                # Success — clear cooldown for this provider
                self._clear_cooldown(provider)
                self.last_provider = provider
                if provider != "gemini":
                    # The key slot describes *Gemini's* answer; a different
                    # provider answering makes it stale.
                    self.last_gemini_key_slot = None
                return response

            # Failure — set cooldown
            self._set_cooldown(provider)

        # All providers failed — return fallback
        user_text = messages[-1].get("content", "") if messages else ""
        return _contextual_fallback(user_text, mode)

    def analyze(self, messages: list[dict]) -> Optional[str]:
        """Run an analysis task — always on Ollama, never on a cloud provider.

        Analysis (condensing search results, working out what Koko meant) is the
        task Gemini and Browser AI are worst at and the one that burns their
        quota, so it stays local:

        * chat provider is Ollama → the configured ``model_analysis``, i.e. the
          normal local setup;
        * chat provider is Gemini or Browser AI → the **largest model actually
          installed** in Ollama, because a small chat model cannot summarise.

        Returns the text, or ``None`` when no local model could answer. Callers
        must treat ``None`` as "no analysis available" and carry on without it.
        """
        if self._is_on_cooldown("ollama"):
            log.debug("Ollama on cooldown — skipping analysis")
            return None

        model = self._analysis_model()
        log.info("Routing analysis request — provider=ollama, model=%s", model)
        response = self._try_ollama(messages, analysis=True, model=model)
        if response:
            self._clear_cooldown("ollama")
            self.last_provider = "ollama"
            self.last_gemini_key_slot = None
            self._clear_cooldown("gemini")
            return response

        self._set_cooldown("ollama")
        log.warning("Local analysis failed — no analysis result")
        return None

    def _analysis_model(self) -> str:
        """The Ollama model analysis should use for the current chat provider."""
        configured = str(self._get_cfg("model_analysis", "") or "")
        preferred = str(self._get_cfg("llm_provider", "") or "").lower()
        if preferred == "ollama":
            # Everything already runs locally — keep the user's own choice.
            return configured or "phi3:mini"

        url = self._get_cfg("ollama_url", "http://localhost:11434")
        biggest = largest_installed_model(url)
        if biggest:
            log.info(
                "Chat provider is '%s' — routing analysis to the largest local model (%s)",
                preferred or "?", biggest,
            )
            return biggest
        log.info("No Ollama models discovered — falling back to model_analysis=%s", configured)
        return configured or "phi3:mini"

    def _check_internet(self, force: bool = False) -> bool:
        """Quick check if internet is reachable via DNS (8.8.8.8:53).

        Result is cached for ``_INTERNET_TTL_SECONDS`` so the blocking socket
        probe does not run on every request.
        """
        now = _time.time()
        if not force and (now - float(_INTERNET_CACHE["ts"])) < _INTERNET_TTL_SECONDS:
            return bool(_INTERNET_CACHE["ok"])

        ok = False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect(("8.8.8.8", 53))
            sock.close()
            ok = True
        except Exception:
            ok = False

        _INTERNET_CACHE["ok"] = ok
        _INTERNET_CACHE["ts"] = now
        return ok

    def _get_provider_order(self) -> list[str]:
        """Determine provider order. User chooses from Settings — no shuffling.

        Rules:
        - User's `llm_provider` setting determines which provider is tried first
        - If preferred provider is cloud-based (gemini/browser) AND no internet → skip to Ollama
        - Gemini keys-2..5 rotate automatically in _try_gemini() to avoid quota exhaustion
        - Browser AI only included if user explicitly logged in and configured it
        - Ollama is always the final fallback (works offline, no API key needed)
        """
        preferred = self._get_cfg("llm_provider", "gemini")
        has_internet = self._check_internet()
        has_gemini_keys = len(get_gemini_keys(self.config)) > 0
        has_browser = is_browser_available()

        # No internet → Ollama only (cloud providers are guaranteed to fail)
        if not has_internet:
            log.info("No internet — using Ollama only (offline mode)")
            return ["ollama"]

        log.info(
            "Provider routing — preferred=%s, internet=%s, gemini_keys=%d, browser=%s",
            preferred, has_internet, len(get_gemini_keys(self.config)), has_browser
        )

        # Build clean priority order — no shuffling, just fallback chain
        order: list[str] = []

        if preferred == "ollama":
            order = ["ollama"]
            if has_gemini_keys:
                order.append("gemini")
            if has_browser:
                order.append("browser")
        elif preferred == "gemini" and has_gemini_keys:
            order = ["gemini"]
            if has_browser:
                order.append("browser")  # No shuffle — clean second priority
            order.append("ollama")
        elif preferred == "browser" and has_browser:
            order = ["browser"]
            if has_gemini_keys:
                order.append("gemini")
            order.append("ollama")
        else:
            # Preferred provider not available — auto-select best available
            if has_gemini_keys:
                order.append("gemini")
            if has_browser:
                order.append("browser")
            order.append("ollama")  # Always available

        # Filter unavailable providers
        filtered: list[str] = []
        for p in order:
            if p == "gemini" and not has_gemini_keys:
                continue
            if p == "browser" and not has_browser:
                continue
            if p not in filtered:
                filtered.append(p)

        log.info("Final provider order: %s", filtered)
        return filtered

    def _try_provider(self, provider: str, messages: list[dict], mode: str, analysis: bool = False) -> Optional[str]:
        """Try a single provider and return the response, or None on failure."""
        if provider == "ollama":
            return self._try_ollama(messages, analysis)
        elif provider == "gemini":
            return self._try_gemini(messages)
        elif provider == "browser":
            return self._try_browser(messages)
        return None

    # ── Individual provider calls ──

    def _try_ollama(
        self,
        messages: list[dict],
        analysis: bool = False,
        model: Optional[str] = None,
    ) -> Optional[str]:
        """Call Ollama with appropriate model settings."""
        url = self._get_cfg("ollama_url", "http://localhost:11434")
        if analysis:
            model = model or self._get_cfg("model_analysis", "dolphin-mistral:7b-v2.6-q4_K_M")
            max_tokens = self._get_cfg("ollama_max_tokens_analysis", 500)
            num_ctx = self._get_cfg("ollama_num_ctx_analysis", 4096)
            timeout = self._get_cfg("ollama_timeout_analysis_seconds", 90)
            log.info("Ollama model selected — task=analysis, model=%s, max_tokens=%d, ctx=%d, timeout=%ds",
                     model, max_tokens, num_ctx, timeout)
        else:
            model = self._get_cfg("model_chat", "phi3:mini")
            max_tokens = self._get_cfg("ollama_max_tokens", 200)
            # Same context window for every task: changing num_ctx makes Ollama
            # reload the model (a 30-60s stall on CPU), so never let chat and
            # analysis disagree here.
            num_ctx = self._get_cfg("ollama_num_ctx", 4096)
            timeout = self._get_cfg("ollama_timeout_chat_seconds", 60)
            if self._get_cfg("ollama_auto_detect", True):
                # A model that was never pulled costs the full timeout (or an
                # instant 404) and the user just sees a spinning indicator.
                model, note = resolve_chat_model(str(model), url=url)
                if note:
                    log.warning(note)
            log.info("Ollama model selected — task=chat, model=%s, max_tokens=%d, ctx=%d, timeout=%ds",
                     model, max_tokens, num_ctx, timeout)

        temperature = self._get_cfg("ollama_temperature", 0.6)
        # Keep the model resident between questions so the next answer does not
        # pay another cold load (the config key used to be ignored entirely).
        keep_alive = self._get_cfg("ollama_keep_alive", "30m") or "30m"

        return call_ollama(
            messages,
            model=model,
            url=url,
            timeout=timeout,
            temperature=temperature,
            max_tokens=max_tokens,
            num_ctx=num_ctx,
            keep_alive=str(keep_alive),
        )

    def _try_gemini(self, messages: list[dict]) -> Optional[str]:
        """Call Gemini, spreading requests across every configured key.

        Keys are used **round-robin**: each request starts at the key after the
        one that answered last, so five keys give five times the daily quota
        instead of four keys idle behind the first. Within a single request a
        failing key still falls through to the next one, so the rotation costs
        nothing in resilience.

        The key is never logged — only its slot (``key 3/5``) — because the log
        file is not a secret store.
        """
        keys = get_gemini_keys(self.config)
        if not keys:
            return None

        num_keys = len(keys)
        for offset in range(num_keys):
            idx = (self._gemini_key_index + offset) % num_keys
            key = keys[idx]

            model = self._get_cfg("gemini_model", "gemini-2.5-flash")
            timeout = self._get_cfg("gemini_timeout_seconds", 15)
            temperature = self._get_cfg("gemini_temperature", 0.75)
            max_tokens = self._get_cfg("gemini_max_output_tokens", 1024)

            response = call_gemini(
                messages,
                api_key=key,
                model=model,
                timeout=timeout,
                temperature=temperature,
                max_output_tokens=max_tokens,
            )
            if response:
                slot = idx + 1
                next_slot = (idx + 1) % num_keys + 1
                self.last_gemini_key_slot = (slot, num_keys)
                self._gemini_key_index = (idx + 1) % num_keys
                log.info(
                    "Gemini key %d/%d answered — next request starts at key %d/%d",
                    slot, num_keys, next_slot, num_keys,
                )
                return response

            log.warning("Gemini key %d/%d failed — trying the next key", idx + 1, num_keys)

        self.last_gemini_key_slot = None
        return None

    def _try_browser(self, messages: list[dict]) -> Optional[str]:
        """Call the browser-based AI provider."""
        if not is_browser_available():
            return None

        try:
            if not self._browser_initialized:
                self._browser_worker = BrowserWorker(
                    self.config if isinstance(self.config, dict) else {}
                )
                if not self._browser_worker.start(headless=True):
                    log.warning("Browser provider: failed to start")
                    return None
                if not self._browser_worker.navigate():
                    log.warning("Browser provider: failed to navigate")
                    return None
                self._browser_initialized = True

            if not self._browser_worker:
                return None

            # Send system prompt if present in first message
            if messages and messages[0].get("role") == "system":
                self._browser_worker.send_system_prompt(messages[0]["content"])
                messages = messages[1:]

            # Send the last user message
            user_msg = messages[-1].get("content", "") if messages else ""
            if not user_msg:
                return None

            return self._browser_worker.send(user_msg)

        except Exception as exc:
            log.warning("Browser provider error: %s", exc)
            self._browser_initialized = False
            return None

    # ── Cleanup ──

    def cleanup(self):
        """Close the browser worker if it was started."""
        if self._browser_worker:
            try:
                self._browser_worker.close()
            except Exception:
                log.warning("Browser worker cleanup failed")
            self._browser_worker = None
            self._browser_initialized = False

    # ── Status ──

    def get_status(self) -> dict:
        """Get the current routing status for debugging."""
        return {
            "ollama_cooldown": self._cooldowns.get("ollama"),
            "gemini_cooldown": self._cooldowns.get("gemini"),
            "browser_cooldown": self._cooldowns.get("browser"),
            "gemini_key_index": self._gemini_key_index,
            "browser_initialized": self._browser_initialized,
            "browser_worker": self._browser_worker is not None,
        }
