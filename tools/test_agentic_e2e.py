"""Agentic end-to-end check for Atom.

Drives the real application stack (overlay, chat controller, hands, sandbox,
brain thread) headless and exercises every agentic ability:

  A. Deterministic coverage — the model's replies are scripted, so the whole
     pipeline (parse → permission gate → sandbox → result feedback) is verified
     for: read anywhere, list, write inside the workspace, write outside with
     deny and with approve, hard-lockdown refusal, shell approval, multi-tool
     chains, multi-round tool loops, memory, workspace commands and the chat
     minimize / restore path.
  B. Live multi-turn chat — the real provider chain answers several turns
     through the same UI, checking that answers are real and that history is
     carried between turns.

Usage:
    .venv/Scripts/python.exe tools/test_agentic_e2e.py            # A + live chat
    .venv/Scripts/python.exe tools/test_agentic_e2e.py --offline  # A only
    .venv/Scripts/python.exe tools/test_agentic_e2e.py --turns 4

Exit code 0 means every hard check passed; WARN lines are informational.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The Windows console defaults to cp1252, which cannot encode the box-drawing
# characters used in this report.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - non-reconfigurable stream
    pass
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false")

from PyQt6.QtCore import QEvent, Qt, QTimer  # noqa: E402
from PyQt6.QtGui import QKeyEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton  # noqa: E402

import atom as atom_mod  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════════
# Result bookkeeping
# ═══════════════════════════════════════════════════════════════════════════════

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.rows.append((status, name, detail))
        icon = {"PASS": "  ok  ", "FAIL": " FAIL ", "WARN": " warn "}[status]
        print(f"[{icon}] {name}" + (f" — {detail}" if detail else ""), flush=True)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.add(PASS if ok else FAIL, name, detail)
        return ok

    def warn(self, name: str, detail: str = "") -> None:
        self.add(WARN, name, detail)

    @property
    def failed(self) -> int:
        return sum(1 for status, _, _ in self.rows if status == FAIL)


# ═══════════════════════════════════════════════════════════════════════════════
# Event-loop helpers — the harness pumps Qt itself instead of calling exec()
# ═══════════════════════════════════════════════════════════════════════════════

def pump(qt: QApplication, seconds: float) -> None:
    """Spin the Qt event loop for *seconds* without blocking signal delivery."""
    end = time.time() + seconds
    while time.time() < end:
        qt.processEvents()
        time.sleep(0.01)


def wait_for(qt: QApplication, predicate, timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        qt.processEvents()
        time.sleep(0.02)
    return bool(predicate())


def chat_rows(overlay) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for i in range(overlay.chat_list.count()):
        item = overlay.chat_list.item(i)
        if item is None or item.data(Qt.ItemDataRole.UserRole + 5):
            continue  # date separator
        if item.data(Qt.ItemDataRole.UserRole + 6) == "thinking":
            continue
        rows.append((item.data(Qt.ItemDataRole.UserRole + 1), item.text() or ""))
    return rows


def texts(overlay, role: str | None = None) -> list[str]:
    """Chat texts, optionally filtered by role (None = every role)."""
    if role is None:
        return [text for _, text in chat_rows(overlay)]
    return [text for r, text in chat_rows(overlay) if r == role]


def wait_for_reply(qt: QApplication, overlay, before: int, timeout: float = 180.0) -> tuple[str, float]:
    """Wait for a *complete* assistant reply.

    A reply row is created empty and then typed out character by character, so
    counting rows (or reading the last row's text immediately) catches a
    blank half-written line. This waits until the text stops growing instead.
    Returns ``(text, seconds)``; text is "" when nothing arrived.
    """
    started = time.time()
    deadline = started + timeout
    last_len, stable = -1, 0
    while time.time() < deadline:
        qt.processEvents()
        time.sleep(0.2)
        rows = texts(overlay, "assistant")
        if len(rows) <= before:
            continue
        newest = rows[-1]
        if newest and len(newest) == last_len:
            stable += 1
            if stable >= 5:            # ~1s without a change = finished typing
                return newest, time.time() - started
        else:
            stable = 0
        last_len = len(newest)
    rows = texts(overlay, "assistant")
    return (rows[-1] if len(rows) > before else ""), time.time() - started


# ═══════════════════════════════════════════════════════════════════════════════
# Scripted model
# ═══════════════════════════════════════════════════════════════════════════════

class ScriptedRouter:
    """Stands in for ProviderRouter: hands back canned replies and records the
    full message array it was asked to answer, which is how history is checked."""

    def __init__(self, replies: list[str] | None = None) -> None:
        self.replies = list(replies or [])
        self.calls: list[list[dict]] = []

    def push(self, *replies: str) -> None:
        self.replies.extend(replies)

    def chat(self, messages, mode="tap"):  # noqa: D401 - router protocol
        self.calls.append(messages)
        return self.replies.pop(0) if self.replies else "Noted."

    def cleanup(self) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# Turn driver
# ═══════════════════════════════════════════════════════════════════════════════

class Harness:
    def __init__(self, app, qt: QApplication, report: Report, router: ScriptedRouter) -> None:
        self.app = app
        self.qt = qt
        self.report = report
        self.router = router
        self.feeds: list[str] = []
        self.payloads: list[dict] = []
        self._install_recorders()

    # ── plumbing ──

    def _install_recorders(self) -> None:
        real_feed = self.app.brain.feed_action_result
        real_ask = self.app.brain.ask

        def recording_feed(result: str):
            self.feeds.append(result)
            return real_feed(result)

        def recording_ask(payload: dict):
            self.payloads.append(payload)
            return real_ask(payload)

        self.app.brain.feed_action_result = recording_feed  # type: ignore[assignment]
        self.app.brain.ask = recording_ask  # type: ignore[assignment]

    def reset(self) -> None:
        self.feeds.clear()
        self.app.brain.clear_queue()
        if self.app.hands.pending is not None:
            self.app.hands.cancel()
        self.app._awaiting_action_result = False

    def turn(self, text: str, *replies: str, router: ScriptedRouter | None = None,
             timeout: float = 25.0) -> list[str]:
        """Send one user message and wait for the app to settle.

        Settled = a new chat row appeared, or the action is waiting for
        permission. Returns the chat texts that exist afterwards.
        """
        (router or self.router).push(*replies)
        before = len(chat_rows(self.app.overlay))
        self.app.overlay.input.setText(text)
        self.app.overlay._on_input_entered()
        wait_for(self.qt, lambda: len(chat_rows(self.app.overlay)) > before
                 or self.app.hands.pending is not None, timeout)
        pump(self.qt, 0.35)
        return texts(self.app.overlay)

    def wait_text(self, needle: str, role: str | None = "assistant", timeout: float = 12.0) -> str:
        """Wait for a chat message containing *needle* — messages are typed out
        with an animation, so reading immediately can catch a half-written line."""
        hit: list[str] = []

        def found() -> bool:
            for text in texts(self.app.overlay, role):
                if needle in text:
                    hit.append(text)
                    return True
            return False

        wait_for(self.qt, found, timeout)
        if hit:
            return hit[-1]
        remaining = texts(self.app.overlay, role)
        return remaining[-1] if remaining else ""

    def answer_permission(self, word: str) -> None:
        self.app.overlay.input.setText(word)
        self.app.overlay._on_input_entered()
        pump(self.qt, 0.6)

    def send_key(self, key, mods=Qt.KeyboardModifier.NoModifier) -> None:
        event = QKeyEvent(QEvent.Type.KeyPress, key, mods)
        self.app.overlay.keyPressEvent(event)
        pump(self.qt, 0.25)


# ═══════════════════════════════════════════════════════════════════════════════
# Part A — deterministic coverage of every agentic ability
# ═══════════════════════════════════════════════════════════════════════════════

def part_a(h: Harness, outside: Path) -> None:
    rep, app, router = h.report, h.app, h.router
    workspace = Path(app.hands.workspace)
    sample = ROOT / "README.md"

    print("\n── A1. Reads are free anywhere on disk ──", flush=True)
    h.reset()
    h.turn("summarise my readme for me",
           f"[read: {sample}]", "It is the Atom README — the companion that\n"
           "lives on your desktop and answers questions.", router=router)
    h.turn("and now list your own folder", "[ls: .]", "Listed it.", router=router)
    joined = "\n".join(h.feeds)
    rep.check("read_file outside workspace runs without prompting",
              any("README" in f for f in h.feeds), f"{len(h.feeds)} tool result(s)")
    rep.check("list_dir reports the sandbox workspace", "sandbox workspace" in joined,
              "listing marked as the workspace")

    print("\n── A2. Writes inside the workspace are free ──", flush=True)
    h.reset()
    h.turn("save a note for me",
           "[write: e2e_note.txt | hello from the e2e run]",
           "Saved it in my workspace.", router=router)
    inside = workspace / "e2e_note.txt"
    rep.check("write inside the workspace needs no permission",
              inside.exists() and app.hands.pending is None)
    rep.check("tool result flags the sandbox scope",
              any("[sandbox]" in f for f in h.feeds), h.feeds[-1][:80] if h.feeds else "no result")
    h.reset()
    h.turn("add a second line to it",
           "[append: e2e_note.txt |  second line]", "Appended.", router=router)
    rep.check("append_file works inside the workspace",
              inside.exists() and "second line" in inside.read_text(encoding="utf-8"))

    print("\n── A3. Outside writes are gated by permission ──", flush=True)
    h.reset()
    h.turn("put that note next to my temp files",
           f"[write: {outside} | denied by the user]",
           "That one needs your say-so.", router=router)
    prompted = app.hands.pending is not None
    rep.check("outside write stops and asks first", prompted)
    rep.check("nothing is written before approval", not outside.exists())
    rep.check("prompt names the workspace", any("workspace" in t.lower() for t in texts(app.overlay, "system")))
    h.answer_permission("no")
    rep.check("denied write never touches the disk", not outside.exists())
    rep.check("denial is reported back to the model",
              any("denied" in f.lower() or "cancel" in f.lower() for f in h.feeds))

    h.reset()
    h.turn("actually just save it there anyway",
           f"[write: {outside} | approved by the user]",
           "Done — wrote it.", router=router)
    still_gated = app.hands.pending is not None
    rep.check("second outside write asks again", still_gated)
    h.answer_permission("yes")
    rep.check("approval lets the write through", outside.exists())
    if outside.exists():
        rep.check("approved file has the right contents",
                  "approved by the user" in outside.read_text(encoding="utf-8"))
        outside.unlink()

    print("\n── A4. Hard lockdown refuses instead of asking ──", flush=True)
    h.reset()
    app.hands._sandbox.allow_outside_writes = False
    try:
        h.turn("write it outside anyway",
               f"[write: {outside} | should never land]", "Understood.", router=router)
        rep.check("locked-down policy refuses without prompting", app.hands.pending is None)
        rep.check("refusal is explained in chat",
                  any("Blocked" in t and "allow_outside_writes" in t for t in texts(app.overlay, "system")))
        rep.check("refusal reaches the model with guidance",
                  any("REFUSED" in f for f in h.feeds))
        rep.check("locked-down write left no file", not outside.exists())
    finally:
        app.hands._sandbox.allow_outside_writes = True

    print("\n── A5. Shell needs approval and really runs ──", flush=True)
    h.reset()
    h.turn("run a shell command to prove it works",
           "[shell: echo atom-e2e-ok]", "Waiting for your permission.", router=router)
    rep.check("shell action asks first", app.hands.pending is not None)
    h.answer_permission("yes")
    rep.check("approved shell actually ran", any("atom-e2e-ok" in f for f in h.feeds),
              h.feeds[-1][:80] if h.feeds else "no result")

    print("\n── A6. Several tools in one reply run as a chain ──", flush=True)
    h.reset()
    h.turn("make a folder and drop a file in it",
           "[mkdir: e2e_dir] then [write: e2e_dir/inside.txt | chained]",
           "Both done.", router=router)
    rep.check("multi-tool chain created the folder", (workspace / "e2e_dir").is_dir())
    rep.check("multi-tool chain wrote the file",
              (workspace / "e2e_dir" / "inside.txt").exists())
    rep.check("both results came back in one payload",
              any("chained" in f or "e2e_dir" in f for f in h.feeds))

    print("\n── A7. Multi-round tool loop ──", flush=True)
    h.reset()
    h.turn("what is in my note?",
           "[read: e2e_note.txt]", "It says hello, and mentions a second line.",
           router=router)
    rep.check("tool result triggers a follow-up round", len(h.feeds) >= 1,
              f"{len(h.feeds)} tool result(s), then the model answered")
    final = h.wait_text("second line")
    rep.check("the loop ends with a real answer", "second line" in final, final[:70])
    rep.check("the original question survives the loop",
              any("what is in my note?" in str(p.get("_original_question", ""))
                  for p in h.payloads if p.get("_action_result_feedback")))

    print("\n── A8. Multi-turn history reaches the model ──", flush=True)
    h.reset()
    before_calls = len(router.calls)
    h.turn("remember the codeword zaqwar", "Noted, zaqwar.", router=router)
    h.turn("what was the codeword?", "It was zaqwar.", router=router)
    rep.check("a second turn was sent", len(router.calls) > before_calls + 1)
    latest = router.calls[-1]
    flat = [m for m in latest]
    rep.check("earlier user turn is replayed", any(
        m["role"] == "user" and "zaqwar" in m["content"] for m in flat))
    rep.check("earlier assistant turn is replayed as assistant", any(
        m["role"] == "assistant" and "zaqwar" in m["content"] for m in flat))
    rep.check("the current question is not duplicated",
              sum(1 for m in flat if m["role"] == "user" and "what was the codeword" in m["content"]) == 1)

    print("\n── A9. Memory facts are injected ──", flush=True)
    h.reset()
    app.chat_controller.on_user_input("/remember E2E: the project is Nightingale")
    pump(h.qt, 0.3)
    before_calls = len(router.calls)
    h.turn("what is my project called?", "Nightingale.", router=router)
    heap = " ".join(str(m.get("content", "")) for m in router.calls[-1])
    rep.check("remembered fact reaches the prompt", "Nightingale" in heap)
    rep.check("memory command produced a reply",
              "Nightingale" in " ".join(app.memory.get("facts", [])))

    print("\n── A10. Workspace + status commands ──", flush=True)
    h.reset()
    app.chat_controller.on_user_input("/workspace")
    ws_text = h.wait_text("read-only")
    rep.check("/workspace names the writable folder", str(workspace) in ws_text, ws_text.splitlines()[0][:70])
    rep.check("/workspace states the read-only policy", "read-only" in ws_text.lower())
    policy = app.hands.blocked_reason({"action": "write_file", "path": str(outside), "text": "x"})
    rep.check("policy query reports outside writes as allowed-with-consent", policy == "")

    print("\n── A11. Chat minimize / restore ──", flush=True)
    overlay = app.overlay
    buttons = overlay._chat_panel.findChildren(QPushButton)
    discoverable = [b for b in buttons if "Minimize chat" in (b.toolTip() or "")]
    rep.check("header has a discoverable minimize button", bool(discoverable),
              discoverable[0].toolTip() if discoverable else "not found")
    overlay._minimize_chat()
    hidden = wait_for(h.qt, lambda: not overlay._chat_panel.isVisible(), timeout=3.0)
    rep.check("minimize hides the chat panel", hidden)
    rep.check("minimize leaves a restore bubble", overlay._float_button.isVisible())
    h.send_key(Qt.Key.Key_M, Qt.KeyboardModifier.ControlModifier)
    pump(h.qt, 0.8)
    rep.check("Ctrl+M restores the chat", not overlay._chat_minimized and overlay._chat_panel.isVisible())
    h.send_key(Qt.Key.Key_Escape)
    pump(h.qt, 0.8)
    rep.check("Esc collapses it again", overlay._chat_minimized)
    overlay._restore_chat()
    pump(h.qt, 0.5)

    print("\n── A12. Cleanup of scratch output ──", flush=True)
    import shutil
    for extra in (workspace / "e2e_dir", workspace / "chain.txt"):
        if extra.is_dir():
            shutil.rmtree(extra, ignore_errors=True)
        elif extra.exists():
            extra.unlink()
    note = workspace / "e2e_note.txt"
    if note.exists():
        note.unlink()
    rep.check("workspace left tidy after the run",
              not note.exists() and not (workspace / "e2e_dir").exists())


# ═══════════════════════════════════════════════════════════════════════════════
# Part B — live multi-turn chat against the real provider chain
# ═══════════════════════════════════════════════════════════════════════════════

LIVE_TURNS = [
    "In one short sentence: what is a Python virtual environment?",
    "And which command creates one?",
    "Keep that in mind. Now: my project is called Nightingale.",
    "What did I just tell you my project is called?",
]

BANNED = ("all models failed", "error:", "traceback", "brain's not online")


def part_b(app, qt: QApplication, rep: Report, turns: int) -> None:
    print("\n── B. Live multi-turn chat (real providers) ──", flush=True)
    app.brain._router = None  # let the thread build a real router on next use
    # The real chat is driven from the UI: stop the proactive perception feed so
    # OCR-triggered requests cannot interleave with the turns under test.
    try:
        app.eyes.stop()
    except Exception:
        pass

    seen: list[str] = []
    app.brain.response_ready.connect(seen.append)
    errors: list[str] = []
    app.brain.error.connect(errors.append)
    asked = LIVE_TURNS[:turns]

    for i, question in enumerate(asked, 1):
        before = len(texts(app.overlay, "assistant"))
        seen.clear()
        app.overlay.input.setText(question)
        app.overlay._on_input_entered()
        answer, took = wait_for_reply(qt, app.overlay, before)
        got = bool(answer.strip())
        print(f"      turn {i}: {took:.0f}s, rows={len(texts(app.overlay, 'assistant'))} "
              f"(before={before}), emitted={len(seen)}, errors={errors[-1:]}", flush=True)
        rep.check(f"turn {i} answered", bool(got and answer.strip()),
                  (answer.strip().splitlines()[0][:90] if answer.strip() else "no reply"))
        low = answer.lower()
        if answer.strip():
            rep.check(f"turn {i} is a real answer", not any(b in low for b in BANNED))
        else:
            rep.add(WARN, f"turn {i} produced no answer",
                    errors[-1][:80] if errors else "nothing came back")
        if i == len(asked) and "nightingale" in " ".join(asked).lower():
            rep.check("live model carried the conversation over",
                      "nightingale" in low, answer.strip()[:80])
        errors.clear()

    print("\n── B2. Live tool use (informational) ──", flush=True)
    before_actions = len(app.hands._sandbox._timestamps)
    app.overlay.input.setText("Use your ls tool to list the files in your workspace, then tell me one filename.")
    app.overlay._on_input_entered()
    wait_for(qt, lambda: len(app.hands._sandbox._timestamps) > before_actions
             or app.hands.pending is not None, timeout=90.0)
    if len(app.hands._sandbox._timestamps) > before_actions or app.hands.pending is not None:
        rep.add(PASS, "live model asked for a tool", "the agentic loop ran end to end")
    else:
        rep.warn("live model asked for a tool",
                 "small local models rarely emit tool markup — pipeline covered by part A")
    pump(qt, 2.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════════

def build_app() -> tuple:
    qt = QApplication.instance() or QApplication(sys.argv)
    qt.setQuitOnLastWindowClosed(False)
    app = atom_mod.AtomApp([sys.argv[0]], qt_app=qt)
    app.config.tts_enabled = False          # keep the run silent + fast
    app.start()
    pump(qt, 2.0)                            # let workers come up
    return qt, app


def main() -> int:
    parser = argparse.ArgumentParser(description="Atom agentic end-to-end check")
    parser.add_argument("--offline", action="store_true", help="skip the live multi-turn chat")
    parser.add_argument("--turns", type=int, default=len(LIVE_TURNS), help="live turns to run")
    parser.add_argument("--part-a-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--part-b-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    rep = Report()
    print("=" * 72)
    print("  ATOM — AGENTIC END-TO-END CHECK")
    print("=" * 72, flush=True)

    qt, app = build_app()
    print(f"workspace : {app.hands.workspace}")
    print(f"policy    : outside writes {'allowed with permission' if app.hands._sandbox.allow_outside_writes else 'LOCKED DOWN'}")
    print(f"brain     : {'running' if app.brain.isRunning() else 'stopped'}", flush=True)

    app.hands._sandbox._max_per_minute = 200  # E2E fires many actions quickly

    h = Harness(app, qt, rep, ScriptedRouter())
    app.brain._router = h.router          # type: ignore[assignment]

    outside = Path(tempfile.gettempdir()) / "atom_e2e_outside.txt"
    if outside.exists():
        outside.unlink()

    try:
        if not args.part_b_only:
            part_a(h, outside)
        if not (args.offline or args.part_a_only):
            part_b(app, qt, rep, args.turns)
    finally:
        if outside.exists():
            outside.unlink()
        QTimer.singleShot(200, app._shutdown)
        pump(qt, 1.5)

    print("\n" + "=" * 72)
    passed = sum(1 for s, _, _ in rep.rows if s == PASS)
    warned = sum(1 for s, _, _ in rep.rows if s == WARN)
    print(f"  {passed} passed, {rep.failed} failed, {warned} warning(s)")
    print("=" * 72, flush=True)
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
