# CHAPI Patient Duplicate Address — Implementation Plan

You are a Claude Code session (Sonnet 4.6) implementing the CHAPI dedup
resolver. Build it **one phase at a time**. After each phase, update
`status.md` and stop. Do **not** run `deploy.sh` — the human reviews and
deploys.

The architectural spec is at
`../.claude/deduplication_rule/dedup-patient-fields-prompt.md`. Read it
first; this file is the per-phase build checklist for this deployment.

## What this folder will contain when done

| File | Purpose |
| --- | --- |
| `main.py` | Entrypoint. Reads env, runs the resolver loop. |
| `dedup.py` | Pure function `dedup_addresses(addresses)`. |
| `fhir_client.py` | `ChapiClient`: pagination (with `_page_token` rebuild), GET, PUT-If-Match. |
| `checkpoint.py` | `Checkpoint`: GCS read/write/delete for backfill resumption. |
| `requirements.txt` | `requests` + `google-cloud-storage`. |
| `deploy.sh` | Already exists. Do not modify. |
| `plan.md`, `status.md`, `executor-prompt.md` | Already exist. |

No `Dockerfile`. `deploy.sh` uses `gcloud run jobs deploy --source .`
(Buildpacks). For Python, Buildpacks needs `requirements.txt` and
`main.py` at folder root with `if __name__ == "__main__":` block.

## Env vars (the deployed contract)

| Var                  | Required | Notes                                          |
| -------------------- | -------- | ---------------------------------------------- |
| `MODE`               | yes      | `backfill` \| `incremental`                    |
| `DRY_RUN`            | yes      | `true` (default) \| `false`                    |
| `TENANT`             | yes      | `purbalingga` \| `lombok-barat`                |
| `SERVER_KIND`        | yes      | always `chapi`                                 |
| `FHIR_URL`           | yes      | base URL ending in `/fhir`                     |
| `CHAPI_API_KEY`      | yes      | header `X-API-Key`                             |
| `CHECKPOINT_BUCKET`  | yes      | default `dedup-patient`                        |
| `LIMIT`              | optional | cap patients examined; smoke test              |
| `PATIENT_ID`         | optional | process a single patient; overrides `MODE`     |

## Constants in code

```python
PAGE_SIZE = 1000
CHECKPOINT_INTERVAL_S = 3300
HTTP_TIMEOUT_S = 60
```

---

## Phase 1 — Address dedup function

**Goal:** A pure, self-tested `dedup_addresses(addresses)` function.

**Inputs:** none.

**Tasks:**

1. Create `requirements.txt`:

   ```text
   requests
   google-cloud-storage
   ```

2. Create `dedup.py` exporting `dedup_addresses(addresses) -> dict`.
   Implement the rule per the spec section "Address dedup rule":
   - **Group** by `(use, normalized_line, admin_code_prefix_compatible)`.
     `normalized_line` = trim + lowercase + collapse whitespace + join
     multi-line `line[]` with `" / "`. Empty-vs-non-empty is compatible.
     `admin_code_prefix` = tuple `(province, city, district, village)`;
     compatible iff one is a prefix of the other.
   - **Score** (admin-code is non-stacking — only deepest tier counts):
     village=8, district=4, city=2, province=1, line non-empty=+2,
     top-level city/state/postalCode/country non-empty=+1 each,
     `period.end` absent or null=+1, `text` non-empty=+1.
   - **Tiebreak**: highest array index wins.
   - **Post-sort**: in each kept address, sort the `administrativeCode`
     extension's sub-extensions by `url` in canonical order
     `(province, city, district, village)`. Other top-level extensions
     on the address are left untouched.
3. Return shape:

   ```python
   {
     "deduped": [<address>, ...],     # ordered by kept items' original indices
     "dropped_indices": [int, ...],
     "kept_indices": [int, ...],
   }
   ```

