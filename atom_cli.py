"""
Atom CLI — Chat with Atom directly from the terminal.

Usage:
    python atom_cli.py                   # Interactive chat mode
    python atom_cli.py --one-shot "hello"  # Single query mode

No Qt dependency needed — uses the same BrainThread for LLM interaction
but communicates via stdin/stdout instead of a GUI.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

# ── Force UTF-8 on Windows to avoid cp1252 UnicodeEncodeError ──
if sys.platform == 'win32' and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# ── Phase 0: Self-healing — auto-install Python packages if missing ──
# The CLI needs PyQt6 for BrainThread. If missing, auto-install then restart.
# Only restarts on SUCCESS to avoid infinite loops.
try:
    import PyQt6
except ImportError:
    print("=" * 55)
    print("  ATOM CLI — Auto-Installer")
    print("  PyQt6 not found. Installing requirements...")
    print("=" * 55)
    _install_ok = False
    try:
        _req_path = Path(__file__).parent / "requirements.txt"
        _ret = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(_req_path)],
            timeout=300
        )
        _install_ok = _ret.returncode == 0
    except Exception as _exc:
        print(f"[Atom] Auto-install error: {_exc}")
    
    # Verify pip install actually made PyQt6 importable before restarting
    if _install_ok:
        _verify = subprocess.run(
            [sys.executable, "-c", "import PyQt6; print('OK')"],
            capture_output=True, timeout=10
        )
        if _verify.returncode == 0 and b'OK' in _verify.stdout:
            print("[Atom] Dependencies installed! Restarting...")
            subprocess.run([sys.executable] + sys.argv)
        else:
            print("[Atom] Pip completed but PyQt6 still not importable.")
            print("[Atom] Try: restart your terminal, or check Python environment.")
    else:
        print()
        print("=" * 55)
        print("  Failed to install dependencies.")
        print("  Please install manually:")
        print(f"    {sys.executable} -m pip install -r requirements.txt")
        print("=" * 55)
    sys.exit(0)

ROOT = Path(__file__).parent
DATA_DIR = ROOT / 'data'
CONFIG_PATH = DATA_DIR / 'config.json'
MEMORY_PATH = DATA_DIR / 'memory.json'

# ── Minimal logger for CLI ──
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger('atom.cli')


def load_json(path, default):
    if not path.exists():
        path.write_text(json.dumps(default, indent=2), encoding='utf-8')
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def save_memory(memory):
    MEMORY_PATH.write_text(json.dumps(memory, indent=2), encoding='utf-8')


# Chat history lives in core.chat_history so the GUI overlay and the CLI share
# one file, one format and one cap (the overlay reads it on launch to restore
# the panel; previously it never did).
from core.chat_history import load_chat_history, save_chat_history  # noqa: E402


def clear_chat_history():
    """Forget the persisted transcript."""
    save_chat_history([])


def start_brain_thread(config, memory):
    """Initialize and start the BrainThread without Qt.
    
    Uses DirectConnection for signals since there's no Qt event loop in CLI mode.
    """
    from PyQt6.QtCore import Qt
    from core.brain import BrainThread

    brain = BrainThread(config, memory)
    
    # Thread-safe response/error collection
    response_container = {'response': None, 'error': None, 'ready': threading.Event()}

    def on_response(text):
        response_container['response'] = text
        response_container['ready'].set()

    def on_error(msg):
        response_container['error'] = msg
        response_container['ready'].set()

    # Use DirectConnection since there's no Qt event loop in CLI mode
    brain.response_ready.connect(on_response, Qt.ConnectionType.DirectConnection)
    brain.error.connect(on_error, Qt.ConnectionType.DirectConnection)
    brain.start()

    return brain, response_container


def ask_brain(brain, response_container, text, history, memory_facts, mode='tap'):
    """Send a message to Atom and wait for response."""
    response_container['response'] = None
    response_container['error'] = None
    response_container['ready'].clear()

    payload = {
        'user_text': text,
        'screen_context': '',
        'history': history,
        'memory': memory_facts,
        'mode': mode,
        'task_type': 'chat',
    }
    brain.ask(payload)

    # Wait for response with timeout
    if not response_container['ready'].wait(timeout=60):
        return None, "Timeout waiting for response"

    if response_container['error']:
        return None, response_container['error']

    return response_container['response'], None


def handle_local_command(text, memory, config):
    """Handle commands that don't need the LLM."""
    normalized = text.strip().lower()
    user_lower = normalized

    # formal! toggle (any number of ! marks -> toggle between tap and formal)
    if user_lower.startswith('formal') and len(user_lower) > 6 and all(c == '!' for c in user_lower[6:]):
        current = config.get('mode', 'tap')
        new_mode = 'formal' if current != 'formal' else 'tap'
        config['mode'] = new_mode
        return 'mode', f"Switched to {new_mode} mode."

    if user_lower in {'quit', 'exit', 'stop', 'goodbye'}:
        return 'quit', None

    if user_lower in {'help', '?'}:
        return 'help', (
            "Atom CLI commands:\n"
            "  remember <fact>   - Save a fact to memory\n"
            "  forget <keyword>  - Remove facts containing keyword\n"
            "  memory            - Show saved memories\n"
            "  time              - Current date and time\n"
            "  weather [city]    - Check weather\n"
            "  mode <name>       - Switch mode (lullaby/whisper/tap/stalk/formal)\n"
            "  clear             - Clear chat history\n"
            "  help              - Show this help\n"
            "  quit/exit         - Exit Atom CLI\n"
            "\nEverything else is sent to Atom's brain for a response."
        )

    if user_lower == 'clear':
        return 'clear', None

    if user_lower in {'memory', 'what do you remember', 'what do you remember?'}:
        facts = memory.get('facts', [])
        if facts:
            return 'memory', "I remember: " + "; ".join(facts)
        return 'memory', "My memory is empty."

    if user_lower.startswith('remember '):
        fact = text[9:].strip()
        if fact:
            if 'facts' not in memory:
                memory['facts'] = []
            for existing in memory['facts']:
                if fact.lower() == existing.lower():
                    return 'memory', "Already in memory."
            memory['facts'].append(fact)
            save_memory(memory)
            return 'memory', f"Saved: {fact}"
        return 'memory', "Remember what?"

    if user_lower.startswith('forget '):
        target = text[7:].strip().lower()
        if target:
            if 'facts' in memory:
                before = len(memory['facts'])
                memory['facts'] = [f for f in memory['facts'] if target not in f.lower()]
                save_memory(memory)
                removed = before - len(memory['facts'])
                if removed:
                    return 'memory', f"Forgotten: {target}"
            return 'memory', f"Couldn't find '{target}' in memory."
        return 'memory', "Forget what?"

    if user_lower in ['time', 'what time is it', "what's the time", 'date', "what's the date"]:
        import datetime
        now = datetime.datetime.now()
        return 'local', f"It's {now.strftime('%I:%M %p').lstrip('0')} on {now.strftime('%A, %B %d, %Y')}."

    if user_lower.startswith('weather') or user_lower in ['weather', 'whats the weather', "what's the weather"]:
        return 'weather', text

    if user_lower.startswith('mode '):
        parts = normalized.split()
        target = parts[1] if len(parts) > 1 else ''
        mode_aliases = {'sleep': 'lullaby', 'ambient': 'tap', 'focused': 'stalk', 'chat-only': 'whisper', 'chatonly': 'whisper'}
        target = mode_aliases.get(target, target)
        if target in {'lullaby', 'whisper', 'tap', 'stalk', 'formal'}:
            config['mode'] = target
            return 'mode', f"Switched to {target} mode."

    if user_lower in ('mode', 'mode?', '?mode', 'current mode', 'what mode'):
        return 'mode', f"Current mode: {config.get('mode', 'tap')}"

    return None, None  # Not a local command


