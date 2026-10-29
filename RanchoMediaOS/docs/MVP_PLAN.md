# MVP implementation and acceptance plan v0.1

本轮完成Phase 0/1；本文件是可实施边界，**没有 Mock E2E 已通过的声称**。第一版目标是一个持久化、经人审、可恢复、可量化学习的闭环，platform数量不作为验收。

## 最小运行规模

一个owner、一个mock account、一个可配置领域（初始AI/科研工程）、一个fixture source、一个topic/brief/canonical/variant/Genome、一次明确review、一个发布job、模拟metric观察、SQL analytics与versioned strategy proposal，产出下一idea。

最小real-LLM升级替换FixtureProvider，不改变流程；同样记录model calls/预算和结构验证。没key继续fixture E2E；报告分别为 OFFLINE_LOOP_PASS 与 REAL_MODEL_NOT_RUN，不把fixture声称为AI创作质量验证。

## Phase 2 落地顺序

| Increment | Deliverables | Done gate |
| --- | --- | --- |
| 2A | pinned Python3.12、web toolchain；DB migrations/owner roles；core models/Genome/SDK errors；fixture source/model | schema constraints+跨owner负例、缺失capability显式错误 |
| 2B | FastAPI+auth；review create/edit/approve/reject/regenerate；OpenAPI client；Review/Drafts/Accounts/Logs薄界面 | edit使旧approval失效、Publish Now未批准拒绝、mock visible |
| 2C | Temporal dev namespace+worker；outbox+effect ledger；MockPlatformAdapter持久模拟server | workflow可resume、same key不同payload conflict、崩溃窗口核对 |
| 2D | Mock metrics/comments/reply能力、observer offsets、SQL outcome/strategy proposal | missing非0、snapshot去重、comments仅suggested、no real side effects |
| 2E | Overview/Calendar/Publications/Analytics/Trends/Experiments/Strategy导航与真实DB视图 | 不用hardcoded成功数字/图表，空态和错误态可见 |

browser-worker不在offline MVP中启动；预留契约与独立目录。docker-compose.yml在2A创建并验证，只为Rancho Postgres/Temporal/API/worker/web，不先复用Postiz whole compose。生产服务没有现成image时不放占位image冒称可运行。

## Workflow contracts

| Workflow | Activities / durable behavior |
| --- | --- |
| TrendHarvestWorkflow | allowed source fetch → scrub/dedup/observations → rank；只读重试3次指数backoff，失败留source health |
| CreateContentWorkflow | topic/research/brief/draft/variant → checks → review；approval通过API事务+outbox signal；decision state在DB权威，signal丢失可read reconcile |
| PublishContentWorkflow | stable job workflow_id → due timer → approval/capability再验 → effect ledger → adapter → confirmation → publication；mutation默认attempts1；unknown只读核对/人工 |
| MetricsObservationWorkflow | from actual published_at七个绝对offset → read → snapshot+outcome；只读可retry，目标unique，成功写事务 |
| CommentMonitoringWorkflow | bounded polling/pagination → dedup/classify → reply proposals；初期不调用reply side effect |
| StrategyUpdateWorkflow | SQL事实快照 → schema验证 → LLM notes/next experiment → append proposed strategy；需human promote |

初始工程timeout建议：read/API 30秒、model 90秒、mock mutation 30秒、将来media mutation按大小有上限15分钟；具体平台限制qualify后版本化。retry safe reads最多3次，rate limited按retry-after并服从预算；任何credential/challenge/capability/permanent不重试。Temporal replay测试不调用网络，workflow时钟只用SDK timers。

## Phase 3验收

1. 新空DB迁移+fixture seed运行，UI选题可追溯Trend/source → Brief → Draft，Genome/model provenance完整。
2. 在Web review人工批准确切revision与mock account/time；API审计保存human actor，worker不能自己approve。测试里的approval是明确fixture test action，仅证明机制。
3. due time发Mock，返回稳定external ID，published status与模拟时间标记；重启API/worker/simulator后数据仍在。
4. 同workflow/key连续重试5次，mock server持久记录**只有1次 external publication**；包含“external已成功但本地DB没写完成”故障注入、并发dispatcher和过期lease恢复。不能只mock函数call_count。
5. Temporal time-skip覆盖七个offset，分别保存target/observed/window/simulation；真实时钟采集测试至少一次，不宣称实际等过30天。
6. SQL算raw/derived/coverage；无historical baseline时z/percentile/reward诚实null，mock cohort测试baseline有效路径。LLM无法写metrics表。
7. Strategy facts引用snapshot/outcome，notes/hypothesis可追溯，下一idea使用版本；数据不足推荐采集/实验，no fabricated winner。
8. 评论mock可采集/去重/分类/reply draft，确认外部reply count为0；独立adapter contract可以测试reply幂等，但不能代表LEVEL2自动发送已启用。

## Test suites

| Suite | Necessary evidence |
| --- | --- |
| unit | rank components/freshness、missing指标/零分母/负差、similarity BLOCK/UNKNOWN、approval invalidation、reward版本 |
| adapter contracts | Mock publish/metrics/comments/reply、每个不支持operation CapabilityError、pagination/permission/rate/unknown semantics |
| DB | FK/owner isolation/unique keys、immutableGenome、approval+outbox事务、snapshot dedup、restart persistence |
| workflow | Temporal replay/time-skip/cancellation/worker crash；effect UNKNOWN不得重发 |
| integration | API review→outbox→Temporal→persist mock→observation→SQL→strategy；realDB，no仅in-memory替代 |
| mock E2E | browser UI人工路径与API状态一致；same publish retry5only1；全部simulation可见 |
| security | secret log/history negative、source prompt injection、SSRF、Git路径逃逸、未授权/跨owner发布拒绝 |

Phase 4仅通过qualification才启用Postiz X/YouTube；私有凭证配置不在聊天粘贴，先sandbox/private visibility，用实际调用与public/private可见性证据。没有remote安全幂等保障时UNKNOWN必须停，不能宣传无条件exactly-once。

Phase 5 Bilibili优先官方授权；Website先本地Git DRY_RUN，测试临时repo/no push，随后opt-in branch/commit/PR。Phase 6真实analytics定义与时间窗验证；Phase 7单个真实allowed趋势源+strategy建议、人审promote。未来十几平台只加qualified adapters。

## 当前 blockers 与边界

Docker CLI存在但Linux engine未运行；Postiz Node22不匹配；SAU原生unit缺conf/requests；真实账号/app scopes、Postiztoken加密、公开API幂等与部分国内官方端点资格未核实。它们阻塞相应live qualification，**不阻塞Phase2自有mock开发**。真实LLM key缺失只阻塞real-model smoke。Phase1没有服务、数据库迁移、Temporal、Web或mock发布结果。
