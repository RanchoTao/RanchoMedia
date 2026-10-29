# Initial public source validation

Date: 2026-10-29, Asia/Shanghai. Scope: publishable source snapshots, not production deployment or a new platform publication.

## Reproducibility

A new Python 3.12 environment was created for this export. Both modules were installed from the exported source; desktop dependencies were installed with `npm ci`. No operator database, credentials, policies, browser profile, manuscripts or model responses were copied into this environment.

| Check | Measured result |
|---|---|
| RanchoMediaOS tests | 73 passed; five existing Alembic CHECK-reflection warnings |
| WeChat/editor/Director/desktop backend tests | 192 passed, 1 skipped; existing TestClient/httpx deprecation warning |
| React desktop tests | 12 passed |
| TypeScript/Vite production build | Passed |
| Public distribution guard | 14 tests passed; rejects runtime/credential paths and source gitlinks |
| Lint, formatting, type checks, pip check | Passed; strict mypy covers 27 OS and 25 editorial source files |
| npm audit | No known vulnerabilities in the resolved tested lock |
| Public-source Windows EXE | PyInstaller build passed; binary not distributed |
| Fresh editor/API process smoke | Editor health HTTP 200 and OS API health HTTP 200; processes shut down afterward |
| Fresh offline demo | Generated Markdown marked unapproved, provider mock-fixture; external platform calls: 0 |

The original Windows client had previously been launched and checked for DPAPI save, invalid configuration, themes, resizing, file selection and read-only content navigation. The public screenshot uses a synthetic account with remote operations disabled. It does not certify an actual platform write. The public-source desktop EXE was built successfully. A separate no-network launch process remained alive, but exposed no main window during the bounded probe; this is not accepted as a new visual UI test. Only that isolated process was stopped; the existing operator client was left untouched.

The public desktop test fixture now creates its own synthetic variant and cover in pytest temporary directories. It no longer depends on an ignored operator material file. The launch/build helpers accept module-local, monorepo-root or an explicitly active virtual environment.

## Source and credential screening

- Source histories inspected: RanchoMediaOS, 4 commits at `51c6d9fd18da1b146669002ae6b951c2da8b16b8`; WeChatPlatform, 10 commits at `ab256e7bf6c1070e5973f47a3e71ab7edf4a8bb7`.
- Gitleaks 8.30.1 was obtained from its official GitHub release and checked against the release SHA256. All source history scans use redacted reports.
- OS history: zero findings. WeChat history: two generic-rule candidates manually reviewed as a redaction-test fake value and an editorial revision idempotency key. No actual credential was identified. Public fixture markers were normalized without turning off rules or using broad test-directory exclusions.
- Original histories remain local and unchanged. The public Git history starts with a reviewed source snapshot; historical private operational/editorial reports and runtime/model-call records are not imported.
- Prepared public files and the complete prepared Git history receive separate Gitleaks scans before push. A path/mode guard and explicit Git-tracked-file review accompany those scans; no one scanner is treated as proof of complete absence of secrets.

Private raw scan logs and original production receipts remain outside the public repository. `SOURCE_PROVENANCE.json` records original object hashes and public file changes without embedding those records or data.

## License and retained limits

The owner selected Apache-2.0 for original Rancho source. Third-party application source is not vendored. Optional pinned dependencies retain their licenses; dependency metadata is listed in `DEPENDENCY_LICENSES.json` and redistribution boundaries in `THIRD_PARTY_NOTICES.md`.

PostgreSQL, Temporal and a real Postiz service have not completed runtime qualification. Windows binaries are not committed, signing/clean-machine installation and multi-monitor DPI transitions remain unaccepted. Historical social creator-web publications do not certify MCP/SAU sessions or unattended Director writes. WeChat public publication/mass sending is disabled; a real draft-write receipt has not been established.
