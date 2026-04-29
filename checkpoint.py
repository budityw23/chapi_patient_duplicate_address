import json
from typing import Optional

from google.api_core.exceptions import NotFound
from google.cloud import storage


class Checkpoint:
    def __init__(self, bucket: str, server: str, tenant: str):
        self._client = storage.Client()
        self._blob = self._client.bucket(bucket).blob(
            f"checkpoint/{server}/{tenant}/state.json"
        )

    def read(self) -> Optional[dict]:
        try:
            return json.loads(self._blob.download_as_text())
        except NotFound:
            return None

    def write(self, state: dict) -> None:
        self._blob.upload_from_string(json.dumps(state), content_type="application/json")

    def delete(self) -> None:
        try:
            self._blob.delete()
        except NotFound:
            pass
