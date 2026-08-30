# Frontend API Reference

> A flat, practical contract for frontend developers: how to authenticate,
> the shared conventions, and every endpoint's request / response shape.
> **No backend architecture here** — for the "why" see the phase guides.
>
> Base URL: `http://localhost:8000/api` (dev). OpenAPI schema is live at
> `/api/schema/`, Swagger UI at `/api/docs/`.

---

## Authentication

Production uses Firebase ID tokens; dev/test uses a username/password login
that returns a DRF token. Either way, send the token on every request:

```
Authorization: Bearer <firebase-id-token>      # production
Authorization: Token <drf-token>               # dev/test (from /auth/login/)
```

### `POST /api/auth/login/`  (dev/test only, no auth required)

```jsonc
// request
{ "username": "manager01", "password": "secret" }

// 200
{
  "token": "9a8b7c…",
  "user": { "id": 5, "username": "manager01", "role": "manager", "organization": 1, "branch": 3 }
}
// 401 → { "error": "帳號或密碼錯誤" }
```

### `GET /api/auth/users/me/`

```jsonc
// 200 — the ONLY trusted mapping from login identity to Employee
{ "id": 5, "username": "alice", "role_name": "employee",
  "organization": 1, "organization_name": "Demo Care Center",
  "branch": 3, "branch_name": "Taipei",
  "employee_pk": 98, "employee_code": "E0001",   // null when the user has
                                                  // no Employee profile —
                                                  // never guess from lists
  "email": "…", "first_name": "…", "last_name": "…", "phone": "", "is_active": true }
```

---

## Shared conventions

- **Format**: JSON in, JSON out. Send `Content-Type: application/json`.
- **List responses** are paginated by DRF:
  ```jsonc
  { "count": 42, "next": "…?page=2", "previous": null, "results": [ … ] }
  ```
- **Org scoping**: every list is auto-filtered to your organization. You
  never see other orgs' data; you don't need to pass an org filter (though
  `?organization=<id>` is accepted for superusers).
- **Common query params** on most list endpoints: `?search=`, `?ordering=`,
  `?page=`, plus resource-specific filters noted below.
- **Errors**:
  ```jsonc
  400 → { "field_name": ["message"] }      // validation
  400 → { "error": "message" }             // action errors
  401 → not authenticated
  403 → authenticated but not permitted
  404 → not found
  402 → billing cap / disabled (AI endpoints only)
  409 → conflict (derive-legal infeasible)
  ```
- **Dates**: `YYYY-MM-DD`. **Times**: `HH:MM` (24h). **Timezone**: Asia/Taipei.
- **Roles** (ascending power): `employee < supervisor < manager < admin`.
  Each endpoint notes the minimum role.

---

## 1. Organizations & branches  (`manager`)

```
GET/POST            /api/organizations/organizations/
GET/PUT/PATCH/DELETE /api/organizations/organizations/{id}/
GET/POST            /api/organizations/branches/
GET/PUT/PATCH/DELETE /api/organizations/branches/{id}/
```

Branch body: `{ "organization": 1, "name": "信義分院", "code": "XY", "address": "", "phone": "" }`

---

## 2. Employees  (`supervisor`; self for consent)

```
GET                 /api/employees/employees/?search=&branch=&is_active=&certification=
POST                /api/employees/employees/
GET/PUT/PATCH/DELETE /api/employees/employees/{id}/
```

Employee object (key fields):
```jsonc
{
  "id": 12, "employee_id": "E0042",
  "user": { "id": 30, "username": "...", "full_name": "王小明" },
  "organization": 1, "branch": 3, "position": "nurse",
  "contract_type": "full_time", "agreed_hours_per_week": "40.00",
  "certifications": [ { "id": 5, "name": "ACLS", "code": "ACLS" } ],
  "hire_date": "2024-01-01", "is_active": true,
  // sensitive — only meaningful with active consent (see §2.2)
  "gender": "male", "birth_date": "1990-05-01",
  "height_cm": "178.50", "weight_kg": "72.00",
  "shift_pattern_preference": "alternating"   // none | alternating | consecutive
}
```

Write sensitive / preference fields with PATCH:
```jsonc
PATCH /api/employees/employees/{id}/
{ "shift_pattern_preference": "alternating", "height_cm": "178.5" }
```

### 2.1 Availability  (per-employee, bulk-replace)

