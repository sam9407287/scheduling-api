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

### `POST /api/auth/login/`  (no auth required; dev/test AND self-registered accounts)

```jsonc
// request — username 或 email 擇一（自助註冊的帳號 username 就是 email）
{ "username": "manager01", "password": "secret" }
{ "email": "someone@outlook.com", "password": "secret" }

// 200
{
  "token": "9a8b7c…",
  "user": { "id": 5, "username": "manager01", "role": "manager", "organization": 1, "branch": 3 }
}
// 401 → { "error": "帳號或密碼錯誤" }
```

### Email 驗證碼自助註冊（2026-10-22）

任何信箱都可以（不限 Gmail）；一個 email 只能有一個帳號；新帳號 = `manager`、
`organization: null`（跟 Google 登入一樣，進系統後走「建立機構」onboarding）。
兩步：

```jsonc
// 1) 寄驗證碼（未登入可打）
POST /api/auth/register/send-code/
{ "email": "someone@outlook.com" }
// 200 { "message": "驗證碼已寄出，請到信箱查看", "email": "someone@outlook.com",
//       "expires_in_seconds": 600, "cooldown_seconds": 60,
//       "debug_code": "352948" }      // 只有本地開發（console email）才有這個欄位，方便串接
// 409 { "code": "email_already_registered" }   → 導去登入（或 Google 登入）
// 429 { "code": "too_many_requests", "reason": "resend_cooldown|email_hourly_limit|ip_hourly_limit",
//       "retry_after_seconds": 42 }            → 「重新寄送」按鈕倒數 retry_after_seconds
// 503 { "code": "email_send_failed" }           → 寄信服務異常，請稍後再試
// 400 → email 格式錯誤

// 2) 驗證碼 + 密碼 → 建帳號並回 Token（之後跟 /auth/login/ 一樣帶 Authorization: Token <key>）
POST /api/auth/register/
{ "email": "someone@outlook.com", "code": "352948", "password": "Str0ng-Pass-2026",
  "first_name": "小明", "last_name": "王", "phone": "0912345678" }   // 後三個 optional
// 201 { "token": "9a8b7c…", "user": { …同 /auth/users/me/ 的欄位, "organization": null } }
// 400 { "code": "invalid_code" }    驗證碼錯（最多錯 5 次）
// 400 { "code": "code_expired" }    10 分鐘過期或還沒索取 → 請重新寄送
// 400 { "code": "code_locked" }     錯太多次 → 請重新寄送
// 400 { "code": "weak_password", "details": ["密碼長度至少 8…", …] }  密碼不符 Django 規則（≥8 碼、不可全數字、不可太常見、不可跟 email 太像）
// 409 { "code": "email_already_registered" }
```

防洗帳號節流（後端固定）：同一 email 60 秒內不得重寄、每小時最多 5 封；同一 IP 每小時 20 封；
驗證碼只存 hash、單次使用、新碼寄出舊碼即作廢。
之後用同一個 email 走 Google 登入會**綁到同一個帳號**（不會變成兩個帳號）。

### Google login (Firebase)

Send `Authorization: Bearer <firebase-id-token>` — no separate login
endpoint. MVP behaviour: known uid → that user; verified email matching
one unbound account → binds (keeps role/org); brand-new account →
auto-provisioned as a MANAGER **with NO organization** (2026-09-23：不再
自動開機構). Errors: 401 `invalid_firebase_token`, 403
`email_not_verified` / `account_inactive`. Details:
[GOOGLE_LOGIN_FRONTEND_GUIDE.md](./GOOGLE_LOGIN_FRONTEND_GUIDE.md).

**Onboarding flow（前端要做的）**: after login call `GET /api/auth/users/me/`;
if `organization` is `null`, route to a「建立機構」page → `POST
/api/organizations/organizations/` (§1) → refetch `/me` (now bound) →
enter the app. While `organization` is null every org-scoped list is empty.

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

