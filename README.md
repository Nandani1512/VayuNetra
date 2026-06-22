# VayuNetra — AI-Powered Urban Air Quality Intelligence

> *From reactive monitoring to proactive, evidence-based intervention.*

VayuNetra is a geospatial air-quality intelligence platform that forecasts AQI at **1 km × 24–72 h** resolution, attributes pollution to source categories, ranks enforcement hotspots, and delivers multilingual citizen advisories — all built on free, public data.

Built for **ET AI Hackathon 2026 — Problem Statement 5**.

---

## 🎯 Problem

India's cities have monitoring data but lack the intelligence layer to act on it. City administrators need:
- **Where** is pollution worst right now and in 24 hours?
- **Why** — which sources are responsible at each location?
- **What to do** — where to deploy inspectors for maximum impact?
- **Who to warn** — which vulnerable populations need alerts?

VayuNetra answers all four.

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        DATA SOURCES                              │
│  OpenAQ · Open-Meteo · Sentinel-5P · MODIS AOD · FIRMS fires  │
│  CAMS · OSM · WorldPop · TomTom Traffic · LULC                 │
└──────────────────────────┬──────────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  INGESTION (Prefect flows) → PostGIS + TimescaleDB · MinIO · Redis │
└──────────────────────────┬───────────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│                      ML PIPELINE                                  │
│  LightGBM quantile forecast (p10/p50/p90 per station × horizon) │
│  LUR downscaler → 1 km grid                                     │
│  SHAP attribution + HYSPLIT back-trajectory + overlay            │
│  Getis-Ord Gi* hotspots + DBSCAN + LLM enforcement briefs       │
└──────────────────────────┬───────────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  SERVING: FastAPI + LangGraph Multi-Agent Supervisor             │
│  Channels: MapLibre Web UI · Telegram Bot · IVR (TwiML)         │
└──────────────────────────────────────────────────────────────────┘
```

Full Mermaid diagram: [`docs/architecture.md`](docs/architecture.md)

---

## ✨ Key Capabilities

| Capability | What it does | Tech |
|-----------|-------------|------|
| **Hyperlocal AQI Forecast** | 1 km grid, 24–72h ahead, quantile intervals (p10/p50/p90) | LightGBM + LUR + persistence blend |
| **Source Attribution** | Per-cell breakdown: vehicular, industrial, biomass, dust, secondary | SHAP over LUR + HYSPLIT trajectories + FIRMS/OSM overlay |
| **Enforcement Intelligence** | Ranked hotspot clusters with severity × population × source scores | Getis-Ord Gi* + DBSCAN + Groq LLM briefs |
| **Multi-City Compare** | Side-by-side Delhi vs Bengaluru with intervention effectiveness chart | Split MapLibre + real enforcement data |
| **Citizen Advisory** | 12 Indian languages × 4 severity × 3 vulnerability tiers | Pre-translated templates + TF-IDF RAG citations |
| **Multi-Agent AI** | Natural language queries routed to forecast/attribution/enforce/advisory tools | LangGraph + Groq LLama-3.3-70B |
| **Vulnerability Targeting** | Auto-selects advisory tier based on ward-level demographics | PostGIS population overlay |

---

## 🖥️ UI Features

- **Dark-themed MapLibre heatmap** — AQI grid with CPCB color bands
- **Click-to-attribute** — Cell → SHAP breakdown + HYSPLIT trajectory on map
- **City summary banner** — Live mean AQI, % cells in Poor+, top source
- **Severe alert system** — Pulsing banner when zones cross thresholds
- **24h sparkline chart** — Per-cell historical trend
- **AI Chat widget** — Ask "What's Delhi's AQI?" and get real data responses
- **Compare mode** — Dual maps + intervention effectiveness chart
- **12-language advisory** — Switch languages live in the UI

---

## 🚀 Quick Start

```bash
# 1. Clone and configure
git clone https://github.com/Nandani1512/VayuNetra.git
cd VayuNetra
cp .env.example .env
$EDITOR .env   # fill in API keys (OpenAQ, FIRMS, Groq)

# 2. Start infrastructure
make up        # PostGIS, Redis, MinIO, MLflow, Prefect via Docker

# 3. Ingest data
make ingest    # pulls OpenAQ + Open-Meteo + satellite data

