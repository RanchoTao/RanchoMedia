# Platform capability matrix — 2026-09-28

这是 capability discovery，不是 Rancho 支持列表。**当前 Rancho 所有 production adapters 均 NOT_IMPLEMENTED / disabled**。上游只说明 pinned code 存在。账号资格、scope、套餐、审批、地区与平台规则需接入时再核实。Official API First；unknown 不自动授权 browser fallback。

Phase 3 实测补充（2026-10-15）：Personal Website 的本地 Git publish/fetch/reconcile 为 `QUALIFIED_OFFLINE`，update/delete、PR/push 与网站部署验证仍未完成。Postiz 公开 API 仅模拟 HTTP 草稿契约验证，不能宣称任一社交平台发帖。所有真实外部发布继续关闭。原始运行报告不随公开源码分发；当前公开资格边界见 [Website qualification](../../docs/RELEASE_VALIDATION.md) 与 [Postiz qualification](../../docs/RELEASE_VALIDATION.md)。

符号：O=官方文档/官方源码确认接口存在；A=需申请/权限/账户类型，未获得；U=本轮未验证；N=该路径没有；S=Rancho/上游服务定时器，非平台原生排程；E=上游自己的发布通知；B=已发现 browser uploader；G=Git 机制。Metrics/Comments/Reply/Search/Trend 为**官方存在性**，不是 upstream 或 Rancho 已实现。

## 能力与权限

| Platform | Official API available? | Publishing | Scheduling | Metrics | Comments | Reply | Search | Trend discovery | Webhook | OAuth / auth | Browser fallback | Risk level |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| X | O/A | text/image/video O/A | S | O/A | O/A search-based | O/A | O/A | U/套餐待核 | U；Postiz E | OAuth1 当前 Postiz，官方 OAuth2 等需 scope | 不优先；U | 中：权限/成本/限流 |
| YouTube | O/A | video O/A；无通用社区图文发布验证 | O private publishAt / S | O/A | O/A | O/A | O/A | U，不承诺旧 trending 语义 | U；Postiz E | Google OAuth2 | B；不优先 | 中：审计/配额/处理状态 |
| TikTok | O/A | photo/video O/A | S | O/A limited display/data | U for commercial app | U | U，Research API 非默认商用 | U | U；Postiz E | OAuth2 scope/app audit | B；不优先 | 中高：未审计 private 限制 |
| Instagram | O/A professional | image/video O/A | S | O/A | O/A | O/A | U/受限能力需查 | U | U；Postiz E | Meta/Instagram OAuth | U；不优先 | 中：professional/app review |
| Threads | O/A | text/image/video O/A | S | O/A | U | O（provider reply 证据）/A | U | U | U；Postiz E | OAuth2 | U；不优先 | 中：scope/配额 |
| Reddit | O/A | text/image/video A | S | U，score 非完整 impressions | O/A | O/A | O/A | O listing/hot，非全网趋势 | U；Postiz E | OAuth2 | U；不优先 | 中高：API 批准/community rules |
| LinkedIn | O/A | text/image/video A | S | O/A member/page 分开 | O/A | O/A | U/不能视为全站搜索 | U | O/A org notifications；Postiz E | OAuth2、vetted product | U；不优先 | 中高：权限/API version |
| Medium | legacy O；新 integration N | legacy token only；可靠性 U | S only legacy | U | U | U | U | U | U；Postiz E | legacy token；新 token N | U；人工导入优先 | 高：API 不再支持 |
| WordPress | O self-hosted / .com | posts/media O | O future / S | .com stats O/A；core U | O/A | O/A | O site search | U | U/plugin；Postiz E | self-hosted application password；.com OAuth | 无需求 | 低中：实例权限/插件差异 |
| 小红书 | O account API；通用笔记发布 U | U，不能混同电商 API | B / S 候选 | U | U | U | U | U | U | 官方 account 授权 O；publisher U | B，隔离且默认关 | 高：DOM/资格/风控 |
| 知乎 | O data/search；官方 media uploader | media O；完整发文 U | U / S 计划 | U | U | U | O/A | U | U | access secret/user token；通用 OAuth U | U；先核实官方发布 | 中高：产品范围未核 |
| 抖音 | O/A SDK/H5/数据；server publish 权限 U | 用户参与 SDK/H5 O；无人值守 API U | B / S 候选 | O/A data | U | U | A/垂类受限 | U | U | OAuth/SDK authorization A | B，官方权限缺失后才评估 | 高：发布方式/风控 |
| Bilibili | O/A | video/article O/A | U native；biliup P；S 计划 | O/A authorized | U | U | U | U | O/A event categories U | account authorization O/A | biliup cookie 为非官方备选，非 browser 本体 | 中高：应用认证/授权 |
| 微信公众号 | U（官方页面本轮无法读取） | U / draft+publish 待专项核实 | U / S 计划 | U | U | U | U | U | U | app token/第三方授权具体权限 U | 无已审计 upstream | 中高：账号类型/发布资格 |
| 视频号 | 通用创作者 API U；不混同小店 | video B | B / S 候选 | U | U | U | U | U | U | 当前 browser QR/cookie；官方 U | B | 高：实名验证/DOM |
| 微博 | U（官方 wiki 访问失败） | video B；official U | S 计划，upstream N | U | U | U | U | U | U | 官方 OAuth 文档待读取；browser cookie | B | 高：权限/旧接口状态 |
| 快手 | O portal；具体 creator access U | 创建视频 portal O/A；browser video/note B | B / S 候选 | U | U | U | U | U | U | 官方授权具体 U；browser QR/cookie | B | 高：资格/DOM |
| 百家号 | U 官方创作者 publishing 文档未获取 | video B；official U | S 计划，upstream N | U | U | U | U | U | U | 当前 cookie；官方 U | B | 高：文档/账号/DOM |
| Personal Website | G（所有者 Git 仓库） | Markdown/MDX/JSON 计划 | S 计划 | N；需 analytics source 另接 | N；需 comment provider | N | N；可后加站内索引 | N | Git/deploy U；非发布确认 | Git SSH/PAT secret ref；非 OAuth 必需 | 不适用 | 低 DRY_RUN；remote write 中 |

