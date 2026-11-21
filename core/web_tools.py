"""
Web Tools — Search, scrape, and sandbox for Atom.

SAFETY FIRST:
- All HTTP requests go through a sandboxed environment
- Downloads are restricted to the sandbox/ directory
- HTML is sanitized (scripts, event handlers, iframes stripped)
- Maximum file size enforced (5MB)
- Timeouts on all requests (15s)
- No write access outside the sandbox directory
"""
import json
import os
import re
import urllib.request
import urllib.parse
import urllib.error
import html.parser
from pathlib import Path
from datetime import datetime
from typing import Optional

from bs4 import BeautifulSoup
from core.logger import get_logger

# ── Lazy trafilatura import (Phase 66: enhanced text extraction) ──
_trafilatura_available = False
try:
    import trafilatura
    _trafilatura_available = True
except ImportError:
    pass

log = get_logger('web_tools')

# ── Sandbox configuration ──

ROOT = Path(__file__).parent.parent
SANDBOX_DIR = ROOT / 'sandbox'
MAX_DOWNLOAD_BYTES = 5 * 1024 * 1024   # 5 MB
REQUEST_TIMEOUT = 15                    # seconds
MAX_RESULTS = 8                         # max search results to return
MAX_SCRAPE_CHARS = 10000                # max characters to extract from a page

_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'


# ── Sandbox directory management ──

def ensure_sandbox() -> Path:
    """Create sandbox directory if it doesn't exist."""
    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    return SANDBOX_DIR


def clean_sandbox(max_age_hours: int = 24):
    """Remove files older than max_age_hours from the sandbox."""
    if not SANDBOX_DIR.exists():
        return
    now = datetime.now().timestamp()
    cleaned = 0
    for f in SANDBOX_DIR.iterdir():
        if f.is_file():
            age_hours = (now - f.stat().st_mtime) / 3600
            if age_hours > max_age_hours:
                try:
                    f.unlink()
                    cleaned += 1
                except Exception as exc:
                    log.warning("Could not clean sandbox file %s: %s", f.name, exc)
    if cleaned:
        log.info("Cleaned %d old files from sandbox", cleaned)


def sandbox_path(filename: str) -> Path:
    """Get a path inside the sandbox directory.
    
    Ensures the filename doesn't escape the sandbox via path traversal.
    """
    safe_name = re.sub(r'[^\w\.\-]', '_', filename)
    return SANDBOX_DIR / safe_name


# ── HTML Sanitizer ──

def sanitize_html(html_content: str) -> str:
    """Strip dangerous elements from HTML while keeping readable text.
    
    Removes:
    - <script> tags and their contents
    - Event handler attributes (onclick, onload, etc.)
    - <iframe>, <object>, <embed> tags
    - <style> tags (keep content as reference)
    """
    if not html_content:
        return ''
    
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Remove dangerous tags
    for tag in soup.find_all(['script', 'iframe', 'object', 'embed', 'noscript']):
        tag.decompose()
    
    # Remove event handler attributes from all tags
    for tag in soup.find_all(True):
        attrs_to_remove = [attr for attr in tag.attrs if attr.startswith('on')]
        for attr in attrs_to_remove:
            del tag[attr]
    
    # Remove <style> contents? No, keep them — they don't execute code
    # But remove <link> tags with stylesheet to prevent external requests
    for link in soup.find_all('link', rel=lambda x: x and 'stylesheet' in x.lower()):
        link.decompose()
    
    return str(soup)


def extract_readable_text(html_content: str, max_chars: int = MAX_SCRAPE_CHARS) -> str:
    """Extract readable text from HTML using trafilatura (Phase 66) with BeautifulSoup fallback.
    
    trafilatura produces cleaner article text by:
    - Identifying main content vs. boilerplate (nav, ads, sidebars)
    - Extracting article title, author, date
    - Preserving paragraph structure
    """
    if _trafilatura_available and html_content:
        try:
            extracted = trafilatura.extract(
                html_content,
                include_links=True,
                include_images=False,
                include_formatting=True,
                output_format='txt',
            )
            if extracted and len(extracted.strip()) > 20:
                text = extracted.strip()
                if len(text) > max_chars:
                    text = text[:max_chars] + '\n... [truncated]'
                return text
            log.debug("trafilatura returned short/empty text, falling back to BeautifulSoup")
        except Exception as exc:
            log.debug("trafilatura extraction failed: %s", exc)
    
    # Fallback: BeautifulSoup
    soup = BeautifulSoup(html_content, 'html.parser')
    for tag in soup.find_all(['script', 'style', 'iframe', 'object', 'embed', 'noscript', 'nav', 'footer']):
        tag.decompose()
    
    text = soup.get_text(separator='\n', strip=True)
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    text = '\n'.join(lines)
    
    if len(text) > max_chars:
        text = text[:max_chars] + '\n... [truncated]'
    return text


