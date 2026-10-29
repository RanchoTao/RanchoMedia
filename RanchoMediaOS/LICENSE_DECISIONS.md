# License decisions — original audit 2026-09-28; public release 2026-10-29

状态：Phase 0 已核实根许可证；依赖的完整 SBOM / 二进制分发审查尚未执行。此文件是工程使用边界，不把进程隔离当成许可证豁免。

| 项目 | 固定提交 | 实际 LICENSE | 修改 | 分发 | 商业使用 | Copyleft | Rancho 决策 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Postiz | `528f6ea37c8cf1156ad4a12de4e7aed853c019f2` | GNU AGPL v3；文件末尾 Postiz 授权声明允许 v3 或 later；package.json 标为 AGPL-3.0 | 允许，履行适用条件 | 允许，保留声明并满足对应源码等义务 | 允许，包括收费；并不免除 copyleft | 有；§5 修改作品、§6 对象代码分发、§13 修改版网络交互 | `WRAP` 独立服务，通过公开 HTTP API 集成；`REFERENCE_ONLY` 接口与工作流设计；不复制或链接其实现到 Rancho core |
| social-auto-upload | `0012d2c355f88f683cc38dde2a2db209e14091bc` | MIT，Copyright (c) 2023 dreammis | 允许 | 允许，副本/重要部分保留版权和许可声明 | 允许，包括闭源软件 | 无根项目 copyleft | `REFERENCE_ONLY` 为当前使用；后续按平台 `WRAP` 独立 browser-worker；选择性 `PORT` 前保留来源、许可并重新审计 |

## 实际读取的证据

- [Postiz LICENSE](https://github.com/gitroomhq/postiz-app/blob/528f6ea37c8cf1156ad4a12de4e7aed853c019f2/LICENSE)，已读取全文；§2 执行、§4–6 分发、§13 网络源码提供、§14 版本选择，末尾项目声明。
- [Postiz package.json](https://github.com/gitroomhq/postiz-app/blob/528f6ea37c8cf1156ad4a12de4e7aed853c019f2/package.json)，license 元数据不能代替 LICENSE。
- [social-auto-upload LICENSE](https://github.com/dreammis/social-auto-upload/blob/0012d2c355f88f683cc38dde2a2db209e14091bc/LICENSE)，已读取全文，保留版权及许可文本是明确条件。
- 官方文本辅助核验：[MIT](https://opensource.org/license/mit)、[AGPL](https://www.gnu.org/licenses/agpl-3.0.html)（本轮 GNU 页面抓取超时；结论依据本地实际 LICENSE）。

## 分发及部署边界

1. 公开源码不携带上游 gitlink 或实现；固定版本位于 ../integrations/upstreams.json，可通过根 scripts/fetch_upstream.py 明确获取。第三方源码不重标为 Rancho 许可证。
2. 若部署修改版 Postiz，向其远程用户提供对应源码的入口；若分发镜像或二进制，按适用条款附许可、声明、对应源码/获取方式。保持独立服务是工程隔离选择，不保证集成作品一定不受 copyleft；组合分发或紧密耦合需重新审查。
3. 所有者于 2026-10-29 明确选择 Apache-2.0，仅覆盖 Rancho 自有源码；第三方不重新授权。详见仓库根 LICENSE / THIRD_PARTY_NOTICES.md。
4. MIT 不自动覆盖依赖、嵌入脚本、图片、模型、浏览器或自动下载的 `biliup` 二进制。其来源、license、版本、hash 必须各自核验。本轮没有下载或执行 `biliup`。
5. `DO_NOT_USE`：未审计的签名服务、stealth 初始化、验证码/安全验证规避路径、上游通用账号/cookie Web 服务。源码许可证不等于平台准许自动化。
6. 真正采用 Postiz 时还需检查 credential 存储：审计发现 platform Integration 的 token/refreshToken String 与直接写入路径，不能凭 OAuth 模块有加密就认定全部 secrets 已加密。该安全门槛独立于许可。

## 复用分类

`DIRECT_REUSE`：原样运行合规部署的独立 upstream 服务；本轮未部署。
`WRAP`：HTTP / 独立 worker 契约集成，不导入 upstream core。
`PORT`：仅 MIT 模块候选，必须独立许可与安全审计、来源记录、测试后才能采纳。
`REFERENCE_ONLY`：研究架构、接口、错误分类，然后独立实现。
`DO_NOT_USE`：安全/平台风险不可接受或许可证未明的组件。
