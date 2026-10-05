"""司法院大法官解釋 / 憲法法庭裁判查詢（constitutional-court 模組）

資料來源：cons.judicial.gov.tw（預設層與理由書/意見書從本地 JSON 快取服務，離線優先）
MCP tool 註冊在 server.py，本模組只匯出核心函式。

支援兩套體制：
- 舊制：釋字第 1 號 - 第 813 號（民國 38-110 年）
- 新制：111 年起憲判字（憲法訴訟法新制）

回傳方式：
1. 預設層精簡：僅回「結論與拘束力來源」欄位（字號/日期/爭點/解釋文/相關法令），
   完整保留該欄位。
2. 長文 opt-in：理由書與意見書必須 LLM 明確要求才回傳，避免 context 爆炸。
3. 全文模式不按字數截斷；關鍵字模式仍回傳命中片段。
4. 字號防碰撞：統一用 case_id 字串介面，後端 regex 解析。
"""

from __future__ import annotations

import html
import json
import re
import time
import zipfile
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

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

# 舊制釋字的意見書欄位：釋字 736 號以前官方站 title 是「意見書、抄本等文件」，737 號起改為「意見書」
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
# 意見書全文（由官網 PDF 附件擷取，scripts/build_opinions.py 產生）。每案一個 member，查詢時才讀。
_OPINIONS_ZIP = _DATA_DIR / "opinions.zip"


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
# 並列引用常省略前綴：「釋字第477號、第747號及第762號」「112年憲判字第4號、第11號」
_CITATION_TAIL = r"((?:\s*[、及與和暨，,]\s*第\s*\d+\s*號)*)"
_CITATION_OLD_RE = re.compile(r"釋字第\s*(\d+)\s*號" + _CITATION_TAIL)
_CITATION_NEW_RE = re.compile(r"(\d{3,4})\s*年\s*憲判字第\s*(\d+)\s*號" + _CITATION_TAIL)
_CITATION_TAIL_NUM = re.compile(r"第\s*(\d+)\s*號")


def _citation_hits(text: str) -> list[tuple[dict, int, int]]:
    """全文中的每一個釋字／憲判字引用：(entry, 該串引用起點, 終點)。"""
    hits = []
    for m in _CITATION_OLD_RE.finditer(text):
        for n in [m.group(1), *_CITATION_TAIL_NUM.findall(m.group(2))]:
            hits.append(({"type": "釋字", "case_id": f"釋字第{int(n)}號", "number": int(n)}, m.start(), m.end()))
    for m in _CITATION_NEW_RE.finditer(text):
        y = int(m.group(1))
        for n in [m.group(2), *_CITATION_TAIL_NUM.findall(m.group(3))]:
            hits.append(({"type": "憲判字", "case_id": f"{y}年憲判字第{int(n)}號", "year": y, "number": int(n)},
                         m.start(), m.end()))
    return hits


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


# ─────────────────────────────────────────────────────────────
# 意見書附件（官網頁面只列標題與 PDF 連結；scripts/build_opinions.py 也用這幾個函式）
# ─────────────────────────────────────────────────────────────

# 標題含「意見書」但不是大法官意見書的附件（鑑定、法庭之友、聲請、機關陳述等）
NOT_JUSTICE = re.compile(r"鑑定|法庭之友|聲請|陳述|相關機關|教授|研究員|律師|醫師|監察院|財政部|政府|基金會|聯盟|協會|公會|研究會|函|簡報|補充|辯論|諮詢|君")
# 標題中的大法官姓名有兩種寫法：「許大法官宗力」（姓 + 大法官 + 名）與「蔡宗珍大法官」（全名 + 大法官）
JUSTICE_SPLIT_NAME = re.compile(
    r"([\u4e00-\u9fff])大法官([\u4e00-\u9fff]{1,2}?)(?=提出|加入|共同|協同|部分|一部|不同|意見|、|，|之|及|與|均|（|\(|）|\)|\.|$)"
)
JUSTICE_FULL_NAME = re.compile(r"([\u4e00-\u9fff]{2,3})大法官(?=提出|加入|、|，|及|與|均|）|\)|$)")
OPINION_TYPE = re.compile(r"(部分不同部分協同|部分協同部分不同|部分協同|部分不同|一部不同|協同|不同)意見書")


