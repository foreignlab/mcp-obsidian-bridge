"""Recent Changes must use the REST API 5.1.0 JSONLogic contract."""

import asyncio
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading
import time
from unittest.mock import patch

import pytest
import requests

from mcp_obsidian import obsidian


class FrozenDateTime(datetime):
    current = datetime(2026, 9, 22, 13, 45)

    @classmethod
    def now(cls, tz=None):
        assert tz is None
        return cls.current


@pytest.fixture
def clock(monkeypatch):
    with monkeypatch.context() as context:
        context.setenv("TZ", "Asia/Tokyo")
        time.tzset()
        context.setattr(obsidian, "datetime", FrozenDateTime, raising=False)
        context.setattr(FrozenDateTime, "current", datetime(2026, 9, 22, 13, 45))
        yield context
    time.tzset()


def response(payload, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    return result


def test_recent_changes_sorts_limits_and_preserves_table_result(clock):
    payload = [
        {"filename": "older.md", "result": 1789988400000},
        {"filename": "最新.md", "result": 1790035200123},
        {"filename": "boundary.md", "result": 1788793200000},
    ]
    with patch("mcp_obsidian.obsidian.requests.post", return_value=response(payload)) as post:
        actual = obsidian.Obsidian("test-key").get_recent_changes(limit=2, days=14)

    assert actual == [
        {"filename": "最新.md", "result": {"file.mtime": "2026-09-22T09:00:00.123+09:00"}},
        {"filename": "older.md", "result": {"file.mtime": "2026-09-21T20:00:00.000+09:00"}},
    ]
    assert post.call_args.args == ("https://127.0.0.1:27124/search/",)
    assert post.call_args.kwargs == {
        "headers": {
            "Authorization": "Bearer test-key",
            "Content-Type": "application/vnd.olrapi.jsonlogic+json",
        },
        "json": {"if": [{">=": [{"var": "stat.mtime"}, 1788793200000]}, {"var": "stat.mtime"}, False]},
        "verify": True,
        "timeout": (3, 6),
    }


def test_recent_changes_empty_result(clock):
    with patch("mcp_obsidian.obsidian.requests.post", return_value=response([])):
        assert obsidian.Obsidian("test-key").get_recent_changes() == []


def test_recent_changes_equal_timestamps_use_filename_order(clock):
    payload = [
        {"filename": "z.md", "result": 1790035200123},
        {"filename": "a.md", "result": 1790035200123},
    ]
    with patch("mcp_obsidian.obsidian.requests.post", return_value=response(payload)):
        actual = obsidian.Obsidian("test-key").get_recent_changes(limit=1)
    assert actual == [
        {"filename": "a.md", "result": {"file.mtime": "2026-09-22T09:00:00.123+09:00"}},
    ]


@pytest.mark.parametrize(("zone", "now", "days", "cutoff"), [
    ("UTC", datetime(2026, 9, 22, 13, 45), 14, 1788825600000),
    ("Asia/Tokyo", datetime(2026, 9, 22, 0, 0), 14, 1788793200000),
    ("Asia/Tokyo", datetime(2026, 9, 22, 23, 59, 59), 14, 1788793200000),
    ("America/New_York", datetime(2026, 3, 9, 13), 1, 1772946000000),
    ("America/New_York", datetime(2026, 11, 2, 13), 1, 1793505600000),
])
def test_recent_changes_uses_calendar_midnight_including_dst(clock, zone, now, days, cutoff):
    clock.setenv("TZ", zone)
    time.tzset()
    clock.setattr(FrozenDateTime, "current", now)
    with patch("mcp_obsidian.obsidian.requests.post", return_value=response([])) as post:
        obsidian.Obsidian("test-key").get_recent_changes(days=days)
    assert post.call_args.kwargs["json"] == {
        "if": [{">=": [{"var": "stat.mtime"}, cutoff]}, {"var": "stat.mtime"}, False],
    }


def test_recent_changes_preserves_api_error(clock):
    error = response({"errorCode": 40070, "message": "Invalid filter query"}, status=400)
    with patch("mcp_obsidian.obsidian.requests.post", return_value=error):
        with pytest.raises(Exception, match="Error 40070: Invalid filter query"):
            obsidian.Obsidian("test-key").get_recent_changes()


def test_recent_changes_preserves_transport_error(clock):
    with patch("mcp_obsidian.obsidian.requests.post", side_effect=requests.Timeout("timed out")):
        with pytest.raises(Exception, match="Request failed: timed out"):
            obsidian.Obsidian("test-key").get_recent_changes()


@pytest.mark.parametrize("via_gateway", [False, True])
def test_recent_changes_rest_contract_includes_boundary(clock, tmp_path, via_gateway):
    """Reject DQL like 5.1.0; require the inclusive, millisecond JSON query."""
    expected_query = {
        "if": [{">=": [{"var": "stat.mtime"}, 1788793200000]}, {"var": "stat.mtime"}, False],
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Type") != "application/vnd.olrapi.jsonlogic+json":
                status, payload = 400, {"errorCode": 40012, "message": "Invalid Content-Type"}
            elif self.path != "/search/" or json.loads(body) != expected_query:
                status, payload = 400, {"errorCode": 40070, "message": "Unexpected query"}
            else:
                status, payload = 200, [
                    {"filename": "boundary.md", "result": 1788793200000},
                    {"filename": "latest.md", "result": 1790035200123},
                ]
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    clock.setenv("NO_PROXY", "*")
    api = obsidian.Obsidian("test-key", protocol="http", host="127.0.0.1", port=server.server_port)
    try:
        if via_gateway:
            from gateway import create_gateway

            clock.setattr(obsidian, "Obsidian", lambda **kwargs: api)
            _, _, call_tool = create_gateway(tmp_path)
            contents = asyncio.run(call_tool("obsidian_get_recent_changes", {"limit": 10, "days": 14}))
            actual = json.loads(contents[0].text)
        else:
            actual = api.get_recent_changes(limit=10, days=14)
        assert actual == [
            {"filename": "latest.md", "result": {"file.mtime": "2026-09-22T09:00:00.123+09:00"}},
            {"filename": "boundary.md", "result": {"file.mtime": "2026-09-08T00:00:00.000+09:00"}},
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
