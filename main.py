import datetime
import json
import logging
import os
import sys
import time
import uuid
from urllib.parse import parse_qs, urlparse

from checkpoint import Checkpoint
from dedup import ADMIN_URL_ORDER, _get_admin_code, dedup_addresses
from fhir_client import ChapiClient

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


def _now_z():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def run():
    log = _setup_logging()

    mode = _require_env("MODE")
    dry_run = os.environ.get("DRY_RUN", "true").lower() != "false"
    tenant = _require_env("TENANT")
    server_kind = _require_env("SERVER_KIND")
    fhir_url = _require_env("FHIR_URL").rstrip("/")
    api_key = _require_env("CHAPI_API_KEY")
    bucket = os.environ.get("CHECKPOINT_BUCKET", "dedup-patient")
    limit = int(os.environ["LIMIT"]) if os.environ.get("LIMIT") else None
    patient_id_env = os.environ.get("PATIENT_ID", "").strip() or None

    if mode not in ("backfill", "incremental"):
        print(
            json.dumps({"severity": "CRITICAL", "message": f"Invalid MODE: {mode!r}"}),
            flush=True,
        )
        sys.exit(1)

    run_id = uuid.uuid4().hex
    start_mono = time.monotonic()
    start_ts = _now_z()

    client = ChapiClient(base_url=fhir_url, api_key=api_key, timeout=HTTP_TIMEOUT_S)
    cp = Checkpoint(bucket=bucket, server=server_kind, tenant=tenant)

    today = datetime.date.today().strftime("%Y-%m-%d")
    if mode == "backfill":
        initial_url = f"{fhir_url}/Patient?_count={PAGE_SIZE}"
    else:
        initial_url = f"{fhir_url}/Patient?_count={PAGE_SIZE}&_lastUpdated=ge{today}"

    checkpoint_written = False
    checkpoint_completed = False

    if mode == "backfill" and not patient_id_env:
        state = cp.read()
        if state and state.get("next_page_token"):
            token = state["next_page_token"]
            initial_url = f"{fhir_url}/Patient?_count={PAGE_SIZE}&_page_token={token}"
            log.info({"event": "checkpoint_resumed", "run_id": run_id, "next_page_token": token})

    log.info({
        "event": "run_start",
        "run_id": run_id,
        "tenant": tenant,
        "server": server_kind,
        "mode": mode,
        "dry_run": dry_run,
        "limit": limit,
        "patient_id": patient_id_env,
        "ts": start_ts,
    })

    examined = 0
    changed = 0
    skipped = 0
    conflict_412 = 0
    error_count = 0
    addresses_dropped_total = 0

    def _emit_summary(chk_written, chk_completed):
        log.info({
            "event": "run_summary",
            "run_id": run_id,
            "tenant": tenant,
            "server": server_kind,
            "mode": mode,
            "dry_run": dry_run,
            "duration_seconds": round(time.monotonic() - start_mono, 1),
            "patients_examined": examined,
            "patients_changed": changed,
            "patients_skipped": skipped,
            "patients_412_conflict": conflict_412,
            "patients_error": error_count,
            "addresses_dropped_total": addresses_dropped_total,
            "checkpoint_written": chk_written,
            "checkpoint_completed": chk_completed,
        })

    def _process_patient(patient):
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

            if dry_run:
                log.info({
                    "event": "patient_change",
                    "run_id": run_id,
                    "tenant": tenant,
                    "server": server_kind,
                    "mode": mode,
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
                new_vid, err = client.put_patient(pid, version_before, updated)

                if err is None:
                    log.info({
                        "event": "patient_change",
                        "run_id": run_id,
                        "tenant": tenant,
                        "server": server_kind,
                        "mode": mode,
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
                        "tenant": tenant,
                        "server": server_kind,
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
                "tenant": tenant,
                "server": server_kind,
                "patient_id": pid,
                "error_type": "exception",
                "status_code": None,
                "fhir_outcome": repr(e)[:2048],
            })
        finally:
            examined += 1

    if patient_id_env:
        try:
            resource, _ = client.get_patient(patient_id_env)
        except Exception as e:
            log.error({
                "event": "patient_error",
                "run_id": run_id,
                "tenant": tenant,
                "server": server_kind,
                "patient_id": patient_id_env,
                "error_type": "exception",
                "status_code": None,
                "fhir_outcome": repr(e)[:2048],
            })
            error_count += 1
            examined += 1
        else:
            _process_patient(resource)
        pagination_complete = True
    else:
        stop_flag = False
        pagination_complete = False

        for bundle in client.iter_patient_bundles(initial_url):
            has_next = False
            next_token = None
            for lnk in bundle.get("link", []):
                if lnk.get("relation") == "next":
                    has_next = True
                    np = parse_qs(urlparse(lnk["url"]).query)
                    next_token = np.get("_page_token", [None])[0]
                    break

            for entry in bundle.get("entry", []) or []:
                patient = entry.get("resource")
                if not patient or patient.get("resourceType") != "Patient":
                    continue
                _process_patient(patient)
                if limit and examined >= limit:
                    stop_flag = True
                    break

            if stop_flag:
                break

            # Checkpoint write at page boundary (backfill only, when time limit approaching)
            if mode == "backfill" and next_token and (time.monotonic() - start_mono) > CHECKPOINT_INTERVAL_S:
                state = {
                    "run_id": run_id,
                    "next_page_token": next_token,
                    "started_at": start_ts,
                    "last_progress_at": _now_z(),
                }
                try:
                    cp.write(state)
                    checkpoint_written = True
                    log.info({"event": "checkpoint_written", "run_id": run_id, "next_page_token": next_token})
                except Exception as e:
                    log.error({"event": "checkpoint_write_error", "run_id": run_id, "error": repr(e)})
                _emit_summary(checkpoint_written, False)
                return

            if not has_next:
                pagination_complete = True

    if mode == "backfill" and pagination_complete and not patient_id_env:
        try:
            cp.delete()
            checkpoint_completed = True
            log.info({"event": "checkpoint_deleted", "run_id": run_id})
        except Exception as e:
            log.error({"event": "checkpoint_delete_error", "run_id": run_id, "error": repr(e)})

    _emit_summary(checkpoint_written, checkpoint_completed)


if __name__ == "__main__":
    run()