def do_weather(text, config):
    """Fetch and display weather."""
    import urllib.request
    import urllib.parse

    location = None
    if text.lower().startswith('weather') and len(text) > 8:
        location = text[8:].strip()

    try:
        if location:
            url = f"https://wttr.in/{urllib.parse.quote(location)}?format=j1"
        else:
            url = "https://wttr.in/?format=j1"

        req = urllib.request.Request(url, headers={'User-Agent': 'curl/7.68.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        if 'current_condition' not in data or not data['current_condition']:
            return "Couldn't fetch weather data."

        current = data['current_condition'][0]
        city = (data.get('nearest_area', [{}])[0].get('areaName', [{}])[0].get('value', 'your area'))
        region = (data.get('nearest_area', [{}])[0].get('region', [{}])[0].get('value', ''))
        loc = f"{city}, {region}" if region else city

        return (
            f"Weather in {loc}: {current.get('weatherDesc', [{}])[0].get('value', 'unknown')}, "
            f"{current.get('temp_C', '?')}°C ({current.get('temp_F', '?')}°F), "
            f"humidity {current.get('humidity', '?')}%, wind {current.get('windspeedKmph', '?')} km/h"
        )
    except Exception as e:
        return f"Weather error: {e}"


def print_bubble(role, text, width=60, use_color=True):
    """Print a formatted chat bubble to the terminal.
    
    Args:
        role: 'user', 'assistant', 'system', 'memory', 'local', 'mode'
        text: The message text
        width: Max width of the bubble
        use_color: Whether to use ANSI color codes
    """
    if not text:
        return

    role_icons = {'user': '▸ You', 'assistant': '▸ Atom', 'system': '▸ ●', 'memory': '▸ 📝', 'local': '▸ ⏰', 'mode': '▸ ⚙'}
    icon = role_icons.get(role, '▸')

    import textwrap
    lines = textwrap.wrap(text, width=width - 4) if len(text) > width - 4 else [text]

    if use_color:
        if role == 'user':
            c, c_end = '\033[36m', '\033[0m'
        elif role == 'assistant':
            c, c_end = '\033[35m', '\033[0m'
        elif role in ('memory', 'local', 'mode'):
            c, c_end = '\033[33m', '\033[0m'
        else:
            c, c_end = '\033[90m', '\033[0m'

        if role in ('memory', 'local', 'mode', 'system'):
            print(f"\n{c}{icon} {lines[0]}{c_end}")
            for line in lines[1:]:
                print(f"{c}   {line}{c_end}")
        else:
            sep = '-' * (width - 2)
            print(f"\n{c}{sep}{c_end}")
            for line in lines:
                print(f"{c}| {line.ljust(width - 4)} |{c_end}")
            print(f"{c}{sep}{c_end}")
    else:
        if role in ('memory', 'local', 'mode', 'system'):
            print(f"\n{icon} {lines[0]}")
            for line in lines[1:]:
                print(f"  {line}")
        else:
            print(f"\n{'─' * (width - 2)}")
            for line in lines:
                print(f"  {line}")
            print(f"{'─' * (width - 2)}")


def main():
    parser = argparse.ArgumentParser(description='Atom CLI — Chat with Atom from the terminal')
    parser.add_argument('--one-shot', '-o', type=str, help='Send a single message and print response, then exit')
    parser.add_argument('--mode', '-m', type=str, default='tap', choices=['tap', 'stalk', 'whisper', 'lullaby', 'formal'],
                        help='Operating mode (default: tap)')
    parser.add_argument('--no-color', action='store_true', help='Disable colored output')
    args = parser.parse_args()

    use_color = not args.no_color

    # Load config and memory
    config = load_json(CONFIG_PATH, {})
    # Use args.mode as initial mode; track current_mode separately for live updates
    config['mode'] = args.mode
    memory = load_json(MEMORY_PATH, {'facts': []})
    chat_history = load_chat_history()

    # ── Run dependency installer (Ollama, models, Tesseract) ──
    try:
        from core.installer import run_setup
        run_setup(pull_model=True)
    except Exception as _exc:
        log.warning("Dependency check: %s", _exc)

    # ── Load .env FIRST so Gemini keys are available for provider checks ──
    from core.brain import load_dotenv, get_gemini_keys
    load_dotenv()
    # get_gemini_keys checks both config.json AND os.environ (populated by load_dotenv)
    gemini_keys = get_gemini_keys(config)

    # ── Color helpers ──
    SEP = '=' * 60
    DASH = '-' * 60

    if use_color:
        dim = '\033[35m'
        gray = '\033[90m'
        reset = '\033[0m'
    else:
        dim = gray = reset = ''

    print(f"{dim}{SEP}{reset}", flush=True)
    print(f"{dim}  Atom CLI - Interactive mode ({args.mode}){reset}", flush=True)
    print(f"{dim}  Type 'help' for commands, 'quit' to exit{reset}", flush=True)
    print(f"{dim}{SEP}{reset}", flush=True)

    # Check for Gemini key (now after .env is loaded)
    if not gemini_keys:
        print(f"{gray}  No Gemini API key - using Ollama only{reset}")
    else:
        print(f"{gray}  {len(gemini_keys)} Gemini API key(s) found - fallback available{reset}")

    # Restore chat history hint
    if chat_history:
        print(f"{gray}  Restored {len(chat_history)} messages from history{reset}")
    print()

    # ── CLI-specific config overrides ──
    # Respect config's provider setting, default to ollama if not set
    if 'llm_provider' not in config:
        config['llm_provider'] = 'ollama'
    # Disable streaming for CLI (no Qt event loop to process stream tokens)
    config['ollama_streaming'] = False
    # Generous timeouts — first request may be cold-loading the model
    config['ollama_timeout_chat_seconds'] = 60
    config['ollama_timeout_analysis_seconds'] = 90
    config['ollama_num_ctx'] = 4096
    config['ollama_max_tokens'] = 500

    # ── Initialize brain ──
    print(f"{gray}  Starting Atom's brain...{reset}")
    brain, response_container = start_brain_thread(config, memory)

    # ── Blocking warm-up ──
    # Pre-load the model into memory so the first real request is fast (2-5s)
    # instead of waiting 20-30s for cold load.
    provider = config.get('llm_provider', 'ollama')
    model_name = config.get('model_chat') or config.get('model', 'stablelm-zephyr:3b')
    
    if provider == 'ollama':
        print(f"{gray}  Warming up {model_name}...{reset}", flush=True)
        _warmup_ok = False
        try:
            import urllib.request, json as _json
            payload = _json.dumps({
                'model': model_name,
                'messages': [{'role': 'user', 'content': 'ping'}],
                'stream': False,
                'options': {'num_predict': 1, 'num_ctx': 256}
            }).encode()
            req = urllib.request.Request(
                'http://localhost:11434/api/chat',
                data=payload,
                headers={'Content-Type': 'application/json'}
            )
            # Use longer timeout (30s) for cold model load
            with urllib.request.urlopen(req, timeout=30) as resp:
                _ = resp.read()
                _warmup_ok = True
        except Exception as exc:
            log.info("Warm-up: %s", exc)
        
        if _warmup_ok:
            print(f"{gray}  {model_name} ready{reset}")
        else:
            # Ollama not responding — check if Gemini fallback is available
            from core.brain import get_gemini_keys
            gemini_keys = get_gemini_keys(config)
            if gemini_keys:
                print(f"{gray}  Ollama not ready — Gemini fallback available{reset}")
            else:
                print(f"{gray}  Ollama not ready — will retry on first request{reset}")
    else:
        print(f"{gray}  Using {provider} backend{reset}")

    print(f"{gray}  Ready!{reset}")
    print(f"{dim}{DASH}{reset}")

    # Show last few messages from history
    if chat_history:
        print(f"{gray}  Previous conversation:{reset}")
        for entry in chat_history[-4:]:
            role = entry.get('role', 'user')
            content = entry.get('content', '')[:80]
            tag = 'You' if role == 'user' else 'Atom'
            print(f"{gray}  {tag}: {content}{'...' if len(entry.get('content', '')) > 80 else ''}{reset}")
        print()

    try:
        if args.one_shot:
            # ── One-shot mode ──
            text = args.one_shot
            print_bubble('user', text, use_color=use_color)
            
            cmd_type, cmd_result = handle_local_command(text, memory, config)
            if cmd_type:
                if cmd_type == 'quit':
                    return
                if cmd_type == 'weather':
                    cmd_result = do_weather(text, config)
                    print_bubble('local', cmd_result, use_color=use_color)
                elif cmd_result:
                    print_bubble(cmd_type, cmd_result, use_color=use_color)
                if cmd_type in ('clear', 'quit'):
                    return
                return

            response, error = ask_brain(brain, response_container, text, chat_history, memory.get('facts', []), args.mode)
            if error:
                print_bubble('system', f"Error: {error}", use_color=use_color)
            elif response:
                print_bubble('assistant', response, use_color=use_color)
                chat_history.append({'role': 'user', 'content': text})
                chat_history.append({'role': 'assistant', 'content': response})
                save_chat_history(chat_history)
        else:
            # ── Interactive mode ──
            while True:
                try:
                    prompt_color = '\033[36m' if use_color else ''
                    reset_color = '\033[0m' if use_color else ''
                    raw = input(f'{prompt_color}You > {reset_color}')
                except (EOFError, KeyboardInterrupt):
                    print()
                    break

                text = raw.strip()
                if not text:
                    continue

                cmd_type, cmd_result = handle_local_command(text, memory, config)
                if cmd_type:
                    if cmd_type == 'quit':
                        goodbye_c = '\033[33m' if use_color else ''
                        goodbye_reset = '\033[0m' if use_color else ''
                        print(f"{goodbye_c}Goodbye!{goodbye_reset}")
                        break
                    if cmd_type == 'clear':
                        chat_history.clear()
                        save_chat_history(chat_history)
                        print("Chat history cleared." if not use_color else "\033[90mChat history cleared.\033[0m")
                        continue
                    if cmd_type == 'help':
                        print(cmd_result)
                        continue
                    if cmd_type == 'weather':
                        cmd_result = do_weather(text, config)
                    if cmd_result:
                        print_bubble(cmd_type, cmd_result, use_color=use_color)
                    if cmd_type == 'mode':
                        CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding='utf-8')
                    continue

                print_bubble('user', text, use_color=use_color)

                thinking_msg = '  Atom is thinking...'
                print(f"\033[90m{thinking_msg}\033[0m" if use_color else f"  {thinking_msg}", end='', flush=True)
                response, error = ask_brain(brain, response_container, text, chat_history, memory.get('facts', []), config.get('mode', args.mode))
                print('\r' + ' ' * (len(thinking_msg) + 4) + '\r', end='')

                if error:
                    print_bubble('system', f"Error: {error}", use_color=use_color)
                elif response:
                    print_bubble('assistant', response, use_color=use_color)
                    chat_history.append({'role': 'user', 'content': text})
                    chat_history.append({'role': 'assistant', 'content': response})
                    save_chat_history(chat_history)

    finally:
        brain.stop()
        brain.wait(1000)


if __name__ == '__main__':
    main()
