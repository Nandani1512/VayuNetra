---
marp: true
theme: default
paginate: true
title: VayuNetra — Air Intelligence Eye
---

# VayuNetra — Air Intelligence Eye

**Geospatial air-quality intelligence for Indian cities**

Forecasts AQI • Attributes sources • Ranks hotspots • Multilingual advisories

---

## The Problem

- **1.67 M deaths/year** in India attributed to air pollution (Lancet 2020)
- Regulators lack hyper-local, actionable intelligence
- Citizens receive generic "poor AQI" alerts with no source context
- Existing systems: sparse monitors, no forecasting, no enforcement ranking

---

## Our Solution

An end-to-end platform that turns **free, public data** into:

1. **72-hour forecasts** at 1 km resolution
2. **Source attribution** (traffic, industry, biomass, dust, secondary)
3. **Enforcement hotspot ranking** with AI-generated briefs
4. **Citizen advisories** in 12 Indian languages

---

## Architecture

![Architecture](../architecture.png)

Data Sources → Prefect Ingestion → PostGIS/TimescaleDB → ML Pipeline → FastAPI → Channels

---

## Capability: Forecast

| Metric | Value |
|--------|-------|
| Horizon | 1–72 hours |
| Resolution | 1 km (3 955 grid cells) |
| RMSE | 47.2 µg/m³ |
| P10–P90 coverage | 75.3% |
| Lift vs persistence | >1.0 (eval-gated) |

Model: LightGBM + LUR downscaler, retrained weekly.

---

## Capability: Source Attribution

- SHAP-based decomposition into 5 source categories
- Validated against SAFAR benchmark: **≤25 pp mean deviation**
- Powers enforcement prioritization and citizen explanations

---

## Capability: Enforcement Intelligence

- **Getis-Ord Gi*** spatial hotspot detection
- **DBSCAN** temporal clustering of violations
- **LLM-generated briefs** for inspectors (location, likely source, urgency)
- Precision\@10 evaluated per city

---

## Capability: Citizen Advisory

- RAG-backed health recommendations
- **12 languages**: Hindi, English, Kannada, Tamil, Telugu, Marathi, Bengali, Gujarati, Punjabi, Odia, Malayalam, Assamese
- Channels: Web UI, Telegram bot, IVR
- chrF evaluated for translation quality

---

## Demo

1. **Map view** — choropleth of real-time + forecast AQI across Delhi
2. **Station drill-down** — 72 h forecast chart with confidence bands
3. **Attribution panel** — pie chart of source contributions
4. **Enforcement tab** — ranked hotspot list with AI briefs
5. **Bot interaction** — Telegram advisory in Hindi

---

## Evaluation Results

| Dimension | Metric | Target | Achieved |
|-----------|--------|--------|----------|
| Forecast | RMSE | <55 µg/m³ | **47.2** |
| Forecast | P10-P90 coverage | >70% | **75.3%** |
| Attribution | Category deviation | ≤25 pp | **✓** |
| Advisory | Languages | 12 | **12** |
| Latency | API p95 | <2 s | **<2 s** |

---

## Tech Stack

| Layer | Choice |
|-------|--------|
| Orchestration | Prefect 2 |
| Database | PostgreSQL + PostGIS + TimescaleDB |
| ML | LightGBM, scikit-learn, SHAP |
| Serving | FastAPI, LangGraph |
| UI | MapLibre GL JS |
| Observability | Prometheus, Grafana, OpenTelemetry |
| Infra | Docker Compose, DVC, MinIO |

---

## Team & Credits

**VayuNetra** — built during ET Hackathon 2026

Data: OpenAQ, Open-Meteo, Copernicus (Sentinel-5P, CAMS), NASA (MODIS, FIRMS), OSM, WorldPop

*All data sources are free and publicly available.*

---
