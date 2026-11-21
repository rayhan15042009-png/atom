"""Resolve "open <something>" targets for Atom.

``[open: google]`` used to run ``start "" google``, which Windows cannot resolve
and answers with an error dialog — the "it doesn't find the right files" bug.
This module turns a loose name into something that actually launches:

1. a URL (``https://…`` or a bare domain),
2. a known app alias (``notepad`` → ``notepad.exe``),
3. a Start Menu shortcut found by name (covers installed apps),
4. a target learned earlier (``data/learned_targets.json``),
5. a close local match — ``googel`` → ``google``, ``notpad`` → ``notepad`` —
   which is learned as an alias so the typo never costs a web lookup again,
6. a best guess from the web — ``<name>.com`` if it resolves, otherwise the top
   hit for "official website <name>" — which is then *saved*, so the second
   time Atom already knows what ``open <name>`` means.

Failures are remembered too. Each miss increments an ``attempts`` count; after
``NEGATIVE_SKIP_AFTER`` misses the web is no longer consulted for that name, so
asking twice for something that does not exist stays instant. A name that
starts resolving locally later (a freshly installed app, a corrected spelling)
replaces its failure entry automatically.
"""

from __future__ import annotations

import difflib
import json
import os
import socket
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.logger import get_logger

log = get_logger("app_resolver")

ROOT = Path(__file__).resolve().parent.parent
LEARNED_PATH = ROOT / "data" / "learned_targets.json"
#: After this many failed lookups, stop asking the web about a name.
NEGATIVE_SKIP_AFTER = 2
#: A typo this close is resolved locally with no network request at all.
FUZZY_STRONG = 0.90
#: Once the web has failed, a looser match is still better than nothing.
FUZZY_CUTOFF = 0.75
UNRESOLVED = "unresolved"

# ── Well-known sites, by the word people actually type ──
KNOWN_SITES: dict[str, str] = {
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "youtube": "https://www.youtube.com",
    "yt": "https://www.youtube.com",
    "github": "https://github.com",
    "gitlab": "https://gitlab.com",
    "reddit": "https://www.reddit.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
    "tiktok": "https://www.tiktok.com",
    "whatsapp": "https://web.whatsapp.com",
    "discord": "https://discord.com/app",
    "spotify": "https://open.spotify.com",
    "netflix": "https://www.netflix.com",
    "twitch": "https://www.twitch.tv",
    "amazon": "https://www.amazon.com",
    "stackoverflow": "https://stackoverflow.com",
    "wikipedia": "https://en.wikipedia.org",
    "chatgpt": "https://chat.openai.com",
    "gemini": "https://gemini.google.com",
    "claude": "https://claude.ai",
    "huggingface": "https://huggingface.co",
    "pypi": "https://pypi.org",
    "npm": "https://www.npmjs.com",
    "steam": "https://store.steampowered.com",
}

# ── Programs that ship with Windows (no Start Menu entry to find) ──
KNOWN_APPS: dict[str, str] = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "paint": "mspaint.exe",
    "mspaint": "mspaint.exe",
    "explorer": "explorer.exe",
    "files": "explorer.exe",
    "file explorer": "explorer.exe",
    "cmd": "cmd.exe",
    "command prompt": "cmd.exe",
    "terminal": "wt.exe",
    "windows terminal": "wt.exe",
    "powershell": "powershell.exe",
    "task manager": "taskmgr.exe",
    "taskmgr": "taskmgr.exe",
    "settings": "ms-settings:",
    "control panel": "control.exe",
    "snipping tool": "snippingtool.exe",
    "word": "winword.exe",
    "excel": "excel.exe",
    "powerpoint": "powerpnt.exe",
}

_START_MENU_DIRS = [
    Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
]

_learned_cache: Optional[dict] = None


# ── Learned targets ─────────────────────────────────────────────────────────

