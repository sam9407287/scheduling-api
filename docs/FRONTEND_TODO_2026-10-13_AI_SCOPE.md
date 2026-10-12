# 前端交辦：AI 排班「有人沒排到」修正 + PM 10/13 回饋（2026-10-13）

> 給 Frank。第一部分（分店範圍）是最優先的一項，做完 PM 回報的「AI 漏排」就會消失；
> 第二部分是 PM 10/13 截圖回饋，後端能做的都已做完，剩前端。
> 後端已完成（commit `bc2bf94`，已 push；生產部署時間以 Sam 通知為準），API 契約見 [FRONTEND_API.md §5.1](./FRONTEND_API.md)。
> 本地開發照舊：後端 `python manage.py runserver 8000`（SQLite），登入 `admin` / `admin123`。
> 9/23 交辦的 5 項已驗收完成，不用再動。

## 根因（一句話）

版本綁了分店時，後端只把**該分店**的員工餵給模型；但週排班表的列是依**頁面的分店下拉**篩選，
預設「全部分店」就會列出全機構的人，其他分店的人永遠是空白列，看起來像 AI 漏排。
模型本身實測 10/10 全排、驗證層 0 退回，問題在範圍顯示不一致。

後端現在會把範圍回傳出來（`scope`）並在 `warnings[0]` 用中文點名被排除的人，前端只要「呈現」即可。

## 要改的 4 點（都在 `src/pages/schedules/SchedulesPage.tsx`）

### 1. 接新的 `scope` 欄位（型別）

`src/types/ai.ts` 的 `LLMScheduleResult` 加：

```ts
scope: {
  branch_id: number | null
  branch_name: string | null
  employees_in_scope: number[]
  employees_out_of_scope: number[]   // 版本不限分店時為空陣列
}
```

### 2. 表格列依「版本的分店」鎖定，不是依頁面下拉（核心修正）

現況：`employees` 來自 `useEmployees({ branch: branchId === 'all' ? undefined : Number(branchId) })`，
`branchId` 是頁面上方的分店下拉，與 `selectedVersion.branch` 無關。

要改成：

- 選到 `selectedVersion.branch !== null` 的版本時，`useEmployees` 的 `branch` 改用 `selectedVersion.branch`，
  並把頁面分店下拉自動切到該分店（或 disable 下拉，顯示「此版本僅排 {branch_name}」）。
- 表格上方加一條橫幅：`此版本僅排「{selectedVersion.branch_name}」的員工，其他分店員工不會出現在此版本。`
  （`branch_name` 版本物件本來就有，`src/types/schedule.ts` 第 9 行。）
- `selectedVersion.branch === null` 時維持現狀（全機構）。

驗收：建一個綁「中山分店」的版本 → 表格只列中山分店員工，橫幅出現；切到不限分店的版本 → 橫幅消失、全機構列出。

### 3. 新增版本對話框的分店欄位

現況已經有「分店（可選）」下拉與「不指定」選項，但預設值會默默繼承頁面篩選
（`openVersionDialog` 裡 `branch: selectedVersion?.branch ?? branchId`），使用者不一定注意到。

要改：

- 選項文字「不指定」改成「不限分店（全機構）」，讓語意清楚。
- 預設值改成空字串（不限分店）；若頁面有篩選分店，用 placeholder 或小字提示「目前頁面篩選：{branch}」即可，不要自動帶入。
- 下拉旁加一行說明：「綁定分店後，AI 排班只會排該分店員工。」

### 4. `warnings` 與 `rejected` 的呈現

現況 `llmResult` 對話框已經會列 `warnings` 與 `rejected`，只需確認：

- `warnings[0]` 可能很長（列出所有被排除員工的姓名＋工號），容器請用 `whitespace-pre-wrap break-words`，不要截斷、不要單行省略。
- `warnings` 順序固定：分店範圍 → 範圍內 0 班的人 → min_staff 缺口。第一條建議用不同底色（例如 amber）突顯。
- `rejected[].reason` 新增「連續工作超過 6 天（七休一）」，照既有方式顯示即可。
- 有 `scope.employees_out_of_scope.length > 0` 時，在成功對話框加一個按鈕「建立不限分店的版本」直接開啟新增版本對話框（可選，但很省客服）。

## API 回應範例（201）

```jsonc
{
  "created_count": 34, "rejected_count": 2,
  "assignments": [{ "id": 90, "employee_id": 12, "date": "2026-10-01", "shift_id": 3 }],
  "rejected": [{ "row": {...}, "reason": "連續工作超過 6 天（七休一）" }],
  "warnings": [
    "此版本只排分店「中山分店」：小明 陳（EMP001）、…屬於其他分店，未納入 AI 排班（要全機構一起排請建立不限分店的版本）",
    "小威 許（EMP009） 在此期間沒有被排到任何班次",
    "2026-10-03 早班 只排到 0/1 人"
  ],
  "scope": { "branch_id": 4, "branch_name": "中山分店",
             "employees_in_scope": [4, 8],
             "employees_out_of_scope": [1, 2, 3, 5, 6, 7, 9, 10] },
  "model": "gemini-3.6-flash", "engine": "llm",
  "billing": { "billing_mode": "generate", "tokens_charged": 10 }
}
```

