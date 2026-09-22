"""cert-sync against a fake Nginx Proxy Manager API served by http.server.

The fake mirrors the upstream contract verified against NginxProxyManager/nginx-proxy-manager:
GET /api is unauthenticated and reports ``setup``; POST /api/users creates the first admin while
not set up; POST /api/tokens answers 400 on bad credentials; certificate creation is JSON with
``additionalProperties: false`` and returns 201; upload is multipart and returns 200.
"""
from __future__ import annotations

import json
import threading
from email import message_from_bytes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

import ca_core
from ca_core import CAError
from issue_cert import issue_cert
from mini_ca import APP
from npm_sync import NPMClient, npm_sync

runner = CliRunner()
ADMIN = ("admin@npm", "s3cret")


class FakeNPM:
    def __init__(self, users: dict[str, str] | None = None) -> None:
        self.users: dict[str, str] = dict(users or {})
        self.certificates: list[dict] = []
        self.uploads: list[tuple[int, dict[str, bytes]]] = []
        self.requests: list[tuple[str, str]] = []
        self.token = "tok-" + "x" * 16
        self.next_id = 7
        self.url = ""

    def handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):  # silence
                pass

            def _send(self, code: int, payload) -> None:
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> bytes:
                return self.rfile.read(int(self.headers.get("Content-Length", "0")))

            def _authed(self) -> bool:
                return self.headers.get("Authorization") == f"Bearer {fake.token}"

            def do_GET(self):
                fake.requests.append(("GET", self.path))
                if self.path in ("/api", "/api/"):
                    return self._send(200, {"status": "OK", "setup": bool(fake.users), "version": {"major": 2}})
                if self.path == "/api/nginx/certificates":
                    if not self._authed():
                        return self._send(403, {"error": {"message": "Permission Denied"}})
                    return self._send(200, fake.certificates)
                self._send(404, {"error": "nope"})

            def do_POST(self):
                fake.requests.append(("POST", self.path))
                body = self._body()
                if self.path == "/api/users" and not fake.users:
                    data = json.loads(body)
                    assert {"name", "nickname", "email"} <= set(data), data
                    fake.users[data["email"]] = data["auth"]["secret"]
                    return self._send(201, {"id": 1, "email": data["email"], "roles": ["admin"]})
                if self.path == "/api/tokens":
                    data = json.loads(body)
                    if fake.users.get(data.get("identity")) != data.get("secret"):
                        return self._send(400, {"error": {"code": 400, "message": "Invalid email or password"}})
                    return self._send(200, {"token": fake.token, "expires": "2099-01-01T00:00:00.000Z"})
                if not self._authed():
                    return self._send(403, {"error": {"message": "Permission Denied"}})
                if self.path == "/api/nginx/certificates":
                    if self.headers.get("Content-Type") != "application/json":
                        return self._send(400, {"error": "expected JSON"})
                    data = json.loads(body)
                    if set(data) - {"provider", "nice_name", "domain_names", "meta"} or data.get("provider") != "other":
                        return self._send(400, {"error": {"message": "data must NOT have additional properties"}})
                    record = {"id": fake.next_id, "provider": "other", "nice_name": data["nice_name"], "domain_names": []}
                    fake.next_id += 1
                    fake.certificates.append(record)
                    return self._send(201, record)
                if self.path.startswith("/api/nginx/certificates/") and self.path.endswith("/upload"):
                    cert_id = int(self.path.split("/")[-2])
                    if not any(c["id"] == cert_id for c in fake.certificates):
                        return self._send(404, {"error": "no such certificate"})
                    msg = message_from_bytes(b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
                    files = {
                        part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
                        for part in msg.get_payload()
                    }
                    fake.uploads.append((cert_id, files))
                    if "certificate" not in files or set(files) - {"certificate", "certificate_key", "intermediate_certificate"}:
                        return self._send(400, {"error": "bad files"})
                    return self._send(200, {"certificate": True, "certificate_key": True})
                self._send(404, {"error": "nope"})

        return Handler


def _serve(fake: FakeNPM):
    server = ThreadingHTTPServer(("127.0.0.1", 0), fake.handler())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fake.url = f"http://127.0.0.1:{server.server_address[1]}"
    return server


@pytest.fixture
def npm():
    fake = FakeNPM(users={ADMIN[0]: ADMIN[1]})
    server = _serve(fake)
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def fresh_npm():
    """An NPM that has never been set up (no admin user)."""
    fake = FakeNPM()
    server = _serve(fake)
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()


def test_sync_creates_then_updates_single_record(ca: Path, npm: FakeNPM) -> None:
    crt = issue_cert("blog.lan", ["www.blog.lan"], ca, ca_core.certs_dir())
    key = crt.with_suffix(".key")

    cert_id = npm_sync("blog.lan", ca_core.certs_dir(), npm.url, *ADMIN, wait=False)
    assert cert_id == 7
    assert [c["nice_name"] for c in npm.certificates] == ["blog.lan"]
    assert npm.uploads == [(7, {"certificate": crt.read_bytes(), "certificate_key": key.read_bytes()})]
    assert ("POST", "/api/users") not in npm.requests

    issue_cert("blog.lan", ["www.blog.lan"], ca, ca_core.certs_dir())  # renewal
    assert npm_sync("blog.lan", ca_core.certs_dir(), npm.url, *ADMIN, wait=False) == 7
    assert len(npm.certificates) == 1  # no duplicate record on re-sync
    assert npm.uploads[-1][1]["certificate"] == crt.read_bytes()
    assert npm.requests.count(("POST", "/api/nginx/certificates")) == 1


def test_sync_bootstraps_admin_on_fresh_npm(ca: Path, fresh_npm: FakeNPM) -> None:
    issue_cert("blog.lan", [], ca, ca_core.certs_dir())
    assert npm_sync("blog.lan", ca_core.certs_dir(), fresh_npm.url, *ADMIN, wait=True) == 7
    assert fresh_npm.users == {ADMIN[0]: ADMIN[1]}
    assert fresh_npm.requests[:3] == [("GET", "/api/"), ("POST", "/api/users"), ("POST", "/api/tokens")]


def test_sync_finds_wildcard_files(ca: Path, npm: FakeNPM) -> None:
    issue_cert("*.wild.dev", [], ca, ca_core.certs_dir())
    assert npm_sync("*.wild.dev", ca_core.certs_dir(), npm.url, *ADMIN, wait=True) == 7
    assert npm.requests[0] == ("GET", "/api/")
    assert npm.certificates[0]["nice_name"] == "*.wild.dev"


def test_sync_errors_are_readable(ca: Path, npm: FakeNPM) -> None:
    with pytest.raises(CAError, match="run 'issue' first"):
        npm_sync("nothing.lan", ca_core.certs_dir(), npm.url, *ADMIN, wait=False)

    issue_cert("blog.lan", [], ca, ca_core.certs_dir())
    with pytest.raises(CAError, match="HTTP 400.*Invalid email or password"):
        npm_sync("blog.lan", ca_core.certs_dir(), npm.url, "admin@npm", "wrong", wait=False)
    with pytest.raises(CAError, match="not reachable"):
        npm_sync("blog.lan", ca_core.certs_dir(), "http://127.0.0.1:9", *ADMIN, wait=False)


def test_client_wait_ready_gives_up_and_2fa_is_explained(npm: FakeNPM) -> None:
    client = NPMClient("http://127.0.0.1:9", timeout=0.5)
    with pytest.raises(CAError):
        client.wait_ready(attempts=2, delay=0)

    client = NPMClient(npm.url)
    client._json = lambda *a, **k: {"requires_2fa": True, "challenge_token": "x"}  # type: ignore[method-assign]
    with pytest.raises(CAError, match="two-factor"):
        client.login(*ADMIN)


def test_cli_npm_sync_reads_env(ca: Path, npm: FakeNPM, monkeypatch: pytest.MonkeyPatch) -> None:
    issue_cert("blog.lan", [], ca, ca_core.certs_dir())
    monkeypatch.setenv("NPM_API_URL", npm.url)
    monkeypatch.setenv("INITIAL_ADMIN_EMAIL", ADMIN[0])
    monkeypatch.setenv("NPM_INITIAL_PASSWORD", ADMIN[1])
    result = runner.invoke(APP, ["npm-sync", "blog.lan", "--no-wait"])
    assert result.exit_code == 0, result.output
    assert "#7 created" in result.output
    missing = runner.invoke(APP, ["npm-sync", "nothing.lan", "--no-wait"])
    assert missing.exit_code == 1 and "run 'issue' first" in missing.output
