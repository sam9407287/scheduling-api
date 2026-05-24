# Phase 2 Frontend Integration Guide

> **Audience**: Frontend engineers integrating Phase 2 features:
> consent / team-constraint REST endpoints (`PR6 4eb4524`), the metered
> billing API + 402 handling (`PR7 edec68e` / `PR8 b43a48b`), and the
> per-employee shift-pattern preferences (`PR9 23676e6`).
>
> **Backend version**: `23676e6` (Phase 2 complete). Read this guide
> alongside [PHASE_1_FRONTEND_GUIDE.md](./PHASE_1_FRONTEND_GUIDE.md) —
> Phase 1 is unchanged, this document only adds and changes things.

---

## 1. What changed in Phase 2

Three concerns each got a thin REST surface and a couple of behaviour
changes on existing endpoints:

| Theme | New endpoints | Existing endpoints changed |
|---|---|---|
| Consent | `…/data-consent/` (GET/POST/DELETE) | none — solver invariant still gates sensitive attrs |
| Team rules | `…/team-constraints/` (full CRUD) | none — the compiler reads the same rows the UI now writes |
| Metered billing | `/api/billing/rates/`, `/usage/`, `/settings/`, `/estimate/` | `/api/ai/schedule/generate/` and `…/derive-legal/` now charge tokens and can return `402` |
| Pattern preference | none — uses existing `EmployeeAvailability` / employee update | OR-Tools objective penalises against `Employee.shift_pattern_preference` |

The cell-identity invariant from Phase 1 (`(employee_pk, schedule_date,
shift_template_id)`) and the `metadata.billing` envelope on generate
responses both still apply — Phase 2 just adds two fields inside the
envelope (`tokens_charged`, `period_usage_after`).

---

## 2. Consent dialog — now wired end-to-end

Endpoint: `/api/employees/employees/{id}/data-consent/`.

Routed on `EmployeeViewSet` so the URL has the `employees/` prefix
twice — keep this in mind when generating clients.

### When to surface the dialog

After login, before navigating into any scheduling page, do:

```http
GET /api/employees/employees/{me_id}/data-consent/
Authorization: Bearer <token>
```

Three outcomes:

| Status | Meaning | UI action |
|---|---|---|
| `204 No Content` | No row exists yet — first login | Show the consent modal |
| `200 OK` with `is_active: true` | Already consented | Skip the modal; let the user open it via Settings → Privacy |
| `200 OK` with `is_active: false` (`revoked_at` set) | Previously consented then revoked | Treat like 204 — modal back up |

### Accepting consent

```http
POST /api/employees/employees/{me_id}/data-consent/
Content-Type: application/json

{ "consent_version": "1.0", "notes": "" }
```

- Returns `201 Created` on first acceptance, `200 OK` if reactivating
  a previously revoked row.
- Body is optional; both fields have sensible defaults.
- **Self-action only**: the request must be made by the employee owning
  the record. Even superusers get `403 Forbidden` here — this is the
  PDPA self-consent rule and is enforced by the backend, not by the UI.

### Revoking consent

```http
DELETE /api/employees/employees/{me_id}/data-consent/
```

- Returns `200 OK` with the now-revoked payload.
- The row is **not deleted** — `revoked_at` is stamped so the audit
  trail stays intact.
- Re-`POST`ing later reactivates the same row (no duplicates).

### Solver-side effect

The moment `is_active` flips to `false`, the next AI generate / derive-
legal call sees `None` for that employee's `gender`, `birth_date`,
`height_cm`, `weight_kg`, and `age_years`. Team rules targeting any of
those attributes silently exclude the employee. Pattern preference
(see §6) is **not** gated — it is the employee's own choice.

---

## 3. Team-constraint builder — now persistent

Endpoint: `/api/shifts/team-constraints/`.

Standard DRF CRUD (`GET` list, `POST` create, `PATCH /:id/` update,
`DELETE /:id/`). Permission is `IsManager`; the queryset is auto-
scoped to the requesting user's organisation for non-superusers.

Schema reference is in [Phase 1 §6](./PHASE_1_FRONTEND_GUIDE.md#6-team-
constraint-builder-phase-2-surface-phase-1-contract) — Phase 2 just
adds the CRUD wire. A few additions:

- `condition_value` is shape-validated by the serializer:
  - `tag` / `certification` → must be a JSON list (`["driver"]`,
    `[5, 7]`)
  - `height_cm` / `weight_kg` / `age_years` → must be numeric
  - `gender` accepts a string ("male", "female", "other", "undisclosed")
