# Railway Deployment Plan

Companion to [ADR-0001](./adr/ADR-0001-deployment-platform.md). This document translates the Railway decision into concrete environments, codebase changes, Railway-side setup, a sequenced rollout, and a post-deploy verification checklist.

## A. Environment Strategy

Three Railway environments, each with its own Postgres and Redis:

| Environment | Git branch | Deploy trigger | Intended use |
|---|---|---|---|
| `development` | feature branches | manual or PR preview | Integration testing, ad-hoc debugging |
| `staging` | `staging` | auto-deploy on push | Pre-prod soak, QA, frontend integration |
| `production` | `main` | auto-deploy on push | Customer traffic |

**Promotion flow**: PR into `staging` → auto-deploys to staging → soak / QA → merge `staging` into `main` → auto-deploys to production. Hotfix path: branch off `main`, PR back into `main` with a follow-up PR into `staging` to keep branches aligned.

Each environment is fully isolated — separate database, separate Redis, separate secrets, separate domain.

## B. Codebase Changes Required Before Production Deploy

Grouped by concern. Each item lists the file it touches.

### B.1 Security / configuration correctness

- **Fix auth regression** — `config/settings/production.py` lines 37–42 currently *replace* `DEFAULT_AUTHENTICATION_CLASSES` with `Token + Session`, dropping Firebase. Change to append rather than replace; preserve `FirebaseAuthentication` as the primary class. (The inline comment says "alongside Firebase" — the code contradicts it.)
- **`ALLOWED_HOSTS` hardening** — `config/settings/base.py:20` defaults to `localhost,127.0.0.1`, but `production.py:11` defaults to `*` if the env var is unset. Change production to *require* `ALLOWED_HOSTS` via `os.environ['ALLOWED_HOSTS']` (raise on missing) so a misconfigured deploy fails loudly instead of accepting any Host header.
- **`CSRF_TRUSTED_ORIGINS`** — populate with the actual Railway public domain and custom domain in each environment's env vars.
- **`SECRET_KEY`** — require explicitly in production (fail on the default `django-insecure-*` fallback).

### B.2 Observability

- **Sentry** — uncomment `sentry-sdk>=2.5.0` in `requirements/production.txt`. Wire in `config/settings/production.py` with Django + Celery integrations. Configuration reads `SENTRY_DSN` and `DJANGO_ENV` (used as the `environment` tag). `traces_sample_rate=0.1` as a starting point.
- **`/health/` endpoint** — add a new `apps/core/` app (or a small module and a single URL include) exposing `GET /health/` that returns 200 JSON with `{status, db, redis, version}` on success and 503 with the failing component name on partial failure. Keep the handler synchronous, non-authenticated, and cheap (single `SELECT 1`, single Redis `PING`). Add a unit test for the healthy path.
- **Version stamping** — `version` in the health payload reads a `GIT_SHA` env var injected at build time by Railway's build args or CI. If unset, fall back to `"unknown"`.

### B.3 Secrets

- **Firebase credentials** — standardize on `FIREBASE_CREDENTIALS_JSON` (the env-var mode already supported in `apps/accounts/authentication.py:23`). Retire `FIREBASE_CREDENTIALS_PATH` for production; file-based credentials don't fit Railway's ephemeral filesystem.
- **`.env.example`** — refresh with the full production env var set: `DJANGO_ENV`, `SENTRY_DSN`, `FIREBASE_CREDENTIALS_JSON`, a realistic Railway domain placeholder, and concrete `CORS_ALLOWED_ORIGINS` / `CSRF_TRUSTED_ORIGINS` examples.

### B.4 Static & media files

- **Static**: Whitenoise is already wired in `production.py` (`CompressedManifestStaticFilesStorage`). No CDN at launch. Revisit if DRF Spectacular UI assets become a bandwidth concern.
- **Media**: no `ImageField` / `FileField` / upload handlers anywhere under `apps/` today, so object storage is not needed for v1. If the first customer needs avatar upload or similar, revisit — Railway volumes work for single-instance scale, but S3-compatible storage is the right long-term target.

### B.5 Database migrations

`railway.toml` currently embeds `python manage.py migrate` in the web service's start command. This is fine for single-replica deploys but creates two problems at multi-replica scale:

- Every replica races to run migrations on boot.
- A failed migration crashes the service loop instead of being caught by CI.

**Recommendation**: move migrations to a CI step that runs before Railway is told to deploy. GitHub Actions → `railway run python manage.py migrate --noinput` against the target environment, succeed or fail the workflow on that → `railway up` only on success. Start command becomes `gunicorn …` only, with no migration call.

