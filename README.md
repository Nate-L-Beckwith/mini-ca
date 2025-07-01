
# 🔐 mini‑ca

*A zero‑dependency Certificate Authority (plus Nginx‑Proxy‑Manager sync) in a single Docker stack*
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

---

## Contents <!-- omit in toc -->
1. [Highlights](#highlights)
2. [Quick start](#quick-start)
3. [Repository layout](#repository-layout)
4. [Docker stack](#docker-stack)
5. [Command‑line interface](#command-line-interface)
6. [NPM certificate sync](#npm-certificate-sync)
7. [Live domain‑watch](#live-domain-watch)
8. [Make targets](#make-targets)
9. [Clean ↔ Rebuild matrix](#clean-rebuild-matrix)
10. [💣 `make nuke` – full reset](#make-nuke-full-reset)
11. [Configuration](#configuration)
12. [Best practices & CI notes](#best-practices--ci-notes)
13. [Troubleshooting](#troubleshooting)

---

## Highlights

| ✔︎ | Feature |
|----|---------|
| **One‑shot root CA** | `entry-init.sh` generates *rootCA.key* + *rootCA.crt* (10 yrs). |
| **Leaf certs on demand** | `issue <domain> [--san ALT...]` drops key / cert / chain per domain. |
| **Wildcard & unlimited SANs** | issue `*.example.dev` with any number of SANs. |
| **Continuous watcher** | tails `/data/DOMAINS` → auto‑issues as lines appear. |
| **Nginx‑Proxy‑Manager sync** | Optional *sync* profile: copies the PEMs into NPM & registers them through the admin API. |
| **Profiles** | *setup* · *minica* · *cli* · *sync*. |
| **Friendly Makefile** | `help · setup · up · issue · logs · clean · nuke`. |
| **Complete wipe** | `make nuke` kills containers, volumes, images **plus** BuildKit cache. |
| **Offline image** | Wheels baked at build‑time → no outbound net at runtime. |

---

## Quick start

```bash
git clone https://github.com/your‑org/mini‑ca.git
cd mini‑ca

# 1  Generate a .env file (interactive)
python3 genenv.py

# 2  Build the image and bootstrap the root CA (+ persistent volume)
make setup                 # == build → init → up

# 3  Mint a certificate whenever you need one
docker compose run --rm cli issue blog.acme.test --san www.blog.acme.test
```

`--san` is optional. If omitted, the tool sets SAN = DOMAIN for you.

Result:

```text
minica-data/
├─ rootCA/
│  ├─ rootCA.key
│  └─ rootCA.crt
└─ certificates/
   └─ blog.acme.test/
      ├─ blog.acme.test.key
      ├─ blog.acme.test.crt
      └─ chain.pem
```

> **Trust chain:** import *rootCA.crt* once – every leaf cert is trusted system‑wide.

---

## Repository layout

```text
mini‑ca/
├─ docker/
│  ├─ Dockerfile             # multi‑stage (minica runtime + wheels)
│  ├─ cert-sync.sh           # host‑mounted script that copies + registers PEMs in NPM
│  ├─ entry.sh               # default entrypoint (watch / cli)
│  └─ entry-init.sh          # one‑shot CA bootstrap
├─ run/                      # pure‑Python CA logic (Typer CLI)
│  ├─ mini_ca.py             # main CLI entrypoint
│  └─ …
├─ docker-compose.yml        # production bundle (with sync profile)
├─ Makefile                  # developer convenience wrapper
└─ README.md                 # you’re here
```

## Working model

The Python files inside `run/` form a minimal certificate authority:

* **init_ca.py** – creates the root key and certificate.
* **issue_cert.py** – generates leaf certificates signed by that root CA.
* **watch.py** – monitors a domain list and issues new certificates
  automatically.

`mini_ca.py` exposes these building blocks through a Typer CLI. The Docker
entrypoints (`entry.sh` and `entry-init.sh`) call the CLI to start the domain
watcher or to bootstrap the CA. Optional `cert-sync.sh` pushes the resulting
certificates into Nginx‑Proxy‑Manager.

---

## Docker stack

| Service   | Profiles          | Role / Entrypoint                | Restart |
|-----------|-------------------|----------------------------------|---------|
| **init**  | `setup`           | root CA bootstrap (`entry-init`) | *no*    |
| **minica**| *(default)*       | live domain watcher (`entry`)    | `unless-stopped` |
| **cli**   | `cli`             | ad‑hoc CA commands (`entry`)     | *no*    |
| **cert-sync** | `sync`        | copy & register PEMs in NPM (`cert-sync.sh`) | *no* |

All share the `minica-data` volume.

---

## Command‑line interface

```bash
mini_ca.py init   [--force]            # rotate root CA
mini_ca.py issue  DOMAIN [--san ALT…]  # one-off leaf
mini_ca.py watch  [--file /data/DOMAINS]
```

CLI container helper:

```bash
docker compose run --rm cli issue "*.wild.dev" --san api.wild.dev
```

---

## NPM certificate sync

Optional but handy if you run **Nginx‑Proxy‑Manager** alongside:

* `cert-sync` mounts
  * `/certs` (read‑only) – everything minica emits
  * `/data` (shared) – NPM’s own volume
* Script flow
  1. Copy `*.pem` → `/data/custom_ssl/<domain>`
  2. Authenticate (`/api/tokens`).
  3. Create/lookup certificate record (`/api/nginx/certificates`).
  4. Upload PEMs (`/api/nginx/certificates/<id>/upload`).

Run manually:

```bash
COMPOSE_PROFILES=sync docker compose run --rm \
  -e DOMAIN=demo.acme.test cert-sync
```

---

## Live domain‑watch

```bash
make up                           # starts minica watcher
echo "store.acme.dev" >> minica-data/DOMAINS
```

Watcher log:

```
✅  Certificate issued for 'store.acme.dev'
```

---

## Make targets

| Target | Description |
|--------|-------------|
| **setup** | build → init → up (*everything, once*). |
| **issue** | `make issue DOMAIN=foo.dev [SAN=alt.dev]` – thin wrapper around the CLI service. |
| **up** | start the watcher (no build). |
| **clean** | stop and remove containers (keeps the data volume). |
| **nuke** | full wipe – all containers, networks, **any volume or image whose name starts `mini-ca_`** and every image labelled `com.docker.compose.project=mini-ca`. |

---

## Clean ↔ Rebuild matrix

| Want | Command |
|------|---------|
| Stop stack, keep certificates | `make clean` |
| Wipe certificates, keep images | `make clean && docker volume rm mini-ca_minica-data` |
| Re‑bootstrap root CA | `make clean && make init` |
| Delete *everything* (volumes, images, build cache) | `make nuke` |

---

## make nuke – full reset

```bash
make nuke
🔴  NUKE: destroying compose project ‘mini-ca’ …
✅  project 'mini-ca' wiped clean
```

Internally:

1. `docker compose down --volumes --remove-orphans`
2. `docker rm -f $(docker ps -aq --filter name=mini-ca_)`
3. `docker volume rm $(docker volume ls -q | grep '^mini-ca_')`
4. `docker rmi -f $(docker images -q --filter label=com.docker.compose.project=mini-ca)`
5. `docker builder prune -f`

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `INITIAL_ADMIN_EMAIL` / `NPM_INITIAL_PASSWORD` | `admin@example.com` / `changeme` | Credentials used by cert‑sync to call the NPM API. |
| `WATCH` | `1` | Set to `0` to skip starting the watcher in `make up`. |
| `UID` (build‑arg) | `1001` | Map container user to host user id. |

---

## Best practices & CI notes

* **Back up** `rootCA.key` / `rootCA.crt` – losing them invalidates every cert.
* Use `make setup` inside CI to mint short‑lived certs, then `make nuke` at the end.
* Mount `minica-data` *read‑only* in any container that only needs to read certs.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `data must NOT have additional properties` when syncing | Script still posting old JSON – ensure `cert-sync.sh` with multipart upload is mounted (bind‑mount path correct and file executable). |
| Cert listed in NPM but invalid path error | Make sure NPM container sees the same `/data` volume as cert‑sync. |
| Exit 22 in `make issue` | Curl `--fail` sees non‑2xx – run cert‑sync manually with `COMPOSE_PROFILES=sync` and inspect the HTTP status / body. |

---

### License

Released under the [MIT License](LICENSE).
