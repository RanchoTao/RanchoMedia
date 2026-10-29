# Contributing

Use Python 3.12 and the locked development environment described in README. Keep platform-specific behavior out of RanchoMediaOS's generic core and reuse stable external uploaders without copying incompatible code.

Run `scripts/check_public_tree.py`, both Python test suites, lint/type checks and the desktop tests/build before a PR. New regressions must use temporary synthetic fixtures. Do not require credentials, copy operator databases or publish content in CI.

Describe local implementation, simulated contract checks, authenticated remote drafts, public publication, public visibility and comment/reply receipts separately. Changes to review, authorization, idempotency or unknown-outcome recovery require direct regression tests.

Preserve upstream notices. Original contributions are under Apache-2.0 unless a file explicitly states otherwise. Do not contribute third-party code or content without redistribution permission. Report security issues privately; public issue templates are not a credential collection channel.
