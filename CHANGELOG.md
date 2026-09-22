# Changelog

## 1.1.0 — 2026-09-22

### Fixed
- **NPM sync could not log in against Nginx Proxy Manager ≥ 2.13**: NPM no longer ships a default admin, and `.env` never set `INITIAL_ADMIN_PASSWORD`. `genenv.py` now writes it (random by default) and `npm-sync` creates the admin itself when NPM is still un-configured.
- cert-sync created a **new NPM certificate record on every run**; it now looks the record up by name and only creates one when missing.
- cert-sync could not find **wildcard** or `--full-path` certificates (folder logic disagreed with the Python code).
- The watcher **re-issued every domain (new keys!) on each container restart**; it now leaves valid certificates alone and only re-issues when missing, expired, within the renewal window, or when a line gains a SAN.
- The watcher only reacted to `modified` events, so atomic saves (editors, `docker cp`) were missed; exceptions inside issuance could silently kill it; `docker stop` was not handled.
- IP addresses passed as SANs were encoded as DNS names; they are now proper `IPAddress` SANs.
- Domain strings were used unvalidated as paths (a line like `/data/rootCA/rootCA` could overwrite the root key). Names are now validated and normalised.
- Private keys were written world-readable; they are now `0600`.
- The whole CA volume, including `rootCA.key`, was mounted into the NPM container; only `certificates/` is now mounted, and only into `cert-sync`.
- `env_file: .env` handed every secret to every container; each service now receives only what it needs.
- `make nuke` pruned **all** unused images on the host older than 24 h; it now removes only this project's images.
- `make init` started the whole stack alongside the one-shot init container (racing on volume permissions); it now runs the init container in the foreground.
- Build from a Windows checkout produced CRLF entrypoints: `.gitattributes` enforces LF and the Dockerfile strips CRs defensively.
- Missing `PYTHONUNBUFFERED` delayed watcher logs in `docker logs`.

### Added
- CLI: `renew`, `list [--json]`, `info [--json]`, `verify`, `add`, `npm-sync`, `--version`; `issue --days`; `init --name/--days` with backups on `--force`.
- Watcher: `#` comments and per-line extra SANs in `DOMAINS`, renewal window (`MINICA_RENEW_DAYS`), periodic re-check (`--rescan`), `--polling` observer, graceful SIGTERM.
- `<name>.fullchain.crt` (leaf + root) next to every certificate.
- Root CA created automatically on first start (`MINICA_AUTO_INIT`); `/data` pre-owned by the service user so fresh volumes need no root init.
- `MINICA_DATA`, `MINICA_CA_NAME`, `MINICA_ROOT_DAYS`, `MINICA_LEAF_DAYS`, `MINICA_RENEW_DAYS` environment variables.
- Image: OCI labels, `HEALTHCHECK`, pinned dependencies, `.dockerignore` allow-list, `VERSION` build arg.
- `Makefile`: `push`, `cert`, `renew`, `sync`, `add`, `list`, `info`, `verify`, `export-ca`, `shell`, `test`, `lint`, `down`, `ps`, `init-force`, self-documenting `help`.
- `genenv.py --yes` and flags for CI; keeps the previous `.env` as `.env.bak`; `.env.example`.
- pytest suite (CA, watcher, CLI, fake NPM API) and a GitHub Actions workflow that tests and publishes multi-arch images to GHCR.

### Changed
- Wildcard certificate files are named `_wildcard.example.lan.*` (legal on Windows); old names are still recognised.
- `cert-sync` is now `mini_ca.py npm-sync` in the mini-ca image; `docker/cert-sync.sh`, the Alpine image and the runtime `apk add` are gone.
- Root certificate: `pathLen=0`, no `digitalSignature` key usage; leaf AKI mirrors the root SKI; certificates are back-dated one hour for clock skew.
- Compose image reference is `${MINICA_IMAGE:-ghcr.io/nate-l-beckwith/mini-ca:latest}`; only the `minica` service carries `build:`.
- `jc21/nginx-proxy-manager` pinned to the `2` tag; NPM container name follows `NPM_CONTAINER_NAME`.

## 1.0.0

Initial release: root CA init, leaf issuance, `/data/DOMAINS` watcher, shell-based NPM sync.
