# Test scenarios

26 tests across 4 test files. Run with:

```bash
pytest tests/ -v
```

## test_checkpoint.py (6 tests)

Tests the GCS-backed `Checkpoint` class used for resuming pagination.

| Test | Scenario |
|---|---|
| `test_read_returns_dict` | Reading a checkpoint returns the parsed JSON dict |
| `test_read_returns_none_on_not_found` | Reading a non-existent checkpoint returns `None` (no error) |
| `test_write_uploads_json` | Writing a checkpoint uploads JSON with correct content type |
| `test_delete_suppresses_not_found` | Deleting a non-existent checkpoint does not raise |
| `test_default_kind_uses_state_json` | Default `kind` ("backfill") uses blob path `checkpoint/.../state.json` |
| `test_rolling_kind_uses_rolling_state_json` | `kind="rolling"` uses blob path `checkpoint/.../rolling_state.json` |

## test_fhir_client.py (8 tests)

Tests the async FHIR HTTP client using `respx` mock transport.

| Test | Scenario |
|---|---|
| `test_iter_patient_bundles_single_page` | Single-page bundle (no `next` link) returns one bundle |
| `test_iter_patient_bundles_two_pages` | Two-page pagination follows `next` link with `_page_token` |
| `test_iter_patient_bundles_preserves_last_updated` | Pagination carries `_lastUpdated` filter to subsequent pages |
| `test_get_patient_success` | `GET Patient/{id}` returns resource and versionId |
| `test_put_patient_success_200` | `PUT Patient/{id}` with 200 returns new versionId, no error |
| `test_put_patient_412` | `PUT` with 412 Conflict returns error tuple `(412, body)` |
| `test_put_patient_500` | `PUT` with 500 returns error tuple `(500, body)` |
| `test_get_patient_raises_on_404` | `GET Patient/{id}` for missing patient raises `HTTPStatusError` |

## test_main.py (7 tests)

Tests the FastAPI endpoints and background task orchestration.

| Test | Scenario |
|---|---|
| `test_health` | `GET /health` returns `200 {"status": "ok"}` |
| `test_run_returns_202_with_run_id` | `POST /run` returns 202 with `run_id`, `status_url`, and `fresh` field |
| `test_run_fresh_query_param` | `POST /run?fresh=true` passes `fresh=true` through to response |
| `test_status_found` | `GET /status/{run_id}` returns stored status record |
| `test_status_not_found` | `GET /status/{run_id}` for unknown run returns 404 |
| `test_run_background_writes_completed_on_success` | Background task writes `status=completed` with summary on success |
| `test_run_background_writes_failed_on_exception` | Background task writes `status=failed` with error message on exception |

## test_status_store.py (5 tests)

Tests the GCS-backed `StatusStore` for per-run status tracking.

| Test | Scenario |
|---|---|
| `test_write_running_creates_correct_schema` | `write_running` creates a JSON record with `status=running` and null terminal fields |
| `test_write_final_merges_with_existing` | `write_final` merges new status into the existing running record |
| `test_write_final_standalone_when_not_found` | `write_final` creates a standalone record if the running record is missing |
| `test_read_returns_none_on_missing` | Reading a non-existent run returns `None` |
| `test_read_returns_dict` | Reading an existing run returns the parsed JSON dict |
