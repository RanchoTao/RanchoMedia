"""Manual operation and explicit local scheduling of the WeChat editorial desk."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from rancho_wechat.api_provider import CompatibleProvider, ModelError, load_config
from rancho_wechat.codex_runner import run_once
from rancho_wechat.content import ContentPackage, import_package, load_package
from rancho_wechat.editorial import EditorialDesk, EditorialError
from rancho_wechat.editorial_review import RevisionPacket, import_revision, regression
from rancho_wechat.exports import register_asset, write_bundle
from rancho_wechat.feeds import ingest_configured
from rancho_wechat.network import CHANNELS, Director, network_root
from rancho_wechat.network_editorial import produce
from rancho_wechat.papers import discover_arxiv
from rancho_wechat.production import run_article, scheduled_tick
from rancho_wechat.providers import ExternalWriter, MockProvider, ModelProvider, OllamaProvider
from rancho_wechat.scheduler import run_forever
from rancho_wechat.store import Store
from rancho_wechat.web import create_app


def workspace() -> Path:
    return Path(os.environ.get("WECHAT_WORKSPACE", ".runtime")).resolve()


def build_desk() -> EditorialDesk:
    root = workspace()
    provider_kind = os.environ.get("WECHAT_PROVIDER", "codex-import").lower()
    provider: ModelProvider
    store = Store(root / "editor.db")
    if provider_kind == "mock":
        provider = MockProvider()
    elif provider_kind == "codex-import":
        provider = ExternalWriter()
    elif provider_kind == "ollama":
        provider = OllamaProvider(
            os.environ.get("WECHAT_OLLAMA_MODEL", ""),
            os.environ.get("WECHAT_OLLAMA_URL", "http://127.0.0.1:11434"),
        )
    elif provider_kind in {"compatible", "deepseek", "qwen"}:
        roles, policy = load_config(
            Path(os.environ.get("WECHAT_MODEL_CONFIG", str(root / "models.json")))
        )
        provider = CompatibleProvider(store, roles, policy)
    else:
        raise ValueError("WECHAT_PROVIDER 支持 codex-import/mock/ollama/compatible/deepseek/qwen")
    return EditorialDesk(
        store,
        provider,
        root / "exports",
        work_mode=os.environ.get("WECHAT_WORK_MODE", "EDITORIAL_FIRST"),
    )


def editor_token(root: Path) -> str:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "editor-token"
    if not path.exists():
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(secrets.token_urlsafe(36))
    if path.is_symlink():
        raise ValueError("编辑令牌路径不能是符号链接")
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < 24:
        raise ValueError("本地编辑令牌无效")
    return value


def demo(desk: EditorialDesk, out: Path) -> dict[str, str]:
    """Writes an explicitly unapproved mock example, never a publish-ready artifact."""
    title = "本地模型的结构化输出怎样接受事实检查"
    existing = next((item for item in desk.list_topics() if item["title"] == title), None)
    topic = existing or desk.create_topic(title, "用文档示例讨论输出约束和人工核验")
    if topic["status"] == "PROPOSED":
        topic = desk.decide_topic(topic["id"], "ACCEPT")
    excerpt = (
        "Structured output format for the model to generate a response from. "
        'Supports either the string "json" or a JSON schema object.'
    )
    desk.add_source(
        "Ollama Generate API 官方文档",
        "https://docs.ollama.com/api/generate",
        excerpt,
        "PRIMARY",
        topic["id"],
    )
    desk.research(topic["id"])
    article = desk.generate(topic["id"])
    version = article["versions"][-1]
    out.mkdir(parents=True, exist_ok=True)
    path = out / "offline-mock-draft-NOT-APPROVED.md"
    content = (
        "<!-- OFFLINE MOCK / NOT APPROVED / NOT PUBLISHED -->\n\n"
        + version["body"]
        + "\n\n来源：[S1] https://docs.ollama.com/api/generate\n"
    )
    path.write_text(content, encoding="utf-8", newline="\n")
    receipt = {
        "status": "MOCK_DRAFT_NOT_APPROVED",
        "provider": desk.provider.name,
        "topic_id": topic["id"],
        "article_id": article["id"],
        "version_id": version["id"],
        "markdown": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "fact_blockers": version["fact_report"]["blockers"],
    }
    (out / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="兰澈的本地公众号编辑台（无公开发布功能）")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("network-status", "network-tick"):
        sub.add_parser(command, help="Director 本地状态或一次持久化调度")
    worker = sub.add_parser("network-worker", help="持久化平台队列；默认30秒检查一次")
    worker.add_argument("--cycles", type=int, default=0, help="0为持续运行，正数为有界验收")
    worker.add_argument("--interval", type=int, default=30)
    pause = sub.add_parser("network-pause", help="暂停/恢复全部或单个平台；不改变授权")
    pause.add_argument("--channel", choices=CHANNELS)
    pause.add_argument("--resume", action="store_true")
    for command in ("network-produce", "network-ingest"):
        operation = sub.add_parser(command, help="真实Codex写作/审稿或幂等导入实际回执")
        operation.add_argument("brief", type=Path)
        if command == "network-produce":
            operation.add_argument("--recover-review", action="store_true")
    queued = sub.add_parser("network-enqueue", help="仅排期Director新稿；不批准旧稿")
    queued.add_argument("production", type=Path)
    queued.add_argument("--at", required=True, help="含时区的ISO时间")
    website_receipt = sub.add_parser("network-record-website", help="记录单篇已核验公网回执")
    website_receipt.add_argument("receipt", type=Path)
    served = sub.add_parser("serve", help="启动 127.0.0.1 编辑台")
    served.add_argument("--port", type=int, default=8765)
    sub.add_parser("doctor", help="显示模型配置状态和当日用量；不打印密钥")
    sub.add_parser("style-profile", help="区分样本事实、已确认偏好与未验证推断")
    revision_import = sub.add_parser("import-revision", help="幂等导入AI编辑稿，始终待审")
    revision_import.add_argument("path", type=Path)
    checked = sub.add_parser("check-editorial", help="按已核对事实映射执行六项质量回归，无总分")
    checked.add_argument("article_id")
    checked.add_argument("evidence", type=Path)
    article_run = sub.add_parser("codex-article", help="一次真实 Codex 长文生成、检查及待审导入")
    article_run.add_argument("brief", type=Path)
    article_run.add_argument("--timeout", type=int, default=300)
    article_run.add_argument("--daily-runs", type=int, default=1)
    article_run.add_argument("--at", help="仅在上海时区到达 HH:MM 后运行，适合定时器触发")
    once = sub.add_parser(
        "codex-once", help="显式调用受支持的 Codex CLI 一次；默认每天最多一次，不自动导入"
    )
    once.add_argument("prompt", type=Path)
    once.add_argument("schema", type=Path)
    once.add_argument("--timeout", type=int, default=120)
    once.add_argument("--daily-runs", type=int, default=1)
    content = sub.add_parser("import-content", help="导入 Codex 真实写作标准内容包")
    content.add_argument("path", type=Path)
    schema = sub.add_parser("content-schema", help="写出标准内容包 JSON Schema")
    schema.add_argument("path", type=Path)
    exported = sub.add_parser("export-bundle", help="导出公众号导入包或待审阅包")
    exported.add_argument("article_id")
    exported.add_argument("--review-only", action="store_true")
    asset = sub.add_parser("import-asset", help="保存本地 PNG 素材与插入位置，不上传公众号")
    asset.add_argument("article_id")
    asset.add_argument("path", type=Path)
    asset.add_argument("--description", required=True)
    asset.add_argument("--position", required=True)
    asset.add_argument("--rights", required=True)
    feedback = sub.add_parser("feedback", help="显示版本差异及可解释风格反馈")
    feedback.add_argument("article_id")
    rule = sub.add_parser("style-rule", help="作者明确确认一条风格规则")
    rule.add_argument("category")
    rule.add_argument("text")
    rule.add_argument("--note", default="作者通过 CLI 明确确认")
    decision = sub.add_parser("style-decision", help="确认或撤销候选风格规则")
    decision.add_argument("rule_id")
    decision.add_argument("decision", choices=["CONFIRM", "REVOKE"])
    analyze = sub.add_parser("analyze-style", help="由已配置 Provider 分析作者作品，结果仅为候选")
    analyze.add_argument("sample_id")
    daily = sub.add_parser("daily", help="执行一次幂等的每日选题任务")
    daily.add_argument("--day", default=date.today().isoformat())
    daily.add_argument("--ingest-feeds", action="store_true")
    sub.add_parser("prepare", help="为已接受且有来源的选题生成待审稿")
    schedule = sub.add_parser("schedule", help="本地持续运行每日任务")
    schedule.add_argument("--at", default="08:00", help="Asia/Shanghai HH:MM")
    imported = sub.add_parser("import-style", help="导入作者作品或外部结构参考")
    imported.add_argument("path", type=Path)
    imported.add_argument("--title", required=True)
    imported.add_argument("--kind", choices=["AUTHOR", "REFERENCE"], default="AUTHOR")
    imported.add_argument(
        "--stage",
        choices=["ORIGINAL", "AI_DRAFT", "AUTHOR_EDIT", "APPROVED", "PUBLISHED"],
        default="ORIGINAL",
    )
    imported.add_argument("--work-id")
    imported.add_argument("--parent-sample-id")
    imported.add_argument("--article-version-id")
    imported.add_argument("--source-url")
    papers = sub.add_parser("papers", help="查询最多 3 篇 arXiv 原始论文元数据")
    papers.add_argument("topic_id")
    papers.add_argument("query")
    example = sub.add_parser("demo", help="生成未批准的离线 Mock 示例文件")
    example.add_argument("--out", type=Path, default=Path("examples/demo-output"))
    args = parser.parse_args(argv)
    try:
        desk = build_desk()
        if args.command.startswith("network-"):
            director = Director(desk, network_root(desk))
            if args.command == "network-status":
                result = director.status()
            elif args.command == "network-record-website":
                director.record_website_receipt(args.receipt)
                result = director.status()
            elif args.command == "network-produce":
                result = produce(
                    desk, director.root, args.brief, recover_review=args.recover_review
                )
            elif args.command == "network-ingest":
                result = director.ingest(args.brief)
            elif args.command == "network-enqueue":
                result = {
                    "task_keys": director.enqueue(
                        json.loads(args.production.read_text(encoding="utf-8")), args.at
                    )
                }
            elif args.command == "network-pause":
                director.set_pause(not args.resume, args.channel)
                result = director.status()
            elif args.command == "network-worker":
                if args.cycles < 0 or not 1 <= args.interval <= 60:
                    raise ValueError("cycles>=0; interval需在1至60秒")
                completed = 0
                while True:
                    print(json.dumps(director.tick(), ensure_ascii=False), flush=True)
                    completed += 1
                    if args.cycles and completed >= args.cycles:
                        break
                    time.sleep(args.interval)
                return 0
            else:
                result = director.tick()
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "serve":
            import uvicorn

            token = editor_token(workspace())
            if not 1024 <= args.port <= 65535:
                raise ValueError("端口需在 1024–65535 范围")
            print(
                f"编辑台：http://127.0.0.1:{args.port} ；"
                f"本地令牌文件：{workspace() / 'editor-token'}",
                flush=True,
            )
            uvicorn.run(create_app(desk, token), host="127.0.0.1", port=args.port, access_log=False)
        elif args.command == "import-revision":
            if args.path.stat().st_size > 1_000_000:
                raise ValueError("编辑内容包超过1MB")
            packet = RevisionPacket.model_validate_json(args.path.read_text(encoding="utf-8-sig"))
            revised = import_revision(desk, packet)
            print(
                json.dumps(
                    {
                        "article_id": revised["id"],
                        "status": revised["status"],
                        "version_id": revised["current_version_id"],
                        "approval": "AUTHOR_APPROVAL_REQUIRED",
                    },
                    ensure_ascii=False,
                )
            )
        elif args.command == "check-editorial":
            if args.evidence.stat().st_size > 1_000_000:
                raise ValueError("编辑映射超过1MB")
            article = desk.article(args.article_id)
            editorial_report = regression(
                article["versions"][-1]["body"],
                json.loads(args.evidence.read_text(encoding="utf-8-sig")),
            )
            print(json.dumps(editorial_report, ensure_ascii=False, indent=2))
            if any(item["status"] == "BLOCKED" for item in editorial_report["checks"].values()):
                return 2
        elif args.command == "style-profile":
            print(json.dumps(desk.style_engine.profile(), ensure_ascii=False, indent=2))
        elif args.command == "codex-article":
            options = {"timeout": args.timeout, "daily_runs": args.daily_runs}
            result = (
                scheduled_tick(desk, workspace(), args.brief, args.at, **options)
                if args.at
                else run_article(desk, workspace(), args.brief, **options)
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "doctor":
            report: dict[str, Any] = (
                desk.provider.doctor()
                if isinstance(desk.provider, CompatibleProvider)
                else {
                    "provider": desk.provider.name,
                    "generation_mode": "EXTERNAL_CODEX_IMPORT"
                    if desk.provider.name == "codex-import"
                    else "MOCK_FIXTURE"
                    if desk.provider.name == "mock-fixture"
                    else "LOCAL_OLLAMA",
                    "public_publish_enabled": False,
                }
            )
            if isinstance(desk.provider, CompatibleProvider):
                report["usage"] = desk.provider.usage()
            print(json.dumps(report, ensure_ascii=False, indent=2))
        elif args.command == "codex-once":
            print(
                json.dumps(
                    run_once(
                        desk.store,
                        workspace() / "codex-runs",
                        args.prompt,
                        args.schema,
                        timeout=args.timeout,
                        daily_runs=args.daily_runs,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "content-schema":
            args.path.parent.mkdir(parents=True, exist_ok=True)
            args.path.write_text(
                json.dumps(ContentPackage.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(str(args.path.resolve()))
        elif args.command == "import-content":
            article = import_package(desk, load_package(args.path))
            print(
                json.dumps(
                    {
                        "article_id": article["id"],
                        "status": article["status"],
                        "version_id": article["current_version_id"],
                    },
                    ensure_ascii=False,
                )
            )
        elif args.command == "export-bundle":
            print(write_bundle(desk, args.article_id, approved=not args.review_only))
        elif args.command == "import-asset":
            if args.path.stat().st_size > 2_000_000:
                raise ValueError("图片超过 2 MB")
            print(
                json.dumps(
                    register_asset(
                        desk,
                        args.article_id,
                        args.path.read_bytes(),
                        args.description,
                        args.position,
                        args.rights,
                    ),
                    ensure_ascii=False,
                )
            )
        elif args.command == "feedback":
            print(
                json.dumps(
                    desk.style_engine.feedback(args.article_id), ensure_ascii=False, indent=2
                )
            )
        elif args.command == "style-rule":
            print(
                json.dumps(
                    desk.style_engine.add_rule(
                        args.category,
                        args.text,
                        {"author_explicit": True, "note": args.note},
                        confirmed=True,
                    ),
                    ensure_ascii=False,
                )
            )
        elif args.command == "style-decision":
            desk.style_engine.decide(args.rule_id, args.decision)
            print("已保存作者的风格选择")
        elif args.command == "analyze-style":
            print(
                json.dumps(
                    desk.style_engine.analyze(args.sample_id, desk.provider),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "daily":
            if args.ingest_feeds:
                print(
                    json.dumps(
                        ingest_configured(desk, workspace() / "feeds.json"), ensure_ascii=False
                    )
                )
            print(
                json.dumps(desk.daily(date.fromisoformat(args.day)), ensure_ascii=False, indent=2)
            )
        elif args.command == "schedule":
            hour, minute = (int(part) for part in args.at.split(":"))
            print("本地调度运行中；停止进程不会跳过审核，重启后同日任务去重。")
            run_forever(desk, hour, minute)
        elif args.command == "prepare":
            print(json.dumps(desk.prepare_accepted(), ensure_ascii=False, indent=2))
        elif args.command == "import-style":
            result = desk.import_style(args.path, args.title, args.kind)
            lineage = desk.style_engine.attach_work(
                result["id"],
                work_id=args.work_id,
                stage=args.stage,
                parent_sample_id=args.parent_sample_id,
                article_version_id=args.article_version_id,
                source_url=args.source_url,
            )
            print(
                json.dumps(
                    {
                        "id": result["id"],
                        "features": json.loads(result["features_json"]),
                        "lineage": lineage,
                    },
                    ensure_ascii=False,
                )
            )
        elif args.command == "papers":
            print(
                json.dumps(
                    discover_arxiv(desk, args.topic_id, args.query),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "demo":
            print(json.dumps(demo(desk, args.out.resolve()), ensure_ascii=False, indent=2))
    except (EditorialError, ModelError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
