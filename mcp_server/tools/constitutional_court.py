"""司法院大法官解釋 / 憲法法庭裁判查詢（constitutional-court 模組）

資料來源：cons.judicial.gov.tw（預設層與理由書/意見書從本地 JSON 快取服務，離線優先）
MCP tool 註冊在 server.py，本模組只匯出核心函式。

支援兩套體制：
- 舊制：釋字第 1 號 - 第 813 號（民國 38-110 年）
- 新制：111 年起憲判字（憲法訴訟法新制）

設計哲學（v0.3.0）：
1. 預設層精簡：僅回「結論與拘束力來源」欄位（字號/日期/爭點/解釋文/相關法令），
   絕不截斷、絕不超過數千字。
2. 長文 opt-in：理由書與意見書必須 LLM 明確要求才回傳，避免 context 爆炸。
3. 硬安全閥：僅在極端大案觸發（15000 字），觸發時大聲告知 LLM 不得斷言「未提及」。
4. 字號防碰撞：統一用 case_id 字串介面，後端 regex 解析。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from mcp_server.tools._errors import error_response

BASE = "https://cons.judicial.gov.tw"
TIMEOUT = 15.0

# 硬安全閥：任何 LLM 曝光的長文欄位最終上限。僅在極端大案觸發。
HARD_SAFETY_VALVE = 15000

# Keyword mode snippet 設定
SNIPPET_CONTEXT = 200          # 每個 match 前後各取 200 字
SNIPPET_MAX_MATCHES = 10       # 最多回傳 10 個 match（超過會告知總數）

# 實質內容門檻。極早期釋字（如釋字 1 號）的「意見書、抄本等文件」欄位只有
# OCR 掃描檔 placeholder（例如「釋字第1號解釋_OCR」只有 11 字），應視為無實質內容。
# 真實的意見書/理由書最短也有數百字。
SUBSTANTIVE_THRESHOLD = 50

# Critical fields for sanity-checking parsed pages.
MIN_FIELDS = 3
# 注意：早期大法官解釋（例如釋字第 1 號）只有「解釋文」而無獨立的「理由書」區塊。
# 因此 OLD_CRITICAL 只要求「解釋字號」與「解釋文」兩個欄位必定存在。
OLD_CRITICAL = ("解釋字號", "解釋文")
NEW_CRITICAL = ("判決字號", "主文", "理由")

# 舊制釋字的意見書欄位，官方站的 title 是「意見書、抄本等文件」
OLD_OPINIONS_KEY = "意見書、抄本等文件"
NEW_OPINIONS_KEY = "意見書"

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_client: Optional[httpx.Client] = None

# In-memory listing caches (process lifetime only).
_old_listing: Optional[dict[int, str]] = None
_new_listing: Optional[dict[tuple[int, int], str]] = None
_new_listing_fetched_at: Optional[float] = None  # unix timestamp of last successful fetch
_NEW_LISTING_TTL = 86400.0  # 24 h — new 憲判字 cases are published during the year

# Full-text search issue index (lazy-loaded from data/*.json once per process).
_old_issues: Optional[dict[str, str]] = None  # key: str(number), value: 解釋爭點
_new_issues: Optional[dict[str, str]] = None  # key: "year_number", value: 案由

# Comprehensive default-layer case cache (lazy-loaded from data/*.json once per process).
# old_cases.json supersedes old_issues.json; new_cases.json supersedes new_issues.json.
_old_cases: Optional[dict[str, dict]] = None  # key: str(number), value: all default-layer fields
_new_cases: Optional[dict[str, dict]] = None  # key: "year_number", value: all default-layer fields

_DATA_DIR = Path(__file__).parent.parent / "data"


# ─────────────────────────────────────────────────────────────
# HTTP with retry
# ─────────────────────────────────────────────────────────────

def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
    return _client


class _TransientError(Exception):
    """Raised for HTTP errors we want to retry (5xx, timeout)."""


def _raise_if_transient(resp: httpx.Response) -> None:
    if 500 <= resp.status_code < 600:
        raise _TransientError(f"HTTP {resp.status_code}")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    retry=retry_if_exception_type(
        (_TransientError, httpx.TimeoutException, httpx.NetworkError)
    ),
    reraise=True,
)
def _fetch(url: str, params: Optional[dict] = None) -> httpx.Response:
    """HTTP GET with retry. Retries on 5xx + timeout/network errors only;
    4xx responses bubble up as-is so callers can distinguish 404 from transient failures.
    """
    resp = _get_client().get(url, params=params)
    _raise_if_transient(resp)
    return resp


# ─────────────────────────────────────────────────────────────
# case_id parsing
# ─────────────────────────────────────────────────────────────

_NEW_YEAR_RE = re.compile(r"(\d+)\s*年")
_NEW_NUM_RE = re.compile(r"憲判[^\d]*(\d+)")
_OLD_NUM_RE = re.compile(r"(?:釋字|解釋)[^\d]*(\d+)")
_PURE_NUM_RE = re.compile(r"^\s*(\d+)\s*$")

# Citation extraction — used by get_citations()
_CITATION_OLD_RE = re.compile(r"釋字第\s*(\d+)\s*號")
_CITATION_NEW_RE = re.compile(r"(\d{3,4})\s*年\s*憲判字第\s*(\d+)\s*號")


def _parse_case_id(case_id: str) -> tuple[str, int, int]:
    """將 case_id 字串解析成 (system, number, year)。

    Returns:
        ("釋字", number, 0) 舊制大法官解釋
        ("憲判字", number, year) 新制憲法法庭裁判

    Raises:
        ValueError: 無法解析或缺少必要資訊
    """
    if case_id is None:
        raise ValueError("case_id 不得為空")
    s = case_id.strip()
    if not s:
        raise ValueError("case_id 不得為空")

    # ── 新制：含「憲判」一定是新制 ──
    if "憲判" in s:
        # 先試「NNN年憲判...」標準寫法
        year_m = _NEW_YEAR_RE.search(s)
        if not year_m:
            # 再試「NNN憲判...」簡寫（例：111憲判1、111憲判字第1號）
            year_m = re.match(r"^\s*(\d+)\s*憲判", s)
        if not year_m:
            raise ValueError(
                f"新制憲判字必須指定年度，收到「{case_id}」缺少年度。"
                "請用如「111年憲判字第1號」的格式，或先用 "
                "search_interpretations(keyword='憲判字') 查該號次屬於哪一年。"
            )
        num_m = _NEW_NUM_RE.search(s)
        if not num_m:
            raise ValueError(f"無法從「{case_id}」抽出憲判字號次")
        return ("憲判字", int(num_m.group(1)), int(year_m.group(1)))

    # ── 舊制：含「釋字」或「解釋」（忽略任何年度標記） ──
    # 特別注意：學生可能寫「88 年釋字第 499 號」，其中 88 是聲請/公布年度，
    # 與本工具的 year 參數語意無關，應被忽略。
    if "釋字" in s or "解釋" in s:
        num_m = _OLD_NUM_RE.search(s)
        if not num_m:
            raise ValueError(f"無法從「{case_id}」抽出釋字號次")
        return ("釋字", int(num_m.group(1)), 0)

    # ── fallback：純數字視為舊制釋字 ──
    pure = _PURE_NUM_RE.match(s)
    if pure:
        return ("釋字", int(pure.group(1)), 0)

    raise ValueError(
        f"無法解析 case_id「{case_id}」。"
        "支援格式範例：「釋字第 748 號」、「釋字748」、"
        "「111年憲判字第1號」、「111憲判1」等。"
    )


# ─────────────────────────────────────────────────────────────
# Listing loaders
# ─────────────────────────────────────────────────────────────

def _load_old_listing() -> dict[int, str]:
    """抓舊制釋字列表（一頁 813 筆）→ {number: internal_id}"""
    global _old_listing
    if _old_listing is not None:
        return _old_listing
    r = _fetch(f"{BASE}/judcurrent.aspx", params={"fid": "2195"})
    r.raise_for_status()
    mapping: dict[int, str] = {}
    for m in re.finditer(
        r'title="釋字第(\d+)號"\s+href="/docdata\.aspx\?fid=100&(?:amp;)?id=(\d+)"',
        r.text,
    ):
        mapping[int(m.group(1))] = m.group(2)
    if not mapping:
        for m in re.finditer(
            r'href="/docdata\.aspx\?fid=100&(?:amp;)?id=(\d+)"[^>]*title="釋字第(\d+)號"',
            r.text,
        ):
            mapping[int(m.group(2))] = m.group(1)
    _old_listing = mapping
    return mapping


def _load_new_listing() -> dict[tuple[int, int], str]:
    """抓新制憲判字列表 → {(year, number): internal_id}。24 h TTL。"""
    global _new_listing, _new_listing_fetched_at
    now = time.time()
    if _new_listing is not None:
        if _new_listing_fetched_at is not None and (now - _new_listing_fetched_at) < _NEW_LISTING_TTL:
            return _new_listing
        _new_listing = None  # expired — force re-fetch
    r = _fetch(f"{BASE}/judcurrentNew1.aspx", params={"fid": "38"})
    r.raise_for_status()
    mapping: dict[tuple[int, int], str] = {}
    for m in re.finditer(
        r'href="/docdata\.aspx\?fid=38&(?:amp;)?id=(\d+)"[^>]*title="(\d+)年憲判字第(\d+)號"',
        r.text,
    ):
        mapping[(int(m.group(2)), int(m.group(3)))] = m.group(1)
    if not mapping:
        for m in re.finditer(
            r'title="(\d+)年憲判字第(\d+)號"\s+href="/docdata\.aspx\?fid=38&(?:amp;)?id=(\d+)"',
            r.text,
        ):
            mapping[(int(m.group(1)), int(m.group(2)))] = m.group(3)
    _new_listing = mapping
    _new_listing_fetched_at = time.time()
    return mapping


# ─────────────────────────────────────────────────────────────
# Parser & sanity check
# ─────────────────────────────────────────────────────────────

def _parse_doc_page(html: str) -> dict[str, str]:
    """解析 docdata.aspx 頁面。舊制與新制共用相同 DOM 結構。"""
    soup = BeautifulSoup(html, "html.parser")
    fields: dict[str, str] = {}
    for ul in soup.find_all("ul"):
        title_li = ul.find("li", class_="title", recursive=False)
        text_li = ul.find("li", class_="text", recursive=False)
        if not title_li or not text_li:
            continue
        title = title_li.get_text(strip=True)
        pres = text_li.select("ul.paragraphs pre")
        if pres:
            paragraphs = [p.get_text("\n", strip=True) for p in pres]
            text = "\n\n".join(p for p in paragraphs if p)
        else:
            text = text_li.get_text("\n", strip=True)
        if title in fields:
            continue
        fields[title] = text
    return fields


def _sanity_check(
    parsed: dict[str, str], critical: tuple[str, ...], source_url: str
) -> Optional[dict]:
    """若解析明顯失敗回錯誤 dict；否則 None。"""
    missing = [f for f in critical if not parsed.get(f)]
    if len(parsed) < MIN_FIELDS or missing:
        return error_response(
            "parse_failed",
            fields_missing=missing,
            fields_found=sorted(parsed.keys()),
            source_url=source_url,
            hint=(
                "官方網站 DOM 結構可能變動，或頁面非預期格式。"
                "請回報 constitutional-court MCP 維護者檢查 parser。"
            ),
        )
    return None


def _extract_snippets(
    text: str,
    keyword: str,
    context: int = SNIPPET_CONTEXT,
    max_matches: int = SNIPPET_MAX_MATCHES,
) -> tuple[list[dict], int]:
    """在 text 中尋找 keyword 的所有非重疊 match，回傳 (snippets, total_count)。

    每個 snippet 是以 match 點為中心、前後各 `context` 字的片段。
    若總 match 數超過 `max_matches`，只回傳前 max_matches 個，但 total_count 反映真實總數。
    """
    if not text or not keyword:
        return [], 0
    snippets: list[dict] = []
    total = 0
    pos = 0
    klen = len(keyword)
    while True:
        idx = text.find(keyword, pos)
        if idx < 0:
            break
        total += 1
        if len(snippets) < max_matches:
            start = max(0, idx - context)
            end = min(len(text), idx + klen + context)
            snippet = text[start:end]
            if start > 0:
                snippet = "..." + snippet
            if end < len(text):
                snippet = snippet + "..."
            snippets.append({"snippet": snippet, "position": idx})
        pos = idx + klen
    return snippets, total


def _apply_safety_valve(text: str) -> tuple[str, bool]:
    """長文硬安全閥。僅在極端大案觸發（預設 15000 字）。

    觸發時在尾端注入明確的 system warning，要求 LLM 不得斷言「未提及」。
    """
    if not text or len(text) <= HARD_SAFETY_VALVE:
        return text, False
    original = len(text)
    cut = original - HARD_SAFETY_VALVE
    warning = (
        f"\n\n[System Warning: 本欄位字數過長（原長 {original} 字），"
        f"已截斷末端 {cut} 字。請優先基於已提供的部分進行推理，"
        f"切勿直接斷言「大法官並未提及某事」——被截斷的內容可能包含關鍵論述。"
        f"若判斷末端內容關鍵，可回報使用者需人工查閱完整判決。]"
    )
    return text[:HARD_SAFETY_VALVE] + warning, True


def _load_old_cases() -> dict[str, dict]:
    """讀取 data/old_cases.json（舊制釋字完整預設層快取），lazy-load 一次。"""
    global _old_cases
    if _old_cases is None:
        p = _DATA_DIR / "old_cases.json"
        _old_cases = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _old_cases


def _load_new_cases() -> dict[str, dict]:
    """讀取 data/new_cases.json（新制憲判字完整預設層快取），lazy-load 一次。"""
    global _new_cases
    if _new_cases is None:
        p = _DATA_DIR / "new_cases.json"
        _new_cases = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _new_cases


def _load_old_issues() -> dict[str, str]:
    """舊制釋字解釋爭點索引：優先讀 old_cases.json，fallback 到 old_issues.json。"""
    global _old_issues
    if _old_issues is None:
        cases = _load_old_cases()
        if cases:
            # Extract issues from comprehensive cache
            _old_issues = {k: v.get("issues", "") for k, v in cases.items()}
        else:
            p = _DATA_DIR / "old_issues.json"
            _old_issues = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _old_issues


def _load_new_issues() -> dict[str, str]:
    """新制憲判字案由索引：優先讀 new_cases.json，fallback 到 new_issues.json。"""
    global _new_issues
    if _new_issues is None:
        cases = _load_new_cases()
        if cases:
            _new_issues = {k: v.get("issue_summary", "") for k, v in cases.items()}
        else:
            p = _DATA_DIR / "new_issues.json"
            _new_issues = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _new_issues


def _is_substantive(text: str) -> bool:
    """欄位內容是否為實質內容。用於區分真實的意見書/理由書與 OCR 掃描檔 placeholder。

    極早期釋字（例如釋字 1 號）的「意見書、抄本等文件」欄位內容僅
    「釋字第1號解釋_OCR」(11 字)，是指向掃描圖檔的 placeholder，
    不是真正的電子版意見書內容。
    """
    return bool(text) and len(text.strip()) >= SUBSTANTIVE_THRESHOLD


def _extract_citations(text: str) -> list[dict]:
    """從裁判全文中抽取所有被引用的案件字號，回傳去重排序後的清單。

    Returns list of:
        {"type": "釋字",   "case_id": "釋字第N號",     "number": N}
        {"type": "憲判字", "case_id": "Y年憲判字第N號", "year": Y, "number": N}
    """
    seen: set[str] = set()
    old_cits: list[dict] = []
    new_cits: list[dict] = []

    for m in _CITATION_OLD_RE.finditer(text):
        n = int(m.group(1))
        cid = f"釋字第{n}號"
        if cid not in seen:
            seen.add(cid)
            old_cits.append({"type": "釋字", "case_id": cid, "number": n})

    for m in _CITATION_NEW_RE.finditer(text):
        y, n = int(m.group(1)), int(m.group(2))
        cid = f"{y}年憲判字第{n}號"
        if cid not in seen:
            seen.add(cid)
            new_cits.append({"type": "憲判字", "case_id": cid, "year": y, "number": n})

    return (
        sorted(old_cits, key=lambda x: x["number"])
        + sorted(new_cits, key=lambda x: (x["year"], x["number"]))
    )


def _get_reasoning_text(
    system: str, number: int, year: int
) -> tuple[str, bool, Optional[dict]]:
    """取得裁判理由書全文。優先讀本地快取（不截斷），再 live fetch（截斷 15000）。
    回傳 (text, truncated, error_dict_or_None)。
    """
    if system == "釋字":
        cached = _load_old_cases().get(str(number))
        if cached and "reasoning" in cached:
            text = cached["reasoning"]
            truncated = len(text) > HARD_SAFETY_VALVE
            return (text[:HARD_SAFETY_VALVE] if truncated else text), truncated, None
        result = _get_old_interpretation(number, True, "", False, "")
    else:
        cached = _load_new_cases().get(f"{year}_{number}")
        if cached and "reasoning" in cached:
            text = cached["reasoning"]
            truncated = len(text) > HARD_SAFETY_VALVE
            return (text[:HARD_SAFETY_VALVE] if truncated else text), truncated, None
        result = _get_new_ruling(year, number, True, "", False, "")
    if not result.get("success"):
        return "", False, result
    return result.get("reasoning") or "", result.get("reasoning_truncated", False), None


def _attach_long_field(
    result: dict,
    raw_text: str,
    field_name: str,
    include_full: bool,
    keyword: str,
) -> None:
    """把長文欄位以「keyword 片段 / 全文 / 不附加」三種模式其中一種附加到 result。

    優先序：keyword 模式 > full 模式 > 不附加

    若 raw_text 未達實質內容門檻（_is_substantive()），視為 placeholder（例如
    OCR 掃描檔連結），回傳 `{field}_unavailable=True` 與明確 hint，避免 LLM
    以為該案有實質電子版但 LLM 自己沒查到。
    """
    keyword = (keyword or "").strip()
    wants_field = bool(keyword) or include_full
    if not wants_field:
        return  # 預設層不附加

    raw_len = len(raw_text or "")

    if not _is_substantive(raw_text or ""):
        result[f"{field_name}_unavailable"] = True
        result[f"{field_name}_full_length"] = raw_len
        result[f"{field_name}_hint"] = (
            f"該案的 {field_name} 欄位實際內容僅 {raw_len} 字，"
            "疑為 OCR 掃描檔 placeholder（常見於極早期釋字）。"
            "官方網站未收錄實質電子版，若需原文請人工查閱司法院網站。"
            "LLM 不應據此認定「該案無相關論述」——該案可能在紙本原件中有論述。"
        )
        return

    if keyword:
        matches, total = _extract_snippets(raw_text or "", keyword)
        result[f"{field_name}_matches"] = matches
        result[f"{field_name}_match_count"] = total
        result[f"{field_name}_keyword"] = keyword
        result[f"{field_name}_full_length"] = raw_len
        if total == 0:
            result[f"{field_name}_hint"] = (
                f"0 個 match。該欄位原長 {raw_len} 字。"
                f"若你認為學生提及的內容確實存在但用了不同字串，"
                f"可改用 include_{field_name}=True 取得全文自行判斷。"
            )
        return

    # include_full 模式
    text, trunc = _apply_safety_valve(raw_text or "")
    result[field_name] = text
    result[f"{field_name}_truncated"] = trunc


# ─────────────────────────────────────────────────────────────
# Public tool functions (registered by server.py)
# ─────────────────────────────────────────────────────────────


def get_interpretation(
    case_id: str,
    include_reasoning: bool = False,
    reasoning_keyword: str = "",
    include_opinions: bool = False,
    opinions_keyword: str = "",
) -> dict:
    """取得司法院大法官解釋 / 憲法法庭裁判全文（分層回傳，支援關鍵字片段模式）。

    ⚠️ 使用時機：當學生的作答引用任何大法官解釋或憲法法庭裁判時，務必先用此工具
    查證，不可依賴模型記憶（模型對釋字內容常有幻覺）。

    📋 case_id 格式：直接吃學生/prompt 寫的字號字串，後端自動解析
    - 舊制釋字：「釋字第748號」「釋字748」「釋字 748 號」「解釋字第748號」「748」皆可
    - 新制憲判字：「111年憲判字第1號」「111年憲判字1」「111憲判1」
    - ⚠️ 若學生寫「88 年釋字第 499 號」，其中「88 年」是聲請/公布年度不是字號年度，
      本工具會自動忽略 88 並按舊制釋字第 499 號處理
    - ⚠️ 若學生只寫「憲判字第 1 號」沒給年度，本工具無法判斷屬於哪年，會回錯誤。
      此時請用 search_interpretations(keyword='憲判字') 查該號次落在哪個年度

    📦 預設層回傳（一定包含，不截斷）：
    - 舊制：case_id, case_number, date, issues（解釋爭點）, main_text（解釋文）,
      related_statutes, has_reasoning, has_opinions, source_url
    - 新制：上述再加 petitioner（聲請人）, issue_summary（案由）, summary（判決摘要）
    預設層總字數穩定在 1,500-3,000 字，已足夠驗證多數引用。

    🔑 關鍵字片段模式（reasoning_keyword / opinions_keyword，⭐ LLM 應優先使用）：
    - `reasoning_keyword="國民主權"` → 只回理由書中含「國民主權」的片段（前後各 200 字
      context），不回全文
    - `opinions_keyword="林子儀"` → 只回意見書中含「林子儀」的片段（定位特定大法官）
    - **優先用 keyword 模式**：10 個片段總字數約 2-5k，遠少於全文 10-30k。多數驗證
      情境只需要「該關鍵字有沒有出現、出現在什麼脈絡」，keyword 模式就夠
    - keyword 模式會覆蓋對應的 bool 參數（設 `opinions_keyword="X"` 即自動觸發
      fetch 意見書，無需再設 `include_opinions=True`）
    - 找不到 match 時回 `*_match_count=0` 與原文長度，LLM 可判斷是否改用全文模式
    - 最多回 10 個 match；若總數超過 10，`*_match_count` 會反映真實總數
    - keyword 模式**不會**觸發安全閥，因為它本來就是抽樣片段

    🔴 include_reasoning（預設 False，全文模式）：取得「理由書」/「理由」全文
    - 何時用：學生引的是具體推論細節、無法先猜 keyword 時
    - 預設層的 has_reasoning 旗標告訴你該案是否有獨立理由書（釋字 1 號等早期解釋沒有）

    🔴 include_opinions（預設 False，全文模式）：取得「意見書」全文
    - 何時用：需看完整協同/不同意見書時
    - 絕對不能因預設層沒看到就斷言學生捏造——意見書是真實存在的文件，只是不具拘束力

    ⚠️ 平行呼叫限制：若同一 turn 需查多個解釋，一律先用預設值抓全部，評估後再對
    「最關鍵的一個」發第二次呼叫。**絕對不要對多個解釋同時開啟全文模式**。若真要
    深挖多個，用 keyword 模式可大幅降低 token 用量。

    🛡️ 極端長文安全閥（僅全文模式觸發）：若單一欄位超過 15000 字（例如釋字 748 同婚案
    的意見書），會截斷並在尾端注入 system warning。LLM 不得因看不到某段就斷言
    「大法官並未提及」。

    Args:
        case_id: 解釋/裁判字號字串
        include_reasoning: 是否回傳「理由書」/「理由」全文
        reasoning_keyword: 若非空，在理由書中搜尋該關鍵字並回片段（覆蓋 include_reasoning）
        include_opinions: 是否回傳「意見書」全文
        opinions_keyword: 若非空，在意見書中搜尋該關鍵字並回片段（覆蓋 include_opinions）

    Returns:
        成功：success=True 與預設層欄位，加上：
          - 全文模式：`reasoning` / `opinions` + `*_truncated`
          - keyword 模式：`reasoning_matches` / `opinions_matches`（list of {snippet, position}）
            + `*_match_count` + `*_keyword` + `*_full_length`（+ `*_hint` 於 0 match 時）
        失敗：success=False 與 error / hint
    """
    try:
        system, number, year = _parse_case_id(case_id)
    except ValueError as e:
        return error_response(str(e), case_id=case_id)

    if system == "釋字":
        return _get_old_interpretation(
            number, include_reasoning, reasoning_keyword, include_opinions, opinions_keyword
        )
    return _get_new_ruling(
        year, number, include_reasoning, reasoning_keyword, include_opinions, opinions_keyword
    )


def _get_old_interpretation(
    number: int,
    include_reasoning: bool,
    reasoning_keyword: str,
    include_opinions: bool,
    opinions_keyword: str,
) -> dict:
    if number <= 0:
        return error_response(f"號次必須為正整數（收到 {number}）")

    # 嘗試從本地快取回傳（離線 + 快速路徑）
    # 若 cached 含 reasoning/opinions 全文，opt-in 欄位也可從快取服務
    kw_r = (reasoning_keyword or "").strip()
    kw_o = (opinions_keyword or "").strip()
    cached = _load_old_cases().get(str(number))
    if cached:
        has_r_cache = "reasoning" in cached
        has_o_cache = "opinions" in cached
        needs_live = (
            (include_reasoning and not has_r_cache) or
            (kw_r and not has_r_cache) or
            (include_opinions and not has_o_cache) or
            (kw_o and not has_o_cache)
        )
        if not needs_live:
            result = {
                "success": True,
                "type": "釋字",
                "case_id": f"釋字第{number}號",
                "case_number": cached.get("case_number", f"釋字第{number}號"),
                "date": cached.get("date", ""),
                "issues": cached.get("issues", ""),
                "main_text": cached.get("main_text", ""),
                "main_text_truncated": cached.get("main_text_truncated", False),
                "related_statutes": cached.get("related_statutes", ""),
                "has_reasoning": cached.get("has_reasoning", False),
                "has_opinions": cached.get("has_opinions", False),
                "source_url": cached.get("source_url") or f"{BASE}/jcc/zh-tw/jep03/show?expno={number}",
            }
            _attach_long_field(result, cached.get("reasoning", ""), "reasoning", include_reasoning, kw_r)
            _attach_long_field(result, cached.get("opinions", ""), "opinions", include_opinions, kw_o)
            return result

    try:
        r = _fetch(f"{BASE}/jcc/zh-tw/jep03/show", params={"expno": str(number)})
    except httpx.HTTPError as e:
        return error_response(f"HTTP 錯誤：{e}")
    except _TransientError as e:
        return error_response(f"官方站暫時不可用（已重試 3 次）：{e}")

    # 軟邊界：無效 expno 會 redirect 到 index.aspx；靠最終 URL 判斷
    final_url = str(r.url)
    if r.status_code != 200 or "docdata.aspx" not in final_url:
        return error_response(
            f"查無釋字第 {number} 號",
            final_url=final_url,
            hint=(
                "舊制釋字官方已公告之最後一號為第 813 號（民國 110.12.24）。"
                "若要查新制憲法法庭裁判請以「N年憲判字第M號」格式傳 case_id。"
            ),
        )

    parsed = _parse_doc_page(r.text)
    sanity = _sanity_check(parsed, OLD_CRITICAL, final_url)
    if sanity is not None:
        return sanity

    # 預設層
    main_text, mt_trunc = _apply_safety_valve(parsed.get("解釋文", ""))
    result = {
        "success": True,
        "type": "釋字",
        "case_id": f"釋字第{number}號",
        "case_number": parsed.get("解釋字號", f"釋字第{number}號"),
        "date": parsed.get("解釋公布院令", ""),
        "issues": parsed.get("解釋爭點", ""),
        "main_text": main_text,
        "main_text_truncated": mt_trunc,
        "related_statutes": parsed.get("相關法令", ""),
        "has_reasoning": _is_substantive(parsed.get("理由書", "")),
        "has_opinions": _is_substantive(parsed.get(OLD_OPINIONS_KEY, "")),
        "source_url": final_url,
    }

    # opt-in 層（full 模式 or keyword 模式）
    _attach_long_field(
        result, parsed.get("理由書", ""), "reasoning", include_reasoning, reasoning_keyword
    )
    _attach_long_field(
        result, parsed.get(OLD_OPINIONS_KEY, ""), "opinions", include_opinions, opinions_keyword
    )

    return result


def _get_new_ruling(
    year: int,
    number: int,
    include_reasoning: bool,
    reasoning_keyword: str,
    include_opinions: bool,
    opinions_keyword: str,
) -> dict:
    if number <= 0 or year <= 0:
        return error_response(
            f"號次與年度必須為正整數（收到 year={year}, number={number}）"
        )

    # 嘗試從本地快取回傳（離線 + 快速路徑）
    kw_r = (reasoning_keyword or "").strip()
    kw_o = (opinions_keyword or "").strip()
    cache_key = f"{year}_{number}"
    cached = _load_new_cases().get(cache_key)
    if cached:
        has_r_cache = "reasoning" in cached
        has_o_cache = "opinions" in cached
        needs_live = (
            (include_reasoning and not has_r_cache) or
            (kw_r and not has_r_cache) or
            (include_opinions and not has_o_cache) or
            (kw_o and not has_o_cache)
        )
        if not needs_live:
            result = {
                "success": True,
                "type": "憲判字",
                "case_id": f"{year}年憲判字第{number}號",
                "case_number": cached.get("case_number", f"{year}年憲判字第{number}號"),
                "date": cached.get("date", ""),
                "petitioner": cached.get("petitioner", ""),
                "issue_summary": cached.get("issue_summary", ""),
                "main_text": cached.get("main_text", ""),
                "main_text_truncated": cached.get("main_text_truncated", False),
                "summary": cached.get("summary", ""),
                "summary_truncated": cached.get("summary_truncated", False),
                "related_statutes": cached.get("related_statutes", ""),
                "has_reasoning": cached.get("has_reasoning", False),
                "has_opinions": cached.get("has_opinions", False),
                "source_url": cached.get("source_url") or f"{BASE}/judcurrentNew1.aspx?fid=38",
            }
            _attach_long_field(result, cached.get("reasoning", ""), "reasoning", include_reasoning, kw_r)
            _attach_long_field(result, cached.get("opinions", ""), "opinions", include_opinions, kw_o)
            return result

    try:
        mapping = _load_new_listing()
    except (httpx.HTTPError, _TransientError) as e:
        return error_response(f"載入憲判字列表失敗：{e}")

    key = (year, number)
    if key not in mapping:
        avail_years = sorted({y for y, _ in mapping.keys()})
        return error_response(
            f"查無 {year} 年憲判字第 {number} 號",
            available_years=avail_years,
            hint="新制憲判字自民國 111 年起，每年號次獨立計算。",
        )

    doc_id = mapping[key]
    try:
        r = _fetch(f"{BASE}/docdata.aspx", params={"fid": "38", "id": doc_id})
    except httpx.HTTPError as e:
        return error_response(f"HTTP 錯誤：{e}")
    except _TransientError as e:
        return error_response(f"官方站暫時不可用（已重試 3 次）：{e}")

    if r.status_code != 200:
        return error_response(f"取得裁判頁失敗 (HTTP {r.status_code})")

    parsed = _parse_doc_page(r.text)
    sanity = _sanity_check(parsed, NEW_CRITICAL, str(r.url))
    if sanity is not None:
        return sanity

    # 預設層：新制含判決摘要（短、官方摘要，預設回）
    main_text, mt_trunc = _apply_safety_valve(parsed.get("主文", ""))
    summary, sm_trunc = _apply_safety_valve(parsed.get("判決摘要", ""))
    result = {
        "success": True,
        "type": "憲判字",
        "case_id": f"{year}年憲判字第{number}號",
        "case_number": parsed.get("判決字號", f"{year}年憲判字第{number}號"),
        "date": parsed.get("判決日期", ""),
        "petitioner": parsed.get("聲請人", ""),
        "issue_summary": parsed.get("案由", ""),
        "main_text": main_text,
        "main_text_truncated": mt_trunc,
        "summary": summary,
        "summary_truncated": sm_trunc,
        "related_statutes": parsed.get("相關法令", ""),
        "has_reasoning": _is_substantive(parsed.get("理由", "")),
        "has_opinions": _is_substantive(parsed.get(NEW_OPINIONS_KEY, "")),
        "source_url": str(r.url),
    }

    _attach_long_field(
        result, parsed.get("理由", ""), "reasoning", include_reasoning, reasoning_keyword
    )
    _attach_long_field(
        result, parsed.get(NEW_OPINIONS_KEY, ""), "opinions", include_opinions, opinions_keyword
    )

    return result


def get_citations(
    case_id: str,
    include_context: bool = False,
) -> dict:
    """從裁判理由書中抽取所有引用的大法官解釋 / 憲判字字號。

    ⚠️ 實作原理：下載「理由書/理由」全文，以 regex 比對
    「釋字第 N 號」與「Y 年憲判字第 N 號」兩種標準格式。

    ⚠️ 限制：
    - 若理由書超過 15000 字被安全閥截斷，截斷後段落中的引用會遺漏；
      此時 `reasoning_truncated=True` 提醒清單可能不完整。
    - 非標準格式目前不匹配，例如：
      「第 748 號解釋」（前面沒有「釋字」）
      「釋字第 A 號、第 B 號、第 C 號」（B 和 C 沒有「釋字第」前綴）
    - 早期大法官解釋中以中文數字書寫字號的案件（如「釋字第八十五號」）不匹配。

    Args:
        case_id: 解釋/裁判字號字串（格式同 get_interpretation）
        include_context: 若為 True，每個引用項目附上原文中前後 80 字的片段

    Returns:
        success=True:
          source_case_id, citations（list），citation_count，reasoning_truncated，
          （若截斷）reasoning_truncated_warning
        success=False: error, hint
    """
    try:
        system, number, year = _parse_case_id(case_id)
    except ValueError as e:
        return error_response(str(e), case_id=case_id)

    text, truncated, err = _get_reasoning_text(system, number, year)
    if err is not None:
        return err

    source_cid = (
        f"釋字第{number}號" if system == "釋字" else f"{year}年憲判字第{number}號"
    )

    citations = _extract_citations(text)

    if include_context and text:
        for entry in citations:
            if entry["type"] == "釋字":
                pattern = re.compile(rf"釋字第\s*{entry['number']}\s*號")
            else:
                pattern = re.compile(
                    rf"{entry['year']}\s*年\s*憲判字第\s*{entry['number']}\s*號"
                )
            snippets = []
            for m in pattern.finditer(text):
                start = max(0, m.start() - 80)
                end = min(len(text), m.end() + 80)
                snippet = (
                    ("..." if start > 0 else "")
                    + text[start:end]
                    + ("..." if end < len(text) else "")
                )
                snippets.append(snippet)
            entry["context_snippets"] = snippets

    result: dict = {
        "success": True,
        "source_case_id": source_cid,
        "citations": citations,
        "citation_count": len(citations),
        "reasoning_truncated": truncated,
    }
    if truncated:
        result["reasoning_truncated_warning"] = (
            "理由書因超過 15000 字被安全閥截斷，截斷部分的引用未被收錄。"
            "本清單可能不完整。若需完整引用，請人工查閱官方網站全文。"
        )
    return result


def search_interpretations(
    keyword: str = "",
    year: int = 0,
    number_from: int = 0,
    number_to: int = 0,
    include_old: bool = True,
    include_new: bool = True,
    max_results: int = 30,
) -> dict:
    """列舉司法院大法官解釋 / 憲法法庭裁判（結構化查詢 + 靜態全文索引）。

    `keyword` 支援兩層匹配：
    1. 標題/字號子字串（如 keyword="499" 命中釋字第 499 號）
    2. 靜態解釋爭點/案由索引（如 keyword="集會自由" 命中包含該詞的所有案件）
       索引由 scripts/build_old_issues.py + build_new_issues.py 預先建立並隨套件發布。

    回傳的 `results` 每筆都帶 `case_id` 字串，LLM 拿到後可直接傳給 get_interpretation。

    Args:
        keyword: 關鍵字（標題/字號子字串，或解釋爭點/案由全文匹配）
        year: 篩選民國年度。0 = 不篩選。> 0 時只回傳新制憲判字
        number_from: 起始號次（含）。0 = 不篩選
        number_to: 截止號次（含）。0 = 不篩選
        include_old: 是否包含舊制釋字（year=0 時才生效）
        include_new: 是否包含新制憲判字
        max_results: 回傳筆數上限（預設 30）
    """
    kw = keyword.strip()
    results: list[dict] = []
    errors: list[str] = []

    def _in_range(no: int) -> bool:
        if number_from and no < number_from:
            return False
        if number_to and no > number_to:
            return False
        return True

    if include_new:
        try:
            new_map = _load_new_listing()
            items = sorted(new_map.items(), key=lambda x: x[0], reverse=True)
            for (y, no), _doc_id in items:
                if year and y != year:
                    continue
                if not _in_range(no):
                    continue
                title = f"{y}年憲判字第{no}號"
                if kw:
                    matched = kw in title or kw == str(no) or kw == str(y)
                    if not matched:
                        matched = kw in (_load_new_issues().get(f"{y}_{no}") or "")
                    if not matched:
                        matched = kw in (_load_new_cases().get(f"{y}_{no}", {}).get("reasoning") or "")
                    if not matched:
                        continue
                results.append(
                    {
                        "type": "憲判字",
                        "case_id": title,
                        "year": y,
                        "number": no,
                        "title": title,
                        "issues": _load_new_issues().get(f"{y}_{no}", "") if kw else "",
                    }
                )
        except (httpx.HTTPError, _TransientError) as e:
            errors.append(f"載入憲判字列表失敗：{e}")

    if include_old and year == 0:
        try:
            old_map = _load_old_listing()
            for no in sorted(old_map.keys(), reverse=True):
                if not _in_range(no):
                    continue
                title = f"釋字第{no}號"
                if kw:
                    matched = kw in title or kw == str(no)
                    if not matched:
                        matched = kw in (_load_old_issues().get(str(no)) or "")
                    if not matched:
                        matched = kw in (_load_old_cases().get(str(no), {}).get("reasoning") or "")
                    if not matched:
                        continue
                results.append(
                    {
                        "type": "釋字",
                        "case_id": title,
                        "number": no,
                        "title": title,
                        "issues": _load_old_issues().get(str(no), "") if kw else "",
                    }
                )
        except (httpx.HTTPError, _TransientError) as e:
            errors.append(f"載入釋字列表失敗：{e}")

    truncated = len(results) > max_results
    return {
        "success": True,
        "keyword": keyword,
        "count": len(results),
        "truncated": truncated,
        "note": (
            "keyword 現支援「標題/字號」子字串匹配與靜態「解釋爭點/案由」全文索引。"
            "若需查看具體內容，請將 results 裡的 case_id 傳給 get_interpretation()。"
        ),
        "errors": errors if errors else None,
        "results": results[:max_results],
    }