def parse_opinion_title(title: str) -> dict:
    """從標題拆出提出者、加入者與意見書類型；早期標題沒寫姓名時為空清單／None。

    >>> parse_opinion_title("許大法官玉秀提出，林大法官子儀、許大法官宗力加入之部分不同意見書")
    {'authors': ['許玉秀'], 'joined': ['林子儀', '許宗力'], 'type': '部分不同'}
    """
    def names(part: str) -> list[str]:
        part = re.sub(r"^[\d.]*|^.*?判決", "", part)  # 去掉號次與「…判決」前綴，免得被當成全名的一部分
        return ["".join(m) for m in JUSTICE_SPLIT_NAME.findall(part)] or JUSTICE_FULL_NAME.findall(part)

    head, sep, tail = title.partition("提出")
    authors = names(head if sep else title)
    joined = names(tail.rsplit("加入", 1)[0]) if "加入" in tail else []
    kind = OPINION_TYPE.search(title)
    return {"authors": authors, "joined": joined, "type": kind.group(1) if kind else None}


def page_attachments(page: str, url: str) -> list[dict]:
    """頁面上所有下載附件的標題與網址。"""
    atts: list[dict] = []
    for href, label in re.findall(r'<a[^>]+href="([^"]*download[^"]*)"[^>]*>(.*?)</a>', page, re.I | re.S):
        link = urljoin(url, html.unescape(href))
        if all(a["url"] != link for a in atts):
            atts.append({"title": re.sub(r"<[^>]+>|\s+", " ", html.unescape(label)).strip(), "url": link})
    return atts


def opinion_documents(attachments: list[dict], require_justice: bool = False) -> list[dict]:
    """挑出大法官意見書附件；有單份意見書就不取抄本合訂本。

    憲判字（require_justice=True）的大法官意見書標題一律含「大法官」；舊制早期少數
    意見書標題沒有（如「387意見書」），改以排除非大法官文件的關鍵字判斷。
    """
    atts = [
        a for a in attachments
        if "意見書" in a["title"] and "打包" not in a["title"]
        and (
            "大法官" in a["title"]
            # 抄本是法院的合訂本（標題會列出內含聲請書等），不套用排除清單
            or (not require_justice and ("抄本" in a["title"] or not NOT_JUSTICE.search(a["title"])))
        )
    ]
    return [a for a in atts if "抄本" not in a["title"]] or atts


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


def _bundled_opinions(member: str) -> Optional[dict]:
    """讀 opinions.zip 中單一案件（member 如 "old/758"）的意見書 {"documents": [{title, url, text}, ...]}；無資料回 None。"""
    if not _OPINIONS_ZIP.exists():
        return None
    with zipfile.ZipFile(_OPINIONS_ZIP) as zf:
        try:
            return json.loads(zf.read(f"{member}.json"))
        except KeyError:
            return None


def _attach_html_opinions(
    result: dict, text: str, include_full: bool, keyword: str, document: str, offset: int = 0
) -> None:
    """沒有 PDF 擷取資料時的意見書（早期釋字的網頁內文，或打包後才公布、走 live 查詢的新案）。

    整段文字無法可靠切成單份；指定 document 時，姓名有出現在內文才回傳，否則明確回報找不到。
    """
    document = (document or "").strip()
    if document and document not in text:
        result["opinions_unavailable"] = True
        result["opinions_hint"] = f"該案意見書中找不到「{document}」。"
        return
    _attach_long_field(result, text, "opinions", include_full or bool(document), keyword, offset)


