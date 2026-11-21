# Third-party notices

Atom itself is licensed under **GPL-3.0-only** (see [LICENSE](LICENSE)). It uses
the following third-party components. None of their source is vendored in this
repository — they are installed from PyPI (or installed separately by the user)
and used through their public APIs.

## Python packages (from `requirements.txt`)

| Package | Licence | Used for |
|---|---|---|
| **PyQt6** | **GPL-3.0-only** | the entire GUI — see the note below |
| opencv-python | Apache-2.0 | image work for the 2D sprite renderer |
| pillow | MIT-CMU | screenshots and image handling |
| mss | MIT | fast desktop capture for OCR |
| pytesseract | Apache-2.0 | OCR wrapper (needs Tesseract itself) |
| pyautogui | BSD-3-Clause | mouse/keyboard action execution |
| psutil | BSD-3-Clause | process and system information |
| pyttsx3 | MPL-2.0 | text-to-speech through Windows SAPI |
| beautifulsoup4 | MIT | parsing search result pages |
| requests | Apache-2.0 | HTTP for search and web fetches |
| playwright | Apache-2.0 | the Browser AI provider (real Chromium session) |
| playwright-stealth | MIT | making the browser session behave normally |
| imagehash | BSD-2-Clause | screen-change detection for the proactive behaviour |
| trafilatura | Apache-2.0 | readable-text extraction from web pages |
| pyperclip | BSD-3-Clause | clipboard access |
| pygame | LGPL-2.1 | sprite/animation support |

## External programs (installed separately, not bundled)

| Program | Licence | Used for |
|---|---|---|
| [Ollama](https://ollama.com) | MIT | local model runtime |
| [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) | Apache-2.0 | screen text recognition |
| The language models you pull | their own (e.g. Llama 3.1 Community Licence, MIT) | local answers — check each model's terms before commercial use |

`setup_atom.py --full` downloads and runs the official Ollama and Tesseract
installers from their vendors; it does not redistribute them.

## A note on PyQt6

PyQt6 is dual-licensed by Riverbank Computing under the **GPLv3** or a paid
**commercial licence**. Atom is distributed under the GPLv3 option, which is why
this project is GPL-3.0-only: any build you distribute must be accompanied by its
source under the same licence. If you want to use Atom's code in a closed-source
product, you will need a commercial PyQt licence from Riverbank; the GPL
obligations on this code remain.

## Artwork

The intro video, logos, 3D model and the `atom`, `pink`, `gamergirl` and `ghost`
skins are original works by the Atom author. The `steve` and `alex` skins are
Minecraft-style character textures; Minecraft and its characters are trademarks
of Mojang Studios / Microsoft, which this project is not affiliated with. They are
included for personal, non-commercial use.
