"""Atomic, idempotent import of real Codex-written research and article artifacts."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from rancho_wechat.editorial import (
    EditorialDesk,
    EditorialError,
    looks_like_instruction,
    normalized,
    now,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Execution(StrictModel):
    executor: Literal["codex-session", "codex-exec", "human"]
    generated_at: datetime
    model: str = Field(min_length=3, max_length=120)
    note: str = Field(max_length=2000)
    style_sample_status: Literal["IMPORTED", "UNAVAILABLE_USER_CONSTRAINTS_ONLY"]

    @field_validator("generated_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value > datetime.now(UTC):
            raise ValueError("生成时间必须带时区且不能在未来")
        return value


class Source(StrictModel):
    ref: str = Field(pattern=r"^S[1-9][0-9]?$", max_length=3)
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(max_length=2000)
    excerpt: str = Field(min_length=20, max_length=20_000)
    kind: Literal["PRIMARY", "SECONDARY", "AUTHOR_MATERIAL", "RSS"]
    retrieved_at: datetime
    published_at: datetime | None = None

    @field_validator("url")
    @classmethod
    def https(cls, value: str) -> str:
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or any(ord(c) < 32 or c in "<>[]()" for c in value)
        ):
            raise ValueError("来源须为无凭据 HTTPS URL")
        return value

    @field_validator("retrieved_at", "published_at")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value > datetime.now(UTC)):
            raise ValueError("来源日期须带时区且不能在未来")
        return value


class Claim(StrictModel):
    ref: str = Field(pattern=r"^C[1-9][0-9]?$", max_length=3)
    source_ref: str
    quote: str = Field(min_length=4, max_length=2000)
    epistemic_status: Literal["SOURCE_REPORT", "OBSERVATION", "PLAN", "INFERENCE", "AUTHOR_OPINION"]
    note: str = Field(default="", max_length=1000)


class Review(StrictModel):
    status: Literal["COMPLETE", "NEEDS_CONFIRMATION"]
    findings: list[dict[str, str]] = Field(max_length=30)
    note: str = Field(min_length=10, max_length=3000)
    independence: Literal["SAME_SESSION_SELF_CHECK", "SEPARATE_REVIEW"]


class Article(StrictModel):
    title_options: list[str] = Field(min_length=1, max_length=5)
    body: str = Field(min_length=200, max_length=100_000)
    summary: str = Field(min_length=10, max_length=500)
    outline: list[str] = Field(min_length=1, max_length=20)
    cover_suggestions: list[str] = Field(max_length=8)
    image_manifest: list[dict[str, str]] = Field(max_length=20)
    claims_used: list[str] = Field(min_length=1, max_length=50)

    @field_validator("title_options")
    @classmethod
    def titles(cls, values: list[str]) -> list[str]:
        if any(not 4 <= len(value) <= 180 or any(ord(c) < 32 for c in value) for value in values):
            raise ValueError("标题长度或字符无效")
        return values


class ContentPackage(StrictModel):
    schema_version: Literal["rancho-wechat.content/1.1"]
    package_id: UUID
    execution: Execution
    topic_title: str = Field(min_length=4, max_length=180)
    topic_idea: str = Field(max_length=3000)
    origin: Literal["MANUAL", "NEWS", "EVERGREEN"]
    news_published_at: datetime | None = None
    sources: list[Source] = Field(min_length=1, max_length=20)
    claims: list[Claim] = Field(min_length=1, max_length=50)
    research_note: str = Field(min_length=40, max_length=30_000)
    article: Article
    fact_check: Review

    @model_validator(mode="after")
    def evidence_links(self) -> ContentPackage:
        if any(ord(c) < 32 for c in self.topic_title):
            raise ValueError("选题标题不能含控制字符")
        sources = {source.ref: source for source in self.sources}
        refs = {claim.ref for claim in self.claims}
        if len(sources) != len(self.sources) or len(refs) != len(self.claims):
            raise ValueError("来源或事实编号重复")
        if any(source.ref != f"S{index}" for index, source in enumerate(self.sources, 1)):
            raise ValueError("来源编号须按 S1/S2 顺序排列")
        for claim in self.claims:
            if (
                claim.source_ref not in sources
                or claim.quote not in sources[claim.source_ref].excerpt
                or looks_like_instruction(claim.quote)
            ):
                raise ValueError("事实摘录不能在来源中定位或含指令性文本")
        if not set(self.article.claims_used).issubset(refs):
            raise ValueError("正文引用了未知事实条目")
        if self.origin == "NEWS" and (
            self.news_published_at is None
            or self.news_published_at.tzinfo is None
            or self.news_published_at > datetime.now(UTC)
        ):
            raise ValueError("新闻必须有带时区的非未来发布时间")
        for finding in self.fact_check.findings:
            if (
                finding.get("severity") not in {"BLOCKER", "WARNING"}
                or not finding.get("text", "").strip()
            ):
                raise ValueError("事实检查条目无效")
        return self


def load_package(path: Path) -> ContentPackage:
    if path.stat().st_size > 2_000_000:
        raise EditorialError("PACKAGE_TOO_LARGE", "内容包超过 2 MB")
    try:
        return ContentPackage.model_validate_json(path.read_text(encoding="utf-8-sig"))
    except (ValidationError, ValueError):
        raise EditorialError(
            "INVALID_PACKAGE", "内容包结构、时间或证据链接无效；请使用 content-schema 检查"
        ) from None


def import_package(desk: EditorialDesk, package: ContentPackage) -> dict[str, Any]:
    raw = package.model_dump_json()
    digest = hashlib.sha256(raw.encode()).hexdigest()
    package_id = str(package.package_id)
    article_id, topic_id, version_id = str(uuid4()), str(uuid4()), str(uuid4())
    stamp = now()
    with desk.store.write() as db:
        existing = db.execute(
            "SELECT * FROM content_imports WHERE package_id=?", (package_id,)
        ).fetchone()
        if existing:
            if existing["sha256"] != digest:
                raise EditorialError(
                    "PACKAGE_CONFLICT", "同一内容包 ID 对应不同内容；未覆盖已有稿件"
                )
            article_id = existing["article_id"]
        else:
            if db.execute(
                "SELECT id FROM topics WHERE normalized=?", (normalized(package.topic_title),)
            ).fetchone():
                raise EditorialError("DUPLICATE_TOPIC", "该选题已存在；请在编辑台修改已有稿件")
            db.execute(
                "INSERT INTO topics VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    topic_id,
                    package.topic_title,
                    normalized(package.topic_title),
                    package.topic_idea,
                    package.origin,
                    "ACCEPTED",
                    package.news_published_at.isoformat() if package.news_published_at else None,
                    stamp,
                    stamp,
                ),
            )
            sources = []
            for source in package.sources:
                identifier = str(uuid4())
                row = {**source.model_dump(mode="json"), "id": identifier, "topic_id": topic_id}
                db.execute(
                    "INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)",
                    (
                        identifier,
                        topic_id,
                        source.title,
                        source.url,
                        source.kind,
                        source.excerpt,
                        source.published_at.isoformat() if source.published_at else None,
                        source.retrieved_at.isoformat(),
                    ),
                )
                sources.append(row)
            lookup = {row["ref"]: row for row in sources}
            claims = []
            claim_ids = {}
            for claim in package.claims:
                identifier = str(uuid4())
                source_row = lookup[claim.source_ref]
                status = (
                    "SOURCE_SUPPORTED"
                    if source_row["kind"] in {"PRIMARY", "AUTHOR_MATERIAL"}
                    else "NEEDS_CONFIRMATION"
                )
                kind = (
                    "INFERENCE"
                    if claim.epistemic_status == "INFERENCE"
                    else ("OPINION" if claim.epistemic_status == "AUTHOR_OPINION" else "FACT")
                )
                db.execute(
                    "INSERT INTO claims VALUES(?,?,?,?,?,?,?,?)",
                    (
                        identifier,
                        topic_id,
                        source_row["id"],
                        claim.quote,
                        claim.quote,
                        kind,
                        status,
                        stamp,
                    ),
                )
                claims.append(
                    {
                        "id": identifier,
                        "status": status,
                        "text": claim.quote,
                        "source_id": source_row["id"],
                    }
                )
                claim_ids[claim.ref] = identifier
            db.execute(
                "INSERT INTO research VALUES(?,?,?,?,?)",
                (topic_id, package.research_note, "RESEARCHED", package.execution.executor, stamp),
            )
            topic = {
                "origin": package.origin,
                "news_published_at": package.news_published_at.isoformat()
                if package.news_published_at
                else None,
            }
            report = desk._fact_report(
                topic,
                sources,
                claims,
                package.article.body,
                [claim_ids[ref] for ref in package.article.claims_used],
            )
            desk._apply_model_review(report, package.fact_check.model_dump())
            report["generation_provider"] = package.execution.executor
            report["execution"] = package.execution.model_dump(mode="json")
            report["title_options"] = package.article.title_options
            report["epistemic_claims"] = [claim.model_dump() for claim in package.claims]
            report["warnings"].append(
                "导入元数据是来源声明；同会话自查不等于独立审稿，仍需作者审核"
            )
            db.execute(
                "INSERT INTO articles VALUES(?,?,?,?,?,?,?,?)",
                (
                    article_id,
                    topic_id,
                    "import:" + package_id,
                    "IN_REVIEW",
                    version_id,
                    None,
                    None,
                    stamp,
                ),
            )
            db.execute(
                "INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    version_id,
                    article_id,
                    1,
                    "AI_DRAFT" if package.execution.executor != "human" else "AUTHOR_EDIT",
                    package.article.title_options[0],
                    package.article.body,
                    package.article.summary,
                    json.dumps(package.article.outline, ensure_ascii=False),
                    json.dumps(
                        {
                            "suggestions": package.article.cover_suggestions,
                            "images": package.article.image_manifest,
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(report, ensure_ascii=False),
                    json.dumps(desk.style_context(), ensure_ascii=False),
                    "导入真实写作内容；未代表作者批准",
                    "",
                    stamp,
                ),
            )
            db.execute(
                "INSERT INTO content_imports VALUES(?,?,?,?,?)",
                (package_id, digest, article_id, package.execution.model_dump_json(), stamp),
            )
            desk._audit(
                db,
                "article",
                article_id,
                "IMPORT_CONTENT",
                {
                    "package_id": package_id,
                    "sha256": digest,
                    "executor": package.execution.executor,
                    "author_approved": False,
                },
            )
    return desk.article(article_id)


def safe_article_markdown(article: dict[str, Any]) -> str:
    """Review artifact only, never a publish authorization."""
    version = article["versions"][-1]
    return f"<!-- IN_REVIEW / AUTHOR_APPROVAL_REQUIRED / NOT_PUBLISHED -->\n\n{version['body']}\n"