錯誤碼不變：409 `schedule_version_locked`、402 額度、502 `llm_call_failed`（重按即可）、503 `llm_not_configured`。
呼叫最長 150 秒，axios 不要設 timeout（目前沒設，保持）。

## 驗收腳本（本地）

1. 員工頁確認至少兩個分店各有員工。
2. 新增版本 A 綁分店 X → 表格只列 X 的人，橫幅出現 → 按 AI 排班 → 成功對話框 `warnings[0]` 點名其他分店員工，`scope.employees_out_of_scope` 非空。
3. 新增版本 B 不限分店 → 表格列全機構，無橫幅 → 按 AI 排班 → `scope.branch_id` 為 null、`employees_out_of_scope` 為空、每個人都有班。
4. 把頁面分店下拉切來切去，表格列數在版本 A 不應改變。

---

# 第二部分：PM 10/13 回饋（後端已完成，剩前端）

## B1. BUG：系統設定「員工優先順序」404、上排 tab 列消失（最優先修，很小）

根因：`src/App.tsx` 有兩組路由，正式環境走 `ProtectedRoute` 那組，裡面的 `/settings`
子路由**漏了 `priorities`**（本地那組有），所以點 tab 掉到全站 `*` 404；404 元件在
`SettingsPage` 之外，tab 列自然不見。

要改：

1. `ProtectedRoute` 那組 `/settings` 加 `<Route path="priorities" element={<EmployeePrioritiesPage />} />`。
2. 兩組 `/settings` 都加一個 `<Route path="*" element={<PlaceholderPage title="找不到此設定頁" />} />`，
   讓設定頁內的 404 仍保留上排 tab（PM 要求「保留上排選項」）。
3. 順手把兩組路由抽成同一個 `settingsRoutes` 常數，以後不會再漏。

驗收：正式環境登入 → 系統設定 → 點「員工優先順序」看得到頁面；手打 `/settings/xxx` 仍有 tab 列。

## B2. AI 排班對話框加「允許同一人同一天排不同崗位的班」checkbox

後端：`POST /api/ai/schedule/llm-generate/` 新參數 `allow_multiple_shifts_per_day`（boolean，**預設 false**＝一天一班）。
回應多 `options: { allow_multiple_shifts_per_day }` 回顯。

要改（`SchedulesPage.tsx` 的 AI 排班對話框）：

- 期間選擇下方加 checkbox「允許同一人同一天排不同崗位的班（分段班）」，預設不勾。
- 送出時帶 `allow_multiple_shifts_per_day: checked`；`LLMScheduleRequest` 型別加此欄位（`src/types/ai.ts`）。
- 不勾時模型若仍輸出同日第二班，會進 `rejected`，reason「同一天已有班次（本次設定為一天一班）」，照既有方式顯示即可。
- 可選：checkbox 的勾選狀態存 localStorage，同機構下次沿用。

## B3. 員工「薪制」欄位 + 國定假日加給顯示

後端：`Employee` 新欄位 `pay_type`：`monthly`（月薪制，預設）| `hourly`（時薪制），
`GET/PATCH /api/employees/employees/{id}/` 讀寫，回應另有 `pay_type_display`；列表 API 也帶 `pay_type`。

業務規則（PM）：月薪制國定假日出勤**沒有雙倍薪**、以補休為原則、無法補休才折發；時薪制才加倍。
後端 `POST /api/overtime/records/calculate/` 已照此分流（細節見 FRONTEND_API.md §8）。

要改：

1. 員工新增／編輯表單加「薪制」下拉（月薪制／時薪制），`src/types/employee.ts` 加 `pay_type`、`pay_type_display`。
2. 員工列表加一欄「薪制」（可選）。
3. 加班紀錄頁：`overtime_type === 'holiday'` 的列顯示 `notes`（月薪制會寫「以補休為原則…請手動調整倍率」），
   並允許編輯 `multiplier`（`PATCH /api/overtime/records/{id}/`），讓管理者決定折發時改成 2.00。

## B4. 「假別」優化 — 待 PM 釐清，先不要動

後端現況：10 種假別（特休／病假／事假／生理假／婚假／喪假／產假／陪產假／公假／其他）、
機構可設各假別年度額度（`/api/leaves/settings/`）、小時制請假、餘額查詢（`/api/leaves/balances/`）。
前端已有請假設定頁與請假管理頁。PM 只寫「假別」兩個字，可能是：
(a) 要自訂假別名稱、(b) 班表格子要顯示是哪種假、(c) 額度設定 UI 不好找。
Sam 會跟 PM 確認後再派工，這項先不做。

---

有 API 問題直接找 Sam。
