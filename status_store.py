import asyncio
import json
from typing import Optional

from google.api_core.exceptions import NotFound
from google.cloud import storage


class StatusStore:
    """
    Stores per-run status JSON in GCS at:
      status/{server}/{tenant}/{run_id}.json
    inside the same bucket used by Checkpoint.
    """

    def __init__(self, bucket: str, server: str, tenant: str) -> None:
        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket)
        self._server = server
        self._tenant = tenant

    def _blob(self, run_id: str):
        return self._bucket.blob(f"status/{self._server}/{self._tenant}/{run_id}.json")

    async def write_running(
        self,
        run_id: str,
        tenant: str,
        server: str,
        mode: str,
        dry_run: bool,
        started_at: str,
    ) -> None:
        record = {
            "run_id": run_id,
            "status": "running",
            "tenant": tenant,
            "server": server,
            "mode": mode,
            "dry_run": dry_run,
            "started_at": started_at,
            "finished_at": None,
            "summary": None,
            "error": None,
        }
        blob = self._blob(run_id)

        def _write():
            blob.upload_from_string(json.dumps(record), content_type="application/json")

        await asyncio.to_thread(_write)

    async def write_final(
        self,
        run_id: str,
        status: str,
        finished_at: str,
        summary: Optional[dict],
        error: Optional[str],
    ) -> None:
        blob = self._blob(run_id)

        def _read_merge_write():
            try:
                existing = json.loads(blob.download_as_text())
            except NotFound:
                existing = {"run_id": run_id}
            existing.update(
                {
                    "status": status,
                    "finished_at": finished_at,
                    "summary": summary,
                    "error": error,
                }
            )
            blob.upload_from_string(json.dumps(existing), content_type="application/json")

        await asyncio.to_thread(_read_merge_write)

    async def read(self, run_id: str) -> Optional[dict]:
        blob = self._blob(run_id)

        def _read():
            try:
                return json.loads(blob.download_as_text())
            except NotFound:
                return None

        return await asyncio.to_thread(_read)
