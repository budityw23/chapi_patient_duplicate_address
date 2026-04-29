# Refactor: chapi_patient_duplicate_address — FastAPI + asyncio

## Context

You are refactoring `chapi_patient_duplicate_address/` from a synchronous CLI batch job
(using `requests`) to an **async FastAPI service** that can run on Cloud Run with
concurrency > 1.

**Scope**: Only touch files inside `chapi_patient_duplicate_address/`. Do NOT modify
`hapi_patient_duplicate_address/`, `exploration/`, or `dedup.py` (pure logic, already
correct).

**Do not follow `plan.md`** — that is the spec for the original synchronous CLI
implementation, which is complete. It contains instructions that directly conflict with
this refactor. `REFACTOR_PROMPT.md` (this file) is the sole spec for the refactor.

**Architecture change**: This moves from a Cloud Run **Job** (`gcloud run jobs deploy`)
to a Cloud Run **Service** (`gcloud run services deploy`). The `deploy.sh` must be
updated accordingly.

**Key design decisions** (do not second-guess these):

- `POST /run` starts a background task and returns `202 {"run_id": "...", "status_url": "/status/<run_id>"}` immediately
- `GET /status/{run_id}` reads run status from GCS
- `GET /health` returns `200 {"status": "ok"}`
- All existing config stays in env vars (no request body needed for `/run`)
- `FHIR_CONCURRENCY` env var (default `20`) — global `asyncio.Semaphore` caps
  concurrent FHIR calls across ALL active runs
- Patients within a bundle are processed concurrently via `asyncio.gather()`
- GCS calls (`Checkpoint`, `StatusStore`) wrapped in `asyncio.to_thread()` — no new
  GCS library
- `dedup_addresses()` is sync/pure — call it directly, no wrapping needed
- Cloud Run `--min-instances=1` so background tasks are not killed by scale-to-zero

---

## PHASE 1 — Dependencies & Project Setup

**Files to create/modify:**

### `requirements.txt` (replace entirely)

```txt
fastapi
uvicorn[standard]
httpx
google-cloud-storage
```

### `.python-version` (new file, one line)

```txt
3.11
```

### `requirements-dev.txt` (new file)

```txt
pytest
pytest-asyncio
respx
```

**After Phase 1:** Verify `pip install -r requirements.txt -r requirements-dev.txt`
runs without error (use a venv or `--dry-run` if you want to avoid polluting the
environment). Do not proceed to Phase 2 until confirmed.

**When done:** Update `status.md` — mark all Refactor Phase 1 checkboxes, write a
one-or-two-sentence note under "Notes from agent". Stop — do not start Phase 2.

---

## PHASE 2 — Async FHIR Client (`fhir_client.py`)

Rewrite `fhir_client.py` entirely. The existing `requests.Session`-based `ChapiClient`
must become an `httpx.AsyncClient`-based async class.

### Contract (exact signatures to implement)

```python
import re
from collections.abc import AsyncIterator
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx


class ChapiClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 60) -> None:
        """Store config; no I/O in __init__."""

    async def __aenter__(self) -> "ChapiClient":
        """Create the httpx.AsyncClient with default headers."""

    async def __aexit__(self, *args) -> None:
        """Close the httpx.AsyncClient."""

    async def iter_patient_bundles(self, initial_url: str) -> AsyncIterator[dict]:
        """
        Yield FHIR Bundle dicts, following pagination.
        Rebuilds the next URL from initial_url to preserve query params
        (same logic as the original _rebuild_next_url).
        Raises httpx.HTTPStatusError on non-2xx.
        """

    async def get_patient(self, patient_id: str) -> tuple[dict, str]:
        """
        GET /Patient/{patient_id}.
        Returns (resource_dict, version_id).
        Raises httpx.HTTPStatusError on non-2xx.
        """

    async def put_patient(
        self, patient_id: str, version_id: str, resource: dict
    ) -> tuple[Optional[str], Optional[tuple[int, dict]]]:
        """
        PUT /Patient/{patient_id} with If-Match: W/"version_id".
        Returns (new_version_id, None) on 200/201.
        Returns (None, (status_code, body_dict)) on any other status — does NOT raise.
        """
```

**Implementation notes:**

- Default headers on the `httpx.AsyncClient`: `X-API-Key`, `Accept: application/fhir+json`
- `iter_patient_bundles` is an `async def` that uses `yield` — making it an
  async generator (Python automatically makes it an `AsyncIterator`)
- The `_rebuild_next_url` logic from the original must be preserved exactly
- `put_patient` catches non-2xx without raising; all other methods raise on non-2xx

### Write `tests/test_fhir_client.py`

