# CHAPI Patient Duplicate Address — Implementation Plan

You are a Claude Code session validating and deploying the CHAPI patient
address dedup service. Work **one phase at a time**. After each phase
update `status.md` and stop. Do **not** run `deploy.sh` unless the phase
explicitly says to.

---

## What this folder contains

| File | Purpose |
| --- | --- |
| `main.py` | FastAPI service. POST /run triggers dedup, GET /status/{run_id} polls progress. |
| `dedup.py` | Pure function `dedup_addresses(addresses)`. Self-tests run at import time. |
| `fhir_client.py` | `ChapiClient`: X-API-Key auth, pagination, GET, PUT-If-Match. |
| `checkpoint.py` | GCS-backed checkpoint for backfill resume. |
| `status_store.py` | GCS-backed run status (running → completed/failed). |
| `requirements.txt` | `fastapi`, `uvicorn[standard]`, `httpx`, `google-cloud-storage`. |
| `requirements-dev.txt` | Test deps (`pytest`, etc.). |
| `tests/` | pytest suite (23 tests across `dedup`, `fhir_client`, `checkpoint`, `status_store`, FastAPI endpoints). |
| `deploy.sh` | Cloud Run Service deploy script. Do not modify unless the phase says to. |

---

## Env vars (deployed contract)

| Var | Required | Notes |
| --- | --- | --- |
| `MODE` | yes | `backfill` \| `incremental` |
| `DRY_RUN` | yes | `true` (default) \| `false` |
| `TENANT` | yes | `purbalingga` |
| `SERVER_KIND` | yes | always `chapi` |
| `FHIR_URL` | yes | `https://fhir-server.dataspheres.purbalinggakab.go.id/fhir` |
| `CHAPI_API_KEY` | yes | API key for FHIR server auth |
| `CHECKPOINT_BUCKET` | optional | GCS bucket for checkpoint/status (default: `dedup-patient`) |
| `LIMIT` | optional | cap patients examined |
| `PATIENT_ID` | optional | process a single patient only |

---

## What was already built (before Phase 1)

All code changes below are **already implemented and committed**. Phase 1
onwards is validation and deployment only.

### `dedup.py` fixes

1. **URL mismatch** (`dedup.py:19`, `dedup.py:84`) — `_get_admin_code` and
   `_sort_admin_code_extensions` used `ext.get("url") == "administrativeCode"`
   (short name). Real FHIR data uses the full URL
   `https://fhir.kemkes.go.id/r4/StructureDefinition/administrativeCode`.
   Fixed with `ext.get("url", "").endswith("administrativeCode")`.

2. **Value key mismatch** (`dedup.py:20`) — `s.get("valueString")` only.
   Real FHIR data uses `valueCode`. Fixed with
   `s.get("valueString") or s.get("valueCode")`.
   Impact: before these two fixes, admin codes were silently ignored for all
   real FHIR data — village-coded addresses had no score advantage, and two
   addresses with the same line but different villages would be incorrectly
   merged.

3. **Score ordering** (`dedup.py:125`) — `kept_indices` was sorted by
   original array index, so the most complete address was not guaranteed
   to be `address[0]` in the PUT body. Fixed to sort by
   `(-score, original_index)`.

### `main.py` fix

4. **`_is_noop` URL fix** (`main.py:91`) — same
   `endswith("administrativeCode")` fix applied to the noop check so
   extension sub-order rewrites are correctly detected.

### Dry-test results (against exploration snapshot, 155 patients)

| Metric | Value |
| --- | --- |
| Patients examined | 155 |
| Would change | 105 |
| Skipped (already clean) | 50 |
| Addresses would be dropped | 232 |

---

## Phase 1 — Local dry-run validation (already complete)

**Tasks:**

1. Install dev deps and run the full pytest suite (gate before any deploy):

   ```bash
   pip install -r requirements-dev.txt
   pytest tests/ -v
   ```

   All 23 tests must pass.

2. Run the dedup self-tests (import-time assertions):

   ```bash
   python3 -c "import dedup; print('ok')"
   ```