Trade-off: CI now needs `RAILWAY_TOKEN` and environment selection logic. The alternative (a Railway "release" job that gates the rollout) is cleaner on paper but Railway's release-phase support is less mature than the CI-driven approach.

### B.6 CI/CD

- GitHub Actions workflow with two jobs:
  - `test`: lint + `pytest` on pull requests and pushes.
  - `deploy`: on pushes to `staging` or `main`, run migrations against the matching Railway environment, then `railway up`.
- Secrets required in GitHub: `RAILWAY_TOKEN`.
- Railway's own auto-deploy can remain enabled as a fallback, but the canonical deploy path goes through CI so migrations are gated.

## C. Railway-Side Setup

### C.1 Services per environment

| Service | Source | Start command | Public? |
|---|---|---|---|
| `web` | `Dockerfile.production` | `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 2 --timeout 120` | Yes |
| `worker` | `Dockerfile.production` | `celery -A config worker -l info --concurrency=2` | No |
| `postgres` | Railway plugin | — | No (private network only) |
| `redis` | Railway plugin | — | No (private network only) |

### C.2 Environment variables

Shared at the project level (referenced by both `web` and `worker`):

- `DATABASE_URL` — from the Postgres plugin.
- `REDIS_URL` — from the Redis plugin.
- `SECRET_KEY` — long random string per environment.
- `DJANGO_SETTINGS_MODULE=config.settings.production`
- `DJANGO_ENV` — `development` / `staging` / `production`.
- `FIREBASE_CREDENTIALS_JSON` — full service account JSON, single line.
- `AI_SCHEDULE_PROVIDER=apps.ai_engine.providers.ortools_provider.ORToolsProvider`
- `SENTRY_DSN` — per-environment DSN.

Web-only:

- `ALLOWED_HOSTS` — Railway-provided public domain + custom domain.
- `CSRF_TRUSTED_ORIGINS` — same, with scheme.
- `CORS_ALLOWED_ORIGINS` — frontend origin(s).
- `SECURE_SSL_REDIRECT=1`

### C.3 Resource ceilings

Starting ceilings, to be tuned after observed load:

- `web`: 512 MB memory, 1 vCPU. Two gunicorn workers × two threads fit comfortably.
- `worker`: 1 GB memory, 1 vCPU. OR-Tools CP-SAT is CPU-bound and memory-light per job, but concurrency 2 plus solver overhead warrants the higher ceiling.
- `postgres`: start on the smallest tier that supports the expected pgvector usage; revisit when RAG ships.
- `redis`: smallest tier.

### C.4 Custom domain

Point `api.<domain>.tw` (or chosen hostname) at the `web` service via CNAME. TLS is auto-provisioned by Railway. Add the custom domain to `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` for production.

## D. Order of Operations

1. **Codebase fixes (§B.1 – §B.6)** merged to `staging`; CI green.
2. **Staging environment** provisioned in Railway (services, plugins, env vars). Smoke-deploy from `staging` branch.
3. **Verify staging** with the checklist in §E.
4. **Production environment** provisioned. Env vars set, Firebase credentials populated, Sentry project configured.
5. **Custom domain** attached to production `web` service. Update `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS`.
6. **First production deploy** by merging `staging` into `main`.
7. **Post-deploy verification (§E)** against production.
8. **Backups and alerts**: confirm Railway's managed Postgres backups are enabled; wire project-level budget alert.

## E. Post-Deploy Verification Checklist

Run this list after every production deploy. It is short by design — automate whatever becomes repetitive.

- `GET /health/` returns 200; `db` and `redis` both `ok`; `version` matches the deployed commit SHA.
- `GET /api/docs/` loads the Swagger UI.
- Frontend can complete a Firebase login round trip (token issued → `/api/auth/me` returns the user).
- Submit a synthetic schedule generation request (`/api/ai/...`); Celery worker picks it up and completes.
- Trigger a synthetic error (e.g., a feature-flagged exception path); Sentry receives it with the correct `environment` tag.
- Postgres active connections and Redis memory usage are within expected bounds on Railway's dashboard.
- HSTS header present; HTTP → HTTPS redirect works externally.
- `/admin/` login works over HTTPS; session cookies are `Secure`.
- Worker logs show Celery heartbeat; no unhandled exceptions in the last 15 minutes.
- Budget alert visible and active on the Railway project.
