"""
Tests for the centralized logger (core/logger.py).

Regression guard — every log line used to be printed TWICE in two different
formats. ``core/logger.py`` attaches its handlers to the ``atom`` logger, while
entry points that also call ``logging.basicConfig()`` (``atom_cli.py`` does)
attach a handler to the Python *root* logger. With propagation left at its
default, each record was handled by both and emitted twice.
"""

from __future__ import annotations

import logging
import unittest

from core.logger import ColoredFormatter, get_logger


class _CountingHandler(logging.Handler):
    """Collects every record it is asked to handle."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        self.records.append(record)


class TestAtomLoggerWiring(unittest.TestCase):
    def setUp(self) -> None:
        # Touch a logger so _setup_root_logger() has definitely run.
        get_logger("logger_test")

    def test_atom_logger_does_not_propagate(self) -> None:
        """The fix itself: 'atom' must not hand records up to root."""
        self.assertFalse(logging.getLogger("atom").propagate)

    def test_record_reaches_atom_handlers_exactly_once(self) -> None:
        log = get_logger("logger_test")
        counter = _CountingHandler()
        atom_logger = logging.getLogger("atom")
        atom_logger.addHandler(counter)
        try:
            log.info("single emission probe")
        finally:
            atom_logger.removeHandler(counter)
        self.assertEqual(len(counter.records), 1)

    def test_root_handler_never_sees_atom_records(self) -> None:
        """Simulates atom_cli.py's logging.basicConfig() root handler.

        Before the fix this assertion failed — the basicConfig handler received
        the record too, which is what produced the duplicate console lines.
        """
        log = get_logger("logger_test")
        root = logging.getLogger()
        counter = _CountingHandler()
        previous_level = root.level
        root.addHandler(counter)
        root.setLevel(logging.DEBUG)
        try:
            log.info("must not reach the root handler")
        finally:
            root.removeHandler(counter)
            root.setLevel(previous_level)
        self.assertEqual(counter.records, [])

    def test_printing_still_works_after_the_fix(self) -> None:
        """propagate=False must not silence logging altogether."""
        log = get_logger("logger_test")
        counter = _CountingHandler()
        atom_logger = logging.getLogger("atom")
        atom_logger.addHandler(counter)
        try:
            log.warning("still audible")
        finally:
            atom_logger.removeHandler(counter)
        self.assertEqual(len(counter.records), 1)
        self.assertEqual(counter.records[0].levelname, "WARNING")


class TestColoredFormatterDoesNotMutateRecords(unittest.TestCase):
    """The console handler must not leak ANSI colours into other handlers.

    All handlers receive the *same* LogRecord object, so mutating it in the
    console formatter corrupted the structured JSON file (which is written by a
    handler that runs afterwards).
    """

    def _record(self) -> logging.LogRecord:
        return logging.LogRecord(
            name="atom.probe",
            level=logging.WARNING,
            pathname=__file__,
            lineno=1,
            msg="clean message",
            args=(),
            exc_info=None,
        )

    def test_format_leaves_the_original_record_untouched(self) -> None:
        record = self._record()
        ColoredFormatter(
            "[%(asctime)s] %(levelname)-8s %(message)s", datefmt="%H:%M:%S"
        ).format(record)
        self.assertEqual(record.levelname, "WARNING")
        self.assertEqual(record.msg, "clean message")

    def test_colour_is_still_applied_to_the_rendered_output(self) -> None:
        rendered = ColoredFormatter(
            "[%(asctime)s] %(levelname)-8s %(message)s", datefmt="%H:%M:%S"
        ).format(self._record())
        self.assertIn("\x1b[33m", rendered)  # yellow WARNING
        self.assertIn("clean message", rendered)

    def test_no_escape_codes_reach_a_later_handler(self) -> None:
        """End-to-end: a handler that runs after the console sees clean values."""
        log = get_logger("logger_test")
        counter = _CountingHandler()
        atom_logger = logging.getLogger("atom")
        atom_logger.addHandler(counter)
        try:
            log.warning("structured probe")
        finally:
            atom_logger.removeHandler(counter)
        self.assertEqual(len(counter.records), 1)
        seen = counter.records[0]
        self.assertEqual(seen.levelname, "WARNING")
        self.assertEqual(seen.getMessage(), "structured probe")
        self.assertNotIn("\x1b", seen.levelname)
        self.assertNotIn("\x1b", seen.getMessage())


class TestLoggerNamespacing(unittest.TestCase):
    def test_namespaced_under_atom(self) -> None:
        self.assertEqual(get_logger("brain").name, "atom.brain")
        self.assertEqual(get_logger("sandbox").name, "atom.sandbox")

    def test_already_prefixed_name_is_not_double_prefixed(self) -> None:
        self.assertEqual(get_logger("atom.brain").name, "atom.brain")

    def test_bare_logger_is_the_atom_root(self) -> None:
        self.assertEqual(get_logger().name, "atom")


if __name__ == "__main__":
    unittest.main()