```
GET                 /api/employees/employees/{id}/availability/
PUT / PATCH         /api/employees/employees/{id}/availability/
POST                /api/employees/employees/{id}/availability/time_slots/
DELETE              /api/employees/employees/{id}/availability/time_slots/{slot_id}/
```

```jsonc
PUT body
{
  "required_hours_per_week": "36.00",
  "special_rules": "週三不可排班",
  "time_slots": [
    { "slot_type": "blocked",   "day_of_week": 2, "start_time": "08:00", "end_time": "12:00" },
    { "slot_type": "preferred", "day_of_week": null, "start_time": "09:00", "end_time": "17:00" }
  ]
}
// slot_type: blocked (hard) | preferred (soft); day_of_week: 0=Mon..6=Sun, null=every day
// GET returns 204 when no availability has been created yet
```

### 2.2 Data consent  (employee self-action)

```
GET    /api/employees/employees/{id}/data-consent/   // 204 if none, else payload
POST   /api/employees/employees/{id}/data-consent/   // create / reactivate
DELETE /api/employees/employees/{id}/data-consent/   // revoke
```

```jsonc
POST body  { "consent_version": "1.0" }
200/201 →  { "id": 7, "consented_at": "…", "revoked_at": null, "consent_version": "1.0", "is_active": true }
```

- POST/DELETE must be the employee themselves (else 403 — even for admins).
- GET works for the employee and supervisors+ (to audit).
- Use `is_active === false` (or 204) to decide whether to show the consent dialog.

---

## 3. Shifts  (`supervisor` for templates, `manager` for rules)

```
GET/POST  /api/shifts/templates/?organization=&is_active=
GET/PUT/PATCH/DELETE /api/shifts/templates/{id}/
GET/PUT   /api/shifts/templates/{id}/employee_priorities/   // GET list, PUT bulk-replace
GET/POST  /api/shifts/rules/
GET/POST  /api/shifts/team-constraints/        // manager
GET/PUT/PATCH/DELETE /api/shifts/team-constraints/{id}/
```

Shift template body:
```jsonc
{
  "organization": 1, "name": "夜班",
  "start_time": "22:00", "end_time": "06:00",
  "break_minutes": 30, "overlap_minutes": 30,
  "min_staff_count": 2,
  "certification_ids": [5, 7]      // write-only; required certs
}
// response also includes read-only "duration_hours"
```

Employee priorities bulk-replace:
```jsonc
PUT /api/shifts/templates/{id}/employee_priorities/
[ { "employee": 12, "priority_rank": 1, "max_extra_shifts": 3 },
  { "employee": 17, "priority_rank": 2, "max_extra_shifts": null } ]
```

Shift rule body (`rule_type` choices: `max_consecutive_days`,
`min_rest_hours`, `max_weekly_hours`, `mandatory_rest_day`,
`max_daily_hours`):
```jsonc
POST /api/shifts/rules/
{ "organization": 1, "name": "每日工時上限",
  "rule_type": "max_daily_hours", "value": { "max_hours": 10 }, "is_active": true }
// value also accepts {"hours": n} / {"value": n} / bare number
```
An active `max_daily_hours` rule feeds the AI solver's daily-hours cap
(precedence: solver default 8h < org rule < request `constraints`).

**Multi-shift days**: the AI solver no longer limits one shift per employee
per day. Same-day shifts are allowed when their times do not overlap; the
daily total stays within `max_daily_hours` when labour law is enforced, and
a same-day split shift does not count as a rest-interval violation.

### 3.1 Team constraint  (Notion-filter rule)

```jsonc
POST /api/shifts/team-constraints/
{
  "organization": 1,
  "shift_template": 7,            // null = any shift
  "branch": null,                 // null = whole org
  "scope_time_of_day": "night",   // any | morning | afternoon | evening | night
  "condition_type": "height_cm",  // gender | height_cm | weight_kg | age_years | tag | certification
  "condition_operator": "gte",    // eq | ne | gte | lte | in | contains
  "condition_value": 175,         // shape depends on condition_type (see below)
  "quantifier": "at_least",       // at_least | at_most | exactly
  "quantity": 1,
  "severity": "hard",             // hard | soft
  "description": "夜班至少 1 名 175cm 以上"
}
```
`condition_value` shapes: gender → `"male"`; height/weight/age → number;
tag → `["driver"]`; certification → `[5,7]` (cert ids).
List filters: `?shift_template=`, `?branch=` (use `?branch=null` for org-wide), `?is_active=`.