def _attach_opinions(
    result: dict, cached: dict, member: str, include_full: bool, keyword: str, document: str = "", offset: int = 0
) -> None:
    """快取路徑的意見書：PDF 擷取全文優先，其次 case JSON 內的 HTML 意見書；並附上附件清單。

    document 非空時只取標題含該字串的意見書（例如大法官姓名），完整回傳該份文字。
    """
    document = (document or "").strip()
    if not (include_full or keyword or document):
        return
    if cached.get("opinion_documents"):
        result["opinion_documents"] = cached["opinion_documents"]
    bundled = _bundled_opinions(member)
    if not bundled:
        _attach_html_opinions(result, cached.get("opinions", ""), include_full, keyword, document, offset)
        return
    docs = bundled["documents"]
    if document:
        # 官網標題把姓名拆成「許大法官宗力」，比對時也試去掉「大法官」後的字串
        docs = [d for d in docs if document in d["title"] or document in d["title"].replace("大法官", "")]
        if not docs:
            result["opinions_unavailable"] = True
            result["opinions_hint"] = f"找不到標題含「{document}」的意見書，請從 opinion_documents 的 title 挑選字串。"
            return
    text = "\n\n".join(
        f"【{d['title']}】" + ("（本份由 PDF 頁面影像轉錄，〔?〕為無法辨識之字，引用前請核對官網 PDF）" if d.get("transcribed") else "")
        + f"\n{d['text']}"
        for d in docs if d["text"]
    )
    _attach_long_field(result, text, "opinions", include_full or bool(document), keyword, offset)


def _extract_citations(text: str) -> list[dict]:
    """從裁判全文中抽取所有被引用的案件字號，回傳去重排序後的清單。

    Returns list of:
        {"type": "釋字",   "case_id": "釋字第N號",     "number": N}
        {"type": "憲判字", "case_id": "Y年憲判字第N號", "year": Y, "number": N}
    """
    unique = {e["case_id"]: e for e, _, _ in _citation_hits(text)}
    return sorted(unique.values(), key=lambda x: (x["type"] != "釋字", x.get("year", 0), x["number"]))


def _get_reasoning_text(
    system: str, number: int, year: int
) -> tuple[str, bool, Optional[dict]]:
    """取得裁判理由書全文。優先讀本地快取，再 live fetch；不按字數截斷。
    回傳 (text, truncated, error_dict_or_None)。
    """
    if system == "釋字":
        cached = _load_old_cases().get(str(number))
        if cached and "reasoning" in cached:
            text = cached["reasoning"]
            return text, False, None
        result = _get_old_interpretation(number, True, "", False, "")
    else:
        cached = _load_new_cases().get(f"{year}_{number}")
        if cached and "reasoning" in cached:
            text = cached["reasoning"]
            return text, False, None
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
    offset: int = 0,
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
            f"該案的 {field_name} 沒有可用的電子全文（實際內容僅 {raw_len} 字）。"
            "可能是該案沒有此類文件、只有掃描圖檔，或文件僅以附件形式公開。"
            "若回傳含 opinion_documents，可依其中的附件連結查閱原文；否則請查閱 source_url。"
            "LLM 不應據此認定「該案無相關論述」。"
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

    # 保留 offset 參數相容性，從指定位置回傳所有剩餘文字。
    result[field_name] = (raw_text or "")[max(0, offset):]
    result[f"{field_name}_truncated"] = False
    result[f"{field_name}_full_length"] = raw_len


# ─────────────────────────────────────────────────────────────
# Public tool functions (registered by server.py)
# ─────────────────────────────────────────────────────────────


