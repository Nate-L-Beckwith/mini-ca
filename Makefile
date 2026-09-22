# ───────────────────────────── variables ──────────────────────────────────
PROJECT   ?= mini-ca
IMAGE     ?= ghcr.io/nate-l-beckwith/mini-ca
TAG       ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo dev)
VERSION   ?= $(TAG)
GIT_SHA   ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)
PLATFORMS ?= linux/amd64,linux/arm64
PYTHON    ?= python3

COMPOSE   = docker compose -f docker-compose.yml --project-name $(PROJECT)
CLI_RUN   = $(COMPOSE) --profile cli  run --rm cli
SYNC_RUN  = $(COMPOSE) --profile sync run --rm cert-sync
SAN_ARGS  = $(foreach s,$(SAN),--san "$(s)")

.PHONY: help build pull push test lint setup init init-force up down restart ps logs shell \
	issue cert renew sync add list info verify export-ca restart-npm clean nuke _need-domain

help: ## show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nmini-ca / NPM helper targets\n\n"} \
	/^[a-zA-Z_-]+:.*##/ {printf "  %-12s %s\n", $$1, $$2} \
	END {printf "\nVariables: DOMAIN=<host>  SAN=\"<alt> <alt>\"  IMAGE=%s  TAG=%s\n\n", "$(IMAGE)", "$(TAG)"}' $(MAKEFILE_LIST)

# ───────────────────────────── image ──────────────────────────────────────
build: ## build the mini-ca image (tag from MINICA_IMAGE in .env)
	$(COMPOSE) build --build-arg VERSION=$(VERSION) --build-arg VCS_REF=$(GIT_SHA) minica

pull: ## pull third-party images (skips the locally built one)
	$(COMPOSE) pull --ignore-buildable

push: ## build multi-arch and push $(IMAGE):$(TAG) and :latest  (make push TAG=1.1.0)
	docker buildx build --platform $(PLATFORMS) -f docker/Dockerfile \
	--build-arg VERSION=$(VERSION) --build-arg VCS_REF=$(GIT_SHA) \
	-t $(IMAGE):$(TAG) -t $(IMAGE):latest --push .

test: ## run the Python test-suite
	$(PYTHON) -m pytest -q

lint: ## shellcheck the entrypoints, byte-compile the Python
	shellcheck docker/*.sh
	$(PYTHON) -m compileall -q run tests genenv.py

# ───────────────────────────── stack ──────────────────────────────────────
setup: build init up ## build image → bootstrap root CA → start stack

init: ## create the root CA (one-shot; runs as root to fix volume ownership)
	$(COMPOSE) --profile setup run --rm init

init-force: ## rotate the root CA (previous key/cert kept as .bak-*)
	$(COMPOSE) --profile setup run --rm init --force

up: ## start the stack (no build)
	$(COMPOSE) up -d

down: ## stop and remove containers (keeps volumes)
	$(COMPOSE) down --remove-orphans

restart: ## restart all services
	$(COMPOSE) restart

ps: ## show service status
	$(COMPOSE) ps

logs: ## follow logs
	$(COMPOSE) logs -f --tail=100

shell: ## shell inside a throw-away mini-ca container
	$(COMPOSE) --profile cli run --rm --entrypoint sh cli

# ───────────────────────────── certificates ───────────────────────────────
issue: _need-domain ## make issue DOMAIN=host [SAN="alt ip"] — issue, push into NPM, reload NPM
	$(CLI_RUN) issue "$(DOMAIN)" $(SAN_ARGS)
	@$(MAKE) --no-print-directory sync DOMAIN="$(DOMAIN)"

cert: _need-domain ## make cert DOMAIN=host [SAN=...] — issue only (no NPM)
	$(CLI_RUN) issue "$(DOMAIN)" $(SAN_ARGS)

renew: _need-domain ## make renew DOMAIN=host — re-issue keeping SANs, push into NPM
	$(CLI_RUN) renew "$(DOMAIN)"
	@$(MAKE) --no-print-directory sync DOMAIN="$(DOMAIN)"

sync: _need-domain ## make sync DOMAIN=host — push an issued cert into NPM and reload it
	$(SYNC_RUN) "$(DOMAIN)"
	@$(MAKE) --no-print-directory restart-npm

add: _need-domain ## make add DOMAIN=host [SAN=...] — append to the watched list
	$(CLI_RUN) add "$(DOMAIN)" $(SAN_ARGS)

list: ## list issued certificates and expiry
	$(CLI_RUN) list

info: ## show root CA subject, expiry and fingerprint
	$(CLI_RUN) info

verify: _need-domain ## make verify DOMAIN=host — check the cert chains to the CA
	$(CLI_RUN) verify "$(DOMAIN)"

export-ca: ## copy rootCA.crt to ./rootCA.crt for import into trust stores
	$(COMPOSE) --profile cli run --rm -T --entrypoint cat cli /data/rootCA/rootCA.crt > rootCA.crt
	@echo "✅  rootCA.crt written to $(CURDIR)/rootCA.crt"

restart-npm: ## reload NPM so it picks up new certificate files
	@$(COMPOSE) restart -t 5 npm
	@echo "✅  NPM reloaded — check SSL Certificates in the NPM UI"

# ───────────────────────────── cleanup ────────────────────────────────────
clean: down ## alias for down (stop containers, keep data)

nuke: ## ⚠  remove containers, volumes and images of this project
	@echo "🔴  NUKE: destroying compose project '$(PROJECT)' …"
	$(COMPOSE) down --volumes --remove-orphans --timeout 30 || true
	docker ps -aq --filter "label=com.docker.compose.project=$(PROJECT)" | xargs -r docker rm -f
	docker volume ls -q --filter "label=com.docker.compose.project=$(PROJECT)" | xargs -r docker volume rm -f
	docker images -q --filter "label=com.docker.compose.project=$(PROJECT)" | xargs -r docker image rm -f
	@echo "✅  project '$(PROJECT)' wiped clean"

_need-domain:
	@test -n "$(DOMAIN)" || { echo "❌  DOMAIN not set — e.g. make $(firstword $(MAKECMDGOALS)) DOMAIN=host.example.lan" >&2; exit 1; }
