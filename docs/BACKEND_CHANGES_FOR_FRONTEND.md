# 後端變更說明（給前端工程師）

> 更新日期：2026-07-02
> 對應後端 commit：見本次 PR
> TL;DR：**這批後端改動是「修 bug + 補資料隔離」，前端不需要改任何程式碼就能正常運作。**
> 本文件說明「發生了什麼、為什麼、你要不要動、要注意什麼」。

---

## 摘要

| # | 後端改動 | 影響端點 | 前端要改嗎 |
|---|---|---|---|
| 1 | 排班 serializer：`employee` / `shift_template` 從唯讀改為可寫入 ID | `POST/PATCH /api/schedules/schedules/` | ❌ 不用 |
| 2 | 員工 serializer：支援 nested `user` 建立/更新 | `POST/PATCH /api/employees/employees/` | ❌ 不用 |
| 3 | **排班相關端點強制依登入者所屬公司（organization）隔離資料** | `/api/schedules/versions/`、`/schedules/`、`/changes/` | ❌ 不用（但請看下方「§3 注意事項」） |

三項都設計成**向後相容**：回應格式不變，前端現有呼叫方式維持有效。

---

## 1. 排班 serializer：可寫入 ID（修「新增排班失敗」）

### 之前的問題
`ScheduleSerializer` 的 `employee`、`shift_template` 是唯讀 nested 欄位，前端 POST 送出的 ID 會被 DRF 丟棄，寫入 DB 時變成 `NULL` → 500 → 前端顯示「新增失敗 無法新增排班」。

### 現在
兩個欄位改為可寫入的關聯欄位（`PrimaryKeyRelatedField`）。

- **Request（前端送出）**：`employee`、`shift_template` 帶「整數 ID」即可 —— 這正是前端目前的做法，**不用改**。
  ```jsonc
  // POST /api/schedules/schedules/
  {
    "schedule_version": 2,
    "employee": 1,            // ← ID
    "shift_template": 3,      // ← ID
    "schedule_date": "2026-07-02",
    "status": "assigned",
    "expected_hours": 7,      // ← 必填，前端目前已從班別模板 duration_hours 帶入
    "notes": ""
  }
  ```
- **Response（後端回傳）**：仍然是 **nested 物件**（`employee`、`shift_template` 展開為完整物件），格式與過去一致 —— 前端讀取邏輯**不用改**。

### 新增的驗證（可能回 400）
後端會檢查「員工 / 班別必須與排班版本屬於同一間公司」，否則回 400：
```json
{ "employee": "Employee must belong to the schedule version organization." }
{ "shift_template": "Shift template must belong to the schedule version organization." }
```
> 正常情況下前端的下拉選單已經限定同公司，不會踩到；但錯誤提示文字可以顯示給使用者。

---

## 2. 員工 serializer：nested user（修「新增員工失敗」）

### 現在
`POST /api/employees/employees/` 支援兩種帶 user 的方式：

```jsonc
// 方式 A：建立新登入帳號
{
  "user": {
    "username": "emp05",
    "password": "初始密碼",
    "email": "emp05@example.com",
    "first_name": "小明",
    "last_name": "陳"
  },
  "employee_id": "EMP005",
  "organization": 1,
  "branch": 2
  // ...其餘員工欄位
}

// 方式 B：綁定既有 user
{ "user_id": 42, "employee_id": "EMP005", ... }
```

- 建立/更新員工時，`user` 為 `write_only`（送進去用，不回傳原樣）。
- **Response** 仍回傳 nested `user` 物件（`to_representation`），前端讀取不變。
- 更新（`PATCH`）時，`user` 內含的欄位會一併更新到該員工的登入帳號；帶 `password` 才會改密碼。

前端目前的員工表單送法已相容，**不用改**。

---

## 3. 排班端點的公司隔離（重點）

### 之前的問題
`/api/schedules/versions/`、`/schedules/`、`/changes/` 這三個端點**沒有依登入者的公司過濾**——只有在前端主動帶 `?organization=` 時才過濾。也就是說，任何非 admin 的使用者只要不帶該參數，就能撈到**所有公司**的排班資料（跨公司資料外洩）。

> 註：`employees`、`organizations`、`compliance` 等其他模組本來就有隔離，只有 `schedules` 這三個漏了。現已補齊，與全站一致。

### 現在
後端在 `get_queryset` 強制隔離：

- **superuser（平台管理員，例如 `admin` 帳號）**：看得到**所有公司**的資料，可用 `?organization=<id>` 切換檢視特定公司。
- **一般使用者（各公司的 manager / supervisor / employee）**：**只**看得到自己 `organization` 的資料，無論有沒有帶 `?organization=`。
  - 即使手動帶別家公司的 `?organization=`，後端仍會先鎖定自己公司 → 結果為空，無法越權。

### 這對「分帳號登入」的意義
你要的「不同人登入只看到自己公司的排班、admin 除外」**後端已經完成，前端不需要額外邏輯**。原因：

1. 前端登入採 Token 模式，每個請求都帶該帳號的 token；
2. 後端依 token 對應的 `user.organization` 過濾；
3. 前端的公司下拉來自 `useOrganizations()`，而該端點也有隔離 —— 非 admin 只會拿到自己那一間（`SchedulesPage` 已有「只有一間就自動選中」的邏輯），admin 才會看到多間可切換。

### §3 注意事項（前端「不用改，但請確認」）

- ✅ **公司下拉的預期行為**：非 admin 登入後，公司選單只會有一間並自動選中；admin 登入會看到全部公司。這是正確行為，不是 bug。
- ✅ **不要在前端寫死 `organization` ID**。目前 `SchedulesPage` 是從 `useOrganizations()` 動態取得，正確；請維持這個做法，不要硬編任何公司 ID。
- ✅ **切換帳號要清 token / 重新登入**。登出時 `authStore.logout()` 會清掉 `devApiToken`，換帳號登入即取得新 token，資料隔離自動跟著切換。
- ⚠️ 若未來新增「其他讀取排班的頁面 / 報表」，不需要自己做公司過濾——後端已保證。但**也不要依賴前端傳 `organization` 來做安全控管**，那只是檢視用途，真正的邊界在後端。

---

## 分帳號登入：現況與如何本地驗證

前端目前已支援 Token 模式登入（`VITE_AUTH_MODE=token`），可直接做分帳號測試：

1. `.env.local` 設定：
   ```env
   VITE_API_BASE_URL=/api
   VITE_AUTH_MODE=token
   ```
2. 用不同公司的帳號分別登入，觀察排班頁只顯示各自公司的資料。
3. 平台管理員帳號（superuser）登入後，公司下拉可切換檢視所有公司。

> 本地測試帳號（後端 seed）：`admin / admin123`（superuser）。
> 各公司一般帳號請向後端索取，或由後端 seed / 後台建立。

---

## 前端需要做的事

**功能面：無。** 這批後端改動不需要前端改任何程式碼。

建議（非必要）僅為體驗優化：

- [ ] 針對 §1 的 400 驗證訊息（跨公司員工/班別），可在 UI 顯示後端回傳的錯誤文字。
- [ ] 確認登出 → 換帳號登入的流程順暢（token 有正確切換）。
- [ ] （若有）移除任何硬編的 `organization` ID，改用 `useOrganizations()`。

有任何 API 行為對不上，直接找後端確認即可。
