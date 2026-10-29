"""Inspectable textual signals, not a truth detector or an author approval score."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any


def quality_signals(body: str, source_refs: set[str], minimum: int = 1200) -> dict[str, Any]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]
    chinese = len(re.findall(r"[\u4e00-\u9fff]", body))
    citations = set(re.findall(r"\[(S\d+)\]", body))
    blockers = []
    if chinese < minimum:
        blockers.append(f"中文正文不足 {minimum} 字（当前 {chinese} 字）")
    if not citations or not citations.issubset(source_refs):
        blockers.append("正文来源标记缺失或未知")
    repeated = [text for text, count in Counter(paragraphs).items() if count > 1]
    if repeated:
        blockers.append("正文有完全重复段落")
    patterns = {
        "模板过渡": r"总而言之|综上所述|在这个快速发展的时代|让我们一起|值得注意的是",
        "强调式转折": r"不是[^。\n]{1,50}而是|更[有值]得[^。\n]{0,12}是|真正值得",
        "自我说明": r"我(?:更愿意|希望|不太想|想先|更想)|这不是一句",
    }
    signals = [
        {"pattern": label, "text": match.group(), "offset": match.start()}
        for label, pattern in patterns.items()
        for match in re.finditer(pattern, body)
    ]
    return {
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "characters": len(body),
        "chinese_characters": chinese,
        "paragraphs": len(paragraphs),
        "citations": sorted(citations),
        "blockers": blockers,
        "signals_for_editor": signals,
        "repeated_paragraphs": repeated,
        "author_approved": False,
        "content_judgment": "AUTHOR_REVIEW_REQUIRED",
        "note": "规则只检查长度、格式和重复；不证明事实真实性、独立观点或文学质量。",
    }
