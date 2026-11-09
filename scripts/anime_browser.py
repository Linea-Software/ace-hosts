"""Constrained Playwright fallback for JavaScript-rendered public site metadata.

Not an anti-bot bypass. Browser contexts are ephemeral, service workers and
third-party requests are disabled, and every page is restricted to one vetted
HTTPS hostname. No logins, downloads, scrolling or recursive crawling.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from contextlib import ExitStack
from urllib.parse import urlsplit

from playwright.sync_api import Browser, Route, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from typing_extensions import Self

logger = logging.getLogger("ace-hosts.anime-browser")
MAX_RENDER_BYTES = 1_000_000
MAX_BROWSER_REQUESTS = 60


def is_public_dns(host: str) -> bool:
    """Require all currently resolved addresses to be globally routable."""
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(
            ipaddress.ip_address(address[4][0]).is_global for address in addresses
        )
    except (OSError, ValueError):
        return False


def same_origin_https(url: str, hostname: str) -> bool:
    """Avoid third-party JS, private URLs, alternative ports and redirects."""
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname == hostname
                and parsed.port in (None, 443)
                and not parsed.username and not parsed.password)
    except ValueError:
        return False


class BrowserRenderer:
    """Lazily start one Chromium process; bound the number of fallback sites."""

    def __init__(self, *, max_sites: int = 15) -> None:
        self.max_sites: int = max_sites
        self.attempts: int = 0
        self._stack: ExitStack = ExitStack()
        self._browser: Browser | None = None

    def close(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            self._stack.close()
            self._browser = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def render(self, host: str, *, timeout: float = 6) -> str | None:
        """Render one allowed homepage and return bounded HTML, or None on failure."""
        if self.attempts >= self.max_sites or not is_public_dns(host):
            return None
        self.attempts += 1
        try:
            if self._browser is None:
                manager = self._stack.enter_context(sync_playwright())
                self._browser = manager.chromium.launch(headless=True)
            context = self._browser.new_context(
                service_workers="block",
                accept_downloads=False,
                java_script_enabled=True,
            )
            try:
                requests_seen = 0

                def safe_route(route: Route) -> None:
                    nonlocal requests_seen
                    requests_seen += 1
                    request = route.request
                    if (requests_seen > MAX_BROWSER_REQUESTS or
                            not same_origin_https(request.url, host) or
                            request.resource_type in {"image", "media", "font", "websocket"}):
                        route.abort()
                    else:
                        route.continue_()

                context.route("**/*", safe_route)
                context.route_web_socket("**/*", lambda ws: ws.close())
                page = context.new_page()
                response = page.goto(f"https://{host}/", wait_until="domcontentloaded",
                                     timeout=max(1000, int(timeout * 1000)))
                if not same_origin_https(page.url, host) or response is None:
                    return None
                if response.status >= 400 or "html" not in response.headers.get("content-type", "").lower():
                    return None
                page.wait_for_timeout(500)
                html = page.content()
                if len(html.encode("utf-8")) > MAX_RENDER_BYTES:
                    raise ValueError("rendered HTML exceeds byte limit")
                return html
            finally:
                context.close()
        except (PlaywrightError, OSError, ValueError) as error:
            logger.info("browser fallback skipped %s: %s", host, error)
            return None
