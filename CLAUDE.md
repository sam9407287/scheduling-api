# CLAUDE.md

This file orients Claude Code (claude.ai/code) sessions to this repository.
Read this first; for deeper detail follow the links in §11.

## Project at a glance

AI scheduling backend for Taiwan healthcare / long-term-care facilities.
Django 5 + DRF + Google OR-Tools (CP-SAT) + Celery + Postgres + Redis,
deployed on Railway. Two phases of new feature work landed in May 2026
(commits `99ce0ca` → `ecbf089`) — see §10 below for what each PR added.

Three frontend buttons fan into one solver: full AI generate, AI gap-fill,
and derive-legal (A from B). All three are metered (Phase 2). Phase 1 added
the dual-schedule (legal A vs actual B) model, per-cell compliance
violations, the drift objective for derive-legal, and the team-constraint
compiler. Phase 2 added the REST CRUD around consent and team constraints,
the metered billing primitives, and shift-pattern preferences.

## Commands

```bash
# Install dependencies (local dev)
pip install -r requirements/development.txt

# Run database migrations
python manage.py migrate

# Start dev server
python manage.py runserver

# Start Celery worker (required for async AI scheduling)
celery -A config worker -l info

# Seed demo data
python manage.py seed_data

# Run all tests (uses SQLite in-memory, no PostgreSQL needed)
pytest

# Run a single test file
pytest tests/test_phase2_integration.py

# Run a single test class or function
pytest tests/test_phase1_compliance.py::TestPerCellViolationShape
pytest tests/test_phase2_billing_wired.py::TestGenerateBillingCapEnforcement::test_blocks_when_cap_would_be_exceeded

# Run with Docker Compose
docker-compose up -d
docker-compose exec web python manage.py migrate
docker-compose logs -f web
```

**Settings module selection:**
- Development: `config.settings.development` (default via `manage.py`)
- Testing: `config.settings.testing` (auto-selected by `pytest.ini`)
- Production: `config.settings.production`

Tests use `DJANGO_SETTINGS_MODULE=config.settings.testing` (set in `pytest.ini`), which swaps PostgreSQL for SQLite in-memory and replaces Firebase auth with Token/Session auth.

## Architecture

### App Structure

All business logic lives under `apps/`:

| App | Responsibility |
|-----|---------------|
| `accounts` | Custom User model, Firebase JWT auth, roles (admin/manager/supervisor/employee) |
| `organizations` | Organization + Branch hierarchy (all data is org-scoped) |
| `employees` | Profiles, contracts, certifications, availability/time-slots, **EmployeeTag** (PR1), **EmployeeDataConsent** (PR1), **sensitive attrs** (gender / birth_date / height / weight, PR1) |
| `shifts` | ShiftTemplate, ShiftRule, ShiftEmployeePriority, **TeamConstraint** (PR1 schema, PR4 compiler, PR6 REST CRUD) |
| `schedules` | ScheduleVersion (with **`derived_from`** self FK, PR1), Schedule rows, ScheduleChange. Endpoints: `check-compliance/` (PR2), `derive-legal/` (PR3) |
| `attendance` | Clock-in/out, anomaly detection |
| `overtime` | OvertimeRecord, OvertimeRule, pay multiplier calculation |
| `compliance` | **Per-cell `Violation` dataclass** (PR2), `check_schedule_violations()` pure function, ComplianceCheck history |
| `ai_engine` | ORToolsProvider (single provider parameterised by mode), **team_constraint_compiler.py** (PR4), Celery task. Endpoint: `POST /api/ai/schedule/generate/` (unified) |
| `audit` | AuditLog model, middleware + signals that log all model writes |
| **`billing`** | **Phase 2 metered billing**. Models: BillingRateConfig, OrgBillingSettings, BillingPeriod, UsageRecord, PaymentMethod (mock today). Pure helpers: `estimate_tokens`, `record_usage`, `would_exceed_cap`. Endpoints: `/api/billing/{rates,usage,settings,estimate}/` |

### Authentication

Every request must carry `Authorization: Bearer <firebase-id-token>`. `FirebaseAuthentication` (`apps/accounts/authentication.py`) verifies the JWT, then `get_or_create`s a local `User` whose `username` is set to `firebase_uid`. Tests bypass this entirely — they use DRF `TokenAuthentication` and create users directly.

### Permission Hierarchy

`IsAdmin > IsManager > IsSupervisor > IsEmployeeOrAbove` — defined in `apps/accounts/permissions.py`. Most write endpoints require `IsSupervisor` or `IsManager`. All endpoints filter querysets to the requesting user's `organization` (and `branch` when set) unless the user is a superuser.

**Self-action exception**: `POST/DELETE /api/employees/employees/{id}/data-consent/` enforces self-action — even superusers get 403. The endpoint sits under `IsAuthenticated` so the consenting employee can reach it.

