# Phase 1 Frontend Integration Guide

> **Audience**: Frontend engineers integrating the new dual-schedule (A/B) flow,
> the one-click compliance grid, the data-consent dialog, and the unified AI
> generate endpoint shipped in commits `99ce0ca` → `df951a3`.
>
> **Backend version**: `df951a3` (Phase 1 complete). API base path:
> `http://localhost:8000/api`. OpenAPI schema is regenerated automatically at
> `/api/schema/` and a TypeScript client can be produced from it (see
> `FRONTEND_DEVELOPER_GUIDE.md`).

---

## 1. What changed in Phase 1

Phase 1 turns the system into a **single scheduling product** with three
buttons. Same data model, same engine, different parameters:

| Button | Seed | Engine call | Charges token? |
|---|---|---|---|
| Manual drag-and-drop | full grid | none | no |
| AI gap-fill | partial B grid | `/api/ai/schedule/generate/` with `seed_version_id` + `minimize_drift_from_seed=true` | yes |
| AI full generate (B) | empty + rules | `/api/ai/schedule/generate/` without seed | yes |
| One-click compliance | full B grid | `/api/schedules/versions/{B}/check-compliance/` | no |
| Derive A from B | full B grid | `/api/schedules/versions/{B}/derive-legal/` | yes |

A and B are two `ScheduleVersion` rows distinguished by `version_type`:

- **B** (`version_type='actual'`) — the real roster. Source of truth. May
  violate labour law.
- **A** (`version_type='legal'`) — derived from B (`derived_from` FK). What
  the company shows to inspectors. Always labour-law compliant.

Frontend grid rendering should always operate against a `ScheduleVersion`
and key cells by `(employee_id, schedule_date, shift_template_id)` — that
triple is the **stable cell identity** used by every Phase 1 endpoint
(violation list, diff list, drift seed).

---

## 2. Data consent dialog (first-login UX)

Sensitive employee attributes — `gender`, `birth_date`, `height_cm`,
`weight_kg`, and `age_years` (derived from `birth_date`) — are gated by a
one-shot consent record. Without consent, these attributes are **invisible
to the scheduling engine** even if values exist in the database. Team rules
targeting any of those attributes (e.g. "night shift needs ≥ 1 male ≥ 175cm")
silently exclude the unconsented employee. There is no special error — the
solver simply treats them as if the attribute were unset.

### When to show the dialog

On first navigation to any scheduling-related page (roster grid, availability
editor, profile), if the current employee has no `EmployeeDataConsent` row.
Determined by `GET /api/employees/me/` (or equivalent) — the absence of
`data_consent` in the response payload means the dialog must appear.

### Dialog content

Show the consent text (provided by legal/PDPA) and a single "I agree" button.
On click:

```http
POST /api/employees/{me}/data-consent/
{
  "consent_version": "1.0"
}
```

Server stamps `consented_at = now()`. Subsequent logins skip the dialog. A
"Revoke consent" toggle should live in Settings → Privacy. Revocation calls
`PATCH /api/employees/{me}/data-consent/` with `{"revoked_at": "now"}` and
takes effect on the next solver run.

> Endpoint contract for the consent CRUD is still being finalised; this guide
> will be updated when those routes land. Until then, treat the DB schema
> (`EmployeeDataConsent(employee, consented_at, revoked_at, consent_version)`)
> as the source of truth.

---

## 3. One-click compliance check

### Request

```http
POST /api/schedules/versions/{version_id}/check-compliance/
Authorization: Bearer <token>
Content-Type: application/json

{
  "rules": {
    "max_weekly_hours": 40,
    "max_daily_hours": 8,
    "min_rest_hours": 11,
    "max_consecutive_days": 6
  }
}
```

The `rules` body is optional; omit it to use the labour-law defaults
above. The endpoint does **not** persist a `ComplianceCheck` row — it is
safe to call on every keystroke or on debounce.

### Response

```json
{
  "schedule_version_id": 42,
  "rules_applied": { "...": "..." },
  "total_count": 3,
  "summary_by_rule": {
    "min_rest_hours": 2,
    "max_consecutive_days": 1
  },
  "violations": [
    {
      "rule": "min_rest_hours",
      "rule_label": "兩班間隔不足",
      "severity": "hard",
      "employee_pk": 12,
      "employee_code": "E0042",
      "employee_name": "王小明",
      "schedule_date": "2026-05-26",
      "shift_template_id": 7,
      "related_dates": ["2026-05-25"],
      "detail": {
        "rest_hours": 2.0,
        "required_hours": 11,
        "previous_shift_id": 4
      }
    }
  ]
}
```

