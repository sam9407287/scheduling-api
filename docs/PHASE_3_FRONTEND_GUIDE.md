# Phase 3 Frontend Integration Guide

> **Audience**: Frontend engineers integrating Phase 3 features:
> soft-vs-hard labour-law rule severity (`PR11 7e73b42`) and monthly usage
> threshold alerts (`PR12 f1933ce`).
>
> **Backend version**: `f1933ce`. Read alongside the
> [Phase 1](./PHASE_1_FRONTEND_GUIDE.md) and
> [Phase 2](./PHASE_2_FRONTEND_GUIDE.md) guides — those contracts are
> unchanged; Phase 3 adds two things and amends the compliance-check
> response.

---

## 1. What changed in Phase 3

| Theme | New endpoint | Existing endpoint changed |
|---|---|---|
| Labour-law severity | `GET/PATCH /api/compliance/settings/` | `check-compliance` response gains `soft_rule_types` + each violation's `severity` is now meaningful; `derive-legal` relaxes soft rules |
| Usage alerts | none (server-driven email) | none — but the Usage widget should explain the alert behaviour |

Phase 3 deliberately ships no external-service dependencies. Stripe and the
labour-law RAG feature are deferred to Phase 4; email alerts use a console
backend (logged, not delivered) until Phase 4 wires a real provider.

---

## 2. Soft vs hard labour-law rules

Until Phase 3, every labour-law violation was `severity: "hard"` and
derive-legal enforced all of them as hard constraints (could return 409
INFEASIBLE). Now an organisation can mark specific rules **soft**:

- **Soft rule** → still reported by the compliance check, but
  `severity: "soft"`. In derive-legal it becomes a heavy penalty, not a
  hard constraint — the solver avoids it but will not fail the whole
  derivation over it.
- **Hard rule** (default) → unchanged: `severity: "hard"`, enforced as a
  hard constraint in derive-legal.

The four configurable rule keys:

```
max_weekly_hours
max_consecutive_days
min_rest_hours
max_daily_hours
```

### 2.1 Settings endpoint

```http
GET /api/compliance/settings/
Authorization: Bearer <token>     (IsManager)
```

```jsonc
200 → {
  "id": 3,
  "organization": 7,
  "soft_rule_types": ["max_weekly_hours"],   // [] = everything hard
  "created_at": "…",
  "updated_at": "…"
}
```

`GET` auto-creates the row on first access (never 404).

```http
PATCH /api/compliance/settings/
{ "soft_rule_types": ["max_weekly_hours", "min_rest_hours"] }
```

- Returns `200` with the updated list.
- `400` if any entry is not one of the four known keys:
  `{"soft_rule_types": ["unknown rule types: ['foo']; valid: [...]"]}`.
- Permission is `IsManager`; lower roles get `403`.

