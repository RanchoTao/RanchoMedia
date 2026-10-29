"""Reproducible evidence checks for local editorial revisions; never an approval score."""

from __future__ import annotations

import difflib
import hashlib
import re
from typing import Any
from urllib.parse import urlparse

from pydantic import Field

from rancho_wechat.content import StrictModel
from rancho_wechat.editorial import EditorialDesk


class RevisionPacket(StrictModel):
    revision_key: str = Field(min_length=4, max_length=160)
    article_id: str
    expected_version_id: str
    title: str = Field(min_length=4, max_length=180)
    body: str = Field(min_length=40, max_length=100000)
    summary: str = Field(max_length=500)
    note: str = Field(max_length=2000)


def import_revision(desk: EditorialDesk, packet: RevisionPacket) -> dict[str, Any]:
    return desk.edit(
        packet.article_id,
        packet.title,
        packet.body,
        packet.summary,
        packet.note,
        expected_version_id=packet.expected_version_id,
        kind="AI_REVISION",
        revision_key=packet.revision_key,
    )


def paragraphs(body: str) -> list[str]:
    prose = body.split("## 参考资料", 1)[0]
    return [
        part.strip()
        for part in re.split(r"\n\s*\n", prose)
        if part.strip() and not part.lstrip().startswith(("#", "<!--"))
    ]


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def paragraph_diff(before: str, after: str) -> list[dict[str, Any]]:
    old, new = paragraphs(before), paragraphs(after)
    return [
        {
            "operation": op,
            "old_paragraphs": old[a:b],
            "new_paragraphs": new[c:d],
            "old_numbers": list(range(a + 1, b + 1)),
            "new_numbers": list(range(c + 1, d + 1)),
        }
        for op, a, b, c, d in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes()
    ]


def regression(body: str, evidence: dict[str, Any]) -> dict[str, Any]:
    """Checks a reviewed mapping, not arbitrary prose truth or reader understanding."""
    parts = paragraphs(body)
    mappings = evidence.get("paragraphs", [])
    sources = {source["ref"]: source for source in evidence.get("sources", [])}
    issues: dict[str, list[str]] = {
        key: []
        for key in (
            "fact_consistency",
            "paragraph_repetition",
            "source_traceability",
            "case_completeness",
            "quotation_accuracy",
            "reader_comprehension",
        )
    }
    if evidence.get("body_sha256") != digest(body):
        issues["fact_consistency"].append("正文已变动，不能沿用旧版事实映射")
    if len(mappings) != len(parts):
        issues["fact_consistency"].append("段落未完整映射")
    for index, (text, mapping) in enumerate(zip(parts, mappings, strict=False), 1):
        if mapping.get("number") != index or mapping.get("sha256") != digest(text):
            issues["fact_consistency"].append(f"第{index}段与映射不一致")
        kind, refs = mapping.get("kind"), mapping.get("sources", [])
        if kind not in {"FACT", "COMMENT"}:
            issues["fact_consistency"].append(f"第{index}段未区分事实/评论")
        if kind == "FACT" and (not refs or not mapping.get("assertion")):
            issues["fact_consistency"].append(f"第{index}段缺事实断言或来源")
        if kind == "COMMENT" and not mapping.get("note"):
            issues["fact_consistency"].append(f"第{index}段缺评论性质说明")
        for ref in refs:
            source = sources.get(ref)
            if not source or f"[{ref}]" not in text:
                issues["source_traceability"].append(f"第{index}段缺来源或正文引用{ref}")
    if not sources:
        issues["source_traceability"].append("没有可追溯来源")
    for ref, source in sources.items():
        parsed = urlparse(source.get("url", ""))
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or not source.get("locator")
            or not source.get("retrieved_at")
            or source.get("snapshot_sha256") != digest(source.get("snapshot", ""))
            or not source.get("snapshot")
        ):
            issues["source_traceability"].append(f"{ref}缺定位、日期、有效URL或快照摘要")
    unknown = set(re.findall(r"\[(S\d+)\]", body)) - sources.keys()
    if unknown:
        issues["source_traceability"].append("未知来源编号：" + ",".join(sorted(unknown)))
    facts_text = "\n".join(
        text
        for text, mapping in zip(parts, mappings, strict=False)
        if mapping.get("kind") == "FACT"
    )
    if not evidence.get("invariants"):
        issues["fact_consistency"].append("没有逐项事实回归断言")
    for rule in evidence.get("invariants", []):
        target = facts_text if rule.get("scope") == "FACT" else body
        found = re.search(rule["pattern"], target) is not None
        if found != rule.get("required", True):
            issues["fact_consistency"].append(rule["label"])
    near_duplicates = []
    for i, first in enumerate(parts):
        for j in range(i + 1, len(parts)):
            second = parts[j]
            a, b = re.sub(r"\s+", "", first), re.sub(r"\s+", "", second)
            if a == b:
                issues["paragraph_repetition"].append(f"第{i + 1}/{j + 1}段完全重复")
            elif min(len(a), len(b)) > 40 and difflib.SequenceMatcher(a=a, b=b).ratio() > 0.85:
                near_duplicates.append([i + 1, j + 1])
    cases = evidence.get("cases", [])
    if not cases:
        issues["case_completeness"].append("未提供真实案例")
    for case in cases:
        if case.get("source") not in sources or not case.get("locator"):
            issues["case_completeness"].append("案例缺原始定位")
        for field in ("setup", "action", "reasoning", "outcome"):
            if not case.get(field) or case[field] not in body:
                issues["case_completeness"].append(f"案例缺{field}或与正文不符")
    quotes = re.findall(r'[“"]([^”"\n]+)[”"]', "\n".join(parts))
    for quote in quotes:
        if not any(quote in source.get("verbatim", "") for source in sources.values()):
            issues["quotation_accuracy"].append("引语无法逐字定位：" + quote[:40])
    for term in evidence.get("defined_terms", []):
        if term not in body:
            issues["reader_comprehension"].append("缺少术语说明：" + term)
    long_sentences = [
        sentence for sentence in re.split(r"[。！？\n]", "\n".join(parts)) if len(sentence) > 140
    ]
    if long_sentences:
        issues["reader_comprehension"].append(f"有{len(long_sentences)}句超过140字符")
    statuses: dict[str, Any] = {}
    for key, found_issues in issues.items():
        status = "BLOCKED" if found_issues else "PASS"
        if key == "reader_comprehension":
            status = "HOLD"  # A real reader must confirm understanding, not an automated score.
        if key == "paragraph_repetition" and not found_issues and near_duplicates:
            status = "HOLD"
        statuses[key] = {"status": status, "issues": found_issues}
    statuses["paragraph_repetition"]["near_duplicate_pairs"] = near_duplicates
    statuses["quotation_accuracy"]["direct_quote_count"] = len(quotes)
    statuses["reader_comprehension"]["note"] = "术语与句长检查不能代替作者/真实读者验收"
    return {
        "body_sha256": digest(body),
        "checks": statuses,
        "paragraph_count": len(parts),
        "source_count": len(sources),
        "article_status": "IN_REVIEW",
        "approval": "AUTHOR_APPROVAL_REQUIRED",
        "semantic_review": "CURRENT_CODEX_SOURCE_REVIEW_NOT_INDEPENDENT_CERTIFICATION",
        "note": "PASS仅表示与本轮已核对的映射/断言一致，无总分，不授权批准或导出。",
    }
