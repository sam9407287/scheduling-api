# 商業流程設計

> 給 PM、業務、客戶 IT、未來維運看的「這個系統怎麼運作 / 客戶會怎麼用」的文件。對齊 commit `ecbf089`（Phase 1 + Phase 2 完成狀態）。
>
> 工程細節請看 [ARCHITECTURE.md](./ARCHITECTURE.md)。前端整合契約看 [PHASE_1_FRONTEND_GUIDE.md](./PHASE_1_FRONTEND_GUIDE.md) 與 [PHASE_2_FRONTEND_GUIDE.md](./PHASE_2_FRONTEND_GUIDE.md)。

---

## 1. 系統定位

**AI 排班系統**，目標客戶為台灣醫療與長照機構（醫院、診所、長照中心、護理之家等）。

核心痛點：
- 機構需排出「能跑運作的真實班表」，同時又要應付**勞動檢查**——查到違法勞基法會罰錢
- 法規（勞基法）剛性，但現場（請假、換班、加班）柔性
- 完全靠人工排班耗時、容易錯、難最佳化
- AI 自動排班結果不一定符合勞基法，仍需人工微調

我們的解法：
- **A / B 雙班表**：B 是現場實際排的（可能違法），A 是給勞檢看的（保證合法）
- **AI 自動排 B**：吃機構規則、個人偏好、團隊條件，吐排班結果
- **一鍵派生 A**：從 B 修補出合法版本，**盡量保留近期日期不動**（勞檢通常查最近）
- **一鍵合規檢查**：把 B 拿去比對勞基法，前端逐格標紅
- **計費模式**：所有 AI 動作都收費（但不同），按月計算、可設定月度上限

---

## 2. 角色與權限

| 角色 | 典型對應 | 可做的事 |
|---|---|---|
| Admin | 機構負責人 / IT | 全部，包含跨分店操作、計費設定、撤銷他人簽核 |
| Manager | 護理長 / HR 主管 | 排班、規則設定、計費上限、簽核班表、查所有員工資料 |
| Supervisor | 各班別主管 | 排班、簽核、看自己分店的員工 |
| Employee | 第一線員工 | 看自己班表、設定可用性、設定個人偏好、同意 / 撤回個資授權 |

**權限階層**：Admin > Manager > Supervisor > Employee。權限是繼承累加的（Manager 可以做 Supervisor 能做的所有事）。

**資料隔離**：所有資料都「綁機構」。一個機構的人**永遠**看不到另一個機構的員工、班表、用量。同機構不同分店則由 Supervisor 級別控制。

---

## 3. 兩種班表：A 與 B 的商業意義

```
B 班表 (actual, 實際版)
├─ 主資料來源 (source of truth)
├─ 反映現場真實運作（含請假、換班、臨時加班）
├─ 可能違反勞基法
└─ 前端拖拉編輯、AI 補齊、AI 全自動生成都會操作 B
                                │
                                ▼  按下「派生 A」
A 班表 (legal, 法規版)
├─ 由 B 派生
├─ 保證符合勞基法（每週工時、連續工作天數、休息間隔）
├─ 與 B 的差異盡量小，且改動優先放在「遠離今天」的日期
└─ 給勞動檢查、政府單位、稽核看
```

**為什麼這樣設計**：勞檢通常查「最近 1-2 週」的班表，所以 A 對近期日期幾乎不調整（保留 B 真實狀態），改動推往更後面。這是客戶在 Phase 1 規劃時明確的需求。

A 是**唯讀衍生產物**——要改就改 B 再重新派生 A。

---

## 4. 流程 #1：員工首次登入與個資授權