Build a settings page with one checkbox per rule key ("treat as warning
only"). An unchecked rule is hard (blocks derive-legal); a checked rule
is soft (warning).

### 2.2 Reading severity in the compliance grid

`POST /api/schedules/versions/{id}/check-compliance/` now returns
`soft_rule_types` alongside the violations, and each violation's
`severity` reflects the org config:

```jsonc
200 → {
  "schedule_version_id": 42,
  "rules_applied": { … },
  "soft_rule_types": ["max_weekly_hours"],
  "total_count": 3,
  "summary_by_rule": { "max_weekly_hours": 1, "min_rest_hours": 2 },
  "violations": [
    {
      "rule": "max_weekly_hours",
      "severity": "soft",          // ← yellow
      "rule_label": "週工時超標",
      "employee_pk": 12, "schedule_date": "2026-06-05",
      "shift_template_id": 7, "related_dates": [...], "detail": {...}
    },
    {
      "rule": "min_rest_hours",
      "severity": "hard",          // ← red
      …
    }
  ]
}
```

Rendering recommendation:

| severity | Grid cell | Side panel |
|---|---|---|
| `hard` | red border + red dot | "違規" (blocks legal derivation) |
| `soft` | amber border + amber dot | "提醒" (allowed, but flagged) |

**Important**: a soft rule is *not hidden*. Every labour-law hit is still
returned — the customer explicitly wants all of them shown. Soft only
changes the colour and the messaging, never suppresses the row.

### 2.3 Override per request (optional)

Both `check-compliance` and `derive-legal` accept a `soft_rule_types`
field in the request body that overrides the org config for that one
call. Use this for a "preview as if X were soft" toggle without mutating
the saved settings:

```jsonc
POST /api/schedules/versions/{id}/check-compliance/
{ "soft_rule_types": ["min_rest_hours"] }
```

### 2.4 derive-legal with soft rules

When a rule is soft (via org config or body override), `derive-legal`
will no longer return `409 INFEASIBLE` purely because that rule cannot be
satisfied — it produces an A that minimises the soft violation instead.
The produced A may therefore still contain soft violations; run
`check-compliance` on the new A to surface them as amber reminders.

A hard rule that cannot be satisfied still returns `409` as before.

---

## 3. Monthly usage threshold alerts

This is **server-driven** — there is no endpoint to call. An hourly
backend job (`scan_billing_thresholds`) checks each org's current-month
usage against `alert_threshold_pct` (set on the billing settings, see
[Phase 2 §4](./PHASE_2_FRONTEND_GUIDE.md#4-billing--read-endpoints)) and
emails `billing_email` once per threshold per month.

### What the frontend should do

- **Nothing is required** — the alert is email-only.
- **Recommended**: mirror the logic client-side using the existing
  `GET /api/billing/usage/` response (`cap_pct_used`). When
  `cap_pct_used >= alert_threshold_pct`, show an in-app amber banner
  ("本月 AI 用量已達 80%") so users see it without checking email.
- The `alert_threshold_pct` and `billing_email` fields are editable via
  the existing `PATCH /api/billing/settings/` (Phase 2). Surface them on
  the same billing settings page.

### Phase 3 limitation

Phase 3 logs the alert via the console email backend — **no email is
actually delivered**. Treat the in-app banner (driven by
`cap_pct_used`) as the real user-facing alert until Phase 4 wires a real
email provider. Nothing in the API contract changes when that happens.

---

## 4. Reference: changed / added endpoints in Phase 3

| Endpoint | Status | Notes |
|---|---|---|
| `GET/PATCH /api/compliance/settings/` | **new** | Per-org soft_rule_types; IsManager |
| `POST /api/schedules/versions/{id}/check-compliance/` | extended | Response gains `soft_rule_types`; `severity` now reflects config; accepts body `soft_rule_types` override |
| `POST /api/schedules/versions/{B}/derive-legal/` | extended | Soft rules relaxed to penalties; accepts body `soft_rule_types` override |
| `POST /api/ai/schedule/generate/` | extended | Accepts body `soft_rule_types`; defaults from org config |
| (hourly Celery beat) | **new** | `scan_billing_thresholds` — email alert, no API surface |

---

## 5. Migration checklist for frontend

- [ ] Add a compliance settings page: one "warning only" checkbox per
      labour-law rule, backed by `GET/PATCH /api/compliance/settings/`.
- [ ] Update the compliance grid to render `severity: "soft"` as amber
      and `"hard"` as red; both remain visible.
- [ ] Optionally add a "preview as soft" toggle using the per-request
      `soft_rule_types` override.
- [ ] Handle the new derive-legal behaviour: a soft-rule org may get a
      `201` A that still has amber violations — prompt the user to
      re-check compliance on the new A.
- [ ] Add the in-app usage banner driven by `cap_pct_used >=
      alert_threshold_pct` from `GET /api/billing/usage/`.

---

## 6. Open items for Phase 4

- **Real email provider** for the threshold alert (SendGrid / SES / …) —
  swap `EMAIL_BACKEND`, no contract change.
- **Stripe integration** — `PaymentMethod.provider='mock'` today.
- **Labour-law RAG** (pgvector + LangChain) — natural-language "which
  article does this violate" queries.
