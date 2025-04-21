from datetime import datetime, timedelta
from pathlib import Path

import typer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def init_ca(ca_dir: Path, force: bool) -> None:
    key_file  = ca_dir / "rootCA.key"
    cert_file = ca_dir / "rootCA.crt"

    if key_file.exists() and cert_file.exists() and not force:
        typer.echo("✅  root CA already present")
        return

    ca_dir.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(65537, 4096)
    subject = issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "mini-ca root")]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.utcnow())
        .not_valid_after(datetime.utcnow() + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )

    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    typer.echo(f"✅  new root CA written to {ca_dir}")