```
員工首次打開排班頁
       │
       ▼
前端 GET /api/employees/.../data-consent/
       │
       ├─ 204 No Content  ─────→ 彈出「個資授權同意書」彈窗
       │                              │
       │                              ▼
       │                       員工點「我同意」
       │                              │
       │                              ▼
       │                       POST .../data-consent/
       │                              │
       │                              ▼
       │                       後端建立 EmployeeDataConsent
       │                              │
       └─ 200 (is_active=true) ──→ 不彈窗，直接進入
                                      │
                                      ▼
                              員工填寫個人偏好
                                      │
                                  ┌───┴─────────────────┐
                                  ▼                     ▼
                            可用性 (時段)         排班模式偏好
                          blocked/preferred       (花花班 / 連上長假)
```

### 重點商業規則

- **同意書必須員工本人按**：連超級管理員都不能代簽（PDPA 自主原則，後端強制 403）
- **撤回不刪資料**：員工可在「設定 → 隱私」撤回，後端只蓋 `revoked_at` 時戳留稽核軌跡
- **撤回立即生效**：下一次 AI 排班時，該員工的性別、身高、體重、年齡立即對求解器隱藏
- **不影響哪些資訊**：tag（如「司機」）、證照、排班模式偏好——這些是員工自願公開的非敏感資訊

---

## 5. 流程 #2：管理者設定規則

機構上線時，管理者需要建立的三類規則：

### 5.1 班別 (ShiftTemplate)

「早班 08:00-16:00，最少 2 人」這類。包含：
- 起訖時間、休息分鐘
- 最低人力需求
- 需要的證照
- （選填）員工優先順位

### 5.2 團隊規則 (TeamConstraint) — Notion-Filter 風格

這是 Phase 1/2 的關鍵新功能。可以表達例如：

| 適用範圍 | 條件 | 數量 | 嚴格度 |
|---|---|---|---|
| 夜班 (start ≥ 22:00) | 性別 = 男 | 至少 1 人 | 硬性 |
| 任何班、深夜時段 | 身高 ≥ 175cm | 至少 1 人 | 硬性 |
| 急診室任何班 | 證照 = ACLS | 至少 2 人 | 硬性 |
| 早班 | 「司機」標籤 | 至少 1 人 | 軟性 |

商業含義：「**硬性**」= 不滿足就完全排不出（INFEASIBLE）；「**軟性**」= 盡量滿足，違反會扣分但仍可排。

### 5.3 計費上限 (OrgBillingSettings)

管理者在「帳務 → 設定」設定月度上限（例如 200 tokens）。超過上限後，當月所有 AI 動作會被拒絕（402 Payment Required）。

---

## 6. 流程 #3：排班（三條路）

排班頁的核心，三個按鈕對應三種商業情境。

### 6.1 純手動拖拉（免費）

最直觀。使用者把員工拖到 grid 上對應的 (日期, 班別) 格。後端寫入 `Schedule` 行，不呼叫 AI，不扣費。

### 6.2 AI 補齊（按次計費，便宜）

使用者已手動拉了一部分（例如「全部都先讓 E1 上滿」），剩下空格按「AI 補齊」。

- AI 讀現有 B 的部分填好結構作為 seed
- 啟動 drift mode：盡量保留現有 cell、補空缺
- 可選打勾「強制合法」→ 勞基法當硬約束
- 費率：**5 tokens / 次**（在後台可調）

### 6.3 AI 全自動生成 B（按次計費，最貴）

使用者空白 grid 直接按。

- AI 從零開始排
- 吃所有 TeamConstraint、員工偏好、班別需求
- 預設不強制合法（B 可違法）；如使用者勾選「強制合法」則一併納入
- 費率：**10 tokens / 次**

**三個按鈕共用同一支 API**：`POST /api/ai/schedule/generate/`。差別只在參數組合：
- 純手動：不呼叫此 API
- AI 補齊：`seed_version_id` + `minimize_drift_from_seed=true`
- AI 全自動：兩個都不傳

---

## 7. 流程 #4：合規檢查 → 派生 A