def get_interpretation(
    case_id: str,
    include_reasoning: bool = False,
    reasoning_keyword: str = "",
    include_opinions: bool = False,
    opinions_keyword: str = "",
    opinion_document: str = "",
    opinions_offset: int = 0,
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
    - keyword 模式只回命中片段；需要完整上下文時使用 include_reasoning／include_opinions

    🔴 include_reasoning（預設 False，全文模式）：取得「理由書」/「理由」全文
    - 何時用：學生引的是具體推論細節、無法先猜 keyword 時
    - 預設層的 has_reasoning 旗標告訴你該案是否有獨立理由書（釋字 1 號等早期解釋沒有）

    🔴 include_opinions（預設 False，全文模式）：取得「意見書」全文
    - 何時用：需看完整協同/不同意見書時
    - 絕對不能因預設層沒看到就斷言學生捏造——意見書是真實存在的文件，只是不具拘束力
    - 回傳會附 `opinion_documents`：每份意見書的 title、authors（提出者）、joined（加入者）、
      type（協同／部分協同／不同／部分不同…）、url（官網 PDF）、chars（0 表示無法擷取電子文字，只能看 url）；
      transcribed=true 表示該份 PDF 無法擷取文字、改由頁面影像轉錄，引用前應核對官網 PDF

    🎯 opinion_document（預設 ""）：只取標題含此字串的意見書全文，例如 `opinion_document="許宗力"`
    - 何時用：只需要其中一位大法官的完整意見時；全文不按字數截斷

    ⚠️ 平行呼叫限制：若同一 turn 需查多個解釋，一律先用預設值抓全部，評估後再對
    「最關鍵的一個」發第二次呼叫。**絕對不要對多個解釋同時開啟全文模式**。若真要
    深挖多個，用 keyword 模式可大幅降低 token 用量。

    Args:
        case_id: 解釋/裁判字號字串
        include_reasoning: 是否回傳「理由書」/「理由」全文
        reasoning_keyword: 若非空，在理由書中搜尋該關鍵字並回片段（覆蓋 include_reasoning）
        include_opinions: 是否回傳「意見書」全文
        opinions_keyword: 若非空，在意見書中搜尋該關鍵字並回片段（覆蓋 include_opinions）
        opinion_document: 若非空，只取標題含此字串的意見書（例如大法官姓名）
        opinions_offset: 意見書全文從第幾字開始回傳（預設 0，回傳所有剩餘文字）

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
            number, include_reasoning, reasoning_keyword, include_opinions, opinions_keyword,
            opinion_document, opinions_offset,
        )
    return _get_new_ruling(
        year, number, include_reasoning, reasoning_keyword, include_opinions, opinions_keyword,
        opinion_document, opinions_offset,
    )


def _get_old_interpretation(
    number: int,
    include_reasoning: bool,
    reasoning_keyword: str,
    include_opinions: bool,
    opinions_keyword: str,
    opinion_document: str = "",
    opinions_offset: int = 0,
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
            _attach_opinions(result, cached, "old/" + str(number), include_opinions, kw_o, opinion_document, opinions_offset)
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

    old_opinions = parsed.get(OLD_OPINIONS_KEY) or parsed.get(NEW_OPINIONS_KEY, "")

    # 預設層
    main_text, mt_trunc = parsed.get("解釋文", ""), False
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
        "has_opinions": _is_substantive(old_opinions),
        "source_url": final_url,
    }

    # opt-in 層（full 模式 or keyword 模式）
    _attach_long_field(
        result, parsed.get("理由書", ""), "reasoning", include_reasoning, reasoning_keyword
    )
    _attach_html_opinions(result, old_opinions, include_opinions, opinions_keyword, opinion_document, opinions_offset)

    return result