### How to render

Every `violation` is pinned to a **single trigger cell** — the
`(employee_pk, schedule_date, shift_template_id)` triple — so the grid can
highlight precisely that square. `related_dates` lists other cells in the
offending window (e.g. the night shift before a too-short rest), so the UI
can choose between two render strategies:

- **Tight mode**: highlight only the trigger cell with a red border, tooltip
  showing `rule_label` + `detail`.
- **Span mode**: highlight the trigger cell **and** every `related_dates`
  cell (use a lighter shade for related). Best for weekly/consecutive
  violations where one trigger does not convey the full picture.

Trigger cell selection per rule:

| Rule | Trigger cell |
|---|---|
| `min_rest_hours` | the *next* shift (the one that started too early) |
| `max_consecutive_days` | the first day past the limit (`max_days + 1` th) |
| `max_weekly_hours` | the last shift of the offending ISO week |
| `max_daily_hours` | the last shift of the offending day |

`severity` is currently always `"hard"` — when soft labour-law rules ship
in Phase 2, treat `"soft"` as a yellow warning and `"hard"` as a red block.

`summary_by_rule` should drive a side panel that lets the user filter the
grid by rule type ("show only rest-interval violations").

---

## 4. Deriving A from B

### When the user clicks "Generate legal version"

```http
POST /api/schedules/versions/{B_id}/derive-legal/
Authorization: Bearer <token>
Content-Type: application/json

{
  "today": "2026-05-25",
  "time_decay_n": 14,
  "drift_weight": 10,
  "constraints": {
    "max_weekly_hours": 40,
    "max_consecutive_days": 6,
    "min_rest_hours": 11
  },
  "label": "May 2026 (legal)"
}
```

All body fields are optional. Defaults:

- `today` = server today (used as the time-decay anchor; cells near today
  cost more to change).
- `time_decay_n` = 14 — the linear-decay window in days. A cell `d` days
  from `today` is weighted `max(1, time_decay_n - |d|)`. So a cell two days
  out costs `12×` to flip; a cell 14+ days out costs `1×`.
- `drift_weight` = 10 — multiplier in front of the time-weighted drift sum.
  Raising it makes the solver more reluctant to change anything.

### Response (201)

```json
{
  "legal_version_id": 87,
  "legal_version_label": "May 2026 (legal)",
  "derived_from_id": 42,
  "solver_metadata": {
    "solver": "OR-Tools CP-SAT",
    "status": "OPTIMAL",
    "solve_time_seconds": 0.41,
    "mode": "derive_legal",
    "time_decay_n": 14
  },
  "diff_summary": {
    "cells_in_b": 84,
    "cells_in_a": 84,
    "cells_unchanged": 82,
    "cells_removed_from_b": 2,
    "cells_added_in_a": 2
  },
  "removed_cells": [
    {"employee_id": 12, "date": "2026-05-31", "shift_id": 4}
  ],
  "added_cells": [
    {"employee_id": 17, "date": "2026-05-31", "shift_id": 4}
  ]
}
```

### Error responses

| Status | When | What to show |
|---|---|---|
| 400 | Target version is not `actual` | "You must derive A from a B (actual) version." |
| 400 | B has zero schedule rows | "B is empty — nothing to derive from." |
| 409 | Solver INFEASIBLE | Show solver diagnostics from `violations[]` and `metadata`. Most common cause: a labour-law cap combined with insufficient candidate employees. Suggest the user widens the candidate pool or relaxes the cap. |

### How to render the diff

Open the new A version in a grid side-by-side with B:

- Cells in `cells_unchanged` (= `b_set ∩ a_set`): render in normal style.
- Cells in `removed_cells`: render in B's column with strikethrough/red.
- Cells in `added_cells`: render in A's column with green/plus icon.

A "view A only" toggle hides B and shows A in the standard grid.

---

## 5. Unified AI generate endpoint

`POST /api/ai/schedule/generate/` is the single entry point for all
solver-backed scheduling. The same endpoint handles three modes; the
frontend picks the parameter combination based on which button the user
clicked.

### Common fields

