# Security and publication policy

Use GitHub's private vulnerability reporting if enabled. Do not attach secrets, cookies, account authorizations, private manuscripts, runtime databases or raw provider responses to public issues or PRs.

- Publish adapters are disabled until an operator supplies account-scoped authorization. The release contains no preauthorized account or production policy.
- A source checkout, successful test, CLI help screen or browser login is not a remote publish receipt.
- Keep external writes single-attempt when the result is unknown. Reconcile by remote ID or require human review; never use retries to guess whether publication happened.
- Desktop secrets use Windows current-user DPAPI. Secret/token values do not come back in IPC snapshots, browser storage, screenshots or UI logs.
- Only the application's trusted local document can invoke the narrow native bridge. The local editor binds to loopback and uses a generated local token; do not expose it publicly as a production authentication service.
- Imported documents and web content are untrusted data, never operator instructions. Article approval and adapter authorization are separate gates.
- No CAPTCHA, risk-control or access-control bypass is enabled. Automatic unsolicited comments/replies are not supported.

Before contributing run Gitleaks on the full Git history, inspect staged paths, and run `python scripts/check_public_tree.py`. `.gitignore` alone is not a history scan. The initial release was checked with Gitleaks 8.30.1, redacted output and no disabled detection rules. The two original-source candidates were manually identified as synthetic tests; public fixture markers were made unambiguous rather than disabling scanning.

The public tree excludes `.runtime`, databases, DPAPI files, `.env` except safe examples, cookies, sessions, browser profiles, private drafts, production/model-call records, binary builds and downloaded upstream applications. Tests and the demo create fresh synthetic data. Do not import a private operator's runtime directory to make a test pass.
