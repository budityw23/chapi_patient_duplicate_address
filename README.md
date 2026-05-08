# chapi_patient_duplicate_address

Deduplicates patient address records on a **CHAPI FHIR server**. Runs as a
**Cloud Run Service** (async FastAPI + uvicorn) so multiple pages of patients
are processed concurrently within a single HTTP-triggered run.

## How it works

1. `POST /run` starts a background task and returns a `run_id` immediately.
2. The background task pages through `Patient` resources (1 000 per page).
3. For each patient with more than one address, `dedup_addresses()` groups
   compatible entries and keeps the most-detailed one per group.
4. In live mode, the winning address list is written back via
   `PUT Patient/{id}` with `If-Match` (optimistic locking).
5. `GET /status/{run_id}` polls progress; final status is persisted to GCS.

### Two-phase incremental mode

In incremental mode the service supports a **fresh + rolling scan** strategy
to avoid wasting time re-reading already-processed patients:

- **Fresh runs** (`POST /run?fresh=true`, 3x/day): Phase 1 scans patients
  updated today (`_lastUpdated=ge{today}`). Phase 2 resumes a rolling
  checkpoint scan through all patients for the remaining time budget.
- **Resume runs** (`POST /run`, 21x/day): skip Phase 1, go straight to the
  rolling checkpoint scan.

The rolling scan pages through every patient in the database and saves its
position to GCS. When it reaches the end it wraps around and starts over.
This guarantees every patient is periodically re-checked.

Cloud Scheduler fires 24 times per day (hourly). Three of those hours
(0:00, 8:00, 16:00 Jakarta) call `/run?fresh=true`; the other 21 call
`/run` without the flag.

### Backfill mode

In backfill mode the service pages through all patients without a date
filter. Pagination resumes from a GCS checkpoint if a run is interrupted
(checkpoint written every ~55 minutes before the Cloud Run 3600 s timeout).

## Dedup logic

Two addresses are **compatible** if they share the same `use`, have
non-conflicting `line` values, and their `administrativeCode` extensions
are prefix-compatible (e.g. province-only vs. full village is compatible;
two different provinces are not).

Among compatible addresses the **winner** is the one with the highest score:

| Signal | Points |
|---|---|
| village-level admin code | 8 |
| district-level admin code | 4 |
| city-level admin code | 2 |
| province-level admin code | 1 |
| non-empty `line` | 2 |
| `city`, `state`, `postalCode`, `country` (each) | 1 |
| open `period` (no `end`) | 1 |
| `text` present | 1 |

Ties are broken by higher array index. The kept address has its
`administrativeCode` sub-extensions sorted in canonical order
(province -> city -> district -> village).

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # runtime
pip install -r requirements-dev.txt   # tests
```

Required Python: 3.11 (see [.python-version](.python-version)).

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `MODE` | yes | -- | `backfill` or `incremental` |
| `TENANT` | yes | -- | Tenant identifier (e.g. `purbalingga`) |
| `SERVER_KIND` | yes | -- | Must be `chapi` |
| `FHIR_URL` | yes | -- | Base FHIR URL (no trailing slash) |
| `CHAPI_API_KEY` | yes | -- | API key sent as `X-API-Key` |
| `DRY_RUN` | no | `true` | Set to `false` to write changes |
| `FRESH` | no | `false` | Env-var default for the `?fresh=` query param |
| `CHECKPOINT_BUCKET` | no | `dedup-patient` | GCS bucket name |
| `FHIR_CONCURRENCY` | no | `20` | Max concurrent FHIR PUT requests |
| `LIMIT` | no | -- | Stop after N patients (testing) |
| `PATIENT_ID` | no | -- | Process a single patient by ID |

## API

| Endpoint | Method | Description |
|---|---|---|
| `/health` | GET | Health check |
| `/run` | POST | Start a dedup run. Returns `202` with `run_id`. |
| `/run?fresh=true` | POST | Start a fresh run (Phase 1 today + Phase 2 rolling). |
| `/status/{run_id}` | GET | Poll run status. |

## Running locally

```bash
# Dry-run smoke test
MODE=incremental TENANT=purbalingga SERVER_KIND=chapi \
  FHIR_URL=https://fhir-server.dataspheres.purbalinggakab.go.id/fhir \
  CHAPI_API_KEY=<key> DRY_RUN=true \
  uvicorn main:app --host 0.0.0.0 --port 8080

# Trigger a resume run
curl -X POST http://localhost:8080/run

# Trigger a fresh run
curl -X POST http://localhost:8080/run?fresh=true

curl http://localhost:8080/status/<run_id>
```

## Tests

```bash
pytest tests/ -v
```

26 tests covering `dedup.py`, `fhir_client.py`, `checkpoint.py`,
`status_store.py`, and the FastAPI endpoints. See [tests/README.md](tests/README.md)
for a breakdown of each test scenario.

## Deploying

```bash
./deploy_purbalingga.sh
./deploy_lobar.sh
```

Or use the generic script:

```bash
./deploy.sh purbalingga
./deploy.sh lombok-barat
```

The service starts with `DRY_RUN=true`; flip to live mode with:

```bash
gcloud run services update dedup-address-chapi-purbalingga \
  --update-env-vars DRY_RUN=false \
  --region asia-southeast2
```

## Cloud Scheduler

Each tenant has two scheduler jobs:

| Job | Schedule (Jakarta) | Target | Purpose |
| --- | --- | --- | --- |
| `dedup-address-chapi-<tenant>-fresh` | `0 0,8,16 * * *` | `/run?fresh=true` | Today's patients + rolling scan |
| `dedup-address-chapi-<tenant>-hourly` | `0 1-7,9-15,17-23 * * *` | `/run` | Rolling scan only |

Total: 24 runs/day, no overlapping schedules.

## GCS layout

```
gs://dedup-patient/
  checkpoint/chapi/<tenant>/state.json          # backfill resume token
  checkpoint/chapi/<tenant>/rolling_state.json  # incremental rolling scan token
  status/chapi/<tenant>/<run_id>.json           # per-run status
```

## File overview

| File | Purpose |
|---|---|
| [main.py](main.py) | FastAPI app -- `/health`, `POST /run`, `GET /status/{run_id}` |
| [dedup.py](dedup.py) | Pure dedup function + 5 module-level self-tests |
| [fhir_client.py](fhir_client.py) | Async CHAPI client (`httpx.AsyncClient`, X-API-Key) |
| [checkpoint.py](checkpoint.py) | GCS-backed async checkpoint (backfill + rolling) |
| [status_store.py](status_store.py) | GCS-backed per-run status (running -> completed/failed) |
| [deploy.sh](deploy.sh) | Generic `gcloud run deploy` helper |
| [deploy_purbalingga.sh](deploy_purbalingga.sh) | Deploy helper for Purbalingga |
| [deploy_lobar.sh](deploy_lobar.sh) | Deploy helper for Lombok Barat |
| [Procfile](Procfile) | uvicorn entrypoint for Cloud Run |
| [requirements.txt](requirements.txt) | Runtime dependencies |
| [requirements-dev.txt](requirements-dev.txt) | Test dependencies |
| [tests/](tests/) | pytest suite (26 tests) |
