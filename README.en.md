# mcp-taiwan-legal-db

**English** · [繁體中文](https://github.com/lawchat-oss/mcp-taiwan-legal-db/blob/main/README.md)

A Model Context Protocol (MCP) server that gives any MCP-compatible AI assistant direct access to Taiwan (ROC) legal databases:

- **Judicial Yuan judgments** — judgment.judicial.gov.tw (full-text search + get)
- **National regulation database** — law.moj.gov.tw (11,700+ laws and ordinances)

Written in Python with [FastMCP](https://github.com/modelcontextprotocol/python-sdk). Pure tool wrapper — no network calls outside the two official Taiwan government sources above.

---

## Why we open-sourced this

Taiwan's legal data is public. Open-sourcing this so nobody has to write the same scraper twice.

---

## ⚡ Install (via PyPI — recommended)

```bash
pip install mcp-taiwan-legal-db
```

> **Note for Debian / Ubuntu / WSL users**: the system Python is protected by PEP 668, so a bare `pip install` is blocked. Use one of:
> - `pipx install mcp-taiwan-legal-db` (recommended — isolated venv, standard for Python CLI tools)
> - or `pip install --user --break-system-packages mcp-taiwan-legal-db`

After install, the `mcp-taiwan-legal-db` entry point is on your PATH. **Wire it into Claude Code** (available from any project):

```bash
claude mcp add taiwan-legal-db mcp-taiwan-legal-db --scope user
```

Then `/mcp` to reload, and Claude will pick up the 8 MCP tools on natural-language queries.

**Optional — F5 WAF fallback**:

```bash
playwright install chromium    # only invoked when the Judicial Yuan WAF triggers; idle otherwise
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
- Filter by `court`, `case_type` (民事/刑事/行政/懲戒), `year_from`/`year_to`
- Returns results auto-sorted by court authority (最高 → 高等 → 地方)

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

```python
# Default tier (offline, ~0ms)
get_interpretation("釋字748")

# Search inside the reasoning for a keyword
get_interpretation("釋字748", reasoning_keyword="婚姻自由")

# Locate a particular Justice in the opinions
get_interpretation("釋字499", opinions_keyword="林子儀")

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

All data is fetched from **public** Taiwan government databases. No other network calls are made:

- `judgment.judicial.gov.tw` — Judicial Yuan judgment database
- `data.judicial.gov.tw` — Judicial Yuan open data API
- `law.moj.gov.tw` — Ministry of Justice national regulation database

`mcp_server/config.py:ALLOWED_DOMAINS` enforces this as a hard allow-list. The server refuses to fetch any URL outside these domains.

## Caching

| Data type | TTL | Location |
|---|---|---|
| Judgment full text | 30 days | `mcp_server/data/cache/legal_mcp.db` (SQLite, created on first run) |
| Search results | 24 hours | same |
| Regulation articles | 7 days | same |
| pcode metadata | 30 days | same |

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
├── LICENSE                # MIT
├── README.md              # 繁體中文 (primary)
├── README.en.md           # This file (English)
├── pyproject.toml         # Package metadata and deps
└── mcp_server/
    ├── __init__.py
    ├── server.py          # FastMCP entry — defines the 5 @mcp.tool() functions
    ├── config.py          # URLs, court codes, cache TTLs, allowed domains
    ├── updater.py         # Standalone pcode_all.json refresh script
    ├── cache/db.py        # SQLite cache layer
    ├── data/
    │   ├── pcode_all.json          # 11,700+ regulations (bundled, ~780 KB)
    │   └── law_histories.json      # Amendment history (bundled, ~9.6 MB)
    ├── models/            # Judgment / Regulation dataclasses
    ├── parsers/           # HTML parsers for judgment and regulation pages
    ├── tools/
    │   ├── judicial_search.py      # search_judgments
    │   ├── judicial_doc.py         # get_judgment
    │   └── regulations.py          # query_regulation, get_pcode, search_regulations
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

[MIT](LICENSE)

## Disclaimer

This is an **unofficial** tool for querying publicly-available Taiwan legal databases. It is not affiliated with, endorsed by, or authorized by the Judicial Yuan, the Ministry of Justice, or any Taiwan government agency.

The data returned by this tool reflects the state of the upstream official sources at the time of query. It may be cached (see TTLs above), and **must not be treated as legal advice or a substitute for the authoritative official sources**. Always verify against the original sources before relying on the data for any legal or official purpose.

**Building on top of this server.** This project is a data-access layer for public Taiwan legal sources. Any agent, application, or service built on top of it — including the examples under [`examples/`](examples/) — is responsible for its own behavior, output accuracy, and user-facing claims.

本工具為非官方的台灣公開法規資料查詢工具，與司法院、法務部或任何台灣政府機關無隸屬關係。查詢結果以上游官方資料庫當下狀態為準，不得作為法律意見或正式用途依據，使用前請向官方資料庫驗證。
