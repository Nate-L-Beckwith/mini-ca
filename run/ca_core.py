"""Shared helpers and configuration for mini-ca.

Everything that touches the on-disk layout or the environment lives here so the
CLI, the watcher and the tests agree on a single source of truth.

Environment variables (all optional):

    MINICA_DATA         data directory                                 (default: /data)
    MINICA_CA_NAME      Common Name of the root certificate            (default: mini-ca root)
    MINICA_ROOT_DAYS    root certificate validity in days              (default: 3650)
    MINICA_LEAF_DAYS    leaf certificate validity in days              (default: 825)
    MINICA_RENEW_DAYS   watcher re-issues certs expiring within N days (default: 30, 0 = never)

Layout under MINICA_DATA:

    rootCA/rootCA.key, rootCA/rootCA.crt
    certificates/<label>/<name>.key | .crt | .fullchain.crt
    DOMAINS                                   (watched list, one entry per line)
"""
from __future__ import annotations

import ipaddress
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

__version__ = "1.1.0"

# Apple (iOS 13+/macOS 10.15+) rejects TLS server certificates valid for longer than this,
# including ones issued by privately trusted roots.
MAX_LEAF_DAYS_APPLE = 825

CA_KEY_NAME = "rootCA.key"
CA_CERT_NAME = "rootCA.crt"
FULLCHAIN_SUFFIX = ".fullchain.crt"

# Certificates are back-dated slightly so hosts with a lagging clock accept them at once.
BACKDATE = timedelta(hours=1)

_LABEL_RE = re.compile(r"^(?!-)[a-z0-9_-]{1,63}(?<!-)$")


class CAError(RuntimeError):
    """User-facing failure (missing CA, invalid name, bad configuration, ...)."""


# ── environment ────────────────────────────────────────────────────────────
def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise CAError(f"{name} must be an integer, got {raw!r}") from exc
    if value < 0:
        raise CAError(f"{name} must be >= 0, got {value}")
    return value


def data_dir() -> Path:
    return Path(os.environ.get("MINICA_DATA", "/data"))


def ca_dir() -> Path:
    return data_dir() / "rootCA"


def certs_dir() -> Path:
    return data_dir() / "certificates"


def domains_file() -> Path:
    return data_dir() / "DOMAINS"


def ca_name() -> str:
    return os.environ.get("MINICA_CA_NAME", "").strip() or "mini-ca root"


def root_days() -> int:
    return _env_int("MINICA_ROOT_DAYS", 3650)


def leaf_days() -> int:
    return _env_int("MINICA_LEAF_DAYS", 825)


def renew_days() -> int:
    return _env_int("MINICA_RENEW_DAYS", 30)


# ── names ──────────────────────────────────────────────────────────────────
def is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def normalize_name(name: str) -> str:
    """Validate and normalise a DNS name (``*.``-wildcard allowed) or an IP address.

    Returns the lower-cased name without a trailing dot, or raises
    :class:`CAError` with a readable message.
    """
    name = name.strip().lower().rstrip(".")
    if not name:
        raise CAError("empty name")
    if is_ip(name):
        return str(ipaddress.ip_address(name))
    if not name.isascii():
        raise CAError(f"{name!r}: non-ASCII names are not supported, use the punycode (xn--) form")
    if len(name) > 253:
        raise CAError(f"{name!r}: name longer than 253 characters")
    labels = name.split(".")
    if labels[0] == "*":
        if len(labels) < 2:
            raise CAError(f"{name!r}: a wildcard needs a base domain (e.g. *.example.lan)")
        labels = labels[1:]
    for label in labels:
        if not _LABEL_RE.match(label):
            raise CAError(f"{name!r}: invalid DNS label {label!r}")
    return name


def general_name(value: str) -> x509.GeneralName:
    """Turn a normalised name into the right SAN type (IPAddress or DNSName)."""
    if is_ip(value):
        return x509.IPAddress(ipaddress.ip_address(value))
    return x509.DNSName(value)


def file_stem(name: str) -> str:
    """Filesystem-safe base name: ``*.example.lan`` -> ``_wildcard.example.lan``.

    ``*`` and ``:`` are not legal in Windows file names, which matters as soon
    as certificates are copied out of the volume with ``docker cp``.
    """
    return name.replace("*", "_wildcard").replace(":", "-")


