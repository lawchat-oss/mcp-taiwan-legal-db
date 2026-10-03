"""各部會、地方政府訴願決定書查詢（行政院以外）

每個來源一組 search / get，格式同 admin_decisions.SOURCES；查詢時即時向官方網站取得，每次查詢最多數個請求，不批次抓取。
決定 id 一律為「來源代碼:原站識別碼」，例如 taichung:1140935、hualien:GL001665。

- 官網公開的就照原樣列出：法務部約 112 年以前、原民會的決定書標題含訴願人姓名（同行政院 108 年以前）。
- 臺北市的 robots.txt 不允許爬取決定書全文路徑：只做使用者觸發的單次查詢。
- 許多網站只能比對標題、或只給頁數不給筆數，差異寫在各來源回傳的 note。
- 部分網站要通過查詢檢查：文化部用瀏覽器開官網、擷取前端的 API 回應；勞動部用官網的語音驗證功能；
  內政部、衛福部以本機 OCR 辨識圖形驗證碼（需安裝 [captcha]）。每次查詢用新的工作階段，不保存登入狀態。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import math
import json
from datetime import datetime, timedelta, timezone
import re
import ssl
from dataclasses import dataclass
from functools import partial
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from mcp_server.tools import fint, tls, public_browser
from mcp_server.tools.agency_interpretations import _date, _html_text, _office_text, _paged, _text
from mcp_server.tools.pdf_text import pdf_to_text

USER_AGENT = fint.USER_AGENT
CATEGORY = "訴願決定"
MAX_FILES = 5

_NO_YEAR = "此來源無法依年度篩選，未套用年度條件"
_NO_DOC = "此來源無法依字號查詢，未套用字號條件"
_NO_COUNT = "站方只提供頁數，總筆數為估計值"
_TITLE_ONLY = "此來源只比對標題"
_PDF_FAIL = "PDF 無法擷取文字，請開 pdf_url 閱讀。"

# 文號前綴不含數字、年月日、書、號（避免把「中央銀行113年3月3日」「…訴願決定書」吃進去）
_DOC_NO = re.compile(r"[^\s\d年月日度書號，。、：:；（）()「」]{1,10}字第\s*[^\s，。、]{1,20}?\s*號")


# ─────────────────────────────────────────────────────────────
# 共用
# ─────────────────────────────────────────────────────────────

def _soup(r: httpx.Response) -> BeautifulSoup:
    return BeautifulSoup(r.text, "html.parser")


def _iso(s: str) -> str:
    d = _date(s or "")
    return d if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) else ""


def _number(doc_number: str) -> str:
    """「府授法訴字第1150124384號」→ 1150124384；只給號碼時原樣使用。"""
    m = re.search(r"第\s*(\d+)\s*號", doc_number) or re.search(r"\d+", doc_number)
    return m.group(m.lastindex or 0) if m else ""


def _doc_no(text: str) -> str:
    m = _DOC_NO.search(text or "")
    return re.sub(r"\s+", "", m.group()) if m else ""


def _cause(text: str) -> str:
    """決定書開頭「訴願人因違反建築法事件，不服…」→ 違反建築法事件"""
    m = re.search(r"因(\S{2,40}?事件)", text or "")
    return m.group(1) if m else ""


def _signed(text: str) -> str:
    """決定書末的「中華民國115年8月21日」→ 2026-08-21（取最後一個）；PDF 擷取的數字間可能夾空白。"""
    found = re.findall(r"中\s*華\s*民\s*國\s*([\d ]{2,6})\s*年\s*([\d ]{1,4})\s*月\s*([\d ]{1,4})\s*日", text or "")
    return _iso("/".join(g.replace(" ", "") for g in found[-1])) if found else ""


def _b64(v: str) -> str:
    try:
        return base64.b64decode(v + "=" * (-len(v) % 4)).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return ""


def _item(native_id: str, doc_number: str, date: str, summary: str) -> dict:
    return {"id": native_id, "doc_number": doc_number, "date": date, "summary": summary}


def _page(total: int, items: list, has_more: bool, *notes: str) -> dict:
    note = "；".join(dict.fromkeys(n for n in notes if n))
    return {"total": total, "items": items, "has_more": has_more, **({"note": note} if note else {})}


def _estimate(page: int, last: int, size: int, n: int) -> int:
    """只給頁數的站：在最後一頁可算出確切筆數，否則以滿頁估計。"""
    return (last - 1) * size + n if page == last else last * size


def _result(doc_number: str, date: str, summary: str, full_text: str, source_url: str,
            pdf_url: str = "", notes: str = "") -> dict:
    d = {"doc_number": doc_number, "date": date, "summary": summary, "full_text": full_text, "source_url": source_url}
    if pdf_url:
        d["pdf_url"] = pdf_url
    if notes or (pdf_url and not full_text):
        d["notes"] = notes or _PDF_FAIL
    return d


async def _pdf_text(http: httpx.AsyncClient, url: str) -> str:
    r = await http.get(url)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise ValueError("官網未回傳 PDF，可能是驗證頁或錯誤頁：" + url)
    return await asyncio.to_thread(pdf_to_text, r.content)  # 大檔解析不卡住其他查詢


def _form(soup: BeautifulSoup, selector: str = "form") -> dict:
    """ASP.NET WebForms 表單目前的欄位值：隱藏欄位、文字欄、下拉選單、已勾選的單選鈕。"""
    f = soup.select_one(selector)
    if f is None:
        raise ValueError("查詢頁格式不符（找不到表單）")
    d = {i["name"]: i.get("value", "") for i in f.find_all("input")
         if i.get("name") and i.get("type") not in ("submit", "button", "image", "radio", "checkbox")}
    for s in f.find_all("select"):
        o = s.find("option", selected=True) or s.find("option")
        if s.get("name"):
            d[s["name"]] = o.get("value", "") if o else ""
    for r in f.find_all("input", type="radio", checked=True):
        d[r["name"]] = r.get("value", "")
    return d


def _client(verify: ssl.SSLContext | bool = True, follow_redirects: bool = True) -> httpx.AsyncClient:
    """需要 cookie／ViewState session 或特殊 TLS 設定的來源，每次查詢用獨立 client。"""
    return httpx.AsyncClient(timeout=60.0, headers={"User-Agent": USER_AGENT},
                             follow_redirects=follow_redirects, verify=verify)


def _legacy_tls() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.set_ciphers("DEFAULT:@SECLEVEL=1")  # 嘉義縣伺服器的 DH 金鑰太短，OpenSSL 預設安全等級會拒絕
    return ctx


def _local(rows: list[dict], keyword: str, year_from: int, year_to: int, doc_number: str, page: int,
           *notes: str) -> dict:
    """全量（或最新一批）清單在本機比對：關鍵字以空白分隔、全部要出現；字號比對號碼。"""
    no = _number(doc_number)
    hits = [r for r in rows if all(t in r["doc_number"] + r["summary"] for t in keyword.split())
            and no in r["doc_number"] + r["summary"]]
    if year_from or year_to:
        lo, hi = year_from + 1911 if year_from else 0, year_to + 1911 if year_to else 9999
        hits = [r for r in hits if not r["date"] or lo <= int(r["date"][:4]) <= hi]
        if any(not r["date"] for r in hits):
            notes += (_NO_YEAR,)
    items, more = _paged(hits, page)
    return _page(len(hits), items, more, _TITLE_ONLY, *notes)


# ─────────────────────────────────────────────────────────────
# 臺中市政府（appeal.taichung.gov.tw）：GET 全文檢索；不給總筆數只給頁數
# ─────────────────────────────────────────────────────────────

TC_BASE = "https://appeal.taichung.gov.tw/Home/"


async def _tc_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    no = _number(doc_number)
    r = await http.get(TC_BASE + "FN0601", params={
        "case_no": no if len(no) == 7 else "", "decisionid": no if len(no) != 7 else "",  # 案號 7 碼、文號 10 碼
        "CaseKind": "", "keywordSearch": keyword, "EnableKeywords": "N", "case_decision_type": "",
        "decisiondate1": f"{year_from}/01/01" if year_from else "",
        "decisiondate2": f"{year_to}/12/31" if year_to else "",
        "PageIndex": page - 1, "IsSearched": "Y",
    })
    r.raise_for_status()
    soup = _soup(r)
    table = next((t for t in soup.select("table") if t.find("th", string="決定書文號")), None)
    if table is None:
        raise ValueError("臺中市訴願查詢頁格式不符（找不到結果表格）")
    items = []
    for tr in table.select("tbody tr"):
        td = [_text(x) for x in tr.find_all("td")]
        if len(td) >= 6:
            items.append(_item(td[1], td[3], _iso(td[4]), f"{td[2]}（{td[5]}）"))
    last = max([int(x.get_text()) for x in soup.select(".pager > *") if x.get_text().strip().isdigit()] or [1])
    return _page(_estimate(page, last, 20, len(items)), items, page < last, _NO_COUNT if page < last else "")


async def _tc_get(http, case_no: str) -> dict:
    url = f"{TC_BASE}FN0602?case_no={case_no}"
    r = await http.get(url)
    r.raise_for_status()
    soup = _soup(r)
    body = soup.select_one("div[headers=f]")
    text = _html_text(str(body)) if body else ""
    if not text:
        raise LookupError(case_no)
    meta = {re.sub(r"[\s：:]", "", _text(th)): _text(th.find_next_sibling("td")) for th in soup.select("table th")}
    return _result(_doc_no(text), _iso(meta.get("決定書日期", "")), meta.get("類型", ""), text, url)


# ─────────────────────────────────────────────────────────────
# 臺北市政府（laws.gov.taipei）：GET 全文檢索；結果依類別分面，一次只列一個類別
# ─────────────────────────────────────────────────────────────

TP_BASE = "https://laws.gov.taipei/Law/LawDecision/"


def _tp_rows(soup: BeautifulSoup) -> list[dict]:
    items = []
    for tr in soup.select("table.table-result tbody tr"):
        td = [_text(x) for x in tr.find_all("td")]
        if len(td) >= 5 and re.fullmatch(r"\d+-\d+", td[4]):
            items.append(_item(td[4], td[3], "", f"{td[1]}類，{td[2]}"))
    return items


async def _tp_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    params = {"SearchString.Keyword1": keyword, "SearchString.Operaton1": "AND"}
    notes = ["臺北市結果依業務類別分組列出，列表沒有日期與案由"]
    year = year_to or year_from
    if year:
        params["year"] = year
        if year_from and year_to and year_from != year_to:
            notes.append(f"臺北市只能指定單一年度，只查 {year} 年")
    else:
        notes.append("未指定年度時依文號排序，民國 90 年代的案件排在前面")
    case = re.fullmatch(r"(\d{1,4})-(\d{1,4})", doc_number)
    if case:
        params.update({"caseNo1": case.group(1), "caseNo2": case.group(2)})
    elif doc_number:
        params["decisionNo"] = _number(doc_number)
    r = await http.get(TP_BASE + "SearchResult", params={**params, "page": 1})
    r.raise_for_status()
    soup = _soup(r)
    facets = []
    for a in soup.select("ul.treemenu a.typeLink[data-id]"):
        m = re.search(r"\((\d+)\)", a.get_text())
        if m:
            facets.append((a["data-id"], int(m.group(1))))
    # 把各類別的頁依序串成一條：第 n 頁對應某類別的某一頁，各只要一個請求（第 1 頁就是預設列出的第一個類別）
    pages = [(cid, p) for cid, n in facets for p in range(1, math.ceil(n / 20) + 1)]
    if page > len(pages):
        items = []
    elif page == 1:
        items = _tp_rows(soup)
    else:
        cid, p = pages[page - 1]
        r = await http.get(TP_BASE + "SearchResult", params={**params, "curcateid": cid, "page": p})
        r.raise_for_status()
        items = _tp_rows(_soup(r))
    return _page(sum(n for _, n in facets), items, page < len(pages), *notes)


async def _tp_get(http, case_no: str) -> dict:
    url = f"{TP_BASE}LawDecisionSearchContent?caseNo={case_no}"
    r = await http.get(url)
    if r.status_code in (404, 500):  # 查無此案號時站方回 500
        raise LookupError(case_no)
    r.raise_for_status()
    law = _soup(r).select_one("article.col-article ul.law")
    body = law.select_one("span[title=訴願決定書內容]") if law else None
    if body is None:
        raise LookupError(case_no)
    title = _text(law.select_one("span.inline-title"))  # 臺北市政府 113.12.17 府訴三字第 1136086353 號訴願決定書
    text = body.get_text("\n", strip=True)
    when = re.search(r"\d{2,3}\.\d{1,2}\.\d{1,2}", title)
    return _result(_doc_no(title), _iso(when.group() if when else ""), _cause(text), text, url)


# ─────────────────────────────────────────────────────────────
# 新北市政府（web.law.ntpc.gov.tw）：GET 全文檢索，每頁 20 筆
# ─────────────────────────────────────────────────────────────

NTPC_BASE = "https://web.law.ntpc.gov.tw/Scripts/"


async def _ntpc_list(http, params: dict) -> tuple[int, list[tuple[dict, str]]]:
    r = await http.get(NTPC_BASE + "Su_list02.aspx",
                       params={"K1": "", "sdate": "00000000", "edate": "99991231", "page": 1, **params})
    r.raise_for_status()
    soup = _soup(r)
    m = re.search(r"查詢結果共計：(\d+)筆", re.sub(r"\s+", "", soup.get_text()))  # 查無資料時整段不出現
    rows = []
    for tr in soup.select("table.tab-News tr"):
        td = tr.find_all("td")
        if len(td) == 4 and td[2].a:
            q = parse_qs(urlparse(td[2].a["href"]).query)
            ecase, eno = q.get("ecase", [""])[0], q.get("eno", [""])[0]
            doc = f"{ecase}字第{eno}號" if eno.isdigit() else ""  # 早期案件 ecase/eno 為「（無）」
            rows.append((_item(_text(td[3]), doc, _iso(_text(td[1])), _text(td[2])),
                         urljoin(NTPC_BASE, td[2].a["href"])))
    return int(m.group(1)) if m else 0, rows


async def _ntpc_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    params = {"K1": keyword, "page": page,
              "sdate": f"{year_from + 1911}0101" if year_from else "00000000",
              "edate": f"{year_to + 1911}1231" if year_to else "99991231"}
    no = _number(doc_number)
    if no:
        params["N2" if "字" in doc_number else "EANO"] = no  # 發文字號的號碼／案號
    total, rows = await _ntpc_list(http, params)
    return _page(total, [i for i, _ in rows], page * 20 < total)


async def _ntpc_get(http, case_no: str) -> dict:
    _, rows = await _ntpc_list(http, {"EANO": case_no})
    hit = next((x for x in rows if x[0]["id"] == case_no), None)
    if hit is None:
        raise LookupError(case_no)
    item, url = hit
    r = await http.get(url)
    r.raise_for_status()
    soup = _soup(r)
    pre = soup.select_one("table.tab-su td.m-td pre")
    text = pre.get_text().strip() if pre else ""
    if not text:
        raise LookupError(case_no)
    meta = {re.sub(r"[\s：:]", "", _text(tr.th)): _text(tr.td) for tr in soup.select("table.tab-su tr") if tr.th and tr.td}
    return _result(re.sub(r"\s+", "", meta.get("發文字號", "")) or item["doc_number"],
                   _iso(meta.get("發文日期", "")) or item["date"], item["summary"], text, url)


# ─────────────────────────────────────────────────────────────
# 高雄市政府（law.kcg.gov.tw）：WebForms postback，每頁 10 筆，站方由舊到新排列
# ─────────────────────────────────────────────────────────────

KH_BASE = "https://law.kcg.gov.tw/"
_KH = "ctl00$ContentPlaceHolder1$"
_KH_MAX_PAGE = 11  # 只能翻到第 1 頁頁碼列上有的頁


def _kh_rows(soup: BeautifulSoup) -> list[dict]:
    items = []
    for tr in soup.select("#ctl00_ContentPlaceHolder1_GV tr[valign=top]"):
        td = [_text(x) for x in tr.find_all("td")]
        m = re.search(r"entry=(\d+)", tr.a["href"]) if tr.a else None
        if m and len(td) >= 5:
            items.append(_item(m.group(1), td[0], _iso(td[1]), f"{td[3]}（{td[4].rstrip('。')}）"))
    return items


async def _kh_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    async with _client() as s:  # 翻頁要沿用同一個 ViewState／session
        r = await s.get(KH_BASE + "plead3.aspx")
        r.raise_for_status()
        form = _form(_soup(r), "form#aspnetForm")
        form.update({
            _KH + "key1": keyword, _KH + ("lawno2" if "字" in doc_number else "lawno1"): _number(doc_number),
            # 預設只查最近一年
            _KH + "start_year": str(max(year_from, 95)), _KH + "start_month": "1", _KH + "start_day": "1",
            _KH + "Button1": "查詢",
        })
        if year_to:
            form.update({_KH + "end_year": str(year_to), _KH + "end_month": "12", _KH + "end_day": "31"})
        r = await s.post(KH_BASE + "plead3.aspx", data=form)
        r.raise_for_status()
        soup = _soup(r)
        m = re.search(r"搜尋結果:(\d+)筆", re.sub(r"\s+", "", soup.get_text()))
        total = int(m.group(1)) if m else 0
        last = math.ceil(total / 10)
        # 11 頁以內倒過來翻，讓第 1 頁是最新的；超過就只能照站方順序
        newest_first = last <= _KH_MAX_PAGE
        target = last - page + 1 if newest_first else page
        if not 1 <= target <= min(last, _KH_MAX_PAGE):
            items = []
        elif target == 1:
            items = _kh_rows(soup)
        else:
            form = _form(soup, "form#aspnetForm") | {"__EVENTTARGET": _KH + "GV", "__EVENTARGUMENT": f"Page${target}"}
            r = await s.post(KH_BASE + "plead3.aspx", data=form)
            r.raise_for_status()
            items = _kh_rows(_soup(r))
    if newest_first:
        items.reverse()
    note = "" if newest_first else "高雄市結果超過 110 筆時由舊到新排列、且只能翻到第 11 頁，請加年度或關鍵字縮小範圍"
    return _page(total, items, page < (last if newest_first else min(last, _KH_MAX_PAGE)), note)


async def _kh_get(http, entry: str) -> dict:
    url = f"{KH_BASE}plead31.aspx?entry={entry}"
    async with _client(follow_redirects=False) as s:
        await s.get(KH_BASE + "index.aspx")  # 沒先進首頁，決定書頁會被導回首頁
        r = await s.get(url)
        r.raise_for_status()
    table = next((t for t in _soup(r).select("table.w3-table") if t.find("th", string="案號")), None)
    meta = {_text(tr.th): tr.td for tr in table.find_all("tr") if tr.th and tr.td} if table else {}
    if not _text(meta.get("案號")):
        raise LookupError(entry)
    text = _html_text(str(table.find_all("tr")[-1].td))
    doc = re.sub(r"^(.+?字第)\1", r"\1", _text(meta.get("決定書文號"))).replace("號號", "號")  # 站方偶爾重複字頭
    return _result(doc, _iso(_text(meta.get("決定日期"))), _text(meta.get("訴願標題")) or _cause(text), text, url)


# ─────────────────────────────────────────────────────────────
# 彰化縣政府（www.chcg.gov.tw）：列表只有案號，關鍵字比對站方內部摘要；全文只有 PDF
# ─────────────────────────────────────────────────────────────

CH_URL = "https://www.chcg.gov.tw/DTO/general/06service/service04.aspx"
CH_FILE = "https://www.chcg.gov.tw/DTO/general/getFile.aspx"


async def _ch_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    r = await http.get(CH_URL, params={"txtQKeyword": keyword, "page": page})
    r.raise_for_status()
    soup = _soup(r)
    items = []
    for a in soup.select("a[href*='getFile.aspx']"):
        m = re.search(r"file_id=(\d+)", a["href"])
        if m:  # 115年訴願會訴願決定書─821（365.00KB）
            items.append(_item(m.group(1), "", "", re.sub(r"[（(][\d.]+\s*[KMG]?B[)）]$", "", _text(a))))
    last = max([int(x) for a in soup.select("a[href*='page=']") for x in re.findall(r"[?&]page=(\d+)", a["href"])] or [1])
    return _page(_estimate(page, last, 10, len(items)), items, page < last,
                 "彰化縣列表只有案號，案由與日期需以 get 取得全文", _NO_COUNT if page < last else "",
                 _NO_YEAR if year_from or year_to else "", _NO_DOC if doc_number else "")


async def _ch_get(http, file_id: str) -> dict:
    url = f"{CH_FILE}?file_id={file_id}&file=1&type=4&did={file_id}"
    r = await http.get(url)
    r.raise_for_status()
    if r.content[:4] != b"%PDF":
        raise LookupError(file_id)
    text = await asyncio.to_thread(pdf_to_text, r.content)
    return _result(_doc_no(text), _signed(text), _cause(text), text, CH_URL, pdf_url=url)


# ─────────────────────────────────────────────────────────────
# 國防部（law.mnd.gov.tw 國防法規資料庫）：GET 全文檢索，每頁 20 筆
# ─────────────────────────────────────────────────────────────

MND_BASE = "https://law.mnd.gov.tw/"


async def _mnd_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    r = await http.get(MND_BASE + "BookRst.aspx", params={
        "K1": keyword, "K2": "", "K3": "", "K4": "", "N1": doc_number,
        "Y1": year_from or "", "M1": "", "D1": "", "Y2": year_to or "", "M2": "", "D2": "", "RowNo": page,
    })
    r.raise_for_status()
    soup = _soup(r)
    items = []
    for tr in soup.select("table.tnormal tr"):
        td, a = tr.find_all("td"), tr.select_one("a[href*='BookDetail.aspx']")
        m = re.search(r"id=(\d+)", a["href"]) if a else None
        if m and len(td) >= 3:  # 列表只有日期與字號
            items.append(_item(m.group(1), _text(a), _iso(_text(td[1])), ""))
    count = _text(soup.select_one("span.msg"))  # 查無資料時是「查無資料」
    total = int(count) if count.isdigit() else 0
    return _page(total, items, page * 20 < total)


async def _mnd_get(http, doc_id: str) -> dict:
    url = f"{MND_BASE}BookDetail.aspx?id={doc_id}"
    r = await http.get(url)
    r.raise_for_status()
    soup = _soup(r)
    pre = soup.select_one("span.text-pre")
    text = pre.get_text().strip() if pre else ""
    if not text:
        raise LookupError(doc_id)
    meta = {re.sub(r"[\s：:]", "", _text(tr.th)): _text(tr.td) for tr in soup.select("table.tab-data tr") if tr.th and tr.td}
    return _result(meta.get("發文字號", ""), _iso(meta.get("發文日期", "")), _cause(text), text, url)


# ─────────────────────────────────────────────────────────────
# 退輔會（www.vac.gov.tw）：POST 全文檢索，所有結果一頁回完
# ─────────────────────────────────────────────────────────────

VAC_BASE = "https://www.vac.gov.tw/"


async def _vac_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    form = {"TBOXTitle": "", "TBOXAppealerName": "", "TBOXDecisionCaseNumber": _number(doc_number),
            "TBOXDecisionContent": keyword,
            "TBOXDecisionDateFrom": f"{year_from + 1911}/01/01" if year_from else "",
            "TBOXDecisionDateTo": f"{year_to + 1911}/12/31" if year_to else ""}
    if not any(form.values()):
        form["TBOXDecisionDateFrom"] = "1990/01/01"  # 至少要一個條件；全部約 700 筆
    r = await http.post(VAC_BASE + "sp-appeal-CDQS-1.html", data=form)
    r.raise_for_status()
    rows = []
    for tr in _soup(r).select("tr"):
        a, td = tr.select_one("a[href*='CDQC']"), tr.find_all("td")
        m = re.search(r"ID=(\d+)&CheckStr=([0-9A-Fa-f]{32})", a["href"]) if a else None
        if m and len(td) >= 3:
            rows.append(_item(f"{m.group(1)}-{m.group(2)}", "", _iso(_text(td[2])), _text(a)))
    items, more = _paged(rows, page)
    return _page(len(rows), items, more)


async def _vac_get(http, native_id: str) -> dict:
    doc_id, check = native_id.split("-")
    url = f"{VAC_BASE}sp-appeal-CDQC-1.html?ID={doc_id}&CheckStr={check}"  # 沒有 CheckStr 會被導走
    r = await http.get(url)
    r.raise_for_status()
    meta = {}
    for g in _soup(r).select("#FormContent .form-group"):
        label, value = g.select_one("label"), g.select_one("div")
        if label and value:
            meta[re.sub(r"\s+", "", label.get_text())] = value
    if "內文" not in meta:
        raise LookupError(native_id)
    text = _html_text(str(meta["內文"]))
    return _result(_text(meta.get("決定書文號")), _signed(text), _cause(text), text, url)


# ─────────────────────────────────────────────────────────────
# 中央銀行（www.law.cbc.gov.tw）：GET 全文檢索，每頁 10 筆；全部約 10 餘筆
# ─────────────────────────────────────────────────────────────

CBC_BASE = "https://www.law.cbc.gov.tw/DOrder/"


async def _cbc_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    r = await http.get(CBC_BASE + "SearchAgain", params={
        "criteria.keyWord1": keyword, "criteria.keyWord2": "", "criteria.keyWord3": "", "criteria.keyWord4": "",
        "criteria.starDate": f"{year_from:03d}0101" if year_from else "",
        "criteria.endDate": f"{year_to:03d}1231" if year_to else "",
        "criteria.number": _number(doc_number), "criteria.pageNumber": page,
    })
    r.raise_for_status()
    soup = _soup(r)
    items = []
    for a in soup.select("a[href*='doid=']"):
        m = re.search(r"doid=(\d+)", a["href"])
        cause, _, rest = _text(a.find_parent("td") or a).partition(" ")  # 人事懲處事件 中央銀行113年3月3日台央法字第…號訴願決定書
        if m:
            items.append(_item(m.group(1), _doc_no(rest), _iso(rest), cause))
    m = re.search(r"共有\s*(\d+)\s*頁", soup.get_text())
    last = int(m.group(1)) if m else 1
    return _page(_estimate(page, last, 10, len(items)), items, page < last, _NO_COUNT if page < last else "")


async def _cbc_get(http, doid: str) -> dict:
    url = f"{CBC_BASE}DOrder?doid={doid}"
    r = await http.get(url)
    r.raise_for_status()
    soup = _soup(r)
    head = {}
    for p in soup.select("p.title-subject"):
        k, _, v = _text(p).partition("：")
        head[k] = v.strip()
    body = soup.select_one("div.letters-desc-text")
    if body is None or not head.get("發文字號"):  # 查無此號時仍回空白模板
        raise LookupError(doid)
    text = _html_text(str(body))
    return _result(_doc_no(head.get("發文字號", "")), _iso(head.get("發文字號", "")), head.get("要旨", ""), text, url)


# ─────────────────────────────────────────────────────────────
# 法務部（www.moj.gov.tw）：GET 標題查詢，每頁 20 筆；全文只有 PDF
# ─────────────────────────────────────────────────────────────

MOJ_LIST = "https://www.moj.gov.tw/2204/2645/2686/Lpsimplelist"


async def _moj_rows(http, query: str, page: int) -> tuple[int, list[tuple[dict, str, bool]]]:
    r = await http.get(MOJ_LIST, params={"q_Name": query, "Page": page, "PageSize": 20})
    r.raise_for_status()
    soup = _soup(r)
    m = re.search(r"共\s*(\d+)\s*筆資料", soup.get_text())
    rows = []
    for tr in soup.select("tr"):
        a = tr.select_one("td[data-title=標題] a[href*='/media/']")
        head = re.match(r"(\S*?字第\s*(\d+)\s*號)[-_\s]*(.*)", _text(a)) if a else None
        if not head:
            continue
        # 標題：法訴字第11513521450號-趙○○因申請…事件(1150824)；括號內是決定日期
        rest = head.group(3)
        when = re.search(r"[（(](\d{3})(\d{2})(\d{2})[)）]\s*$", rest)
        summary = re.sub(r"[（(]\d{7}[)）]\s*$", "", rest).strip("-_ ")
        date = _iso("/".join(when.groups())) if when else _iso(_text(tr.select_one("td[data-title*=日期]")))
        item = _item(head.group(2), re.sub(r"\s+", "", head.group(1)), date, summary)
        rows.append((item, urljoin(MOJ_LIST, a["href"])))
    return int(m.group(1)) if m else 0, rows


async def _moj_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    no = _number(doc_number)
    total, rows = await _moj_rows(http, no or keyword, page)
    notes = [_TITLE_ONLY, _NO_YEAR if year_from or year_to else "", "已改以字號查詢，關鍵字未套用" if no and keyword else ""]
    return _page(total, [i for i, _ in rows], page * 20 < total, *notes)  # 約 112 年以前的標題未遮蔽姓名（官網原樣）


async def _moj_get(http, number: str) -> dict:
    _, rows = await _moj_rows(http, number, 1)
    hit = next(((i, url) for i, url in rows if i["id"] == number), None)
    if hit is None:
        raise LookupError(number)
    item, pdf_url = hit
    text = await _pdf_text(http, pdf_url)
    return _result(item["doc_number"], item["date"], item["summary"], text, MOJ_LIST, pdf_url=pdf_url)


# ─────────────────────────────────────────────────────────────
# 金管會（www.fsc.gov.tw）：GET 全文檢索，HTML 全文
# ─────────────────────────────────────────────────────────────

FSC_BASE = "https://www.fsc.gov.tw/ch/home.jsp"
_FSC = {"id": "809", "parentpath": "0,7"}


async def _fsc_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    no = _number(doc_number)
    params = {**_FSC, "mcustomize": "appeal_list.jsp", "keyword": keyword, "appealnumber": "", "subject": "",
              "page": page, "pagesize": 20}
    if no:  # 8 碼是案號；決定書字號的號碼只能用全文檢索找
        params["appealnumber" if len(no) == 8 else "keyword"] = no
    r = await http.get(FSC_BASE, params=params)
    r.raise_for_status()
    items = []
    for li in _soup(r).select("div.newslist li[role=row]"):
        a = li.select_one("a[href*='dataserno=']")
        m = re.search(r"dataserno=(\d+)", a["href"]) if a else None
        if m:
            items.append(_item(m.group(1), _text(li.select_one("span.unit")), _iso(_text(li.select_one("span.date"))),
                               _text(li.select_one("span.title"))))
    m = re.search(r'共有\s*<span class="red">(\d+)', r.text)  # 查無資料時不出現
    total = int(m.group(1)) if m else 0
    return _page(total, items, page * 20 < total, _NO_YEAR if year_from or year_to else "",
                 "已改以字號查詢，關鍵字未套用" if no and keyword and len(no) != 8 else "")


async def _fsc_get(http, serno: str) -> dict:
    url = f"{FSC_BASE}?{urlencode({**_FSC, 'mcustomize': 'appeal_view.jsp', 'dataserno': serno})}"
    r = await http.get(url)
    r.raise_for_status()
    main = _soup(r).select_one("div.maincontent")
    edit = main.select_one("div.page-edit") if main else None
    if edit is None or not _text(edit):
        raise LookupError(serno)
    subject, when = _text(main.select_one("div.subject")), _text(main.select_one("div.date"))
    for x in main.select("div.subject, div.date"):
        x.decompose()
    text = re.sub(r"^發布單位：.*\n", "", _html_text(str(main)))
    return _result(_doc_no(text), _iso(when), subject, text, url)


# ─────────────────────────────────────────────────────────────
# 交通部（nseweb.motc.gov.tw）：WebForms postback（session 綁定），每頁 10 筆；列表沒有日期
# ─────────────────────────────────────────────────────────────

MOTC_URL = "https://nseweb.motc.gov.tw/NSEWEB/WebSite/Sys/Func01"
_MOTC = "ctl00$ctl00$MainContent$MainContent$"


def _motc_tls() -> ssl.SSLContext:
    return tls.context_with(tls.TWCA_SECURE_SSL_CA)


async def _motc_query(s: httpx.AsyncClient, keyword: str = "", number: str = "",
                      year_from: int = 0, year_to: int = 0) -> BeautifulSoup:
    r = await s.get(MOTC_URL)
    r.raise_for_status()
    form = _form(_soup(r)) | {
        _MOTC + "dwQ_m13_text1": keyword, _MOTC + "dwQ_m13_book_send_no": number,
        _MOTC + "u_date_s$tbx_Date": f"{year_from:03d}/01/01" if year_from > 92 else "092/07/01",  # 資料起日
        _MOTC + "dwF_sort": "DESC", _MOTC + "bt_Query": "查詢",
    }
    if year_to:
        form[_MOTC + "u_date_e$tbx_Date"] = f"{year_to:03d}/12/31"
    r = await s.post(MOTC_URL, data=form)
    r.raise_for_status()
    return _soup(r)


def _motc_rows(soup: BeautifulSoup) -> list[tuple[dict, str]]:
    rows = []
    for tr in soup.select("table#MainContent_MainContent_dgG tr"):
        no, head = tr.select_one("[id*='dwG_m13_book_send_no_']"), tr.select_one("a[id*='dwG_m13_header_']")
        target = re.search(r"__doPostBack\('([^']+)'", head.get("href", "")) if head else None
        if no and _text(no).isdigit() and target:
            rows.append((_item(_text(no), "", "", _text(head)), target.group(1)))
    return rows


async def _motc_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    async with _client(verify=_motc_tls()) as s:
        soup = await _motc_query(s, keyword, _number(doc_number), year_from, year_to)
        m = re.search(r"共(\d+)筆資料", soup.get_text())
        total = int(m.group(1)) if m else 0
        if page > 1 and (page - 1) * 10 < total:
            form = _form(soup) | {"__EVENTTARGET": _MOTC + "dgG$ctl01$dgG_PageNum", "__EVENTARGUMENT": "",
                                  _MOTC + "dgG$ctl01$dgG_PageNum": str(page)}
            r = await s.post(MOTC_URL, data=form)
            r.raise_for_status()
            soup = _soup(r)
    items = [i for i, _ in _motc_rows(soup)] if (page - 1) * 10 < total else []
    return _page(total, items, page * 10 < total, "交通部列表沒有日期")


async def _motc_get(http, number: str) -> dict:
    async with _client(verify=_motc_tls()) as s:
        soup = await _motc_query(s, number=number)
        target = next((t for i, t in _motc_rows(soup) if i["id"] == number), None)
        if target is None:
            raise LookupError(number)
        r = await s.post(MOTC_URL, data=_form(soup) | {"__EVENTTARGET": target, "__EVENTARGUMENT": ""})
        r.raise_for_status()
    doc = _soup(r).select_one("#div_print")
    if doc is None:
        raise ValueError("交通部決定書頁格式不符")

    def field(name: str) -> str:
        return _text(doc.select_one(f"#MainContent_MainContent_dwF_dwF_{name}"))

    text = _html_text(str(doc))
    return _result(f"{field('m13_book_send_word')}字第{number}號", _iso(field("m13_book_send_date")),
                   field("m13_header"), text, MOTC_URL)


# ─────────────────────────────────────────────────────────────
# 清單很短、在本機比對標題的來源：國科會、數位部、工程會、外交部（全文都是 PDF）
# ─────────────────────────────────────────────────────────────

NSTC_BASE = "https://www.nstc.gov.tw"
NSTC_LIST = NSTC_BASE + "/law/ch/list/120f2d63-fdac-4d36-a6e6-19bac8bc430d"


async def _nstc_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    r = await http.get(NSTC_LIST, params={"pageNum": 1, "pageSize": 200, "view_mode": "listView"})  # 全部不到百筆
    r.raise_for_status()
    rows = []
    for li in _soup(r).select("ul.list_list_dow li"):
        # 多數直接是附件（表單 action），少數是內容頁連結；id 取其 UUID
        link = li.select_one("form[action*='/nstc/attachments/']") or li.select_one("a[href*='/law/ch/detail/']")
        if link:
            title = _text(li.select_one("div.box_450") or link)  # 科會訴字第1150025407號訴願案決定書
            rows.append(_item((link.get("action") or link["href"]).rstrip("/").rsplit("/", 1)[-1], _doc_no(title), "", title))
    return _local(rows, keyword, year_from, year_to, doc_number, page)


async def _nstc_get(http, uuid: str) -> dict:
    pdf_url = f"{NSTC_BASE}/nstc/attachments/{uuid}"
    r = await http.get(pdf_url)
    if r.content[:4] != b"%PDF":  # 不是附件就是內容頁，附件連結在頁裡
        page_url = f"{NSTC_BASE}/law/ch/detail/{uuid}"
        page = await http.get(page_url)
        a = _soup(page).select_one("a[href*='/nstc/attachments/']") if page.status_code == 200 else None
        if a is None:
            raise LookupError(uuid)
        pdf_url = urljoin(page_url, a["href"])
        r = await http.get(pdf_url)
        r.raise_for_status()
    text = await asyncio.to_thread(pdf_to_text, r.content)
    return _result(_doc_no(text[:100]), _signed(text), _cause(text), text, NSTC_LIST, pdf_url=pdf_url)


MODA_LIST = "https://moda.gov.tw/information-service/govinfo/administrative-appeal/1235"


async def _moda_rows(http) -> list[tuple[dict, str]]:
    r = await http.get(MODA_LIST)
    r.raise_for_status()
    rows = []
    for a in _soup(r).select("ul.list4-2 li a[href*='/File/Get/']"):
        for x in a.select("span.fileTypeI"):
            x.decompose()
        # 114年1月14日 113-002 黃O凱以113年9月16日訴願書提起訴願
        m = re.match(r"(\d+年\d+月\d+日)\s*(\S+)\s*(.*)", _text(a.select_one("b") or a))
        if m:
            fid = a["href"].rstrip("/").rsplit("/", 1)[-1]
            rows.append((_item(fid, m.group(2), _iso(m.group(1)), m.group(3)), a["href"]))
    return rows


async def _list_get(http, rows: list[tuple[dict, str]], fid: str, source_url: str) -> dict:
    """短清單來源：從清單找到該筆的 PDF 連結再下載。"""
    hit = next(((i, u) for i, u in rows if i["id"] == fid), None)
    if hit is None:
        raise LookupError(fid)
    item, pdf_url = hit
    text = await _pdf_text(http, pdf_url)
    summary = item["summary"] if item["summary"] != item["doc_number"] else _cause(text)  # 標題只有字號時改用案由
    return _result(item["doc_number"], item["date"] or _signed(text), summary, text, source_url, pdf_url=pdf_url)


async def _moda_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    return _local([i for i, _ in await _moda_rows(http)], keyword, year_from, year_to, doc_number, page)


async def _moda_get(http, fid: str) -> dict:
    return await _list_get(http, await _moda_rows(http), fid, MODA_LIST)


PCC_LIST = "https://www.pcc.gov.tw/content/index?eid=10166&type=C"


async def _pcc_rows(http) -> list[tuple[dict, str]]:
    r = await http.get(PCC_LIST)
    r.raise_for_status()
    rows = []
    for a in _soup(r).select("a[href*='downloadFile']"):
        q = parse_qs(urlparse(a["href"]).query)
        fid = re.fullmatch(r"article/(\d+)\.pdf", _b64(q.get("fpath", [""])[0]))
        # 115.01.15工程願字第1140026767號-訴願人因工程施工查核扣點事件.pdf
        m = re.match(r"(\d+\.\d+\.\d+)\s*(\S*?字第\d+號)[-－\s]*(.*?)(?:\.pdf)?$", q.get("sname", [""])[0])
        if fid and m:
            rows.append((_item(fid.group(1), m.group(2), _iso(m.group(1)), m.group(3)), urljoin(PCC_LIST, a["href"])))
    return rows


async def _pcc_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    return _local([i for i, _ in await _pcc_rows(http)], keyword, year_from, year_to, doc_number, page)


async def _pcc_get(http, fid: str) -> dict:
    return await _list_get(http, await _pcc_rows(http), fid, PCC_LIST)


# 外交部是 Rhythm CMS，但列表直接連到附件，舊案的附件序號又不是文章序號，只能從清單找（全部十餘筆）
MOFA_LIST = "https://www.mofa.gov.tw/News.aspx?n=1013&sms=229&page=1&PageSize=200"


async def _mofa_rows(http) -> list[tuple[dict, str]]:
    r = await http.get(MOFA_LIST)
    r.raise_for_status()
    rows = []
    for a in _soup(r).select("#CCMS_Content table tbody tr a[href*='Download.ashx']"):
        sn, title = _r5_sn(a["href"]), _text(a)  # 外訴願字第115001號
        if sn:
            rows.append((_item(sn, _doc_no(title), "", title), a["href"]))
    return rows


async def _mofa_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    return _local([i for i, _ in await _mofa_rows(http)], keyword, year_from, year_to, doc_number, page)


async def _mofa_get(http, sn: str) -> dict:
    return await _list_get(http, await _mofa_rows(http), sn, MOFA_LIST)


# ─────────────────────────────────────────────────────────────
# GLRS 主管法規共用系統（花蓮縣、金門縣）：GET 全文檢索，HTML 全文
# ─────────────────────────────────────────────────────────────

async def _glrs_search(base: str, filters: dict, http, keyword: str, year_from: int, year_to: int,
                       doc_number: str, page: int) -> dict:
    r = await http.get(base + "LawResult.aspx", params={
        "NLawTypeID": "", "GroupID": "", "CategoryID": "", **filters,  # 沒有 NLawTypeID 會一筆都不列
        "KW": keyword, "name": 1, "content": 1, "now": 1, "fei": 1, "page": page, "size": 20,
        "StartDate": f"{year_from + 1911}0101" if year_from else "",
        "EndDate": f"{year_to + 1911}1231" if year_to else "",
        "LNumber": _number(doc_number),
    })
    r.raise_for_status()
    soup = _soup(r)
    items = []
    for tr in soup.select("table.tab-list tr"):
        td, a = tr.find_all("td"), tr.select_one("a[href*='LawContent.aspx?id=']")
        m = re.search(r"id=(GL\d+)", a["href"]) if a else None
        if m and len(td) >= 3:
            title = re.sub(r"\s+", "", a.get_text())  # 花蓮縣政府訴願決定書(115年訴字第16號)／115年度府訴決字第008號；關鍵字包在 span 裡
            no = re.search(r"[（(]\s*(.+?)\s*[)）]", title)
            tail = title.rpartition("-")[2].rstrip("。")  # 原民會：「案由-原民訴字第…號」
            doc = no.group(1) if no else (tail if "-" in title and "字第" in tail else title)
            items.append(_item(m.group(1), doc, _iso(_text(td[1])), title))
    m = re.search(r"共\s*(\d+)\s*筆", _text(soup.select_one("li.pageinfo")))
    total = int(m.group(1)) if m else len(items)
    return _page(total, items, page * 20 < total)


async def _glrs_get(base: str, filters: dict, http, gl_id: str) -> dict:
    url = f"{base}LawContent.aspx?id={gl_id}"
    r = await http.get(url)
    r.raise_for_status()
    soup = _soup(r)
    meta = {re.sub(r"[\s：:]", "", _text(tr.th)): _text(tr.td) for tr in soup.select("table.tab-edit tr") if tr.th and tr.td}
    body = soup.select_one("div.law-content")
    if body is None or "訴願" not in meta.get("法規體系", ""):  # 同一系統也放自治法規
        raise LookupError(gl_id)
    text = _html_text(str(body))
    return _result(meta.get("發文字號", ""), _iso(meta.get("公發布日", "")),
                   _cause(text) or meta.get("法規名稱", ""), text, url)


# ─────────────────────────────────────────────────────────────
# Rhythm CMS v5（News.aspx?n=&sms=）：苗栗、臺東、嘉義市、嘉義縣、宜蘭、新竹縣（外交部見上）
# 新聞式列表＋附件；查詢只比對標題，全文是附件 PDF。嘉義市、嘉義縣一筆是一次審議會的所有決定書
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Rhythm:
    base: str
    n: str
    sms: str = ""
    search: dict | None = None  # 查詢表單的固定欄位（送出鈕、類別）；None = 沒有可用的站內查詢
    always_post: bool = False   # 列表混了其他類別，一律要帶類別查詢
    kw_field: str = "jNewsModule_field_2"
    date_fields: tuple[str, str] | None = None  # 民國 yyy/MM/dd
    listing: str = "News.aspx"
    detail: str = "News_Content.aspx"
    detail_sms: str = ""  # 內容頁要帶 sms 的站
    title_selector: str = "#CCMS_Content h3"  # 內容頁的標題；空字串 = 內容頁沒有標題
    legacy_tls: bool = False


def _r5_session(cfg: _Rhythm, http: httpx.AsyncClient):
    return _client(verify=_legacy_tls()) if cfg.legacy_tls else contextlib.nullcontext(http)


def _r5_sn(link: str) -> str:
    """列表連結 → 文章序號：News_Content.aspx?s=、RelData.aspx?ParentSN=，或直接附件 /relfile/<sms>/<序號>/。"""
    q = parse_qs(urlparse(link).query)
    for k in ("s", "ParentSN"):
        if q.get(k, [""])[0].isdigit():
            return q[k][0]
    m = re.search(r"/relfile/\d+/(\d+)/", _b64(q.get("u", [""])[0]), re.I)
    return m.group(1) if m else ""


def _r5_rows(soup: BeautifulSoup) -> list[dict]:
    rows = []
    for row in soup.select("#CCMS_Content table tbody tr") or soup.select("div.directory_list"):
        a = row.select_one("a[data-fancyboxopen]") or row.select_one("a[href]")
        sn = _r5_sn(a.get("data-fancyboxopen") or a.get("href", "")) if a else ""
        if not sn:
            continue
        cells = {td.get("data-title", ""): _text(td) for td in row.find_all("td")}
        title = next((cells[k] for k in ("主題", "主旨", "標題") if cells.get(k)), _text(a))
        when = re.search(r"\d{2,4}-\d{1,2}-\d{1,2}", next((v for k, v in cells.items() if "日期" in k), _text(row)))
        case = re.search(r"案號\s*(\w+)", title)
        doc = cells.get("決定書案號") or _doc_no(title) or (case.group(1) if case else "")
        rows.append(_item(sn, doc, _iso(when.group()) if when else "", title))
    return rows


async def _r5_search(cfg: _Rhythm, http, keyword: str, year_from: int, year_to: int, doc_number: str,
                     page: int) -> dict:
    term = doc_number or keyword  # 標題多半含完整字號（苗府訴字第49號），只送號碼會比對到一堆
    list_url = f"{cfg.base}{cfg.listing}?n={cfg.n}" + (f"&sms={cfg.sms}" if cfg.sms else "")
    async with _r5_session(cfg, http) as h:
        if cfg.search is None:
            if term or year_from or year_to:  # 沒有站內查詢：抓最新 200 筆在本機比對
                r = await h.get(f"{list_url}&page=1&PageSize=200")
                r.raise_for_status()
                return _local(_r5_rows(_soup(r)), keyword, year_from, year_to, doc_number, page,
                              "此來源沒有站內查詢，只比對最新 200 筆")
            url = list_url
        else:
            fields = dict(cfg.search)
            if term:
                fields[cfg.kw_field] = term
            if cfg.date_fields and (year_from or year_to):
                fields[cfg.date_fields[0]] = f"{year_from:03d}/01/01" if year_from else ""
                fields[cfg.date_fields[1]] = f"{year_to:03d}/12/31" if year_to else ""
            if len(fields) > len(cfg.search) or cfg.always_post:
                # 查詢是 POST，站方 302 到帶 _Query=<GUID> 的列表網址，之後可用 GET 翻頁
                land = await h.get(list_url)
                land.raise_for_status()
                soup = _soup(land)
                hidden = {i["name"]: i.get("value", "") for i in soup.select("input[type=hidden]") if i.get("name")}
                action = urljoin(str(land.url), (soup.find("form") or {}).get("action") or list_url)
                r = await h.post(action, data={**hidden, **fields}, follow_redirects=False)
                location = r.headers.get("location", "")
                if "_Query=" not in location:
                    raise ValueError("查詢頁格式不符（沒有導向查詢結果）")
                url = urljoin(str(r.url), location)
            else:
                url = list_url
        r = await h.get(f"{url}&page={page}&PageSize=20")  # 帶 params= 會蓋掉網址原有的查詢字串
        r.raise_for_status()
    soup = _soup(r)
    items = _r5_rows(soup)
    pages = [int(x) for a in soup.select("a[href*='page=']") for x in re.findall(r"[?&]page=(\d+)", a["href"])]
    last = max(pages + [page if items else 1])
    notes = [_TITLE_ONLY if term else "", _NO_COUNT if page < last else "",
             _NO_YEAR if (year_from or year_to) and not cfg.date_fields else ""]
    return _page(_estimate(page, last, 20, len(items)), items, page < last, *notes)


async def _r5_get(cfg: _Rhythm, http, sn: str) -> dict:
    url = f"{cfg.base}{cfg.detail}?n={cfg.n}" + (f"&sms={cfg.detail_sms}" if cfg.detail_sms else "") + f"&s={sn}"
    async with _r5_session(cfg, http) as h:
        r = await h.get(url)
        if r.status_code == 404:
            raise LookupError(sn)
        r.raise_for_status()
        soup = _soup(r)
        own, other = {}, {}
        for a in soup.select("a[href*='Download.ashx']"):
            q = parse_qs(urlparse(a["href"]).query)
            path = _b64(q.get("u", [""])[0])  # /001/Upload/<站>/relfile/<sms>/<序號>/<guid>.pdf
            if path.lower().endswith(".pdf"):
                # 優先取本文章序號下的附件（頁面可能另附相關法規）；宜蘭舊案的附件序號不同，只好全收
                mine = re.search(rf"/relfile/\d+/{sn}/", path, re.I)
                (own if mine else other).setdefault(a["href"], _b64(q.get("n", [""])[0]) or path.rsplit("/", 1)[-1])
        files = own or other
        if not files:  # 例如宜蘭 105 年的彙整項目：頁面在，官網沒放檔案
            raise ValueError(f"官網這筆沒有附件 PDF，請開 {url}")
        texts = []
        for href, name in list(files.items())[:MAX_FILES]:
            text = await _pdf_text(h, href)
            texts.append(f"【{name}】\n{text}" if len(files) > 1 else text)
    title = _text(soup.select_one(cfg.title_selector)) if cfg.title_selector else ""
    full = "\n\n".join(texts)
    # 字號只看標題與決定書開頭：內文後段的字號多半是原處分的
    case = re.search(r"案號\s*[：:]?\s*(\d[\d-]*\d)", f"{title} {full[:300]}")
    doc = _doc_no(title) or (case.group(1) if case else "") or _doc_no(full[:100])
    data = _result(doc, _signed(full), title or _cause(full), full, url, pdf_url=next(iter(files)))
    if len(files) > 1:
        data["attachments"] = [{"name": n, "url": u} for u, n in files.items()]
        if len(texts) < len(files):
            data["notes"] = f"本筆含 {len(files)} 份決定書 PDF，全文只擷取前 {len(texts)} 份；其餘見 attachments"
    return data


_R5_FORM = {"jNewsModule_BtnSend": "送出查詢"}
_R5_DATES = ("jNewsModule_field_SDate_1", "jNewsModule_field_EDate_1")


def _r5(cfg: _Rhythm):
    return partial(_r5_search, cfg), partial(_r5_get, cfg)


def _glrs(base: str, **filters: str):
    return partial(_glrs_search, base, filters), partial(_glrs_get, base, filters)


# ─────────────────────────────────────────────────────────────
# 對外介面
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# 農業部、教育部：公開 WebForms，不使用登入狀態；分頁最多五頁
# ─────────────────────────────────────────────────────────────

_MOA_BASE = "https://appeal.moa.gov.tw/Mondel/LaKm/"
_MOE_BASE = "https://appeal.moe.gov.tw/"
_WEBFORMS_LIMIT = "此來源單次查詢限前五頁；請縮小關鍵字範圍，或至 source_url 查詢後續頁面"


async def _education_agriculture_search(key, http, keyword, year_from, year_to, doc_number, page):
    if not 1 <= page <= 5:
        raise ValueError(_WEBFORMS_LIMIT)
    moa = key == "moa"
    url = _MOA_BASE + "LaKmQry.aspx" if moa else _MOE_BASE + "hope_search.aspx"
    prefix = "ctl00$ContentPlaceHolder1$" if moa else "ctl00$cphContent$"
    async with _client() as client:
        r = await client.get(url)
        r.raise_for_status()
        data = _form(_soup(r))
        if moa:
            data.update({prefix + "TextBox8": keyword, prefix + "hidCount": "1",
                         prefix + "txtAppealnum": _number(doc_number), prefix + "Button1": "查詢"})
            if year_from:
                data[prefix + "txtADateBegin"] = f"{year_from:03d}/01/01"
            if year_to:
                data[prefix + "txtADateEnd"] = f"{year_to:03d}/12/31"
        else:
            data.update({prefix + "txtKW1": keyword or _number(doc_number), prefix + "butSearch": "查詢"})
            if keyword and doc_number:
                data.update({prefix + "txtKW2": _number(doc_number), prefix + "ddlOper1": "1"})
        r = await client.post(url, data=data)
        r.raise_for_status()
        soup = _soup(r)
        if moa and page > 1:
            button = soup.select_one(f'input[type=submit][value="{page}"]')
            if button is None:
                return _page(0, [], False, "指定頁碼不存在")
            data = _form(soup)
            data[button["name"]] = button["value"]
            r = await client.post(url, data=data)
            r.raise_for_status()
            soup = _soup(r)
        elif not moa:
            for _ in range(1, page):
                next_page = soup.select_one('a[id$="butNext"][href]')
                if next_page is None:
                    return _page(0, [], False, "指定頁碼不存在")
                data = _form(soup)
                data.update({"__EVENTTARGET": prefix + "ucPager$butNext", "__EVENTARGUMENT": ""})
                r = await client.post(url, data=data)
                r.raise_for_status()
                soup = _soup(r)
    selector = "#ContentPlaceHolder1_GridView1" if moa else "table.tableList"
    table = soup.select_one(selector)
    if table is None:
        if not re.search(r"查無|無符合|沒有資料|0\s*筆", _text(soup)):
            raise RuntimeError(f"官網未回傳查詢結果（可能是驗證或改版）：{url}")
        return _page(0, [], False)
    items = []
    for tr in table.select("tr"):
        a = tr.select_one('a[href*="Doc11.aspx"]' if moa else 'a[href*="hope_view.aspx"]')
        if a is None:
            continue
        args = parse_qs(urlparse(a["href"]).query)
        rid = args.get("No" if moa else "cid", [""])[0]
        cells = tr.find_all("td")
        if not rid or len(cells) < (3 if moa else 4):
            continue
        items.append(_item(rid, _doc_no(_text(a)) if moa else _text(cells[3]),
                           _iso(_text(cells[2])), _text(a)))
    text = _text(soup)
    count = re.search(r"共\s*(\d+)\s*" + ("頁" if moa else "筆"), text)
    total = int(count.group(1)) * 10 if count and moa else int(count.group(1)) if count else len(items)
    size = 10 if moa else 20
    notes = [_WEBFORMS_LIMIT, "列表日期為登錄日期" if moa else "官網僅公開最近二年決定書",
             _NO_COUNT if moa and count else "", _NO_YEAR if not moa and (year_from or year_to) else ""]
    return _page(total, items, page * size < total, *notes)


async def _moa_get(http, native_id):
    url = f"{_MOA_BASE}Doc11.aspx?No={native_id}&BriefNo={native_id}&Flag=Other"
    r = await http.get(url)
    r.raise_for_status()
    soup = _soup(r)
    body = soup.select_one("table")
    text = _html_text(str(body)) if body else ""
    if "訴願決定書" not in text or len(text) < 100:
        raise ValueError("農業部未回傳決定書內容，可能是驗證頁或格式已變更")
    return _result(_doc_no(text), _signed(text), _cause(text), text, url)


async def _moe_get(http, native_id):
    url = f"{_MOE_BASE}hope_view.aspx?cid={native_id}"
    r = await http.get(url)
    r.raise_for_status()
    soup = _soup(r)
    body = soup.select_one("pre")
    text = body.get_text().strip() if body else ""
    if "訴願決定書" not in text:
        raise ValueError("教育部未回傳決定書內容，可能是驗證頁或格式已變更")
    number = re.search(r"發文字號[：:]\s*(\S+)", text)
    date = re.search(r"發文日期[：:]\s*(.*)", text)
    return _result(number.group(1) if number else "", _iso(date.group(1)) if date else "",
                   _cause(text), text, url, notes="官網僅公開最近二年決定書")


# 經濟部：官網 SPA 的公開查詢 API，固定 isOpen=true
_MOEA_BASE = "https://eportal2.moea.gov.tw/EE120/"


async def _moea_query(conditions, page=1):
    body = dict.fromkeys(("caseNoString", "docNo", "appealPerson", "appealPersonRelated",
                          "appealPersonParticipant", "cause", "mainText", "fact", "reason", "fullText"))
    body.update(dateRange={"startDate": None, "endDate": None}, isOpen=True)
    body.update(conditions)
    async with _client() as client:
        r = await client.get(_MOEA_BASE + "api/pams-csrf-token")
        r.raise_for_status()
        csrf = r.json()
        if csrf.get("headerName") != "X-XSRF-TOKEN" or not csrf.get("token"):
            raise RuntimeError("經濟部公開查詢無法取得 CSRF token")
        r = await client.post(_MOEA_BASE + "api/decisionDoc/search", params={"from": (page - 1) * 10},
                              json=body, headers={"X-XSRF-TOKEN": csrf["token"]})
        r.raise_for_status()
        data = r.json()
        if not isinstance(data.get("resultList"), list) or "totalSize" not in data:
            raise RuntimeError("經濟部查詢回應格式不符")
        return data


async def _moea_search(http, keyword, year_from, year_to, doc_number, page):
    conditions = {"fullText": keyword or None, "docNo": _number(doc_number) or None}
    dates = {"startDate": f"{year_from + 1911}-01-01" if year_from else None,
             "endDate": f"{year_to + 1911}-12-31" if year_to else None}
    conditions["dateRange"] = dates
    data = await _moea_query(conditions, page)
    items = [_item(x["caseno"], x.get("sendReceno", ""), _iso(x.get("sendDay", "")),
                   _html_text(x.get("casrea", ""))) for x in data["resultList"]]
    return _page(data["totalSize"], items, page * 10 < data["totalSize"], "官網僅公開本年度及前五年度決定書")


async def _moea_get(http, native_id):
    data = await _moea_query({"caseNoString": native_id})
    row = next((x for x in data["resultList"] if x.get("caseno") == native_id), None)
    if row is None:
        raise LookupError(native_id)
    fields = (("訴願人", "psn1"), ("關係人", "relationPE"), ("參加人", "participatePE"),
              ("案由", "casrea"), ("主文", "mainText"), ("事實", "fact"), ("理由", "reason"))
    text = "\n\n".join(label + "\n" + _html_text(row[k]).replace("\r", "\n") for label, k in fields if row.get(k))
    return _result(row.get("sendReceno", ""), _iso(row.get("sendDay", "")), _html_text(row.get("casrea", "")),
                   text, _MOEA_BASE + "page/decision-doc-query", notes="案號：" + native_id)


# 中選會：前端使用的公開分頁 API，全文仍由官網文章的附件取得
_CEC_BASE = "https://web.cec.gov.tw/"


async def _cec_search(http, keyword, year_from, year_to, doc_number, page):
    params = {"id": "156", "page": page, "keyword": keyword or doc_number, "webRoute": "central",
              "beginDate": f"{year_from + 1911}-01-01" if year_from else "",
              "endDate": f"{year_to + 1911}-12-31" if year_to else ""}
    r = await http.get(_CEC_BASE + "api/central/article/list", params=params)
    r.raise_for_status()
    data = r.json()
    if str(data.get("code")) != "0" or not isinstance(data.get("data", {}).get("articleList"), list):
        raise RuntimeError("中選會查詢回應格式不符")
    data = data["data"]
    items = [_item(x["directPath"], "", _iso(x.get("beginTime", "")[:8]), x.get("directName", ""))
             for x in data["articleList"] if x.get("directType") == "005" and re.fullmatch(r"\d+", x.get("directPath", ""))]
    return _page(data["pages"]["totalCount"], items, page < data["pages"]["totalPage"], _TITLE_ONLY,
                 "日期為刊登日期", _NO_DOC if keyword and doc_number else "")


async def _cec_get(http, native_id):
    url = _CEC_BASE + "central/article/" + native_id
    r = await http.get(url)
    r.raise_for_status()
    soup = _soup(r)
    title = _text(soup.select_one("h2.title"))
    a = soup.select_one('a[href^="https://web.cec.gov.tw/api/file/"][href$=".pdf"]')
    if not title or a is None:
        raise ValueError("中選會未回傳決定書文章或 PDF 附件")
    text = await _pdf_text(http, a["href"])
    return _result(_doc_no(text), _signed(text), title, text, url, a["href"])


# 環境部：公開 DataTables API；一次只請求使用者指定的一頁
_MOENV_BASE = "https://aamis-web.moenv.gov.tw/"


async def _moenv_query(http, conditions, page=1):
    data = {"draw": "1", "start": str((page - 1) * 10), "length": "10",
            "order[0][column]": "2", "order[0][dir]": "desc", **conditions}
    for i, key in enumerate(("", "name", "date", "docNo", "caseNo", "")):
        data.update({f"columns[{i}][data]": key, f"columns[{i}][name]": "",
                     f"columns[{i}][searchable]": "true", f"columns[{i}][orderable]": "true",
                     f"columns[{i}][search][value]": "", f"columns[{i}][search][regex]": "false"})
    r = await http.post(_MOENV_BASE + "Search/Decision/Read", data=data)
    r.raise_for_status()
    result = r.json()
    if not isinstance(result.get("data"), list) or "recordsFiltered" not in result:
        raise RuntimeError("環境部公開查詢回應格式不符")
    return result


async def _moenv_search(http, keyword, year_from, year_to, doc_number, page):
    if not 1 <= page <= 30:
        raise ValueError("環境部每次查詢最多 300 筆（30 頁），請縮小條件")
    today = datetime.now().date()
    start = year_from or year_to or today.year - 1911  # 只給一個年度時查該年度，不會變成反向區間
    end = year_to or year_from
    data = await _moenv_query(http, {"Keyword": keyword, "DocNo": _number(doc_number),
        "DateStartString": f"{start}/01/01", "DateEndString": f"{end}/12/31" if end else
        f"{today.year - 1911}/{today.month:02d}/{today.day:02d}"}, page)
    rows = [_item(x["caseNo"], x.get("docNo", ""), _iso(x.get("date", "")),
                  _html_text(x.get("subject") or x.get("name", ""))) for x in data["data"]]
    return _page(data["recordsFiltered"], rows, page * 10 < min(data["recordsFiltered"], 300),
                 "未指定年度時查本年度、只指定一個年度時查該年度；官網每次查詢最多 300 筆，請縮小條件")


async def _moenv_get(http, native_id):
    data = await _moenv_query(http, {"CaseNo": native_id})
    row = next((x for x in data["data"] if x.get("caseNo") == native_id), None)
    if row is None:
        raise LookupError(native_id)
    fields = (("訴願人", "name"), ("原處分機關", "punishOrgName"), ("案由", "subject"),
              ("主文", "summary"), ("事實", "fact"), ("理由", "reason"))
    text = "\n\n".join(label + "\n" + _html_text(row[k]) for label, k in fields if row.get(k))
    file_id = row.get("fileId", "")
    pdf = _MOENV_BASE + "File/Download/" + file_id if re.fullmatch(r"[a-fA-F0-9-]{36}", file_id) else ""
    return _result(row.get("docNo", ""), _iso(row.get("date", "")), _html_text(row.get("subject", "")),
                   text, _MOENV_BASE + "Search/Decision", pdf, "案號：" + native_id)


# 勞動部：公開語音驗證功能傳回 SpeechSynthesis 所需的數字字串
_MOL_BASE = "https://appealweb.mol.gov.tw/Appeal/"


async def _mol_search(http, keyword, year_from, year_to, doc_number, page):
    async with _client() as client:
        r = await client.get(_MOL_BASE + "AppealCaseDecision")
        r.raise_for_status()
        data = _form(_soup(r))
        r = await client.get(_MOL_BASE + "GetValidateCode")
        r.raise_for_status()
        r = await client.get(_MOL_BASE + "GetVoice")
        r.raise_for_status()
        code = r.json()
        if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9]{4,8}", code):
            raise RuntimeError("勞動部語音驗證碼格式不符，無法完成查詢")
        year = year_to or year_from
        data.update(validCode=code, contentPublic1=keyword, outgoingWordNum=_number(doc_number),
                    caseYear=str(year) if year else "", pageNumber=str(page), pageSize="10")
        r = await client.get(_MOL_BASE + "AppealCaseDecisionResult", params=data)
        r.raise_for_status()
        soup = _soup(r)
        if page > 1:
            data = _form(soup, "#main-form")
            data.update(pageNumber=str(page), pageSize="10")
            r = await client.get(_MOL_BASE + "AppealCaseDecisionResult", params=data)
            r.raise_for_status()
            soup = _soup(r)
    table = soup.select_one("main table")
    count = re.search(r"共\s*([\d,]+)\s*筆", _text(soup))
    if table is None or count is None:
        raise RuntimeError("勞動部未回傳可確認的查詢結果，驗證可能未完成")
    rows = []
    for tr in table.select("tbody tr"):
        a = tr.select_one('a[href*="AppealCaseDecisionContent?caseId="]')
        cells = tr.find_all("td")
        if a and len(cells) >= 5:
            rid = parse_qs(urlparse(a["href"]).query)["caseId"][0]
            rows.append(_item(rid, _text(cells[3]), _iso(_text(cells[2])), _text(cells[4])))
    total = int(count.group(1).replace(",", ""))
    return _page(total, rows, page * 10 < total, "列表日期為發文日期",
                 f"此來源只查單一年度，本次查 {year} 年" if year_from and year_to and year_from != year_to else "")


async def _mol_get(http, native_id):
    url = _MOL_BASE + "AppealCaseDecisionContent?caseId=" + native_id
    r = await http.get(url)
    r.raise_for_status()
    node = _soup(r).select_one("main .con-flow")
    text = _html_text(str(node)) if node else ""
    if "訴願決定書" not in text:
        raise ValueError("勞動部未回傳決定書內容，可能是驗證頁或格式已變更")
    return _result(_doc_no(text), _signed(text), _cause(text), text, url)


# 文化部：一般訪客的 SPA 會自行取得公開站台 token；擷取該次前端 API 回應
_MOC_BASE = "https://appeal.moc.gov.tw/home/zh-tw/mocappeal"
_MOC_API = "https://themedata.culture.tw/api/cms/mocappeal"


def _epoch_date(value):
    return datetime.fromtimestamp(value / 1000, timezone(timedelta(hours=8))).date().isoformat() if value else ""


async def _moc_search(http, keyword, year_from, year_to, doc_number, page):
    # 官網整串當成一個詞組比對（「電影 電影」0 筆），所以只送一個詞：有字號查字號，否則取第一個詞
    terms = (doc_number or keyword).split()
    query = {"search": terms[0] if terms else ""}
    # 年度未證實是獨立欄位；不假裝 API 已套用。
    params = {"limit": 10, "offset": (page - 1) * 10, "query": json.dumps(query, ensure_ascii=False, separators=(",", ":")),
              "sort": "issueDate", "order": "desc"}
    # 空白要編成 %20：前端把「+」當成字面加號，多詞查詢會失敗
    data = await public_browser.response_json(_MOC_BASE + "?" + urlencode(params, quote_via=quote), _MOC_API + "?")
    if not isinstance(data.get("rows"), list) or "total" not in data:
        raise RuntimeError("文化部查詢回應格式不符")
    rows = [_item(str(x["id"]), "", _epoch_date(x.get("issueDate")), x.get("title", "")) for x in data["rows"]]
    return _page(data["total"], rows, page * 10 < data["total"], "日期為刊登日期", _NO_YEAR if year_from or year_to else "",
                 "本次以字號查詢，keyword 未套用" if doc_number and keyword else "",
                 f"官網只比對單一詞組，本次只查「{terms[0]}」" if len(terms) > 1 else "")


async def _moc_get(http, native_id):
    url = _MOC_BASE + "/" + native_id
    row = await public_browser.response_json(url, _MOC_API + "/" + native_id)
    if not row.get("decideDocumentNo") or not row.get("reason"):
        raise ValueError("文化部公開 API 未回傳決定書內容")
    fields = (("訴願人姓氏", "appellantSurname"), ("訴願人", "appellant"),
              ("訴願人二姓氏", "appellant2Surname"), ("訴願人二", "appellant2"),
              ("訴願人三姓氏", "appellant3Surname"), ("訴願人三", "appellant3"),
              ("法定代理人", "legalRepresentative"), ("代表人", "representative"), ("代理人", "appellantProxy"),
              ("案由", "description"), ("主文", "content"), ("事實", "fact"), ("理由", "reason"),
              ("主任委員", "chairperson"), ("委員", "commissioner"), ("附記", "teching"))
    text = "\n\n".join(label + "\n" + _html_text(row[k]) for label, k in fields if row.get(k))
    return _result(row.get("decideDocumentNo", ""), _epoch_date(row.get("decideDate")),
                   row.get("title", ""), text, url)


# 基隆市：標題為案號；不下載其他結果的 PDF 做全文檢索
_KL_BASE = "https://www.klcg.gov.tw/"


async def _keelung_search(http, keyword, year_from, year_to, doc_number, page):
    r = await http.get(_KL_BASE + "tw/klcg1/2669.html", params={"q_stitle": doc_number or keyword,
                       "q_xbody": "", "nowPage": page, "pageSize": 10})
    r.raise_for_status()
    soup = _soup(r)
    count = re.search(r"共\s*(\d+)\s*筆資料", _text(soup))
    if not count:
        raise RuntimeError("基隆市查詢頁格式不符")
    rows = []
    for a in soup.select('a[href*="/2669-"]'):
        match = re.search(r"/2669-(\d+)\.html", a["href"])
        if match:
            rows.append(_item(match.group(1), _text(a), "", _text(a)))
    total = int(count.group(1))
    return _page(total, rows, page * 10 < total, _TITLE_ONLY + "（多為案號）",
                 _NO_YEAR if year_from or year_to else "",
                 "本次以字號查標題，keyword 未套用" if keyword and doc_number else "")


async def _keelung_get(http, native_id):
    url = _KL_BASE + "tw/klcg1/2669-" + native_id + ".html"
    r = await http.get(url)
    r.raise_for_status()
    soup = _soup(r)
    a = soup.select_one('a[href^="/wSite/public/Attachment/"][href$=".pdf"]')
    if a is None:
        raise ValueError("基隆市未回傳決定書 PDF 附件")
    pdf = urljoin(_KL_BASE, a["href"])
    text = await _pdf_text(http, pdf)
    return _result(_doc_no(text), _signed(text), _cause(text), text, url, pdf)


# 人事總處：每次僅讀一頁標題並比對；不抓完整清單
_DGPA_BASE = "https://www.dgpa.gov.tw/"


async def _dgpa_search(http, keyword, year_from, year_to, doc_number, page):
    r = await http.get(_DGPA_BASE + "informationlist", params={"uid": 130, "page": page})
    r.raise_for_status()
    soup = _soup(r)
    count = re.search(r"共\s*(\d+)\s*筆", _text(soup))
    if count is None:
        raise RuntimeError("人事總處查詢頁格式不符")
    rows = []
    for a in soup.select('a[href^="information?uid=130&pid="]'):
        title = _text(a)
        day = re.search(r"\d{3}\.\d{2}\.\d{2}", title)
        rid = parse_qs(urlparse(a["href"]).query)["pid"][0]
        rows.append(_item(rid, "", _iso(day.group()) if day else "", title))
    hits = _local(rows, keyword, year_from, year_to, doc_number, 1)["items"]
    total = int(count.group(1))
    return _page(total, hits, page * 10 < total,
                 "只比對指定頁面的標題及刊登年度；總筆數為未篩選清單筆數，空頁不代表其他頁沒有符合資料")


async def _dgpa_get(http, native_id):
    url = _DGPA_BASE + "information?uid=130&pid=" + native_id
    r = await http.get(url)
    r.raise_for_status()
    soup = _soup(r)
    a = soup.select_one('a[href^="/FileConversion?"][href*=".odt"]')
    if a is None:
        raise ValueError("人事總處未回傳決定書 ODT 附件")
    attachment = urljoin(_DGPA_BASE, a["href"])
    r = await http.get(attachment)
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        raise ValueError("人事總處未回傳 ODT，可能是錯誤頁")
    text = _office_text(r.content)
    if not text:
        raise RuntimeError("人事總處附件無法擷取文字")
    return {**_result(_doc_no(text), _signed(text), _text(a), text, url), "attachment_url": attachment}


# 內政部／衛福部：每次新工作階段，本機 OCR 最多辨識兩次
_MOI_BASE = "https://aarc.moi.gov.tw/Decision/"
_MOHW_BASE = "https://service.mohw.gov.tw/AppealSearch/"


async def _captcha_search(key, http, keyword, year_from, year_to, doc_number, page):
    from mcp_server.tools.public_captcha import recognize

    moi = key == "moi"
    base = _MOI_BASE if moi else _MOHW_BASE
    year = datetime.now().year - 1911
    start, end = year_from or year_to or year, year_to or year_from or year
    notes = [f"查詢民國 {start} 至 {end} 年；未指定年度時限本年度"]
    if moi and end != start:
        raise ValueError("內政部官網一次回傳全部符合清單，請指定單一年度以限制查詢範圍")
    async with _client() as client:
        for attempt in range(2):
            if attempt:
                await asyncio.sleep(1)
            r = await client.get(base)
            r.raise_for_status()
            soup = _soup(r)
            data = _form(soup)
            image = await client.get(base + ("VerificationCode" if moi else "ModCaptcha/JpegImage.ashx"))
            image.raise_for_status()
            code = await recognize(image.content)
            if moi:
                data.update(Captcha=code, FullTextKw=keyword, IsIncludeFullTextKw="True",
                            DecisionNumber=_number(doc_number), StrStartDate=f"{start}/01/01", StrEndDate=f"{end}/12/31",
                            CaseTypeArray=[x["value"] for x in soup.select('input[name=CaseTypeArray][checked]')])
            else:
                data.update(TBOXCaptcha=code, TXTKeyword1=keyword, TXTAppealNo=_number(doc_number),
                            TXTSDate=f"{start}/01/01", TXTEDate=f"{end}/12/31", send="送出")
            r = await client.post(base + ("Search" if moi else "SearchResult.aspx"), data=data)
            r.raise_for_status()
            soup = _soup(r)
            # 衛福部驗證失敗也會顯示「查無資料」，不可當成真的零筆。
            valid = soup.select_one("table.appeals_table" if moi else "table tbody")
            if valid is not None:
                break
        else:
            raise RuntimeError("官網未回傳可確認的結果表格；兩次驗證未完成或結果為空，不能判定查無資料")
        if not moi and page > 1:
            data = _form(soup)
            data.update({"ctl00$content$ucPage$txtPageSelector": str(page), "ctl00$content$ucPage$btn_go": "go"})
            r = await client.post(base + "SearchResult.aspx", data=data)
            r.raise_for_status()
            soup = _soup(r)
    table = soup.select_one("table.appeals_table" if moi else "table")
    if table is None:
        raise RuntimeError("官網分頁未回傳結果表格")
    rows = []
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        a = tr.select_one('a[href*="Detail?desid="]' if moi else 'a[href*="AppNo="][href*="type=odt"]')
        if a and len(cells) >= 5:
            args = parse_qs(urlparse(a["href"]).query)
            rid = args["desid" if moi else "AppNo"][0]
            rows.append(_item(rid, _text(cells[3 if moi else 2]), _iso(_text(cells[2 if moi else 3])),
                              _text(cells[4 if moi else 1])))
    if moi:
        items, more = _paged(rows, page)
        return _page(len(rows), items, more, *notes)
    count = re.search(r"共\s*(\d+)\s*筆資料", _text(soup))
    if count is None:
        raise RuntimeError("衛福部未回傳可確認的筆數")
    total = int(count.group(1))
    return _page(total, rows, page * 10 < total, *notes)


async def _moi_get(http, native_id):
    url = _MOI_BASE + "Detail?" + urlencode({"desid": native_id})
    r = await http.get(url)
    r.raise_for_status()
    node = _soup(r).select_one(".page_pdf")
    text = _html_text(str(node)) if node else ""
    if "訴願" not in text or "主文" not in text:
        raise ValueError("內政部未回傳決定書內容，可能是驗證頁或格式已變更")
    return _result(_doc_no(text), _signed(text), _cause(text), text, url)


async def _mohw_get(http, native_id):
    url = _MOHW_BASE + "AppealDownload.aspx?" + urlencode({"AppNo": native_id, "type": "odt"})
    r = await http.get(url)
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        raise ValueError("衛福部未回傳 ODT，可能是驗證頁或錯誤頁")
    text = _office_text(r.content)
    if not text:
        raise RuntimeError("衛福部未回傳可讀取的 ODT 決定書")
    return {**_result(_doc_no(text), _signed(text), _cause(text), text, _MOHW_BASE), "attachment_url": url}


def _register(key: str, label: str, agency: str, aliases: tuple[str, ...], id_pattern: str, search, get):
    """補上 id 前綴、機關、類別；get 先驗證原站識別碼格式（不合格式不組網址）。"""

    async def _search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
        g = await search(http, keyword.strip(), year_from, year_to, doc_number.strip(), page)
        items = [{"id": f"{key}:{i.pop('id')}", "agency": agency, "category": CATEGORY, **i} for i in g.pop("items")]
        return {"source": label, "category": CATEGORY, **g, "items": items}

    async def _get(http, native_id: str) -> dict:
        if not re.fullmatch(id_pattern, native_id):
            raise LookupError(native_id)
        return {"agency": agency, "category": CATEGORY, **await get(http, native_id)}

    return key, (label, (*aliases, "訴願"), _search, _get)


SOURCES = dict([
    _register("moi", "內政部訴願決定", "內政部", ("內政部",), r"[A-Za-z0-9+/]{11}=", partial(_captcha_search, "moi"), _moi_get),
    _register("mohw", "衛福部訴願決定", "衛生福利部", ("衛福部", "衛生福利部"), r"[0-9]{10}", partial(_captcha_search, "mohw"), _mohw_get),
    _register("moenv", "環境部訴願決定", "環境部", ("環境部", "環保署"), r"[0-9]{11}[A-Z]{2}[0-9]{2}", _moenv_search, _moenv_get),
    _register("mol", "勞動部訴願決定", "勞動部", ("勞動部", "勞委會"), r"[0-9]{1,10}", _mol_search, _mol_get),
    _register("moc", "文化部訴願決定", "文化部", ("文化部",), r"[0-9]{1,10}", _moc_search, _moc_get),
    _register("keelung", "基隆市訴願決定", "基隆市政府", ("基隆", "基隆市", "基隆市政府"), r"[0-9]{1,10}", _keelung_search, _keelung_get),
    _register("dgpa", "人事總處訴願決定", "行政院人事行政總處", ("人事總處", "行政院人事行政總處"), r"[0-9]{1,10}", _dgpa_search, _dgpa_get),
    _register("moea", "經濟部訴願決定", "經濟部", ("經濟部",), r"[A-Z]\d{9}", _moea_search, _moea_get),
    _register("cec", "中選會訴願決定", "中央選舉委員會", ("中選會", "中央選舉委員會"), r"\d{1,10}", _cec_search, _cec_get),
    _register("moa", "農業部訴願決定", "農業部", ("農業部", "農委會"), r"\d{1,10}",
              partial(_education_agriculture_search, "moa"), _moa_get),
    _register("moe", "教育部訴願決定", "教育部", ("教育部",), r"\d{9}",
              partial(_education_agriculture_search, "moe"), _moe_get),
    _register("taichung", "臺中市政府訴願決定", "臺中市政府",
              ("臺中市政府", "臺中市", "台中市", "臺中", "台中", "中市"), r"\d{5,10}", _tc_search, _tc_get),
    _register("taipei", "臺北市政府訴願決定", "臺北市政府",
              ("臺北市政府", "臺北市", "台北市", "臺北", "台北", "北市"), r"\d{1,4}-\d{1,4}", _tp_search, _tp_get),
    _register("ntpc", "新北市政府訴願決定", "新北市政府",
              ("新北市政府", "新北市", "新北"), r"\d{6,12}", _ntpc_search, _ntpc_get),
    _register("kaohsiung", "高雄市政府訴願決定", "高雄市政府",
              ("高雄市政府", "高雄市", "高雄", "高市"), r"\d{1,8}", _kh_search, _kh_get),
    _register("changhua", "彰化縣政府訴願決定", "彰化縣政府",
              ("彰化縣政府", "彰化縣", "彰化"), r"\d{1,8}", _ch_search, _ch_get),
    _register("hualien", "花蓮縣政府訴願決定", "花蓮縣政府", ("花蓮縣政府", "花蓮縣", "花蓮"), r"GL\d{6}",
              *_glrs("https://glrs.hl.gov.tw/glrsout/", NLawTypeID="9", CategoryID="24")),
    _register("kinmen", "金門縣政府訴願決定", "金門縣政府", ("金門縣政府", "金門縣", "金門"), r"GL\d{6}",
              *_glrs("https://law.kinmen.gov.tw/", NLawTypeID="8", GroupID="8")),
    _register("miaoli", "苗栗縣政府訴願決定", "苗栗縣政府", ("苗栗縣政府", "苗栗縣", "苗栗"), r"\d{1,10}",
              *_r5(_Rhythm("https://www.miaoli.gov.tw/general_affairs/", "872", "9710", search=_R5_FORM))),
    _register("taitung", "臺東縣政府訴願決定", "臺東縣政府", ("臺東縣政府", "臺東縣", "台東縣", "臺東", "台東"), r"\d{1,10}",
              *_r5(_Rhythm("https://www.taitung.gov.tw/", "13382", "12660", always_post=True,
                           search={**_R5_FORM, "jNewsModule_field_1": "539"},  # 類別「訴願決定書」
                           kw_field="jNewsModule_field_0", date_fields=_R5_DATES))),
    _register("chiayi_city", "嘉義市政府訴願決定", "嘉義市政府", ("嘉義市政府", "嘉義市", "嘉義"), r"\d{1,10}",
              *_r5(_Rhythm("https://general.chiayi.gov.tw/", "3813", "11872", search=_R5_FORM, date_fields=_R5_DATES))),
    _register("chiayi_county", "嘉義縣政府訴願決定", "嘉義縣政府", ("嘉義縣政府", "嘉義縣", "嘉義"), r"\d{1,10}",
              *_r5(_Rhythm("https://www.cyhg.gov.tw/", "1220", "12642", legacy_tls=True))),
    _register("yilan", "宜蘭縣政府訴願決定", "宜蘭縣政府", ("宜蘭縣政府", "宜蘭縣", "宜蘭"), r"\d{1,10}",
              *_r5(_Rhythm("https://www.e-land.gov.tw/", "9929", always_post=True,
                           search={"ddlCategory_Main": "12330", "btnSearch": "查詢"},  # 政府資訊公開「訴願決定書」類
                           kw_field="txtKeyword", date_fields=("SDate", "EDate"),
                           listing="OpenData_Default.aspx", detail="OpenData_DealData.aspx", detail_sms="12330",
                           title_selector=""))),
    _register("hsinchu_county", "新竹縣政府訴願決定", "新竹縣政府", ("新竹縣政府", "新竹縣"), r"\d{1,10}",
              *_r5(_Rhythm("https://gdd.hsinchu.gov.tw/", "520", "8965"))),
    _register("mofa", "外交部訴願決定", "外交部", ("外交部",), r"\d{1,10}", _mofa_search, _mofa_get),
    _register("mnd", "國防部訴願決定", "國防部", ("國防部",), r"\d{1,8}", _mnd_search, _mnd_get),
    _register("moj", "法務部訴願決定", "法務部", ("法務部",), r"\d{8,14}", _moj_search, _moj_get),
    _register("motc", "交通部訴願決定", "交通部", ("交通部",), r"\d{8,12}", _motc_search, _motc_get),
    _register("fsc_appeal", "金管會訴願決定", "金融監督管理委員會", ("金管會", "金融監督管理委員會"), r"\d{12}",
              _fsc_search, _fsc_get),
    _register("cbc", "中央銀行訴願決定", "中央銀行", ("中央銀行", "央行"), r"\d{1,6}", _cbc_search, _cbc_get),
    _register("vac", "退輔會訴願決定", "國軍退除役官兵輔導委員會", ("退輔會", "輔導會", "國軍退除役官兵輔導委員會"),
              r"\d{1,8}-[0-9A-Fa-f]{32}", _vac_search, _vac_get),
    _register("nstc", "國科會訴願決定", "國家科學及技術委員會", ("國科會", "國家科學及技術委員會"),
              r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", _nstc_search, _nstc_get),
    _register("moda", "數位部訴願決定", "數位發展部", ("數位部", "數位發展部"), r"[A-Za-z0-9]{6,32}",
              _moda_search, _moda_get),
    _register("pcc", "工程會訴願決定", "行政院公共工程委員會", ("工程會", "公共工程委員會", "行政院公共工程委員會"),
              r"\d{6,20}", _pcc_search, _pcc_get),
    # 部會主管法規共用系統的「函釋及訴願決定」類（幾乎都是訴願決定；標題含訴願人姓名，官網原樣）
    _register("cip", "原民會訴願決定", "原住民族委員會", ("原民會", "原住民族委員會"), r"GL\d{6}",
              *_glrs("https://law.cip.gov.tw/", NLawTypeID="all", GroupID="5")),
])
