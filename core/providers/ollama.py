"""
Ollama provider — local LLM inference via the Ollama API.

Calls /api/chat with a messages array. Supports configurable model,
temperature, max_tokens, context window, and streaming.
"""

from __future__ import annotations

import json as _json
import logging
import re
import time as _time
import urllib.error as _error
import urllib.request as _request
from typing import Optional

log = logging.getLogger("atom.providers.ollama")


def call_ollama(
    messages: list[dict],
    model: str = "phi3:mini",
    url: str = "http://localhost:11434",
    timeout: int = 30,
    temperature: float = 0.75,
    max_tokens: int = 300,
    num_ctx: int = 2048,
    stream: bool = False,
    keep_alive: str = "30m",
) -> Optional[str]:
    """Call Ollama /api/chat with a messages array.

    Args:
        messages: List of ``{"role": ..., "content": ...}`` dicts.
        model: Model name (default ``phi3:mini``).
        url: Ollama server URL.
        timeout: Request timeout in seconds.
        temperature: LLM temperature (0.0-1.0).
        max_tokens: Max tokens to generate.
        num_ctx: Context window size.
        stream: Whether to stream tokens (not yet implemented).
        keep_alive: How long Ollama keeps the model resident after the call
            (e.g. ``"30m"``). Without this Ollama unloads the model after its
            5-minute default, so every later question pays a cold load again.

    Returns:
        Response text or ``None`` on failure.
    """
    payload = _json.dumps({
        "model": model,
        "messages": messages,
        "stream": stream,
        "keep_alive": keep_alive,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "num_ctx": num_ctx,
        },
    }).encode()

    req = _request.Request(
        f"{url.rstrip('/')}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        with _request.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read().decode())
            return data.get("message", {}).get("content", "")
    except _error.HTTPError as exc:
        log.warning("Ollama HTTP %d: %s", exc.code, exc.read()[:200])
        return None
    except Exception as exc:
        log.warning("Ollama error: %s", exc)
        return None


# ── Installed-model discovery ────────────────────────────────────────────────
# Analysis should run on the most capable model the machine actually has, not on
# a hard-coded name that may not be pulled. /api/tags is cheap and local.

_PARAM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([BM])", re.IGNORECASE)
# Models that cannot chat — never route a request to them.
_NON_CHAT_HINTS = ("embed", "embedding", "bge-", "minilm", "nomic-embed", "clip")


def list_models(url: str = "http://localhost:11434", timeout: float = 3.0) -> list[dict]:
    """Return the models installed in Ollama (``[]`` when it is not running)."""
    try:
        req = _request.Request(f"{url.rstrip('/')}/api/tags")
        with _request.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read().decode())
        models = data.get("models") or []
        return [m for m in models if isinstance(m, dict) and m.get("name")]
    except Exception as exc:
        log.debug("Ollama model list failed: %s", exc)
        return []


def _param_billions(model: dict) -> float:
    """Parameter count of a model in billions (0.0 when unknown)."""
    details = model.get("details") or {}
    for value in (details.get("parameter_size"), model.get("parameter_size")):
        if not value:
            continue
        match = _PARAM_RE.search(str(value))
        if not match:
            continue
        amount = float(match.group(1))
        return amount / 1000.0 if match.group(2).upper() == "M" else amount
    return 0.0


def pick_largest_model(models: list[dict]) -> Optional[str]:
    """The most capable chat model among *models*, or ``None``.

    Ranked by parameter count (from ``details.parameter_size``) and then by the
    on-disk size, which is the only signal available for some custom builds.
    Embedding-only models are skipped.
    """
    candidates = [
        m for m in models
        if str(m.get("name", "")).lower() and
        not any(hint in str(m.get("name", "")).lower() for hint in _NON_CHAT_HINTS)
    ]
    if not candidates:
        return None
    best = max(candidates, key=lambda m: (_param_billions(m), int(m.get("size") or 0)))
    return str(best.get("name"))


def largest_installed_model(url: str = "http://localhost:11434",
                            timeout: float = 3.0) -> Optional[str]:
    """Convenience: the biggest chat model installed in Ollama (or ``None``)."""
    return pick_largest_model(list_models(url, timeout))


_MODELS_CACHE: dict[str, tuple[float, list[dict]]] = {}
_MODELS_CACHE_TTL = 60.0


def installed_models(url: str = "http://localhost:11434",
                     timeout: float = 3.0,
                     ttl: float = _MODELS_CACHE_TTL) -> list[dict]:
    """``list_models`` with a short cache.

    This runs before chat requests, and ``/api/tags`` costs a round trip; one
    lookup a minute is plenty to notice a freshly pulled model.
    """
    now = _time.monotonic()
    cached = _MODELS_CACHE.get(url)
    if cached:
        # An empty list means Ollama is not running. Remember that too, for a
        # shorter while: otherwise every single chat request pays the probe
        # timeout before the router can fall back to another provider.
        ttl_for_entry = ttl if cached[1] else min(ttl, 10.0)
        if (now - cached[0]) < ttl_for_entry:
            return cached[1]
    models = list_models(url, timeout)
    _MODELS_CACHE[url] = (now, models)
    return models


def resolve_chat_model(configured: str,
                       url: str = "http://localhost:11434",
                       timeout: float = 3.0) -> tuple[str, Optional[str]]:
    """The model chat should actually ask for, and why it changed.

    A configured name that was never pulled makes Ollama answer 404 (or sit on
    the load for the whole timeout) while the user stares at "Atom is
    thinking…". When the configured model is missing, the largest installed chat
    model is used instead; the second element is a human-readable note.

    Returns ``(model, note)``; ``note`` is ``None`` when nothing was substituted.
    """
    name = str(configured or "").strip()
    models = installed_models(url, timeout)
    if not models or not name:
        return name, None  # cannot tell (server down) — let the call decide
    names = {str(m.get("name", "")) for m in models}
    if name in names:
        return name, None
    tagged = f"{name}:latest"
    if tagged in names:
        return tagged, None
    best = pick_largest_model(models)
    if not best:
        return name, None
    return best, (
        f"Ollama model '{name}' is not installed — using '{best}' instead "
        f"(installed: {', '.join(sorted(names))})"
    )
