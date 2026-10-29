"""Explainable, reversible author-style signals; no foundation-model fine-tuning."""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

STYLE_RULES = (
    "不强调年龄、学历、身份或逆袭叙事。",
    "项目文章不写成产品说明书。",
    "不用夸张标题、廉价励志、生硬金句或刻意笑话。",
    "先展现事实，再自然展开作者判断；观点可以鲜明，但不捏造事实。",
    "避免重复解释和连续免责声明。",
    "不机械模仿外部作者；作者最后的编辑选择优先。",
)


def read_sample(path: Path) -> str:
    if path.stat().st_size > 2_000_000:
        raise ValueError("样本超过 2 MB")
    if path.suffix.lower() in {".md", ".txt"}:
        text = path.read_text(encoding="utf-8-sig")
        prefix = text.lstrip().lower()
        if re.search(r"<!doctype\s+html|<html\b|<script\b", prefix):
            raise ValueError("网页HTML不能冒充作者Markdown；请人工提供正文")
        if prefix.startswith(("checking your browser", "verify you are human", "请完成验证")):
            raise ValueError("验证码或验证页面不能作为作者作品")
        return text
    if path.suffix.lower() != ".docx":
        raise ValueError("仅支持 Markdown、TXT、DOCX")
    with zipfile.ZipFile(path) as archive:
        member = archive.getinfo("word/document.xml")
        if member.file_size > 5_000_000:
            raise ValueError("DOCX 正文过大")
        root = ElementTree.fromstring(archive.read(member))
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = [
        "".join(node.text or "" for node in para.findall(".//w:t", namespace))
        for para in root.findall(".//w:p", namespace)
    ]
    return "\n\n".join(part for part in paragraphs if part.strip())


def sample_features(text: str) -> dict[str, int | float | str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    headings = [line for line in text.splitlines() if line.lstrip().startswith("#")]
    lengths = [len(part) for part in paragraphs]
    return {
        "paragraphs": len(paragraphs),
        "average_paragraph_chars": round(sum(lengths) / max(1, len(lengths)), 1),
        "headings": len(headings),
        "question_marks": text.count("？") + text.count("?"),
        "opening_type": "question" if paragraphs and "？" in paragraphs[0] else "statement",
    }


def content_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def edit_signals(before: str, after: str) -> list[tuple[str, str]]:
    """Conservative hypotheses. One edit never becomes an active preference."""
    result: list[tuple[str, str]] = []
    vague = ("总而言之", "综上所述", "在这个快速发展的时代", "值得注意的是")
    if any(phrase in before and phrase not in after for phrase in vague):
        result.append(("avoid", "减少空泛总结和模板化过渡"))
    if len(after) < len(before) * 0.8 and len(before) > 100:
        result.append(("prefer", "压缩重复解释，保留事实与判断"))
    if before.count("？") < after.count("？"):
        result.append(("prefer", "必要时用问题开启叙事"))
    return result
