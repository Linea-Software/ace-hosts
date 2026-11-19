"""Conservative public-HTML URL policy for anime discovery.

A missing robots.txt is not permission to inspect account, authentication,
service or private endpoints. Apply this filter even when robots.txt exists:
robots disallow rules remain authoritative, and this policy is an additional
denylist, not an override. Matching is heuristic and deliberately fail-closed.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, unquote, urlsplit


# Compare decoded URL *segments*, not substrings: /manga/authors and
# /series/accounting are legitimate content paths. Framework static assets
# (/_next/static/*.js, /_nuxt/*.js) remain available to the opt-in browser;
# JSON/data routes and API endpoints still fail closed.
PRIVATE_SEGMENTS = frozenset({
    ".aws", ".env", ".git", ".hg", ".svn", ".well-known",
    "actuator", "admin", "administrator", "ajax",
    "api", "auth", "authenticate", "authentication", "backend", "billing",
    "callback", "callbacks", "cart", "cdn-cgi", "cgi-bin", "checkout",
    "config", "configuration", "console", "control-panel", "csrf",
    "dashboard", "debug", "graphql", "graphiql", "api.php", "api-docs",
    "openapi", "swagger", "internal", "jwt", "login", "logout", "log-in",
    "log-out", "metrics", "oauth", "oauth2", "oidc", "orders", "password",
    "payment", "payments", "phpmyadmin", "private", "profile",
    "profiles", "register", "registration", "reset-password",
    "rest", "rpc", "secret", "secrets", "server-status", "session",
    "sessions", "settings", "sign-in", "sign-out", "sign-up",
    "signin", "signout", "signup", "status", "token", "tokens",
    "xmlrpc.php", "rpc.php", "admin.php", "install.php",
    "user", "users", "webhook", "webhooks", "wp-admin", "wp-json",
    "wp-login.php",
})
SERVICE_HOST_PREFIXES = frozenset({
    "api", "auth", "admin", "backend", "internal", "login",
    "oauth", "accounts", "account", "private",
})
PRIVATE_QUERY_KEYS = frozenset({
    "access_token", "api_key", "apikey", "auth", "authorization", "code",
    "csrf", "csrf_token", "email", "id_token", "jwt", "key", "login",
    "logout", "nonce", "password", "passwd", "refresh_token", "reset",
    "secret", "session", "sessionid", "sid", "token", "user_token",
})
PRIVATE_FILE_SUFFIXES = (
    ".bak", ".conf", ".config", ".csv", ".db", ".env", ".ini", ".json",
    ".key", ".log", ".pem", ".pfx", ".p12", ".sql", ".sqlite", ".toml",
    ".txt", ".xml", ".yaml", ".yml", ".zip",
)


def _decode_limited(value: str) -> str:
    """Expose percent-encoded path separators and nested escaping."""
    for _ in range(3):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    return value


def public_crawl_url(url: str) -> bool:
    """Allow likely public HTML URLs; reject sensitive paths and query keys.

    This filter complements robots.txt, never replaces an explicit disallow.
    It is used at enqueue time and again immediately before HTTP requests.
    """
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or
                parsed.port not in (None, 443) or
                parsed.username is not None or parsed.password is not None):
            return False
        if parsed.hostname.split(".")[0].casefold() in SERVICE_HOST_PREFIXES:
            return False
        raw_path = _decode_limited(parsed.path or "/").replace("\\", "/")
        segments = [segment.casefold() for segment in raw_path.split("/") if segment]
        if any(segment in PRIVATE_SEGMENTS or segment in {".", ".."}
               for segment in segments):
            return False
        # A versioned API (e.g. /v1/graphql or /api/v2) is blocked through
        # the service segment, regardless of its nesting depth.
        if segments and segments[-1].endswith(PRIVATE_FILE_SUFFIXES):
            # Only the single explicitly requested public sitemap is needed.
            if segments != ["sitemap.xml"]:
                return False
        query = _decode_limited(parsed.query)
        if len(query) > 720:
            return False
        for key, _value in parse_qsl(query, keep_blank_values=True, max_num_fields=64):
            if key.casefold() in PRIVATE_QUERY_KEYS:
                return False
        return True
    except (UnicodeError, ValueError):
        return False
