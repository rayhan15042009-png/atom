"""
Gemini provider — cloud LLM inference via the Google Gemini API.

Features:
- call_gemini() with system instruction, temperature, max tokens
- Key rotation across multiple configured keys
- Quota management (conservative free-tier limits)
- Key validation
"""

from __future__ import annotations

import json as _json
import logging
import os
import re
import time as _time
from collections import deque
from pathlib import Path
from typing import Optional

import urllib.error as _error
import urllib.request as _request

log = logging.getLogger("atom.providers.gemini")

# ── Key validation ───────────────────────────────────────────────────────────

# Legacy format (pre-2025):  AIzaSy...  (35+ alphanumeric chars)
# New Auth format (2025+):   AQ.xxx     (40+ base64url chars)
# Rejected:                  ya29.xxx   (real OAuth 2.0 tokens, not API keys)
_LEGACY_KEY_PATTERN = re.compile(r"^AIza[A-Za-z0-9_-]{35,}$")
_AUTH_KEY_PATTERN = re.compile(r"^AQ\.[A-Za-z0-9_-]{40,}$")
_OAUTH_TOKEN_PATTERN = re.compile(r"^ya29\.[A-Za-z0-9_\-.]{20,}$")


def validate_gemini_key(api_key: str) -> Optional[str]:
    """Validate a Gemini API key; return an error string or None if valid.

    Accepts both legacy (AIzaSy...) and new Auth (AQ.xxx) formats.
    Rejects ya29.* OAuth tokens — those need the OAuth 2.0 flow, not API key auth.
    """
    if not api_key:
        return "API key is empty"
    if len(api_key) < 20:
        return "API key is too short (expected 20+ characters)"
    if len(api_key) > 500:
        return "API key is too long (expected <500 characters)"
    # Reject real OAuth 2.0 tokens (these are NOT API keys)
    if _OAUTH_TOKEN_PATTERN.match(api_key):
        return ("This looks like a Google OAuth 2.0 token (ya29.*). "
                "Gemini API keys start with 'AIzaSy' (legacy) or 'AQ.' (new Auth format). "
                "Get one at aistudio.google.com/apikey")
    # Accept legacy format (AIzaSy...)
    if _LEGACY_KEY_PATTERN.match(api_key):
        return None
    # Accept new Auth format (AQ.xxx — current format as of 2025+)
    if _AUTH_KEY_PATTERN.match(api_key):
        return None
    # Accept any non-empty string of printable ASCII (lenient fallback)
    if all(c.isprintable() and not c.isspace() for c in api_key):
        return None
    return "API key contains invalid characters"

# Names that can hold a key, in rotation order. They match `.env` /
# `env.template` so adding a key there is all a user has to do.
ENV_KEY_NAMES: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "GEMINI_API_KEY_2",
    "GEMINI_API_KEY_3",
    "GEMINI_API_KEY_4",
    "GEMINI_API_KEY_5",
)

CONFIG_KEY_NAMES: tuple[str, ...] = (
    "gemini_api_key",
    "gemini_api_key_2",
    "gemini_api_key_3",
    "gemini_api_key_4",
    "gemini_api_key_5",
)


def _config_value(config, name: str) -> str:
    """Read one config field, from a dict or a config object."""
    if config is None:
        return ""
    if isinstance(config, dict):
        return str(config.get(name) or "")
    return str(getattr(config, name, None) or "")


def save_gemini_keys(values: dict[str, str]) -> list[tuple[str, bool]]:
    """Persist Gemini keys to ``.env`` and make them live immediately.

    Keys are secrets, so they are written to the git-ignored ``.env`` — never to
    ``data/config.json``, which is committed. *values* maps an env name
    (``GEMINI_API_KEY``, ``GEMINI_API_KEY_2`` …) to its key; an empty value
    removes that line. Returns ``[(name, saved_ok)]``.
    """
    env_path = Path(__file__).resolve().parents[2] / ".env"
    newline = "\n"
    try:
        raw = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        if "\r\n" in raw:
            newline = "\r\n"  # keep the file's own line endings
        lines = raw.splitlines()
    except Exception as exc:
        log.warning("Could not read %s: %s", env_path, exc)
        lines = []

    wanted = {k: (v or "").strip() for k, v in values.items()}
    results: list[tuple[str, bool]] = []
    written: set[str] = set()
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        name = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else ""
        if name in wanted:
            if name in written:  # drop duplicates
                continue
            written.add(name)
            key = wanted[name]
            if not key:
                continue  # empty value removes the entry
            out.append(f"{name}={key}")
            results.append((name, True))
        else:
            out.append(line)

    for name in ENV_KEY_NAMES:
        if name in wanted and name not in written and wanted[name]:
            out.append(f"{name}={wanted[name]}")
            results.append((name, True))

    try:
        text = newline.join(out).rstrip() + newline
        env_path.write_text(text, encoding="utf-8", newline="")
    except Exception as exc:
        log.warning("Could not write %s: %s", env_path, exc)
        return [(name, False) for name, _ in results]

    for name, value in wanted.items():
        if value:
            os.environ[name] = value
        else:
            os.environ.pop(name, None)
    log.info("Saved %d Gemini key(s) to .env", sum(1 for _, ok in results if ok))
    return results


