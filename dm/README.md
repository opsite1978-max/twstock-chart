# 降血糖藥健保導航 · Type 2 Diabetes NHI Navigator

第二型糖尿病用藥決策工具：輸入病人的糖化血色素（HbA1c）、估算腎絲球過濾率（eGFR）、尿液白蛋白／肌酸酐比值（UACR）、共病與目前用藥，即時列出符合台灣健保藥品給付規定的選項、依腎功能調整的起始與最大劑量，以及可複製的病歷記載草稿。

A clinical decision aid for type 2 diabetes in Taiwan. It checks each drug class against the National Health Insurance reimbursement rules (sections 5.1 and 2.16), adjusts doses for kidney function, and drafts a chart note.

## 功能

- **新診斷**：核對診斷標準，HbA1c ≥8.0% 建議雙藥起始，只推薦健保允許起始的組合。
- **追蹤病人**：點選目前用藥與劑量、過往檢驗紀錄趨勢、eGFR 每年下降速度、HbA1c 連續未達標次數。
- **用藥選擇**：每個藥標示推薦／健保可申報／條件未滿／避免／自費，點選後顯示必須一起停用的藥。
- **用藥建議**：調整後處方表、腎臟風險分級（KDIGO）、病歷草稿。

## 三種使用方式

| 方式 | 檔案 | 說明 |
| --- | --- | --- |
| 網頁版（手機、電腦） | `index.html` | 開啟 GitHub Pages 網址即可使用；手機可「加入主畫面」，第一次開啟後離線也能用 |
| Windows 程式 | `app/` | 由 GitHub Actions 自動產生免安裝 .exe |
| 單一檔案 | `index.html` | 直接下載後用瀏覽器開啟 |

## 第一次設定 GitHub Pages（只需做一次）

1. 進入這個儲存庫，點上方 **Settings**。
2. 左側選 **Pages**。
3. 「Build and deployment」的 Source 選 **Deploy from a branch**，Branch 選 **main**、資料夾選 **/ (root)**，按 **Save**。
4. 約 1 分鐘後，頁面上方會出現網址，例如 `https://帳號.github.io/dm-nhi-navigator/`。

手機加入主畫面：
- iPhone：用 Safari 開網址 → 分享 → 加入主畫面。
- Android：用 Chrome 開網址 → 右上角選單 → 安裝應用程式（或加到主畫面）。

## 取得 Windows .exe

1. 點上方 **Actions** → 左側 **Build Windows app** → 右側 **Run workflow**。
2. 約 5 分鐘完成後，點進該次執行，最下方 **Artifacts** 下載 `DM-NHI-Navigator-windows`（zip 內即為 .exe）。
3. 首次執行若出現「Windows 已保護您的電腦」，點「其他資訊」→「仍要執行」。

## 修改內容

- 網頁版只要改 `index.html`；改完後把 `sw.js` 第 2 行的 `dm-nhi-v1` 加 1（例如 `dm-nhi-v2`），手機才會抓到新版。
- Windows 版改 `app/index.html`，推送後會自動重新產生 .exe。
- 可以在 GitHub 網頁上直接編輯（點檔案 → 鉛筆圖示），不需要安裝任何程式。

## 規定依據

- 全民健康保險藥品給付規定 5.1 糖尿病用藥（114/6/1 修訂）
- 2.16 dapagliflozin、empagliflozin 用於心衰竭與慢性腎臟病（114/3/1 修訂）
- 腎功能劑量依各藥仿單、KDIGO 2022 糖尿病合併慢性腎臟病指引、美國糖尿病學會 2026 照護標準

規定整理至 115/10/20。

## 免責聲明

本工具僅供臨床決策參考，不取代醫師判斷，也不保證健保核付。開立前請以健保署最新給付規定、院所用藥品項與仿單為準。工具不會儲存或傳送任何病人資料，所有計算都在使用者自己的瀏覽器中進行。

This tool is for clinical reference only. It does not replace physician judgment or guarantee reimbursement. No patient data is stored or transmitted.
