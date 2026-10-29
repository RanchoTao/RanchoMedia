# Phase 2 actual test report

Status: **COMPLETE — offline core runtime and persistent mock**. Baseline `3af20b3`. Verified 2026-09-28 on Windows using Python **3.12.14** in the repository `.venv`. The machine-readable timestamp, exit codes, durations and hard evidence are in the original private phase2_quality.json (not distributed).

## Executed quality gates

Command: `.venv/Scripts/python.exe infra/verify_phase2.py`.

| Gate | Actual result |
| --- | --- |
| Ruff format check | PASS |
| Ruff lint | PASS |
| Strict mypy on all 23 runtime Python source files | PASS; no type-check suppression |
| Unit | **17 passed**, 0 failed/skipped |
| SDK/mock adapter contract | **11 passed**, 0 failed/skipped |
| Integration / schema / analytics | **21 passed**, 0 failed/skipped |
| E2E | **3 passed**, 0 failed/skipped |
| Total | **52 passed**, 0 failed/skipped |
| Own working-tree text secret scan | PASS; no candidate matches |
| Upstream pins and dirty status | Both exact pins preserved; both working trees clean |

Lint/format cover new runtime, tests and migration/verification scripts. Frozen Phase 1 `infra/audit_baseline.py` is excluded from these Phase 2 style gates; it was not rewritten to satisfy a new formatter. Runtime typechecking stays strict (`strict=true`) and is independent of fixture assertions and generated migration DDL. Dependency versions actually installed are pinned in [requirements.lock](../requirements.lock).

## Hard evidence from the executed E2E

```text
PHASE2_CORE_LOOP: PASS
EXTERNAL_POSTS_AFTER_5_IDENTICAL_RETRIES: 1
UNKNOWN_OUTCOME_EXTERNAL_POSTS: 1
UNKNOWN_OUTCOME_FINAL_OUTCOME: SUCCEEDED
RESTART_PERSISTENCE: PASS
REAL_API_PROCESS_RESTART: PASS
NEXT_TOPIC_BEFORE: Tools
NEXT_TOPIC_AFTER: Research
OBSERVED_MOCK_ENGAGEMENT_RATE: 0.1617867867867868
```

`test_full_mock_media_loop` persists three trends, explicit idea/brief/draft, canonical and variant genomes. Publishing before approval returns `CONTENT_NOT_APPROVED` with zero external posts. After exact human fixture approval, publication succeeds; five identical retries reuse job/external ID, retain attempt count 1, and leave one job/publication/external post. It persists 1/6/24-hour metrics, computes SQL analytics, saves proposed strategy facts/notes and changes candidate ordering. The next idea and generated genome retain that strategy UUID and the next draft again needs review. Three snapshots count as **one** observed publication.

`test_publish_unknown_outcome_recovery` commits the external post, deliberately loses the response, and observes `OUTCOME_UNKNOWN` with no local publication. It then shuts down the first app/engines and creates new ones against the same files. Recovery uses lookup; final outcome is `SUCCEEDED`, attempts remain 1, and exactly one external/local publication exists.

`test_api_subprocess_restart_recovers_unknown` starts a real Uvicorn child process on loopback and uses HTTP requests, closes that process, starts another against the same databases, and recovers the same unknown job. It then stores a metric and reads the actual SQL summary. The external database is inspected after shutdown and contains one post. No browser or real platform was involved.

## Additional verified failure/correctness paths

- Eight concurrent identical API requests produce one job, publication, effect, outbox and external post; no application mutex is used. Concurrent adapter calls separately prove native key uniqueness.
- Same key/different payload is rejected at both local job and external mock boundaries.
- A simulated process crash between external commit and local confirmation leaves `DISPATCHING`; a new runtime reconciles without sending again. An expired lease with no external match remains unknown and never resends.
- All five deterministic injections are checked. Before-send failure retries safely, rate-limit retries respect 60 seconds, terminal/human outcomes do not send again.
- Edit creates an immutable second revision and revokes the first approval. Reject blocks publishing; rejection of a future scheduled job cancels it when due. Approval schedule mismatch is refused.
- Cross-owner reads fail, a cross-owner composite FK insertion fails, missing/wrong fixture actor receives 401, naive time receives 422. Direct SQL payload mutation, genome mutation and illegal published-job transitions fail database guards.
- Metric age deduplication survives runtime restart; external metric state is also persistent. Normal media workflow leaves mock reply count 0. Separate SDK contract tests exercise mock comments/reply idempotency.
- Empty/zero/null metrics, unsupported watch fields, counter reset, matched-age baseline and as-of cutoff are verified. Twenty valid matched historical publications enable a baseline; one mismatched-age publication is excluded. SQL topic facts agree with an independently averaged set of stored reports.
- Identical injected strategy timestamps still select the newest successor proposal, and concurrent next-topic reads return the same persisted candidate UUID.
- Safe structured logging discards synthetic private marker messages and token/cookie/header attributes.

## Database and infrastructure boundary

```text
BUSINESS_TABLES: 24 (19 core + 5 external mock)
SQLITE_FRESH_ALEMBIC_UPGRADES: PASS
SQLITE_METADATA_CONSISTENCY: PASS
SQLITE_EMPTY_DOWNGRADE_UPGRADE: PASS
POSTGRES_DDL_COMPILE_AND_OFFLINE_MIGRATION_SQL: PASS
POSTGRES_RUNTIME_VALIDATION: PENDING_DOCKER
TEMPORAL_SERVER_REPLAY_TIMER_VALIDATION: PENDING_ENVIRONMENT
```

Every offline test uses a **file-backed** migrated database, not `:memory:` or an in-memory object-store substitute. Core and mock databases have separate transactions; fault tests prove the actual external table commit occurs before failure. PostgreSQL offline SQL generation creates no connection and cannot qualify real PostgreSQL execution. Docker was not repaired, reinstalled or bypassed.

## Secret scan boundary

Actual redacted scan output is the original private phase2_secret_scan.json (not distributed). It includes own tracked/untracked source, fixtures and explicit ignored environment/log/capture text where present. It emits only paths/line/rule information for own findings, and only path/classification/status for upstream candidates; matching values are never printed or copied.

One already known upstream candidate remains at `upstream/postiz/.env.example`, classification `secret_assignment_literal`, status `CANDIDATE_UNVERIFIED`. No credential validity test occurred. No candidate was found in social-auto-upload or own code. The heuristic covers working-tree text up to 2 MB; it does not constitute binary, history or credential validity auditing.

## Deferred

Real PostgreSQL/Temporal infrastructure qualification, production authentication/roles, real editorial/fact/similarity gates, real LLM and platform adapters, UI, video production, full source harvesting, experiment engine/RL, public deployment and automatic comment reply are not implemented or qualified by these tests. Mock performance is simulated evidence of mechanics, not proof of real audience performance or statistically significant strategy quality.

Public-source validation uses fresh synthetic data; see [current release validation](../../docs/RELEASE_VALIDATION.md). Historical raw run reports are intentionally withheld.
