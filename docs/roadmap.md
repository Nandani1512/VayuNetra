# VayuNetra — Post-Hackathon Production Roadmap (Phase 12)

This document is the bridge from a 4-day hackathon prototype to a production
service a municipal corporation or pollution-control board could operate. It is
deliberately **not coded** — it scopes the work, sequences it, and states the
acceptance bar for each milestone.

Guiding principle carried over from the build: **every claim must be
reproducible and every number must cite its source.** Production raises the bar
from "demoable" to "defensible in court and in a clinic."

---

## 0 · Where we are (end of hackathon)

| Capability | Hackathon state | Production gap |
|---|---|---|
| Forecast | LightGBM quantile, beats persistence (CI-gated) | spatial coherence, longer horizons, calibration |
| Attribution | SHAP + back-trajectory + overlays | true receptor modelling (PMF) needs speciated data |
| Enforcement | Gi\* hotspots + LLM brief + audit log | legal chain-of-custody, signed briefs |
| Advisory | 12-lang templates + TF-IDF RAG | pgvector RAG, IVR, WhatsApp Business |
| Cities | Delhi + Bengaluru (config-only) | 900+ CAAQMS onboarding pipeline |
| Infra | docker-compose + k8s manifest (not deployed) | managed k8s, IaC, HA, backups |
| MLOps | MLflow + Evidently + Prometheus/Grafana | auto-retrain loop, model governance |

---

## 1 · Model upgrades (Q1)

**Goal:** measurable accuracy and coherence gains over the LightGBM baseline,
proven on the same `make eval` harness so the comparison is apples-to-apples.

- **Temporal:** Temporal Fusion Transformer (PyTorch Forecasting) for multi-horizon
  quantiles with covariate attention. Keep LightGBM as the always-on fallback.
- **Spatial:** Graph WaveNet / diffusion-convolutional GNN over the station graph
  for spatially-coherent fields (kills the "checkerboard" artefact of per-cell LUR).
- **Calibration:** conformal prediction wrappers so p10/p90 have *guaranteed*
  empirical coverage; track coverage as a first-class eval metric.

**Acceptance:** TFT/GNN beats the LightGBM stack by ≥10 % RMSE at 48–72 h on
held-out cities, with p10–p90 coverage within ±3 pp of nominal, *and* inference
stays within the §6.2 latency budgets. If it doesn't beat the baseline, it ships
behind a flag, not on the critical path. (Same discipline as the Phase-2 gate.)

---

## 2 · True source apportionment (Q1–Q2)

**Goal:** replace SHAP-overlay heuristics with receptor modelling that a
regulator will accept as evidence.

- Procure **speciated PM2.5** (elemental/organic carbon, ions, trace metals) from
  CPCB or via partnership with IIT-Kanpur / TERI.
- Run **Positive Matrix Factorization (PMF, EPA PMF 5.0)** and chemical mass
  balance; cross-validate factors against the SHAP-overlay output.
- Keep SHAP-overlay as the **real-time** estimator; PMF becomes the **periodic
  ground-truth** that recalibrates the overlay weights.

**Acceptance:** PMF factor profiles published; overlay weights recalibrated so
real-time attribution sits within ≤15 pp (tightened from the hackathon's 25 pp)
of the PMF reference on the validation quarter.

---

## 3 · Multi-city scale (Q2)

**Goal:** from 2 cities to the national CAAQMS network without per-city code.

- **Onboarding pipeline:** a single `onboard_city` flow that, given a bbox and a
  language, builds the grid, pulls static layers, backfills history and registers
  Hydra config — target **< 1 day per city, zero code**.
- **Sharding:** per-city compute isolation; tile pre-computation jobs fan out on
  the Prefect work pool; Redis tile cache per city.
- **Storage:** Timescale continuous aggregates + compression policies; partition
  `observation`/`forecast` by city and time.

**Acceptance:** 10 cities live with identical UI/API; p95 latency budgets hold;
adding city #11 requires only a config PR.

---

## 4 · Auto-retrain & model governance (Q2–Q3)

**Goal:** close the loop so the model maintains itself, with humans in the loop
only for promotion.

- **Trigger:** Evidently drift score crosses threshold → Prefect deployment fires
  a retrain → MLflow logs the candidate.
- **Gate:** the candidate must pass the persistence gate *and* beat the current
  `prod` model on the rolling eval window before it is eligible.
- **Deploy:** MLflow registry stage transition → **blue/green** model serving with
  automatic rollback on SLO regression.
- **Governance:** model cards per release; data lineage via DVC; immutable audit
  of which model produced which enforcement brief.

**Acceptance:** a full drift → retrain → shadow → promote → rollback cycle runs
end-to-end in staging with no manual code changes; mean time-to-refresh < 24 h.

---

## 5 · Government & citizen integration (Q3)

**Goal:** make the outputs usable inside real institutional workflows.

- **CPCB API:** formalize data access (SLA, rate limits) instead of best-effort scraping.
- **RBAC:** municipal roles (inspector, ward officer, health officer) on top of the
  existing JWT `inspector` scope; SSO via the department IdP.
- **Signed enforcement briefs:** PDF with digital signature + chain-of-custody hash,
  so a brief is admissible and tamper-evident.
- **Citizen reach:** WhatsApp Business API, **IVR for feature phones** (huge in rural
  India), and accessibility (screen-reader-compliant advisories).
- **pgvector RAG:** replace the in-process TF-IDF corpus with chunked WHO/CPCB/IIT-K
  PDFs in pgvector + BGE embeddings; keep citations mandatory.

**Acceptance:** one department issues a real enforcement action off a signed brief;
citizen advisories reach ≥3 channels including IVR.

---

## 6 · Reliability, security & cost (continuous)

- **Infra-as-code:** Terraform-managed managed-k8s (OCI Always-Free tier or a paid
  tier); HA Postgres with PITR backups; MinIO → managed object store.
- **Security:** secrets in a vault (not `.env`), per-service least-privilege,
  dependency scanning, signed container images, pen-test before pilot.
- **Cost guardrails:** per-source API budget dashboards; alert before quota burn;
  spot/preemptible compute for batch tiling.
- **SRE:** error-budget policy tied to the existing Prometheus SLO alerts;
  on-call runbook derived from `docs/demo_script.md` recovery cheatsheet.

**Acceptance:** documented RPO/RTO, a green DR drill, and a security sign-off.

---

## 7 · Pilot & impact (Q3–Q4)

**Goal:** prove real-world value, not just metrics.

- Partner with **one municipal corporation** for a **90-day pilot** on 1–2 wards.
- Define impact KPIs up front: enforcement actions taken, advisory reach, and
  (where measurable) exposure reduction in targeted hotspots.
- Publish a **case study** with the eval methodology and honest limitations.

**Acceptance:** a signed pilot MoU, a public case study, and a go/no-go decision
backed by the pilot's measured KPIs.

---

## Sequencing summary

```
Q1: Model upgrades (1) ───┐
    PMF data + modelling (2)─┼──► Q2: Multi-city scale (3)
                             └──►      Auto-retrain (4) ──► Q3: Gov/citizen integration (5)
Reliability/security/cost (6) runs continuously ─────────► Q3–Q4: Pilot & case study (7)
```

Each milestone reuses the existing `make eval` harness and observability stack as
its acceptance instrument — so "done" always means *measured*, never *claimed*.
