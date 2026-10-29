# 架构决定与接入协议

## ADR-001：独立仓库、独立数据边界

2026-10-21 检查：`RanchoMediaOS` 位于相邻目录，HEAD 为 `51c6d9fd18da1b146669002ae6b951c2da8b16b8`，工作树干净、无已配置远端。本模块作为 sibling `RanchoMedia-WeChatPlatform/` 建立独立 Git 仓库；不修改 OS、不把微信文章逻辑放进通用核心。这保留 OS Phase 3 的离线资格验证记录。

逻辑链路：未来 VD/Life OS 的人工入口 → 未来 RanchoMedia 总控 → RanchoMediaOS 通用审计、状态、幂等、Adapter 资格 → 本模块公众号编辑流程。当前 **尚未部署任何跨仓库运行连接**。本模块先在本地 SQLite 自洽运行，下面是给未来总控/OS 的拟定稳定边界，不能称作已集成。

## v1 内部组件

- `EditorialDesk`：状态变更、事实门禁、审批与幂等业务边界。
- `Store`：SQLite 事务；`topics/sources/claims/research/articles/versions/style_samples/style_signals/tasks/daily_recommendations/audit/exports` 表。
- `ModelProvider.generate_json(task, payload)`：`research` 和 `draft` 结构化结果；Mock 与本机 Ollama 实现。
- `feeds` / `papers`：显式配置 RSS/Atom 和手工 arXiv 元数据入口；都不授予事实认证。
- `scheduler`：Asia/Shanghai 本地触发；`daily:YYYY-MM-DD` 唯一任务键、事务、5 分钟 lease 和失败后恢复。准备稿件依赖文章 `(topic_id, angle)` 唯一键及来源/事实状态复核。
- `web` / `cli`：作者操作入口。Web API 使用本地令牌；无发布接口。

## 与 RanchoMediaOS 的建议协议（未接线）

上游以 `topic_id`、`article_id`、`version_id`（UUID 字符串）引用微信对象。跨系统请求携带稳定的 `task_key` 和人工主体；回复携带状态、输出引用、审计时间、来源状态。OS 可承载通用任务调度、审批记录和 adapter qualification；本模块保留微信文章正文、图片素材位置、风格偏好和导出格式。文章只能在本模块内的作者明确 `APPROVE` 且事实核对确认后变成 `APPROVED`，然后才生成本地 Markdown。总控不能把候选稿状态推断为可发布。未来若加入官方草稿箱 API，须单独验证账号权限、独立资格注册、显式授权和未知结果核对；v1 不提供该实现。

最小事件草案：`topic.proposed/accepted/rejected`、`research.ready/needs_confirmation`、`article.drafted/edited/revision_requested/rejected/approved`、`export.markdown_created`。事件载荷只传 ID、版本、状态、审计号及摘要哈希；正文和凭据留在平台模块。该草案当前没有外部消费者。

## 门禁与限制

状态：`PROPOSED → ACCEPTED/REJECTED`；已接受选题获得研究记录后才可生成 `IN_REVIEW` 稿。作者编辑新增不可变版本；`APPROVE` 需要研究非陈旧、事实报告无 blocker、作者 `fact_attested`。新增来源会使旧研究变为 `STALE`，撤销既有批准。`REVISE` 留在待改，`REJECT` 终止该稿。导出只接收 `APPROVED` 指向的版本。

原始来源摘录的匹配是**本地可追溯性检查**，不验证网页当下内容、版权许可或模型推理正确性。作者核查确认是必要步骤。新闻超过七天或无带时区日期时阻止“当天新闻”路径；常青题中的当天新闻措辞被标记。RSS 由用户配置，默认不抓取；arXiv 是手工触发。模型输入不可信材料以数据字段隔离，疑似指令性事实会被拒绝，但不能宣称提示词注入已被完全消除。

PostgreSQL/Temporal/Docker 在 RanchoMediaOS Phase 3 仍缺运行验收，本模块不依赖它们完成本地闭环。未来迁移需补充 SQL 迁移、真实 OS 集成测试、幂等事件收件箱及备份/恢复演练。

## ADR-002：v1.1 的真实写作入口

默认入口改为 `codex-import`：当前 Codex 会话写作，服务器导入经过 Schema 校验的内容包。包内包括来源、逐字摘录、研究、正文、自查与执行者声明，`content_imports` 用包 ID 和内容摘要去重；导入状态固定为待审。执行者声明不构成外部模型身份认证，同会话自查也不构成独立科学认证。

`AuthorStyleEngine` 增加作品版本关系、段落级差异、明确偏好和候选规则。真实 Provider 提示词只加载 CONFIRMED 规则；旧 Mock 夹具保留既有重复模式行为，不能据此声称真实作者风格已经学会。AI_REVISION 与 AUTHOR_EDIT 分开，AI 修订不进入作者偏好证据。

兼容 API 包装器保留为可选后端，研究、写作、审查分角色配置；SQLite 账本在发送前预留预算，结果缓存避免重复消费，未知结果需核对。新表均为 additive CREATE TABLE，原 v1 内容与审计记录不被迁移重写。Windows 增加 tzdata 以支持 Asia/Shanghai。

`codex-once` 是显式单次 CLI 工具，有每日次数、时长和结果摘要检查，不是默认调度后端；SDK/App Server 仅完成可行性核验。导出增加批准包与待审包的明确区别、素材摘要和 MANUAL_ACCEPTANCE_REQUIRED 标记，未增加公众号外部副作用。
