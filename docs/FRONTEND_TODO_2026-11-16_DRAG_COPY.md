# 前端交辦（2026-11-16）— 排班表拖曳要有「移動模式／複製模式」

PM 需求：週排班表拖曳班次時，旁邊要有一顆可以切換的按鈕。

- **移動模式**（現在的行為、預設）：班次搬到新格子，原本的位置消失。
- **複製模式**：原本的班次保留，目標格**多排一班**同班別。

Android／iOS 已完成並實測（見「手機 App 的做法」）；網頁版請照同樣規則做。**後端不用改**，沒有新 API。

## 要改的地方（`src/pages/schedules/SchedulesPage.tsx`）

1. **加一個狀態與切換按鈕**
   - `const [dragMode, setDragMode] = useState<'move' | 'copy'>('move')`
   - 兩段切換「移動｜複製」，放在週排班表標題列（上一週／下一週那一排）附近；只有草稿版本（`canEditSelectedVersion`）才顯示。
   - 切換時給一則提示：複製模式「拖曳班次會在新位置多排一班，原本的班次保留」；移動模式「拖曳班次會把它搬到新位置」。

2. **`performDrop(src, targetEmployeeId, targetDate)` 依模式分流**
   - 移動：維持現在的 `schedulesApi.update(src.id, { ...srcBase, employee, schedule_date })`。
   - 複製：改呼叫 `schedulesApi.create(...)`，欄位同「新增排班」對話框送出的內容：
     `schedule_version`、`employee: targetEmployeeId`、`schedule_date: targetDate`、
     `shift_template: src.shiftTemplateId`、`expected_hours`（用班別時數）、`status: 'assigned'`。
     **備註不要帶過去**（備註通常是針對原本那一班寫的）。
   - 前面的檢查兩種模式都要保留，順序不變：
     - 放回同一格（同員工同日期）→ 不動作。
     - 目標格已有同班別 → 「無法合併相同班別」。
     - 目標員工設定「不可排國定假日」且當天是假日 → 擋下。
     - 目標日有已核准請假 → `runWithLeaveWarning` 先問一次（`handleDrop` 已經包好，不用動）。
     - 跨版本重疊 → 只提示不擋。
   - 成功提示：複製用「已複製班次」之類的文案，不要沿用「移動成功」。

3. **拖曳中的視覺**
   - 移動模式：維持現狀。
   - 複製模式：原本那張卡片不要淡出（它會留在原地），拖曳中的卡片加一個「＋複製」小標記。
     用原生 HTML5 拖曳的話可設 `ev.dataTransfer.effectAllowed = 'copy'`／`dropEffect = 'copy'`，游標會變成加號。

4. **員工月班表對話框**（`EmployeeMonthScheduleDialog.tsx`）是唯讀的，不用動。

## 手機 App 的做法（供對照）

- 切換鈕放在週表頭第二列（和「僅看違規／僅看提醒」同一列），只有草稿版本顯示，預設「移動」。
- 複製時走跟「新增排班」同一段邏輯，所以上面那些檢查完全一樣。
- 實測（本機後端）：移動 → `PATCH schedules/schedules/{id}/`，班次總數不變；
  複製 → `POST schedules/schedules/` 回 201，班次多一筆、原班次仍在。
