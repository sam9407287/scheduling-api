# 前端回饋（2026-10-26）— 針對 `FRONTEND_HANDOFF_2026-10-22.md` 的驗收結果

後端已在本機（Django dev server + Vite）把交接文件的五組功能用 Playwright 全部走過一遍，
API 36 項、UI 22 項皆通過。只有一處文案要改，另有兩點是後端這邊補的、前端不用動。

## 1. 要改：`kind: makeup` 不是「補班日」，是「補假日」（放假）

`GET /api/compliance/holidays/calendar/` 的 `kind`：

| kind | 意思 | 例 |
|---|---|---|
| `holiday` | 節日本日 | 2026-10-10 國慶日 |
| `makeup` | **補假日**（節日逢週六／日依「紀念日及節日實施條例」補放假的那天，**也是放假**） | 2026-10-09 國慶日補假、2026-10-26 光復節補假 |

目前 `SchedulesPage.tsx` 對 `makeup` 顯示藍底並在名稱後加「（補班日）」，畫面會出現
「國慶日補假（補班日）」，語意相反（補班日 = 要上班）。自 115 年起條例已取消彈性調整放假／補班，
內建日曆表裡**沒有任何需要上班的補班日**。

建議：
- 去掉「（補班日）」後綴，直接顯示後端 `name`（名稱已含「補假」）。
- 底色可與 `holiday` 同用假日色，或保留藍色區分「補假」，但 tooltip／文案寫「補假日（放假）」。
- 合規檢查對 `makeup` 日排班一樣會回 soft `holiday_scheduling`（後端把兩種 kind 都當假日），前端呈現不用分流。

交接文件 §4「`kind: makeup`：藍色底色、名稱後加『補班日』」請一併更正。

## 2. 後端已補（前端不用改）：代建／修改出勤後的工時與異常重算

交接文件 §2 假設「`actual_hours`、`anomaly_flag`、`anomaly_reason` 不由前端送值；後端會重新計算」。
10/22 的後端其實只在員工自己 `clock-out/` 時重算，管理者 `POST /api/attendance/attendances/`
與 `PATCH` 存進去就是 `actual_hours: null`、沒有異常判定。2026-10-26 已修：

- `POST` / `PATCH` 只要 `clock_in` 與 `clock_out` 齊全，後端就重算 `actual_hours`，
  並重跑異常檢查（超過 12 小時、遲到、早退、無排班出勤），舊的未解決異常紀錄會先清掉再重建。
- `anomaly_flag` / `anomaly_reason` 改為 read-only；前端就算送了也會被忽略。
- `actual_hours` 仍可寫（舊介面相容），但 clock 時間齊全時一律以後端計算值覆蓋。

前端目前的 payload（`employee_id / work_date / clock_in / clock_out / notes`）完全正確，
建立後列表顯示的工時與異常就會是後端算的。

## 3. 後端已補（前端不用改）：最低人力 `organization` 對 superuser 的預設

`POST /api/shifts/staffing-requirements/` 原本只對非 superuser 自動帶入機構；superuser
（例如本機 `admin`）沒傳 `organization` 會 400。現在 superuser 綁有機構時也自動帶入；
只有「superuser 且沒綁機構」才必須明傳。前端現行 `organization: user.organization || undefined` 不用動。

## 4. 回歸確認過的項目（供對照）

- 註冊：取得驗證碼 → 倒數 → 開發模式顯示 `debug_code` → 密碼不一致擋下 → 建立帳號 → 導向機構 onboarding；
  `/api/auth/login/` 以 `{email, password}` 登入成功；重送 429 帶 `retry_after_seconds`。
- 代建出勤：對話框選員工、跨時段 08:00–21:30 → 後端 13.50h、標「超時工作」；PATCH / DELETE 正常。
- 最低人力：max < min 時「儲存」停用；每天／每週固定日／特定日期三種都能建；列表顯示機構、全日總人數。
- 班表：切到 10/05–10/11 那週欄頭顯示「國慶日補假」「國慶日」；合規檢查後欄頭出現
  「全日 n/2」與各班別「早班 n/3」等覆蓋徽章（已達標／人力不足／超過上限）。
- 合規規則強度：五條規則按鈕都在，點「每七日休息日」硬性↔軟性切換並寫回 `soft_rule_types`。
- `npm run build`、`tsc --noEmit` 通過；瀏覽器 console 無錯誤。

## 5. 正式環境備忘（Sam）

Railway 尚未設 SMTP 變數，線上「取得驗證碼」會回 200 但沒有信。設定步驟見
`docs/deployment-plan.md` 最後一節「Email（註冊驗證碼）SMTP 設定」。
