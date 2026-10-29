# Roadmap

| Phase | Status at 2026-09-28 | Scope / exit |
| --- | --- | --- |
| 0 | COMPLETE 静态审计；build/live资格未通过 | full-history clones、pinnedSHAs、实际license、architecture/module/capability矩阵及复用边界 |
| 1 | COMPLETE 文档设计基线 | architecture、DB relationships/constraints、Agent/provider、Genome、analytics/security/autonomy、最小闭环验收 |
| 2 | NOT_STARTED | own DB/API/Web/Worker/Temporal/SDK/Mock、依赖lock、schema与安全tests |
| 3 | NOT_STARTED | 人审mock闭环，持久化重启/5次重试1次发布、metric/SQL/next recommendation |
| 4 | NOT_STARTED / live qualification gates | Postiz X/YouTube，account权限/token安全/unknown核对 |
| 5 | NOT_STARTED | Bilibili official资格+adapter、Website DRY_RUN/local branch/opt-in PR |
| 6 | NOT_STARTED | 真实指标snapshots/semantic字段/归因/normalization |
| 7 | NOT_STARTED | RSS/HN单源trend与quantitative+LLM策略建议闭环 |

后续平台波次：微信公众号/抖音 → 小红书/知乎/视频号，其余按资格与需求。browser-worker只为确有能力或权限缺口的功能开启；每种格式单独qualify。bandits在可靠outcomes/reward后，暂不上RL。

每阶段记录实际测试、失败、未运行与blockers；architecture変更新ADR；版本/平台规则变化触发重新审计。长期运行/自动监测在核心闭环验收后配置，不以本轮文档代替已运行后台系统。