def short_label(name: str) -> str:
    """Leading DNS label used as the certificate folder (``foo`` for ``*.foo.bar``)."""
    if is_ip(name):
        return file_stem(name)
    return [label for label in name.split(".") if label and label != "*"][0]


def cert_folder(domain: str, certs_root: Path, full_path: bool = False) -> Path:
    return certs_root / (file_stem(domain) if full_path else short_label(domain))


def cert_paths(domain: str, folder: Path) -> tuple[Path, Path, Path]:
    """Return ``(crt, key, fullchain)`` paths for *domain* inside *folder*."""
    stem = file_stem(domain)
    return folder / f"{stem}.crt", folder / f"{stem}.key", folder / f"{stem}{FULLCHAIN_SUFFIX}"


def find_cert(domain: str, certs_root: Path) -> tuple[Path, bool] | None:
    """Locate the issued certificate for *domain*.

    Returns ``(crt_path, full_path)`` where *full_path* tells which folder
    layout it lives in, or ``None`` when nothing has been issued yet. The
    pre-1.1 naming of wildcard files (``*.example.lan.crt``) is recognised too.
    """
    for full_path in (False, True):
        folder = cert_folder(domain, certs_root, full_path)
        crt, key, _ = cert_paths(domain, folder)
        if crt.is_file() and key.is_file():
            return crt, full_path
        legacy_crt, legacy_key = folder / f"{domain}.crt", folder / f"{domain}.key"
        if legacy_crt.is_file() and legacy_key.is_file():
            return legacy_crt, full_path
    return None


# ── filesystem ─────────────────────────────────────────────────────────────
def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    """Write *data* to a temp file next to *path* and rename it into place.

    Readers (the watcher, NPM, a ``docker cp``) therefore never see a partially
    written key or certificate. *mode* is applied explicitly because O_CREAT's
    mode is masked by the umask and ignored for pre-existing files.
    """
    ensure_dir(path.parent)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, mode)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_private(path: Path, data: bytes) -> None:
    """Write *data* to *path* readable by the owner only (mode 0600)."""
    _atomic_write(path, data, 0o600)


def write_public(path: Path, data: bytes) -> None:
    _atomic_write(path, data, 0o644)


# ── crypto helpers ─────────────────────────────────────────────────────────
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def private_pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )


def public_pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def load_cert(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def load_ca(ca_root: Path) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key_file, cert_file = ca_root / CA_KEY_NAME, ca_root / CA_CERT_NAME
    if not key_file.is_file() or not cert_file.is_file():
        raise CAError(f"root CA not found in {ca_root} — run 'mini_ca.py init' (or 'make init') first")
    key = serialization.load_pem_private_key(key_file.read_bytes(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise CAError("root CA key must be an RSA key")
    return key, load_cert(cert_file)


def is_issued_by(cert: x509.Certificate, ca_cert: x509.Certificate) -> bool:
    """True when *cert* was signed by *ca_cert*'s key.

    After ``init --force`` the issuer *name* is unchanged, so only a signature
    check tells a current leaf from one signed by the previous root.
    """
    try:
        cert.verify_directly_issued_by(ca_cert)
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True


def fingerprint(cert: x509.Certificate) -> str:
    digest = cert.fingerprint(hashes.SHA256()).hex().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def days_left(cert: x509.Certificate, now: datetime | None = None) -> int:
    now = now or utcnow()
    return (cert.not_valid_after_utc - now).days


def sans_of(cert: x509.Certificate) -> list[str]:
    """DNS and IP subject alternative names of *cert* as plain strings."""
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound:
        return []
    names = list(san.get_values_for_type(x509.DNSName))
    names.extend(str(ip) for ip in san.get_values_for_type(x509.IPAddress))
    return names


def common_name(cert: x509.Certificate) -> str:
    attrs = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    return str(attrs[0].value) if attrs else ""


def iter_issued(certs_root: Path) -> Iterator[tuple[Path, x509.Certificate]]:
    """Yield ``(path, certificate)`` for every leaf certificate under *certs_root*."""
    if not certs_root.is_dir():
        return
    for path in sorted(certs_root.glob("*/*.crt")):
        if path.name.endswith(FULLCHAIN_SUFFIX):
            continue
        try:
            yield path, load_cert(path)
        except ValueError:
            continue
