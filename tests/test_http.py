import httpx
import pytest
import respx

from app.core.http import HttpError

URL = "https://api.example.com/x"


@respx.mock
async def test_retries_on_5xx_then_succeeds(http):
    route = respx.get(URL).mock(side_effect=[httpx.Response(503), httpx.Response(502), httpx.Response(200, json={"ok": 1})])
    assert await http.get_json(URL) == {"ok": 1}
    assert route.call_count == 3


@respx.mock
async def test_retries_on_transport_error(http):
    route = respx.get(URL).mock(side_effect=[httpx.ReadTimeout("t"), httpx.Response(200, json=[])])
    assert await http.get_json(URL) == []
    assert route.call_count == 2


@respx.mock
async def test_gives_up_after_max_retries(http):
    route = respx.get(URL).respond(429)
    with pytest.raises(HttpError, match="429"):
        await http.get_json(URL)
    assert route.call_count == 3  # 1 tentativa + 2 retries


@respx.mock
async def test_does_not_retry_client_errors(http):
    route = respx.get(URL).respond(400, json={"error": "bad"})
    with pytest.raises(HttpError, match="400"):
        await http.get_json(URL)
    assert route.call_count == 1


@respx.mock
async def test_invalid_json_raises(http):
    respx.get(URL).respond(200, text="<html>challenge</html>")
    with pytest.raises(HttpError, match="JSON"):
        await http.get_json(URL)


def test_backoff_honors_retry_after(http):
    assert http._backoff(0, "7") == 7.0
    assert http._backoff(0, "999") == 60.0