**Self-service tenants (2026-09-23)**：
- `POST organizations/` body: `{ "name": "小天使照護" }` — `code` 可省略
  （後端自動產生 `ORG-XXXXXXXX`）。建立成功後該機構**自動綁定為這個帳號的
  租戶**，refetch `/me` 即可拿到 `organization`。
- 一個帳號限一間：已有機構再 POST → **409** `organization_already_exists`。
  前端在使用者已有機構時應隱藏「新增機構」入口。
- 可見性：list/detail 只回自己的機構（別人的機構 404）；superuser 看全部。
- `DELETE` 限 superuser，一般 manager 刪自己的機構 → 403。

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
  "pay_type": "monthly", "pay_type_display": "月薪制",   // 2026-10-13 新增：monthly | hourly，預設 monthly；影響國定假日加給（§8）
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

**Deleting an employee** (`DELETE /api/employees/employees/{id}/`)
replaces the old resign flow: it atomically removes the employee, their
LOGIN ACCOUNT, and ALL history (schedules — including approved versions —
leaves, contracts). Irreversible; past rosters and Excel exports will no
longer show this person. Show a strong confirm dialog before calling it.
`is_active` still exists for filtering but is no longer the offboarding
mechanism.

### 2.0 Certifications master list  (`manager` to write, `supervisor` to read)

Custom certification types — the dropdown is NOT fixed; managers add their
own via this CRUD:

```
GET/POST  /api/employees/certifications/?search=
GET/PATCH/DELETE /api/employees/certifications/{id}/
```

```jsonc
POST body  { "name": "護理師執照", "code": "RN", "description": "", "is_required": false }
// name and code are globally unique → 400 on duplicates
```

Attach to an employee with the write-only `certification_ids` on
POST/PATCH `/api/employees/employees/{id}/` (full replace — send the
complete list); read them back from the `certifications` array.

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
GET/POST  /api/shifts/staffing-requirements/   // manager（2026-10-22 每日人力需求）
GET/PUT/PATCH/DELETE /api/shifts/staffing-requirements/{id}/
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

### 3.2 每日人力需求（「每天需要幾個人」，2026-10-22，PM 10/13 回饋）

`ShiftTemplate.min_staff_count` 是「每天都一樣」的最低人數；這張表讓人數
**依星期幾或特定日期不同**，並可設上限。沒有規則命中的 (日期, 班別) 仍用
`min_staff_count`。

```jsonc
POST /api/shifts/staffing-requirements/
{ "organization": 1,            // 非 superuser 可省略（後端強制為登入者機構）
  "branch": null,               // null = 全機構；填分店 id = 只對該分店的版本生效
  "shift_template": 4,          // null = 「當日總人數」（不分班別、同一人多班只算一人）
  "weekday": 6,                 // 0=週一…6=週日；與 date 擇一；都不填 = 每天
  "date": null,                 // "2026-12-24" 特定日期（優先於 weekday）
  "min_staff": 2,               // 0 = 當天可不排
  "max_staff": 3,               // null = 不限；有填必須 ≥ min_staff（硬上限）
  "note": "週日人少", "is_active": true }
// 201 回傳同欄位 + "weekday_label": "週日", "shift_template_name": "早班", "branch_name"
// 400 { "date": [...] } date 與 weekday 同時填；{ "max_staff": [...] } 上限小於下限；
//     { "shift_template": [...] } 班別不屬於本機構
GET /api/shifts/staffing-requirements/?shift_template=4|null&weekday=6&date=2026-12-24&branch=null&is_active=true
```

優先序（越具體越優先）：`date` > `weekday` > 每天；同一具體程度分店專屬 > 全機構。

生效位置（全部後端處理，前端只要提供設定 UI 與顯示結果）：
- **AI 排班（§5.1 LLM）**：規則進 prompt；超過 `max_staff` 的多餘班次進 `rejected`
  （reason 含「人數上限」）；`warnings` 的缺口以規則人數計（含「當日總人數只排到 x/y 人」）。
