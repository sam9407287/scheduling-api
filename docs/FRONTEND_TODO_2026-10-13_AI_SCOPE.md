# 前端交辦：AI 排班「有人沒排到」修正（2026-10-13）

> 給 Frank。這是目前最優先的一項，做完 PM 回報的「AI 漏排」就會消失。
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

有 API 問題直接找 Sam。
