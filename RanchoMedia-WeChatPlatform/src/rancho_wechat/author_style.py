"""Author decision profiles, explicit confirmation, work lineage and edit evidence."""

from __future__ import annotations

import difflib
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from rancho_wechat.providers import ModelProvider
from rancho_wechat.store import Store
from rancho_wechat.style import edit_signals

CATEGORIES = {
    "title",
    "theme",
    "opening",
    "argument",
    "rhythm",
    "humor",
    "judgment",
    "avoid",
    "ending",
}
STAGES = {"ORIGINAL", "AI_DRAFT", "AUTHOR_EDIT", "APPROVED", "PUBLISHED"}


def stamp() -> str:
    return datetime.now(UTC).isoformat()


def paragraph_changes(before: str, after: str) -> tuple[list[str], list[str]]:
    old = [part.strip() for part in before.split("\n\n") if part.strip()]
    new = [part.strip() for part in after.split("\n\n") if part.strip()]
    deleted, added = [], []
    for tag, i, j, start, end in difflib.SequenceMatcher(
        a=old, b=new, autojunk=False
    ).get_opcodes():
        if tag in {"delete", "replace"}:
            deleted.extend(old[i:j])
        if tag in {"insert", "replace"}:
            added.extend(new[start:end])
    return deleted, added


