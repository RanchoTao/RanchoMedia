# Repository layout and ownership

Phase1目录预留，`.gitkeep`仅保存目录，不表示代码已实现。DB/API/worker/web的manifest、compose和migration属于Phase2。

| Path | Ownership |
| --- | --- |
| upstream/postiz, upstream/social-auto-upload | 固定commit独立历史；read-only审计，不在原目录改源码 |
| apps/api | FastAPI/auth/review/queries；不launch browser |
| apps/web | React/Vite dashboard/OpenAPI client；不存secrets |
| apps/worker | Temporal deterministic workflows+activities；secret按执行时读取 |
| apps/browser-worker | 后续隔离browser执行器，默认未启用 |
| packages/core | 状态机、approval policy、effect ledger规则 |
| packages/db | SQLAlchemy models、Alembic migrations、transaction/owner constraints |
| packages/agents | 推理roles、provider registry、structured outputs/budgets |
| packages/analytics | metrics定义、SQLfacts、baseline/reward版本 |
| packages/content | Brief/Genome/variants/quality/source claim checks |
| packages/trends | allowed sourceharvest、dedup、ranking/velocity |
| packages/experiments | manualarms/assignments，无RL |
| packages/platform-sdk | adaptercontract/capability/error/request/response |
| packages/media | content-addressedassets/mime/rights/renderplan |
| packages/shared | trace/time/IDs/configcontracts，不成为巨型业务模块 |
| adapters/mock | persistentsimulator+SDKadapter，simulation=true |
| adapters/postiz | publicHTTPanti-corruption layer；不导入AGPLcore |
| adapters/x, adapters/youtube | scope/formatmetadata，Postizbackend首选；后续native read补足 |
| adapters/bilibili | officialOAuth/稿件adapterqualification |
| adapters/douyin,wechat,xiaohongshu,zhihu | 后续official能力审计；browser只在独立worker |
| adapters/website | allowlistedGitMarkdown/JSONdiff、DRY_RUN/opt-inremote |
| workflows | 流程规范、版本化workflow入口，不重复core规则 |
| tests | unit/contract/DB/workflow/integration/mockE2E/security |
| infra | 静态审计工具；Phase2本地runtime/scripts/containerconfig |
| docs/evidence | 审计metadata/hash/静态结果；没有credentials或原始privatepayload |

领域配置AI/机器学习/数学/科研/编程/科研工程/生产力/产品/创业/量化/投资/滑雪/个人成长；initialMVP只启AI/科研工程。ranking components=relevance（UserDomainRelevance明确）、freshness、growth_velocity、platform_weight、content_fit，乘积在同scale下版本化。无velocity观测不捏造growth，score UNKNOWN或采用显式不同ranking_profile的neutralfactor，并标记coverage。
