# Google 登入串接指南（給前端工程師）

> 2026-09-17。回應你的 `GOOGLE_LOGIN_BACKEND_API_HANDOFF_2026-09-16.md`。
> 後端已實作並部署；本文說明 MVP 採用的方案與你要做的事。

## 一、MVP 產品決策（與你文件的差異）

Sam 拍板：**MVP 階段任何 Google 帳號登入都直接成為 manager**。
（2026-09-23 更新）**不再自動開機構**——新帳號進系統後自己建立機構，
建立的機構自動綁定為該帳號的租戶，靠既有機構隔離看不到別人的資料。因此：

- **不做邀請流程**（你文件 §7 的 invitations/link/unlink 全部不做）——
  使用者只有管理者、量小，且後端目前沒有真實寄信服務。
- **email 綁定取代邀請**：後端只信 Firebase 驗證過的 email
  （`email_verified == true`），預建帳號的綁定見下節。
- 你文件的安全清單（§12）與錯誤碼精神（§9）已採納實作。
- 帳密（Token）登入照舊並存，當 fallback。

## 二、後端實際行為（已上線）

收到 `Authorization: Bearer <firebase-id-token>` 時依序：

1. **`firebase_uid` 已存在** → 該 User 登入（回訪）。
2. **首次登入，verified email 恰好對到一個未綁定的既有帳號** →
   回填 `firebase_uid` 綁定，**保留原本的 role / organization / branch**
   （例：Sam 預建的 admin 帳號用 Google 登入後仍是 admin）。
3. **全新帳號** → 建立 manager 角色 User，**`organization` 為 null**
   （2026-09-23 起不再自動開機構）。前端導向「建立機構」頁，
   `POST /api/organizations/organizations/` 建立後自動綁定（詳
   FRONTEND_API.md §1）。該帳號從此只看得到自己機構的資料。
4. 檢查 `is_active`，停用帳號一律拒絕。

錯誤碼（`AuthenticationFailed` detail 為 `{code, message}`）：

| HTTP | code | 情境 |
|---|---|---|
| 401 | `invalid_firebase_token` | token 無效/過期/專案不符 |
| 403 | `email_not_verified` | Google email 未驗證 |
| 403 | `account_inactive` | 系統帳號已停用 |

`GET /api/auth/users/me/` 新增 `firebase_linked: boolean`。

## 三、你要做的事

1. Firebase Console（intelligent-scheduling-system）→ Authentication →
   Sign-in method → 啟用 **Google** provider。
2. **產生 service account key JSON 傳給 Sam**（專案設定 → 服務帳戶 →
   產生新的私密金鑰）——後端要靠它驗票，沒有這把鑰匙生產環境的
   Google 登入不會通。
3. LoginPage 加「使用 Google 登入」按鈕：`signInWithPopup(auth, new
   GoogleAuthProvider())`（popup 被擋時 fallback `signInWithRedirect`）。
   你的 `client.ts` 攔截器已會自動帶 token，登入後照舊打 `/users/me/`。
4. **建議登入頁兩種模式並存**（同一頁：Google 按鈕＋帳密表單走
   `/api/auth/login/`），不要用 `VITE_AUTH_MODE` 整包二選一——
   帳密是 fallback，Google/Firebase 掛掉時還能進系統。
5. build env：填 `VITE_FIREBASE_*`（你的 Firebase 專案 config）。

## 四、新帳號的空白狀態

（2026-09-23 更新）新 manager 進來時 `/me` 的 `organization` 是 **null**：
先導向「建立機構」頁（`POST /api/organizations/organizations/`，body 只需
`{"name": "..."}`，code 可省略）；已有機構的帳號再建會收 409
`organization_already_exists`，請隱藏入口。建完機構後仍是**全新空機構**：
沒有員工、班別、班表，首頁請確保空資料時有合理的引導畫面
（建員工/建班別的入口），不要白屏。

## 五、之後要升級時

未來若要限制「只有受邀者能登入」，只需把後端第 3 步（自動開通）
關掉改回拒絕——你文件的邀請制設計保留為 Phase 2 選項，資料模型
不衝突。