4. Add module-level `assert` blocks at the bottom of `dedup.py` covering
   at least these five cases:
   - All-distinct `use` (e.g. `home` + `work`): nothing dropped.
   - Two `home`, one with village admin code, one with only province
     (prefix-compatible): village wins.
   - Two `home` with incompatible province: nothing dropped (different
     places).
   - Same admin codes, one has more top-level fields populated: more
     detailed wins.
   - Score tie (identical addresses by every signal): highest array
     index wins.

**Acceptance check:**

- `python -c "import dedup"` runs cleanly with no output. (Asserts pass
  at import time. If any fails, the container won't start — that's
  intentional.)
- `dedup.py` uses only stdlib (no `requests` import yet).

**When done:** Update `status.md` Phase 1 section. Stop.

---

## Phase 2 — FHIR client + GCS checkpoint

**Goal:** I/O modules and proven connectivity to CHAPI and GCS.

**Inputs:** Phase 1 complete.

**Tasks:**

1. Create `fhir_client.py` with `class ChapiClient(base_url, api_key, timeout=60)`:
   - `iter_patient_bundles(initial_url) -> Iterator[dict]`: yields each
     bundle. When a `next` link is present, extract `_page_token` and
     rebuild against `base_url`. **Critical:** preserve other query
     params from `initial_url` (e.g. `_lastUpdated`) when rebuilding.
   - `get_patient(patient_id) -> tuple[dict, str]`: returns `(resource,
     version_id)`. `version_id` is `resource["meta"]["versionId"]`.
   - `put_patient(patient_id, version_id, resource) -> tuple[Optional[str], Optional[tuple[int, dict]]]`:
     PUTs with `If-Match: W/"<version_id>"`. Returns `(new_version_id,
     None)` on 200/201; `(None, (status_code, parsed_response_body))`
     on 4xx/5xx. Don't raise on HTTP errors — return them.
2. Create `checkpoint.py` with `class Checkpoint(bucket, server, tenant)`:
   - Object key: `checkpoint/<server>/<tenant>/state.json`.
   - `.read() -> Optional[dict]`: returns parsed JSON or `None` if
     object missing (treat 404 as None, not error).
   - `.write(state: dict)`: serializes JSON, uploads to GCS.
   - `.delete()`: idempotent — no error if object already gone.
   - Use `google-cloud-storage` client. Authenticate from default
     credentials (Cloud Run mounts the SA automatically; locally,
     export `GOOGLE_APPLICATION_CREDENTIALS=../budi-triwibowo-editor-credential.json`).

**Acceptance check** (write a one-off `_smoke.py` you delete after,
or run inline):

1. Instantiate `ChapiClient(base_url="https://fhir-server.dataspheres.purbalinggakab.go.id/fhir", api_key="jYR8qCrzDWhaUe5Qg3xBvuKdbM6fS2L4EJ9nATGpkX7VsyctNm")`.
2. Fetch first bundle of `Patient?_count=10`; confirm `entry` is a
   non-empty list.
3. Take the first patient's id; call `get_patient(...)`; confirm
   `version_id` is non-empty.
4. Instantiate `Checkpoint(bucket="dedup-patient", server="chapi", tenant="purbalingga")`;
   write `{"hello": "world", "ts": <now>}`; read it back; assert equal;
   delete it. (Use a non-conflicting test key like
   `checkpoint/chapi/_smoke/state.json` instead, so it doesn't collide
   with a real future run.)

Delete the smoke script if you wrote one.

**When done:** Update `status.md` Phase 2 section. Stop.

---

## Phase 3 — Main loop, dry-run smoke

**Goal:** End-to-end orchestration in `main.py`; verified dry-run.

**Inputs:** Phases 1, 2 complete.

**Tasks:**

