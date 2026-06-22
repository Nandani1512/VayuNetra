# VayuNetra — Air Intelligence Eye

Geospatial air-quality intelligence platform. Forecasts AQI at 1 km × 1–72 h,
attributes pollution to source categories, ranks enforcement hotspots, and
delivers multilingual citizen advisories — all on free, public data.

See `plan.txt` (product spec) and `implementation_plan.md` (engineering plan).
For the pitch, see `docs/deck/deck.md`; for the live walkthrough, `docs/demo_script.md`.

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
make eval                 # -> reports/eval_<ts>/summary.md
```

### Deterministic demo (network-proof)

```bash
export DEMO_MODE=true && make demo    # serves frozen snapshots from reports/demo/
```

`make eval` produces a complete `reports/eval_<ts>/summary.md` even with **no
stack running** — it degrades live → frozen demo fixtures → a seeded synthetic
benchmark, tagging each metric's provenance. See `docs/demo_script.md`.

## Makefile targets

| Target              | What it does                                            |
|---------------------|---------------------------------------------------------|
| `make up`           | Brings docker-compose stack up + runs migrations        |
| `make down`         | Stops stack and removes volumes                         |
| `make demo`         | Up + loads frozen-snapshot demo data (`DEMO_MODE`)      |
| `make test`         | Runs unit + integration tests                           |
| `make lint`         | ruff + black --check + mypy                             |
| `make serve`        | Runs FastAPI + UI on http://127.0.0.1:8000              |
| `make smoke`        | Hits every endpoint and checks p95 budgets              |
| `make ingest`       | Triggers all Prefect ingestion flows                    |
| `make bootstrap`    | Pulls 6 months of history into DVC snapshot             |
| `make eval`         | Full eval harness → `reports/eval_<ts>/summary.md`      |
| `make drift`        | Evidently feature-drift report → `reports/drift/`       |
| `make data-quality` | Data-quality expectation suites over core tables        |
| `make verify-keys`  | Pings every external API and prints OK/FAIL             |

## Evaluation harness (Phase 9)

`python -m vayunetra.eval.run [all|forecast|attribution|enforcement|advisory|latency]`

Writes CSVs, PNGs, `results.json` and a deck-ready `summary.md` into
`reports/eval_<timestamp>/`. Covers forecast lift vs persistence/climatology,
attribution deviation vs SAFAR, enforcement precision@k, advisory language
coverage + RAG citation rate, and in-process endpoint p50/p95.

## Observability (Phase 10)

- **Metrics:** the API exposes Prometheus metrics at `/metrics`
  (`vayunetra_http_requests_total`, `…_request_duration_seconds`,
  `…_inference_latency_seconds`, and `…_forecast_rmse` / `…_forecast_lift_vs_persistence`
  fed from the latest `make eval` run).
- **Tracing & logs:** OpenTelemetry spans (OTLP) + structured JSON logs carrying
  `request_id` and `trace_id`.
- **Dashboards:** `deploy/grafana/dashboards/` — API SLOs, Model Performance,
  Ingestion Health (auto-provisioned; Prometheus alert rules in
  `deploy/prometheus/alerts.yml`). Bring up with the `obs` profile.
- **Drift & data quality:** `vayunetra.mlops.drift` (Evidently) and
  `vayunetra.mlops.data_quality` (dependency-free expectation suites).

## Deliverables (Phase 11)

- `docs/architecture.mmd` — Mermaid system diagram
  (`mmdc -i docs/architecture.mmd -o docs/architecture.png`).
- `docs/deck/deck.md` — 12-slide Marp deck.
- `docs/demo_script.md` — scripted 5-min demo + deterministic backup.
- `docs/roadmap.md` — post-hackathon production roadmap (Phase 12).

## Repo layout

See `implementation_plan.md` §1.

## Status

| Phase | Status |
|------:|--------|
| 0 — Foundation | done |
| 1 — Data ingestion + storage | done |
| 2 — Forecast | done |
| 3 — LUR downscaling | done |
| 4 — Source attribution | done |
| 5 — Enforcement intelligence | done |
| 6 — API + agents | done (REST + LangGraph supervisor) |
| 7 — Web UI | done (MapLibre single-page + multi-city compare) |
| 8 — Citizen bot | done (12 languages × Telegram + IVR) |
| 9 — Eval harness | done |
| 10 — MLOps & observability | done |
| 11 — Deliverables | done |
| 12 — Production roadmap | documented (`docs/roadmap.md`) |
