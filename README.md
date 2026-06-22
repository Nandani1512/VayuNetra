# VayuNetra — Air Intelligence Eye

Geospatial air-quality intelligence platform. Forecasts AQI at 1 km × 1-72 h,
attributes pollution to source categories, ranks enforcement hotspots, and
delivers multilingual citizen advisories — all on free, public data.

See `plan.txt` (product spec) and `implementation_plan.md` (engineering plan).

## Quick start

```bash
# 1. Copy env template and fill in keys
cp .env.example .env
$EDITOR .env

# 2. Bring up the stack
make up

# 3. Verify external API keys
make verify-keys

# 4. Bootstrap historical data (one-off, ~30 min)
make bootstrap

# 5. Train forecast model
make train-forecast city=delhi pollutant=pm25 horizon=24

# 6. Run evaluation harness
make eval
```

## Makefile targets

| Target              | What it does                                            |
|---------------------|--------------------------------------------------------|
| `make up`           | Brings docker-compose stack up + runs migrations       |
| `make down`         | Stops stack and removes volumes                        |
| `make demo`         | Up + loads frozen-snapshot demo data                   |
| `make test`         | Runs unit + integration tests                          |
| `make lint`         | ruff + black --check + mypy                            |
| `make ingest`       | Triggers all Prefect ingestion flows                   |
| `make bootstrap`    | Pulls 6 months of history into DVC snapshot            |
| `make eval`         | Runs evaluate.py — forecast/attribution/enforce/advis. |
| `make verify-keys`  | Pings every external API and prints OK/FAIL            |

## Repo layout

See `implementation_plan.md` §1.

## Status

| Phase | Status |
|------:|--------|
| 0 — Foundation | done |
| 1 — Data ingestion + storage | done |
| 2 — Forecast | not started |
| 3 — LUR downscaling | not started |
| 4 — Source attribution | not started |
| 5 — Enforcement intelligence | not started |
| 6 — API + agents | not started |
| 7 — Web UI | not started |
| 8 — Citizen bot | not started |
| 9 — Eval harness | not started |
| 10 — MLOps | continuous |
| 11 — Deliverables | not started |
