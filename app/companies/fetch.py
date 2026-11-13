"""Ограниченное чтение разрешённых публичных страниц, без CAPTCHA/proxy обходов."""

import asyncio
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from app.companies.parsers import HOSTS
from app.config import Settings

AGENT = "ByteStudioCompanyResearch/1.0"


class PageUnavailable(RuntimeError):
    def __init__(self, code: str, *, retry_after: float = 0):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


def robots_allowed(text: str, url: str) -> bool:
    groups = []
    agents = []
    rules = []
    has_rules = False
    for line in [*text.splitlines(), "User-agent: __end__"]:
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if has_rules:
                groups.append((agents, rules))
                agents = []
                rules = []
                has_rules = False
            agents.append(value.lower())
        elif key in {"allow", "disallow"} and agents:
            has_rules = True
            if value:
                rules.append((key, value))
    selected = []
    specific = -1
    for agents, rules in groups:
        matches = [len(a) if a != "*" else 0 for a in agents if a == "*" or a in AGENT.lower()]
        if not matches:
            continue
        size = max(matches)
        if size > specific:
            selected = []
            specific = size
        if size == specific:
            selected.extend(rules)
    path = urlsplit(url).path + ("?" + urlsplit(url).query if urlsplit(url).query else "")
    matched = []
    for kind, pattern in selected:
        end = pattern.endswith("$")
        pattern = pattern.removesuffix("$")
        regex = "^" + ".*".join(re.escape(p) for p in unquote(pattern).split("*")) + ("$" if end else "")
        if re.search(regex, unquote(path)):
            matched.append((len(pattern.replace("*", "")), kind == "allow"))
    return max(matched, default=(0, True))[1]


def find_browser(explicit_path: str | None = None) -> str | None:
    explicit = explicit_path or os.environ.get("CHROME_PATH")
    if explicit:
        path = Path(os.path.expandvars(explicit)).expanduser()
        if path.is_file():
            return str(path)
    for name in ("chrome", "chromium", "google-chrome", "msedge"):
        path = shutil.which(name)
        if path:
            return path
    if sys.platform == "win32":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            for suffix in ("Google/Chrome/Application/chrome.exe", "Microsoft/Edge/Application/msedge.exe"):
                path = Path(os.environ.get(variable, "/__missing__")) / suffix
                if path.is_file():
                    return str(path)
    return None


def blocked(html: str) -> bool:
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    value = title[1].casefold() if title else ""
    return any(
        word in value
        for word in ("captcha", "robot", "403", "подтвердите", "доступ ограничен", "before you continue", "consent")
    )


