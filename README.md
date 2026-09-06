<p align="center">
  <img src="docs/assets/hero.svg" alt="ToolRush — 終結工具呼叫稅" width="100%"/>
</p>

<p align="center">
  <a href="#the-problem"><img src="https://img.shields.io/badge/status-已上線運作-22c55e?style=flat-square" alt="shipped and live"/></a>
  <a href="v2/README.md"><img src="https://img.shields.io/badge/version-2.1%20(macOS)-f97316?style=flat-square" alt="v2.1 macOS"/></a>
  <a href="v2/evidence/"><img src="https://img.shields.io/badge/tests-206%20passed%20(upstream)-4ade80?style=flat-square" alt="206 tests passed (upstream)"/></a>
  <img src="https://img.shields.io/badge/platform-macOS%20%2F%20Windows-38bdf8?style=flat-square" alt="macOS / Windows"/>
</p>

現代 AI Agent 模型串流輸出 Token 的速度，往往比宿主環境讀取一個檔案還要快。效能瓶頸早已不再是每秒生成多少 Token（TPS），而是**工具呼叫稅（Tool-Call Tax）**：每一次呼叫 `read_file`、`search_files` 與 `terminal`，背後都在為原本只要幾微秒的操作承受反覆開關行程（Process Spawn）、Shell 往返跳轉、包裝層序列化與重複分發的沈重負擔。

**ToolRush 徹底終結這項代價。** 它是專為 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 打造的低開銷執行層：完全保留原生工具介面、相同的輸出規格與安全防禦邊界，同時提供極低延遲傳輸、真正的程式化批次並行能力，以及跨版本升級相容機制。

<p align="center">
  <img src="docs/assets/benchmarks.svg" alt="實測基準圖表：原生 Shell 與 ToolRush 於正式環境之對比" width="100%"/>
</p>

## 實測成果（於真實安裝之 Hermes 環境測量 — 無 Mock、無假數據）

### 1. macOS 實測數據（Apple Silicon M 系列 · Hermes Agent v0.21.0）

| 執行面向 | 原生 Hermes (Cold Spawn) | ToolRush v2 (Warm Shell) | 提速幅度 |
|---|---:|---:|---:|
| **Terminal 呼叫延遲 (中位數)** | 45.57 ms | 7.96 ms | **5.72x (降低 82.5% 開銷)** |
| **底層 Raw Bash 執行** | 3.08 ms | 1.07 ms | **2.88x 提速** |
| **批次多目標搜尋 (`search_files` 4 目標)** | 230.50 ms | 28.92 ms | **7.97x (節省 87.5% 耗時)** |
| **行程管理與清理** | 頂層 PID 終止（易殘留孤兒） | POSIX Process Group 連鎖回收 (`killpg`) | **健全回收中斷與逾時子程序** |

### 2. Windows 實測數據（原作者基準）

| 執行面向 | 改造前 | 改造後 | 提速幅度 |
|---|---:|---:|---:|
| **原生檔案讀取** | 255.23 ms | 4.44 ms | **57.5x** |
| **常駐暖 Shell** (Persistent Bash) | 285 ms | 12.1 ms | **23.6x** |
| **搜尋傳輸** (直連 `rg`) | 183–455 ms | 27–97 ms | **4.7–6.8x** |
| **批次並行 RPC** | 108 ms (循序) | 53 ms (批次) | **2.1x** (重疊度 3.3x) |

*以上皆為「工具實際執行耗時（Wall Time）」，並非包含模型思考的完整回合時間。這反映最真實的情況：在密集調用工具的回合能感受到極大提速，純對話回合則維持原狀。完整數據、p95 分布與測試方法請參閱 [`v2/README.md`](v2/README.md)。*

---

## 系統架構

<p align="center">
  <img src="docs/assets/architecture.svg" alt="五大核心面向：原生讀取、直連 rg、常駐暖 Shell、批次並行 RPC、更新存活" width="100%"/>
</p>

