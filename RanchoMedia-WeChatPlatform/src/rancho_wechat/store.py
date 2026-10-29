"""SQLite persistence for the local author desk; schema uses PostgreSQL-portable primitives."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, normalized TEXT NOT NULL UNIQUE,
 idea TEXT NOT NULL, origin TEXT NOT NULL, status TEXT NOT NULL,
 news_published_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, topic_id TEXT REFERENCES topics(id), title TEXT NOT NULL,
 url TEXT NOT NULL, kind TEXT NOT NULL, excerpt TEXT NOT NULL,
 published_at TEXT, retrieved_at TEXT NOT NULL,
 UNIQUE(topic_id, url)
);
CREATE TABLE IF NOT EXISTS claims (
 id TEXT PRIMARY KEY, topic_id TEXT NOT NULL REFERENCES topics(id),
 source_id TEXT REFERENCES sources(id), text TEXT NOT NULL, quote TEXT NOT NULL,
 kind TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(topic_id, source_id, text)
);
CREATE TABLE IF NOT EXISTS research (
 topic_id TEXT PRIMARY KEY REFERENCES topics(id), note TEXT NOT NULL,
 status TEXT NOT NULL, provider TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS articles (
 id TEXT PRIMARY KEY, topic_id TEXT NOT NULL REFERENCES topics(id),
 angle TEXT NOT NULL, status TEXT NOT NULL, current_version_id TEXT,
 approved_version_id TEXT, fact_attested_at TEXT, created_at TEXT NOT NULL,
 UNIQUE(topic_id, angle)
);
CREATE TABLE IF NOT EXISTS versions (
 id TEXT PRIMARY KEY, article_id TEXT NOT NULL REFERENCES articles(id),
 ordinal INTEGER NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL,
 body TEXT NOT NULL, summary TEXT NOT NULL, outline_json TEXT NOT NULL,
 cover_json TEXT NOT NULL, fact_report_json TEXT NOT NULL,
 style_context_json TEXT NOT NULL, note TEXT NOT NULL, diff TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(article_id, ordinal)
);
CREATE TABLE IF NOT EXISTS style_samples (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL,
 text TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE, features_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS style_signals (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, text TEXT NOT NULL,
 evidence_version_id TEXT NOT NULL REFERENCES versions(id), confidence REAL NOT NULL,
 occurrences INTEGER NOT NULL, active INTEGER NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(kind, text)
);
CREATE TABLE IF NOT EXISTS tasks (
 task_key TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
 payload_json TEXT NOT NULL, attempts INTEGER NOT NULL,
 lease_until TEXT, result_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_recommendations (
 day TEXT NOT NULL, topic_id TEXT NOT NULL REFERENCES topics(id),
 rank INTEGER NOT NULL, PRIMARY KEY(day, rank), UNIQUE(day, topic_id)
);
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, target_type TEXT NOT NULL,
 target_id TEXT NOT NULL, action TEXT NOT NULL, details_json TEXT NOT NULL,
 at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exports (
 id TEXT PRIMARY KEY, article_id TEXT NOT NULL REFERENCES articles(id),
 version_id TEXT NOT NULL REFERENCES versions(id), path TEXT NOT NULL,
 sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(article_id, version_id)
);
CREATE INDEX IF NOT EXISTS ix_sources_topic ON sources(topic_id);
CREATE INDEX IF NOT EXISTS ix_claims_topic ON claims(topic_id);
CREATE INDEX IF NOT EXISTS ix_versions_article ON versions(article_id, ordinal);
CREATE INDEX IF NOT EXISTS ix_audit_target ON audit(target_type, target_id, id);
CREATE TABLE IF NOT EXISTS model_requests (
 request_key TEXT PRIMARY KEY, status TEXT NOT NULL, result_json TEXT,
 error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_calls (
 id TEXT PRIMARY KEY, request_key TEXT NOT NULL REFERENCES model_requests(request_key),
 task TEXT NOT NULL, model TEXT NOT NULL, day TEXT NOT NULL, status TEXT NOT NULL,
 reserved_tokens INTEGER NOT NULL, reserved_micros INTEGER NOT NULL,
 charged_tokens INTEGER NOT NULL, charged_micros INTEGER NOT NULL,
 usage_json TEXT, currency TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_model_calls_day ON model_calls(day);
CREATE TABLE IF NOT EXISTS style_rules (
 id TEXT PRIMARY KEY, category TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL,
 confidence REAL NOT NULL, evidence_json TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, UNIQUE(category,text)
);
CREATE TABLE IF NOT EXISTS author_work_versions (
 sample_id TEXT PRIMARY KEY REFERENCES style_samples(id), work_id TEXT NOT NULL,
 stage TEXT NOT NULL, parent_sample_id TEXT REFERENCES style_samples(id),
 article_version_id TEXT REFERENCES versions(id), source_url TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edit_feedback (
 version_id TEXT PRIMARY KEY REFERENCES versions(id),
 previous_version_id TEXT NOT NULL REFERENCES versions(id),
 deleted_json TEXT NOT NULL, added_json TEXT NOT NULL, patterns_json TEXT NOT NULL,
 explicit_preferences_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS content_imports (
 package_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
 article_id TEXT NOT NULL REFERENCES articles(id),
 execution_json TEXT NOT NULL, imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS codex_runs (
 run_key TEXT PRIMARY KEY, day TEXT NOT NULL, status TEXT NOT NULL,
 output_path TEXT NOT NULL, usage_json TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS article_assets (
 id TEXT PRIMARY KEY, article_id TEXT NOT NULL REFERENCES articles(id),
 path TEXT NOT NULL, description TEXT NOT NULL, position TEXT NOT NULL,
 rights_note TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(article_id,sha256)
);
"""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