def load_learned() -> dict[str, dict]:
    """Read the saved ``name → target`` map (cached)."""
    global _learned_cache
    if _learned_cache is not None:
        return _learned_cache
    try:
        if LEARNED_PATH.exists():
            data = json.loads(LEARNED_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _learned_cache = {str(k).lower(): v for k, v in data.items() if isinstance(v, dict)}
                return _learned_cache
    except Exception as exc:
        log.warning("Could not read learned targets: %s", exc)
    _learned_cache = {}
    return _learned_cache


def _save(learned: dict) -> bool:
    """Write the learned map and refresh the cache."""
    global _learned_cache
    try:
        LEARNED_PATH.parent.mkdir(parents=True, exist_ok=True)
        LEARNED_PATH.write_text(json.dumps(learned, indent=2), encoding="utf-8")
        _learned_cache = learned
        return True
    except Exception as exc:
        log.warning("Could not save learned targets: %s", exc)
        return False


def remember(name: str, kind: str, target: str, source: str = "learned") -> dict:
    """Persist one resolved target so the next request is instant."""
    name = (name or "").strip().lower()
    if not name or not target:
        return {}
    learned = dict(load_learned())
    previous = learned.get(name) or {}
    hits = int(previous.get("hits", 0)) + 1 if previous.get("kind") != UNRESOLVED else 1
    learned[name] = {
        "kind": kind, "target": target, "source": source, "hits": hits,
    }
    if _save(learned):
        log.info("Learned target: '%s' → %s (%s, hits=%d)", name, target, kind, hits)
    return learned[name]


def record_failure(name: str) -> dict:
    """Remember that *name* could not be resolved.

    Attempts accumulate across sessions, which is what lets ``resolve`` stop
    paying for a web lookup on a name that has never worked.
    """
    name = (name or "").strip().lower().rstrip("/")
    if not name:
        return {}
    learned = dict(load_learned())
    previous = learned.get(name) or {}
    attempts = int(previous.get("attempts", 0)) + 1
    learned[name] = {
        "kind": UNRESOLVED,
        "target": "",
        "source": "failed",
        "attempts": attempts,
        "last_try": datetime.now().isoformat(timespec="seconds"),
    }
    if _save(learned):
        log.info("Open target '%s' unresolved (attempt %d)", name, attempts)
    return learned[name]


def is_locally_blacklisted(name: str) -> bool:
    """True when this name has already failed enough times to skip the web."""
    entry = load_learned().get((name or "").strip().lower().rstrip("/")) or {}
    return entry.get("kind") == UNRESOLVED and int(entry.get("attempts", 0)) >= NEGATIVE_SKIP_AFTER


def suggest(name: str, limit: int = 3) -> list[str]:
    """Close matches for an unresolvable name, for a helpful error message."""
    name = (name or "").strip().lower()
    if not name:
        return []
    pool = list(KNOWN_SITES) + list(KNOWN_APPS)
    pool += [k for k, v in load_learned().items() if isinstance(v, dict) and v.get("kind") != UNRESOLVED]
    matches = difflib.get_close_matches(name, pool, n=limit, cutoff=0.6)
    return [m for m in matches if m != name]


def _fuzzy_local(name: str, cutoff: float = FUZZY_STRONG) -> Optional[dict]:
    """Map a misspelling onto a known site/app and learn the alias."""
    candidates = list(KNOWN_SITES) + list(KNOWN_APPS)
    close = difflib.get_close_matches(name, candidates, n=1, cutoff=cutoff)
    if not close:
        return None
    match = close[0]
    if match in KNOWN_APPS:
        target = KNOWN_APPS[match]
        kind = "uri" if target.endswith(":") else "app"
    else:
        target = KNOWN_SITES[match]
        kind = "url"
    log.info("Fuzzy match: '%s' → '%s'", name, match)
    remember(name, kind, target, f"fuzzy:{match}")
    return {"kind": kind, "target": target, "source": "fuzzy"}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _looks_like_url(value: str) -> bool:
    if value.startswith(("http://", "https://")):
        return True
    return bool(urllib.parse.urlparse("https://" + value).netloc.count(".")) and " " not in value


def _host_exists(url: str, timeout: float = 2.5) -> bool:
    """Cheap DNS check — avoids opening a browser on a dead guess."""
    try:
        host = urllib.parse.urlparse(url if "://" in url else "https://" + url).hostname or ""
        if not host:
            return False
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(host, 443)
        return True
    except Exception:
        return False
    finally:
        socket.setdefaulttimeout(None)


def find_start_menu_shortcut(name: str) -> Optional[str]:
    """Locate an installed-app shortcut whose filename matches *name*."""
    wanted = (name or "").strip().lower()
    if not wanted:
        return None
    exact: Optional[str] = None
    partial: Optional[str] = None
    for folder in _START_MENU_DIRS:
        try:
            if not folder.exists():
                continue
            for path in folder.rglob("*.lnk"):
                stem = path.stem.lower().strip()
                if stem == wanted:
                    exact = str(path)
                    break
                if partial is None and (wanted in stem or stem in wanted):
                    partial = str(path)
        except Exception as exc:
            log.debug("Start Menu scan failed in %s: %s", folder, exc)
        if exact:
            return exact
    return partial


def _web_guess(name: str) -> Optional[str]:
    """Look the name up on the web and keep the most official-looking result."""
    from core.web_tools import search_web

    name = name.strip()
    candidates: list[str] = []
    direct = f"https://{name.replace(' ', '')}.com"
    if _host_exists(direct):
        candidates.append(direct)
    try:
        for result in search_web(f"official website {name}", max_results=5):
            url = (result.get("url") or "").strip()
            if url and "wikipedia.org" not in url and url not in candidates:
                candidates.append(url)
    except Exception as exc:
        log.warning("Web lookup for '%s' failed: %s", name, exc)
    return candidates[0] if candidates else None


# ── Public API ──────────────────────────────────────────────────────────────

def resolve(target: str, *, allow_web_lookup: bool = True) -> Optional[dict]:
    """Resolve *target* to ``{"kind", "target", "source"}`` or ``None``.

    ``kind`` is ``"url"``, ``"app"``, ``"path"`` or ``"uri"`` (a ``ms-settings:``
    style protocol). When nothing is found and *allow_web_lookup* is true, the
    web is consulted and the answer is remembered.
    """
    raw = (target or "").strip().strip('"').strip("'")
    if not raw:
        return None
    key = raw.lower().rstrip("/")

    # 1. A URL, or something that is obviously a domain.
    if raw.startswith(("http://", "https://")):
        return {"kind": "url", "target": raw, "source": "explicit"}
    if _looks_like_url(raw):
        url = "https://" + raw
        if _host_exists(url):
            remember(key, "url", url, "domain")
            return {"kind": "url", "target": url, "source": "domain"}

    # 2. A file or folder that exists on disk.
    candidate = Path(raw)
    try:
        if candidate.exists():
            return {"kind": "path", "target": str(candidate.resolve()), "source": "disk"}
    except OSError:
        pass

    # 3. Built-in app aliases.
    if key in KNOWN_APPS:
        exe = KNOWN_APPS[key]
        kind = "uri" if exe.endswith(":") else "app"
        return {"kind": kind, "target": exe, "source": "alias"}

    # 4. Well-known sites.
    if key in KNOWN_SITES:
        return {"kind": "url", "target": KNOWN_SITES[key], "source": "site"}

    # 5. Something learned earlier (a resolved target, not a failure record).
    learned = load_learned().get(key)
    if learned and learned.get("kind") != UNRESOLVED and learned.get("target"):
        remember(key, learned["kind"], learned["target"], learned.get("source", "learned"))
        return dict(learned)

    # 6. An installed application from the Start Menu. This runs again for a
    #    previously failed name, so installing the app is enough to fix it.
    shortcut = find_start_menu_shortcut(key)
    if shortcut:
        remember(key, "path", shortcut, "start-menu")
        return {"kind": "path", "target": shortcut, "source": "start-menu"}

    # 7. An obvious misspelling of something local — learn it, no network.
    fuzzy = _fuzzy_local(key, FUZZY_STRONG)
    if fuzzy:
        return fuzzy

    # 8. Ask the web, unless this name has already failed enough to have earned
    #    a rest (it is still retried locally on every request, so installing the
    #    app or fixing the spelling makes it work again immediately).
    if not allow_web_lookup:
        log.info("Could not resolve open target (web lookup disabled): %s", raw)
        return None

    if is_locally_blacklisted(key):
        entry = load_learned().get(key) or {}
        log.info("Skipping web lookup for '%s' — failed %d time(s) already",
                 key, int(entry.get("attempts", 0)))
        return None

    url = _web_guess(raw)
    if url:
        remember(key, "url", url, "web")
        return {"kind": "url", "target": url, "source": "web"}

    # The web knew nothing either. A looser local match beats failing.
    fuzzy = _fuzzy_local(key, FUZZY_CUTOFF)
    if fuzzy:
        return fuzzy

    # Every source was tried and none answered: remember that, so the next
    # request for this name is instant instead of another network round trip.
    record_failure(key)
    log.info("Could not resolve open target: %s", raw)
    return None


def launch(resolved: dict) -> str:
    """Open a resolved target and return the human-readable result line."""
    kind = resolved.get("kind", "")
    target = resolved.get("target", "")
    if not target:
        raise ValueError("Nothing to open")

    if kind == "url":
        import webbrowser
        if not webbrowser.open(target):
            # Last resort on Windows: hand it to the shell.
            os.startfile(target)  # type: ignore[attr-defined]
        return f"Opened {target} in the browser."

    if kind == "uri":
        os.startfile(target)  # type: ignore[attr-defined]
        return f"Opened {target}."

    # An app, a .lnk or a file: the shell knows how to start all three.
    os.startfile(target)  # type: ignore[attr-defined]
    return f"Opened {Path(target).name if os.path.exists(target) else target}."
