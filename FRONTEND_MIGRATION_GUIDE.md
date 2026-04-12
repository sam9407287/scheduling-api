# 後端修改說明 — 前端對應調整指引

> 更新日期：2026-04-12  
> 本文件說明後端已修復的 Bug，以及前端可能需要對應調整的部分。

---

## 1. Firebase 認證：`username` 改為使用 `firebase_uid`

### 後端變動

**檔案：** `apps/accounts/authentication.py`

原先建立新使用者時，`username` 欄位使用 `email`（若無 email 才 fallback 到 `firebase_uid`）。  
現在統一改為 **`username = firebase_uid`**，避免因 email 衝突造成 IntegrityError 崩潰。

### 前端需注意

- **登入流程本身不受影響**，前端仍使用 Firebase ID Token (`Authorization: Bearer <token>`) 發請求。
- 若前端有任何地方顯示或依賴 `username` 欄位（例如在 Profile 頁顯示 username），現在會看到 Firebase UID 字串而非 email。
- **建議**：若要顯示用戶識別，改用 `email` 或 `first_name + last_name`，不要依賴 `username`。

---

## 2. 版本比較 API：`differences` 陣列語意修正

### 後端變動

**檔案：** `apps/schedules/views.py`  
**API：** `GET /schedules/versions/{id}/compare/?version2_id={id2}`

**舊邏輯（有 Bug）：** `differences` 永遠為空陣列，因為比較的是已包含在 key 中的欄位。

**新邏輯（已修正）：** `differences` 現在會回傳「兩個版本中相同班表（同員工 + 同日期 + 同班別），但 `expected_hours`、`status` 或 `notes` 不同」的項目。

### Response 格式（無變動）

```typescript
interface CompareResult {
  version1: ScheduleVersion
  version2: ScheduleVersion
  only_in_version1: string[]   // key 格式："{employee_id}_{date}_{shift_template_id}"
  only_in_version2: string[]
  differences: Array<{
    key: string
    version1: Schedule          // 完整 Schedule 物件
    version2: Schedule
  }>
}
```

### 前端需注意

- `only_in_version1` / `only_in_version2` 的格式與語意**不變**。
- `differences` 以前永遠是空陣列；現在修正後**可能有資料**，需確認 UI 能正確顯示。
- 目前前端 `SchedulesPage.tsx` L872 以 `JSON.stringify` 顯示 differences，**建議改為結構化顯示**，例如：

```tsx
{compareResult.differences.map((diff) => (
  <div key={diff.key}>
    <p>班表 key：{diff.key}</p>
    <p>版本1 狀態：{diff.version1.status} / 版本2 狀態：{diff.version2.status}</p>
    <p>版本1 預計工時：{diff.version1.expected_hours} / 版本2：{diff.version2.expected_hours}</p>
  </div>
))}
```

---

## 3. 合規性 API：`rest_hours` 回傳值精度提升

### 後端變動

**檔案：** `apps/compliance/engine.py`

原先跨日休息時間計算只取整數小時（例如 22:30 結束、08:15 開始 → 算成 10 小時，實際為 9.75 小時），現已修正為精確計算（精度到小數點後 2 位）。

### 影響的 API

`POST /compliance/check/` 等合規檢查端點，回傳的 violations 陣列中：

```json
{
  "type": "rest_interval_violation",
  "rest_hours": 9.75,        // 舊版可能回傳整數 10（錯誤值）
  "min_rest_hours": 11.0,    // 型別從 int 改為 float
  ...
}
```

### 前端需注意

- `rest_hours` 和 `min_rest_hours` 現在可能為 **浮點數**（例如 `9.75`）。
- 若前端有顯示這些數值，建議格式化為「X 小時 Y 分」或保留 1 位小數，例如：

```typescript
// 推薦顯示方式
const formatHours = (h: number) => {
  const hours = Math.floor(h)
  const minutes = Math.round((h - hours) * 60)
  return minutes > 0 ? `${hours} 小時 ${minutes} 分` : `${hours} 小時`
}
```

- 對應的 TypeScript 型別若有定義 `min_rest_hours: number`，**不需要修改**（`number` 涵蓋整數與浮點數）。

---

## 摘要

| 影響範圍 | 前端調整必要性 | 說明 |
|----------|-------------|------|
| Firebase 登入 | 低（若未用 username 顯示） | username 欄位內容改變 |
| 版本比較 `differences` | **中** | 以前永遠空，現在有資料，UI 需能顯示 |
| 合規性 `rest_hours` | 低 | 型別從 int 改 float，需注意顯示格式 |
