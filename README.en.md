# mcp-taiwan-legal-db

**English** · [繁體中文](https://github.com/lawchat-oss/mcp-taiwan-legal-db/blob/main/README.md)

A Model Context Protocol (MCP) server that gives any MCP-compatible AI assistant direct access to Taiwan (ROC) legal databases:

> This tool scrapes official sites live, and the requests go out from your own machine. Read the [Disclaimer](#disclaimer) first: you are responsible for following each site's terms and the applicable law, and results are not legal advice.

- **Judicial Yuan judgments** — judgment.judicial.gov.tw (full-text search + get, with the appeal history of each case)
- **National regulation database** — law.moj.gov.tw (11,700+ laws and ordinances, with official English translations, latest promulgation dates and effective-date notes)
- **Constitutional Court** — 871 Grand Justices interpretations (釋字) and Constitutional Court judgments (憲判字), with full reasoning text, served offline from a bundled cache; pending cases, oral hearings, amicus calls and case-file documents queried live
- **Administrative interpretations (行政函釋)** — 51 official sources: Ministry of Justice, Labor, Health and Welfare, Finance, Economic Affairs, Interior, Transportation, the Central Bank, the Financial Supervisory Commission, the Directorate-General of Personnel Administration, the Consumer Protection Committee, the Examination Yuan system, Taipei and New Taipei City and more, plus the IPO's patent and trademark examination guidelines; each result carries the official "discontinued" / "in force" marking where the site provides one (live)
- **Court resolutions and precedents** — Judicial Yuan law database: Supreme Court resolutions, legal Q&A conferences, discontinued precedents, 院字 / 院解字, Grand Chamber rulings, selected judgments (精選裁判)
- **Administrative appeals and quasi-judicial decisions** — appeal decisions of the Executive Yuan, ministries and local governments; Fair Trade Commission decisions, Ministry of Labor unfair-labor-practice rulings, civil-service protection decisions, FSC sanctions, procurement complaint reviews, Control Yuan cases, lawyer disciplinary decisions
- **Legislative materials** — each article's text and legislative reasons (立法理由) at every amendment, the legislative process, Legislative Yuan bills (including pending drafts) and gazette records, pre-announced draft regulations
- **Statistics and sentencing** — Judicial Yuan judicial statistics (annual / monthly), Ministry of Justice statistics, the annual *Crime Situation and Analysis* report, sentencing statistics from the Judicial Yuan sentencing information system
- **Legal literature** — Judicial Yuan research reports (incl. 司法研究年報), the National Central Library's Taiwan periodical index, GRB research projects, open-access law journals
- **Other legal texts** — local government regulations, treaties and tax agreements, TWSE / TPEx / TAIFEX rules

Written in Python with the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk). Pure tool wrapper — it makes no network calls outside the official sources listed under [Data sources](#data-sources) (government agencies, plus public institutions such as the National Central Library, Academia Sinica, a national university and the stock exchanges).

---

## Why we open-sourced this

Taiwan's legal data is public. Open-sourcing this so nobody has to write the same scraper twice.

---

## Features

| Feature | Description |
|---------|-------------|
| **26 MCP tools** | Judgment search / full text / appeal history, regulation queries (incl. English translations and amendment tracking), 釋字 / 憲判字 lookup, citation graph, Constitutional Court case files, administrative interpretations and examination guidelines, resolutions / Q&A conferences / precedents, appeal and quasi-judicial decisions, legislative reasons and records, statistics and sentencing, legal literature, local regulations and treaties |
| **Offline cache** | 871 Grand Justices interpretations and Constitutional Court judgments (with full reasoning text, plus Justices' opinions extracted from the official PDFs) served instantly from bundled data |
| **Citation graph** | Extracts every 釋字 / 憲判字 cited in an interpretation's reasoning (backward), or lists later 釋字 / 憲判字 that cite it (forward), for tracing the evolution of constitutional doctrine |
| **Full-text search** | Keyword search over judgments + 釋字 issue / reasoning full text |
| **Hybrid request strategy** | httpx direct by default (~0.25s); when the Judicial Yuan F5 WAF or another site's JavaScript check blocks it, falls back to a Playwright browser and resumes |

---

## ⚡ Install (via PyPI — recommended)

```bash
pip install mcp-taiwan-legal-db
```

> **Note for Debian / Ubuntu / WSL users**: the system Python is protected by PEP 668, so a bare `pip install` is blocked. Use one of:
> - `pipx install mcp-taiwan-legal-db` (recommended — isolated venv, standard for Python CLI tools)
> - or `pip install --user --break-system-packages mcp-taiwan-legal-db`

> **Windows / enterprise deployment**: install it as an isolated tool with [uv](https://docs.astral.sh/uv/) or pipx so it never touches the system Python's site-packages:
> ```powershell
> uv tool install mcp-taiwan-legal-db
> uv tool update-shell   # adds the tool directory to PATH; restart the terminal afterwards
> ```
> The package directory is only read, never written: the query cache, WAF cookies and the weekly statute-code table updates live in each user's own directory (Windows: `%LOCALAPPDATA%\mcp-taiwan-legal-db`; macOS / Linux: `~/.cache/mcp-taiwan-legal-db`), so shared all-users locations such as `C:\Program Files` work too. Set the `MCP_TAIWAN_LEGAL_DB_HOME` environment variable to use a different directory.

After install, the `mcp-taiwan-legal-db` entry point is on your PATH. **Wire it into Claude Code** (available from any project):

```bash
claude mcp add taiwan-legal-db mcp-taiwan-legal-db --scope user
```

Then `/mcp` to reload, and Claude will pick up the 26 MCP tools on natural-language queries.

**Chromium**: the Judicial Yuan WAF fallback and the sources that need a browser (Ministry of Culture appeals, NCC, Yunlin County and others) download and install it automatically on first use (about 150 MB, once). In environments without outbound downloads, install it in advance:

```bash
uvx --from mcp-taiwan-legal-db playwright install chromium    # launched only when a query needs a browser; idle otherwise
```

**Captcha recognition**: Interior and Health and Welfare appeals need a local OCR dependency for their image captchas; install with the `[captcha]` extra: `pip install "mcp-taiwan-legal-db[captcha]"` (uvx: `uvx --from "mcp-taiwan-legal-db[captcha]" mcp-taiwan-legal-db`; the Claude Code plugin includes it). Without it, those two sources report that it is missing; other sources are unaffected.

---

## Development setup

If you want to clone, modify, and run tests:

```bash
# 1. Clone the repo
git clone https://github.com/lawchat-oss/mcp-taiwan-legal-db.git
cd mcp-taiwan-legal-db

# 2. Create and populate the virtual environment
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e .

# 3. Install Playwright Chromium for official sources that require a browser
.venv/bin/playwright install chromium

# 4. Verify the server starts and registers all 26 tools
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

**Expected output:**
```
Server: 台灣法律資料庫
Tools: ['search_judgments', 'get_judgment', 'query_regulation', 'get_pcode', 'search_regulations', 'get_interpretation', 'search_interpretations', 'get_citations', 'search_agency_interpretations', 'get_agency_interpretation', 'search_precedents', 'get_precedent', 'search_administrative_decisions', 'get_administrative_decision', 'get_legislative_history', 'search_constitutional_docket', 'get_constitutional_case_file', 'search_legislative_records', 'get_legislative_record', 'search_statistics', 'get_statistics', 'get_sentencing_statistics', 'search_legal_literature', 'get_legal_literature', 'search_other_regulations', 'get_other_regulation']
✓ Setup OK
```

If that prints without errors, you're done. The repo ships a `.mcp.json` at the root, so **any Claude Code session opened inside this folder will automatically load the server**. No extra registration needed.

---

## What you get

26 MCP tools, all read-only, all hitting only official public databases (see [Data sources](#data-sources)).

### Statutes and judgments

| Tool | Purpose | Typical call |
|---|---|---|
| `search_judgments` | Search Judicial Yuan judgment database | `search_judgments(case_word="台上", case_number="3753", year_from=114, court="最高法院")` |
| `get_judgment` | Fetch full text of a single judgment by JID or URL, with its appeal history | `get_judgment(jid="TPSM,114,台上,3753,20251112,1")` |
| `query_regulation` | Query regulation articles (single, range, or a list across the law), the chapter outline, amendment history, official English translation | `query_regulation(law_name="民法", article_no="184~186,247-1")` |
| `get_pcode` | Resolve regulation name → pcode (law code) | `get_pcode(law_name="律師法")` → `"I0020006"` |
| `search_regulations` | Keyword search across 11,700+ regulations, or list those amended since a date | `search_regulations(keyword="勞動")` |

### Constitutional Court

| Tool | Purpose | Typical call |
|---|---|---|
| `get_interpretation` | Full text of a Grand Justices interpretation (釋字) or Constitutional Court judgment (憲判字) — served from local cache | `get_interpretation("釋字748", reasoning_keyword="婚姻")` |
| `search_interpretations` | Search 釋字 / 憲判字 (matches title + issue + reasoning full text) | `search_interpretations(keyword="集會自由")` |
| `get_citations` | Citation graph: the 釋字 / 憲判字 an interpretation cites, or the later ones that cite it | `get_citations("釋字748", include_context=True)` |
| `search_constitutional_docket` | Pending cases, scheduled oral hearings and open amicus curiae calls | `search_constitutional_docket(status="amicus")` |
| `get_constitutional_case_file` | Case-file documents: petitions, replies, expert opinions, amicus briefs, hearing transcripts and more | `get_constitutional_case_file("113年憲判字第8號")` |

### Administrative interpretations and court resolutions

| Tool | Purpose | Typical call |
|---|---|---|
| `search_agency_interpretations` | Search interpretive letters and rulings (行政函釋) and the IPO's examination guidelines across 51 official sources, live, flagging discontinued ones | `search_agency_interpretations(keyword="加班費", agency="勞動部")` |
| `get_agency_interpretation` | Full text of one interpretation (subject, explanation, related articles, editor's notes, validity marking) | `get_agency_interpretation("moj:FE393340")` |
| `search_precedents` | Search Supreme Court resolutions (決議), legal Q&A conferences (法律問題座談), discontinued precedents (停止適用判例), Judicial Yuan interpretations (院字/院解字), Grand Chamber rulings (大法庭) and selected judgments (精選裁判) | `search_precedents(keyword="借名登記", category="決議")` |
| `get_precedent` | Full text of one of those, including editor's notes such as 不再援用 (no longer followed) | `get_precedent("D:A,20170214,001")` |

### Administrative appeals and quasi-judicial decisions

| Tool | Purpose | Typical call |
|---|---|---|
| `search_administrative_decisions` | Search administrative appeal decisions (訴願決定: Executive Yuan, ministries, local governments) and decisions of quasi-judicial bodies | `search_administrative_decisions(keyword="個人資料", source="行政院")` |
| `get_administrative_decision` | Full text of one decision (extracted from the official HTML or PDF) | `get_administrative_decision("ey:A-115-000633")` |

### Legislative materials

| Tool | Purpose | Typical call |
|---|---|---|
| `get_legislative_history` | Each enacted / amended text of an article with its legislative reasons (立法理由), plus the legislative process of the latest amendment | `get_legislative_history("勞動基準法", "24")` |
| `search_legislative_records` | Search Legislative Yuan bills (including pending drafts), the Legislative Yuan Gazette, and pre-announced draft regulations | `search_legislative_records("勞動基準法", kind="bills")` |
| `get_legislative_record` | Full text of a bill, gazette record or draft-regulation notice | `get_legislative_record("bill:202110226160000")` |

### Statistics and legal literature

| Tool | Purpose | Typical call |
|---|---|---|
| `search_statistics` | Search judicial statistics (annual / monthly), Ministry of Justice statistics and the *Crime Situation and Analysis* report | `search_statistics(keyword="收結", source="司法統計")` |
| `get_statistics` | Contents of a statistical table or the text of a report | `get_statistics("moj:INF_COMMON_P/807")` |
| `get_sentencing_statistics` | Sentencing statistics from the Judicial Yuan sentencing information system (case counts, sentence averages and distribution) | `get_sentencing_statistics(crime="竊盜")` |
| `search_legal_literature` | Search Judicial Yuan research reports, the NCL periodical index, GRB research projects and open-access law journals | `search_legal_literature("量刑", source="司法研究年報")` |
| `get_legal_literature` | Bibliographic record, abstract and full text (where publicly available) | `get_legal_literature("ncl:A15001353")` |

### Other legal texts

| Tool | Purpose | Typical call |
|---|---|---|
| `search_other_regulations` | Search texts outside the national regulation database: local government regulations, treaties and agreements, exchange rules | `search_other_regulations("違章建築", source="臺北市")` |
| `get_other_regulation` | Articles (single, range, list; unstructured documents in full) | `get_other_regulation("taichung:GL001385", article_no="3")` |

### Tool details

<details>
<summary><b><code>search_judgments</code></b></summary>

Searches the Judicial Yuan judgment system. Supports:

- **Precise case number lookup** (fast, HTTP GET): set `case_word` + `case_number` + `year_from`
- **Full-text keyword search**: set `keyword`
- **Main-text filter**: `main_text="被告應將 移轉"` + `keyword="借名登記"` → narrows to cases where the defendant was ordered to transfer (i.e. lost)
- Filter by `court`, `case_type` (民事/刑事/行政/懲戒), `year_from`/`year_to`
- Returns results auto-sorted by court authority (最高 → 高等 → 地方)
- **Coverage**: near-complete from ROC year 89 (2000) onward; years 81–88 (1992–1999) are sparse; nothing before 80 (1991). This is the Judicial Yuan system's own coverage — the tool applies no year clipping

**Important**: when looking up a specific case by its number, **always** use `case_word`+`case_number`, not `keyword`. Putting a case number in `keyword` will not find it.

```python
# ✅ Correct — find 114 台上 3753 Supreme Court
search_judgments(case_word="台上", case_number="3753", year_from=114, court="最高法院")

# ✅ Correct — full-text search
search_judgments(keyword="預售屋 遲延交屋")

# ❌ Wrong — putting case number in keyword
search_judgments(keyword="114年度台上字第3753號")
```
</details>

<details>
<summary><b><code>get_judgment</code></b></summary>

Fetches a single judgment's full structured text.

- Input: `jid` (from `search_judgments` results) OR `url`
- Output: `{case_id, court, date, main_text, facts, reasoning, cited_statutes, cited_cases, full_text, source_url, history, history_note}`
- Uses HTTP GET to data.aspx for full text
- Caches the full text for 30 days; the appeal history changes as cases are appealed, so it is cached separately for only 24 hours

```python
get_judgment(jid="TPSM,114,台上,3753,20251112,1")
# → history: 臺中地院 111 易 203 → 臺中高分院 113 上易 80 → … each instance's decision (with jid and url)
```

`history` is the Judicial Yuan's list of decisions in the same case across instances (歷審). Before citing a judgment, check whether a higher court has since reversed or remanded it. `pending_supreme_court=true` means the case is currently before the Supreme Court / Supreme Administrative Court; no higher instance after the last entry does not mean the judgment is final (the appeal period may still be running, or the higher court's decision is not online yet).

Single judgments can be 10K+ tokens. Prefer `search_judgments` metadata first, only fetch full text when the user explicitly needs it.
</details>

<details>
<summary><b><code>query_regulation</code></b></summary>

Queries articles in the national regulation database: a single article, a range, or a list across the law, up to 50 articles per call. Without an article number it returns no article text, only the chapter outline (`structure`: the headings of each part / chapter / section with their first article) and the article range; a whole law can run to more than a thousand articles, which would only flood the agent's context.

```python
# Single article
query_regulation(law_name="民法", article_no="184")

# Ranges and lists, mixed freely (「第184條」 and 「247之1」 are accepted too)
query_regulation(law_name="民法", article_no="184~198")
query_regulation(law_name="民法", article_no="184,185,247-1")

# No article number: chapter outline and article range
query_regulation(law_name="律師法")

# With amendment history; with an article number, also that article's past texts (article_history)
query_regulation(law_name="勞動基準法", article_no="24", include_history=True)

# Official English translation (about 970 laws and some ordinances)
query_regulation(law_name="勞動基準法", article_no="24", language="en")
```

`law` also carries `last_amended` (latest promulgation date) and `category` (competent authority / classification); when a law has a special commencement date it adds `effective_date` / `effective_note` (e.g. "takes effect six months after promulgation", "commencement date set by the Executive Yuan") — check these before citing a newly amended article. They come from `law_meta.json`, refreshed weekly.

`language="en"` returns the official English translation with `english_version_date`. Translations often lag behind Chinese amendments; when the translation is older, `note` says so. The Chinese text is authoritative. The translation files are downloaded to the user data directory on first use (~16 MB) and refreshed weekly.

With `article_no` and `include_history`, `article_history.revisions` lists each enactment, addition, amendment and deletion of that article with its date and the text at the time, for side-by-side comparison. Only the historical versions whose amendment record touches the article are fetched (Civil Code art. 184 needs 5 of 36), and the version list and version texts are cached; versions that fail to load are listed in `failed_versions` and the result is marked `partial`.

Supports both `law_name` (resolved automatically, abbreviations such as 勞基法 included) and direct `pcode`. Beyond 50 articles the result carries `has_more` and where to continue; requested single articles that do not exist are listed in `missing`. `from_no` / `to_no` are equivalent to `article_no="from~to"`.
</details>

<details>
<summary><b><code>get_pcode</code></b></summary>

Converts a regulation name to its pcode (the law.moj.gov.tw internal ID).

```python
get_pcode(law_name="律師法")
# → {"success": true, "law_name": "律師法", "pcode": "I0020006", "status": "現行法規"}

get_pcode(law_name="勞基法")
# → fuzzy match to "勞動基準法" → {"success": true, "pcode": "N0030001", ...}
```

Covers 11,700+ laws and ordinances. Bundled `pcode_all.json` is auto-refreshed weekly from the official API.
</details>

<details>
<summary><b><code>search_regulations</code></b></summary>

Keyword search across regulation names. Paginated (50 per page), current regulations sorted before abolished ones.

```python
search_regulations(keyword="勞動")
search_regulations(keyword="勞動", offset=50)  # page 2
search_regulations(keyword="消費", exclude_abolished=True)

# Laws enacted or amended on or after a date (newest first), optionally by competent authority
search_regulations(amended_since="2026-09-01")
search_regulations(amended_since="115-07-01", category="勞動部")
```

Each result carries `last_amended` and `category`, which makes this usable for tracking amendments.
</details>

<details>
<summary><b><code>get_interpretation</code></b></summary>

Retrieves the full text of a Grand Justices interpretation (釋字 No. 1–813) or a Constitutional Court judgment (憲判字). The default tier is served instantly from a bundled JSON cache.

**Layered design** (saves context):

| Tier | Trigger | Offline? |
|------|---------|----------|
| Default (case ID / date / issue / interpretation text) | always returned | ✓ |
| Reasoning excerpt by keyword | `reasoning_keyword="..."` | ✓ |
| Full reasoning (no character limit) | `include_reasoning=True` | ✓ |
| Opinion excerpt by keyword | `opinions_keyword="..."` | ✓ |
| Full opinions | `include_opinions=True` | ✓ |
| One opinion in full | `opinion_document="許宗力"` | ✓ |
| Read from a chosen offset to the end (legacy parameter) | `opinions_offset=15000` | ✓ |

```python
# Default tier (offline, ~0ms)
get_interpretation("釋字748")

# Search inside the reasoning for a keyword
get_interpretation("釋字748", reasoning_keyword="婚姻自由")

# Locate a particular Justice in the opinions
get_interpretation("釋字758", opinions_keyword="湯德宗")

# Read one Justice's opinion in full
get_interpretation("釋字758", opinion_document="許宗力")

# Optionally read all remaining text from a chosen character offset
get_interpretation("釋字777", opinion_document="吳陳鐶", opinions_offset=15000)

# Constitutional Court judgment under the new regime
get_interpretation("111年憲判字第1號")
```

Recommended pattern: use the keyword-excerpt modes to locate the relevant passage first; only fall back to full text when needed.
</details>

<details>
<summary><b><code>search_interpretations</code></b></summary>

Searches Grand Justices interpretations and Constitutional Court judgments. The keyword matches the title, the issue statement, and the reasoning full text simultaneously.

```python
# Full-text search (across issue + reasoning)
search_interpretations(keyword="集會自由")

# Filter by year (post-2022 Constitutional Court judgments)
search_interpretations(keyword="言論自由", year=112)

# List the last 10 釋字 interpretations
search_interpretations(number_from=804, number_to=813)
```
</details>

<details>
<summary><b><code>get_citations</code></b></summary>

Extracts every prior 釋字 / 憲判字 cited in a given interpretation's reasoning. Direction: traces what the target *cited* (backward lookup).

```python
get_citations("釋字748")
# → citations: [釋字第242號, 釋字第362號, 釋字第365號, ...]

# Include an 80-character context window around each citation
get_citations("釋字748", include_context=True)

# Forward lookup: later 釋字 / 憲判字 whose holding or reasoning cites this one
get_citations("釋字748", direction="cited_by")
# → cited_by: [釋字第763號, 釋字第791號, ...]
```

Enumerations such as 釋字第477號、第747號及第762號 are picked up one by one. `cited_by` scans every bundled case (not the opinions, and not cases published after the bundle was built); to find court judgments citing an interpretation, use `search_judgments(keyword="釋字第748號")`.
</details>

<details>
<summary><b><code>search_constitutional_docket</code> / <code>get_constitutional_case_file</code></b></summary>

`get_interpretation` only covers published decisions. These two tools query the case progress and case-file documents the Constitutional Court publishes on its site (live):

| `status` | Content |
|---|---|
| `pending` (default) | Accepted cases under review (acceptance date, petitioner (private individuals are pseudonymised as 甲, 乙, 丙), docket number, lead / joined case, cause) |
| `hearing` | Cases with a scheduled or completed oral hearing or briefing |
| `amicus` | Cases currently inviting amicus curiae briefs |

```python
search_constitutional_docket(keyword="勞動")                     # pending cases whose cause mentions 勞動
search_constitutional_docket(status="amicus")                    # cases inviting amicus briefs

get_constitutional_case_file("113年憲判字第8號")                  # all public documents and hearing announcements
get_constitutional_case_file("113年憲判字第8號", keyword="人性尊嚴")  # only documents containing the keyword, with snippets
get_constitutional_case_file(document_id="492306")               # full text of one document (extracted from the PDF)
```

`case_id` can be a 憲判字, a 釋字, a pending docket number (e.g. 114年度憲立字第3號) or an id returned by `search_constitutional_docket`. Keyword matching runs on the court's own unpunctuated text extraction and is limited to 憲判字 and pending cases; OCR of scanned files may contain errors, and the court publishes only the first 20 pages of amicus briefs. Case lists and case pages are cached locally for one day.
</details>

<details>
<summary><b><code>search_agency_interpretations</code> / <code>get_agency_interpretation</code></b></summary>

Each agency publishes its interpretations in its own system; there is no shared API. This tool queries the following official systems at request time (51 sources in all; Foreign Affairs, Veterans Affairs, the Nuclear Safety Commission, the National Development Council, NCC, Hakka Affairs, Overseas Community Affairs, Sports, Customs new letters and MAC advertising letters are queried only when explicitly named in `agency`), merges the hits newest-first, and keeps one copy of a letter that appears in several sources (the issuing agency's own system wins):

| Source | Content |
|---|---|
| Ministry of Justice regulation system (mojlaw) | 行政函釋, 法規諮詢意見 |
| Ministry of Labor regulation system | 行政函釋, 解釋令 |
| Ministry of Health and Welfare regulation system | 行政函釋 |
| Ministry of Environment regulation system | 行政函釋 |
| Public Construction Commission | Government Procurement Act interpretations |
| Ministry of Finance tax ruling system | Tax ruling compilation (法令彙編), newly issued rulings (新頒令釋) |
| Ministry of Finance regulation system | Interpretive orders and administrative rules of the Ministry, Customs Administration, National Property Administration and National Treasury Administration |
| Ministry of Economic Affairs regulation system | Interpretive orders and administrative rules of the Ministry and its agencies (Water Resources, Standards, Trade, etc.) |
| MOEA Administration of Commerce | Company Act, Business Registration Act, etc. |
| Intellectual Property Office | Copyright interpretations; patent examination guidelines (web full text) and trademark examination guidelines (PDF) |
| Bureau of Standards, Metrology and Inspection | Interpretive letters (commodity inspection, metrology) |
| Directorate-General of Personnel Administration | Civil service personnel interpretations (appointment, pay, leave) |
| Executive Yuan Consumer Protection Committee | Consumer Protection Act interpretations (titles and abstracts matched) |
| Control Yuan sunshine-law site | Competent authorities' interpretations of the Political Donations Act, the Conflict of Interest Act and the asset disclosure law (titles matched) |
| MOI Department of Household Registration, National Land Management Agency, Department of Land Administration, National Fire Agency | Household registration and nationality; building administration and urban planning; land administration (incl. discontinued letters); fire safety |
| Ministry of Transportation regulation system | Administrative interpretations (orders, letters, notices) |
| Central Bank regulation system | Administrative orders and letters |
| Examination Yuan shared regulation system | Ministry of Civil Service, Civil Service Protection and Training Commission, Ministry of Examination and Examination Yuan interpretations |
| Ministries' shared regulation systems | Administrative rules (where interpretive orders and letters are filed; results also include ordinary administrative rules) of the FSC, Education, Agriculture, Interior, Culture, NSTC, Indigenous Peoples, Ocean Affairs, Fair Trade Commission, Mainland Affairs Council, Central Election Commission (incl. its interpretations) and DGBAS; Foreign Affairs, Veterans Affairs, Nuclear Safety and NDC only when named |
| Taipei City regulation system | Taipei City Government interpretations, plus central-agency interpretations it carries |
| New Taipei City regulation system | New Taipei City Government and central-agency interpretations (the 5 categories with the most hits are listed; the rest are counted) |
| Judicial Yuan law database (FINT) | Cross-agency interpretations (Judicial Yuan, Ministry of Justice and others) |
| NCC | Administrative interpretations, including individual letters, from its regulation system; specify `agency="NCC"` |
| Hakka Affairs, Overseas Community Affairs, Sports | Administrative rules; explicit source selection; no inferred in-force status |
| Customs new letters | Public CSRF form; title search only, date filters not applied |
| MAC main-site advertising letters | Only letters and guidance in the advertising category; title matching, not all MAC correspondence |
| Executive Yuan Gazette | Interpretive rules officially published under Administrative Procedure Act Art. 159(2)(ii); partial fallback for Digital Affairs, whose regulation-system verification still cannot be completed automatically |

```python
# All sources
search_agency_interpretations(keyword="個人資料", year_from=113, year_to=114)

# One or more agencies (comma-separated; short names like 金管會 / 衛福部 work)
search_agency_interpretations(keyword="加班費", agency="勞動部")
search_agency_interpretations(keyword="私募", agency="金管會")
search_agency_interpretations(keyword="時效取得", agency="地政司")
search_agency_interpretations(keyword="考績", agency="銓敘部")
search_agency_interpretations(keyword="專利要件", agency="專利")   # IPO guidelines match chapter titles only
search_agency_interpretations(keyword="加班費", agency="人事總處")
search_agency_interpretations(keyword="關係人", agency="陽光法令")

# By document number
search_agency_interpretations(doc_number="法律字第11403512580號")

# Full text (id from the search results)
get_agency_interpretation("moj:FE393340")
```

**Validity marking**: `status` on results and full texts is the official site's own marking for that record. Check it before citing.

| status | Meaning |
|---|---|
| `停止適用` | The site marks the letter as discontinued or repealed; `status_note` gives the date, the letter that discontinued it, or the site's own wording |
| `部分停止適用` | Partly discontinued (Ministry of Transportation) |
| `適用中` | Only from sources whose site has a two-state current / discontinued field (Labor, Health and Welfare, the Examination Yuan system, Environment, Land Administration, the ministries' shared systems' administrative rules), meaning the site lists it as current; letters in the Ministry of Finance's latest compilation are also `適用中` (re-reviewed and retained; repeals after the compilation are not marked) |
| no `status` | The site has no marking or did not mark it — **this does not mean the letter is still in force**. Household Registration, the National Fire Agency, the IPO and the Executive Yuan Gazette have no validity field at all; Justice, the PCC, FINT, Taipei, the Central Bank and Transportation mark only discontinued letters |

Official sites occasionally miss a marking or list the same letter twice (the DGPA has one letter recorded twice, one copy marked discontinued and one not), so read the full text, the `notes` (editor's notes) and later letters before citing.

`categories` reports each source / category's total and whether there is another page; a source that is temporarily down carries `error` while the others still return. Recipient lists (正本 / 副本) are omitted from the full text. The National Land Management Agency and the IPO (copyright interpretations) only publish full dumps, so the first query downloads them to the user data directory (~16 MB and ~13 MB) and refreshes them weekly / daily. Each source paginates on its own (mostly 20 per page, some 10 or 25). The National Fire Agency system only exposes summaries; the letters themselves are scanned PDF attachments.
</details>

<details>
<summary><b><code>search_precedents</code> / <code>get_precedent</code></b></summary>

Searches the parts of the Judicial Yuan law database that the judgment search (`search_judgments`) cannot reach:

| Category | Content |
|---|---|
| 決議 | Supreme Court civil/criminal division resolutions, Supreme Administrative Court joint-meeting resolutions (before the 2019 Grand Chamber reform) |
| 法律問題座談 | Court legal Q&A conferences, notarial and disciplinary Q&A |
| 停止適用判例 | Precedents discontinued under Court Organization Act Art. 57-1 for lack of a full text (only the headnote survives) |
| 司法解釋 | Daliyuan and Supreme Court interpretations, Judicial Yuan 院字 / 院解字 interpretations |
| 大法庭 | Supreme Court and Supreme Administrative Court Grand Chamber rulings |
| 精選裁判 | Judgments of all court levels edited by the Judicial Yuan with a headnote (裁判要旨); `reference_value=true` marks those the court selected as having reference value (具參考價值 / 足資討論) |
| 具參考價值裁判 | Only the `reference_value=true` judgments above (only when requested) |

```python
search_precedents(keyword="借名登記")                      # resolutions, Q&A, precedents, interpretations, Grand Chamber, selected judgments
search_precedents(keyword="情事變更", category="決議,司法解釋")
search_precedents(keyword="借名登記", category="具參考價值裁判")
get_precedent("D:A,20170214,001")                          # Supreme Court civil division meeting 106-3
```

Check the editor's note in `fields` (e.g. 不再援用) before citing a resolution or precedent. The site returns at most the first 500 hits per category; narrow with keywords or years when there are more.
</details>

<details>
<summary><b><code>search_administrative_decisions</code> / <code>get_administrative_decision</code></b></summary>

Without `source`, these default sources are searched:

| Source | Content |
|---|---|
| Executive Yuan Petitions and Appeals Committee | Administrative appeal decisions (PDF full text; cases filed up to ROC 108 are HTML and use the 院臺訴字 number as id, e.g. `ey:1070210137`) |
| Fair Trade Commission | Disposition and non-disposition decisions (~5,800, PDF full text; spaces inside the keyword are treated as part of a phrase) |
| Ministry of Labor Board of Unfair Labor Practice Decisions | Unfair labor practice rulings (search results carry no date; the full text does) |
| Civil Service Protection and Training Commission | Reexamination and re-appeal decisions (pension-reform cases excluded) |
| FSC, Banking Bureau, Securities and Futures Bureau, Insurance Bureau | Sanction cases (four sites merged; totals are estimates) |

These are searched only when named in `source`:

| `source` | Content |
|---|---|
| 工程會 / 採購申訴 | Procurement complaint review decisions (no keyword search on the official site — look up by case number such as 訴1130123 or by year; only the reasoning is published) |
| 監察院 (or 調查報告 / 糾正 / 彈劾 / 糾舉) | Control Yuan investigation reports, corrective measures, impeachments and censures (the site is slow; a query can take tens of seconds) |
| 律師懲戒 | Lawyer disciplinary and disciplinary-review decisions (needs a precise keyword such as a name or case number) |
| An agency or local government, e.g. 臺北市, 新北市, 國防部, 交通部, 法務部, 金管會, 退輔會; 訴願 = every appeal source | Appeal decisions of ministries (Justice, Foreign Affairs, National Defense, Transportation, FSC, Central Bank, Veterans Affairs, NSTC, Digital Affairs, Public Construction Commission, Indigenous Peoples, Economic Affairs, Agriculture, Education, Culture, Environment, Labor, Interior, Health and Welfare, the Central Election Commission and Personnel Administration) and local governments (Taipei, New Taipei, Taichung, Kaohsiung, Changhua, Hualien, Kinmen, Miaoli, Taitung, Chiayi City, Chiayi County, Yilan, Hsinchu County, Keelung). Some sites only match titles or only report page counts; see each source's `note` |

```python
search_administrative_decisions(keyword="個人資料", source="行政院")
search_administrative_decisions(doc_number="公處字第115060號")             # exact lookup by number
search_administrative_decisions(keyword="資遣", source="不當勞動行為")
search_administrative_decisions(keyword="洗錢", source="裁罰")              # FSC sanctions
search_administrative_decisions(keyword="長照", source="監察院")
search_administrative_decisions(keyword="違規停車", source="臺北市,新北市")
get_administrative_decision("ey:A-115-000633")                             # text extracted from the PDF
```

Decisions are returned as the official sites publish them: most agencies mask the parties' names (○○), while some older cases (Executive Yuan cases filed up to ROC 108, Ministry of Justice decisions from before about ROC 112) and the Council of Indigenous Peoples' decisions are published unmasked; this server does not mask them further. Labor uses its public voice-verification endpoint; Interior and Health and Welfare use local image OCR (`[captcha]`); Culture uses Playwright. Interior, Environment and Health and Welfare default to the current year. Education and Agriculture are limited to five pages. Keelung and CEC search titles; Personnel Administration filters only the requested page. Finance and Taoyuan remain unreachable from the test environment; Tainan verification is still unreliable and was not added. Names of disciplined lawyers are officially public. `source="醫事懲戒"` queries currently posted medical disciplinary notices (default: physicians; a profession prefix changes the category), matching names, locations and certificate numbers. Scanned decisions are supplied as PDF links. When a PDF has no extractable text (mostly FTC files before 2008 or scans), `pdf_url` is returned for the user to open.
</details>

<details>
<summary><b><code>get_legislative_history</code></b></summary>

Fetches, from the Legislative Yuan law system, the text of an article at each enactment / amendment together with the **legislative reasons** (立法理由; available for amendments from ROC 59 / 1970 on). Use it for "why does this article say this" or "what was the amendment for"; `query_regulation(include_history=True)` shows how the text changed, this adds the reasons.

```python
get_legislative_history("勞動基準法", "24")   # enacted 1984, amended 2016 and 2018 — each text and reason
get_legislative_history("民法", "1030-1")     # the Civil Code is stored per book; mapped to 民法第四編親屬 automatically
get_legislative_history("刑法", "339-4")      # short names resolve to the official name 中華民國刑法
```

The response also carries `latest_amendment_process`: first reading, committee review, second and third reading dates with gazette references for the law's most recent amendment (which may not have touched this article). Pass a step's `gazette_pdf_id` to `get_legislative_record` to read that meeting's gazette record when looking for legislative intent.
</details>

<details>
<summary><b><code>search_legislative_records</code> / <code>get_legislative_record</code></b></summary>

| `kind` | Content |
|---|---|
| `bills` (default) | Legislative Yuan bills (draft laws and amendments). `status="pending"` under review (current term only by default — bills do not carry over between terms), `all`, or `passed` (third reading); each with proposers, date, session, progress and the bill PDF (incl. the comparison table) |
| `gazette` | Legislative Yuan Gazette (plenary, committee and public hearing records, including legislators' and officials' remarks), full-text search with matching snippets |
| `drafts` | Draft regulations pre-announced in the Executive Yuan Gazette (with the comment deadline) |

```python
search_legislative_records("勞動基準法", kind="bills")                 # pending amendments to the Labor Standards Act
search_legislative_records("勞動基準法第五十五條", kind="gazette")     # legislative intent: law name + article
search_legislative_records("個人資料", kind="drafts")                  # ministries' draft regulations
get_legislative_record("bill:202110226160000")                         # bill text and review progress
search_legislative_records("條例", kind="join", status="pending")     # JOIN draft laws; closed = ended consultations
# Consultation state is not legal validity. Returns notice text, one selected draft/comparison PDF, and attachment links.
```

20 results per page (10 for `drafts`); full text is returned without character truncation. A bill's progress changes, so bill texts are not cached long-term.
</details>

<details>
<summary><b><code>search_statistics</code> / <code>get_statistics</code></b></summary>

| `source` | Content |
|---|---|
| 司法統計 | Judicial Yuan annual judicial statistics: cases received / closed by court and type, appeals, remands, etc. (`year` in ROC years, default latest) |
| 月報 | Judicial Yuan monthly judicial statistics: the latest month of a given year |
| 法務統計 | Ministry of Justice common statistics: investigation, prosecution, conviction, enforcement, corrections (updated monthly, cached for one day) |
| 犯罪狀況 | Academy for the Judiciary (MOJ) annual *Crime Situation and Analysis* report (chapter PDFs + data XLSX) |

```python
search_statistics(keyword="收結", source="司法統計")
search_statistics(keyword="詐欺", source="犯罪狀況")
get_statistics("moj:INF_COMMON_P/807")      # persons convicted, by offence (district prosecutors' offices)
```

Keywords match table or report titles only. Tables are returned as text with `|`-separated columns.
</details>

<details>
<summary><b><code>get_sentencing_statistics</code></b></summary>

Sentencing statistics from the Judicial Yuan fact-based sentencing information system (事實型量刑資訊系統): the number of matching judgments and, per penalty type, the average, maximum, minimum and distribution. Covers 10 offence groups: homicide, robbery, bodily harm, drunk driving, hit-and-run, fraud, theft, drugs, firearms and sexual offences. These are statistics of past judgments, not sentencing guidelines.

```python
get_sentencing_statistics()                     # list offences and courts
get_sentencing_statistics(crime="竊盜")          # statistics, plus the available statutes (law_options) and factors (factor_options)
get_sentencing_statistics(crime="竊盜", law="第320條第1項", court="臺北地院", factors="累犯=是")
```

Only the aggregate statistics shown on the public pages are used; the per-case lists and judgment details, which the site reserves for Judicial Yuan users, are never called.
</details>

<details>
<summary><b><code>search_legal_literature</code> / <code>get_legal_literature</code></b></summary>

Official and open-access sources only — no subscription databases:

| `source` | Content |
|---|---|
| 司法研究年報 | Judicial Yuan research reports (incl. 司法研究年報), full text split by chapter |
| 期刊 | National Central Library Taiwan periodical index: bibliographic records and abstracts of law journal articles; full text where the author has licensed it |
| GRB | Government Research Bulletin: abstracts of NSTC- and ministry-funded research projects (reports must be downloaded on the GRB site) |
| 開放期刊 | Academia Sinica Law Journal, NCCU Law Review and NTU Law Journal (official-site full text; NTU only resolves identifiable issues and PDFs explicitly marked full text/final manuscript, never abstracts) |

```python
search_legal_literature("量刑", source="司法研究年報")
search_legal_literature("勞動派遣", source="期刊", year_from=105)
get_legal_literature("ncl:A15001353")           # record and abstract; full text when licensed
```

Cite author, title, journal, volume and year. Full texts licensed through the NCL are for personal reading only: the server never caches them; do not store or redistribute them. Full text is returned without character truncation.
</details>

<details>
<summary><b><code>search_other_regulations</code> / <code>get_other_regulation</code></b></summary>

Legal texts outside the national regulation database's list of laws and ordinances, which `query_regulation` cannot reach:

| Category | Sources |
|---|---|
| Local government regulations | Taipei, New Taipei, Taoyuan, Taichung, Tainan, Kaohsiung, Keelung, Hsinchu County and City, Miaoli, Changhua, Nantou, Chiayi County and City, Pingtung, Yilan, Hualien, Taitung, Penghu, Kinmen, Lienchiang and Yunlin (current regulations only; Yunlin uses a browser when challenged) |
| Treaties and agreements | Treaties in the national regulation database (title match only), the MOFA treaty database (some older treaties are scans with only a PDF link), MOF income tax agreements |
| Exchange rules | TWSE, TPEx and TAIFEX (TPEx and TAIFEX rules come from the SFI regulation system — for reference only, no republishing) |

```python
search_other_regulations("違章建築", source="臺北市")
search_other_regulations("日本 所得稅", source="條約")       # treaties: "country topic"
search_other_regulations("營業細則", source="證交所")
get_other_regulation("taichung:GL001385", article_no="3")   # Taichung City Funeral Management Autonomy Ordinance, art. 3
```

Without `source` all 28 sources are queried, so name one. Most sources treat the keyword as a single term; give one term at a time. Article-structured texts return the `articles` selected by `article_no`, written as in `query_regulation` (single, range, list, up to 50 per call); without an article number only the article range is returned. Unstructured documents (guidelines, treaties) return `full_text`.
</details>

---

## Example prompts

```
"Look up Article 184 of the Civil Code"
"Find Supreme Court judgments about delayed delivery of pre-sale housing"
"What are the key points in the reasoning of Interpretation No. 748?"
"Which Grand Justices interpretations discuss freedom of assembly?"
"Which earlier interpretations did Interpretation No. 748 cite?"
"Look up 111 年憲判字第 1 號"
"What has the Ministry of Labor ruled on overtime pay?"
"Which Ministry of Finance rulings on dividends have been discontinued?"
"How does the DGPA treat unused-leave pay for civil servants?"
"How does the Public Construction Commission interpret withholding bid bonds?"
"Are there Supreme Court resolutions on nominee registration (借名登記)?"
"Look up 院解字第 3829 號"
"Which Executive Yuan appeal decisions involve personal data?"
"Why was Article 24 of the Labor Standards Act amended?"
"What are the earlier and later instances of this Supreme Court judgment? Was it remanded?"
"Show me Article 24 of the Labor Standards Act in English"
"List Ministry of Labor regulations amended since July 2026"
"Which amendments to the Labor Standards Act are pending in the Legislative Yuan?"
"Which amicus briefs in the death penalty case discuss human dignity?"
"Which labor-related cases are pending before the Constitutional Court?"
"How long are sentences for repeat-offender theft at the Taipei District Court?"
"Are there Judicial Yuan research reports on sentencing?"
"Which Taipei City regulations deal with illegal structures?"
"Recent FSC sanctions for anti-money-laundering failures"
```

---

## Registering with your Claude client

Pick the section that matches the Claude client you use.

### Claude Code (CLI)

Claude Code auto-loads `.mcp.json` files at the project root. This repo already ships one:

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

**Zero config**: `cd` into the repo and run `claude`. You'll see `taiwan-legal-db` in the MCP server list and nothing else in this folder.

**Share with teammates**: the `.mcp.json` is committed to the repo. Anyone who clones and completes the Quick Start gets the same MCP registration automatically.

**Add to another project** (e.g. you want this MCP available in some other folder): use `claude mcp add` with project scope:

```bash
cd /path/to/your/other/project
claude mcp add taiwan-legal-db --scope project -- \
  /absolute/path/to/mcp-taiwan-legal-db/.venv/bin/python \
  -m mcp_server.server
```

This writes a `.mcp.json` in your other project's root. Change `--scope project` to `--scope user` if you want it in every project you open.

### Claude Desktop (macOS / Windows)

Claude Desktop uses a single global config file at:

- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Windows (Microsoft Store / WinGet / MSIX installs)**: `C:\Users\<YourName>\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Roaming\Claude\claude_desktop_config.json`

**Easiest way to open it**: in Claude Desktop, click the menu bar (not the window) → **Settings** → **Developer** → **Edit Config**. If the file doesn't exist yet, Claude Desktop creates it.

Add this under `mcpServers` (merge with anything already there):

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

Replace `/absolute/path/to/mcp-taiwan-legal-db` with your actual clone path. The `cwd` field is required so Python finds the `mcp_server` package.

**After saving, fully quit and reopen Claude Desktop** (not just close the window — on macOS use ⌘Q, on Windows right-click the tray icon → Quit). The config is only re-read on restart.

### Claude Cowork (Pro and above)

Claude Cowork runs inside Claude Desktop and **shares the same `claude_desktop_config.json`** — there is no separate Cowork config. Any MCP server you register for Claude Desktop is automatically bridged into Cowork's sandboxed VM by the Claude Desktop SDK layer.

**Setup**:

1. Follow the **Claude Desktop** section above to add `taiwan-legal-db` to `claude_desktop_config.json`
2. **Fully quit and reopen Claude Desktop** — this also restarts Cowork
3. Open a Cowork session. The `taiwan-legal-db` tools will be available to the Cowork agent

**Note**: Cowork is available on Claude Pro / Max / Team / Enterprise, and only accesses folders you explicitly grant permission to. The MCP server itself runs on your host (not inside the Cowork VM) and communicates via the Desktop SDK bridge, so it has access to the bundled `pcode_all.json` data file regardless of which folder you grant Cowork.

### Other MCP-compatible clients

Any MCP client that follows the [Model Context Protocol specification](https://modelcontextprotocol.io/) can use this server. The launch command is always the same:

```
.venv/bin/python -m mcp_server.server
```

...with `cwd` set to the repo root (so Python can find the `mcp_server` package). Consult your client's documentation for where to add the `mcpServers` JSON block.

---

## Build an A2A agent on top of this server

Want to drive these tools from an A2A agent? See [`examples/agno-bindu/`](examples/agno-bindu/) — a community-contributed A2A agent example.

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'mcp_server'`**
→ You did not run `pip install -e .` inside the venv. Go back to Quick Start step 2.

**`FileNotFoundError: data/pcode_all.json`**
→ The bundled `mcp_server/data/pcode_all.json` is missing or got deleted. Restore from `git checkout mcp_server/data/pcode_all.json`, or trigger a refresh:
```bash
.venv/bin/python -m mcp_server.updater
```

**MCP client reports "server failed to start"**
→ Run the verify command from Quick Start step 4 directly. If it fails, the import chain is broken — read the traceback. If it passes, the issue is in the MCP client's launch configuration (wrong path, wrong cwd).

**`ssl.SSLCertVerificationError: ... Missing Subject Key Identifier`**
→ This is OpenSSL 3.6+ broadly rejecting the TWCA Global Root CA — **not a stale-`certifi` problem**. This repo uses [`truststore`](https://github.com/sethmlarson/truststore) so Python validates against the OS-native trust store (macOS Security framework, Windows CryptoAPI, Linux system CA), keeping **full SSL verification (`verify=True`) on every path** — it never uses `verify=False`. This works on macOS, Windows, and Linux with OpenSSL <3.6. Linux with OpenSSL 3.6+ (Fedora 40+, future Ubuntu LTS) may still be affected — issue reports welcome.

---

## WAF Handling

The Judicial Yuan's `judgment.judicial.gov.tw` is behind an F5 BIG-IP ASM WAF. Plain HTTP requests may be blocked (returning a fixed 245-byte "Request Rejected" page).

This project uses a hybrid strategy:

- Requests go out via httpx directly by default (~0.25s)
- When a block is detected (response contains `Request Rejected` or JS challenge markers `bobcmn` / `TSPD`), it falls back to Playwright to execute the JS challenge
- The resulting TSPD cookies are persisted to `.judicial_cookies.json` in the user data directory (0600 permissions)
- Subsequent queries resume via httpx with the refreshed cookies

`cons.judicial.gov.tw` (Constitutional Court) and `law.moj.gov.tw` (regulations) are not affected — they bypass the WAF path entirely.

---

## Data sources

Every live query goes to a **public** database run by a Taiwan government agency or public institution. The server keeps no database of its own; it fetches from the official site at the moment a user asks:

| Source | Domain | Used for |
|--------|--------|----------|
| Judicial Yuan judgment system | judgment.judicial.gov.tw | Judgment search, full text and appeal history (`FJUD/Default_AD.aspx`, `data.aspx`, `controls/GetJudHistory.ashx`) |
| National regulation database | law.moj.gov.tw | Regulation articles + amendment history (`LawClass/*`), English translations and commencement data (`api/*`), treaties |
| Constitutional Court | cons.judicial.gov.tw | 憲判字 published after the bundle was built (everything else is bundled offline), docket and case-file documents |
| Judicial Yuan law database (FINT) | legal.judicial.gov.tw | Resolutions, legal Q&A conferences, discontinued precedents, Judicial Yuan interpretations, Grand Chamber, selected judgments, cross-agency interpretations |
| Judicial Yuan judicial statistics | www.judicial.gov.tw | Annual and monthly judicial statistics |
| Judicial Yuan sentencing information system | intellisen.judicial.gov.tw | Sentencing statistics (public aggregates) |
| Judicial Yuan e-publications | jirs.judicial.gov.tw | Research reports, 司法研究年報 |
| Ministry of Justice regulation system | mojlaw.moj.gov.tw | 行政函釋, 法規諮詢意見 |
| Ministry of Labor regulation system | laws.mol.gov.tw | 行政函釋, 解釋令 |
| Ministry of Health and Welfare regulation system | mohwlaw.mohw.gov.tw | 行政函釋 |
| Public Construction Commission | planpe.pcc.gov.tw | Procurement interpretations |
| Ministry of Finance tax ruling system | ttc.mof.gov.tw | Tax rulings |
| MOEA Administration of Commerce | gcis.nat.gov.tw | Company / commercial law interpretations |
| Ministry of Finance regulation system | law-out.mof.gov.tw | Interpretive orders and administrative rules (incl. Customs, National Property, National Treasury) |
| Ministry of Economic Affairs regulation system | law.moea.gov.tw | Interpretive orders and administrative rules |
| Bureau of Standards, Metrology and Inspection | www.bsmi.gov.tw | Interpretive letters |
| Directorate-General of Personnel Administration | law.dgpa.gov.tw | Personnel interpretations |
| Executive Yuan Consumer Protection Committee | www.ey.gov.tw | Consumer Protection Act interpretations |
| Control Yuan sunshine-law site | sunshine.cy.gov.tw | Political donations, conflict of interest and asset disclosure interpretations |
| Intellectual Property Office | www.tipo.gov.tw | Copyright interpretations (open data), patent and trademark examination guidelines |
| MOI Department of Household Registration | www.ris.gov.tw | Household registration interpretations |
| MOI National Land Management Agency | www.nlma.gov.tw | Building / planning interpretations |
| MOI Department of Land Administration | www.land.moi.gov.tw | Land administration interpretations |
| MOI National Fire Agency | law.nfa.gov.tw | Fire safety interpretations |
| Ministry of Environment regulation system | oaout.moenv.gov.tw | 行政函釋 |
| Examination Yuan shared regulation system | law.exam.gov.tw | Civil service, protection and examination interpretations |
| Ministries' shared regulation systems | law.fsc.gov.tw, edu.law.moe.gov.tw, law.moa.gov.tw, glrs.moi.gov.tw, law.moc.gov.tw, law.nstc.gov.tw, law.cip.gov.tw, law.oac.gov.tw, law.ftc.gov.tw, law.mac.gov.tw, law.cec.gov.tw, law.dgbas.gov.tw, law.mofa.gov.tw, law.vac.gov.tw, erss.nusc.gov.tw, theme.ndc.gov.tw, law.hakka.gov.tw, law.ocac.gov.tw, law.sports.gov.tw | Administrative rules (interpretive orders, letters) of the FSC, Education, Agriculture, Interior, Culture, NSTC, Indigenous Peoples, Ocean Affairs, FTC, Mainland Affairs, Central Election Commission, DGBAS, Foreign Affairs, Veterans Affairs, Nuclear Safety, NDC, Hakka Affairs, Overseas Community Affairs and Sports |
| NCC regulation system | ncclaw.ncc.gov.tw | Interpretive letters, including individual replies |
| Customs Administration | web.customs.gov.tw | Newly issued interpretive letters |
| Mainland Affairs Council | www.mac.gov.tw | Letters and opinions in the mainland-advertising section |
| Ministry of Transportation regulation system | motclaw.motc.gov.tw | Administrative interpretations |
| Central Bank regulation system | www.law.cbc.gov.tw | Orders and letters, appeal decisions |
| Taipei City regulation system | laws.gov.taipei | Interpretations, appeal decisions, local regulations |
| New Taipei City regulation system | web.law.ntpc.gov.tw | Interpretations, appeal decisions, local regulations |
| Executive Yuan Gazette | gazette.nat.gov.tw | Agencies' interpretive rules, pre-announced draft regulations |
| Executive Yuan Petitions and Appeals Committee | appeal.ey.gov.tw | Administrative appeal decisions |
| Fair Trade Commission | www.ftc.gov.tw | Disposition decisions |
| Public Construction Commission | web.pcc.gov.tw, www.pcc.gov.tw | Procurement complaint reviews, appeal decisions |
| Ministry of Labor Board of Unfair Labor Practice Decisions | uflb.mol.gov.tw | Unfair labor practice rulings |
| Civil Service Protection and Training Commission | web13.csptc.gov.tw | Reexamination and re-appeal decisions |
| FSC and its bureaus | www.fsc.gov.tw, www.banking.gov.tw, www.sfb.gov.tw, www.ib.gov.tw | Sanction cases, FSC appeal decisions |
| Control Yuan | www.cy.gov.tw | Investigation reports, corrective measures, impeachments, censures |
| Ministry of Justice lawyer search system | lawyerbc.moj.gov.tw | Lawyer disciplinary decisions |
| MOHW medical affairs system | ma.mohw.gov.tw | Medical disciplinary notices |
| Ministries' appeal decisions | www.moj.gov.tw, www.mofa.gov.tw, law.mnd.gov.tw, nseweb.motc.gov.tw, www.vac.gov.tw, www.nstc.gov.tw, moda.gov.tw, law.cip.gov.tw, eportal2.moea.gov.tw, appeal.moa.gov.tw, appeal.moe.gov.tw, aamis-web.moenv.gov.tw, appealweb.mol.gov.tw, appeal.moc.gov.tw, themedata.culture.tw, aarc.moi.gov.tw, service.mohw.gov.tw, web.cec.gov.tw, www.dgpa.gov.tw | Justice, Foreign Affairs, National Defense, Transportation, Veterans Affairs, NSTC, Digital Affairs, Indigenous Peoples, Economic Affairs, Agriculture, Education, Environment, Labor, Culture, Interior, Health and Welfare, Central Election Commission, DGPA |
| Local governments' appeal decisions | appeal.taichung.gov.tw, web.law.ntpc.gov.tw, law.kcg.gov.tw, www.chcg.gov.tw, glrs.hl.gov.tw, law.kinmen.gov.tw, www.miaoli.gov.tw, www.taitung.gov.tw, general.chiayi.gov.tw, www.cyhg.gov.tw, www.e-land.gov.tw, gdd.hsinchu.gov.tw, www.klcg.gov.tw | Taichung, New Taipei, Kaohsiung, Changhua, Hualien, Kinmen, Miaoli, Taitung, Chiayi City, Chiayi County, Yilan, Hsinchu County, Keelung (Taipei above) |
| Legislative Yuan law system | lis.ly.gov.tw | Legislative history, reasons, legislative process and gazette pages |
| Legislative Yuan parliamentary and gazette site | ppg.ly.gov.tw | Bills, Legislative Yuan Gazette |
| Public Policy Participation Platform (JOIN) | join.gov.tw | Draft laws open for comment |
| Ministry of Justice statistics | www.rjsd.moj.gov.tw | Common statistical tables |
| Academy for the Judiciary (MOJ) | www.cprc.moj.gov.tw | *Crime Situation and Analysis* |
| National Central Library periodical index | tpl.ncl.edu.tw | Article records, abstracts, licensed full text |
| Government Research Bulletin (GRB) | www.grb.gov.tw, grbdef.stpi.niar.org.tw | Research project records and abstracts |
| Academia Sinica Institutum Iurisprudentiae | www.iias.sinica.edu.tw | Academia Sinica Law Journal full text |
| National Chengchi University College of Law | review.law.nccu.edu.tw | NCCU Law Review full text |
| National Taiwan University College of Law | www.law.ntu.edu.tw | NTU Law Journal full text |
| Local government regulation systems | law.tycg.gov.tw, law.taichung.gov.tw, outlaw.kcg.gov.tw, law01.tainan.gov.tw, exlaw.klcg.gov.tw, hclaw.hsinchu.gov.tw, law.hccg.gov.tw, law.miaoli.gov.tw, lawsearch.chcg.gov.tw, glrs.nantou.gov.tw, law.cyhg.gov.tw, law.chiayi.gov.tw, ptlaw.pthg.gov.tw, glrslaw.e-land.gov.tw, glrs.hl.gov.tw, law.taitung.gov.tw, law.penghu.gov.tw, law.kinmen.gov.tw, law.matsu.gov.tw, law.yunlin.gov.tw | Autonomy ordinances and regulations (Taipei and New Taipei above) |
| MOFA treaty database | no06.mofa.gov.tw | Treaties and agreements |
| Ministry of Finance | www.mof.gov.tw | Income tax agreements |
| TWSE regulation knowledge base | twse-regulation.twse.com.tw | TWSE rules |
| Securities and futures regulation system (SFI) | www.selaw.com.tw | TPEx and TAIFEX rules |

Endpoints, query methods, coverage limits and the sources not covered are in [SOURCES.md](SOURCES.md).

`get_judgment` accepts a user-supplied URL, and `mcp_server/config.py:ALLOWED_DOMAINS` restricts that to the judgment and regulation domains; every other tool only calls the fixed endpoints above and never takes an arbitrary URL. The following sites disallow crawlers in robots.txt (or exclude specific paths); for those the server only performs single, user-triggered lookups and never bulk-fetches: the Judicial Yuan law database (legal.judicial.gov.tw), the Ministry of Health and Welfare system (mohwlaw.mohw.gov.tw), the Executive Yuan appeals site (appeal.ey.gov.tw), the Legislative Yuan parliamentary site (ppg.ly.gov.tw), the MOI Department of Land Administration (www.land.moi.gov.tw), the appeal full-text path of the Taipei City regulation system (laws.gov.taipei), the treaty search of the national regulation database (law.moj.gov.tw), the Ministry of Finance `/download/` files (www.mof.gov.tw) and the DGPA website (www.dgpa.gov.tw). The securities and futures regulation system (www.selaw.com.tw) forbids republishing without written permission, so TPEx and TAIFEX rules are for reference only and results carry a notice. Interpretations, resolutions, appeal and FTC decisions and similar documents are official documents, which Taiwan's Copyright Act Art. 9 excludes from copyright; journal articles, research reports and exchange rules are not, so follow each source's terms when quoting them. Full texts licensed through the National Central Library are for personal reading only and are never written to the cache.

**Privacy and access limits**: the server only relays what the official sites publish, at the moment a user asks; it adds no masking of its own and keeps no database. Some appeal decisions (Executive Yuan cases filed up to ROC 108, Ministry of Justice decisions from before about ROC 112, the Council of Indigenous Peoples) are published with the parties' names unmasked and are returned as published. Only public, unauthenticated data is queried. Fresh browser sessions can complete JavaScript/Cloudflare checks and public-query captchas. Each request stays within the user’s query and selected full text; failed verification is reported as an error, never as zero results. No login, staff-only access or bulk collection is used. The Judicial Yuan sentencing system is used only for its public aggregate statistics; the per-judgment list that the site reserves for court staff is never called. See the [Disclaimer](#disclaimer) for the full terms.

**Judgment year coverage**: this server proxies the Judicial Yuan system live and has no database of its own, so effective coverage = whatever the Judicial Yuan holds. Measured (counting hits for keyword 「竊盜」): tens of thousands per year from ROC 89 (2000) onward, ~2,000 total for 81–88 (1992–1999), zero before 80 (1991). The Judicial Yuan states its open-data dump has "the same scope as the judgment search system", so no earlier public source exists. Expect empty or sparse results for pre-2000 queries.

The Constitutional Court corpus (釋字 / 憲判字) is **not** fetched at query time — it is bundled offline (`old_cases.json` / `new_cases.json` / `opinions.zip`), originally sourced from `cons.judicial.gov.tw` and regenerated by a maintainer-run script. See [SOURCES.md](SOURCES.md).

### Constitutional Court data

| Dataset | Records | With reasoning | With opinions | Size |
|---------|---------|----------------|---------------|------|
| Grand Justices interpretations (`old_cases.json`) | 813 | 734 | 472 | 7.4 MB |
| Constitutional Court judgments (`new_cases.json`) | 58 | 58 | 57 | 2.0 MB |
| Justices' opinions, full text (`opinions.zip`) | 1,541 documents | — | 1,541 with full text | 10.8 MB |

From 釋字 No. 401 onward, and for all 憲判字, the official site publishes Justices' opinions only as PDF attachments. Their text is extracted and bundled in `opinions.zip`; responses include `opinion_documents` with each opinion's title, official PDF link and character count. 22 PDFs use fonts that cannot be decoded or contain page images (mostly some opinions in 釋字 Nos. 735–753); they were transcribed verbatim from the page images (flagged `transcribed`; check the official PDF before quoting). The Constitutional Court's published PDFs are treated as authoritative; the National Regulations Database carries later-edited versions of earlier opinions (normalized wording, corrected typos, party names redacted), so wording may differ slightly. Rebuild with `scripts/build_opinions.py`; when the court publishes new 憲判字, `scripts/build_new_cases.py` adds just the new cases (with their opinions).

Interpretations, precedents, decisions, literature, statistics, legislative records and constitutional documents come back as complete text, without character truncation; regulations and other legal texts return only the articles selected with `article_no` (up to 50 per call), never a whole law. Source pagination, keyword-snippet mode, attachment-count and download-size limits still apply. Scanned documents without a text layer retain their original links.

## Caching

| Data type | TTL | Location |
|---|---|---|
| Judgment full text | 30 days | `legal_mcp.db` in the user data directory (SQLite, created on first run) |
| Search results | 24 hours | same |
| Regulation articles | 7 days | same |
| pcode metadata | 30 days | same |
| Appeal history (歷審) | 24 hours (separate from the full text) | same |
| Constitutional Court case lists and case pages | 1 day | same |
| Single documents: decisions, legislative reasons, Constitutional Court case-file documents, gazette records and draft notices, statistical tables, literature | 30 days | same |
| Interpretations and precedents (may be discontinued later) | 7 days | same |
| Local regulations, treaties, exchange rules | 7 days | same |
| Ministry of Justice common statistics | 1 day | same |
| Bill texts, NCL-licensed full texts | not cached | — |
| Consumer Protection Committee, Control Yuan sunshine-law and BSMI interpretation lists | 1 day | memory (refetched after a server restart) |
| English translations, National Land Management Agency letters, IPO copyright interpretations | refreshed weekly (IPO daily) | `en_laws.zip`, `en_orders.zip`, `nlma_interpcomp.json`, `tipo_copyright.xml` in the user data directory |
| 釋字 / 憲判字 | bundled data (never expires) | `mcp_server/data/old_cases.json`, `new_cases.json`, `opinions.zip` |

User data directory: `%LOCALAPPDATA%\mcp-taiwan-legal-db` on Windows, `~/.cache/mcp-taiwan-legal-db` on macOS / Linux (under `XDG_CACHE_HOME` if set); override it with the `MCP_TAIWAN_LEGAL_DB_HOME` environment variable. Flush everything: delete `legal_mcp.db` in that directory.

## pcode_all.json auto-update

On startup, the server checks the age of `pcode_all.json`. If the last update was before the most recent Saturday, it triggers a background refresh from `law.moj.gov.tw` official API and writes the result (together with `law_histories.json` and `law_meta.json`) to the user data directory, leaving the bundled files untouched; on read, whichever of the bundled file and the user copy is newer wins. Failures are logged as warnings and do not block startup.

Manual refresh:
```bash
.venv/bin/python -m mcp_server.updater
```

---

## Project layout

```
mcp-taiwan-legal-db/
├── .gitignore
├── .mcp.json              # Auto-registration for in-folder Claude Code sessions
├── LICENSE                # MIT (code)
├── DATA_LICENSE           # CC0 1.0 (Constitutional Court data)
├── SOURCES.md             # Data provenance
├── CITATION.cff           # Academic citation metadata
├── README.md              # 繁體中文 (primary)
├── README.en.md           # This file (English)
├── pyproject.toml         # Package metadata and deps
└── mcp_server/
    ├── __init__.py
    ├── server.py          # MCPServer entry — defines the 26 @mcp.tool() functions
    ├── config.py          # URLs, court codes, cache TTLs, allowed domains
    ├── updater.py         # Standalone pcode_all.json refresh script
    ├── healthcheck.py     # Live health check of the official sources (python -m mcp_server.healthcheck)
    ├── cache/db.py        # SQLite cache layer
    ├── data/
    │   ├── pcode_all.json          # 11,700+ regulations (bundled, ~780 KB)
    │   ├── law_histories.json      # Amendment history (bundled, ~9.6 MB)
    │   ├── law_meta.json           # Latest promulgation date, commencement notes, category (bundled, ~1.2 MB)
    │   ├── old_cases.json          # 813 Grand Justices interpretations, full text (bundled, ~7.4 MB)
    │   ├── new_cases.json          # 58 Constitutional Court judgments, full text (bundled, ~2.0 MB)
    │   └── opinions.zip            # Justices' opinions extracted from official PDFs (bundled, ~10.8 MB)
    ├── models/            # Judgment / Regulation dataclasses
    ├── parsers/           # HTML parsers for judgment and regulation pages
    ├── tools/
    │   ├── judicial_search.py      # search_judgments
    │   ├── judicial_doc.py         # get_judgment (incl. appeal history)
    │   ├── regulations.py          # query_regulation, get_pcode, search_regulations
    │   ├── constitutional_court.py # get_interpretation, search_interpretations, get_citations
    │   ├── constitutional_docket.py # search_constitutional_docket, get_constitutional_case_file
    │   ├── agency_interpretations.py # search_agency_interpretations, get_agency_interpretation
    │   ├── ip_guidelines.py        # IPO patent and trademark examination guidelines
    │   ├── fint.py                 # search_precedents, get_precedent (Judicial Yuan law database)
    │   ├── admin_decisions.py      # search_administrative_decisions, get_administrative_decision
    │   ├── quasi_judicial.py       # Quasi-judicial decisions (procurement complaints, labor rulings, CSPTC, FSC sanctions, Control Yuan, lawyer discipline)
    │   ├── appeals.py              # Ministries' and local governments' appeal decisions
    │   ├── legislative.py          # get_legislative_history (Legislative Yuan law system)
    │   ├── legislative_records.py  # search_legislative_records, get_legislative_record
    │   ├── statistics.py           # search_statistics, get_statistics
    │   ├── sentencing.py           # get_sentencing_statistics
    │   ├── literature.py           # search_legal_literature, get_legal_literature
    │   ├── other_regulations.py    # search_other_regulations, get_other_regulation
    │   ├── tls.py                  # Bundled TWCA intermediates for sites that omit them
    │   └── pdf_text.py             # PDF text extraction (opinions, decisions, case-file documents)
    └── tests/             # pytest suite
```

## Running the test suite

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest mcp_server/tests/ -v
```

The unit tests mock the official sites, so they cannot tell when a site changes. Before a release, or when someone reports a source returning nothing, run the live health check: it queries each source once, fetches the first full text, and confirms the validity marking still reads on a few interpretations known to be discontinued.

```bash
.venv/bin/python -m mcp_server.healthcheck                          # everything (about 130 checks, a few minutes)
.venv/bin/python -m mcp_server.healthcheck interpretations mof mol  # one tool, some sources
.venv/bin/python -m mcp_server.healthcheck status                   # validity markings only
```

Each line is `OK`, `THIN` (very little text came back), `EMPTY` (no results) or `FAIL`; the exit code is 1 if anything is `EMPTY` or `FAIL`.

---

## About

Maintained by [LawChat](https://lawchat.com.tw) — a Taiwan legal AI platform.

- Website: [lawchat.com.tw](https://lawchat.com.tw)
- Contact: opensource@lawchat.com.tw
- Issues: [GitHub Issues](https://github.com/lawchat-oss/mcp-taiwan-legal-db/issues)

Best-effort maintenance — we keep upstream (Judicial Yuan, Ministry of Justice) compatibility working, no SLA on issues.

## License

**Code**: [MIT License](LICENSE)

**Constitutional Court data**: [CC0 1.0](DATA_LICENSE) (public-domain dedication) — free to use, modify, and redistribute with no permission or attribution required. For academic citation, see [CITATION.cff](CITATION.cff).

Judgment and regulation data sources: [Judicial Yuan](https://judgment.judicial.gov.tw) and [Ministry of Justice](https://law.moj.gov.tw) (public government data).
Constitutional Court data source: [Judicial Yuan Constitutional Court](https://cons.judicial.gov.tw) (public domain under Article 9 of the ROC Copyright Act). See [SOURCES.md](SOURCES.md).

## Disclaimer

**Unofficial.** This tool is not affiliated with, endorsed by, or authorized by the Judicial Yuan, the Ministry of Justice, or any Taiwan government agency.

**This tool is a scraper.** Each time a user runs a query, it sends requests from the user's own machine to the official sites and extracts their web pages, PDFs, or the public APIs their front ends use. Some of these sites disallow crawlers in robots.txt; some present JavaScript / Cloudflare checks or image captchas, which the tool completes locally with a browser (Playwright) or OCR, or through the site's own voice-verification feature; where a site checks its captcha only in the browser, the tool submits the query form directly. All of this happens only for a single, user-initiated query: no login, no staff or privileged-user access, and no bulk download of documents. A few sites have no search function, so at query time the tool downloads the list they publish, matches it locally and caches it briefly. Apart from connecting to the Judicial Yuan judgment system when the server starts and refreshing the national regulation database's open-data code list once a week, the tool fetches nothing in the background and keeps no database.

**Your responsibility.** Requests go out from your machine and network. You are responsible for checking and following each site's terms of use and the applicable law (including Taiwan's Copyright Act and Personal Data Protection Act), and for how you use the results. Do not modify or use this tool to scrape in bulk, at high frequency, or on an automated schedule, and do not build a database from or republish the results; sources that forbid republication (such as the SFI regulation system) and NCL-licensed full texts are for personal reading only.

**Personal data.** Some official documents (such as appeal decisions and disciplinary decisions) are published with the parties' names, and this tool returns them as published without masking. When you process personal data in them, keep within the purposes and reasonable use permitted by the Personal Data Protection Act.

**Accuracy; not legal advice.** Results reflect the official sites at the time of the query and may be incomplete or wrong because of caching (see the TTL table above), site changes, extraction errors, or OCR of scanned files. Neither the tool nor its results are legal advice; verify against the official source before citing or relying on anything.

**No warranty.** The tool is provided "as is" under the MIT License, without warranty of any kind. To the extent permitted by law, the maintainers are not liable for any damage arising from the use of the tool or its results, including incorrect data, a site blocking your connection, or disputes arising from how you use it.

**Building on top of this server.** The project is designed to run on each user's own machine. If you host it as a centralized service or build an agent or application on it (including the examples under [`examples/`](examples/)), you are responsible for every request it sends to the official sites, for how the data is used, and for what you tell your users.

**For site operators.** If an agency or site operator has concerns about how this tool accesses their site, or wants a source removed or accessed differently, open a [GitHub issue](https://github.com/lawchat-oss/mcp-taiwan-legal-db/issues) or email opensource@lawchat.com.tw and we will adjust or remove that source promptly.
