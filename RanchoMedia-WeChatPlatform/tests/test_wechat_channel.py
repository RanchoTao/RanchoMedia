"""Synthetic SDK contracts only; not a real remote WeChat draft receipt."""

from types import SimpleNamespace

from rancho_wechat.wechat_channel import WechatDraftChannel


def configured(monkeypatch, sdk):
    monkeypatch.setenv("WECHAT_TEST_APPID", "wxSYNTHETIC")
    monkeypatch.setenv("WECHAT_TEST_ACCESS_TOKEN", "SYNTHETIC_SECRET_NOT_REAL")
    return WechatDraftChannel(
        "synthetic-account",
        enabled=True,
        authorized_by="SYNTHETIC_TEST_ONLY",
        app_id_env="WECHAT_TEST_APPID",
        access_token_env="WECHAT_TEST_ACCESS_TOKEN",
        client=sdk,
    )


def request():
    return {
        "articles": [
            {
                "title": "TEST ONLY draft",
                "content": "<p>Synthetic content.</p>",
                "thumb_media_id": "synthetic-permanent-cover-id",
                "author": "RanchoMedia 编辑部",
                "need_open_comment": True,
            }
        ]
    }


def test_missing_auth_or_credentials_never_calls_sdk(monkeypatch):
    calls = []
    sdk = SimpleNamespace(draft=SimpleNamespace(add=lambda *a: calls.append(a)))
    assert (
        WechatDraftChannel("test", client=sdk).add_draft(request())["status"]
        == "WAITING_AUTHORIZATION"
    )
    channel = configured(monkeypatch, sdk)
    monkeypatch.delenv("WECHAT_TEST_ACCESS_TOKEN")
    assert channel.add_draft(request())["status"] == "WAITING_CREDENTIALS"
    assert not calls


def test_add_get_list_are_distinct_and_no_public_url(monkeypatch):
    calls = []
    sdk = SimpleNamespace(
        draft=SimpleNamespace(
            add=lambda articles: calls.append(articles) or {"media_id": "synthetic-draft-id"},
            get=lambda media_id: {"news_item": request()["articles"]},
            batchget=lambda **kw: {"item": [{"media_id": "synthetic-draft-id"}], "total_count": 1},
        )
    )
    channel = configured(monkeypatch, sdk)
    assert channel.capabilities()["status"] == "CONFIGURED_UNVERIFIED"
    added = channel.add_draft(request())
    assert added["status"] == "REMOTE_DRAFT_CREATED"
    assert added["remote_id"] == "synthetic-draft-id" and added["remote_url"] is None
    assert calls[0][0]["need_open_comment"] is False
    assert channel.get_draft(added["remote_id"])["status"] == "REMOTE_DRAFT_CONFIRMED"
    assert channel.list_drafts()["status"] == "REMOTE_DRAFT_LIST_READ"
    assert len(calls) == 1


def test_mutation_timeout_is_unknown_and_not_retried_or_leaked(monkeypatch):
    calls = []

    def fail(articles):
        calls.append(articles)
        raise TimeoutError("https://example.invalid/?access_token=SYNTHETIC_SECRET_NOT_REAL")

    channel = configured(monkeypatch, SimpleNamespace(draft=SimpleNamespace(add=fail)))
    result = channel.add_draft(request())
    assert result["status"] == "UNKNOWN_REMOTE_STATUS"
    assert result["remote_id"] is None and len(calls) == 1
    assert "SYNTHETIC_SECRET_NOT_REAL" not in str(result)


def test_missing_media_id_cannot_be_success(monkeypatch):
    channel = configured(monkeypatch, SimpleNamespace(draft=SimpleNamespace(add=lambda x: {})))
    assert channel.add_draft(request())["status"] == "UNKNOWN_REMOTE_STATUS"


def test_explicit_rejection_not_inferred_permission_and_no_retry(monkeypatch):
    class PermissionError(Exception):
        errcode = 48001

    calls = []

    def fail(articles):
        calls.append(articles)
        raise PermissionError("private error body")

    channel = configured(monkeypatch, SimpleNamespace(draft=SimpleNamespace(add=fail)))
    result = channel.add_draft(request())
    assert result["status"] == "CAPABILITY_BLOCKED"
    assert result["evidence"]["errcode"] == 48001 and len(calls) == 1
    assert "private error body" not in str(result)


def test_cover_and_personal_author_require_validation(monkeypatch):
    calls = []
    channel = configured(
        monkeypatch, SimpleNamespace(draft=SimpleNamespace(add=lambda a: calls.append(a)))
    )
    data = request()
    del data["articles"][0]["thumb_media_id"]
    assert channel.add_draft(data)["status"] == "VALIDATION_BLOCKED"
    data = request()
    data["articles"][0]["author"] = "unapproved-personal-author"
    assert channel.add_draft(data)["status"] == "VALIDATION_BLOCKED"
    assert not calls
