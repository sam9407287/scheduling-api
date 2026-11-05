# 交接 2026-11-05：Android v0.2.0 ＋ iOS 新客戶端

本次對話做了什麼、驗到哪裡、還剩什麼。三個 repo 都已 push。

## 1. 完成

### 前端（Frank 維護，本次只 pull）
- `scheduling-frontend` 拉到 `fdafe89`（11/01），線上 bundle 同版。沒有改任何檔案。

### Android `scheduling-android`（最新 `14dd041`，v0.2.0 / versionCode 2）
把網頁 10/12–11/01 的功能全部移植：
- 每日班別計畫三步精靈（期間含排休摘要 → 星期範本／複製上一張班表／長按設上限 → 逐日微調日曆、不開班日、假日底色）；工具列「每日班別」；漏設天數提醒；AI 排班前沒有計畫先擋並導去設定；400 `shift_plan_required` 中文訊息
- 國定假日表頭底色與名稱（補假日藍色）；員工排班意願徽章；`forbid` 員工拖進假日格或指派時前端擋下；`prefer` 顯示 ★
- 合規檢查：人力覆蓋（不足／超過上限／未開班）、表頭 coverage 徽章、`holiday_work_refused` / `holiday_scheduling`、一例一休文案依員工同意與否
- AI 對話框：排休摘要＋每人配額表、同日多班 checkbox
- 帳號或 Email 登入、Email 驗證碼註冊分頁、無機構提示、密碼欄顯示／隱藏
- 修掉舊 bug：JSON 不送預設值導致建立版本 400、debug 自動登入競態、DRF 欄位錯誤只顯示 Bad Request、全螢幕 Dialog 底部按鈕被手勢列蓋住
- `scripts/smoke.sh [base_url] [user] [pass] [shot_dir]`：安裝 APK、自動登入、走每個畫面截圖、檢查 OkHttp 狀態碼、對 Railway 做連線探測

### iOS `scheduling-ios`（新 repo，GitHub private `sam9407287/scheduling-ios`，最新 `55d14a0`）
- SwiftUI、iOS 17+，檔案與畫面一對一對齊 Android（同色票、文案、拖放機制）
- 專案用 `xcodegen generate` 從 `project.yml` 產生，`.xcodeproj` 不進版控
- `SchedulingAppUITests/SmokeTests.swift`：三個 XCUITest（排班流程含拖曳／建版本→精靈／登出→註冊），`TEST_RUNNER_SMOKE_*` 環境變數自動登入，`SMOKE_READONLY=1` 跳過寫入流程
- 測試揪出並修掉：`duration_hours` 字串解碼失敗、登出不切頁、同一 view 疊多個 `.alert` 吞掉動作、無員工時表頭撐寬畫面、密碼欄 AutoFill 吃掉輸入
- 兩個 README 都有建置與 smoke 步驟

### Smoke 結果

| 環境 | Android | iOS |
|---|---|---|
| 本地 SQLite（admin/admin123） | `scripts/smoke.sh` 10/10 | XCUITest 3/3 |
| Railway 生產（admin，唯讀） | 登入、API 全 200、合規、AI、每日班別、切版本正常（截圖＋logcat 判讀） | XCUITest 排班流程 1/1，寫入兩案自動跳過 |

生產資料觀察：幸福長照機構 8 草稿／4 已簽核；「2026 11月」44 筆班次、計畫夜 4／早 9／中 9、合規 1 違規＋75 班次人力不足；「2026 10月」（11/2 單日）沒有計畫，App 正確提示。生產資料沒有被更動。

### 安裝包 `~/Desktop/scheduling-builds/2026-11-05/`
- `scheduling-android-0.2.0-release.apk`（手機直接裝，debug key 簽）、`-debug.apk`
- `scheduling-ios-0.2.0-device-unsigned.app.zip`（真機，未簽章，需自己憑證簽或用 Xcode 跑）、`-simulator-debug.app.zip`
- `SHA256SUMS.txt`、`README.md`

## 2. 待辦

### 下一步建議
1. **真機安裝驗收**：Android 直接裝 APK；iOS 在 `project.yml` 填 `DEVELOPMENT_TEAM` 後 `xcodegen generate`，Xcode 選 Team 跑真機。
2. **Android smoke 腳本對生產的斷言不可靠**：連 Railway 時 uiautomator 一直回 `null root node`（本地正常），腳本的文字判定會誤報失敗；要查原因或改成純截圖比對。
3. **iOS 拖曳缺邊緣自動捲動**（Android 有）。
4. **Google 登入**兩平台都還沒做（需 Firebase Android/iOS app、SHA-1、`google-services.json` / `GoogleService-Info.plist`）。
5. **其他頁面**（員工／出勤／加班／請假／設定）App 從未有過，仍以網頁版為準。
6. 正式簽章 keystore（Android）與 App Store 設定（iOS）。

### 本地測試資料（可清）
本地 SQLite 多了：版本「2026 10月」「2026 9月」「2026 11月」、SMOKE-nobranch 兩筆、使用者 `apk.smoke.*@example.com` / `ios.smoke.*@example.com`、員工 1–3 的假日意願被改成 forbid／prefer／avoid。刪除動作當時被權限分類器擋下，沒有清。

### 環境備註
- 生產 `admin` 密碼只在對話中出現，沒有寫進任何 repo、腳本、記憶檔。
- 權限分類器會擋：`railway ssh`（連唯讀查詢）、帶帳密的 curl 打生產、`gh repo create`、刪本地 DB 資料。需要時由 Sam 在終端機自己跑。
- Android 模擬器 `Medium_Phone_API_35`（/data 剩約 370MB）與本地 `runserver 8000` 在對話結束時仍在背景執行，可直接關掉。

### 既有待辦（本次未動，見 memory `project_current_status.md`）
- Railway 缺 SMTP 三個變數（`EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` / `DEFAULT_FROM_EMAIL`），註冊驗證碼不會真的寄出
- 刪 `~/Downloads/intelligent-scheduling-system-firebase-adminsdk-fbsvc-e196d378d1.json`
- PM #5a / #7 / #5b 待釐清；MVP P0「簽核時自動合規檢查」、reject 機制、出勤彙總、證照到期提醒未做
- `PM_FEEDBACK_BACKLOG`、`FEATURE_STATUS`、`BUSINESS_FLOWS` §12 等文件狀態過期