3. Run a dry simulation against
   `../exploration/out/chapi_multi_address_patients.json` and print a
   summary (examined / would_change / skipped / addresses_dropped).

4. Spot-check 3 patients: confirm address[0] is highest-score, different
   villages are kept separate, village-coded address wins over no-code.

Self-tests pass; dry simulation confirmed 105/155 patients would change,
232 addresses dropped, 50 skipped. Spot-checks confirmed address[0] is
highest-score, different villages kept separate, village-coded address
wins over no-code. See `status.md` for details.

---

## Phase 2 — Deploy with DRY_RUN=true, MODE=backfill, LIMIT=1000

**Goal:** Deploy the updated code to Cloud Run and execute a dry run
against live CHAPI FHIR to confirm real patient data matches expectations.

**Tasks:**

1. Deploy the service:

   ```bash
   cd chapi_patient_duplicate_address
   ./deploy.sh purbalingga
   ```

2. Update env vars for backfill with bounded sample:

   ```bash
   gcloud run services update dedup-address-chapi-purbalingga \
     --update-env-vars MODE=backfill,LIMIT=1000 \
     --region asia-southeast2 --project stellar-orb-451904-d9
   ```

3. Trigger a run:

   ```bash
   SERVICE_URL=$(gcloud run services describe dedup-address-chapi-purbalingga \
     --region asia-southeast2 --project stellar-orb-451904-d9 \
     --format 'value(status.url)')

   curl -s -X POST "$SERVICE_URL/run" \
     -H "Authorization: Bearer $(gcloud auth print-identity-token)"
   ```

   Save the returned `run_id`. Poll until complete:

   ```bash
   curl -s "$SERVICE_URL/status/<run_id>" \
     -H "Authorization: Bearer $(gcloud auth print-identity-token)"
   ```

4. Fetch and review logs **scoped to this run_id** (replace `<RUN_ID>` with
   the value returned by `POST /run`):

   ```bash
   RUN_ID=<run_id from POST /run>

   # Run summary
   gcloud logging read \
     "resource.type=\"cloud_run_revision\"
      AND resource.labels.service_name=\"dedup-address-chapi-purbalingga\"
      AND jsonPayload.run_id=\"$RUN_ID\"
      AND jsonPayload.event=\"run_summary\"" \
     --project stellar-orb-451904-d9 --limit 1 --format json
   ```

   Confirm: `dry_run=true`, no errors, `patients_changed` is non-zero.
   (Alternatively, `GET /status/$RUN_ID` returns the same summary.)

5. Pull 5 `patient_change` log entries (also scoped to `run_id`) and verify:

   ```bash
   gcloud logging read \
     "resource.type=\"cloud_run_revision\"
      AND resource.labels.service_name=\"dedup-address-chapi-purbalingga\"
      AND jsonPayload.run_id=\"$RUN_ID\"
      AND jsonPayload.event=\"patient_change\"" \
     --project stellar-orb-451904-d9 --limit 5 --format json
   ```

   - `count_after <= count_before`
   - `kept_address.admin_code` has village where available

6. Update `status.md` — Phase 2 complete.

---

## Phase 3 — Smoke test with DRY_RUN=false, LIMIT=10

**Goal:** Write real changes for 10 patients, verify the FHIR server
accepted them and the data looks correct.

**Tasks:**

1. Update env vars:

   ```bash
   gcloud run services update dedup-address-chapi-purbalingga \
     --update-env-vars DRY_RUN=false,LIMIT=10 \
     --region asia-southeast2 --project stellar-orb-451904-d9
   ```

2. Trigger a run and wait for completion (same curl as Phase 2). Save the
   returned `run_id`.

3. Fetch logs **scoped to this run_id** and collect the `patient_id` values
   from `patient_change` events:

   ```bash
   gcloud logging read \
     "resource.type=\"cloud_run_revision\"
      AND resource.labels.service_name=\"dedup-address-chapi-purbalingga\"
      AND jsonPayload.run_id=\"$RUN_ID\"
      AND jsonPayload.event=\"patient_change\"" \
     --project stellar-orb-451904-d9 --limit 20 --format json
   ```

