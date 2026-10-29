from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from rancho_wechat.content import ContentPackage, import_package
from rancho_wechat.editorial import EditorialDesk, EditorialError
from rancho_wechat.exports import register_asset, write_bundle
from rancho_wechat.providers import ExternalWriter, MockProvider
from rancho_wechat.store import Store
from rancho_wechat.web import create_app


def package() -> ContentPackage:
    stamp = datetime.now(UTC).isoformat()
    quote = "The synthetic CORE implements an audit queue and excludes private research results."
    return ContentPackage.model_validate(
        {
            "schema_version": "rancho-wechat.content/1.1",
            "package_id": str(uuid4()),
            "execution": {
                "executor": "codex-session",
                "generated_at": stamp,
                "model": "SYNTHETIC CONTRACT FIXTURE",
                "note": "TEST ONLY, not actual Codex writing evidence",
                "style_sample_status": "UNAVAILABLE_USER_CONSTRAINTS_ONLY",
            },
            "topic_title": "合成测试：公开工程的证据边界",
            "topic_idea": "测试原始资料和待审导入",
            "origin": "MANUAL",
            "sources": [
                {
                    "ref": "S1",
                    "title": "Synthetic original source",
                    "url": "https://example.org/core",
                    "excerpt": quote,
                    "kind": "PRIMARY",
                    "retrieved_at": stamp,
                }
            ],
            "claims": [
                {
                    "ref": "C1",
                    "source_ref": "S1",
                    "quote": quote,
                    "epistemic_status": "SOURCE_REPORT",
                    "note": "Only asserts what documentation says",
                }
            ],
            "research_note": "测试资料说明框架机制已经出现，但没有给出数学结果。"
            "本记录只演示结构和状态边界，不应被称为实际科研验证。",
            "article": {
                "title_options": ["合成测试：公开工程的证据边界"],
                "body": "# 合成测试：公开工程的证据边界\n\n"
                + (
                    "源码可以展示一个系统如何记录失败，不能代替对数学命题的证明。"
                    "材料只说明审计框架的范围，文章判断需要作者逐项确认。[S1]\n\n" * 5
                )
                + "总而言之，这是一份测试材料。",
                "summary": "合成测试材料，不是实际文章。",
                "outline": ["资料说明", "作者判断"],
                "cover_suggestions": [],
                "image_manifest": [],
                "claims_used": ["C1"],
            },
            "fact_check": {
                "status": "COMPLETE",
                "findings": [],
                "note": "TEST ONLY, not independent scientific verification",
                "independence": "SAME_SESSION_SELF_CHECK",
            },
        }
    )


def desk(tmp_path: Path) -> EditorialDesk:
    return EditorialDesk(Store(tmp_path / "db.sqlite"), ExternalWriter(), tmp_path / "exports")


def test_atomic_idempotent_real_artifact_import_and_approval_gate(tmp_path: Path):
    editor = desk(tmp_path)
    data = package()
    article = import_package(editor, data)
    assert article["status"] == "IN_REVIEW"
    assert article["versions"][0]["fact_report"]["generation_provider"] == "codex-session"
    assert import_package(editor, data)["id"] == article["id"]
    assert len(editor.article(article["id"])["versions"]) == 1
    data.article.summary = "同一个包 ID 发生了修改，必须拒绝覆盖。"
    with pytest.raises(EditorialError) as error:
        import_package(editor, data)
    assert error.value.code == "PACKAGE_CONFLICT"
    with pytest.raises(EditorialError):
        write_bundle(editor, article["id"])
    review = write_bundle(editor, article["id"], approved=False)
    with zipfile.ZipFile(review) as archive:
        assert "NOT_PUBLISHED" in archive.read("article.md").decode()
        receipt = json.loads(archive.read("receipt.json"))
        assert receipt["author_approved"] is False
        assert receipt["platform_acceptance"] == "MANUAL_ACCEPTANCE_REQUIRED"
    editor.decide_article(article["id"], "APPROVE", fact_attested=True)
    bundle = write_bundle(editor, article["id"])
    with zipfile.ZipFile(bundle) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read("manifest.json"))
        assert all(
            hashlib.sha256(archive.read(name)).hexdigest() == digest
            for name, digest in manifest.items()
        )
        assert json.loads(archive.read("images.json"))["images_uploaded"] is False


def test_edit_feedback_explicit_confirmation_and_optimistic_version(tmp_path: Path):
    editor = desk(tmp_path)
    article = import_package(editor, package())
    before = article["versions"][0]
    after = editor.edit(
        article["id"],
        before["title"],
        before["body"].replace("总而言之，这是一份测试材料。", "留下一个具体问题？"),
        before["summary"],
        "删除模板总结",
        expected_version_id=before["id"],
        explicit_preferences=["结尾留在具体问题，不再重复全篇总结"],
    )
    report = editor.style_engine.feedback(article["id"])
    assert report["versions"][0]["deleted"]
    assert report["versions"][0]["added"]
    confirmed = editor.style_context()["confirmed_rules"]
    assert len(confirmed) == 1 and confirmed[0]["text"].startswith("结尾")
    candidates = [rule for rule in report["rules"] if rule["status"] == "CANDIDATE"]
    assert candidates
    editor.style_engine.decide(candidates[0]["id"], "CONFIRM")
    assert len(editor.style_context()["confirmed_rules"]) == 2
    editor.style_engine.decide(candidates[0]["id"], "REVOKE")
    assert len(editor.style_context()["confirmed_rules"]) == 1
    with pytest.raises(EditorialError) as error:
        editor.edit(
            article["id"],
            before["title"],
            before["body"],
            before["summary"],
            "stale",
            expected_version_id=before["id"],
        )
    assert error.value.code == "VERSION_CONFLICT"
    with pytest.raises(EditorialError) as approval:
        editor.decide_article(
            article["id"], "APPROVE", fact_attested=True, expected_version_id=before["id"]
        )
    assert approval.value.code == "VERSION_CONFLICT"
    assert after["current_version_id"] != before["id"]