class AuthorStyleEngine:
    def __init__(self, store: Store) -> None:
        self.store = store

    def add_rule(
        self, category: str, text: str, evidence: dict[str, Any], *, confirmed: bool = False
    ) -> dict[str, Any]:
        if category not in CATEGORIES or not 4 <= len(text.strip()) <= 500:
            raise ValueError("风格类别或偏好长度无效")
        with self.store.write() as db:
            if (
                evidence.get("version_id")
                and db.execute(
                    "SELECT id FROM versions WHERE id=?", (evidence["version_id"],)
                ).fetchone()
                is None
            ):
                raise ValueError("风格证据版本不存在")
            existing = db.execute(
                "SELECT * FROM style_rules WHERE category=? AND text=?", (category, text.strip())
            ).fetchone()
            if existing:
                proofs = json.loads(existing["evidence_json"])
                if evidence not in proofs:
                    proofs.append(evidence)
                status = "CONFIRMED" if confirmed else existing["status"]
                db.execute(
                    "UPDATE style_rules SET evidence_json=?,confidence=?,status=?,"
                    "updated_at=? WHERE id=?",
                    (
                        json.dumps(proofs, ensure_ascii=False),
                        1 if confirmed else min(0.3 * len(proofs), 0.9),
                        status,
                        stamp(),
                        existing["id"],
                    ),
                )
                identifier = existing["id"]
            else:
                identifier = str(uuid4())
                db.execute(
                    "INSERT INTO style_rules VALUES(?,?,?,?,?,?,?,?)",
                    (
                        identifier,
                        category,
                        text.strip(),
                        "CONFIRMED" if confirmed else "CANDIDATE",
                        1 if confirmed else 0.3,
                        json.dumps([evidence], ensure_ascii=False),
                        stamp(),
                        stamp(),
                    ),
                )
            db.execute(
                "INSERT INTO audit(target_type,target_id,action,details_json,at) VALUES(?,?,?,?,?)",
                (
                    "style_rule",
                    identifier,
                    "AUTHOR_CONFIRM" if confirmed else "CANDIDATE",
                    json.dumps(evidence, ensure_ascii=False),
                    stamp(),
                ),
            )
        return next(rule for rule in self.rules() if rule["id"] == identifier)

    def decide(self, identifier: str, decision: str) -> None:
        if decision not in {"CONFIRM", "REVOKE"}:
            raise ValueError("风格规则只允许 CONFIRM / REVOKE")
        with self.store.write() as db:
            if (
                db.execute("SELECT id FROM style_rules WHERE id=?", (identifier,)).fetchone()
                is None
            ):
                raise ValueError("风格规则不存在")
            db.execute(
                "UPDATE style_rules SET status=?,updated_at=? WHERE id=?",
                ("CONFIRMED" if decision == "CONFIRM" else "REVOKED", stamp(), identifier),
            )
            db.execute(
                "INSERT INTO audit(target_type,target_id,action,details_json,at) VALUES(?,?,?,?,?)",
                ("style_rule", identifier, decision, "{}", stamp()),
            )

    def rules(self, *, confirmed_only: bool = False) -> list[dict[str, Any]]:
        with self.store.connection() as db:
            query = (
                "SELECT * FROM style_rules"
                + (" WHERE status='CONFIRMED'" if confirmed_only else "")
                + " ORDER BY created_at,id"
            )
            rows = [dict(row) for row in db.execute(query)]
        for row in rows:
            row["evidence"] = json.loads(row.pop("evidence_json"))
        return rows

    def profile(self) -> dict[str, Any]:
        """Separate observable work metadata from explicit preferences and hypotheses."""
        with self.store.connection() as db:
            samples = [
                dict(row)
                for row in db.execute(
                    "SELECT s.id,s.title,s.kind,s.sha256,s.features_json,w.stage,w.source_url "
                    "FROM style_samples s LEFT JOIN author_work_versions w ON w.sample_id=s.id"
                )
            ]
        for sample in samples:
            sample["observed_features"] = json.loads(sample.pop("features_json"))
        rules = self.rules()
        return {
            "author": "兰澈",
            "facts": samples,
            "published_author_samples": [
                sample
                for sample in samples
                if sample["kind"] == "AUTHOR" and sample["stage"] == "PUBLISHED"
            ],
            "confirmed_preferences": [rule for rule in rules if rule["status"] == "CONFIRMED"],
            "unverified_inferences": [rule for rule in rules if rule["status"] == "CANDIDATE"],
            "revoked": [rule for rule in rules if rule["status"] == "REVOKED"],
            "note": "作品中的写法只证明这篇文章这样写过；推断不是作者明确偏好。"
            "段落统计不证明叙事质量。没有正式作品时不能宣称风格迁移验收。",
        }

    def attach_work(
        self,
        sample_id: str,
        *,
        work_id: str | None = None,
        stage: str = "ORIGINAL",
        parent_sample_id: str | None = None,
        article_version_id: str | None = None,
        source_url: str | None = None,
    ) -> dict[str, Any]:
        if stage not in STAGES or (
            source_url
            and (
                urlparse(source_url).scheme != "https"
                or urlparse(source_url).username
                or urlparse(source_url).password
            )
        ):
            raise ValueError("作品阶段或公开来源链接无效")
        with self.store.write() as db:
            if parent_sample_id == sample_id:
                raise ValueError("作品不能以自身为父版本")
            existing = db.execute(
                "SELECT * FROM author_work_versions WHERE sample_id=?", (sample_id,)
            ).fetchone()
            if existing:
                if stage != existing["stage"] or any(
                    value is not None and value != existing[field]
                    for field, value in (
                        ("work_id", work_id),
                        ("parent_sample_id", parent_sample_id),
                        ("article_version_id", article_version_id),
                        ("source_url", source_url),
                    )
                ):
                    raise ValueError("相同样本的作品阶段或版本关系冲突，未覆盖原记录")
                return dict(existing)
            if parent_sample_id:
                parent = db.execute(
                    "SELECT * FROM author_work_versions WHERE sample_id=?", (parent_sample_id,)
                ).fetchone()
                if parent is None:
                    raise ValueError("先导入并登记父作品版本")
                if work_id and work_id != parent["work_id"]:
                    raise ValueError("父子作品必须属于同一 work_id")
                work_id = parent["work_id"]
            if (
                article_version_id
                and db.execute(
                    "SELECT id FROM versions WHERE id=?", (article_version_id,)
                ).fetchone()
                is None
            ):
                raise ValueError("关联文章版本不存在")
            db.execute(
                "INSERT INTO author_work_versions VALUES(?,?,?,?,?,?,?)",
                (
                    sample_id,
                    work_id or str(uuid4()),
                    stage,
                    parent_sample_id,
                    article_version_id,
                    source_url,
                    stamp(),
                ),
            )
            return dict(
                db.execute(
                    "SELECT * FROM author_work_versions WHERE sample_id=?", (sample_id,)
                ).fetchone()
            )

    def works(self) -> list[dict[str, Any]]:
        with self.store.connection() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT w.*,s.title,s.kind FROM author_work_versions w "
                    "JOIN style_samples s ON s.id=w.sample_id ORDER BY w.created_at"
                )
            ]

    def analyze(self, sample_id: str, provider: ModelProvider) -> list[dict[str, Any]]:
        with self.store.connection() as db:
            sample = db.execute("SELECT * FROM style_samples WHERE id=?", (sample_id,)).fetchone()
        if sample is None or sample["kind"] != "AUTHOR" or not sample["text"]:
            raise ValueError("仅分析作者自有作品正文")
        result = provider.generate_json(
            "style",
            {
                "sample_id": sample_id,
                "title": sample["title"],
                "text": sample["text"],
                "source_is_untrusted": True,
            },
        )
        candidates = result.get("rules")
        if not isinstance(candidates, list):
            raise ValueError("风格分析结果无效")
        rules = []
        for item in candidates[:15]:
            if not isinstance(item, dict) or item.get("category") not in CATEGORIES:
                continue
            evidence = str(item.get("evidence", ""))
            if len(evidence) < 4 or evidence not in sample["text"]:
                continue
            rules.append(
                self.add_rule(
                    item["category"],
                    str(item.get("text", "")),
                    {"sample_id": sample_id, "quote": evidence, "provider": provider.name},
                )
            )
        return rules

    def record_edit(
        self,
        article_id: str,
        version_id: str,
        previous_id: str,
        before: str,
        after: str,
        explicit: list[str],
        *,
        author_edit: bool = True,
    ) -> None:
        deleted, added = paragraph_changes(before, after)
        patterns = edit_signals(before, after) if author_edit else []
        with self.store.write() as db:
            db.execute(
                "INSERT INTO edit_feedback VALUES(?,?,?,?,?,?)",
                (
                    version_id,
                    previous_id,
                    json.dumps(deleted, ensure_ascii=False),
                    json.dumps(added, ensure_ascii=False),
                    json.dumps(patterns, ensure_ascii=False),
                    json.dumps(explicit, ensure_ascii=False),
                ),
            )
        for kind, text in patterns:
            self.add_rule(
                "avoid" if kind == "avoid" else "rhythm",
                text,
                {
                    "article_id": article_id,
                    "version_id": version_id,
                    "previous_version_id": previous_id,
                },
            )
        for text in explicit:
            self.add_rule(
                "judgment",
                text,
                {"article_id": article_id, "version_id": version_id, "author_explicit": True},
                confirmed=True,
            )

    def feedback(self, article_id: str) -> dict[str, Any]:
        with self.store.connection() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT f.*,v.ordinal,v.kind,v.note FROM edit_feedback f "
                    "JOIN versions v ON v.id=f.version_id WHERE v.article_id=? ORDER BY v.ordinal",
                    (article_id,),
                )
            ]
        for row in rows:
            for key in ("deleted", "added", "patterns", "explicit_preferences"):
                row[key] = json.loads(row.pop(key + "_json"))
        return {
            "article_id": article_id,
            "versions": rows,
            "rules": [
                rule
                for rule in self.rules()
                if any(proof.get("article_id") == article_id for proof in rule["evidence"])
            ],
        }