- The same `description` field you display in the live "this means…"
  preview should be saved back; the audit log reads it verbatim.
- Filtering: `?organization=`, `?shift_template=`, `?branch=`, plus the
  literal string `?branch=null` to fetch org-wide rules with no branch
  set.

### Validation errors

The serializer returns `400` with a `condition_value` key when the
shape is wrong:

```json
{"condition_value": ["tag requires a list value"]}
```

Surface those inline in the builder; they are user-correctable.

---

## 4. Billing — read endpoints

### `GET /api/billing/rates/`

Read-only list. Returns the seeded rates from migration
`billing.0002_seed_default_rates`:

```json
{
  "results": [
    {"id": 1, "billing_mode": "generate",     "tokens_per_call": 10, "effective_from": "2026-05-25T…"},
    {"id": 2, "billing_mode": "fill_gaps",    "tokens_per_call": 5,  "effective_from": "…"},
    {"id": 3, "billing_mode": "derive_legal", "tokens_per_call": 3,  "effective_from": "…"}
  ]
}
```

Use these to render a price table next to each AI button. The numbers
can change without a backend release — the customer's admin edits them
via Django admin and the API picks up the latest effective row.

### `GET /api/billing/usage/?year=YYYY&month=M`

Defaults to the current month if `year` / `month` are omitted. Returns:

```jsonc
{
  "organization_id": 7,
  "period": {
    "id": 42,
    "period_year": 2026,
    "period_month": 5,
    "total_tokens": 47,
    "status": "open"
  },
  "cap": 100,            // null when unlimited
  "cap_pct_used": 47.0,  // null when cap is null
  "records": [           // last 100, newest first
    {
      "id": 138,
      "billing_mode": "generate",
      "tokens_charged": 10,
      "solver_status": "success",
      "schedule_version": 87,
      "user": 22,
      "request_metadata": {
        "period_start": "2026-06-01",
        "period_end":   "2026-06-30",
        "employee_count": 18,
        "shift_count": 4
      },
      "created_at": "2026-05-25T07:14:22Z"
    }
  ]
}
```

Build the in-app "Usage this month" widget off `cap_pct_used`. Show the
last 100 records in a collapsible history table; sort by `created_at`
desc (already pre-sorted).

### `GET /api/billing/settings/` and `PATCH …`

```jsonc
GET → {
  "id": 11,
  "organization": 7,
  "monthly_cap_tokens": 100,      // null = unlimited
  "alert_threshold_pct": 80,
  "billing_email": "ops@hospital.tw",
  "is_billing_enabled": true
}

PATCH → body {"monthly_cap_tokens": 200, "alert_threshold_pct": 75}
```

- `GET` auto-creates the row on first access — frontend never gets a
  404 for missing settings.
- `PATCH` only accepts partial updates; you can change one field at a
  time. `monthly_cap_tokens: null` clears the cap.
- Permission is `IsManager`. Showing the page to lower roles → 403.

### `POST /api/billing/estimate/`

Dry-run cost preview. No DB writes.

```http
POST /api/billing/estimate/
Content-Type: application/json

{ "billing_mode": "generate" }
```

```jsonc
200 → {
  "billing_mode": "generate",
  "tokens_to_charge": 10,
  "current_period_tokens": 47,
  "projected_period_tokens": 57,
  "monthly_cap_tokens": 100,
  "would_exceed_cap": false
}
```

Call this when the user hovers / focuses on an AI button to populate
the "≈ 10 tokens (47/100 used this month)" tooltip. The endpoint is
deliberately cheap so debounced hover handlers are fine.

`400` if `billing_mode` is not one of the three known modes.

---

## 5. Billing — what changed on the AI endpoints

Two existing endpoints now charge tokens:

- `POST /api/ai/schedule/generate/`
- `POST /api/schedules/versions/{B_id}/derive-legal/`

### Successful charge

Response body's `metadata.billing` envelope grows two fields:

```jsonc
"metadata": {
  "solver": "OR-Tools CP-SAT",
  "status": "OPTIMAL",
  "billing": {
    "consume_token": true,
    "billing_mode": "generate",       // generate | fill_gaps | derive_legal
    "enforce_labor_law": false,
    "tokens_charged": 10,             // NEW in Phase 2
    "period_usage_after": 57          // NEW in Phase 2
  }
}
```

