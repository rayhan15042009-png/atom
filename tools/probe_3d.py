"""Probe the 3D renderer: does it produce visible pixels?

Usage: .venv/Scripts/python tools/probe_3d.py [character]
Saves logs/probe_3d_<character>.png and prints an alpha/colour summary.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QSurfaceFormat
from PyQt6.QtWidgets import QApplication, QMainWindow

fmt = QSurfaceFormat()
fmt.setAlphaBufferSize(8)
fmt.setSamples(4)
fmt.setDepthBufferSize(24)
fmt.setStencilBufferSize(8)
QSurfaceFormat.setDefaultFormat(fmt)

from ui.renderer_3d import BlockRenderer3D, create_3d_widget  # noqa: E402

character = sys.argv[1] if len(sys.argv) > 1 else "atom"

app = QApplication(sys.argv[:1])
win = QMainWindow()
from PyQt6.QtCore import Qt

win.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
win.resize(400, 560)
widget = create_3d_widget(win)
if widget is None:
    print("RESULT: create_3d_widget returned None")
    raise SystemExit(2)
win.setCentralWidget(widget)
win.show()
widget.set_profile(character)
widget.cam_rotation = 20.0
widget.cam_elevation = 8.0
widget.cam_distance = 5.2
widget.pose = "talk"  # non-idle: stops the idle auto-rotation while probing


def finish() -> None:
    img = widget.grabFramebuffer()
    out = ROOT / "logs" / f"probe_3d_{character}.png"
    img = img.scaled(300, 420)
    img.save(str(out))
    opaque = 0
    colours = set()
    step = max(1, img.width() // 60)
    for y in range(0, img.height(), step):
        for x in range(0, img.width(), step):
            c = img.pixelColor(x, y)
            if c.alpha() > 8:
                opaque += 1
                colours.add((c.red() // 16, c.green() // 16, c.blue() // 16))
    total = (img.width() // step + 1) * (img.height() // step + 1)
    print(f"RESULT: size={img.width()}x{img.height()} opaque_samples={opaque}/{total} "
          f"distinct_colours={len(colours)} saved={out}")
    print("VERDICT:", "RENDERS" if opaque > 10 and len(colours) > 3 else "BLANK")
    app.quit()


QTimer.singleShot(1200, finish)
app.exec()
