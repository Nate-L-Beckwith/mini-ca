#!/usr/bin/env python3
"""
genenv.py – create (or replace) .env for the mini‑ca + NPM bundle.

* Blows away any existing .env.
* Lets you choose the container name once; every DB user / schema setting
  reuses that value so there’s no duplication.
"""

from pathlib import Path
import socket, secrets, textwrap

# ── helpers ────────────────────────────────────────────────────────────────
def host_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 53))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()

def token() -> str:
    return secrets.token_urlsafe(24)

# ── interactive prompts ────────────────────────────────────────────────────
default_name = "npm"
npm_name = input(f"NPM container name [{default_name}]: ").strip() or default_name
host_ip  = input(f"Docker host IP [{host_ip()}]: ").strip() or host_ip()
admin_em = input("Initial NPM admin email [admin@npm]: ").strip() or "admin@npm"

# ── write .env (fresh) ─────────────────────────────────────────────────────
env_path = Path(__file__).resolve().parent / ".env"
if env_path.exists():
    env_path.unlink()

env_path.write_text(textwrap.dedent(f"""\
    ### Global ###############################################################
    TZ=America/New_York
    DOCKERHOST={host_ip}

    ### MariaDB (used by NPM) ################################################
    DB_MYSQL_HOST=db
    DB_MYSQL_PORT=3306
    DB_MYSQL_USER={npm_name}
    DB_MYSQL_NAME={npm_name}
    MYSQL_ROOT_PASSWORD={token()}
    DB_MYSQL_PASSWORD={token()}

    ### Nginx‑Proxy‑Manager ##################################################
    NPM_CONTAINER_NAME={npm_name}
    INITIAL_ADMIN_EMAIL={admin_em}
    NPM_INITIAL_PASSWORD={token()}

    # host‑port bindings
    NPM_PORT={host_ip}:80:80
    NPM_UI_PORT={host_ip}:81:81
    NPM_S_PORTS={host_ip}:443:443
    """))

print("✅  .env written →", env_path)
