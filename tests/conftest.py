"""Fixtures: a real Nestling server with an owner, a family and a child.

The server comes from `NESTLING_URL` (already running, e.g. in CI) or `NESTLING_BIN` (a built
`nestling` binary, started here on a free port with an empty database). Without either, the
tests that need it are skipped.
"""

from __future__ import annotations

from collections.abc import Generator
import json
import os
import socket
import subprocess
import tempfile
import time
from typing import Any
import urllib.error
import urllib.request
import uuid

import pytest

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def call(url: str, method: str, path: str, token: str | None = None, body: Any = None) -> Any:
    req = urllib.request.Request(f"{url}/api/v1{path}", method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, data, timeout=10) as res:
        raw = res.read()
        return json.loads(raw) if raw else None


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server:
    """Started on first use (sockets are only allowed inside a test)."""

    def __init__(self) -> None:
        self.url: str | None = os.environ.get("NESTLING_URL", "").rstrip("/") or None
        self.proc: subprocess.Popen | None = None
        self.tmp: tempfile.TemporaryDirectory | None = None

    def start(self) -> str:
        if self.url:
            return self.url
        binary = os.environ.get("NESTLING_BIN")
        if not binary:
            pytest.skip("set NESTLING_URL or NESTLING_BIN to run against a Nestling server")
        port = _free_port()
        self.tmp = tempfile.TemporaryDirectory()
        env = {
            **os.environ,
            "NESTLING_BIND": f"127.0.0.1:{port}",
            "NESTLING_DATABASE_URL": f"sqlite://{self.tmp.name}/nestling.db?mode=rwc",
            "NESTLING_OPEN_REGISTRATION": "true",
        }
        self.proc = subprocess.Popen([binary], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                call(url, "GET", "/auth/setup")
                break
            except (urllib.error.URLError, ConnectionError):
                time.sleep(0.1)
        self.url = url
        return url

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
            self.proc.wait(10)
        if self.tmp:
            self.tmp.cleanup()


@pytest.fixture(scope="session")
def server() -> Generator[_Server]:
    server = _Server()
    yield server
    server.stop()


@pytest.fixture
def nestling(server: _Server, socket_enabled) -> dict[str, Any]:
    """A fresh owner, family and child on the server (each test gets its own)."""
    server_url = server.start()
    email = f"owner-{uuid.uuid4().hex[:8]}@example.com"
    setup = call(server_url, "GET", "/auth/setup")
    if setup["needs_setup"] or setup["open_registration"]:
        owner = call(server_url, "POST", "/auth/register", body={"email": email, "password": PASSWORD, "name": "Alex"})["token"]
    else:
        pytest.skip("the server needs open registration (NESTLING_OPEN_REGISTRATION=true)")
    family = call(server_url, "POST", "/families", owner, {"name": "Home", "timezone": "America/Toronto"})
    child = call(server_url, "POST", f"/families/{family['id']}/children", owner, {"name": "Robin", "birth_date": "2026-06-01"})
    return {"url": server_url, "email": email, "owner": owner, "family_id": family["id"], "child_id": child["id"]}


def connected_app(nestling: dict[str, Any], name: str = "Home Assistant") -> dict[str, Any]:
    """Add a connected app to the family; skips on servers without connected apps."""
    try:
        return call(nestling["url"], "POST", f"/families/{nestling['family_id']}/integrations", nestling["owner"], {"name": name})
    except urllib.error.HTTPError as e:
        if e.code == 404:
            pytest.skip("this Nestling server has no connected apps yet")
        raise
