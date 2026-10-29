from __future__ import annotations

import base64
import copy

import pytest
from fastapi.testclient import TestClient
from test_content_and_feedback import package

from rancho_wechat.content import import_package
from rancho_wechat.editorial import EditorialDesk, EditorialError
from rancho_wechat.editorial_review import (
    RevisionPacket,
    digest,
    import_revision,
    paragraph_diff,
    paragraphs,
    regression,
)
from rancho_wechat.exports import write_bundle
from rancho_wechat.production import run_article
from rancho_wechat.providers import ExternalWriter
from rancho_wechat.store import Store
from rancho_wechat.web import create_app


def fixture():
    # Synthetic prose with source-bound regression facts, never a real authored article.
    body = (
        "合成回归：579项完整复现结果，其中55项、约9.5%。[S1]\n\n"
        "合成数学案例：AB=AC，构造BC中点D；用BD=DC、AD公共边和边边边全等，得到底角相等。[S2]\n\n"
        "这段是合成评论，提出让读者逐步核对推理的编辑建议。"
    )
    sources = [
        {
            "ref": ref,
            "url": "https://example.org/" + ref,
            "locator": "TEST ONLY",
            "retrieved_at": "2026-10-24T00:00:00+08:00",
            "snapshot": "synthetic snapshot",
            "snapshot_sha256": digest("synthetic snapshot"),
            "verbatim": "",
        }
        for ref in ("S1", "S2")
    ]
    evidence = {
        "body_sha256": digest(body),
        "sources": sources,
        "paragraphs": [
            {
                "number": i,
                "sha256": digest(text),
                "kind": "FACT" if i < 3 else "COMMENT",
                "sources": [f"S{i}"] if i < 3 else [],
                "assertion": text if i < 3 else "",
                "note": "TEST ONLY comment" if i == 3 else "",
            }
            for i, text in enumerate(paragraphs(body), 1)
        ],
        "invariants": [
            {
                "label": "denominator must be579 findings",
                "scope": "FACT",
                "pattern": r"579项完整复现结果，其中55项、约9\.5%",
            },
            {
                "label": "midpoint, not an arbitrary point",
                "scope": "FACT",
                "pattern": "构造BC中点D",
            },
        ],
        "cases": [
            {
                "source": "S2",
                "locator": "synthetic fixture",
                "setup": "AB=AC",
                "action": "构造BC中点D",
                "reasoning": "边边边全等",
                "outcome": "底角相等",
            }
        ],
        "defined_terms": ["边边边全等"],
    }
    return body, evidence


def rebind(body, evidence):
    updated = copy.deepcopy(evidence)
    updated["body_sha256"] = digest(body)
    for text, item in zip(paragraphs(body), updated["paragraphs"], strict=False):
        item["sha256"] = digest(text)
    return updated


def test_six_dimensions_without_overall_score_or_reader_auto_pass():
    body, evidence = fixture()
    result = regression(body, evidence)
    assert len(result["checks"]) == 6
    assert "score" not in result and "overall_score" not in result
    assert result["checks"]["reader_comprehension"]["status"] == "HOLD"
    assert result["approval"] == "AUTHOR_APPROVAL_REQUIRED"
    assert all(
        value["status"] == "PASS"
        for key, value in result["checks"].items()
        if key != "reader_comprehension"
    )


@pytest.mark.parametrize(("old", "new"), [("579", "2,190"), ("中点D", "任意点D")])
def test_fact_mutation_detected_even_if_hashes_updated(old, new):
    body, evidence = fixture()
    body = body.replace(old, new)
    result = regression(body, rebind(body, evidence))
    assert result["checks"]["fact_consistency"]["status"] == "BLOCKED"


def test_repeat_missing_case_untraceable_source_and_fake_quote():
    body, evidence = fixture()
    repeated = body + "\n\n" + paragraphs(body)[0]
    assert regression(repeated, evidence)["checks"]["paragraph_repetition"]["status"] == "BLOCKED"
    missing = copy.deepcopy(evidence)
    missing["cases"][0]["reasoning"] = "not present"
    assert regression(body, missing)["checks"]["case_completeness"]["status"] == "BLOCKED"
    missing["sources"][0]["locator"] = ""
    assert regression(body, missing)["checks"]["source_traceability"]["status"] == "BLOCKED"
    forged = body.replace("编辑建议", "编辑建议：“专家已经证明所有结论错误”")
    assert (
        regression(forged, rebind(forged, evidence))["checks"]["quotation_accuracy"]["status"]
        == "BLOCKED"
    )


