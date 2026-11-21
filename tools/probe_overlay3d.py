"""Probe the overlay's 3D path: does the render window appear and paint?"""
from __future__ import annotations

import sys
import time
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
cfg.render_mode = "3d"
ov = AtomOverlay(cfg)
ov._init_window_flags()
ov.show()


def finish() -> None:
    win = getattr(ov, "_render_win", None)
    print("renderer_3d:", type(ov.renderer_3d).__name__)
    print("render_win visible:", None if win is None else win.isVisible())
    print("frames:", ov._3d_frames_rendered, "after", round(time.time() - started, 2), "s")
    if win is not None:
        img = win.grab()
        out = ROOT / "logs" / "probe_overlay_3d.png"
        img.save(str(out))
        print("grabbed", img.width(), img.height(), "->", out)
    app.quit()


started = time.time()
QTimer.singleShot(2500, finish)
app.exec()
