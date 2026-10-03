"""行政救濟與處分決定查詢：行政院訴願決定、公平交易委員會處分書，以及 quasi_judicial 的準司法機關決定、
appeals 的各部會與地方政府訴願決定

兩者全文都只有 PDF（行政院 108 年以前收辦的案件是 HTML）；查詢時即時向官方網站取得、擷取文字。
決定 id 一律為「來源代碼:原站識別碼」，例如 ey:A-115-000633、ftc:73ce9ef1-….pdf。
行政院訴願網站的 robots.txt 不允許爬蟲：這裡只做使用者觸發的單次查詢，不批次抓取。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date, datetime
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

from mcp_server.cache.db import CacheDB
from mcp_server.tools import appeals, fint, quasi_judicial
from mcp_server.tools._errors import error_response
from mcp_server.tools.pdf_text import pdf_to_text

logger = logging.getLogger(__name__)

USER_AGENT = fint.USER_AGENT
MAX_FULL_TEXT = 30000

EY_BASE = "https://appeal.ey.gov.tw"
FTC_LIST_URL = "https://www.ftc.gov.tw/internet/main/decision/decisionList.aspx?mid=11"
FTC_PDF_BASE = "https://www.ftc.gov.tw/uploadDecision/"
_FTC = "ctl00$ContentPlaceHolder1$"


def _roc_to_iso(s: str) -> str:
    m = re.search(r"(\d{2,3})\D(\d{1,2})\D(\d{1,2})", s or "")
    return f"{int(m.group(1)) + 1911:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else (s or "")


def _roc_slash(year: int, end: bool) -> str:
    return f"{year}/{'12/31' if end else '01/01'}" if year else ""


# ─────────────────────────────────────────────────────────────
# 行政院訴願決定（appeal.ey.gov.tw）：JSON API，免 session
# ─────────────────────────────────────────────────────────────

_EY_CASE_NO = re.compile(r"^A-\d{3}-\d{6}$")
# 每頁頁首「案號：A-115-000633 第 1 頁(共 6 頁)」，擷取後會黏在段落中間
_EY_PAGE_HEADER = re.compile(r"案號：A-\d{3}-\d{6}\s*第\s*\d+\s*頁\s*[（(]共\s*\d+\s*頁[)）]\s*")


def _ey_layout(text: str) -> str:
    """決定書 PDF 沒有縮排可判斷段落：在「主文／事實／理由」與句末後的「一、」等處斷行。"""
    text = _EY_PAGE_HEADER.sub("", text)
    text = re.sub(r"(?<=[。：])\s*(主文|事實|理由)(?=[\s\S])", r"\n\1\n", text)
    return re.sub(r"(?<=[。：])\s*([一二三四五六七八九十]+、|[（(][一二三四五六七八九十]+[)）])", r"\n\1", text).strip()


async def _ey_read(http: httpx.AsyncClient, *, keyword="", case_no="", number="", start="", end="", page=1) -> dict:
    fields = [
        ("PageNo", page), ("Name", ""), ("Reason", ""), ("Type", ""), ("No", number), ("CaseNo", case_no),
        ("StartDateString", start), ("EndDateString", end),
        ("Keyword", keyword),  # 實際篩選的是這個欄位；進階條件清單至少要有一筆，否則站方回 500
        ("MultiKeyword[0].ConditionType", "and"), ("pageSize", 20),
    ]
    r = await http.post(
        f"{EY_BASE}/Search/Search01/Read", content=urlencode(fields),
        headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                 "X-Requested-With": "XMLHttpRequest"},
    )
    r.raise_for_status()
    return r.json()


async def _ey_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    number = doc_number.strip()
    data = await _ey_read(
        http, keyword=keyword,
        case_no=number if _EY_CASE_NO.match(number) else "",
        number="" if _EY_CASE_NO.match(number) else re.sub(r"\D", "", number),
        start=_roc_slash(year_from, False), end=_roc_slash(year_to, True), page=page,
    )
    rows = data.get("Data") or []
    # 108 年以前收辦的案件（id 為純數字、全文為 HTML）連列表的案由都沒有遮蔽訴願人姓名，暫不列出
    kept = [x for x in rows if _EY_CASE_NO.match(x.get("DCS_ID", ""))]
    total = int(data.get("Total") or 0)
    group = {
        "source": "行政院訴願審議委員會", "category": "訴願決定", "total": total,
        "has_more": page * 20 < total,
        "items": [{
            "id": f"ey:{x['DCS_ID']}", "agency": "行政院", "category": "訴願決定",
            "doc_number": x["DCS_ID"], "date": _roc_to_iso(x.get("DCS_DATE", "")),
            "summary": (x.get("DCS_MASKEDSHORTREASON") or "").strip(),
        } for x in kept],
    }
    if len(kept) < len(rows):
        group["note"] = (f"本頁另有 {len(rows) - len(kept)} 件 108 年以前收辦的案件未列出"
                         "（官網未遮蔽當事人姓名）；需要時請至 appeal.ey.gov.tw 查閱。")
    return group


async def _ey_get(http, case_no: str) -> dict:
    if not _EY_CASE_NO.match(case_no):
        raise LookupError(case_no)
    data = await _ey_read(http, case_no=case_no)
    row = next((x for x in data.get("Data") or [] if x.get("DCS_ID") == case_no), None)
    if row is None or not row.get("DCS_FILEID"):
        raise LookupError(case_no)
    pdf_url = f"{EY_BASE}/File/Decision/{row['DCS_FILEID']}"
    r = await http.get(pdf_url)
    r.raise_for_status()
    text = _ey_layout(await asyncio.to_thread(pdf_to_text, r.content))  # 大檔解析不卡住其他查詢
    number = re.search(r"院臺訴字第\s*\d+\s*號", text)
    return {
        "agency": "行政院", "category": "訴願決定", "case_no": case_no,
        "doc_number": re.sub(r"\s+", "", number.group()) if number else "",
        "date": _roc_to_iso(row.get("DCS_DATE", "")),
        "summary": (row.get("DCS_MASKEDSHORTREASON") or "").strip(),
        "full_text": text,
        "notes": "" if text else "PDF 無法擷取文字，請開 pdf_url 閱讀。",
        "pdf_url": pdf_url,
        "source_url": f"{EY_BASE}/Search/Search01",
    }


# ─────────────────────────────────────────────────────────────
# 公平交易委員會處分書（www.ftc.gov.tw）：ASP.NET postback，每頁 10 筆
# ─────────────────────────────────────────────────────────────

_ftc_state: dict[str, str] | None = None
_ftc_state_at = 0.0
_FTC_STATE_TTL = 3600.0


async def _ftc_hidden_state(http: httpx.AsyncClient) -> dict[str, str]:
    """查詢表單的 __VIEWSTATE 等隱藏欄位（不綁 session，可重複使用一段時間）。"""
    global _ftc_state, _ftc_state_at
    if _ftc_state is None or time.time() - _ftc_state_at > _FTC_STATE_TTL:
        r = await http.get(FTC_LIST_URL)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        _ftc_state = {
            i["name"]: i.get("value", "")
            for i in soup.select("input[type=hidden]") if i.get("name", "").startswith("__")
        }
        _ftc_state_at = time.time()
    return _ftc_state


async def _ftc_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    start = f"{year_from + 1911}/01/01" if year_from else ""
    end = f"{year_to + 1911}/12/31" if year_to else ""
    form = dict(await _ftc_hidden_state(http))
    form.update({
        # 第 1 頁要觸發查詢鈕；翻頁要觸發頁碼選單（頁碼 1 觸發頁碼選單不會生效）
        "__EVENTTARGET": _FTC + ("searchButton" if page == 1 else "dl_toPage"),
        _FTC + "PdfKeyWords": doc_number.strip() or keyword,  # 全文比對；空白會被當成詞組的一部分
        _FTC + "CaseKindID": "", _FTC + "LawID": "", _FTC + "lawList": "",
        _FTC + "FormalDocDateStart": start, _FTC + "FormalDocDateEnd": end,
        _FTC + "HiddenFormalDocDateStart": start, _FTC + "HiddenFormalDocDateEnd": end,
        _FTC + "dl_toPage": str(page),
    })
    r = await http.post(FTC_LIST_URL, data=form)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    total_el = soup.select_one("#ContentPlaceHolder1_lb_totalRows")
    if total_el is None:
        raise ValueError("公平會查詢頁格式不符（找不到筆數欄位）")
    items = []
    for ul in soup.select("ul.result-list"):
        ps = ul.select("li > p")
        a = ul.select_one("li.result-reason a[href]")
        if not a or "/uploadDecision/" not in a["href"]:
            continue
        items.append({
            "id": "ftc:" + a["href"].split("/uploadDecision/", 1)[1],
            "agency": "公平交易委員會", "category": ps[1].get_text(strip=True) if len(ps) > 1 else "處分書",
            "doc_number": "", "date": ps[0].get_text(strip=True).replace("/", "-") if ps else "",
            "summary": a.get_text(" ", strip=True),
            "related_laws": list(ps[2].stripped_strings) if len(ps) > 2 else [],
        })
    total = int(total_el.get_text(strip=True) or 0)
    group = {"source": "公平交易委員會", "category": "處分書及決議書", "total": total,
             "has_more": page * 10 < total, "items": items}
    if doc_number.strip() and keyword:
        group["note"] = "此來源以發文字號查詢，關鍵字未套用"
    return group


async def _ftc_get(http, name: str) -> dict:
    if "/" in name or not name.lower().endswith(".pdf"):
        raise LookupError(name)
    pdf_url = FTC_PDF_BASE + name
    r = await http.get(pdf_url)
    if r.status_code == 404:
        raise LookupError(name)
    r.raise_for_status()
    text = await asyncio.to_thread(pdf_to_text, r.content)
    number = re.search(r"公(?:處|結|釋)字第\s*\d+\s*號", text)
    return {
        "agency": "公平交易委員會", "category": "處分書",
        "doc_number": re.sub(r"\s+", "", number.group()) if number else "",
        "date": "", "summary": "",
        "full_text": text,
        "notes": "" if text else "此 PDF 無法擷取文字（多為 2008 年以前的舊檔），請開 pdf_url 閱讀。",
        "pdf_url": pdf_url,
        "source_url": FTC_LIST_URL,
    }


# ─────────────────────────────────────────────────────────────
# 對外介面
# ─────────────────────────────────────────────────────────────

SOURCES = {
    "ey": ("行政院訴願決定", ("訴願", "行政院", "訴願決定"), _ey_search, _ey_get),
    "ftc": ("公平交易委員會處分書", ("公平會", "公平交易委員會", "處分書"), _ftc_search, _ftc_get),
    **quasi_judicial.SOURCES,
    **appeals.SOURCES,
}
# 不指定來源時查這幾個。工程會申訴審議判斷沒有關鍵字檢索（總數會是整段期間的件數）、監察院（單次查詢可達數十秒）、
# 律師懲戒（需精確關鍵字）、各部會與地方政府訴願，都要指定才查
DEFAULT_SOURCES = ("ey", "ftc", "uflb", "csptc", "fsc_sanction")


def resolve_sources(source: str) -> list[str] | None:
    if not source.strip():
        return list(DEFAULT_SOURCES)
    keys = []
    for name in [n for n in re.split(r"[,，、\s]+", source.strip()) if n]:
        hit = [k for k, (label, aliases, *_) in SOURCES.items() if name in (k, label) or name in aliases]
        if not hit:
            return None
        keys += hit
    return list(dict.fromkeys(keys))


class AdminDecisionClient:
    def __init__(self, cache: CacheDB):
        self.cache = cache
        self.http = httpx.AsyncClient(timeout=60.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True)

    async def close(self):
        await self.http.aclose()

    async def search(self, keyword: str, source: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
        keys = resolve_sources(source)
        if keys is None:
            return error_response(f"不支援的來源「{source}」",
                                  supported_sources=[label for label, *_ in SOURCES.values()])
        params = {"tool": "admin_decisions", "keyword": keyword, "sources": keys, "year_from": year_from,
                  "year_to": min(year_to, date.today().year - 1911) if year_to else 0,
                  "doc_number": doc_number, "page": page}
        cached = await self.cache.get_search(params)
        if cached:
            return {**cached, "cached": True}

        async def run(key: str) -> dict:
            label, _, search, _ = SOURCES[key]
            try:
                return await search(self.http, keyword, year_from, params["year_to"], doc_number, page)
            except Exception as e:  # 單一來源掛掉不拖垮其他來源
                logger.warning("處分／訴願搜尋失敗 %s: %s", key, e, exc_info=not isinstance(e, httpx.HTTPError))
                return {"source": label, "error": f"{type(e).__name__}: {e}"}

        groups = await asyncio.gather(*(run(k) for k in keys))
        results = sorted((i for g in groups for i in g.get("items", [])), key=lambda i: i["date"], reverse=True)
        result = {
            "success": True, "keyword": keyword, "page": page,
            "categories": [{k: v for k, v in g.items() if k != "items"}
                           | ({"returned": len(g["items"])} if "items" in g else {}) for g in groups],
            "total_count": sum(g.get("total", 0) for g in groups),
            "results": results,
            "timestamp": datetime.now().isoformat(),
        }
        if not any("error" in g or g.get("partial") for g in groups):  # 有來源失敗就不快取，下次重試
            await self.cache.set_search(params, result)
        return result

    async def get(self, decision_id: str) -> dict:
        key, _, native_id = decision_id.partition(":")
        if key not in SOURCES or not native_id:
            return error_response(f"id 格式錯誤：「{decision_id}」，請使用 search_administrative_decisions 回傳的 id")
        cache_key = f"decision:{decision_id}"
        cached = await self.cache.get_judgment(cache_key)
        if cached:
            return {"success": True, "cached": True, **cached}
        label, _, _, get = SOURCES[key]
        try:
            data = await get(self.http, native_id)
        except LookupError:
            return error_response(f"{label}查無此件：{decision_id}")
        except (httpx.HTTPError, ValueError) as e:
            return error_response(f"{label}連線或解析失敗：{type(e).__name__}: {e}")
        full = data["full_text"]
        data = {"id": decision_id, "source": label, **data,
                "full_text": full[:MAX_FULL_TEXT], "full_text_truncated": len(full) > MAX_FULL_TEXT}
        if full:  # 掃描檔、尚未公開理由的案件不長期快取，日後可能取得全文
            await self.cache.set_judgment(cache_key, data, source="admin_decision")
        return {"success": True, "cached": False, **data}
