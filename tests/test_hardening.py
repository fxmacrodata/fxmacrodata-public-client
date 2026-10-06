"""Regression tests for 0.1.2: header authentication and transport hardening."""
import copy
import json
import logging
import pickle
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fxmacrodata_public import FXMacroDataClient, FXMacroDataError, IncompleteHistoryError, __version__
from fxmacrodata_public.pagination import collect_history
from test_client import Response, Session, initialized_session

KEY = "FXMD-synthetic-unit-test-key-0123456789"


def page(rows, offset=0, total=None, **extra):
    total = len(rows) if total is None else total
    more = offset + len(rows) < total
    meta = {"limit": 100, "offset": offset, "returned_count": len(rows), "total_count": total,
            "has_more": more, "next_offset": offset + len(rows) if more else None}
    meta.update(extra)
    return {"data": rows, "pagination": meta}


# --- REST key transport ---------------------------------------------------

def test_rest_key_is_sent_only_in_x_api_key_header():
    transport = Session(Response({"data": []}))
    FXMacroDataClient(api_key=KEY, session=transport).execute("indicator_history", {"currency": "usd", "indicator": "inflation"})
    method, url, options = transport.calls[0]
    assert options["headers"]["X-API-Key"] == KEY
    assert "api_key" not in options["params"] and KEY not in url
    assert KEY not in json.dumps(options["params"]) and options["allow_redirects"] is False


def test_history_path_and_stream_use_header_not_query():
    transport = Session(Response(page([{"date": "2026-01-01"}])),
                        Response(headers={"Content-Type": "text/event-stream"}, lines=[b"data: {}", b""]))
    client = FXMacroDataClient(api_key=KEY, session=transport)
    client.history_path("/v1/announcements/usd/inflation")
    client.execute("stream_events", {"max_events": 1})
    for _, url, options in transport.calls:
        assert options["headers"]["X-API-Key"] == KEY
        assert "api_key" not in options["params"] and KEY not in url


def test_anonymous_requests_send_no_credential_header():
    transport = Session(Response({"data": []}))
    FXMacroDataClient(api_key="", session=transport).execute("release_calendar", {"currency": "usd"})
    headers = transport.calls[0][2]["headers"]
    assert "X-API-Key" not in headers and "Authorization" not in headers


def test_environment_key_is_trimmed_and_used(monkeypatch):
    monkeypatch.setenv("FXMACRODATA_API_KEY", f"  {KEY}\n")
    transport = Session(Response({"status": "ok"}))
    FXMacroDataClient(session=transport).execute("ping")
    assert transport.calls[0][2]["headers"]["X-API-Key"] == KEY


@pytest.mark.parametrize("arguments", [{"X-API-Key": "other"}, {"x-api-key": "other"}, {"Authorization": "Bearer other"}])
def test_credential_headers_cannot_be_operation_arguments(arguments):
    transport = Session()
    with pytest.raises(FXMacroDataError):
        FXMacroDataClient(api_key="", session=transport).execute("ping", arguments)
    with pytest.raises(FXMacroDataError):
        FXMacroDataClient(api_key="", session=transport).history_path("/v1/announcements/usd/inflation", arguments)
    assert not transport.calls


def test_mcp_uses_bearer_header_and_never_stores_key_in_session_headers():
    payload = {"content": [{"type": "text", "text": "ok"}], "isError": False}
    transport = initialized_session(Response({"jsonrpc": "2.0", "id": 2, "result": payload}))
    client = FXMacroDataClient(api_key=KEY, session=transport)
    client.execute("mcp_ping")
    for _, url, options in transport.calls:
        assert options["headers"]["Authorization"] == f"Bearer {KEY}"
        assert "api_key" not in options["params"] and KEY not in url
    assert KEY not in json.dumps(client._mcp_headers)


