"""Root CA creation and rotation."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import typer
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from ca_core import (
    BACKDATE,
    CA_CERT_NAME,
    CA_KEY_NAME,
    CAError,
    ca_name,
    ensure_dir,
    fingerprint,
    private_pem,
    public_pem,
    root_days,
    utcnow,
    write_private,
    write_public,
)


def _backup(path: Path, stamp: str) -> Path | None:
    if not path.exists():
        return None
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    path.replace(backup)
    return backup


def init_ca(
    ca_root: Path,
    force: bool = False,
    name: str | None = None,
    days: int | None = None,
) -> x509.Certificate | None:
    """Create the root CA in *ca_root*.

    Returns the new certificate, or ``None`` when a CA already exists and
    *force* is not set. With *force* the previous key and certificate are kept
    as ``*.bak-<timestamp>`` files instead of being destroyed.
    """
    key_file, cert_file = ca_root / CA_KEY_NAME, ca_root / CA_CERT_NAME
    name = (name or ca_name()).strip()
    days = days if days is not None else root_days()
    if days < 1:
        raise CAError("validity must be at least 1 day")
    if not 1 <= len(name) <= 64:
        raise CAError("the CA name must be 1-64 characters long")

    if key_file.exists() and cert_file.exists() and not force:
        typer.echo(f"✅  root CA already present in {ca_root}")
        return None

    ensure_dir(ca_root)
    if force:
        stamp = utcnow().strftime("%Y%m%d-%H%M%S")
        for backup in (_backup(key_file, stamp), _backup(cert_file, stamp)):
            if backup is not None:
                typer.echo(f"   ‣ kept previous {backup.name}")

    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    not_before = utcnow() - BACKDATE
    not_after = not_before + timedelta(days=days)  # validity is exactly *days*, back-dating included
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        # path_length=0: this root signs leaf certificates only, never intermediates
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                key_cert_sign=True,
                crl_sign=True,
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )

    write_private(key_file, private_pem(key))
    write_public(cert_file, public_pem(cert))

    typer.echo(
        f"✅  new root CA written to {ca_root}\n"
        f"   ‣ subject : CN={name}\n"
        f"   ‣ expires : {cert.not_valid_after_utc:%Y-%m-%d}\n"
        f"   ‣ SHA-256 : {fingerprint(cert)}"
    )
    if force:
        typer.echo(
            "⚠️  the root CA was rotated: certificates issued before now are no longer "
            "trusted by clients that import the new rootCA.crt — re-issue them"
        )
    return cert
