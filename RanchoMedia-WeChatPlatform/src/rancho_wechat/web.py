"""Loopback author desk. JSON mutations require a local token; no publish endpoint exists."""

import base64
import binascii
import secrets
import tempfile
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from rancho_wechat.api_provider import CompatibleProvider, ModelError
from rancho_wechat.content import ContentPackage, import_package
from rancho_wechat.editorial import EditorialDesk, EditorialError
from rancho_wechat.exports import write_bundle
from rancho_wechat.network import Director, network_root
from rancho_wechat.papers import discover_arxiv


class TopicInput(BaseModel):
    title: str
    idea: str = ""


class NetworkPause(BaseModel):
    paused: bool
    channel: str | None = None


class Decision(BaseModel):
    decision: str
    note: str = ""
    fact_attested: bool = False
    expected_version_id: str | None = None


class SourceInput(BaseModel):
    title: str
    url: str
    excerpt: str
    kind: str = "PRIMARY"
    published_at: str | None = None


class DraftInput(BaseModel):
    angle: str = "main"


class PaperQuery(BaseModel):
    query: str


class EditInput(BaseModel):
    title: str
    body: str
    summary: str = ""
    note: str = ""
    explicit_preferences: list[str] = Field(default_factory=list, max_length=10)
    expected_version_id: str | None = None


class RevisionInput(BaseModel):
    instruction: str = Field(min_length=4, max_length=2000)


class RuleInput(BaseModel):
    category: str
    text: str = Field(min_length=4, max_length=500)
    version_id: str | None = None


class SampleInput(BaseModel):
    filename: str
    title: str
    kind: str = "AUTHOR"
    content_b64: str = Field(max_length=3_000_000)
    stage: str = "ORIGINAL"
    work_id: str | None = None
    parent_sample_id: str | None = None
    article_version_id: str | None = None
    source_url: str | None = None


