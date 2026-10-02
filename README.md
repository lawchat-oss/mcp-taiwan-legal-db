# mcp-taiwan-legal-db

[English](https://github.com/lawchat-oss/mcp-taiwan-legal-db/blob/main/README.en.md) · **繁體中文**

台灣法規、裁判書、憲法法庭裁判、行政函釋、判解、訴願決定與立法理由 — MCP Server。

讓任何 MCP 相容的 AI 助手直接存取台灣公開法律資料：

- **司法院裁判書** — judgment.judicial.gov.tw（全文搜尋 + 取得）
- **全國法規資料庫** — law.moj.gov.tw（11,700+ 部法規）
- **憲法法庭** — cons.judicial.gov.tw（871 筆大法官解釋 + 憲判字，含理由書全文，離線快取）
- **行政機關函釋** — 法務部、勞動部、衛福部、金管會、環境部、工程會、財政部、經濟部、內政部等 13 個官方系統（即時查詢）
- **判解** — 司法院法學資料檢索系統（最高法院決議、法律問題座談、停止適用判例、院字／院解字、大法庭）
- **訴願決定與處分書** — 行政院訴願決定、公平會處分書（PDF 全文擷取）
- **立法理由** — 立法院法律系統，每一條歷次修正的條文與理由

以 Python 搭配 [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) 寫成。純工具 wrapper，只連線台灣政府官方來源（詳見下方「資料來源與統計」），不發送任何其他網路請求；憲法法庭資料為內建離線打包。

---

## 特色

| 功能 | 說明 |
|------|------|
| **15 個 MCP 工具** | 裁判書搜尋/全文、法規查詢、釋字/憲判字查詢、引用關係圖譜、行政函釋、決議／座談／判例、訴願決定／處分書、立法理由 |
| **離線快取** | 871 筆大法官解釋與憲判字（含理由書全文，以及從官網 PDF 擷取的大法官意見書全文）從本地資料即時回傳 |
| **引用關係圖譜** | 從理由書抽取所有引用的釋字/憲判字，追溯憲法學說演變 |
| **全文搜尋** | 裁判書關鍵字搜尋 + 釋字爭點/理由書全文搜尋 |
| **混合請求策略** | 預設用 httpx 直打（~0.25s），觸發司法院 F5 WAF 時自動以 Playwright 刷 cookie 後繼續 |

---

## ⚡ 安裝（PyPI，推薦）

```bash
pip install mcp-taiwan-legal-db
```

> **Debian / Ubuntu / WSL 注意**：系統 Python 受 PEP 668 保護，直接 `pip install` 會被擋。請改用：
> - `pipx install mcp-taiwan-legal-db`（推薦，自動建隔離 venv，CLI tool 標準裝法）
> - 或 `pip install --user --break-system-packages mcp-taiwan-legal-db`

> **Windows / 企業部署**：建議用 [uv](https://docs.astral.sh/uv/) 或 pipx 裝成獨立工具，不碰系統 Python 的 site-packages：
> ```powershell
> uv tool install mcp-taiwan-legal-db
> uv tool update-shell   # 把工具目錄加進 PATH，重開終端機後生效
> ```
> 套件目錄只讀不寫：查詢快取、WAF cookies 與每週更新的法規代碼表都寫在每位使用者自己的目錄（Windows：`%LOCALAPPDATA%\mcp-taiwan-legal-db`；macOS / Linux：`~/.cache/mcp-taiwan-legal-db`），所以裝到 `C:\Program Files` 等全使用者共用位置也能用。要改位置可設環境變數 `MCP_TAIWAN_LEGAL_DB_HOME`。

裝完後 entry point `mcp-taiwan-legal-db` 會在 PATH 上。**接到 Claude Code**（任何專案都能用）：

```bash
claude mcp add taiwan-legal-db mcp-taiwan-legal-db --scope user
```

接著 `/mcp` 重啟連線、Claude 就會在自然語言查詢時自動用 15 個 MCP tool。

**Chromium（司法院 WAF fallback）**：v1.1.0 起會在第一次需要時自動下載安裝，不用手動處理。無法連外下載的環境請預先安裝：

```bash
uvx --from mcp-taiwan-legal-db playwright install chromium    # 僅在司法院 WAF 觸發時使用，平時 idle
```

---

## 開發環境設置

下面是 clone 來修程式 / 跑測試的流程：

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

# 4. 驗證伺服器可以啟動並註冊 15 個工具
.venv/bin/python -c "
import asyncio
from mcp_server.server import mcp
print('Server:', mcp.name)
tools = asyncio.run(mcp.list_tools())
print('Tools:', [t.name for t in tools])
assert len(tools) == 15, f'Expected 15 tools, got {len(tools)}'
print('✓ Setup OK')
"
```

**預期輸出：**
```
Server: 台灣法律資料庫
Tools: ['search_judgments', 'get_judgment', 'query_regulation', 'get_pcode', 'search_regulations', 'get_interpretation', 'search_interpretations', 'get_citations', 'search_agency_interpretations', 'get_agency_interpretation', 'search_precedents', 'get_precedent', 'search_administrative_decisions', 'get_administrative_decision', 'get_legislative_history']
✓ Setup OK
```

上面沒報錯就完成了。Repo 根目錄已經帶一份 `.mcp.json`，**任何在此資料夾內開的 Claude Code session 會自動載入這個 server**，不需要額外註冊。

---

## 有什麼工具可以用

15 個 MCP 工具，全部唯讀，全部只打台灣政府的公開資料庫。

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

### 行政函釋與判解

| 工具 | 用途 | 典型呼叫 |
|---|---|---|
| `search_agency_interpretations` | 搜尋各部會行政函釋（13 個官方來源，即時查詢） | `search_agency_interpretations(keyword="加班費", agency="勞動部")` |
| `get_agency_interpretation` | 取得函釋全文（主旨、說明、相關法條、編註） | `get_agency_interpretation("moj:FE393340")` |
| `search_precedents` | 搜尋決議、法律問題座談、停止適用判例、司法解釋（院字/院解字）、大法庭裁定 | `search_precedents(keyword="借名登記", category="決議")` |
| `get_precedent` | 取得判解全文（含編註，例如「不再援用」） | `get_precedent("D:A,20170214,001")` |

### 訴願、處分與立法理由

| 工具 | 用途 | 典型呼叫 |
|---|---|---|
| `search_administrative_decisions` | 搜尋行政院訴願決定、公平會處分書（全文檢索） | `search_administrative_decisions(keyword="個人資料", source="訴願")` |
| `get_administrative_decision` | 取得決定書／處分書全文（由官網 PDF 擷取） | `get_administrative_decision("ey:A-115-000633")` |
| `get_legislative_history` | 某一條文歷次制定、修正時的條文與立法理由 | `get_legislative_history("勞動基準法", "24")` |

### 工具細節

<details>
<summary><b><code>search_judgments</code></b></summary>

搜尋司法院判決系統。支援：

- **精確案號查詢**（快，HTTP GET）：設定 `case_word` + `case_number` + `year_from`
- **全文關鍵字搜尋**：設定 `keyword`
- **裁判主文篩選**：`main_text="被告應將 移轉"` + `keyword="借名登記"` → 找被告敗訴的借名登記案
- 可依 `court`、`case_type`（民事／刑事／行政／懲戒）、`year_from`／`year_to` 過濾
- 結果自動依法院層級排序（最高 → 高等 → 地方）
- **資料涵蓋範圍**：民國 89 年（2000）起接近完整，81–88 年（1992–1999）僅零星收錄，80 年（1991）以前查無。這是司法院系統本身的收錄範圍，本工具不做任何年份裁切

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

# 附修法沿革；指定條號時另回傳該條歷次條文（article_history）
query_regulation(law_name="勞動基準法", article_no="24", include_history=True)
```

指定條號並開啟 `include_history` 時，`article_history.revisions` 會列出該條每次制定、增訂、修正、刪除的日期與當時條文，可直接前後對照。只讀取修法沿革中動到該條的歷史版本（例如民法第 184 條只需 36 個版本中的 5 個），版本清單與歷史版本全文都會快取；讀取失敗的版本會列在 `failed_versions` 並標 `partial`。

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
| 單份意見書全文 | `opinion_document="許宗力"` | ✓ |
| 超過 15,000 字的意見書續讀後段 | `opinions_offset=15000`（值取自回傳的 `opinions_next_offset`） | ✓ |

```python
# 預設層（離線，~0ms）
get_interpretation("釋字748")

# 理由書中搜尋關鍵字
get_interpretation("釋字748", reasoning_keyword="婚姻自由")

# 在意見書中定位特定大法官
get_interpretation("釋字758", opinions_keyword="湯德宗")

# 只讀某位大法官的完整意見書（意見書合計過長被截斷時）
get_interpretation("釋字758", opinion_document="許宗力")

# 單份意見書超過 15,000 字被截斷時，用回傳的 opinions_next_offset 續讀
get_interpretation("釋字777", opinion_document="吳陳鐶", opinions_offset=15000)

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

<details>
<summary><b><code>search_agency_interpretations</code> / <code>get_agency_interpretation</code></b></summary>

各部會函釋分散在各自的系統，沒有共用 API。這個工具在查詢當下同時向下列官方系統查詢，合併後依發文日期排序；同一件函釋在多個來源出現時只保留一筆（以機關自己的系統為準）：

| 來源 | 內容 |
|---|---|
| 法務部主管法規查詢系統 | 行政函釋、法規諮詢意見 |
| 勞動部勞動法令查詢系統 | 行政函釋、解釋令 |
| 衛生福利法規檢索系統 | 行政函釋 |
| 金管會主管法規查詢系統 | 行政規則（解釋令、函收在這一類） |
| 環境部主管法規查詢系統 | 行政函釋 |
| 工程會政府採購法規解釋函令 | 採購法令解釋令、函 |
| 財政部各稅法令函釋檢索系統 | 稅務法令彙編、新頒令釋 |
| 經濟部商業發展署 商工行政法規 | 公司法、商業登記法、商業會計法、有限合夥法函釋 |
| 經濟部智慧財產局 | 著作權解釋令函 |
| 內政部戶政司 | 戶籍、國籍、姓名等法令解釋 |
| 內政部國土管理署 | 解釋函彙編（建築管理、都市計畫、住宅） |
| 司法院法學資料檢索系統 | 跨機關行政函釋（司法院、法務部及其他機關） |
| 行政院公報 | 各部會依行政程序法第 159 條第 2 項第 2 款發布的解釋性規定（交通部、教育部等沒有專屬函釋系統的機關從這裡查） |

```python
# 全部來源
search_agency_interpretations(keyword="個人資料", year_from=113, year_to=114)

# 指定機關（可用逗號分隔多個；簡稱如「金管會」「衛福部」也可以）
search_agency_interpretations(keyword="加班費", agency="勞動部")
search_agency_interpretations(keyword="私募", agency="金管會")
search_agency_interpretations(keyword="核釋", agency="交通部")   # → 行政院公報中交通部的解釋令

# 用發文字號找
search_agency_interpretations(doc_number="法律字第11403512580號")

# 讀全文（id 取自搜尋結果）
get_agency_interpretation("moj:FE393340")
```

回傳的 `categories` 列出每個來源／類別的總筆數與是否有下一頁；某個來源暫時連不上時，該類別帶 `error`，其他來源照常回傳。全文省略正本、副本受文者清單。國土管理署與智慧局官方只提供全量清單，第一次查詢會下載到使用者資料目錄（分別約 16 MB、13 MB），之後每週／每天更新一次。
</details>

<details>
<summary><b><code>search_precedents</code> / <code>get_precedent</code></b></summary>

查司法院法學資料檢索系統裡、裁判書系統（`search_judgments`）查不到的判解：

| 類別 | 內容 |
|---|---|
| 決議 | 最高法院民刑事庭會議決議、最高行政法院聯席會議決議（108 年大法庭制度施行前） |
| 法律問題座談 | 各級法院法律座談會、公證法律問題研討、懲戒法律問題座談 |
| 停止適用判例 | 依法院組織法第 57 條之 1 停止適用、已無裁判全文的判例（僅存判例要旨） |
| 司法解釋 | 大理院解釋、最高法院解釋、司法院院字／院解字解釋 |
| 大法庭 | 最高法院、最高行政法院大法庭裁定 |

```python
search_precedents(keyword="借名登記")                      # 五類全部
search_precedents(keyword="情事變更", category="決議,司法解釋")
get_precedent("D:A,20170214,001")                          # 最高法院 106 年度第 3 次民事庭會議
```

引用決議、判例前請看 `fields` 裡的編註（例如「不再援用」）。站方每類最多提供前 500 筆，筆數多時請加關鍵字或年度縮小範圍。
</details>

<details>
<summary><b><code>search_administrative_decisions</code> / <code>get_administrative_decision</code></b></summary>

| 來源 | 內容 |
|---|---|
| 行政院訴願審議委員會 | 近 10 年的訴願決定書（PDF 全文）。108 年以前收辦的案件官網未遮蔽訴願人姓名，暫不列出 |
| 公平交易委員會 | 處分書及不處分決議書（約 5,800 件，PDF 全文） |

```python
search_administrative_decisions(keyword="個人資料", source="訴願")
search_administrative_decisions(doc_number="公處字第115060號")    # 依字號精確查詢
get_administrative_decision("ey:A-115-000633")                    # 由 PDF 擷取全文
```

PDF 無法擷取文字時（多為 2008 年以前的公平會舊檔）回傳 `pdf_url` 讓使用者自行開啟。公平會把關鍵字中的空白視為詞組的一部分，多個詞請分次查。
</details>

<details>
<summary><b><code>get_legislative_history</code></b></summary>

從立法院法律系統取得某一條文每次制定、修正時的條文與**立法理由**（民國 59 年以後的修正才有理由）。適合回答「這條為什麼這樣規定」「當初修法的目的」；`query_regulation(include_history=True)` 回傳的是條文變遷，這裡多了立法理由。

```python
get_legislative_history("勞動基準法", "24")   # 73 年制定、105、107 年修正，各版條文與理由
get_legislative_history("民法", "1030-1")     # 民法在立法院系統分編收錄，會自動對應到「民法第四編親屬」
get_legislative_history("刑法", "339-4")      # 簡稱會轉成正式名稱「中華民國刑法」
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
「勞動部對加班費有哪些函釋」
「工程會對不發還押標金有什麼解釋」
「最高法院有沒有關於借名登記的決議」
「查院解字第 3829 號」
「行政院有哪些個資相關的訴願決定」
「勞基法第 24 條當初為什麼這樣修」
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

## 在這個 server 上面建 A2A agent

想用 A2A agent 驅動這些工具？請見 [`examples/agno-bindu/`](examples/agno-bindu/) — 一個社群貢獻的 A2A agent 範例。

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
- 取得 TSPD cookies 後持久化到使用者資料目錄的 `.judicial_cookies.json`（0600 權限）
- 後續查詢繼續用 httpx 帶 cookies 執行

`cons.judicial.gov.tw`（釋字）跟 `law.moj.gov.tw`（法規）沒這個問題，不經過 WAF 流程。

---

## 資料來源與統計

查詢時連網的都是台灣政府的**公開**資料庫，伺服器本身不建資料庫，只在使用者查詢的當下向官方網站取資料：

| 來源 | 網域 | 用途 |
|------|------|------|
| 司法院裁判書系統 | judgment.judicial.gov.tw | 裁判書搜尋與全文（`FJUD/Default_AD.aspx`、`data.aspx`） |
| 全國法規資料庫 | law.moj.gov.tw | 法規條文與修法沿革（`LawClass/*`） |
| 司法院憲法法庭 | cons.judicial.gov.tw | 資料包建置後才公布的憲判字（其餘釋字／憲判字為離線資料） |
| 司法院法學資料檢索系統 | legal.judicial.gov.tw | 決議、法律問題座談、停止適用判例、司法解釋、大法庭、跨機關行政函釋 |
| 法務部主管法規查詢系統 | mojlaw.moj.gov.tw | 行政函釋、法規諮詢意見 |
| 勞動部勞動法令查詢系統 | laws.mol.gov.tw | 行政函釋、解釋令 |
| 衛生福利法規檢索系統 | mohwlaw.mohw.gov.tw | 行政函釋 |
| 工程會政府採購法規解釋函令 | planpe.pcc.gov.tw | 採購法令解釋令、函 |
| 財政部各稅法令函釋檢索系統 | ttc.mof.gov.tw | 稅務法令彙編、新頒令釋 |
| 經濟部商業發展署 | gcis.nat.gov.tw | 商工行政法規函釋 |
| 經濟部智慧財產局 | www.tipo.gov.tw | 著作權解釋令函（開放資料） |
| 內政部戶政司 | www.ris.gov.tw | 戶政法令解釋 |
| 內政部國土管理署 | www.nlma.gov.tw | 解釋函彙編 |
| 金管會主管法規查詢系統 | law.fsc.gov.tw | 行政規則（解釋令、函） |
| 環境部主管法規查詢系統 | oaout.moenv.gov.tw | 行政函釋 |
| 行政院公報資訊網 | gazette.nat.gov.tw | 各部會解釋性規定 |
| 行政院訴願審議委員會 | appeal.ey.gov.tw | 訴願決定書 |
| 公平交易委員會 | www.ftc.gov.tw | 處分書及不處分決議書 |
| 立法院法律系統 | lis.ly.gov.tw | 立法沿革與立法理由 |

`get_judgment` 接受使用者傳入的 URL，`mcp_server/config.py:ALLOWED_DOMAINS` 以硬編碼 allow-list 限制只能是裁判書與法規兩個網域；其他工具只連上表固定網址，不接受任意 URL。司法院法學資料檢索系統、衛福部系統與行政院訴願網站的 robots.txt 不允許爬蟲，本工具對這些來源只做使用者觸發的單次查詢，不批次抓取。函釋、決議、訴願決定、處分書等皆屬公文，依著作權法第 9 條不受著作權保護。

**裁判書年份涵蓋範圍**：本工具即時代理司法院系統，沒有自己的資料庫，有效年份 = 司法院收錄範圍。實測（以「竊盜」為關鍵字計數）民國 89 年（2000）起每年數萬筆，81–88 年（1992–1999）合計約 2,000 筆，80 年（1991）以前為零。司法院公告其開放資料檔「收錄範圍與裁判書查詢系統相同」，因此沒有更早的公開來源。查詢 2000 年以前的裁判請預期查無或零星。

憲法法庭資料（釋字／憲判字）**不在查詢時連網取得** — 它是離線打包的（`old_cases.json`／`new_cases.json`／`opinions.zip`），來源為 `cons.judicial.gov.tw`，由維護腳本離線重建。詳見 [SOURCES.md](SOURCES.md)。

### 憲法法庭資料統計

| 資料集 | 筆數 | 含理由書 | 含意見書 | 檔案大小 |
|--------|------|---------|---------|---------|
| 舊制釋字（old_cases.json） | 813 | 734 | 472 | 7.4 MB |
| 新制憲判字（new_cases.json） | 58 | 58 | 57 | 2.0 MB |
| 大法官意見書全文（opinions.zip） | 1,541 份 | — | 1,541 份有全文 | 10.8 MB |

釋字 401 號以後與憲判字的大法官意見書，官網只以 PDF 附件公開，已擷取文字打包進 `opinions.zip`，查詢回傳的 `opinion_documents` 會列出每份意見書的標題、官網 PDF 連結與字數。其中 22 份 PDF 的字型無法解碼或頁面為圖片（主要是釋字 735–753 號的部分意見書），改以頁面影像逐字轉錄（回傳時標註 `transcribed`，引用前請核對官網 PDF）。意見書以憲法法庭網站公布的 PDF 為準；全國法規資料庫收錄的早期意見書是事後編修的版本（用字統一、修正筆誤、當事人姓名去識別化），兩者文字可能略有出入。重建方式見 `scripts/build_opinions.py`；官網公布新的憲判字後，用 `scripts/build_new_cases.py` 只補新案（含意見書）。

## 快取

| 資料類型 | TTL | 位置 |
|---|---|---|
| 判決全文 | 30 天 | 使用者資料目錄的 `legal_mcp.db`（SQLite，首次啟動時建立） |
| 搜尋結果 | 24 小時 | 同上 |
| 法規條文 | 7 天 | 同上 |
| pcode metadata | 30 天 | 同上 |
| 釋字/憲判字 | 本地資料（不過期） | `mcp_server/data/old_cases.json`、`new_cases.json`、`opinions.zip` |

使用者資料目錄：Windows 為 `%LOCALAPPDATA%\mcp-taiwan-legal-db`，macOS / Linux 為 `~/.cache/mcp-taiwan-legal-db`（有設 `XDG_CACHE_HOME` 則在其下），可用環境變數 `MCP_TAIWAN_LEGAL_DB_HOME` 改位置。全部清除：刪掉該目錄下的 `legal_mcp.db`。

## pcode_all.json 自動更新

伺服器啟動時會檢查 `pcode_all.json` 的時間戳。如果最後一次更新在最近的週六之前，會在背景觸發從 `law.moj.gov.tw` 官方 API 重新抓取，結果（連同 `law_histories.json`）寫進使用者資料目錄，不動套件內建檔；讀取時內建檔與使用者副本取較新者。失敗會記為 warning，不會阻擋啟動。

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
    ├── server.py          # MCPServer 入口 — 定義 15 個 @mcp.tool() function
    ├── config.py          # URL、法院代碼、快取 TTL、allowed domains
    ├── updater.py         # 獨立的 pcode_all.json 更新 script
    ├── cache/db.py        # SQLite 快取層
    ├── data/
    │   ├── pcode_all.json          # 11,700+ 部法規（內建，~780 KB）
    │   ├── law_histories.json      # 修法沿革（內建，~9.6 MB）
    │   ├── old_cases.json          # 813 筆舊制釋字全文（內建，~7.4 MB）
    │   ├── new_cases.json          # 58 筆新制憲判字全文（內建，~2.0 MB）
    │   └── opinions.zip            # 大法官意見書全文，由官網 PDF 擷取（內建，~10.8 MB）
    ├── models/            # Judgment / Regulation dataclass
    ├── parsers/           # 判決與法規頁面的 HTML parser
    ├── tools/
    │   ├── judicial_search.py      # search_judgments
    │   ├── judicial_doc.py         # get_judgment
    │   ├── regulations.py          # query_regulation, get_pcode, search_regulations
    │   ├── constitutional_court.py # get_interpretation, search_interpretations, get_citations
    │   ├── agency_interpretations.py # search_agency_interpretations, get_agency_interpretation
    │   ├── fint.py                 # search_precedents, get_precedent（司法院法學資料檢索系統）
    │   ├── admin_decisions.py      # search_administrative_decisions, get_administrative_decision
    │   ├── legislative.py          # get_legislative_history（立法院法律系統）
    │   └── pdf_text.py             # PDF 文字擷取（意見書、訴願決定書、處分書共用）
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

**在本 server 之上建構的應用**：本專案是台灣公開法律來源的資料存取層。任何基於它建構的 agent、應用程式或服務（包含 [`examples/`](examples/) 內的範例），須自行負責其行為、輸出正確性與對使用者的聲明。