1. **單一搜尋核心，極速傳輸**：不以粗糙邏輯重寫搜尋。直連 `rg` 二進制檔，完整保留 `.gitignore` 規則、正則語法、Context 旗標與設定檔；原生檔案讀取完全復用 upstream 的邊界限制、安全守衛、二進制/文件路由與輸出組合器。
2. **正確性先於速度**：修復搜尋結果結尾損壞 JSON 的問題；分頁具備穩定排序與「更多結果」標記；正則反斜線與前置連字號維持字面量解析；統一處理換行（CRLF）與末行無換行的情況。
3. **真正的程式化並行**：在 `execute_code` 中提供 `from hermes_tools import parallel`。單次 RPC 可並行分發 1–16 個唯讀操作至最多 4 個工作執行緒，嚴格維持輸入順序，並完整保持鑑權、白名單與呼叫配額限制。寫入與終端指令一律拒絕並行。
4. **健全的串流常駐暖 Shell（Warm-Shell）**：維護單一持久化 bash，透過 OS pipe 串流傳輸，具備有限記憶體解析、原子快照提交、精確保留 Exit Code/CWD/環境變數。在 macOS/POSIX 上透過 `os.setsid` 與 `os.killpg` 乾淨終止取消與逾時的指令樹；在 Windows 上維持專屬行程管理。
5. **強化調度准入防禦**：靜態檢查拒絕隱式寫入（`wget`、`curl -o`、`sed w`、分支建立、環境包裝腳本、共用 CWD 異動）。准入不等於授權：被拒絕加速的操作依然會以安全標準循序流程執行。
6. **更新存活機制**：外掛於記憶體中動態掛載相容 patch，不修改 upstream 源碼。遇到未知變動時主動降級發出警告，絕不覆蓋新版官方程式碼。
7. **完整診斷與回滾機制**：提供 `doctor.py --smoke`、各面向獨立開關（`TOOLRUSH_*=0`）、主開關 `toolrush.enabled: false`，以及完整可重現的基準測試腳本。

---

## macOS 一鍵安裝方式

開啟終端機貼上以下指令，即可從本 Fork 自動下載並啟用：

```bash
mkdir -p ~/.hermes/plugins && git clone --depth=1 https://github.com/lunkerchen/toolrush.git /tmp/tr-install && cp -r /tmp/tr-install/v2/plugin ~/.hermes/plugins/toolrush && rm -rf /tmp/tr-install && hermes plugins enable toolrush
```

安裝完成後於下次啟動 Hermes Agent 時即刻生效。

---

## 驗收與證據

<p align="center">
  <img src="https://img.shields.io/badge/regression-206%20passed%20%C2%B7%200%20failed%20%C2%B7%200%20skipped-4ade80?style=for-the-badge&logo=pytest&logoColor=white" alt="206 passed"/>
  <img src="https://img.shields.io/badge/negative%20controls-5%20fail%20on%20revert-f97316?style=for-the-badge" alt="5 negative controls"/>
  <img src="https://img.shields.io/badge/live%20activation-verified%20in%20running%20kernel-38bdf8?style=for-the-badge" alt="live activation verified"/>
</p>

- **通過 206 個回歸測試案例**（Upstream 基準），0 失敗、0 跳過。
- **5 組對照反向測試（Negative Controls）**：當加速修復被還原時皆如預期觸發失敗，杜絕虛假通過。
- **實機端到端驗證**：已於實際運行的 `execute_code` 核心中成功驗證 `parallel` RPC，重啟後設定與憑證經 SHA-256 驗證位元組完全一致。
- 完整合約判決、XML 證據與原始基準測試數據見 [`v2/evidence/`](v2/evidence/)。

---

## 專案結構

| 路徑 | 內容說明 |
|---|---|
| [`v2/`](v2/README.md) | **正式交付實作** — 完整報告、設計文件、MANIFEST (sha256)、外掛本體、安裝源碼快照、測試證據 |
| [`toolrush.py`](toolrush.py) | v1 實驗性運行時（fast_read / batch_read、持久連線池、session 快取） |
| [`toolrush_search.py`](toolrush_search.py) · [`toolrush_exec.py`](toolrush_exec.py) | wave-3 行程內搜尋 · wave-2 常駐 shell 執行器 |
| `bench_*.py`, `dissect_*.py` | 用於分析與命名「工具呼叫稅」的 Profile 與 Benchmark 腳本 |
| `validation-contract*.md` | 每一階段的驗證合約文件（Contract-first） |
| `results.md`, `*.json` | 實測證據數據 — 絕無任何虛構數字 |

---

## 核心守則

- **先立合約再寫程式**：無對照測試即視為未完成。
- **實驗室絕不直改正式環境程式碼**：先於原型驗證，再透過開關與外掛安全整合。
- **與官方標準路徑維持位元組級結果一致**：輸出格式不符即不予發布。
- **全線安全預設（Fail-Closed）**：被拒絕加速的操作退回安全循序路徑，絕不報錯中斷。
- **所有證據留存於儲存庫中**：堅持百分之百實測數據。

---

為 Nous Research 打造之 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 所設計。
macOS / POSIX 支援分支由 [Laban Chen](https://github.com/lunkerchen) 移植並維護（[PR #1](https://github.com/OnlyTerp/toolrush/pull/1)）。