---

## 4. Schedules  (`supervisor`)

A `ScheduleVersion` is one roster; `version_type` is `actual` (B) or
`legal` (A). `Schedule` rows are the cells.

```
GET/POST  /api/schedules/versions/?organization=&version_type=&status=
GET/PUT/PATCH/DELETE /api/schedules/versions/{id}/
POST      /api/schedules/versions/{id}/approve/
POST      /api/schedules/versions/{id}/unapprove/             // see §4.3
GET       /api/schedules/versions/{id}/compare/?version2_id=<id>
POST      /api/schedules/versions/{id}/create_dual_versions/
POST      /api/schedules/versions/{id}/check-compliance/      // see §4.1
POST      /api/schedules/versions/{B_id}/derive-legal/        // see §4.2

GET       /api/schedules/versions/approved-timeline/           // see §4.4
GET/POST  /api/schedules/overlap-decisions/                    // see §4.4
GET       /api/schedules/day-overview/?date=                   // see §4.5

GET/POST  /api/schedules/schedules/?version=&employee=&date_from=&date_to=
GET/PUT/PATCH/DELETE /api/schedules/schedules/{id}/
GET/POST  /api/schedules/changes/
```

`ScheduleVersion.status`, `approved_by`, `approved_at` are **read-only** in
PUT/PATCH — state only moves through `approve` / `unapprove`.

**Approved-version lock**: while a version's status is not `draft`
(approved/published/archived), every schedule write (POST/PUT/PATCH/DELETE
on `/api/schedules/schedules/`) targeting it returns:

```jsonc
// 409
{ "code": "schedule_version_locked", "error": "Approved schedule versions are read-only." }
```

Unapprove the version first to edit it.

ScheduleVersion body (period fields are READ-ONLY — do not send them):
```jsonc
{ "organization": 1, "branch": 3, "version_label": "2026-06",
  "version_type": "actual" }
// response adds: status, period_start, period_end, derived_from,
//                schedule_count, *_display
```

`period_start`/`period_end` are the backend-maintained data-coverage range:
initialised to the server's today on create, auto-EXPANDED (never shrunk)
whenever a schedule is created/updated outside the current range. They are
not a scheduling restriction — any date can be scheduled at any time.

Schedule (cell) body:
```jsonc
{ "schedule_version": 42, "employee": 12, "shift_template": 7,
  "schedule_date": "2026-06-05", "expected_hours": "8.00", "status": "draft", "notes": "" }
```

**No time restrictions** (2026-08-07): `schedule_date` is NOT validated
against the version's `period_start`/`period_end` — schedules can be added
or removed on any date. The period is display metadata (default view range),
nothing more. Use §4.5 day-overview to show what other rosters already have
on a date.

### 4.1 One-click compliance check  (free, no DB write)

```jsonc
POST /api/schedules/versions/{id}/check-compliance/
// optional body:
{ "rules": { "max_weekly_hours": 40, "max_daily_hours": 8,
             "min_rest_hours": 11, "max_consecutive_days": 6 },
  "soft_rule_types": ["max_weekly_hours"] }   // optional override

// 200
{
  "schedule_version_id": 42,
  "rules_applied": { … },
  "soft_rule_types": ["max_weekly_hours"],
  "total_count": 3,
  "summary_by_rule": { "min_rest_hours": 2, "max_weekly_hours": 1 },
  "violations": [
    {
      "rule": "min_rest_hours", "rule_label": "兩班間隔不足",
      "severity": "hard",                       // hard=red, soft=amber; both always shown
      "employee_pk": 12, "employee_code": "E0042", "employee_name": "王小明",
      "schedule_date": "2026-06-06",            // the trigger cell
      "shift_template_id": 7,
      "related_dates": ["2026-06-05"],          // other cells in the offending window
      "detail": { "rest_hours": 2.0, "required_hours": 11 }
    }
  ]
}
```
Render each violation on the cell keyed by `(employee_pk, schedule_date, shift_template_id)`.

### 4.2 Derive A from B  (charges tokens)

