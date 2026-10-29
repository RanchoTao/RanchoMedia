# Postiz 技术审计

审计日期：2026-09-28。提交 `528f6ea37c8cf1156ad4a12de4e7aed853c019f2`，完整克隆，HEAD 可达 3120 个提交，非 shallow。最后提交 2026-09-28 08:55:35 UTC。近期涉及媒体上传、provider、工作流重试；说明仍有维护，不能证明账号权限或线上可靠性。

证据以固定提交源码为准。本文所有源码路径相对 `upstream/postiz/`；可在 [固定版本](https://github.com/gitroomhq/postiz-app/tree/528f6ea37c8cf1156ad4a12de4e7aed853c019f2) 查看。**静态支持 ≠ 实际账号验证**。所有平台 LIVE_TEST=NOT_RUN。

## 架构与依赖

| 领域 | 源码事实 | Rancho 含义 |
| --- | --- | --- |
| 语言 / monorepo | TypeScript；pnpm workspaces；`packageManager=pnpm@10.6.1` | 不把整个依赖树带入 intelligence core |
| 后端 | NestJS 11，`apps/backend`；共享 `libraries/nestjs-libraries` | 包装公开 `/public/v1`，不依赖内部 controller |
| 前端 | Next 16.3.1 / React 19.2.4，`apps/frontend` | 自建 review UI；不复制 AGPL UI |
| DB | Prisma 6.5.0 / PostgreSQL，`database/prisma/schema.prisma` | Postiz DB 与 Rancho DB 分离；不共享表或迁移 |
| workflow / cache | Temporal SDK ^1.14.0；orchestrator；ioredis/Redis、throttler | 使用相同引擎理念，独立 namespace / task queue |
| provider | `integration.manager.ts` 显式 registry；SocialAbstract + SocialProvider | 每个平台 capability 要按方法核实 |
| 身份认证 | app auth providers、组织 API key、OAuth2 客户授权；平台单独 OAuth | Rancho 组织/审批权限不能依赖平台 OAuth 代替 |
| 部署 | Docker compose / 容器构建、开发服务；Postgres、Redis、Temporal；开发 compose 含 ES | 不原样复制开发默认口令/开放端口/整套服务 |
| Agent / MCP | Mastra、LangGraph、`chat/start.mcp.ts`，按组织鉴权的 tools | 有可发布 tools，不允许 Rancho LLM 绕过 review 直接调用 |
| CLI / SDK | `apps/sdk` 发布脚本；README 的 CLI/agent 链接属另行分发工具 | MCP 源码已核实；外部 CLI 不算本仓库已构建或 license 已审计 |

Node engines 要求 `>=22.12.0 <23.0.0`，但 volta 字段是 20.17.0；CI `build.yml` 用 22.12.0。本机 PATH Node=25.2.1，bundled=24.19.0，均不匹配。**BUILD=NOT_RUN / 环境不满足**；未安装依赖、未跑迁移、未拉镜像。没有把这种环境差异报告为 upstream 构建失败。根脚本 `prisma-db-push --accept-data-loss` / reset 不用于 Rancho。Postiz 内另有未初始化的 `libraries/plugins/src/list/public-api` SSH 子模块，独立源码及 license 不在本轮审计覆盖内。

## 发布、排程、错误与上传

- `apps/backend/src/public-api/routes/v1/public.integrations.controller.ts:59`：公开 API；posts 列表/创建、upload / upload-from-url、integrations/settings、analytics、debug、工具调用。API key 或 OAuth 是 Postiz 服务授权，不等于外部平台授权。
- `posts.service.ts:719–766` 启动 `postWorkflowV112`，稳定 workflowId `post_{postId}`，冲突策略 TERMINATE_EXISTING；日期等待在 workflow durable timer，provider 执行即时发布。重复生成新 Postiz post ID 仍可重复发帖。
- `post.workflow.v1.1.2.ts:23–107` 区分只读和不可逆 activity：publish/comment 最大自动尝试为 1，读取最多 3 次；15 秒 heartbeat、3 分钟 heartbeat timeout、30 分钟 mutation budget；pending/check/finalize 各自处理。
- 同文件 `:244–416`、`:427–594` 显式 unknown / unconfirmed，避免发布成功后 DB 或通知失败再发布。**不能把无 heartbeat 当作绝对未发生外部动作**；若 worker 在第一次 heartbeat 前执行副作用并崩溃，仍需结果核对。Rancho 采用更保守 ledger/reconciliation。
- `social.abstract.ts:343–547` 内部 fetch 仍有错误分类和请求重试。顶层 activity attempts=1 并不自动证明 HTTP mutation exactly-once，需 endpoint 级验证。
- X 支持媒体处理与发帖确认；YouTube `postPending:553` 建立 resumable session，后续探测上传状态，finalize 上传/缩略图。上传结果、处理完成、公开可见必须分别存状态。
- URL 上传与 outbound 需检查 SSRF、大小、mime、超时及域名。不要把任意用户 URL 或模型给出的 URL 直接传入。

## 逐平台能力（源码边界）

`Y`=具体实现，`R`=Rancho 可借助 Postiz durable scheduler，`P`=部分/仅特定途径，`L`=Postiz 本地记录，`N`=未发现通用实现。`webhook=E` 是 Postiz 发布事件，不是平台观众事件。`reply_comment=P` 是 provider 的发布后评论/线程方法，不代表有观众评论 inbox 或公共任意回复 API。图像/视频不包括嵌入外链；text 不包括媒体标题。

| Platform | publish_text | publish_image | publish_video | schedule | fetch_post | fetch_metrics | fetch_comments | reply_comment | search | trending | delete | edit | webhook |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| X | Y | Y | Y | R | L/P | Y | N | P | N | N | L | L | E |
| YouTube | N | N | Y | R | L/P | Y | N | N | N | N | L | L | E |
| TikTok | N | Y | Y | R | L/P | Y | N | N | N | N | L | L | E |
| Instagram | N | Y | Y | R | L/P | Y | N | P | N | N | L | L | E |
| Threads | Y | Y | Y | R | L/P | Y | N | P | N | N | L | L | E |
| Reddit | Y | Y | Y | R | L | N | N | P | N | N | L | L | E |
| LinkedIn member | Y | Y | Y | R | L | N | N | P | N | N | L | L | E |
| LinkedIn page | Y | Y | Y | R | L/P | Y | N | P | N | N | L | L | E |
| WordPress | Y | P | N | R | L | N | N | N | N | N | L | L | E |
| Medium | Y | P | N | R | L | N | N | N | N | N | L | L | E |

WordPress image 是 featured_media；Medium image 是文章 Markdown / 外部资源，未发现独立原生图片上传；二者不是独立 image post。YouTube 的缩略图不是图片发帖。TikTok 图片/视频分别有不同权限与 settings。Reddit / LinkedIn media 发布受账号与 community/app 权限约束。

方法证据：`libraries/nestjs-libraries/src/integrations/social/` 下各 provider：X `postPending:801, comment:1380, postAnalytics:1571`；YouTube `postPending:553, postAnalytics:971`；TikTok `postPending:824, missing:1096, postAnalytics:1130`；Instagram `postPending:696, comment:1065, postAnalytics:1256`；Threads `postPending:444, comment:684, postAnalytics:824`；Reddit `postPending:320, comment:640`；LinkedIn `postPending:835, comment:1103`；LinkedIn page `analytics:320, postAnalytics:445`；WordPress `post:279`；Medium `post:102`。统一接口 [social.integrations.interface.ts](https://github.com/gitroomhq/postiz-app/blob/528f6ea37c8cf1156ad4a12de4e7aed853c019f2/libraries/nestjs-libraries/src/integrations/social/social.integrations.interface.ts) 没有通用 delete/update/fetch_comments/search/trends。

## 容易误读的数据与安全边界

1. `schema.prisma:426` 的 Comments 含 userId/anchor/resolved，是团队协作批注；不能当观众评论数据。`Post:456` 是排程/发布记录，没有 Rancho Genome、approval revision、metric observation ledger。
2. `posts.service.ts:677` 的 deletePost 软删除/终止 workflow；公开 DELETE 不能宣称删除已发平台帖子。公开 settings/status 更新也不等于平台编辑。
3. `Integration:367` 有 token/refreshToken 字符串；`integration.repository.ts:310–385` 直接写参数。查到 OAuth 客户凭证另有 fixedEncryption，不足以证明 platform tokens 也加密。真实自托管连接前要求验证存储加密，若不满足采用经审计独立 fork patch / 其他适合的服务，不在当前 upstream 修改。
4. `post.activity.ts:484–539` sendWebhooks 是发布后 best-effort，当前 headers 无签名、异常吞掉、未保证投递成功。不能把它作为权威或唯一发布确认；用私网 inbox + 去重 + 主动查询。webhook callback 本身不许触发未经审核的新发布。
5. X postAnalytics 返回 impressions/likes/retweets/replies/quotes/bookmarks；YouTube 单视频返回 views/likes/comments/favorites，频道 analytics 另有 watch duration、subscriber 等。聚合范围不可混用。
6. 多个 analytics catch 返回 `[]`；X 可被 DISABLE_X_ANALYTICS 禁用。Rancho 映射为 UNKNOWN/DISABLED/ERROR，不填 0。YouTube Favorites 字段不能被当成通用 saves；具体指标语义另查官方定义。
7. Prisma 的 Trending/TrendingLog 来自仓库其他历史功能，存在表名不证明社媒跨平台趋势抓取。Mastra 大量表不等于内容表现策略闭环已提供。

## 复用决策与缺口

| 模块 | 分类 | 条件 |
| --- | --- | --- |
| X / YouTube / 其他官方 provider | WRAP | Public API contract、账号 app scopes、发布和结果核对、credential 加密、幂等资格测试通过才启用 |
| 独立原版 Postiz 部署 | DIRECT_REUSE 候选 | AGPL notices/source 义务、存储安全与精确镜像版本审计 |
| Temporal / OAuth / media 设计 | REFERENCE_ONLY | Rancho 独立编写，不复制 Postiz 源码 |
| Postiz analytics | WRAP | 字段映射与空结果显式状态；Rancho 保存观察时间序列 |
| Postiz internal auth/DB/Agent | DO_NOT_USE 于 Rancho core | 不共享 DB、platform token 或 LLM 直接发布权限 |
| provider 源码 port | DO_NOT_USE 当前方案 | 保持许可边界；如将来做派生作品须单独 license 决策 |

没有 credentials 不阻塞 mock 系统；**Postiz 本轮未构建、未运行、未证明可靠连接 X/YouTube**。Medium 官方已停止支持 API 和发新 integration token，保留 provider 代码不意味着新账号可用：[官方说明](https://help.medium.com/hc/en-us/articles/213480228-API-Importing)。公开 API 信息见 [Postiz API](https://docs.postiz.com/public-api/introduction)，实际行为以当前 pinned source 优先。
