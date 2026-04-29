# CHAPI Implementation Status

**Last updated:** 2026-04-29 (Refactor Phase 5 — final validation complete, ready to deploy)
**Last updated by:** agent

The agent updates this file at the end of each phase. Use this file to
remember where you stopped if the implementation spans multiple sessions.

---

## Original Implementation — COMPLETE (do not re-implement)

Phases 1–5 of the original synchronous CLI implementation are fully done and
merged to `main`. The code is production-ready (deployed to Cloud Run Job).
These notes are kept as context only.

- [x] Phase 1 — `dedup.py` pure function, 5 self-tests pass
- [x] Phase 2 — `fhir_client.py` (ChapiClient, sync requests), `checkpoint.py` (GCS)
- [x] Phase 3 — `main.py` dry-run loop, checkpoint resume, JSON logging
- [x] Phase 4 — live PUT, 412/4xx/5xx handling, backfill checkpoint write
- [x] Phase 5 — pre-deploy validation, regression dry-run clean, handed off to human

---

## Refactor — FastAPI + asyncio (Cloud Run Service)

Goal: switch from a sync CLI batch job (Cloud Run Job) to an async FastAPI
service (Cloud Run Service) that supports concurrency > 1. Background task
model for `/run`; GCS-backed run status.

Reference: `REFACTOR_PROMPT.md` (full spec for all 5 refactor phases).

---

### Refactor Phase 1 — Dependencies & Setup

- [x] `requirements.txt` updated: `fastapi`, `uvicorn[standard]`, `httpx`, `google-cloud-storage`; `requests` removed
- [x] `.python-version` created with content `3.11`
- [x] `requirements-dev.txt` created: `pytest`, `pytest-asyncio`, `respx`
- [x] `pip install -r requirements.txt -r requirements-dev.txt` succeeds (no errors)

**Notes from agent:** Replaced `requirements.txt` (dropped `requests`, added `fastapi`, `uvicorn[standard]`, `httpx`), created `.python-version` (`3.11`) and `requirements-dev.txt`. Verified via a temporary venv — all packages resolved and installed cleanly with no conflicts.

---

### Refactor Phase 2 — Async FHIR Client

- [x] `fhir_client.py` rewritten using `httpx.AsyncClient`
- [x] `ChapiClient` is an async context manager (`__aenter__` / `__aexit__`)
- [x] `iter_patient_bundles` is an async generator (`AsyncIterator[dict]`)
- [x] `get_patient` and `put_patient` are `async def`
- [x] `_rebuild_next_url` logic preserved exactly (preserves `_lastUpdated`, etc.)
- [x] `put_patient` returns `(None, (status, body))` on non-2xx instead of raising
- [x] `tests/test_fhir_client.py` written — all 8 required test cases present
- [x] `pytest.ini` or `pyproject.toml` sets `asyncio_mode = "auto"`
- [x] `pytest tests/test_fhir_client.py -v` — all 8 tests pass

**Notes from agent:** Rewrote `fhir_client.py` with `httpx.AsyncClient` as an async context manager; `_rebuild_next_url` preserved verbatim; `put_patient` returns `(None, (status, body))` on non-2xx without raising. Created `tests/test_fhir_client.py` (8 cases using `respx.MockRouter` + pytest-asyncio auto mode) and `pytest.ini`; all 8 tests pass.

---

### Refactor Phase 3 — Async Checkpoint + StatusStore

- [x] `checkpoint.py` updated: `read`, `write`, `delete` are `async def` via `asyncio.to_thread()`
- [x] `status_store.py` created: `StatusStore(bucket, server, tenant)`
- [x] `StatusStore.write_running(run_id, tenant, server, mode, dry_run, started_at)` writes `status="running"` to GCS
- [x] `StatusStore.write_final(run_id, status, finished_at, summary, error)` does read–merge–write
- [x] `StatusStore.read(run_id)` returns `Optional[dict]`; `None` if not found
- [x] GCS path for status: `status/{server}/{tenant}/{run_id}.json`
- [x] `tests/test_checkpoint.py` written — all 4 required test cases present
- [x] `tests/test_status_store.py` written — all 5 required test cases present
- [x] `pytest tests/test_checkpoint.py tests/test_status_store.py -v` — all 9 tests pass

**Notes from agent:** Updated `checkpoint.py`: each GCS call wrapped in an inner sync function passed to `asyncio.to_thread()`; logic unchanged. Created `status_store.py`: `write_final` does a read–merge–write inside a single `to_thread` call (NotFound starts from an empty dict); GCS path is `status/{server}/{tenant}/{run_id}.json`. All 9 tests pass using `unittest.mock.patch` on `storage.Client`.

---

### Refactor Phase 4 — FastAPI App + Async Main Logic

