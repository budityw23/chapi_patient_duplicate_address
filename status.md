# CHAPI Implementation Status

**Last updated:** not started
**Last updated by:** —

The agent updates this file at the end of each phase. Use this file to
remember where you stopped if the implementation spans multiple sessions.

---

## Phase 1 — Address dedup function

- [ ] `requirements.txt` created with `requests` + `google-cloud-storage`
- [ ] `dedup.py` exports `dedup_addresses(addresses) -> dict`
- [ ] Module-level asserts cover all 5 required cases
- [ ] `python -c "import dedup"` runs cleanly (asserts pass at import)

**Notes from agent:**
_(empty)_

---

## Phase 2 — FHIR client + GCS checkpoint

- [ ] `fhir_client.py`: `ChapiClient` with `iter_patient_bundles`, `get_patient`, `put_patient`
- [ ] `_page_token` rebuild preserves other query params (e.g. `_lastUpdated`)
- [ ] `checkpoint.py`: `Checkpoint` with `read` / `write` / `delete` (`delete` is idempotent)
- [ ] Smoke verified: bundle fetch, single GET, GCS round trip on `_smoke` key
- [ ] Smoke script removed (no scratch files left behind)

**Notes from agent:**
_(empty)_

---

## Phase 3 — Main loop, dry-run smoke

- [ ] `main.py` reads env, validates required vars, fails fast on missing
- [ ] JSON-formatted stdout logging (stdlib `logging`, no `google-cloud-logging` lib)
- [ ] Search URL correct for `MODE=backfill` and `MODE=incremental`
- [ ] Backfill checkpoint resume reads from GCS at startup
- [ ] Per-patient flow: skip ≤1, skip no-change, emit `patient_change` event
- [ ] Counters wired (examined, changed, skipped, 412, error, addresses_dropped)
- [ ] `LIMIT` honored
- [ ] `run_summary` event emitted at exit
- [ ] Local dry-run with `LIMIT=200` produces ≥1 valid `patient_change` event
- [ ] Eyeballed ~10 events: kept_address.admin_code depth ≥ each dropped entry's

**Notes from agent:**
_(empty)_

---

## Phase 4 — Live PUT smoke

- [ ] Real PUT-If-Match wired into the per-patient flow
- [ ] 412 / 4xx / 5xx handling emits correct `error_type` and increments counter
- [ ] Exception handling: per-patient catch, emit `error_type: exception`, never abort
- [ ] Backfill checkpoint write at page boundary when elapsed > 3300s
- [ ] Local run with `MODE=backfill DRY_RUN=false LIMIT=5` (or LIMIT=50 if needed) applied real changes
- [ ] One changed patient verified via `curl` GET — `len(address) == count_after`

**Notes from agent:**
_(empty)_

---

## Phase 5 — Pre-deploy validation

- [ ] All required files present; no `_smoke.py`, `__pycache__/`, or scratch
- [ ] `requirements.txt` lists both deps
- [ ] `main.py` has `if __name__ == "__main__":` entrypoint
- [ ] `deploy.sh` byte-identical to baseline
- [ ] Final regression dry-run (`LIMIT=50`) clean
- [ ] STOPPED before running `deploy.sh` — handed off to human

**Notes from agent:**
_(empty)_

---

## Ready to deploy

When Phase 5 is checked, the human runs:

```bash
./deploy.sh purbalingga
```

(and optionally later `./deploy.sh lombok-barat` once URL/key for that
tenant are known.)
