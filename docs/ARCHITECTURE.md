# Architecture

> Engineering-side reference for the scheduling-api backend. Targets new
> contributors, ops, and future Claude sessions. Last updated against
> commit `ecbf089` (Phase 1 + Phase 2 complete).
>
> For business-facing flows see [BUSINESS_FLOWS.md](./BUSINESS_FLOWS.md).
> For frontend integration see [PHASE_1_FRONTEND_GUIDE.md](./PHASE_1_FRONTEND_GUIDE.md)
> and [PHASE_2_FRONTEND_GUIDE.md](./PHASE_2_FRONTEND_GUIDE.md).

---

## 1. System topology

```
┌──────────────────────────────────────────────────────────────────────┐
│ Frontend (separate repo) — calls REST API with Firebase JWT          │
└─────────────────────────────────┬────────────────────────────────────┘
                                  │
                  HTTPS (Railway custom domain)
                                  │
┌─────────────────────────────────┴────────────────────────────────────┐
│ web service  (gunicorn, Dockerfile.production)                       │
│   • DRF views + serializers                                          │
│   • FirebaseAuthentication middleware                                │
│   • AuditLogMiddleware (thread-local request capture)                │
│   • Pre-flight billing cap check                                     │
└──┬──────────────────────────────────┬────────────────────────────────┘
   │                                  │
   │ enqueue (run_async=true)         │ direct call (sync solve)
   ▼                                  │
┌──────────────────────┐              │
│ worker service       │              │
│   (Celery)           │              │
│   • generate_schedule_task          │
└──┬───────────────────┘              │
   │                                  │
   ▼                                  ▼
┌──────────────────────────────────────────────────────────────────────┐
│ ORToolsProvider  (apps/ai_engine/providers/ortools_provider.py)       │
│   • CP-SAT solver                                                    │
│   • Hard constraints (structural + labour-law in derive/enforce mode)│
│   • Soft constraints (fairness, preferences, team rules, pattern)    │
│   • Drift objective (time-decay weighted, derive-legal only)         │
└──────────────────────────────────────────────────────────────────────┘

Postgres (managed)              Redis (managed)
  ├─ all app tables                ├─ Celery broker
  └─ audit_log                     └─ Celery result backend
```

Both services build from the same `Dockerfile.production`, differing
only by start command (gunicorn vs. celery). All managed services live
on Railway's private network; only the web service is publicly exposed.

---

## 2. App map

All business logic lives under `apps/`. Each app's `models.py` is the
canonical schema reference; `views.py` is the REST surface.

| App | Responsibility | Phase added |
|---|---|---|
| `accounts` | Custom User model, Firebase JWT auth, role table | 0 |
| `organizations` | Organization + Branch hierarchy (all data is org-scoped) | 0 |
| `employees` | Profiles, contracts, certifications, **EmployeeTag** (PR1), **EmployeeDataConsent** (PR1) | 0/1 |
| `shifts` | ShiftTemplate, ShiftRule, ShiftEmployeePriority, **TeamConstraint** (PR1) | 0/1 |
| `schedules` | ScheduleVersion (with **`derived_from`** self FK, PR1), Schedule, ScheduleChange | 0/1 |
| `attendance` | Clock-in/out, anomaly detection | 0 |
| `overtime` | OvertimeRecord, OvertimeRule, pay multiplier calculation | 0 |
| `compliance` | Per-cell `Violation` dataclass + ComplianceCheck history (PR2) | 0/1 |
| `ai_engine` | ORToolsProvider, **team-constraint compiler** (PR4), Celery task | 0/1 |
| `audit` | AuditLog model, middleware + signals that log all model writes | 0 |
| `billing` | **Metered billing** — rates, periods, usage, settings, payment methods (PR7) | 2 |

---

## 3. Domain model (key tables)

```
Organization ─┬─ Branch ─┬─ Employee ─┬─ EmployeeAvailability ─ EmployeeTimeSlot
              │          │            ├─ EmployeeDataConsent  (PDPA gate)
              │          │            ├─ tags M2M → EmployeeTag
              │          │            ├─ certifications M2M → Certification
              │          │            └─ contracts → Contract
              │          ├─ ShiftTemplate ─ ShiftEmployeePriority
              │          └─ TeamConstraint  (Notion-filter rules)
              │
              ├─ ScheduleVersion ─ Schedule  (cells)
              │      │
              │      └─ derived_from → ScheduleVersion (self, A → B)
              │
              ├─ ComplianceCheck  (history of one-click checks that persisted)
              │
              ├─ OrgBillingSettings  (monthly_cap_tokens, kill switch)
              ├─ BillingPeriod  (org × YYYY-MM, denormalised total)
              ├─ UsageRecord ─ schedule_version FK
              └─ PaymentMethod  (mock today, Stripe-ready)
```

