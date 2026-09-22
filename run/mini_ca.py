#!/usr/bin/env python
"""mini-ca — a tiny private certificate authority.

All state lives under $MINICA_DATA (default /data): rootCA/ holds the root key
and certificate, certificates/<label>/ the issued leaves, and DOMAINS is the
list followed by the `watch` command.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

import ca_core
from ca_core import CAError, __version__
from init_ca import init_ca
from issue_cert import issue_cert, renew_cert
from npm_sync import DEFAULT_API_URL, npm_sync
from watch import parse_domains, watch_file

APP = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
    help=__doc__,
)

_DAYS_HELP = "Validity in days [default: $MINICA_LEAF_DAYS or 825]."


def _fail(message: object) -> None:
    typer.secho(f"❌  {message}", err=True, fg=typer.colors.RED)
    raise typer.Exit(code=1)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"mini-ca {__version__}")
        raise typer.Exit()


@APP.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version, is_eager=True, help="Show the version and exit."
    ),
) -> None:
    """mini-ca — a tiny private certificate authority."""


@APP.command()
def init(
    force: bool = typer.Option(False, "--force", help="Rotate the CA even if one exists (old files are kept as .bak-*)."),
    name: Optional[str] = typer.Option(None, "--name", help="Common Name of the root [default: $MINICA_CA_NAME or 'mini-ca root']."),
    days: Optional[int] = typer.Option(None, "--days", min=1, help="Validity in days [default: $MINICA_ROOT_DAYS or 3650]."),
) -> None:
    """Create (or rotate) the root CA."""
    try:
        init_ca(ca_core.ca_dir(), force=force, name=name, days=days)
        ca_core.ensure_dir(ca_core.certs_dir())  # so a volume subpath mount of it works before the first issue
    except (CAError, OSError, ValueError) as exc:
        _fail(exc)


@APP.command()
def issue(
    domain: str = typer.Argument(..., help="DNS name (wildcards allowed) or IP address."),
    san: Optional[list[str]] = typer.Option(None, "--san", help="Extra SAN (DNS name or IP); repeatable."),
    full_path: bool = typer.Option(False, "--full-path", help="Store under certificates/<full name>/ instead of the first label."),
    days: Optional[int] = typer.Option(None, "--days", min=1, help=_DAYS_HELP),
) -> None:
    """Issue a certificate + key for DOMAIN."""
    try:
        issue_cert(domain, san or [], ca_core.ca_dir(), ca_core.certs_dir(), full_path=full_path, days=days)
    except (CAError, OSError, ValueError) as exc:
        _fail(exc)


@APP.command()
def renew(
    domain: str = typer.Argument(..., help="Name of an already issued certificate."),
    days: Optional[int] = typer.Option(None, "--days", min=1, help=_DAYS_HELP),
) -> None:
    """Re-issue DOMAIN with a fresh key, keeping its SANs and folder."""
    try:
        renew_cert(domain, ca_core.ca_dir(), ca_core.certs_dir(), days=days)
    except (CAError, OSError, ValueError) as exc:
        _fail(exc)


@APP.command("list")
def list_certs(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """List issued certificates with their expiry."""
    threshold = ca_core.renew_days()
    try:
        _, ca_cert = ca_core.load_ca(ca_core.ca_dir())
    except (CAError, ValueError):
        ca_cert = None
    rows = []
    for path, cert in ca_core.iter_issued(ca_core.certs_dir()):
        left = ca_core.days_left(cert)
        sans = ca_core.sans_of(cert)
        if left < 0:
            status = "EXPIRED"
        elif ca_cert is not None and not ca_core.is_issued_by(cert, ca_cert):
            status = "OLD CA"  # signed by a rotated root: renew it
        elif threshold and left <= threshold:
            status = "RENEW SOON"
        else:
            status = "ok"
        rows.append(
            {
                "name": ca_core.common_name(cert) or (sans[0] if sans else "?"),
                "sans": sans,
                "not_after": cert.not_valid_after_utc.strftime("%Y-%m-%d"),
                "days_left": left,
                "status": status,
                "path": str(path),
            }
        )
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    if not rows:
        typer.echo(f"no certificates under {ca_core.certs_dir()}")
        return
    width = max(len(row["name"]) for row in rows)
    typer.echo(f"{'NAME':<{width}}  {'EXPIRES':<10}  {'DAYS':>5}  {'STATUS':<10}  SANS")
    for row in rows:
        typer.echo(
            f"{row['name']:<{width}}  {row['not_after']:<10}  {row['days_left']:>5}  "
            f"{row['status']:<10}  {', '.join(row['sans'])}"
        )


@APP.command()
def info(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Show the root CA subject, validity, fingerprint and data paths."""
    try:
        _, cert = ca_core.load_ca(ca_core.ca_dir())
    except (CAError, OSError, ValueError) as exc:
        _fail(exc)
    data = {
        "version": __version__,
        "data_dir": str(ca_core.data_dir()),
        "certificate": str(ca_core.ca_dir() / ca_core.CA_CERT_NAME),
        "subject": cert.subject.rfc4514_string(),
        "not_before": cert.not_valid_before_utc.strftime("%Y-%m-%d"),
        "not_after": cert.not_valid_after_utc.strftime("%Y-%m-%d"),
        "days_left": ca_core.days_left(cert),
        "sha256": ca_core.fingerprint(cert),
    }
    if as_json:
        typer.echo(json.dumps(data, indent=2))
        return
    width = max(len(k) for k in data)
    for key, value in data.items():
        typer.echo(f"{key:<{width}}  {value}")


