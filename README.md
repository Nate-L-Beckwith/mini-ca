# mini-ca

> A lightweight, self-contained Certificate Authority for your LAN — with a domain-list watcher and optional Nginx Proxy Manager sync, all in one Docker stack.

[![ci](https://github.com/Nate-L-Beckwith/mini-ca/actions/workflows/ci.yml/badge.svg)](https://github.com/Nate-L-Beckwith/mini-ca/actions/workflows/ci.yml)
[![image](https://img.shields.io/badge/ghcr.io-nate--l--beckwith%2Fmini--ca-blue)](https://github.com/Nate-L-Beckwith/mini-ca/pkgs/container/mini-ca)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

---

## Contents

1. [Highlights](#highlights)
2. [Quick start](#quick-start)
3. [Trusting the root CA](#trusting-the-root-ca)
4. [Command-line interface](#command-line-interface)
5. [Live domain watch](#live-domain-watch)
6. [NPM certificate sync](#npm-certificate-sync)
7. [Docker stack](#docker-stack)
8. [Make targets](#make-targets)
9. [Configuration](#configuration)
10. [Building and publishing the image](#building-and-publishing-the-image)
11. [Backup, reset and upgrade](#backup-reset-and-upgrade)
12. [Repository layout](#repository-layout)
13. [Development](#development)
14. [Troubleshooting](#troubleshooting)

---

## Highlights

| Feature | Description |
|---------|-------------|
| **Zero-step root CA** | The first start creates `rootCA.key` + `rootCA.crt` (RSA-4096, 10 years). `init --force` rotates it and keeps the old pair as `.bak-*`. |
| **Leaf certs on demand** | `issue <name> [--san ALT ...]` — DNS names, wildcards **and IP addresses** (proper `IPAddress` SANs). |
| **Idempotent watcher** | Follows `/data/DOMAINS`; issues what is missing, re-issues what expires within 30 days, never rotates a valid cert on restart. |
| **Renew · list · verify** | `renew` keeps SANs and layout; `list` shows expiry; `verify` checks the chain with the `cryptography` verifier. |
| **NPM sync** | `npm-sync` pushes a cert into Nginx Proxy Manager over its API — no `curl`/`jq`, no extra image, no duplicate records. |
| **Private keys stay private** | Keys are written `0600`; only the `certificates/` subtree is ever mounted into other containers. |
| **Reproducible image** | Pinned dependencies, multi-stage build, non-root user, OCI labels, health check, multi-arch (amd64 + arm64) via CI. |
| **Windows-friendly** | `.gitattributes` keeps LF endings; wildcard files are named `_wildcard.example.lan.*` so `docker cp` works on Windows. |

---

## Quick start

### With the full stack (mini-ca + Nginx Proxy Manager)

```bash
git clone https://github.com/Nate-L-Beckwith/mini-ca.git
cd mini-ca

python3 genenv.py                 # 1. write .env (random passwords; add --yes to skip prompts)
make setup                        # 2. build image → create root CA → start stack
make issue DOMAIN=blog.acme.test SAN="www.blog.acme.test 192.168.1.10"
                                  # 3. issue a cert and push it into NPM
make export-ca                    # 4. ./rootCA.crt → import into your trust stores (see below)
```

Then open the NPM UI (`http://<host>:81`, credentials printed by `genenv.py`), edit your proxy host → **SSL** → pick the certificate named after the domain.

### Standalone (just the CA)

```bash
docker run -d --name minica --restart unless-stopped \
  -v minica-data:/data ghcr.io/nate-l-beckwith/mini-ca:latest

docker exec minica mini_ca.py add blog.acme.test --san 192.168.1.10   # the watcher issues it
docker exec minica mini_ca.py list
docker cp minica:/data/rootCA/rootCA.crt .
docker cp minica:/data/certificates/blog .                              # key, cert, fullchain
```

**Output structure**

```text
/data/
├── DOMAINS                         one entry per line: <name> [<extra SAN> ...]  # comments allowed
├── rootCA/
│   ├── rootCA.key                  0600
│   └── rootCA.crt
└── certificates/
    ├── blog/                       first DNS label (or the full name with --full-path)
    │   ├── blog.acme.test.key
    │   ├── blog.acme.test.crt
    │   └── blog.acme.test.fullchain.crt      leaf + root, for servers that want a chain
    └── wild/
        ├── _wildcard.wild.dev.key            "*.wild.dev" → Windows-safe file names
        └── ...
```

---

## Trusting the root CA

Import `rootCA.crt` **once** per device; every certificate mini-ca issues is then trusted.

```bash
make export-ca            # or: docker cp minica:/data/rootCA/rootCA.crt .
```

| Platform | Command / steps |
|----------|-----------------|
| **Windows** (admin PowerShell) | `certutil -addstore -f Root rootCA.crt` |
| **macOS** | `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain rootCA.crt` |
| **Debian / Ubuntu** | `sudo cp rootCA.crt /usr/local/share/ca-certificates/mini-ca.crt && sudo update-ca-certificates` |
| **Fedora / RHEL** | `sudo cp rootCA.crt /etc/pki/ca-trust/source/anchors/ && sudo update-ca-trust` |
| **Firefox** | Uses its own store: `about:config` → `security.enterprise_roots.enabled = true`, or import under Settings → Certificates. |
| **iOS** | Send the `.crt` to the device, install the profile, then Settings → General → About → Certificate Trust Settings → enable. |
| **Android** | Settings → Security → Encryption & credentials → Install a certificate → CA certificate. |

`mini_ca.py info` prints the SHA-256 fingerprint so you can check what you imported.

---

## Command-line interface

```text
mini_ca.py init      [--force] [--name CN] [--days N]      create (or rotate) the root CA
mini_ca.py issue     NAME [--san ALT]... [--full-path] [--days N]
mini_ca.py renew     NAME [--days N]                       fresh key, same SANs and folder
mini_ca.py list      [--json]                              issued certificates and expiry
mini_ca.py info      [--json]                              root CA subject, expiry, fingerprint
mini_ca.py verify    NAME [--cert FILE]                    chain + hostname check
mini_ca.py add       NAME [--san ALT]... [--file PATH]     append to the watched list
mini_ca.py npm-sync  NAME [--api-url URL] [--email E] [--password P] [--no-wait]
mini_ca.py watch     [--file PATH] [--polling] [--rescan SECONDS]
mini_ca.py --version
```

`NAME` and every `--san` may be a DNS name, a `*.wildcard`, or an IPv4/IPv6 address. Names are validated and lower-cased; anything path-like is rejected.

**Examples**

```bash
# via the compose "cli" profile (Makefile: make cert / renew / list / verify / info)
docker compose --profile cli run --rm cli issue "*.wild.dev" --san api.wild.dev --san 10.0.0.5
docker compose --profile cli run --rm cli issue app.example.com --full-path --days 398
docker compose --profile cli run --rm cli renew app.example.com
docker compose --profile cli run --rm cli list
docker compose --profile cli run --rm cli verify app.example.com

# inside the running watcher container
docker compose exec minica mini_ca.py info
```

---

## Live domain watch

The `minica` service follows `/data/DOMAINS`. Each line is:

```text
# name            extra SANs (optional)
blog.acme.test
nas.lan           192.168.1.10 www.nas.lan
*.wild.dev
```

Add entries with `make add DOMAIN=nas.lan SAN="192.168.1.10"` (or `mini_ca.py add`, or `docker compose exec minica sh -c 'echo nas.lan >> /data/DOMAINS'`). The watcher

- issues a certificate for every entry that has none,
- re-issues when a certificate has expired, expires within `MINICA_RENEW_DAYS` (default 30), or the line gained a SAN,
- leaves valid certificates alone — restarting the container never rotates keys,
- skips invalid lines with a logged warning instead of dying,
- re-checks the whole list every `--rescan` seconds (default 300) in addition to file events,
- stops cleanly on `docker stop` (SIGTERM).

On filesystems without change notifications start it with `--polling` (`command: ["--polling"]` in compose, or `MINICA_POLLING=1`).

> Renewals change the files on disk; run `make sync DOMAIN=<name>` afterwards if the certificate lives in NPM.

---

## NPM certificate sync

`make issue` / `make renew` / `make sync DOMAIN=<name>` run the `cert-sync` service, which is the same mini-ca image executing `mini_ca.py npm-sync` inside NPM's network namespace:

1. `POST /api/tokens` — log in with `INITIAL_ADMIN_EMAIL` / `NPM_INITIAL_PASSWORD` from `.env`.
2. `GET /api/nginx/certificates` — look for an existing *custom* certificate named after the domain; create one only if missing (no duplicates on renewal).
3. `POST /api/nginx/certificates/<id>/upload` — upload `<name>.crt` + `<name>.key`. NPM validates and stores them itself under `/data/custom_ssl/npm-<id>/`.
4. `make` restarts NPM so nginx picks up renewed files.

The first sync of a domain creates the certificate record; attach it to a proxy host once in the NPM UI (Proxy Host → SSL). Later `make renew` / `make sync` runs update it in place.

Manual run:

```bash
docker compose --profile sync run --rm cert-sync blog.acme.test
```

Only the `certificates/` subtree of the mini-ca volume is mounted into `cert-sync`; the root key never leaves the `minica` container.

---

## Docker stack

| Service | Profile | Image | Role | Restart |
|---------|---------|-------|------|---------|
| **minica** | *(default)* | mini-ca | Domain-list watcher (`entry.sh`); creates the root CA on first start | `unless-stopped` |
| **init** | `setup` | mini-ca (as root) | One-shot: fix `/data` ownership, create/rotate root CA (`entry-init.sh [--force]`) | no |
| **cli** | `cli` | mini-ca | Ad-hoc `mini_ca.py` commands | no |
| **cert-sync** | `sync` | mini-ca | `mini_ca.py npm-sync <domain>` in NPM's network namespace | no |
| **db** | *(default)* | `jc21/mariadb-aria` | MariaDB for NPM | `unless-stopped` |
| **npm** | *(default)* | `jc21/nginx-proxy-manager:2` | Nginx Proxy Manager | `unless-stopped` |

Volumes: `minica-data` (CA + certificates), `npm_data`, `npm_mysql`, `npm_letsencrypt`. Secrets are passed to each service explicitly (no `env_file`), so the DB root password never reaches NPM or cert-sync.

> The `cert-sync` mount uses a volume `subpath`, which needs Docker Engine ≥ 26 and Compose ≥ 2.24.

---

## Make targets

Run `make help` for the live list.

| Target | Description |
|--------|-------------|
| `setup` | `build` → `init` → `up` |
| `build` / `pull` / `push` | Build the image; pull the third-party images; `push TAG=1.1.0` builds multi-arch and pushes `$(IMAGE):TAG` + `:latest` |
| `init` / `init-force` | Create / rotate the root CA (one-shot, fixes volume ownership) |
| `up` / `down` / `restart` / `ps` / `logs` / `shell` | Stack lifecycle |
| `issue DOMAIN=… [SAN="…"]` | Issue a certificate, push it into NPM, reload NPM |
| `cert DOMAIN=… [SAN="…"]` | Issue only (no NPM) |
| `renew DOMAIN=…` | Re-issue (same SANs), push into NPM, reload NPM |
| `sync DOMAIN=…` | Push an already issued certificate into NPM, reload NPM |
| `add DOMAIN=… [SAN="…"]` | Append to `/data/DOMAINS` (the watcher issues it) |
| `list` / `info` / `verify DOMAIN=…` | Inspect certificates and the CA |
| `export-ca` | Write `./rootCA.crt` for import into trust stores |
| `test` / `lint` | pytest; shellcheck + byte-compile |
| `clean` | Stop and remove containers (keeps volumes) |
| `nuke` | Remove this project's containers, volumes and images |

---

## Configuration

### `.env` (generated by `genenv.py`, see `.env.example`)

| Variable | Default | Description |
|----------|---------|-------------|
| `MINICA_IMAGE` | `ghcr.io/nate-l-beckwith/mini-ca:latest` | Image used by all mini-ca services (point at a local tag to test builds). |
| `MINICA_CA_NAME` / `MINICA_LEAF_DAYS` / `MINICA_RENEW_DAYS` | `mini-ca root` / `825` / `30` | Forwarded to the containers (see below). |
| `TZ` | `America/New_York` | Container timezone. |
| `DOCKERHOST` | *(auto-detected)* | Host IP used in the port bindings. |
| `DB_MYSQL_HOST` / `DB_MYSQL_PORT` | `db` / `3306` | MariaDB endpoint for NPM. |
| `DB_MYSQL_USER` / `DB_MYSQL_NAME` | *(container name)* | Database user and schema. |
| `MYSQL_ROOT_PASSWORD` / `DB_MYSQL_PASSWORD` | *(random)* | Database passwords — applied when the `npm_mysql` volume is first created. |
| `NPM_CONTAINER_NAME` | `npm` | Container name of NPM (`<name>_db` for MariaDB). |
| `INITIAL_ADMIN_EMAIL` / `INITIAL_ADMIN_PASSWORD` | `admin@npm` / *(random)* | NPM's initial admin — applied only when NPM's database is first created. |
| `NPM_INITIAL_PASSWORD` | *(same as above)* | Password `cert-sync` logs in with. Update it if you change the admin password in the UI. |
| `NPM_PORT` / `NPM_UI_PORT` / `NPM_S_PORTS` | `<ip>:80:80` / `<ip>:81:81` / `<ip>:443:443` | Host-port bindings. `genenv.py --ui-bind 127.0.0.1` keeps the admin UI local. |

`genenv.py` keeps a previous `.env` as `.env.bak` and prints the admin credentials once. Regenerating `.env` does **not** change passwords already stored in existing `npm_mysql` / `npm_data` volumes.

### mini-ca environment variables (image)

| Variable | Default | Description |
|----------|---------|-------------|
| `MINICA_DATA` | `/data` | Data directory (also makes the CLI usable outside Docker, e.g. in tests). |
| `MINICA_CA_NAME` | `mini-ca root` | Common Name of the root certificate (at `init`). |
| `MINICA_ROOT_DAYS` / `MINICA_LEAF_DAYS` | `3650` / `825` | Validity periods. 825 days is the maximum Apple accepts for privately trusted TLS certificates. |
| `MINICA_RENEW_DAYS` | `30` | Watcher re-issues certificates expiring within this many days (`0` = never). |
| `MINICA_AUTO_INIT` | `1` | `entry.sh` creates the root CA if none exists. Set `0` to require an explicit `init`. |
| `MINICA_POLLING` / `MINICA_RESCAN_SECONDS` | `0` / `300` | Watcher: polling observer; periodic full re-check interval. |
| `NPM_API_URL` / `NPM_API_EMAIL` / `NPM_API_PASSWORD` | `http://127.0.0.1:81` / — / — | Used by `npm-sync` (falls back to `INITIAL_ADMIN_EMAIL`, `NPM_INITIAL_PASSWORD`, `INITIAL_ADMIN_PASSWORD`). |

---

## Building and publishing the image

```bash
make build                         # local build, tagged as $MINICA_IMAGE from .env
make push TAG=1.1.0                # multi-arch (amd64+arm64) → ghcr.io/nate-l-beckwith/mini-ca:1.1.0 and :latest
make push IMAGE=docker.io/you/mini-ca TAG=1.1.0
```

`make push` needs `docker buildx` and a `docker login` to the registry. The image carries OCI labels (`version`, `revision`, `source`) and `mini_ca.py --version` reports the build's `VERSION`.

**CI** (`.github/workflows/ci.yml`) runs the tests, shellcheck and an in-image smoke test on every push/PR, and publishes to GHCR on pushes to `main`/`dev` and on `v*` tags:

```bash
git tag v1.1.0 && git push --tags     # → ghcr.io/nate-l-beckwith/mini-ca:1.1.0, :1.1, :latest
```

`.dockerignore` is an allow-list, so `.env`, `.git` and local certificates never enter the build context. The Dockerfile also strips CR characters, so building from a Windows checkout is safe even without `.gitattributes`.

---

## Backup, reset and upgrade

**Back up `rootCA.key` + `rootCA.crt`** — losing them means re-importing a new root on every device.

```bash
docker run --rm -v mini-ca_minica-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/minica-data.tgz -C /data .                         # backup
docker run --rm -v mini-ca_minica-data:/data -v "$PWD":/backup alpine \
  sh -c 'tar xzf /backup/minica-data.tgz -C /data && chown -R 1001:1001 /data'   # restore
```

| Goal | Command |
|------|---------|
| Stop stack, keep everything | `make clean` |
| Wipe certificates and CA, keep images | `make clean && docker volume rm mini-ca_minica-data` |
| Rotate the root CA (old pair kept as `.bak-*`) | `make init-force`, then re-issue and re-import the root everywhere |
| Delete this project's containers, volumes and images | `make nuke` |

### Upgrading from 1.0

- **Wildcard file names changed** from `*.example.lan.*` to `_wildcard.example.lan.*`. Old files are still found by `renew`, `verify`, `npm-sync` and the watcher; the next issue writes the new names.
- **`.env`**: add `INITIAL_ADMIN_PASSWORD` (or re-run `genenv.py`). For an NPM database that already exists, set `NPM_INITIAL_PASSWORD` to your current admin password.
- **compose**: `cert-sync` now uses the mini-ca image and a volume `subpath` (Docker ≥ 26). The whole CA volume is no longer mounted into NPM.
- **Watcher** no longer re-issues every domain on restart; keys stay stable until 30 days before expiry.
- Existing volumes keep working: `make init` fixes ownership if needed and leaves an existing CA untouched.

---

## Repository layout

```text
mini-ca/
├── docker/
│   ├── Dockerfile          Multi-stage build (wheels + slim runtime, non-root, healthcheck)
│   ├── entry.sh            Default entrypoint: auto-init root CA, run the watcher
│   └── entry-init.sh       One-shot bootstrap as root (ownership fix + init [--force])
├── run/
│   ├── mini_ca.py          Typer CLI: init · issue · renew · list · info · verify · add · npm-sync · watch
│   ├── ca_core.py          Layout, env config, name validation, key/cert helpers
│   ├── init_ca.py          Root CA generation / rotation with backups
│   ├── issue_cert.py       Leaf issuance + renewal (DNS, wildcard and IP SANs, fullchain)
│   ├── watch.py            Idempotent domain-list watcher
│   └── npm_sync.py         Nginx Proxy Manager API client (stdlib only)
├── tests/                  pytest suite incl. a fake NPM API server
├── .github/workflows/ci.yml  tests + multi-arch image publish to GHCR
├── docker-compose.yml      Stack with profiles: setup · cli · sync
├── genenv.py               .env generator (interactive or --yes)
├── .env.example            Documented template
├── Makefile                Developer / operator targets (make help)
├── requirements*.txt       Pinned dependencies
└── .gitattributes, .dockerignore
```

---

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest -q                                          # ~30 s, generates real RSA keys
shellcheck docker/*.sh

MINICA_DATA=/tmp/ca python run/mini_ca.py init      # run the CLI outside Docker
```

The tests never touch `/data`; every test gets its own `MINICA_DATA`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `[minica] ERROR: /data is not writable by uid 1001` | Volume created before 1.1 or a bind mount owned by root: `make init` (runs `chown` as root), then `make up`. |
| `root CA not found … run 'mini_ca.py init'` | `MINICA_AUTO_INIT=0` and no CA yet, or a wrong `MINICA_DATA`: run `make init`. |
| `NPM API not reachable at http://127.0.0.1:81` | NPM is still starting (cert-sync waits ~60 s) or the `npm` service is not part of the stack; check `make ps` / `make logs`. |
| `NPM API POST /api/tokens failed with HTTP 401` | `NPM_INITIAL_PASSWORD` in `.env` does not match the admin password stored in NPM (you changed it in the UI, or regenerated `.env` for an existing database). |
| `no certificate for 'x' … run 'issue' first` | `make issue DOMAIN=x` (or `make cert`) before `make sync`. |
| Certificate uploaded but a proxy host still serves the old one | Attach the certificate to the proxy host once (Proxy Host → SSL); after renewals `make sync` restarts NPM to reload files. |
| Browser says the cert is not trusted | Import `rootCA.crt` on that device (see [Trusting the root CA](#trusting-the-root-ca)); compare fingerprints with `mini_ca.py info`. Firefox needs `security.enterprise_roots.enabled`. |
| `invalid DNS label` when adding a name | Only letters, digits, `-` and `_` per label, optional leading `*.`; IP addresses are accepted as-is. |
| `subpath` error from Docker on `make sync` | Docker Engine < 26 / Compose < 2.24: upgrade, or replace the `cert-sync` mount with `minica-data:/data:ro`. |
| Watcher does not react to edits (bind mount / network share) | Start with `--polling` (`command: ["--polling"]`) or wait for the periodic re-check (`--rescan`, default 300 s). |

---

## License

Released under the [MIT License](LICENSE).
