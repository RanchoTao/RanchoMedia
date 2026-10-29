# 逐动作能力与验收边界

核验日期：2026-10-29。来源为现有代码与本机真实回执的脱敏复核；私有回执、授权、Cookie、稿件和模型调用记录不随源码发布。不会为发布代码重新发布社交内容。

| 平台 | 已实现代码 / 实际复用 | 实测范围 | 尚缺内容 |
|---|---|---|---|
| 微信公众号 | `wechat_channel.py` 的 add/get/batchget、素材包装；Desktop DPAPI/单次确认；固定 wechatpy | token-only 单次真实请求成功并加密保存；此前 draft/batchget 只读成功。新 token 后必须重新检查权限 | 没有远端草稿 ID；素材上传/draft.add/get 写入闭环未验收；公开发表和群发关闭 |
| 小红书 | `channel_variant` 图文适配、工具资格状态；外部 xiaohongshu-mcp 候选 | 官方创作者 Web UI 一次受监督图文发布；独立笔记详情和空评论区 | MCP/API 未认证；没有已启用的 Director 工具发布器；无人值守、入站样本、回复未验 |
| 抖音 | 视频场景适配、既有渲染脚本、SAU 工具资格状态 | 官方创作者 Web UI 一次受监督视频发布；独立视频页和空评论区 | SAU/API 未认证；Director 工具写入关闭；无人值守、入站样本、回复未验 |
| 网站 | OS 本地 Git WebsiteAdapter；单稿 GitHub Pages canary 与只读核对 | 真实本地 Git、副作用恢复；一次授权 push、部署与公开正文核验 | 网站 canary 限定原作者仓库/单稿，不是任意站点通用生产部署器 |
| X | 线程变体、Postiz CLI/HTTP 集成候选 | CLI、Mock HTTP 契约 | Postiz 服务和 X OAuth 未认证；无真实发帖/评论/回复 |
| 知乎 | 文章格式、官方 ZhihuSync validator 候选 | 本地 JSON validator | 定向账号权限/远端预览发布未验；完整许可文本未核定，不 vendor |
| Bilibili | 视频变体、SAU help、官方开放平台研究 | 本地 CLI 入口 | 账号/上传/视频 ID/公开状态/评论未验；不将 Cookie 上传称为官方 API |
| 微信视频号 | SAU 上游能力研究 | 没有 Rancho 账号执行回执 | 未实现已认证的独立 Rancho Adapter |
| YouTube | OS 预留目录、Postiz 上游契约研究 | 没有 Rancho 账号执行回执 | 未实现已认证的独立 Rancho Adapter |

已公开 canary 的引用：

- [网站文章](https://ranchotao.com/content/rancho/7a1ebcdf-86de-581c-9d07-35fb26e8b849/)；发布部署提交 `5ab32f4a0d5f9af903d60a3aff01079c3f4f5052`。
- [小红书 canonical ID](https://www.xiaohongshu.com/explore/6ae24a98000000002f02a77f)；核验发生在授权登录态，匿名可见性未验，URL 不包含分享/安全参数。
- [抖音视频](https://www.douyin.com/video/7701807186179017993)；同样不把登录态详情等同于所有访问者可见。

这些是历史单次实测事实，不是当前账号授权文件或持续运行保证。源码不附带使用这些账号/内容的权限。新操作者必须自行授权，不能复用原账号身份。

本地模拟、SDK/CLI 安装、网页登录、远端草稿、正式发布、公开可见、评论读取和回复分别报告。当前没有实测的自动公开公众号发布，也没有普遍可用的六平台无人值守发布/运营闭环。
