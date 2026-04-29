import json
from unittest.mock import MagicMock, patch

import pytest
from google.api_core.exceptions import NotFound

from checkpoint import Checkpoint


@pytest.fixture
def mock_gcs():
    with patch("checkpoint.storage.Client") as MockClient:
        mock_blob = MagicMock()
        MockClient.return_value.bucket.return_value.blob.return_value = mock_blob
        yield Checkpoint("test-bucket", "chapi", "purbalingga"), mock_blob


async def test_read_returns_dict(mock_gcs):
    cp, mock_blob = mock_gcs
    state = {"page_token": "abc123", "count": 42}
    mock_blob.download_as_text.return_value = json.dumps(state)

    result = await cp.read()

    assert result == state


async def test_read_returns_none_on_not_found(mock_gcs):
    cp, mock_blob = mock_gcs
    mock_blob.download_as_text.side_effect = NotFound("blob")

    result = await cp.read()

    assert result is None


async def test_write_uploads_json(mock_gcs):
    cp, mock_blob = mock_gcs
    state = {"page_token": "tok999", "count": 7}

    await cp.write(state)

    mock_blob.upload_from_string.assert_called_once_with(
        json.dumps(state), content_type="application/json"
    )


async def test_delete_suppresses_not_found(mock_gcs):
    cp, mock_blob = mock_gcs
    mock_blob.delete.side_effect = NotFound("blob")

    await cp.delete()  # must not raise
