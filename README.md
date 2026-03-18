# mini-ca

> A lightweight, self-contained Certificate Authority and optional Nginx Proxy Manager sync — all in a single Docker stack.

[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

---

## Contents

1. [Highlights](#highlights)
2. [Quick start](#quick-start)
3. [Repository layout](#repository-layout)
4. [Working model](#working-model)
5. [Docker stack](#docker-stack)
6. [Command-line interface](#command-line-interface)
7. [NPM certificate sync](#npm-certificate-sync)
8. [Live domain watch](#live-domain-watch)
9. [Make targets](#make-targets)
10. [Clean / Rebuild matrix](#clean--rebuild-matrix)
11. [`make nuke` — full reset](#make-nuke--full-reset)
12. [Configuration](#configuration)
13. [Best practices & CI notes](#best-practices--ci-notes)
14. [Troubleshooting](#troubleshooting)

---

## Highlights

| Feature | Description |
|---------|-------------|
| **One-shot root CA** | `entry-init.sh` generates `rootCA.key` + `rootCA.crt` (10-year validity). |
| **Leaf certs on demand** | `issue <domain> [--san ALT...]` produces a key and certificate per domain. |
| **Wildcard & unlimited SANs** | Issue `*.example.dev` with any number of Subject Alternative Names. |
| **Continuous watcher** | Monitors `/data/DOMAINS` and auto-issues certificates as new lines appear. |
| **NPM sync** | Optional *sync* profile copies PEMs into Nginx Proxy Manager and registers them via the admin API. |
| **Compose profiles** | `setup` / `cli` / `sync` — only run what you need. |
| **Makefile helpers** | `help` · `setup` · `up` · `issue` · `logs` · `clean` · `nuke` |
| **Offline runtime** | Python wheels are baked at build time — no outbound network at runtime. |

---

## Quick start

```bash
git clone https://github.com/Nate-L-Beckwith/mini-ca.git
cd mini-ca

# 1. Generate a .env file (interactive prompts)
python3 genenv.py

# 2. Build the image, bootstrap the root CA, and start the stack
make setup              # build → init → up

# 3. Issue a certificate
make issue DOMAIN=blog.acme.test
```

You can also call the CLI directly for more control:

```bash
docker compose run --rm cli issue blog.acme.test --san www.blog.acme.test
```

`--san` is optional. When omitted, the SAN list defaults to the domain itself.

**Output structure:**

```text
/data/
├── rootCA/
│   ├── rootCA.key
│   └── rootCA.crt
└── certificates/
    └── blog/
        ├── blog.acme.test.key
        └── blog.acme.test.crt
```

> **Trust chain:** Import `rootCA.crt` into your OS or browser trust store once — every leaf certificate issued by this CA will be trusted automatically.

---

## Repository layout

```text
mini-ca/
├── docker/
│   ├── Dockerfile          Multi-stage build (wheels + slim runtime)
│   ├── cert-sync.sh        Copies + registers PEMs in NPM via its API
│   ├── entry.sh            Default entrypoint (domain watcher)
│   └── entry-init.sh       One-shot CA bootstrap entrypoint
├── run/
│   ├── mini_ca.py          Typer CLI entrypoint
│   ├── init_ca.py          Root CA generation
│   ├── issue_cert.py       Leaf certificate issuance
│   ├── ca_core.py          Shared helpers (load CA, ensure dirs)
│   └── watch.py            File watcher for automatic issuance
├── docker-compose.yml      Full stack definition (with profiles)
├── genenv.py               Interactive .env generator
├── Makefile                Developer convenience targets
├── requirements.txt        Python dependencies
└── README.md
```

---

## Working model

The Python modules inside `run/` form a minimal certificate authority:

| Module | Purpose |
|--------|---------|
| `init_ca.py` | Generates the root RSA-4096 key and self-signed CA certificate (10-year validity). |
| `issue_cert.py` | Creates RSA-2048 leaf certificates signed by the root CA (825-day validity). |
| `ca_core.py` | Shared utilities — loads the CA key/cert from disk, ensures directories exist. |
| `watch.py` | Uses `watchdog` to monitor a domain list file and issue certificates for new entries. |
| `mini_ca.py` | Typer CLI that wires everything together as `init`, `issue`, and `watch` commands. |

The Docker entrypoints (`entry.sh`, `entry-init.sh`) call the CLI. The optional `cert-sync.sh` pushes certificates into Nginx Proxy Manager.

---

## Docker stack

| Service | Profile | Role | Restart |
|---------|---------|------|---------|
| **init** | `setup` | Root CA bootstrap (`entry-init.sh`) | no |
| **minica** | *(default)* | Live domain watcher (`entry.sh`) | `unless-stopped` |
| **cli** | `cli` | Ad-hoc certificate commands | no |
| **db** | *(default)* | MariaDB for NPM | `unless-stopped` |
| **npm** | *(default)* | Nginx Proxy Manager | `unless-stopped` |
| **cert-sync** | `sync` | Copy and register PEMs in NPM | no |

All mini-ca services share the `minica-data` volume.

---

## Command-line interface

```text
mini_ca.py init   [--force]                Create (or rotate) the root CA
mini_ca.py issue  DOMAIN [--san ALT ...]   Issue a leaf certificate
mini_ca.py watch  [--file PATH]            Watch a domain list and auto-issue
```

**Examples:**

```bash
# Issue a wildcard cert with extra SANs
docker compose run --rm cli issue "*.wild.dev" --san api.wild.dev --san admin.wild.dev

# Store certs under the full FQDN folder instead of the first label
docker compose run --rm cli issue app.example.com --full-path
```

---

## NPM certificate sync

Optional — useful if you run **Nginx Proxy Manager** alongside mini-ca.

The `cert-sync` service:
1. Copies `<domain>.crt` and `<domain>.key` into NPM's `/data/custom_ssl/` directory.
2. Authenticates against the NPM admin API (`/api/tokens`).
3. Creates or looks up a certificate record (`/api/nginx/certificates`).
4. Uploads the PEM files (`/api/nginx/certificates/<id>/upload`).

**Manual run:**

```bash
COMPOSE_PROFILES=sync docker compose run --rm \
  -e DOMAIN=demo.acme.test cert-sync
```

**Via Makefile:**

```bash
make issue DOMAIN=demo.acme.test   # issues cert + syncs to NPM + restarts NPM
```

---

## Live domain watch

The default `minica` service watches `/data/DOMAINS` for changes. Append a domain to trigger automatic issuance:

```bash
make up
echo "store.acme.dev" >> minica-data/DOMAINS
```

Watcher output:

```text
✅  Certificate issued for 'store.acme.dev' → /data/certificates/store
```

---

## Make targets

| Target | Description |
|--------|-------------|
| `help` | Print available targets. |
| `setup` | `build` → `init` → `up` (full bootstrap). |
| `build` | Build the Docker image. |
| `init` | Run the CA bootstrap (setup profile). |
| `up` | Start the stack (no build). |
| `issue` | `make issue DOMAIN=foo.dev` — issue cert, sync to NPM, restart NPM. |
| `logs` | Follow live container logs. |
| `clean` | Stop and remove containers (keeps data volumes). |
| `nuke` | Full wipe — containers, volumes, images, and dangling layers. |

---

## Clean / Rebuild matrix

| Goal | Command |
|------|---------|
| Stop stack, keep certificates | `make clean` |
| Wipe certificates, keep images | `make clean && docker volume rm mini-ca_minica-data` |
| Re-bootstrap root CA | `make clean && make init` |
| Delete everything | `make nuke` |

---

## `make nuke` — full reset

```bash
make nuke
# 🔴  NUKE: destroying compose project 'mini-ca' …
# ✅  project 'mini-ca' wiped clean
```

What it does:

1. `docker compose down --volumes --remove-orphans` — stop all services and delete volumes.
2. Remove any remaining containers whose name matches `mini-ca_`.
3. Delete all Docker volumes starting with `mini-ca_`.
4. Remove all images labelled `com.docker.compose.project=mini-ca` and prune dangling images older than 24 hours.

---

## Configuration

### `.env` (generated by `genenv.py`)

| Variable | Default | Description |
|----------|---------|-------------|
| `TZ` | `America/New_York` | Container timezone. |
| `DOCKERHOST` | *(auto-detected)* | Host IP for port bindings. |
| `DB_MYSQL_HOST` | `db` | MariaDB hostname. |
| `DB_MYSQL_PORT` | `3306` | MariaDB port. |
| `DB_MYSQL_USER` / `DB_MYSQL_NAME` | *(container name)* | Database user and schema. |
| `MYSQL_ROOT_PASSWORD` / `DB_MYSQL_PASSWORD` | *(random)* | Database passwords (auto-generated). |
| `INITIAL_ADMIN_EMAIL` | `admin@npm` | NPM admin email (used by cert-sync). |
| `NPM_INITIAL_PASSWORD` | `changeme` | NPM admin password (used by cert-sync). |
| `NPM_PORT` / `NPM_UI_PORT` / `NPM_S_PORTS` | `80` / `81` / `443` | Host-port bindings for NPM. |

---

## Best practices & CI notes

- **Back up `rootCA.key` and `rootCA.crt`** — losing them invalidates every issued certificate.
- Use `make setup` in CI to mint short-lived certs, then `make nuke` to clean up.
- Mount `minica-data` **read-only** in any container that only needs to read certificates.
- The root CA private key is stored **unencrypted** — keep the volume secure and restrict access.
- Leaf certificates use 825-day validity (within the CA/Browser Forum maximum for public CAs).

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `data must NOT have additional properties` during sync | Ensure the current `cert-sync.sh` (multipart upload version) is bind-mounted. Check the path and that the file is executable. |
| Cert listed in NPM but "invalid path" error | Verify the NPM container sees the same `/data` volume as cert-sync. |
| Exit 22 from `make issue` | `curl --fail` received a non-2xx response. Run cert-sync manually with `COMPOSE_PROFILES=sync` and inspect the HTTP status and response body. |
| `rootCA already present` on init | Expected when the CA exists. Use `--force` to regenerate: `docker compose run --rm cli init --force` |

---

## License

Released under the [MIT License](LICENSE).