```
B 班表已建立
       │
       ▼
[按鈕] 一鍵勞基法檢查 (免費)
       │
       │  POST /api/schedules/versions/{B}/check-compliance/
       │
       ▼
回傳逐格違規列表
       │
       │  違規格附 (employee_pk, schedule_date, shift_template_id)
       │  前端可直接標紅
       │
       ├──── 0 個違規 ────────┐
       │                       │
       └─ N 個違規             │
              │                │
              ▼                ▼
       使用者決定：     [按鈕] 派生合法 A 班表
       (a) 改 B 再檢查         │
       (b) 直接派生 A          │  POST .../derive-legal/
                               │
                               ▼
                          OR-Tools 解出 A：
                          - 滿足所有勞基法硬約束
                          - 最小化「改動格子數 × 時間遞減權重」
                          - 近期日期幾乎不動，調整推往遠期
                               │
                               ▼
                          建立新 ScheduleVersion (legal, derived_from=B)
                          回傳：
                          - legal_version_id
                          - diff_summary (改了幾格)
                          - removed_cells / added_cells
                               │
                               ▼
                          扣費：3 tokens
```

### 時間遞減權重的商業意義

預設 `time_decay_n = 14`：

- 改 *今天* 的格子：成本 14×
- 改 *2 天後* 的格子：成本 12×
- 改 *14 天後或更遠* 的格子：成本 1×

換句話說：**改近期 1 格 ≈ 改遠期 14 格的成本**。求解器自動把調整推往遠端，近期幾乎保留 B 原貌——這正是「臨時勞檢查最近班表」的對應策略。

---

## 8. 流程 #5：計費

```
管理者設定上限         AI 動作 (生成/補齊/派生)         查用量
      │                       │                          │
      ▼                       ▼                          ▼
OrgBillingSettings        ┌─────────────────────┐    GET /api/billing/usage/
monthly_cap_tokens=200    │ Pre-flight check    │
      │                   │ would_exceed_cap?   │    回傳：
      │                   └─────────┬───────────┘    - period.total_tokens
      │                             │                - cap
      │             ┌───否──────────┴────是────┐     - cap_pct_used %
      │             ▼                          ▼     - records (最近 100 筆)
      │     OR-Tools solve              402 拒絕
      │             │                          │
      │             ▼                          ▼
      │      record_usage(...)            前端顯示
      │             │                  「達月度上限」提示
      │             ▼
      │   UsageRecord + BillingPeriod
      │   total_tokens 同 atomic 累計
      │             │
      │             ▼
      │  Response 附 metadata.billing
      │   - tokens_charged
      │   - period_usage_after
      │
      │             │
      │             ▼
      │  使用者立刻看到「剛剛這次扣了 X、本月累積 Y」
      │
```

### 計費關鍵商業規則

| 規則 | 內容 |
|---|---|
| **先扣不退** | 解不出來（INFEASIBLE）也照樣扣費，因為求解器確實跑過了 |
| **按次定額** | 三種模式各有固定價格，不按時長 / 員工數動態變動 |
| **月度結算** | 每月 1 日重置；本月 cap 滿即停止 AI 服務（手動排不受影響） |
| **管理者可即時調整 cap** | 達 cap 後馬上加額度，下一次 call 立刻可用 |
| **dry-run 不扣費** | API 接受 `consume_token=false` 但僅供測試 / 內部用，前端正式環境不傳 |
| **kill switch** | `is_billing_enabled=false` 立即停用所有 AI 動作（支援端緊急斷尾） |
| **跨機構完全隔離** | 一機構超 cap 不影響其他機構 |

### 預估費用（hover tooltip）

前端在使用者 hover 在 AI 按鈕上時：
```
POST /api/billing/estimate/  {billing_mode: "generate"}
→ tokens_to_charge: 10, current: 47, projected: 57, cap: 200
```
顯示：「≈ 10 tokens · 本月已用 47/200」

不會寫 DB、夠便宜可以 debounce 觸發。

---

## 9. Phase 1 / 2 / 3 路線圖

