# CHAPI Executor Prompt

Paste one prompt at a time into Claude Code CLI. Wait for the agent to
finish and update `status.md` before sending the next phase.

---

## Phase 1 — Local dry-run validation

```text
Read plan.md and status.md in this folder (chapi_patient_duplicate_address).

Execute Phase 1 only:
1. Install dev deps and run the full pytest suite (gate before any deploy):
   pip install -r requirements-dev.txt
   pytest tests/ -v
   All 23 tests must pass.
2. Run the dedup self-tests: python3 -c "import dedup; print('ok')"
3. Run a dry simulation across ../exploration/out/chapi_multi_address_patients.json —
   for each patient compute dedup_addresses (from dedup.py),
   print a summary (examined / would_change / skipped / addresses_dropped)
   and one example of a change.
4. Spot-check 3 patients manually: confirm address[0] has highest score,
   different villages are kept separate, village-coded address wins over no-code.

When done, update status.md to mark Phase 1 complete with a one-sentence note.
Stop after Phase 1 — do not start Phase 2.
```

---

## Phase 2 — Deploy with DRY_RUN=true, MODE=backfill, LIMIT=1000

```text
Read plan.md and status.md in this folder (chapi_patient_duplicate_address).
Confirm Phase 1 is complete in status.md, then read the Phase 2 tasks in plan.md.

Execute Phase 2 only:
1. Run ./deploy.sh purbalingga from inside chapi_patient_duplicate_address.
2. Update env vars: MODE=backfill, LIMIT=1000.
3. Trigger a run via POST /run; SAVE the returned run_id; poll GET /status/{run_id} until completion.
4. Fetch the run_summary log AND 5 patient_change log entries — every gcloud
   logging query must include `jsonPayload.run_id="<run_id>"` so you only see
   this execution. Alternatively use GET /status/{run_id} for the summary.
5. Verify: dry_run=true, no errors, kept_address has village where available.

Use the GCP credential at ../budi-triwibowo-editor-credential.json if needed.
Project: stellar-orb-451904-d9, region: asia-southeast2,
service: dedup-address-chapi-purbalingga.

Service URL: https://dedup-address-chapi-purbalingga-343467406062.asia-southeast2.run.app

When done, update status.md to mark Phase 2 complete.
Stop after Phase 2 — do not start Phase 3.
```

---

## Phase 3 — Smoke test DRY_RUN=false LIMIT=10

```text
Read plan.md and status.md in this folder (chapi_patient_duplicate_address).
Confirm Phase 2 is complete in status.md, then read the Phase 3 tasks in plan.md.

Execute Phase 3 only:
1. Update the service with DRY_RUN=false and LIMIT=10.
2. Trigger a run via POST /run; SAVE the returned run_id; wait for completion.
3. Collect patient_id values from patient_change log entries — scope the
   query with `jsonPayload.run_id="<run_id>"` so you only see this execution.
4. Fetch each changed patient from FHIR and verify:
   - address count matches count_after from the log
   - address[0] has the deepest admin code (village if any exists)
   - different village codes are NOT merged (kept as separate addresses)
   - no data was invented
5. After verification, reset DRY_RUN=true and clear LIMIT.

FHIR URL: https://fhir-server.dataspheres.purbalinggakab.go.id/fhir
Use the GCP credential at ../budi-triwibowo-editor-credential.json.
Project: stellar-orb-451904-d9, region: asia-southeast2.
Service URL: https://dedup-address-chapi-purbalingga-343467406062.asia-southeast2.run.app

When done, update status.md to mark Phase 3 complete.
Stop after Phase 3 — do not start Phase 4.
```

---

## Phase 4 — Production batch run LIMIT=15000

```text
Read plan.md and status.md in this folder (chapi_patient_duplicate_address).
Confirm Phase 3 is complete in status.md, then read the Phase 4 tasks in plan.md.

Execute Phase 4 only:
1. Update the service with DRY_RUN=false and LIMIT=15000.
2. Trigger a run via POST /run; SAVE the returned run_id; poll
   GET /status/{run_id} until status is completed or checkpointed.
   Note: LIMIT has page-level granularity (PAGE_SIZE=1000), so this
   processes up to 15 pages. The first ~1000 patients were already
   cleaned in Phase 3 and will be skipped quickly.
3. Fetch the run_summary (GET /status/{run_id} or scoped gcloud log).
   Confirm: dry_run=false, patients_error < 1%, patients_changed non-zero.
4. Spot-check 5 patients from patient_change logs against live FHIR:
   - address[0] is the richest address (deepest admin code)
   - different village codes are NOT merged (kept as separate addresses)
   - no data was invented
5. If status is "checkpointed", note the checkpoint position — subsequent
   runs will resume from there.
6. Reset DRY_RUN=true (keep LIMIT=15000 for future batches).

Use the GCP credential at ../budi-triwibowo-editor-credential.json.
Project: stellar-orb-451904-d9, region: asia-southeast2.
Service URL: https://dedup-address-chapi-purbalingga-343467406062.asia-southeast2.run.app

When done, update status.md to mark Phase 4 complete.
Stop after Phase 4 — do not start Phase 5.
```

---

## Phase 5 — Post-batch verification and progress report

```text
Read plan.md and status.md in this folder (chapi_patient_duplicate_address).
Confirm Phase 4 is complete in status.md, then read the Phase 5 tasks in plan.md.

Execute Phase 5 only — verification only, no writes:
1. Fetch the Phase 4 run_summary (GET /status/{run_id} using the run_id
   from Phase 4 in status.md). Record patients_examined, patients_changed,
   addresses_dropped_total, patients_error.
2. Pick 5 patients changed in Phase 4 and fetch from FHIR. Verify:
   - address count matches count_after
   - address[0] has the deepest admin code
   - different village codes are NOT merged
   - no data was invented
3. Write a result summary in status.md under "Result summary":
   - Total patients processed so far (Phase 3 + Phase 4)
   - Total changed, total addresses dropped
   - Checkpoint state (can subsequent runs resume?)
   - Estimated remaining (>1M total minus processed)
   - Note if Phase 4 can be re-run to continue processing

FHIR URL: https://fhir-server.dataspheres.purbalinggakab.go.id/fhir
Use the GCP credential at ../budi-triwibowo-editor-credential.json.
Service URL: https://dedup-address-chapi-purbalingga-343467406062.asia-southeast2.run.app

When done, update status.md to mark Phase 5 complete.
```

---

## If you need to resume mid-phase

```text
Read status.md and tell me the next unchecked item. Do not implement anything yet.
```

---

## If you are unsure which phase is next

```text
Read plan.md and status.md in chapi_patient_duplicate_address. Tell me
which phase is next and what the first task is. Do not implement anything.
```
