import asyncio
import datetime
import json
import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlparse

from fastapi import BackgroundTasks, FastAPI, HTTPException

from checkpoint import Checkpoint
from dedup import ADMIN_URL_ORDER, _get_admin_code, dedup_addresses
from fhir_client import ChapiClient
from status_store import StatusStore

PAGE_SIZE = 1000
CHECKPOINT_INTERVAL_S = 3300
HTTP_TIMEOUT_S = 60


class _JsonFormatter(logging.Formatter):
    _SEV = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    def format(self, record):
        sev = self._SEV.get(record.levelno, "INFO")
        if isinstance(record.msg, dict):
            payload = {"severity": sev}
            payload.update(record.msg)
        else:
            payload = {"severity": sev, "message": record.getMessage()}
        return json.dumps(payload, default=str)


def _setup_logging():
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return logging.getLogger(__name__)


def _require_env(name):
    val = os.environ.get(name, "").strip()
    if not val:
        print(
            json.dumps({"severity": "CRITICAL", "message": f"Missing required env var: {name}"}),
            flush=True,
        )
        sys.exit(1)
    return val


MODE = _require_env("MODE")
TENANT = _require_env("TENANT")
SERVER_KIND = _require_env("SERVER_KIND")
FHIR_URL = _require_env("FHIR_URL").rstrip("/")
CHAPI_API_KEY = _require_env("CHAPI_API_KEY")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() != "false"
CHECKPOINT_BUCKET = os.environ.get("CHECKPOINT_BUCKET", "dedup-patient")
LIMIT = int(os.environ["LIMIT"]) if os.environ.get("LIMIT") else None
PATIENT_ID = os.environ.get("PATIENT_ID", "").strip() or None

if MODE not in ("backfill", "incremental"):
    print(
        json.dumps({"severity": "CRITICAL", "message": f"Invalid MODE: {MODE!r}"}),
        flush=True,
    )
    sys.exit(1)


def _now_z():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _is_noop(addrs, result):
    if len(result["deduped"]) != len(addrs):
        return False
    for orig_idx, deduped_addr in zip(result["kept_indices"], result["deduped"]):
        orig = addrs[orig_idx]
        for orig_ext in orig.get("extension", []):
            if orig_ext.get("url") == "administrativeCode":
                orig_order = [s["url"] for s in orig_ext.get("extension", [])]
                for ded_ext in deduped_addr.get("extension", []):
                    if ded_ext.get("url") == "administrativeCode":
                        if orig_order != [s["url"] for s in ded_ext.get("extension", [])]:
                            return False
    return True


def _kept_address_summary(addr):
    code = _get_admin_code(addr)
    admin = {k: v for k, v in zip(ADMIN_URL_ORDER, code) if v is not None}
    line = addr.get("line") or []
    return {
        "use": addr.get("use"),
        "line0": line[0] if line else None,
        "city": addr.get("city"),
        "admin_code": admin,
    }


