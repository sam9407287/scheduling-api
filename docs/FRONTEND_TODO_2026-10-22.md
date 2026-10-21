# 前端交辦：自助註冊、每日人力需求、一例一休、國定假日（2026-10-22）

> 給 Frank。後端四項都已完成並部署（commit 見 git log 2026-10-22），API 契約全在
> [FRONTEND_API.md](./FRONTEND_API.md)（§Authentication、§3.2、§4.1、§5.1、§6）。
> 本地開發照舊：後端 `python manage.py runserver 8000`（SQLite），`admin` / `admin123`。
> 本地沒有真的寄信：`send-code` 回應會多一個 `debug_code` 欄位直接給你驗證碼。

## 1. 帳號密碼註冊頁（Email 驗證碼）

登入頁加「註冊」分頁，兩步：

1. 輸入 email → `POST /api/auth/register/send-code/`
   - 200：顯示「驗證碼已寄到 xxx」，開始 `cooldown_seconds`（60 秒）倒數後才可「重新寄送」。
   - 409 `email_already_registered`：提示「已註冊，請直接登入或用 Google 登入」。
   - 429 `too_many_requests`：顯示「請 N 秒後再試」（`retry_after_seconds`）。
   - 503 `email_send_failed`：顯示「寄信服務暫時異常」。
2. 輸入 6 位數驗證碼 + 密碼（≥8 碼）+ 姓名（選填）→ `POST /api/auth/register/`
   - 201：回 `token` + `user`，**跟 `/auth/login/` 的回應一模一樣**，直接走現有登入成功流程
     （存 token → `/me` → `organization` 是 null → 建立機構 onboarding）。
   - 400 `invalid_code` / `code_expired` / `code_locked` / `weak_password`（`details` 是中文訊息陣列，直接列出）。
3. 登入頁：`/auth/login/` 現在接受 `{ "email", "password" }`，自助註冊的帳號用 email 登入；
   既有的 `{ "username", "password" }` 不變。

任何信箱都能收（不限 Gmail）。同一個 email 之後用 Google 登入會綁到同一個帳號。

## 2. 每日人力需求（「每天需要幾個人」）

設定頁新增「人力需求」表（`/api/shifts/staffing-requirements/`，§3.2）：

- 一列 = 「星期幾 或 特定日期（擇一，都不填＝每天）」×「班別（或『全日總人數』）」→「最少 / 最多人」。
- 建議 UI：下拉「每天 / 週一…週日 / 指定日期」、下拉班別（含「全日總人數」）、兩個數字欄、備註、啟用。
- 沒有規則的日子仍用班別模板的 `min_staff_count`，所以這張表只放「跟平常不一樣」的日子。

班表頁顯示結果：一鍵合規檢查回應多了 `coverage`（§4.1）。在每一天（每個班別）的欄頭顯示
「已排/需要」，`status: under` 紅、`over` 橘、`ok` 正常。`shift_template_id: null` 的那筆是「當日總人數」。

AI 排班（LLM）會照規則排，排不滿會在 `warnings` 說「只排到 x/y 人」，超過上限的列在 `rejected`。

## 3. 一例一休（不用新 UI，只要顯示正確）

- 合規檢查 `weekly_rest_days` 現在分兩種顏色：`severity: "hard"`（一週上 7 天＝例假日出勤，紅）
  與 `"soft"`（一週上 6 天＝休息日出勤要加班費，黃）。前端照 `severity` 著色即可，邏輯都在後端。
- AI 排班／派生合規版會自動守住「每週最多 5 天」；LLM 多排的第 6 天會出現在 `rejected`（reason 含「一例一休」）。
- 設定頁「軟性規則」清單多一個選項 `weekly_rest_days`（人手真的不夠時可以把它標 soft，AI 才排得出來）。

## 4. 班表預設顯示國定假日（近兩年）

- 進班表時打一次 `GET /api/compliance/holidays/calendar/?start=<期間起>&end=<期間迄>`（§4.1 下方）。
  後端內建 **2026、2027 全年台灣國定假日（含補假日）**，機構不用自己建。
- 班表日期欄頭：`kind: holiday` 顯示節日名稱（例「中秋節」）＋假日底色；`kind: makeup` 顯示「國慶日補假」。
  `source: org` 是機構自訂（例院慶），照樣顯示。
- 機構自訂假日的 CRUD 維持 `/api/compliance/holidays/`；同一天自訂名稱會蓋過內建名稱。
- 假日排班不阻擋，只是合規檢查會有 soft `holiday_scheduling` 提醒（加倍給薪／補休）。

## 給 Sam 的部署備忘

Railway `web` 服務要設這幾個變數，`send-code` 才會真的寄信（沒設時 API 仍回 200 但信只印在 log）：

```
EMAIL_HOST_USER=<寄件用 Gmail 帳號>
EMAIL_HOST_PASSWORD=<該 Gmail 的 16 碼應用程式密碼>
DEFAULT_FROM_EMAIL=<同上帳號或顯示名稱 <帳號>>
# 以下有預設值，通常不用改：EMAIL_HOST=smtp.gmail.com EMAIL_PORT=587 EMAIL_USE_TLS=true
```