Use `respx` to mock HTTP calls. Use `pytest-asyncio` for async test functions.
Add `pytest.ini` or `pyproject.toml` with `asyncio_mode = "auto"`.

**Required test cases:**

1. `test_iter_patient_bundles_single_page` — mock GET returning a bundle with no
   `next` link; assert the single bundle is yielded and iteration stops.

2. `test_iter_patient_bundles_two_pages` — mock GET twice; first response has a
   `next` link with `_page_token=tok123`; second response has no next link.
   Assert both bundles are yielded and the second request URL contains
   `_page_token=tok123`.

3. `test_iter_patient_bundles_preserves_last_updated` — initial URL contains
   `_lastUpdated=ge2024-01-01`; mock two pages; assert the second request URL
   still contains `_lastUpdated=ge2024-01-01`.

4. `test_get_patient_success` — mock GET returning a patient JSON with
   `meta.versionId = "3"`; assert returns `(resource, "3")`.

5. `test_put_patient_success_200` — mock PUT returning 200 with
   `meta.versionId = "4"`; assert returns `("4", None)`.

6. `test_put_patient_412` — mock PUT returning 412 with a JSON body;
   assert returns `(None, (412, body_dict))` and does NOT raise.

7. `test_put_patient_500` — mock PUT returning 500; assert returns
   `(None, (500, ...))` and does NOT raise.

8. `test_get_patient_raises_on_404` — mock GET returning 404; assert
   `httpx.HTTPStatusError` is raised.

Run `pytest tests/test_fhir_client.py -v` and confirm all 8 pass before
proceeding to Phase 3.

**When done:** Update `status.md` — mark all Refactor Phase 2 checkboxes, write a
one-or-two-sentence note under "Notes from agent". Stop — do not start Phase 3.

---

## PHASE 3 — Async Checkpoint + StatusStore

### Update `checkpoint.py`

Wrap every GCS call in `asyncio.to_thread()`. Keep the class interface identical
except all three public methods become `async def`.

```python
class Checkpoint:
    def __init__(self, bucket: str, server: str, tenant: str) -> None: ...

    async def read(self) -> Optional[dict]: ...   # was sync
    async def write(self, state: dict) -> None: ...  # was sync
    async def delete(self) -> None: ...              # was sync
```

The underlying GCS logic (blob path, JSON encode/decode, NotFound handling)
stays exactly the same — only the `asyncio.to_thread()` wrapping is new.

### Create `status_store.py` (new file)

```python
import asyncio
import json
from typing import Optional

from google.api_core.exceptions import NotFound
from google.cloud import storage


class StatusStore:
    """
    Stores per-run status JSON in GCS at:
      status/{server}/{tenant}/{run_id}.json
    inside the same bucket used by Checkpoint.
    """

    def __init__(self, bucket: str, server: str, tenant: str) -> None: ...

    async def write_running(
        self,
        run_id: str,
        tenant: str,
        server: str,
        mode: str,
        dry_run: bool,
        started_at: str,
    ) -> None:
        """
        Write initial status. Schema:
        {
          "run_id": str,
          "status": "running",
          "tenant": str,
          "server": str,
          "mode": str,
          "dry_run": bool,
          "started_at": str (ISO-Z),
          "finished_at": null,
          "summary": null,
          "error": null
        }
        """

    async def write_final(
        self,
        run_id: str,
        status: str,          # "completed" | "failed" | "checkpointed"
        finished_at: str,     # ISO-Z
        summary: Optional[dict],
        error: Optional[str],
    ) -> None:
        """
        Merge into the existing object (read–merge–write) so metadata from
        write_running is preserved.
        If the object does not exist, write a standalone record.
        """

    async def read(self, run_id: str) -> Optional[dict]:
        """Return the status dict, or None if not found."""
```

All three GCS calls must use `asyncio.to_thread()`.

### Write `tests/test_checkpoint.py` and `tests/test_status_store.py`

Use `unittest.mock.patch` to mock `google.cloud.storage.Client`.

**`tests/test_checkpoint.py` required cases:**

1. `test_read_returns_dict` — blob.download_as_text returns JSON; assert dict returned.
2. `test_read_returns_none_on_not_found` — blob raises `NotFound`; assert None.
3. `test_write_uploads_json` — assert `blob.upload_from_string` called with correct JSON.
4. `test_delete_suppresses_not_found` — blob.delete raises `NotFound`; assert no exception.

**`tests/test_status_store.py` required cases:**

1. `test_write_running_creates_correct_schema` — assert GCS object has `status="running"`,
   `finished_at=None`, `summary=None`, `error=None`.
2. `test_write_final_merges_with_existing` — mock read returning the running record,
   then write_final with status="completed"; assert final object has both the original
   `started_at` and the new `finished_at`/`summary`.
