"""Bounded real Codex production using existing CLI receipts and editor contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import Field

from rancho_wechat.codex_runner import run_once
from rancho_wechat.content import StrictModel
from rancho_wechat.editorial import EditorialDesk, EditorialError
from rancho_wechat.network import Director, fingerprint
from rancho_wechat.production import WritingBrief, save_json, strict_schema


class ClaimReview(StrictModel):
    claim_id: str
    status: str
    reason: str


class Finding(StrictModel):
    severity: str
    text: str


class AutoReview(StrictModel):
    verdict: str
    risk: str
    claim_checks: list[ClaimReview] = Field(min_length=1)
    findings: list[Finding]
    review_note: str


def produce(
    desk: EditorialDesk,
    root: Path,
    brief_path: Path,
    *,
    recover_review: bool = False,
) -> dict[str, Any]:
    brief = WritingBrief.model_validate_json(brief_path.read_text(encoding="utf-8-sig"))
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / "production-input.json"
    digest = fingerprint(brief.model_dump(mode="json"))
    if manifest_path.exists():
        if json.loads(manifest_path.read_text(encoding="utf-8"))["brief_sha256"] != digest:
            raise EditorialError("PRODUCTION_INPUT_CONFLICT", "请为不同选题使用新的工作目录")
    else:
        save_json(manifest_path, {"brief_sha256": digest, "daily_call_limit": 3})
    writer_prompt, writer_schema = root / "writer-prompt.txt", root / "writer-schema.json"
    if not writer_prompt.exists():
        writer_prompt.write_text(
            "按结构返回研究、标题、正文与自查。中性RanchoMedia编辑部口吻，不能署个人名，"
            "不能第一人称或编造经验。只使用所给原始来源，假设例子明确标注。"
            "正文1200至1800汉字，使用[S1]等引用，claims_used只含已提供ID。"
            "fact_check.independence=SAME_SESSION_SELF_CHECK。不要调用工具。\n"
            + brief.model_dump_json(),
            encoding="utf-8",
        )
        save_json(writer_schema, strict_schema([claim.ref for claim in brief.claims]))
    writer_receipt = root / "writer-call.json"
    if not writer_receipt.exists():
        save_json(
            writer_receipt,
            run_once(
                desk.store,
                root / "codex-runs",
                writer_prompt,
                writer_schema,
                timeout=300,
                daily_runs=3,
            ),
        )
    draft, _ = Director._call_output(writer_receipt)
    stem = "review-recovery" if recover_review else "review"
    prompt, schema = root / (stem + "-prompt.txt"), root / (stem + "-schema.json")
    if not prompt.exists():
        prompt.write_text(
            "独立核查，不相信初稿自己的PASS。核对所有核心事实/引用、年份、分母、虚构案例、"
            "个人署名和风险。低风险且全部关键事实可溯源才PASS。claim_checks覆盖全部事实ID。"
            "verdict=PASS/REVISE/REVIEW_REQUIRED；risk=LOW/HIGH/UNKNOWN；"
            "claim status=SOURCE_SUPPORTED/UNVERIFIED/CONTRADICTED；"
            "finding severity=BLOCKER/WARNING。只输出审稿，合计不超过500中文字；不生成平台版本。\n"
            + json.dumps(
                {
                    "sources": brief.model_dump(mode="json")["sources"],
                    "claims": [claim.model_dump() for claim in brief.claims],
                    "body": draft["article"]["body"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        save_json(schema, AutoReview.model_json_schema())
    call_path = root / (stem + "-call.json")
    if not call_path.exists():
        save_json(
            call_path,
            run_once(desk.store, root / "codex-runs", prompt, schema, timeout=180, daily_runs=3),
        )
    Director._call_output(call_path)  # No blind replay of an UNKNOWN existing call.
    return {
        "status": "WRITTEN_AND_REVIEWED",
        "writer_receipt": str(writer_receipt),
        "review_receipt": str(call_path),
        "billing_amount": "UNKNOWN",
        "next": "prepare semantic channel variants, then network-ingest; no automatic publication",
    }