@APP.command()
def verify(
    domain: str = typer.Argument(..., help="Host name (or IP) the certificate must be valid for."),
    cert: Optional[Path] = typer.Option(
        None, "--cert", exists=True, dir_okay=False, help="Certificate file [default: the one issued for DOMAIN]."
    ),
) -> None:
    """Check that a certificate chains to this CA and is valid for DOMAIN today."""
    from cryptography.x509.verification import PolicyBuilder, Store, VerificationError

    try:
        name = ca_core.normalize_name(domain)
        _, ca_cert = ca_core.load_ca(ca_core.ca_dir())
        if cert is None:
            found = ca_core.find_cert(name, ca_core.certs_dir())
            if found is None:
                raise CAError(f"no certificate for {name!r} under {ca_core.certs_dir()} (use --cert)")
            cert = found[0]
        leaf = ca_core.load_cert(cert)
        # A wildcard can only be verified against a concrete host name.
        host = name.replace("*.", "wildcard-check.", 1) if name.startswith("*.") else name
        verifier = PolicyBuilder().store(Store([ca_cert])).build_server_verifier(ca_core.general_name(host))
        verifier.verify(leaf, [])
    except VerificationError as exc:
        _fail(f"{cert} is NOT valid for {domain}: {exc}")
    except (CAError, OSError, ValueError) as exc:
        _fail(exc)
    typer.echo(
        f"✅  {cert} is valid for {name} until {leaf.not_valid_after_utc:%Y-%m-%d} "
        f"({ca_core.days_left(leaf)} days) and chains to '{ca_core.common_name(ca_cert)}'"
    )


@APP.command()
def add(
    domain: str = typer.Argument(..., help="DNS name (wildcards allowed) or IP address."),
    san: Optional[list[str]] = typer.Option(None, "--san", help="Extra SAN (DNS name or IP); repeatable."),
    file: Optional[Path] = typer.Option(None, "--file", help="Domain list [default: $MINICA_DATA/DOMAINS]."),
) -> None:
    """Append DOMAIN to the watched list so the watcher issues it."""
    target = file or ca_core.domains_file()
    try:
        name = ca_core.normalize_name(domain)
        extra = [ca_core.normalize_name(s) for s in san or []]
        ca_core.ensure_dir(target.parent)
        target.touch(exist_ok=True)
        current = target.read_bytes()
        listed = set()
        for entry, _, _ in parse_domains(current.decode("utf-8", errors="replace")):
            try:
                listed.add(ca_core.normalize_name(entry))
            except CAError:
                continue
        if name in listed:
            typer.echo(f"{name} is already listed in {target}")
            return
        line = " ".join([name, *extra])
        prefix = b"" if not current or current.endswith(b"\n") else b"\n"
        with target.open("ab") as fh:
            fh.write(prefix + f"{line}\n".encode("utf-8"))
    except (CAError, OSError, ValueError) as exc:
        _fail(exc)
    typer.echo(f"✅  added '{line}' to {target}")


@APP.command("npm-sync")
def npm_sync_cmd(
    domain: str = typer.Argument(..., help="Name of an already issued certificate."),
    api_url: str = typer.Option(DEFAULT_API_URL, "--api-url", envvar="NPM_API_URL", help="NPM admin API base URL."),
    email: str = typer.Option(
        ..., "--email", envvar=["NPM_API_EMAIL", "INITIAL_ADMIN_EMAIL"], help="NPM admin e-mail."
    ),
    password: str = typer.Option(
        ..., "--password", envvar=["NPM_API_PASSWORD", "NPM_INITIAL_PASSWORD", "INITIAL_ADMIN_PASSWORD"],
        help="NPM admin password.",
    ),
    no_wait: bool = typer.Option(False, "--no-wait", help="Fail immediately if the API is not up yet."),
) -> None:
    """Upload the certificate for DOMAIN into Nginx Proxy Manager (create or update)."""
    try:
        npm_sync(domain, ca_core.certs_dir(), api_url, email, password, wait=not no_wait)
    except (CAError, OSError, ValueError) as exc:
        _fail(exc)


@APP.command()
def watch(
    file: Optional[Path] = typer.Option(None, "--file", help="Domain list to follow [default: $MINICA_DATA/DOMAINS]."),
    polling: bool = typer.Option(
        False, "--polling", envvar="MINICA_POLLING", help="Poll instead of inotify (filesystems without change events)."
    ),
    rescan: int = typer.Option(
        300, "--rescan", envvar="MINICA_RESCAN_SECONDS", min=0,
        help="Re-check the list and expiries every N seconds (0 = only on file changes).",
    ),
) -> None:
    """Follow FILE and keep a certificate issued for every entry."""
    try:
        watch_file(file or ca_core.domains_file(), ca_core.ca_dir(), ca_core.certs_dir(), polling=polling, rescan=rescan)
    except (CAError, OSError, ValueError) as exc:
        _fail(exc)


if __name__ == "__main__":
    APP()
