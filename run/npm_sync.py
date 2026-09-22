"""Push an issued certificate into Nginx Proxy Manager (NPM) through its REST API.

Flow against NPM's admin API (default http://127.0.0.1:81, i.e. run in NPM's
network namespace):

    0. GET  /api                                 readiness; ``setup: false`` means no admin exists yet
       POST /api/users                           (only then) create the admin from the cert-sync credentials
    1. POST /api/tokens                          {identity, secret}   -> {token}
    2. GET  /api/nginx/certificates              find provider "other" record whose nice_name is the domain
       POST /api/nginx/certificates              {provider: "other", nice_name}   when none exists
    3. POST /api/nginx/certificates/<id>/upload  multipart: certificate=<crt>, certificate_key=<key>

NPM validates the pair, stores it under /data/custom_ssl/npm-<id>/ and records
it in its database, so nothing needs to be copied into NPM's volume by hand.
Only the standard library is used: no curl, jq or extra image required.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any
from urllib import error, request

import typer

from ca_core import CAError, find_cert, load_cert, normalize_name

DEFAULT_API_URL = "http://127.0.0.1:81"


def _multipart(boundary: str, files: list[tuple[str, str, bytes]]) -> bytes:
    """Encode ``(field, filename, content)`` tuples as multipart/form-data."""
    out = bytearray()
    for field, filename, content in files:
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode()
        out += b"Content-Type: application/x-pem-file\r\n\r\n"
        out += content + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out)


class NPMClient:
    """Minimal client for the parts of the NPM API that cert-sync needs."""

    def __init__(self, base_url: str = DEFAULT_API_URL, timeout: float = 15.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.token: str | None = None

    # transport --------------------------------------------------------------
    def _request(self, method: str, path: str, body: bytes | None = None, headers: dict[str, str] | None = None) -> Any:
        hdrs = {"Accept": "application/json"}
        if self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
        hdrs.update(headers or {})
        req = request.Request(self.base + path, data=body, method=method, headers=hdrs)
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - admin API on a fixed URL
                raw = resp.read()
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()[:500]
            raise CAError(f"NPM API {method} {path} failed with HTTP {exc.code}: {detail or exc.reason}") from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise CAError(f"NPM API not reachable at {self.base}: {exc}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise CAError(f"NPM API {method} {path} returned non-JSON: {raw[:200]!r}") from exc

    def _json(self, method: str, path: str, payload: dict[str, Any]) -> Any:
        return self._request(method, path, json.dumps(payload).encode(), {"Content-Type": "application/json"})

    # operations -------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        """GET /api — unauthenticated; ``{"status": "OK", "setup": bool, "version": {...}}``."""
        data = self._request("GET", "/api")
        return data if isinstance(data, dict) else {}

    def wait_ready(self, attempts: int = 30, delay: float = 2.0) -> dict[str, Any]:
        """Block until GET /api answers (NPM takes a while after container start)."""
        for attempt in range(1, attempts + 1):
            try:
                return self.status()
            except CAError:
                if attempt == attempts:
                    raise
                time.sleep(delay)
        return {}

    def create_admin(self, email: str, password: str, name: str = "Administrator") -> None:
        """First-run bootstrap: NPM >= 2.13 ships without a default admin and lets the
        first user be created unauthenticated while ``setup`` is false."""
        self._json(
            "POST",
            "/api/users",
            {
                "name": name,
                "nickname": "Admin",
                "email": email,
                "roles": ["admin"],
                "auth": {"type": "password", "secret": password},
            },
        )

    def login(self, identity: str, secret: str) -> None:
        data = self._json("POST", "/api/tokens", {"identity": identity, "secret": secret})
        if isinstance(data, dict) and data.get("requires_2fa"):
            raise CAError(
                f"NPM account {identity!r} has two-factor authentication enabled; "
                "use a dedicated API user without 2FA for cert-sync"
            )
        token = data.get("token") if isinstance(data, dict) else None
        if not token:
            raise CAError(f"NPM login as {identity!r} failed: no token in response")
        self.token = token

    def find_certificate(self, nice_name: str) -> int | None:
        items = self._request("GET", "/api/nginx/certificates") or []
        for item in items:
            if item.get("provider") == "other" and item.get("nice_name") == nice_name:
                return int(item["id"])
        return None

    def create_certificate(self, nice_name: str) -> int:
        data = self._json("POST", "/api/nginx/certificates", {"provider": "other", "nice_name": nice_name})
        try:
            return int(data["id"])
        except (TypeError, KeyError, ValueError) as exc:
            raise CAError(f"unexpected response when creating the certificate record: {data!r}") from exc

    def upload(self, cert_id: int, certificate: bytes, private_key: bytes) -> Any:
        boundary = f"----minica{uuid.uuid4().hex}"
        body = _multipart(boundary, [("certificate", "fullchain.pem", certificate), ("certificate_key", "privkey.pem", private_key)])
        return self._request(
            "POST",
            f"/api/nginx/certificates/{cert_id}/upload",
            body,
            {"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )


def npm_sync(
    domain: str,
    certs_root: Path,
    api_url: str,
    email: str,
    password: str,
    wait: bool = True,
) -> int:
    """Upload the certificate issued for *domain* into NPM; returns NPM's certificate id."""
    domain = normalize_name(domain)
    found = find_cert(domain, certs_root)
    if found is None:
        raise CAError(f"no certificate for {domain!r} under {certs_root} — run 'issue' first")
    crt_path, _ = found
    key_path = crt_path.with_suffix(".key")
    if not key_path.is_file():
        raise CAError(f"private key {key_path} is missing")
    expires = load_cert(crt_path).not_valid_after_utc

    client = NPMClient(api_url)
    if wait:
        typer.echo(f"⏳  waiting for the NPM API at {client.base} …")
        status = client.wait_ready()
    else:
        status = client.status()
    if status.get("setup") is False:
        typer.echo(f"ℹ️  NPM has no admin user yet — creating {email} from the cert-sync credentials")
        client.create_admin(email, password)
    client.login(email, password)

    cert_id = client.find_certificate(domain)
    created = cert_id is None
    if created:
        cert_id = client.create_certificate(domain)
    client.upload(cert_id, crt_path.read_bytes(), key_path.read_bytes())

    typer.echo(
        f"✅  {domain} → NPM certificate #{cert_id} {'created' if created else 'updated'} "
        f"(expires {expires:%Y-%m-%d}); attach it to a proxy host under SSL if not done yet"
    )
    return cert_id
