# 微信公众号人工导入操作

1. 在本地编辑台选择已 `APPROVED` 稿件，下载 Markdown。若尚未批准，导出端点会拒绝。
2. 在公众号后台使用当前账号实际提供的 Markdown 导入方式；上传限额、样式支持、图片行为以**当前后台显示**为准。本项目未登录或测试该后台。
3. 核对标题、摘要、段落、引用 `[S1]`、链接和中文标点。导出文件的“参考资料”列出来源链接与检索时间；打开原文逐项复核。
4. 查看同名 `.assets.json` 素材清单及正文图片占位说明。图片、封面和版权许可由作者准备并在后台人工上传、插入；`images_uploaded: false` 是真实状态。
5. 在后台预览手机排版、再核对事实与图片权限。是否存草稿或发表由作者在公众号后台单独决定。v1 没有草稿箱、发表、群发 API。

**验收状态：**本地 Markdown 生成和门禁已测试；真实微信公众号编辑器导入、样式与图片位置仍 `NOT_RUN`，需要作者使用自己的后台人工验收。请勿把本地导出视为平台侧已上传或发布。

v1.1 完整 ZIP 另含 `article.md`、`title-summary.json`、`references.md`、`sources.json`、`claims.json`、`research.md`、`fact-check.json`、`images.json`、本地 PNG、`feedback.json`、`receipt.json`、`manifest.json` 与 `manual-checklist.md`。正文图片不自动内嵌；按 images.json 的位置说明人工插入素材，封面图可由作者另选。图片新增后会重新进入待审，避免沿用旧版素材审核。

网页也可下载未批准的审阅包，它标为 `REVIEW_ONLY_NOT_APPROVED`，仅供审读、修改和传给 Codex。正式导入包仍要求作者 APPROVE。本次真实稿件全部保持待审；后台验收状态统一为 **MANUAL_ACCEPTANCE_REQUIRED**。