The `derive-legal` endpoint puts the same fields at the top-level
`billing` key instead of nested under metadata:

```jsonc
"billing": {
  "billing_mode": "derive_legal",
  "tokens_charged": 3,
  "period_usage_after": 60
}
```

Use `period_usage_after` to refresh the in-app usage widget without
making a second `GET /usage/` round-trip.

### Charged on failure too

Pre-debit per the customer rule ("先扱不退"). When the solver returns
`success: false` (e.g. `INFEASIBLE` because no eligible employee
matched a team rule), the response **still carries** `tokens_charged`
and a `UsageRecord` row with `solver_status='infeasible'` (or
`'error'`) is persisted. Surface this in the UI — silently swallowing
a charge after an apparent failure will confuse users.

### `402 Payment Required` — two reasons

The pre-flight cap check fires *before* the solver runs. Response:

```jsonc
402 → {
  "error": "monthly billing cap exceeded",
  "billing_mode": "generate",
  "tokens_required": 10,
  "current_period_tokens": 95,
  "projected_period_tokens": 105,
  "monthly_cap_tokens": 100
}
```

UI: replace the button's normal action with "Cap reached — adjust your
limit in Settings" linking to `/billing/settings`. No `UsageRecord` is
written, so the call is free.

Or, if the org has been globally paused (`is_billing_enabled=false`):

```jsonc
402 → { "error": "billing is disabled for this organization", "billing_mode": "generate" }
```

UI: show a sticky banner; only a support contact can re-enable.

### Opting out

For automated tests or internal dry-runs, the same endpoints accept
`"consume_token": false` in the body. With that flag set, the cap is
ignored, no `UsageRecord` is written, and the response carries the
billing envelope **without** `tokens_charged` / `period_usage_after`.
Production UI should never send this — but it is what your e2e tests
will want.

---

## 6. Shift pattern preference UI

The field `Employee.shift_pattern_preference` finally has solver
behaviour attached. Three values; the employee picks one in their
availability / profile page:

| Value | Label (zh-Hant) | Solver effect |
|---|---|---|
| `none` (default) | 無偏好 | No-op; nothing added to the objective |
| `alternating` | 花花班（早晚交錯） | Penalty per consecutive day where the employee works the *same time-of-day bucket* on both days |
| `consecutive` | 連上放長假 | Penalty per work/rest transition between consecutive days, so the schedule packs work-days into blocks |

This is not a sensitive attribute — the employee picks it themselves
from the availability UI, so it is **not** gated by
`EmployeeDataConsent`. It is exposed to the solver even before the
consent dialog is accepted.

### Update mechanism

There is no dedicated endpoint; use the existing
`PATCH /api/employees/employees/{id}/` with:

```json
{ "shift_pattern_preference": "alternating" }
```

The employee themselves should be allowed to change this — wire it
into the availability page as a radio group with a one-line tooltip
explaining the trade-off.

### Weights

The penalty is `2` per offending consecutive-day pair, deliberately
lower than the fairness weight (`10`). Pattern preference is a
tie-breaker: when multiple equally-fair schedules exist, the one
that respects more employees' preferences wins. It will not push
work onto an over-loaded co-worker just to satisfy a pattern.

If the customer ends up reporting "I picked alternating but I still
got two morning shifts in a row", that is usually correct — the
solver had to assign that pattern to satisfy `min_staff_count` and a
heavier constraint.

---

## 7. Reference: changed and added endpoints in Phase 2

| Endpoint | Status | Notes |
|---|---|---|
| `GET/POST/DELETE /api/employees/employees/{id}/data-consent/` | **new** | Self-action only for POST/DELETE; supervisors can GET to audit |
| `GET/POST/PATCH/DELETE /api/shifts/team-constraints/` | **new** | Full CRUD, IsManager, org-scoped |
| `GET /api/billing/rates/` | **new** | Read-only; admin edits via Django admin |
| `GET /api/billing/usage/` | **new** | `?year=&month=`; defaults to current month |
| `GET/PATCH /api/billing/settings/` | **new** | Auto-creates on first GET; IsManager |
| `POST /api/billing/estimate/` | **new** | Dry-run; no DB writes |
| `POST /api/ai/schedule/generate/` | extended | Now pre-flights cap, post-debits a UsageRecord, may return `402` |
| `POST /api/schedules/versions/{B}/derive-legal/` | extended | Same cap + post-debit behaviour as generate |
| `PATCH /api/employees/employees/{id}/` | unchanged shape | New writable field: `shift_pattern_preference` |

