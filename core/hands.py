import os
import subprocess
from PyQt6.QtCore import QObject, pyqtSignal
import pyautogui
from core.logger import get_logger

log = get_logger('hands')


class ActionExecutor(QObject):
    confirmed = pyqtSignal(str)
    cancelled = pyqtSignal(str)

    # Actions that never need the user's approval (no side effects).
    _READ_ONLY_ACTIONS = frozenset({
        'search', 'scrape', 'read_file', 'list_dir',
        'clipboard_read', 'describe_screen',
    })
    # Actions executed through the sandbox (rate limited + audited).
    _SANDBOX_ACTIONS = frozenset({
        'read_file', 'write_file', 'append_file', 'list_dir',
        'make_dir', 'delete_file',
        'clipboard_read', 'clipboard_write', 'describe_screen',
    })

    def __init__(self, overlay):
        super().__init__()
        self.overlay = overlay
        self.pending = None

        # Sandbox: rate-limited, audited, confined to a single workspace folder
        from core.sandbox import ActionSandbox, resolve_workspace
        cfg = getattr(overlay, '_config', None)
        perms = getattr(cfg, 'permissions', None) if cfg is not None else None
        self._sandbox = ActionSandbox(
            max_per_minute=int(getattr(perms, 'max_actions_per_minute', 10) or 10),
            undo_stack_size=int(getattr(perms, 'undo_stack_size', 5) or 5),
            workspace=resolve_workspace(cfg),
            allow_outside_writes=bool(getattr(perms, 'allow_outside_writes', True)),
        )
        # Multi-step action chain
        self._action_chain = []

    # ── Permission policy ──

    @property
    def workspace(self):
        """The single folder Atom may write to freely."""
        return self._sandbox.workspace

    def requires_permission(self, action) -> bool:
        """True when the action must be approved by the user before running.

        Reads are free. Writes inside the sandbox workspace are free. Everything
        else (writes elsewhere, shell, typing, clicking, opening apps) asks first.
        """
        act = (action or {}).get('action', '')
        if act in self._READ_ONLY_ACTIONS:
            return False
        if act in self._SANDBOX_ACTIONS:
            return self._sandbox.requires_permission(action)
        if act == 'chain':
            return any(self.requires_permission(step) for step in (action.get('steps') or []))
        return True

    def blocked_reason(self, action) -> str:
        """Why the action can never run even with approval ('' when it can).

        Refusing up-front beats asking for a permission that could not be
        honoured — e.g. a write outside the workspace while
        ``permissions.allow_outside_writes`` is false.
        """
        act = (action or {}).get('action', '')
        if act == 'chain':
            for step in action.get('steps') or []:
                reason = self.blocked_reason(step)
                if reason:
                    return reason
            return ''
        if act in self._SANDBOX_ACTIONS:
            return self._sandbox.blocked_reason(action) or ''
        return ''

    def request_action(self, action):
        """Queue an action for the user to approve; nothing runs until confirm()."""
        self.pending = action
        self.overlay.show_action_preview(action)

    def confirm(self):
        """Run the pending action. Calling this IS the user's explicit permission,
        which is what lets a write escape the sandbox workspace (subject to
        ``permissions.allow_outside_writes``)."""
        if not self.pending:
            self.cancelled.emit("Nothing to do.")
            return
        try:
            result = self.execute(self.pending, permission_granted=True)
            self.overlay.clear_action_preview()
            self.confirmed.emit(result)
        except Exception as exc:
            msg = f"Action failed: {exc}"
            log.warning(msg)
            self.cancelled.emit(msg)
        finally:
            self.pending = None

    def cancel(self):
        self.pending = None
        self.overlay.clear_action_preview()
        self.cancelled.emit("Action canceled.")

    def execute(self, action, permission_granted: bool = False):
        act = action.get('action', '')
        if act == 'shell':
            return self._execute_shell(action)
        if act == 'open':
            return self._execute_open(action)
        if act in ('click', 'click_at'):
            return self._execute_click(action)
        if act in ('type', 'type_text'):
            return self._execute_type(action)
        if act == 'search':
            return self._execute_search(action)
        if act == 'scrape':
            return self._execute_scrape(action)
        # Sandboxed file / clipboard actions (rate limited + audited)
        if act in self._SANDBOX_ACTIONS:
            return self._sandbox.execute(
                action, confirmed=True, permission_granted=permission_granted,
            )
        if act == 'chain':
            return self._execute_chain(action, permission_granted)
        raise ValueError(f"Unknown action: {act}")

    # Phase 65: Multi-step action chaining
    def queue_chain(self, actions: list):
        """Queue a sequence of actions to execute one after another."""
        self._action_chain = actions
        self._execute_next_in_chain()

    def _execute_next_in_chain(self):
        """Execute the next action in the chain, if any."""
        if not self._action_chain:
            self.confirmed.emit("Chain complete.")
            return
        next_action = self._action_chain.pop(0)
        self.request_action(next_action)

    def _execute_chain(self, action, permission_granted: bool = False):
        """Execute a chain of actions sequentially."""
        steps = action.get('steps', [])
        results = []
        for step in steps:
            try:
                result = self.execute(step, permission_granted=permission_granted)
                results.append(result)
            except Exception as e:
                results.append(f"Step failed: {e}")
                break
        return '\n'.join(results)
    def _execute_shell(self, action):
        command = action.get('command', '')
        description = action.get('description', command)
        if not command:
            raise ValueError("No command specified")
        if command.lower().startswith('start '):
            target = command[6:].strip()
            log.info("Shell: opening %s", target)
            os.system(f'start "" {target}')
            return f"Opened: {description or target}"
        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
            output = (result.stdout or result.stderr or '').strip()[:200]
            if output:
                return f"Ran: {description}\n{output}"
            return f"Ran: {description}"
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"Command timed out: {description}")
        except Exception as e:
            raise RuntimeError(f"Command failed: {e}")

    def _execute_open(self, action):
        target = action.get('target', action.get('description', ''))
        exe = action.get('exe', target)
        if not exe:
            raise ValueError("No target specified to open")
        log.info("Opening: %s", exe)
        # Resolve loose names: "google" is a URL, "notepad" is an app, "steam"
        # is a Start Menu shortcut, and anything unknown is looked up once and
        # remembered. Passing the raw word to `start` (the old behaviour) just
        # produced a Windows error dialog.
        from core.app_resolver import launch, record_failure, resolve, suggest
        resolved = resolve(exe)
        if not resolved:
            # The failure was already recorded by resolve(); offer the closest
            # names so a typo costs one correction instead of a dead end.
            hints = suggest(exe)
            message = f"I don't know how to open '{exe}'."
            if hints:
                message += " Did you mean " + ", ".join(f"'{h}'" for h in hints) + "?"
            message += " I'll remember it once it resolves."
            raise ValueError(message)
        log.info("Resolved open target '%s' via %s: %s",
                 exe, resolved.get('source'), resolved.get('target'))
        try:
            return launch(resolved)
        except Exception:
            # The saved target is wrong or vanished (app uninstalled, stale
            # shortcut): drop it and count the failure, so the next attempt
            # resolves again instead of failing identically forever.
            log.warning("Launch failed for '%s' — forgetting the mapping", exe)
            record_failure(exe)
            raise

    def _execute_click(self, action):
        x = action.get('x')
        y = action.get('y')
        if x is not None and y is not None:
            if not isinstance(x, int) or not isinstance(y, int):
                raise ValueError("x and y must be integers")
            pyautogui.click(x, y)
            return f"Clicked at ({x}, {y})."
        raise ValueError("Click action needs coordinates (x, y)")

    def _execute_type(self, action):
        text = action.get('text', '')
        if not text:
            raise ValueError("No text to type")
        safe_text = ''.join(c for c in text if c.isprintable() or c in '\n\t\r')
        if not safe_text:
            raise ValueError("Text contains no printable characters")
        pyautogui.write(safe_text)
        preview = safe_text[:40] + ('...' if len(safe_text) > 40 else '')
        return f"Typed: {preview}"

    def _execute_search(self, action):
        query = action.get('query', '')
        if not query:
            raise ValueError("No search query specified")
        from core.web_tools import search_web, format_search_results
        results = search_web(query)
        formatted = format_search_results(results)
        return f"Search results for: {query[:60]}\n\n{formatted}"

    def _execute_scrape(self, action):
        url = action.get('url', '')
        if not url:
            raise ValueError("No URL specified")
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        from core.web_tools import scrape_page, format_scrape_result
        data = scrape_page(url)
        return format_scrape_result(data)
