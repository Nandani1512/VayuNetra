# VayuNetra — Demo Script (5 minutes)

## Prerequisites

```bash
cp .env.example .env   # fill API keys (or use DEMO_MODE)
```

## 1. Start the Stack

```bash
# Live mode (requires network + API keys)
make up && make serve

# OR deterministic demo (no network needed)
export DEMO_MODE=true && make demo
```

UI available at **http://127.0.0.1:8000**

---

## 2. API Endpoint Walkthrough

### Health check
```bash
curl http://127.0.0.1:8000/health
# → {"status": "ok", "version": "1.0.0"}
```

### Forecast (72 h, single station)
```bash
curl "http://127.0.0.1:8000/api/v1/forecast?station_id=delhi_ito&hours=72"
```
Returns hourly PM2.5 predictions with confidence intervals.

### Attribution
```bash
curl "http://127.0.0.1:8000/api/v1/attribution?lat=28.63&lon=77.22"
```
Returns source-category percentages (traffic, industry, biomass, dust, secondary).

### Enforcement hotspots
```bash
curl "http://127.0.0.1:8000/api/v1/enforcement/hotspots?city=delhi&top_k=10"
```
Returns ranked hotspots with Gi* z-scores and AI-generated briefs.

### Citizen advisory
```bash
curl "http://127.0.0.1:8000/api/v1/advisory?lat=28.63&lon=77.22&lang=hi"
```
Returns health advisory in Hindi with RAG citations.

---

## 3. UI Demo Flow

1. **Open map** — Shows Delhi with AQI choropleth (1 km cells, 3 955 total)
2. **Click a cell** — Side panel shows 72 h forecast chart with P10/P90 bands
3. **Toggle "Attribution"** — Pie chart overlay showing source breakdown
4. **Switch to "Enforcement" tab** — Ranked list of hotspots; click one for AI brief
5. **Open advisory panel** — Select language (12 available), get personalized advice

---

## 4. Key Talking Points

| Point | Detail |
|-------|--------|
| **Free data only** | OpenAQ, Copernicus, NASA — no paid APIs |
| **Forecast accuracy** | RMSE 47.2 µg/m³, beats persistence baseline |
| **Hyper-local** | 1 km resolution via LUR downscaling |
| **Actionable** | Enforcement briefs with source + location + urgency |
| **Inclusive** | 12 Indian languages, Telegram + IVR for low-connectivity |
| **Eval-gated** | Every model must beat persistence or it doesn't deploy |
| **Observable** | Prometheus metrics, Grafana dashboards, drift detection |

---

## 5. Fallback: Deterministic Demo

If anything fails during live demo:

```bash
export DEMO_MODE=true && make demo
```

This serves frozen snapshots from `reports/demo/` — all endpoints return pre-computed results. The eval harness (`make eval`) also degrades gracefully: live → frozen fixtures → synthetic benchmark.

---

## Timing Guide

| Segment | Duration |
|---------|----------|
| Problem + architecture | 1 min |
| Live API calls | 1.5 min |
| UI walkthrough | 1.5 min |
| Metrics + Q&A | 1 min |
