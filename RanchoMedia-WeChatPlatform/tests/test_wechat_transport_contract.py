"""Pinned SDK exercised against an in-memory adapter; never uses the network."""

import json

import pytest

requests = pytest.importorskip("requests")
wechatpy = pytest.importorskip("wechatpy")

from rancho_wechat.wechat_diagnostics import (  # noqa: E402
    configure_http,
    error_detail,
    request_route,
)


@pytest.mark.parametrize("errcode", [40164, "40164"])
def test_pinned_token_transport_is_single_bounded_and_normalizes_rejections(monkeypatch, errcode):
    calls = []

    class MemoryAdapter(requests.adapters.BaseAdapter):
        def send(self, request, **kwargs):
            calls.append(kwargs)
            result = requests.Response()
            result.status_code = 200
            result.request = request
            result._content = json.dumps(
                {
                    "errcode": errcode,
                    "errmsg": "invalid ip 203.0.113.7, not in whitelist rid: synthetic-sdk-rid",
                }
            ).encode()
            return result

        def close(self):
            pass

    monkeypatch.setenv("HTTPS_PROXY", "http://invalid-proxy.invalid:1")
    client = wechatpy.WeChatClient("wxSYNTHETIC", "SYNTHETIC_SECRET", auto_retry=False, timeout=20)
    configure_http(client._http)
    assert client._http.adapters["https://"].max_retries.total == 0
    client._http.mount("https://", MemoryAdapter())
    with pytest.raises(wechatpy.exceptions.WeChatClientException) as caught:
        client.fetch_access_token()
    result = error_detail(caught.value, "token", sensitive=("SYNTHETIC_SECRET",))
    assert result["errcode"] == 40164 and result["status"] == "REMOTE_REJECTED"
    assert result["rid"] == "synthetic-sdk-rid"
    assert len(calls) == 1 and calls[0]["timeout"] == 20 and not calls[0]["proxies"]
    assert "SYNTHETIC_SECRET" not in str(result) and "https://" not in str(result)


def test_session_get_cannot_override_no_redirect_or_timeout_policy():
    calls = []

    class RedirectAdapter(requests.adapters.BaseAdapter):
        def send(self, request, **kwargs):
            calls.append(kwargs)
            response = requests.Response()
            response.status_code = 302
            response.headers["Location"] = "https://never-follow.invalid/"
            response.request = request
            response._content = b""
            return response

        def close(self):
            pass

    session = requests.Session()
    configure_http(session)
    session.mount("https://", RedirectAdapter())
    # requests also enforces max_redirects while preparing its next-request hint.
    with pytest.raises(requests.exceptions.TooManyRedirects):
        session.get("https://api.weixin.qq.com/", timeout=None, allow_redirects=True)
    assert len(calls) == 1 and calls[0]["timeout"] == 20


def test_explicit_proxy_path_matches_for_echo_and_wechat_and_never_discloses_proxy_password(
    monkeypatch,
):
    monkeypatch.setenv("HTTPS_PROXY", "http://user:SYNTHETIC_PASSWORD@127.0.0.1:7897")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    session = requests.Session()
    configure_http(session, use_env_proxy=True)
    wechat_route = request_route(session, "https://api.weixin.qq.com/cgi-bin/token")
    echo_route = request_route(session, "https://api.ipify.org")
    assert wechat_route == echo_route
    assert wechat_route["proxy_host"] == "127.0.0.1" and wechat_route["proxy_port"] == 7897
    assert wechat_route["proxy_credentials_present"] is True
    assert "SYNTHETIC_PASSWORD" not in str(wechat_route)
    assert session.adapters["https://"].max_retries.total == 0
