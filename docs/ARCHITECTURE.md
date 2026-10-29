# Architecture and execution boundaries

The initial public release preserves two working modules. There is no root-level restructuring of their Python packages or database schemas.

## RanchoMediaOS

`packages/core`, `packages/platform-sdk`, `packages/db`, `packages/content`, `packages/analytics`, `apps/api` and `workflows` contain the generic protocols, state, persistent queue, review, audit and qualification registry. Adapters include Mock, local Git Website and Postiz HTTP. SQLite is the tested local backend. PostgreSQL/Temporal are design/runtime targets whose live qualification has not been completed.

## RanchoMedia-WeChatPlatform

`src/rancho_wechat` contains the actual editorial model, source/fact checks, writer providers, style/version feedback, export and FastAPI editor. `network.py` provides the Director and persistent channel intents; `network_editorial.py` handles bounded Codex writing/review and import. `wechat_channel.py` wraps the pinned official draft API. `desktop.py`/`desktop_worker.py` provide the Windows host and isolated SDK process, while `desktop/src` contains the React UI.

The Director shares the editor's task ledger. It does not instantiate six independent, already qualified, continuously running LLM agents. Platform-specific variants and qualification states are independent, and external tool failures do not certify or disable unrelated platforms. Non-WeChat tool writes are currently explicitly blocked by the Director. One-off supervised social creator-web actions were performed outside that automatic dispatch path.

## Contracts and invariants

- Content packages carry sources/claims and are imported idempotently. AI output is not author approval.
- Editing creates immutable versions, diffs and candidate style feedback; a single edit is not a permanent style rule.
- Accounts, content fingerprints and times are bound to authorization. Defaults contain no accounts or publish rights.
- A stable task/effect intent precedes an external mutation. An unknown result is not automatically retried. Remote IDs are reconciled separately from local commits or process exits.
- The desktop bridge only exposes `get_snapshot`, `save_config`, `choose_cover`, `start_operation`, `open_docs`. Only the trusted application document can call it. There is no arbitrary command/URL bridge.
- DPAPI secrets flow once from password inputs to Python; snapshots return flags, not secrets. Historical successes are not treated as current credentials after token removal, expiry or account changes.
- Read-only confirmations cannot grant draft-write scope. A separate single-draft confirmation is needed for cover/draft writes. There is no public-publish/mass-send endpoint in the WeChat wrapper.

## Migration and provenance

The Windows host uses pywebview/WebView2, not Tauri. This reuses the Python business and DPAPI boundary without requiring a Rust/MSVC migration. React and the JSON bridge contract remain separable. The original HTML editor and Tkinter entry are retained as fallback interfaces.

External application source is not vendored or hidden in gitlinks. `integrations/upstreams.json` pins optional source/tool references. Original module commit and blob hashes are preserved in `SOURCE_PROVENANCE.json`; original private histories are not rewritten. Public changes are limited to licensing, distribution/privacy documentation, standalone test fixtures and monorepo launcher compatibility.