class PublicPages:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None, renderer=None):
        self.settings = settings
        self.client = client
        self.owned = client is None
        self.renderer = renderer
        self.robots = {}
        self.locks: dict[str, asyncio.Lock] = {}
        self.last: dict[str, float] = {}
        self.render_lock = asyncio.Lock()

    def validate(self, provider: str, url: str) -> None:
        try:
            p = urlsplit(url)
            valid = (
                p.scheme == "https"
                and p.hostname in HOSTS.get(provider, set())
                and not p.username
                and not p.password
                and p.port in {None, 443}
            )
        except ValueError:
            valid = False
        if not valid:
            raise PageUnavailable("url_not_allowed")

    async def _get(self, provider: str, url: str):
        self.validate(provider, url)
        if self.client is None:
            self.client = httpx.AsyncClient(
                timeout=self.settings.company_timeout_seconds,
                trust_env=False,
                follow_redirects=False,
                headers={"User-Agent": AGENT},
            )
        host = urlsplit(url).hostname
        async with self.locks.setdefault(host, asyncio.Lock()):
            await asyncio.sleep(
                max(0, self.settings.company_request_interval_seconds - (time.monotonic() - self.last.get(host, 0)))
            )
            self.last[host] = time.monotonic()
            async with self.client.stream(
                "GET", url, follow_redirects=False, timeout=self.settings.company_timeout_seconds
            ) as r:
                if r.status_code == 429:
                    delay = r.headers.get("Retry-After", "60")
                    if delay.isdigit():
                        seconds = float(delay)
                    else:
                        try:
                            until = parsedate_to_datetime(delay)
                            if until.tzinfo is None:
                                until = until.replace(tzinfo=timezone.utc)
                            seconds = max(0, (until - datetime.now(timezone.utc)).total_seconds())
                        except (TypeError, ValueError, OverflowError):
                            raise PageUnavailable("http_429_manual_review") from None
                    raise PageUnavailable("http_429", retry_after=seconds)
                if r.status_code in {401, 403}:
                    raise PageUnavailable("access_blocked")
                data = bytearray()
                async for part in r.aiter_bytes(chunk_size=8192):
                    if len(data) + len(part) > self.settings.company_max_page_bytes:
                        raise PageUnavailable("page_too_large")
                    data.extend(part)
                return r.status_code, r.headers, bytes(data).decode("utf-8", errors="replace")

    async def fetch(self, provider: str, url: str) -> str:
        self.validate(provider, url)
        host = urlsplit(url).hostname
        if host not in self.robots:
            status, _, text = await self._get(provider, "https://" + host + "/robots.txt")
            if status == 404:
                text = "User-agent: *\nAllow: /"
            elif status != 200:
                raise PageUnavailable("robots_unavailable")
            self.robots[host] = text
        if not robots_allowed(self.robots[host], url):
            raise PageUnavailable("robots_disallowed")
        for _ in range(3):
            status, headers, text = await self._get(provider, url)
            if 300 <= status < 400:
                target = urljoin(url, headers.get("Location", ""))
                self.validate(provider, target)
                if urlsplit(target).hostname != host:
                    raise PageUnavailable("redirect_requires_review")
                if not robots_allowed(self.robots[host], target):
                    raise PageUnavailable("robots_disallowed")
                url = target
                continue
            if status != 200:
                raise PageUnavailable("http_" + str(status))
            if blocked(text):
                raise PageUnavailable("captcha_or_consent")
            return text
        raise PageUnavailable("redirect_limit")

    async def render(self, provider: str, url: str) -> str:
        async with self.render_lock:
            return await self._render(provider, url)

    async def _render(self, provider: str, url: str) -> str:
        self.validate(provider, url)
        if not self.settings.company_render_enabled:
            raise PageUnavailable("browser_required")
        # Вызывается только после fetch/robots; прямой вызов тоже обязан проверить robots.
        if urlsplit(url).hostname not in self.robots or not robots_allowed(self.robots[urlsplit(url).hostname], url):
            raise PageUnavailable("robots_unavailable")
        if self.renderer:
            html = await self.renderer(url)
        else:
            browser = await asyncio.to_thread(find_browser, self.settings.company_chrome_path)
            if not browser:
                raise PageUnavailable("browser_not_found")
            temporary = await asyncio.to_thread(tempfile.TemporaryDirectory, prefix="byte-company-browser-")
            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    browser,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--user-data-dir=" + temporary.name,
                    "--dump-dom",
                    "--virtual-time-budget=" + str(self.settings.company_browser_budget_ms),
                    url,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                data = bytearray()
                async with asyncio.timeout(self.settings.company_timeout_seconds):
                    while part := await process.stdout.read(8192):
                        if len(data) + len(part) > self.settings.company_max_page_bytes:
                            raise PageUnavailable("page_too_large")
                        data.extend(part)
                    await process.wait()
                if process.returncode:
                    raise PageUnavailable("browser_failed")
                html = data.decode("utf-8", errors="replace")
            finally:
                if process is not None and process.returncode is None:
                    process.kill()
                    await process.wait()
                try:
                    await asyncio.to_thread(temporary.cleanup)
                except OSError:
                    pass
        if len(html.encode()) > self.settings.company_max_page_bytes:
            raise PageUnavailable("page_too_large")
        if blocked(html):
            raise PageUnavailable("captcha_or_consent")
        return html

    async def close(self):
        if self.owned and self.client is not None:
            await self.client.aclose()
            self.client = None
            self.robots.clear()
