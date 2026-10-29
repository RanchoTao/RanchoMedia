import logging
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from rancho_wechat.wechat_diagnostics import (
    configure_http,
    error_code,
    error_detail,
    safe_message,
)


@pytest.mark.parametrize("value", [True, False, None, 0, "0", "not-an-error", {}, 4.5])
def test_non_rejection_is_not_misclassified(value):
    assert error_code(value) is None


def test_redaction_preserves_ip_and_rid_without_exception_string_or_request_url():
    secret = "fake+key/key"

    class Failure(Exception):
        errcode = "40164"
        errmsg = (
            "invalid ip 203.0.113.1 ipv6 ::ffff:203.0.113.1, not in whitelist rid: abc-123 "
            f"url=https://invalid/?arbitrary={quote(secret, safe='')} "
            f"echo={secret} AppSecret='arbitrary-secret' Authorization=Bearer arbitrary-token"
        )

        def __str__(self):
            pytest.fail("must never stringify exception or request")

    result = error_detail(Failure(), "token", sensitive=(secret,))
    assert result["errcode"] == 40164 and result["rid"] == "abc-123"
    assert result["wechat_observed_ip"] == "203.0.113.1"
    assert secret not in str(result) and quote(secret, safe="") not in str(result)
    assert "https://" not in str(result)
    assert "arbitrary-secret" not in str(result) and "arbitrary-token" not in str(result)


def test_transport_errors_never_echo_their_message():
    result = error_detail(
        TimeoutError("https://invalid/?secret=never-retain"), "token", mutation=True
    )
    assert result["status"] == "UNKNOWN_REMOTE_STATUS" and result["errmsg"] is None
    assert "never-retain" not in str(result)
    assert safe_message(None) is None


def test_token_sdk_timeout_is_enforced_at_session_not_only_client():
    calls = []
    session = SimpleNamespace(
        trust_env=True,
        max_redirects=5,
        proxies={"https": "private-proxy"},
        request=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    configure_http(session)
    session.request(
        "GET", "https://api.weixin.qq.com/cgi-bin/token", allow_redirects=True, timeout=None
    )
    assert session.trust_env is False and session.max_redirects == 0 and session.proxies == {}
    assert calls[0][1] == {"timeout": 20, "allow_redirects": False}


def test_transport_logging_is_suppressed_and_restored_even_on_failure(caplog):
    def fail(*args, **kwargs):
        logging.getLogger("urllib3.connectionpool").warning("secret-query-must-not-log")
        raise TimeoutError()

    old = logging.root.manager.disable
    session = SimpleNamespace(request=fail)
    configure_http(session)
    with pytest.raises(TimeoutError):
        session.request("GET", "https://invalid/")
    assert "secret-query-must-not-log" not in caplog.text
    assert logging.root.manager.disable == old
