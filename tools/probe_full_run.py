"""End-to-end probe: real intro → Atom → a live search question → screenshots.

Boots the app the way `atom.py main()` does (intro first, Atom after), then asks
a question that needs current information and captures what is actually on
screen. Skips the dependency installer so a model pull cannot stall the run.

Usage: .venv/Scripts/python tools/probe_full_run.py
Writes logs/probe_run_0_intro.png, probe_run_1_atom.png, probe_run_2_answer.png
and prints the chat transcript contents.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Logs are redirected on Windows (cp1252); emoji in chat text would crash print.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PyQt6.QtCore import QTimer, Qt  # noqa: E402
from PyQt6.QtGui import QSurfaceFormat  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from atom import AtomApp, _fmt  # noqa: E402
from core.config_manager import load_config  # noqa: E402
from core.logger import get_logger  # noqa: E402
from ui.intro import AtomIntroSequence  # noqa: E402

log = get_logger("probe_full_run")

QUESTION = "what is the current price of bitcoin today?"
LOGS = ROOT / "logs"

QSurfaceFormat.setDefaultFormat(_fmt)
app = QApplication(sys.argv[:1])
app.setQuitOnLastWindowClosed(False)
config = load_config()

state: dict = {
    "atom": None, "asked_at": 0.0, "done": False,
    "assistant_before": 0, "last_text": "", "stable": 0,
}


def screenshot(name: str) -> None:
    """Whole-desktop capture so the intro/overlay is verified as drawn."""
    try:
        from PIL import ImageGrab

        img = ImageGrab.grab(all_screens=True)
        img.save(LOGS / name)
        print(f"SCREENSHOT {name} {img.size}", flush=True)
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"SCREENSHOT FAILED {name}: {exc}", flush=True)


def capture_panel(name: str) -> None:
    atom = state["atom"]
    if atom and atom.overlay:
        atom.overlay._chat_panel.grab().save(str(LOGS / name))
        print(f"PANEL {name}", flush=True)


def chat_geometry() -> None:
    """Print the last two rows' rects vs the viewport, to see clipping."""
    atom = state["atom"]
    if not atom or not atom.overlay:
        return
    lst = atom.overlay.chat_list
    vp = lst.viewport().rect()
    sb = lst.verticalScrollBar()
    print(f"GEO viewport={vp.width()}x{vp.height()} scroll={sb.value()}/{sb.maximum()}", flush=True)
    for i in range(max(0, lst.count() - 2), lst.count()):
        item = lst.item(i)
        r = lst.visualItemRect(item)
        print(
            f"GEO row{i} hint={item.sizeHint().height()} rect=({r.top()},{r.bottom()}) "
            f"text={item.text()[:28]!r}", flush=True,
        )


def transcript() -> list[tuple[str, str]]:
    atom = state["atom"]
    if not atom or not atom.overlay:
        return []
    items = []
    for i in range(atom.overlay.chat_list.count()):
        item = atom.overlay.chat_list.item(i)
        if item is None:
            continue
        role = item.data(Qt.ItemDataRole.UserRole + 1)
        text = item.text() or ""
        if text:
            items.append((str(role), text))
    return items


def wait_for_answer(elapsed: int = 0) -> None:
    atom = state["atom"]
    if not atom or not atom.overlay:
        return
    assistant = [text for role, text in transcript() if role == "assistant"]
    typing_done = getattr(atom.overlay, "_typing_item", None) is None
    latest = assistant[-1] if len(assistant) > state["assistant_before"] else ""

    if latest and latest == state["last_text"]:
        state["stable"] += 1
    else:
        state["stable"] = 0
        state["last_text"] = latest

    # Elapsed is in seconds: the timer runs every 500 ms, so add half a second.
    if (latest and typing_done and state["stable"] >= 2) or elapsed > 180:
        QTimer.singleShot(1200, finish)
        return
    QTimer.singleShot(500, lambda: wait_for_answer(elapsed + 0.5))


def ask_question() -> None:
    atom = state["atom"]
    if not atom or not atom.chat_controller:
        print("NO ATOM APP", flush=True)
        app.quit()
        return
    screenshot("probe_run_1_atom.png")
    capture_panel("probe_run_1_atom_chat.png")
    print("ATOM STARTED — asking:", QUESTION, flush=True)
    state["asked_at"] = time.time()
    state["assistant_before"] = len(
        [1 for role, _ in transcript() if role == "assistant"]
    )
    atom.chat_controller.on_user_input(QUESTION)
    QTimer.singleShot(900, lambda: (capture_panel("probe_run_1_thinking_chat.png"), chat_geometry()))
    QTimer.singleShot(1000, wait_for_answer)


def finish() -> None:
    if state["done"]:
        return
    state["done"] = True
    screenshot("probe_run_2_answer.png")
    capture_panel("probe_run_2_answer_chat.png")
    print("=== TRANSCRIPT (last 4) ===", flush=True)
    for role, text in transcript()[-4:]:
        print(f"[{role}] {text[:400]}".replace("\n", " | "), flush=True)
    atom = state["atom"]
    if atom:
        try:
            atom._shutdown()
        except Exception as exc:
            print("shutdown error:", exc, flush=True)
    QTimer.singleShot(500, app.quit)


def on_intro_done() -> None:
    log.info("Intro finished — starting Atom (probe)")
    intro.stop()
    intro.deleteLater()
    atom = AtomApp(sys.argv[:1], qt_app=app)
    atom.start()
    state["atom"] = atom
    QTimer.singleShot(4000, ask_question)


intro = AtomIntroSequence(config)
intro.all_done.connect(on_intro_done)

# Proof that the intro is on screen while it runs (boot screen, then the video).
QTimer.singleShot(3000, lambda: screenshot("probe_run_0a_intro.png"))
QTimer.singleShot(9000, lambda: screenshot("probe_run_0_intro.png"))
# Hard stops: never leave an orphaned full-screen window behind.
QTimer.singleShot(240000, lambda: (log.warning("Intro watchdog fired"), on_intro_done()))
QTimer.singleShot(400000, finish)

print("INTRO START", flush=True)
intro.start()
sys.exit(app.exec())
