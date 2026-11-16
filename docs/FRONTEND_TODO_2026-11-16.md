# 前端交辦（2026-11-16）— PM 回報「員工要照編號數字排，不是 1、10、11、12、2」

PM 的機構員工編號是 `1`～`14`。員工管理列表與排班表目前排成
`1, 10, 11, 12, 13, 14, 2, 3, 4, 5…`，很難找人；PM 要照數字大小 `1, 2, 3 … 10, 11`。

原因：員工編號是文字欄位，後端原本用字串排序。拆成兩件事：一件後端已修、一件要前端改一行。

## 1. 後端已修（前端不用改）：員工清單預設依編號「自然排序」

- `GET /api/employees/employees/` 沒有帶 `?ordering=` 時，回傳順序改為自然排序：
  數字段照數值比較（`1, 2, 3, 10, 11`；`EMP2` 在 `EMP10` 前面；`EMP001, EMP002, EMP010` 不變），
  文字段不分大小寫。分頁（每頁 20 筆）之間也維持這個順序。
- 有帶 `?ordering=xxx` 時行為不變（照資料庫排序）。
- `GET /api/ai/schedule/rest-quota/` 的 `employees` 也改成同樣順序。
- API 形狀沒有變。**部署後**，下面兩個畫面不用改程式就會是對的，因為它們直接照 API 的順序顯示：
  - 員工管理列表（`EmployeesPage`，還沒點欄位排序時）
  - 排班管理的週排班表（`SchedulesPage`）
- `DataTable` 點「員工編號」欄排序時本來就用 `localeCompare(..., { numeric: true })`，不用動。

## 2. 要改：簽核班表總表的員工排序（`ApprovalScheduleSummaryPage.tsx`）

第 176 行附近自己重排了一次，而且沒有開 numeric，會把後端排好的順序又排回字串順序：

```ts
// 現況
[...(employeesQuery.data?.results ?? [])].sort((a, b) => a.employee_id.localeCompare(b.employee_id))
// 改成（或乾脆拿掉 sort，直接用 API 的順序）
[...(employeesQuery.data?.results ?? [])].sort((a, b) =>
  a.employee_id.localeCompare(b.employee_id, 'zh-TW', { numeric: true }))
```

`lib/scheduleExcelExport.ts` 已經是 `{ numeric: true }`，不用動。

## 3. 其他要自己排序員工的地方

之後若有新的畫面要在前端排序員工，請一律用
`localeCompare(other, 'zh-TW', { numeric: true })`，結果會和後端一致。

## 手機 App

Android／iOS 已同步：除了吃後端的順序，App 端也各自再用同一套規則排一次。