- **OR-Tools（§5 generate / §4.2 derive-legal）**：min/max 都是硬約束；人不夠會 INFEASIBLE，
  請把 message 顯示出來。
- **一鍵合規檢查（§4.1）**：回應多了 `coverage`（每天每班需要 vs 已排），見 §4.1。

UI 建議：設定頁一張表「星期/日期 × 班別 → 最少–最多人」；班表每一天的欄頭顯示
「已排/需要」（來自 §4.1 `coverage`），`under` 標紅、`over` 標橘。

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
             "min_rest_hours": 11, "max_consecutive_days": 6,
             // 2026-09-18 勞基法補全 (defaults shown):
             "max_daily_total_hours": 12,        // §32 normal+OT hard cap
             "max_monthly_overtime_hours": 46,   // §32 monthly OT (hard)
             "weekly_rest_days": 2,              // §36 一例一休：休 0 天=hard、休 1 天=soft（2026-10-22）
             "min_break_minutes": 30 },          // §35 >4h shift break (soft)
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
  ],
  // 2026-10-22：每日人力 coverage（需要 vs 已排）。每天每個啟用班別一筆；
  // shift_template_id=null 的是「當日總人數」規則（只有設了該規則的日子才有）。
  // 公休日略過。required_* 來自 §3.2 規則（source="rule"）或模板 min_staff_count（source="template"）。
  "coverage": [
    { "date": "2026-06-01", "shift_template_id": 7, "shift_name": "早班",
      "required_min": 2, "required_max": null, "assigned": 1,
      "status": "under",            // ok | under | over
      "source": "template" }
  ],
  "coverage_summary": { "under": 3, "over": 0, "ok": 57 }
}
```
Render each violation on the cell keyed by `(employee_pk, schedule_date, shift_template_id)`.
Render `coverage` on the day/shift header（例：「1/2」紅字），not on employee cells.

**一例一休（§36，2026-10-22 進規則）** — `weekly_rest_days`：每 ISO 週（週一～週日）
應休 2 天。`detail.rest_days` 0 ⇒ `severity: "hard"`（例假日出勤，紅）、1 ⇒ `"soft"`
（休息日出勤，需加班費，黃）；`detail.regular_day_off_worked` 直接告訴你是哪一種。
只判「版本期間完整涵蓋」的週。AI 排班／派生合規版會自動守住每週 ≤ 5 天（見 §5、§5.1）。

New rule codes (labels come back in `rule_label`, already 中文):
`max_daily_total_hours` / `max_monthly_overtime_hours` are HARD by default;
`min_break_minutes` / `holiday_scheduling` come back with `severity: "soft"`
by default (legal with overtime pay / consent) — render amber, never block
(`weekly_rest_days` is mixed, see above). `holiday_scheduling` fires from the
**built-in Taiwan national holiday table + the org's custom holidays**:

```
GET /api/compliance/holidays/calendar/?start=2026-01-01&end=2027-12-31   (any logged-in user)
// 2026-10-22：班表「一開始就顯示節日」用這支。預設 start=今年 1/1、end=明年 12/31（最長 3 年）。
// 後端內建 2026、2027 全年台灣國定假日（含補假日），不用機構自己建。
// 200
{ "start": "2026-01-01", "end": "2027-12-31", "count": 46,
  "holidays": [
    { "date": "2026-02-17", "name": "春節（初一）", "kind": "holiday", "source": "national", "id": null },
    { "date": "2026-10-09", "name": "國慶日補假",   "kind": "makeup",  "source": "national", "id": null },
    { "date": "2026-11-11", "name": "院慶",         "kind": "holiday", "source": "org",      "id": 3 } ] }
// kind: holiday=節日本日 / makeup=補假日；source: national=內建 / org=機構自訂（同一天機構自訂名稱優先）
// 機構自訂假日仍用下面的 CRUD 增刪（國定假日不可刪，要例外就加一筆同日期的自訂名稱）