---

## 8. End-to-end Phase 2 workflow

```text
Day 0 (org onboarding)
──────────────────────
1. Manager visits /billing/settings → backend returns the default row.
   Sets monthly_cap_tokens = 200 and clicks Save.
   PATCH /api/billing/settings/ {monthly_cap_tokens: 200}
2. Manager opens the team-constraint builder, adds two rules:
   POST /api/shifts/team-constraints/ {…, scope_time_of_day: "night",
                                       condition_type: "gender",
                                       condition_operator: "eq",
                                       condition_value: "male",
                                       quantifier: "at_least", quantity: 1,
                                       severity: "hard"}
   POST again with condition_type: "certification", quantity: 2.

Day 1 (employee first login)
────────────────────────────
3. Nurse logs in. Frontend calls
   GET /api/employees/employees/{me}/data-consent/ → 204.
   Modal opens, nurse clicks "I agree".
   POST /api/employees/employees/{me}/data-consent/ {consent_version: "1.0"}
   → 201 created.
4. Nurse picks "花花班" in availability.
   PATCH /api/employees/employees/{me}/ {shift_pattern_preference: "alternating"}

Day 2 (manager runs scheduling)
───────────────────────────────
5. Manager opens the scheduling page.
   On hover over "AI generate B":
     POST /api/billing/estimate/ {billing_mode: "generate"}
     → tokens_to_charge=10, current=0, projected=10, cap=200
   Tooltip: "≈ 10 tokens • 0/200 used this month"
6. Click. Backend pre-flights, runs OR-Tools, writes UsageRecord.
   200 → assignments[…], metadata.billing.tokens_charged = 10,
                          metadata.billing.period_usage_after = 10
7. Manager edits the grid, then clicks Check compliance.
   Compliance check is free (not metered).
8. Manager clicks "Derive A from B".
   POST /api/schedules/versions/{B}/derive-legal/ → 201
   billing.tokens_charged = 3, billing.period_usage_after = 13.
9. The Usage widget reflects "13/200 used" without a refresh —
   the post-debit response carried period_usage_after.

Day 20 (cap approaching)
────────────────────────
10. Cumulative usage = 180. Backend sees 180/200 and the alert
    threshold is 80% — Phase 3 will email billing_email. Phase 2
    just exposes the data so the frontend can render its own banner
    using cap_pct_used from /api/billing/usage/.

Day 22 (cap hit)
────────────────
11. Manager clicks "AI generate B" again. Pre-flight projects 195 + 10
    = 205 > 200 → 402.
    UI swaps the button for a "Cap reached — Settings" link.
```

---

## 9. Migration checklist for frontend

If you are upgrading a Phase 1 frontend:

- [ ] Add the consent gating: every scheduling page guarded by a check
  against `is_active` from `GET /data-consent/`.
- [ ] Build the team-constraint builder against the schema reference;
  hook into the new CRUD endpoints.
- [ ] Add the Usage widget driven by `cap_pct_used`.
- [ ] Add the Billing Settings page (manager-only) for cap and email.
- [ ] Add the `estimate` hover tooltip on every AI button.
- [ ] Handle `402` distinctly from `400` / `403` / `500` everywhere AI
  endpoints are called — the body shape is documented in §5.
- [ ] Surface `metadata.billing.tokens_charged` and `period_usage_after`
  on the result modal; refresh the Usage widget from `period_usage_after`
  without an extra fetch.
- [ ] Add the `shift_pattern_preference` radio group to the employee
  availability page.

---

## 10. Open items for Phase 3

- **Stripe integration**: `PaymentMethod.provider='mock'` is a stand-in
  today; the schema fields (`external_token`, `last_4`, `brand`) are
  already in place so wiring Stripe webhooks will not need a migration.
- **Threshold alerts**: `alert_threshold_pct` and `billing_email` are
  exposed today but no email is sent. Phase 3 will add a Celery beat
  task that scans open `BillingPeriod` rows once a day.
- **Soft labour-law rules**: the customer asked for "all labour-law
  hits shown as reminders". Phase 1's compliance engine already returns
  every violation per-cell, and Phase 2's billing surface does not
  block on labour law; if soft warnings become a separate UI track
  (yellow vs red), Phase 3 will add a `severity='soft'` toggle on the
  compliance rule rows.
