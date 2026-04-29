import re

import httpx
import pytest
import respx

from fhir_client import ChapiClient

BASE = "https://chapi.example.com/fhir"
KEY = "test-key"

# Matches the Patient list endpoint (with query params) but not Patient/{id}
PATIENT_LIST_PAT = re.compile(r"chapi\.example\.com/fhir/Patient\?")


async def test_iter_patient_bundles_single_page():
    url = f"{BASE}/Patient?_count=1000"
    bundle = {"resourceType": "Bundle", "link": []}

    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get(url).mock(return_value=httpx.Response(200, json=bundle))
        async with ChapiClient(BASE, KEY) as c:
            result = [b async for b in c.iter_patient_bundles(url)]

    assert result == [bundle]


async def test_iter_patient_bundles_two_pages():
    url1 = f"{BASE}/Patient?_count=1000"
    chapi_next = "https://healthcare.googleapis.com/fhir/Patient?_page_token=tok123"
    bundle1 = {"resourceType": "Bundle", "link": [{"relation": "next", "url": chapi_next}]}
    bundle2 = {"resourceType": "Bundle", "link": []}
    responses_iter = iter([httpx.Response(200, json=bundle1), httpx.Response(200, json=bundle2)])

    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get(PATIENT_LIST_PAT).mock(side_effect=lambda req: next(responses_iter))
        async with ChapiClient(BASE, KEY) as c:
            result = [b async for b in c.iter_patient_bundles(url1)]
        second_url = str(mock.calls[-1].request.url)

    assert result == [bundle1, bundle2]
    assert "_page_token=tok123" in second_url


async def test_iter_patient_bundles_preserves_last_updated():
    url1 = f"{BASE}/Patient?_count=1000&_lastUpdated=ge2024-01-01"
    chapi_next = "https://healthcare.googleapis.com/fhir/Patient?_page_token=tok456"
    bundle1 = {"resourceType": "Bundle", "link": [{"relation": "next", "url": chapi_next}]}
    bundle2 = {"resourceType": "Bundle", "link": []}
    responses_iter = iter([httpx.Response(200, json=bundle1), httpx.Response(200, json=bundle2)])

    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get(PATIENT_LIST_PAT).mock(side_effect=lambda req: next(responses_iter))
        async with ChapiClient(BASE, KEY) as c:
            result = [b async for b in c.iter_patient_bundles(url1)]
        second_url = str(mock.calls[-1].request.url)

    assert len(result) == 2
    assert "_lastUpdated=ge2024-01-01" in second_url


async def test_get_patient_success():
    patient_id = "patient-123"
    resource = {"resourceType": "Patient", "id": patient_id, "meta": {"versionId": "3"}}

    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get(f"{BASE}/Patient/{patient_id}").mock(return_value=httpx.Response(200, json=resource))
        async with ChapiClient(BASE, KEY) as c:
            result_resource, version_id = await c.get_patient(patient_id)

    assert result_resource == resource
    assert version_id == "3"


async def test_put_patient_success_200():
    patient_id = "patient-456"
    resource = {"resourceType": "Patient", "id": patient_id}
    response_body = {"resourceType": "Patient", "id": patient_id, "meta": {"versionId": "4"}}

    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.put(f"{BASE}/Patient/{patient_id}").mock(return_value=httpx.Response(200, json=response_body))
        async with ChapiClient(BASE, KEY) as c:
            new_vid, err = await c.put_patient(patient_id, "3", resource)

    assert new_vid == "4"
    assert err is None


async def test_put_patient_412():
    patient_id = "patient-789"
    resource = {"resourceType": "Patient", "id": patient_id}
    body_dict = {"resourceType": "OperationOutcome", "issue": []}

    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.put(f"{BASE}/Patient/{patient_id}").mock(return_value=httpx.Response(412, json=body_dict))
        async with ChapiClient(BASE, KEY) as c:
            new_vid, err = await c.put_patient(patient_id, "2", resource)

    assert new_vid is None
    assert err == (412, body_dict)


async def test_put_patient_500():
    patient_id = "patient-abc"
    resource = {"resourceType": "Patient", "id": patient_id}
    body_dict = {"error": "internal server error"}

    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.put(f"{BASE}/Patient/{patient_id}").mock(return_value=httpx.Response(500, json=body_dict))
        async with ChapiClient(BASE, KEY) as c:
            new_vid, err = await c.put_patient(patient_id, "1", resource)

    assert new_vid is None
    status_code, _ = err
    assert status_code == 500


async def test_get_patient_raises_on_404():
    patient_id = "not-found"

    async with respx.MockRouter(assert_all_called=False) as mock:
        mock.get(f"{BASE}/Patient/{patient_id}").mock(
            return_value=httpx.Response(404, json={"resourceType": "OperationOutcome"})
        )
        with pytest.raises(httpx.HTTPStatusError):
            async with ChapiClient(BASE, KEY) as c:
                await c.get_patient(patient_id)
