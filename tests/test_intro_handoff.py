"""The intro video plays once — when it ends, Atom loads instead of replaying it.

The welcome screen and the dance screen both play ``assets/atom_intro.mp4``.
Handing the welcome screen straight to the dance screen restarted the whole
video from the top, which is exactly what "the intro replays after the video
finishes" looked like.
"""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui.intro import AtomIntroSequence  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])


class _Screen:
    def __init__(self, has_video: bool) -> None:
        self._has_intro_video = has_video


class TestWelcomeHandoff(unittest.TestCase):
    def _sequence(self, has_video: bool):
        seq = AtomIntroSequence.__new__(AtomIntroSequence)
        seq._current_screen = _Screen(has_video)
        seq.calls = []
        seq._on_all_done = lambda: seq.calls.append("all_done")
        seq._show_dance = lambda: seq.calls.append("dance")
        return seq

    def test_video_finished_loads_atom(self) -> None:
        seq = self._sequence(has_video=True)
        seq._on_welcome_done()
        self.assertEqual(seq.calls, ["all_done"])

    def test_skipped_video_still_loads_atom(self) -> None:
        """A click calls _cleanup_intro_video(), which clears _has_intro_video.

        The dance screen replays the same clip, so mistaking a skip for
        "no video" is exactly the reported replay.
        """
        seq = AtomIntroSequence.__new__(AtomIntroSequence)
        seq._current_screen = _Screen(has_video=False)
        seq._current_screen._played_intro_video = True
        seq.calls = []
        seq._on_all_done = lambda: seq.calls.append("all_done")
        seq._show_dance = lambda: seq.calls.append("dance")
        seq._on_welcome_done()
        self.assertEqual(seq.calls, ["all_done"])

    def test_no_video_still_shows_the_dance(self) -> None:
        seq = self._sequence(has_video=False)
        seq._on_welcome_done()
        self.assertEqual(seq.calls, ["dance"])

    def test_missing_flag_is_treated_as_no_video(self) -> None:
        seq = AtomIntroSequence.__new__(AtomIntroSequence)
        seq._current_screen = object()
        seq.calls = []
        seq._on_all_done = lambda: seq.calls.append("all_done")
        seq._show_dance = lambda: seq.calls.append("dance")
        seq._on_welcome_done()
        self.assertEqual(seq.calls, ["dance"])


if __name__ == "__main__":
    unittest.main()
