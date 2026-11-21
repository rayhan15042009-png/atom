"""
Perception — Screen capture, OCR, perceptual hash skip guard, and watcher.

Step 3 of the rebuild: full perception pipeline.

Pipeline:
  capture → downscale → grayscale → Otsu binarization → OCR → perceptual-hash skip
  → watcher (keyword/repeated-pattern detection) → emit signals

Features:
- Region-based capture (active window bounding rect via Win32)
- Full-screen fallback
- Configurable downscaling for low-impact mode
- Otsu binarization (OpenCV preferred, manual threshold fallback)
- Perceptual hash skip guard (imagehash.avg_hash with MD5 fallback)
  - Skip up to 2 identical screens, force OCR every 3rd cycle per spec
- OcrWatcher: keyword filter, repeated pattern detection, character n-gram similarity
- Error pattern detection (Python tracebacks, JS errors, HTTP errors, etc.)
- Mode-aware OCR intervals
- CPU-aware throttling
- Self-speech guard (*tap* patterns filtered)
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import time as _time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import mss
import numpy as np
import psutil
import pytesseract
from PIL import Image
from PyQt6.QtCore import QThread, pyqtSignal

from core.logger import get_logger

log = get_logger("eyes")

# ── Optional imports ─────────────────────────────────────────────────────────

_imagehash_available = False
try:
    import imagehash
    _imagehash_available = True
except ImportError:
    # _log not available yet at this import time
    pass

_cv2_available = False
try:
    import cv2
    _cv2_available = True
except ImportError:
    # _log not available yet at this import time
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════════════

# Keywords that flag OCR data as "interesting"
INTEREST_KEYWORDS = [
    "error", "fail", "exception", "crash", "bug", "wrong", "missing",
    "help", "what is", "how to", "why does",
]

# Koko/Atom name variations
KOKO_NAMES = ["koko", "atom"]

# Max identical screens to skip before forcing OCR
_MAX_SKIP_OCR = 2

# ── Error patterns: (regex_pattern, urgency) ──

ERROR_PATTERNS: list[tuple[str, int]] = [
    # Python tracebacks
    (r"Traceback \(most recent call last\)", 8),
    (r'File ".*?\.py", line \d+', 7),
    (r"[A-Za-z]+Error:", 7),
    (r"[A-Za-z]+Exception:", 7),
    # JavaScript errors
    (r"Uncaught (Type|Reference|Syntax|Range)Error", 8),
    (r"Cannot read property", 7),
    (r"is not defined", 6),
    # HTTP errors
    (r"HTTP \d{3}", 7),
    (r"Error \d{3}:", 7),
    (r"Connection refused", 8),
    (r"Connection timed out", 8),
    # Compile / build errors
    (r"ERROR: Failed to", 8),
    (r"error MSB", 7),
    (r"LINK : fatal error", 8),
    # Permission errors
    (r"Permission denied", 7),
    (r"Access denied", 7),
    # Git conflicts
    (r"CONFLICT", 7),
    (r"Merge conflict", 7),
    (r">>>>>>>", 6),
    (r"<<<<<<<", 6),
    # General failures
    (r"FAILED", 6),
    (r"failed:", 6),
    (r"fatal:", 7),
    (r"panic:", 8),
    (r"abort:", 8),
    (r"segfault", 9),
    (r"stack overflow", 9),
]

# Compile once
_ERROR_COMPILED: list[tuple[re.Pattern, int]] = [
    (re.compile(p), u) for p, u in ERROR_PATTERNS
]


# ═══════════════════════════════════════════════════════════════════════════════
# OcrResult dataclass
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class OcrResult:
    """Structured OCR output with confidence, error detection, and urgency scoring."""
    active_window: str = ""
    app_name: str = ""
    visible_text: str = ""
    detected_errors: list[str] = field(default_factory=list)
    urgency: int = 1
    confidence_avg: float = 0.0
    changed: bool = True
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return {
            "active_window": self.active_window,
            "app_name": self.app_name,
            "visible_text": self.visible_text[:500],
            "detected_errors": self.detected_errors,
            "urgency": self.urgency,
            "confidence_avg": round(self.confidence_avg, 2),
            "changed": self.changed,
            "timestamp": self.timestamp,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Error scanning
# ═══════════════════════════════════════════════════════════════════════════════

def scan_for_errors(text: str) -> tuple[list[str], int]:
    """Scan text for error patterns. Returns (detected_errors, max_urgency)."""
    if not text:
        return [], 0
    detected: list[str] = []
    max_urgency = 0
    for pattern, urgency in _ERROR_COMPILED:
        if pattern.search(text):
            detected.append(pattern.pattern)
            max_urgency = max(max_urgency, urgency)
    return detected, max_urgency


# ═══════════════════════════════════════════════════════════════════════════════
# Perceptual hash helpers
# ═══════════════════════════════════════════════════════════════════════════════

# Module-level state for hash-based OCR skipping
_last_perceptual_hash: Optional[str] = None
_skip_ocr_count: int = 0


def compute_perceptual_hash(img: mss.Screenshot) -> str:
    """Compute perceptual hash using imagehash (avg_hash) with MD5 fallback."""
    global _imagehash_available
    if _imagehash_available:
        try:
            pil_img = Image.frombytes("RGB", img.size, img.rgb)
            small = pil_img.resize((16, 16), Image.LANCZOS)
            return str(imagehash.average_hash(small))
        except Exception as exc:
            log.debug("Perceptual hash fallback to MD5: %s", exc)
    return hashlib.md5(img.rgb[:40000]).hexdigest()


def should_skip_ocr(perceptual_hash: str) -> bool:
    """Check if OCR should be skipped due to unchanged screen content.

    Skips up to _MAX_SKIP_OCR identical screens, then forces OCR
    on the 3rd cycle regardless (per master prompt spec).
    """
    global _last_perceptual_hash, _skip_ocr_count
    if perceptual_hash == _last_perceptual_hash and _skip_ocr_count < _MAX_SKIP_OCR:
        _skip_ocr_count += 1
        return True
    _last_perceptual_hash = perceptual_hash
    _skip_ocr_count = 0
    return False


def reset_hash_cache() -> None:
    """Reset the hash cache (e.g., on mode change)."""
    global _last_perceptual_hash, _skip_ocr_count
    _last_perceptual_hash = None
    _skip_ocr_count = 0


# ═══════════════════════════════════════════════════════════════════════════════
# Image preprocessing
# ═══════════════════════════════════════════════════════════════════════════════

def preprocess_image(image: Image.Image) -> Image.Image:
    """Preprocess image for better OCR: grayscale → Otsu binarization.

    Uses OpenCV for Otsu if available, otherwise falls back to
    a simple mean-threshold binarization.
    """
    # Convert to grayscale
    if image.mode != "L":
        image = image.convert("L")

    img_array = np.array(image)

    if _cv2_available:
        try:
            _, binary = cv2.threshold(img_array, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            return Image.fromarray(binary)
        except Exception:
            log.warning("Image binarization failed, using raw")
            pass

    # Fallback: mean-threshold binarization
    threshold = img_array.mean()
    binary = np.where(img_array > threshold, 255, 0).astype(np.uint8)
    return Image.fromarray(binary)


# ═══════════════════════════════════════════════════════════════════════════════
# Win32 window helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _get_active_window_rect_win32() -> Optional[tuple[int, int, int, int]]:
    """Get bounding rect of active foreground window (Windows only)."""
    try:
        if platform.system() != "Windows":
            return None
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        rect = wintypes.RECT()
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception as exc:
        log.debug("Failed to get active window rect: %s", exc)
    return None


def _get_active_window_title_win32() -> str:
    """Get title of the active foreground window (Windows only)."""
    try:
        if platform.system() != "Windows":
            return ""
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception as exc:
        log.debug("Failed to get window title: %s", exc)
        return ""


# ═══════════════════════════════════════════════════════════════════════════════
# OcrWatcher — lightweight keyword filter
# ═══════════════════════════════════════════════════════════════════════════════

class OcrWatcher:
    """Lightweight keyword filter that decides whether OCR data is interesting.

    Maintains short-term memory of recent OCR passes and detects:
    - Keywords like 'error', 'exception', 'fail'
    - Repeated patterns (same text appearing 3+ times)
    - Mentions of Koko/Atom
    - Similarity-based near-duplicate detection
    - Self-speech guard (filters *tap* patterns)

    Uses character bigram Jaccard similarity for fuzzy matching.
    """

    def __init__(self, max_history: int = 5, similarity_threshold: float = 0.7):
        self.history: deque[dict] = deque(maxlen=max_history)
        self.similarity_threshold = similarity_threshold

    @staticmethod
    def _text_similarity(a: str, b: str) -> float:
        """Compute character bigram Jaccard similarity (0.0–1.0)."""
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        bigrams_a = set(a[i:i+2] for i in range(len(a) - 1))
        bigrams_b = set(b[i:i+2] for i in range(len(b) - 1))
        if not bigrams_a or not bigrams_b:
            return 0.0
        intersection = len(bigrams_a & bigrams_b)
        union = len(bigrams_a | bigrams_b)
        return intersection / max(union, 1)

    def add(self, ocr_data: dict) -> dict:
        """Process OCR data and return enriched dict with 'interesting' flag and 'reason'.

        Args:
            ocr_data: dict with 'ocr_text', 'active_window', 'timestamp'

        Returns:
            Same dict with added 'interesting' (bool) and 'reason' (str or None)
        """
        text = (ocr_data.get("ocr_text") or "").strip().lower()

        result = dict(ocr_data)
        result["interesting"] = False
        result["reason"] = None

        # Store in history
        entry = {
            "text": text,
            "timestamp": ocr_data.get("timestamp", 0),
            "window": ocr_data.get("active_window", ""),
        }
        self.history.append(entry)

        # Skip empty text
        if not text:
            return result

        # ── Self-speech guard ──
        # If OCR text contains *tap*, it's Atom's own speech being read back
        if "*tap*" in text or "atom:" in text[:50].lower():
            return result

        # ── Similarity-based repeated detection ──
        if len(self.history) >= 3:
            recent = list(self.history)[:-1]
            similar_count = 0
            for past_entry in reversed(recent):
                past_text = (past_entry.get("text") or "")[:100]
                current_prefix = text[:100]
                if len(current_prefix) > 10 and len(past_text) > 10:
                    sim = self._text_similarity(current_prefix, past_text)
                    if sim >= self.similarity_threshold:
                        similar_count += 1
                        if similar_count >= 2:
                            result["interesting"] = True
                            result["reason"] = (
                                f"similar repeated: {current_prefix[:40]}... "
                                f"({similar_count + 1}x similar)"
                            )
                            return result

            # Exact repeated pattern (same text 3+ times)
            texts = [entry["text"][:80] for entry in self.history]
            counts = Counter(texts)
            for fragment, count in counts.items():
                if count >= 3 and fragment.strip() and len(fragment) > 10:
                    result["interesting"] = True
                    result["reason"] = f'repeated: "{fragment[:40]}..." ({count}x)'
                    return result

        # ── Keyword match ──
        matched_keywords = [kw for kw in INTEREST_KEYWORDS if kw in text]
        if matched_keywords:
            result["interesting"] = True
            result["reason"] = f"keyword: {', '.join(matched_keywords[:3])}"
            return result

        # ── Name mention ──
        matched_names = [name for name in KOKO_NAMES if name in text]
        if matched_names:
            result["interesting"] = True
            result["reason"] = f"mention: {', '.join(matched_names)}"
            return result

        return result

    def get_recent_context(self, max_chars: int = 300) -> str:
        """Get recent OCR history as a deduplicated summary string."""
        if not self.history:
            return ""
        parts: list[str] = []
        seen_texts: list[str] = []
        for entry in list(self.history)[-3:]:
            text = (entry.get("text") or "")[:100].strip()
            window = entry.get("window", "")
            if text:
                is_dup = False
                for seen in seen_texts:
                    if self._text_similarity(text[:40], seen[:40]) > self.similarity_threshold:
                        is_dup = True
                        break
                if not is_dup:
                    seen_texts.append(text)
                    parts.append(f"[{window}] {text}")
        return "\n".join(parts)[:max_chars]


# ═══════════════════════════════════════════════════════════════════════════════
# Tesseract path detection
# ═══════════════════════════════════════════════════════════════════════════════

def find_tesseract() -> Optional[str]:
    """Locate the Tesseract-OCR executable.

    Checks, in order:
    1. TESSERACT_CMD / TESSERACT_PATH env vars
    2. Common Windows install paths
    3. System PATH

    Sets os.environ['TESSERACT_CMD'] if found so pytesseract can find it.
    """
    # Check env vars first
    env_path = os.environ.get("TESSERACT_CMD") or os.environ.get("TESSERACT_PATH")
    if env_path and Path(env_path).exists():
        os.environ['TESSERACT_CMD'] = str(Path(env_path))
        return str(Path(env_path))

    # Check common install paths
    common = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for candidate in common:
        p = Path(candidate)
        if p.exists():
            os.environ['TESSERACT_CMD'] = str(p)
            pytesseract.pytesseract.tesseract_cmd = str(p)
            return str(p)

    # Check PATH
    which = shutil.which("tesseract")
    if which:
        os.environ['TESSERACT_CMD'] = which
        pytesseract.pytesseract.tesseract_cmd = which
        return which
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# PerceptionWorker — QThread with full capture/OCR loop
# ═══════════════════════════════════════════════════════════════════════════════

class PerceptionWorker(QThread):
    """Background thread for screen capture and OCR.

    Full pipeline: capture → downscale → grayscale → Otsu → OCR →
    perceptual-hash skip → watcher → emit signals.
    """

    ocr_ready = pyqtSignal(dict)          # All OCR results (for context storage)
    ocr_interesting = pyqtSignal(dict)    # Interesting OCR results only (for LLM trigger)
    active_window = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, config: Any = None):
        super().__init__()
        self.config = config if isinstance(config, dict) else (config or {})
        self._running = True
        self.cpu_history: deque[float] = deque(maxlen=6)
        self.watcher = OcrWatcher(max_history=5)

        # Locate Tesseract — also sets os.environ['TESSERACT_CMD'] for downstream use
        tess_path = find_tesseract()
        if tess_path:
            pytesseract.pytesseract.tesseract_cmd = tess_path
            os.environ['TESSERACT_CMD'] = tess_path
            self._tesseract_available = True
        else:
            self._tesseract_available = False

    def run(self):
        """Main loop — capture, preprocess, OCR, hash-check, emit."""
        try:
            with mss.MSS() as sct:
                while self._running:
                    mode = self._get_mode()
                    interval = self._ocr_interval(mode)

                    # Lullaby mode: skip OCR entirely
                    if mode == "lullaby":
                        self._sleep_interruptibly(1.0)
                        continue

                    # CPU check (non-blocking, instant reading)
                    cpu = psutil.cpu_percent(interval=0)
                    self.cpu_history.append(cpu)
                    if self._should_suspend():
                        self._sleep_interruptibly(interval)
                        continue

                    # Tesseract check
                    if not self._tesseract_available:
                        self.error.emit(
                            "Tesseract not found. Install Tesseract-OCR and "
                            "set TESSERACT_CMD or add tesseract.exe to PATH."
                        )
                        self._sleep_interruptibly(interval)
                        continue

                    # Capture and process
                    self._capture_ocr_once(sct)
                    self._sleep_interruptibly(interval)

        except Exception as exc:
            self.error.emit(f"Perception worker stopped: {exc}")

    # ── Config helpers ──

    def _get_mode(self) -> str:
        if isinstance(self.config, dict):
            return self.config.get("mode", "tap")
        return getattr(self.config, "mode", "tap")

    def _ocr_interval(self, mode: str) -> float:
        if isinstance(self.config, dict):
            base = float(self.config.get("ocr_interval_seconds", 8))
            if mode == "stalk":
                base = float(self.config.get("ocr_interval_stalk_seconds", 5))
            elif mode == "whisper":
                base = float(self.config.get("ocr_interval_whisper_seconds", 15))
        else:
            base = float(getattr(self.config, "ocr", {}).get("interval_seconds", 8))
            if mode == "stalk":
                base = float(getattr(self.config, "ocr", {}).get("interval_stalk_seconds", 5))
            elif mode == "whisper":
                base = float(getattr(self.config, "ocr", {}).get("interval_whisper_seconds", 15))
        return max(0.5, base)

    def _get_downscale(self) -> str:
        if isinstance(self.config, dict):
            return self.config.get("low_impact_downscale", "600x400")
        return getattr(self.config, "ocr", {}).get("low_impact_downscale", "600x400")

    def _get_low_impact(self) -> bool:
        if isinstance(self.config, dict):
            return self.config.get("low_impact_mode", True)
        return getattr(self.config, "ocr", {}).get("low_impact_mode", True)

    def _get_cpu_threshold(self) -> int:
        if isinstance(self.config, dict):
            return self.config.get("cpu_threshold_ocr_suspend", 70)
        return getattr(self.config, "ocr", {}).get("cpu_threshold_suspend", 70)

    def _should_suspend(self) -> bool:
        if len(self.cpu_history) < 3:
            return False
        avg = sum(self.cpu_history) / len(self.cpu_history)
        return avg > self._get_cpu_threshold()

    # ── Capture and OCR ──

    def _capture_ocr_once(self, sct: mss.mss) -> None:
        """Single capture → OCR → hash-check → emit cycle."""
        try:
            # Region-based capture: active window
            window_rect = _get_active_window_rect_win32()
            if window_rect:
                left, top, right, bottom = window_rect
                monitor: dict = {
                    "left": left, "top": top,
                    "width": max(100, right - left),
                    "height": max(100, bottom - top),
                }
            else:
                monitor = sct.monitors[1]  # fallback to full screen

            img = sct.grab(monitor)

            # Perceptual hash skip guard
            phash = compute_perceptual_hash(img)
            if should_skip_ocr(phash):
                title = _get_active_window_title_win32()
                self.active_window.emit(title)
                return

            # Convert to PIL
            image = Image.frombytes("RGB", img.size, img.rgb)

            # Downscale in low-impact mode
            if self._get_low_impact():
                scale_str = self._get_downscale()
                try:
                    w, h = map(int, scale_str.split("x"))
                    image = image.resize((w, h), Image.LANCZOS)
                except (ValueError, AttributeError):
                    pass

            # Preprocess: grayscale + Otsu
            image = preprocess_image(image)

            # OCR
            try:
                text = pytesseract.image_to_string(image, timeout=8)
            except RuntimeError as exc:
                self.error.emit(f"OCR timed out: {exc}")
                return
            except Exception as exc:
                self.error.emit(f"OCR failed: {exc}")
                return

            title = _get_active_window_title_win32()
            self.active_window.emit(title)

            # Build structured result
            detected_errors, urgency = scan_for_errors(text.strip())

            # Package OCR data
            ocr_data: dict = {
                "active_window": title,
                "ocr_text": text.strip(),
                "timestamp": _time.time(),
            }

            # Always emit raw OCR data for context storage
            self.ocr_ready.emit(ocr_data)

            # Run through watcher
            enriched = self.watcher.add(ocr_data)
            if enriched.get("interesting"):
                log.info(
                    "Watcher triggered: %s (urgency=%d)",
                    enriched.get("reason"), urgency,
                )
                enriched["ocr_result"] = OcrResult(
                    active_window=title,
                    app_name=title.split(" - ")[-1] if " - " in title else title,
                    visible_text=text.strip(),
                    detected_errors=detected_errors,
                    urgency=urgency,
                    confidence_avg=0.0,
                    changed=True,
                    timestamp=_time.time(),
                ).to_dict()
                self.ocr_interesting.emit(enriched)

        except Exception as exc:
            self.error.emit(f"OCR error: {exc}")

    def _sleep_interruptibly(self, seconds: float) -> None:
        """Sleep in small chunks so the thread can be stopped promptly."""
        remaining_ms = int(seconds * 1000)
        while self._running and remaining_ms > 0:
            chunk = min(remaining_ms, 200)  # 200ms chunks = fewer wakeups
            self.msleep(chunk)
            remaining_ms -= chunk

    def stop(self) -> None:
        """Stop the thread gracefully."""
        self._running = False
        self.quit()
        if self.isRunning() and not self.wait(1500):
            self.terminate()
            self.wait(500)