未把无法访问的官方页面转换成“不存在 API”。没有一般性声称微信公众号/微博/小红书/知乎没有官方发布能力。某些平台的垂类 API、商家 API、分享 SDK、Research API 不等于本项目可获权的服务器发帖接口。

## 当前上游与首选接入

| Platform | Current implementation in upstream | Preferred Rancho implementation / qualification |
| --- | --- | --- |
| X | Postiz API provider，text/media、schedule、metrics、thread/comment | Phase 4 Postiz HTTP；生产开关依赖幂等/secret/scopes qualification |
| YouTube | Postiz resumable API/metrics；SAU Studio browser | Phase 4 Postiz；comments/reply 后续官方 native；video 原生变体需审核素材 |
| TikTok | Postiz photo/video；SAU legacy browser video | Postiz 官方；不先用 browser |
| Instagram | Postiz FB-linked + standalone professional provider | Postiz 官方；按账号类型拆 capability |
| Threads | Postiz API + thread replies + metrics | Postiz 官方；inbox 单独开发 |
| Reddit | Postiz API publish/comment；没有通用 analytics | Postiz publish；official read metrics 定义另查，遵守 community rules |
| LinkedIn | Postiz member 与 page provider；page analytics | Postiz；member/page capabilities 不合并 |
| Medium | Postiz legacy token provider | 默认禁用，人工 export/import；有合法旧 token 再验证 |
| WordPress | Postiz application-password posts/media | Postiz 或独立 REST；区分 .com / self-hosted |
| 小红书 | SAU 新 browser note/video；旧签名 client 另有路径 | 先 official account/publishing 权限核实；缺失时 clean browser worker；旧签名路径禁用 |
| 知乎 | 两 upstream 无实现；发现官方 data 与媒体工具 | 官方 data/search 起步，专项核实完整 publishing；不能默认 browser |
| 抖音 | SAU browser video/note/date | Phase 后续官方授权优先；fallback 需要独立 worker qualification |
| Bilibili | SAU CLI + 动态下载 biliup；非 official API | Phase 5 独立官方 adapter；若授权不可获，先人工流程，cookie fallback 专项安全/许可审核 |
| 微信公众号 | 两 upstream 无实现 | Phase 后续专项官方 draft/publish/data 审计后实现 |
| 视频号 | SAU browser video/date；note class 未完成 | 后续隔离 browser，遇实名/风控 NEEDS_HUMAN；no auto retry mutation |
| 微博 | SAU browser video | 官方文档/权限资格先查；browser 后备 |
| 快手 | SAU browser video/note/date；video 有 bounded retry | 官方 creator API 先查；browser 两种格式分别资格测试 |
| 百家号 | SAU browser video，无已实现定时 | 官方 creator API 先查；browser 后备 |
| Personal Website | 两 upstream 不提供 Git publisher | Phase 5 自建 Git adapter，默认 DRY_RUN；新 branch commit，可选 PR，remote push 明确 opt-in |