```jsonc
POST /api/schedules/versions/{B_id}/derive-legal/
// all optional:
{ "today": "2026-05-30", "time_decay_n": 14, "drift_weight": 10,
  "constraints": { "max_weekly_hours": 40, "max_consecutive_days": 6, "min_rest_hours": 11 },
  "soft_rule_types": [], "label": "2026-06 (legal)", "consume_token": true }

// 201
{
  "legal_version_id": 87, "derived_from_id": 42,
  "diff_summary": { "cells_in_b": 84, "cells_in_a": 84, "cells_unchanged": 82,
                    "cells_removed_from_b": 2, "cells_added_in_a": 2 },
  "removed_cells": [ { "employee_id": 12, "date": "2026-06-30", "shift_id": 4 } ],
  "added_cells":   [ { "employee_id": 17, "date": "2026-06-30", "shift_id": 4 } ],
  "billing": { "billing_mode": "derive_legal", "tokens_charged": 3, "period_usage_after": 13 }
}
// 400 if target is not an actual(B) version or B is empty
// 402 if monthly cap exceeded / billing disabled
// 409 if no legal schedule is possible (hard rules unsatisfiable)
```

### 4.3 Unapprove  (approved → draft)

```jsonc
POST /api/schedules/versions/{id}/unapprove/
{ "reason": "排班內容有誤" }        // required, non-blank

// 200 → full ScheduleVersion body (status back to "draft",
//        approved_by / approved_at cleared)
// 400 { "error": "reason is required" }
// 409 { "code": "unapprove_conflict", "error": "Only approved versions can be unapproved." }
```

The reason lands in the audit log. There is **no overlap check** on approve
or unapprove — several approved versions covering the same period is a
normal, supported state.

### 4.4 Approval summary  (簽核總表: conflicts + overlap decisions)

```jsonc
GET /api/schedules/versions/approved-timeline/
    ?organization=1&version_type=actual&date_from=2026-08-01&date_to=2026-08-31[&branch=3|all]
// version_type/date_from/date_to required; range ≤ 62 days.
// branch filters by the EMPLOYEE's current branch (not the version's).

// 200
{
  "versions":  [ /* approved versions (period overlaps range OR has
                    schedules in range — out-of-period schedules visible) */ ],
  "schedules": [ /* Schedule bodies in range, plus previous-day
                    cross-midnight shifts bleeding into the range */ ],
  "conflicts": [   // cross-version TIME-intersection groups per employee.
                   // Same-version combine is never a conflict; versions of
                   // different branches still conflict (one person, one body).
    {
      "conflict_key": "9d41…",          // from member ids + updated_at:
                                         // any member edit → new key
      "starts_at": "2026-08-03T08:00:00",
      "ends_at": "2026-08-03T16:00:00",
      "employee_id": 12,
      "schedule_ids": [101, 205],
      "schedules": [ /* full Schedule bodies of the group */ ],
      "decision": null                   // or the stored decision (below)
    }
  ],
  "unresolved_conflict_count": 1
}
```

Overlaps are informational — nothing blocks saving or approving. The manager
resolves each group:

```jsonc
POST /api/schedules/overlap-decisions/
{ "conflict_key": "9d41…", "schedule_ids": [101, 205],
  "decision": "select",                  // keep a subset…
  "selected_schedule_ids": [101],        // …which must not overlap each other
  "comment": "" }
// or
{ "conflict_key": "9d41…", "schedule_ids": [101, 205],
  "decision": "coexist",                 // keep all
  "selected_schedule_ids": [101, 205],
  "comment": "支援性重疊，主管確認" }      // REQUIRED for coexist

// 201 created / 200 same conflict_key re-submitted (updates the decision)
// 400 invalid selection (empty, outside group, or overlapping picks) /
//     missing coexist comment
// 409 { "code": "conflict_changed", ... } — the group changed since the
//     timeline was fetched; re-fetch and re-prompt
GET /api/schedules/overlap-decisions/?employee=&date_from=&date_to=   // audit listing
```

Response decision body: `{ id, conflict_key, organization, branch,
version_type, employee, schedule_date, schedule_ids, decision,
selected_schedule_ids, comment, decided_by, decided_by_name, decided_at,
created_at }`.

### 4.5 Day overview  (cross-version info for one date)

```jsonc
GET /api/schedules/day-overview/?date=2026-08-03[&employee=12][&exclude_version=42][&include_archived=true]

// 200 — what other rosters already scheduled that day (informational only,
//        no conflict detection; archived versions excluded by default)
{
  "date": "2026-08-03",
  "entries": [
    { "version": { "id": 5, "version_label": "8月B", "version_type": "actual",
                   "status": "approved", "branch": 3, "branch_name": "北店",
                   "period_start": "2026-08-01", "period_end": "2026-08-31" },
      "schedules": [ /* full Schedule bodies */ ] }
  ]
}
```