def test_mcp_short_legacy_key_keeps_documented_query_fallback():
    transport = initialized_session(Response({"jsonrpc": "2.0", "id": 2, "result": {"content": []}}))
    FXMacroDataClient(api_key="short-key", session=transport).execute("mcp_ping")
    assert all(c[2]["params"] == {"api_key": "short-key"} and "Authorization" not in c[2]["headers"] for c in transport.calls)


# --- Redirects --------------------------------------------------------------

@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_is_not_followed_and_names_no_location(status):
    response = Response({}, status=status, headers={"Location": "https://attacker.example/steal"})
    transport = Session(response)
    with pytest.raises(FXMacroDataError, match="redirect") as error:
        FXMacroDataClient(api_key=KEY, session=transport).execute("ping")
    assert len(transport.calls) == 1 and transport.calls[0][2]["allow_redirects"] is False
    assert "attacker" not in str(error.value) and KEY not in str(error.value) and response.closed


def test_redirect_on_mcp_is_not_followed():
    response = Response({}, status=307, headers={"Location": "https://attacker.example/mcp"})
    transport = Session(response)
    with pytest.raises(FXMacroDataError, match="redirect"):
        FXMacroDataClient(api_key=KEY, session=transport).execute("mcp_ping")
    assert len(transport.calls) == 1


def test_real_session_does_not_follow_redirect_with_key(monkeypatch):
    """Exercise requests' own redirect machinery, not only the flag we pass."""
    import requests
    from requests.adapters import HTTPAdapter

    sent = []

    class FakeAdapter(HTTPAdapter):
        def send(self, request, **kwargs):
            sent.append(request)
            response = requests.Response()
            response.status_code = 302
            response.headers["Location"] = "https://attacker.example/steal"
            response.url = request.url
            response.request = request
            response.raw = None
            response._content = b""
            return response

    session = requests.Session()
    session.mount("https://", FakeAdapter())
    with pytest.raises(FXMacroDataError, match="redirect"):
        FXMacroDataClient(api_key=KEY, session=session).execute("ping")
    assert [r.url for r in sent] == ["https://api.fxmacrodata.com/v1/ping"]
    assert sent[0].headers["X-API-Key"] == KEY


# --- Key masking and validation ----------------------------------------------

def test_key_is_stored_masked():
    client = FXMacroDataClient(api_key=KEY, session=Session())
    for text in (repr(client), str(client), repr(vars(client)), repr(client._credential), str(client._credential)):
        assert KEY not in text
    assert "_api_key" not in vars(client)
    with pytest.raises(TypeError):
        pickle.dumps(client._credential)
    with pytest.raises(TypeError):
        copy.deepcopy(client._credential)


def test_subclasses_can_still_read_the_key_as_a_string():
    """deerflow/hermes integrations pass self._api_key to their own redaction."""
    class Integration(FXMacroDataClient):
        def _safe(self, value):
            assert isinstance(self._api_key, str)
            return super()._safe(value)

    result = Integration(api_key=KEY, session=Session(Response({"echo": KEY}))).execute("ping")
    assert result.payload == {"echo": "[redacted]"}


@pytest.mark.parametrize("key", ["bad\r\nkey", "bad\x00key", "ключ-unicode-key", "tab\tinside"])
def test_unsendable_key_is_rejected_at_creation_without_echo(key):
    with pytest.raises(FXMacroDataError, match="HTTP header") as error:
        FXMacroDataClient(api_key=key, session=Session())
    assert key not in str(error.value)


def test_key_never_appears_in_errors_or_logs(caplog):
    import requests
    transport = Session(requests.ConnectionError(f"X-API-Key: {KEY}"), Response({"detail": KEY}, status=500),
                        Response({"error": KEY}))
    client = FXMacroDataClient(api_key=KEY, session=transport)
    with caplog.at_level(logging.DEBUG):
        for _ in range(3):
            with pytest.raises(FXMacroDataError) as error:
                client.execute("ping")
            assert KEY not in str(error.value) and error.value.__cause__ is None
            assert KEY not in repr(error.value.__context__ or "")
    assert KEY not in caplog.text


