[![MseeP.ai Security Assessment Badge](https://mseep.net/pr/lawchat-oss-mcp-taiwan-legal-db-badge.png)](https://mseep.ai/app/lawchat-oss-mcp-taiwan-legal-db)

# mcp-taiwan-legal-db

[English](README.en.md) · **繁體中文**

台灣法規、裁判書、憲法法庭裁判 — MCP Server。

讓任何 MCP 相容的 AI 助手直接存取台灣公開法律資料：

- **司法院裁判書** — judgment.judicial.gov.tw（全文搜尋 + 取得）
- **全國法規資料庫** — law.moj.gov.tw（11,700+ 部法規）
- **憲法法庭** — cons.judicial.gov.tw（868 筆大法官解釋 + 憲判字，含理由書全文，離線快取）

以 Python 搭配 [FastMCP](https://github.com/modelcontextprotocol/python-sdk) 寫成。純工具 wrapper 只打上述三個台灣官方來源，不會發送任何其他網路請求。

---

## 特色

| 功能 | 說明 |
|------|------|
| **8 個 MCP 工具** | 裁判書搜尋/全文、法規查詢、釋字/憲判字查詢、引用關係圖譜 |
| **離線快取** | 868 筆大法官解釋與憲判字（含理由書/意見書全文）從本地 JSON 即時回傳 |
| **引用關係圖譜** | 從理由書抽取所有引用的釋字/憲判字，追溯憲法學說演變 |
| **全文搜尋** | 裁判書關鍵字搜尋 + 釋字爭點/理由書全文搜尋 |
| **混合請求策略** | 預設用 httpx 直打（~0.25s），觸發司法院 F5 WAF 時自動以 Playwright 刷 cookie 後繼續 |

---

## ⚡ 快速上手

照順序執行下列指令。Linux / macOS / WSL + Python 3.10+ 可直接複製貼上。

```bash
# 1. Clone repo
git clone https://github.com/lawchat-oss/mcp-taiwan-legal-db.git
cd mcp-taiwan-legal-db

# 2. 建立並初始化虛擬環境
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .

# 3. 安裝 Playwright Chromium（僅在司法院 WAF 觸發時使用，一般查詢不會啟動）
.venv/bin/playwright install chromium

# 4. 驗證伺服器可以啟動並註冊 8 個工具
.venv/bin/python -c "
import asyncio
from mcp_server.server import mcp
print('Server:', mcp.name)
tools = asyncio.run(mcp.list_tools())
print('Tools:', [t.name for t in tools])
assert len(tools) == 8, f'Expected 8 tools, got {len(tools)}'
print('✓ Setup OK')
"
```

**預期輸出：**
```
Server: 台灣法律資料庫
Tools: ['search_judgments', 'get_judgment', 'query_regulation', 'get_pcode', 'search_regulations', 'get_interpretation', 'search_interpretations', 'get_citations']
✓ Setup OK
```

上面沒報錯就完成了。Repo 根目錄已經帶一份 `.mcp.json`，**任何在此資料夾內開的 Claude Code session 會自動載入這個 server**，不需要額外註冊。

---

## 有什麼工具可以用

8 個 MCP 工具，全部唯讀，全部只打台灣政府的公開資料庫。

### 法規與裁判

| 工具 | 用途 | 典型呼叫 |
|---|---|---|
| `search_judgments` | 搜尋司法院裁判書資料庫 | `search_judgments(keyword="預售屋 遲延交屋", case_type="民事")` |
| `get_judgment` | 依 JID 或 URL 取得單筆判決全文 | `get_judgment(jid="TPSM,114,台上,3753,20251112,1")` |
| `query_regulation` | 查詢法規條文／範圍／全文／修法沿革 | `query_regulation(law_name="民法", article_no="184")` |
| `get_pcode` | 將法規名稱解析為 pcode（法規代號） | `get_pcode(law_name="律師法")` |
| `search_regulations` | 以關鍵字搜尋 11,700+ 部法規 | `search_regulations(keyword="勞動")` |

### 憲法法庭

| 工具 | 用途 | 典型呼叫 |
|---|---|---|
| `get_interpretation` | 大法官解釋/憲判字全文（離線快取） | `get_interpretation("釋字748", reasoning_keyword="婚姻")` |
| `search_interpretations` | 搜尋釋字/憲判字（爭點 + 理由書全文） | `search_interpretations(keyword="集會自由")` |
| `get_citations` | 引用關係圖譜（往前追溯） | `get_citations("釋字748", include_context=True)` |

### 工具細節

<details>
<summary><b><code>search_judgments</code></b></summary>

搜尋司法院判決系統。支援：

- **精確案號查詢**（快，HTTP GET）：設定 `case_word` + `case_number` + `year_from`
- **全文關鍵字搜尋**：設定 `keyword`
- **裁判主文篩選**：`main_text="被告應將 移轉"` + `keyword="借名登記"` → 找被告敗訴的借名登記案
- 可依 `court`、`case_type`（民事／刑事／行政／懲戒）、`year_from`／`year_to` 過濾
- 結果自動依法院層級排序（最高 → 高等 → 地方）

**重要**：要查某個特定案號時，**一定**要用 `case_word`+`case_number`，不要放進 `keyword`。

```python
# ✅ 正確 — 查 114 台上 3753 最高法院
search_judgments(case_word="台上", case_number="3753", year_from=114, court="最高法院")

# ✅ 正確 — 全文搜尋
search_judgments(keyword="預售屋 遲延交屋")

# ❌ 錯 — 把案號放進 keyword
search_judgments(keyword="114年度台上字第3753號")
```
</details>

<details>
<summary><b><code>get_judgment</code></b></summary>

取得單筆判決的結構化全文。

- 輸入：`jid`（從 `search_judgments` 結果取得）或 `url`
- 輸出：`{case_id, court, date, main_text, facts, reasoning, cited_statutes, cited_cases, full_text, source_url}`
- HTTP GET data.aspx 取得全文
- 結果快取 30 天

```python
get_judgment(jid="TPSM,114,台上,3753,20251112,1")
```

單筆判決可能超過 1 萬 token。建議先用 `search_judgments` 取得 metadata，只在使用者明確需要時才抓全文。
</details>

<details>
<summary><b><code>query_regulation</code></b></summary>

查詢全國法規資料庫。

```python
# 單一條文
query_regulation(law_name="民法", article_no="184")

# 條文範圍
query_regulation(law_name="民法", from_no="184", to_no="198")

# 完整法規
query_regulation(law_name="律師法")

# 附修法沿革
query_regulation(law_name="勞動基準法", article_no="23", include_history=True)
```

支援 `law_name`（透過 `get_pcode` 自動解析 pcode）或直接傳 `pcode`。子條文如 `247-1`、`15-1` 都支援。
</details>

<details>
<summary><b><code>get_interpretation</code></b></summary>

取得大法官解釋（釋字第 1–813 號）或憲法法庭裁判（憲判字）全文。預設層從本地 JSON 快取即時回傳。

**分層設計**（節省 context）：

| 層級 | 觸發條件 | 離線？ |
|------|---------|-------|
| 預設層（字號/日期/爭點/解釋文） | 永遠回傳 | ✓ |
| 理由書片段 | `reasoning_keyword="關鍵字"` | ✓ |
| 理由書全文（最多 15,000 字） | `include_reasoning=True` | ✓ |
| 意見書片段 | `opinions_keyword="關鍵字"` | ✓ |
| 意見書全文 | `include_opinions=True` | ✓ |

```python
# 預設層（離線，~0ms）
get_interpretation("釋字748")

# 理由書中搜尋關鍵字
get_interpretation("釋字748", reasoning_keyword="婚姻自由")

# 在意見書中定位特定大法官
get_interpretation("釋字499", opinions_keyword="林子儀")

# 新制憲判字
get_interpretation("111年憲判字第1號")
```

建議先用 keyword 片段模式定位，只在需要時才開全文模式。
</details>

<details>
<summary><b><code>search_interpretations</code></b></summary>

搜尋大法官解釋與憲判字。關鍵字同時匹配標題、爭點、理由書全文。

```python
# 全文搜尋（搜爭點 + 理由書）
search_interpretations(keyword="集會自由")

# 篩選年度（新制）
search_interpretations(keyword="言論自由", year=112)

# 列舉最後 10 筆釋字
search_interpretations(number_from=804, number_to=813)
```
</details>

<details>
<summary><b><code>get_citations</code></b></summary>

從理由書中抽取所有引用的釋字/憲判字字號。追溯方向：查詢指定裁判**引用了哪些先前裁判**。

```python
get_citations("釋字748")
# → citations: [釋字第242號, 釋字第362號, 釋字第365號, ...]

# 附上引用前後 80 字片段
get_citations("釋字748", include_context=True)
```
</details>

---

## 範例問法

```
「查民法第 184 條」
「搜尋跟預售屋遲延交屋有關的最高法院判決」
「釋字 748 的理由書重點是什麼」
「哪些大法官解釋討論過集會自由」
「釋字 748 引用了哪些先前的釋字」
「查 111 年憲判字第 1 號」
```

---

## 註冊到你的 Claude client

依你使用的 Claude client 選對應的段落。

### Claude Code (CLI)

Claude Code 會自動載入專案根目錄的 `.mcp.json`。這個 repo 已經內建一份：

```json
{
  "mcpServers": {
    "taiwan-legal-db": {
      "command": ".venv/bin/python",
      "args": ["-m", "mcp_server.server"]
    }
  }
}
```

**零設定**：`cd` 進 repo 之後跑 `claude` 就好。MCP server 列表會看到 `taiwan-legal-db`，而且此資料夾不會有其他多餘的 server。

**跟隊友分享**：`.mcp.json` 已經 commit 進 repo。任何人 clone 下來跟著 Quick Start 跑完，就會自動完成 MCP 註冊。

**加到其他專案**（你想在另一個資料夾用這個 MCP）：用 `claude mcp add` 以 project scope 加入：

```bash
cd /path/to/your/other/project
claude mcp add taiwan-legal-db --scope project -- \
  /absolute/path/to/mcp-taiwan-legal-db/.venv/bin/python \
  -m mcp_server.server
```

這會在你另一個專案的根目錄寫出一份 `.mcp.json`。想在每個專案都能用，把 `--scope project` 改成 `--scope user`。

### Claude Desktop (macOS / Windows)

Claude Desktop 使用一個全域設定檔：

- **macOS**：`~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**：`%APPDATA%\Claude\claude_desktop_config.json`
- **Windows (Microsoft Store / WinGet / MSIX 安裝)**：`C:\Users\<YourName>\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`

**最快開啟方式**：在 Claude Desktop 點選單列（不是視窗）→ **Settings** → **Developer** → **Edit Config**。檔案若不存在 Claude Desktop 會自動建立。

在 `mcpServers` 下加入以下內容（跟已有內容合併）：

```json
{
  "mcpServers": {
    "taiwan-legal-db": {
      "command": "/absolute/path/to/mcp-taiwan-legal-db/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/absolute/path/to/mcp-taiwan-legal-db"
    }
  }
}
```

把 `/absolute/path/to/mcp-taiwan-legal-db` 換成你的實際 clone 路徑。`cwd` 欄位必填，Python 才找得到 `mcp_server` 套件。

**存檔後，完全關閉並重新開啟 Claude Desktop**（不是只關視窗 — macOS 用 ⌘Q、Windows 右鍵工具列圖示 → Quit）。設定檔只會在重啟時重新載入。

### Claude Cowork (Pro 以上方案)

Claude Cowork 跑在 Claude Desktop 裡面，**共用同一個 `claude_desktop_config.json`** — 沒有另外的 Cowork 設定檔。任何你在 Claude Desktop 註冊的 MCP server 會自動透過 Claude Desktop SDK 橋接進 Cowork 的沙盒 VM。

**設定步驟**：

1. 照上面 **Claude Desktop** 段落把 `taiwan-legal-db` 加進 `claude_desktop_config.json`
2. **完全關閉並重新開啟 Claude Desktop** — 同時也會重啟 Cowork
3. 開一個 Cowork session，`taiwan-legal-db` 的工具就可以用了

**注意**：Cowork 目前在 Claude Pro / Max / Team / Enterprise 方案都可以用，且只能存取你明確授權的資料夾。MCP server 本身跑在你的 host 上（不是 Cowork VM 裡面），透過 Desktop SDK bridge 溝通，所以不管你授權哪個資料夾給 Cowork，它都存取得到內建的資料檔。

### 其他 MCP 相容 client

任何符合 [Model Context Protocol 規範](https://modelcontextprotocol.io/) 的 MCP client 都可以使用這個 server。啟動指令永遠是：

```
.venv/bin/python -m mcp_server.server
```

⋯⋯加上 `cwd` 設定為 repo 根目錄（Python 才找得到 `mcp_server` 套件）。設定位置請參考你使用的 client 的文件，找 `mcpServers` JSON 區塊寫在哪裡。

---

## 疑難排解

**`ModuleNotFoundError: No module named 'mcp_server'`**
→ 你沒有在 venv 裡面跑 `pip install -e .`。回到 Quick Start 步驟 2。

**`FileNotFoundError: data/pcode_all.json`**
→ 內建的 `mcp_server/data/pcode_all.json` 不見或被刪了。用 `git checkout mcp_server/data/pcode_all.json` 還原，或觸發重新下載：
```bash
.venv/bin/python -m mcp_server.updater
```

**MCP client 回報「伺服器啟動失敗」**
→ 直接跑 Quick Start 步驟 3 的驗證指令。若失敗，代表 import chain 壞了 — 看 traceback。若通過，問題在 MCP client 的啟動設定（路徑或 cwd 錯了）。

**`ssl.SSLCertVerificationError: ... Missing Subject Key Identifier`**
→ 這是 OpenSSL 3.6+ 對 TWCA Global Root CA 的廣泛 rejection，**不是 certifi 舊的問題**。本 repo 透過 [`truststore`](https://github.com/sethmlarson/truststore) 套件讓 Python 改用作業系統原生的 trust store（macOS Security framework、Windows CryptoAPI、Linux 系統 CA），**所有路徑都保留完整 SSL 驗證（`verify=True`）**，不使用 `verify=False`。這在 macOS、Windows 以及 OpenSSL <3.6 的 Linux 都能正常工作。OpenSSL 3.6+ 的 Linux 環境（Fedora 40+、未來的 Ubuntu LTS）目前可能仍有問題，歡迎 issue 回報。

---

## WAF 處理機制

司法院 `judgment.judicial.gov.tw` 部署了 F5 BIG-IP ASM WAF，純 HTTP 請求可能被擋（回固定 245 bytes 的 "Request Rejected"）。

本專案採混合策略：

- 預設用 httpx 直接請求（~0.25s）
- 偵測到被擋（response 含 `Request Rejected` 或 JS challenge marker `bobcmn` / `TSPD`）自動 fallback 到 Playwright 跑一次 JS challenge
- 取得 TSPD cookies 後持久化到 `mcp_server/data/.judicial_cookies.json`（0600 權限，已 gitignore）
- 後續查詢繼續用 httpx 帶 cookies 執行

`cons.judicial.gov.tw`（釋字）跟 `law.moj.gov.tw`（法規）沒這個問題，不經過 WAF 流程。

---

## 資料來源與統計

所有資料都取自台灣政府**公開**資料庫。不會對外做其他網路呼叫：

| 來源 | 網域 | 說明 |
|------|------|------|
| 司法院裁判書系統 | judgment.judicial.gov.tw | 裁判書搜尋與全文 |
| 司法院憲法法庭 | cons.judicial.gov.tw | 大法官解釋與憲判字 |
| 全國法規資料庫 | law.moj.gov.tw | 法規條文與修法沿革 |

`mcp_server/config.py:ALLOWED_DOMAINS` 以硬編碼 allow-list 強制執行。伺服器會拒絕抓取任何不在這些網域的 URL。

### 憲法法庭資料統計

| 資料集 | 筆數 | 含理由書 | 含意見書 | 檔案大小 |
|--------|------|---------|---------|---------|
| 舊制釋字（old_cases.json） | 813 | 734 | 370 | 7.4 MB |
| 新制憲判字（new_cases.json） | 55 | 55 | 55 | 1.8 MB |

## 快取

| 資料類型 | TTL | 位置 |
|---|---|---|
| 判決全文 | 30 天 | `mcp_server/data/cache/legal_mcp.db`（SQLite，首次啟動時建立） |
| 搜尋結果 | 24 小時 | 同上 |
| 法規條文 | 7 天 | 同上 |
| pcode metadata | 30 天 | 同上 |
| 釋字/憲判字 | 本地 JSON（不過期） | `mcp_server/data/old_cases.json`、`new_cases.json` |

全部清除：刪掉 `mcp_server/data/cache/legal_mcp.db`。快取檔在 `.gitignore` 內。

## pcode_all.json 自動更新

伺服器啟動時會檢查 `mcp_server/data/pcode_all.json` 的時間戳。如果最後一次更新在最近的週六之前，會在背景觸發從 `law.moj.gov.tw` 官方 API 重新抓取。失敗會記為 warning，不會阻擋啟動。

手動更新：
```bash
.venv/bin/python -m mcp_server.updater
```

---

## 專案結構

```
mcp-taiwan-legal-db/
├── .gitignore
├── .mcp.json              # 資料夾內 Claude Code session 自動註冊用
├── LICENSE                # MIT（程式碼）
├── DATA_LICENSE           # CC0 1.0（憲法法庭資料）
├── SOURCES.md             # 資料來源說明
├── CITATION.cff           # 學術引用格式
├── README.md              # 本檔（繁體中文）
├── README.en.md           # English version
├── pyproject.toml         # 套件 metadata 與相依
└── mcp_server/
    ├── __init__.py
    ├── server.py          # FastMCP 入口 — 定義 8 個 @mcp.tool() function
    ├── config.py          # URL、法院代碼、快取 TTL、allowed domains
    ├── updater.py         # 獨立的 pcode_all.json 更新 script
    ├── cache/db.py        # SQLite 快取層
    ├── data/
    │   ├── pcode_all.json          # 11,700+ 部法規（內建，~780 KB）
    │   ├── law_histories.json      # 修法沿革（內建，~9.6 MB）
    │   ├── old_cases.json          # 813 筆舊制釋字全文（內建，~7.4 MB）
    │   └── new_cases.json          # 55 筆新制憲判字全文（內建，~1.8 MB）
    ├── models/            # Judgment / Regulation dataclass
    ├── parsers/           # 判決與法規頁面的 HTML parser
    ├── tools/
    │   ├── judicial_search.py      # search_judgments
    │   ├── judicial_doc.py         # get_judgment
    │   ├── regulations.py          # query_regulation, get_pcode, search_regulations
    │   └── constitutional_court.py # get_interpretation, search_interpretations, get_citations
    └── tests/             # pytest 測試
```

## 執行測試

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest mcp_server/tests/ -v
```

---

## 關於

由 [LawChat](https://lawchat.com.tw) 維護 — 一個台灣法律 AI 平台。

- 官網：[lawchat.com.tw](https://lawchat.com.tw)
- 聯絡：opensource@lawchat.com.tw
- 回報問題：[GitHub Issues](https://github.com/lawchat-oss/mcp-taiwan-legal-db/issues)

Best-effort 維護 — 我們會盡量跟上 upstream（司法院、法務部）頁面變動，但不保證 issue 的回覆時效。

## 授權

**程式碼**：[MIT License](LICENSE)

**憲法法庭資料**：[CC0 1.0](DATA_LICENSE)（公有領域貢獻）— 任何人皆可自由使用、修改及散布，無需取得授權或署名。學術引用格式請參考 [CITATION.cff](CITATION.cff)。

裁判書與法規資料來源：[司法院](https://judgment.judicial.gov.tw)、[法務部](https://law.moj.gov.tw)（政府公開資料）。
憲法法庭資料來源：[司法院憲法法庭](https://cons.judicial.gov.tw)（依中華民國著作權法第 9 條屬公有領域）。詳見 [SOURCES.md](SOURCES.md)。

## 免責聲明

This is an **unofficial** tool for querying publicly-available Taiwan legal databases. It is not affiliated with, endorsed by, or authorized by the Judicial Yuan, the Ministry of Justice, or any Taiwan government agency.

The data returned by this tool reflects the state of the upstream official sources at the time of query. It may be cached (see TTLs above), and **must not be treated as legal advice or a substitute for the authoritative official sources**. Always verify against the original sources before relying on the data for any legal or official purpose.

本工具為**非官方**的台灣公開法規資料查詢工具，與司法院、法務部或任何台灣政府機關無隸屬關係。查詢結果以上游官方資料庫當下狀態為準（且可能被快取 — 見上方 TTL 表），**不得作為法律意見或正式用途依據**，使用前請向官方資料庫驗證。
