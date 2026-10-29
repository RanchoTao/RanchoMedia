"""Reusable local review/import bundles; platform-side acceptance remains manual."""

from __future__ import annotations

import hashlib
import json
import shutil
import struct
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

from rancho_wechat.content import safe_article_markdown
from rancho_wechat.editorial import EditorialDesk, EditorialError, now


def register_asset(
    desk: EditorialDesk,
    article_id: str,
    content: bytes,
    description: str,
    position: str,
    rights_note: str,
) -> dict[str, Any]:
    desk.article(article_id)
    if (
        len(content) > 2_000_000
        or len(content) < 32
        or not content.startswith(b"\x89PNG\r\n\x1a\n")
        or content[12:16] != b"IHDR"
    ):
        raise EditorialError("INVALID_ASSET", "仅接收 2 MB 以内 PNG 图片")
    width, height = struct.unpack(">II", content[16:24])
    if not 1 <= width <= 10000 or not 1 <= height <= 10000 or not rights_note.strip():
        raise EditorialError("INVALID_ASSET", "图片尺寸无效或缺少权利说明")
    digest, identifier = hashlib.sha256(content).hexdigest(), str(uuid4())
    root = desk.export_root.parent / "assets" / article_id
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{identifier}.png"
    with desk.store.write() as db:
        existing = db.execute(
            "SELECT * FROM article_assets WHERE article_id=? AND sha256=?", (article_id, digest)
        ).fetchone()
        if existing:
            return dict(existing)
        path.write_bytes(content)
        db.execute(
            "INSERT INTO article_assets VALUES(?,?,?,?,?,?,?,?)",
            (
                identifier,
                article_id,
                str(path),
                description[:1000],
                position[:200],
                rights_note[:1000],
                digest,
                now(),
            ),
        )
        db.execute(
            "UPDATE articles SET status='IN_REVIEW',approved_version_id=NULL,"
            "fact_attested_at=NULL WHERE id=?",
            (article_id,),
        )
        desk._audit(
            db,
            "article",
            article_id,
            "REGISTER_LOCAL_ASSET",
            {"asset_id": identifier, "sha256": digest, "images_uploaded": False},
        )
    return {"id": identifier, "path": str(path), "sha256": digest}


def write_bundle(desk: EditorialDesk, article_id: str, *, approved: bool = True) -> Path:
    if desk.work_mode == "EDITORIAL_FIRST":
        raise EditorialError("EDITORIAL_ONLY", "本轮关闭所有导出包，包括待审 ZIP")
    article = desk.article(article_id)
    if approved and article["status"] != "APPROVED":
        raise EditorialError("NOT_APPROVED", "正式导入包只允许已批准稿件；待审稿可另存审阅包")
    version = article["versions"][-1]
    root = desk.export_root / article_id / str(version["id"])
    root.mkdir(parents=True, exist_ok=True)
    if approved:
        markdown = desk.export_markdown(article_id).read_text(encoding="utf-8")
    else:
        markdown = safe_article_markdown(article)
    (root / "article.md").write_text(markdown, encoding="utf-8", newline="\n")
    sources = desk.sources(article["topic_id"])
    snapshot = [
        {
            **source,
            "ref": f"S{index}",
            "excerpt_sha256": hashlib.sha256(source["excerpt"].encode()).hexdigest(),
        }
        for index, source in enumerate(sources, 1)
    ]
    with desk.store.connection() as db:
        research = db.execute(
            "SELECT note FROM research WHERE topic_id=?", (article["topic_id"],)
        ).fetchone()
        execution = db.execute(
            "SELECT execution_json FROM content_imports WHERE article_id=?", (article_id,)
        ).fetchone()
        assets = [
            dict(row)
            for row in db.execute(
                "SELECT * FROM article_assets WHERE article_id=? ORDER BY created_at", (article_id,)
            )
        ]
    asset_root = (desk.export_root.parent / "assets").resolve()
    for asset in assets:
        original = Path(asset["path"]).resolve()
        if (
            not original.is_relative_to(asset_root)
            or hashlib.sha256(original.read_bytes()).hexdigest() != asset["sha256"]
        ):
            raise EditorialError("ASSET_INTEGRITY_FAILED", "图片素材路径或摘要不符")
        asset["bundle_file"] = f"asset-{asset['id']}.png"
        shutil.copyfile(original, root / asset["bundle_file"])
        asset.pop("path")
    data: dict[str, Any] = {
        "title-summary.json": {
            "title": version["title"],
            "summary": version["summary"],
            "title_options": version["fact_report"].get("title_options", []),
        },
        "sources.json": snapshot,
        "claims.json": desk.claims(article["topic_id"]),
        "fact-check.json": version["fact_report"],
        "images.json": {
            **version["cover"],
            "images_uploaded": False,
            "local_assets": assets,
            "instruction": "按 position 人工插入；图片版权与封面需作者逐项确认",
        },
        "feedback.json": desk.style_engine.feedback(article_id),
        "receipt.json": {
            "article_id": article_id,
            "version_id": version["id"],
            "status": article["status"],
            "author_approved": article["status"] == "APPROVED",
            "bundle_kind": "APPROVED_IMPORT_PACKAGE" if approved else "REVIEW_ONLY_NOT_APPROVED",
            "platform_acceptance": "MANUAL_ACCEPTANCE_REQUIRED",
            "public_published": False,
            "execution": json.loads(execution[0])
            if execution
            else version["fact_report"].get(
                "execution",
                {
                    "provider": version["fact_report"].get(
                        "generation_provider", "legacy-unrecorded"
                    )
                },
            ),
        },
    }
    for name, value in data.items():
        (root / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    (root / "research.md").write_text(research[0] if research else "无研究记录", encoding="utf-8")
    references = "\n".join(
        f"[S{index}] {source['title']}\n{source['url']}\n检索时间：{source['retrieved_at']}\n"
        for index, source in enumerate(sources, 1)
    )
    (root / "references.md").write_text(references, encoding="utf-8")
    checklist = (
        "# 公众号人工导入检查表\n\n"
        "状态：MANUAL_ACCEPTANCE_REQUIRED\n\n"
        + (
            "本稿已有本地作者批准记录。\n\n"
            if approved
            else "本包仅供审阅；尚无作者批准，不可作为发表授权。\n\n"
        )
        + "- [ ] 作者逐项核实正文、原始来源、日期、数字与引用\n"
        "- [ ] 确认研究结果、观察、推测与未来计划的表述边界\n"
        "- [ ] 在实际公众号后台人工导入 article.md，核对标题与摘要\n"
        "- [ ] 根据 images.json 准备有权使用的封面和图片，人工插入\n"
        "- [ ] 手机预览段落、标题、链接、图片位置及参考资料\n"
        "- [ ] 记录后台导入限制、排版差异和人工验收日期\n\n"
        "本系统没有上传、公开发表或群发接口。\n"
    )
    (root / "manual-checklist.md").write_text(checklist, encoding="utf-8")
    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.iterdir()
        if path.is_file() and path.name != "manifest.json"
    }
    (root / "manifest.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    target = root.with_suffix(".zip")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.iterdir()):
            archive.write(path, path.name)
    with desk.store.write() as db:
        desk._audit(
            db,
            "article",
            article_id,
            "EXPORT_IMPORT_BUNDLE" if approved else "EXPORT_REVIEW_BUNDLE",
            {
                "version_id": version["id"],
                "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "platform_acceptance": "MANUAL_ACCEPTANCE_REQUIRED",
            },
        )
    return target
