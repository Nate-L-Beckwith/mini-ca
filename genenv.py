#!/usr/bin/env python3
"""genenv.py - create the .env file for the mini-ca + Nginx Proxy Manager stack.

Interactive by default (every prompt has a default). Non-interactive:

    python3 genenv.py --yes [--name npm] [--ip 192.168.1.10] [--email admin@npm] [--password ...]

An existing .env is kept as .env.bak. The database and NPM admin passwords
written here only take effect when the db / npm volumes are first created;
regenerating .env for a running stack does NOT change them.
"""
from __future__ import annotations

import argparse
import os
import secrets
import socket
import sys
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_IMAGE = "ghcr.io/nate-l-beckwith/mini-ca:latest"


def host_ip() -> str:
    """Best-effort LAN address of this machine (no packet is actually sent)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 53))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def ask(prompt: str, default: str, assume_yes: bool) -> str:
    if assume_yes:
        return default
    try:
        return input(f"{prompt} [{default}]: ").strip() or default
    except EOFError:
        return default


def render(*, name: str, ip: str, ui_bind: str, email: str, password: str, tz: str, image: str) -> str:
    return textwrap.dedent(
        f"""\
        ### mini-ca #############################################################
        # image used by docker-compose.yml (point at a local build with e.g. minica:dev)
        MINICA_IMAGE={image}
        # MINICA_CA_NAME=mini-ca root
        # MINICA_LEAF_DAYS=825
        # MINICA_RENEW_DAYS=30

        ### Global ###############################################################
        TZ={tz}
        DOCKERHOST={ip}

        ### MariaDB (used by NPM) ################################################
        DB_MYSQL_HOST=db
        DB_MYSQL_PORT=3306
        DB_MYSQL_USER={name}
        DB_MYSQL_NAME={name}
        MYSQL_ROOT_PASSWORD={token()}
        DB_MYSQL_PASSWORD={token()}

        ### Nginx-Proxy-Manager ##################################################
        NPM_CONTAINER_NAME={name}
        # NPM >= 2.13 has no default admin; these create it when its database is first created
        INITIAL_ADMIN_EMAIL={email}
        INITIAL_ADMIN_PASSWORD={password}
        # credentials cert-sync uses for the NPM API — update if you change the admin password in the UI
        NPM_INITIAL_PASSWORD={password}
        # set to true on hosts without IPv6 ("Address family not supported by protocol" in NPM logs)
        DISABLE_IPV6=false

        # host-port bindings (host-ip:host-port:container-port)
        NPM_PORT={ip}:80:80
        NPM_UI_PORT={ui_bind}:81:81
        NPM_S_PORTS={ip}:443:443
        """
    )


def main(argv: list[str] | None = None) -> int:
    # Windows consoles may use a legacy code page; never crash on the status symbols below.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        description="Create the .env file for the mini-ca + Nginx Proxy Manager stack.",
        epilog="Without --yes every value is prompted for, with the shown defaults.",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="accept all defaults, no prompts")
    parser.add_argument("--name", help="NPM container name, also used as DB user/schema [npm]")
    parser.add_argument("--ip", help="host IP to bind NPM's ports 80/443 to [auto-detected LAN address]")
    parser.add_argument("--ui-bind", help="host IP to bind the NPM admin UI (:81) to [same as --ip; 127.0.0.1 = local only]")
    parser.add_argument("--email", help="initial NPM admin e-mail [admin@npm]")
    parser.add_argument("--password", help="initial NPM admin password [random]")
    parser.add_argument("--tz", default="America/New_York", help="container timezone [%(default)s]")
    parser.add_argument("--image", default=DEFAULT_IMAGE, help="mini-ca image reference [%(default)s]")
    parser.add_argument("-o", "--output", type=Path, default=HERE / ".env", help="where to write [%(default)s]")
    args = parser.parse_args(argv)

    name = args.name or ask("NPM container name", "npm", args.yes)
    ip = args.ip or ask("Docker host IP for NPM port bindings", host_ip(), args.yes)
    ui_bind = args.ui_bind or ask("Bind NPM admin UI (:81) to", ip, args.yes)
    email = args.email or ask("Initial NPM admin e-mail", "admin@npm", args.yes)
    password = args.password or ask("Initial NPM admin password", token(12), args.yes)

    out: Path = args.output
    if out.exists():
        backup = out.with_name(out.name + ".bak")
        out.replace(backup)
        print(f"ℹ️  previous {out.name} kept as {backup.name}")

    out.write_text(
        render(name=name, ip=ip, ui_bind=ui_bind, email=email, password=password, tz=args.tz, image=args.image),
        encoding="utf-8",
        newline="\n",
    )
    try:
        os.chmod(out, 0o600)  # holds passwords; no effect on Windows
    except OSError:
        pass

    print(f"✅  {out.name} written → {out}")
    print(f"    NPM admin login : {email} / {password}")
    print("    (only applied when the NPM database is first created)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