# ── Web Search (Brave Search API primary, DuckDuckGo fallback) ──

def _get_brave_api_key(config: Optional[dict] = None) -> Optional[str]:
    """Get Brave Search API key from env or config."""
    env_key = os.environ.get('BRAVE_SEARCH_API_KEY')
    if env_key:
        return env_key.strip()
    if config:
        cfg_key = config.get('brave_search_api_key')
        if cfg_key:
            return str(cfg_key).strip()
    return None


def search_brave(query: str, max_results: int = MAX_RESULTS, api_key: str = None) -> Optional[list[dict]]:
    """Search using Brave Search API (free tier: 2000 req/month, no key needed for basic).
    
    Brave Search API returns richer results than DuckDuckGo, with:
    - Web results with descriptions
    - News results
    - No rate limiting on free tier (up to 2000 req/month)
    
    Returns None if API key is missing or request fails (so callers can fallback).
    """
    if not api_key:
        return None
    
    try:
        url = f'https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote(query)}&count={min(max_results, 10)}'
        req = urllib.request.Request(url, headers={
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip',
            'X-Subscription-Token': api_key,
        })
        
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        
        results = []
        web_results = data.get('web', {}).get('results', [])
        for item in web_results[:max_results]:
            title = item.get('title', '')
            url = item.get('url', '')
            snippet = item.get('description', '') or item.get('snippet', '')
            if title and url:
                results.append({
                    'title': title[:200],
                    'url': url,
                    'snippet': snippet[:400],
                })
        
        log.info("Brave Search for '%s' returned %d results", query, len(results))
        return results
    except urllib.error.HTTPError as e:
        if e.code == 401:
            log.warning("Brave Search API: invalid key (401)")
        elif e.code == 429:
            log.warning("Brave Search API: rate limited (429)")
        else:
            log.warning("Brave Search API HTTP %d: %s", e.code, str(e)[:80])
        return None
    except urllib.error.URLError as e:
        log.warning("Brave Search connection failed: %s", e.reason)
        return None
    except Exception as e:
        log.warning("Brave Search error: %s", str(e)[:80])
        return None


def _unwrap_ddg_href(href: str) -> str:
    """Turn a DuckDuckGo redirect (``//duckduckgo.com/l/?uddg=...``) into its real URL."""
    if not href:
        return ''
    if href.startswith('//'):
        href = 'https:' + href
    if 'uddg=' in href:
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            return (qs.get('uddg') or [href])[0]
        except Exception:
            return href
    return href


def _search_html_fallback(query: str, max_results: int = MAX_RESULTS) -> list[dict]:
    """DuckDuckGo HTML search — the main key-free source of *current* results.

    The Instant Answer API only knows Wikipedia-style topics, so a question like
    "what is the best phone right now" came back empty and Atom answered "no
    results were yielded". The HTML endpoint returns real web results; POST is
    used first because the GET form is far more likely to serve a captcha page.
    """
    headers = {
        'User-Agent': _USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'en-US,en;q=0.9',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Referer': 'https://duckduckgo.com/',
    }
    html_content = ''
    try:
        body = urllib.parse.urlencode({'q': query, 'kl': 'us-en'}).encode()
        req = urllib.request.Request('https://html.duckduckgo.com/html/', data=body,
                                     headers=headers)
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            html_content = resp.read().decode('utf-8', errors='replace')
    except Exception as exc:
        log.warning("DDG HTML POST failed: %s", str(exc)[:80])
        try:
            url = f'https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}'
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                html_content = resp.read().decode('utf-8', errors='replace')
        except Exception as exc2:
            log.warning("DDG HTML GET failed: %s", str(exc2)[:80])
            return []

    low = html_content.lower()
    if any(kw in low for kw in ('captcha', 'verify you are human', 'unusual traffic')):
        log.warning("DDG HTML was a challenge page for '%s'", query[:60])
        return []

    soup = BeautifulSoup(html_content, 'html.parser')
    results: list[dict] = []
    seen: set[str] = set()

    for body_el in soup.select('.result'):
        link = body_el.select_one('.result__a') or body_el.find('a', href=True)
        if not link:
            continue
        title = link.get_text(' ', strip=True)
        href = _unwrap_ddg_href(link.get('href', ''))
        if not title or not href.startswith(('http://', 'https://')):
            continue
        if href in seen:
            continue
        snippet_el = body_el.select_one('.result__snippet')
        snippet = snippet_el.get_text(' ', strip=True) if snippet_el else ''
        seen.add(href)
        results.append({'title': title[:200], 'url': href, 'snippet': snippet[:400]})
        if len(results) >= max_results:
            break

    if not results:
        # Layout change fallback: any external link with readable text.
        for a in soup.find_all('a', href=True):
            href = _unwrap_ddg_href(a.get('href', ''))
            text = a.get_text(' ', strip=True)
            if not href.startswith(('http://', 'https://')) or len(text) < 15:
                continue
            if 'duckduckgo.com' in href or href in seen:
                continue
            seen.add(href)
            results.append({'title': text[:200], 'url': href, 'snippet': ''})
            if len(results) >= max_results:
                break

    log.info("DDG HTML search for '%s' returned %d results", query[:60], len(results))
    return results


