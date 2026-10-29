"""WeChat-specific editorial flow. It has no public-publish operation."""

from __future__ import annotations

import difflib
import hashlib
import html
import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse
from uuid import uuid4

from rancho_wechat.api_provider import ModelError
from rancho_wechat.author_style import AuthorStyleEngine
from rancho_wechat.providers import ModelProvider
from rancho_wechat.store import Store
from rancho_wechat.style import STYLE_RULES, content_sha, edit_signals, read_sample, sample_features

EVERGREEN = (
    ("数学研究中的反例怎样改变问题", "从一个具体反例和研究者的选择切入"),
    ("AI for Science 的验证究竟验证了什么", "区分演示、基准与可重复实验"),
    ("研究工具如何改变人的提问方式", "从作者自己的实践寻找证据"),
    ("论文里最重要的负面结果", "讨论没有成功的实验如何产生知识"),
    ("模型能力与科学发现之间的距离", "选择可核对的案例，而非空泛判断"),
    ("技术公司叙事中的时间尺度", "对照产品发布与研究积累"),
    ("科学史中的工具与偶然性", "从原始文献核对人物故事"),
    ("MathForge 的一个真实工程取舍", "只使用作者允许公开的项目材料"),
    ("一个研究想法怎样被证伪", "呈现可复现的转折过程"),
    ("AI 与数学家的合作边界", "避免把辅助计算写成自动证明"),
    ("数据、图表与观点之间的缝隙", "审视量化事实的来源和口径"),
    ("从一篇原始论文重新理解热点", "不把旧研究伪装成当天新闻"),
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def normalized(title: str) -> str:
    return re.sub(r"[\W_]+", "", title.casefold(), flags=re.UNICODE)


def looks_like_instruction(value: str) -> bool:
    return bool(
        re.search(
            r"(?i)ignore (?:all )?(?:previous|prior) instructions|"
            r"system prompt|<\|system\|>|忽略.{0,12}(?:指令|系统提示)|"
            r"(?:请|必须).{0,12}(?:泄露|输出).{0,12}(?:密钥|令牌)",
            value,
        )
    )


def decode_json(value: str | None, fallback: Any = None) -> Any:
    return json.loads(value) if value else fallback


class EditorialError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code, self.message = code, message
        super().__init__(message)


class EditorialDesk:
    def __init__(
        self,
        store: Store,
        provider: ModelProvider,
        export_root: Path,
        *,
        work_mode: str = "STANDARD",
    ) -> None:
        if work_mode not in {"STANDARD", "EDITORIAL_FIRST"}:
            raise ValueError("工作模式无效")
        self.work_mode = work_mode
        self.store, self.provider = store, provider
        self.export_root = export_root.resolve()
        self.export_root.mkdir(parents=True, exist_ok=True)
        self.style_engine = AuthorStyleEngine(store)

    @staticmethod
    def _row(db: Any, sql: str, args: tuple[Any, ...]) -> dict[str, Any]:
        row = db.execute(sql, args).fetchone()
        if row is None:
            raise EditorialError("NOT_FOUND", "记录不存在")
        return dict(row)

    @staticmethod
    def _audit(db: Any, kind: str, identifier: str, action: str, details: Any) -> None:
        db.execute(
            "INSERT INTO audit(target_type,target_id,action,details_json,at) VALUES(?,?,?,?,?)",
            (kind, identifier, action, json.dumps(details, ensure_ascii=False), now()),
        )

    def create_topic(
        self,
        title: str,
        idea: str = "",
        origin: str = "MANUAL",
        news_published_at: str | None = None,
    ) -> dict[str, Any]:
        title, idea = title.strip(), idea.strip()
        if not (4 <= len(title) <= 180) or any(ord(c) < 32 for c in title):
            raise EditorialError("INVALID_TITLE", "标题需为 4–180 字")
        if origin not in {"MANUAL", "RSS", "EVERGREEN", "NEWS"}:
            raise EditorialError("INVALID_ORIGIN", "选题来源无效")
        if news_published_at:
            try:
                datetime.fromisoformat(news_published_at)
            except ValueError as error:
                raise EditorialError("INVALID_DATE", "新闻日期格式无效") from error
        identifier, stamp = str(uuid4()), now()
        with self.store.write() as db:
            existing = db.execute(
                "SELECT * FROM topics WHERE normalized=?", (normalized(title),)
            ).fetchone()
            if existing:
                raise EditorialError("DUPLICATE_TOPIC", f"重复选题：{existing['id']}")
            db.execute(
                "INSERT INTO topics VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    title,
                    normalized(title),
                    idea,
                    origin,
                    "PROPOSED",
                    news_published_at,
                    stamp,
                    stamp,
                ),
            )
            self._audit(db, "topic", identifier, "CREATE", {"origin": origin})
            return self._row(db, "SELECT * FROM topics WHERE id=?", (identifier,))

    def list_topics(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.store.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM topics ORDER BY created_at DESC LIMIT ?", (min(limit, 200),)
                )
            ]

    def topic(self, identifier: str) -> dict[str, Any]:
        with self.store.connection() as db:
            return self._row(db, "SELECT * FROM topics WHERE id=?", (identifier,))

    def decide_topic(self, identifier: str, decision: str) -> dict[str, Any]:
        if decision not in {"ACCEPT", "REJECT"}:
            raise EditorialError("INVALID_DECISION", "选题只能接受或拒绝")
        with self.store.write() as db:
            topic = self._row(db, "SELECT * FROM topics WHERE id=?", (identifier,))
            if topic["status"] != "PROPOSED":
                raise EditorialError("INVALID_STATE", "选题已经处理")
            status = "ACCEPTED" if decision == "ACCEPT" else "REJECTED"
            db.execute(
                "UPDATE topics SET status=?,updated_at=? WHERE id=?", (status, now(), identifier)
            )
            self._audit(db, "topic", identifier, decision, {})
            return self._row(db, "SELECT * FROM topics WHERE id=?", (identifier,))

    def update_topic(self, identifier: str, title: str, idea: str) -> dict[str, Any]:
        title = title.strip()
        if not (4 <= len(title) <= 180) or any(ord(c) < 32 for c in title):
            raise EditorialError("INVALID_TITLE", "标题需为 4–180 字")
        with self.store.write() as db:
            topic = self._row(db, "SELECT * FROM topics WHERE id=?", (identifier,))
            if topic["status"] != "PROPOSED":
                raise EditorialError("INVALID_STATE", "只能修改未接受的选题")
            other = db.execute(
                "SELECT id FROM topics WHERE normalized=? AND id<>?",
                (normalized(title), identifier),
            ).fetchone()
            if other:
                raise EditorialError("DUPLICATE_TOPIC", "修改后与已有选题重复")
            db.execute(
                "UPDATE topics SET title=?,normalized=?,idea=?,updated_at=? WHERE id=?",
                (title, normalized(title), idea.strip(), now(), identifier),
            )
            self._audit(db, "topic", identifier, "EDIT", {"title": title})
            return self._row(db, "SELECT * FROM topics WHERE id=?", (identifier,))

    def add_source(
        self,
        title: str,
        url: str,
        excerpt: str,
        kind: str,
        topic_id: str | None = None,
        published_at: str | None = None,
    ) -> dict[str, Any]:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or len(url) > 2000
            or any(ord(c) < 32 or c in "<>[]()" for c in url)
        ):
            raise EditorialError("INVALID_SOURCE_URL", "来源必须是无凭据的 HTTPS 链接")
        if kind not in {"PRIMARY", "SECONDARY", "AUTHOR_MATERIAL", "RSS"}:
            raise EditorialError("INVALID_SOURCE_KIND", "来源类别无效")
        if (
            not title.strip()
            or any(ord(c) < 32 for c in title)
            or not (20 <= len(excerpt) <= 20_000)
        ):
            raise EditorialError("INVALID_SOURCE", "来源标题或摘录无效（摘录至少 20 字）")
        if published_at:
            try:
                parsed_date = datetime.fromisoformat(published_at)
                if parsed_date.tzinfo is None:
                    raise ValueError("timezone required")
            except ValueError as error:
                raise EditorialError("INVALID_DATE", "发布日期需要带时区的 ISO 时间") from error
        identifier = str(uuid4())
        with self.store.write() as db:
            if topic_id:
                self._row(db, "SELECT id FROM topics WHERE id=?", (topic_id,))
            existing = db.execute(
                "SELECT * FROM sources WHERE topic_id IS ? AND url=?", (topic_id, url)
            ).fetchone()
            if existing:
                return dict(existing)
            db.execute(
                "INSERT INTO sources VALUES(?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    topic_id,
                    title.strip()[:300],
                    url,
                    kind,
                    excerpt.strip(),
                    published_at,
                    now(),
                ),
            )
            if topic_id:
                db.execute("UPDATE research SET status='STALE' WHERE topic_id=?", (topic_id,))
                affected = [
                    row["id"]
                    for row in db.execute("SELECT id FROM articles WHERE topic_id=?", (topic_id,))
                ]
                db.execute(
                    "UPDATE articles SET status='IN_REVIEW',approved_version_id=NULL,"
                    "fact_attested_at=NULL WHERE topic_id=?",
                    (topic_id,),
                )
                for article_id in affected:
                    self._audit(
                        db,
                        "article",
                        article_id,
                        "SOURCE_CHANGED_REVIEW_INVALIDATED",
                        {"source_id": identifier},
                    )
            self._audit(db, "source", identifier, "IMPORT", {"topic_id": topic_id, "kind": kind})
            return self._row(db, "SELECT * FROM sources WHERE id=?", (identifier,))

    def sources(self, topic_id: str | None = None) -> list[dict[str, Any]]:
        with self.store.connection() as db:
            if topic_id is None:
                rows = db.execute("SELECT * FROM sources ORDER BY retrieved_at DESC LIMIT 200")
            else:
                rows = db.execute(
                    "SELECT * FROM sources WHERE topic_id=? ORDER BY retrieved_at", (topic_id,)
                )
            return [dict(row) for row in rows]

    def add_claim(
        self, topic_id: str, source_id: str, text: str, quote: str, kind: str = "FACT"
    ) -> dict[str, Any]:
        text, quote = text.strip(), quote.strip()
        if kind not in {"FACT", "QUOTE", "INFERENCE", "OPINION"}:
            raise EditorialError("INVALID_CLAIM_KIND", "事实类别无效")
        with self.store.write() as db:
            source = self._row(db, "SELECT * FROM sources WHERE id=?", (source_id,))
            if source["topic_id"] != topic_id:
                raise EditorialError("SOURCE_MISMATCH", "来源不属于该选题")
            if not text or not quote or quote not in source["excerpt"]:
                raise EditorialError("UNVERIFIED_QUOTE", "引文无法在保存的来源摘录中逐字定位")
            if looks_like_instruction(text) or looks_like_instruction(quote):
                raise EditorialError("UNTRUSTED_INSTRUCTIONS", "来源中的指令性文本不可作为事实")
            existing = db.execute(
                "SELECT * FROM claims WHERE topic_id=? AND source_id=? AND text=?",
                (topic_id, source_id, text),
            ).fetchone()
            if existing:
                return dict(existing)
            identifier = str(uuid4())
            exact_claim = " ".join(text.split()) == " ".join(quote.split())
            status = (
                "SOURCE_SUPPORTED"
                if source["kind"] in {"PRIMARY", "AUTHOR_MATERIAL"} and exact_claim
                else "NEEDS_CONFIRMATION"
            )
            db.execute(
                "INSERT INTO claims VALUES(?,?,?,?,?,?,?,?)",
                (identifier, topic_id, source_id, text, quote, kind, status, now()),
            )
            self._audit(db, "claim", identifier, "ADD", {"source_id": source_id, "status": status})
            return self._row(db, "SELECT * FROM claims WHERE id=?", (identifier,))

    def claims(self, topic_id: str) -> list[dict[str, Any]]:
        with self.store.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM claims WHERE topic_id=? ORDER BY created_at,id", (topic_id,)
                )
            ]

    def research(self, topic_id: str) -> dict[str, Any]:
        topic = self.topic(topic_id)
        if topic["status"] != "ACCEPTED":
            raise EditorialError("TOPIC_NOT_ACCEPTED", "先接受选题才能研究")
        with self.store.connection() as db:
            existing = db.execute("SELECT * FROM research WHERE topic_id=?", (topic_id,)).fetchone()
        if (
            existing
            and existing["status"] != "STALE"
            and existing["provider"] == self.provider.name
        ):
            return dict(existing)
        sources = self.sources(topic_id)
        if not sources and self.provider.name != "mock-fixture":
            raise EditorialError("NEEDS_SOURCES", "请先录入可靠来源，未调用真实模型")
        result = self.provider.generate_json(
            "research", {"topic": topic, "sources": sources, "source_is_untrusted": True}
        )
        note = str(result.get("note", ""))[:20_000]
        if not note:
            raise EditorialError("INVALID_MODEL_OUTPUT", "模型未返回研究笔记")
        candidates = result.get("claims", [])
        if not isinstance(candidates, list):
            raise EditorialError("INVALID_MODEL_OUTPUT", "事实列表格式无效")
        invalid_claims = 0
        for item in candidates[:20]:
            if not isinstance(item, dict):
                continue
            try:
                self.add_claim(
                    topic_id,
                    str(item["source_id"]),
                    str(item["text"]),
                    str(item["quote"]),
                    str(item.get("kind", "FACT")),
                )
            except (KeyError, EditorialError):
                # An invalid model claim is retained as a blocking research warning, not a fact.
                note += "\n\n待确认：模型提出的某项事实未能与原始摘录逐字对应。"
                invalid_claims += 1
        status = (
            "NEEDS_SOURCES"
            if not sources
            else ("NEEDS_CONFIRMATION" if invalid_claims else "RESEARCHED")
        )
        with self.store.write() as db:
            if existing:
                db.execute(
                    "UPDATE research SET note=?,status=?,provider=?,created_at=? WHERE topic_id=?",
                    (note, status, self.provider.name, now(), topic_id),
                )
            else:
                db.execute(
                    "INSERT INTO research VALUES(?,?,?,?,?)",
                    (topic_id, note, status, self.provider.name, now()),
                )
            self._audit(
                db,
                "topic",
                topic_id,
                "RESEARCH",
                {"status": status, "provider": self.provider.name},
            )
            return self._row(db, "SELECT * FROM research WHERE topic_id=?", (topic_id,))

    def style_context(self) -> dict[str, Any]:
        with self.store.connection() as db:
            samples = [
                {"title": row["title"], "features": decode_json(row["features_json"])}
                for row in db.execute(
                    "SELECT * FROM style_samples ORDER BY created_at DESC LIMIT 4"
                )
            ]
            signals = [
                {"kind": row["kind"], "text": row["text"], "confidence": row["confidence"]}
                for row in db.execute(
                    "SELECT * FROM style_signals WHERE active=1 AND occurrences>=2 "
                    "ORDER BY confidence DESC LIMIT 10"
                )
            ]
        return {
            "author": "兰澈",
            "constraints": STYLE_RULES,
            "samples": samples,
            "preferences": signals,
            "confirmed_rules": self.style_engine.rules(confirmed_only=True),
        }

    def import_style(self, path: Path, title: str, kind: str = "AUTHOR") -> dict[str, Any]:
        if kind not in {"AUTHOR", "REFERENCE"}:
            raise EditorialError("INVALID_SAMPLE_KIND", "样本类别无效")
        text = read_sample(path)
        if len(text.strip()) < 80:
            raise EditorialError("SHORT_SAMPLE", "样本至少 80 字")
        digest, identifier = content_sha(text), str(uuid4())
        features = sample_features(text)
        with self.store.write() as db:
            existing = db.execute(
                "SELECT * FROM style_samples WHERE sha256=?", (digest,)
            ).fetchone()
            if existing:
                if existing["kind"] != kind:
                    raise EditorialError("SAMPLE_KIND_CONFLICT", "相同正文已以另一类别导入，未覆盖")
                return dict(existing)
            db.execute(
                "INSERT INTO style_samples VALUES(?,?,?,?,?,?,?)",
                (
                    identifier,
                    title.strip()[:200],
                    kind,
                    text if kind == "AUTHOR" else "",
                    digest,
                    json.dumps(features, ensure_ascii=False),
                    now(),
                ),
            )
            self._audit(db, "style_sample", identifier, "IMPORT", {"kind": kind})
            return self._row(db, "SELECT * FROM style_samples WHERE id=?", (identifier,))

    def style_signals(self) -> list[dict[str, Any]]:
        with self.store.connection() as db:
            return [
                dict(row)
                for row in db.execute("SELECT * FROM style_signals ORDER BY created_at DESC")
            ]

    def revoke_style_signal(self, identifier: str) -> None:
        with self.store.write() as db:
            self._row(db, "SELECT id FROM style_signals WHERE id=?", (identifier,))
            db.execute("UPDATE style_signals SET active=0 WHERE id=?", (identifier,))
            self._audit(db, "style_signal", identifier, "REVOKE", {})

    def _fact_report(
        self,
        topic: dict[str, Any],
        sources: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        body: str,
        claims_used: list[str],
    ) -> dict[str, Any]:
        blockers: list[str] = []
        warnings: list[str] = []
        if not sources:
            blockers.append("缺失来源")
        if not claims:
            blockers.append("没有可核对的事实条目")
        unsupported = [claim["id"] for claim in claims if claim["status"] != "SOURCE_SUPPORTED"]
        if unsupported:
            blockers.append(f"{len(unsupported)} 项事实仍待原始来源核实")
        known = {claim["id"] for claim in claims}
        if (
            not isinstance(claims_used, list)
            or any(not isinstance(item, str) for item in claims_used)
            or not set(claims_used).issubset(known)
        ):
            blockers.append("模型使用了不存在的事实 ID")
        ranks = {f"S{index}" for index, _ in enumerate(sources, 1)}
        unknown_citations = set(re.findall(r"\[S(\d+)\]", body)) - {rank[1:] for rank in ranks}
        if unknown_citations:
            blockers.append("正文包含未知来源编号")
        if claims_used and not re.search(r"\[S\d+\]", body):
            blockers.append("正文缺少来源标记")
        excerpts = "\n".join(source["excerpt"] for source in sources)
        for quoted in re.findall(r"[“\"]([^”\"]{8,})[”\"]", body):
            if quoted not in excerpts:
                blockers.append("正文中的直接引语无法逐字核对")
                break
        if topic["origin"] in {"RSS", "NEWS"}:
            raw_date = topic["news_published_at"]
            if not raw_date:
                blockers.append("新闻未记录发布时间")
            else:
                published = datetime.fromisoformat(raw_date)
                if published.tzinfo is None or datetime.now(UTC) - published > timedelta(days=7):
                    blockers.append("新闻超过七天或缺少时区，不能作为当天新闻")
                elif published > datetime.now(UTC):
                    blockers.append("新闻日期位于未来")
        if (
            any(phrase in body for phrase in ("最新消息", "今天发布", "刚刚发布"))
            and topic["origin"] == "EVERGREEN"
        ):
            blockers.append("常青选题被描述为当天新闻")
        if self.provider.name != "mock-fixture":
            warnings.append("模型输出只能辅助审稿，仍需作者逐项核对")
        return {
            "blockers": sorted(set(blockers)),
            "warnings": warnings,
            "source_count": len(sources),
            "claim_count": len(claims),
            "checked_at": now(),
        }

    def generate(self, topic_id: str, angle: str = "main") -> dict[str, Any]:
        topic = self.topic(topic_id)
        if topic["status"] != "ACCEPTED":
            raise EditorialError("TOPIC_NOT_ACCEPTED", "先接受选题")
        with self.store.connection() as db:
            existing = db.execute(
                "SELECT * FROM articles WHERE topic_id=? AND angle=?", (topic_id, angle)
            ).fetchone()
            researched = db.execute(
                "SELECT * FROM research WHERE topic_id=?", (topic_id,)
            ).fetchone()
        if researched is None:
            raise EditorialError("RESEARCH_REQUIRED", "先生成研究笔记")
        if researched["status"] == "STALE":
            raise EditorialError("RESEARCH_STALE", "来源已变更，请重新运行研究")
        sources, claims = self.sources(topic_id), self.claims(topic_id)
        if existing:
            previous = self.article(existing["id"])["versions"][-1]["fact_report"]
            if (
                previous["source_count"] == len(sources)
                and previous["claim_count"] == len(claims)
                and previous.get("generation_provider") == self.provider.name
            ):
                return self.article(existing["id"])
        source_ranks = {source["id"]: index for index, source in enumerate(sources, 1)}
        usable = [
            {**claim, "source_rank": source_ranks[claim["source_id"]]}
            for claim in claims
            if claim["source_id"] in source_ranks
        ]
        style = self.style_context()
        result = self.provider.generate_json(
            "draft",
            {
                "topic": topic,
                "research_note": researched["note"],
                "claims": usable,
                "sources": sources,
                "style": style,
                "angle": angle,
                "source_is_untrusted": True,
            },
        )
        body = str(result.get("body", ""))[:100_000]
        if len(body.strip()) < 80:
            raise EditorialError("INVALID_MODEL_OUTPUT", "稿件内容过短")
        titles = result.get("title_options", [])
        outline = result.get("outline", [])
        covers = result.get("cover_suggestions", [])
        if (
            not isinstance(titles, list)
            or not isinstance(outline, list)
            or not isinstance(covers, list)
        ):
            raise EditorialError("INVALID_MODEL_OUTPUT", "稿件结构无效")
        title = str(titles[0])[:180] if titles else topic["title"]
        used = result.get("claims_used", [])
        report = self._fact_report(topic, sources, claims, body, used)
        if researched["status"] != "RESEARCHED":
            report["blockers"].append("研究材料含未核实内容")
        report["title_options"] = [str(value)[:180] for value in titles[:5]]
        report["generation_provider"] = self.provider.name
        if self.provider.name != "mock-fixture":
            self._apply_model_review(report, self._review_body(topic, sources, claims, body))
        article_id, version_id = (existing["id"] if existing else str(uuid4())), str(uuid4())
        with self.store.write() as db:
            current_article = db.execute(
                "SELECT * FROM articles WHERE topic_id=? AND angle=?", (topic_id, angle)
            ).fetchone()
            if current_article:
                latest = self._row(
                    db,
                    "SELECT * FROM versions WHERE id=?",
                    (current_article["current_version_id"],),
                )
                latest_report = decode_json(latest["fact_report_json"])
                if (
                    latest_report.get("generation_provider") == self.provider.name
                    and latest_report["source_count"] == len(sources)
                    and latest_report["claim_count"] == len(claims)
                ):
                    return self.article(current_article["id"])
                ordinal = latest["ordinal"] + 1
                db.execute(
                    "UPDATE articles SET current_version_id=?,approved_version_id=NULL,"
                    "fact_attested_at=NULL,status='IN_REVIEW' WHERE id=?",
                    (version_id, article_id),
                )
            else:
                ordinal = 1
                db.execute(
                    "INSERT INTO articles VALUES(?,?,?,?,?,?,?,?)",
                    (article_id, topic_id, angle[:100], "IN_REVIEW", version_id, None, None, now()),
                )
            db.execute(
                "INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    version_id,
                    article_id,
                    ordinal,
                    "AI_DRAFT",
                    title,
                    body,
                    str(result.get("summary", ""))[:500],
                    json.dumps(outline, ensure_ascii=False),
                    json.dumps(
                        {"suggestions": covers[:8], "images": result.get("image_manifest", [])},
                        ensure_ascii=False,
                    ),
                    json.dumps(report, ensure_ascii=False),
                    json.dumps(style, ensure_ascii=False),
                    "",
                    "",
                    now(),
                ),
            )
            self._audit(
                db,
                "article",
                article_id,
                "GENERATE",
                {"provider": self.provider.name, "angle": angle},
            )
        return self.article(article_id)

    def list_articles(self) -> list[dict[str, Any]]:
        with self.store.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT a.*,t.title AS topic_title,v.title AS article_title FROM articles a "
                    "JOIN topics t ON t.id=a.topic_id "
                    "LEFT JOIN versions v ON v.id=a.current_version_id "
                    "ORDER BY a.created_at DESC LIMIT 100"
                )
            ]

    def article(self, identifier: str) -> dict[str, Any]:
        with self.store.connection() as db:
            article = self._row(db, "SELECT * FROM articles WHERE id=?", (identifier,))
            article["versions"] = [
                self._version_dict(row)
                for row in db.execute(
                    "SELECT * FROM versions WHERE article_id=? ORDER BY ordinal", (identifier,)
                )
            ]
            return article

    @staticmethod
    def _version_dict(row: Any) -> dict[str, Any]:
        result = dict(row)
        for name in ("outline_json", "cover_json", "fact_report_json", "style_context_json"):
            result[name[:-5]] = decode_json(result.pop(name))
        return result

    def edit(
        self,
        article_id: str,
        title: str,
        body: str,
        summary: str,
        note: str,
        *,
        explicit_preferences: list[str] | None = None,
        expected_version_id: str | None = None,
        kind: str = "AUTHOR_EDIT",
        revision_key: str | None = None,
    ) -> dict[str, Any]:
        if not title.strip() or any(ord(c) < 32 for c in title) or len(body.strip()) < 40:
            raise EditorialError("INVALID_EDIT", "标题或正文过短")
        if revision_key and (kind != "AI_REVISION" or not 4 <= len(revision_key) <= 160):
            raise EditorialError("INVALID_REVISION_KEY", "编辑导入键仅用于 AI 修订")
        request_sha = hashlib.sha256(
            json.dumps(
                [title, body, summary, note, expected_version_id], ensure_ascii=False
            ).encode()
        ).hexdigest()
        with self.store.write() as db:
            if revision_key:
                for record in db.execute(
                    "SELECT details_json FROM audit WHERE target_type='article' "
                    "AND target_id=? AND action='AI_REVISION'",
                    (article_id,),
                ):
                    details = json.loads(record["details_json"])
                    if details.get("revision_key") == revision_key:
                        if details.get("request_sha256") != request_sha:
                            raise EditorialError("REVISION_CONFLICT", "同编辑键内容冲突，未覆盖")
                        return self.article(article_id)
            article = self._row(db, "SELECT * FROM articles WHERE id=?", (article_id,))
            if article["status"] == "REJECTED":
                raise EditorialError("INVALID_STATE", "已退稿文章不可修改")
            previous = self._row(
                db, "SELECT * FROM versions WHERE id=?", (article["current_version_id"],)
            )
            if expected_version_id and previous["id"] != expected_version_id:
                raise EditorialError("VERSION_CONFLICT", "文章已被修改，请加载最新版本后再保存")
            if kind not in {"AUTHOR_EDIT", "AI_REVISION"}:
                raise EditorialError("INVALID_VERSION_KIND", "版本类别无效")
            if explicit_preferences and (
                kind != "AUTHOR_EDIT"
                or len(explicit_preferences) > 10
                or any(not 4 <= len(item.strip()) <= 500 for item in explicit_preferences)
            ):
                raise EditorialError("INVALID_PREFERENCES", "作者偏好需为 4–500 字，每次最多十条")
            topic = self._row(db, "SELECT * FROM topics WHERE id=?", (article["topic_id"],))
            sources = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM sources WHERE topic_id=? ORDER BY retrieved_at",
                    (article["topic_id"],),
                )
            ]
            claims = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM claims WHERE topic_id=? ORDER BY created_at,id",
                    (article["topic_id"],),
                )
            ]
            report = self._fact_report(
                topic, sources, claims, body, [item["id"] for item in claims]
            )
            report["generation_provider"] = decode_json(previous["fact_report_json"]).get(
                "generation_provider", "legacy-unrecorded"
            )
            previous_report = decode_json(previous["fact_report_json"])
            for field in ("execution", "epistemic_claims", "title_options"):
                if field in previous_report:
                    report[field] = previous_report[field]
            report["warnings"].append("编辑后须重新人工核对，原始自查结论不自动继承")
            if self.provider.name not in {"mock-fixture", "codex-import"}:
                report["blockers"].append("独立模型事实审查待运行")
            diff = "".join(
                difflib.unified_diff(
                    previous["body"].splitlines(keepends=True),
                    body.splitlines(keepends=True),
                    fromfile=f"v{previous['ordinal']}",
                    tofile=f"v{previous['ordinal'] + 1}",
                )
            )
            identifier = str(uuid4())
            db.execute(
                "INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    article_id,
                    previous["ordinal"] + 1,
                    kind,
                    title.strip()[:180],
                    body,
                    summary.strip()[:500],
                    previous["outline_json"],
                    previous["cover_json"],
                    json.dumps(report, ensure_ascii=False),
                    previous["style_context_json"],
                    note[:2000],
                    diff,
                    now(),
                ),
            )
            db.execute(
                "UPDATE articles SET current_version_id=?,approved_version_id=NULL,"
                "fact_attested_at=NULL,status='IN_REVIEW' WHERE id=?",
                (identifier, article_id),
            )
            for signal_kind, text in (
                edit_signals(previous["body"], body) if kind == "AUTHOR_EDIT" else []
            ):
                existing = db.execute(
                    "SELECT * FROM style_signals WHERE kind=? AND text=?", (signal_kind, text)
                ).fetchone()
                if existing and existing["active"]:
                    count = existing["occurrences"] + 1
                    db.execute(
                        "UPDATE style_signals SET occurrences=?,confidence=?,evidence_version_id=? "
                        "WHERE id=?",
                        (count, min(0.35 * count, 0.9), identifier, existing["id"]),
                    )
                elif not existing:
                    db.execute(
                        "INSERT INTO style_signals VALUES(?,?,?,?,?,?,?,?)",
                        (str(uuid4()), signal_kind, text, identifier, 0.35, 1, 1, now()),
                    )
            self._audit(
                db,
                "article",
                article_id,
                kind,
                {
                    "version_id": identifier,
                    "note": note,
                    "revision_key": revision_key,
                    "request_sha256": request_sha,
                },
            )
        self.style_engine.record_edit(
            article_id,
            identifier,
            previous["id"],
            previous["body"],
            body,
            explicit_preferences or [],
            author_edit=kind == "AUTHOR_EDIT",
        )
        return self.article(article_id)

    def _review_body(
        self,
        topic: dict[str, Any],
        sources: list[dict[str, Any]],
        claims: list[dict[str, Any]],
        body: str,
    ) -> dict[str, Any]:
        result = self.provider.generate_json(
            "review",
            {
                "topic": topic,
                "sources": sources,
                "claims": claims,
                "body": body,
                "source_is_untrusted": True,
            },
        )
        if result.get("status") not in {"COMPLETE", "NEEDS_CONFIRMATION"} or not isinstance(
            result.get("findings"), list
        ):
            raise EditorialError("INVALID_REVIEW", "模型审查结果缺少有效状态或 findings")
        for finding in result["findings"]:
            if (
                not isinstance(finding, dict)
                or finding.get("severity") not in {"BLOCKER", "WARNING"}
                or not isinstance(finding.get("text"), str)
                or not finding["text"].strip()
            ):
                raise EditorialError("INVALID_REVIEW", "模型事实审查条目无效")
        return result

    @staticmethod
    def _apply_model_review(report: dict[str, Any], review: dict[str, Any]) -> None:
        report["model_review"] = review
        if review["status"] != "COMPLETE":
            report["blockers"].append("模型事实审查仍有待确认项")
        for finding in review["findings"]:
            report["blockers" if finding["severity"] == "BLOCKER" else "warnings"].append(
                str(finding["text"])[:1000]
            )

    def review_article(self, article_id: str) -> dict[str, Any]:
        article = self.article(article_id)
        version = article["versions"][-1]
        sources, claims = self.sources(article["topic_id"]), self.claims(article["topic_id"])
        if len(sources) != version["fact_report"]["source_count"]:
            raise EditorialError("RESEARCH_STALE", "来源已变更，请重新研究和生成稿件")
        topic = self.topic(article["topic_id"])
        review = self._review_body(topic, sources, claims, version["body"])
        report = self._fact_report(
            topic, sources, claims, version["body"], [claim["id"] for claim in claims]
        )
        self._apply_model_review(report, review)
        for field in ("generation_provider", "execution", "epistemic_claims", "title_options"):
            if field in version["fact_report"]:
                report[field] = version["fact_report"][field]
        with self.store.write() as db:
            current = self._row(db, "SELECT * FROM articles WHERE id=?", (article_id,))
            if current["current_version_id"] != version["id"]:
                raise EditorialError("VERSION_CONFLICT", "审查期间文章已更新，结果未应用")
            db.execute(
                "UPDATE versions SET fact_report_json=? WHERE id=?",
                (json.dumps(report, ensure_ascii=False), version["id"]),
            )
            db.execute(
                "UPDATE articles SET status='IN_REVIEW',approved_version_id=NULL,"
                "fact_attested_at=NULL WHERE id=?",
                (article_id,),
            )
            self._audit(
                db,
                "article",
                article_id,
                "MODEL_REVIEW",
                {"version_id": version["id"], "provider": self.provider.name},
            )
        return self.article(article_id)

    def revise_article(self, article_id: str, instruction: str) -> dict[str, Any]:
        if not 4 <= len(instruction.strip()) <= 2000:
            raise EditorialError("INVALID_REVISION", "请给出 4–2000 字的修改要求")
        article = self.article(article_id)
        if article["status"] == "REJECTED":
            raise EditorialError("INVALID_STATE", "退稿文章不能自动修订")
        version = article["versions"][-1]
        result = self.provider.generate_json(
            "revise",
            {
                "version": version,
                "topic": self.topic(article["topic_id"]),
                "sources": self.sources(article["topic_id"]),
                "claims": self.claims(article["topic_id"]),
                "style": self.style_context(),
                "instruction": instruction,
            },
        )
        titles = result.get("title_options", [version["title"]])
        if not isinstance(titles, list) or not titles or not isinstance(result.get("body"), str):
            raise EditorialError("INVALID_MODEL_OUTPUT", "AI 修订结果无效")
        revised = self.edit(
            article_id,
            str(titles[0]),
            result["body"],
            str(result.get("summary", version["summary"])),
            instruction,
            expected_version_id=version["id"],
            kind="AI_REVISION",
        )
        if self.provider.name != "mock-fixture":
            return self.review_article(revised["id"])
        return revised

    def decide_article(
        self,
        article_id: str,
        decision: str,
        note: str = "",
        fact_attested: bool = False,
        expected_version_id: str | None = None,
    ) -> dict[str, Any]:
        if self.work_mode == "EDITORIAL_FIRST" and decision == "APPROVE":
            raise EditorialError("AUTHOR_APPROVAL_REQUIRED", "本轮仅编辑，不允许批准")
        if decision not in {"APPROVE", "REVISE", "REJECT"}:
            raise EditorialError("INVALID_DECISION", "稿件决策无效")
        with self.store.write() as db:
            article = self._row(db, "SELECT * FROM articles WHERE id=?", (article_id,))
            if expected_version_id and article["current_version_id"] != expected_version_id:
                raise EditorialError("VERSION_CONFLICT", "稿件已更新，请重新阅读最新版本再审核")
            if article["status"] not in {"IN_REVIEW", "REVISION_REQUESTED"}:
                raise EditorialError("INVALID_STATE", "稿件当前不可审核")
            version = self._row(
                db, "SELECT * FROM versions WHERE id=?", (article["current_version_id"],)
            )
            report = decode_json(version["fact_report_json"])
            if decision == "APPROVE":
                research = db.execute(
                    "SELECT status FROM research WHERE topic_id=?", (article["topic_id"],)
                ).fetchone()
                source_count = db.execute(
                    "SELECT COUNT(*) FROM sources WHERE topic_id=?", (article["topic_id"],)
                ).fetchone()[0]
                if (
                    research is None
                    or research["status"] == "STALE"
                    or source_count != report["source_count"]
                ):
                    raise EditorialError("RESEARCH_STALE", "来源变更后需重新研究和生成稿件")
                if research["status"] != "RESEARCHED":
                    raise EditorialError("FACT_GATE_BLOCKED", "研究材料仍有待核查项")
                if report["blockers"]:
                    raise EditorialError("FACT_GATE_BLOCKED", "仍有事实核查阻塞项")
                topic = self._row(db, "SELECT * FROM topics WHERE id=?", (article["topic_id"],))
                if topic["origin"] in {"NEWS", "RSS"}:
                    published = (
                        datetime.fromisoformat(topic["news_published_at"])
                        if topic["news_published_at"]
                        else None
                    )
                    if (
                        published is None
                        or published.tzinfo is None
                        or not timedelta(0) <= datetime.now(UTC) - published <= timedelta(days=7)
                    ):
                        raise EditorialError(
                            "NEWS_FRESHNESS_REVIEW_REQUIRED",
                            "新闻时间已过期或无效，需重新核对选题与时间表述",
                        )
                if (
                    self.provider.name not in {"mock-fixture", "codex-import"}
                    and report.get("model_review", {}).get("status") != "COMPLETE"
                ):
                    raise EditorialError("MODEL_REVIEW_REQUIRED", "真实模型稿件必须完成事实审查")
                if not fact_attested:
                    raise EditorialError("FACT_ATTESTATION_REQUIRED", "作者必须确认已逐项核查")
                status, approved = "APPROVED", version["id"]
            else:
                status, approved = (
                    ("REVISION_REQUESTED" if decision == "REVISE" else "REJECTED"),
                    None,
                )
            db.execute(
                "UPDATE articles SET status=?,approved_version_id=?,fact_attested_at=? WHERE id=?",
                (
                    status,
                    approved,
                    now() if fact_attested and decision == "APPROVE" else None,
                    article_id,
                ),
            )
            self._audit(
                db,
                "article",
                article_id,
                decision,
                {"version_id": version["id"], "note": note[:2000], "fact_attested": fact_attested},
            )
        return self.article(article_id)

    def audit(self, kind: str, identifier: str) -> list[dict[str, Any]]:
        with self.store.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM audit WHERE target_type=? AND target_id=? ORDER BY id",
                    (kind, identifier),
                )
            ]

    def export_markdown(self, article_id: str) -> Path:
        if self.work_mode == "EDITORIAL_FIRST":
            raise EditorialError("EDITORIAL_ONLY", "EDITORIAL_FIRST 不生成对外导出文件")
        article = self.article(article_id)
        if article["status"] != "APPROVED" or not article["approved_version_id"]:
            raise EditorialError("NOT_APPROVED", "只有已批准版本可以导出")
        version = next(
            item for item in article["versions"] if item["id"] == article["approved_version_id"]
        )
        sources = self.sources(article["topic_id"])
        safe_body = html.escape(version["body"], quote=False)
        # Preserve Markdown syntax, but neutralize raw HTML/MDX on import.
        lines = [
            f"# {html.escape(version['title'], quote=False)}",
            "",
            f"摘要：{html.escape(version['summary'], quote=False)}",
            "",
            safe_body.removeprefix(f"# {version['title']}\n").strip(),
            "",
            "## 参考资料",
            "",
        ]
        lines += [
            f"[S{index}] {html.escape(source['title'], quote=False)} — "
            f"{source['url']}（检索：{source['retrieved_at']}）"
            for index, source in enumerate(sources, 1)
        ]
        lines += ["", "<!-- 图片需在公众号后台人工上传并按素材清单插入；未自动上传。 -->", ""]
        content = "\n".join(lines)
        target = self.export_root / f"{article_id}-{version['id']}.md"
        manifest = self.export_root / f"{article_id}-{version['id']}.assets.json"
        target.write_text(content, encoding="utf-8", newline="\n")
        manifest.write_text(
            json.dumps(
                {
                    "article_id": article_id,
                    "version_id": version["id"],
                    "images_uploaded": False,
                    "images": version["cover"].get("images", []),
                    "cover_suggestions": version["cover"].get("suggestions", []),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        with self.store.write() as db:
            db.execute(
                "INSERT OR IGNORE INTO exports VALUES(?,?,?,?,?,?)",
                (str(uuid4()), article_id, version["id"], str(target), digest, now()),
            )
            self._audit(
                db,
                "article",
                article_id,
                "EXPORT_MARKDOWN",
                {"version_id": version["id"], "sha256": digest},
            )
        return target

    def daily(self, day: date) -> dict[str, Any]:
        """One idempotent daily recommendation task. Lease permits recovery after interruption."""
        day_string, stamp = day.isoformat(), now()
        key = f"daily:{day_string}"
        lease = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        with self.store.write() as db:
            task = db.execute("SELECT * FROM tasks WHERE task_key=?", (key,)).fetchone()
            if task and task["status"] == "DONE":
                return cast(dict[str, Any], decode_json(task["result_json"]))
            if task and task["status"] == "RUNNING" and task["lease_until"] > stamp:
                raise EditorialError("TASK_IN_PROGRESS", "当日任务正在运行")
            if task:
                db.execute(
                    "UPDATE tasks SET status='RUNNING',attempts=attempts+1,"
                    "lease_until=?,updated_at=? "
                    "WHERE task_key=?",
                    (lease, stamp, key),
                )
            else:
                db.execute(
                    "INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        key,
                        "DAILY_TOPICS",
                        "RUNNING",
                        json.dumps({"day": day_string}),
                        1,
                        lease,
                        None,
                        stamp,
                        stamp,
                    ),
                )
        try:
            with self.store.write() as db:
                existing = [
                    dict(row)
                    for row in db.execute(
                        "SELECT t.* FROM daily_recommendations d JOIN topics t ON t.id=d.topic_id "
                        "WHERE d.day=? ORDER BY d.rank",
                        (day_string,),
                    )
                ]
                candidates = existing[:]
                feed_sources = [
                    dict(row)
                    for row in db.execute(
                        "SELECT * FROM sources WHERE topic_id IS NULL AND published_at IS NOT NULL "
                        "ORDER BY published_at DESC LIMIT 30"
                    )
                ]
                for source in feed_sources:
                    if len(candidates) >= 3:
                        break
                    published = datetime.fromisoformat(source["published_at"])
                    if (
                        published.tzinfo is None
                        or published.date() > day
                        or day - published.date() > timedelta(days=7)
                    ):
                        continue
                    title = source["title"][:180]
                    if db.execute(
                        "SELECT 1 FROM topics WHERE normalized=?", (normalized(title),)
                    ).fetchone():
                        continue
                    topic_id = str(uuid4())
                    db.execute(
                        "INSERT INTO topics VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            topic_id,
                            title,
                            normalized(title),
                            "来自授权 RSS；需核实原始来源与研究价值",
                            "RSS",
                            "PROPOSED",
                            source["published_at"],
                            now(),
                            now(),
                        ),
                    )
                    db.execute("UPDATE sources SET topic_id=? WHERE id=?", (topic_id, source["id"]))
                    db.execute(
                        "INSERT INTO daily_recommendations VALUES(?,?,?)",
                        (day_string, topic_id, len(candidates) + 1),
                    )
                    self._audit(
                        db,
                        "topic",
                        topic_id,
                        "RECOMMEND",
                        {"day": day_string, "source_id": source["id"]},
                    )
                    candidates.append(self._row(db, "SELECT * FROM topics WHERE id=?", (topic_id,)))
                for title, idea in EVERGREEN:
                    if len(candidates) >= 3:
                        break
                    if db.execute(
                        "SELECT 1 FROM topics WHERE normalized=?", (normalized(title),)
                    ).fetchone():
                        continue
                    topic_id = str(uuid4())
                    db.execute(
                        "INSERT INTO topics VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            topic_id,
                            title,
                            normalized(title),
                            idea + "；常青选题，尚无来源",
                            "EVERGREEN",
                            "PROPOSED",
                            None,
                            now(),
                            now(),
                        ),
                    )
                    db.execute(
                        "INSERT INTO daily_recommendations VALUES(?,?,?)",
                        (day_string, topic_id, len(candidates) + 1),
                    )
                    self._audit(
                        db, "topic", topic_id, "RECOMMEND", {"day": day_string, "source_id": None}
                    )
                    candidates.append(self._row(db, "SELECT * FROM topics WHERE id=?", (topic_id,)))
                result = {
                    "day": day_string,
                    "topics": candidates,
                    "count": len(candidates),
                    "target": 3,
                    "warning": "常青选题不是当天新闻；研究与发稿须先补充来源并人工审核。",
                }
                db.execute(
                    "UPDATE tasks SET status='DONE',lease_until=NULL,result_json=?,updated_at=? "
                    "WHERE task_key=?",
                    (json.dumps(result, ensure_ascii=False), now(), key),
                )
                return result
        except BaseException:
            with self.store.write() as db:
                db.execute(
                    "UPDATE tasks SET status='FAILED',updated_at=? WHERE task_key=?", (now(), key)
                )
            raise

    def prepare_accepted(self) -> list[dict[str, str]]:
        """Advance accepted topics only; leave source gaps visible for the author."""
        if self.provider.name != "mock-fixture" and not getattr(
            getattr(self.provider, "policy", None), "allow_batch", False
        ):
            raise EditorialError(
                "BATCH_DISABLED", "真实模型批量生成默认关闭；请逐篇操作或显式配置预算与 allow_batch"
            )
        outcomes: list[dict[str, str]] = []
        with self.store.connection() as db:
            topic_ids = [
                row["id"]
                for row in db.execute(
                    "SELECT id FROM topics WHERE status='ACCEPTED' ORDER BY created_at,id"
                )
            ]
        for topic_id in topic_ids:
            try:
                if not self.sources(topic_id):
                    outcomes.append({"topic_id": topic_id, "status": "NEEDS_SOURCES"})
                    continue
                research = self.research(topic_id)
                if research["status"] != "RESEARCHED":
                    outcomes.append({"topic_id": topic_id, "status": str(research["status"])})
                    continue
                article = self.generate(topic_id)
                outcomes.append(
                    {
                        "topic_id": topic_id,
                        "article_id": str(article["id"]),
                        "status": str(article["status"]),
                    }
                )
            except EditorialError as error:
                outcomes.append({"topic_id": topic_id, "status": error.code})
            except ModelError as error:
                outcomes.append({"topic_id": topic_id, "status": error.code})
            except (OSError, TimeoutError, ValueError):
                outcomes.append({"topic_id": topic_id, "status": "PROVIDER_UNAVAILABLE"})
        return outcomes