### Cell-identity invariant

Every endpoint that operates on a schedule "cell" identifies it by the
**triple** `(employee_id, schedule_date, shift_template_id)`:

- Compliance violations (`Violation.employee_pk / schedule_date / shift_template_id`)
- derive-legal diffs (`removed_cells` / `added_cells`)
- The ScheduleVersion `compare` action

The frontend grid must key rows off this triple; `Schedule.id` differs
between B and A even for the same cell.

---

## 4. Authentication

Every request must carry `Authorization: Bearer <firebase-id-token>`.

`apps/accounts/authentication.py::FirebaseAuthentication` verifies the
JWT, then `get_or_create`s a local `User` whose `username` equals the
Firebase UID.

Tests bypass this entirely — `pytest.ini` selects
`config.settings.testing`, which uses DRF `TokenAuthentication` /
`SessionAuthentication` and creates users via fixtures in
`conftest.py`. Production preserves Firebase as the first authentication
class while keeping Token/Session for admin (see
`config/settings/production.py` and `test_production_settings.py`).

---

## 5. Permission hierarchy

```
IsAdmin > IsManager > IsSupervisor > IsEmployeeOrAbove
```

Defined in `apps/accounts/permissions.py`. Most write endpoints require
`IsSupervisor` or `IsManager`. **Every** queryset in non-superuser
contexts filters by `request.user.organization` (and `branch` where
relevant). The org-scope filter is the system's primary data-isolation
boundary; a missing filter is a security bug.

### Self-action exception

`POST /api/employees/employees/{id}/data-consent/` and
`DELETE …/data-consent/` enforce **self-action only** — even superusers
get 403. The endpoint sits under `IsAuthenticated` (not `IsSupervisor`)
specifically so the consenting employee can reach it; the rule is
enforced in the action body. This is the PDPA "data subject must
consent themselves" rule.

---

## 6. AI scheduling engine

`apps/ai_engine/providers/base.py` defines the `BaseScheduleProvider`
abstract interface. The active provider is resolved at runtime from
`settings.AI_SCHEDULE_PROVIDER` (defaults to `ORToolsProvider`).

### Single provider, parameterised behaviour

The Phase 1 design locked in **one engine, multiple modes** rather than
separate providers for each scenario. `ScheduleRequest` carries
parameter switches the provider reads:

| Field | Behaviour |
|---|---|
| `seed` | Optional list of `{employee_id, date, shift_id}` triples — the B grid for repair / gap-fill |
| `minimize_drift_from_seed` | When true with a seed, the drift objective fires (PR3) |
| `enforce_labor_law` | When true (or whenever drift mode is on), labour-law rules become hard constraints (PR3) |
| `time_decay_n` | Linear-decay window for drift weighting (`max(1, n - \|day - today\|)`) |
| `team_constraints` | List of rule dicts compiled into CP-SAT clauses (PR4) |

The view layer (`apps/ai_engine/views.py`) maps the frontend's three
buttons onto these flag combinations:

```
Full generate    → no seed, minimize_drift_from_seed = false
AI gap-fill      → partial seed, minimize_drift_from_seed = true
Derive A from B  → full seed, minimize_drift_from_seed = true, enforce_labor_law = true
```

### Constraint layers

Listed in order of how the OR-Tools model is built up:

1. **Structural hard** (always):
   - min_staff_count per (day, shift)
   - one shift per (employee, day)
   - unavailable dates from confirmed schedules
   - required certifications
   - blocked time slots (employee availability)

2. **Labour-law hard** (drift mode OR `enforce_labor_law=true`):
   - max_weekly_hours — minutes summed per ISO week
   - max_consecutive_days — sliding window of (cap+1) days has ≤ cap work days
   - min_rest_hours — pair-wise (day_i, shift_a) → (day_j, shift_b) forbidden when rest < threshold

3. **Team-constraint hard or soft** (PR4 compiler):
   - `at_least` / `at_most` / `exactly` over a scope-filtered eligible set
   - soft severity emits slack variables; weight = 15 per missing unit
   - hard severity with zero matching employees → forced infeasibility

4. **Objective (sum of soft terms)** — weights chosen so customer-
   facing fairness dominates individual preferences:
   - fairness disparity ×10
   - employee shift preferences ×(5 to 0)
   - preferred time slots ×3
   - shift-priority rank → 10 − rank weight; max_extra_shifts overcap ×20
   - team-constraint soft ×15 per slack unit
   - required-hours under ×5 / over ×2
   - **shift pattern preference** (PR9) ×2 per offending pair
   - **drift cost** (PR3) `drift_weight × time_weight × |result − seed|` (default `drift_weight=10`)

