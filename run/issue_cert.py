"""Leaf certificate issuance and renewal."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Iterable

import typer
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from ca_core import (
    BACKDATE,
    MAX_LEAF_DAYS_APPLE,
    CAError,
    cert_folder,
    cert_paths,
    find_cert,
    general_name,
    leaf_days,
    load_ca,
    load_cert,
    normalize_name,
    private_pem,
    public_pem,
    sans_of,
    utcnow,
    write_private,
    write_public,
)

_MAX_CN_LENGTH = 64  # RFC 5280 ub-common-name


def _authority_key_identifier(ca_cert: x509.Certificate) -> x509.AuthorityKeyIdentifier:
    """Mirror the CA's SubjectKeyIdentifier so the AKI/SKI pair matches byte for byte."""
    try:
        ski = ca_cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    except x509.ExtensionNotFound:
        return x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_cert.public_key())
    return x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ski)


def issue_cert(
    domain: str,
    san: Iterable[str],
    ca_root: Path,
    certs_root: Path,
    full_path: bool = False,
    days: int | None = None,
) -> Path:
    """Issue a leaf certificate + key for *domain* and return the certificate path.

    *san* may hold DNS names (wildcards allowed) and IP addresses; the domain
    itself is always the first SAN. Files land in ``certs_root/<label>/`` where
    ``<label>`` is the first DNS label, or the full name with *full_path*.
    """
    domain = normalize_name(domain)
    names = list(dict.fromkeys([domain, *(normalize_name(s) for s in san)]))
    days = days if days is not None else leaf_days()
    if days < 1:
        raise CAError("validity must be at least 1 day")

    ca_key, ca_cert = load_ca(ca_root)

    # Validity is exactly *days* long, back-dating included: "now + days" on top
    # of a back-dated start would exceed Apple's 825-day ceiling by an hour.
    now = utcnow()
    not_before = now - BACKDATE
    not_after = not_before + timedelta(days=days)
    ca_expiry = ca_cert.not_valid_after_utc
    if ca_expiry <= now:
        raise CAError(f"the root CA expired on {ca_expiry:%Y-%m-%d} — rotate it with 'init --force'")
    if not_after > ca_expiry:
        typer.echo(f"⚠️  validity would outlive the root CA; clamping to {ca_expiry:%Y-%m-%d}", err=True)
        not_after = ca_expiry
    if days > MAX_LEAF_DAYS_APPLE:
        typer.echo(f"⚠️  Apple devices reject TLS certificates valid for more than {MAX_LEAF_DAYS_APPLE} days", err=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # A CN longer than 64 characters cannot be encoded; a SAN-only certificate is
    # fine for every modern client as long as the SAN extension is critical.
    has_cn = len(domain) <= _MAX_CN_LENGTH
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)] if has_cn else [])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([general_name(n) for n in names]), critical=not has_cn)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .add_extension(_authority_key_identifier(ca_cert), critical=False)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    folder = cert_folder(domain, certs_root, full_path)
    crt_path, key_path, chain_path = cert_paths(domain, folder)
    write_private(key_path, private_pem(key))
    write_public(crt_path, public_pem(cert))
    write_public(chain_path, public_pem(cert) + public_pem(ca_cert))

    typer.echo(
        f"\n✅  certificate issued for '{domain}' → {folder}\n"
        f"   ‣ cert      : {crt_path}\n"
        f"   ‣ key       : {key_path}\n"
        f"   ‣ fullchain : {chain_path}\n"
        f"   ‣ SANs      : {', '.join(names)}\n"
        f"   ‣ expires   : {not_after:%Y-%m-%d} ({(not_after - not_before).days} days)"
    )
    return crt_path


def renew_cert(
    domain: str,
    ca_root: Path,
    certs_root: Path,
    days: int | None = None,
) -> Path:
    """Re-issue *domain* with a fresh key, keeping its SANs and folder layout."""
    domain = normalize_name(domain)
    found = find_cert(domain, certs_root)
    if found is None:
        raise CAError(f"no certificate for {domain!r} under {certs_root} — use 'issue' first")
    crt_path, full_path = found
    sans = [n for n in sans_of(load_cert(crt_path)) if n != domain]
    return issue_cert(domain, sans, ca_root, certs_root, full_path=full_path, days=days)
