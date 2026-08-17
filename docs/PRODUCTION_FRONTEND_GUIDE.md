# 雲端後端串接指南（給前端工程師）

> 更新日期：2026-08-15
> 後端已部署至 Railway，本文說明前端如何連線、認證與注意事項。
> API 端點細節請看 [FRONTEND_API.md](./FRONTEND_API.md)（扁平 API 參考，含請求/回應格式）。

---

## 1. 部署資訊

| 項目 | 值 |
|---|---|
| Base URL | `https://web-production-8eb28.up.railway.app` |
| API 前綴 | `https://web-production-8eb28.up.railway.app/api` |
| Django Admin | `https://web-production-8eb28.up.railway.app/admin/` |
| 資料庫 | Railway Postgres（正式資料，請勿當測試場） |
| 平台 | Railway（asia-southeast 就近節點由平台自動分配） |

## 2. 前端連線設定

前端 repo 已支援環境變數切換，不需改程式碼。建立 `.env.production`（或修改 `.env.local`）：

```bash
# 直連雲端後端
VITE_API_BASE_URL=https://web-production-8eb28.up.railway.app/api
# 認證模式：token（生產目前用 DRF Token，Firebase 尚未啟用）
VITE_AUTH_MODE=token
VITE_BYPASS_AUTH=false
```

本地開發（連本地後端）維持原樣：`VITE_API_BASE_URL=/api`＋Vite proxy。

**兩種模式可以並存**：`.env.local` 連本地、`.env.production` 連雲端，
`npm run build` 會吃 production 檔。

## 3. 認證

- 生產環境登入沿用同一支端點：`POST /api/auth/login/`，body `{"username", "password"}`，回傳 `{token, user}`。
- 之後所有請求帶 `Authorization: Token <token>`（`VITE_AUTH_MODE=token` 時 axios interceptor 已自動處理）。
- **帳號密碼另行提供**（LINE 傳給你，不寫在文件裡）。生產密碼與本地開發的 `admin/admin123` 不同。
- Firebase JWT 認證（`Authorization: Bearer <idToken>`）後端已內建但尚未設定憑證，等客戶拍板 D2 決策後啟用；届時前端只要把 `VITE_AUTH_MODE` 切回 `firebase`。

## 4. CORS 與網域

- 目前後端允許的跨域來源：`http://localhost:3000`（你本地 dev server 直連雲端 API 用）。
- **前端要部署上線時，把你的正式網域告訴 Sam**，後端加進 `CORS_ALLOWED_ORIGINS` 即可（Railway 環境變數，改完自動重啟）。
- 若前端也部署在 Railway 同專案內，走公網網域即可，不需特別處理。

## 5. 已部署的功能範圍

與 2026-08-07 對齊版（`scheduling-api` main 分支 `bf32fd6`）一致，包含你最近串接的所有端點：

- 簽核工作流：`approve` / `unapprove`（需 reason）、已簽核版本鎖定（寫入回 `409 schedule_version_locked`）
- 簽核總表：`GET /api/schedules/versions/approved-timeline/`（conflicts＋unresolved_conflict_count）
- 重疊裁決：`POST /api/schedules/overlap-decisions/`（select / coexist，stale key 回 `409 conflict_changed`）
- 版本期間自動化：建立版本不送日期、period 唯讀、班次寫入自動外擴
- 多班次 AI 排班、`max_daily_hours` 規則、勞基法軟硬規則、逐格合規檢查
- 個資同意、團隊規則、計費（rates/usage/estimate/settings）

## 6. 生產環境與本地的差異（重要）

| 項目 | 本地 | 雲端生產 |
|---|---|---|
| 資料庫 | SQLite（`dev_local.sqlite3`） | Postgres |
| 認證 | Token（admin/admin123） | Token（獨立密碼） |
| **AI 非同步排班** | 可用 | **`run_async: true` 不可用**（尚未部署 Celery worker/Redis），請一律用同步模式（預設）。同步 generate/derive-legal 正常 |
| 月度用量警示信 | console 輸出 | 未啟用（Celery beat 未部署） |
| 資料 | seed 示範資料 | 已跑 seed（同一批示範機構/員工/班別），可直接操作 demo |
| HTTPS | 無 | 強制（HTTP 自動轉 HTTPS） |

## 7. 疑難排解

- **401**：token 過期或未帶 → 重新登入。
- **403**：權限不足（角色低於 supervisor 的寫入操作）。
- **409**：三種業務衝突碼——`schedule_version_locked`（先取消簽核）、`unapprove_conflict`（版本已不是 approved）、`conflict_changed`（衝突群組已變，重抓 timeline）。
- **CORS 錯誤**：你的來源網域不在允許清單，找 Sam 加。
- **502/503**：Railway 冷啟或部署中，等 30 秒重試；持續發生找 Sam 看 log。

## 8. 部署節奏

- 後端 main 分支更新後由 Sam 手動 `railway up` 部署（本地 312 項測試全綠才部署）。
- API 契約變動一律先更新 `docs/FRONTEND_API.md` 再部署，看該檔 git log 即可知道改了什麼。
