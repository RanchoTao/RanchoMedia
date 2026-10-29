# RanchoMedia Desktop 2.0 · 第一阶段

Windows 原生窗口、React 编辑工作空间、微信公众号安全接入。其余平台的历史接入不会因为桌面页面未实现而被视为未认证，也不会在这里伪造新的回执。

## 直接使用

在当前仓库中双击 `dist/RanchoMedia/RanchoMedia.exe`。保留整个 `RanchoMedia` 目录及 `_internal` 子目录；不要只复制 EXE。默认工作区按当前 `仓库/dist/RanchoMedia` 布局定位，也可显式指定：

```powershell
.\dist\RanchoMedia\RanchoMedia.exe --workspace "C:\dev\RanchoMedia\RanchoMedia-WeChatPlatform"
```

这是**依附当前工作区的便携版本**，未签名，尚未制作安装器或自动更新器。需要 Windows WebView2 和现有已固定 SDK 环境；不会打包、迁移或清空真实凭据和内容。真实调用继续由 `.runtime/ecosystem/wechat-bili/.venv/Scripts/python.exe` 执行。

## 开发与构建

```powershell
# 仓库根目录；已有虚拟环境可直接安装可选依赖
.\.venv\Scripts\python.exe -m pip install -e ".[desktop,desktop-build,dev]"
cd desktop
npm ci
npm run build
npm test
cd ..
powershell -NoProfile -File scripts/start_desktop.ps1

# 重建 Windows 便携包
powershell -NoProfile -File scripts/build_desktop.ps1
```

前端 `npm run dev` 只提供外观预览。普通浏览器没有原生 IPC，明确显示未连接宿主，不能保存凭据或访问微信。完整开发验收运行 `start_desktop.ps1 -Build`。

隔离测试使用独立配置且禁止所有微信请求：

```powershell
powershell -NoProfile -File scripts/start_desktop.ps1 -Isolated
.\dist\RanchoMedia\RanchoMedia.exe --workspace "C:\dev\RanchoMedia\RanchoMedia-WeChatPlatform" --state-dir "C:\dev\RanchoMedia\RanchoMedia-WeChatPlatform\.runtime\desktop-acceptance\ui-profile" --no-network
```

## 公众号操作

1. 在「平台账号 → 微信公众号 → 编辑配置」中核对名称与 AppID。本人在本机输入 AppSecret，点击保存。保存使用原有当前用户 DPAPI 格式；留空保留原值，更换 Secret 会清除旧令牌。
2. 在公众号后台设置 API IP 白名单。以**微信请求实际出口**为准；代理回显出口不一定等于微信请求出口。桌面沿用已有 token 验证记录中明确选择的直连／环境代理路径，不自动修改代理或白名单。新配置默认直连；需要首次选择环境代理时使用仓库现有 `--token-only --use-env-proxy` 入口。
3. 「验证凭据」单次获取令牌；「检查草稿权限」只读草稿列表。二者分别确认，不会顺带上传或创建内容。
4. 核对已准备稿件与本机封面。「创建草稿」确认框绑定 AppID、正文和封面指纹，只创建这一篇既有稿件的草稿；已有草稿 ID 时走核对，不重复添加。
5. 成功以微信远端 ID 和读取回执为准。公开发表、群发、自动回复都没有操作入口。图片规格和账号实际写权限仍由真实微信回执确认。

每次操作不自动重试，SDK 连接/读取超时 20 秒，宿主进程上限 150 秒。超时、崩溃或不明结果保留意图和锁。先核对进程、历史回执和公众号后台，不能通过反复点击重新上传。没有草稿 ID 的不明结果需要人工查后台；应用不会猜测成功或自动清锁。

验证凭据和权限检查只保存诊断范围的授权，旧 CLI 不能复用它来建草稿。上传／建草稿需要新的单稿确认。新 token 获取成功后，先前的权限探测不作为当前 token 的权限验收，需再次手动只读检查。

## 数据、接口与回退

- `src/rancho_wechat/desktop.py`：Windows WebView2 宿主、窄 IPC、DPAPI 配置适配、只读数据库摘要、任务启动和监视。
- `desktop/src/types.ts`：类型化 IPC 契约。公开方法只有 `get_snapshot/save_config/choose_cover/start_operation/open_docs`。
- `src/rancho_wechat/desktop_worker.py`：独立 SDK 子进程，复用现有草稿执行器。无通用命令执行、任意 URL 或公开发布方法。
- 普通启动只读现有配置与回执；原稿件数据库以 SQLite `mode=ro` 打开。内容库最多显示最近 30 篇，调度最多最近 20 条，均为只读。
- AppSecret/token 只经内存 IPC 传入 Python，再保存 DPAPI；快照只返回存在标志，不返回明文。浏览器仅可持久化主题选择。CSP 禁止远端连接、框架与表单提交，宿主阻止非应用文档导航。
- `.runtime/social-live/wechat/desktop-events.jsonl` 仅保存操作名与状态码；SDK stdout/stderr 不进入界面日志。运行中禁改配置，跨窗口锁避免同时提交。
- 原 HTML 作者编辑台和 `scripts/wechat_live_canary.py --ui` 保留。正文编辑、版本比较和审批继续使用原有入口；新桌面不自动批准或修改稿件。

第一阶段采用 pywebview/WebView2，避免在缺少 Rust/MSVC 的本机强制进行 Tauri 迁移。React 与 JSON 契约独立，后续可替换宿主。设计规范见模块 docs/DESKTOP_UI_DESIGN_SYSTEM.md；公开验证摘要见 ../../docs/RELEASE_VALIDATION.md。原私有运行报告不随源码分发。
