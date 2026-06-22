.PHONY: help up down demo logs ps test lint fmt ingest bootstrap eval verify-keys clean migrate shell

COMPOSE      := docker compose -f deploy/docker-compose.yml --env-file .env
COMPOSE_DEMO := docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.demo.yml --env-file .env

help:
	@awk 'BEGIN {FS=":.*##"; printf "Targets:\n"} /^[a-zA-Z_-]+:.*##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

up: ## Bring up the full dev stack and apply migrations
	$(COMPOSE) --profile dev up -d
	$(MAKE) migrate

down: ## Tear down stack and remove volumes
	$(COMPOSE) --profile dev --profile obs --profile demo down -v

demo: ## Bring up the stack in DEMO_MODE serving frozen snapshots
	$(COMPOSE_DEMO) --profile demo up -d
	$(MAKE) migrate

logs: ## Tail logs from all services
	$(COMPOSE) logs -f --tail=200

ps: ## List running containers
	$(COMPOSE) ps

migrate: ## Apply schema migrations
	$(COMPOSE) exec -T api python -m vayunetra.storage.bootstrap || \
	  poetry run python -m vayunetra.storage.bootstrap

shell: ## Open a psql shell on the database
	$(COMPOSE) exec postgis psql -U $${POSTGRES_USER:-vayunetra} -d $${POSTGRES_DB:-vayunetra}

test: ## Run pytest
	poetry run pytest -q

lint: ## Run linters
	poetry run ruff check src tests scripts
	poetry run black --check src tests scripts
	poetry run mypy src

fmt: ## Auto-format code
	poetry run ruff check --fix src tests scripts
	poetry run black src tests scripts

verify-keys: ## Ping every external API and report OK/FAIL
	poetry run python scripts/verify_keys.py

ingest: ## Trigger all Prefect ingestion flows once for the configured city
	poetry run python -m vayunetra.ingestion --all-cities

bootstrap: ## Pull 6 months of history into a DVC snapshot
	poetry run python scripts/bootstrap_history.py

eval: ## Run the full evaluation harness
	poetry run python -m vayunetra.eval.run --all

smoke: ## Hit every API endpoint and verify p95 budgets
	poetry run python scripts/smoke_endpoints.py

clean: ## Remove caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
