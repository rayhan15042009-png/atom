"""Persisted chat transcript, shared by the GUI overlay and the CLI.

Why this exists
---------------
``atom_cli.py`` has always written ``data/chat_history.json``, but the GUI never
read it: every launch of ``atom.py`` opened an empty chat panel even though the
conversation was sitting on disk, and the brain therefore also lost the context
of the last conversation. Both front ends now use this module, so there is one
file, one format and one cap.

Format — a JSON list, oldest first::

    [{"role": "user", "content": "hello", "ts": "2026-09-25T07:31:02"}, ...]

``ts`` is optional. The CLI has always written ``{"role", "content"}`` without
it, and those files keep working; the overlay uses ``ts`` for the restored
bubble's timestamp when it is present. Only ``user`` and ``assistant`` turns are
stored — the panel's status/system rows are presentation, not conversation —
which also keeps the restored list a valid LLM message history
(``core/brain.py`` replays it into the prompt).
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / "data" / "chat_history.json"

#: Turns kept on disk, and replayed into the panel on launch. This is about what
#: the *user* can scroll back through — the model's context is capped separately
#: and much smaller (``core.brain.MAX_HISTORY_TURNS``), so a long transcript costs
#: nothing at inference time. 50 was the old cap and made anything older than the
#: last 25 exchanges vanish between sessions.
MAX_STORED = 500
MAX_RESTORED = 500

#: How many turns the CLI prints as its "Previous conversation" preamble. The
#: transcript may now hold hundreds; a wall of text on every launch is not useful.
MAX_PREVIEW = 8

#: Roles that are part of the conversation. ``system`` rows are UI status
#: (greetings, action previews, errors) and are not persisted.
PERSISTED_ROLES = ("user", "assistant")


def _clean(entry: Any) -> Optional[dict]:
    """Return a normalised entry, or ``None`` if it is not usable."""
    if not isinstance(entry, dict):
        return None
    role = entry.get("role")
    content = entry.get("content")
    if not isinstance(role, str) or role not in PERSISTED_ROLES:
        return None
    if not isinstance(content, str) or not content.strip():
        return None
    out: dict = {"role": role, "content": content}
    timestamp = entry.get("ts")
    if isinstance(timestamp, str) and timestamp:
        out["ts"] = timestamp
    return out


def load_chat_history(max_items: int = MAX_RESTORED, path: Optional[Path] = None) -> list[dict]:
    """Read the transcript, newest ``max_items`` entries, oldest first.

    A missing, unreadable or malformed file yields an empty list — a lost
    transcript must never stop the app from starting.
    """
    target = Path(path) if path is not None else HISTORY_PATH
    try:
        if not target.exists():
            return []
        saved = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(saved, list):
        return []
    valid = [e for e in (_clean(item) for item in saved) if e is not None]
    return valid[-max_items:]


def save_chat_history(entries: list[dict], path: Optional[Path] = None) -> None:
    """Write the newest :data:`MAX_STORED` usable entries.

    Best-effort: a read-only disk must not raise into the UI thread.
    """
    target = Path(path) if path is not None else HISTORY_PATH
    keep = [e for e in (_clean(item) for item in entries or []) if e is not None]
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(keep[-MAX_STORED:], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def append_chat_message(entries: list[dict], role: str, content: str,
                        ts: Optional[datetime.datetime] = None,
                        path: Optional[Path] = None) -> list[dict]:
    """Append one turn, trim to the cap, persist, and return the new list.

    Non-persisted roles are ignored, so callers can pass every message the panel
    shows without filtering first.
    """
    stamp = (ts or datetime.datetime.now()).isoformat(timespec="seconds")
    entries = list(entries or [])
    entry = _clean({"role": role, "content": content, "ts": stamp})
    if entry is not None:
        entries.append(entry)
    save_chat_history(entries, path=path)
    return entries[-MAX_STORED:]


def parse_timestamp(value: Any) -> Optional[datetime.datetime]:
    """Parse an entry's ``ts``; ``None`` when absent or unparseable."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
