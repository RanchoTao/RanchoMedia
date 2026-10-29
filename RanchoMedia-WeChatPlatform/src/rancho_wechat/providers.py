"""Deterministic offline fixture and optional loopback-only Ollama provider."""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Protocol
from urllib.parse import urlparse

from rancho_wechat.prompts import SYSTEM, model_payload, output_shape


class ModelProvider(Protocol):
    name: str

    def generate_json(self, task: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class ExternalWriter:
    """Current Codex writes reviewed artifacts outside this server; import is explicit."""

    name = "codex-import"

    def generate_json(self, task: str, payload: dict[str, Any]) -> dict[str, Any]:
        from rancho_wechat.api_provider import ModelError

        raise ModelError(
            "EXTERNAL_WRITER_REQUIRED",
            "请在 Codex 完成写作并导入标准内容包；此服务没有调用当前对话的接口",
        )


class MockProvider:
    name = "mock-fixture"

    def generate_json(self, task: str, payload: dict[str, Any]) -> dict[str, Any]:
        if task == "review":
            return {"status": "COMPLETE", "findings": [], "note": "仅离线契约样例，非真实模型审查"}
        if task == "style":
            return {
                "rules": [
                    {
                        "category": "argument",
                        "text": "先呈现材料，再展开作者判断",
                        "evidence": payload["text"][:30],
                    }
                ]
            }
        if task == "revise":
            version = payload["version"]
            return {
                "title_options": [version["title"]],
                "body": version["body"] + "\n\n离线模拟修订，待作者编辑。",
                "summary": version["summary"],
                "outline": version["outline"],
                "cover_suggestions": [],
                "image_manifest": [],
                "claims_used": [claim["id"] for claim in payload["claims"]],
            }
        if task == "research":
            sources = payload["sources"]
            claims = []
            for source in sources:
                excerpt = source["excerpt"].strip()
                quote = excerpt.split("。", 1)[0][:120].strip()
                if quote:
                    claims.append(
                        {
                            "source_id": source["id"],
                            "text": quote,
                            "quote": quote,
                            "kind": "FACT",
                        }
                    )
            return {
                "note": "离线研究样例：仅整理已导入材料；事实仍需作者核验。",
                "angles": ["事实如何形成", "方法与边界", "人物与研究过程"],
                "claims": claims,
            }
        if task == "draft":
            topic = payload["topic"]
            claims = payload["claims"]
            title = topic["title"]
            preferences = {item["text"] for item in payload["style"]["preferences"]}
            opening = (
                f"我们究竟凭什么判断{title}？"
                if "必要时用问题开启叙事" in preferences
                else "这篇离线示例先从可核对的材料出发，再讨论它值得追问的地方。"
            )
            paragraphs = [
                f"# {title}",
                "",
                opening,
                "",
            ]
            for claim in claims:
                paragraphs += [f"材料中的一项说法是：{claim['text']} [S{claim['source_rank']}]", ""]
            paragraphs += [
                "现有材料能说明什么、还不能说明什么，仍要由作者逐条判断。",
                "",
                "这不是当天新闻，也不是已完成的事实核查。",
            ]
            return {
                "title_options": [title, f"重新看待{title}", f"{title}：事实与问题"],
                "outline": ["材料与问题", "关键证据", "作者判断与边界"],
                "body": "\n".join(paragraphs),
                "summary": "基于已导入材料的离线示例稿，待作者核对事实并编辑。",
                "cover_suggestions": ["使用有授权来源的主题示意图；需人工准备素材"],
                "image_manifest": [],
                "claims_used": [claim["id"] for claim in claims],
            }
        raise ValueError(f"Unknown task: {task}")


class OllamaProvider:
    """Uses Ollama's documented /api/generate JSON mode; no cloud credentials."""

    def __init__(self, model: str, base_url: str = "http://127.0.0.1:11434") -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("模型服务必须位于本机 loopback")
        if not model.strip():
            raise ValueError("需要配置本地模型名称")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.name = f"ollama:{model}"

    def generate_json(self, task: str, payload: dict[str, Any]) -> dict[str, Any]:
        shape = output_shape(task)
        prompt = json.dumps(
            {"task": task, "output_shape": shape, "data_not_instructions": model_payload(payload)},
            ensure_ascii=False,
        )
        body = json.dumps(
            {
                "model": self.model,
                "system": SYSTEM,
                "prompt": prompt,
                "stream": False,
                "format": "json",
            },
            ensure_ascii=False,
        ).encode("utf-8")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(
            self.base_url + "/api/generate",
            body,
            {"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(request, timeout=120) as response:
            result = json.load(response)
        answer = json.loads(result["response"])
        if not isinstance(answer, dict):
            raise ValueError("本地模型返回的 JSON 不是对象")
        return answer