```jsonc
{
  "organization_id": 1,
  "branch_id": 3,                  // optional
  "period_start": "2026-06-01",
  "period_end":   "2026-06-30",
  "employee_ids":         [12,17,33],   // optional, default = all active in org
  "shift_template_ids":   [4,5,6],      // optional, default = all active in org
  "constraints": { "max_weekly_hours": 40 },
  "preferences": {},
  "enforce_labor_law": false,
  "consume_token": true,
  "run_async": false
}
```

### Mode A — Full AI generation of B

Use when the user starts with an empty grid and hits "AI 生成 B".

```jsonc
{
  // common fields above, plus:
  "enforce_labor_law": false  // or true if the user ticked "must be legal"
}
```

No seed. The solver fills the grid from scratch, honouring `TeamConstraint`
rules from the DB and (optionally) labour-law as hard constraints.

### Mode B — AI gap-fill

Use when the user has partially filled B by dragging and now clicks "AI
補齊".

```jsonc
{
  "seed_version_id": 42,
  "minimize_drift_from_seed": true,
  "today": "2026-05-25",
  "time_decay_n": 14,
  "enforce_labor_law": false
}
```

The seed comes from the partial B (`seed_version_id`). The solver keeps
filled cells in place (drift cost) and fills the empty ones. Set
`enforce_labor_law: true` if the user wants the gap-fill to stay legal.

### Mode C — Derive A from B

You can either call the dedicated `POST /schedules/versions/{B}/derive-legal/`
(simpler, returns diff summary) or use this generic endpoint with:

```jsonc
{
  "seed_version_id": 42,
  "minimize_drift_from_seed": true,
  "enforce_labor_law": true,
  "today": "2026-05-25",
  "time_decay_n": 14
}
```

Either route produces the same A.

### Response

```jsonc
{
  "success": true,
  "assignments": [
    { "employee_id": 12, "date": "2026-06-01", "shift_id": 4, "shift_name": "早" }
  ],
  "score": 173.0,
  "violations": [],
  "message": null,
  "metadata": {
    "solver": "OR-Tools CP-SAT",
    "status": "OPTIMAL",
    "solve_time_seconds": 0.31,
    "mode": "derive_legal",          // or "generate"
    "time_decay_n": 14,
    "billing": {
      "consume_token": true,
      "billing_mode": "derive_legal", // or "generate" / "fill_gaps"
      "enforce_labor_law": true
    }
  }
}
```

The `metadata.billing` object is the **token-billing intent** for this
call. Phase 1 does not deduct tokens — `consume_token: true` is purely
informational so the UI can pre-render "this will cost X tokens" before
Phase 2 lands real billing. The `billing_mode` field tells the UI which
pricing tier applies. Pricing per mode is set on the customer's plan and
is not exposed by this endpoint.

`run_async: true` returns `202 Accepted` with `{task_id, status, billing}`
instead of the result; the frontend polls `GET /api/ai/schedule/tasks/{task_id}/`
to retrieve the final `assignments`.

### Validation errors

- `400` `minimize_drift_from_seed=true requires seed_version_id` — drift
  mode needs an explicit seed.
- `400` `period_start must be before period_end`
- `404` seed version not found
- `403` seed version belongs to a different organisation

---

## 6. Team-constraint builder (Phase 2 surface, Phase 1 contract)

The backend already accepts and applies `TeamConstraint` rows; building the
UI is a Phase 2 deliverable. The schema is finalised so frontend can
prototype against it now:

```jsonc
POST /api/shifts/team-constraints/
{
  "shift_template_id": 7,         // null = any shift
  "branch_id": null,              // null = whole org
  "scope_time_of_day": "night",   // any / morning / afternoon / evening / night
  "condition_type": "height_cm",  // gender | height_cm | weight_kg | age_years | tag | certification
  "condition_operator": "gte",    // eq | ne | gte | lte | in | contains
  "condition_value": 175,         // shape depends on condition_type, see below
  "quantifier": "at_least",       // at_least | at_most | exactly
  "quantity": 1,
  "severity": "hard",             // hard | soft
  "description": "Night shift needs ≥ 1 person 175cm+"
}
```

`condition_value` shape by `condition_type`:

| condition_type | value example | matches operators |
|---|---|---|
| `gender` | `"male"` | eq, ne, in |
| `height_cm` / `weight_kg` / `age_years` | `175` | eq, ne, gte, lte |
| `tag` | `["driver", "bilingual"]` | contains (subset), in (intersection) |
| `certification` | `[5, 7]` (cert IDs) | contains, in |

### Time-of-day buckets

The compiler routes shifts into buckets by `start_time`:

