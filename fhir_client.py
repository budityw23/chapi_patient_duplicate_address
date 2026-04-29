import re
from collections.abc import AsyncIterator
from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse

import httpx


class ChapiClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    async def __aenter__(self) -> "ChapiClient":
        self._client = httpx.AsyncClient(
            headers={"X-API-Key": self.api_key, "Accept": "application/fhir+json"},
            timeout=self.timeout,
        )
        return self

    async def __aexit__(self, *args) -> None:
        await self._client.aclose()

    async def iter_patient_bundles(self, initial_url: str) -> AsyncIterator[dict]:
        url = initial_url
        while url:
            resp = await self._client.get(url)
            resp.raise_for_status()
            bundle = resp.json()
            yield bundle
            next_raw = None
            for link in bundle.get("link", []):
                if link.get("relation") == "next":
                    next_raw = link["url"]
                    break
            url = self._rebuild_next_url(initial_url, next_raw) if next_raw else None

    def _rebuild_next_url(self, initial_url: str, next_url: str) -> str:
        next_params = parse_qs(urlparse(next_url).query, keep_blank_values=True)
        page_token = next_params.get("_page_token", [None])[0]

        initial_parsed = urlparse(initial_url)
        params = {k: v[0] for k, v in parse_qs(initial_parsed.query, keep_blank_values=True).items()}
        if page_token:
            params["_page_token"] = page_token

        return f"{initial_parsed.scheme}://{initial_parsed.netloc}{initial_parsed.path}?{urlencode(params)}"

    async def get_patient(self, patient_id: str) -> tuple[dict, str]:
        url = f"{self.base_url}/Patient/{patient_id}"
        resp = await self._client.get(url)
        resp.raise_for_status()
        resource = resp.json()
        version_id = resource["meta"]["versionId"]
        return resource, version_id

    async def put_patient(
        self, patient_id: str, version_id: str, resource: dict
    ) -> tuple[Optional[str], Optional[tuple[int, dict]]]:
        url = f"{self.base_url}/Patient/{patient_id}"
        headers = {
            "Content-Type": "application/fhir+json",
            "If-Match": f'W/"{version_id}"',
        }
        resp = await self._client.put(url, json=resource, headers=headers)
        if resp.status_code in (200, 201):
            try:
                new_vid = resp.json()["meta"]["versionId"]
            except Exception:
                etag = resp.headers.get("ETag", "")
                m = re.match(r'W/"([^"]+)"', etag)
                new_vid = m.group(1) if m else etag
            return new_vid, None
        else:
            try:
                body = resp.json()
            except Exception:
                body = {"raw": resp.text}
            return None, (resp.status_code, body)
