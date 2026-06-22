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

train-forecast: ## Train the LightGBM quantile forecaster (params: city, pollutant, horizon)
	poetry run python -m vayunetra.models.forecast.lightgbm_trainer \
	  --city $${city:-delhi} --pollutant $${pollutant:-pm25} --horizon $${horizon:-24}

loso-cv: ## Leave-one-station-out CV for the forecast model
	poetry run python -m vayunetra.models.forecast.cv \
	  --city $${city:-delhi} --pollutant $${pollutant:-pm25} --horizon $${horizon:-24}

eval-forecast: ## Walk-forward evaluation across horizons
	poetry run python -m vayunetra.eval.walk_forward \
	  --city $${city:-delhi} --pollutant $${pollutant:-pm25}

train-lur: ## Train the LUR downscaling model
	poetry run python -m vayunetra.models.lur.trainer \
	  --city $${city:-delhi} --pollutant $${pollutant:-pm25}

predict-grid: ## Predict 1km grid for a city/horizon and write into forecast table
	poetry run python -m vayunetra.models.lur.predictor \
	  --city $${city:-delhi} --pollutant $${pollutant:-pm25} --horizon $${horizon:-24}

enforce: ## Run hotspot detection + LLM brief for a city
	.venv/bin/python -c "import asyncio, json; from vayunetra.enforcement.service import enforce; \
	  print(json.dumps(asyncio.run(enforce(city='$${city:-delhi}', horizon_h=$${horizon:-24})), indent=2, default=str))"

serve: ## Run the FastAPI + UI on http://127.0.0.1:8000
	set -a && source .env && set +a && \
	.venv/bin/uvicorn vayunetra.api.main:app --host 127.0.0.1 --port 8000 --reload

smoke: ## Hit every API endpoint and verify p95 budgets
	poetry run python scripts/smoke_endpoints.py

clean: ## Remove caches
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +
