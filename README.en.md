# mcp-taiwan-legal-db

**English** · [繁體中文](https://github.com/lawchat-oss/mcp-taiwan-legal-db/blob/main/README.md)

A Model Context Protocol (MCP) server that gives any MCP-compatible AI assistant direct access to Taiwan (ROC) legal databases:

- **Judicial Yuan judgments** — judgment.judicial.gov.tw (full-text search + get)
- **National regulation database** — law.moj.gov.tw (11,700+ laws and ordinances)
- **Constitutional Court** — 868 Grand Justices interpretations (釋字) and Constitutional Court judgments (憲判字), with full reasoning text, served offline from a bundled cache

Written in Python with the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk). Pure tool wrapper — it makes no network calls outside the official Taiwan government sources listed under [Data sources](#data-sources).

---

## Why we open-sourced this

Taiwan's legal data is public. Open-sourcing this so nobody has to write the same scraper twice.

---

## Features

| Feature | Description |
|---------|-------------|
| **8 MCP tools** | Judgment search / full text, regulation queries, 釋字 / 憲判字 lookup, citation graph |
| **Offline cache** | 868 Grand Justices interpretations and Constitutional Court judgments (with full reasoning text, plus Justices' opinions extracted from the official PDFs) served instantly from bundled data |
| **Citation graph** | Extracts every 釋字 / 憲判字 cited in an interpretation's reasoning, for tracing the evolution of constitutional doctrine |
| **Full-text search** | Keyword search over judgments + 釋字 issue / reasoning full text |
| **Hybrid request strategy** | httpx direct by default (~0.25s); auto-falls back to Playwright to clear the Judicial Yuan F5 WAF, then resumes |

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
> Install it **per user** (the default location is in the user profile). At runtime the server writes its query cache and statute-code table updates next to the package, so a shared all-users location that regular users cannot write to (such as `C:\Program Files`) fails at startup. All-users shared installs are not supported yet.

After install, the `mcp-taiwan-legal-db` entry point is on your PATH. **Wire it into Claude Code** (available from any project):

```bash
claude mcp add taiwan-legal-db mcp-taiwan-legal-db --scope user
```

Then `/mcp` to reload, and Claude will pick up the 8 MCP tools on natural-language queries.

**Chromium (Judicial Yuan WAF fallback)**: since v1.1.0 it is downloaded automatically the first time it is needed. On machines that cannot download at runtime, pre-install it:

```bash
uvx --from mcp-taiwan-legal-db playwright install chromium    # only used when the Judicial Yuan WAF triggers; idle otherwise
```

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

# 3. Install Playwright Chromium (only invoked when the Judicial Yuan WAF triggers; idle otherwise)
.venv/bin/playwright install chromium

# 4. Verify the server starts and registers all 8 tools
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

**Expected output:**
```
Server: 台灣法律資料庫
Tools: ['search_judgments', 'get_judgment', 'query_regulation', 'get_pcode', 'search_regulations', 'get_interpretation', 'search_interpretations', 'get_citations']
✓ Setup OK
```

If that prints without errors, you're done. The repo ships a `.mcp.json` at the root, so **any Claude Code session opened inside this folder will automatically load the server**. No extra registration needed.

---

## What you get

Eight MCP tools, all read-only, all hitting only public Taiwan government databases.

### Statutes and judgments

| Tool | Purpose | Typical call |
|---|---|---|
| `search_judgments` | Search Judicial Yuan judgment database | `search_judgments(case_word="台上", case_number="3753", year_from=114, court="最高法院")` |
| `get_judgment` | Fetch full text of a single judgment by JID or URL | `get_judgment(jid="TPSM,114,台上,3753,20251112,1")` |
| `query_regulation` | Query a regulation article / range / full text / amendment history | `query_regulation(law_name="民法", article_no="184")` |
| `get_pcode` | Resolve regulation name → pcode (law code) | `get_pcode(law_name="律師法")` → `"I0020006"` |
| `search_regulations` | Keyword search across 11,700+ regulations | `search_regulations(keyword="勞動")` |

### Constitutional Court

| Tool | Purpose | Typical call |
|---|---|---|
| `get_interpretation` | Full text of a Grand Justices interpretation (釋字) or Constitutional Court judgment (憲判字) — served from local cache | `get_interpretation("釋字748", reasoning_keyword="婚姻")` |
| `search_interpretations` | Search 釋字 / 憲判字 (matches title + issue + reasoning full text) | `search_interpretations(keyword="集會自由")` |
| `get_citations` | Citation graph: extract every 釋字 / 憲判字 cited in a given interpretation's reasoning | `get_citations("釋字748", include_context=True)` |

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
- Output: `{case_id, court, date, main_text, facts, reasoning, cited_statutes, cited_cases, full_text, source_url}`
- Uses HTTP GET to data.aspx for full text
- Caches results for 30 days

```python
get_judgment(jid="TPSM,114,台上,3753,20251112,1")
```

Single judgments can be 10K+ tokens. Prefer `search_judgments` metadata first, only fetch full text when the user explicitly needs it.
</details>

<details>
<summary><b><code>query_regulation</code></b></summary>

Queries the national regulation database.

```python
# Single article
query_regulation(law_name="民法", article_no="184")

# Range
query_regulation(law_name="民法", from_no="184", to_no="198")

# Full law
query_regulation(law_name="律師法")

# With amendment history
query_regulation(law_name="勞動基準法", article_no="23", include_history=True)
```

Supports both `law_name` (automatic pcode resolution via `get_pcode`) and direct `pcode`. Sub-articles like `247-1`, `15-1` work.
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
```
</details>

<details>
<summary><b><code>get_interpretation</code></b></summary>

Retrieves the full text of a Grand Justices interpretation (釋字 No. 1–813) or a Constitutional Court judgment (憲判字). The default tier is served instantly from a bundled JSON cache.

**Layered design** (saves context):

| Tier | Trigger | Offline? |
|------|---------|----------|
| Default (case ID / date / issue / interpretation text) | always returned | ✓ |
| Reasoning excerpt by keyword | `reasoning_keyword="..."` | ✓ |
| Full reasoning (up to 15,000 chars) | `include_reasoning=True` | ✓ |
| Opinion excerpt by keyword | `opinions_keyword="..."` | ✓ |
| Full opinions | `include_opinions=True` | ✓ |
| One opinion in full | `opinion_document="許宗力"` | ✓ |
| Rest of an opinion longer than 15,000 chars | `opinions_offset=15000` (from the returned `opinions_next_offset`) | ✓ |

```python
# Default tier (offline, ~0ms)
get_interpretation("釋字748")

# Search inside the reasoning for a keyword
get_interpretation("釋字748", reasoning_keyword="婚姻自由")

# Locate a particular Justice in the opinions
get_interpretation("釋字758", opinions_keyword="湯德宗")

# Read one Justice's opinion in full (when all opinions together exceed the size cap)
get_interpretation("釋字758", opinion_document="許宗力")

# An opinion over 15,000 chars is truncated; continue from the returned opinions_next_offset
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
```
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
- The resulting TSPD cookies are persisted to `mcp_server/data/.judicial_cookies.json` (0600 permissions, gitignored)
- Subsequent queries resume via httpx with the refreshed cookies

`cons.judicial.gov.tw` (Constitutional Court) and `law.moj.gov.tw` (regulations) are not affected — they bypass the WAF path entirely.

---

## Data sources

Live queries go to two **public** Taiwan government domains:

| Source | Domain | Used for |
|--------|--------|----------|
| Judicial Yuan judgment system | judgment.judicial.gov.tw | Judgment search + full text (`FJUD/Default_AD.aspx`, `data.aspx`) |
| National regulation database | law.moj.gov.tw | Regulation articles + amendment history (`LawClass/*`) |

`mcp_server/config.py:ALLOWED_DOMAINS` enforces a hard allow-list of exactly these two domains — the server refuses any URL outside them.

**Judgment year coverage**: this server proxies the Judicial Yuan system live and has no database of its own, so effective coverage = whatever the Judicial Yuan holds. Measured (counting hits for keyword 「竊盜」): tens of thousands per year from ROC 89 (2000) onward, ~2,000 total for 81–88 (1992–1999), zero before 80 (1991). The Judicial Yuan states its open-data dump has "the same scope as the judgment search system", so no earlier public source exists. Expect empty or sparse results for pre-2000 queries.

The Constitutional Court corpus (釋字 / 憲判字) is **not** fetched at query time — it is bundled offline (`old_cases.json` / `new_cases.json` / `opinions.zip`), originally sourced from `cons.judicial.gov.tw` and regenerated by a maintainer-run script. See [SOURCES.md](SOURCES.md).

### Constitutional Court data

| Dataset | Records | With reasoning | With opinions | Size |
|---------|---------|----------------|---------------|------|
| Grand Justices interpretations (`old_cases.json`) | 813 | 734 | 472 | 7.4 MB |
| Constitutional Court judgments (`new_cases.json`) | 55 | 55 | 54 | 1.8 MB |
| Justices' opinions, full text (`opinions.zip`) | 1,536 documents | — | 1,536 with full text | 10.8 MB |

From 釋字 No. 401 onward, and for all 憲判字, the official site publishes Justices' opinions only as PDF attachments. Their text is extracted and bundled in `opinions.zip`; responses include `opinion_documents` with each opinion's title, official PDF link and character count. 22 PDFs use fonts that cannot be decoded or contain page images (mostly some opinions in 釋字 Nos. 735–753); they were transcribed verbatim from the page images (flagged `transcribed`; check the official PDF before quoting). The Constitutional Court's published PDFs are treated as authoritative; the National Regulations Database carries later-edited versions of earlier opinions (normalized wording, corrected typos, party names redacted), so wording may differ slightly. Rebuild with `scripts/build_opinions.py`.

## Caching

| Data type | TTL | Location |
|---|---|---|
| Judgment full text | 30 days | `mcp_server/data/cache/legal_mcp.db` (SQLite, created on first run) |
| Search results | 24 hours | same |
| Regulation articles | 7 days | same |
| pcode metadata | 30 days | same |
| 釋字 / 憲判字 | bundled data (never expires) | `mcp_server/data/old_cases.json`, `new_cases.json`, `opinions.zip` |

Flush everything: delete `mcp_server/data/cache/legal_mcp.db`. The cache file is in `.gitignore`.

## pcode_all.json auto-update

On startup, the server checks the age of `mcp_server/data/pcode_all.json`. If the last update was before the most recent Saturday, it triggers a background refresh from `law.moj.gov.tw` official API. Failures are logged as warnings and do not block startup.

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
    ├── server.py          # MCPServer entry — defines the 8 @mcp.tool() functions
    ├── config.py          # URLs, court codes, cache TTLs, allowed domains
    ├── updater.py         # Standalone pcode_all.json refresh script
    ├── cache/db.py        # SQLite cache layer
    ├── data/
    │   ├── pcode_all.json          # 11,700+ regulations (bundled, ~780 KB)
    │   ├── law_histories.json      # Amendment history (bundled, ~9.6 MB)
    │   ├── old_cases.json          # 813 Grand Justices interpretations, full text (bundled, ~7.4 MB)
    │   ├── new_cases.json          # 55 Constitutional Court judgments, full text (bundled, ~1.8 MB)
    │   └── opinions.zip            # Justices' opinions extracted from official PDFs (bundled, ~10.8 MB)
    ├── models/            # Judgment / Regulation dataclasses
    ├── parsers/           # HTML parsers for judgment and regulation pages
    ├── tools/
    │   ├── judicial_search.py      # search_judgments
    │   ├── judicial_doc.py         # get_judgment
    │   ├── regulations.py          # query_regulation, get_pcode, search_regulations
    │   └── constitutional_court.py # get_interpretation, search_interpretations, get_citations
    └── tests/             # pytest suite
```

## Running the test suite

```bash
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest mcp_server/tests/ -v
```

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

This is an **unofficial** tool for querying publicly-available Taiwan legal databases. It is not affiliated with, endorsed by, or authorized by the Judicial Yuan, the Ministry of Justice, or any Taiwan government agency.

The data returned by this tool reflects the state of the upstream official sources at the time of query. It may be cached (see TTLs above), and **must not be treated as legal advice or a substitute for the authoritative official sources**. Always verify against the original sources before relying on the data for any legal or official purpose.

**Building on top of this server.** This project is a data-access layer for public Taiwan legal sources. Any agent, application, or service built on top of it — including the examples under [`examples/`](examples/) — is responsible for its own behavior, output accuracy, and user-facing claims.

本工具為非官方的台灣公開法規資料查詢工具，與司法院、法務部或任何台灣政府機關無隸屬關係。查詢結果以上游官方資料庫當下狀態為準，不得作為法律意見或正式用途依據，使用前請向官方資料庫驗證。
