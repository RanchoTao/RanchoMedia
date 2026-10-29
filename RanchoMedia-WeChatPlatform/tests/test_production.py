from __future__ import annotations

import json
from datetime import datetime

import pytest
from test_content_and_feedback import package

from rancho_wechat import production
from rancho_wechat.editorial import EditorialDesk, EditorialError
from rancho_wechat.providers import ExternalWriter
from rancho_wechat.quality import quality_signals
from rancho_wechat.store import Store


def inputs(tmp_path):
    data = package()
    brief = production.WritingBrief(
        topic_title=data.topic_title,
        angle="TEST ONLY：基于合成证据解释同一个具体问题。",
        sources=data.sources,
        claims=data.claims,
    )
    path = tmp_path / "brief.json"
    path.write_text(brief.model_dump_json(), encoding="utf-8")
    paragraphs = [
        f"第{i}个具体问题，" + "只讨论合成资料支持的框架边界，核对实际执行与观察。" * 3
        for i in range(22)
    ]
    data.article.body = "\n\n".join(paragraphs) + "[S1]"
    data.research_note *= 2
    return path, data


def desk(tmp_path):
    return EditorialDesk(Store(tmp_path / "db.sqlite"), ExternalWriter(), tmp_path / "exports")


def fake_runner(tmp_path, data, calls):
    def run(store, root, prompt, schema, **kwargs):
        calls.append(prompt.read_text(encoding="utf-8"))
        answer = tmp_path / "answer.json"
        answer.write_text(
            json.dumps(
                {
                    "research_note": data.research_note,
                    "article": data.article.model_dump(),
                    "fact_check": data.fact_check.model_dump(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return {"status": "SUCCEEDED", "output": str(answer), "run_key": "SYNTHETIC"}

    return run


def test_article_pipeline_import_and_restart_idempotency(tmp_path, monkeypatch):
    path, data = inputs(tmp_path)
    calls = []
    monkeypatch.setattr(production, "run_once", fake_runner(tmp_path, data, calls))
    first = production.run_article(desk(tmp_path), tmp_path, path)
    second = production.run_article(desk(tmp_path), tmp_path, path)
    assert first["article_id"] == second["article_id"]
    editor = desk(tmp_path)
    article = editor.article(first["article_id"])
    assert article["status"] == "IN_REVIEW"
    assert article["approved_version_id"] is None
    assert len(article["versions"]) == 1
    assert "TEST ONLY" in calls[0] and "CONFIRMED" not in calls[0]
    assert first["public_publish_enabled"] is False
    with editor.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 1
    changed = json.loads(path.read_text(encoding="utf-8"))
    changed["angle"] += "更改选题，禁止静默覆盖。"
    path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(EditorialError, match="同日已有不同选题"):
        production.run_article(editor, tmp_path, path)
    assert len(calls) == 2  # Runner's cache is independently tested; no new subprocess here.


def test_short_output_retained_but_not_imported(tmp_path, monkeypatch):
    path, data = inputs(tmp_path)
    data.article.body = "合成短稿中的事实不能变成作者批准。[S1]" * 12
    monkeypatch.setattr(production, "run_once", fake_runner(tmp_path, data, []))
    editor = desk(tmp_path)
    with pytest.raises(EditorialError) as failure:
        production.run_article(editor, tmp_path, path)
    assert failure.value.code == "CONTENT_QUALITY_BLOCKED"
    assert list((tmp_path / "production").glob("*/content.json"))
    with editor.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0


def test_unknown_outcome_never_imported(tmp_path, monkeypatch):
    path, _ = inputs(tmp_path)
    monkeypatch.setattr(
        production, "run_once", lambda *args, **kwargs: {"status": "UNKNOWN_TIMEOUT"}
    )
    editor = desk(tmp_path)
    with pytest.raises(EditorialError, match="不自动重试"):
        production.run_article(editor, tmp_path, path)
    with editor.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0


def test_invalid_evidence_prevents_spending(tmp_path, monkeypatch):
    path, _ = inputs(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["claims"][0]["quote"] = "FAKE QUOTE NOT IN SOURCE"
    path.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setattr(production, "run_once", lambda *args, **kwargs: pytest.fail("quota spent"))
    with pytest.raises(ValueError):
        production.run_article(desk(tmp_path), tmp_path, path)


def test_one_call_cannot_claim_independent_review(tmp_path, monkeypatch):
    path, data = inputs(tmp_path)
    data.fact_check.independence = "SEPARATE_REVIEW"
    monkeypatch.setattr(production, "run_once", fake_runner(tmp_path, data, []))
    with pytest.raises(ValueError, match="不能自称独立审稿"):
        production.run_article(desk(tmp_path), tmp_path, path)


def test_time_trigger_not_due_then_due(tmp_path, monkeypatch):
    class Clock:
        @staticmethod
        def now(tz):
            return datetime(2026, 10, 21, 8, 10, tzinfo=tz)

    monkeypatch.setattr(production, "datetime", Clock)
    calls = []
    monkeypatch.setattr(
        production, "run_article", lambda *args, **kw: calls.append(kw) or {"status": "DRAFT_READY"}
    )
    assert production.scheduled_tick(None, tmp_path, tmp_path, "08:11")["status"] == "NOT_DUE"
    assert not calls
    assert production.scheduled_tick(None, tmp_path, tmp_path, "08:10")["status"] == "DRAFT_READY"
    assert len(calls) == 1


def test_profile_separates_published_samples_and_preferences(tmp_path):
    editor = desk(tmp_path)
    assert editor.style_engine.profile()["published_author_samples"] == []
    sample = tmp_path / "author.md"
    sample.write_text("# TEST ONLY\n\n这是合成作品，不能冒充真实作者样本。" * 4, encoding="utf-8")
    imported = editor.import_style(sample, "合成作者样本")
    editor.style_engine.attach_work(imported["id"], stage="PUBLISHED")
    explicit = editor.style_engine.add_rule(
        "ending", "TEST ONLY: 结尾用具体问题。", {"author_explicit": True}, confirmed=True
    )
    editor.style_engine.add_rule(
        "humor", "TEST ONLY: 可能保留克制反讽。", {"sample_id": imported["id"]}
    )
    profile = editor.style_engine.profile()
    assert len(profile["published_author_samples"]) == 1
    assert len(profile["unverified_inferences"]) == 1
    assert profile["confirmed_preferences"][0]["id"] == explicit["id"]


def test_quality_flags_are_not_approval_or_truth():
    result = quality_signals("总而言之，合成示例。[S9]\n\n重复。\n\n重复。", {"S1"})
    assert len(result["blockers"]) == 3
    assert result["signals_for_editor"]
    assert result["author_approved"] is False


def test_strict_cli_schema_has_no_freeform_objects():
    schema = production.strict_schema()

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node["properties"]) == set(node["required"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)


def test_annotated_ids_normalized_without_touching_prose(tmp_path, monkeypatch):
    path, data = inputs(tmp_path)
    body = data.article.body
    data.article.claims_used = ["C1：模型输出附加了描述，但引用的ID仍存在。"]
    monkeypatch.setattr(production, "run_once", fake_runner(tmp_path, data, []))
    editor = desk(tmp_path)
    result = production.run_article(editor, tmp_path, path)
    assert editor.article(result["article_id"])["versions"][-1]["body"] == body
    normalization = next((tmp_path / "production").glob("*/normalization.json"))
    assert json.loads(normalization.read_text(encoding="utf-8"))["body_changed"] is False


def test_unknown_annotated_id_rejected(tmp_path, monkeypatch):
    path, data = inputs(tmp_path)
    data.article.claims_used = ["C9：未经提供的事实。"]
    monkeypatch.setattr(production, "run_once", fake_runner(tmp_path, data, []))
    with pytest.raises(ValueError, match="未知事实"):
        production.run_article(desk(tmp_path), tmp_path, path)


def test_changed_persisted_input_cannot_spend_again(tmp_path, monkeypatch):
    path, data = inputs(tmp_path)
    calls = []
    monkeypatch.setattr(production, "run_once", fake_runner(tmp_path, data, calls))
    editor = desk(tmp_path)
    production.run_article(editor, tmp_path, path)
    prompt = next((tmp_path / "production").glob("*/prompt.txt"))
    prompt.write_text("Tampered prompt", encoding="utf-8")
    with pytest.raises(EditorialError) as failure:
        production.run_article(editor, tmp_path, path)
    assert failure.value.code == "PRODUCTION_INPUT_CHANGED"
    assert len(calls) == 1


def test_previously_covered_topic_skips_before_model_call(tmp_path, monkeypatch):
    from rancho_wechat.content import import_package

    path, data = inputs(tmp_path)
    editor = desk(tmp_path)
    original = import_package(editor, data)
    monkeypatch.setattr(
        production, "run_once", lambda *a, **k: pytest.fail("duplicate topic spent quota")
    )
    result = production.run_article(editor, tmp_path, path)
    assert result["status"] == "SKIPPED_ALREADY_COVERED"
    assert result["article_id"] == original["id"]
    assert result["additional_model_calls"] == 0
