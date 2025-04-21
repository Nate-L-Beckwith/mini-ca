from datetime import datetime, timedelta
from pathlib import Path
import typer
from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ca_core import ensure_dir, load_ca


def _short_label(name: str) -> str:
    """Return the leading DNS label (foo from foo.bar.tld / *.foo.bar)."""
    return name.lstrip("*.").split(".")[0]


def issue_cert(
    domain: str,
    san: list[str],
    ca_dir: Path,
    certs_dir: Path,
    full_path: bool = False,
) -> None:
    """Issue a leaf certificate + key and write them to *certs_dir*."""
    ca_key, ca_cert = load_ca(ca_dir)
    if not isinstance(ca_key, rsa.RSAPrivateKey):
        raise TypeError("CA key must be an RSA key for certificate signing.")

    # ─── build X.509 ──────────────────────────────────────────────────────
    key  = rsa.generate_private_key(65537, 2048)
    subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
    sans = [x509.DNSName(domain), *[x509.DNSName(d) for d in san]]

    cert = (
        x509.CertificateBuilder()
        .subject_name(subj)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow())
        .not_valid_after(datetime.utcnow() + timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(sans), False)
        .sign(ca_key, hashes.SHA256())
    )

    # ─── output folder ────────────────────────────────────────────────────
    folder   = domain if full_path else _short_label(domain)
    out_dir  = certs_dir / folder
    ensure_dir(out_dir)

    (out_dir / f"{domain}.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    (out_dir / f"{domain}.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    typer.echo(
        f"\n✅  Certificate issued for '{domain}' → {out_dir}\n"
        f"   ‣ cert : {out_dir}/{domain}.crt\n"
        f"   ‣ key  : {out_dir}/{domain}.key"
    )