## 资料与核验限制

2026-09-28 查看官方资料；若只有搜索索引摘要或 JS shell，明确标记，不当成 endpoint 可调用证据。

- [YouTube API reference](https://developers.google.com/youtube/v3/docs)、[videos/publishAt](https://developers.google.com/youtube/v3/docs/videos)：video upload、comment/reply/search、private publishAt；账号资格未验证。
- [TikTok Direct Post](https://developers.tiktok.com/docs/en/content-posting-api-get-started)：photo/video scopes 与 app audit；未经 audit 的可见性受限。
- [LinkedIn Community Management](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/community-management-overview?view=li-lms-2026-01)：vetted tiers、member/page analytics、org notifications。API 版本随时间变更，adapter 必须 pin 并监测 sunset。
- [Reddit API](https://www.reddit.com/dev/api/)：submit/comment/search/listing 文档；不代表 commercial access 已获准。
- [X 官方 API 工具](https://developer.x.com/apitools/api?endpoint=%2F2%2Ftweets&method=get)：接口辅助；写入/media 另依据 pinned Postiz 官方 endpoint 调用源码；套餐与 trend 未核实。
- [Meta Instagram](https://developers.facebook.com/docs/instagram-platform/content-publishing/)、[Threads](https://developers.facebook.com/docs/threads/)：本轮页面抓取失败，发布/metrics/reply 依据 pinned provider 官方 endpoint/scopes 源码；未知项保持 U。
- [Medium 停止 API integrations](https://help.medium.com/hc/en-us/articles/213480228-API-Importing)、[归档 API](https://github.com/Medium/medium-api-docs)：legacy 不是新建连接承诺。
- [WordPress posts](https://developer.wordpress.org/rest-api/reference/posts/)、[auth](https://developer.wordpress.org/rest-api/using-the-rest-api/authentication/)、[.com API](https://developer.wordpress.com/docs/api/getting-started/)：REST posts/future、application passwords、.com stats 不合并为所有实例都有 analytics。
- [Bilibili 文档](https://open.bilibili.com/doc)：浏览结果为搜索索引摘要，列出账号授权、稿件发布/删除/查询、授权数据、webhooks、身份审核；具体 scope/endpoint 与获权待登录后专项核验。本轮未登录。
- [抖音官方发布 SDK/H5](https://partner.open-douyin.com/docs/resource/zh-CN/dop/ability/opensdk/content-management/share-and-publish-to-douyin)、[API list](https://partner.open-douyin.com/docs/resource/zh-CN/dop/develop/openapi/list)：用户参与发布与限制搜索；不推断无人值守写接口。
- [小红书官方 account quick start](https://openaccount.xiaohongshu.com/docs/quick-start)、[电商平台](https://open.xiaohongshu.com/)：授权存在；创作者笔记写入仍 U。
- [知乎数据平台](https://developer.zhihu.com/)（搜索索引证据，直接读取超时）、[官方媒体云 uploader](https://github.com/zhihu/zhihu-mediacloud-uploader)：data/search 与 media_key 存在，完整 publishing qualification 待办；未安装工具。
- [快手 creator portal](https://open.kuaishou.com/platform/openApi?menu=2)（JS shell/index 摘要）；小程序短剧上传不是通用 creator 发布证明。
- [微信官方发布页面](https://developers.weixin.qq.com/doc/offiaccount/Publish/Publish.html)、[微博官方 reply](https://open.weibo.com/wiki/2/comments/reply)：本轮访问失败。百家号未找到可读官方 creator API 文档；第三方 SDK 不作为权威验证。

下一次接入需记录：文档版本/日期、app/账号 scopes、授权资格、格式/配额、真实 publication ID 与可见性、失败分类、幂等资格、观众数据语义、secret 存储、撤权行为。通过前 production capability.enabled=false。