# --- Configuration ----------------------------------------------------------

@pytest.mark.parametrize("url", ["http://api.fxmacrodata.com", "ftp://api.fxmacrodata.com", "api.fxmacrodata.com",
                                 "https://user:pass@api.fxmacrodata.com", "https://api.fxmacrodata.com?api_key=x",
                                 "https://api.fxmacrodata.com#x", "https://api.fxmacrodata.com/v1", "https://",
                                 "https://api.fxmacrodata.com:notaport", "https://api.fxmacrodata.com/ space", "", None, 1])
def test_base_url_must_be_plain_https_origin(url):
    with pytest.raises(FXMacroDataError, match="base_url"):
        FXMacroDataClient(api_key="", base_url=url, session=Session())


@pytest.mark.parametrize("url", ["http://mcp.fxmacrodata.com/mcp", "https://mcp.fxmacrodata.com/mcp?x=1"])
def test_mcp_url_must_be_https(url):
    with pytest.raises(FXMacroDataError, match="mcp_url"):
        FXMacroDataClient(api_key="", mcp_url=url, session=Session())


def test_custom_https_base_url_is_used_for_rest_and_source_url():
    transport = Session(Response({"status": "ok"}))
    result = FXMacroDataClient(api_key=KEY, base_url="https://API.example.test/", session=transport).execute("ping")
    assert transport.calls[0][1] == "https://api.example.test/v1/ping"
    assert result.source_url == "https://api.example.test/v1/ping"


def test_default_timeout_is_finite_and_sent_on_every_request():
    transport = Session(Response({"status": "ok"}))
    client = FXMacroDataClient(api_key="", session=transport)
    client.execute("ping")
    assert client.timeout == 30 and transport.calls[0][2]["timeout"] == 30


def test_version_metadata_is_consistent():
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in pyproject


# --- HTTP 200 with an error body, wrong shape, non-JSON ----------------------

@pytest.mark.parametrize("payload", [
    {"error": "subscription_required", "message": "private remote detail"},
    {"detail": "Not authenticated"},
    {"success": False, "data": []},
    {"status": "error", "message": "private remote detail"},
    {"errors": [{"msg": "private remote detail"}]},
])
def test_http_200_error_body_raises_typed_error(payload):
    with pytest.raises(FXMacroDataError, match="reported an error") as error:
        FXMacroDataClient(api_key=KEY, session=Session(Response(payload))).execute("ping")
    assert "private remote detail" not in str(error.value)


def test_http_200_error_code_is_surfaced_when_safe():
    with pytest.raises(FXMacroDataError, match=r"\(subscription_required\)"):
        FXMacroDataClient(api_key="", session=Session(Response({"error": "subscription_required"}))).execute("ping")
    with pytest.raises(FXMacroDataError) as error:
        FXMacroDataClient(api_key=KEY, session=Session(Response({"error": KEY + " leaked"}))).execute("ping")
    assert KEY not in str(error.value)


@pytest.mark.parametrize("payload", [None, "text", 42, True])
def test_http_200_wrong_shape_raises_typed_error(payload):
    with pytest.raises(FXMacroDataError, match="unexpected response shape"):
        FXMacroDataClient(api_key="", session=Session(Response(payload))).execute("ping")


@pytest.mark.parametrize("body", [b"<html>maintenance</html>", b"", b"\xff\xfe", b'{"data": ['])
def test_http_200_non_json_raises_typed_error(body):
    response = Response(headers={"Content-Type": "text/html"})
    response.content = body
    with pytest.raises(FXMacroDataError, match="invalid response"):
        FXMacroDataClient(api_key="", session=Session(response)).execute("ping")
    assert response.closed


