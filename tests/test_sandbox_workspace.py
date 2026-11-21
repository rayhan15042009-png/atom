"""
Tests for the sandbox workspace policy + permission gate (Phase 163).

Policy under test:
- one writable workspace folder (``sandbox/`` by default)
- everything else on disk is read-only
- writes outside the workspace only happen with an explicit per-action grant
  (and only when ``permissions.allow_outside_writes`` is true)
- shell / typing / clicking / opening always need permission
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

_APP = QApplication.instance() or QApplication([])

from core.config_manager import AppConfig  # noqa: E402
from core.sandbox import (  # noqa: E402
    AUDIT_LOG_PATH,
    ActionSandbox,
    PathPolicyError,
    READ_ONLY_ACTIONS,
    WORKSPACE_WRITE_ACTIONS,
    resolve_workspace,
)
from core.brain import BrainThread, get_system_prompt  # noqa: E402


def _outside_path() -> str:
    """A path that is reliably outside any temp workspace."""
    return str(Path(tempfile.gettempdir()).parent / "atom_outside_probe.txt")


class SandboxTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name) / "sandbox"
        self.sandbox = ActionSandbox(workspace=self.workspace, max_per_minute=100)

    def tearDown(self) -> None:
        self._tmp.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# Workspace resolution
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkspaceResolution(unittest.TestCase):
    def test_default_is_project_sandbox(self) -> None:
        ws = resolve_workspace(None)
        self.assertEqual(ws.name, "sandbox")

    def test_relative_value_resolves_against_project_root(self) -> None:
        cfg = AppConfig()
        cfg.permissions.sandbox_dir = "workbench"
        self.assertEqual(resolve_workspace(cfg).name, "workbench")

    def test_absolute_value_is_used_as_is(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = AppConfig()
            cfg.permissions.sandbox_dir = tmp
            self.assertEqual(resolve_workspace(cfg), Path(tmp).resolve())

    def test_empty_value_falls_back_to_default(self) -> None:
        cfg = AppConfig()
        cfg.permissions.sandbox_dir = ""
        self.assertEqual(resolve_workspace(cfg).name, "sandbox")

    def test_default_lets_permission_unlock_outside_writes(self) -> None:
        """Default policy: read-only everywhere, but a granted write goes through."""
        perms = AppConfig().permissions
        self.assertTrue(perms.allow_outside_writes)
        self.assertEqual(perms.sandbox_dir, "sandbox")

    def test_sandbox_api_default_is_conservative(self) -> None:
        """The class default stays locked-down; callers opt in explicitly."""
        sandbox = ActionSandbox(workspace=Path(tempfile.gettempdir()) / "atom_api_probe")
        self.assertFalse(sandbox.allow_outside_writes)


class TestPolicyWithoutHome(unittest.TestCase):
    """HOME is not always set (service accounts, CI, sandboxed shells).

    The policy layer used to call Path.home() at import time to build a legacy
    constant, which raised with HOME unset and took the whole module — and with
    it the brain thread — down.
    """

    def test_resolve_workspace_works_without_home(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_workspace(None).name, "sandbox")

    def test_executing_actions_works_without_home(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with tempfile.TemporaryDirectory() as tmp:
                workspace = Path(tmp) / "ws"
                sandbox = ActionSandbox(workspace=workspace)
                out = sandbox.execute({"action": "write_file", "path": "n.txt", "text": "hi"},
                                      confirmed=True)
                self.assertIn("Wrote", out)
                self.assertTrue((workspace / "n.txt").exists())


# ═══════════════════════════════════════════════════════════════════════════════
# Path classification + write checks
# ═══════════════════════════════════════════════════════════════════════════════

class TestPathPolicy(SandboxTestBase):
    def test_relative_paths_land_in_the_workspace(self) -> None:
        self.assertEqual(self.sandbox.resolve_path("notes.txt"),
                         (self.workspace / "notes.txt").resolve())
        self.assertEqual(self.sandbox.resolve_path("."), self.workspace.resolve())

    def test_classify_inside_and_outside(self) -> None:
        self.assertEqual(self.sandbox.classify("a/b.txt"), "workspace")
        self.assertEqual(self.sandbox.classify(_outside_path()), "outside")

    def test_workspace_itself_counts_as_inside(self) -> None:
        self.assertEqual(self.sandbox.classify(str(self.workspace)), "workspace")

    def test_parent_directory_is_outside(self) -> None:
        self.assertEqual(self.sandbox.classify(str(self.workspace.parent)), "outside")

    def test_check_write_allows_workspace(self) -> None:
        resolved = self.sandbox.check_write("sub/file.txt")
        self.assertTrue(self.sandbox.is_inside_workspace(resolved))

    def test_check_write_blocks_outside_without_permission(self) -> None:
        with self.assertRaises(PathPolicyError):
            self.sandbox.check_write(_outside_path())

    def test_check_write_blocks_outside_when_disabled_even_with_permission(self) -> None:
        self.assertFalse(self.sandbox.allow_outside_writes)
        with self.assertRaises(PathPolicyError):
            self.sandbox.check_write(_outside_path(), permission_granted=True)

    def test_check_write_allows_outside_when_enabled_and_granted(self) -> None:
        sandbox = ActionSandbox(workspace=self.workspace, allow_outside_writes=True)
        resolved = sandbox.check_write(_outside_path(), permission_granted=True)
        self.assertFalse(sandbox.is_inside_workspace(resolved))

    def test_check_write_requires_a_path(self) -> None:
        with self.assertRaises(ValueError):
            self.sandbox.check_write("")


# ═══════════════════════════════════════════════════════════════════════════════
# Permission matrix
# ═══════════════════════════════════════════════════════════════════════════════

class TestPermissionMatrix(SandboxTestBase):
    def test_read_only_actions_never_ask(self) -> None:
        for act in ("read_file", "list_dir", "clipboard_read", "search", "scrape"):
            with self.subTest(action=act):
                self.assertFalse(self.sandbox.requires_permission({"action": act, "path": "x"}))

    def test_reads_outside_the_workspace_never_ask(self) -> None:
        self.assertFalse(self.sandbox.requires_permission(
            {"action": "read_file", "path": _outside_path()}))

    def test_write_inside_workspace_does_not_ask(self) -> None:
        self.assertFalse(self.sandbox.requires_permission(
            {"action": "write_file", "path": "draft.md", "text": "hi"}))

    def test_write_outside_workspace_asks(self) -> None:
        for act in WORKSPACE_WRITE_ACTIONS:
            with self.subTest(action=act):
                self.assertTrue(self.sandbox.requires_permission(
                    {"action": act, "path": _outside_path()}))

    def test_write_without_a_path_asks(self) -> None:
        self.assertTrue(self.sandbox.requires_permission(
            {"action": "write_file", "text": "hi"}))

    def test_side_effect_actions_always_ask(self) -> None:
        for act in ("shell", "type", "click", "open"):
            with self.subTest(action=act):
                self.assertTrue(self.sandbox.requires_permission({"action": act}))

    def test_chain_asks_if_any_step_asks(self) -> None:
        reads_only = {"action": "chain", "steps": [
            {"action": "read_file", "path": "a.txt"},
            {"action": "list_dir", "path": "."},
        ]}
        self.assertFalse(self.sandbox.requires_permission(reads_only))
        with_shell = {"action": "chain", "steps": [
            {"action": "read_file", "path": "a.txt"},
            {"action": "shell", "command": "dir"},
        ]}
        self.assertTrue(self.sandbox.requires_permission(with_shell))

    def test_unknown_action_is_treated_as_dangerous(self) -> None:
        self.assertTrue(self.sandbox.requires_permission({"action": "mystery"}))

    def test_read_only_action_set_is_documented(self) -> None:
        self.assertIn("read_file", READ_ONLY_ACTIONS)
        self.assertNotIn("write_file", READ_ONLY_ACTIONS)


# ═══════════════════════════════════════════════════════════════════════════════
# Execution
# ═══════════════════════════════════════════════════════════════════════════════

class TestSandboxExecution(SandboxTestBase):
    def test_execute_requires_confirmation(self) -> None:
        with self.assertRaises(ValueError):
            self.sandbox.execute({"action": "read_file", "path": "x"})

    def test_execute_rejects_unknown_action(self) -> None:
        with self.assertRaises(ValueError):
            self.sandbox.execute({"action": "format_disk"}, confirmed=True)

    def test_write_then_read_round_trip(self) -> None:
        self.sandbox.execute(
            {"action": "write_file", "path": "note.txt", "text": "hello"}, confirmed=True)
        out = self.sandbox.execute({"action": "read_file", "path": "note.txt"}, confirmed=True)
        self.assertIn("hello", out)
        self.assertTrue((self.workspace / "note.txt").exists())

    def test_append_adds_to_existing_file(self) -> None:
        self.sandbox.execute({"action": "write_file", "path": "n.txt", "text": "a"}, confirmed=True)
        self.sandbox.execute({"action": "append_file", "path": "n.txt", "text": "b"}, confirmed=True)
        self.assertEqual((self.workspace / "n.txt").read_text(), "ab")

    def test_list_dir_shows_entries(self) -> None:
        self.sandbox.execute({"action": "write_file", "path": "x.txt", "text": "1"}, confirmed=True)
        out = self.sandbox.execute({"action": "list_dir", "path": "."}, confirmed=True)
        self.assertIn("x.txt", out)
        self.assertIn("sandbox workspace", out)

    def test_make_dir_and_delete(self) -> None:
        self.sandbox.execute({"action": "make_dir", "path": "sub/deep"}, confirmed=True)
        self.assertTrue((self.workspace / "sub" / "deep").is_dir())
        self.sandbox.execute({"action": "delete_file", "path": "sub"}, confirmed=True)
        self.assertFalse((self.workspace / "sub").exists())

    def test_write_outside_refused_at_execute(self) -> None:
        with self.assertRaises(PathPolicyError):
            self.sandbox.execute(
                {"action": "write_file", "path": _outside_path(), "text": "nope"},
                confirmed=True,
            )

    def test_write_outside_refused_even_with_grant_when_disabled(self) -> None:
        with self.assertRaises(PathPolicyError):
            self.sandbox.execute(
                {"action": "write_file", "path": _outside_path(), "text": "nope"},
                confirmed=True, permission_granted=True,
            )

    def test_refuses_to_delete_the_workspace_root(self) -> None:
        with self.assertRaises(PathPolicyError):
            self.sandbox.execute({"action": "delete_file", "path": "."}, confirmed=True)

    def test_refuses_recursive_delete_of_outside_directory(self) -> None:
        sandbox = ActionSandbox(workspace=self.workspace, allow_outside_writes=True)
        with tempfile.TemporaryDirectory() as outside:
            with self.assertRaises(PathPolicyError):
                sandbox.execute(
                    {"action": "delete_file", "path": outside},
                    confirmed=True, permission_granted=True,
                )

    def test_read_missing_file_reports_cleanly(self) -> None:
        out = self.sandbox.execute({"action": "read_file", "path": "ghost.txt"}, confirmed=True)
        self.assertIn("not found", out.lower())

    def test_rate_limit_blocks_after_budget(self) -> None:
        sandbox = ActionSandbox(workspace=self.workspace, max_per_minute=2)
        for _ in range(2):
            sandbox.execute({"action": "list_dir", "path": "."}, confirmed=True)
        with self.assertRaises(ValueError):
            sandbox.execute({"action": "list_dir", "path": "."}, confirmed=True)


class TestSandboxUndo(SandboxTestBase):
    def test_undo_removes_a_newly_created_file(self) -> None:
        self.sandbox.execute({"action": "write_file", "path": "new.txt", "text": "x"}, confirmed=True)
        self.assertTrue((self.workspace / "new.txt").exists())
        self.sandbox.undo_last_write()
        self.assertFalse((self.workspace / "new.txt").exists())

    def test_undo_restores_previous_contents(self) -> None:
        self.sandbox.execute({"action": "write_file", "path": "f.txt", "text": "first"}, confirmed=True)
        self.sandbox.execute({"action": "write_file", "path": "f.txt", "text": "second"}, confirmed=True)
        self.sandbox.undo_last_write()
        self.assertEqual((self.workspace / "f.txt").read_text(), "first")

    def test_undo_with_empty_stack_is_safe(self) -> None:
        self.assertIn("no file write", self.sandbox.undo_last_write().lower())


class TestSandboxAuditLog(SandboxTestBase):
    def test_denied_write_is_audited(self) -> None:
        if AUDIT_LOG_PATH.exists():
            AUDIT_LOG_PATH.unlink()
        with self.assertRaises(PathPolicyError):
            self.sandbox.execute(
                {"action": "write_file", "path": _outside_path(), "text": "x"}, confirmed=True)
        self.assertTrue(AUDIT_LOG_PATH.exists())
        last = json.loads(AUDIT_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()[-1])
        self.assertEqual(last["decision"], "deny")
        self.assertEqual(last["action"], "write_file")


# ═══════════════════════════════════════════════════════════════════════════════
# ActionExecutor permission gate
# ═══════════════════════════════════════════════════════════════════════════════

class TestBlockedReason(SandboxTestBase):
    """A locked-down policy refuses up-front instead of asking for nothing."""

    def test_none_when_outside_writes_are_enabled(self) -> None:
        sandbox = ActionSandbox(workspace=self.workspace, allow_outside_writes=True)
        self.assertIsNone(sandbox.blocked_reason(
            {"action": "write_file", "path": _outside_path(), "text": "x"}))

    def test_explains_when_locked_down(self) -> None:
        reason = self.sandbox.blocked_reason(
            {"action": "write_file", "path": _outside_path(), "text": "x"})
        self.assertIsNotNone(reason)
        self.assertIn("allow_outside_writes", reason)
        # The message reports the resolved workspace. On a CI runner the temp
        # folder is still reached through an 8.3 short name (RUNNER~1), which
        # realpath expands, so compare the resolved forms instead of the raw
        # string.
        self.assertIn(
            os.path.normcase(os.path.realpath(str(self.workspace))),
            os.path.normcase(reason),
        )

    def test_workspace_write_is_never_blocked(self) -> None:
        self.assertIsNone(self.sandbox.blocked_reason(
            {"action": "write_file", "path": "notes.txt", "text": "x"}))

    def test_shell_is_not_blocked_by_the_write_policy(self) -> None:
        self.assertIsNone(self.sandbox.blocked_reason({"action": "shell", "command": "dir"}))

    def test_chain_reports_the_blocking_step(self) -> None:
        chain = {"action": "chain", "steps": [
            {"action": "write_file", "path": "ok.txt", "text": "x"},
            {"action": "write_file", "path": _outside_path(), "text": "x"},
        ]}
        self.assertIsNotNone(self.sandbox.blocked_reason(chain))

    def test_chain_of_workspace_writes_is_clear(self) -> None:
        chain = {"action": "chain", "steps": [
            {"action": "write_file", "path": "a.txt", "text": "x"},
            {"action": "make_dir", "path": "reports"},
        ]}
        self.assertIsNone(self.sandbox.blocked_reason(chain))


class TestHandsBlockedReason(unittest.TestCase):
    """ActionExecutor surfaces the same policy to the UI layer."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        from core.hands import ActionExecutor
        cfg = AppConfig()
        cfg.permissions.sandbox_dir = self._tmp.name
        cfg.permissions.allow_outside_writes = False
        self.hands = ActionExecutor(_FakeOverlay(cfg))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_outside_write_reports_a_reason(self) -> None:
        self.assertTrue(self.hands.blocked_reason(
            {"action": "write_file", "path": _outside_path(), "text": "x"}))

    def test_workspace_write_has_no_reason(self) -> None:
        self.assertEqual(self.hands.blocked_reason(
            {"action": "write_file", "path": "draft.md", "text": "x"}), "")

    def test_shell_has_no_reason_even_when_locked_down(self) -> None:
        self.assertEqual(self.hands.blocked_reason({"action": "shell", "command": "dir"}), "")

    def test_chain_reports_the_blocking_step(self) -> None:
        self.assertTrue(self.hands.blocked_reason({"action": "chain", "steps": [
            {"action": "read_file", "path": "a.txt"},
            {"action": "write_file", "path": _outside_path(), "text": "x"},
        ]}))