def editor(tmp_path):
    return EditorialDesk(
        Store(tmp_path / "editor.db"),
        ExternalWriter(),
        tmp_path / "exports",
        work_mode="EDITORIAL_FIRST",
    )


def test_idempotent_revision_import_conflict_and_original_immutable(tmp_path):
    desk = editor(tmp_path)
    article = import_package(desk, package())
    original = article["versions"][-1]
    packet = RevisionPacket(
        revision_key="revision-test-1",
        article_id=article["id"],
        expected_version_id=original["id"],
        title=original["title"],
        body=original["body"] + "\n\n这是TEST ONLY新增编辑评论，不属于作者修改。",
        summary=original["summary"],
        note="TEST ONLY AI revision",
    )
    first = import_revision(desk, packet)
    # New Store and Desk simulate an independently restarted importer.
    second = import_revision(editor(tmp_path), packet)
    assert first["current_version_id"] == second["current_version_id"]
    assert second["versions"][0]["body"] == original["body"]
    assert len(second["versions"]) == 2 and second["status"] == "IN_REVIEW"
    assert not desk.style_engine.rules() and not desk.style_signals()
    packet.body += " changed"
    with pytest.raises(EditorialError) as failure:
        import_revision(desk, packet)
    assert failure.value.code == "REVISION_CONFLICT"
    assert len(desk.article(article["id"])["versions"]) == 2


def test_editorial_mode_blocks_approval_all_exports_and_generation(tmp_path):
    desk = editor(tmp_path)
    article = import_package(desk, package())
    with pytest.raises(EditorialError, match="不允许批准"):
        desk.decide_article(article["id"], "APPROVE", fact_attested=True)
    with pytest.raises(EditorialError):
        desk.export_markdown(article["id"])
    with pytest.raises(EditorialError):
        write_bundle(desk, article["id"], approved=False)
    with pytest.raises(EditorialError):
        run_article(desk, tmp_path, tmp_path / "does-not-exist.json")
    with TestClient(create_app(desk, "synthetic-editorial-token-123456")) as client:
        client.headers["X-Editor-Token"] = "synthetic-editorial-token-123456"
        assert client.get("/api/health").json()["work_mode"] == "EDITORIAL_FIRST"
        assert (
            client.get(f"/api/articles/{article['id']}/bundle?review_only=true").status_code == 409
        )
        assert (
            client.post(
                f"/api/articles/{article['id']}/decision",
                json={"decision": "APPROVE", "fact_attested": True},
            ).status_code
            == 409
        )


def test_manual_markdown_import_twice_has_one_sample_work_and_audit(tmp_path):
    desk = editor(tmp_path)
    text = "# 合成作者导入验证\n\n" + "TEST ONLY：验证本地Markdown上传的内容和版本关系。" * 6
    payload = {
        "filename": "synthetic.md",
        "title": "合成样本，不是真实作者文章",
        "kind": "AUTHOR",
        "stage": "PUBLISHED",
        "source_url": "https://example.org/author",
        "content_b64": base64.b64encode(text.encode()).decode(),
    }
    with TestClient(create_app(desk, "synthetic-editorial-token-123456")) as client:
        client.headers["X-Editor-Token"] = "synthetic-editorial-token-123456"
        first = client.post("/api/style/samples", json=payload)
        second = client.post("/api/style/samples", json=payload)
        assert first.status_code == second.status_code == 200
        assert first.json()["id"] == second.json()["id"]
        assert second.json()["text"] == text
        wrong_kind = {**payload, "kind": "REFERENCE"}
        assert client.post("/api/style/samples", json=wrong_kind).status_code == 409
        assert (
            client.post("/api/style/samples", json={**payload, "stage": "ORIGINAL"}).status_code
            == 422
        )
        captcha = {
            **payload,
            "content_b64": base64.b64encode(
                ("<!DOCTYPE html><html>" + "captcha" * 30).encode()
            ).decode(),
        }
        assert client.post("/api/style/samples", json=captcha).status_code == 422
    with desk.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM style_samples").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM author_work_versions").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM audit WHERE action='IMPORT'").fetchone()[0] == 1


def test_paragraph_diff_preserves_removed_and_added_context():
    changes = paragraph_diff("旧事实。\n\n待删除评论。", "旧事实。\n\n有依据的新案例。")
    replaced = next(item for item in changes if item["operation"] == "replace")
    assert replaced["old_paragraphs"] == ["待删除评论。"]
    assert replaced["new_paragraphs"] == ["有依据的新案例。"]
