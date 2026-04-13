# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

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
pytest tests/test_api.py

# Run a single test class or function
pytest tests/test_api.py::TestAuthAPI
pytest tests/test_api.py::TestAuthAPI::test_get_current_user_profile

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
| `accounts` | Custom User model, Firebase JWT auth, roles (admin/manager/supervisor) |
| `organizations` | Organization + Branch hierarchy (all data is org-scoped) |
| `employees` | Employee profiles, contracts, certifications, availability/time-slots |
| `shifts` | ShiftTemplate (班別), ShiftRule, ShiftEmployeePriority |
| `schedules` | ScheduleVersion (legal/actual, draft→approved lifecycle) + Schedule rows |
| `attendance` | Clock-in/out, anomaly detection |
| `overtime` | OvertimeRecord, OvertimeRule, pay multiplier calculation |
| `compliance` | `compliance/engine.py` checks rest intervals, weekly hours, consecutive days |
| `ai_engine` | OR-Tools CP-SAT solver, async Celery task, pluggable provider interface |
| `audit` | AuditLog model, middleware + signals that log all model writes |

### Authentication

Every request must carry `Authorization: Bearer <firebase-id-token>`. `FirebaseAuthentication` (`apps/accounts/authentication.py`) verifies the JWT, then `get_or_create`s a local `User` whose `username` is set to `firebase_uid`. Tests bypass this entirely — they use DRF `TokenAuthentication` and create users directly.

### Permission Hierarchy

`IsAdmin > IsManager > IsSupervisor > IsEmployeeOrAbove` — defined in `apps/accounts/permissions.py`. Most write endpoints require `IsSupervisor` or `IsManager`. All endpoints filter querysets to the requesting user's `organization` (and `branch` when set) unless the user is a superuser.

### AI Scheduling Engine

`apps/ai_engine/providers/base.py` defines the `BaseScheduleProvider` interface (4 abstract methods: `generate_schedule`, `optimize_schedule`, `check_compliance`, `evaluate_change`). The active provider is resolved at runtime from `settings.AI_SCHEDULE_PROVIDER` (defaults to `ORToolsProvider`).

`ORToolsProvider` uses Google OR-Tools CP-SAT solver:
- **Hard constraints** (model.Add == 0): min staff per shift/day, one shift per employee per day, unavailable dates, required certifications, blocked time slots
- **Soft constraints** (objective penalties): fairness disparity (weight 10), shift preferences (weight 5–0), preferred time slots (penalty 3), required weekly hours (under×5 / over×2), shift employee priority rank (rank 1→penalty 0, rank 4→penalty 8, not listed→penalty 10), max_extra_shifts cap (penalty ×20)

For async generation (`run_async: true`), the view delegates to the `generate_schedule_task` Celery task and returns `202 Accepted` with a `task_id`.

### Schedule Versioning

`ScheduleVersion` has a `version_type` (`legal` / `actual`) and a status workflow (`draft → published → approved → archived`). The `approve` action uses an atomic `filter(status='draft').update(...)` to prevent race-condition double-approvals. The `compare` endpoint diffs two versions by `(employee_id, schedule_date, shift_template_id)` key, returning items that differ in `expected_hours`, `status`, or `notes`.

### Employee Availability

Each employee optionally has one `EmployeeAvailability` record (OneToOne) with nested `EmployeeTimeSlot` entries (`blocked` = hard constraint, `preferred` = soft constraint). The `PUT /employees/{id}/availability/` endpoint performs a full bulk-replace of time_slots. The `generate` endpoint auto-loads availability from the DB — the frontend does not need to pass it.

### Compliance Engine

`apps/compliance/engine.py` contains pure Python logic (no DB calls). Cross-midnight rest intervals are calculated using `datetime.combine()` + subtraction to avoid hour-only truncation errors. `min_rest_hours` is typed as `float` to support fractional hours (e.g., `9.75`).

### Audit Logging

`AuditLogMiddleware` sets the current request in thread-local storage. `apps/audit/signals.py` listens to `post_save`/`post_delete` on all models and writes `AuditLog` rows. Failures are logged via `logger.error(...)` (not silently swallowed). Audit is disabled in the test settings via `AUDIT_DISABLED = True`.

### Key Conventions

- **Decimal arithmetic**: All monetary and hour calculations stay in `Decimal` — never cast to `float` for intermediate arithmetic.
- **Atomic updates**: Use `queryset.filter(...).update(...)` instead of read-then-save for status transitions to avoid race conditions.
- **Data isolation**: All querysets in non-superuser contexts must filter by `request.user.organization`.
- **Time zone**: `Asia/Taipei` (UTC+8). Always use `django.utils.timezone` utilities, not bare `datetime.now()`.
- **Frontend migration docs**: When changing any API contract, update `FRONTEND_MIGRATION_GUIDE.md`.