class _FakeOverlay:
    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def show_action_preview(self, action: dict) -> None:
        self.previewed = action

    def clear_action_preview(self) -> None:
        self.previewed = None


class TestActionExecutorPermissionGate(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        from core.hands import ActionExecutor
        cfg = AppConfig()
        cfg.permissions.sandbox_dir = self._tmp.name
        self.overlay = _FakeOverlay(cfg)
        self.hands = ActionExecutor(self.overlay)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_workspace_follows_config(self) -> None:
        self.assertEqual(self.hands.workspace, Path(self._tmp.name).resolve())

    def test_read_does_not_need_permission(self) -> None:
        self.assertFalse(self.hands.requires_permission({"action": "read_file", "path": "a"}))

    def test_write_in_workspace_does_not_need_permission(self) -> None:
        self.assertFalse(self.hands.requires_permission(
            {"action": "write_file", "path": "draft.md", "text": "x"}))

    def test_write_outside_needs_permission(self) -> None:
        self.assertTrue(self.hands.requires_permission(
            {"action": "write_file", "path": _outside_path(), "text": "x"}))

    def test_shell_needs_permission(self) -> None:
        self.assertTrue(self.hands.requires_permission(
            {"action": "shell", "command": "echo hi"}))

    def test_write_in_workspace_executes_through_sandbox(self) -> None:
        result = self.hands.execute(
            {"action": "write_file", "path": "ok.txt", "text": "yo"})
        self.assertIn("Wrote", result)
        self.assertTrue((Path(self._tmp.name) / "ok.txt").exists())

    def test_request_action_does_not_execute(self) -> None:
        self.hands.request_action({"action": "shell", "command": "echo hi"})
        self.assertIsNotNone(self.hands.pending)
        self.assertEqual(self.overlay.previewed["action"], "shell")

    def test_confirm_runs_pending_then_clears(self) -> None:
        results = []
        self.hands.confirmed.connect(results.append)
        self.hands.request_action({"action": "list_dir", "path": "."})
        self.hands.confirm()
        self.assertIsNone(self.hands.pending)
        self.assertTrue(results)
        self.assertIsNone(self.overlay.previewed)

    def test_cancel_never_executes(self) -> None:
        cancelled = []
        self.hands.cancelled.connect(cancelled.append)
        self.hands.request_action({"action": "shell", "command": "echo hi"})
        self.hands.cancel()
        self.assertIsNone(self.hands.pending)
        self.assertTrue(cancelled)

    def test_confirm_reports_failure_for_blocked_write(self) -> None:
        failures = []
        self.hands.cancelled.connect(failures.append)
        self.hands._sandbox.allow_outside_writes = False  # hard lockdown
        self.hands.request_action({"action": "write_file", "path": _outside_path(), "text": "x"})
        self.hands.confirm()
        self.assertTrue(failures)
        self.assertIn("outside", failures[0].lower())

    def test_confirm_is_the_permission_grant(self) -> None:
        """Approving an outside write must actually let it through when enabled."""
        target = Path(self._tmp.name).parent / "atom_grant_probe.txt"
        self.hands._sandbox.allow_outside_writes = True
        results = []
        self.hands.confirmed.connect(results.append)
        self.hands.request_action({"action": "write_file", "path": str(target), "text": "granted"})
        self.assertFalse(target.exists(), "nothing may be written before approval")
        self.hands.confirm()
        try:
            self.assertTrue(results, "confirm() should report a result")
            self.assertTrue(target.exists(), "approval should allow the write")
        finally:
            if target.exists():
                target.unlink()

    def test_execute_without_grant_cannot_escape_workspace(self) -> None:
        target = Path(self._tmp.name).parent / "atom_no_grant_probe.txt"
        self.hands._sandbox.allow_outside_writes = True
        with self.assertRaises(PathPolicyError):
            self.hands.execute({"action": "write_file", "path": str(target), "text": "x"})
        self.assertFalse(target.exists())


# ═══════════════════════════════════════════════════════════════════════════════
# Brain tool parsing + prompt
# ═══════════════════════════════════════════════════════════════════════════════

class TestFileToolParsing(unittest.TestCase):
    def setUp(self) -> None:
        self.brain = BrainThread()

    def test_read_tool(self) -> None:
        self.assertEqual(self.brain._parse_action("[read: notes.txt]"),
                         {"action": "read_file", "path": "notes.txt"})

    def test_ls_tool(self) -> None:
        self.assertEqual(self.brain._parse_action("[ls: .]"),
                         {"action": "list_dir", "path": "."})

    def test_write_tool_splits_path_and_content(self) -> None:
        action = self.brain._parse_action("[write: draft.md | hello world]")
        self.assertEqual(action, {"action": "write_file", "path": "draft.md", "text": "hello world"})

    def test_write_tool_allows_empty_body(self) -> None:
        action = self.brain._parse_action("[write: empty.txt | ]")
        self.assertEqual(action, {"action": "write_file", "path": "empty.txt", "text": ""})

    def test_write_tool_allows_newlines(self) -> None:
        action = self.brain._parse_action("[write: a.txt | line1\nline2]")
        self.assertIn("line2", action["text"])

    def test_append_mkdir_rm_tools(self) -> None:
        self.assertEqual(self.brain._parse_action("[append: log.txt | more]")["action"], "append_file")
        self.assertEqual(self.brain._parse_action("[mkdir: reports]")["action"], "make_dir")
        self.assertEqual(self.brain._parse_action("[rm: old.txt]")["action"], "delete_file")

    def test_mixed_call_order_is_preserved(self) -> None:
        text = "[ls: .] then [read: a.txt] then [write: b.txt | hi]"
        names = [a["action"] for a in self.brain._parse_actions(text)]
        self.assertEqual(names, ["list_dir", "read_file", "write_file"])

    def test_legacy_tools_still_parse(self) -> None:
        self.assertEqual(self.brain._parse_action("[search: rust]")["action"], "search")
        self.assertEqual(self.brain._parse_action("[shell: dir]")["action"], "shell")

    def test_slash_and_spacing_variants_parse(self) -> None:
        """Small local models write `[/write: x | y]`; dropping that call silently
        meant the user got prose where an action was intended."""
        expected = {"action": "write_file", "path": "a.txt", "text": "hi"}
        for text in ("[write: a.txt | hi]", "[/write: a.txt | hi]",
                     "[ write: a.txt | hi ]", "[/ write: a.txt | hi]"):
            with self.subTest(text=text):
                self.assertEqual(self.brain._parse_action(text), expected)

    def test_slash_variant_paths_still_work(self) -> None:
        self.assertEqual(self.brain._parse_action("[/read: notes.txt]"),
                         {"action": "read_file", "path": "notes.txt"})
        self.assertEqual(self.brain._parse_action("[/rm: old.txt]")["action"], "delete_file")

    def test_variant_markup_is_stripped_from_display(self) -> None:
        cleaned = self.brain._strip_action_markers("Saving it now [/write: a.txt | hi] for you")
        self.assertNotIn("write:", cleaned)
        self.assertIn("Saving it now", cleaned)

    def test_prose_without_brackets_is_not_an_action(self) -> None:
        self.assertEqual(self.brain._parse_actions("write: a.txt | hi"), [])


class TestWorkspaceHintInPrompt(unittest.TestCase):
    def test_hint_absent_by_default(self) -> None:
        self.assertNotIn("Your workspace:", get_system_prompt("tap"))

    def test_hint_present_and_read_only_flagged(self) -> None:
        prompt = get_system_prompt("tap", workspace=r"D:\work\sandbox")
        self.assertIn(r"D:\work\sandbox", prompt)
        self.assertIn("READ-ONLY", prompt)

    def test_hint_tells_the_model_it_may_ask_for_an_outside_path(self) -> None:
        """The model must not refuse on its own — permission is Koko's call."""
        prompt = get_system_prompt("tap", workspace="C:/ws")
        self.assertIn("approves or denies", prompt)
        self.assertIn("never refuse on your own", prompt)

    def test_hint_claims_ownership_of_the_workspace(self) -> None:
        prompt = get_system_prompt("tap", workspace="C:/ws")
        self.assertIn("YOURS", prompt)
        self.assertIn("no\n  permission needed", prompt)

    def test_brain_resolves_workspace_from_config(self) -> None:
        cfg = AppConfig()
        cfg.permissions.sandbox_dir = "workbench"
        brain = BrainThread(cfg)
        self.assertIsNotNone(brain._workspace_path())
        self.assertIn("workbench", brain._workspace_path())


if __name__ == "__main__":
    unittest.main()
