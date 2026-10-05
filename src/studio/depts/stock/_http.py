"""HTTP for the stock department: urllib only (no extra deps), timeouts, polite retries on
429/5xx (honouring Retry-After, capped), a 24 h disk cache of API responses in
STUDIO_HOME/cache/stock (Pixabay's terms require caching), and streamed downloads.

Secrets (key/token/client_id params, auth headers) never appear in errors or cache files:
the cache is keyed by a hash and stores only the response body."""
from __future__ import annotations

import hashlib
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from ...config import cache_dir

UA = "studio-mcp/0.1 (https://github.com/joeshwoa/studio-mcp)"
CACHE_TTL = 24 * 3600
SECRET_PARAMS = {"key", "token", "client_id", "api_key", "client_secret", "access_token"}

_locks: dict[str, threading.Lock] = {}
_last: dict[str, float] = {}
_glock = threading.Lock()
MIN_INTERVAL = {"wikimedia": 1.0, "openverse": 0.5, "archive": 0.3}   # seconds between calls per host family


class HTTPFail(Exception):
    def __init__(self, msg: str, status: int = 0):
        super().__init__(msg)
        self.status = status


def stock_cache() -> Path:
    p = cache_dir() / "stock"
    p.mkdir(parents=True, exist_ok=True)
    return p


def redact(url: str) -> str:
    """URL with secret query values replaced by ***."""
    try:
        u = urllib.parse.urlsplit(url)
        q = [(k, "***" if k.lower() in SECRET_PARAMS else v) for k, v in urllib.parse.parse_qsl(u.query, keep_blank_values=True)]
        return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(q), ""))
    except Exception:
        return url.split("?")[0]


def build_url(base: str, params: dict | list | None = None) -> str:
    if not params:
        return base
    items = params.items() if isinstance(params, dict) else params
    q = urllib.parse.urlencode([(k, v) for k, v in items if v is not None and v != ""], doseq=True)
    return base + ("&" if "?" in base else "?") + q


def _throttle(family: str):
    gap = MIN_INTERVAL.get(family, 0)
    if not gap:
        return
    with _glock:
        lock = _locks.setdefault(family, threading.Lock())
    with lock:
        wait = _last.get(family, 0) + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[family] = time.time()


def safe_url(url: str) -> str:
    """Percent-encode stray spaces/unicode in provider URLs (e.g. NASA ids with spaces)."""
    return urllib.parse.quote(url, safe=":/?&=%#+,;@~!$'()*[]")


def request(url: str, headers: dict | None = None, method: str = "GET", timeout: float = 25, retries: int = 3,
            family: str = "", max_wait: float = 30.0) -> bytes:
    """Raw bytes of a small response, with retries on 429/5xx/timeouts."""
    h = {"User-Agent": UA, "Accept": "application/json, */*"}
    h.update(headers or {})
    last_err = ""
    for attempt in range(retries + 1):
        _throttle(family)
        req = urllib.request.Request(safe_url(url), headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            status = e.code
            body = b""
            try:
                body = e.read()[:300]
            except Exception:
                pass
            last_err = f"HTTP {status} from {redact(url)} {body.decode('utf-8', 'replace')[:160]}".strip()
            if status in (429, 500, 502, 503, 504) and attempt < retries:
                ra = e.headers.get("Retry-After", "") if e.headers else ""
                try:
                    wait = float(ra)
                except ValueError:
                    wait = 2.0 * (2 ** attempt)
                if wait > max_wait:
                    raise HTTPFail(last_err + f" (rate limited; retry after {int(wait)} s)", status)
                time.sleep(wait + random.uniform(0, 0.5))
                continue
            raise HTTPFail(last_err, status)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last_err = f"{type(e).__name__} for {redact(url)}: {getattr(e, 'reason', e)}"
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise HTTPFail(last_err)
    raise HTTPFail(last_err or f"failed: {redact(url)}")


def get_json(url: str, params: dict | list | None = None, headers: dict | None = None, ttl: int = CACHE_TTL,
             family: str = "", timeout: float = 25) -> dict | list:
    """GET JSON with the 24 h disk cache (ttl=0 disables)."""
    full = build_url(url, params)
    key = hashlib.sha1((full + "|" + json.dumps(sorted((headers or {}).items()))).encode()).hexdigest()
    cf = stock_cache() / "api" / f"{key}.json"
    if ttl and cf.exists() and time.time() - cf.stat().st_mtime < ttl:
        try:
            return json.loads(cf.read_text(encoding="utf-8"))
        except Exception:
            pass
    raw = request(full, headers=headers, family=family, timeout=timeout)
    try:
        data = json.loads(raw)
    except Exception:
        raise HTTPFail(f"non-JSON reply from {redact(full)}: {raw[:120]!r}")
    if ttl:
        cf.parent.mkdir(parents=True, exist_ok=True)
        tmp = cf.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(cf)
    return data


def download(url: str, dest: Path, headers: dict | None = None, timeout: float = 60, max_bytes: int = 4 << 30,
             family: str = "") -> Path:
    """Stream to dest (.part then rename). Retries transient failures twice."""
    h = {"User-Agent": UA}
    h.update(headers or {})
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    err = ""
    for attempt in range(3):
        _throttle(family)
        try:
            req = urllib.request.Request(safe_url(url), headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r, open(part, "wb") as fh:
                n = 0
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    n += len(chunk)
                    if n > max_bytes:
                        raise HTTPFail(f"file larger than {max_bytes >> 20} MB — refused: {redact(url)}")
                    fh.write(chunk)
            if part.stat().st_size == 0:
                raise HTTPFail(f"empty download: {redact(url)}")
            part.replace(dest)
            return dest
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code} downloading {redact(url)}"
            if e.code not in (429, 500, 502, 503, 504):
                break
            time.sleep(2 * (attempt + 1))
        except HTTPFail:
            part.unlink(missing_ok=True)
            raise
        except Exception as e:
            err = f"{type(e).__name__} downloading {redact(url)}: {e}"
            time.sleep(1.5 * (attempt + 1))
    part.unlink(missing_ok=True)
    raise HTTPFail(err)


def cached_file(url: str, ext: str = ".jpg", headers: dict | None = None, max_bytes: int = 25 << 20,
                family: str = "") -> Path:
    """Download once into cache/stock/thumbs (thumbnails, icon SVGs for previews)."""
    key = hashlib.sha1(url.encode()).hexdigest()
    d = stock_cache() / "thumbs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{key}{ext}"
    if p.exists() and p.stat().st_size > 0:
        return p
    return download(url, p, headers=headers, timeout=30, max_bytes=max_bytes, family=family)