def _get_new_ruling(
    year: int,
    number: int,
    include_reasoning: bool,
    reasoning_keyword: str,
    include_opinions: bool,
    opinions_keyword: str,
    opinion_document: str = "",
    opinions_offset: int = 0,
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
            _attach_opinions(result, cached, "new/" + cache_key, include_opinions, kw_o, opinion_document, opinions_offset)
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
    main_text, mt_trunc = parsed.get("主文", ""), False
    summary, sm_trunc = parsed.get("判決摘要", ""), False
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
    # 憲判字頁面的「意見書」欄位只是附件標題清單，全文在 PDF；打包後才公布的新案只能給附件連結
    docs = opinion_documents(page_attachments(r.text, str(r.url)), require_justice=True)
    if docs:
        result["has_opinions"] = True
        result["opinion_documents"] = [
            {"title": d["title"], **parse_opinion_title(d["title"]), "url": d["url"]} for d in docs
        ]
        if include_opinions or opinions_keyword or opinion_document:
            result["opinions_unavailable"] = True
            result["opinions_hint"] = (
                "本案在資料包建置後才公布，意見書全文目前只有官網 PDF，請開 opinion_documents 的 url 閱讀；"
                "下一版資料包會收錄全文。"
            )
    else:
        _attach_html_opinions(
            result, parsed.get(NEW_OPINIONS_KEY, ""), include_opinions, opinions_keyword, opinion_document, opinions_offset
        )

    return result


def _cited_by(system: str, number: int, year: int, include_context: bool) -> list[dict]:
    """本地收錄的釋字／憲判字中，主文或理由書提到目標字號的案件（不含意見書）。"""
    target = f"釋字第{number}號" if system == "釋字" else f"{year}年憲判字第{number}號"
    cases = [
        ({"type": "釋字", "case_id": f"釋字第{k}號", "number": int(k)}, v) for k, v in _load_old_cases().items()
    ] + [
        ({"type": "憲判字", "case_id": f"{k.split('_')[0]}年憲判字第{k.split('_')[1]}號",
          "year": int(k.split("_")[0]), "number": int(k.split("_")[1])}, v)
        for k, v in _load_new_cases().items()
    ]
    out = []
    for entry, case in cases:
        if entry["case_id"] == target:
            continue
        text = f"{case.get('main_text') or ''}\n{case.get('reasoning') or ''}"
        spans = [(a, b) for e, a, b in _citation_hits(text) if e["case_id"] == target]
        if not spans:
            continue
        entry = {**entry, "date": case.get("date", "")}
        if include_context:
            entry["context_snippets"] = [
                ("..." if a > 80 else "") + text[max(0, a - 80): b + 80] + "..." for a, b in spans
            ]
        out.append(entry)
    return sorted(out, key=lambda x: (x["type"] != "釋字", x.get("year", 0), x["number"]))


def get_citations(
    case_id: str,
    include_context: bool = False,
    direction: str = "cites",
) -> dict:
    """從裁判理由書中抽取所有引用的大法官解釋 / 憲判字字號。

    ⚠️ 實作原理：下載「理由書/理由」全文，以 regex 比對
    「釋字第 N 號」與「Y 年憲判字第 N 號」兩種標準格式。

    ⚠️ 限制：
    - 非標準格式目前不匹配，例如「第 748 號解釋」（前面沒有「釋字」）。
      並列的「釋字第 A 號、第 B 號及第 C 號」會一併收錄。
    - 早期大法官解釋中以中文數字書寫字號的案件（如「釋字第八十五號」）不匹配。

    Args:
        case_id: 解釋/裁判字號字串（格式同 get_interpretation）
        include_context: 若為 True，每個引用項目附上原文中前後 80 字的片段
        direction: "cites"（預設）＝這件引用了哪些；"cited_by"＝後來哪些釋字／憲判字的主文或理由書引用了這件
            （比對本地收錄的全部案件，不含意見書與資料包建置後才公布的新案）

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

    if direction == "cited_by":
        cited_by = _cited_by(system, number, year, include_context)
        return {
            "success": True,
            "source_case_id": f"釋字第{number}號" if system == "釋字" else f"{year}年憲判字第{number}號",
            "cited_by": cited_by,
            "cited_by_count": len(cited_by),
            "note": "要找引用這件的法院判決，用 search_judgments(keyword=\"釋字第N號\") 這類完整字號全文檢索。",
        }
    if direction != "cites":
        return error_response("direction 只能是 cites 或 cited_by", case_id=case_id)

    text, truncated, err = _get_reasoning_text(system, number, year)
    if err is not None:
        return err

    source_cid = (
        f"釋字第{number}號" if system == "釋字" else f"{year}年憲判字第{number}號"
    )

    citations = _extract_citations(text)

    if include_context and text:
        spans: dict[str, list[tuple[int, int]]] = {}
        for e, a, b in _citation_hits(text):
            spans.setdefault(e["case_id"], []).append((a, b))
        for entry in citations:
            entry["context_snippets"] = [
                ("..." if a > 80 else "") + text[max(0, a - 80): b + 80] + ("..." if b + 80 < len(text) else "")
                for a, b in spans.get(entry["case_id"], [])
            ]

    result: dict = {
        "success": True,
        "source_case_id": source_cid,
        "citations": citations,
        "citation_count": len(citations),
        "reasoning_truncated": truncated,
    }
    if truncated:
        result["reasoning_truncated_warning"] = (
            "來源理由書不完整，缺少部分的引用未被收錄。"
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
        max_results: 回傳筆數上限（預設 30，上限 200）
    """
    if max_results <= 0:
        return error_response("max_results 必須大於 0")
    max_results = min(max_results, 200)
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


