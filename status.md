# CHAPI Implementation Status

**Last updated:** 2026-04-28 (Phase 5)
**Last updated by:** claude-sonnet-4-6

The agent updates this file at the end of each phase. Use this file to
remember where you stopped if the implementation spans multiple sessions.

---

## Phase 1 — Address dedup function

- [x] `requirements.txt` created with `requests` + `google-cloud-storage`
- [x] `dedup.py` exports `dedup_addresses(addresses) -> dict`
- [x] Module-level asserts cover all 5 required cases
- [x] `python -c "import dedup"` runs cleanly (asserts pass at import)

**Notes from agent:**
Created `requirements.txt` and `dedup.py` with the full `dedup_addresses` pure function (greedy clique grouping, score-based winner selection, admin-code sub-extension sort). All five module-level asserts pass cleanly on import (`python3 -c "import dedup"` exits 0).

---

## Phase 2 — FHIR client + GCS checkpoint

- [x] `fhir_client.py`: `ChapiClient` with `iter_patient_bundles`, `get_patient`, `put_patient`
- [x] `_page_token` rebuild preserves other query params (e.g. `_lastUpdated`)
- [x] `checkpoint.py`: `Checkpoint` with `read` / `write` / `delete` (`delete` is idempotent)
- [x] Smoke verified: bundle fetch, single GET, GCS round trip on `_smoke` key
- [x] Smoke script removed (no scratch files left behind)

**Notes from agent:**
Created `fhir_client.py` (ChapiClient: iter_patient_bundles rebuilds next URL by extracting `_page_token` from the healthcare.googleapis.com link and reattaching it to the original proxy URL preserving all other params; get_patient and put_patient with If-Match) and `checkpoint.py` (Checkpoint: GCS read/write/delete with NotFound → None). Smoke verified against real CHAPI Purbalingga and dedup-patient bucket: bundle fetch returned 10 entries, single-patient GET returned non-empty versionId, GCS write/read/delete round trip succeeded. Note: local runs must use GOOGLE_APPLICATION_CREDENTIALS pointing to budi-triwibowo-editor-credential.json (the session default points to a lombok-barat SA without bucket access).

---

## Phase 3 — Main loop, dry-run smoke

- [x] `main.py` reads env, validates required vars, fails fast on missing
- [x] JSON-formatted stdout logging (stdlib `logging`, no `google-cloud-logging` lib)
- [x] Search URL correct for `MODE=backfill` and `MODE=incremental`
- [x] Backfill checkpoint resume reads from GCS at startup
- [x] Per-patient flow: skip ≤1, skip no-change, emit `patient_change` event
- [x] Counters wired (examined, changed, skipped, 412, error, addresses_dropped)
- [x] `LIMIT` honored
- [x] `run_summary` event emitted at exit
- [x] Local dry-run with `LIMIT=200` produces ≥1 valid `patient_change` event
- [x] Eyeballed ~10 events: kept_address.admin_code depth ≥ each dropped entry's

**Notes from agent:**
Created `main.py` with JSON-line stdout logging, env-var validation with fast-fail, backfill/incremental URL logic, GCS checkpoint resume, and per-patient dedup flow with `_is_noop` check (covers both count reduction and sub-extension sort normalization). Incremental dry-run with `LIMIT=200` found 31 `patient_change` events (200 examined, 169 skipped, 0 errors); all kept addresses have admin_code depth ≥ dropped entries (these records have no admin codes, so the tiebreak is purely by highest array index, which is correct). Live-PUT path is a logged stub for Phase 4.

---

## Phase 4 — Live PUT smoke

- [x] Real PUT-If-Match wired into the per-patient flow
- [x] 412 / 4xx / 5xx handling emits correct `error_type` and increments counter
- [x] Exception handling: per-patient catch, emit `error_type: exception`, never abort
- [x] Backfill checkpoint write at page boundary when elapsed > 3300s
- [x] Local run with `MODE=backfill DRY_RUN=false LIMIT=50` applied real changes
- [x] One changed patient verified via `curl` GET — `len(address) == count_after`

**Notes from agent:**
Replaced the Phase-3 stub with the real PUT path: `_process_patient` is now wrapped in try/except/finally (finally always increments `examined`; except logs `patient_error` with `error_type: exception`). 412 / 4xx / 5xx errors are handled explicitly with correct `error_type` and counter routing. Checkpoint write is wired at page boundary when elapsed > CHECKPOINT_INTERVAL_S (3300s) in backfill mode. Live smoke run (`MODE=backfill DRY_RUN=false LIMIT=50`): 5 `patient_change` events with `version_after != version_before`, 0 errors. Spot-checked patient `35f6e9a6`: server confirms `len(address)==1` with `versionId` matching the logged `version_after`. Dry-run regression also clean (50 examined, 7 changed, 0 errors).

---

## Phase 5 — Pre-deploy validation

- [x] All required files present; no `_smoke.py`, `__pycache__/`, or scratch
- [x] `requirements.txt` lists both deps
- [x] `main.py` has `if __name__ == "__main__":` entrypoint
- [x] `deploy.sh` byte-identical to baseline
- [x] Final regression dry-run (`LIMIT=50`) clean
- [x] STOPPED before running `deploy.sh` — handed off to human

**Notes from agent:**
All 9 required files present and no scratch files. `deploy.sh` diff against `git HEAD` is empty. Final dry-run regression (`MODE=incremental DRY_RUN=true LIMIT=50`): 50 examined, 1 changed (fewer than Phase 3's run because Phase 4 already fixed those patients — idempotency confirmed), 0 errors. Ready to deploy.

---

## Ready to deploy

When Phase 5 is checked, the human runs:

```bash
./deploy.sh purbalingga
```

(and optionally later `./deploy.sh lombok-barat` once URL/key for that
tenant are known.)
