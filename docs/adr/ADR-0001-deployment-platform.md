# ADR-0001: Deployment Platform

**Status:** Proposed
**Date:** 2026-04-19
**Deciders:** Backend tech lead

## Context

`scheduling-api` is a Django 5 + DRF backend for an AI scheduling system targeting medical and long-term care institutions in Taiwan. The runtime is a two-process application:

- A gunicorn web tier serving the REST API (`Dockerfile.production`, 2 workers × 2 threads).
- A Celery worker that runs OR-Tools CP-SAT schedule generation via `generate_schedule_task`.

Persistent state lives in managed PostgreSQL; Celery uses Redis as both broker and result backend. Authentication is Firebase (ID token verification; supports both file-path and JSON-env-var credential modes per `apps/accounts/authentication.py`). The application is localized to `zh-Hant` with `TIME_ZONE = "Asia/Taipei"`.

The project is under active development and targeting production deployment. `Dockerfile.production`, `railway.toml`, and `.railway.json` are already in-tree and working against Railway for development and staging use.

Expected production load at initial launch is low-RPS with burst-heavy, CPU-bound schedule-generation jobs (seconds to minutes per job, run asynchronously on the Celery worker). Sync endpoints — compliance checks, CRUD on employees/shifts/schedules — dominate latency sensitivity.

The roadmap includes a labor-law RAG feature using `pgvector` and LangChain. Neither dependency is installed yet.

## Decision

**Use Railway as the production platform.**

Rationale:

- **Native multi-process support.** Railway models independent services that share environment variables and private networking. The web and Celery worker run as two services from the same `Dockerfile.production` with different start commands — no adapter layer, no Jobs-vs-Services redesign.
- **Dockerfile-based deploy is already working.** `Dockerfile.production` + `railway.toml` are committed and have been validated in dev/staging. Migrating to another platform means rebuilding build/deploy config that currently works.
- **Bundled managed Postgres and Redis.** One provider, one bill, one dashboard, one networking surface. Both services are accessed over Railway's private `*.railway.internal` network rather than the public internet.
- **Team familiarity.** Operational knowledge — memory ceilings, log viewing, deploy lifecycle — is Railway-specific and already internalized.
- **Fits the stack without rearchitecture.** No change to Celery, no change to migrations strategy, no FaaS cold-start assumptions to design around.

## Alternatives Considered

**Fly.io.** Strong APAC region coverage (Singapore, Tokyo) would reduce Taipei latency, and Fly's `[processes]` model handles web + worker cleanly. Not chosen: Fly Postgres is user-managed rather than fully managed, and the team has no operational history with Fly; the switch cost is not justified by the latency win at current scale.

**GCP Cloud Run + Cloud SQL (`asia-east1`).** Best raw latency to Taiwan customers (in-region) and a mature `pgvector` story on Cloud SQL. Not chosen: Cloud Run's request-scoped model is a poor fit for a long-running Celery worker — it forces either a Cloud Run Jobs redesign or a side GCE VM, both of which increase operational surface. Billing model is also harder to cap predictably than Railway's.

**Render.** Clean background-worker primitive and fixed-tier pricing. Not chosen: no compelling operational or architectural advantage over Railway for our stack, and the same migration cost applies.

## Railway Architecture

- **Services**: `web` (gunicorn), `worker` (Celery), managed Postgres, managed Redis.
- **Networking**: `web` is publicly exposed at a custom domain; `worker`, Postgres, and Redis are reached via Railway's private network. Database and Redis URLs are never public.
- **Environment variables**: Core secrets and connection strings (`DATABASE_URL`, `REDIS_URL`, `SECRET_KEY`, `FIREBASE_CREDENTIALS_JSON`, `SENTRY_DSN`, etc.) are defined on the project and referenced per service. Service-specific overrides (gunicorn worker count, Celery concurrency) live on the individual service.
- **Secret management**: All secrets in Railway environment variables; nothing committed. Firebase credentials use the `FIREBASE_CREDENTIALS_JSON` mode (already supported in `apps/accounts/authentication.py`) rather than a file path, since Railway has no durable filesystem.
- **Build / start**: Both services build from `Dockerfile.production`. `web` runs gunicorn against `config.wsgi:application`; `worker` runs `celery -A config worker -l info`.

## Known Railway Constraints and Mitigations

- **Region.** Railway currently runs out of US-West. Taipei ↔ US-West RTT has a ~150 ms floor. For the predominantly REST-style API and async schedule jobs this is acceptable; the assumption is validated by post-launch telemetry. Revisit if a customer contract or observed UX regression forces in-region deployment.
- **pgvector availability.** Railway's managed Postgres supports extensions, but `pgvector` specifically must be verified by running `CREATE EXTENSION vector;` against a provisioned instance before the RAG feature is committed to this platform. Tracked in §Open Questions.
- **Cost model.** Railway is usage-based rather than fixed-tier. Mitigations: per-service memory and CPU ceilings; project-level budget alerts; monitoring of worker job durations (a regression that makes OR-Tools runs longer is the most likely cost spike vector).
- **Cold start / scaling.** Railway keeps services warm — no FaaS-style cold start. The Celery worker is always-on, so job pickup latency is bounded by queue drain, not boot time. Horizontal scale requires explicit replica configuration; the migration strategy in the deployment plan addresses multi-replica concerns.
- **Vendor lock on data.** Migrating off Railway later means a real Postgres + Redis data move, not just a redeploy. Accepted as the cost of bundled managed services.

## Consequences

- **Easier**: single-vendor ops surface; fast iteration loop; existing `Dockerfile.production` and `railway.toml` carry forward; no rearchitecture; no new CI primitives to learn.
- **Harder**: Taipei latency has a floor we cannot tune past; migrating off Railway is a real data move, not a redeploy; the `pgvector` story depends on vendor extension support we haven't yet verified.
- **Required follow-up**: clean separation of `development`, `staging`, and `production` environments in Railway; secrets discipline (no file-based credentials); observability (Sentry, `/health/`, log aggregation) in place before first customer traffic; concrete per-service memory ceilings and a project-level budget alert.

## Open Questions / Revisit Triggers

Revisit this decision if any of the following become true:

- A customer contract or Taiwanese regulation requires in-region data residency.
- `CREATE EXTENSION vector;` is unsupported or performs poorly on Railway's managed Postgres when the RAG feature ships.
- Sustained monthly cost trajectory deviates materially from traffic growth despite memory and CPU ceilings.
- An SLA commitment requires multi-region deployment or an APAC region.
- Schedule-generation CPU or memory needs exceed what Railway services can be provisioned for.

## Action Items

Captured in `docs/deployment-plan.md`.
