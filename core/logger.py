"""
Atom Logging System

Centralized logging with structured JSON output for machine-parseable telemetry.
Writes to both stdout (colored) and a rotating log file in the logs/ directory.
Also provides a structured JSON log file for debugging dashboards.
"""

import json
import logging
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Optional

ROOT = Path(__file__).parent.parent
LOG_DIR = ROOT / 'logs'
LOG_FILE = LOG_DIR / 'atom.log'
STRUCTURED_LOG_FILE = LOG_DIR / 'atom_structured.jsonl'

# Module-level logger cache
_loggers = {}

# ANSI color codes for terminal output
_COLORS = {
    'DEBUG': '\033[36m',      # Cyan
    'INFO': '\033[32m',       # Green
    'WARNING': '\033[33m',    # Yellow
    'ERROR': '\033[31m',      # Red
    'CRITICAL': '\033[35m',   # Magenta
    'RESET': '\033[0m',
}


class ColoredFormatter(logging.Formatter):
    """Add ANSI colors to console log output.

    Colours are applied to a *copy* of the record. A single LogRecord instance
    is handed to every handler in turn, so mutating it here leaked escape codes
    into the handlers that ran afterwards — ``logs/atom_structured.jsonl`` was
    being written with ``"level": "\\u001b[33mWARNING\\u001b[0m"`` and a leading
    ``\\u001b[0m`` on every message, which made the machine-parseable telemetry
    file unparseable.
    """

    def format(self, record):
        record = logging.makeLogRecord(record.__dict__)
        levelname = record.levelname
        color = _COLORS.get(levelname, _COLORS['RESET'])
        record.levelname = f"{color}{levelname}{_COLORS['RESET']}"
        record.msg = f"{_COLORS['RESET']}{record.msg}"
        return super().format(record)


class StructuredFileHandler(logging.Handler):
    """Writes structured JSON log lines (one JSON object per line) for machine parsing."""
    
    def __init__(self, path: Path):
        super().__init__()
        self.path = path
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._file = None
        
    def _ensure_file(self):
        if self._file is None:
            self._file = open(self.path, 'a', encoding='utf-8')
        return self._file
    
    def emit(self, record: logging.LogRecord):
        try:
            entry = {
                'timestamp': datetime.utcfromtimestamp(record.created).isoformat() + 'Z',
                'level': record.levelname,
                'logger': record.name,
                'message': record.getMessage(),
                'module': record.module,
                'function': record.funcName,
                'line': record.lineno,
            }
            if record.exc_info and record.exc_info[1]:
                entry['exception'] = {
                    'type': type(record.exc_info[1]).__name__,
                    'message': str(record.exc_info[1]),
                    'traceback': ''.join(traceback.format_exception(*record.exc_info)),
                }
            fh = self._ensure_file()
            fh.write(json.dumps(entry, default=str) + '\n')
            fh.flush()
        except Exception:
            self.handleError(record)
    
    def close(self):
        if self._file:
            self._file.close()
            self._file = None
        super().close()


def _setup_root_logger():
    """Configure the root Atom logger once."""
    if _setup_root_logger._done:
        return
    _setup_root_logger._done = True

    # Suppress logging module's own error output when handlers fail
    # (e.g. atexit _shutdown after pytest closes streams — harmless I/O error noise)
    logging.raiseExceptions = False

    # Ensure log directory exists
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger('atom')
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    # Do NOT hand records up to the Python root logger. Every entry point that
    # also calls logging.basicConfig() (atom_cli.py does) installs a root
    # handler, and with propagation on each message was emitted twice — once by
    # our console handler and once by theirs, in a different format.
    root.propagate = False

    # File handler — rotating, 5 MB per file, keep 3 backups
    file_handler = RotatingFileHandler(
        str(LOG_FILE),
        maxBytes=5_242_880,  # 5 MB
        backupCount=3,
        encoding='utf-8',
    )
    file_fmt = logging.Formatter(
        '[%(asctime)s] %(levelname)-8s %(name)s — %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    file_handler.setFormatter(file_fmt)
    file_handler.setLevel(logging.DEBUG)
    root.addHandler(file_handler)

    # Console handler — colored, INFO+
    console_handler = logging.StreamHandler(sys.stdout)
    console_fmt = ColoredFormatter(
        '[%(asctime)s] %(levelname)-8s %(message)s',
        datefmt='%H:%M:%S',
    )
    console_handler.setFormatter(console_fmt)
    console_handler.setLevel(logging.INFO)
    root.addHandler(console_handler)

    # Structured JSON handler (DEBUG level, writes to separate file)
    struct_handler = StructuredFileHandler(STRUCTURED_LOG_FILE)
    struct_handler.setLevel(logging.DEBUG)
    root.addHandler(struct_handler)

    root.info("Logger initialized — %s", LOG_FILE)


_setup_root_logger._done = False


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """
    Get a logger for the given module name.

    Usage:
        log = get_logger(__name__)
        log.info("Hello %s", "world")
        log.error("Something broke")

    Returns a logger named 'atom.<name>' for structured filtering.
    """
    _setup_root_logger()
    if name is None:
        name = 'atom'
    elif not name.startswith('atom.'):
        name = f'atom.{name}'

    if name not in _loggers:
        logger = logging.getLogger(name)
        _loggers[name] = logger
    return _loggers[name]


def shutdown_logger():
    """Flush and close all handlers."""
    root = logging.getLogger('atom')
    for handler in root.handlers:
        handler.close()
    root.handlers.clear()
