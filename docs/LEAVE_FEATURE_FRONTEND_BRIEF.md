# 請假功能前端開發簡報

> 2026-08-26。後端已上線（本地＋Railway 生產都已部署）。
> API 詳細契約見 FRONTEND_API.md §8.1；本文講 UX 流程與畫面建議。

## 產品決策（已與 Sam 確認）

1. 全日假、單層審核：員工送出 → supervisor 以上核准/駁回（駁回必填理由）。
2. **主管代登記直接視同已核准**（電話請假情境，POST 同一支端點即可）。
3. 核准後班次**標記為 `leave` 狀態保留紀錄**，不刪除；取消核准自動還原。
4. 特休額度依勞基法年資級距自動計算，只有「已核准的特休」扣額度。
5. 請假日 = AI 排班硬約束；**手動排班在請假日只警告不阻擋**（與跨版本重疊同哲學）。

## 畫面建議

### 員工端「我的請假」
- 申請表單：假別下拉（10 種，用 `leave_type_display`）＋日期範圍＋事由
- **選完日期立即呼叫 `GET /requests/impact/`**，顯示「這幾天你有 N 個已排班次」
- 特休選項旁顯示 `GET /requests/balance/` 的「剩餘 X 天」；超額仍可送出（後端不擋，主管審核時自行判斷）
- 列表：pending/approved/rejected/cancelled 狀態徽章；rejected 顯示 review_note
- pending 可自行取消（`POST {id}/cancel/`）

### 管理端「請假管理」
- 側邊欄 badge：`GET /requests/?status=pending` 的 count
- 審核卡片同畫面顯示 impact（受影響班次清單）＋該員工特休餘額
- 核准/駁回按鈕；駁回強制填 note
- 「代員工登記」按鈕 → 同一個表單但可選員工，送出即已核准
- 已核准的請假可取消（還原班次），需 supervisor 以上

### 班表整合
- Schedule 新增 `status: "leave"`：格子用明顯請假樣式（灰底＋「假」字之類）
- 手動排班/拖曳到該員工的已核准請假日：跳警告 toast 但允許（可用
  `GET /requests/?employee=&status=approved&date_from=&date_to=` 查是否請假日）
- AI 排班會自動避開請假日，前端不用做事

## 409 錯誤碼
- `leave_not_pending`：核准/駁回時申請已不是 pending → 重新整理列表
- `leave_not_cancellable`：已 rejected/cancelled 的不能再取消
