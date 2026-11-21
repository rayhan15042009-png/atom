"""
Atom Dependency Installer — Auto-Setup Module

Checks and installs all required dependencies:
- Python version
- Python packages (pip)
- Ollama binary
- LLM models
- Tesseract OCR
- Assets (3D models, sprites)
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.logger import get_logger

log = get_logger("atom.installer")

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / 'data'
CONFIG_PATH = DATA_DIR / 'config.json'
REQUIREMENTS_PATH = ROOT / 'requirements.txt'

# Common Tesseract install paths for Windows
TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]

TESSERACT_URL = (
    "https://github.com/UB-Mannheim/tesseract/releases/download/"
    "v5.3.3.20231005/tesseract-ocr-w64-setup-5.3.3.20231005.exe"
)

OLLAMA_URLS = {
    "Windows": "https://ollama.com/download/OllamaSetup.exe",
    "Linux": "https://ollama.com/install.sh",
    "Darwin": "https://ollama.com/download/Ollama-darwin.zip",
}

STATUS_ICONS = {
    "ok": "[OK]",
    "warn": "[!!]",
    "fail": "[X]",
    "skip": "[-]",
    "info": "[*]",
}


class DependencyInstaller:
    """Checks and installs all Atom dependencies."""

    def __init__(self):
        self.results: List[Dict[str, str]] = []
        self.config = self._load_config()

    def _load_config(self) -> dict:
        if CONFIG_PATH.exists():
            try:
                return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
            except Exception:
                log.warning("Failed to check Ollama")
        return {}

    def _save_status(self, status: str, label: str, detail: str = ""):
        self.results.append({
            "status": status,
            "label": label,
            "detail": detail,
        })

    def _run_subprocess(self, args: List[str], **kwargs) -> subprocess.CompletedProcess:
        """Run a subprocess with default timeout and error handling."""
        kwargs.setdefault("capture_output", True)
        kwargs.setdefault("text", True)
        kwargs.setdefault("timeout", 120)
        if platform.system() == "Windows":
            kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
        return subprocess.run(args, **kwargs)

    # ── Python version ──────────────────────────────────────────

    def check_python(self) -> bool:
        """Require Python 3.10+."""
        v = sys.version_info
        if v.major >= 3 and v.minor >= 10:
            self._save_status("ok", f"Python {v.major}.{v.minor}.{v.micro} found")
            return True
        self._save_status("fail", f"Python {v.major}.{v.minor} - need 3.10+")
        return False

    # ── Python packages ─────────────────────────────────────────

    def check_packages(self) -> bool:
        """Check and install missing Python packages from requirements.txt."""
        if not REQUIREMENTS_PATH.exists():
            self._save_status("warn", "requirements.txt not found — skipping package check")
            return True

        try:
            result = self._run_subprocess(
                [sys.executable, "-m", "pip", "list", "--format=json"]
            )
            installed = {}
            for pkg in json.loads(result.stdout):
                installed[pkg["name"].lower()] = pkg["version"]
        except Exception as e:
            self._save_status("warn", f"Could not check packages: {e}")
            return True

        missing = []
        requirements = REQUIREMENTS_PATH.read_text(encoding='utf-8').strip().splitlines()
        for line in requirements:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Parse "package==version" or "package>=version" or just "package"
            pkg_name = line.split("==")[0].split(">=")[0].split("<=")[0].split("!=")[0].strip()
            if not pkg_name:
                continue
            if pkg_name.lower() not in installed:
                missing.append(line.split("#")[0].strip())

        if not missing:
            self._save_status("ok", f"All {len(installed)} Python packages installed")
            return True

        self._save_status("info", f"Installing {len(missing)} missing packages...")
        try:
            install_cmd = [sys.executable, "-m", "pip", "install"] + missing
            result = self._run_subprocess(install_cmd, timeout=300)
            if result.returncode == 0:
                self._save_status("ok", f"Installed {len(missing)} packages via pip")
                return True
            self._save_status("warn", f"pip install had issues: {result.stderr[:200]}")
            return True
        except subprocess.TimeoutExpired:
            self._save_status("warn", "Package install timed out — continuing anyway")
            return True
        except Exception as e:
            self._save_status("warn", f"Package install error: {e}")
            return True

    # ── Ollama ──────────────────────────────────────────────────

    def check_ollama(self) -> bool:
        """Check if Ollama is installed and running."""
        try:
            result = self._run_subprocess(["ollama", "--version"])
            version = result.stdout.strip() or result.stderr.strip()
            self._save_status("ok", f"Ollama {version}")
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        self._save_status("info", "Ollama not found — attempting install...")
        return self._install_ollama()

    def _install_ollama(self) -> bool:
        """Download and install Ollama for the current platform."""
        system = platform.system()
        url = OLLAMA_URLS.get(system)
        if not url:
            self._save_status("fail", f"No Ollama installer for {system}")
            return False

        try:
            installer_path = os.path.join(tempfile.gettempdir(), "ollama_installer")
            self._save_status("info", f"Downloading Ollama for {system}...")

            if system == "Windows":
                installer_path += ".exe"
                urllib.request.urlretrieve(url, installer_path)
                self._save_status("info", "Running Ollama installer...")
                self._run_subprocess([installer_path, "/S"], timeout=300)
                # Add to PATH for current session
                ollama_dir = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama")
                if os.path.exists(ollama_dir):
                    os.environ["PATH"] += os.pathsep + ollama_dir
            elif system == "Linux":
                # Pipe install.sh to sh
                self._run_subprocess(
                    f"curl -fsSL {url} | sh",
                    shell=True,
                    timeout=300,
                )
            elif system == "Darwin":
                urllib.request.urlretrieve(url, installer_path + ".zip")
                self._save_status("info", "macOS: please install Ollama manually from ollama.com")
                return False

            # Verify installation
            try:
                result = self._run_subprocess(["ollama", "--version"])
                self._save_status("ok", f"Ollama installed: {result.stdout.strip()}")
                return True
            except Exception:
                self._save_status("warn", "Ollama may need a manual restart")
                return True

        except Exception as e:
            self._save_status("warn", f"Ollama install failed: {e}")
            return False

    def ensure_ollama_running(self) -> bool:
        """Ensure Ollama service is running."""
        try:
            result = self._run_subprocess(
                ["ollama", "list"], timeout=10, capture_output=True
            )
            return result.returncode == 0
        except Exception:
            log.warning("Failed to stop existing Ollama service")

        self._save_status("info", "Starting Ollama service...")
        try:
            if platform.system() == "Windows":
                subprocess.Popen(
                    ["ollama", "serve"],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            time.sleep(3)

            # Verify it started
            try:
                result = self._run_subprocess(["ollama", "list"], timeout=10)
                return result.returncode == 0
            except Exception:
                self._save_status("warn", "Ollama may not have started — will retry later")
                return True
        except Exception as e:
            self._save_status("warn", f"Could not start Ollama: {e}")
            return False

    # ── LLM Model ───────────────────────────────────────────────

    def check_model(self, model_name: str = None) -> bool:
        """Check if an LLM model is pulled. Pulls it if missing.

        Args:
            model_name: Name of the model to check/pull. If None, reads from
                       config using model_chat (primary) or model keys.
        """
        if not model_name:
            model_name = (
                self.config.get("model_chat")
                or self.config.get("model_primary")
                or self.config.get("model", "")
            )
        if not model_name:
            self._save_status("skip", "No LLM model configured")
            return True

        try:
            result = self._run_subprocess(["ollama", "list"], timeout=15)
            if model_name in result.stdout:
                self._save_status("ok", f"Model: {model_name}")
                return True
        except Exception:
            self._save_status("warn", "Could not list Ollama models")
            return True

        self._save_status("info", f"Pulling model {model_name}...")
        try:
            result = self._run_subprocess(
                ["ollama", "pull", model_name],
                timeout=600,  # 10 min for model download
            )
            if result.returncode == 0:
                self._save_status("ok", f"Model pulled: {model_name}")
                return True
            self._save_status("warn", f"Model pull had issues: {result.stderr[:200]}")
            return True
        except subprocess.TimeoutExpired:
            self._save_status("warn", "Model pull timed out (large download) — will retry later")
            return True
        except Exception as e:
            self._save_status("warn", f"Model pull error: {e}")
            return True

    def check_models(self) -> bool:
        """Check and pull BOTH chat AND analysis models from config."""
        chat_model = (
            self.config.get("model_chat")
            or self.config.get("model_primary")
            or self.config.get("model", "")
        )
        analysis_model = self.config.get("model_analysis", "")

        all_ok = True
        if chat_model:
            if not self.check_model(chat_model):
                all_ok = False
        if analysis_model and analysis_model != chat_model:
            if not self.check_model(analysis_model):
                all_ok = False
        return all_ok

    # ── Tesseract ───────────────────────────────────────────────

    def check_tesseract(self) -> bool:
        """Check if Tesseract OCR is installed."""
        # Check explicit env vars
        env_path = os.environ.get("TESSERACT_CMD") or os.environ.get("TESSERACT_PATH")
        if env_path and Path(env_path).exists():
            tess_dir = str(Path(env_path).parent)
            if tess_dir not in os.environ.get("PATH", ""):
                os.environ["PATH"] += os.pathsep + tess_dir
            self._save_status("ok", f"Tesseract at {env_path}")
            return True

        # Check common paths
        for path in TESSERACT_PATHS:
            if Path(path).exists():
                tess_dir = str(Path(path).parent)
                os.environ["PATH"] += os.pathsep + tess_dir
                self._save_status("ok", f"Tesseract at {path}")
                return True

        # Try PATH
        tess_exe = shutil.which("tesseract")
        if tess_exe:
            self._save_status("ok", f"Tesseract found on PATH")
            return True

        if platform.system() != "Windows":
            self._save_status("warn", "Tesseract not found — install manually (apt: tesseract-ocr)")
            return True

        # Download and install
        return self._install_tesseract()

    def _install_tesseract(self) -> bool:
        """Download and install Tesseract on Windows."""
        self._save_status("info", "Tesseract not found — downloading installer...")
        try:
            installer_path = os.path.join(tempfile.gettempdir(), "tesseract-installer.exe")
            urllib.request.urlretrieve(TESSERACT_URL, installer_path)
            self._save_status("info", "Running Tesseract installer...")
            self._run_subprocess([installer_path, "/S"], timeout=300)

            # Add to PATH
            tess_dir = r"C:\Program Files\Tesseract-OCR"
            if Path(tess_dir).exists():
                os.environ["PATH"] += os.pathsep + tess_dir
                self._save_status("ok", "Tesseract installed successfully")
                return True

            self._save_status("warn", "Tesseract installed but not found at expected path")
            return True
        except Exception as e:
            self._save_status("warn", f"Tesseract install failed: {e}")
            return True

    # ── Assets ──────────────────────────────────────────────────

    def check_assets(self) -> bool:
        """Verify required asset files exist."""
        model_path = self.config.get("3d", {}).get("model_path", "assets/models/atom.gltf")
        model_full = ROOT / model_path if not os.path.isabs(model_path) else Path(model_path)

        if model_full.exists():
            self._save_status("ok", f"3D model: {model_full.name}")
        else:
            self._save_status("warn", f"3D model not found: {model_full}")

        return True

    # ── Run all checks ──────────────────────────────────────────

    def run_all(self, pull_model: bool = True) -> List[Dict[str, str]]:
        """
        Run all dependency checks and installations.

        Args:
            pull_model: Whether to pull LLM models if missing

        Returns:
            List of status dicts with keys: status, label, detail
        """
        self.results = []

        self.check_python()
        self.check_packages()
        self.check_ollama()
        self.ensure_ollama_running()
        if pull_model:
            self.check_models()
        self.check_tesseract()
        self.check_assets()

        return self.results

    def print_report(self):
        """Print a formatted dependency report to stdout."""
        print("")
        print("=")
        for r in self.results:
            icon = STATUS_ICONS.get(r["status"], "?")
            print(f"  {icon}  {r['label']}")
        failed = [r for r in self.results if r["status"] == "fail"]
        ok = [r for r in self.results if r["status"] == "ok"]
        print("=")

    def all_ok(self) -> bool:
        """Return True if no critical failures."""
        return not any(r["status"] == "fail" for r in self.results)


def run_setup(pull_model: bool = True) -> DependencyInstaller:
    """
    Convenience function: run all setup checks and print the report.

    Args:
        pull_model: Whether to pull the LLM model

    Returns:
        DependencyInstaller instance with results
    """
    installer = DependencyInstaller()
    installer.run_all(pull_model=pull_model)
    installer.print_report()
    return installer