GET/POST /api/compliance/holidays/?year=2026     (manager) — 機構自訂假日
GET/PATCH/DELETE /api/compliance/holidays/{id}/
POST body { "organization": 1, "date": "2026-11-11", "name": "院慶" }
// (organization, date) unique → 400 on duplicates
```

All rule evaluation is BACKEND-only — the frontend renders returned
violations and must not re-derive rule logic client-side.

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
{ "reason": "排班內容有誤" }        // OPTIONAL — one-click cancel works with {}

// 200 → full ScheduleVersion body (status back to "draft",
//        approved_by / approved_at cleared)
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
  "enforce_labor_law": false,      // true = labour law as hard constraints（週工時、連續天數、間隔、日工時、一例一休 每週≤5天）
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

### 5.1 LLM 排班（AI 排班請求按鈕）  (`manager`, charges tokens)

前端「AI 排班請求」按鈕唯一要打的端點——由免費 LLM 模型直接產生班表，
後端驗證後**寫入版本**，前端 refetch schedules 即可看到格子：

```jsonc
POST /api/ai/schedule/llm-generate/
{ "schedule_version": 42,
  "period_start": "2026-10-01",   // optional，預設版本期間
  "period_end": "2026-10-07",     // optional；區間上限 62 天
  "allow_multiple_shifts_per_day": false,  // 2026-10-13 新增：同一人同一天可否排不同崗位的班；預設 false（一天一班）
  "consume_token": true }          // 計費比照 generate（10 tokens）

// 201
{ "created_count": 34, "rejected_count": 2,
  "assignments": [{ "id": 90, "employee_id": 12, "date": "2026-10-01", "shift_id": 3 }],
  "rejected": [{ "row": {...}, "reason": "缺少班別要求的證照" }],  // 模型的不合格輸出，最多 50 筆
  "warnings": [                                                 // 最多 50 筆，請原樣顯示
    "此版本只排分店「中山分店」：小明 陳（EMP001）、…屬於其他分店，未納入 AI 排班（要全機構一起排請建立不限分店的版本）",
    "小威 許（EMP009） 在此期間沒有被排到任何班次",
    "2026-10-03 早班 只排到 0/1 人"],                             // 依序：分店範圍 → 範圍內未排到的人 → min_staff 缺口
  "scope": { "branch_id": 4, "branch_name": "中山分店",           // 2026-10-05 新增：這次排班的員工範圍
             "employees_in_scope": [4, 8],
             "employees_out_of_scope": [1, 2, 3, 5, 6, 7, 9, 10] }, // 版本不限分店時 branch_id=null、此陣列為空
  "options": { "allow_multiple_shifts_per_day": false },   // 2026-10-13 新增：本次生效的選項（回顯）
  "model": "gemini-3.6-flash", "engine": "llm",   // 實際回答的模型；主模型過載時會是備援模型
  "billing": { "billing_mode": "generate", "tokens_charged": 10 } }