def _search_wikipedia(query: str, max_results: int = MAX_RESULTS) -> list[dict]:
    """Wikipedia search + intro extracts — reliable for factual questions."""
    results: list[dict] = []
    try:
        api = 'https://en.wikipedia.org/w/api.php'
        params = {
            'action': 'query', 'list': 'search', 'srsearch': query,
            'format': 'json', 'srlimit': str(max(1, min(max_results, 5))),
        }
        url = api + '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={'User-Agent': _USER_AGENT})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        hits = data.get('query', {}).get('search', []) or []

        for hit in hits:
            title = (hit.get('title') or '').strip()
            if not title:
                continue
            page = f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
            snippet = re.sub(r'<[^>]+>', '', hit.get('snippet') or '')
            snippet = urllib.parse.unquote(snippet)
            results.append({
                'title': title[:200],
                'url': page,
                'snippet': snippet.strip()[:400],
            })
            if len(results) >= max_results:
                break

        # One real paragraph beats a one-line snippet for answering a question.
        if results:
            first = results[0]['title']
            params = {
                'action': 'query', 'prop': 'extracts', 'exintro': '1',
                'explaintext': '1', 'titles': first, 'format': 'json',
            }
            url = api + '?' + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={'User-Agent': _USER_AGENT})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            pages = (data.get('query', {}).get('pages') or {}).values()
            for page in pages:
                extract = (page.get('extract') or '').strip()
                if extract:
                    results[0]['snippet'] = extract[:1200]
                    break

        log.info("Wikipedia search for '%s' returned %d results", query[:60], len(results))
    except Exception as exc:
        log.warning("Wikipedia search failed: %s", str(exc)[:80])
    return results


def _search_ddg_instant(query: str, max_results: int = MAX_RESULTS) -> list[dict]:
    """DuckDuckGo Instant Answer API — definitions and encyclopedia topics."""
    results: list[dict] = []
    try:
        api_url = f'https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1&skip_disambig=1'
        req = urllib.request.Request(api_url, headers={'User-Agent': _USER_AGENT})
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        abstract = data.get('AbstractText', '')
        if abstract:
            results.append({
                'title': data.get('AbstractSource', 'DuckDuckGo'),
                'url': data.get('AbstractURL', ''),
                'snippet': abstract[:600],
            })

        topics = list(data.get('RelatedTopics', []) or [])
        while topics and len(results) < max_results:
            topic = topics.pop(0)
            if 'Topics' in topic:
                topics = list(topic['Topics']) + topics
                continue
            text = topic.get('Text')
            first = topic.get('FirstURL')
            if text and first:
                results.append({
                    'title': text[:100].split(' - ')[0],
                    'url': first,
                    'snippet': text[:400],
                })
    except Exception as exc:
        log.warning("DDG instant answer failed: %s", str(exc)[:80])
    return results[:max_results]


def search_web(query: str, max_results: int = MAX_RESULTS, config: Optional[dict] = None) -> list[dict]:
    """Search the web, trying several key-free sources until one answers.

    Order: Brave (if a key is configured) → DuckDuckGo HTML (current web
    results) → Wikipedia (factual) → DuckDuckGo Instant Answer (definitions).
    Results are de-duplicated by URL. An empty list means every source really
    failed, so callers can say so honestly instead of claiming "no results".
    """
    query = (query or '').strip()
    if not query:
        return []

    sources = []
    brave_key = _get_brave_api_key(config)
    if brave_key:
        sources.append(('brave', lambda: search_brave(query, max_results, brave_key)))
    sources.append(('ddg-html', lambda: _search_html_fallback(query, max_results)))
    sources.append(('wikipedia', lambda: _search_wikipedia(query, max_results)))
    sources.append(('ddg-instant', lambda: _search_ddg_instant(query, max_results)))

    collected: list[dict] = []
    seen: set[str] = set()
    for name, fetch in sources:
        try:
            batch = fetch() or []
        except Exception as exc:
            log.warning("Search source '%s' crashed: %s", name, str(exc)[:80])
            batch = []
        added = 0
        for item in batch:
            url = (item.get('url') or '').strip()
            if not url or url in seen:
                continue
            seen.add(url)
            collected.append(item)
            added += 1
        if added:
            log.info("Search for '%s': %d results from %s", query[:60], added, name)
        # Enough real results — stop spending time on the slower sources.
        if len(collected) >= max_results:
            break

    if not collected:
        log.info("Search for '%s' produced no results from any source", query[:60])
    return collected[:max_results]