### Async path

When `run_async: true`, the view enqueues `generate_schedule_task` via
Celery and returns `202 Accepted` with a `task_id`. The task carries
a `_billing` side-channel so it can write the UsageRecord after the
solver returns. Cap pre-check still runs synchronously in the view —
we want the customer to see 402 immediately, not after a worker delay.

---

## 7. Compliance engine

`apps/compliance/engine.py` exposes two entry points (PR2):

- `check_schedule_violations(schedule_version, rules=None) → List[Violation]`
  Pure function, no DB writes. Used by the one-click button endpoint
  `POST /api/schedules/versions/{id}/check-compliance/`.

- `ComplianceEngine.check_schedule_compliance(...) → ComplianceCheck`
  Persists a `ComplianceCheck` row for the audit trail. Used by
  `POST /api/compliance/checks/check_schedule/`. The stored violation
  dicts carry both the new per-cell keys (`rule`, `schedule_date`,
  `shift_template_id`, `severity`, `related_dates`, `detail`) and the
  legacy keys (`type`, `rest_hours`, …) so test_bugfixes and older
  consumers keep working.

### Trigger-cell selection per rule

| Rule | Trigger cell |
|---|---|
| `min_rest_hours` | the *next* shift (the one that started too early) |
| `max_consecutive_days` | the first day past the limit (`max_days + 1` th) |
| `max_weekly_hours` | the last shift of the offending ISO week |
| `max_daily_hours` | the last shift of the offending day |

`related_dates` carries the rest of the offending window so the
frontend can highlight either the trigger or the whole span.

Cross-midnight rest intervals are computed with `datetime.combine()` +
subtraction to avoid hour-only truncation; `min_rest_hours` is `float`
so fractional caps like `9.75` work.

---

## 8. Consent boundary

`apps/employees/models.py::Employee` exposes a single trust boundary
for sensitive attributes:

```python
def sensitive_attributes_for_solver(self) -> dict:
    if not self.has_active_data_consent():
        return {field: None for field in self.SENSITIVE_FIELDS}
    return {field: getattr(self, field) or None for field in self.SENSITIVE_FIELDS}
```

`SENSITIVE_FIELDS = ('gender', 'birth_date', 'height_cm', 'weight_kg')`.

The view layer wraps this in `_employee_attributes_for_solver(emp)` —
**every** path that hands employee data to the solver goes through
that helper. The team-constraint compiler then reads
`employee['attributes']` and routes sensitive matches against the
gated values. An unconsented employee can never satisfy a sensitive
team rule.

Non-sensitive attributes (`tag_codes`, `certification_ids`,
`shift_pattern_preference`) are exposed without the gate — they are
employee self-set or operationally-public.

---

## 9. Billing pipeline (Phase 2)

Metered, **not** prepaid. Customers attach a credit card (Phase 3 will
wire real Stripe; today `PaymentMethod.provider='mock'`), see per-call
estimates ahead of time, and get charged per usage with a configurable
monthly cap.

### Per-call flow

```
View receives request
   │
   ├─ classify billing_mode  (generate | fill_gaps | derive_legal)
   │
   ├─ if consume_token:
   │     ├─ check is_billing_enabled → 402 if disabled
   │     └─ would_exceed_cap()      → 402 if projected > cap
   │
   ├─ run solver (sync) OR enqueue Celery task (async)
   │
   └─ if consume_token:
         └─ record_usage(...)  ← pre-debit, atomic, under select_for_update
              ↓
         response.metadata.billing = {tokens_charged, period_usage_after}
```

### Atomic accumulation

`apps/billing/models.py::record_usage` is `@transaction.atomic` and
locks the current `BillingPeriod` row via `select_for_update` before
bumping `total_tokens`. That keeps the running total race-safe under
concurrent solver invocations and lets the cap check stay O(1) (a
read of `period.total_tokens`) instead of summing UsageRecords.

### Pre-debit semantics

The customer chose "先扣不退" — `record_usage` is called regardless of
solver outcome. `solver_status` records whether the call succeeded
(`'success'`), the solver returned INFEASIBLE (`'infeasible'`), or
something crashed (`'error'`). All three result in the same token
charge.

### Rate resolution

`BillingRateConfig.current_rate_for(billing_mode)` returns the latest
row whose `effective_from <= now`. Admins insert new rows via Django
admin to change pricing; history is preserved. Migration 0002 seeds
the three Phase 2 modes at `generate=10`, `fill_gaps=5`,
`derive_legal=3`.

---

## 10. Schedule versioning

`ScheduleVersion` has a `version_type` (`legal` / `actual`) and a
status workflow (`draft → published → approved → archived`).

