"""
Action Sandbox — Phase 163

Whitelist-based action execution confined to a single writable workspace.

Policy
------
* **Workspace** (`sandbox/` at the project root by default, configurable via
  ``permissions.sandbox_dir``) is the ONLY place Atom may write freely. Reads
  and writes inside it never need approval.
* **Everything else on disk is read-only.** A write outside the workspace is
  refused outright unless (a) ``permissions.allow_outside_writes`` is true (the
  default) **and** (b) the user explicitly granted permission for that single
  action — the overlay asks before anything is executed. With (a) false the write
  is refused without asking, and :meth:`ActionSandbox.blocked_reason` explains why.
* Actions with side effects outside the filesystem (typing, clicking, opening
  apps, running shell commands) always require explicit permission.
* Rate limit: max N actions per minute (default 10).
* Undo stack for typing plus a separate undo stack for file writes.
* JSONL audit log to ``logs/actions.jsonl`` recording the path and allow/deny
  decision for every filesystem action.
"""
import json
import os
import shutil
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Optional, Union

from core.logger import get_logger

log = get_logger('sandbox')

ROOT = Path(__file__).parent.parent
LOGS_DIR = ROOT / 'logs'
AUDIT_LOG_PATH = LOGS_DIR / 'actions.jsonl'

# ── Default workspace (a folder next to this project) ──
DEFAULT_WORKSPACE_NAME = 'sandbox'

# ── Whitelisted actions ──
ALLOWED_ACTIONS = {
    'type', 'click_at', 'click_text', 'open_app',
    'web_search', 'web_scrape',
    'read_file', 'write_file', 'append_file', 'list_dir',
    'make_dir', 'delete_file',
    'clipboard_read', 'clipboard_write',
    'describe_screen', 'shell',
}

# Actions that never change anything on disk or on screen → no approval needed.
READ_ONLY_ACTIONS = {
    'read_file', 'list_dir', 'describe_screen',
    'clipboard_read', 'web_search', 'web_scrape', 'search', 'scrape',
}

# Actions that only touch files. Allowed freely inside the workspace; anything
# outside it needs the user's explicit permission.
WORKSPACE_WRITE_ACTIONS = {
    'write_file', 'append_file', 'make_dir', 'delete_file',
}

# NOTE: the legacy ``write_allowed_paths`` roots (Documents/Desktop/Downloads)
# no longer grant free writes — see the Phase 163 policy above. They are still
# accepted in old config files, but nothing here expands them: doing that at
# import time called Path.home(), which raises when HOME is unset (service
# accounts, CI, sandboxed shells) and took the whole module down with it.


class PathPolicyError(PermissionError):
    """Raised when a path is outside the workspace and no permission was granted."""


def resolve_workspace(config=None) -> Path:
    """Resolve the sandbox workspace folder from config (default ``<root>/sandbox``).

    Relative values are resolved against the project root so the setting stays
    portable across machines.
    """
    raw = None
    if config is not None:
        perms = config.get('permissions') if isinstance(config, dict) else getattr(config, 'permissions', None)
        if perms is not None:
            raw = perms.get('sandbox_dir') if isinstance(perms, dict) else getattr(perms, 'sandbox_dir', None)
    if not raw:
        raw = DEFAULT_WORKSPACE_NAME
    path = Path(os.path.expandvars(os.path.expanduser(str(raw))))
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


