import httpx

from services.tomtom_service import TomTomService


def test_tomtom_flow_request_uses_key_and_documented_kmph_unit():
    observed = {}

    def handler(request):
        observed["url"] = str(request.url)
        observed["params"] = dict(request.url.params)
        return httpx.Response(200, json={
            "flowSegmentData": {
                "currentSpeed": 42,
                "freeFlowSpeed": 55,
                "currentTravelTime": 31,
                "freeFlowTravelTime": 24,
                "confidence": 0.88,
                "roadClosure": False,
            }
        })

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = TomTomService(api_key="test-key", client=client)
    result = service.flow_at(20.2961, 85.8245)

    assert result["status"] == "ok"
    assert result["is_live"] is True  # mocked endpoint response, not a live-network test
    assert observed["params"]["key"] == "test-key"
    assert observed["params"]["unit"] == "kmph"
    assert observed["params"]["point"] == "20.2961,85.8245"
    assert result["data"]["flowSegmentData"]["currentSpeed"] == 42


def test_tomtom_incident_request_uses_key_and_bbox():
    observed = {}

    def handler(request):
        observed["url"] = str(request.url)
        observed["params"] = dict(request.url.params)
        return httpx.Response(200, json={"incidents": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    service = TomTomService(api_key="test-key", client=client)
    result = service.incidents(85.78, 20.25, 85.90, 20.34)
    assert result["status"] == "ok"
    assert observed["params"]["key"] == "test-key"
    assert observed["params"]["bbox"] == "85.78,20.25,85.9,20.34"
    assert "/traffic/services/5/incidentDetails" in observed["url"]


def test_tomtom_without_api_key_is_explicitly_disabled():
    result = TomTomService(api_key="").flow_at(20.2961, 85.8245)
    assert result["status"] == "disabled"
    assert result["is_live"] is False
    assert "not configured" in result["message"]