// 409 schedule_version_locked — 已簽核版本，先取消簽核
// 503 llm_not_configured — 後端未設模型 API key（找 Sam）
// 502 llm_call_failed — 主模型＋所有備援模型都失敗（後端已自動重試＋換模型），可稍後再按
// 402 — 月度 token 額度已滿
```

行為要點：
- **分店範圍（2026-10-05）**：版本綁了分店時，後端只排該分店員工，`scope.employees_out_of_scope` 列出被排除的人、`warnings[0]` 用中文說明。前端在選到有 `branch` 的版本時，請把表格鎖定到該分店（或顯示橫幅），否則其他分店的人會看起來像「AI 漏排」。新增版本表單的分店欄位要讓使用者看得到並可選「不限分店」。
- **同日多班（2026-10-13）**：`allow_multiple_shifts_per_day` 預設 `false`，同一員工同一天只保留第一個班，
  第二個班進 `rejected`（reason「同一天已有班次（本次設定為一天一班）」），版本裡既有的班也算。
  設 `true` 時模型可為同一人在同一天排不同崗位（時間不重疊、每日 ≤12h）。請在 AI 排班對話框放 checkbox。
- **七休一**：模型輸出若讓某人連續工作超過 6 天，第 7 天起的班次會進 `rejected`（reason 含「七休一」），版本內既有班次也算進連續天數。
- **一例一休（2026-10-22）**：prompt 要求每人每週（週一～週日）最多上 5 天；模型還是多排時，同一週第 6 天起的班次進 `rejected`（reason 含「一例一休」），版本內既有班次也算。
- **每日人力需求（2026-10-22，§3.2）**：規則人數進 prompt；超過 `max_staff` 的列進 `rejected`（reason 含「人數上限」）；缺口 `warnings` 以規則人數為準。
- **耗時**：正常 30–60 秒；主模型過載自動退避重試並切換備援模型時後端有 150 秒總預算上限，按鈕請保持 loading、不要設 axios timeout。
- 模型輸出**逐筆驗證**（員工/班別存在、日期在期間、證照、請假日、重複），
  不合格的丟棄並列在 `rejected`，絕不寫入髒資料。
- 只**新增**班次（既有格子不動、重複跳過）；寫入的格子 `notes = "AI 排班"`。
- `warnings` 回報 min_staff 沒排滿的缺口——顯示給管理者，不阻擋。
- 呼叫可能需要 10–60 秒（模型推論），按鈕請做 loading 狀態。
- 成功後 refetch `/schedules/schedules/?version=` 更新畫面即可。

## 6. Compliance config  (`manager`)

```
GET/POST  /api/compliance/rules/
GET       /api/compliance/checks/         // history of persisted checks
GET/PATCH /api/compliance/settings/       // soft vs hard rule config
```

```jsonc
GET  /api/compliance/settings/  → { "soft_rule_types": ["max_weekly_hours"],
                                    "weekly_closed_days": [6] }
PATCH same { "soft_rule_types": ["max_weekly_hours", "min_rest_hours"] }
// valid keys: max_weekly_hours, max_consecutive_days, min_rest_hours, max_daily_hours,
//             weekly_rest_days（一例一休，2026-10-22；標 soft 後 AI/派生 A 人手不足時可放寬）
// 400 on unknown keys
```

**機構公休日（排休，2026-09-23 / PM#1）** — `weekly_closed_days`：

- 整數清單，**0=週一 … 6=週日**（例：診所固定週日公休 → `[6]`）。
  PATCH 會自動去重排序；超出 0–6 或整週全休 → 400。
- 行為（警告哲學）：**手動排班仍可排**，但一鍵合規檢查（§4.1）會對公休日
  的格子回 soft violation `org_closed_day`（`rule_label`「機構公休日排班」、
  `detail.weekday_label` 如「週日」）；**AI 排班（§5、§5.1）與派生合規版
  （§4.2）完全避開公休日**——LLM 模式排進公休日的列會進 `rejected`
  （reason「機構公休日」），min_staff 缺口警告也不計公休日。
- 前端建議：班表把公休日欄位灰底；設定 UI 用七顆 checkbox。

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
          // POST body: { "employee_id": 12, "work_date": "2026-10-10", "actual_hours": "8.00", ... }
          // employee_id 省略 = 本人打卡（帳號須有員工檔案，否則 400）；跨機構 400（2026-10-13）
          // 管理者代建：{ employee_id, work_date, clock_in, clock_out, notes }（ISO datetime，可跨日）
          // clock_in + clock_out 齊全時 actual_hours 由後端重算覆蓋，並重跑異常檢查（超時 / 遲到 /
          // 早退 / 無排班出勤）；anomaly_flag / anomaly_reason 為 read-only（2026-10-26）
PATCH/DELETE /api/attendance/attendances/{id}/
          // PATCH 可改 clock_in / clock_out / notes；改完同樣重算工時與異常，舊的未解決異常先清掉
GET/POST  /api/attendance/anomalies/
GET/POST  /api/overtime/records/
GET/POST  /api/overtime/rules/
```
Standard DRF CRUD; shapes follow the models (`/api/schema/` has the full
field list).

