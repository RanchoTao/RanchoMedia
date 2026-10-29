# Autonomy policy v0.1

默认 LEVEL 2；提升等级是显式版本化配置+human approval，不是 LLM 自我决定。当前 Phase 1 没有后台daemon、automation、真实发帖或自动回复。

| Level | Capability |
| --- | --- |
| 0 | analysis only，no drafts or external mutations |
| 1 | 自动draft/研究提案，no schedule/发布 |
| 2 | 自动产生排程proposal，**人工批准具体revision/目标/日期后才执行** |
| 3 | 仅经审核白名单类型、账户与预算自动发布，仍有风险门和kill switch |
| 4 | 大部分内容可在政策护栏内自动发布，高风险转human |
| 5 | 高自治但有审计、预算、权限与human override，绝非无限权限 |

状态级 gates：sources evidence → draft/variant质量 → FactCheck+semantic+rights+risk → READY_FOR_REVIEW → 具体审批 → scheduled → dispatch recheck。没有校验服务即 UNKNOWN/BLOCKED。AI confidence 只是模型估计，不能当事实证明或自动批准凭据。

Review 页面显示 content、references、trend来源、平台payload预览、risk flags、similarity scores+算法/阈值版本、fact claim证据、AI confidence+来源、expected audience。队列包含Drafts/Ready for Review/Approved/Scheduled/Published/Failed/Needs Attention。操作Approve/Reject/Edit/Regenerate/Schedule/Publish Now必须经服务API；编辑或regenerate撤销审批，Schedule不自动Approve。

初版评论所有类型只建议，**不自动发送**。以后：THANKS/QUESTION_SIMPLE → 有条件AUTO；QUESTION_TECHNICAL → confidence+证据决定AUTO/REVIEW；DISCUSSION/CRITICISM → REVIEW；SPAM/ABUSE →不自动回复/删除；PRIVACY/LEGAL/MEDICAL/BUSINESS → HUMAN；UNKNOWN → REVIEW。投资/财务建议列高风险human，即使不在用户的评论枚举中。classification、policy_version、decision/reason保存。

每天/账户/工作流有发布、reply、模型预算、timeout与错误率限额；rate limit用平台retry-after，超过限额停止排程/dispatch。全局和账号 kill switch 只停止新动作，已 IN_FLIGHT 核对结果。任何OUTCOME_UNKNOWN不得自动重复；安全挑战NEEDS_HUMAN不得用刷新/换代理/stealth继续尝试。

LEVEL 2审批绑定日期；想改日期或目标账户生成新approval。受委托Postiz/native schedule在取消时必须确认远端已取消，未确认保留UNKNOWN。browser handoff包含最小action与证据，不自动向他人发消息。本工程请求不授权平台真实发布、回复、Git remote push或远端新自动化；本轮只建立基础架构。