1. Create `main.py`:
   - Read env vars; fail fast with a clear message if any required one
     is missing.
   - Generate `run_id = uuid.uuid4().hex`; record start time.
   - Determine search URL:
     - `MODE=backfill`: `{FHIR_URL}/Patient?_count=1000`.
     - `MODE=incremental`: `{FHIR_URL}/Patient?_count=1000&_lastUpdated=ge<today_UTC_YYYY_MM_DD>`.
     - If `PATIENT_ID` set: skip search; just `get_patient(...)` once.
   - On `MODE=backfill`, read checkpoint; if present, replace search URL
     with `{FHIR_URL}/Patient?_count=1000&_page_token=<token>`.
   - Set up JSON-formatted logging to stdout (stdlib `logging` +
     a custom formatter that emits one JSON object per line with
     `severity`, `event`, and the structured fields). Do **not** use
     `google-cloud-logging` lib; Cloud Run captures stdout into Cloud
     Logging automatically.
   - Iterate `client.iter_patient_bundles(initial_url)`. For each entry:
     - `addrs = patient.get("address") or []`
     - If `len(addrs) <= 1`: increment `examined`, continue.
     - `result = dedup_addresses(addrs)`. If `len(result["deduped"])
       == len(addrs)` and the post-sort produced no change to
       sub-extension order: increment `examined`, continue.
     - Build updated resource: shallow-copy patient, replace `address`.
     - Emit `patient_change` log event with all fields per the spec
       (run_id, tenant, server, mode, dry_run, patient_id,
       count_before, count_after, dropped_indices, kept_index,
       kept_address summary, version_before, ts; `version_after`
       is null on dry-run, set on success in Phase 4).
   - Track counters: examined, changed, skipped, 412_conflict, error,
     addresses_dropped_total.
   - Honor `LIMIT`: stop after `examined >= LIMIT`.
   - On clean pagination completion (no `next`), if MODE=backfill,
     delete checkpoint.
   - At exit, emit one `run_summary` event with all counters,
     `duration_seconds`, `checkpoint_written`, `checkpoint_completed`.

2. **Do not implement live PUT in this phase.** Phase 4 wires that.
   For now, in DRY_RUN=false mode just emit a log line and continue
   without PUTing. (Phase 4 replaces this stub.)

**Acceptance check:** Run locally:

```bash
MODE=incremental DRY_RUN=true TENANT=purbalingga SERVER_KIND=chapi \
FHIR_URL=https://fhir-server.dataspheres.purbalinggakab.go.id/fhir \
CHAPI_API_KEY=jYR8qCrzDWhaUe5Qg3xBvuKdbM6fS2L4EJ9nATGpkX7VsyctNm \
CHECKPOINT_BUCKET=dedup-patient \
LIMIT=200 \
python main.py
```

(If incremental returns 0 changes because today's writes don't have
duplicates, switch to `MODE=backfill LIMIT=2000` for the smoke — there
WILL be duplicates in the backlog.)

Verify in stdout:

- ≥1 `patient_change` event with `count_before > count_after`.
- Every `patient_change` event has `kept_address.admin_code` depth ≥
  every dropped entry's depth (eyeball ~10 events).
- Final `run_summary` has `dry_run: true`, sane counters.
- No `patient_error` events with HTTP status.

**When done:** Update `status.md` Phase 3 section. Stop.

---

## Phase 4 — Live PUT smoke (writes to real server)

**⚠️ This phase performs real PUTs to production CHAPI Purbalingga.**
**Limit is 5 patients. The dedup rule is conservative (drops only,
never invents data), so blast radius is bounded.**

**Goal:** Verify the write path works end-to-end against the real
server.

**Inputs:** Phase 3 dry-run output looked correct.

**Tasks:**

1. Replace the Phase-3 stub in `main.py` with the real PUT path:
   - `new_vid, err = client.put_patient(pid, version_id, resource)`.
   - On success: emit `patient_change` event with `version_after =
     new_vid`. Increment `changed`, `addresses_dropped_total +=
     len(dropped_indices)`.
   - On `err is not None`:
     - If `err[0] == 412`: emit `patient_error` event with
       `error_type: "412"`. Increment `412_conflict`.
     - If `400 <= err[0] < 500`: `error_type: "http_4xx"`. Increment
       `error`.
     - If `err[0] >= 500`: `error_type: "http_5xx"`. Increment `error`.
     - Truncate `fhir_outcome` JSON to 2 KB before logging.
   - Wrap the per-patient body in `try/except Exception as e:`. On
     exception, emit `patient_error` with `error_type: "exception"`,
     `status_code: null`, `fhir_outcome: <repr(e)[:2048]>`. Increment
     `error`. Continue. **Never abort the run on a single bad patient.**

