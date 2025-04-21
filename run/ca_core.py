from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import serialization


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def load_ca(ca_dir: Path):
    key  = serialization.load_pem_private_key(
        (ca_dir / "rootCA.key").read_bytes(), password=None
    )
    cert = x509.load_pem_x509_certificate(
        (ca_dir / "rootCA.crt").read_bytes()
    )
    return key, cert
