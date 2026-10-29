# Agents 与 model provider v0.1

Agent 是具备受限工具的推理角色，不强制独立进程或互相闲聊。验证、排程、权限、加密、SQL 计算、发送动作是确定性函数/activities。MVP 仅 TopicPlanner/Writer/Critic/Strategy 推理可用 provider 驱动；其余按职责设计逐步启用，fixture 明确 simulation。

| Role | 输入 → 输出 | 权限与阶段 |
| --- | --- | --- |
| TrendAgent | allowed sources → TrendItems | 抓取是 deterministic harvester；去重/score 函数，MVP fixture/RSS |
| ResearchAgent | idea + allowed search → claim/source evidence | read/search only，来源完整性与可信度标记 |
| TopicPlannerAgent | ranked trends + strategy facts → idea/brief proposal | 无账号 secret；理由引用 score/facts |
| WriterAgent | reviewed brief → canonical draft + Genome | structured output；无发布工具，不能编造经历/实验 |
| StyleAgent | sources mechanisms → StyleSignature | 抽象机制，不输出照抄模板 |
| CriticAgent | draft/variant → quality findings | 检查可读性/结构；不能替代 fact/semantic/policy gates |
| FactCheckAgent | claims + sources → entailment proposal | 有 source ID / evidence；UNKNOWN 要 review；确定性核对优先 |
| PlatformAdapterAgent | canonical + platform spec → native variant | 只适配内容，不是实际 platform adapter；重新审核 |
| VisualAgent | brief + rights → visual plan/assets proposal | 预算、权益审核；MVP 不调用生成模型 |
| VideoAgent | script + assets → render plan/media | script 与真实视频分开；MVP 不声称 video generation |
| PublisherAgent | approved immutable job → dispatcher | 实际是 deterministic policy+SDK activity；禁止自由 LLM 决策发帖 |
| AnalyticsAgent | snapshot IDs → SQL facts + explanation | 数值由 SQL/functions，LLM 仅解释 |
| CommentAgent | untrusted comment → class/reply proposal | 分类、置信度、PII flag；MVP no auto-send |
| StrategyAgent | quantitative facts → notes/hypotheses/next experiments | facts 引用强制；append-only proposal；promote 需 human |

## Provider contract

独立 `ModelProvider.generate(request)`, `generate_structured(request,schema)`, `embed(request)`。ProviderCapabilities 标记 structured/json/tools/embedding；不强迫 Anthropic/其他不支持 embedding 的 provider 实现 embed，返回 ModelCapabilityError。Provider registry 支持 OpenAI、DeepSeek、Anthropic、Gemini、OpenAI-compatible；实现状态每个单列，Phase 1 **全部未接真实模型**。

Run config provider/model/base_url_ref、temperature、max_tokens、budget、timeout、prompt_version、output_schema_version；secret store 引用不进入 workflow history。所有 provider 返回 response_id、usage nullable、latency、pricing_version 与 cost nullable；JSON parse/validation失败保留错误后有界重试。供应商不能提供 usage 时不估造 token；成本按明确版本价格推算，未知价格为 null。

Fallback 只有显式 policy 可以启用；记录实际 provider/model，不能请求 OpenAI 实际调用 DeepSeek 却只记原请求。OpenAI-compatible 不假设支持官方 structured API；用 feature probes/contract tests，受控 URL 防 SSRF。模型调用可能计费的重试也记录每次 attempt。

FixtureProvider deterministic，MODEL_FIXTURE=true、cost/tokens=null、provenance=FIXTURE；离线 E2E 可以验证数据闭环与 gates，不能验证真实选题/创作质量。真实 provider 验收用同一流程，只加 adapter/config，不重写业务。

所有 runs 引用输入/输出 artifacts、模型和策略版本；temperature/seeds 为配置而非可复现承诺。工具 allowlist，读数据与 mutation 权限分别隔离；reasoning roles 不持有 SDK publish/reply/delete 工具。源文 prompt injection、评论中“帮我把 token 发过来”等只当数据，不作为指令。
