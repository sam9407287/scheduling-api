# 後端修改說明 — 前端對應調整指引

> 最後更新：2026-04-12

---

## 目錄

1. [Firebase 認證 username 變更](#1-firebase-認證)
2. [版本比較 differences 修正](#2-版本比較)
3. [合規性 rest_hours 精度提升](#3-合規性)
4. [新功能：員工可用性 / 時段設定 API](#4-員工可用性時段設定-新功能)
5. [新功能：AI 排班引擎擴充](#5-ai-排班引擎擴充-新功能)

---

## 1. Firebase 認證

`username` 欄位改為儲存 `firebase_uid`，登入流程不受影響，但顯示使用者識別請改用 `email` 或 `first_name + last_name`。

---

## 2. 版本比較

`GET /schedules/versions/{id}/compare/?version2_id={id2}`

`differences` 陣列以前永遠為空（Bug），現在會回傳「同一員工 + 同日期 + 同班別，但 `expected_hours` / `status` / `notes` 不同」的項目。前端 UI 需能正確渲染這個陣列。

```typescript
interface CompareResult {
  version1: ScheduleVersion
  version2: ScheduleVersion
  only_in_version1: string[]
  only_in_version2: string[]
  differences: Array<{ key: string; version1: Schedule; version2: Schedule }>
}
```

---

## 3. 合規性

`rest_hours` / `min_rest_hours` 型別從整數改為浮點數（例如 `9.75`）。建議顯示格式：

```typescript
const formatHours = (h: number) => {
  const hrs = Math.floor(h)
  const min = Math.round((h - hrs) * 60)
  return min > 0 ? `${hrs} 小時 ${min} 分` : `${hrs} 小時`
}
```

---

## 4. 員工可用性/時段設定（新功能）

### 4-1. 功能說明

每位員工可設定：
- **每週所需工時**（不填 = 沿用合約設定）
- **不可排班時段（blocked）**：硬約束，AI 絕對不排
- **優先偏好時段（preferred）**：軟約束，AI 盡量排
- **特殊規則文字**：自然語言，傳給 LLM 排班時作為提示

時段可以設定「套用到特定星期幾」或「每天都套用」，且可以動態新增多筆。

---

### 4-2. TypeScript 型別定義

```typescript
// src/types/employee.ts 新增

export type SlotType = 'blocked' | 'preferred'

export interface EmployeeTimeSlot {
  id: number
  slot_type: SlotType
  slot_type_display: string
  day_of_week: number | null   // 0=週一 … 6=週日，null=每天
  day_of_week_display: string  // '週一' … '週日' | '每天'
  start_time: string           // 'HH:MM'
  end_time: string             // 'HH:MM'
  label: string
  created_at: string
}

export interface EmployeeAvailability {
  id: number
  employee: number
  required_hours_per_week: string | null  // Decimal as string, null = use contract
  special_rules: string
  effective_from: string | null           // 'YYYY-MM-DD'
  effective_to: string | null
  time_slots: EmployeeTimeSlot[]
  created_at: string
  updated_at: string
}

export interface EmployeeAvailabilityUpdateRequest {
  required_hours_per_week?: number | null
  special_rules?: string
  effective_from?: string | null
  effective_to?: string | null
  time_slots?: Array<{
    slot_type: SlotType
    day_of_week: number | null
    start_time: string    // 'HH:MM'
    end_time: string      // 'HH:MM'
    label?: string
  }>
}
```

---

### 4-3. API Endpoints

#### 取得員工可用性設定

```
GET /employees/{id}/availability/
```

**Response 200：**
```json
{
  "id": 1,
  "employee": 42,
  "required_hours_per_week": "32.00",
  "special_rules": "只能排上午班，週三全天不可排",
  "effective_from": null,
  "effective_to": null,
  "time_slots": [
    {
      "id": 1,
      "slot_type": "blocked",
      "slot_type_display": "不可排班",
      "day_of_week": 2,
      "day_of_week_display": "週三",
      "start_time": "00:00",
      "end_time": "23:59",
      "label": "週三全天"
    },
    {
      "id": 2,
      "slot_type": "preferred",
      "slot_type_display": "優先偏好",
      "day_of_week": null,
      "day_of_week_display": "每天",
      "start_time": "08:00",
      "end_time": "16:00",
      "label": "偏好早班"
    }
  ]
}
```

**Response 204：** 員工尚未建立可用性設定（視為無限制）

---

#### 建立 / 完整替換可用性設定

```
PUT /employees/{id}/availability/
```

> **PUT 會整批替換 time_slots**，前端應傳送完整的時段清單（不是 diff）。

**Request Body：**
```json
{
  "required_hours_per_week": 32,
  "special_rules": "只能排上午班",
  "effective_from": null,
  "effective_to": null,
  "time_slots": [
    { "slot_type": "blocked", "day_of_week": 2, "start_time": "00:00", "end_time": "23:59", "label": "週三全天" },
    { "slot_type": "preferred", "day_of_week": null, "start_time": "08:00", "end_time": "16:00", "label": "偏好早班" }
  ]
}
```

---

#### 部分更新（不替換 time_slots 時）

```
PATCH /employees/{id}/availability/
```

只傳需要更新的欄位，**若不傳 `time_slots`，現有時段不會被刪除**。

---

#### 單筆新增時段（前端「+新增」按鈕）

```
POST /employees/{id}/availability/time_slots/
```

**Request Body：**
```json
{
  "slot_type": "blocked",
  "day_of_week": 5,
  "start_time": "18:00",
  "end_time": "22:00",
  "label": "週六晚上家庭時間"
}
```

**Response 201：** 新建立的 `EmployeeTimeSlot` 物件

---

#### 刪除單筆時段

```
DELETE /employees/{id}/availability/time_slots/{slot_id}/
```

**Response 204**

---

### 4-4. 前端 UI 建議

**員工表單 / 詳情頁 - Availability 區塊：**

```
┌─────────────────────────────────────────────────┐
│ 排班可用性設定                                    │
├─────────────────────────────────────────────────┤
│ 每週所需工時  [____] 小時  （留空 = 沿用合約）    │
├─────────────────────────────────────────────────┤
│ 不可排班時段                          [+ 新增]   │
│  ┌──────────┬────────┬─────────┬────────────┐   │
│  │ 星期幾   │ 開始   │ 結束    │ 備註  [刪] │   │
│  │ 每天 ▼  │ 22:00  │ 06:00   │ 夜班      │   │
│  │ 週三 ▼  │ 00:00  │ 23:59   │ 全天      │   │
│  └──────────┴────────┴─────────┴────────────┘   │
├─────────────────────────────────────────────────┤
│ 優先偏好時段                          [+ 新增]   │
│  ┌──────────┬────────┬─────────┬────────────┐   │
│  │ 每天 ▼  │ 08:00  │ 16:00   │ 偏好早班  │   │
│  └──────────┴────────┴─────────┴────────────┘   │
├─────────────────────────────────────────────────┤
│ 特殊規則說明（AI 排班時參考）                    │
│ ┌────────────────────────────────────────────┐  │
│ │ 請盡量安排連續班次，避免同週內班別跳動...   │  │
│ └────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────┤
│ 生效日期範圍：[____] 至 [____]  （留空 = 永久）  │
└─────────────────────────────────────────────────┘
```

**操作邏輯：**
- 頁面載入時 `GET /employees/{id}/availability/`（204 = 空白表單）
- 按「+ 新增」時新增一列到本地 state，**不立即呼叫 API**
- 按「儲存」時以 `PUT` 送出完整設定（含所有時段）
- 按「刪除」icon 只移除本地 state 列（儲存時整批替換）
- 若只需刪除單筆且已儲存：`DELETE /employees/{id}/availability/time_slots/{slot_id}/`

**星期幾下拉選單值對應：**
```typescript
const DAY_OPTIONS = [
  { value: null, label: '每天' },
  { value: 0, label: '週一' },
  { value: 1, label: '週二' },
  { value: 2, label: '週三' },
  { value: 3, label: '週四' },
  { value: 4, label: '週五' },
  { value: 5, label: '週六' },
  { value: 6, label: '週日' },
]
```

---

## 5. AI 排班引擎擴充（新功能）

### 5-1. 非同步參數統一

`POST /ai/generate/` 的 `async` 欄位已重新命名為 **`run_async`**（`async` 是 JavaScript 保留字，使用 `run_async` 避免潛在問題）。

```typescript
// 舊（不再支援）
{ "async": true }

// 新
{ "run_async": true }
```

---

### 5-2. 新端點

| Method | Path | 說明 |
|--------|------|------|
| POST | `/ai/generate/` | 生成排班（已存在，加入 availability 整合） |
| POST | `/ai/optimize/` | 優化現有排班版本 |
| POST | `/ai/check_compliance/` | AI 合規檢查（不再回傳 501） |
| POST | `/ai/evaluate_change/` | 評估排班異動影響（不再回傳 501） |

---

### 5-3. `/ai/generate/` 新增欄位說明

employee 的 `availability` 資料會由後端自動從 DB 載入，**前端不需手動傳入**，只需確保員工在「員工可用性設定」頁面設定正確。

若需手動指定員工不可用日期（例如特定假期），可透過 `constraints` 傳入：

```json
{
  "organization_id": 1,
  "period_start": "2024-01-01",
  "period_end": "2024-01-07",
  "constraints": {
    "employee_unavailability": {
      "42": ["2024-01-03"],
      "43": ["2024-01-05", "2024-01-06"]
    }
  }
}
```

---

### 5-4. `/ai/optimize/` 端點

```
POST /ai/optimize/
```

**Request Body：**
```json
{
  "schedule_version_id": 5,
  "constraints": {
    "max_weekly_hours": 40,
    "min_rest_hours": 11
  }
}
```

**Response 200：** 同 `/ai/generate/` 的 `ScheduleResult` 格式

---

### 5-5. `/ai/check_compliance/` 端點

```
POST /ai/check_compliance/
```

**Request Body：**
```json
{
  "schedule_version_id": 5,
  "constraints": { "max_weekly_hours": 40 }
}
```

**Response 200：**
```json
{
  "is_compliant": false,
  "violations": [
    {
      "type": "weekly_hours_violation",
      "employee_id": 42,
      "week_start": "2024-01-01",
      "total_hours": 48.5,
      "max_hours": 40,
      "message": "員工 42 於 2024-01-01 當週工時 48.5 小時，超過限制 40 小時"
    }
  ],
  "warnings": [],
  "details": {
    "total_assignments": 35,
    "employees_checked": 8
  }
}
```

---

### 5-6. `/ai/evaluate_change/` 端點

```
POST /ai/evaluate_change/
```

**Request Body：**
```json
{
  "schedule_version_id": 5,
  "proposed_change": {
    "type": "substitute",
    "employee_id": 42,
    "date": "2024-01-03",
    "shift_id": 2,
    "new_employee_id": 43
  }
}
```

**`type` 可選值：**
- `substitute`：換人（需傳 `new_employee_id`）
- `cancel`：取消這筆排班
- `modify`：改班別或日期（傳 `new_shift_id` 和/或 `new_date`）

**Response 200：**
```json
{
  "can_apply": false,
  "impact_score": 4.0,
  "violations": [
    {
      "type": "rest_interval_violation",
      "employee_id": 43,
      "message": "員工 43 兩班休息時間 8.5 小時，低於限制 11 小時"
    }
  ],
  "warnings": [],
  "affected_employees": [42, 43]
}
```

**前端建議：** 在「調班確認」對話框送出前呼叫此端點，顯示 `violations` 警告，讓使用者決定是否強制執行。

---

## 變更摘要

| 影響範圍 | 前端調整必要性 | 說明 |
|----------|-------------|------|
| Firebase username | 低 | 改用 email 顯示 |
| versions compare differences | **中** | 現在有資料，UI 需渲染 |
| rest_hours 型別 | 低 | float，需格式化顯示 |
| **員工可用性 API** | **高（新功能）** | 員工表單新增 availability 區塊 |
| AI `async` → `run_async` | **高** | 參數名稱必須改 |
| AI optimize / check_compliance / evaluate_change | **中（新功能）** | 可串接進排班流程 |
