"""Set Atom up in one step: virtual environment, packages, data files.

Idempotent and safe to re-run. It never overwrites an existing ``.env`` or the
``data/`` files, needs no administrator rights, and does not download models.

    python setup_atom.py            # packages, Chromium, data dir, checks
    python setup_atom.py --full     # also install Ollama/Tesseract + pull a model
    python setup_atom.py --no-playwright
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
MIN_PYTHON = (3, 10)
RECOMMENDED_PYTHON = (3, 11)

#: Imported in the venv to prove the GUI stack really installed.
IMPORT_CHECK = (
    "import PyQt6.QtWidgets, cv2, PIL, mss, pytesseract, pyttsx3, psutil, playwright"
)


def say(step: str, message: str = "") -> None:
    print(f"[setup] {step:<22} {message}".rstrip(), flush=True)


def venv_python() -> Path:
    """Path to the interpreter inside .venv (Windows layout first)."""
    win = VENV / "Scripts" / "python.exe"
    if win.exists():
        return win
    return VENV / "bin" / "python"


def run(cmd: list[str], label: str, timeout: int | None = None) -> bool:
    """Run a command, show its output only when it fails."""
    try:
        result = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout
        )
    except FileNotFoundError:
        say(label, f"command not found: {cmd[0]}")
        return False
    except subprocess.TimeoutExpired:
        say(label, "timed out (continuing)")
        return True
    if result.returncode != 0:
        say(label, f"failed (exit {result.returncode})")
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-6:]
        for line in tail:
            print(f"          {line}")
        return False
    say(label, "ok")
    return True


def check_python() -> bool:
    version = sys.version_info
    text = f"{version.major}.{version.minor}.{version.micro}"
    if (version.major, version.minor) < MIN_PYTHON:
        say("Python", f"{text} — Atom needs {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+")
        print("\nInstall Python 3.11 from https://python.org/downloads and tick")
        print("'Add python.exe to PATH', then run this again.")
        return False
    note = "ok" if (version.major, version.minor) >= RECOMMENDED_PYTHON else "ok (3.11 recommended)"
    say("Python", f"{text} — {note}")
    return True


def make_venv() -> bool:
    if venv_python().exists():
        say("Virtual env", "already exists (.venv)")
        return True
    say("Virtual env", "creating .venv ...")
    return run([sys.executable, "-m", "venv", str(VENV)], "Virtual env")


def install_packages() -> bool:
    py = venv_python()
    requirements = ROOT / "requirements.txt"
    if not requirements.exists():
        say("Packages", "requirements.txt is missing — cannot install")
        return False
    run([str(py), "-m", "pip", "install", "--upgrade", "pip", "--quiet"], "pip", timeout=300)
    say("Packages", "installing requirements.txt (a few minutes) ...")
    return run(
        [str(py), "-m", "pip", "install", "-r", str(requirements), "--quiet"],
        "Packages",
        timeout=1800,
    )


def install_playwright(with_browsers: bool) -> bool:
    if not with_browsers:
        say("Chromium", "skipped (--no-playwright)")
        return True
    say("Chromium", "installing Playwright Chromium (~130 MB) ...")
    ok = run(
        [str(venv_python()), "-m", "playwright", "install", "chromium"],
        "Chromium",
        timeout=1800,
    )
    if not ok:
        say("Chromium", "Browser AI needs it; everything else works without it")
    return True  # never fatal


def prepare_data() -> bool:
    data = ROOT / "data"
    data.mkdir(exist_ok=True)
    example = data / ".env.example"
    env_file = ROOT / ".env"
    if env_file.exists():
        say("Settings", ".env kept as it is")
    elif example.exists():
        shutil.copyfile(example, env_file)
        say("Settings", "created .env from data/.env.example")
    else:
        say("Settings", "no data/.env.example found")
    say("Data folder", "ready (config and memory are created on first run)")
    return True


def verify_imports() -> bool:
    ok = run([str(venv_python()), "-c", IMPORT_CHECK], "Imports")
    if not ok:
        print("          One of the packages did not import. Re-running setup")
        print("          usually fixes a half-finished pip install.")
    return ok


def report_optional_tools() -> None:
    ollama = shutil.which("ollama")
    say("Ollama", f"found at {ollama}" if ollama else "not installed (optional, for offline answers)")

    tesseract = shutil.which("tesseract")
    if not tesseract:
        for candidate in (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ):
            if Path(candidate).exists():
                tesseract = candidate
                break
    say("Tesseract", tesseract if tesseract else "not installed (optional, screen reading)")

    env_file = ROOT / ".env"
    has_key = False
    if env_file.exists():
        text = env_file.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY=") and len(line.split("=", 1)[1].strip()) > 8:
                has_key = True
    say(
        "Gemini key",
        "set in .env" if has_key else "not set — add GEMINI_API_KEY to .env, or use Ollama",
    )


def full_install() -> bool:
    """Let Atom's own installer fetch Ollama, Tesseract and the model."""
    say("System tools", "running Atom's dependency installer (may take a while) ...")
    return run(
        [
            str(venv_python()),
            "-c",
            "from core.installer import run_setup; run_setup(pull_model=True)",
        ],
        "System tools",
        timeout=5400,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Set Atom up on this machine.")
    parser.add_argument(
        "--full",
        action="store_true",
        help="also install Ollama/Tesseract and pull the default local model (~2 GB)",
    )
    parser.add_argument(
        "--no-playwright",
        action="store_true",
        help="skip the Chromium download (Browser AI will not work)",
    )
    args = parser.parse_args()

    os.chdir(ROOT)
    print("=" * 66)
    print("  Atom setup")
    print(f"  {ROOT}")
    print("=" * 66)

    if not check_python():
        return 1
    if not make_venv():
        return 1
    if not install_packages():
        print("\n[setup] pip could not install everything. Common causes:")
        print("        - no internet, or a proxy blocking pypi.org")
        print("        - a Python version without wheels for opencv/pyqt6")
        return 1
    install_playwright(with_browsers=not args.no_playwright)
    prepare_data()
    verify_imports()
    if args.full:
        full_install()
    report_optional_tools()

    print("-" * 66)
    print("  Done. Start Atom with:  run_atom.bat")
    print("  (or: .venv\\Scripts\\python atom.py)")
    print("-" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
