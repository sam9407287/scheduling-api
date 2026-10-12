# 生產煙霧測試（smoke）

對**已部署**的前後端跑一輪端到端檢查，涵蓋 9/23、10/5、10/13 三次前端交辦的每一項。
不碰 Django ORM，全部走 HTTP；瀏覽器測試用 Playwright 無頭 Chromium。

## 怎麼跑

```bash
# 只跑不需登入的檢查（服務存活、CORS、線上 bundle 是不是最新 build）
venv/bin/pytest smoke/ -v

# 完整跑（需要一個 manager 以上、已綁機構的帳密帳號）
SMOKE_USERNAME=xxx SMOKE_PASSWORD=yyy venv/bin/pytest smoke/ -v

# 連 Gemini 一起打（30–150 秒，consume_token=false 不計費）
SMOKE_RUN_LLM=1 SMOKE_USERNAME=xxx SMOKE_PASSWORD=yyy venv/bin/pytest smoke/ -v

# 看著瀏覽器跑 / 錄影
SMOKE_USERNAME=xxx SMOKE_PASSWORD=yyy venv/bin/pytest smoke/test_ui_smoke.py --headed --video on
```

| 環境變數 | 預設 | 說明 |
|---|---|---|
| `SMOKE_API_URL` | `https://web-production-8eb28.up.railway.app/api` | 後端 |
| `SMOKE_WEB_URL` | `https://intelligent-scheduling-system.web.app` | 前端 |
| `SMOKE_USERNAME` / `SMOKE_PASSWORD` | 無 | 帳密登入帳號（Google 帳號沒有密碼，不能用） |
| `SMOKE_RUN_LLM` | 關 | `1` 才會打 AI 排班 |

本地也能跑：`SMOKE_API_URL=http://localhost:8000/api SMOKE_WEB_URL=http://localhost:3000 SMOKE_USERNAME=admin SMOKE_PASSWORD=admin123`。

## 它會在生產做什麼

所有建立的資料都掛在登入帳號自己的機構底下，名稱前綴 `SMOKE-<run id>`，
測試結束自動刪除（分店、班別、兩名員工連同登入帳號、版本與班次、假日、出勤、加班紀錄）。
刪不乾淨會讓測試失敗並列出殘留物。操作日誌會留下這一輪的紀錄，這是預期的。

## 覆蓋對照

| 交辦項目 | API 測試 | UI 測試 |
|---|---|---|
| 9/23 AI 排班按鈕 | `TestLLMSchedule`（需 `SMOKE_RUN_LLM`） | AI 排班對話框、loading 文案 |
| 9/23 建立機構 onboarding | `TestIdentity`（重複建機構 409） | 登入後不落在 onboarding |
| 9/23 公休日 | `TestComplianceSettings` 去重、整週全休 400 | 設定頁「每週固定公休日」 |
| 9/23 版本改名 | `test_rename_version` | — |
| 9/23 總表日期選擇器 | `approved-timeline` 任意區間 | 兩個日期輸入框 |
| 10/5 分店範圍 `scope` | `TestLLMSchedule` scope 欄位 | 分店版本橫幅、表格鎖定 |
| 10/5 不限分店選項 | 建立 `branch: null` 版本 | 新增版本對話框選項文案 |
| 10/13 B1 設定頁 404 | — | 員工優先順序 tab、未知路徑 fallback |
| 10/13 B2 同日多班 checkbox | `options` 回顯預設 false | checkbox 存在且預設未勾 |
| 10/13 B3 薪制 + 假日加給 | 建立／PATCH `pay_type`、假日出勤倍率分流、PATCH 倍率、PUT 405 | 新增員工表單「薪制」 |
| 簽核鎖定 / 取消簽核 | 409 `schedule_version_locked`、unapprove 回 draft | — |
| 合規檢查 | 連排 7 天觸發 `max_consecutive_days` | — |
| 操作日誌 / 計費 | 本輪有紀錄、估價 generate = 10 | 操作日誌頁 |
| 前端部署是否最新 | bundle 內含各功能字串 | — |