class ActionSandbox:
    """Sandboxed action executor with workspace confinement and audit logging."""

    def __init__(
        self,
        max_per_minute: int = 10,
        undo_stack_size: int = 5,
        workspace: Union[str, Path, None] = None,
        allow_outside_writes: bool = False,
    ):
        self._max_per_minute = max(1, int(max_per_minute))
        self._undo_stack: deque = deque(maxlen=max(1, int(undo_stack_size)))
        self._file_undo_stack: deque = deque(maxlen=max(1, int(undo_stack_size)))
        self._timestamps: deque = deque()

        self.workspace = self._normalise_workspace(workspace)
        self.allow_outside_writes = bool(allow_outside_writes)

        # Ensure logs + workspace exist
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            self.workspace.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            log.warning("Could not create sandbox workspace %s: %s", self.workspace, exc)

    @staticmethod
    def _normalise_workspace(workspace: Union[str, Path, None]) -> Path:
        if workspace is None:
            return (ROOT / DEFAULT_WORKSPACE_NAME).resolve()
        path = Path(os.path.expandvars(os.path.expanduser(str(workspace))))
        if not path.is_absolute():
            path = ROOT / path
        return path.resolve()

    # ── Workspace policy ──

    def resolve_path(self, raw: Optional[str], base: Optional[Path] = None) -> Path:
        """Resolve a user/LLM supplied path.

        Relative paths are resolved against the workspace (not the process cwd),
        so ``[write: notes.txt | hi]`` lands in the sandbox rather than somewhere
        unpredictable.
        """
        text = str(raw or '').strip().strip('"\'')
        if text in ('', '.', './'):
            return self.workspace
        path = Path(os.path.expandvars(os.path.expanduser(text)))
        if not path.is_absolute():
            path = (base or self.workspace) / path
        try:
            return path.resolve()
        except OSError:
            return path.absolute()

    def is_inside_workspace(self, path: Path) -> bool:
        """True when *path* is the workspace itself or somewhere inside it."""
        try:
            Path(path).resolve().relative_to(self.workspace)
            return True
        except (ValueError, OSError):
            return False

    def classify(self, path: Union[str, Path]) -> str:
        """Return ``"workspace"`` or ``"outside"`` for a path."""
        return "workspace" if self.is_inside_workspace(self.resolve_path(str(path))) else "outside"

    @staticmethod
    def _target_path(action: dict) -> Optional[str]:
        for key in ('path', 'file', 'target'):
            value = action.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

    def requires_permission(self, action: dict) -> bool:
        """True when this action must be approved by the user before running."""
        act = (action or {}).get('action', '')
        if not act:
            return True
        if act in READ_ONLY_ACTIONS:
            return False
        if act == 'chain':
            return any(self.requires_permission(step) for step in (action.get('steps') or []))
        if act in WORKSPACE_WRITE_ACTIONS:
            raw = self._target_path(action)
            if raw is None:
                return True  # cannot tell where it lands → ask
            return self.classify(raw) != 'workspace'
        # shell / type / click / open_app / clipboard_write / anything unknown
        return True

    def check_write(self, raw: Optional[str], permission_granted: bool = False) -> Path:
        """Validate a write target.

        Returns the resolved path when writing is allowed, otherwise raises
        :class:`PathPolicyError` explaining what was refused and why.
        """
        if not raw or not str(raw).strip():
            raise ValueError("No path specified")
        target = self.resolve_path(raw)
        if self.is_inside_workspace(target):
            return target
        if not permission_granted:
            raise PathPolicyError(
                f"'{target}' is outside my workspace ({self.workspace}), which is "
                f"read-only, so writing there needs Koko's explicit permission first."
            )
        if not self.allow_outside_writes:
            raise PathPolicyError(
                f"Writes outside my workspace are switched off "
                f"(permissions.allow_outside_writes = false), so I cannot touch "
                f"'{target}' even with permission. Turn that setting on and ask again."
            )
        return target

    def blocked_reason(self, action: dict) -> Optional[str]:
        """Reason *action* can never run even with approval — or ``None``.

        A write outside the workspace is refused outright when
        ``allow_outside_writes`` is false, so the caller can skip asking for a
        permission that could not be honoured anyway.
        """
        act = (action or {}).get('action', '')
        if act == 'chain':
            for step in action.get('steps') or []:
                reason = self.blocked_reason(step)
                if reason:
                    return reason
            return None
        if act in WORKSPACE_WRITE_ACTIONS and not self.allow_outside_writes:
            raw = self._target_path(action)
            if raw is None:
                return None  # unknown target → the normal approval flow decides
            target = self.resolve_path(raw)
            if not self.is_inside_workspace(target):
                return (
                    f"'{target}' is outside my workspace ({self.workspace}) and outside "
                    f"writes are switched off (permissions.allow_outside_writes = false). "
                    f"Turn that setting on if you want me to ask before writing there."
                )
        return None

    # ── Rate limiting ──

    def _check_rate_limit(self) -> bool:
        """Check if we've exceeded the rate limit. Returns True if allowed."""
        now = time.time()
        while self._timestamps and now - self._timestamps[0] > 60:
            self._timestamps.popleft()
        if len(self._timestamps) >= self._max_per_minute:
            return False
        self._timestamps.append(now)
        return True

    # ── Audit logging ──

    def _log(self, action: dict, result: str, decision: str = 'allow', path: str = ''):
        """Write an audit log entry."""
        try:
            entry = {
                'timestamp': time.time(),
                'action': action.get('action', 'unknown'),
                'description': action.get('description', ''),
                'decision': decision,
                'path': path,
                'result': str(result)[:200],
            }
            with open(str(AUDIT_LOG_PATH), 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception as exc:
            log.warning("Failed to write audit log: %s", exc)

    # ── Main execution ──

    def execute(self, action: dict, confirmed: bool = False, permission_granted: bool = False) -> str:
        """Execute a sandboxed action. Returns result string.

        Args:
            action: The action dict, e.g. ``{"action": "write_file", "path": "a.txt"}``.
            confirmed: Must be True — every action requires the caller to have
                gone through the confirmation path.
            permission_granted: True when the user explicitly approved this
                specific action (lets writes escape the workspace).

        Raises:
            ValueError: If the action is not allowed, not confirmed, or rate limited.
            PathPolicyError: If a write target is outside the workspace without permission.
        """
        act_type = action.get('action', '')

        if act_type not in ALLOWED_ACTIONS:
            self._log(action, f"not allowed: {act_type}", decision='deny')
            raise ValueError(f"Action '{act_type}' is not in the allowed list.")

        if not confirmed:
            self._log(action, "missing confirmation", decision='deny')
            raise ValueError("Action requires explicit confirmation.")

        if not self._check_rate_limit():
            self._log(action, "rate limited", decision='deny')
            raise ValueError(f"Rate limit exceeded: max {self._max_per_minute} actions per minute.")

        target = self.resolve_path(self._target_path(action)) if self._target_path(action) else None
        acting_path = self.classify(target) if target else ''
        if acting_path == 'outside' and act_type in WORKSPACE_WRITE_ACTIONS and not permission_granted:
            self._log(action, "outside workspace without permission", decision='deny',
                      path=str(target))
            raise PathPolicyError(
                f"Blocked: '{target}' is outside the sandbox workspace "
                f"'{self.workspace}'. A write needs Koko's explicit permission first."
            )

        try:
            result = self._dispatch(action, permission_granted=permission_granted)
        except Exception as exc:
            self._log(action, f"error: {exc}", decision='deny', path=str(target or ''))
            raise

        if act_type == 'type':
            self._undo_stack.append(action)

        self._log(action, result, decision='allow', path=str(target or ''))
        return result

    # ── Dispatch ──

    def _dispatch(self, action: dict, permission_granted: bool = False) -> str:
        """Route action to the appropriate executor."""
        act_type = action.get('action', '')

        if act_type == 'type':
            return self._exec_type(action)
        if act_type == 'click_at':
            return self._exec_click_at(action)
        if act_type == 'click_text':
            return f"Click on text: {action.get('text', '')}"
        if act_type == 'open_app':
            return self._exec_open_app(action)
        if act_type == 'web_search':
            return f"Search queued: {str(action.get('query', ''))[:80]}"
        if act_type == 'web_scrape':
            return f"Scrape queued: {str(action.get('url', ''))[:80]}"
        if act_type == 'read_file':
            return self._exec_read_file(action)
        if act_type == 'list_dir':
            return self._exec_list_dir(action)
        if act_type == 'write_file':
            return self._exec_write_file(action, permission_granted)
        if act_type == 'append_file':
            return self._exec_append_file(action, permission_granted)
        if act_type == 'make_dir':
            return self._exec_make_dir(action, permission_granted)
        if act_type == 'delete_file':
            return self._exec_delete_file(action, permission_granted)
        if act_type == 'clipboard_read':
            return self._exec_clipboard_read()
        if act_type == 'clipboard_write':
            return self._exec_clipboard_write(action)
        if act_type == 'describe_screen':
            return "describe_screen: use OCR result"
        if act_type == 'shell':
            return self._exec_shell(action)
        return f"Unknown action type: {act_type}"

    # ── Miscellaneous executors ──

    def _exec_type(self, action: dict) -> str:
        try:
            import pyautogui
            text = action.get('text', '')
            pyautogui.write(text, interval=0.05)
            return f"Typed: {text[:60]}"
        except ImportError:
            return "pyautogui not installed"

    def _exec_click_at(self, action: dict) -> str:
        try:
            import pyautogui
            x = action.get('x', 0)
            y = action.get('y', 0)
            pyautogui.click(x, y)
            return f"Clicked at ({x}, {y})"
        except ImportError:
            return "pyautogui not installed"

    def _exec_open_app(self, action: dict) -> str:
        app = action.get('app', action.get('command', ''))
        if not app:
            raise ValueError("No app specified")
        subprocess.Popen(f'start {app}', shell=True)
        return f"Opened: {app}"

    def _exec_clipboard_read(self) -> str:
        try:
            import pyperclip
            return pyperclip.paste()[:500]
        except ImportError:
            return "pyperclip not installed"

    def _exec_clipboard_write(self, action: dict) -> str:
        try:
            import pyperclip
            text = action.get('text', '')
            pyperclip.copy(text)
            return f"Copied {len(text)} chars to clipboard"
        except ImportError:
            return "pyperclip not installed"

    def _exec_shell(self, action: dict) -> str:
        command = action.get('command', '')
        if not command:
            raise ValueError("No command specified")
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=10)
        output = (result.stdout or result.stderr or '')[:1000]
        return output or f"Exit code: {result.returncode}"

    # ── File executors ──

    _MAX_READ_BYTES = 256 * 1024

    def _exec_read_file(self, action: dict) -> str:
        """Read a file. Reads are allowed anywhere on disk (read-only policy)."""
        raw = self._target_path(action) or ''
        path = self.resolve_path(raw)
        if not path.exists():
            return f"File not found: {path}"
        if path.is_dir():
            return f"'{path}' is a directory — use list_dir instead."
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except (OSError, ValueError) as exc:
            return f"Could not read {path}: {exc}"
        truncated = ''
        if len(text) > self._MAX_READ_BYTES:
            text = text[: self._MAX_READ_BYTES]
            truncated = f"\n\n[truncated at {self._MAX_READ_BYTES} chars of {size} bytes]"
        return f"{path}\n{text}{truncated}"

    def _exec_list_dir(self, action: dict) -> str:
        raw = self._target_path(action) or '.'
        path = self.resolve_path(raw)
        if not path.exists():
            return f"Not found: {path}"
        if path.is_file():
            return f"{path} ({path.stat().st_size} bytes)"
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError as exc:
            return f"Could not list {path}: {exc}"
        lines = [f"{path}{'  (sandbox workspace)' if self.is_inside_workspace(path) else ''}"]
        for entry in entries[:200]:
            if entry.is_dir():
                lines.append(f"  [dir]  {entry.name}/")
            else:
                try:
                    kb = entry.stat().st_size / 1024
                except OSError:
                    kb = 0.0
                lines.append(f"  [file] {entry.name}  ({kb:.1f} KB)")
        if len(entries) > 200:
            lines.append(f"  … {len(entries) - 200} more")
        if len(lines) == 1:
            lines.append("  (empty)")
        return "\n".join(lines)

    def _snapshot_for_undo(self, path: Path) -> None:
        """Remember the previous contents so a bad write can be reverted."""
        try:
            previous = path.read_text(encoding='utf-8') if path.exists() and path.is_file() else None
            self._file_undo_stack.append((str(path), previous))
        except (OSError, ValueError) as exc:
            log.debug("Could not snapshot %s for undo: %s", path, exc)

    def _exec_write_file(self, action: dict, permission_granted: bool = False) -> str:
        resolved = self.check_write(self._target_path(action), permission_granted)
        if resolved.is_dir():
            raise IsADirectoryError(f"'{resolved}' is a directory")
        content = str(action.get('text', action.get('content', '')))
        self._snapshot_for_undo(resolved)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding='utf-8')
        scope = "sandbox" if self.is_inside_workspace(resolved) else "outside sandbox (permitted)"
        return f"Wrote {len(content)} chars to {resolved} [{scope}]"

    def _exec_append_file(self, action: dict, permission_granted: bool = False) -> str:
        resolved = self.check_write(self._target_path(action), permission_granted)
        if resolved.is_dir():
            raise IsADirectoryError(f"'{resolved}' is a directory")
        content = str(action.get('text', action.get('content', '')))
        self._snapshot_for_undo(resolved)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with open(resolved, 'a', encoding='utf-8') as f:
            f.write(content)
        return f"Appended {len(content)} chars to {resolved}"

    def _exec_make_dir(self, action: dict, permission_granted: bool = False) -> str:
        resolved = self.check_write(self._target_path(action), permission_granted)
        resolved.mkdir(parents=True, exist_ok=True)
        return f"Created directory {resolved}"

    def _exec_delete_file(self, action: dict, permission_granted: bool = False) -> str:
        resolved = self.check_write(self._target_path(action), permission_granted)
        if resolved == self.workspace:
            raise PathPolicyError("Refusing to delete the sandbox workspace itself.")
        if not resolved.exists():
            return f"Nothing to delete: {resolved}"
        if resolved.is_dir():
            if not self.is_inside_workspace(resolved):
                # Never recursively delete a folder outside the workspace.
                raise PathPolicyError(
                    f"Refusing to recursively delete a directory outside the sandbox: {resolved}"
                )
            shutil.rmtree(resolved)
        else:
            resolved.unlink()
        return f"Deleted {resolved}"

    # ── Undo ──

    def undo_last_type(self) -> str:
        """Undo the last type action (simulated Ctrl+Z)."""
        if not self._undo_stack:
            return "Nothing to undo."
        try:
            import pyautogui
            pyautogui.hotkey('ctrl', 'z')
            self._undo_stack.pop()
            return "Undone last action (Ctrl+Z)"
        except ImportError:
            return "pyautogui not installed for undo"

    def undo_last_write(self) -> str:
        """Restore the file contents from before the most recent write."""
        if not self._file_undo_stack:
            return "No file write to undo."
        path_str, previous = self._file_undo_stack.pop()
        path = Path(path_str)
        try:
            if previous is None:
                if path.exists():
                    path.unlink()
                return f"Removed {path} (it did not exist before the write)"
            path.write_text(previous, encoding='utf-8')
            return f"Restored previous contents of {path}"
        except OSError as exc:
            return f"Undo failed for {path}: {exc}"

    # ── Cleanup ──

    def close(self):
        """Close audit log file if open."""
        pass