- `night`: `start_time ≥ 22:00` or `< 05:00`
- `morning`: `05:00 ≤ start_time < 12:00`
- `afternoon`: `12:00 ≤ start_time < 17:00`
- `evening`: `17:00 ≤ start_time < 22:00`
- `any`: matches every shift

### Severity semantics

- `hard`: model.Add() — the schedule cannot violate it.
- `soft`: emits a slack variable with weight `SOFT_PENALTY_PER_UNIT = 15`
  per missing unit, added to the objective.

A `hard` rule with **zero matching employees** in the candidate pool
forces INFEASIBLE so the UI can surface "no candidate matches" rather
than producing a silent non-compliant schedule.

### Builder UX recommendations

Three-tier dropdown matching the schema:

```
Scope          → branch / shift / time-of-day chips
Condition      → type → operator → value (input morphs by type)
Quantifier     → at_least / at_most / exactly + integer
Severity       → hard / soft pill
```

Show a live "this means: …" preview that mirrors `description`. Save the
preview text into `description` so the audit log captures the rule in
human-readable form.

---

## 7. Putting it together: a typical workflow

```text
Day 1
─────
1. User logs in. data_consent missing → modal opens. User clicks "I agree".
2. User opens scheduling page for next month. Empty grid.
3. User clicks "AI generate B".
   POST /api/ai/schedule/generate/ { …, enforce_labor_law: false }
   metadata.billing.billing_mode === "generate"
4. Assignments render in the grid as B (actual).
5. User drags two cells around. Each drag updates the B ScheduleVersion
   via /api/schedules/schedules/{id}/ PATCH.

Day 2
─────
6. User clicks "Check compliance".
   POST /api/schedules/versions/{B}/check-compliance/
   Two rest-interval violations come back. Grid highlights the two
   triggering night→morning transitions.
7. User clicks "Generate legal A".
   POST /api/schedules/versions/{B}/derive-legal/
   201 with diff_summary { cells_unchanged: 82, cells_removed_from_b: 2 }
   A renders with green/red overlay against B.
8. Manager approves A. POST /api/schedules/versions/{A}/approve/
```

---

## 8. Cell-identity invariant (read this)

Every Phase 1 endpoint that talks about a "cell" — violation, drift,
removed/added in the diff — uses the same triple:

```
(employee_pk, schedule_date, shift_template_id)
```

Build your grid keyed off this triple. Don't key by `Schedule.id` because A
rows have different IDs than B rows even when they refer to the same cell.

---

## 9. Token billing (Phase 1 contract is stable; charging lands in Phase 2)

Every solver-backed call ships `metadata.billing`:

```jsonc
{
  "consume_token": true,         // false = caller asked for a dry run
  "billing_mode": "derive_legal", // generate | fill_gaps | derive_legal
  "enforce_labor_law": false
}
```

The Phase 1 backend records this intent but does not actually deduct
tokens. Phase 2 will introduce `TokenBalance` and `TokenTransaction` and a
`/api/billing/estimate/` endpoint that takes the same request body and
returns the cost without solving. Until then, you can hard-code a price
table client-side and use `billing_mode` as the key.

---

## 10. Reference: changed/added endpoints in Phase 1

| Endpoint | Status | Purpose |
|---|---|---|
| `POST /api/schedules/versions/{id}/check-compliance/` | new | One-click per-cell violation list (no DB write) |
| `POST /api/schedules/versions/{B}/derive-legal/` | new | Build A from B with min drift |
| `POST /api/ai/schedule/generate/` | extended | Now accepts seed_version_id, minimize_drift_from_seed, time_decay_n, today, drift_weight, enforce_labor_law, consume_token |
| `GET /api/schedules/versions/` | extended | Response now includes `derived_from` |
| `POST /api/compliance/checks/check_schedule/` | unchanged | Still persists a ComplianceCheck row; the violation dicts now carry both legacy keys (`type`, `rest_hours`, `employee_id` string) **and** new keys (`rule`, `schedule_date`, `shift_template_id`, `severity`, `related_dates`, `detail`) |

---

## 11. Open items for follow-up

- Dedicated CRUD endpoints for `EmployeeDataConsent` — schema is in DB,
  REST surface to be added in a follow-up commit.
- Dedicated CRUD endpoints for `TeamConstraint` — same story; the rules
  table exists, the API listing/mutating it lands next.
- Token estimate endpoint (`/api/billing/estimate/`) is Phase 2.
- Soft labour-law rules (warnings rather than blockers) are Phase 2.
