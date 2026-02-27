# AI 排班系統 - 前端開發者指南

本文檔提供給前端工程師，說明如何串接 AI 排班系統的後端 API。

## 目錄

1. [快速開始](#快速開始)
2. [認證流程](#認證流程)
3. [API 端點總覽](#api-端點總覽)
4. [核心業務流程](#核心業務流程)
5. [建議的前端架構](#建議的前端架構)
6. [錯誤處理](#錯誤處理)

---

## 快速開始

### API Base URL

- **開發環境**: `http://localhost:8000/api`
- **測試環境**: `https://your-test-domain.com/api`
- **正式環境**: `https://your-production-domain.com/api`

### 取得 OpenAPI 規格

後端會自動產生 OpenAPI 3.0 規格文件：

- **Swagger UI**: `http://localhost:8000/api/docs/`
- **ReDoc**: `http://localhost:8000/api/redoc/`
- **OpenAPI JSON**: `http://localhost:8000/api/schema/`

### 產生 TypeScript API Client

使用 `openapi-typescript-codegen` 自動產生型別安全的 API Client：

```bash
npm install openapi-typescript-codegen

# 從 OpenAPI spec 產生 TypeScript 程式碼
npx openapi-typescript-codegen \
  --input http://localhost:8000/api/schema/ \
  --output ./src/api/generated \
  --client axios
```

或在 `package.json` 中加入 script：

```json
{
  "scripts": {
    "generate-api": "openapi-typescript-codegen --input http://localhost:8000/api/schema/ --output ./src/api/generated --client axios"
  }
}
```

---

## 認證流程

### 1. Firebase Auth 登入

使用 Firebase Auth 進行使用者登入：

```typescript
import { getAuth, signInWithEmailAndPassword } from 'firebase/auth';

const auth = getAuth();
const userCredential = await signInWithEmailAndPassword(auth, email, password);
const idToken = await userCredential.user.getIdToken();
```

### 2. 將 Token 加入 API 請求

所有 API 請求都需要在 Header 中帶入 Firebase ID Token：

```typescript
import axios from 'axios';

const apiClient = axios.create({
  baseURL: process.env.REACT_APP_API_BASE_URL,
});

// 請求攔截器：自動加入 Token
apiClient.interceptors.request.use(async (config) => {
  const auth = getAuth();
  const user = auth.currentUser;
  if (user) {
    const token = await user.getIdToken();
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});
```

### 3. Token 刷新

Firebase Token 會自動刷新，但建議在回應攔截器中處理 401 錯誤：

```typescript
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401) {
      // Token 過期，重新取得
      const auth = getAuth();
      const user = auth.currentUser;
      if (user) {
        const newToken = await user.getIdToken(true); // 強制刷新
        error.config.headers.Authorization = `Bearer ${newToken}`;
        return apiClient.request(error.config);
      }
    }
    return Promise.reject(error);
  }
);
```

### 4. 角色與權限

後端會根據使用者的 `role` 決定權限：

- **admin**: 系統管理員，可存取所有功能
- **manager**: 管理者，可管理組織內所有資料
- **supervisor**: 主管，可查看和管理排班、出勤
- **employee**: 員工，只能查看自己的資料和打卡

---

## API 端點總覽

### 認證與使用者

- `GET /api/auth/users/me/` - 取得當前使用者資料
- `PATCH /api/auth/users/update_profile/` - 更新個人資料
- `GET /api/auth/users/` - 取得使用者列表（需 manager 權限）
- `GET /api/auth/roles/` - 取得角色列表

### 組織與分店

- `GET /api/organizations/organizations/` - 取得機構列表
- `GET /api/organizations/branches/` - 取得分店列表
- `GET /api/organizations/branches/?organization={id}` - 取得特定機構的分店

### 員工管理

- `GET /api/employees/employees/` - 取得員工列表
- `POST /api/employees/employees/` - 建立員工
- `GET /api/employees/employees/{id}/` - 取得員工詳情
- `PATCH /api/employees/employees/{id}/` - 更新員工
- `GET /api/employees/employees/{id}/contracts/` - 取得員工契約
- `POST /api/employees/employees/{id}/add_contract/` - 新增契約
- `GET /api/employees/employees/{id}/certifications/` - 取得員工證照
- `POST /api/employees/employees/{id}/add_certification/` - 新增證照

### 班別管理

- `GET /api/shifts/templates/` - 取得班別模板列表
- `POST /api/shifts/templates/` - 建立班別模板
- `GET /api/shifts/rules/` - 取得排班規則列表

### 排班管理

- `GET /api/schedules/versions/` - 取得排班版本列表
- `POST /api/schedules/versions/` - 建立排班版本
- `GET /api/schedules/versions/{id}/` - 取得排班版本詳情
- `POST /api/schedules/versions/{id}/approve/` - 簽核排班版本
- `POST /api/schedules/versions/{id}/create_dual_versions/` - 建立雙軌版本
- `GET /api/schedules/versions/{id}/compare/?version2_id={id}` - 比對兩個版本差異
- `GET /api/schedules/schedules/` - 取得排班列表
- `POST /api/schedules/schedules/` - 建立排班
- `GET /api/schedules/changes/` - 取得排班異動記錄

### AI 排班

- `POST /api/ai/schedule/generate/` - 產生排班表（同步）
- `POST /api/ai/schedule/generate/?async=true` - 產生排班表（非同步）

**請求範例**：

```json
{
  "organization_id": 1,
  "branch_id": 1,
  "period_start": "2026-03-01",
  "period_end": "2026-03-31",
  "employee_ids": [1, 2, 3],
  "shift_template_ids": [1, 2],
  "constraints": {},
  "preferences": {}
}
```

### 出勤打卡

- `GET /api/attendance/attendances/` - 取得出勤記錄列表
- `POST /api/attendance/attendances/clock_in/` - 上班打卡
- `POST /api/attendance/attendances/clock_out/` - 下班打卡
- `GET /api/attendance/anomalies/` - 取得異常記錄列表

### 加班管理

- `GET /api/overtime/records/` - 取得加班記錄列表
- `POST /api/overtime/records/calculate/` - 計算加班時數

### 合規檢查

- `GET /api/compliance/checks/` - 取得合規檢查記錄
- `POST /api/compliance/checks/check_schedule/` - 檢查排班合規性
- `POST /api/compliance/checks/check_attendance/` - 檢查出勤合規性

---

## 核心業務流程

### 排班流程

1. **發起排班請求**
   ```
   POST /api/ai/schedule/generate/
   ```

2. **AI 產生排班表**（同步或非同步）
   - 同步：直接回傳結果
   - 非同步：回傳 `task_id`，需輪詢結果

3. **建立排班版本**
   ```
   POST /api/schedules/versions/
   {
     "organization": 1,
     "version_label": "2026年3月排班",
     "version_type": "legal",
     "period_start": "2026-03-01",
     "period_end": "2026-03-31"
   }
   ```

4. **將 AI 產生的排班加入版本**
   ```
   POST /api/schedules/schedules/
   ```

5. **主管簽核**
   ```
   POST /api/schedules/versions/{id}/approve/
   ```

6. **建立實際版**
   ```
   POST /api/schedules/versions/{id}/create_dual_versions/
   ```

### 打卡流程

1. **上班打卡**
   ```
   POST /api/attendance/attendances/clock_in/
   ```

2. **下班打卡**
   ```
   POST /api/attendance/attendances/clock_out/
   ```

3. **系統自動計算工時和加班**

4. **如有異常，系統自動標記**

### 代班/拆班流程

1. **建立排班異動**
   ```
   POST /api/schedules/changes/
   {
     "schedule": 123,
     "change_type": "substitute",
     "replacement_employee": 456,
     "reason": "原員工請假"
   }
   ```

2. **更新實際版排班**

3. **系統自動記錄差異**

### 雙軌差異比對

```
GET /api/schedules/versions/{legal_id}/compare/?version2_id={actual_id}
```

回傳兩個版本的差異，包括：
- 只在法規版存在的排班
- 只在實際版存在的排班
- 兩個版本都有的排班但內容不同

---

## 建議的前端架構

### 目錄結構

```
src/
├── api/
│   ├── generated/          # 自動產生的 API Client
│   └── client.ts          # Axios instance + interceptors
├── components/
│   ├── layout/            # AppLayout, Sidebar, Header
│   ├── schedule/          # CalendarView, GanttView, ShiftCard
│   ├── employee/          # EmployeeTable, EmployeeForm
│   ├── attendance/        # AttendanceTable, ClockInWidget
│   └── common/            # Button, Modal, Table, Form
├── pages/
│   ├── Dashboard/
│   ├── Schedule/
│   ├── Employees/
│   ├── Attendance/
│   └── ...
├── hooks/
│   ├── useAuth.ts
│   ├── useSchedule.ts
│   └── useEmployee.ts
├── store/                 # Zustand stores
└── utils/
```

### 建議使用的套件

- **React Router**: 路由管理
- **Zustand**: 狀態管理（輕量）
- **React Query / SWR**: 資料獲取與快取
- **React Big Calendar / FullCalendar**: 排班日曆
- **Ant Design / MUI**: UI 元件庫（可選）

### 建議的頁面清單

1. **登入頁** (`/login`)
2. **Dashboard** (`/dashboard`) - 營運總覽
3. **排班管理** (`/schedules`)
   - 排班日曆視圖
   - 排班列表視圖
   - AI 排班產生器
   - 雙軌差異比對
4. **員工管理** (`/employees`)
5. **出勤管理** (`/attendance`)
6. **加班管理** (`/overtime`)
7. **合規檢查** (`/compliance`)
8. **系統設定** (`/settings`)

---

## 錯誤處理

### 標準錯誤回應格式

```json
{
  "error": "錯誤訊息",
  "detail": "詳細說明",
  "code": "ERROR_CODE"
}
```

### 常見錯誤碼

- `400`: 請求參數錯誤
- `401`: 未授權（Token 無效或過期）
- `403`: 權限不足
- `404`: 資源不存在
- `500`: 伺服器錯誤

### 錯誤處理範例

```typescript
try {
  const response = await apiClient.post('/api/schedules/versions/', data);
  return response.data;
} catch (error) {
  if (error.response?.status === 401) {
    // Token 過期，重新登入
    await handleTokenRefresh();
  } else if (error.response?.status === 403) {
    // 權限不足
    showError('您沒有權限執行此操作');
  } else {
    // 其他錯誤
    showError(error.response?.data?.error || '發生錯誤');
  }
  throw error;
}
```

---

## 其他資源

- **Swagger UI**: 互動式 API 文件
- **Postman Collection**: 匯出後可在 Postman 中使用
- **OpenAPI Spec**: 機器可讀的 API 規格

如有問題，請聯繫後端工程師。