def get_gemini_keys(config=None) -> list[str]:
    """Get all configured Gemini API keys, in rotation order, de-duplicated.

    Reads the environment first — ``GEMINI_API_KEY`` then ``_2`` … ``_5`` — and
    then the same names in the config. Every configured key is returned so
    ``ProviderRouter._try_gemini`` can rotate through all of them.

    Only ``GEMINI_API_KEY`` used to be read from the environment, so keys placed
    at ``GEMINI_API_KEY_2`` … ``_5`` were loaded by ``load_dotenv`` and then
    silently ignored: five configured keys behaved like one.
    """
    keys: list[str] = []
    for name in ENV_KEY_NAMES:
        value = (os.environ.get(name) or "").strip().strip("\"'")
        if value and value not in keys:
            keys.append(value)
    for name in CONFIG_KEY_NAMES:
        value = _config_value(config, name).strip().strip("\"'")
        if value and value not in keys:
            keys.append(value)
    return keys


# ── API call ─────────────────────────────────────────────────────────────────

def call_gemini(
    messages: list[dict],
    api_key: str,
    model: str = "gemini-2.5-flash",
    timeout: int = 20,
    temperature: float = 0.75,
    max_output_tokens: int = 1024,
) -> Optional[str]:
    """Call the Google Gemini API.

    Args:
        messages: List of ``{"role": ..., "content": ...}`` dicts.
        api_key: Gemini API key.
        model: Model name.
        timeout: Request timeout in seconds.
        temperature: LLM temperature.
        max_output_tokens: Max output tokens.

    Returns:
        Response text or ``None`` on failure.
    """
    # Convert messages to Gemini format
    gemini_contents: list[dict] = []
    for msg in messages:
        role = "user" if msg.get("role") == "user" else "model"
        gemini_contents.append({
            "role": role,
            "parts": [{"text": msg.get("content", "")}],
        })

    # System instruction from first message if role=system
    system_instruction = None
    if messages and messages[0].get("role") == "system":
        system_instruction = {"parts": [{"text": messages[0]["content"]}]}
        gemini_contents = gemini_contents[1:]

    payload: dict = {
        "contents": gemini_contents,
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
        },
    }
    if system_instruction:
        payload["systemInstruction"] = system_instruction

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )

    req = _request.Request(
        url,
        data=_json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,  # Use header — ?key= param deprecated for new Auth keys
        },
    )

    try:
        with _request.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read().decode())
        candidates = data.get("candidates", [])
        if not candidates:
            return None
        parts = candidates[0].get("content", {}).get("parts", [])
        return parts[0].get("text", "") if parts else None
    except _error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        log.warning("Gemini HTTP %d: %s", exc.code, body[:200])
        return None
    except Exception as exc:
        log.warning("Gemini error: %s", exc)
        return None


# ── Quota manager ────────────────────────────────────────────────────────────

class GeminiQuotaManager:
    """Tracks Gemini API usage to prevent quota exhaustion.

    Conservative limits for the free tier:
    - 10 requests per minute
    - 1,400 requests per day
    """

    MAX_REQUESTS_PER_MINUTE = 10
    MAX_REQUESTS_PER_DAY = 1400

    def __init__(self) -> None:
        self._minute_requests: deque[float] = deque()
        self._day_requests: deque[float] = deque()
        self._day_date = _time.localtime().tm_yday

    def can_request(self) -> bool:
        """Check if a request can be made within quota limits."""
        now = _time.time()
        today = _time.localtime().tm_yday
        while self._minute_requests and self._minute_requests[0] < now - 60:
            self._minute_requests.popleft()
        if today != self._day_date:
            self._day_requests.clear()
            self._day_date = today
        return (
            len(self._minute_requests) < self.MAX_REQUESTS_PER_MINUTE
            and len(self._day_requests) < self.MAX_REQUESTS_PER_DAY
        )

    def record_request(self) -> None:
        """Record that a request was made."""
        now = _time.time()
        self._minute_requests.append(now)
        self._day_requests.append(now)

    def seconds_until_available(self) -> float:
        """Seconds until a quota slot opens up."""
        if not self._minute_requests:
            return 0.0
        oldest = self._minute_requests[0]
        elapsed = _time.time() - oldest
        return max(0.0, 60.0 - elapsed)

    def get_status(self) -> dict:
        """Get current quota status for debugging."""
        return {
            "minute_used": len(self._minute_requests),
            "minute_max": self.MAX_REQUESTS_PER_MINUTE,
            "day_used": len(self._day_requests),
            "day_max": self.MAX_REQUESTS_PER_DAY,
            "available": self.can_request(),
            "seconds_until_available": self.seconds_until_available(),
        }