def create_app(desk: EditorialDesk, editor_token: str) -> FastAPI:
    if len(editor_token) < 24:
        raise ValueError("Editor token is too short")
    app = FastAPI(title="RanchoMedia WeChat Editor", version="1.3.0")

    def authorize(x_editor_token: Annotated[str | None, Header()] = None) -> None:
        if x_editor_token is None or not secrets.compare_digest(x_editor_token, editor_token):
            raise HTTPException(401, "需要本地编辑台令牌")

    Gate = Annotated[None, Depends(authorize)]

    @app.exception_handler(EditorialError)
    async def editorial_error(_: object, error: EditorialError) -> object:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"code": error.code, "message": error.message})

    @app.exception_handler(ModelError)
    async def model_error(_: object, error: ModelError) -> object:
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=409, content={"code": error.code, "message": error.message})

    @app.exception_handler(ValueError)
    async def invalid_input(_: object, error: ValueError) -> object:
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=422, content={"message": "输入无效，请检查内容格式、风格类别或作品关系"}
        )

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (Path(__file__).parent / "index.html").read_text(encoding="utf-8")

    @app.get("/api/health")
    def health(_: Gate) -> dict[str, str | bool]:
        return {
            "status": "ready",
            "provider": desk.provider.name,
            "work_mode": desk.work_mode,
            "public_publish_enabled": False,
        }

    @app.get("/api/model")
    def model_status(_: Gate) -> dict[str, object]:
        if isinstance(desk.provider, CompatibleProvider):
            return {"config": desk.provider.doctor(), "usage": desk.provider.usage()}
        return {
            "provider": desk.provider.name,
            "independent_programmatic_generation": desk.provider.name
            not in {"codex-import", "mock-fixture"},
            "billing_verified": False,
        }

    @app.post("/api/content/import")
    def content_import(item: ContentPackage, _: Gate) -> dict[str, object]:
        return import_package(desk, item)

    @app.get("/api/topics")
    def topics(_: Gate) -> list[dict[str, object]]:
        return desk.list_topics()

    @app.post("/api/topics")
    def create_topic(item: TopicInput, _: Gate) -> dict[str, object]:
        return desk.create_topic(item.title, item.idea)

    @app.put("/api/topics/{topic_id}")
    def update_topic(topic_id: str, item: TopicInput, _: Gate) -> dict[str, object]:
        return desk.update_topic(topic_id, item.title, item.idea)

    @app.post("/api/daily")
    def daily(_: Gate) -> dict[str, object]:
        return desk.daily(date.today())

    @app.post("/api/prepare")
    def prepare(_: Gate) -> list[dict[str, str]]:
        return desk.prepare_accepted()

    @app.post("/api/topics/{topic_id}/decision")
    def topic_decision(topic_id: str, item: Decision, _: Gate) -> dict[str, object]:
        return desk.decide_topic(topic_id, item.decision)

    @app.get("/api/topics/{topic_id}/sources")
    def sources(topic_id: str, _: Gate) -> list[dict[str, object]]:
        return desk.sources(topic_id)

    @app.post("/api/topics/{topic_id}/sources")
    def add_source(topic_id: str, item: SourceInput, _: Gate) -> dict[str, object]:
        return desk.add_source(
            item.title, item.url, item.excerpt, item.kind, topic_id, item.published_at
        )

    @app.post("/api/topics/{topic_id}/papers")
    def papers(topic_id: str, item: PaperQuery, _: Gate) -> list[dict[str, object]]:
        return discover_arxiv(desk, topic_id, item.query)

    @app.get("/api/topics/{topic_id}/claims")
    def claims(topic_id: str, _: Gate) -> list[dict[str, object]]:
        return desk.claims(topic_id)

    @app.post("/api/topics/{topic_id}/research")
    def research(topic_id: str, _: Gate) -> dict[str, object]:
        return desk.research(topic_id)

    @app.post("/api/topics/{topic_id}/draft")
    def draft(topic_id: str, item: DraftInput, _: Gate) -> dict[str, object]:
        return desk.generate(topic_id, item.angle)

    @app.get("/api/articles")
    def articles(_: Gate) -> list[dict[str, object]]:
        return desk.list_articles()

    @app.get("/api/articles/{article_id}")
    def article(article_id: str, _: Gate) -> dict[str, object]:
        return desk.article(article_id)

    @app.put("/api/articles/{article_id}")
    def edit(article_id: str, item: EditInput, _: Gate) -> dict[str, object]:
        return desk.edit(
            article_id,
            item.title,
            item.body,
            item.summary,
            item.note,
            explicit_preferences=item.explicit_preferences,
            expected_version_id=item.expected_version_id,
        )

    @app.post("/api/articles/{article_id}/revise")
    def revise(article_id: str, item: RevisionInput, _: Gate) -> dict[str, object]:
        return desk.revise_article(article_id, item.instruction)

    @app.post("/api/articles/{article_id}/model-review")
    def model_review(article_id: str, _: Gate) -> dict[str, object]:
        return desk.review_article(article_id)

    @app.get("/api/articles/{article_id}/feedback")
    def feedback(article_id: str, _: Gate) -> dict[str, object]:
        return desk.style_engine.feedback(article_id)

    @app.post("/api/articles/{article_id}/decision")
    def article_decision(article_id: str, item: Decision, _: Gate) -> dict[str, object]:
        return desk.decide_article(
            article_id, item.decision, item.note, item.fact_attested, item.expected_version_id
        )

    @app.get("/api/articles/{article_id}/audit")
    def article_audit(article_id: str, _: Gate) -> list[dict[str, object]]:
        return desk.audit("article", article_id)

    @app.get("/api/articles/{article_id}/export")
    def export(article_id: str, _: Gate) -> FileResponse:
        target = desk.export_markdown(article_id)
        return FileResponse(target, media_type="text/markdown; charset=utf-8", filename=target.name)

    @app.get("/api/articles/{article_id}/bundle")
    def bundle(article_id: str, _: Gate, review_only: bool = False) -> FileResponse:
        target = write_bundle(desk, article_id, approved=not review_only)
        return FileResponse(target, media_type="application/zip", filename=target.name)

    @app.get("/api/style")
    def style(_: Gate) -> dict[str, object]:
        return {
            "context": desk.style_context(),
            "signals": desk.style_signals(),
            "rules": desk.style_engine.rules(),
            "works": desk.style_engine.works(),
        }

    @app.post("/api/style/rules")
    def add_rule(item: RuleInput, _: Gate) -> dict[str, object]:
        return desk.style_engine.add_rule(
            item.category,
            item.text,
            {"version_id": item.version_id, "author_explicit": True},
            confirmed=True,
        )

    @app.post("/api/style/rules/{rule_id}/decision")
    def rule_decision(rule_id: str, item: Decision, _: Gate) -> dict[str, bool]:
        desk.style_engine.decide(rule_id, item.decision)
        return {"saved": True}

    @app.post("/api/style/samples/{sample_id}/analyze")
    def analyze(sample_id: str, _: Gate) -> list[dict[str, object]]:
        return desk.style_engine.analyze(sample_id, desk.provider)

    @app.post("/api/style/samples")
    def style_sample(item: SampleInput, _: Gate) -> dict[str, object]:
        suffix = Path(item.filename).suffix.lower()
        if suffix not in {".md", ".txt", ".docx"}:
            raise EditorialError("INVALID_SAMPLE_KIND", "仅支持 md/txt/docx")
        try:
            content = base64.b64decode(item.content_b64, validate=True)
        except binascii.Error as error:
            raise EditorialError("INVALID_SAMPLE", "样本编码错误") from error
        if len(content) > 2_000_000:
            raise EditorialError("SAMPLE_TOO_LARGE", "样本超过 2 MB")
        with tempfile.TemporaryDirectory(prefix="wechat-style-") as directory:
            path = Path(directory) / f"sample{suffix}"
            path.write_bytes(content)
            sample = desk.import_style(path, item.title, item.kind)
            sample["lineage"] = desk.style_engine.attach_work(
                sample["id"],
                work_id=item.work_id,
                stage=item.stage,
                parent_sample_id=item.parent_sample_id,
                article_version_id=item.article_version_id,
                source_url=item.source_url,
            )
            return sample

    @app.post("/api/style/signals/{signal_id}/revoke")
    def revoke(signal_id: str, _: Gate) -> dict[str, bool]:
        desk.revoke_style_signal(signal_id)
        return {"revoked": True}

    @app.get("/api/network/status")
    def network_status(_: Gate) -> dict[str, object]:
        return Director(desk, network_root(desk)).status()

    @app.post("/api/network/pause")
    def network_pause(item: NetworkPause, _: Gate) -> dict[str, object]:
        director = Director(desk, network_root(desk))
        director.set_pause(item.paused, item.channel)
        return director.status()

    @app.post("/api/network/tick")
    def network_tick(_: Gate) -> dict[str, object]:
        return Director(desk, network_root(desk)).tick()

    return app