3. `test_write_final_standalone_when_not_found` — mock read raising NotFound; assert
   write_final still writes a record without crashing.
4. `test_read_returns_none_on_missing` — blob raises NotFound; assert None.
5. `test_read_returns_dict` — blob returns JSON; assert dict.

Run `pytest tests/test_checkpoint.py tests/test_status_store.py -v` and confirm
all tests pass before Phase 4.

**When done:** Update `status.md` — mark all Refactor Phase 3 checkboxes, write a
one-or-two-sentence note under "Notes from agent". Stop — do not start Phase 4.

---

## PHASE 4 — FastAPI App + Async Main Logic (`main.py` + `deploy.sh`)

This is the largest phase. Rewrite `main.py` entirely.

### App structure

```python
import asyncio, datetime, json, logging, os, sys, time, uuid
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from checkpoint import Checkpoint
from dedup import ADMIN_URL_ORDER, _get_admin_code, dedup_addresses
from fhir_client import ChapiClient
from status_store import StatusStore
```

### Constants (keep identical to original)

```python
PAGE_SIZE = 1000
CHECKPOINT_INTERVAL_S = 3300
HTTP_TIMEOUT_S = 60
```

### Config — read once at startup

Read all env vars at module level (same `_require_env` helper as before). If a
required var is missing, log CRITICAL and `sys.exit(1)` — same fast-fail behavior.

Required vars: `MODE`, `TENANT`, `SERVER_KIND`, `FHIR_URL`, `CHAPI_API_KEY`.
Optional vars: `DRY_RUN` (default `"true"`), `CHECKPOINT_BUCKET` (default
`"dedup-patient"`), `FHIR_CONCURRENCY` (default `"20"`), `LIMIT`, `PATIENT_ID`.

### Lifespan — global semaphore

```python
_fhir_sem: asyncio.Semaphore  # set in lifespan

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _fhir_sem
    _fhir_sem = asyncio.Semaphore(int(os.environ.get("FHIR_CONCURRENCY", "20")))
    yield

app = FastAPI(lifespan=lifespan)
```

### Endpoints

```python
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/run", status_code=202)
async def trigger_run(background_tasks: BackgroundTasks):
    """
    Start a dedup run as a background task.
    Returns immediately with run_id.
    """
    run_id = uuid.uuid4().hex
    status_store = StatusStore(
        bucket=CHECKPOINT_BUCKET, server=SERVER_KIND, tenant=TENANT
    )
    await status_store.write_running(
        run_id=run_id,
        tenant=TENANT,
        server=SERVER_KIND,
        mode=MODE,
        dry_run=DRY_RUN,
        started_at=_now_z(),
    )
    background_tasks.add_task(_run_background, run_id, status_store)
    return {"run_id": run_id, "status_url": f"/status/{run_id}"}


@app.get("/status/{run_id}")
async def get_status(run_id: str):
    """Read run status from GCS."""
    status_store = StatusStore(
        bucket=CHECKPOINT_BUCKET, server=SERVER_KIND, tenant=TENANT
    )
    record = await status_store.read(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run not found")
    return record
```

### Background task function

```python
async def _run_background(run_id: str, status_store: StatusStore) -> None:
    """
    Wraps run() for use as a FastAPI background task.
    Writes final status to GCS regardless of success or failure.
    """
    try:
        summary = await run(run_id)
        await status_store.write_final(
            run_id=run_id,
            status="completed",
            finished_at=_now_z(),
            summary=summary,
            error=None,
        )
    except Exception as exc:
        log = logging.getLogger(__name__)
        log.error({"event": "background_task_error", "run_id": run_id, "error": repr(exc)})
        await status_store.write_final(
            run_id=run_id,
            status="failed",
            finished_at=_now_z(),
            summary=None,
            error=repr(exc)[:2048],
        )
```

### `async def run(run_id: str) -> dict` (returns summary dict)

This is the async rewrite of the original `run()`. Key changes:

1. Use `async with ChapiClient(...) as client:` for the HTTP client lifecycle.

2. `async for bundle in client.iter_patient_bundles(initial_url):` for pagination.

3. Process patients within each bundle concurrently:

   ```python
   patients = [
       entry["resource"]
       for entry in bundle.get("entry", []) or []
       if entry.get("resource") and entry["resource"].get("resourceType") == "Patient"
   ]
   await asyncio.gather(*[_process_patient(p, client, ctx) for p in patients])
   ```

   where `ctx` is a small dataclass/dict carrying the mutable counters and config.

4. `_process_patient` acquires `_fhir_sem` only around the `await client.put_patient()`
   call, not around the entire function.

