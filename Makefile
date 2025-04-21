# ────────────────────────── variables ──────────────────────────
PROJECT      ?= mini-ca
COMPOSE       = docker compose -f docker-compose.yml --project-name $(PROJECT)

CLI_RUN       = $(COMPOSE) --profile cli  run --rm cli
SYNC_RUN      = $(COMPOSE) --profile sync run --rm -e DOMAIN=$(DOMAIN) cert-sync
LOCAL_IMAGES = minica:latest

# ────────────────────────── targets ────────────────────────────
.PHONY: help build setup up init issue restart-npm logs clean nuke

help:
	@echo "\nMini‑CA / NPM helper targets\n"
	@printf "  %-15s%s\n" setup      "build images if needed, bootstrap root‑CA, start stack"
	@printf "  %-15s%s\n" up         "start stack (NO build)"
	@printf "  %-15s%s\n" issue      "make issue DOMAIN=<host> – cert + import → NPM"
	@printf "  %-15s%s\n" logs       "follow live logs"
	@printf "  %-15s%s\n" clean      "stop containers (keep data)"
	@printf "  %-15s%s\n" nuke       "⚠  remove containers, volumes & local images\n"

# --------------------------------------------------------------
build:
	$(COMPOSE) build

init:
	$(COMPOSE) --profile setup up -d

setup: build init up

up:
	$(COMPOSE) up -d

logs:
	$(COMPOSE) logs -f

issue: | $(if $(DOMAIN),,no_domain)
	@echo "👉  issuing $(DOMAIN)"
	$(CLI_RUN) issue $(DOMAIN)
	@echo "👉  copying into NPM"
	$(SYNC_RUN)
	@$(MAKE) restart-npm

restart-npm:
	@$(COMPOSE) restart -t 5 npm
	@echo "✅  cert imported - check NPM ▸ SSL Certificates"

clean:
	$(COMPOSE) down --remove-orphans

nuke:
	@echo "🔴  NUKE: destroying compose project ‘mini-ca’ ..."
	# 1. Stop & remove every container, network, and volume in the project
	docker compose -f docker-compose.yml --project-name mini-ca down --volumes --remove-orphans --timeout 30 || true
	# 2. Remove *any* container whose name or label starts with mini-ca_
	docker ps -a --filter "name=mini-ca_" --format "{{.ID}}" | xargs -r docker rm -f
	# 3. Delete all volumes whose names start with mini-ca_
	docker volume ls -q | grep '^mini-ca_' | xargs -r docker volume rm -f
	# 4. Delete every image built by this compose project (labelled automatically)
	docker images -a --filter "label=com.docker.compose.project=mini-ca" --format "{{.ID}}" \
	    | xargs -r docker image rm -f
	docker image prune -af --filter "until=24h" > /dev/null

	@echo "✅  project 'mini-ca' wiped clean"

no_domain:
	$(error ❌  DOMAIN variable not set – use ‘make issue DOMAIN=…’)