def test_news_freshness_is_rechecked_at_approval(tmp_path: Path):
    editor = desk(tmp_path)
    data = package()
    data.origin = "NEWS"
    data.news_published_at = datetime.now(UTC)
    article = import_package(editor, data)
    with editor.store.write() as db:
        db.execute(
            "UPDATE topics SET news_published_at='2020-01-01T00:00:00+00:00' WHERE id=?",
            (article["topic_id"],),
        )
    with pytest.raises(EditorialError) as error:
        editor.decide_article(article["id"], "APPROVE", fact_attested=True)
    assert error.value.code == "NEWS_FRESHNESS_REVIEW_REQUIRED"


def test_work_lineage_and_model_candidate_rules_never_auto_confirm(tmp_path: Path):
    editor = desk(tmp_path)
    original = tmp_path / "original.md"
    original.write_text(
        "# SYNTHETIC AUTHOR SAMPLE\n\n" + "先摆出可定位的证据，再讨论它没有回答的问题。" * 12,
        encoding="utf-8",
    )
    first = editor.import_style(original, "合成样本")
    editor.style_engine.attach_work(first["id"], stage="ORIGINAL")
    candidates = editor.style_engine.analyze(first["id"], MockProvider())
    assert candidates and not editor.style_context()["confirmed_rules"]
    original.write_text(
        original.read_text(encoding="utf-8") + "\n\n作者新的结尾。", encoding="utf-8"
    )
    second = editor.import_style(original, "合成修改稿")
    relation = editor.style_engine.attach_work(
        second["id"], parent_sample_id=first["id"], stage="AUTHOR_EDIT"
    )
    assert relation["parent_sample_id"] == first["id"]
    assert len(editor.style_engine.works()) == 2


def test_content_schema_rejects_fake_source_future_date_and_approval_injection():
    data = package().model_dump(mode="json")
    data["claims"][0]["quote"] = "invented quotation"
    with pytest.raises(ValidationError):
        ContentPackage.model_validate(data)
    data = package().model_dump(mode="json")
    data["status"] = "APPROVED"
    with pytest.raises(ValidationError):
        ContentPackage.model_validate(data)
    data = package().model_dump(mode="json")
    data["sources"][0]["retrieved_at"] = "2999-01-01T00:00:00Z"
    with pytest.raises(ValidationError):
        ContentPackage.model_validate(data)


def test_web_real_import_edit_and_bundle_flow(tmp_path: Path):
    editor = desk(tmp_path)
    client = TestClient(create_app(editor, "a" * 32))
    auth = {"X-Editor-Token": "a" * 32}
    created = client.post(
        "/api/content/import", headers=auth, json=package().model_dump(mode="json")
    )
    assert created.status_code == 200
    article = created.json()
    assert client.get(f"/api/articles/{article['id']}/bundle", headers=auth).status_code == 409
    assert (
        client.get(
            f"/api/articles/{article['id']}/bundle?review_only=true", headers=auth
        ).status_code
        == 200
    )
    generated = client.post(f"/api/topics/{article['topic_id']}/draft", headers=auth, json={})
    assert generated.status_code == 409
    assert generated.json()["code"] in {"EXTERNAL_WRITER_REQUIRED", "RESEARCH_REQUIRED"}


def test_asset_hash_gate_and_ai_revision_not_author_learning(tmp_path: Path):
    import struct

    editor = desk(tmp_path)
    article = import_package(editor, package())
    original = article["versions"][0]
    revised = editor.edit(
        article["id"],
        original["title"],
        original["body"] + "\n\nAI 候选增加的结尾？",
        original["summary"],
        "AI fixture revision",
        kind="AI_REVISION",
    )
    assert revised["versions"][-1]["kind"] == "AI_REVISION"
    assert not editor.style_engine.rules()
    assert not editor.style_engine.feedback(article["id"])["versions"][0]["patterns"]
    editor.decide_article(article["id"], "APPROVE", fact_attested=True)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" + struct.pack(">II", 10, 10) + b"\x00" * 20
    asset = register_asset(
        editor, article["id"], png, "TEST PNG header fixture", "cover", "TEST ONLY original fixture"
    )
    assert editor.article(article["id"])["status"] == "IN_REVIEW"
    Path(asset["path"]).write_bytes(b"tampered")
    with pytest.raises(EditorialError) as error:
        write_bundle(editor, article["id"], approved=False)
    assert error.value.code == "ASSET_INTEGRITY_FAILED"
