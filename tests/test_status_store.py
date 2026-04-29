import json
from unittest.mock import MagicMock, patch

import pytest
from google.api_core.exceptions import NotFound

from status_store import StatusStore

BUCKET = "dedup-patient"
SERVER = "chapi"
TENANT = "purbalingga"


@pytest.fixture
def mock_store():
    with patch("status_store.storage.Client") as MockClient:
        mock_bucket = MagicMock()
        MockClient.return_value.bucket.return_value = mock_bucket
        yield StatusStore(BUCKET, SERVER, TENANT), mock_bucket


async def test_write_running_creates_correct_schema(mock_store):
    store, mock_bucket = mock_store
    mock_blob = MagicMock()
    mock_bucket.blob.return_value = mock_blob

    await store.write_running(
        run_id="run1",
        tenant=TENANT,
        server=SERVER,
        mode="incremental",
        dry_run=True,
        started_at="2026-04-29T10:00:00Z",
    )

    mock_blob.upload_from_string.assert_called_once()
    written = json.loads(mock_blob.upload_from_string.call_args[0][0])
    assert written["run_id"] == "run1"
    assert written["status"] == "running"
    assert written["started_at"] == "2026-04-29T10:00:00Z"
    assert written["finished_at"] is None
    assert written["summary"] is None
    assert written["error"] is None


async def test_write_final_merges_with_existing(mock_store):
    store, mock_bucket = mock_store
    mock_blob = MagicMock()
    mock_bucket.blob.return_value = mock_blob

    running_record = {
        "run_id": "run2",
        "status": "running",
        "started_at": "2026-04-29T10:00:00Z",
        "finished_at": None,
        "summary": None,
        "error": None,
    }
    mock_blob.download_as_text.return_value = json.dumps(running_record)

    summary = {"examined": 100, "changed": 10}
    await store.write_final(
        run_id="run2",
        status="completed",
        finished_at="2026-04-29T11:00:00Z",
        summary=summary,
        error=None,
    )

    mock_blob.upload_from_string.assert_called_once()
    written = json.loads(mock_blob.upload_from_string.call_args[0][0])
    assert written["status"] == "completed"
    assert written["started_at"] == "2026-04-29T10:00:00Z"
    assert written["finished_at"] == "2026-04-29T11:00:00Z"
    assert written["summary"] == summary
    assert written["error"] is None


async def test_write_final_standalone_when_not_found(mock_store):
    store, mock_bucket = mock_store
    mock_blob = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_blob.download_as_text.side_effect = NotFound("blob")

    await store.write_final(
        run_id="run3",
        status="failed",
        finished_at="2026-04-29T12:00:00Z",
        summary=None,
        error="something went wrong",
    )

    mock_blob.upload_from_string.assert_called_once()
    written = json.loads(mock_blob.upload_from_string.call_args[0][0])
    assert written["status"] == "failed"
    assert written["error"] == "something went wrong"
    assert written["run_id"] == "run3"


async def test_read_returns_none_on_missing(mock_store):
    store, mock_bucket = mock_store
    mock_blob = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    mock_blob.download_as_text.side_effect = NotFound("blob")

    result = await store.read("run4")

    assert result is None


async def test_read_returns_dict(mock_store):
    store, mock_bucket = mock_store
    mock_blob = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    record = {"run_id": "run5", "status": "completed"}
    mock_blob.download_as_text.return_value = json.dumps(record)

    result = await store.read("run5")

    assert result == record