5. Checkpoint write (`await cp.write(state)`) and delete (`await cp.delete()`) are now
   awaited (they're async in Phase 3).

6. When the checkpoint interval triggers, call `await status_store.write_final(...)` with
   `status="checkpointed"` before returning.

7. Return the summary dict (same fields as the original `run_summary` log event).

**Counter safety**: counters (`examined`, `changed`, etc.) are plain ints mutated by
`_process_patient`. In asyncio, mutations between `await` points are safe on the single
event loop thread — no locks needed. Keep them as `nonlocal` ints inside `run()`.

**Keep all existing logic unchanged:**

- `_is_noop()` — identical
- `_kept_address_summary()` — identical
- `_JsonFormatter` + `_setup_logging()` — identical
- `_require_env()` — identical
- Checkpoint resume logic — identical (now uses `await cp.read()`)
- Limit / PATIENT_ID env var handling — identical
- All log events and their fields — identical

### Update `deploy.sh`

Switch from `gcloud run jobs deploy` to `gcloud run services deploy`.

Diff from current:

- Replace `gcloud run jobs deploy "$JOB_NAME" \` with `gcloud run services deploy "$SERVICE_NAME" \`
- Replace `--task-timeout 3600 \` with `--timeout 3600 \`
- Replace `--max-retries 0 \` with `--min-instances 1 \`
- Add `--port 8080 \`
- Add `--concurrency 80 \`
- Add `FHIR_CONCURRENCY=20` to `--set-env-vars`
- Update the post-deploy echo instructions to use `curl` trigger instead of
  `gcloud run jobs execute`

Also rename `JOB_NAME` variable to `SERVICE_NAME` throughout.

Add a `Procfile` (new file) with:

```txt
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```

### Write `tests/test_main.py`

Use `fastapi.testclient.TestClient` (synchronous) for endpoint tests.
Mock `StatusStore`, `ChapiClient`, and `Checkpoint` so no real GCS or HTTP calls.

**Required test cases:**

1. `test_health` — GET /health returns `200 {"status": "ok"}`.

2. `test_run_returns_202_with_run_id` — mock `StatusStore.write_running` and
   `_run_background`; POST /run returns 202, body has `run_id` (non-empty string)
   and `status_url` starting with `/status/`.

3. `test_status_found` — mock `StatusStore.read` returning a dict; GET
   /status/abc123 returns 200 with that dict.

4. `test_status_not_found` — mock `StatusStore.read` returning None; GET
   /status/abc123 returns 404.

5. `test_run_background_writes_completed_on_success` — call `_run_background`
   directly (not via endpoint); mock `run()` to return a summary dict; assert
   `status_store.write_final` called with `status="completed"`.

6. `test_run_background_writes_failed_on_exception` — mock `run()` to raise
   `RuntimeError("boom")`; assert `status_store.write_final` called with
   `status="failed"` and `error` containing `"boom"`.

Run `pytest tests/ -v` (all tests from all phases) and confirm every test passes
before declaring Phase 4 complete.

**When done:** Update `status.md` — mark all Refactor Phase 4 checkboxes, write a
one-or-two-sentence note under "Notes from agent". Stop — do not start Phase 5.

---

## PHASE 5 — Final Validation

1. Run the full test suite:

   ```bash
   pytest tests/ -v --tb=short
   ```

   All tests must pass. Fix any failures before continuing.

2. Verify imports are clean:

   ```bash
   MODE=incremental TENANT=test SERVER_KIND=chapi FHIR_URL=http://x CHAPI_API_KEY=x \
     python -c "from main import app; print('OK')"
   ```

   Must print `OK` with no errors or warnings.

3. Confirm `dedup.py` is unchanged: `git diff dedup.py` must show no changes.

4. Confirm `hapi_patient_duplicate_address/` is unchanged:
   `git diff hapi_patient_duplicate_address/` must show no changes.

5. Print a summary of every file changed and why.

**When done:** Update `status.md` — mark all Refactor Phase 5 checkboxes, write the
change summary under "Notes from agent". Do NOT run `deploy.sh` — the human deploys.
Stop.

---

## Constraints & style rules

- No new abstractions beyond what this prompt specifies.
- No comments that explain what the code does — only add a comment if the WHY is
  non-obvious (e.g., "asyncio.gather is safe here because the semaphore is held only
  around the PUT call, not the full coroutine").
- Do not add retry logic, circuit breakers, or token refresh — not in scope.
- Do not create README or documentation files.
- Keep log event field names identical to the original (`event`, `run_id`, `tenant`,
  `server`, `mode`, `dry_run`, `patient_id`, etc.) — log consumers depend on these.
- `asyncio_mode = "auto"` must be set in `pytest.ini` or `pyproject.toml` so all
  async test functions run without `@pytest.mark.asyncio` decorators.