# ── Web Scraping (with sandbox) ──

def fetch_url(url: str, max_bytes: int = MAX_DOWNLOAD_BYTES) -> Optional[str]:
    """Fetch a URL's content through the sandbox.
    
    Safety measures:
    - Only http/https schemes allowed
    - File size limited to max_bytes
    - Timeout enforced
    - Content is sanitized (scripts stripped)
    - Download goes to sandbox directory
    
    Returns the sanitized HTML content as a string, or None on failure.
    """
    # Validate URL
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        log.warning("Blocked non-HTTP URL: %s", url[:80])
        return None
    
    # Validate URL isn't local/private
    hostname = parsed.hostname or ''
    if hostname in ('localhost', '127.0.0.1', '0.0.0.0', '::1'):
        log.warning("Blocked localhost URL: %s", url[:80])
        return None
    if hostname.startswith('192.168.') or hostname.startswith('10.') or hostname.startswith('172.'):
        # These are private IP ranges
        log.warning("Blocked private IP URL: %s", url[:80])
        return None
    
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': _USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        })
        
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            # Check content type
            content_type = resp.headers.get('Content-Type', '')
            if 'text/html' not in content_type and 'text/plain' not in content_type:
                log.warning("Blocked non-HTML content type: %s", content_type)
                return None
            
            # Read with size limit
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                log.warning("Content exceeds max size (%d bytes), truncating", max_bytes)
                raw = raw[:max_bytes]
            
            html_content = raw.decode('utf-8', errors='replace')
        
        # Sanitize
        sanitized = sanitize_html(html_content)
        
        # Save to sandbox for reference
        ensure_sandbox()
        safe_filename = re.sub(r'[^\w\-]', '_', hostname + '_' + datetime.now().strftime('%H%M%S')) + '.html'
        sandbox_path(safe_filename).write_text(sanitized, encoding='utf-8')
        
        log.info("Fetched and sanitized %s (saved to sandbox/%s)", url[:80], safe_filename)
        return sanitized
    
    except urllib.error.HTTPError as e:
        if e.code == 403:
            log.warning("Blocked by %s (403 Forbidden)", hostname)
        elif e.code == 404:
            log.warning("Page not found: %s", url[:80])
        else:
            log.warning("HTTP %d fetching %s: %s", e.code, url[:80], str(e)[:100])
        return None
    except urllib.error.URLError as e:
        log.warning("Connection error fetching %s: %s", url[:80], e.reason)
        return None
    except Exception as e:
        log.warning("Fetch error for %s: %s", url[:80], str(e)[:100])
        return None


def scrape_page(url: str) -> Optional[dict]:
    """Scrape a URL and extract readable content.
    
    Returns a dict with 'title', 'text', 'url', 'word_count' or None on failure.
    Content is extracted through the sandbox (sanitized, script-free).
    """
    html_content = fetch_url(url)
    if not html_content:
        return None
    
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Extract title
        title = ''
        title_tag = soup.find('title')
        if title_tag:
            title = title_tag.get_text(strip=True)
        
        # Extract readable text
        text = extract_readable_text(html_content)
        
        return {
            'title': title or url,
            'text': text,
            'url': url,
            'word_count': len(text.split()),
        }
    except Exception as e:
        log.warning("Scrape parse error for %s: %s", url[:80], str(e)[:100])
        return None


def format_search_results(results: list[dict]) -> str:
    """Format search results into a readable string for Atom's response."""
    if not results:
        return "No results found."
    
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        if r.get('snippet'):
            lines.append(f"   {r['snippet'][:200]}")
        lines.append(f"   {r['url']}")
    return '\n'.join(lines)


def format_scrape_result(data: dict) -> str:
    """Format scraped page content into a readable summary."""
    if not data:
        return "Couldn't read that page."
    
    title = data.get('title', 'Untitled')
    text = data.get('text', '')
    url = data.get('url', '')
    wc = data.get('word_count', 0)
    
    # Truncate text for Atom's response
    max_preview = 1500
    if len(text) > max_preview:
        text = text[:max_preview] + '\n... [rest truncated]'
    
    return f"Title: {title}\nURL: {url}\nWords: {wc}\n\n{text}"