4. For each changed patient, fetch from FHIR and verify:
   - `address` array length matches `count_after` from the log
   - `address[0]` has the highest admin code depth (village if any)
   - Different village codes are NOT merged (kept as separate addresses)
   - No data was invented — line text and admin codes match the original

5. Reset `LIMIT` and `DRY_RUN` after validation:

   ```bash
   gcloud run services update dedup-address-chapi-purbalingga \
     --update-env-vars DRY_RUN=true,LIMIT= \
     --region asia-southeast2 --project stellar-orb-451904-d9
   ```

6. Update `status.md` — Phase 3 complete.

---

## Phase 4 — Production batch run, LIMIT=15000

**Goal:** Process up to 15,000 patients with real writes. The first ~1000
(already cleaned in Phase 3) will be skipped quickly; the remaining ~14,000
are new.

**Important — LIMIT granularity:** `LIMIT` breaks after the current page
boundary (`PAGE_SIZE=1000`), so `LIMIT=15000` processes up to 15 pages.
If the run exceeds 55 minutes (`CHECKPOINT_INTERVAL_S=3300`), a checkpoint
is saved so subsequent runs resume from where this one stopped.

**Tasks:**

1. Set `DRY_RUN=false`, `LIMIT=15000`:

   ```bash
   gcloud run services update dedup-address-chapi-purbalingga \
     --update-env-vars DRY_RUN=false,LIMIT=15000 \
     --region asia-southeast2 --project stellar-orb-451904-d9
   ```

2. Trigger a run and wait for completion (may take several minutes).
   Save the returned `run_id`. Poll `GET /status/$RUN_ID` until status
   is `completed` or `checkpointed`.

3. Fetch the `run_summary` log **scoped to this run_id** (or `GET
   /status/$RUN_ID`) and confirm:
   - `dry_run=false`
   - `patients_error` < 1% of `patients_examined`
   - `patients_changed` and `addresses_dropped_total` are non-zero

   ```bash
   gcloud logging read \
     "resource.type=\"cloud_run_revision\"
      AND resource.labels.service_name=\"dedup-address-chapi-purbalingga\"
      AND jsonPayload.run_id=\"$RUN_ID\"
      AND jsonPayload.event=\"run_summary\"" \
     --project stellar-orb-451904-d9 --limit 1 --format json
   ```

4. Spot-check 5 patients from the log against live FHIR and verify:
   - `address[0]` is the richest address (deepest admin code)
   - Different village codes are NOT merged (kept as separate addresses)
   - No data was invented

5. If status is `checkpointed` (not `completed`), note the checkpoint
   position — subsequent runs will resume from there.

6. Reset `DRY_RUN=true` (keep `LIMIT=15000` for future batches):

   ```bash
   gcloud run services update dedup-address-chapi-purbalingga \
     --update-env-vars DRY_RUN=true \
     --region asia-southeast2 --project stellar-orb-451904-d9
   ```

7. Update `status.md` — Phase 4 complete.

---

## Phase 5 — Post-batch verification and progress report

**Goal:** Verify the batch results against live FHIR and document
progress vs remaining patients. The total dataset is >1M patients,
so this phase also tracks how much work remains.

**Tasks:**

1. Fetch run_summary from the Phase 4 `run_id` (via `GET /status/$RUN_ID`
   or logs). Record: `patients_examined`, `patients_changed`,
   `addresses_dropped_total`, `patients_error`.

2. Pick 5 patients that were changed in Phase 4 and fetch from FHIR.
   Verify:
   - Address count matches `count_after`
   - `address[0]` has the deepest admin code
   - Different village codes are NOT merged
   - No data was invented

3. Document in `status.md` under "Result summary":
   - Total patients processed so far (Phase 3 + Phase 4)
   - Total changed, total addresses dropped
   - Checkpoint state: can subsequent runs resume?
   - Estimated remaining (>1M total minus processed)
   - If more batches are needed, note that Phase 4 can be re-run —
     the checkpoint will resume from where it left off.

4. Update `status.md` — Phase 5 complete.
