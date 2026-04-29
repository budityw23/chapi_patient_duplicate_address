# CHAPI Executor Prompt — FastAPI + asyncio Refactor

This file tells **you (the human)** how to drive the refactor using Claude Code
(Sonnet 4.6). Each phase has a prompt you paste into Claude Code. Wait for the
agent to finish and update `status.md` before moving to the next phase.

The full spec is in `REFACTOR_PROMPT.md`. The agent reads it directly — you do
not need to paste its contents.

---

## Setup

Open Claude Code inside `chapi_patient_duplicate_address/`:

```bash
cd /home/budi/code/sphere_project/patient_duplicate_fields/chapi_patient_duplicate_address
claude --model claude-sonnet-4-6
```

No worktree is needed — work directly on `main`.

---

## Phase prompts

Paste one at a time. The agent reads `REFACTOR_PROMPT.md` for the full spec and
`status.md` to know what is already done. Wait for `status.md` to be updated
before sending the next prompt.

---

### Refactor Phase 1 — Dependencies & Setup

```text
Read REFACTOR_PROMPT.md and status.md in this folder.

The original implementation (CLI batch job) is complete. You are starting the
FastAPI + asyncio refactor described in REFACTOR_PROMPT.md. Do not read or
follow plan.md — it is the spec for the original implementation and its
instructions conflict with the refactor (e.g. it says not to modify deploy.sh,
keep requests, and use a __main__ entrypoint — all of which the refactor
changes).

Implement Refactor Phase 1 only:
- Update requirements.txt (remove requests, add fastapi, uvicorn[standard], httpx, keep google-cloud-storage)
- Create .python-version with content: 3.11
- Create requirements-dev.txt with: pytest, pytest-asyncio, respx

After creating the files, verify with:
  pip install -r requirements.txt -r requirements-dev.txt

Update status.md: mark all Refactor Phase 1 checkboxes as done, write a brief
note under "Notes from agent". Stop — do not start Phase 2.
```

---

### Refactor Phase 2 — Async FHIR Client

```text
Read status.md to confirm Refactor Phase 1 is complete. Read REFACTOR_PROMPT.md
for the Phase 2 spec.

Implement Refactor Phase 2 only:
- Rewrite fhir_client.py using httpx.AsyncClient (exact signatures in REFACTOR_PROMPT.md)
- Create tests/ directory and tests/test_fhir_client.py with all 8 required test cases
- Add pytest.ini (or pyproject.toml) with asyncio_mode = "auto"

Run tests before marking complete:
  pytest tests/test_fhir_client.py -v

All 8 tests must pass. Fix any failures before updating status.md.

Update status.md: mark all Refactor Phase 2 checkboxes, write a note. Stop —
do not start Phase 3.
```

---

### Refactor Phase 3 — Async Checkpoint + StatusStore

```text
Read status.md to confirm Refactor Phases 1 and 2 are complete. Read
REFACTOR_PROMPT.md for the Phase 3 spec.

Implement Refactor Phase 3 only:
- Update checkpoint.py: wrap read, write, delete in asyncio.to_thread()
- Create status_store.py: StatusStore class with write_running, write_final, read
- Create tests/test_checkpoint.py with all 4 required test cases
- Create tests/test_status_store.py with all 5 required test cases

Run tests before marking complete:
  pytest tests/test_checkpoint.py tests/test_status_store.py -v

All 9 tests must pass. Fix any failures before updating status.md.

Update status.md: mark all Refactor Phase 3 checkboxes, write a note. Stop —
do not start Phase 4.
```

---

### Refactor Phase 4 — FastAPI App + Async Main Logic

```text
Read status.md to confirm Refactor Phases 1–3 are complete. Read
REFACTOR_PROMPT.md for the Phase 4 spec.

Implement Refactor Phase 4 only:
- Rewrite main.py as a FastAPI app (endpoints, lifespan, background task, async run loop)
- Update deploy.sh: switch from Cloud Run Job to Cloud Run Service as specified
- Create Procfile: web: uvicorn main:app --host 0.0.0.0 --port $PORT
- Create tests/test_main.py with all 6 required test cases

Key constraints from the spec (do not deviate):
- POST /run returns 202 immediately; does NOT block until the run finishes
- Semaphore acquired only around await client.put_patient(), not the full coroutine
- All original log event field names must be preserved
- deploy.sh switches gcloud run jobs deploy to gcloud run services deploy

Run the full test suite before marking complete:
  pytest tests/ -v

All tests (from all phases) must pass. Fix any failures before updating status.md.

Update status.md: mark all Refactor Phase 4 checkboxes, write a note. Stop —
do not start Phase 5.
```

---

### Refactor Phase 5 — Final Validation

```text
Read status.md to confirm Refactor Phases 1–4 are complete. Read
REFACTOR_PROMPT.md for the Phase 5 spec.

Run the final validation checks in order:

1. Full test suite:
   pytest tests/ -v --tb=short
   Must be zero failures.

2. Import check with dummy env vars:
   MODE=incremental TENANT=test SERVER_KIND=chapi FHIR_URL=http://x CHAPI_API_KEY=x \
     python -c "from main import app; print('OK')"
   Must print OK with no errors.

3. Verify dedup.py is unchanged:
   git diff dedup.py
   Must be empty.

4. Verify hapi_patient_duplicate_address/ is unchanged:
   git diff ../hapi_patient_duplicate_address/
   Must be empty.

5. Print a summary of every file you changed or created and why.

Do NOT run deploy.sh — the human will deploy after reviewing your work.

Update status.md: mark all Refactor Phase 5 checkboxes, write a note including
the change summary. Stop.
```

---

## After all phases complete

Review the change summary from Phase 5, then deploy:

```bash
./deploy.sh purbalingga
```

Set up Cloud Scheduler to `POST https://<service-url>/run` every hour.

---

## If you need to resume mid-refactor

Open Claude Code in the folder again and paste the next unchecked phase prompt.
The agent reads `status.md` to confirm prior phases are done.

If you are unsure which phase is next:

```text
Read status.md and tell me the next incomplete refactor phase. Do not implement anything yet.
```
