# social-auto-upload 技术审计

日期 2026-09-28；commit `0012d2c355f88f683cc38dde2a2db209e14091bc`；完整历史，327 个 HEAD 可达提交，非 shallow；最新提交 2026-09-03 +08:00。8 月有 MIT license、CLI/图文/账号和视频号工作，最新快手补丁限定上传/发布重试。维护存在，不能推断 DOM 今日仍兼容。

证据路径相对 `upstream/social-auto-upload/`。[固定源码](https://github.com/dreammis/social-auto-upload/tree/0012d2c355f88f683cc38dde2a2db209e14091bc)。根 MIT 已读全文；依赖、脚本和二进制不自动享有相同许可。

## 架构

| 项 | 观察 |
| --- | --- |
| 核心 | Python asyncio，各 `uploader/<platform>/main.py`；平台专用 class / 参数，没有完整 analytics/reply SDK |
| 安装 | pyproject setuptools，Python >=3.10,<3.13；uv.lock；`sau=sau_cli:main`；requirements.txt 为另一套较大的历史依赖 |
| 浏览器 | 新模块 Patchright 1.58.2；旧 TikTok / XHS 等仍用 Playwright；requirements 却 pin Playwright 1.52.0 且不含 Patchright |
| Web | `sau_backend.py` Flask 3.1.1 async + CORS；Vue 3.5/Pinia/Element Plus/Vite 6 前端 |
| 数据 | sqlite3 + db/database.db、cookie/storage_state JSON、视频文件；SQLAlchemy/alembic 也在历史依赖表，不代表核心已有持久工作流 |
| queue | Web login 使用线程、内存 Queue/SSE；没有 Temporal 或可靠恢复的发布 ledger |
| auth | QR / browser login + cookie_auth；平台多个 account_name 文件；没有贯穿 Web API 的明确用户鉴权层 |
| API | `/postVideo`, `/postVideoBatch`, `/getAccounts`, `/getValidAccounts`, `/uploadCookie`, `/downloadCookie` 等；不是版本化 platform contract |
| deployment | Docker multi-stage，Node 22.21.1 / Python 3.10.19；镜像 requirements 路径及 playwright install；Windows start script |

`pyproject.toml` 与 requirements / Docker 路径有真实漂移：`sau_cli.py` 顶层 import 多个 Patchright 模块，Docker 却按 requirements 装旧 Playwright；还需生成 conf.py。opencv、浏览器、biliup 要单独锁版/审计；不能直接靠 requirements 安装视为可用。

## 平台能力

Y=具体代码；P=部分、遗留例子或外部工具；N=未发现，不能 silent fail。schedule 是平台 UI 填日期或 biliup dtime，不是持久化 scheduler。fetch_post 不包含跳转到管理页这个成功判断。全部 LIVE_TEST=NOT_RUN。

| Platform | publish_text | publish_image | publish_video | schedule | fetch_post | fetch_metrics | fetch_comments | reply_comment | search | trending | delete | edit | webhook |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 小红书 | N | Y | Y | Y | N | N | N | N | N | N | N | N | N |
| 抖音 | N | Y | Y | Y | N | N | N | N | N | N | N | N | N |
| Bilibili | N | N | P | P | N | N | N | N | N | N | N | N | N |
| 视频号 | N | N | Y | Y | N | N | N | N | N | N | N | N | N |
| 快手 | N | Y | Y | Y | N | N | N | N | N | N | N | N | N |
| 百家号 | N | N | Y | N | N | N | N | N | N | N | N | N | N |
| TikTok | N | N | Y | Y | N | N | N | N | N | N | N | N | N |
| YouTube | N | N | Y | N | N | N | N | N | N | N | N | N | N |
| 微博 | N | N | Y | N | N | N | N | N | N | N | N | N | N |
| 微信公众号 / 知乎 | N | N | N | N | N | N | N | N | N | N | N | N | N |

来源：`sau_cli.py` requests/dispatch/parser，各 `uploader` 和 README:73–85 交叉核对。图文是 note + images，不等于独立纯文字能力。视频号 `TencentNote:1147` 存在但核心步骤 `:1190–1196` 明确 NotImplementedError，不能计入支持。YouTube Studio 支持可见性/播放列表，但没有已实现 schedule CLI。百家号 constructor 有 publish_date 参数而未实现定时，不以参数存在推断能力。Bilibili `sau_cli.py` 调用外部 `biliup` 上传/日期；`uploader/bilibili_uploader` 仅 runtime 准备，不是官方 OAuth adapter。

## 账号、DOM 与风控

- account_name 对应独立 cookie 文件，context 从 storage_state 创建，成功后保存；并不是安全多租户 vault。Rancho 要 account_id+worker isolation+并发锁+加密秘密存储。`uploader/__init__.py` import 会创建 cookies 目录，不能将它随意导入 core/API。
- 新模块有 `get_by_role` / 文本 locator / CSS fallback、登录跳转检测、参数校验。CSS hash / 中文按钮 / 固定 URL / sleep 对 DOM 变化仍脆弱；账号/地区/审核 UI 会不同。
- 快手 `main.py:676–746` 视频上传有 deadline、发布次数上限；`KSNote:859` 仍 while True。旧 TikTok `main_chrome.py:258,282`、旧 XHS QR 等存在未统一限制的循环。一次补丁不能代表全部平台统一超时。
- `tencent_uploader/main.py:524–548` 识别实名验证并生成 QR 等待管理员扫码，未发现验证码自动破解；Rancho 必须立即停止为 NEEDS_HUMAN，手动完成后核对结果再恢复，不能复制无限等候行为。
- `set_init_script` / `utils/stealth.min.js` 与旧 `xhs_uploader/main.py` 的签名服务属 DO_NOT_USE。Rancho 不使用反检测初始化或代签网络服务。
- 部分截图调用没有持久化 path/受控 retention；暂无跨平台统一 DOM dump、脱敏 trace、确定的 platform publication ID 返回结构。管理页跳转或“发布成功”日志不等于已公开上线。
- cookie 导入/下载 API、宽泛 CORS、GET 删除操作和 Flask 入口不适合直接暴露网络；不复用整个 Web backend。
- `bilibili_uploader/runtime.py` 请求 GitHub latest，自动取对应架构资产、下载解压后执行，没有固定 release hash；subprocess 没有统一 timeout。Rancho 禁用自动 latest 下载并要求独立二进制 license/hash/来源审核。没有实际下载。

## 复用边界

| 模块 | 分类 | 当前选择 |
| --- | --- | --- |
| DOM selectors / 参数校验 / CLI account 设计 | REFERENCE_ONLY | 审计参考，基于允许的 UI 独立实现 |
| 经安全改造平台 uploader | WRAP 候选 | 独立 browser-worker，禁用 stealth，bounded timeout/人工风控/side-effect ledger/结果确认后才接入 |
| 少量 MIT 校验器 | PORT 候选 | 每个文件保留版权/许可证及来源；当前未复制 |
| 整个 Flask/Vue 系统 | DO_NOT_USE 于 Rancho | 不共享 cookie API/DB/账号模型 |
| TikTok / YouTube browser | REFERENCE_ONLY | 优先 Postiz 官方 API，不因为有源码就用 browser |
| 旧 XHS 签名服务 / stealth / 未锁版 binary downloader | DO_NOT_USE | 不纳入 Rancho runtime |

## 实测与构建状态

主机 PATH Python=3.13.9 超出声明范围；bundled Python=3.12.14 符合。未安装大依赖、未启动浏览器/平台登录。

执行上游 `test_bilibili_runtime.py` 的 unittest：原生入口失败于缺少 `conf`；只在内存加载 conf.example.py 后再次尝试，失败于缺少 `requests`。**UNIT_TEST=BLOCKED，0 个真实 case 通过；不是上游断言失败**。保留此事实，继续 AST 静态检查，未修改 upstream tracked 源码。完整 Python package / Docker / Vue BUILD=NOT_RUN。浏览器 DOM fixture、CLI 和账号 E2E=NOT_RUN。后续 Phase 2 用隔离 Python 3.12 环境和经审计依赖；不能据这些测试尝试宣称 Bilibili 投稿已验证。
