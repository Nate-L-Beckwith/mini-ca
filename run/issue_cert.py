from datetime import datetime, timedelta, timezone
from pathlib import Path
import typer
from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
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
    sans = [x509.DNSName(d) for d in dict.fromkeys([domain, *san])]

    cert = (
        x509.CertificateBuilder()
        .subject_name(subj)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=825))
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True,
        )
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
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
            x509.ExtendedKeyUsage([
                ExtendedKeyUsageOID.SERVER_AUTH,
                ExtendedKeyUsageOID.CLIENT_AUTH,
            ]),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(
                ca_cert.public_key()
            ),
            critical=False,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
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