Call it (typically with `exclude_version=<editing version>`) when adding a
schedule so the manager sees that day's entries in other rosters.

---

## 5. AI scheduling  (`manager`, charges tokens)

One endpoint, three modes by parameter combination.

```jsonc
POST /api/ai/schedule/generate/
{
  "organization_id": 1, "branch_id": 3,
  "period_start": "2026-06-01", "period_end": "2026-06-30",
  "employee_ids": [12, 17],        // optional, default = all active
  "shift_template_ids": [4, 5],    // optional, default = all active
  "constraints": {}, "preferences": {},
  "enforce_labor_law": false,      // true = labour law as hard constraints
  "soft_rule_types": [],           // optional per-call soft labour rules
  "consume_token": true,
  "run_async": false,

  // mode switches:
  "seed_version_id": 42,           // omit = full generate; present = use B as seed
  "minimize_drift_from_seed": true // with seed: gap-fill / derive style
}
```

| Button | Params |
|---|---|
| Full AI generate B | no `seed_version_id` |
| AI gap-fill | `seed_version_id` + `minimize_drift_from_seed: true` |
| Derive A | `seed_version_id` + `minimize_drift_from_seed: true` + `enforce_labor_law: true` (or use §4.2) |

```jsonc
// 200 (sync)
{
  "success": true,
  "assignments": [ { "employee_id": 12, "date": "2026-06-01", "shift_id": 4, "shift_name": "早" } ],
  "score": 173.0, "violations": [], "message": null,
  "metadata": {
    "status": "OPTIMAL", "mode": "generate",
    "billing": { "billing_mode": "generate", "tokens_charged": 10,
                 "period_usage_after": 10, "consume_token": true }
  }
}
// 202 (run_async:true) → { "task_id": "…", "status": "pending", "billing": { … } }
// 402 → { "error": "monthly billing cap exceeded", "tokens_required": 10,
//         "current_period_tokens": 95, "projected_period_tokens": 105, "monthly_cap_tokens": 100 }
// 400 → minimize_drift_from_seed without seed_version_id
```
`success:false` with a `violations` array means the solver could not find a
schedule (still billed — pre-debit). Show the message.

---

## 6. Compliance config  (`manager`)

```
GET/POST  /api/compliance/rules/
GET       /api/compliance/checks/         // history of persisted checks
GET/PATCH /api/compliance/settings/       // soft vs hard rule config
```

```jsonc
GET  /api/compliance/settings/  → { "soft_rule_types": ["max_weekly_hours"] }
PATCH same { "soft_rule_types": ["max_weekly_hours", "min_rest_hours"] }
// valid keys: max_weekly_hours, max_consecutive_days, min_rest_hours, max_daily_hours
// 400 on unknown keys
```

---

## 7. Billing  (`manager` for settings; `authenticated` for reads)

```
GET   /api/billing/rates/                       // current token prices
GET   /api/billing/usage/?year=&month=          // current month if omitted
GET/PATCH /api/billing/settings/                // monthly cap + alert threshold
POST  /api/billing/estimate/                    // dry-run cost preview
```

```jsonc
GET /api/billing/usage/
{
  "organization_id": 7,
  "period": { "period_year": 2026, "period_month": 6, "total_tokens": 47, "status": "open" },
  "cap": 100, "cap_pct_used": 47.0,           // both null if unlimited
  "records": [ { "billing_mode": "generate", "tokens_charged": 10,
                 "solver_status": "success", "created_at": "…" } ]   // last 100
}

GET /api/billing/settings/
{ "monthly_cap_tokens": 100, "alert_threshold_pct": 80,
  "billing_email": "ops@hospital.tw", "is_billing_enabled": true }
PATCH same body (partial). monthly_cap_tokens:null = unlimited.

POST /api/billing/estimate/  { "billing_mode": "generate" }
→ { "billing_mode": "generate", "tokens_to_charge": 10,
    "current_period_tokens": 47, "projected_period_tokens": 57,
    "monthly_cap_tokens": 100, "would_exceed_cap": false }
```

Render a "this month X/Y used" widget from `cap_pct_used`. When it reaches
`alert_threshold_pct`, show an amber banner (the backend also emails the
contact, but treat the banner as the user-facing signal).

