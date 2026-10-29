"""Small, credential-free WeChat diagnostics shared by the local entry and worker."""

from __future__ import annotations

import importlib
import ipaddress
import logging
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, quote_plus, urlsplit


def error_code(value: Any) -> int | None:
    """The token SDK preserves string codes; zero and booleans are not rejections."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value != 0 else None
    if isinstance(value, str) and re.fullmatch(r"-?\d{1,10}", value.strip()):
        return error_code(int(value))
    return None


def safe_message(value: Any, sensitive: tuple[str, ...] = ()) -> str | None:
    if not isinstance(value, str):
        return None
    text = value
    for secret in sorted((s for s in sensitive if s), key=len, reverse=True):
        for encoded in {secret, quote(secret, safe=""), quote_plus(secret)}:
            text = text.replace(encoded, "[REDACTED]")
    # Never retain a request URL, even when its query uses an unexpected key.
    text = re.sub(r"https?://[^\s<>\"']+", "[URL_REDACTED]", text, flags=re.I)
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer [REDACTED]", text)
    text = re.sub(
        r"(?i)([\"']?(?:app_?secret|access_?token|refresh_?token|secret|password|"
        r"authorization)[\"']?\s*[:=]\s*)(?:[\"'][^\"']*[\"']|[^\s,;&}]+)",
        r"\1[REDACTED]",
        text,
    )
    return "".join(c if c.isprintable() else " " for c in text)[:2048]


def error_detail(
    error: Exception,
    stage: str,
    *,
    mutation: bool = False,
    sensitive: tuple[str, ...] = (),
) -> dict[str, Any]:
    code = error_code(getattr(error, "errcode", None))
    result: dict[str, Any] = {
        "status": "REMOTE_REJECTED"
        if code is not None
        else ("UNKNOWN_REMOTE_STATUS" if mutation else "REMOTE_READ_FAILED"),
        "stage": stage,
        "errcode": code,
        # Deliberately do not stringify the exception, request or response.
        "errmsg": safe_message(getattr(error, "errmsg", None), sensitive),
        "rid": None,
        "retry": "MANUAL_ONLY"
        if code is not None
        else ("FORBIDDEN" if mutation else "BOUNDED_MANUAL_READ_ONLY"),
    }
    message = result["errmsg"] or ""
    rid = re.search(r"\brid\s*[:=]\s*([A-Za-z0-9_-]{1,128})(?![A-Za-z0-9_-])", message)
    if rid:
        result["rid"] = rid.group(1)
    if code == 40164:
        address = re.search(r"\binvalid ip\s+([\da-fA-F:.]+)", message, re.I)
        if address:
            try:
                result["wechat_observed_ip"] = str(ipaddress.ip_address(address.group(1)))
            except ValueError:
                pass
    if code is None:
        result["error_type"] = type(error).__name__
    return result


def request_route(session: Any, endpoint: str) -> dict[str, Any]:
    """Inspect effective routing without sending a request or copying proxy credentials."""
    if not hasattr(session, "merge_environment_settings"):
        return {"status": "UNAVAILABLE"}
    settings = session.merge_environment_settings(endpoint, {}, False, None, None)
    proxy = importlib.import_module("requests.utils").select_proxy(endpoint, settings["proxies"])
    parsed = urlsplit(proxy) if proxy else None
    return {
        "status": "CONFIGURED",
        "trust_env": session.trust_env,
        "proxy_host": parsed.hostname if parsed else None,
        "proxy_port": parsed.port if parsed else None,
        "proxy_credentials_present": bool(parsed and (parsed.username or parsed.password)),
    }


def configure_http(session: Any, timeout: int = 20, *, use_env_proxy: bool = False) -> None:
    """One bounded request; environment proxy routing must be selected explicitly.

    Pinned wechatpy's token GET ignores its client timeout, so enforce the bound
    at Session.request as well as on the client.
    """
    session.trust_env = use_env_proxy
    session.max_redirects = 0
    if hasattr(session, "proxies"):
        session.proxies.clear()
    if hasattr(session, "request"):
        original = session.request

        def bounded_request(*args: Any, **kwargs: Any) -> Any:
            # Session.get supplies allow_redirects=True, so defaults/partial are insufficient.
            kwargs.update(timeout=timeout, allow_redirects=False)
            # urllib3 DEBUG logs include query strings. The isolated worker records
            # its own safe receipt instead, and restores the caller's logging level.
            previous_logging = logging.root.manager.disable
            logging.disable(max(previous_logging, logging.CRITICAL))
            try:
                return original(*args, **kwargs)
            finally:
                logging.disable(previous_logging)

        session.request = bounded_request
    if hasattr(session, "adapters"):
        adapter = importlib.import_module("requests.adapters").HTTPAdapter
        session.mount("https://", adapter(max_retries=0))
        session.mount("http://", adapter(max_retries=0))
    if hasattr(session, "hooks"):
        session._rancho_http_responses = []

        def record_response(response: Any, **kwargs: Any) -> None:
            # Record the actual HTTP response metadata, never its body or request URL.
            session._rancho_http_responses.append(
                {
                    "http_status": response.status_code,
                    "received_at": datetime.now(UTC).isoformat(),
                    "elapsed_ms": round(response.elapsed.total_seconds() * 1000, 2),
                }
            )

        session.hooks.setdefault("response", []).append(record_response)