# 4. Train models
make train-forecast city=delhi pollutant=pm25 horizon=24
make train-lur city=delhi pollutant=pm25

# 5. Launch
make serve     # http://127.0.0.1:8000
```

### Deterministic Demo (no network needed)

```bash
export DEMO_MODE=true && make demo
```

---

## 📁 Project Structure

```
src/vayunetra/
├── api/                  # FastAPI routers + observability
│   ├── routers/          # /forecast, /attribution, /enforce, /advisory, /agent/chat
│   └── schemas/          # Pydantic models
├── agents/               # LangGraph supervisor (router → tools → composer)
├── models/
│   ├── forecast/         # LightGBM trainer, baselines, CV
│   ├── lur/              # Land Use Regression downscaler
│   └── attribution/      # SHAP explainer, HYSPLIT trajectory, overlay
├── enforcement/          # Gi* hotspots, DBSCAN, ranker, LLM brief
├── advisory/             # Templates (12 langs), RAG retrieval
├── channels/             # IVR (TwiML) channel
├── ingestion/            # Prefect flows: OpenAQ, Open-Meteo, S5P, FIRMS, traffic
├── features/             # Feature engineering (forecast + LUR)
├── storage/              # PostGIS + SQLAlchemy models
├── serving/              # Tile cache (Redis + MinIO)
└── eval/                 # Evaluation harness

frontend/                 # Vanilla JS + MapLibre (no build step)
bots/telegram/            # Telegram bot with consent flow + daily broadcast
docs/                     # Architecture, deck, demo script
conf/                     # YAML configs (city, model, ingestion)
deploy/                   # Docker Compose, Dockerfile, Prometheus, Grafana
tests/                    # Unit + eval tests (21 passing)
```

---

## 📊 Evaluation Metrics

| Metric | Value |
|--------|-------|
| Forecast RMSE (PM2.5, 24h) | 47.2 µg/m³ |
| P10–P90 coverage | 75.3% |
| Attribution deviation vs SAFAR | ≤25 pp mean |
| Language coverage | 12/12 Indian languages |
| API response time (p95) | <3 seconds end-to-end |
| Forecast endpoint (p95) | 0.4s |
| Advisory endpoint (p95) | 0.02s |

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| ML | LightGBM, SHAP, scikit-learn |
| LLM | Groq (LLama-3.3-70B), Ollama fallback |
| Multi-Agent | LangGraph + LangChain |
| API | FastAPI, Pydantic, async SQLAlchemy |
| Database | PostGIS + TimescaleDB |
| Cache | Redis + MinIO (S3-compatible) |
| Orchestration | Prefect |
| MLOps | MLflow, Evidently (drift detection) |
| Frontend | MapLibre GL JS, vanilla JS, Canvas |
| Monitoring | Prometheus, Grafana, OpenTelemetry |
| Deployment | Docker Compose, Kubernetes-ready |

---

## 🌍 Supported Cities

| City | Stations | Grid Cells | Pollutants |
|------|----------|-----------|-----------|
| Delhi | 60 | 2,695 | PM2.5, PM10, NO₂ |
| Bengaluru | 36 | 1,260 | PM2.5, PM10, NO₂ |

---

## 📋 Makefile Targets

| Target | What it does |
|--------|-------------|
| `make up` | Start Docker stack + migrations |
| `make serve` | FastAPI + UI on :8000 |
| `make demo` | Deterministic demo (frozen data) |
| `make train-forecast` | Train LightGBM quantile model |
| `make train-lur` | Train LUR downscaler |
| `make eval` | Full evaluation harness → `reports/` |
| `make smoke` | Hit all endpoints, check p95 budgets |
| `make ingest` | Run all Prefect ingestion flows |
| `make test` | Run pytest (21 tests) |
| `make lint` | ruff + black + mypy |

---

## 📦 Deliverables

- ✅ Working prototype (live at localhost:8000)
- ✅ Architecture diagram ([`docs/architecture.md`](docs/architecture.md))
- ✅ Presentation deck ([`docs/deck/slides.md`](docs/deck/slides.md))
- ✅ Demo script ([`docs/demo_script.md`](docs/demo_script.md))

---

## 👥 Team

Built for ET AI Hackathon 2026.

---

## 📄 License

MIT
