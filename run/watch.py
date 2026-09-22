"""Follow a domain list and keep a certificate issued for every entry.

Each non-empty line of the watched file is::

    <name> [<extra SAN> ...]        # optional comment

where ``<name>`` is a DNS name (``*.``-wildcard allowed) or an IP address.
A certificate is (re-)issued when none exists yet, when it has expired or is
about to (``MINICA_RENEW_DAYS``), or when the line gained a SAN the current
certificate lacks. Valid certificates are left alone, so restarting the
watcher never rotates keys behind your back.
"""
from __future__ import annotations

import signal
import threading
from pathlib import Path

import typer
from watchdog.events import (
    EVENT_TYPE_CLOSED,
    EVENT_TYPE_CREATED,
    EVENT_TYPE_MODIFIED,
    EVENT_TYPE_MOVED,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer
from watchdog.observers.polling import PollingObserver

from cryptography import x509

from ca_core import (
    CAError,
    days_left,
    ensure_dir,
    find_cert,
    is_issued_by,
    load_ca,
    load_cert,
    normalize_name,
    renew_days,
    sans_of,
)
from issue_cert import issue_cert

# Editors save atomically (write temp file, rename over the target), "docker cp"
# creates, "echo >>" modifies and inotify reports a completed write as "closed".
_TRIGGERS = {EVENT_TYPE_CLOSED, EVENT_TYPE_CREATED, EVENT_TYPE_MODIFIED, EVENT_TYPE_MOVED}


def _log(msg: str, err: bool = False) -> None:
    typer.echo(f"[watch] {msg}", err=err)


def parse_domains(text: str) -> list[tuple[str, list[str], str]]:
    """Split the file into ``(name, extra_sans, raw_line)`` tuples, skipping blanks and ``#`` comments."""
    entries = []
    for raw in text.splitlines():
        body = raw.split("#", 1)[0].strip()
        if not body:
            continue
        name, *sans = body.split()
        entries.append((name, sans, raw.strip()))
    return entries


class DomainsHandler(FileSystemEventHandler):
    """Re-syncs certificates with the domain list whenever the file changes."""

    def __init__(self, file: Path, ca_root: Path, certs_root: Path, renew_within: int | None = None):
        self.file, self.ca_root, self.certs_root = file, ca_root, certs_root
        self.renew_within = renew_days() if renew_within is None else renew_within
        self._lock = threading.Lock()
        self._reported: set[str] = set()  # entries already logged as present/invalid

    # watchdog callback --------------------------------------------------
    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or event.event_type not in _TRIGGERS:
            return
        paths = (event.src_path, getattr(event, "dest_path", None))
        if any(p and Path(str(p)).name == self.file.name for p in paths):
            self.process()

    # work ---------------------------------------------------------------
    def process(self) -> None:
        """Bring the certificates in line with the file. Never raises."""
        with self._lock:
            try:
                text = self.file.read_text(encoding="utf-8", errors="replace")
            except FileNotFoundError:
                return
            try:
                _, ca_cert = load_ca(self.ca_root)
            except (CAError, ValueError) as exc:
                self._once("root-ca", f"cannot issue: {exc}", err=True)
                return
            self._reported.discard("root-ca")
            for name, sans, raw in parse_domains(text):
                try:
                    domain = normalize_name(name)
                    extra = [normalize_name(s) for s in sans]
                except CAError as exc:
                    self._once(raw, f"skipping {raw!r}: {exc}", err=True)
                    continue
                self._ensure(domain, extra, ca_cert)

    def _once(self, key: str, msg: str, err: bool = False) -> None:
        if key not in self._reported:
            self._reported.add(key)
            _log(msg, err=err)

    def _ensure(self, domain: str, sans: list[str], ca_cert: x509.Certificate) -> None:
        found = find_cert(domain, self.certs_root)
        full_path = False
        reason = "new"
        if found is not None:
            crt_path, full_path = found
            try:
                cert = load_cert(crt_path)
            except ValueError:
                reason = "unreadable certificate"
            else:
                left = days_left(cert)
                missing = set(sans) - set(sans_of(cert))
                # A leaf can never outlive the root: one clamped to the CA's own expiry
                # must not be renewed over and over, only the CA can be rotated.
                clamped = cert.not_valid_after_utc >= ca_cert.not_valid_after_utc
                if not is_issued_by(cert, ca_cert):
                    reason = "signed by a previous root CA"
                elif left < 0:
                    reason = "expired"
                elif self.renew_within and left <= self.renew_within and not clamped:
                    reason = f"expires in {left} days"
                elif missing:
                    reason = f"new SANs: {', '.join(sorted(missing))}"
                else:
                    if clamped and self.renew_within and left <= self.renew_within:
                        self._once(
                            "root-ca-expiry",
                            f"root CA expires in {days_left(ca_cert)} days — rotate it with 'init --force'",
                            err=True,
                        )
                    self._once(domain, f"{domain}: certificate present, {left} days left")
                    return
        try:
            _log(f"{domain}: issuing ({reason})")
            issue_cert(domain, sans, self.ca_root, self.certs_root, full_path=full_path)
            self._reported.discard(domain)
        except Exception as exc:  # noqa: BLE001 — one bad entry must not stop the watcher
            _log(f"ERROR issuing {domain}: {exc}", err=True)


def watch_file(
    file: Path,
    ca_root: Path,
    certs_root: Path,
    polling: bool = False,
    rescan: int = 300,
) -> None:
    """Issue for the current file contents, then follow the file until SIGTERM/SIGINT.

    *rescan* seconds between full re-checks (catches missed events and
    approaching expiry); 0 disables the periodic pass.
    """
    file = Path(file)
    ensure_dir(file.parent)
    file.touch(exist_ok=True)
    load_ca(ca_root)  # fail fast with a readable error when the CA is missing

    handler = DomainsHandler(file, ca_root, certs_root)
    handler.process()

    observer = PollingObserver(timeout=2) if polling else Observer()
    observer.schedule(handler, str(file.parent), recursive=False)
    observer.start()

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, lambda *_: stop.set())
        except (ValueError, OSError):  # not the main thread, or unsupported platform
            pass

    every = f"re-checking every {rescan}s" if rescan else "no periodic re-check"
    _log(f"following {file} ({'polling' if polling else 'inotify'}, {every}); Ctrl-C to stop")
    elapsed = 0
    try:
        while not stop.wait(1.0):
            elapsed += 1
            if rescan and elapsed >= rescan:
                elapsed = 0
                handler.process()
    finally:
        observer.stop()
        observer.join(timeout=5)
        _log("stopped")