### AI Scheduling Engine (single provider, parameterised)

`apps/ai_engine/providers/base.py` defines the `BaseScheduleProvider` interface. The active provider is resolved at runtime from `settings.AI_SCHEDULE_PROVIDER` (defaults to `ORToolsProvider`).

`ScheduleRequest` switches drive behaviour — there is **one engine**, not three:

| Field | Effect |
|---|---|
| `seed` | List of `{employee_id, date, shift_id}` — the B grid for repair/gap-fill |
| `minimize_drift_from_seed` | True ⇒ drift objective fires (PR3); changes are minimised against seed and weighted by time-decay |
| `enforce_labor_law` | True (or drift mode) ⇒ labour-law rules become hard constraints |
| `team_constraints` | List of TeamConstraint dicts compiled into CP-SAT clauses (PR4) |
| `time_decay_n` | Linear decay window for drift weighting; default 14 |

The view maps frontend buttons:
- **Full generate** → no seed
- **AI gap-fill** → partial seed + minimize_drift_from_seed
- **Derive A** → full seed + minimize_drift_from_seed + enforce_labor_law

Soft objective weights (high → low): fairness 10, team-constraint soft slack 15-per-unit, max_extra_shifts overcap 20, required-hours under 5 / over 2, employee shift preferences 5–0, preferred time slots 3, drift `drift_weight × time_weight` (default `drift_weight=10`), **shift pattern preference 2** (PR9, tie-breaker only).

For async generation (`run_async: true`), the view delegates to the `generate_schedule_task` Celery task and returns `202 Accepted` with a `task_id`. The task carries a `_billing` side-channel so it records UsageRecord after solving; the cap check still happens synchronously in the view.

### Compliance Engine (per-cell)

`apps/compliance/engine.py` exposes two entry points (PR2):

- `check_schedule_violations(version, rules=None) → List[Violation]` — pure function, no DB writes. Used by `POST /api/schedules/versions/{id}/check-compliance/`.
- `ComplianceEngine.check_schedule_compliance(version, rules=None) → ComplianceCheck` — persists a row for audit.

Each `Violation` is pinned to a single trigger cell `(employee_pk, schedule_date, shift_template_id)` with the rest of the offending window in `related_dates`. Trigger-cell selection per rule:

| Rule | Trigger cell |
|---|---|
| `min_rest_hours` | the *next* shift (started too early) |
| `max_consecutive_days` | the first day past the limit (`max_days + 1` th) |
| `max_weekly_hours` | the last shift of the offending ISO week |
| `max_daily_hours` | the last shift of the offending day |

Cross-midnight rest intervals are calculated with `datetime.combine()` + subtraction. `min_rest_hours` is `float` so fractional caps (9.75 h) work. Legacy keys (`type`, `rest_hours`, …) coexist with the new per-cell keys on `ComplianceCheck.violations` for backward compatibility.

### Schedule Versioning (dual track)

`ScheduleVersion` has a `version_type` (`legal` / `actual`) and a status workflow (`draft → published → approved → archived`).

- **B** = `actual` — real roster (may violate labour law). Source of truth.
- **A** = `legal` — derived from B (`ScheduleVersion.derived_from` self FK, PR1). Always labour-law compliant.

The `approve` action uses an atomic `filter(status='draft').update(...)` to prevent race-condition double-approvals. The `compare` endpoint diffs two versions by `(employee_id, schedule_date, shift_template_id)` — the **cell-identity triple** used throughout the system.

### Data consent boundary (PDPA)

`Employee.sensitive_attributes_for_solver()` is the **single trust boundary** for sensitive employee data. `SENSITIVE_FIELDS = (gender, birth_date, height_cm, weight_kg)`. Without an active `EmployeeDataConsent` row, the method returns all-None — the team-constraint compiler then cannot match those fields against the employee. Non-sensitive attrs (tags, certifications, shift_pattern_preference) bypass the gate.

The view layer wraps this in `_employee_attributes_for_solver(emp)`. Every solver path goes through that helper. New code touching sensitive fields for solver purposes must route through it.

### Metered Billing (Phase 2)

`apps/billing/` is the unique Phase 2 app. Models:

- `BillingRateConfig` — admin-tunable flat-fee per `billing_mode`. History preserved by `effective_from`; latest row wins.
- `OrgBillingSettings` — per-org `monthly_cap_tokens`, `alert_threshold_pct`, `is_billing_enabled` kill switch.
- `BillingPeriod` — org × YYYY-MM with denormalised `total_tokens` (bumped under `select_for_update`).
- `UsageRecord` — one row per AI call. Pre-debit: created even on INFEASIBLE / error.
- `PaymentMethod` — mock today; Stripe schema fields ready for Phase 3.

Per-call flow on `/api/ai/schedule/generate/` and `/api/schedules/versions/{B}/derive-legal/`:

