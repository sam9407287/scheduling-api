# 功能成熟度與交付狀態

> 給接手的工程師、PM、客戶 IT 看的「現在到底完成了什麼、哪些是核心、哪些只是先寫好骨架等未來接」的對照表。對齊 commit `e020242`（Phase 3 完成）。
>
> 技術細節看 [ARCHITECTURE.md](./ARCHITECTURE.md)，商業流程看 [BUSINESS_FLOWS.md](./BUSINESS_FLOWS.md)，前端契約看 `PHASE_{1,2,3}_FRONTEND_GUIDE.md`。

---

## 如何讀這份文件：成熟度圖例

| 標記 | 意義 | 可以拿去做什麼 |
|---|---|---|
| ✅ **主要** | 核心功能，已完整實作 + 測試覆蓋，系統靠它運作 | 可直接上線給客戶用 |
| 🔹 **次要** | 支援性功能，已完整 + 測試，但非系統命脈 | 可上線，缺了不影響主流程 |
| 🟡 **預留擴展** | **故意只寫骨架 / mock**，欄位與介面已備好，等 Phase 4 接真實服務 | 不要當成已完成功能 demo；可向客戶說明「架構已預留」 |
| ⏳ **未實作** | Phase 4 才做 | 還不存在 |

**一句話總覽**：主要 + 次要功能（✅🔹）都是真的可用、有測試保護的。🟡 是「為了未來好擴展而提前寫的接口」，常見於計費付款與通知這種需要外部服務的地方——架構接好了，但還沒接真錢、真信。

---

## A. 主要功能（✅ 核心，系統命脈）

這些是整個產品的價值所在，全部完整實作且有測試。

| 功能 | 說明 | 在哪 | 測試 |
|---|---|---|---|
| ✅ A/B 雙班表 | B=實際（可違法）、A=合法（派生）。`ScheduleVersion.version_type` + `derived_from` 血緣 | `schedules` | schema 13 |
| ✅ 一鍵勞基法檢查 | 逐格回傳違規，標到 `(員工,日期,班別)`，前端可逐格標紅 | `compliance/engine.py` | compliance 8 |
| ✅ 派生 A（drift 修補） | 滿足勞基法 + 最小變動 + 近期日期優先不動（時間遞減權重） | `ai_engine` drift mode | derive-legal 7 |
| ✅ AI 自動排班引擎 | 單一 OR-Tools Provider，靠參數驅動「全自動/補齊/派生」三模式 | `ai_engine/providers/ortools_provider.py` | team-constraints 17 |
| ✅ 團隊規則編譯器 | Notion-filter 風格規則（夜班需≥1男≥175cm…）動態翻成 CP-SAT | `ai_engine/team_constraint_compiler.py` | team-constraints 17 |
| ✅ 個資同意守門 | 未同意員工的敏感屬性對求解器一律 None（PDPA 自主原則，連 superuser 不能代簽） | `Employee.sensitive_attributes_for_solver` | consent 14 |
| ✅ 按用量計費 | 三模式定額扣費、月度上限、pre-flight 402、先扣不退、原子累計 | `apps/billing` | billing 31 |
| ✅ 多租戶資料隔離 | 所有 queryset 綁機構，跨 org 完全看不到彼此 | 全系統 | integration 9 |

## B. 次要功能（🔹 支援性，已完整）

有它更好用，缺它主流程照跑。

| 功能 | 說明 | 成熟度 |
|---|---|---|
| 🔹 軟性勞基法規則 | 每條規則可設 hard（阻擋）/ soft（黃色提醒、派生時不 INFEASIBLE）。**一律顯示不抑制** | ✅ 完整 + 測試 11 |
| 🔹 排班模式偏好 | 花花班（早晚交錯）/ 連上長假，OR-Tools 軟約束（權重低，只當 tie-breaker） | ✅ 完整 + 測試 6 |
| 🔹 月度用量通知 | Celery beat 每小時掃描，達門檻寄信 + dedupe | ⚠️ 邏輯完整，但 **email 走 console backend（見 🟡）** |
| 🔹 員工可用性/時段偏好 | blocked（硬）/ preferred（軟）時段 | ✅ 完整 |
| 🔹 班別員工優先順位 | 加班/額外班意願分配 | ✅ 完整 |
| 🔹 稽核 log | 所有 model 寫入自動記錄；UsageRecord 為計費獨立軌跡 | ✅ 完整 |
| 🔹 估費預覽 | `/api/billing/estimate/` hover 時預估費用，不寫 DB | ✅ 完整 |

---

## C. 預留擴展（🟡 故意只寫骨架，等 Phase 4）

**這一段最重要——以下都不是「已完成功能」，而是「架構已預留、等接外部服務」。** 對外 demo 時請說明這層。

