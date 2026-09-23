# 前端交辦清單（2026-09-23）

> 後端全部完成並已部署 Railway 生產。以下五項都只剩前端 UI，
> API 契約在 [FRONTEND_API.md](./FRONTEND_API.md)（章節號標在各項）。
> 本地開發照舊：後端 `python manage.py runserver 8000`（SQLite），
> 登入 `admin` / `admin123`。

## 1. AI 排班請求按鈕（§5.1）— 最優先

- 班表頁加「AI 排班」按鈕 → `POST /api/ai/schedule/llm-generate/`
  帶 `{ "schedule_version": <id> }`（可選 `period_start`/`period_end` 縮小範圍）。
- **一定要做 loading 狀態**：模型要跑 10–60 秒。
- 成功（201）後 refetch 該版本的 schedules，格子就會出現（`notes` 標「AI 排班」）。
- 錯誤處理：409 已簽核鎖定、402 額度不足、502 `llm_call_failed`
  （免費模型偶爾高峰塞車，提示使用者重按一次即可）、503 未設定 key。
- 回應裡的 `warnings`（人力缺口）與 `rejected`（被擋掉的列）建議顯示出來。

## 2. 建立機構 onboarding 頁（§1 + Google login 段落）

- **Google 登入不再自動開機構**：登入後打 `GET /api/auth/users/me/`，
  `organization` 為 `null` → 導到「建立機構」頁。
- `POST /api/organizations/organizations/` body 只需 `{ "name": "..." }`
  （`code` 可省略，後端自動產生）→ 建立後自動綁定 → refetch `/me` 進系統。
- 已有機構的帳號再建會收 409 `organization_already_exists` → 請隱藏「新增機構」入口。
- 機構列表現在只回自己那間（admin 例外），別人的機構 404。

## 3. 機構公休日設定 + 班表灰底（§6）— PM#1 排休

- 設定頁：七顆星期 checkbox 讀寫 `GET/PATCH /api/compliance/settings/`
  的 `weekly_closed_days`（**0=週一 … 6=週日**，例：週日公休 `[6]`）。
- 班表：公休日整欄灰底。
- 合規檢查結果新規則 `org_closed_day`（soft）：照其他黃色提醒渲染即可。
- 行為：手動仍可排（只警告）；AI 排班與派生合規版會自動避開，不用前端擋。

## 4. 版本重新命名入口（PM#2）

- `PATCH /api/schedules/versions/{id}/` 帶 `{ "version_label": "新名字" }`
  本來就可用，只缺 UI（列表或標題旁加編輯 icon）。

## 5. 簽核總表日期選擇器（PM#4，可能連帶修好 PM#5b）

- 總表目前只能一週一週跳；後端 approved-timeline 支援任意
  `date_from`/`date_to`（≤62 天），加日期選擇器即可。
- PM 反映「簽核後總表看不到班」大概率是這個導航問題（班在別的月份），
  做完這項後請 PM 重測。

---

有 API 問題直接找 Sam；CORS 目前白名單是 localhost:3000 與
intelligent-scheduling-system 的兩個網域，換網域要先講。