def test_successful_payloads_with_error_like_fields_are_kept():
    for payload in ({"status": "ok"}, {"data": [], "error": None}, {"data": [{"error": "x"}], "errors": []}, []):
        result = FXMacroDataClient(api_key="", session=Session(Response(payload))).execute("ping")
        assert result.payload == payload


def test_history_path_200_error_body_is_an_error():
    with pytest.raises(IncompleteHistoryError, match="reported an error"):
        FXMacroDataClient(api_key="", session=Session(Response({"detail": "x"}))).history_path("/v1/announcements/usd/inflation")


def test_arbitrary_fetch_error_text_is_not_repeated_in_history_error():
    def fetch(_):
        raise RuntimeError(f"api_key={KEY}")
    with pytest.raises(IncompleteHistoryError) as error:
        collect_history(fetch)
    assert KEY not in str(error.value)


# --- Pagination ------------------------------------------------------------

@pytest.mark.parametrize("field,value", [
    ("has_more", "true"), ("has_more", 1), ("offset", "0"), ("offset", -1), ("offset", True),
    ("returned_count", "1"), ("returned_count", 1.0), ("total_count", "2"), ("total_count", -2),
    ("limit", "100"), ("next_offset", "1"), ("next_offset", 1.5), ("next_offset", True),
])
def test_present_but_malformed_pagination_field_is_an_error(field, value):
    first = page([{"date": "2026-01-01"}], total=2)
    first["pagination"][field] = value
    with pytest.raises(IncompleteHistoryError):
        collect_history(lambda _: first)


@pytest.mark.parametrize("bad", ["not-a-dict", [], 0, False])
def test_non_object_pagination_is_an_error(bad):
    with pytest.raises(IncompleteHistoryError, match="invalid"):
        collect_history(lambda _: {"data": [], "pagination": bad})


@pytest.mark.parametrize("next_offset", [0, 5, None])
def test_next_offset_must_advance_contiguously(next_offset):
    first = page([{"date": "2026-01-01"}], total=3)
    first["pagination"]["next_offset"] = next_offset
    with pytest.raises(IncompleteHistoryError, match="advance"):
        collect_history(lambda _: first)


def test_final_page_cannot_report_a_further_offset():
    last = page([{"date": "2026-01-01"}])
    last["pagination"]["next_offset"] = 7
    with pytest.raises(IncompleteHistoryError, match="further offset"):
        collect_history(lambda _: last)


def test_page_beyond_reported_total_is_an_error():
    bad = page([{"date": "2026-01-01"}, {"date": "2026-01-02"}], total=1)
    bad["pagination"].update(has_more=False, next_offset=None)
    with pytest.raises(IncompleteHistoryError, match="beyond"):
        collect_history(lambda _: bad)


def test_contract_shaped_pages_still_complete():
    rows = [{"date": f"2026-01-0{i}"} for i in range(1, 6)]
    result = collect_history(lambda q: page(rows[q["offset"]:q["offset"] + 2], q["offset"], total=5), {"limit": 2})
    assert result["data"] == rows and result["complete"]


@pytest.mark.parametrize("params", [{"offset": "x"}, {"offset": -1}, {"limit": 0}, {"limit": True}])
def test_invalid_requested_offset_or_limit_fails_before_fetch(params):
    calls = []
    with pytest.raises(ValueError):
        collect_history(lambda q: calls.append(q) or page([]), params)
    assert not calls


@pytest.mark.parametrize("resume", ["checkpoint", {"next_offset": "1"}, {"next_offset": -1}, {"next_offset": None}])
def test_malformed_resume_checkpoint_is_rejected(resume):
    from fxmacrodata_public.pagination import iter_pages
    first = page([{"date": "2026-01-01"}], total=2)
    checkpoint = next(iter_pages(lambda _: first))["client_resume"]
    if isinstance(resume, dict):
        resume = {**checkpoint, **resume}
    with pytest.raises(IncompleteHistoryError):
        list(iter_pages(lambda _: first, resume=resume))