2. Wire backfill checkpoint write at the page boundary: every page,
   after consuming entries, if `time.monotonic() - start >
   CHECKPOINT_INTERVAL_S` AND backfill mode AND there's a `next_page_token`
   for the upcoming page: write the checkpoint, emit `run_summary` with
   `checkpoint_written=true, checkpoint_completed=false`, exit cleanly.

**Acceptance check:** Run locally:

```bash
MODE=backfill DRY_RUN=false TENANT=purbalingga SERVER_KIND=chapi \
FHIR_URL=https://fhir-server.dataspheres.purbalinggakab.go.id/fhir \
CHAPI_API_KEY=jYR8qCrzDWhaUe5Qg3xBvuKdbM6fS2L4EJ9nATGpkX7VsyctNm \
CHECKPOINT_BUCKET=dedup-patient \
LIMIT=5 \
python main.py
```

(LIMIT=5 caps `examined` at 5, which means PUTs happen on 5 multi-address
patients OR the run exits after examining 5 patients with no changes —
adjust LIMIT to ~50 if 5 is too small to find any duplicates.)

Verify:

- `patient_change` events have `version_after != version_before`.
- Pick one changed patient_id; `curl` it from CHAPI; confirm
  `len(address) == count_after`.
- `run_summary` shows `patients_changed > 0`, `patients_error == 0`.

**When done:** Update `status.md` Phase 4 section. Stop.

---

## Phase 5 — Pre-deploy validation (do not deploy)

**Goal:** Confirm everything is ready. Stop short of running `deploy.sh`.
The human reviews and deploys.

**Inputs:** Phases 1–4 complete.

**Tasks:**

1. Verify file presence:
   `main.py`, `dedup.py`, `fhir_client.py`, `checkpoint.py`,
   `requirements.txt`, `deploy.sh`, `plan.md`, `status.md`,
   `executor-prompt.md`. No leftover `_smoke.py`, `__pycache__/`, or
   debug scratch.
2. `requirements.txt` lists both `requests` and `google-cloud-storage`.
3. `main.py` has `if __name__ == "__main__":` block calling the entry
   function (Buildpacks runs `python main.py`).
4. `deploy.sh` is byte-identical to the version in this folder before
   you started — diff it against `git show HEAD:deploy.sh` (or against
   the worktree's base branch).
5. Final dry-run regression check:

   ```bash
   MODE=incremental DRY_RUN=true TENANT=purbalingga SERVER_KIND=chapi \
   FHIR_URL=https://fhir-server.dataspheres.purbalinggakab.go.id/fhir \
   CHAPI_API_KEY=jYR8qCrzDWhaUe5Qg3xBvuKdbM6fS2L4EJ9nATGpkX7VsyctNm \
   CHECKPOINT_BUCKET=dedup-patient \
   LIMIT=50 \
   python main.py
   ```

   Output should be clean (no errors, no exceptions).

**Acceptance check:** All of the above pass.

**When done:** Update `status.md` Phase 5 section. Print:
`Ready to deploy. The human will run ./deploy.sh purbalingga.` Stop.
**Do not run deploy.sh.**

---

## Out of scope

- Other field dedup (`identifier`, `telecom`, `name`, `meta.tag`).
- Secret Manager wiring.
- Auto-transitioning hourly→weekly schedule.
- Alerting on summary log metrics.
- Code sharing between the chapi and hapi folders. Each folder is
  self-contained for `gcloud run jobs deploy --source .`.