| Phase | 範圍 | 狀態 |
|---|---|---|
| **0**（前置） | 基礎 Django + DRF + Firebase auth + 多租戶 + 基本 OR-Tools | ✅ |
| **Phase 1** | A/B 雙班表 + 逐格 compliance + drift mode + team constraints + 統一 generate API + 同意守門 | ✅ |
| **Phase 2** | Consent / TeamConstraint REST CRUD + metered billing schema + cap enforcement + shift pattern preference + 整合測試 + 前端文件 | ✅ |
| **Phase 3**（建議下一步） | Stripe 真實付款整合（PaymentMethod 已留欄位）、月度通知 alert（threshold + 寄信）、軟性勞基法警告 UI 分流（嚴格 vs 提醒）、勞動法 RAG（pgvector） | 待開工 |

---

## 10. 客戶上線典型路徑

```
W0：合約簽訂、確認費率
       │
W1：機構建立、Admin 帳號建立
       │
W1-2：員工帳號匯入、員工首次登入並同意個資授權
       │
W2：管理者設定班別、團隊規則、月度 cap
       │
W2-3：管理者試跑 AI 生成、調整規則、用 estimate 校準預算
       │
W3+：正式運作
       │
       ├─ 每週：排班 → 補齊 → 派生 A → 簽核
       ├─ 月度：管理者看用量、調整 cap
       ├─ 臨檢時：拿 A 給勞檢看
       └─ 年度：續約、調費率
```

---

## 11. 常見客戶問答

**Q：A 跟 B 看到的人會不會差很多？**
A：不會。派生 A 時 OR-Tools 在所有合法解中挑「改動最少」的，且改動會被推往較遠的日期。實務上 B 違規越多、A 與 B 差越大；B 已經接近合法，A 幾乎是 B 的複製品。

**Q：員工不同意個資使用會怎樣？**
A：該員工仍可被排班，但任何依賴性別 / 身高 / 體重 / 年齡的團隊規則都「看不到」他。例如「夜班至少 1 男」這條規則對未同意員工視同沒這個男生。

**Q：AI 排出來不合法怎麼辦？**
A：兩條路：(1) 手動微調 B 後再「派生 A」；(2) 直接在 AI 全自動生成時勾選「強制合法」（此時勞基法當硬約束，但 AI 可能 INFEASIBLE）。

**Q：我可以用多少 AI 排班？**
A：你設多少 cap 就最多用多少。沒設上限就無限。但達到 cap 我們會收滿那個月的費用——你可以即時調 cap。

**Q：解不出來也要付費嗎？**
A：是的。「先扣不退」是設計上明確的規則——OR-Tools 跑一次的成本是固定的，無論成不成功。可以在按下去之前用 estimate 看一下費用。

**Q：可以匯出班表給 Excel 嗎？**
A：可以透過排班版本的 schedules 端點拉 JSON，前端組成 CSV / Excel。

---

## 12. 限制與已知議題

### 12.1 Stripe 尚未整合
`PaymentMethod` 目前都是 mock 行為，所有「付款成功」是 stub。Phase 3 才會接 Stripe 真扣款 → 月結帳單。

### 12.2 月度通知尚未上線
`alert_threshold_pct` 與 `billing_email` 欄位已存在，但達門檻時不會寄信。Phase 3 會加 Celery beat 定期掃描。

### 12.3 跨機構排班不支援
員工只屬於一個機構；長照集團內跨機構支援需要 Phase 3+。

### 12.4 OR-Tools 規模上限
單次 generate 期間建議 ≤ 31 天，員工 ≤ 100 人，班別 ≤ 10 種。超過此規模 solver 時間可能超過 5 分鐘 timeout（async 路徑可拉長到 30 分鐘）。

### 12.5 勞動法引用尚未做 RAG
目前勞基法規則寫在程式中（`max_weekly_hours=40` 等）。Phase 3 規劃 pgvector + LangChain 做動態法規檢索，讓使用者問「我這樣排班違反勞基法第幾條」也能回答。
