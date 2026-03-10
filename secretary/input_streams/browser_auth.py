"""
Browser-assisted Learn auth importer.

Goal:
- let user login in browser once
- automatically import Cookie (+ best-effort CSRF) into learn-stream config
"""

from __future__ import annotations

import re
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

from secretary.input_streams.learn import LearnStreamError
from secretary.input_streams.scheduler import (
    get_default_config_path,
    load_stream_config,
    save_stream_config,
)


LEARN_DOMAIN = "learn.tsinghua.edu.cn"
CSRF_COOKIE_KEYS = (
    "_csrf",
    "csrf",
    "csrf_token",
    "csrftoken",
    "x-csrf-token",
    "xsrf-token",
)


def _load_cookiejar(browser: str = "auto"):
    try:
        import browser_cookie3  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise LearnStreamError(
            "缺少依赖 browser-cookie3，请先执行: pip install browser-cookie3"
        ) from exc

    loaders = {
        "chrome": browser_cookie3.chrome,
        "chromium": browser_cookie3.chromium,
        "edge": browser_cookie3.edge,
        "firefox": browser_cookie3.firefox,
        "brave": browser_cookie3.brave,
    }
    tried: list[str] = []

    def _try(name: str):
        tried.append(name)
        return loaders[name](domain_name=LEARN_DOMAIN)

    if browser != "auto":
        if browser not in loaders:
            raise LearnStreamError(f"不支持的 browser: {browser}")
        return browser, _try(browser), tried

    for name in ("chrome", "chromium", "edge", "brave", "firefox"):
        try:
            jar = _try(name)
        except Exception:
            continue
        cookies = list(jar)
        if cookies:
            return name, jar, tried
    raise LearnStreamError(f"未找到 {LEARN_DOMAIN} 登录 Cookie，已尝试: {', '.join(tried)}")


def _cookie_header_and_csrf(cookiejar) -> tuple[str, str]:
    pairs: list[tuple[str, str]] = []
    csrf = ""
    seen: set[str] = set()

    for c in cookiejar:
        domain = getattr(c, "domain", "") or ""
        if LEARN_DOMAIN not in domain:
            continue
        name = getattr(c, "name", "")
        value = getattr(c, "value", "")
        if not name:
            continue
        key = name.lower()
        if key not in seen:
            pairs.append((name, value))
            seen.add(key)
        if not csrf and key in CSRF_COOKIE_KEYS:
            csrf = value

    if not pairs:
        raise LearnStreamError(f"浏览器中未发现 {LEARN_DOMAIN} Cookie")
    cookie_header = "; ".join(f"{k}={v}" for k, v in pairs)
    return cookie_header, csrf


def _csrf_from_page(cookie_header: str, base_url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(
        base_url,
        headers={
            "Cookie": cookie_header,
            "User-Agent": "study-browser-auth/0.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    patterns = [
        r'name=["\']_csrf["\']\s+value=["\']([^"\']+)["\']',
        r'"_csrf"\s*:\s*"([^"]+)"',
        r"csrfToken\s*[:=]\s*['\"]([^'\"]+)['\"]",
        r"x-csrf-token\s*[:=]\s*['\"]([^'\"]+)['\"]",
    ]
    for pat in patterns:
        m = re.search(pat, html, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def import_learn_auth_from_browser(
    *,
    config_path: str | Path | None = None,
    browser: str = "auto",
    open_login: bool = False,
    base_url: str = "https://learn.tsinghua.edu.cn",
) -> dict[str, Any]:
    """
    Import Learn Cookie/CSRF from local browser and persist to learn-stream config.
    """
    if open_login:
        webbrowser.open(base_url)

    browser_name, jar, tried = _load_cookiejar(browser=browser)
    cookie_header, csrf = _cookie_header_and_csrf(jar)
    if not csrf:
        try:
            csrf = _csrf_from_page(cookie_header, base_url=base_url)
        except Exception:
            csrf = ""

    cfg_path = Path(config_path).expanduser().resolve() if config_path else get_default_config_path()
    config = load_stream_config(cfg_path)

    cookie_file = cfg_path.parent / "browser_cookie.txt"
    cookie_file.parent.mkdir(parents=True, exist_ok=True)
    cookie_file.write_text(cookie_header, encoding="utf-8")
    try:
        cookie_file.chmod(0o600)
    except Exception:
        pass

    # Prefer cookie_file over plain cookie to reduce accidental exposure.
    config["cookie"] = ""
    config["cookie_file"] = str(cookie_file)
    if csrf:
        config["csrf_token"] = csrf
    save_stream_config(config, cfg_path)

    return {
        "ok": True,
        "browser": browser_name,
        "attempted_browsers": tried,
        "config_path": str(cfg_path),
        "cookie_file": str(cookie_file),
        "csrf_found": bool(csrf),
    }