- [x] `main.py` rewritten as FastAPI app
- [x] `GET /health` returns `200 {"status": "ok"}`
- [x] `POST /run` returns `202 {"run_id": "...", "status_url": "/status/<run_id>"}` immediately
- [x] `GET /status/{run_id}` reads from GCS `StatusStore`; returns `404` if not found
- [x] `lifespan` creates global `asyncio.Semaphore(FHIR_CONCURRENCY)` at startup
- [x] `_run_background` writes `status="completed"` or `status="failed"` to GCS on finish
- [x] `run()` is `async def`; uses `async with ChapiClient(...) as client:`
- [x] Pagination loop: `async for bundle in client.iter_patient_bundles(...):`
- [x] Patients within each bundle processed concurrently via `asyncio.gather()`
- [x] Semaphore acquired only around `await client.put_patient()` (not the full coroutine)
- [x] Checkpoint write and delete are `await`-ed
- [x] All original log event field names preserved
- [x] `run()` returns a summary dict (same fields as original `run_summary` log event)
- [x] `deploy.sh` updated: `gcloud run jobs deploy` → `gcloud run services deploy`
- [x] `deploy.sh` updated: `--task-timeout` → `--timeout`, `--max-retries 0` → `--min-instances 1`
- [x] `deploy.sh` updated: `--port 8080`, `--concurrency 80`, `FHIR_CONCURRENCY=20` added
- [x] `Procfile` created: `web: uvicorn main:app --host 0.0.0.0 --port $PORT`
- [x] `tests/test_main.py` written — all 6 required test cases present
- [x] `pytest tests/ -v` — all tests from all phases pass

**Notes from agent:** Rewrote `main.py` as FastAPI app with lifespan semaphore, three endpoints, `_run_background` error handler, and async `run()`. `_process_patient` is a closure inside `run()` using `nonlocal` counters; semaphore wraps only the `put_patient` await. All 9 original log event types preserved. Updated `deploy.sh`: `JOB_NAME`→`SERVICE_NAME`, jobs→services, added `--port 8080 --concurrency 80 --min-instances 1 FHIR_CONCURRENCY=20`. Created `Procfile`. All 23 tests (Phases 1–4) pass.

---

### Refactor Phase 5 — Final Validation

- [x] `pytest tests/ -v --tb=short` — full suite clean, zero failures
- [x] `MODE=incremental TENANT=test SERVER_KIND=chapi FHIR_URL=http://x CHAPI_API_KEY=x python -c "from main import app; print('OK')"` prints `OK`
- [x] `git diff dedup.py` — empty (dedup logic untouched)
- [x] `git diff ../hapi_patient_duplicate_address/` — empty (HAPI untouched)
- [x] Change summary printed by agent

**Notes from agent:** All 23 tests pass; import check prints `OK`; `dedup.py` and `hapi_patient_duplicate_address/` diffs are empty.

**Files changed/created in this refactor:**
- `requirements.txt` — dropped `requests`, added `fastapi`, `uvicorn[standard]`, `httpx`
- `.python-version` (new) — pins Python 3.11
- `requirements-dev.txt` (new) — `pytest`, `pytest-asyncio`, `respx`
- `fhir_client.py` — `requests.Session` → `httpx.AsyncClient`; async context manager; all methods `async def`; `_rebuild_next_url` verbatim
- `checkpoint.py` — GCS calls wrapped in `asyncio.to_thread()`; all methods now `async def`; logic unchanged
- `status_store.py` (new) — per-run GCS status at `status/{server}/{tenant}/{run_id}.json`; `write_running`, `write_final` (read–merge–write), `read`
- `main.py` — sync CLI → FastAPI service; `/health`, `POST /run`, `GET /status/{run_id}`; lifespan semaphore; `_run_background`; async `run()` with `asyncio.gather()` per bundle; all helpers and log event field names preserved verbatim
- `deploy.sh` — `gcloud run jobs` → `gcloud run services`; `JOB_NAME`→`SERVICE_NAME`; `--task-timeout`/`--max-retries 0` → `--timeout`/`--min-instances 1`; added `--port 8080`, `--concurrency 80`, `FHIR_CONCURRENCY=20`
- `Procfile` (new) — `web: uvicorn main:app --host 0.0.0.0 --port $PORT`
- `pytest.ini` (new) — `asyncio_mode = auto`, `pythonpath = .`
- `tests/test_fhir_client.py` (new) — 8 tests for `ChapiClient`
- `tests/test_checkpoint.py` (new) — 4 tests for async `Checkpoint`
- `tests/test_status_store.py` (new) — 5 tests for `StatusStore`
- `tests/test_main.py` (new) — 6 tests for endpoints and `_run_background`

**Unchanged:** `dedup.py`, `hapi_patient_duplicate_address/`, `plan.md`

---

## Ready to deploy

When Refactor Phase 5 is fully checked, the human runs:

```bash
./deploy.sh purbalingga
```

The service will be available at a Cloud Run URL. Trigger a run:

```bash
curl -X POST https://<service-url>/run
# returns: {"run_id": "...", "status_url": "/status/<run_id>"}

curl https://<service-url>/status/<run_id>
# returns: run status from GCS
```

Set up Cloud Scheduler to POST to `/run` every hour.
