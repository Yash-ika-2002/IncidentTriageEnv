---
title: IncidentTriageEnv
emoji: 🚨
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
tags:
  - openenv
---

# IncidentTriageEnv

An [OpenEnv](https://github.com/openenv/openenv) environment for **production API incident triage**. An AI agent receives real-time telemetry (error rates, latency, log snippets) and must diagnose, classify, escalate, and resolve production incidents. Simulates real on-call SRE/DevOps workflows.

---

## Overview

| Field | Value |
|---|---|
| Domain | DevOps / SRE / Incident Management |
| Tasks | 3 (easy → medium → hard) |
| Reward range | [0.0, 1.0] |
| Action space | 4 typed actions |
| Observation fields | 8 fields |
| Max episode steps | 10 / 15 / 20 |

---

## Observation Space

```
incident_id         str     Unique incident identifier (e.g. INC-2024-0042)
timestamp           str     ISO-8601 datetime of first detection
service_name        str     Primary service reporting anomalies
error_rate          float   Fraction of requests returning 5xx (0.0–1.0)
p99_latency_ms      int     99th-percentile latency in milliseconds
log_snippet         str     Recent relevant log lines from the service
affected_endpoints  list    API endpoints currently returning errors
step_count          int     Steps taken so far in this episode
```

---

## Action Space

```jsonc
{"kind": "diagnose", "service": "<name>", "root_cause": "<enum>"}
// root_cause: database_overload|memory_leak|network_partition|
//             dependency_timeout|misconfiguration|traffic_spike|cert_expiry|unknown

{"kind": "set_severity", "level": "P1|P2|P3|P4"}

{"kind": "escalate", "team": "backend|frontend|infrastructure|database|security|platform|networking"}

{"kind": "resolve", "message": "<>=20 char resolution summary>"}
```

---

## Tasks

### Task 1 — Single-Service 5xx Spike (Easy)
Payment service returning 72% error rate due to database connection pool exhaustion. Logs clearly point to one root cause. Expected score: 0.7–1.0.

### Task 2 — Cascading Failure auth→api→db (Medium)
Config change on auth-service switched session backend from Redis to unindexed Postgres, causing cascading 503s. Agent must trace the dependency chain. Expected score: 0.4–0.8.

### Task 3 — Ambiguous Multi-Signal Incident (Hard)
Three services show simultaneous anomalies. Two (recommendation, search) are victims of a Redis cluster network partition. notification-service is a red herring. Expected score: 0.2–0.65.

---

## Reward Function

| Component | Weight |
|---|---|
| Correct service identified | +0.35 |
| Correct severity | +0.25 |
| Correct team escalated | +0.25 |
| Resolution message quality | +0.15 |
| Step penalty (beyond step 10) | −0.10/step |
| Repeat penalty | −0.05/repeat |

---

## Setup

### Local

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 7860

# In another terminal
export API_BASE_URL=https://api.openai.com/v1
export MODEL_NAME=gpt-4o-mini
export HF_TOKEN=sk-...
python inference.py
```

### Docker

```bash
docker build -t incident-triage-env .
docker run -p 7860:7860 incident-triage-env
curl http://localhost:7860/health
```

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/reset` | Start new episode: `{"task_id": "task_easy|task_medium|task_hard"}` |
| POST | `/step` | Take an action |
| GET | `/state` | Full internal state snapshot |
| GET | `/health` | Health check |

---

## Baseline Scores

Measured with `gpt-4o-mini` (temperature=0.2):

| Task | Score |
|---|---|
| task_easy | 0.85 |
| task_medium | 0.60 |
| task_hard | 0.35 |
| **mean** | **0.60** |
