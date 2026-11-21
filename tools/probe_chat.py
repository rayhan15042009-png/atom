"""Probe the chat panel: add a long reply and grab the panel to check clipping."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QSurfaceFormat
from PyQt6.QtWidgets import QApplication

fmt = QSurfaceFormat()
fmt.setAlphaBufferSize(8)
fmt.setSamples(4)
fmt.setDepthBufferSize(24)
QSurfaceFormat.setDefaultFormat(fmt)

from core.config_manager import load_config  # noqa: E402
from ui.overlay import AtomOverlay  # noqa: E402

app = QApplication(sys.argv[:1])
cfg = load_config()
ov = AtomOverlay(cfg)
ov._init_window_flags()
ov.show()

LONG = (
    "*tap* Here is the full answer, Koko. Python 3.13 shipped on October 7, 2024 "
    "with a new interactive interpreter, an experimental free-threaded build, "
    "and the first round of JIT work.\n\n"
    "1. The REPL now supports multi-line editing and colour output.\n"
    "2. `python -X gil=0` runs without the global interpreter lock.\n"
    "3. Error messages point at the failing expression, not just the line.\n\n"
    "If you want the exact release notes I can open the page for you.\n\n"
    "This paragraph exists purely to make the reply long enough that it needs "
    "several wrapped lines — the exact case where the bubble used to be cut off "
    "at the bottom because the row height and the painted height disagreed."
)


def add() -> None:
    ov.add_chat_message("user", "what changed in python 3.13?")
    ov.add_chat_message("assistant", LONG, animate=False)


def finish() -> None:
    ov.chat_list.scrollToBottom()
    panel = ov._chat_panel
    img = panel.grab()
    out = ROOT / "logs" / "probe_chat_panel.png"
    img.save(str(out))
    total = 0
    for i in range(ov.chat_list.count()):
        item = ov.chat_list.item(i)
        if item is not None:
            total += item.sizeHint().height()
    print("panel", panel.width(), panel.height(), "items", ov.chat_list.count(),
          "sum(hints)", total, "->", out)
    app.quit()


QTimer.singleShot(600, add)
QTimer.singleShot(1600, finish)
app.exec()