1. Classify billing_mode (`generate` / `fill_gaps` / `derive_legal`).
2. If `consume_token`, pre-flight `would_exceed_cap()` → 402 if blocked (or if `is_billing_enabled=false`).
3. Run solver.
4. If `consume_token`, `record_usage()` → atomic period total bump + UsageRecord row. Response carries `metadata.billing.tokens_charged` and `period_usage_after`.

Pricing per migration `0002_seed_default_rates`: generate=10, fill_gaps=5, derive_legal=3 (admin-editable).

### Audit Logging

`AuditLogMiddleware` sets the current request in thread-local storage. `apps/audit/signals.py` listens to `post_save`/`post_delete` on all models and writes `AuditLog` rows. Failures are logged via `logger.error(...)` (not silently swallowed). Audit is disabled in the test settings via `AUDIT_DISABLED = True`. UsageRecord (billing app) is an independent audit-grade trail of every AI call.

## Key Conventions

- **Decimal arithmetic**: All monetary and hour calculations stay in `Decimal` — never cast to `float` for intermediate arithmetic.
- **Atomic updates**: Use `queryset.filter(...).update(...)` instead of read-then-save for status transitions to avoid race conditions.
- **Data isolation**: All querysets in non-superuser contexts must filter by `request.user.organization`. Missing filter = security bug.
- **Cell identity**: Use the `(employee_id, schedule_date, shift_template_id)` triple, not `Schedule.id`. Phase 1 endpoints all key off this.
- **Consent gate**: Sensitive employee attrs enter the solver only via `_employee_attributes_for_solver`. Don't read `Employee.gender / birth_date / height_cm / weight_kg` directly in solver-feeding code.
- **Pre-debit billing**: Failed AI calls still incur a charge. `consume_token=false` exists for dry runs.
- **Time zone**: `Asia/Taipei` (UTC+8). Always use `django.utils.timezone` utilities, not bare `datetime.now()`.
- **Commit messages**: English (convention adopted 2026-05-24).
- **Frontend integration docs**: When changing any API contract, update the relevant `docs/PHASE_{N}_FRONTEND_GUIDE.md`.

## Phase 1 / Phase 2 commit map

Quick reference for "which commit introduced X":

| Commit | What it did |
|---|---|
| `99ce0ca` | PR1: Schema — sensitive employee attrs, EmployeeTag, EmployeeDataConsent, TeamConstraint, ScheduleVersion.derived_from |
| `6960079` | PR2: Per-cell `Violation`, `/check-compliance/` endpoint |
| `3b21f3e` | PR3: Drift mode + labour-law hard constraints + `/derive-legal/` endpoint |
| `df951a3` | PR4: Team-constraint compiler + unified `/generate/` API + consent invariant landed |
| `c3948fc` | PR5: Phase 1 frontend guide |
| `4eb4524` | PR6: REST CRUD for EmployeeDataConsent + TeamConstraint |
| `edec68e` | PR7: Metered billing schema + estimate/usage/settings API |
| `b43a48b` | PR8: Wire metered billing into generate + derive-legal (cap + post-debit) |
| `23676e6` | PR9: shift_pattern_preference soft constraints (alternating / consecutive) |
| `f6bee07` | PR10: Phase 2 frontend guide |
| `ecbf089` | Integration / cross-PR boundary test suite (9 cases) |

Total test count: **245 passing + 1 SQLite-skipped**.

## Where to read next

Three docs sit alongside this file:

| Doc | Audience | Read when |
|---|---|---|
| [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md) | Engineers, ops | You need the system topology, layered constraints, billing pipeline detail, or the deployment story |
| [docs/BUSINESS_FLOWS.md](./docs/BUSINESS_FLOWS.md) (中文) | PM, sales, customer IT | You need the "why" — what does the customer actually do, what are the business rules, how does the consent / billing flow look from a user's perspective |
| [docs/PHASE_1_FRONTEND_GUIDE.md](./docs/PHASE_1_FRONTEND_GUIDE.md) & [docs/PHASE_2_FRONTEND_GUIDE.md](./docs/PHASE_2_FRONTEND_GUIDE.md) | Frontend engineers | API contract reference per phase; request/response shapes, status codes, UX recommendations |
| [docs/adr/ADR-0001-deployment-platform.md](./docs/adr/ADR-0001-deployment-platform.md) | Anyone touching deploy | Why Railway, what the trade-offs are |
| [docs/deployment-plan.md](./docs/deployment-plan.md) | Anyone deploying | Step-by-step deploy sequence with security/observability checklists |

Plus the [memory directory](/Users/sam/.claude/projects/-Users-sam-Desktop-scheduling-api/memory/) carries Claude-specific project notes (decisions, conventions, why-we-did-it-this-way) across sessions.
