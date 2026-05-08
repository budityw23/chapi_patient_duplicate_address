import os

# Set required env vars before importing main so _require_env does not sys.exit.
os.environ.setdefault("MODE", "incremental")
os.environ.setdefault("TENANT", "test-tenant")
os.environ.setdefault("SERVER_KIND", "chapi")
os.environ.setdefault("FHIR_URL", "http://test.example.com/fhir")
os.environ.setdefault("CHAPI_API_KEY", "test-key")

from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from main import app, _run_background  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_run_returns_202_with_run_id(client):
    mock_store = MagicMock()
    mock_store.write_running = AsyncMock()

    with patch("main.StatusStore", return_value=mock_store), \
         patch("main._run_background", new_callable=AsyncMock):
        response = client.post("/run")

    assert response.status_code == 202
    data = response.json()
    assert data["run_id"]
    assert data["status_url"].startswith("/status/")
    assert "fresh" in data


def test_run_fresh_query_param(client):
    mock_store = MagicMock()
    mock_store.write_running = AsyncMock()

    with patch("main.StatusStore", return_value=mock_store), \
         patch("main._run_background", new_callable=AsyncMock):
        response = client.post("/run?fresh=true")

    assert response.status_code == 202
    assert response.json()["fresh"] is True


def test_status_found(client):
    record = {"run_id": "abc123", "status": "completed"}
    mock_store = MagicMock()
    mock_store.read = AsyncMock(return_value=record)

    with patch("main.StatusStore", return_value=mock_store):
        response = client.get("/status/abc123")

    assert response.status_code == 200
    assert response.json() == record


def test_status_not_found(client):
    mock_store = MagicMock()
    mock_store.read = AsyncMock(return_value=None)

    with patch("main.StatusStore", return_value=mock_store):
        response = client.get("/status/abc123")

    assert response.status_code == 404


async def test_run_background_writes_completed_on_success():
    mock_store = MagicMock()
    mock_store.write_final = AsyncMock()
    summary = {"event": "run_summary", "patients_examined": 10, "patients_changed": 2}

    with patch("main.run", new_callable=AsyncMock, return_value=summary):
        await _run_background("run123", mock_store, False)

    mock_store.write_final.assert_called_once()
    kwargs = mock_store.write_final.call_args.kwargs
    assert kwargs["status"] == "completed"
    assert kwargs["summary"] == summary
    assert kwargs["error"] is None


async def test_run_background_writes_failed_on_exception():
    mock_store = MagicMock()
    mock_store.write_final = AsyncMock()

    with patch("main.run", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
        await _run_background("run456", mock_store, False)

    mock_store.write_final.assert_called_once()
    kwargs = mock_store.write_final.call_args.kwargs
    assert kwargs["status"] == "failed"
    assert kwargs["summary"] is None
    assert "boom" in kwargs["error"]
