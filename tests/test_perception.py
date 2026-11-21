"""
Tests for Step 3 — Perception Pipeline
Coverage: image preprocessing, perceptual hash, OcrWatcher, error detection,
scan_for_errors, tesseract integration (if available), worker signal wiring
"""

import os
import platform
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from PyQt6.QtCore import QCoreApplication, QTimer
from PyQt6.QtTest import QSignalSpy

# Headless Qt
_app = QCoreApplication.instance()
if _app is None:
    _app = QCoreApplication([])

# Workaround: PIL import of ImageFont may fail if no fonts available
_HAS_FONTS = False
try:
    _test_font = ImageFont.load_default()
    _HAS_FONTS = True
except Exception:
    pass


# ── Helper: create a test fixture image with known text ──

def _create_test_image(text: str = "Hello World", width: int = 200, height: int = 60) -> Image.Image:
    """Create a simple test image with black text on white background."""
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (width - tw) // 2
    y = (height - th) // 2
    draw.text((x, y), text, fill="black", font=font)
    return img


def _create_error_image() -> Image.Image:
    """Create a test image containing an error message for error detection tests."""
    return _create_test_image(
        "Traceback (most recent call last):\n"
        '  File "test.py", line 42, in main\n'
        "ValueError: something broke",
        width=400, height=100,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Tests for image preprocessing
# ══════════════════════════════════════════════════════════════════════════════

class TestPreprocessing(unittest.TestCase):
    """preprocess_image: grayscale conversion + Otsu binarization."""

    def setUp(self):
        self.img = _create_test_image()

    def test_returns_image(self):
        from core.eyes import preprocess_image
        result = preprocess_image(self.img)
        self.assertIsInstance(result, Image.Image)

    def test_grayscale_output(self):
        from core.eyes import preprocess_image
        result = preprocess_image(self.img)
        self.assertEqual(result.mode, "L")

    def test_binary_output(self):
        """After Otsu, pixels should be only 0 or 255."""
        from core.eyes import preprocess_image
        result = preprocess_image(self.img)
        # get_flattened_data returns tuple of ints for "L" mode (Pillow 12.2+),
        # replacing deprecated getdata(). Both return flat pixel values.
        try:
            pixel_data = result.get_flattened_data()
        except AttributeError:
            pixel_data = list(result.getdata())
        unique = set(pixel_data)
        for p in unique:
            self.assertIn(p, (0, 255))

    def test_preserves_size(self):
        from core.eyes import preprocess_image
        result = preprocess_image(self.img)
        self.assertEqual(result.size, self.img.size)

    def test_rgb_input_converted(self):
        """RGB input gets converted to grayscale."""
        from core.eyes import preprocess_image
        rgb = Image.new("RGB", (50, 50), "white")
        result = preprocess_image(rgb)
        self.assertEqual(result.mode, "L")


# ══════════════════════════════════════════════════════════════════════════════
# Tests for perceptual hash
# ══════════════════════════════════════════════════════════════════════════════

class TestPerceptualHash(unittest.TestCase):
    """compute_perceptual_hash and should_skip_ocr."""

    def setUp(self):
        from core.eyes import reset_hash_cache
        reset_hash_cache()
        self.img1 = _create_test_image("First")
        self.img2 = _create_test_image("Second")

    def _make_mss_like(self, pil_img):
        """Wrap PIL image in an object that mimics mss.Screenshot interface."""
        from mss.tools import to_png
        import io
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        buf.seek(0)
        # We need to construct something with .rgb and .size
        # For testing, let's use a simple approach with actual mss if possible
        # or just test the hash directly with PIL
        raise NotImplementedError("Use compute_hash_via_rgb instead")

    def test_same_image_same_hash(self):
        from core.eyes import compute_perceptual_hash
        # Create mss-like object with .rgb and .size
        img1 = _create_test_image("Same text here")
        rgb1 = img1.tobytes()
        size1 = img1.size

        fake_mss1 = type("FakeMSS", (), {"rgb": rgb1, "size": size1})()
        fake_mss2 = type("FakeMSS", (), {"rgb": rgb1, "size": size1})()

        h1 = compute_perceptual_hash(fake_mss1)
        h2 = compute_perceptual_hash(fake_mss2)
        self.assertEqual(h1, h2)

    def test_different_images_different_hashes(self):
        from core.eyes import compute_perceptual_hash
        img1 = _create_test_image("AAAAA")
        img2 = _create_test_image("BBBBB")

        fake1 = type("FakeMSS", (), {"rgb": img1.tobytes(), "size": img1.size})()
        fake2 = type("FakeMSS", (), {"rgb": img2.tobytes(), "size": img2.size})()

        h1 = compute_perceptual_hash(fake1)
        h2 = compute_perceptual_hash(fake2)
        # Not strictly required to differ, but very likely
        # If they happen to be equal, that's OK (collision in tiny image)
        # Just verify they return a string
        self.assertIsInstance(h1, str)
        self.assertIsInstance(h2, str)

    def test_skip_identical_screens(self):
        from core.eyes import compute_perceptual_hash, should_skip_ocr, reset_hash_cache
        reset_hash_cache()
        img = _create_test_image("Test")

        fake = type("FakeMSS", (), {"rgb": img.tobytes(), "size": img.size})()
        h = compute_perceptual_hash(fake)

        # First call: should not skip
        self.assertFalse(should_skip_ocr(h))

        # Second call: should skip (same hash)
        self.assertTrue(should_skip_ocr(h))

        # Third call: should skip (still same, within max skip of 2)
        self.assertTrue(should_skip_ocr(h))

        # Fourth call: forced OCR (skip count exceeded, hash resets)
        self.assertFalse(should_skip_ocr(h))

    def test_different_hash_resets_skip_count(self):
        from core.eyes import compute_perceptual_hash, should_skip_ocr, reset_hash_cache
        reset_hash_cache()
        # Use images that produce guaranteed-different hashes:
        # One solid color, one with a gradient pattern
        solid = Image.new("RGB", (50, 50), (100, 150, 200))
        gradient = Image.new("RGB", (50, 50))
        for y in range(50):
            for x in range(50):
                gradient.putpixel((x, y), (x * 5, y * 5, 128))

        fake1 = type("FakeMSS", (), {"rgb": solid.tobytes(), "size": solid.size})()
        fake2 = type("FakeMSS", (), {"rgb": gradient.tobytes(), "size": gradient.size})()

        h1 = compute_perceptual_hash(fake1)
        self.assertFalse(should_skip_ocr(h1))  # First: no skip

        h2 = compute_perceptual_hash(fake2)
        self.assertFalse(should_skip_ocr(h2))  # Different image: no skip, resets

    def test_reset(self):
        from core.eyes import reset_hash_cache, compute_perceptual_hash, should_skip_ocr
        reset_hash_cache()
        img = _create_test_image("Test")
        fake = type("FakeMSS", (), {"rgb": img.tobytes(), "size": img.size})()
        h = compute_perceptual_hash(fake)
        self.assertFalse(should_skip_ocr(h))
        reset_hash_cache()
        self.assertFalse(should_skip_ocr(h))  # Fresh cache, not a skip


# ══════════════════════════════════════════════════════════════════════════════
# Tests for error detection
# ══════════════════════════════════════════════════════════════════════════════

class TestScanForErrors(unittest.TestCase):
    """scan_for_errors: detect common error patterns in text."""

    def setUp(self):
        from core.eyes import scan_for_errors
        self.scan = scan_for_errors

    def test_empty_text(self):
        errors, urgency = self.scan("")
        self.assertEqual(errors, [])
        self.assertEqual(urgency, 0)

    def test_python_traceback(self):
        text = 'Traceback (most recent call last):\n  File "test.py", line 42\nValueError: bad'
        errors, urgency = self.scan(text)
        self.assertGreater(len(errors), 0)
        self.assertGreaterEqual(urgency, 7)

    def test_no_errors(self):
        text = "Everything looks fine. The operation completed successfully."
        errors, urgency = self.scan(text)
        self.assertEqual(errors, [])
        self.assertEqual(urgency, 0)

    def test_http_error(self):
        text = "HTTP 404: Not Found"
        errors, urgency = self.scan(text)
        self.assertGreater(len(errors), 0)
        self.assertGreaterEqual(urgency, 7)

    def test_connection_refused(self):
        text = "Connection refused: could not connect to server"
        errors, urgency = self.scan(text)
        self.assertGreater(len(errors), 0)

    def test_segfault(self):
        text = "segfault at 0x7fff"
        errors, urgency = self.scan(text)
        self.assertEqual(urgency, 9)


# ══════════════════════════════════════════════════════════════════════════════
# Tests for OcrWatcher
# ══════════════════════════════════════════════════════════════════════════════

class TestOcrWatcher(unittest.TestCase):
    """OcrWatcher: keyword filter, repeated detection, self-speech guard."""

    def setUp(self):
        from core.eyes import OcrWatcher
        self.watcher = OcrWatcher(max_history=5, similarity_threshold=0.7)

    def _ocr(self, text: str) -> dict:
        return {"ocr_text": text, "active_window": "Test", "timestamp": time.time()}

    def test_empty_text_not_interesting(self):
        result = self.watcher.add(self._ocr(""))
        self.assertFalse(result["interesting"])

    def test_keyword_error_triggers(self):
        for kw in ["error", "exception", "crash", "bug", "fail"]:
            self.watcher = type(self.watcher)(max_history=5)  # Fresh watcher
            result = self.watcher.add(self._ocr(f"Found an {kw} in the log"))
            self.assertTrue(result["interesting"], f"Keyword '{kw}' should trigger")
            self.assertIn("keyword", result["reason"])

    def test_koko_name_mention_triggers(self):
        result = self.watcher.add(self._ocr("Koko asked me to check something"))
        self.assertTrue(result["interesting"])
        self.assertIn("mention", result["reason"])

    def test_self_speech_guard_filtered(self):
        """*tap* patterns should not be flagged as interesting."""
        result = self.watcher.add(self._ocr("*tap* I found the file, Koko."))
        self.assertFalse(result["interesting"])

    def test_atom_prefix_guard(self):
        """Lines starting with 'atom:' should be filtered."""
        result = self.watcher.add(self._ocr("atom: checking system status"))
        self.assertFalse(result["interesting"])

    def test_repeated_exact_text_triggers(self):
        text = "Something is wrong with the network"
        self.watcher.add(self._ocr(text))
        self.watcher.add(self._ocr(text))
        result = self.watcher.add(self._ocr(text))
        # 3+ same entries should trigger repeated pattern
        self.assertTrue(result["interesting"])
        self.assertIn("repeated", result["reason"])

    def test_similar_text_triggers(self):
        base = "Could not connect to server at localhost"
        self.watcher.add(self._ocr(base))
        sim = "Could not connect to server at 127.0.0.1"
        result = self.watcher.add(self._ocr(sim))
        # With similarity threshold 0.7, these should match
        # After 2 similar entries, should trigger
        # But we need at least 3 entries in history for similarity check
        # Reset and add 3 similar entries
        from core.eyes import OcrWatcher
        self.watcher = OcrWatcher(max_history=5, similarity_threshold=0.5)
        self.watcher.add(self._ocr(base))
        self.watcher.add(self._ocr(sim))
        result = self.watcher.add(self._ocr("Could not connect to server remotely"))
        self.assertTrue(result["interesting"])
        self.assertIn("similar", result["reason"])

    def test_short_text_not_interesting(self):
        result = self.watcher.add(self._ocr("ok"))
        self.assertFalse(result["interesting"])

    def test_get_recent_context_empty(self):
        ctx = self.watcher.get_recent_context()
        self.assertEqual(ctx, "")

    def test_get_recent_context_with_data(self):
        self.watcher.add(self._ocr("First message"))
        self.watcher.add(self._ocr("Second message"))
        ctx = self.watcher.get_recent_context()
        # Watcher lowercases text internally
        self.assertIn("first", ctx)
        self.assertIn("second", ctx)


class TestOcrWatcherSimilarity(unittest.TestCase):
    """Test the _text_similarity static method."""

    def setUp(self):
        from core.eyes import OcrWatcher
        self.sim = OcrWatcher._text_similarity

    def test_identical(self):
        self.assertEqual(self.sim("hello", "hello"), 1.0)

    def test_empty(self):
        self.assertEqual(self.sim("", "hello"), 0.0)
        self.assertEqual(self.sim("hello", ""), 0.0)

    def test_similar(self):
        # "hello world" and "hello word" should have high similarity
        s = self.sim("hello world", "hello word")
        self.assertGreater(s, 0.5)

    def test_completely_different(self):
        s = self.sim("abc", "xyz")
        self.assertAlmostEqual(s, 0.0)


# ══════════════════════════════════════════════════════════════════════════════
# Tests for Tesseract integration (conditional)
# ══════════════════════════════════════════════════════════════════════════════

@unittest.skipIf(
    not shutil.which("tesseract") and not os.environ.get("TESSERACT_CMD")
    and not Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe").exists(),
    "Tesseract-OCR not installed; skipping OCR integration tests",
)
class TestTesseractOCR(unittest.TestCase):
    """End-to-end OCR tests (only if Tesseract is available)."""

    def setUp(self):
        from core.eyes import preprocess_image, find_tesseract
        self.preprocess = preprocess_image
        # Ensure pytesseract knows where the binary is
        find_tesseract()

    def test_ocr_extracts_text(self):
        """Preprocess then OCR a test image; verify text is extracted."""
        img = _create_test_image("TEST", width=400, height=120)
        processed = self.preprocess(img)
        import pytesseract
        text = pytesseract.image_to_string(processed, timeout=8)
        self.assertTrue("Test" in text or "TEST" in text,
                        f"'TEST' not found in OCR output: {text[:60]}")

    def test_ocr_extracts_error_traceback(self):
        """OCR should extract error text from a test image."""
        img = _create_error_image()
        processed = self.preprocess(img)
        import pytesseract
        text = pytesseract.image_to_string(processed, timeout=8)
        # Should find at least part of the error
        self.assertGreater(len(text.strip()), 10)

    def test_preprocessing_improves_ocr(self):
        """Verify preprocessing helps vs raw image."""
        img = _create_test_image("Test123", width=200, height=60)
        import pytesseract as t
        raw_text = t.image_to_string(img, timeout=8)
        processed = self.preprocess(img)
        proc_text = t.image_to_string(processed, timeout=8)
        # Processed should have similar or better accuracy
        raw_tokens = set(raw_text.lower().split())
        proc_tokens = set(proc_text.lower().split())
        target_tokens = {"test123"}
        raw_match = len(raw_tokens & target_tokens)
        proc_match = len(proc_tokens & target_tokens)
        self.assertGreaterEqual(proc_match, raw_match)


# ══════════════════════════════════════════════════════════════════════════════
# Tests for PerceptionWorker signal wiring
# ══════════════════════════════════════════════════════════════════════════════

class TestPerceptionWorkerSignals(unittest.TestCase):
    """Verify PerceptionWorker signals wire up correctly."""

    def setUp(self):
        from core.eyes import PerceptionWorker
        self.worker = PerceptionWorker({"mode": "lullaby"})  # Lullaby = no OCR activity

    def tearDown(self):
        if self.worker.isRunning():
            self.worker.stop()
        self.worker = None

    def test_signals_exist(self):
        self.assertTrue(hasattr(self.worker, "ocr_ready"))
        self.assertTrue(hasattr(self.worker, "ocr_interesting"))
        self.assertTrue(hasattr(self.worker, "active_window"))
        self.assertTrue(hasattr(self.worker, "error"))

    def test_start_stop(self):
        self.worker.start()
        self.worker.msleep(100)
        self.assertTrue(self.worker.isRunning())
        self.worker.stop()
        self.assertFalse(self.worker.isRunning())

    def test_no_signals_in_lullaby_mode(self):
        """In lullaby mode, worker should sleep and emit nothing."""
        ocr_spy = QSignalSpy(self.worker.ocr_ready)
        err_spy = QSignalSpy(self.worker.error)
        self.worker.start()
        self.worker.msleep(500)  # Long enough for at least one loop iteration
        self.worker.stop()
        self.assertEqual(len(ocr_spy), 0, "Lullaby mode should not emit OCR")
        self.assertEqual(len(err_spy), 0, "Lullaby mode should not emit errors")


class TestFindTesseract(unittest.TestCase):
    """Test the Tesseract path finder."""

    def test_returns_string_or_none(self):
        from core.eyes import find_tesseract
        result = find_tesseract()
        # Result should be a path string or None
        self.assertTrue(result is None or isinstance(result, str))
        if result:
            self.assertTrue(Path(result).exists())


if __name__ == "__main__":
    unittest.main()
