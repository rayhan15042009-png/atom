"""``AtomApp._shutdown`` must be safe to run more than once.

It is registered with ``atexit`` *and* called from every quit path (the quit
command, the tray menu, ``QApplication.aboutToQuit``), so a normal exit ran it
twice: the log said "Shutting down..." twice, every worker was stopped twice
and state was written twice. The second pass is now a no-op.
"""

from __future__ import annotations

import os
import sys
import types
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

import atom as atom_module  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])


class _Worker:
    def __init__(self) -> None:
        self.stops = 0

    def isRunning(self) -> bool:  # noqa: N802 - Qt naming
        return True

    def stop(self) -> None:
        self.stops += 1


class TestShutdownIsIdempotent(unittest.TestCase):
    def _app(self) -> tuple[types.SimpleNamespace, list[str], list[_Worker]]:
        workers = [_Worker(), _Worker(), _Worker()]
        saves: list[str] = []
        fake = types.SimpleNamespace(
            brain=workers[0],
            eyes=workers[1],
            voice=workers[2],
            anim_machine=None,
            familiar=None,
            _familiar_timer=None,
            _save_state=lambda: saves.append("save"),
        )
        return fake, saves, workers

    def test_second_call_does_not_repeat_the_work(self) -> None:
        fake, saves, workers = self._app()
        quits: list[int] = []
        fake_qt = types.SimpleNamespace(quit=lambda: quits.append(1))
        with mock.patch.object(atom_module, "QApplication", fake_qt):
            atom_module.AtomApp._shutdown(fake)
            atom_module.AtomApp._shutdown(fake)
        self.assertEqual([w.stops for w in workers], [1, 1, 1],
                         "workers were stopped twice")
        self.assertEqual(saves, ["save"], "state was saved twice")
        self.assertEqual(len(quits), 1, "the app was told to quit twice")

    def test_first_call_still_does_everything(self) -> None:
        fake, saves, workers = self._app()
        with mock.patch.object(
            atom_module, "QApplication", types.SimpleNamespace(quit=lambda: None)
        ):
            atom_module.AtomApp._shutdown(fake)
        self.assertEqual([w.stops for w in workers], [1, 1, 1])
        self.assertEqual(saves, ["save"])
        self.assertTrue(fake._shutting_down)


if __name__ == "__main__":
    unittest.main()