### 國定假日出勤加給依薪制分流（2026-10-13，PM 補充）

`POST /api/overtime/records/calculate/ { "attendance_id": 7 }` 遇到出勤日是機構
國定假日（§6 holiday 表）時，整日工時以 `overtime_type: "holiday"` 一筆記錄：

| `employee.pay_type` | `multiplier` | `notes` |
|---|---|---|
| `hourly` 時薪制 | 機構 holiday 規則倍率，沒設則 `2.00` | 「時薪制國定假日出勤：工資加倍」 |
| `monthly` 月薪制 | `1.00`（**不加倍**） | 「月薪制國定假日出勤：不加倍，以補休為原則；無法補休時再折發一日工資（請手動調整倍率）」 |

月薪制的「補休 vs 折發」由管理者事後決定：折發就 `PATCH /api/overtime/records/{id}/`
把 `multiplier` 改成 `2.00`（`manager` 以上；**只接受 `multiplier` 與 `notes`** 兩個欄位，
其他欄位忽略；無 PUT / DELETE，回 405；跨機構 404）。前端在加班紀錄列表請把 `notes` 顯示出來，
員工表單加「薪制」下拉（§2 `pay_type`）。

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

**Time-range leave** (hourly, simplified v1): set `request_unit:
"time_range"` with `start_time`/`end_time` — single day only
(`start_date == end_date`), same-day `start < end`, no cross-midnight.
Approval of a time-range leave does NOT touch schedule rows
(`affected_schedule_ids` stays `[]`) — overlay it in the roster from
`GET /requests/?status=approved`. The AI solver blocks only shifts whose
times overlap the interval; the rest of the day stays schedulable.

```
GET /api/leaves/balances/[?employee=]     // multi-type balance panel
GET/PUT /api/leaves/settings/             // org day_minutes + per-type quotas (supervisor+)
```

`balances/` returns every leave type in MINUTES (day = org `day_minutes`,
default 480, configurable 60–1440 via `settings/`):
```jsonc
{ "employee": 98, "as_of": "2026-09-01", "day_minutes": 480,
  "entitlement_year_start": "2026-01-01", "entitlement_year_end": "2026-12-31",
  "balances": [
    { "leave_type": "sick", "leave_type_display": "病假",
      "entitled_minutes": 14400,      // null = unlimited (event-based types)
      "used_minutes": 240, "pending_minutes": 0,
      "remaining_minutes": 14160 }    // can go NEGATIVE — overdraft warns, never blocks
  ] }
```
Quota defaults follow 勞工請假規則 (sick 30d / personal 14d / menstrual
12d; marriage/bereavement/maternity/… unlimited); orgs override via
`PUT settings/ { "day_minutes": 420, "quotas": [{ "leave_type": "sick",
"annual_quota_minutes": 10000 }] }`. Annual (特休) is always statutory —
not configurable. Balances are computed live, so changing `day_minutes`
rescales history. `impact/` accepts optional `&start_time=&end_time=` and
then returns `overlap_minutes` + `daily_breakdown` per date.

Rows with status `"leave"`/`"cancelled"` are excluded from labour-law
compliance checks and from derive-legal seeding — they are not worked time.

Legacy `balance/` (annual-only, day-based) still works but is deprecated
in favour of `balances/`.

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

## 8.2 Audit logs  (`manager`, read-only)

```
GET /api/audit/logs/?action=&model_name=&user=&search=&date_from=&date_to=
GET /api/audit/logs/{id}/          // adds old_data/new_data/ip/user_agent
```

Every model write is recorded automatically by the audit signal pipeline;
this API is strictly read-only (mutations → 405). Non-superusers see only
their own organization's user actions. List items carry
`{user_name, action(+_display), model_name, record_id, changes, timestamp}`.
See [AUDIT_LOG_FRONTEND_GUIDE.md](./AUDIT_LOG_FRONTEND_GUIDE.md).

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