---

## 8. Attendance & overtime  (`supervisor`)

```
GET/POST  /api/attendance/attendances/?employee=&work_date=
GET/POST  /api/attendance/anomalies/
GET/POST  /api/overtime/records/
GET/POST  /api/overtime/rules/
```
Standard DRF CRUD; shapes follow the models (`/api/schema/` has the full
field list).

---

## 8.1 Leaves  (`authenticated`; approval needs `supervisor`)

Full-day leave with single-layer approval. Employees see and submit only
their own; supervisor+ sees the whole org and may submit ON BEHALF of an
employee — on-behalf requests are auto-approved (phone-in leave).

**Submission source** (backend-owned, read-only `submission_source`):
- `self` — the target employee IS the requester (including a supervisor
  filing their own leave) → created as `pending`, goes through review.
- `manager_proxy` — supervisor+ filing for someone else → auto-approved.
No one can approve/reject their OWN request (403
`self_approval_forbidden`) — withdrawing your own request is `cancel/`.

```
GET/POST  /api/leaves/requests/?status=&employee=&date_from=&date_to=
GET       /api/leaves/requests/{id}/
POST      /api/leaves/requests/{id}/approve/    // supervisor+, body {"note"?}
POST      /api/leaves/requests/{id}/reject/     // supervisor+, body {"note"} REQUIRED
POST      /api/leaves/requests/{id}/cancel/     // own pending, or supervisor+ on approved
GET       /api/leaves/requests/impact/?employee=&start_date=&end_date=
GET       /api/leaves/requests/balance/[?employee=]
```

Create body:
```jsonc
{ "employee": 12, "leave_type": "annual",   // annual特休 sick病假 personal事假
                                            // menstrual生理假 marriage婚假 bereavement喪假
                                            // maternity產假 paternity陪產假 official公假 other
  "start_date": "2026-09-10", "end_date": "2026-09-11", "reason": "..." }
// response adds: status(+_display), total_days, employee_code/name,
//                created_by/reviewed_by(+_name), review_note, affected_schedule_ids
```

Behaviour on approve (single source of truth for the roster):
- the employee's Schedule rows in range (non-archived versions) flip to
  `status: "leave"` — kept, never deleted; the roster shows the vacancy.
- `affected_schedule_ids` stores `[{id, prev_status}]`; cancelling an
  approved leave restores exactly those rows (cells hand-edited afterwards
  are left alone).
- approved leave days are HARD unavailable dates for AI generate and
  derive-legal. Manual scheduling on a leave day is still allowed —
  warn, don't block (same philosophy as cross-version overlaps).
- 409 codes: `leave_not_pending` (approve/reject a non-pending request),
  `leave_not_cancellable`. 403 code: `self_approval_forbidden`.

Impact preview (`impact/`) returns the schedules that would be affected —
call it right after the user picks dates so they see "這幾天你有 N 個班"
before submitting, and again on the review screen.

Balance (`balance/`) tracks annual-leave (特休) quota per Labor Standards
Act §38 (6mo→3d, 1y→7, 2y→10, 3y→14, 5y→15, 10y+→+1/yr cap 30),
anniversary-year accounting; only APPROVED `annual` requests deduct:
```jsonc
{ "employee": 12, "hire_date": "2024-01-01", "as_of": "2026-08-26",
  "entitlement_year_start": "2026-01-01", "entitlement_year_end": "2026-12-31",
  "entitled_days": 10, "used_days": 3, "remaining_days": 7 }
```

Schedule rows gained a `"leave"` status choice — render those cells with a
distinct 請假 style in the roster grid.

## 9. Typical call sequence

```
login → GET /employees/.../data-consent/ (show dialog if 204/inactive)
      → POST consent
      → manager: PATCH /billing/settings/ (cap), POST /shifts/team-constraints/ (rules)
      → POST /ai/schedule/generate/ (build B)  ← reads metadata.billing
      → write cells via /schedules/schedules/ (or persist returned assignments)
      → POST /schedules/versions/{B}/check-compliance/ (highlight violations)
      → POST /schedules/versions/{B}/derive-legal/ (build A)
      → POST /schedules/versions/{A}/approve/
```

For full per-feature context and UX recommendations see
`PHASE_1_FRONTEND_GUIDE.md`, `PHASE_2_FRONTEND_GUIDE.md`,
`PHASE_3_FRONTEND_GUIDE.md`.
