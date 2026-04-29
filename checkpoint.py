import asyncio
import json
from typing import Optional

from google.api_core.exceptions import NotFound
from google.cloud import storage


class Checkpoint:
    def __init__(self, bucket: str, server: str, tenant: str) -> None:
        self._client = storage.Client()
        self._blob = self._client.bucket(bucket).blob(
            f"checkpoint/{server}/{tenant}/state.json"
        )

    async def read(self) -> Optional[dict]:
        def _read():
            try:
                return json.loads(self._blob.download_as_text())
            except NotFound:
                return None
        return await asyncio.to_thread(_read)

    async def write(self, state: dict) -> None:
        def _write():
            self._blob.upload_from_string(json.dumps(state), content_type="application/json")
        await asyncio.to_thread(_write)

    async def delete(self) -> None:
        def _delete():
            try:
                self._blob.delete()
            except NotFound:
                pass
        await asyncio.to_thread(_delete)