| 項目 | 現在的狀態 | 為什麼這樣設計 | Phase 4 怎麼接 |
|---|---|---|---|
| 🟡 信用卡付款 | `PaymentMethod.provider='mock'`，永遠「成功」。Stripe 欄位（`external_token`/`last_4`/`brand`）已備 | 讓計費的扣款/上限流程能端到端測試，不用真錢 | 接 Stripe SDK + webhook，**不需 migration**（欄位已在） |
| 🟡 Email 寄送 | `EMAIL_BACKEND=console`，通知只寫 log 不真寄 | Phase 3 不引入外部服務依賴；先驗證通知**邏輯**（門檻判斷、dedupe）正確 | 換 `EMAIL_BACKEND` 環境變數即可，`_send_threshold_email` helper 已隔離，契約不變 |
| 🟡 `estimate_tokens(mode, request_metadata)` | 收 `request_metadata` 參數但目前沒用（定額計費） | 留位給未來「按規模動態定價」，不動呼叫端就能升級 | 在函式內讀 metadata 算動態價 |
| 🟡 `consume_token` 旗標 | Phase 1 就留的計費 hook，Phase 2 才接上真扣款 | 先有 API 契約、後補實作，前端不用改 | 已接上（Phase 2 完成） |
| 🟡 `BaseScheduleProvider` 介面 | 抽象 4 方法，目前只有 ORToolsProvider 一個實作 | 排班引擎可插拔（未來換商用 solver 或 LLM 排班） | 寫新 Provider 類別 + 改 `AI_SCHEDULE_PROVIDER` 設定 |
| 🟡 `consent_version` 欄位 | 同意書版本號，目前固定 "1.0" | 未來條款更新時可要求員工重新同意 | 比對版本號決定是否重彈同意書 |
| 🟡 ScheduleVersion 狀態流 | `draft→published→approved→archived` 都在，但目前主要用 draft/approved | 完整生命週期已建模 | 補 published/archived 的轉換動作 |

---

## D. 尚未實作（⏳ Phase 4 才做）

| 項目 | 範圍 | 卡在哪 |
|---|---|---|
| ⏳ Stripe 真實整合 | 真扣款、webhook、月結帳單 | 需 Stripe 帳號 |
| ⏳ 真實 email 服務 | SendGrid / SES 等 | 服務選型 + 帳號 |
| ⏳ 勞動法 RAG | pgvector + LangChain，自然語言問「違反第幾條」 | 法規語料來源 + 版權/robots 確認 |
| ⏳ 跨機構排班 | 長照集團跨機構支援 | 員工目前綁單一機構 |
| ⏳ 班表匯出 | Excel/CSV 直接匯出 | 目前需前端從 JSON 自組 |

---

## E. 測試覆蓋摘要

**總計 264 passed + 1 skipped**（skip 的是並發鎖測試，僅在 Postgres 跑，SQLite 環境跳過）。

| 階段 | 測試數 | 涵蓋 |
|---|---|---|
| 舊有基礎 | 140 | model CRUD、API、auth、bug 回歸、production 設定 |
| Phase 1 | 45 | schema 13、compliance 8、derive-legal 7、team-constraints 17 |
| Phase 2 | 60 | consent/CRUD 14、billing-schema 21、billing-wired 10、pattern 6、**整合 9** |
| Phase 3 | 19 | soft-rules 11、alerts 8 |

**整合測試（9 個）** 特別驗證跨功能邊界：完整 E2E 流程、cap 跨模式累積、跨 org 隔離、consent 撤回即時生效、空輸入不崩潰。這層是重構時的安全網。

**驗證狀態**（2026-05-30）：`pytest` 全綠、`makemigrations --check` 無遺漏 migration、working tree 與 `origin/main` 同步。

---

## F. 給接手者的建議起手式

1. 讀 [CLAUDE.md](../CLAUDE.md)（10 分鐘，含完整 commit map）
2. 讀本檔 C 章「預留擴展」——避免把 mock 當成已完成功能
3. 跑 `pytest tests/test_phase2_integration.py -v`——最快理解全系統怎麼串起來
4. 要接 Phase 4 時，C 章每一列都標好了「怎麼接」，且多數不需要 migration（欄位已預留）

---

## G. 一頁總結（給主管/客戶）

- **能 demo 的完整閉環**：員工同意 → 設規則/上限 → AI 排班（計費）→ 一鍵檢查 → 派生合法班表 → 簽核。
- **真的可用**：排班引擎、勞基法檢查、A/B 派生、團隊規則、計費上限、多租戶隔離——全部有測試保護。
- **架構已預留、尚未接真服務**：信用卡扣款（mock）、email 寄送（只寫 log）。這兩個 Phase 4 接上即可，不必改架構。
- **完全還沒做**：勞動法 RAG 問答、跨機構排班、Excel 匯出。
- **品質**：264 個自動化測試全綠，每個功能改動都有對應測試把關。
