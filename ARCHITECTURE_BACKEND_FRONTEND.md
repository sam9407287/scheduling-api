# 排班系統 — 後端與前端整合設計架構文件

> 文件版本：1.0
> 建立日期：2026-05-04
> 對應後端版本：commit `f03e1a2`
> 文件用途：給前端工程師、客戶 IT、未來維運人員參考。完整描述目前後端寫法、資料流、API 合約、與前端對接的設計建議。

---

## 目錄

1. [系統總覽](#1-系統總覽)
2. [技術堆疊](#2-技術堆疊)
3. [部署架構](#3-部署架構)
4. [資料模型總圖](#4-資料模型總圖)
5. [核心模組詳解](#5-核心模組詳解)
6. [AI 排班引擎詳解](#6-ai-排班引擎詳解)
7. [認證與授權](#7-認證與授權)
8. [API 設計慣例](#8-api-設計慣例)
9. [完整 API 端點清單](#9-完整-api-端點清單)
10. [前端整合指南](#10-前端整合指南)
11. [關鍵業務流程](#11-關鍵業務流程)
12. [資料一致性與並行控制](#12-資料一致性與並行控制)
13. [錯誤處理與例外](#13-錯誤處理與例外)
14. [效能與擴展](#14-效能與擴展)
15. [監控與稽核](#15-監控與稽核)
16. [已知限制與技術債](#16-已知限制與技術債)

---

## 1. 系統總覽

### 1.1 系統定位

本系統是「**多組織、多分店、AI 自動排班**」的 SaaS 平台，核心功能：

- 員工資料、合約、證照、可用時段管理
- 班別（ShiftTemplate）定義與排班規則
- **AI 自動排班引擎**（Google OR-Tools CP-SAT 求解器）
- 雙軌排班版本管理（法規版 / 實際版）
- 出勤打卡、異常偵測
- 加班記錄與勞基法合規檢查
- 全模型稽核日誌

### 1.2 高階流程

```
[Manager] 建立組織/班別/員工/證照
    ↓
[Supervisor] 設定每位員工的可用時段、優先順位、特殊規則
    ↓
[Supervisor] 觸發「AI 自動排班」
    ↓
[AI Engine] 讀取員工、班別、規則 → OR-Tools 求解 → 寫入 Schedule
    ↓
[Supervisor] 檢視草稿班表、與其他版本比對、修改
    ↓
[Supervisor] 發布並核准（draft → published → approved）
    ↓
[Employee] App 上看自己班表 → 上下班打卡
    ↓
[System] 計算實際工時、偵測異常、產出加班記錄
    ↓
[System] 對排班與出勤進行勞基法合規檢查
```

---

## 2. 技術堆疊

### 2.1 後端

| 元件 | 版本 | 用途 |
|------|------|------|
| Python | 3.11+ | 主要語言 |
| Django | 5.x | Web 框架 |
| Django REST Framework | 3.14 | API 層 |
| PostgreSQL | 16 | 主資料庫（生產環境） |
| SQLite | (in-memory) | 測試資料庫 |
| Redis | 7.x | Celery broker、cache |
| Celery | 5.x | 非同步任務佇列 |
| Google OR-Tools | latest | CP-SAT 求解器 |
| Firebase Admin SDK | latest | JWT 認證 |
| drf-spectacular | latest | OpenAPI/Swagger 文件 |
| pytest + pytest-django | — | 測試 |

### 2.2 建議前端

| 元件 | 版本 | 用途 |
|------|------|------|
| React | 18+ | UI 框架 |
| TypeScript | 5.x | 型別 |
| Vite | latest | Build tool |
| TanStack Query (React Query) | 5.x | API 狀態管理 |
| Firebase JS SDK | latest | 用戶端登入 |
| openapi-typescript-codegen | — | 從 `/api/schema/` 自動產生型別 |
| date-fns / dayjs | — | 時間處理 |
| Tailwind CSS / shadcn-ui | — | 樣式（建議） |
| FullCalendar / react-big-calendar | — | 班表月曆視圖（如需） |

---

## 3. 部署架構

### 3.1 邏輯架構

```
┌─────────────────────────────────────────────────────────────┐
│                      用戶端瀏覽器                              │
│              React SPA (Firebase JS SDK)                    │
└─────────────────────────┬───────────────────────────────────┘
                          │ HTTPS
                          │ Authorization: Bearer <Firebase ID Token>
                          ↓
┌─────────────────────────────────────────────────────────────┐
│                     Reverse Proxy                           │
│                  (nginx / Cloudflare)                       │
└─────────────────────────┬───────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────────┐
│                  Django + DRF (Gunicorn)                    │
│   ┌──────────────────────────────────────────────────────┐  │
│   │ Middleware: CORS → Auth → AuditLogMiddleware        │  │
│   │ Authentication: FirebaseAuthentication              │  │
│   │ Permission: IsAdmin > IsManager > IsSupervisor > … │  │
│   └──────────────────────────────────────────────────────┘  │
└──────┬──────────────────┬───────────────────┬───────────────┘
       │                  │                   │
       ↓                  ↓                   ↓
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│ PostgreSQL  │    │   Redis     │    │  Firebase   │
│ (主資料)    │    │ (Celery     │    │ (Auth verify)│
│             │    │  broker)    │    │             │
└─────────────┘    └──────┬──────┘    └─────────────┘
                          │
                          ↓
                   ┌─────────────┐
                   │  Celery     │
                   │  Worker     │
                   │  (OR-Tools) │
                   └─────────────┘
```

### 3.2 環境設定

| 設定模組 | 用途 | 資料庫 | 認證 |
|---------|------|--------|------|
| `config.settings.development` | 本地開發（manage.py 預設） | dev_db.sqlite3 | Firebase / Token |
| `config.settings.testing` | 自動測試（pytest.ini 指定） | SQLite in-memory | DRF Token |
| `config.settings.production` | 生產 | PostgreSQL | Firebase（強制） |

---

## 4. 資料模型總圖

### 4.1 ER 概覽

```
Organization ─┬─< Branch
              │
              └─< Employee ─┬─< Contract
                            ├─< EmployeeAvailability ─< EmployeeTimeSlot
                            ├─>< Certification (M2M)
                            ├─< Attendance ─< AnomalyRecord
                            ├─< OvertimeRecord
                            └─< Schedule (透過 ScheduleVersion)

ShiftTemplate ─┬─>< Certification (M2M, required_certifications)
               ├─< ShiftEmployeePriority ─> Employee
               └─< Schedule

ScheduleVersion ─< Schedule
                ─< ScheduleChange

User ─> Role
     ─> Organization
     ─> Branch
```

### 4.2 主要模型欄位（精簡）

#### Organization / Branch

```
Organization { id, name, code(unique), address, phone, email, is_active }
Branch       { id, organization_fk, name, code, address, phone, [unique(org, code)] }
```

#### Employee

```
Employee {
  id, user_fk, employee_id(unique), organization_fk, branch_fk,
  position(自由文字), contract_type(full_time|part_time|dispatch),
  agreed_hours_per_week(Decimal, default=40.00),
  hire_date, is_active,
  certifications M2M
}

Contract {
  id, employee_fk, contract_type, start_date, end_date,
  base_salary(Decimal nullable),
  agreed_hours_per_week(Decimal),
  agreed_hours_per_month(Decimal nullable)
}

Certification {
  id, name, code(unique), description, is_required
}

EmployeeAvailability {  # OneToOne with Employee
  id, employee_fk(unique),
  required_hours_per_week(Decimal nullable),  # null = use contract
  special_rules(TextField),  # 給 LLM 用的自然語言
  effective_from(Date nullable),
  effective_to(Date nullable)
}

EmployeeTimeSlot {
  id, availability_fk,
  slot_type(blocked|preferred),
  day_of_week(0-6 nullable, null=每天),
  start_time, end_time, label
}
```

#### Shift

```
ShiftTemplate {
  id, organization_fk, name,
  start_time, end_time,                # 跨午夜：end < start
  break_minutes(default=0),
  overlap_minutes(default=30),         # 交接緩衝（目前未強制）
  min_staff_count(default=1, min=1),
  required_certifications M2M,
  duration_hours (computed property)
}

ShiftRule {
  id, organization_fk, name,
  rule_type(max_consecutive_days|min_rest_hours|max_weekly_hours|mandatory_rest_day),
  value(JSONField)  # 結構未驗證
}

ShiftEmployeePriority {  # 班別 × 員工優先順位（新功能）
  id, shift_template_fk, employee_fk,
  priority_rank(>=1, 1=最高),
  max_extra_shifts(nullable),
  [unique(shift_template, employee)]
}
```

#### Schedule

```
ScheduleVersion {
  id, organization_fk, branch_fk(nullable),
  version_label, version_type(legal|actual),
  period_start, period_end,
  status(draft|published|approved|archived),
  created_by_fk, approved_by_fk(nullable), approved_at(nullable)
}

Schedule {
  id, schedule_version_fk, employee_fk, shift_template_fk,
  schedule_date, expected_hours(Decimal),
  status(draft|assigned|confirmed|completed|cancelled),
  notes,
  [unique(version, employee, date, template)]
}

ScheduleChange {
  id, schedule_version_fk,
  change_type(substitute|split|transfer|cancel|modify),
  original_employee_fk(nullable), replacement_employee_fk(nullable),
  schedule_date, reason
}
```

#### Attendance / Overtime

```
Attendance {
  id, employee_fk, work_date,
  clock_in(DateTime nullable), clock_out(DateTime nullable),
  actual_hours(Decimal nullable),
  is_substitute, substitute_for_fk,
  is_cross_branch, cross_branch_fk,
  anomaly_flag, anomaly_reason,
  [unique(employee, work_date)]
}

AnomalyRecord {
  id, attendance_fk,
  anomaly_type(late|early_leave|no_clock_in|no_clock_out|overtime|mismatch),
  severity(low|medium|high),
  resolved, resolved_by_fk, resolution_notes
}

OvertimeRecord {
  id, employee_fk, overtime_date,
  overtime_type(regular|rest_day|holiday|special_holiday),
  hours(Decimal), multiplier(Decimal default=1.34),
  hourly_rate(nullable), calculated_amount(nullable)  # 不會自動計算
}

OvertimeRule {
  id, organization_fk, overtime_type,
  multiplier(default=1.34),
  max_hours_per_day(nullable), max_hours_per_month(nullable),
  [unique(org, type)]
}
```

#### Compliance / Audit / Accounts

```
LaborLawRule {  # 目前未被引擎讀取，hard-code 在 engine.py
  id, name, rule_type, value(Decimal), description
}

ComplianceCheck {
  id, organization_fk,
  check_type(schedule|attendance), check_period_start, check_period_end,
  status(pass|warning|violation),
  violations(JSON), warnings(JSON),
  checked_by_fk(nullable)
}

AuditLog {
  id, user_fk, action(create|update|delete|approve|...),
  model_name, record_id, content_type_fk,
  old_data, new_data, changes,  # 全為 JSONField
  timestamp, ip_address
}

User (extends AbstractUser) {
  ...AbstractUser fields,
  firebase_uid(unique nullable), role_fk, organization_fk, branch_fk
}

Role {
  id, name(admin|manager|supervisor|employee), description,
  permissions(JSONField)  # 結構未定義
}
```

---

## 5. 核心模組詳解

### 5.1 accounts —— 認證與權限

- `User` 繼承 `AbstractUser`，新增 `firebase_uid`、`role`、`organization`、`branch`。
- `username` 在 Firebase 流程下會被設為 `firebase_uid`（避免 email 衝突）。
- `Role.permissions` 是 `JSONField`，目前**結構未定義**——靠程式約定，前後端要對齊。
- 權限 class 在 `apps/accounts/permissions.py`：
  - `IsAdmin`：`is_superuser` 或 `role.name == 'admin'`
  - `IsManager`：admin 或 manager
  - `IsSupervisor`：admin / manager / supervisor
  - `IsEmployeeOrAbove`：只要登入即可

### 5.2 organizations —— 多租戶基礎

- 所有「業務模型」都帶 `organization_fk`（部分含 `branch_fk`）。
- 多租戶隔離**在 ViewSet `get_queryset()` 過濾**——資料庫層級沒有 row-level security。
- 標準模式：

  ```python
  if not user.is_superuser:
      qs = qs.filter(organization=user.organization)
      if user.branch:
          qs = qs.filter(branch=user.branch)
  ```

### 5.3 employees —— 員工主檔與可用性

- `Employee` 是 User 的延伸（OneToOne）——一個帳號對應一名員工。
- `EmployeeAvailability` 是 **OneToOne**——一名員工只能有一份可用性設定（限制：無法設定時段切割）。
- `EmployeeTimeSlot`：可選 `day_of_week`（0=週一 … 6=週日，null=每天）。
- 可用性更新 API（`PUT /availability/`）採**全量替換 time_slots**——前端送什麼，後端就替換成什麼，不做 diff。

### 5.4 shifts —— 班別與優先順位

- `ShiftTemplate.duration_hours` 是 computed property，會自動扣除 `break_minutes`，並支援跨午夜。
- `ShiftEmployeePriority`（**新功能**）：每個班別維護一個員工優先順位清單，用於加班/額外班分配。
- `ShiftRule` 在資料庫存在但**未被引擎使用**（規則目前 hard-coded）。

### 5.5 schedules —— 班表版本管理

- 雙軌制：
  - `version_type='legal'`：法規版（給政府查的，必須完全合規）
  - `version_type='actual'`：實際版（真實執行情況，可能因臨時調班而違規）
- 狀態機：`draft → published → approved → archived`
- `approve` API 用 `filter(status='draft').update(...)` 達成原子操作（避免重複核准）。
- `compare` API 比對兩個版本，回傳：
  - `only_in_version1` / `only_in_version2`：key 列表
  - `differences`：同一 key 但 `expected_hours` / `status` / `notes` 不同
- key 格式：`"{employee_id}_{schedule_date}_{shift_template_id}"`

### 5.6 attendance —— 出勤打卡

- `clock_in` / `clock_out` 兩個 endpoint，自動寫 Attendance + 偵測異常。
- 異常類型自動偵測（`attendance/views.py:_check_anomalies`）：
  - 5 分鐘寬限期內不算遲到
  - 遲到 >15 分=低、>60 分=高
  - 早退、無打卡、超時 12 小時、無對應排班
- 異常記錄需**人工解除**（呼叫 `/anomalies/{id}/resolve/`）。

### 5.7 overtime —— 加班記錄

- `OvertimeRecord` 採**手動建立**——目前**不會從 Attendance 自動推導加班**。
- `calculated_amount`（金額）需要外部呼叫 `/calculate/` 或手動填。
- 預設倍率 1.34（台灣勞基法第 24 條）。

### 5.8 compliance —— 勞基法合規引擎

- 純 Python 邏輯（`apps/compliance/engine.py`），不依賴 ORM 細節。
- **目前已實作的檢查：**
  - 每週工時上限（max_weekly_hours）
  - 連續工作天數（max_consecutive_days）
  - 兩班間隔（min_rest_hours）—— 已正確處理跨午夜班（`datetime.combine` + `timedelta(days=1)` 當 end < start）
- **規則中定義但未實作的檢查：**
  - 每日工時上限（max_daily_hours）
  - 強制休息日（mandatory_rest_day）

### 5.9 ai_engine —— AI 排班引擎

詳見 [§6](#6-ai-排班引擎詳解)。

### 5.10 audit —— 稽核日誌

- 透過 `AuditLogMiddleware` 把 `request` 存入 thread-local。
- `signals.py` 監聽 `post_save` / `post_delete`，自動寫 AuditLog。
- 排除模型：`audit`、`sessions`、`contenttypes`、`admin`、`auth`、`migrations`。
- 日誌寫入失敗時，呼叫 `logger.error(..., exc_info=True)`，**不**中斷主流程。

---

## 6. AI 排班引擎詳解

### 6.1 架構

```
HTTP POST /api/ai/schedule/generate/
    │
    ├── run_async=true ─→ Celery task (generate_schedule_task)
    │                          │
    └── run_async=false ─→ 同步呼叫
                               ↓
              ┌─────────────────────────────────┐
              │ ai_engine/views.py: 組裝 ScheduleRequest  │
              │  (從 DB 拉 employees, shifts,     │
              │   availability, priorities, …)  │
              └────────────┬────────────────────┘
                           ↓
              ┌─────────────────────────────────┐
              │ get_provider() ← settings.AI_SCHEDULE_PROVIDER │
              │  (預設: ORToolsProvider)         │
              └────────────┬────────────────────┘
                           ↓
              ┌─────────────────────────────────┐
              │ ORToolsProvider.generate_schedule()  │
              │  1. 建立 BoolVar[emp][day][shift]   │
              │  2. 加入 Hard Constraints          │
              │  3. 加入 Soft Constraints (objective)│
              │  4. solver.Solve() (max 300s)     │
              │  5. 解析 assignments              │
              └────────────┬────────────────────┘
                           ↓
              ┌─────────────────────────────────┐
              │ 寫入 ScheduleVersion + Schedule rows │
              └─────────────────────────────────┘
```

### 6.2 Provider 介面（可插拔）

`apps/ai_engine/providers/base.py`：

```python
class BaseScheduleProvider(ABC):
    def generate_schedule(request: ScheduleRequest) -> ScheduleResult: ...
    def optimize_schedule(...): ...
    def check_compliance(...): ...
    def evaluate_change(...): ...
```

切換 provider 只需改 `settings.AI_SCHEDULE_PROVIDER`，不需要動程式碼。未來可擴充 LLM provider。

### 6.3 OR-Tools 約束清單

#### 硬約束（Hard Constraints — 違反就無解）

| # | 約束 | 程式位置 |
|---|------|---------|
| H1 | 每個班別每天必達 `min_staff_count` 人 | `ortools_provider.py:464-466` |
| H2 | 每位員工同一天最多 1 班 | `:469-472` |
| H3 | 員工 `unavailable_dates` 內絕不排 | `:475-481` |
| H4 | 員工須持有班別所有 `required_certifications`（AND） | `:484-494` |
| H5 | 不可與 `blocked` 時段重疊 | `:497-516` |

#### 軟約束（Soft Constraints — Objective 罰分）

| # | 約束 | 罰分公式 |
|---|------|---------|
| S1 | 公平性（每位員工排班數差距） | `10 × (max_shifts − min_shifts)` |
| S2 | 班別偏好分數 | `max(0, 10 − preference_score)` per assignment |
| S3 | 偏好時段（`preferred` slot） | 不在偏好時段內 = `+3` 罰分 |
| S4 | 員工優先順位 rank | rank 1→0、2→3、3→6、4→8、未列名→10 |
| S5 | `max_extra_shifts` 上限 | 超過上限每班 +20 罰分 |
| S6 | 每週工時達標 | 不足每小時 ×5、超過每小時 ×2 |

> 求解器會最小化 objective（罰分總和），最佳解=罰分最小。

### 6.4 ScheduleRequest / ScheduleResult 結構

```python
@dataclass
class ScheduleRequest:
    organization_id: int
    branch_id: Optional[int]
    period_start: date
    period_end: date
    employees: list[dict]      # 含 id, certifications, preferences, availability, priorities
    shifts: list[dict]         # 含 id, times, min_staff_count, employee_priorities
    constraints: dict          # 自訂硬約束
    preferences: dict          # 自訂軟偏好

@dataclass
class ScheduleResult:
    success: bool
    assignments: list[dict]    # {employee_id, shift_template_id, schedule_date, expected_hours}
    score: float | None        # objective 罰分（不可解時=None；序列化時 inf 會轉成 null）
    violations: list[dict]
    metadata: dict             # solver 統計、耗時等
    message: str | None
```

### 6.5 計算工時的 Decimal 慣例

- **所有工時計算用 `Decimal`，不在中間步驟轉 `float`**。
- 跨午夜班：`datetime.combine(date, end_time) + timedelta(days=1) when end < start`。
- ScheduleResultSerializer 用 `SerializerMethodField` 處理 `float('inf')`，序列化時轉 `None`（避免 JSON 編碼錯誤）。

---

## 7. 認證與授權

### 7.1 認證流程（Firebase）

```
[Frontend]
1. firebase.auth().signInWithEmailAndPassword(email, pw)
2. user.getIdToken() ─→ JWT
3. 所有 API 帶 Authorization: Bearer <JWT>

[Backend - FirebaseAuthentication]
4. 從 header 取 token
5. firebase_admin.auth.verify_id_token(token)
6. firebase_uid = decoded['uid']
7. user, _ = User.objects.get_or_create(
     username=firebase_uid,
     defaults={'firebase_uid': firebase_uid, 'email': decoded.get('email'), …}
   )
8. 並行請求建立同一使用者時可能拋 IntegrityError → except 後再 get(firebase_uid=...)
9. return (user, None)
```

> ⚠️ 啟動時若無 Firebase 憑證會直接 raise——**生產環境必須設定** `FIREBASE_CREDENTIALS_PATH` 或 `FIREBASE_CREDENTIALS_JSON`。
> 測試環境（`config.settings.testing`）跳過 Firebase，改用 DRF Token + Session。

### 7.2 授權層級

```
IsAdmin (is_superuser 或 role=admin)
   │
   ├── IsManager (admin / manager)
   │      │
   │      ├── IsSupervisor (admin / manager / supervisor)
   │      │      │
   │      │      └── IsEmployeeOrAbove (任何已登入用戶)
```

### 7.3 多租戶隔離

- 所有非 superuser 的 ViewSet `get_queryset()` 都會過濾 `organization`（與 `branch`）。
- 資料庫**沒有**row-level security，依賴程式紀律。
- 寫入時需驗證 `organization == request.user.organization`，否則惡意客戶端可指定他人組織。

---

## 8. API 設計慣例

### 8.1 通用慣例

| 項目 | 規則 |
|------|------|
| Base URL | `/api/` |
| 認證 | `Authorization: Bearer <Firebase ID Token>` |
| Content-Type | `application/json` |
| 時區 | 所有 DateTime 為 `Asia/Taipei` (UTC+8) |
| 日期格式 | `YYYY-MM-DD` |
| 時間格式 | `HH:MM:SS` |
| Decimal | 字串（避免 float 精度問題），例：`"40.00"` |
| 分頁 | `?page=1&page_size=20`（DRF PageNumberPagination） |
| 過濾 | `?field=value`（每個 ViewSet 不同） |
| 搜尋 | `?search=keyword`（DRF SearchFilter） |
| 排序 | `?ordering=field` 或 `?ordering=-field`（DRF OrderingFilter） |

### 8.2 HTTP 狀態碼

| 碼 | 意義 | 使用情境 |
|----|------|---------|
| 200 | OK | GET / PUT / PATCH 成功 |
| 201 | Created | POST 成功 |
| 202 | Accepted | 非同步任務已接收（AI 排班 async） |
| 204 | No Content | DELETE 成功 |
| 400 | Bad Request | Serializer 驗證失敗 |
| 401 | Unauthorized | 缺 token / token 無效 |
| 403 | Forbidden | 權限不足 |
| 404 | Not Found | 資源不存在或不屬於本組織 |
| 409 | Conflict | 資源衝突（unique 違反） |
| 500 | Internal | 後端錯誤（前端應顯示通用訊息 + 上報） |

### 8.3 錯誤回應格式

DRF 預設：

```json
// 400 - 欄位驗證錯誤
{
  "field_name": ["錯誤訊息 1", "錯誤訊息 2"],
  "another_field": ["錯誤訊息"]
}

// 401 / 403
{ "detail": "Authentication credentials were not provided." }

// 自訂業務錯誤
{ "detail": "排班版本已核准，無法修改" }
```

前端建議統一處理：

```typescript
async function apiCall<T>(promise: Promise<T>): Promise<T> {
  try { return await promise }
  catch (e) {
    if (e.response?.status === 401) { /* refresh token / redirect */ }
    if (e.response?.status === 403) { /* show permission error */ }
    if (e.response?.data?.detail) toast.error(e.response.data.detail)
    else if (e.response?.data) /* render field-level errors */
    throw e
  }
}
```

---

## 9. 完整 API 端點清單

> 所有端點皆需認證；以 `IsXxx` 標示最低權限要求。

### 9.1 認證 (`/api/auth/`)

| Method | Path | 權限 | 用途 |
|--------|------|------|------|
| POST | `/auth/login/` | AllowAny | 開發/測試用 token 登入 |
| GET | `/auth/users/me/` | IsAuthenticated | 取得目前登入者資料 |
| PATCH | `/auth/users/update_profile/` | IsAuthenticated | 自編輯個人資料 |
| GET, POST | `/auth/users/` | IsManager | 使用者列表/建立 |
| GET, PATCH, DELETE | `/auth/users/{id}/` | IsManager | 使用者單筆 CRUD |
| GET | `/auth/roles/` | IsAuthenticated | 角色列表（唯讀） |

### 9.2 組織 (`/api/organizations/`)

| Method | Path | 權限 | 用途 |
|--------|------|------|------|
| CRUD | `/organizations/organizations/` | IsAdmin | 組織管理 |
| CRUD | `/organizations/branches/` | IsManager | 分店管理 |

### 9.3 員工 (`/api/employees/`)

| Method | Path | 權限 | 用途 |
|--------|------|------|------|
| CRUD | `/employees/employees/` | IsSupervisor | 員工 CRUD |
| GET, PUT, PATCH | `/employees/employees/{id}/availability/` | IsSupervisor | 取得/全量替換可用性 |
| POST | `/employees/employees/{id}/availability/time_slots/` | IsSupervisor | 新增單筆時段 |
| DELETE | `/employees/employees/{id}/availability/time_slots/{slot_id}/` | IsSupervisor | 刪除單筆時段 |
| GET | `/employees/employees/{id}/contracts/` | IsSupervisor | 合約列表 |
| POST | `/employees/employees/{id}/add_contract/` | IsSupervisor | 新增合約 |
| GET | `/employees/employees/{id}/certifications/` | IsSupervisor | 證照列表 |
| POST | `/employees/employees/{id}/add_certification/` | IsSupervisor | 加證照 |
| DELETE | `/employees/employees/{id}/remove_certification/` | IsSupervisor | 移除證照 |
| CRUD | `/employees/certifications/` | IsManager | 證照主檔 |
| CRUD | `/employees/contracts/` | IsSupervisor | 合約 CRUD |

### 9.4 班別 (`/api/shifts/`)

| Method | Path | 權限 | 用途 |
|--------|------|------|------|
| CRUD | `/shifts/templates/` | IsSupervisor | ShiftTemplate CRUD |
| GET, PUT | `/shifts/templates/{id}/employee_priorities/` | IsSupervisor | 取得/全量替換員工優先順位（**新功能**） |
| CRUD | `/shifts/rules/` | IsManager | ShiftRule CRUD |

### 9.5 排班 (`/api/schedules/`)

| Method | Path | 權限 | 用途 |
|--------|------|------|------|
| CRUD | `/schedules/versions/` | IsSupervisor | ScheduleVersion CRUD |
| POST | `/schedules/versions/{id}/approve/` | IsSupervisor | 原子核准 |
| POST | `/schedules/versions/{id}/create_dual_versions/` | IsSupervisor | 建立 actual 版副本 |
| GET | `/schedules/versions/{id}/compare/?version2_id={id2}` | IsSupervisor | 版本比對 |
| CRUD | `/schedules/schedules/` | IsSupervisor | Schedule 單筆 CRUD |
| CRUD | `/schedules/changes/` | IsSupervisor | 班表異動記錄 |

### 9.6 出勤 (`/api/attendance/`)

| Method | Path | 權限 | 用途 |
|--------|------|------|------|
| CRUD | `/attendance/attendances/` | IsAuthenticated | 出勤記錄 |
| POST | `/attendance/attendances/clock_in/` | IsAuthenticated | 上班打卡 |
| POST | `/attendance/attendances/clock_out/` | IsAuthenticated | 下班打卡（自動算工時+異常） |
| CRUD | `/attendance/anomalies/` | IsSupervisor | 異常記錄 |
| POST | `/attendance/anomalies/{id}/resolve/` | IsSupervisor | 解除異常 |

### 9.7 加班 (`/api/overtime/`)

| Method | Path | 權限 | 用途 |
|--------|------|------|------|
| CRUD | `/overtime/rules/` | IsManager | 加班規則 CRUD |
| GET | `/overtime/records/` | IsAuthenticated | 加班記錄列表（自動依員工過濾） |
| POST | `/overtime/records/calculate/` | IsAuthenticated | 從出勤手動計算加班 |

### 9.8 合規 (`/api/compliance/`)

| Method | Path | 權限 | 用途 |
|--------|------|------|------|
| CRUD | `/compliance/rules/` | IsManager | 法規規則 CRUD（目前未被引擎讀取） |
| GET | `/compliance/checks/` | IsAuthenticated | 合規檢查列表（唯讀） |
| POST | `/compliance/checks/check_schedule/` | IsAuthenticated | 對排班版本執行合規檢查 |
| POST | `/compliance/checks/check_attendance/` | IsAuthenticated | 對出勤期間執行合規檢查 |

### 9.9 AI 引擎 (`/api/ai/`)

| Method | Path | 權限 | 用途 |
|--------|------|------|------|
| POST | `/ai/schedule/generate/` | IsManager | 產生排班（同步或 `run_async: true` 非同步） |
| POST | `/ai/schedule/optimize/` | IsManager | 優化既有排班版本 |
| POST | `/ai/schedule/check_compliance/` | IsManager | AI 合規檢查 |
| POST | `/ai/schedule/evaluate_change/` | IsManager | 評估提案異動的影響 |

### 9.10 OpenAPI 文件

| Path | 內容 |
|------|------|
| `/api/schema/` | OpenAPI 3 JSON schema |
| `/api/docs/` | Swagger UI |
| `/api/redoc/` | ReDoc UI |

---

## 10. 前端整合指南

### 10.1 開機流程

1. 前端使用者用 Firebase JS SDK 登入。
2. 取得 ID Token，每次請求帶 `Authorization: Bearer <token>`。
3. 第一次呼叫 `GET /auth/users/me/`，後端會：
   - 驗證 token
   - 不存在則 `get_or_create` User
   - 回傳完整 user profile（含 role, organization, branch）
4. 前端依 `role.name` 決定顯示哪些功能。

### 10.2 推薦 API client 產生方式

```bash
# 生產 TypeScript types + client
npx openapi-typescript-codegen \
  --input http://localhost:8000/api/schema/ \
  --output src/api/generated \
  --client axios
```

### 10.3 狀態管理建議

- TanStack Query (React Query) 處理伺服器狀態。
- Mutation 後 `invalidateQueries` 強制重抓相關查詢。
- 範例：

```typescript
const { data: schedules } = useQuery({
  queryKey: ['schedules', { versionId }],
  queryFn: () => api.schedules.list({ version: versionId })
})

const approve = useMutation({
  mutationFn: api.scheduleVersions.approve,
  onSuccess: () => {
    queryClient.invalidateQueries({ queryKey: ['scheduleVersions'] })
  }
})
```

### 10.4 重要型別定義（TypeScript）

```typescript
// User
type Role = 'admin' | 'manager' | 'supervisor' | 'employee'

interface User {
  id: number
  username: string  // = firebase_uid
  email: string
  first_name: string
  last_name: string
  firebase_uid: string
  role: { id: number; name: Role; description: string }
  organization: number | null
  branch: number | null
}

// Schedule
type ScheduleStatus = 'draft' | 'assigned' | 'confirmed' | 'completed' | 'cancelled'
type VersionType = 'legal' | 'actual'
type VersionStatus = 'draft' | 'published' | 'approved' | 'archived'

interface Schedule {
  id: number
  schedule_version: number
  employee: number
  employee_name: string
  shift_template: number
  shift_template_name: string
  schedule_date: string  // YYYY-MM-DD
  expected_hours: string  // Decimal as string
  status: ScheduleStatus
  notes: string
}

// Compare
interface CompareResult {
  version1: ScheduleVersion
  version2: ScheduleVersion
  only_in_version1: string[]
  only_in_version2: string[]
  differences: Array<{
    key: string
    version1: Schedule
    version2: Schedule
  }>
}

// Availability
type SlotType = 'blocked' | 'preferred'

interface EmployeeTimeSlot {
  id: number
  slot_type: SlotType
  slot_type_display: string
  day_of_week: number | null
  day_of_week_display: string
  start_time: string
  end_time: string
  label: string
}

interface EmployeeAvailability {
  id: number
  employee: number
  required_hours_per_week: string | null
  special_rules: string
  effective_from: string | null
  effective_to: string | null
  time_slots: EmployeeTimeSlot[]
}

// AI 排班請求
interface ScheduleRequest {
  organization_id: number
  branch_id?: number | null
  period_start: string
  period_end: string
  employee_ids?: number[]
  shift_template_ids?: number[]
  constraints?: Record<string, unknown>
  preferences?: Record<string, unknown>
  run_async?: boolean
}

interface ScheduleResult {
  success: boolean
  assignments: Array<{
    employee_id: number
    shift_template_id: number
    schedule_date: string
    expected_hours: string
  }>
  score: number | null  // null 表示無解或 inf
  violations: Array<{ type: string; message: string; [k: string]: unknown }>
  metadata: Record<string, unknown>
  message?: string
  task_id?: string  // run_async=true 時才有
}

// ShiftEmployeePriority（新功能）
interface ShiftEmployeePriority {
  id: number
  shift_template: number
  employee: number
  employee_name: string
  priority_rank: number          // 1=最高
  max_extra_shifts: number | null
}
```

### 10.5 常見前端任務的 API 對應

| 前端任務 | 主要 API |
|---------|---------|
| 登入 | Firebase JS + `GET /auth/users/me/` |
| 員工列表 | `GET /employees/employees/?branch=&search=` |
| 編輯員工可用性 | `PUT /employees/{id}/availability/`（全量替換） |
| 設定班別員工優先順位 | `PUT /shifts/templates/{id}/employee_priorities/` |
| 觸發 AI 排班（同步） | `POST /ai/schedule/generate/` |
| 觸發 AI 排班（非同步） | `POST /ai/schedule/generate/` with `run_async: true` |
| 比對版本 | `GET /schedules/versions/{id}/compare/?version2_id={id2}` |
| 核准排班 | `POST /schedules/versions/{id}/approve/` |
| 員工打卡 | `POST /attendance/attendances/clock_in/` / `clock_out/` |
| 異常處理 | `GET /attendance/anomalies/` → `POST /anomalies/{id}/resolve/` |
| 合規檢查 | `POST /compliance/checks/check_schedule/` |
| 評估換班影響 | `POST /ai/schedule/evaluate_change/` |

### 10.6 顯示 rest_hours 等小數的格式工具

```typescript
const formatHours = (h: number) => {
  const hrs = Math.floor(h)
  const min = Math.round((h - hrs) * 60)
  return min > 0 ? `${hrs} 小時 ${min} 分` : `${hrs} 小時`
}
```

### 10.7 顯示使用者識別

`user.username` 是 `firebase_uid`，**不適合**顯示。請改用：

```typescript
const displayName =
  `${user.last_name}${user.first_name}`.trim() ||
  user.email ||
  '未命名使用者'
```

### 10.8 跨午夜班的處理

`ShiftTemplate.start_time > end_time` 表示跨午夜（例：22:00 → 06:00）。前端日曆/甘特圖渲染時：

```typescript
function isCrossMidnight(t: ShiftTemplate) {
  return t.end_time < t.start_time
}

// 跨午夜班的「結束日期」是排班日期 +1
function getEndDate(scheduleDate: string, t: ShiftTemplate) {
  const d = new Date(scheduleDate)
  if (isCrossMidnight(t)) d.setDate(d.getDate() + 1)
  return d
}
```

---

## 11. 關鍵業務流程

### 11.1 AI 排班完整流程

```
[Manager 操作]
1. POST /ai/schedule/generate/
   {
     "organization_id": 1,
     "period_start": "2026-05-01",
     "period_end": "2026-05-31",
     "run_async": true
   }
   ← 後端不需要前端傳 employees / shifts / availability，
     會自動依 organization 載入。

[後端處理]
2. ScheduleRequestSerializer 驗證
3. 自動載入：
   - Active employees in organization
   - Active shift templates
   - 每位員工的 EmployeeAvailability + time_slots
   - 每個班別的 ShiftEmployeePriority
4. 建立 ScheduleVersion (status=draft)
5. 呼叫 ORToolsProvider.generate_schedule(request)
6. solver.Solve()（最多 300 秒）
7. 解析結果 → 寫入 Schedule rows
8. 回傳 task_id（async）或 ScheduleResult（sync）

[Manager 後續操作]
9. GET /ai/tasks/{task_id}/ 輪詢任務狀態（async 模式）
10. GET /schedules/versions/{id}/ 看草稿
11. (可選) 與舊版本比對：GET .../compare/?version2_id=...
12. (可選) 手動修改：POST/PATCH /schedules/schedules/
13. POST /schedules/versions/{id}/approve/ 核准
```

### 11.2 員工打卡流程

```
[Employee 上班]
1. App 取得 GPS / 觸發打卡
2. POST /attendance/attendances/clock_in/
   { "work_date": "2026-05-04" }
3. 後端：
   - 從 request.user 反查 Employee
   - 若已有 Attendance 該日 → 更新；否則建立
   - 設定 clock_in = now()

[Employee 下班]
4. POST /attendance/attendances/clock_out/
5. 後端：
   - 設定 clock_out = now()
   - calculate_hours() 計算 actual_hours
   - _check_anomalies() 檢查：
     a. 比對排班 → 遲到？早退？
     b. 工時 > 12h → overtime anomaly
     c. 無對應排班 → mismatch anomaly
   - 異常 → 建立 AnomalyRecord + 設定 anomaly_flag

[Supervisor 處理異常]
6. GET /attendance/anomalies/?resolved=false
7. POST /attendance/anomalies/{id}/resolve/
   { "resolution_notes": "確認為合理加班" }
```

### 11.3 合規檢查流程

```
[手動觸發]
1. POST /compliance/checks/check_schedule/
   { "schedule_version_id": 42 }
2. 後端 ComplianceEngine.check_schedule_compliance()：
   - 取所有 Schedule 屬於該版本
   - 對每位員工：
     a. 每週工時檢查
     b. 連續工作天數檢查
     c. 兩班間隔檢查（含跨午夜處理）
   - 統整 violations / warnings
3. 建立 ComplianceCheck 記錄
4. 回傳 status: pass / warning / violation
```

> ⚠️ 目前合規檢查**不會**自動在排班發布時觸發——必須手動呼叫。

---

## 12. 資料一致性與並行控制

### 12.1 原子操作（Atomic Update）

排班核准用 `filter().update()` 避免雙重核准：

```python
updated = ScheduleVersion.objects.filter(
    pk=pk, status='draft'
).update(status='approved', approved_by=user, approved_at=now())
if updated == 0:
    return Response({'detail': '已核准或不存在'}, status=400)
```

### 12.2 Bulk Replace 模式

可用性與優先順位用「全量替換」：

```python
@transaction.atomic
def update_priorities(shift_template, items):
    shift_template.employee_priorities.all().delete()
    ShiftEmployeePriority.objects.bulk_create([
        ShiftEmployeePriority(shift_template=shift_template, **i)
        for i in items
    ])
```

前端送什麼，後端就替換成什麼，**不做 diff**。

### 12.3 並行 User 建立

Firebase 認證流程在高並行下可能拋 `IntegrityError`：

```python
try:
    user, _ = User.objects.get_or_create(
        username=firebase_uid, defaults={...}
    )
except IntegrityError:
    user = User.objects.get(firebase_uid=firebase_uid)
```

### 12.4 Decimal vs Float

**禁止**在中間步驟把 Decimal 轉成 float：

```python
# 錯
total = sum(float(s.expected_hours) for s in schedules)

# 對
total = sum((s.expected_hours for s in schedules), Decimal('0'))
```

但合規檢查的 `min_rest_hours` 可以是 `float`（支援 `9.75` 這種值）。

---

## 13. 錯誤處理與例外

### 13.1 序列化器中的 inf/nan

`AI 引擎 ScheduleResult.score` 可能是 `float('inf')`（無解情境），JSON 不允許。`ScheduleResultSerializer` 用 `SerializerMethodField`：

```python
def get_score(self, obj):
    s = obj.score if hasattr(obj, 'score') else obj.get('score')
    if s is None or (isinstance(s, float) and not math.isfinite(s)):
        return None
    return s
```

→ **前端應預期 `score` 可能為 `null`**，代表無解或失敗。

### 13.2 稽核失敗不中斷主流程

```python
# audit/signals.py
try:
    AuditLog.objects.create(...)
except Exception as e:
    logger.error('Audit post_save failed: %s', e, exc_info=True)
    # 不 re-raise
```

### 13.3 跨午夜班的時間計算

```python
current_end_dt = datetime.combine(date, end_time)
if end_time < start_time:
    current_end_dt += timedelta(days=1)  # 跨午夜
```

---

## 14. 效能與擴展

### 14.1 OR-Tools 規模建議

| 規模 | 員工 × 天 × 班別 | 預期求解時間 |
|------|----------------|-------------|
| 小 | 20 × 30 × 3 = 1,800 | < 5 秒 |
| 中 | 50 × 30 × 3 = 4,500 | 10–30 秒 |
| 大 | 100 × 30 × 5 = 15,000 | 1–3 分鐘 |
| 極大 | 200 × 30 × 5 = 30,000 | 5+ 分鐘（可能超時） |

> Solver timeout 預設 300 秒。若超時，會回傳「最佳已知解」或 `success=False`。

### 14.2 N+1 查詢防範

關鍵 ViewSet 已使用 `prefetch_related`：

```python
queryset = ShiftTemplate.objects.prefetch_related(
    'required_certifications', 'employee_priorities'
)
```

### 14.3 索引

關鍵索引（`db_index=True` + `Meta.indexes`）：
- `Schedule.(schedule_date, employee)`
- `Schedule.(schedule_version, schedule_date)`
- `Attendance.(work_date, employee)`
- `OvertimeRecord.(overtime_date, employee)`
- `AuditLog.(model_name, record_id)`, `(user, timestamp)`, `(action, timestamp)`

---

## 15. 監控與稽核

### 15.1 AuditLog 寫入時機

| 事件 | 寫入欄位 |
|------|---------|
| `post_save`（新增） | `action='create'`, `new_data` |
| `post_save`（更新） | `action='update'`, `changes={field: {old, new}, ...}` |
| `post_delete` | `action='delete'`, `old_data` |
| 自訂事件（approve、publish） | view 中手動呼叫 |

### 15.2 可監控的指標

| 指標 | 來源 |
|------|------|
| 排班核准延遲 | `ScheduleVersion.approved_at - created_at` |
| 異常打卡率 | `Attendance.anomaly_flag = True` 比例 |
| 加班時數 | `OvertimeRecord.hours` 加總 |
| 合規違反率 | `ComplianceCheck.status = 'violation'` 比例 |
| AI 排班耗時 | `ScheduleResult.metadata` |

---

## 16. 已知限制與技術債

> 這些是已知議題，若客戶需要請主動討論納入版本規劃。

### 16.1 模型限制

- **EmployeeAvailability OneToOne** → 一名員工無法同時有多份「不同時段」可用性。
- **`Schedule.unique_together(version, employee, date, template)`** → 一日一班，不支援 split shift。
- **ShiftTemplate 無 max_staff_count** → 只有最少人數，無最多人數限制。
- **Certification 無組織範圍** → 全系統共用一份證照清單。
- **Position 自由文字** → 無受控詞彙表。

### 16.2 邏輯未完整實作

- **`max_daily_hours`、`mandatory_rest_day`** → 規則已定義但合規引擎未檢查。
- **`OvertimeRule.max_hours_per_day/month`** → 已定義但無執行機制。
- **`ShiftRule`、`LaborLawRule`** → 模型存在但未被引擎讀取（規則 hard-coded 在程式中）。
- **`EmployeeAvailability.effective_from/to`** → 模型有欄位但 AI 引擎未使用。
- **`ShiftTemplate.overlap_minutes`** → 模型有欄位但無強制邏輯。
- **`OvertimeRecord.calculated_amount`** → 不會自動計算。

### 16.3 未實作功能

- 請假管理（LeaveRequest）
- 國定假日表 / 自動 holiday/special_holiday 倍率
- 通知系統（push、email、SMS）
- 報表 / 匯出（PDF、Excel、CSV）
- GPS / 位置驗證打卡
- 證照到期提醒
- 多語系 i18n（目前全繁中 hard-coded）
- SSO（除 Firebase 外）
- 跨 Branch 員工支援能力清單

### 16.4 安全與部署

- 多租戶隔離**靠程式紀律**——非資料庫層級保護。
- Firebase 憑證**啟動時必須提供**——無 fallback。
- 無自動備份 / 災難恢復腳本。
- 無 rate limiting（DRF throttling 未啟用）。

---

## 附錄 A：程式檔案地圖

```
scheduling-api/
├── apps/
│   ├── accounts/              # User / Role / Firebase 認證
│   ├── ai_engine/             # AI 排班 + OR-Tools provider
│   │   └── providers/
│   │       ├── base.py        # ScheduleRequest / ScheduleResult / BaseProvider
│   │       └── ortools_provider.py  # OR-Tools 求解器
│   ├── attendance/            # 打卡、異常
│   ├── audit/                 # 稽核日誌
│   │   ├── middleware.py
│   │   └── signals.py
│   ├── compliance/            # 勞基法引擎
│   │   └── engine.py
│   ├── employees/             # 員工、合約、可用性
│   ├── organizations/         # 組織、分店
│   ├── overtime/              # 加班
│   ├── schedules/             # 排班版本、單筆排班
│   └── shifts/                # 班別、優先順位
├── config/
│   └── settings/
│       ├── base.py
│       ├── development.py
│       ├── testing.py
│       └── production.py
├── tests/                     # pytest 測試
├── docs/
│   ├── FRONTEND_DEVELOPER_GUIDE.md
│   └── adr/                   # 架構決策記錄
├── CLAUDE.md                  # Claude Code 工作指引
├── FRONTEND_MIGRATION_GUIDE.md
├── CUSTOMER_MEETING_QUESTIONS.md  # ← 本系列另一份
└── ARCHITECTURE_BACKEND_FRONTEND.md  # ← 本檔
```

---

## 附錄 B：常用本地開發指令

```bash
# 安裝
pip install -r requirements/development.txt

# Migration
python manage.py migrate

# 測試資料
python manage.py seed_data

# 啟 API
python manage.py runserver

# 啟 Celery（AI 非同步排班需要）
celery -A config worker -l info

# 跑測試
pytest                         # 全部
pytest tests/test_api.py       # 單檔
pytest --cov                   # 含覆蓋率

# Docker
docker-compose up -d
docker-compose exec web python manage.py migrate
```

---

## 附錄 C：未來擴展建議

如果客戶確認要進一步發展，建議的優先順序：

1. **請假管理模組** —— 排班的最大盲區
2. **每日工時與強制休息日合規檢查** —— 法規完整性
3. **國定假日表 + 自動加班倍率** —— 薪資正確性
4. **加班費自動計算** —— 減少人工
5. **通知系統** —— UX 體驗
6. **報表匯出** —— 管理層需求
7. **LLM 排班 provider** —— 自然語言規則
8. **多語系 i18n** —— 海外擴展

---

> 本架構文件對應 commit `f03e1a2`。如後續結構有重大變動，請同步更新本文件。
