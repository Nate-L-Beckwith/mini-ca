from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import serialization

def save_key(p: Path, key):
    p.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )

def save_cert(p: Path, cert: x509.Certificate):
    p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

def load_root(root_dir: Path):
    key_p, crt_p = root_dir/"rootCA.key", root_dir/"rootCA.crt"
    key = serialization.load_pem_private_key(key_p.read_bytes(), None)
    cert = x509.load_pem_x509_certificate(crt_p.read_bytes())
    return key, cert
