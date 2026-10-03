# mcp-taiwan-legal-db

[English](https://github.com/lawchat-oss/mcp-taiwan-legal-db/blob/main/README.en.md) · **繁體中文**

台灣法規、裁判書、憲法法庭裁判與卷宗、行政函釋、判解、訴願與準司法決定、立法資料、司法統計與法學文獻 — MCP Server。

讓任何 MCP 相容的 AI 助手直接存取台灣公開法律資料：

- **司法院裁判書** — judgment.judicial.gov.tw（全文搜尋 + 取得，附歷審清單）
- **全國法規資料庫** — law.moj.gov.tw（11,700+ 部法規，含官方英譯、最新公布日與施行日註記）
- **憲法法庭** — cons.judicial.gov.tw（871 筆大法官解釋 + 憲判字，含理由書全文，離線快取；受理中案件、言詞辯論、法庭之友與卷內書狀即時查詢）
- **行政機關函釋** — 法務部、勞動部、衛福部、財政部、經濟部、內政部、金管會、交通部、中央銀行、人事總處、消保處、考試院系統、臺北市、新北市等 45 個官方來源，含智慧局專利、商標審查基準；標出官網的「停止適用」與「現行」標示（即時查詢）
- **判解** — 司法院法學資料檢索系統（最高法院決議、法律問題座談、停止適用判例、院字／院解字、大法庭、精選裁判）
- **訴願與準司法決定** — 行政院、各部會與縣市政府訴願決定，公平會處分書、勞動部不當勞動行為裁決、保訓會復審／再申訴決定、金管會裁罰、工程會採購申訴、監察院案件、律師懲戒決議
- **立法資料** — 每一條歷次修正的條文與立法理由、立法歷程、立法院議案（含審查中草案）與公報紀錄、法規命令草案預告
- **統計與量刑** — 司法統計年報／月報、法務統計、《犯罪狀況及其分析》、司法院量刑資訊系統的刑度統計
- **法學文獻** — 司法院專題研究報告（含司法研究年報）、國圖期刊論文索引、GRB 研究計畫、開放取用法學期刊
- **其他規範** — 地方自治法規、條約協定與租稅協定、證交所／櫃買中心／期交所規章

以 Python 搭配 [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) 寫成。純工具 wrapper，只連線下方「資料來源與統計」列出的官方網站（政府機關，以及國家圖書館、中研院、國立大學、證券交易所等公共機構），不發送任何其他網路請求；釋字／憲判字資料為內建離線打包。

---

## 特色

| 功能 | 說明 |
|------|------|
| **26 個 MCP 工具** | 裁判書搜尋/全文/歷審、法規查詢（含英譯與修法追蹤）、釋字/憲判字查詢、引用關係圖譜、憲法法庭卷宗、行政函釋與審查基準、判解、訴願與準司法決定、立法理由與立法紀錄、統計與量刑、法學文獻、地方法規與條約 |
| **離線快取** | 871 筆大法官解釋與憲判字（含理由書全文，以及從官網 PDF 擷取的大法官意見書全文）從本地資料即時回傳 |
| **引用關係圖譜** | 從理由書抽取所有引用的釋字/憲判字（往前追溯），或列出後來引用某件的釋字/憲判字（往後追溯），追溯憲法學說演變 |
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

接著 `/mcp` 重啟連線、Claude 就會在自然語言查詢時自動用 26 個 MCP tool。

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

# 4. 驗證伺服器可以啟動並註冊 26 個工具
.venv/bin/python -c "
import asyncio
from mcp_server.server import mcp
print('Server:', mcp.name)
tools = asyncio.run(mcp.list_tools())
print('Tools:', [t.name for t in tools])
assert len(tools) == 26, f'Expected 26 tools, got {len(tools)}'
print('✓ Setup OK')
"
```

**預期輸出：**
```
Server: 台灣法律資料庫
Tools: ['search_judgments', 'get_judgment', 'query_regulation', 'get_pcode', 'search_regulations', 'get_interpretation', 'search_interpretations', 'get_citations', 'search_agency_interpretations', 'get_agency_interpretation', 'search_precedents', 'get_precedent', 'search_administrative_decisions', 'get_administrative_decision', 'get_legislative_history', 'search_constitutional_docket', 'get_constitutional_case_file', 'search_legislative_records', 'get_legislative_record', 'search_statistics', 'get_statistics', 'get_sentencing_statistics', 'search_legal_literature', 'get_legal_literature', 'search_other_regulations', 'get_other_regulation']
✓ Setup OK
```

上面沒報錯就完成了。Repo 根目錄已經帶一份 `.mcp.json`，**任何在此資料夾內開的 Claude Code session 會自動載入這個 server**，不需要額外註冊。

---

## 有什麼工具可以用

26 個 MCP 工具，全部唯讀，全部只連線官方公開資料庫（見「資料來源與統計」）。

### 法規與裁判

| 工具 | 用途 | 典型呼叫 |
|---|---|---|
| `search_judgments` | 搜尋司法院裁判書資料庫 | `search_judgments(keyword="預售屋 遲延交屋", case_type="民事")` |
| `get_judgment` | 依 JID 或 URL 取得單筆判決全文與歷審清單 | `get_judgment(jid="TPSM,114,台上,3753,20251112,1")` |
| `query_regulation` | 查詢法規條文／範圍／全文／修法沿革／官方英譯 | `query_regulation(law_name="民法", article_no="184")` |
| `get_pcode` | 將法規名稱解析為 pcode（法規代號） | `get_pcode(law_name="律師法")` |
| `search_regulations` | 以關鍵字搜尋 11,700+ 部法規，或列出某日以後修正公布的法規 | `search_regulations(keyword="勞動")` |

### 憲法法庭

| 工具 | 用途 | 典型呼叫 |
|---|---|---|
| `get_interpretation` | 大法官解釋/憲判字全文（離線快取） | `get_interpretation("釋字748", reasoning_keyword="婚姻")` |
| `search_interpretations` | 搜尋釋字/憲判字（爭點 + 理由書全文） | `search_interpretations(keyword="集會自由")` |
| `get_citations` | 引用關係圖譜（往前或往後追溯） | `get_citations("釋字748", include_context=True)` |
| `search_constitutional_docket` | 受理中、排定言詞辯論、徵求法庭之友意見的案件 | `search_constitutional_docket(status="amicus")` |
| `get_constitutional_case_file` | 卷內文書：聲請書、答辯書、鑑定意見、法庭之友意見書、言詞辯論筆錄等 | `get_constitutional_case_file("113年憲判字第8號")` |

### 行政函釋與判解

| 工具 | 用途 | 典型呼叫 |
|---|---|---|
| `search_agency_interpretations` | 搜尋各機關行政函釋與智慧局審查基準（45 個官方來源，即時查詢；標出停止適用） | `search_agency_interpretations(keyword="加班費", agency="勞動部")` |
| `get_agency_interpretation` | 取得函釋全文（主旨、說明、相關法條、編註、效力標示） | `get_agency_interpretation("moj:FE393340")` |
| `search_precedents` | 搜尋決議、法律問題座談、停止適用判例、司法解釋（院字/院解字）、大法庭裁定、精選裁判 | `search_precedents(keyword="借名登記", category="決議")` |
| `get_precedent` | 取得判解全文（含編註，例如「不再援用」） | `get_precedent("D:A,20170214,001")` |

### 訴願與準司法決定

| 工具 | 用途 | 典型呼叫 |
|---|---|---|
| `search_administrative_decisions` | 搜尋訴願決定（行政院、各部會、縣市政府）與準司法機關的決定、處分 | `search_administrative_decisions(keyword="個人資料", source="行政院")` |
| `get_administrative_decision` | 取得決定書／處分書全文（由官網 HTML 或 PDF 擷取） | `get_administrative_decision("ey:A-115-000633")` |

### 立法資料

| 工具 | 用途 | 典型呼叫 |
|---|---|---|
| `get_legislative_history` | 某一條文歷次制定、修正時的條文與立法理由，附最近一次修正的立法歷程 | `get_legislative_history("勞動基準法", "24")` |
| `search_legislative_records` | 搜尋立法院議案（含審查中草案）、立法院公報、法規命令草案預告 | `search_legislative_records("勞動基準法", kind="bills")` |
| `get_legislative_record` | 取得議案、公報紀錄或草案預告全文 | `get_legislative_record("bill:202110226160000")` |

### 統計與法學文獻

| 工具 | 用途 | 典型呼叫 |
|---|---|---|
| `search_statistics` | 搜尋司法統計年報／月報、法務統計、《犯罪狀況及其分析》 | `search_statistics(keyword="收結", source="司法統計")` |
| `get_statistics` | 取得統計表內容或報告全文 | `get_statistics("moj:INF_COMMON_P/807")` |
| `get_sentencing_statistics` | 司法院量刑資訊系統的刑度統計（判決數、刑度平均與分布） | `get_sentencing_statistics(crime="竊盜")` |
| `search_legal_literature` | 搜尋司法院專題研究報告、國圖期刊論文索引、GRB 研究計畫、開放取用法學期刊 | `search_legal_literature("量刑", source="司法研究年報")` |
| `get_legal_literature` | 取得書目、摘要與全文（有公開全文時） | `get_legal_literature("ncl:A15001353")` |

### 其他規範

| 工具 | 用途 | 典型呼叫 |
|---|---|---|
| `search_other_regulations` | 搜尋全國法規資料庫以外的規範：地方自治法規、條約協定、交易所規章 | `search_other_regulations("違章建築", source="臺北市")` |
| `get_other_regulation` | 取得全文或單一條文 | `get_other_regulation("taichung:GL001385", article_no="3")` |

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
- 輸出：`{case_id, court, date, main_text, facts, reasoning, cited_statutes, cited_cases, full_text, source_url, history, history_note}`
- HTTP GET data.aspx 取得全文
- 全文快取 30 天；歷審清單會隨上訴變動，另外只快取 24 小時

```python
get_judgment(jid="TPSM,114,台上,3753,20251112,1")
# → history: 臺中地院 111 易 203 → 臺中高分院 113 上易 80 → … 各審級裁判（含 jid、url）
```

`history` 是司法院依案號串起的歷審清單，引用判決前先看後面還有沒有上級審裁判、是否已被廢棄或發回。`pending_supreme_court=true` 表示案件目前在最高法院／最高行政法院審理中；清單最後一筆之後沒有更高審級，不等於已經確定（可能仍在上訴期間內，或上級審裁判尚未上網）。

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

# 官方英譯（約 970 部法律與部分命令）
query_regulation(law_name="勞動基準法", article_no="24", language="en")
```

回傳的 `law` 另含 `last_amended`（最新公布日）與 `category`（主管機關分類）；有特殊施行日時含 `effective_date`／`effective_note`（如「自公布後六個月施行」「施行日期由行政院定之」），引用新修正條文前先看這兩欄確認是否已施行。這些欄位來自每週更新的 `law_meta.json`。

`language="en"` 回傳官方英譯本與 `english_version_date`；英譯常落後中文修正，版本較舊時 `note` 會提醒，法律效力以中文為準。英譯檔第一次查詢時下載到使用者資料目錄（約 16 MB），每週更新。

指定條號並開啟 `include_history` 時，`article_history.revisions` 會列出該條每次制定、增訂、修正、刪除的日期與當時條文，可直接前後對照。只讀取修法沿革中動到該條的歷史版本（例如民法第 184 條只需 36 個版本中的 5 個），版本清單與歷史版本全文都會快取；讀取失敗的版本會列在 `failed_versions` 並標 `partial`。

支援 `law_name`（透過 `get_pcode` 自動解析 pcode）或直接傳 `pcode`。子條文如 `247-1`、`15-1` 都支援。
</details>

<details>
<summary><b><code>search_regulations</code></b></summary>

以名稱關鍵字搜尋法規，每頁 50 筆，現行法規排在已廢止之前。每筆含 `last_amended` 與 `category`，可用來追蹤修法：

```python
search_regulations(keyword="勞動")
search_regulations(keyword="消費", exclude_abolished=True)

# 某日以後新制定／修正公布的法規（新到舊），可再依主管機關篩選
search_regulations(amended_since="2026-09-01")
search_regulations(amended_since="115-07-01", category="勞動部")
```
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

# 往後追溯：後來哪些釋字／憲判字的主文或理由書引用了這件
get_citations("釋字748", direction="cited_by")
# → cited_by: [釋字第763號, 釋字第791號, ...]
```

並列寫法「釋字第 477 號、第 747 號及第 762 號」會逐一收錄。`cited_by` 比對本地收錄的全部案件（不含意見書與資料包建置後才公布的新案）；要找引用某件的法院判決，改用 `search_judgments(keyword="釋字第748號")`。
</details>

<details>
<summary><b><code>search_constitutional_docket</code> / <code>get_constitutional_case_file</code></b></summary>

`get_interpretation` 只有已公布的裁判；這兩個工具查憲法法庭官網公開的案件進度與卷內文書（即時查詢）：

| `status` | 內容 |
|---|---|
| `pending`（預設） | 已受理、審理中的案件（受理日期、聲請人（人民以甲乙丙代稱）、案號、主案／併案、案由） |
| `hearing` | 已排定或已舉行言詞辯論、說明會的案件 |
| `amicus` | 目前公開徵求法庭之友意見的案件 |

```python
search_constitutional_docket(keyword="勞動")                     # 受理中、案由含「勞動」的案件
search_constitutional_docket(status="amicus")                    # 正在徵求法庭之友意見的案件

get_constitutional_case_file("113年憲判字第8號")                  # 列出卷內全部公開文件與言詞辯論公告
get_constitutional_case_file("113年憲判字第8號", keyword="人性尊嚴")  # 只列出內容含關鍵字的文件並附片段
get_constitutional_case_file(document_id="492306")               # 讀單一文件全文（PDF 擷取）
```

`case_id` 可以是憲判字、釋字、受理中案號（如「114年度憲立字第3號」）或 `search_constitutional_docket` 回傳的 id。關鍵字比對的是官方擷取的無標點文字，限憲判字與受理中案件；掃描檔的 OCR 可能有錯字，法庭之友意見書官方只公開前 20 頁。案件清單與卷宗頁在本機快取一天。
</details>

<details>
<summary><b><code>search_agency_interpretations</code> / <code>get_agency_interpretation</code></b></summary>

各機關函釋分散在各自的系統，沒有共用 API。這個工具在查詢當下同時向下列官方系統查詢（共 45 個來源；外交部、退輔會、核安會、國發會的行政規則多為內部作業要點，只在 `agency` 指名時查），合併後依發文日期排序；同一件函釋在多個來源出現時只保留一筆（以機關自己的系統為準）：

| 來源 | 內容 |
|---|---|
| 法務部主管法規查詢系統 | 行政函釋、法規諮詢意見 |
| 勞動部勞動法令查詢系統 | 行政函釋、解釋令 |
| 衛生福利法規檢索系統 | 行政函釋 |
| 環境部主管法規查詢系統 | 行政函釋 |
| 工程會政府採購法規解釋函令 | 採購法令解釋令、函 |
| 財政部各稅法令函釋檢索系統 | 稅務法令彙編、新頒令釋 |
| 財政部主管法規查詢系統 | 財政部與關務署、國有財產署、國庫署的核釋令與行政規則 |
| 經濟部主管法規查詢系統 | 經濟部本部及水利署、標準檢驗局、國際貿易署等的解釋令與行政規則 |
| 經濟部商業發展署 商工行政法規 | 公司法、商業登記法、商業會計法、有限合夥法函釋 |
| 經濟部智慧財產局 | 著作權解釋令函；專利審查基準（網頁版全文）、商標審查基準（PDF） |
| 經濟部標準檢驗局 | 解釋函令（商品檢驗、度量衡等） |
| 行政院人事行政總處 | 人事法令解釋（公務員任用、給與、休假等） |
| 行政院消費者保護處 | 消費者保護法函釋與法規諮詢意見（只比對標題與摘要） |
| 監察院陽光法令主題網 | 政治獻金法、公職人員利益衝突迴避法、財產申報法的主管機關函釋（只比對標題） |
| 內政部戶政司、國土管理署、地政司、消防署 | 戶籍與國籍、建築管理與都市計畫、地政（含已停止適用）、消防法令解釋 |
| 交通部法規系統 | 行政解釋（令、函、公告） |
| 中央銀行法規系統 | 行政令函 |
| 考試院主管法規共用系統 | 銓敘部、保訓會、考選部、考試院行政函釋 |
| 各部會主管法規共用系統 | 金管會、教育部、農業部、內政部、文化部、國科會、原民會、海委會、公平會、陸委會、中選會（含行政函釋）、主計總處的行政規則（解釋令、函收在這一類，結果會混有一般行政規則）；外交部、退輔會、核安會、國發會只在指名時查 |
| 臺北市法規查詢系統 | 臺北市政府解釋令函，以及該系統收錄的中央機關函釋 |
| 新北市法規查詢系統 | 新北市政府與中央機關函釋（依筆數取前 5 類列出，其餘只列筆數） |
| 司法院法學資料檢索系統 | 跨機關行政函釋（司法院、法務部及其他機關） |
| 行政院公報 | 各機關依行政程序法第 159 條第 2 項第 2 款發布的解釋性規定（國發會、NCC 等沒有專屬函釋系統的機關從這裡查） |

```python
# 全部來源
search_agency_interpretations(keyword="個人資料", year_from=113, year_to=114)

# 指定機關（可用逗號分隔多個；簡稱如「金管會」「衛福部」也可以）
search_agency_interpretations(keyword="加班費", agency="勞動部")
search_agency_interpretations(keyword="私募", agency="金管會")
search_agency_interpretations(keyword="時效取得", agency="地政司")
search_agency_interpretations(keyword="考績", agency="銓敘部")
search_agency_interpretations(keyword="專利要件", agency="專利")   # 智慧局審查基準只比對章名
search_agency_interpretations(keyword="加班費", agency="人事總處")
search_agency_interpretations(keyword="關係人", agency="陽光法令")

# 用發文字號找
search_agency_interpretations(doc_number="法律字第11403512580號")

# 讀全文（id 取自搜尋結果）
get_agency_interpretation("moj:FE393340")
```

**效力標示**：結果與全文的 `status` 是官網對該筆資料的標示，引用前必看。

| status | 意思 |
|---|---|
| `停止適用` | 官網標示已停止適用或廢止；`status_note` 附停止日期、依據的函或原標示（例如「本筆資料，依據…號函，自…停止適用」） |
| `部分停止適用` | 交通部的標示 |
| `適用中` | 只在官網有「現行／停止適用」兩態欄位的來源出現（勞動部、衛福部、考試院系統、環境部、地政司、各部會主管法規共用系統的行政規則）；財政部法令彙編收錄的函釋也標「適用中」（經重新研審保留適用，彙編後才廢止的官網不另標示） |
| 沒有 `status` | 官網沒有標示或沒標示，**不代表仍然有效**。戶政司、消防署、智慧局、行政院公報等官網完全沒有效力欄位；法務部、工程會、司法院法學檢索、臺北市、央行、交通部只標停止的，沒標的不確定 |

官網偶有漏標或重複登錄（例如人事總處同一件函有一筆標停止、一筆沒標），引用前請讀全文、留意 `notes`（編註）與後續函釋。

回傳的 `categories` 列出每個來源／類別的總筆數與是否有下一頁；某個來源暫時連不上時，該類別帶 `error`，其他來源照常回傳。全文省略正本、副本受文者清單。國土管理署與智慧局著作權函釋官方只提供全量清單，第一次查詢會下載到使用者資料目錄（分別約 16 MB、13 MB），之後每週／每天更新一次。每個來源各自分頁（多數每頁 20 筆，部分 10 或 25 筆）。消防署只能查摘要，函文是掃描 PDF 附件。
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
| 精選裁判 | 司法院編輯、附「裁判要旨」的各級法院裁判；`reference_value=true` 表示該院選為「具參考價值」或「足資討論」 |
| 具參考價值裁判 | 只查上述 `reference_value=true` 的裁判（須指定才查） |

```python
search_precedents(keyword="借名登記")                      # 決議、座談、判例、司法解釋、大法庭、精選裁判
search_precedents(keyword="情事變更", category="決議,司法解釋")
search_precedents(keyword="借名登記", category="具參考價值裁判")
get_precedent("D:A,20170214,001")                          # 最高法院 106 年度第 3 次民事庭會議
```

引用決議、判例前請看 `fields` 裡的編註（例如「不再援用」）。站方每類最多提供前 500 筆，筆數多時請加關鍵字或年度縮小範圍。
</details>

<details>
<summary><b><code>search_administrative_decisions</code> / <code>get_administrative_decision</code></b></summary>

不指定 `source` 時查下列預設來源：

| 來源 | 內容 |
|---|---|
| 行政院訴願審議委員會 | 訴願決定書（PDF 全文；108 年以前收辦的案件是 HTML，id 為院臺訴字號碼，如 `ey:1070210137`） |
| 公平交易委員會 | 處分書及不處分決議書（約 5,800 件，PDF 全文；關鍵字中的空白會被當成詞組的一部分） |
| 勞動部不當勞動行為裁決委員會 | 不當勞動行為裁決（搜尋結果沒有日期，讀全文才有） |
| 公務人員保障暨培訓委員會 | 復審、再申訴決定（不含年金改革案件） |
| 金管會、銀行局、證期局、保險局 | 裁罰案件（四個網站合併；總數為估計） |

以下來源要在 `source` 指定才查：

| `source` | 內容 |
|---|---|
| 「工程會」「採購申訴」 | 採購申訴審議判斷（官方沒有關鍵字檢索，用案號如「訴1130123」或年度查；內文只公開判斷理由） |
| 「監察院」（或「調查報告」「糾正」「彈劾」「糾舉」） | 調查報告、糾正案、彈劾案、糾舉案（官網回應慢，單次可能數十秒） |
| 「律師懲戒」 | 律師懲戒、懲戒覆審決議（需姓名或案號這類精確關鍵字） |
| 機關或縣市名，如「臺北市」「新北市」「國防部」「交通部」「法務部」「金管會」「退輔會」；「訴願」= 全部訴願來源 | 各部會（法務部、外交部、國防部、交通部、金管會、中央銀行、退輔會、國科會、數位部、工程會、原民會）與縣市政府（臺北市、新北市、臺中市、高雄市、彰化縣、花蓮縣、金門縣、苗栗縣、臺東縣、嘉義市、嘉義縣、宜蘭縣、新竹縣）訴願決定。部分網站只能比對標題、只給頁數，差異見各來源的 `note` |

```python
search_administrative_decisions(keyword="個人資料", source="行政院")
search_administrative_decisions(doc_number="公處字第115060號")             # 依字號精確查詢
search_administrative_decisions(keyword="資遣", source="不當勞動行為")
search_administrative_decisions(keyword="洗錢", source="裁罰")              # 金管會裁罰案件
search_administrative_decisions(keyword="長照", source="監察院")
search_administrative_decisions(keyword="違規停車", source="臺北市,新北市")
get_administrative_decision("ey:A-115-000633")                             # 由 PDF 擷取全文
```

官網公開的決定書照原樣提供：多數機關已遮蔽當事人姓名（○○），部分舊案（行政院 108 年以前收辦、法務部約 112 年以前）與原民會的決定書官網未遮蔽，本工具也不另外遮蔽。勞動部、財政部、內政部、衛福部、臺南市的查詢需要驗證碼，未收錄；經濟部、農業部、教育部的訴願網站尚未收錄。律師懲戒決議的被付懲戒律師姓名是官方公開資訊。PDF 無法擷取文字時（多為 2008 年以前的公平會舊檔或掃描檔）回傳 `pdf_url` 讓使用者自行開啟。
</details>

<details>
<summary><b><code>get_legislative_history</code></b></summary>

從立法院法律系統取得某一條文每次制定、修正時的條文與**立法理由**（民國 59 年以後的修正才有理由）。適合回答「這條為什麼這樣規定」「當初修法的目的」；`query_regulation(include_history=True)` 回傳的是條文變遷，這裡多了立法理由。

```python
get_legislative_history("勞動基準法", "24")   # 73 年制定、105、107 年修正，各版條文與理由
get_legislative_history("民法", "1030-1")     # 民法在立法院系統分編收錄，會自動對應到「民法第四編親屬」
get_legislative_history("刑法", "339-4")      # 簡稱會轉成正式名稱「中華民國刑法」
```

另附 `latest_amendment_process`：整部法律最近一次修正的一讀、委員會審查、二讀、三讀日期與公報頁次（不一定修到本條）。其中 `gazette_pdf_id` 傳給 `get_legislative_record` 可讀該次會議的公報紀錄，找立法者原意。
</details>

<details>
<summary><b><code>search_legislative_records</code> / <code>get_legislative_record</code></b></summary>

| `kind` | 內容 |
|---|---|
| `bills`（預設） | 立法院議案（法律案草案、修正草案）。`status="pending"` 審查中（預設只看本屆，屆期不連續）、`all` 全部、`passed` 已三讀；每筆含提案人、提案日期、會期、進度與關係文書 PDF（含條文對照表） |
| `gazette` | 立法院公報（院會、委員會、公聽會紀錄，含委員與官員發言），全文檢索並附命中片段 |
| `drafts` | 行政院公報刊登的法規命令訂定、修正草案預告（含陳述意見截止日期） |

```python
search_legislative_records("勞動基準法", kind="bills")                 # 本屆審查中的勞基法修正草案
search_legislative_records("勞動基準法第五十五條", kind="gazette")     # 找立法者原意：法律名稱＋條次
search_legislative_records("個人資料", kind="drafts")                  # 各部會辦法、細則的草案預告
get_legislative_record("bill:202110226160000")                         # 議案全文與審議進度
```

每頁 20 筆（`drafts` 10 筆），全文超過 60,000 字會截斷。議案的審議進度會變，全文不長期快取。
</details>

<details>
<summary><b><code>search_statistics</code> / <code>get_statistics</code></b></summary>

| 來源（`source`） | 內容 |
|---|---|
| 司法統計 | 司法院司法統計年報：各級法院各類案件收結、終結情形、上訴、發回更審等統計表（`year` 指定民國年，預設最新一年） |
| 月報 | 司法院司法統計月報：指定年度最新一個月的統計表 |
| 法務統計 | 法務部常用統計表：偵查、起訴、定罪、執行、矯正等（每月滾動更新，只快取一天） |
| 犯罪狀況 | 法務部司法官學院《犯罪狀況及其分析》年度報告（篇章 PDF＋數據 XLSX） |

```python
search_statistics(keyword="收結", source="司法統計")
search_statistics(keyword="詐欺", source="犯罪狀況")
get_statistics("moj:INF_COMMON_P/807")      # 地方檢察署執行裁判確定有罪人數
```

關鍵字只比對表名或報告標題。統計表以「|」分欄的文字回傳。
</details>

<details>
<summary><b><code>get_sentencing_statistics</code></b></summary>

司法院「事實型量刑資訊系統」的刑度統計：符合條件的判決數，以及各刑種的平均、最高、最低刑度與分布。涵蓋殺人、強盜搶奪、傷害、不能安全駕駛、肇事逃逸、詐欺、竊盜、毒品、槍砲、妨害性自主 10 類案件。這是過去判決的統計，不是量刑基準。

```python
get_sentencing_statistics()                     # 列出罪名與法院
get_sentencing_statistics(crime="竊盜")          # 統計，並回傳可選的法條（law_options）與量刑因子（factor_options）
get_sentencing_statistics(crime="竊盜", law="第320條第1項", court="臺北地院", factors="累犯=是")
```

只用公開頁面呈現的彙總統計；官網只開放給院內使用者的個案清單與判決明細不呼叫。
</details>

<details>
<summary><b><code>search_legal_literature</code> / <code>get_legal_literature</code></b></summary>

只用官方與開放取用來源，不含付費資料庫：

| 來源（`source`） | 內容 |
|---|---|
| 司法研究年報 | 司法院專題研究報告（含司法研究年報），全文按章分檔 |
| 期刊 | 國家圖書館臺灣期刊論文索引：各法學期刊論文的書目與摘要；作者授權者有全文 |
| GRB | 政府研究資訊系統：國科會與各部會補助的研究計畫摘要（報告全文需在官網下載） |
| 開放期刊 | 中研院法學期刊、政大法學評論（全文取自期刊官網） |

```python
search_legal_literature("量刑", source="司法研究年報")
search_legal_literature("勞動派遣", source="期刊", year_from=105)
get_legal_literature("ncl:A15001353")           # 書目、摘要；有授權時附全文
```

引用時請附作者、篇名、刊名卷期與年份。國家圖書館授權的全文只供個人查閱，本工具不寫入快取，請勿轉存或散布。全文超過 60,000 字會截斷。
</details>

<details>
<summary><b><code>search_other_regulations</code> / <code>get_other_regulation</code></b></summary>

全國法規資料庫法律命令清單以外、`query_regulation` 查不到的規範：

| 類別 | 來源 |
|---|---|
| 地方自治法規 | 臺北市、新北市、桃園市、臺中市、臺南市、高雄市、基隆市、新竹縣市、苗栗縣、彰化縣、南投縣、嘉義縣市、屏東縣、宜蘭縣、花蓮縣、臺東縣、澎湖縣、金門縣、連江縣（只收現行法規；雲林縣官網有 Cloudflare 驗證，未收錄） |
| 條約及協定 | 全國法規資料庫條約（只比對名稱）、外交部條約協定資料庫（部分舊約是掃描檔，只有 PDF 連結）、財政部所得稅協定 |
| 交易所規章 | 臺灣證券交易所、證券櫃檯買賣中心、臺灣期貨交易所（櫃買、期交所規章取自證基會法規系統，僅供查閱、不得轉載） |

```python
search_other_regulations("違章建築", source="臺北市")
search_other_regulations("日本 所得稅", source="條約")       # 條約可用「國家 主題」
search_other_regulations("營業細則", source="證交所")
get_other_regulation("taichung:GL001385", article_no="3")   # 臺中市殯葬管理自治條例第 3 條
```

不填 `source` 會同時查全部 21 個來源，建議指定。多數來源把整串關鍵字當成一個詞，請一次給一個詞。分條的規範回傳 `articles`，要點、條約等未分條的回傳 `full_text`。
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
「財政部對股利的函釋，哪些已經停止適用」
「公務員未休假加班費，人事總處怎麼解釋」
「工程會對不發還押標金有什麼解釋」
「最高法院有沒有關於借名登記的決議」
「查院解字第 3829 號」
「行政院有哪些個資相關的訴願決定」
「勞基法第 24 條當初為什麼這樣修」
「這件最高法院判決的前審是哪幾件？有沒有發回？」
「勞基法第 24 條的英文版」
「列出 115 年 7 月以後修正公布的勞動部主管法規」
「勞基法現在有哪些審查中的修正草案」
「死刑案的法庭之友意見書有哪些提到人性尊嚴」
「憲法法庭現在受理了哪些勞動相關的案件」
「臺北地院竊盜累犯通常判多久」
「司法研究年報有沒有關於量刑的研究」
「臺北市有關違章建築的自治法規」
「金管會最近對洗錢防制缺失的裁罰」
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

查詢時連網的都是台灣政府機關與公共機構的**公開**資料庫，伺服器本身不建資料庫，只在使用者查詢的當下向官方網站取資料：

| 來源 | 網域 | 用途 |
|------|------|------|
| 司法院裁判書系統 | judgment.judicial.gov.tw | 裁判書搜尋、全文與歷審清單（`FJUD/Default_AD.aspx`、`data.aspx`、`controls/GetJudHistory.ashx`） |
| 全國法規資料庫 | law.moj.gov.tw | 法規條文與修法沿革（`LawClass/*`）、官方英譯與法規施行日等資料（`api/*`）、條約協定 |
| 司法院憲法法庭 | cons.judicial.gov.tw | 資料包建置後才公布的憲判字（其餘釋字／憲判字為離線資料）、受理案件與卷內文書 |
| 司法院法學資料檢索系統 | legal.judicial.gov.tw | 決議、法律問題座談、停止適用判例、司法解釋、大法庭、精選裁判、跨機關行政函釋 |
| 司法院 司法統計 | www.judicial.gov.tw | 司法統計年報、月報 |
| 司法院事實型量刑資訊系統 | intellisen.judicial.gov.tw | 量刑統計（公開彙總） |
| 司法院電子出版品 | jirs.judicial.gov.tw | 專題研究報告、司法研究年報 |
| 法務部主管法規查詢系統 | mojlaw.moj.gov.tw | 行政函釋、法規諮詢意見 |
| 勞動部勞動法令查詢系統 | laws.mol.gov.tw | 行政函釋、解釋令 |
| 衛生福利法規檢索系統 | mohwlaw.mohw.gov.tw | 行政函釋 |
| 工程會政府採購法規解釋函令 | planpe.pcc.gov.tw | 採購法令解釋令、函 |
| 財政部各稅法令函釋檢索系統 | ttc.mof.gov.tw | 稅務法令彙編、新頒令釋 |
| 經濟部商業發展署 | gcis.nat.gov.tw | 商工行政法規函釋 |
| 財政部主管法規查詢系統 | law-out.mof.gov.tw | 財政部、關務署、國有財產署、國庫署核釋令與行政規則 |
| 經濟部主管法規查詢系統 | law.moea.gov.tw | 經濟部本部與所屬機關解釋令、行政規則 |
| 經濟部標準檢驗局 | www.bsmi.gov.tw | 解釋函令 |
| 行政院人事行政總處 | law.dgpa.gov.tw | 人事法令解釋 |
| 行政院消費者保護處 | www.ey.gov.tw | 消保法函釋 |
| 監察院陽光法令主題網 | sunshine.cy.gov.tw | 政治獻金法、利益衝突迴避法、財產申報法函釋 |
| 經濟部智慧財產局 | www.tipo.gov.tw | 著作權解釋令函（開放資料）、專利與商標審查基準 |
| 內政部戶政司 | www.ris.gov.tw | 戶政法令解釋 |
| 內政部國土管理署 | www.nlma.gov.tw | 解釋函彙編 |
| 內政部地政司 | www.land.moi.gov.tw | 地政法令解釋 |
| 內政部消防署 | law.nfa.gov.tw | 消防法令解釋 |
| 環境部主管法規查詢系統 | oaout.moenv.gov.tw | 行政函釋 |
| 考試院主管法規共用系統 | law.exam.gov.tw | 銓敘部、保訓會、考選部、考試院函釋 |
| 各部會主管法規共用系統 | law.fsc.gov.tw、edu.law.moe.gov.tw、law.moa.gov.tw、glrs.moi.gov.tw、law.moc.gov.tw、law.nstc.gov.tw、law.cip.gov.tw、law.oac.gov.tw、law.ftc.gov.tw、law.mac.gov.tw、law.cec.gov.tw、law.dgbas.gov.tw、law.mofa.gov.tw、law.vac.gov.tw、erss.nusc.gov.tw、theme.ndc.gov.tw | 金管會、教育部、農業部、內政部、文化部、國科會、原民會、海委會、公平會、陸委會、中選會、主計總處、外交部、退輔會、核安會、國發會的行政規則（解釋令、函） |
| 交通部法規系統 | motclaw.motc.gov.tw | 行政解釋 |
| 中央銀行法規系統 | www.law.cbc.gov.tw | 行政令函、訴願決定 |
| 臺北市法規查詢系統 | laws.gov.taipei | 函釋、訴願決定、地方法規 |
| 新北市法規查詢系統 | web.law.ntpc.gov.tw | 函釋、訴願決定、地方法規 |
| 行政院公報資訊網 | gazette.nat.gov.tw | 各機關解釋性規定、法規命令草案預告 |
| 行政院訴願審議委員會 | appeal.ey.gov.tw | 訴願決定書 |
| 公平交易委員會 | www.ftc.gov.tw | 處分書及不處分決議書 |
| 行政院公共工程委員會 | web.pcc.gov.tw、www.pcc.gov.tw | 採購申訴審議判斷、訴願決定 |
| 勞動部不當勞動行為裁決委員會 | uflb.mol.gov.tw | 裁決決定 |
| 公務人員保障暨培訓委員會 | web13.csptc.gov.tw | 復審、再申訴決定 |
| 金管會、銀行局、證期局、保險局 | www.fsc.gov.tw、www.banking.gov.tw、www.sfb.gov.tw、www.ib.gov.tw | 裁罰案件、金管會訴願決定 |
| 監察院 | www.cy.gov.tw | 調查報告、糾正案、彈劾案、糾舉案 |
| 法務部律師查詢系統 | lawyerbc.moj.gov.tw | 律師懲戒決議 |
| 各部會訴願決定 | www.moj.gov.tw、www.mofa.gov.tw、law.mnd.gov.tw、nseweb.motc.gov.tw、www.vac.gov.tw、www.nstc.gov.tw、moda.gov.tw、law.cip.gov.tw | 法務部、外交部、國防部、交通部、退輔會、國科會、數位部、原民會訴願決定 |
| 縣市政府訴願決定 | appeal.taichung.gov.tw、web.law.ntpc.gov.tw、law.kcg.gov.tw、www.chcg.gov.tw、glrs.hl.gov.tw、law.kinmen.gov.tw、www.miaoli.gov.tw、www.taitung.gov.tw、general.chiayi.gov.tw、www.cyhg.gov.tw、www.e-land.gov.tw、gdd.hsinchu.gov.tw | 臺中市、新北市、高雄市、彰化縣、花蓮縣、金門縣、苗栗縣、臺東縣、嘉義市、嘉義縣、宜蘭縣、新竹縣訴願決定（臺北市見上） |
| 立法院法律系統 | lis.ly.gov.tw | 立法沿革、立法理由、立法歷程與公報頁 |
| 立法院議事暨公報資訊網 | ppg.ly.gov.tw | 議案、立法院公報 |
| 法務部 法務統計資訊網 | www.rjsd.moj.gov.tw | 常用統計表 |
| 法務部司法官學院 | www.cprc.moj.gov.tw | 《犯罪狀況及其分析》 |
| 國家圖書館 臺灣期刊論文索引 | tpl.ncl.edu.tw | 期刊論文書目、摘要、授權全文 |
| 政府研究資訊系統 GRB | www.grb.gov.tw、grbdef.stpi.niar.org.tw | 研究計畫書目與摘要 |
| 中研院法律學研究所 | www.iias.sinica.edu.tw | 中研院法學期刊全文 |
| 政治大學法學院 | review.law.nccu.edu.tw | 政大法學評論全文 |
| 縣市法規查詢系統 | law.tycg.gov.tw、law.taichung.gov.tw、outlaw.kcg.gov.tw、law01.tainan.gov.tw、exlaw.klcg.gov.tw、hclaw.hsinchu.gov.tw、law.hccg.gov.tw、law.miaoli.gov.tw、lawsearch.chcg.gov.tw、glrs.nantou.gov.tw、law.cyhg.gov.tw、law.chiayi.gov.tw、ptlaw.pthg.gov.tw、glrslaw.e-land.gov.tw、glrs.hl.gov.tw、law.taitung.gov.tw、law.penghu.gov.tw、law.kinmen.gov.tw、law.matsu.gov.tw | 自治條例、自治規則等地方法規（臺北市、新北市見上） |
| 外交部條約協定資料庫 | no06.mofa.gov.tw | 條約協定 |
| 財政部 | www.mof.gov.tw | 所得稅協定 |
| 臺灣證券交易所 法規分享知識庫 | twse-regulation.twse.com.tw | 證交所規章 |
| 證券暨期貨法令判解查詢系統 | www.selaw.com.tw | 櫃買中心、期交所規章 |

`get_judgment` 接受使用者傳入的 URL，`mcp_server/config.py:ALLOWED_DOMAINS` 以硬編碼 allow-list 限制只能是裁判書與法規兩個網域；其他工具只連上表固定網址，不接受任意 URL。下列網站的 robots.txt 不允許爬蟲（或排除特定路徑），本工具對它們只做使用者觸發的單次查詢，不批次抓取：司法院法學資料檢索系統（legal.judicial.gov.tw）、衛福部法規檢索系統（mohwlaw.mohw.gov.tw）、行政院訴願網站（appeal.ey.gov.tw）、立法院議事暨公報資訊網（ppg.ly.gov.tw）、內政部地政司（www.land.moi.gov.tw）、臺北市法規查詢系統的訴願決定全文路徑（laws.gov.taipei）、全國法規資料庫的條約查詢（law.moj.gov.tw）、財政部的 `/download/` 檔案（www.mof.gov.tw）。證券暨期貨法令判解查詢系統（www.selaw.com.tw）載明非經授權不得轉載，櫃買中心、期交所規章只供查閱，結果都附提醒。函釋、決議、訴願決定、處分書等皆屬公文，依著作權法第 9 條不受著作權保護；期刊論文、研究報告與交易所規章則不在此列，請依各來源的使用規定引用。國家圖書館授權的全文只供個人查閱，本工具一律不寫入快取。

**個資與存取限制**：本工具只在使用者查詢時即時轉取官網公開的內容，不另外遮蔽、也不建資料庫；部分訴願決定（行政院 108 年以前收辦、法務部約 112 年以前、原民會）官網未遮蔽當事人姓名，結果照原樣呈現。需要驗證碼或 Cloudflare 驗證的網站（勞動部、財政部、內政部、衛福部、臺南市的訴願查詢，醫事懲戒、NCC、數位部法規系統等）不收錄，也不嘗試繞過。司法院量刑資訊系統只用公開頁面的彙總統計，官網只開放給院內使用者的個案判決清單一律不呼叫。

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
| 歷審清單 | 24 小時（與判決全文分開） | 同上 |
| 憲法法庭案件清單、卷宗頁 | 1 天 | 同上 |
| 單篇全文：決定書、立法理由、憲法法庭卷內文書、公報與草案預告、統計表、研究文獻 | 30 天 | 同上 |
| 函釋、判解全文（可能事後停止適用） | 7 天 | 同上 |
| 地方法規、條約、交易所規章 | 7 天 | 同上 |
| 法務統計常用統計表 | 1 天 | 同上 |
| 議案全文、國家圖書館授權全文 | 不快取 | — |
| 消保處、監察院陽光法令、標準檢驗局的函釋清單 | 1 天 | 記憶體（伺服器重啟即重抓） |
| 官方英譯、國土管理署解釋函、智慧局著作權函釋 | 每週更新（智慧局每天） | 使用者資料目錄的 `en_laws.zip`、`en_orders.zip`、`nlma_interpcomp.json`、`tipo_copyright.xml` |
| 釋字/憲判字 | 本地資料（不過期） | `mcp_server/data/old_cases.json`、`new_cases.json`、`opinions.zip` |

使用者資料目錄：Windows 為 `%LOCALAPPDATA%\mcp-taiwan-legal-db`，macOS / Linux 為 `~/.cache/mcp-taiwan-legal-db`（有設 `XDG_CACHE_HOME` 則在其下），可用環境變數 `MCP_TAIWAN_LEGAL_DB_HOME` 改位置。全部清除：刪掉該目錄下的 `legal_mcp.db`。

## pcode_all.json 自動更新

伺服器啟動時會檢查 `pcode_all.json` 的時間戳。如果最後一次更新在最近的週六之前，會在背景觸發從 `law.moj.gov.tw` 官方 API 重新抓取，結果（連同 `law_histories.json` 與 `law_meta.json`）寫進使用者資料目錄，不動套件內建檔；讀取時內建檔與使用者副本取較新者。失敗會記為 warning，不會阻擋啟動。

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
    ├── server.py          # MCPServer 入口 — 定義 26 個 @mcp.tool() function
    ├── config.py          # URL、法院代碼、快取 TTL、allowed domains
    ├── updater.py         # 獨立的 pcode_all.json 更新 script
    ├── healthcheck.py     # 官方來源即時健康檢查（python -m mcp_server.healthcheck）
    ├── cache/db.py        # SQLite 快取層
    ├── data/
    │   ├── pcode_all.json          # 11,700+ 部法規（內建，~780 KB）
    │   ├── law_histories.json      # 修法沿革（內建，~9.6 MB）
    │   ├── law_meta.json           # 最新公布日、施行日註記、主管機關分類（內建，~1.2 MB）
    │   ├── old_cases.json          # 813 筆舊制釋字全文（內建，~7.4 MB）
    │   ├── new_cases.json          # 58 筆新制憲判字全文（內建，~2.0 MB）
    │   └── opinions.zip            # 大法官意見書全文，由官網 PDF 擷取（內建，~10.8 MB）
    ├── models/            # Judgment / Regulation dataclass
    ├── parsers/           # 判決與法規頁面的 HTML parser
    ├── tools/
    │   ├── judicial_search.py      # search_judgments
    │   ├── judicial_doc.py         # get_judgment（含歷審清單）
    │   ├── regulations.py          # query_regulation, get_pcode, search_regulations
    │   ├── constitutional_court.py # get_interpretation, search_interpretations, get_citations
    │   ├── constitutional_docket.py # search_constitutional_docket, get_constitutional_case_file
    │   ├── agency_interpretations.py # search_agency_interpretations, get_agency_interpretation
    │   ├── ip_guidelines.py        # 智慧局專利、商標審查基準
    │   ├── fint.py                 # search_precedents, get_precedent（司法院法學資料檢索系統）
    │   ├── admin_decisions.py      # search_administrative_decisions, get_administrative_decision
    │   ├── quasi_judicial.py       # 準司法機關決定（採購申訴、裁決、保訓會、金管會裁罰、監察院、律師懲戒）
    │   ├── appeals.py              # 各部會與縣市政府訴願決定
    │   ├── legislative.py          # get_legislative_history（立法院法律系統）
    │   ├── legislative_records.py  # search_legislative_records, get_legislative_record
    │   ├── statistics.py           # search_statistics, get_statistics
    │   ├── sentencing.py           # get_sentencing_statistics
    │   ├── literature.py           # search_legal_literature, get_legal_literature
    │   ├── other_regulations.py    # search_other_regulations, get_other_regulation
    │   ├── tls.py                  # 未送中繼憑證網站用的 TWCA 中繼憑證
    │   └── pdf_text.py             # PDF 文字擷取（意見書、決定書、處分書、卷內文書共用）
    └── tests/             # pytest 測試
```

## 執行測試

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest mcp_server/tests/ -v
```

單元測試以 MockTransport 模擬官網，看不出官網改版。發版前、或有人回報某個來源查不到東西時，跑即時健康檢查：每個來源實際查一次、再取第一筆全文，並用幾件已知停止適用的函釋確認效力標示還讀得到。

```bash
.venv/bin/python -m mcp_server.healthcheck                          # 全部（約 130 項，幾分鐘）
.venv/bin/python -m mcp_server.healthcheck interpretations mof mol  # 只查指定工具與來源
.venv/bin/python -m mcp_server.healthcheck status                   # 只查效力標示
```

結果每列標 `OK`、`THIN`（取回的文字偏短）、`EMPTY`（查無結果）或 `FAIL`；有 `EMPTY` 或 `FAIL` 時 exit code 為 1。

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
