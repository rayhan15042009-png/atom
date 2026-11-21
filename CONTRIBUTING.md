# Contributing to Atom

Thanks for wanting to help. Atom is a Windows desktop app: a PyQt6 overlay, a
provider router (Ollama / Gemini / browser session), and a pixel character with
its own animation state machine.

## Licensing of contributions

Atom is released under **GPL-3.0-only**. By opening a pull request you agree that
your contribution is licensed under the same terms (**inbound = outbound**). This
keeps a single licence for the whole project and leaves room to relicense future
versions (for example after moving off PyQt6) without tracking down every past
contributor.

## Before you send a pull request

```bat
setup_atom.bat
.venv\Scripts\python -m pytest tests -q
```

- All tests must pass (they run offline; GUI tests use Qt's `offscreen` platform).
- If you change rendering or the chat panel, also run
  `python tools/probe_full_run.py` and look at the screenshots in `logs/` — several
  bugs in this project only ever showed up on screen, not in the assertions.
- Keep the change focused. Describe *why* in the commit message; a fix with a
  failing test that now passes is the ideal shape.

## Style

- Match the surrounding code: type hints where the file already uses them, no new
  dependencies without a reason, comments explain *why* rather than restating code.
- Never commit `data/*.json`, `.env`, `logs/`, or anything under `data/browser_*`.
  They hold your chat transcript, saved browser cookies and API keys. `.gitignore`
  covers them; do not force-add.
- Do not add attribution lines or tool-generated badges to source files.

## Reporting bugs

Include: what you did, what happened, what you expected, the provider you were
using (Ollama / Gemini / Browser AI), and the last ~30 lines of `logs/atom.log`.
Remove anything private before pasting — the log contains your message text.