_fhir_sem = asyncio.Semaphore(20)  # overridden in lifespan with FHIR_CONCURRENCY


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _fhir_sem
    _fhir_sem = asyncio.Semaphore(int(os.environ.get("FHIR_CONCURRENCY", "20")))
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/run", status_code=202)
async def trigger_run(background_tasks: BackgroundTasks):
    run_id = uuid.uuid4().hex
    status_store = StatusStore(bucket=CHECKPOINT_BUCKET, server=SERVER_KIND, tenant=TENANT)
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
    status_store = StatusStore(bucket=CHECKPOINT_BUCKET, server=SERVER_KIND, tenant=TENANT)
    record = await status_store.read(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run not found")
    return record


async def _run_background(run_id: str, status_store: StatusStore) -> None:
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


async def run(run_id: str) -> dict:
    log = _setup_logging()

    start_mono = time.monotonic()
    start_ts = _now_z()

    cp = Checkpoint(bucket=CHECKPOINT_BUCKET, server=SERVER_KIND, tenant=TENANT)
    status_store = StatusStore(bucket=CHECKPOINT_BUCKET, server=SERVER_KIND, tenant=TENANT)

    today = datetime.date.today().strftime("%Y-%m-%d")
    if MODE == "backfill":
        initial_url = f"{FHIR_URL}/Patient?_count={PAGE_SIZE}"
    else:
        initial_url = f"{FHIR_URL}/Patient?_count={PAGE_SIZE}&_lastUpdated=ge{today}"

    checkpoint_written = False
    checkpoint_completed = False

    if MODE == "backfill" and not PATIENT_ID:
        state = await cp.read()
        if state and state.get("next_page_token"):
            token = state["next_page_token"]
            initial_url = f"{FHIR_URL}/Patient?_count={PAGE_SIZE}&_page_token={token}"
            log.info({"event": "checkpoint_resumed", "run_id": run_id, "next_page_token": token})

    log.info({
        "event": "run_start",
        "run_id": run_id,
        "tenant": TENANT,
        "server": SERVER_KIND,
        "mode": MODE,
        "dry_run": DRY_RUN,
        "limit": LIMIT,
        "patient_id": PATIENT_ID,
        "ts": start_ts,
    })

    examined = 0
    changed = 0
    skipped = 0
    conflict_412 = 0
    error_count = 0
    addresses_dropped_total = 0

    def _make_summary(chk_written, chk_completed):
        return {
            "event": "run_summary",
            "run_id": run_id,
            "tenant": TENANT,
            "server": SERVER_KIND,
            "mode": MODE,
            "dry_run": DRY_RUN,
            "duration_seconds": round(time.monotonic() - start_mono, 1),
            "patients_examined": examined,
            "patients_changed": changed,
            "patients_skipped": skipped,
            "patients_412_conflict": conflict_412,
            "patients_error": error_count,
            "addresses_dropped_total": addresses_dropped_total,
            "checkpoint_written": chk_written,
            "checkpoint_completed": chk_completed,
        }

    async def _process_patient(patient):
        nonlocal examined, changed, skipped, error_count, addresses_dropped_total, conflict_412

        pid = ""
        try:
            pid = patient.get("id", "")
            addrs = patient.get("address") or []
            version_before = (patient.get("meta") or {}).get("versionId", "")

            if len(addrs) <= 1:
                skipped += 1
                return

            result = dedup_addresses(addrs)

            if _is_noop(addrs, result):
                skipped += 1
                return

            kept_addr = result["deduped"][0]

            if DRY_RUN:
                log.info({
                    "event": "patient_change",
                    "run_id": run_id,
                    "tenant": TENANT,
                    "server": SERVER_KIND,
                    "mode": MODE,
                    "dry_run": True,
                    "patient_id": pid,
                    "count_before": len(addrs),
                    "count_after": len(result["deduped"]),
                    "dropped_indices": result["dropped_indices"],
                    "kept_indices": result["kept_indices"],
                    "kept_address": _kept_address_summary(kept_addr),
                    "version_before": version_before,
                    "version_after": None,
                    "ts": _now_z(),
                })
                changed += 1
                addresses_dropped_total += len(result["dropped_indices"])
            else:
                updated = dict(patient)
                updated["address"] = result["deduped"]
                async with _fhir_sem:
                    new_vid, err = await client.put_patient(pid, version_before, updated)

                if err is None:
                    log.info({
                        "event": "patient_change",
                        "run_id": run_id,
                        "tenant": TENANT,
                        "server": SERVER_KIND,
                        "mode": MODE,
                        "dry_run": False,
                        "patient_id": pid,
                        "count_before": len(addrs),
                        "count_after": len(result["deduped"]),
                        "dropped_indices": result["dropped_indices"],
                        "kept_indices": result["kept_indices"],
                        "kept_address": _kept_address_summary(kept_addr),
                        "version_before": version_before,
                        "version_after": new_vid,
                        "ts": _now_z(),
                    })
                    changed += 1
                    addresses_dropped_total += len(result["dropped_indices"])
                else:
                    status_code, body = err
                    fhir_outcome = json.dumps(body)[:2048]
                    if status_code == 412:
                        error_type = "412"
                        conflict_412 += 1
                    elif 400 <= status_code < 500:
                        error_type = "http_4xx"
                        error_count += 1
                    else:
                        error_type = "http_5xx"
                        error_count += 1
                    log.error({
                        "event": "patient_error",
                        "run_id": run_id,
                        "tenant": TENANT,
                        "server": SERVER_KIND,
                        "patient_id": pid,
                        "error_type": error_type,
                        "status_code": status_code,
                        "fhir_outcome": fhir_outcome,
                    })

        except Exception as e:
            error_count += 1
            log.error({
                "event": "patient_error",
                "run_id": run_id,
                "tenant": TENANT,
                "server": SERVER_KIND,
                "patient_id": pid,
                "error_type": "exception",
                "status_code": None,
                "fhir_outcome": repr(e)[:2048],
            })
        finally:
            examined += 1

    pagination_complete = False

    async with ChapiClient(base_url=FHIR_URL, api_key=CHAPI_API_KEY, timeout=HTTP_TIMEOUT_S) as client:
        if PATIENT_ID:
            try:
                resource, _ = await client.get_patient(PATIENT_ID)
            except Exception as e:
                log.error({
                    "event": "patient_error",
                    "run_id": run_id,
                    "tenant": TENANT,
                    "server": SERVER_KIND,
                    "patient_id": PATIENT_ID,
                    "error_type": "exception",
                    "status_code": None,
                    "fhir_outcome": repr(e)[:2048],
                })
                error_count += 1
                examined += 1
            else:
                await _process_patient(resource)
            pagination_complete = True
        else:
            async for bundle in client.iter_patient_bundles(initial_url):
                has_next = False
                next_token = None
                for lnk in bundle.get("link", []):
                    if lnk.get("relation") == "next":
                        has_next = True
                        np = parse_qs(urlparse(lnk["url"]).query)
                        next_token = np.get("_page_token", [None])[0]
                        break

                patients = [
                    entry["resource"]
                    for entry in bundle.get("entry", []) or []
                    if entry.get("resource") and entry["resource"].get("resourceType") == "Patient"
                ]
                await asyncio.gather(*[_process_patient(p) for p in patients])

                if LIMIT and examined >= LIMIT:
                    break

                if MODE == "backfill" and next_token and (time.monotonic() - start_mono) > CHECKPOINT_INTERVAL_S:
                    state = {
                        "run_id": run_id,
                        "next_page_token": next_token,
                        "started_at": start_ts,
                        "last_progress_at": _now_z(),
                    }
                    try:
                        await cp.write(state)
                        checkpoint_written = True
                        log.info({"event": "checkpoint_written", "run_id": run_id, "next_page_token": next_token})
                    except Exception as e:
                        log.error({"event": "checkpoint_write_error", "run_id": run_id, "error": repr(e)})

                    summary = _make_summary(checkpoint_written, False)
                    log.info(summary)
                    await status_store.write_final(
                        run_id=run_id,
                        status="checkpointed",
                        finished_at=_now_z(),
                        summary=summary,
                        error=None,
                    )
                    return summary

                if not has_next:
                    pagination_complete = True

    if MODE == "backfill" and pagination_complete and not PATIENT_ID:
        try:
            await cp.delete()
            checkpoint_completed = True
            log.info({"event": "checkpoint_deleted", "run_id": run_id})
        except Exception as e:
            log.error({"event": "checkpoint_delete_error", "run_id": run_id, "error": repr(e)})

    summary = _make_summary(checkpoint_written, checkpoint_completed)
    log.info(summary)
    return summary
