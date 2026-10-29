# Third-party components and license boundaries

Only Rancho-owned source is Apache-2.0. Dependencies keep their own copyright and licenses. There is no third-party application source, browser profile, media runtime or upstream executable vendored in this repository.

| Component | Fixed source/version | License reviewed | Reuse in this release |
|---|---|---|---|
| Postiz server | `528f6ea37c8cf1156ad4a12de4e7aed853c019f2` | AGPL-3.0; see exact upstream LICENSE | External HTTP adapter; no Postiz implementation copied |
| Postiz CLI | `postiz@2.0.26`, gitroomhq/postiz-agent | AGPL-3.0 | Optional external CLI; not included, not authenticated by a clone |
| social-auto-upload | `0012d2c355f88f683cc38dde2a2db209e14091bc` | MIT, Copyright 2023 dreammis | External tool/qualification candidate; no source or uploader binary included |
| xiaohongshu-mcp | v2.5.5 / `a5c8f7799980ba1fdd501999843eb2d17e4c9a9f` | Apache-2.0 | Optional external MCP candidate; no executable or authenticated session included |
| wechatpy | `a695b89891e0473113c946a3a44d948fcf217404` | MIT | Official-API Python wrapper dependency, installed separately at the fixed commit |
| ZhihuSync | `a216c2677d00613c57dcdf11d884484379364ac3` | Skill metadata says MIT; complete license text not verified | Reference only. No source copied; not installed by default |
| pywebview / Python.NET | 6.2.1 / 3.2.1 in the tested environment | BSD-3-Clause / MIT | Desktop dependencies, installed through pip |
| React / Radix / Lucide / Tailwind / Vite | Exact resolved versions in desktop/package-lock.json | MIT / ISC and dependency-specific licenses | npm dependencies; keep installed package notices when redistributing bundles |
| PyInstaller | 6.22.3 | GPL-2.0-or-later with bootloader exception | Build tool only; no generated executable is committed |

The full npm lock includes MPL-2.0 build dependencies as well as MIT, Apache-2.0, ISC, BSD-2-Clause, BSD-3-Clause, MIT-0 and 0BSD packages. See `docs/DEPENDENCY_LICENSES.json` for the inspected package metadata. This release distributes source, not installed dependencies or a desktop binary. Building a distribution does not make those components Apache-2.0; retain their notices and comply with their own source/distribution obligations.

Postiz remains an independently obtained service. HTTP/process separation is an engineering boundary, not a blanket legal exemption. Modified AGPL network services and redistributed artifacts must meet applicable AGPL requirements, including corresponding-source access where required. Do not copy AGPL implementation into the Rancho core under the Apache label.

FFmpeg/biliup/browser binaries, models, downloaded media and platform SDK assets require their own version, provenance and redistribution review. The existing renderer/uploader references do not license those artifacts. ZhihuSync must not be vendored until its full license is established.

Author articles and platform content are not covered by the code license. Import only material you own or are authorized to use. An open-source license does not grant platform automation permissions or authorize account access.

Primary references: [Postiz](https://github.com/gitroomhq/postiz-app/blob/528f6ea37c8cf1156ad4a12de4e7aed853c019f2/LICENSE), [SAU](https://github.com/dreammis/social-auto-upload/blob/0012d2c355f88f683cc38dde2a2db209e14091bc/LICENSE), [XHS MCP](https://github.com/xpzouying/xiaohongshu-mcp/blob/a5c8f7799980ba1fdd501999843eb2d17e4c9a9f/LICENSE), [wechatpy](https://github.com/wechatpy/wechatpy/tree/a695b89891e0473113c946a3a44d948fcf217404), [ZhihuSync](https://github.com/zhihu/ZhihuSync/tree/a216c2677d00613c57dcdf11d884484379364ac3).
