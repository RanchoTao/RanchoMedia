# RanchoMediaOS

Generic task, persistence, qualification, adapter and audit infrastructure. The original module layout is retained; see the [monorepo README](../README.md).

Tested capabilities: persistent local Mock loop, immutable review binding, idempotency/effect ledger, restart and unknown-outcome handling, real local Git WebsiteAdapter and a Postiz HTTP wrapper against a simulated service. SQLite supports local development. A live PostgreSQL/Temporal/Postiz runtime is not certified.

## Local API simulation

From this directory, after installing the root development environment:

```powershell
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m alembic -c alembic-mock.ini upgrade head
$env:LOCAL_API_TOKEN = & ..\.venv\Scripts\python.exe -c 'import secrets; print(secrets.token_urlsafe(32))'
..\.venv\Scripts\python.exe -m uvicorn rancho_api.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

The runtime creates only ignored local SQLite state. Business requests require both `X-Actor-ID` and the locally generated `X-Rancho-API-Token`; fixture UUIDs alone are not authentication. This development API is not a production multi-user identity service. Do not expose it publicly.

Run `..\.venv\Scripts\python.exe -m pytest -q`, `-m ruff check .` and `-m mypy` from this directory. The integration tests create their own temporary databases and local Git repositories, not production accounts.

Pinned Postiz/SAU applications are external and not required for the local tests. Optional read-only source audits can first obtain them using `python ../scripts/fetch_upstream.py postiz` / `social-auto-upload`. This replaces source gitlinks and does not install/start upstream services or recursively fetch their submodules. See [notices](../THIRD_PARTY_NOTICES.md), [architecture](ARCHITECTURE.md), [security model](docs/SECURITY_MODEL.md) and [license decisions](LICENSE_DECISIONS.md).

Original Rancho source: [Apache-2.0](LICENSE). Upstream projects keep their own licenses.
