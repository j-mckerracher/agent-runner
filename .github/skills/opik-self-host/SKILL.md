---
name: opik-self-host
version: 1.0.0
description: >
  Complete guide for Opik self-hosting and on-premise deployment.
  Use this skill when setting up, configuring, scaling, or troubleshooting self-hosted Opik instances.
  Keywords: Opik, self-host, self-hosting, local deployment, Kubernetes, Helm chart, architecture,
  scaling, backup, configuration, anonymous usage statistics, LLM model registry, dataset versioning migration.
---

## When to Use

Activate this skill when:

- Deploying Opik locally for development or proof-of-concept using Docker Compose
- Setting up Opik on a Kubernetes cluster for production workloads
- Installing or upgrading using Helm charts
- Configuring external access, storage (S3), databases, or ingress
- Scaling Opik for high-volume deployments (millions of traces/day)
- Migrating datasets or versions between Opik releases
- Troubleshooting self-hosted Opik issues or performance
- Understanding Opik's multi-service architecture (Java, Python, ClickHouse, MySQL, Redis)
- Enabling or disabling anonymous usage statistics
- Setting up LLM model registry or dataset versioning

---

## Deployment Options

### Local Deployment (Development)

Use Docker Compose for quick local setup — **not production-ready**.

**Quick start:**
```bash
git clone https://github.com/comet-ml/opik.git
cd opik
./opik.sh  # or ./opik.ps1 on Windows
```

Opik runs at `http://localhost:5173`. Data persists in `~/opik`.

**Configure Python SDK:**
```bash
opik configure --use_local
# or in Python:
import opik
opik.configure(use_local=True)
```

**Docs:** [Local Deployment Guide](https://www.comet.com/docs/opik/self-host/local_deployment)

### Kubernetes Deployment (Production)

Use Helm chart for production-grade, scalable deployments.

**Prerequisites:** Helm, kubectl, (optional: kubectx, kubens)

**Install:**
```bash
helm repo add opik https://comet-ml.github.io/opik/
helm repo update
VERSION=latest
helm upgrade --install opik -n opik --create-namespace opik/opik \
    --set component.backend.image.tag=$VERSION \
    --set component.python-backend.image.tag=$VERSION \
    --set component.python-backend.env.PYTHON_CODE_EXECUTOR_IMAGE_TAG="$VERSION" \
    --set component.frontend.image.tag=$VERSION
```

**Port-forward:** `kubectl port-forward -n opik svc/opik-frontend 5173`

**Docs:** [Kubernetes Guide](https://www.comet.com/docs/opik/self-host/kubernetes)

### Helm Chart

Production-ready Helm deployment with advanced configuration options.

- Ingress for external access
- ClickHouse replication and backup
- External S3 storage (AWS/MinIO)
- IAM role support
- Custom ClickHouse users and profiles
- Bitnami migration (chart v1.9.39+)

**Docs:** [Helm Chart Details](https://www.comet.com/docs/opik/self-host/helm)

---

## Architecture Overview

Opik comprises multiple stateless and stateful services:

| Component | Role |
|-----------|------|
| **Java Backend** | REST API, auth, business logic (Dropwizard, Java 21) |
| **Python Backend** | Evaluator execution, optimization workflows (Flask, Gunicorn) |
| **Frontend** | React SPA with Nginx reverse proxy (TypeScript, Vite) |
| **ClickHouse** | Analytical data: traces, spans, experiments (+ Zookeeper) |
| **MySQL** | Transactional data: projects, datasets, prompts |
| **Redis** | Caching, rate limiting, job queues, streams |
| **MinIO/S3** | Object storage: artifacts, file uploads |

**Observability:** OpenTelemetry vendor-neutral instrumentation.

**Docs:** [Platform Architecture](https://www.comet.com/docs/opik/self-host/architecture)

---

## Common Tasks

| Task | Documentation |
|------|----------------|
| Quick local start | [Local Deployment](https://www.comet.com/docs/opik/self-host/local_deployment) |
| Production Kubernetes | [Kubernetes Deployment](https://www.comet.com/docs/opik/self-host/kubernetes) |
| Helm configuration | [Helm Charts](https://www.comet.com/docs/opik/self-host/helm) |
| Scale for high volume | [Scaling Guide](https://www.comet.com/docs/opik/self-host/scaling) |
| Backup ClickHouse | [Backup & Recovery](https://www.comet.com/docs/opik/self-host/backup) |
| Configure external access | [Kubernetes - Advanced](https://www.comet.com/docs/opik/self-host/kubernetes) |
| Disable telemetry | [Anonymous Usage Statistics](https://www.comet.com/docs/opik/self-host/configure/anonymous_usage_statistics) |
| Migrate dataset versions | [Dataset Versioning Migration](https://www.comet.com/docs/opik/self-host/configure/dataset_versioning_migration) |
| Troubleshoot issues | [Troubleshooting Guide](https://www.comet.com/docs/opik/self-host/troubleshooting) |

---

## Code Snippet: Local Docker Compose Start

Stop/start Opik safely without data loss:

```bash
# Stop Opik (preserves data in ~/opik)
./opik.sh --stop

# Restart or upgrade (data persists via mounted volumes)
cd opik
./opik.sh
```

---

## Scaling Reference

**Production workload example** (reference deployment):
- **Users:** ~600 daily active
- **Traces/day:** 4–6 million
- **Weekly ingestion:** ~100 GB
- **Stored data:** 5 TB (40M traces, 250M spans)

**Infrastructure:**
- Backend: 10 pods (3 CPU, 5 GB each)
- Python Backend: 12 pods (1.5 CPU, 2 GB each)
- Frontend: 3 pods (minimal resources)
- ClickHouse: 2 replicas (30 CPU, 230 GB each, 15 TB SSD)

**Key settings:**
- Async ClickHouse inserts: enabled
- Rate limit: 10K events/60s per client
- Max query time: 60s
- Max memory/query: 10 GB

---

## Configuration Highlights

- **Anonymous stats:** Opt-out via `OPIK_USAGE_REPORT_ENABLED=false`
- **Dataset versioning:** Auto-migrates on v1.9.92+, takes 2–10 min
- **External storage:** S3 buckets, IAM roles, MinIO support
- **Database replication:** ClickHouse cluster with Zookeeper
- **Ingress & TLS:** Fully configurable in Helm values
- **LLM proxy:** OpenAI, Anthropic, Google Gemini support

---

## Reference

- [Opik Self-Host Overview](https://www.comet.com/docs/opik/self-host/overview)
- [Opik GitHub Repository](https://github.com/comet-ml/opik)
- [Opik API Reference](https://www.comet.com/docs/opik/reference/rest-api/overview)
- [Opik Changelog](https://www.comet.com/docs/opik/changelog)
- [Helm Chart Docs](https://comet-ml.github.io/opik/)
