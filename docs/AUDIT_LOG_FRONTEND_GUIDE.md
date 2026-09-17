# 操作日誌串接指南（給前端工程師）

> 2026-09-18。`AuditPage.tsx` 目前渲染的是寫死的 `mockAudit` 陣列——
> 後端其實從第一天就在真實記錄每筆操作（AuditLog），只是沒有查詢 API。
> 現在 API 補上了，把 mock 換成真資料即可。

## 一、端點（唯讀，manager 以上）

```
GET /api/audit/logs/?action=&model_name=&user=&search=&date_from=&date_to=&page=
GET /api/audit/logs/{id}/
```

- 標準 DRF 分頁（count/next/previous/results），依時間新→舊排序。
- **唯讀**：POST/PATCH/DELETE 一律 405——稽核不可竄改的保證來自這裡。
- Org isolation：只看得到自己機構使用者的操作；`user=null` 的系統寫入
  （Celery 排程等）僅 superuser 可見。

## 二、資料形狀

清單項（瘦身版）：

```jsonc
{
  "id": 512,
  "user": 5, "user_name": "蔡承軒",        // user=null 時 user_name="系統"
  "action": "cancel",                       // create|update|delete|approve|reject|publish|cancel
  "action_display": "取消",
  "model_name": "ScheduleVersion",
  "record_id": 12,
  "changes": { "reason": "排班內容有誤" },  // 各動作的變更摘要，可能為 null
  "timestamp": "2026-09-18T02:15:33+08:00"
}
```

Detail（`/{id}/`）額外多：`old_data`、`new_data`（完整前後快照）、
`ip_address`、`user_agent`——展開列時再抓即可，清單不含這些重欄位。

## 三、UI 對應建議（沿用你現有版面即可）

- 你目前的「類型」badge → 用 `action_display`（或依 `action` 配色）。
- 「目標」列 → `model_name` + `record_id`（常見 model_name：
  ScheduleVersion / Schedule / LeaveRequest / Employee / ShiftTemplate /
  OrgLeaveSettings…）。要人話描述可依 model_name 做前端字典，未知值
  直接顯示原字串。
- `changes` 有值時顯示摘要（例：取消簽核的 reason 就在這）。
- 篩選器直接對應 query params；搜尋框 → `?search=`（比對模型名與使用者名）。
- 「匯出日誌」按鈕：後端暫無匯出端點，可前端把當前查詢結果轉 CSV，
  或先移除按鈕。

## 四、注意

- mock 資料裡的「打卡遲到」「申請調班」「自動比對打卡」這幾種事件型態
  後端目前**不存在**（打卡比對排程是未實作功能）——換真資料後這些卡片
  類型自然消失，不要在前端硬造。
- 頁首那句「不可竄改之稽核紀錄」現在名副其實：資料由後端 signal 自動
  寫入、API 唯讀。
