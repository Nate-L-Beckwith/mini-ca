#!/usr/bin/env python
from pathlib import Path
import typer

from init_ca import init_ca
from issue_cert import issue_cert
from watch import watch_file

APP        = typer.Typer(add_completion=False)
CA_DIR     = Path("/data/rootCA")
CERTS_DIR  = Path("/data/certificates")


@APP.command()
def init(force: bool = typer.Option(False, help="overwrite existing CA if present")):
    """Create (or rotate) the root CA."""
    init_ca(CA_DIR, force)


@APP.command()
def issue(
    domain: str,
    san: list[str] = typer.Option(None, "--san", help="additional SANs"),
    full_path: bool = typer.Option(
        False,
        "--full-path",
        help="store certs under full FQDN folder instead of first label",
    ),
):
    """Issue a certificate for *DOMAIN*."""
    default_sans = san or [domain]          # CN always duplicated into SAN list
    issue_cert(domain, default_sans, CA_DIR, CERTS_DIR, full_path)


@APP.command()
def watch(
    file: Path = typer.Option(
        "/data/DOMAINS", "--file", help="domain list file to watch"
    )
):
    """Continuously watch *FILE* and issue for every new line."""
    watch_file(file, CA_DIR, CERTS_DIR)


if __name__ == "__main__":
    APP()
