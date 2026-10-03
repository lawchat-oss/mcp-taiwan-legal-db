"""立法資料：立法院議案（含審查中的草案）、立法院公報、行政院公報的法規命令草案預告、JOIN 的法律草案預告

- 議案、公報：立法院議事暨公報資訊網（ppg.ly.gov.tw）前端呼叫的 JSON API，免 session。
  站方 robots.txt 不允許爬蟲：這裡只做使用者觸發的單次查詢，不批次抓取。
- 法規命令草案預告：行政院公報（gazette.nat.gov.tw）「公告及送達」類，標題含「預告」；翻頁靠 session。
- 法律草案預告：公共政策網路參與平臺（join.gov.tw）前端使用的公開查詢 API。
- 立法院法律系統「立法歷程」列出的公報頁 PDF（lis.ly.gov.tw/lgcgi/lypdftxt）。

紀錄 id 一律為「種類:原站識別碼」：bill:202110226160000、gazette:115/64/LCIDC01_1156401_00007、
draft:151088、join:<UUID>、lispdf:<十六進位>。
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date, datetime
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

from mcp_server.cache.db import CacheDB
from mcp_server.tools._errors import error_response
from mcp_server.tools.fint import USER_AGENT
from mcp_server.tools.legislative import _client as lis_client
from mcp_server.tools.pdf_text import pdf_to_text

logger = logging.getLogger(__name__)

PPG = "https://ppg.ly.gov.tw/ppg/"
GAZETTE = "https://gazette.nat.gov.tw/egFront/"
LIS_PDF = "https://lis.ly.gov.tw/lgcgi/lypdftxt?xdd!"
PAGE_SIZE = 20
MAX_PDF_BYTES = 60 * 1024 * 1024  # 一冊公報 PDF 可達 30 MB 以上

BILL_APIS = {"pending": "pending-bills-search", "all": "all-bills", "passed": "three-read-bills-search"}
_GAZETTE_PDF = re.compile(r"^\d{2,3}/\d{1,3}/[A-Za-z0-9_]{6,40}$")


def current_term(today: date | None = None) -> int:
    """立法委員屆別：第 11 屆自 2024-02-01 起，每屆 4 年。"""
    today = today or date.today()
    months = (today.year - 2024) * 12 + today.month - 2
    return 11 + max(months, 0) // 48


def _roc(s: str) -> str:
    m = re.search(r"(\d{2,3})\D(\d{1,2})\D(\d{1,2})", s or "")
    return f"{int(m.group(1)) + 1911:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def _strip_tags(s: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(s or "", "html.parser").get_text(" ")).strip()


async def _pdf_text(http: httpx.AsyncClient, url: str) -> tuple[str, bool]:
    """下載 PDF（有大小上限）並擷取文字；回傳 (文字, 是否因過大而略過)。"""
    async with http.stream("GET", url) as r:
        r.raise_for_status()
        chunks, size = [], 0
        async for chunk in r.aiter_bytes():
            size += len(chunk)
            if size > MAX_PDF_BYTES:
                return "", True
            chunks.append(chunk)
    return await asyncio.to_thread(pdf_to_text, b"".join(chunks)), False


# ─────────────────────────────────────────────────────────────
# 立法院議案
# ─────────────────────────────────────────────────────────────

async def _ppg(http: httpx.AsyncClient, api: str, params: dict) -> dict:
    r = await http.get(PPG + "api/v1/" + api, params=params)
    r.raise_for_status()
    return r.json()


async def search_bills(http: httpx.AsyncClient, keyword: str, status: str, term: int, page: int) -> dict:
    params = {"size": PAGE_SIZE, "page": page, "sortCode": "11", "keyword": keyword}
    # 屆期不連續：審查中的議案只看本屆，否則會混進上屆已失效的草案
    term = term or (current_term() if status == "pending" else 0)
    if term:
        params["term"] = term
    data = await _ppg(http, BILL_APIS[status], params)
    items = []
    for x in data.get("items") or []:
        pdf = next((a["link"].replace("\\", "/") for a in x.get("attachments") or [] if a.get("attachmentType") == "PDF"), "")
        items.append({
            "id": f"bill:{x['id']}", "title": x.get("title", ""), "proposer": x.get("content") or "",
            "date": _roc(x.get("content3") or ""), "session": x.get("content4") or "",
            "status": x.get("content5") or "", "pdf_url": pdf,
        })
    total = data.get("totalItems") or 0
    return {"kind": "bills", "status": status, "term": term or None, "total": total, "items": items,
            "has_more": page * PAGE_SIZE < total}


async def get_bill(http: httpx.AsyncClient, bill_no: str) -> dict:
    if not re.fullmatch(r"\d{10,20}", bill_no):
        raise LookupError(bill_no)
    url = f"{PPG}bills/{bill_no}/details"
    r = await http.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    head = soup.find(id="section-0")
    if head is None:
        raise LookupError(bill_no)
    def lines(el) -> list[str]:
        return el.get_text("\n", strip=True).split("\n") if el else []

    first = lines(head)
    pdf = soup.select_one("a[title^='關係文書PDF']")
    data = {
        "title": first[0] if first else "", "proposer": first[1] if len(first) > 1 else "",
        "status": first[2] if len(first) > 2 else "",
        "proposers": lines(soup.find(id="section-1"))[1:],
        "progress": [l for l in lines(soup.find(id="section-3"))[1:] if l != "相關公報"],
        "source_url": url, "full_text": "",
    }
    if pdf:
        data["pdf_url"] = pdf["href"].replace("\\", "/")
        text, too_big = await _pdf_text(http, data["pdf_url"])
        data["full_text"] = text
        if not text:
            data["note"] = "關係文書 PDF 過大或無法擷取文字，請開 pdf_url。" if too_big else "關係文書 PDF 無法擷取文字，請開 pdf_url。"
        else:
            data["note"] = "關係文書含案由、說明與條文對照表；對照表三欄（修正條文、現行條文、說明）擷取後會逐行交錯。"
    return data


# ─────────────────────────────────────────────────────────────
# 立法院公報（院會、委員會紀錄）
# ─────────────────────────────────────────────────────────────

async def search_gazette(http: httpx.AsyncClient, keyword: str, term: int, page: int) -> dict:
    params = {"size": PAGE_SIZE, "page": page, "sortCode": "01", "keyword": keyword}
    if term:
        params["term"] = term
    data = await _ppg(http, "publication", params)
    items = []
    for x in data.get("items") or []:
        pdf = next((a["link"].replace("\\", "/") for a in x.get("attachments") or [] if a.get("name") == "PDF"), "")
        m = re.search(r"/pdf/(\d{2,3}/\d{1,3}/[A-Za-z0-9_]+)\.pdf$", pdf)
        if not m:
            continue
        items.append({
            "id": f"gazette:{m.group(1)}", "title": x.get("title", ""),
            "date": _roc(x.get("content4") or x.get("content3") or ""),
            "summary": _strip_tags(x.get("content") or ""),
            "matches": _strip_tags(x.get("hitKeywords") or "")[:300],
            "pdf_url": pdf,
        })
    total = data.get("totalItems") or 0
    return {"kind": "gazette", "total": total, "items": items, "has_more": page * PAGE_SIZE < total}


async def get_gazette(http: httpx.AsyncClient, native: str) -> dict:
    if not _GAZETTE_PDF.fullmatch(native):
        raise LookupError(native)
    url = f"{PPG}PublicationOfficialGazettes/download/communique1/final/pdf/{native}.pdf"
    text, too_big = await _pdf_text(http, url)
    data = {"title": f"立法院公報 {native.rsplit('/', 1)[-1]}", "pdf_url": url, "source_url": url, "full_text": text}
    if not text:
        data["note"] = "公報 PDF 過大（超過 60 MB）或無法擷取文字，請開 pdf_url。" if too_big else "公報 PDF 無法擷取文字，請開 pdf_url。"
    return data


async def get_lis_pdf(native: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{20,80}", native):
        raise LookupError(native)
    url = LIS_PDF + native
    async with lis_client() as http:
        text, too_big = await _pdf_text(http, url)
    data = {"title": "立法院公報（立法歷程所列頁次）", "pdf_url": url, "source_url": url, "full_text": text}
    if not text:
        data["note"] = "PDF 無法擷取文字，請開 pdf_url。"
    return data


# ─────────────────────────────────────────────────────────────
# 行政院公報：法規命令訂定、修正草案預告
# ─────────────────────────────────────────────────────────────

async def search_drafts(keyword: str, page: int) -> dict:
    # 站方要求「關鍵字、欄位、邏輯」三組依序出現；httpx 會合併同名參數，所以自己組 query string
    terms = [("預告", "title"), ("草案", "title"), (keyword, "text")]
    params = [("action", "doQuery"), ("styleL", "3")]
    for word, scope in terms:
        params += [("keywords", word), ("fields", scope), ("logics", "AND")]
    async with httpx.AsyncClient(timeout=60.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True) as s:
        r = await s.get(GAZETTE + "advancedSearchResult.do?" + urlencode(params))
        if page > 1:  # 翻頁靠 session 記住查詢條件
            r = await s.get(GAZETTE + "advancedSearchResult.do", params={"action": "doChangePage", "pageNum": page})
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    m = re.search(r"共\s*([\d,]+)\s*筆資料", soup.get_text())
    items = []
    for block in soup.select("div.List_Item"):
        a = block.select_one('a[href^="detail.do?metaid="]')
        if not a:
            continue
        title = a.get("title") or a.get_text(" ", strip=True)
        agency, sep, subject = title.partition("：")
        d = re.search(r"\d{4}-\d{2}-\d{2}", block.get_text(" "))
        metaid = re.search(r"metaid=(\d+)", a["href"]).group(1)
        items.append({
            "id": f"draft:{metaid}",
            "title": subject if sep else title, "agency": re.sub(r"(令|函|公告)$", "", agency) if sep else "",
            "date": d.group() if d else "",
        })
    total = int(m.group(1).replace(",", "")) if m else len(items)
    return {"kind": "drafts", "total": total, "items": items, "has_more": page * 10 < total}


async def get_draft(http: httpx.AsyncClient, metaid: str) -> dict:
    if not metaid.isdigit():
        raise LookupError(metaid)
    url = f"{GAZETTE}detail.do?metaid={metaid}&log=detailLog"
    r = await http.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    pdf = re.search(r"/EG_FileManager/[^\"'\s&]+/Eg\.pdf", r.text)
    if not pdf:
        raise LookupError(metaid)
    fields = {dt.get_text(strip=True).rstrip("：:"): dd.get_text(" ", strip=True)
              for dt, dd in zip(soup.select("dl dt"), soup.select("dl dd"))}
    pdf_url = str(r.url.join(pdf.group()))
    body, _ = await _pdf_text(http, pdf_url)
    title = (soup.title.get_text(strip=True) if soup.title else "").removeprefix("行政院公報資訊網").strip(" -|")
    agency, _, subject = title.partition("：")
    number = re.search(r"[^\s：:日]{1,15}字第\s*\d+\s*號", body[:300])  # 字號緊接在發文日期「…日」之後
    return {
        "title": subject or title, "agency": re.sub(r"(令|函|公告)$", "", agency),
        "date": fields.get("出刊日期", ""), "doc_number": re.sub(r"\s", "", number.group()) if number else "",
        "category": fields.get("類型", ""), "comment_deadline": fields.get("表示意見截止日期", ""),
        "full_text": body, "pdf_url": pdf_url, "source_url": url,
        "note": "預告期間內可依公告所載方式向主管機關陳述意見；草案內容以公報 PDF 為準。",
    }


# ─────────────────────────────────────────────────────────────
# 對外介面
# ─────────────────────────────────────────────────────────────

# JOIN：補足行政院公報以外的「法律草案預告」；公開前端同一查詢 API
_JOIN = "https://join.gov.tw/"
_JOIN_ID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")


async def search_join(keyword: str, status: str, page: int) -> dict:
    from mcp_server.tools.agency_interpretations import _session
    data = {"page": page, "size": 20, "keyword": keyword, "organization": "", "period": "all",
            "searchType": "finish" if status == "closed" else "nonfinish", "year": "", "searchScope": "Law",
            "onlyDataTypeBasic": False, "queryMode": "", "enablePeriod": True}
    async with _session() as http:
        r = await http.get(_JOIN + "policies/")
        r.raise_for_status()
        r = await http.post(_JOIN + "policies/v2/data/list", params={"page": page, "size": 20, "sort": "publishDate,desc"},
                           json=data, headers={"X-Requested-With": "XMLHttpRequest", "Origin": _JOIN.rstrip("/"),
                                               "Referer": _JOIN + "policies/"})
        r.raise_for_status()
    data = r.json()
    if not data.get("success") or not isinstance(data.get("result"), list) or "totalResults" not in data:
        raise ValueError("JOIN 未回傳可確認的查詢結果")
    items = []
    for row in data["result"]:
        rid = row.get("policyUid", "")
        if not _JOIN_ID.fullmatch(rid) or row.get("dataType") != "Law":
            raise ValueError("JOIN 法律草案類別或識別碼格式已變更")
        items.append({"id": "join:" + rid, "title": row.get("policyTitle", ""),
                      "summary": row.get("policyAbstract", ""), "source_url": _JOIN + "policies/detail/" + rid})
    return {"kind": "join", "consultation_state": "已結束" if status == "closed" else "進行中",
            "total": data["totalResults"], "items": items, "has_more": page < data["totalPages"]}


async def get_join(http, native: str) -> dict:
    from mcp_server.tools.agency_interpretations import _html_text
    from urllib.parse import urljoin
    if not _JOIN_ID.fullmatch(native):
        raise LookupError(native)
    url = _JOIN + "policies/detail/" + native
    r = await http.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    body = soup.select_one(".shareMailBody")
    if body is None:
        raise LookupError(native)
    title = soup.select_one(".shareMailSubject")
    attachments = [{"title": _strip_tags(str(a)), "url": urljoin(_JOIN, a["href"])}
                   for a in soup.select('.policy-detail a[href^="/attachments/"][href*="/download/"]')]
    text = _html_text(str(body))
    result = {"title": _strip_tags(str(title)) if title else "法律草案預告", "full_text": text,
              "source_url": url, "attachments": attachments}
    pdf = next((a["url"] for a in attachments if a["url"].lower().endswith(".pdf") and
                any(word in a["title"] for word in ("草案", "對照表"))), "")
    if pdf:
        body_text, too_big = await _pdf_text(http, pdf)
        result.update(pdf_url=pdf, full_text=text + ("\n\n【草案附件】\n" + body_text if body_text else ""))
        result["note"] = ("草案附件超過 60 MB，請開 PDF" if too_big else
                          "草案附件無可擷取文字，請開 PDF" if not body_text else "對照表的欄位擷取後可能交錯，請對照 PDF")
    return result


class LegislativeRecordsClient:
    def __init__(self, cache: CacheDB):
        self.cache = cache
        self.http = lis_client()  # 立法院網站（ppg、lis）都要舊式 TLS 重新協商；行政院公報不受影響

    async def close(self):
        await self.http.aclose()

    async def search(self, keyword: str, kind: str, status: str, term: int, page: int) -> dict:
        params = {"tool": "legislative_records", "keyword": keyword, "kind": kind, "status": status,
                  "term": term, "page": page}
        cached = await self.cache.get_search(params)
        if cached:
            return {**cached, "cached": True}
        try:
            if kind == "bills":
                group = await search_bills(self.http, keyword, status, term, page)
            elif kind == "join":
                group = await search_join(keyword, status, page)
            elif kind == "gazette":
                group = await search_gazette(self.http, keyword, term, page)
            else:
                group = await search_drafts(keyword, page)
        except (httpx.HTTPError, ValueError) as e:
            return error_response(f"立法資料來源連線失敗：{type(e).__name__}: {e}")
        result = {"success": True, "keyword": keyword, "page": page, **group, "timestamp": datetime.now().isoformat()}
        await self.cache.set_search(params, result)
        return result

    async def get(self, record_id: str) -> dict:
        kind, _, native = record_id.partition(":")
        cache_key = f"legrec:{record_id}"
        cached = await self.cache.get_judgment(cache_key)
        if cached and not cached.get("full_text_truncated"):
            return {"success": True, "cached": True, **cached}
        try:
            if kind == "bill":
                data = await get_bill(self.http, native)
            elif kind == "gazette":
                data = await get_gazette(self.http, native)
            elif kind == "join":
                data = await get_join(self.http, native)
            elif kind == "draft":
                data = await get_draft(self.http, native)
            elif kind == "lispdf":
                data = await get_lis_pdf(native)
            else:
                raise LookupError(record_id)
        except LookupError:
            return error_response(f"查無此筆或 id 格式錯誤：「{record_id}」，請使用 search_legislative_records 回傳的 id")
        except (httpx.HTTPError, ValueError) as e:
            return error_response(f"立法資料來源連線失敗：{type(e).__name__}: {e}")
        full = data.get("full_text", "")
        data = {"id": record_id, **data, "full_text": full, "full_text_truncated": False}
        if full and kind != "bill":  # 議案的審議進度會變，不長期快取
            await self.cache.set_judgment(cache_key, data, source="legislative_records")
        return {"success": True, "cached": False, **data}
