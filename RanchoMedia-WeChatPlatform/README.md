# RanchoMedia-WeChatPlatform

The existing Python editorial/Director module and React Windows desktop are preserved here. See the [root README](../README.md) for the monorepo installation and actual platform capability matrix.

## Local editorial loop

From this directory, with the root virtual environment installed:

```powershell
$env:WECHAT_PROVIDER='mock'
$env:WECHAT_WORK_MODE='EDITORIAL_FIRST'
$env:WECHAT_WORKSPACE='.runtime/v1.1'
..\.venv\Scripts\python.exe -m rancho_wechat.cli demo --out .runtime/demo
..\.venv\Scripts\python.exe -m rancho_wechat.cli serve --port 8767
```

The example is deliberately Mock, unapproved and unpublished. Real content uses `import-content` or bounded `codex-article` with authorized sources and legitimate Codex authentication. Compatible API providers and Ollama are optional. The CLI `--help` lists imports, feedback, style rules, scheduling and Director commands.

`EDITORIAL_FIRST` blocks approval/export in addition to normal fact/review gates. Changing to `STANDARD` must be an operator choice; it does not authorize platform writes. Editing and content approval remain separate from account/content-scoped publishing authorization.

Desktop: build `desktop` with `npm ci && npm run build`, then run `..\.venv\Scripts\python.exe -m rancho_wechat.desktop --workspace .`. The old HTML editor and Tkinter canary remain available. See [desktop instructions](desktop/README.md).

The official WeChat wrapper supports draft preparation and readback, not public publishing or mass sending. Other platform variants/tool probes are not general qualified uploaders. All operator state stays under ignored `.runtime` paths. No credentials, real author works, drafts or production policies are included.

License: [Apache-2.0](LICENSE) for original module source; [third-party notices](../THIRD_PARTY_NOTICES.md).