- **B** = `version_type='actual'` — the real roster the team operates
  on, may violate labour law.
- **A** = `version_type='legal'` — the shown-to-inspector copy, derived
  from B with the minimum drift necessary to be legal.
- `ScheduleVersion.derived_from` (self FK, PR1) records A → B lineage.

The `approve` action uses an atomic
`filter(status='draft').update(...)` to prevent race-condition double-
approvals. The `compare` endpoint diffs two versions by the cell-
identity triple.

---

## 11. Audit logging

`AuditLogMiddleware` (apps/audit/middleware.py) stashes the current
request in thread-local storage. `apps/audit/signals.py` listens to
`post_save` / `post_delete` on **every** model and writes an
`AuditLog` row. Failures are logged via `logger.error(...)`, not
silently swallowed. Audit is disabled in tests via
`AUDIT_DISABLED = True` in `config/settings/testing.py`.

`UsageRecord` is itself an audit-grade history of every AI call, so
billing decisions are independently traceable from generic model audit.

---

## 12. Deployment (Railway)

Two services on Railway, both built from `Dockerfile.production`:

- `web`: `gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 2 --timeout 120`
- `worker`: `celery -A config worker -l info --concurrency=2`

Managed Postgres + Redis. Migrations run via CI before deploy (see
[docs/deployment-plan.md](./deployment-plan.md)).

Three environments — `development`, `staging`, `production` — each
with isolated Postgres / Redis / secrets / domain.

---

## 13. Settings module selection

| Module | When | Key differences |
|---|---|---|
| `config.settings.development` | `manage.py runserver` (default) | DEBUG, SQLite or local Postgres |
| `config.settings.testing` | `pytest` (via `pytest.ini`) | SQLite in-memory, audit disabled, DRF Token/Session auth |
| `config.settings.production` | Railway, `DJANGO_ENV=production` | Firebase auth, ALLOWED_HOSTS required, Whitenoise, Sentry |

---

## 14. Key conventions

- **Decimal arithmetic**: all monetary and hour calculations stay in
  `Decimal`; never cast to `float` for intermediate work.
- **Atomic updates**: prefer `queryset.filter(...).update(...)` over
  read-then-save for status transitions.
- **Data isolation**: every non-superuser queryset filters by
  `request.user.organization`. Missing filter = security bug.
- **Time zone**: `Asia/Taipei` (UTC+8). Use `django.utils.timezone`
  utilities, never bare `datetime.now()`.
- **Consent gate**: sensitive employee attrs only enter the solver via
  `_employee_attributes_for_solver`. New code that touches
  `Employee.gender / birth_date / height_cm / weight_kg` for solver
  purposes must route through that helper.
- **Cell identity**: use the `(employee_id, schedule_date,
  shift_template_id)` triple, not `Schedule.id`.
- **Pre-debit billing**: failed AI calls still incur a charge.
  `consume_token=false` exists for dry runs.
- **Commit messages**: English (PR convention from 2026-05-24 onwards).

---

## 15. Test layout

```
tests/
├── test_api.py                                       (auth, swagger)
├── test_advanced.py                                  (legacy)
├── test_bugfixes.py                                  (legacy compliance)
├── test_models.py                                    (legacy model CRUD)
├── test_production_settings.py                      (deploy hardening)
│
├── test_phase1_schema.py                             (PR1)
├── test_phase1_compliance.py                         (PR2)
├── test_phase1_derive_legal.py                       (PR3)
├── test_phase1_team_constraints.py                   (PR4)
│
├── test_phase2_consent_and_team_constraint_crud.py   (PR6)
├── test_phase2_billing_schema.py                     (PR7)
├── test_phase2_billing_wired.py                      (PR8)
├── test_phase2_pattern_preferences.py                (PR9)
│
└── test_phase2_integration.py                        (cross-PR boundary)
```

Total: 245 passing + 1 SQLite-skipped concurrent-write test.
Run all: `pytest`. Sub-second per suite, ~2 s for the full run.

---

## 16. Where to start (new contributor checklist)

1. Read this file end-to-end (~10 min).
2. Skim `apps/<x>/models.py` for each Phase 1/2 app in the table above
   (15 min).
3. Read `apps/ai_engine/providers/ortools_provider.py::generate_schedule`
   — that one method is the heart of the system.
4. Read `apps/billing/models.py` — three pure helpers
   (`estimate_tokens`, `record_usage`, `would_exceed_cap`) glue the
   billing layer to everything else.
5. Run `pytest tests/test_phase2_integration.py -v` — the integration
   suite is the fastest tour of how it all fits together.
6. For business context (why we did it this way) read
   [BUSINESS_FLOWS.md](./BUSINESS_FLOWS.md).
