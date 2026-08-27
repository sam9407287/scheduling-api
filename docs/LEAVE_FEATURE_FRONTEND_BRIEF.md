# 請假功能前端開發需求（交給前端工程師）

> 2026-08-27。後端 API 已上線（本地＋Railway 生產皆可打）。
> API 完整契約見 FRONTEND_API.md §8.1；本文定義「必須做到的行為」。
> **畫面長相、版面配置、元件選擇由你設計**——你最了解現有 UI 的語彙，
> 請延續你做排班管理、簽核總表時的風格與互動慣例即可。

## 一、產品決策（已定案，行為請照做）

1. 全日假（不做半天/小時），單層審核：員工送出 → supervisor 以上核准或駁回，**駁回必填理由**。
2. **主管代員工登記 = 送出即自動核准**（電話請假情境）。用同一支 `POST /api/leaves/requests/`，後端依身份自動判斷。
3. 核准後，該員工範圍內的班次會被後端標成 `status: "leave"`（保留紀錄不刪除）；取消核准會自動還原。前端不用自己動班次。
4. 特休（annual）額度後端依勞基法年資自動計算；**超額申請不擋件**，餘額只是給申請人與審核者的參考資訊。
5. 請假日的手動排班**警告但不阻擋**（與跨版本重疊同一哲學）；AI 排班後端已自動避開請假日，前端零成本。

## 二、必要的使用者能力（驗收標準）

**員工**：
- 能申請請假（假別 × 日期範圍 × 事由）並看到自己歷次申請與狀態
- 送出前能看到「這段期間我已有 N 個班次」（`GET /requests/impact/`）
- 選特休時能看到剩餘天數（`GET /requests/balance/`）
- 能取消自己 pending 的申請；被駁回能看到理由

**主管**：
- 能看到待審申請（含數量提示）並核准/駁回
- 審核時能在同一個視野看到影響（受影響班次）與該員工特休餘額——不用跳頁查
- 能代任何員工登記請假（即刻生效）
- 能取消已核准的請假（後端會還原班次）

**班表**：
- `status: "leave"` 的格子在週班表/月班表/Excel 上有可辨識的呈現方式（樣式你決定）
- 把班排到某人的已核准請假日時給予警告（不阻擋）

## 三、API 速查

```
GET/POST  /api/leaves/requests/?status=&employee=&date_from=&date_to=
POST      /api/leaves/requests/{id}/approve/    (supervisor+, body {"note"?})
POST      /api/leaves/requests/{id}/reject/     (supervisor+, body {"note"} 必填)
POST      /api/leaves/requests/{id}/cancel/     (自己的 pending；approved 需 supervisor+)
GET       /api/leaves/requests/impact/?employee=&start_date=&end_date=
GET       /api/leaves/requests/balance/[?employee=]
```

- 假別 10 種，直接用回傳的 `leave_type_display` 顯示，不要前端寫死中文。
- 員工帳號只看得到自己的申請（後端已過濾）；主管看全機構。
- 409 錯誤碼：`leave_not_pending`（申請已被處理，重新整理列表）、`leave_not_cancellable`。
- Schedule 物件的 `status` 多了 `"leave"` 選項，留意既有的狀態 switch/badge 有沒有 default 分支。

## 四、留給你設計判斷的部分（刻意不規定）

- 請假入口放哪（獨立頁？排班頁側欄？員工個人頁？）
- 審核介面的形式（列表展開？卡片？Dialog？）
- 請假格子的視覺（底色/圖示/文字，只要跟現有班別色不打架）
- 月班表與 Excel 匯出裡請假日要不要特別標（建議要，形式你定）
- 餘額、影響預覽的呈現時機與位置

有 API 行為問題直接找 Sam；契約如需調整，後端可以配合改，先講再動。
