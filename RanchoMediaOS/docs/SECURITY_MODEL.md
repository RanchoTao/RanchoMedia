# Security model v0.1

默认本地单所有者、LEVEL 2、platform mock；真实 credentials 不在本轮要求/读取范围。Phase 1 只有文档及静态检查，本文约束要在 Phase 2 用负例验证，不能宣称安全实现完成。

## Secrets 与边界

- OAuth token、refresh token、API key、cookies、storage_state、browser profiles 都是 secrets。DB 只存 secret_ref 与 scope/expiry/key_version；本地 Windows secret-store 可用 OS vault/DPAPI，容器用挂载 secret 或 vault，主密钥不在 DB/compose。选定 provider 前真实模式 disabled。
- Secret resolution 仅执行 worker 在发送前读取，不进入 Temporal args/results/history/search attributes、LLM prompts、API responses、audit 或 exception body。API/frontend/analysis 不能读 token。short-lived signed media URL 同样应 redaction。
- `.env` / cookie / profiles / private artifacts ignore；example 仅空值/非敏感配置。日志用结构化 allowlist、header/key redaction 与注册值 redaction；secret scan 输出 path/line/rule，不输出值。
- 真实 Postiz 自托管 platform token 的明文存储风险未解决。mock development 不依赖它。Phase 4 前需外部服务的 credential storage 实证；不满足则独立 fork security patch 或替代路径，履行 AGPL，不在原 clone 改源。

## 权限与审查

认证 owner 对所有 API 必需，read-only analytics 与 reviewer / publisher / admin 权限分离；本地 fixture seed owner 是测试身份，不能作为 production anonymous mode。CSRF、Origin检查、cookie secure/sameSite 与 owner-scoped FK/查询分别实施。账户连接授权只赋予服务能力，不能代替内容审批。

approval 绑定内容 revision/hash、account/platform、媒体、引用、check/policy version；edit/regenerate/adaptation 后失效；publish worker 发之前再验。审批之外的 side effect，例如真实 reply/delete/update/remote push，均需独立审核与 mode许可。所有权限拒绝也审计。

来源网页、RSS、评论为不可信输入：结构化包装、长度/mime限制、HTML清洗、工具allowlist，禁止跟随来源指令。Research/Writer 无publish tools。MDX 可执行组件/JS，Website 默认 Markdown 或受控 JSON；MDX 只允许模板生成和审核 allowlist，不能把模型任意 MDX 直接执行。

## Network、browser 与文件

- 采集/URL上传只允许 http(s)与明确主机，阻止 local/private/link-local/metadata、DNS rebinding、重定向到受限IP；response size/time/mime、下载hash/媒体扫描限制。worker 出站按adapter allowlist，不开放任意 URL mutation。
- Browser worker 后续单独进程/容器，独立每account临时profile，限制并发，受控租约与全局deadline；API不能启动browser。失败截图+DOM+logs存private artifact store；不能直接进入普通日志、模型输入或Git。
- 风控/验证码/实名/登录挑战/account warning → 立即 NEEDS_HUMAN，停止写动作；不规避、不代签、不采用stealth。手动解决后重新health/reconcile/review，而不是盲重发。
- Screenshot/DOM 可能含二维码、cookies、用户隐私、token URLs，默认访问reviewer、加密、短保留（初始7天可配置）；脱敏失败则不导出，不能声称脱敏一定完善。
- Git Website allowlisted repository、根路径resolve后 containment检查、允许扩展名/slug、拒绝symlink/junction逃逸/`../`，固定branch/new branch；subprocess参数数组，不拼shell。DRY_RUN生成diff，无commit/push；CREATE_PR才新branch/commit/push+PR，DIRECT_PUSH仅明确目标分支授权，main默认禁止。credentials用secretref/已有安全credential helper，不在 URL 或 command args。

## 数据与供应链

PII minimization、comments pseudonym、source provenance/rights、retention/deletion audit。Raw payload 执行scrub，不将原始token或完整浏览器state塞JSONB。

锁依赖和构建runtime；不跑上游destructive migration，不未经审计执行install scripts。克隆不等于信任外部代码。biliup latest auto-downloader关闭；未来binary必须license、release版本、SHA256与安全提取/timeout检查。browser二进制版本明确，但不用于规避平台验证。

本轮 secret scan 范围、结果和限制见 [public source verification](../../docs/RELEASE_VALIDATION.md)。静态启发式 scan 不等于全历史gitleaks/SBOM/漏洞扫描；真正发布前执行更完整扫描与安全测试，发现上游疑似值只记录位置、不传播内容。
