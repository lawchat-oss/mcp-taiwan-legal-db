"""行政機關函釋查詢（#9）

各部會的函釋分散在各自的系統、格式各異，沒有共用 API；每個來源一組 search / get，查詢時即時抓官方網站：

| 代碼 | 來源 | 內容 |
|---|---|---|
| moj | 法務部主管法規查詢系統 mojlaw.moj.gov.tw | 行政函釋、法規諮詢意見 |
| mol | 勞動部勞動法令查詢系統 laws.mol.gov.tw | 行政函釋、解釋令 |
| pcc | 工程會政府採購法規解釋函令 planpe.pcc.gov.tw | 採購法令解釋令、函 |
| mof | 財政部各稅法令函釋檢索系統 ttc.mof.gov.tw | 稅務法令彙編、新頒令釋 |
| gcis | 經濟部商業發展署 商工行政法規 gcis.nat.gov.tw | 公司法、商業登記法等函釋 |
| ris | 內政部戶政司 www.ris.gov.tw | 戶籍、國籍、姓名等函釋 |
| nlma | 內政部國土管理署 www.nlma.gov.tw | 建築管理、都市計畫、住宅等解釋函（整份清單下載後在本機比對） |
| fint | 司法院法學資料檢索系統 legal.judicial.gov.tw | 跨機關行政函釋（司法院、法務部等） |
| gazette | 行政院公報 gazette.nat.gov.tw | 各部會依行政程序法第 159 條第 2 項第 2 款發布的解釋性規定 |

函釋 id 一律為「來源代碼:原站識別碼」，例如 moj:FE393340、mol:007:勞動發事:1150512614A。
函釋屬公文，依著作權法第 9 條不受著作權保護。
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import io
import json
import logging
import os
import re
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urlencode
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from mcp_server.cache.db import CacheDB
from mcp_server.config import USER_DATA_DIR
from mcp_server.tools import fint
from mcp_server.tools._errors import error_response

logger = logging.getLogger(__name__)

# 工程會的 WAF 會擋舊版 Chrome 的 User-Agent
USER_AGENT = fint.USER_AGENT
PAGE_SIZE = 20
MAX_FULL_TEXT = 20000
_unwrap = fint.unwrap

MOJ_BASE = "https://mojlaw.moj.gov.tw"
MOL_BASE = "https://laws.mol.gov.tw"
PCC_BASE = "https://planpe.pcc.gov.tw/prms/explainLetter/"
MOF_BASE = "https://ttc.mof.gov.tw"
GCIS_BASE = "https://gcis.nat.gov.tw/elawAp/api/"
RIS_BASE = "https://www.ris.gov.tw/info-lawsExplained/app/aw0711"
NLMA_LIST_URL = "https://www.nlma.gov.tw/sites/www.nlma.gov.tw/ch/main/interpcomp/list.json"
NLMA_PAGE_URL = "https://www.nlma.gov.tw/ch/titlelist/interpcomp/{}"
GAZETTE_BASE = "https://gazette.nat.gov.tw/egFront/"

TIPO_XML_URL = "https://www.tipo.gov.tw/public/Data/data_output_1.xml"
MOHW_BASE = "https://mohwlaw.mohw.gov.tw/FINT/"

# 正本、副本只是受文者清單，佔篇幅又沒有法律內容
_RECIPIENTS = re.compile(r"\n(?:正[\s　]*本|副[\s　]*本)[\s　]*[：:].*", re.S)
_DOC_NUMBER = re.compile(r"[^\s，。、（）()「」]{1,30}字第\s*[0-9A-Za-z０-９\-]+\s*號[令函]?")


@dataclass
class Query:
    keyword: str = ""
    start: str = ""  # 西元 YYYYMMDD
    end: str = ""
    number: str = ""  # 發文字號（可只填號碼）
    page: int = 1
    agency_names: list[str] = field(default_factory=list)  # 行政院公報用：以標題機關名篩選

    @property
    def number_digits(self) -> str:
        """發文字號中的號碼：「台財稅發第6180號令」→ 6180；只給號碼時原樣使用。"""
        m = re.search(r"第\s*([0-9A-Za-z]+)\s*號", self.number) or re.search(r"[0-9A-Za-z]+", self.number)
        return m.group(m.lastindex or 0) if m else ""


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def _html_text(fragment: str) -> str:
    """HTML 片段 → 純文字（<p>/<br> 變換行）。"""
    soup = BeautifulSoup(fragment or "", "html.parser")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for p in soup.find_all(["p", "div", "li"]):
        p.insert_after("\n")
    return re.sub(r"\n\s*\n+", "\n", html.unescape(soup.get_text())).strip()


def _date(s: str) -> str:
    """「114.11.25」「民國 110 年 05 月 17 日」「105/05/31」「2025/03/12」→ 2025-11-25；無法解析回原字串。"""
    m = re.fullmatch(r"\s*(\d{4})(\d{2})(\d{2})\s*", s or "") or re.search(
        r"(\d{2,4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})", s or ""
    )
    if not m:
        return (s or "").strip()
    y, mo, d = (int(g) for g in m.groups())
    return f"{y + 1911 if y < 1911 else y:04d}-{mo:02d}-{d:02d}"


def _fold(doc_number: str) -> str:
    return re.sub(r"\s+", " ", doc_number).replace(" 字第", "字第").strip()


def _group(source: str, category: str, total: int, items: list[dict], has_more: bool, **extra) -> dict:
    return {"source": source, "category": category, "total": total, "items": items, "has_more": has_more, **extra}


def _session() -> httpx.AsyncClient:
    """需要 cookie / CSRF 的來源，每次查詢用獨立 session。"""
    return httpx.AsyncClient(timeout=60.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def _paged(rows: list, page: int, size: int = PAGE_SIZE) -> tuple[list, bool]:
    return rows[(page - 1) * size: page * size], page * size < len(rows)


def _in_range(iso: str, q: Query) -> bool:
    d = iso.replace("-", "")
    return (not q.start or d >= q.start) and (not q.end or d <= q.end)


# ─────────────────────────────────────────────────────────────
# 法務部（mojlaw.moj.gov.tw）
# ─────────────────────────────────────────────────────────────

_MOJ_CATEGORIES = {"etype5": "行政函釋", "etype3": "法規諮詢意見"}


async def _moj_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    out = []
    for check, label in _MOJ_CATEGORIES.items():
        r = await http.get(f"{MOJ_BASE}/LawResult.aspx", params={
            "check": check, "kw": q.keyword, "star": q.start, "end": q.end, "number": q.number_digits,
            "iPageSize": PAGE_SIZE, "page": q.page, "sort": 1,
        })
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        m = re.search(r"資料類別：\S+\s+(\d+)\s*筆", soup.get_text(" "))
        items = []
        for tr in soup.select("tr:has(a[href^='LawContentExShow'])"):
            a = tr.select_one("a[href^='LawContentExShow']")
            fe = re.search(r"id=(FE\d+)", a["href"])
            if not fe:
                continue
            agency, _, doc_number = _text(a).partition(" ")
            items.append({
                "id": f"moj:{fe.group(1)}", "agency": agency, "category": label,
                "doc_number": _fold(doc_number), "date": _date(_text(tr.select_one("span"))),
                "summary": _unwrap(tr.select_one("pre").get_text()) if tr.select_one("pre") else "",
            })
        total = int(m.group(1)) if m else len(items)
        out.append(_group("法務部", label, total, items, q.page * PAGE_SIZE < total))
    return out


async def _moj_get(http: httpx.AsyncClient, native_id: str) -> dict:
    url = f"{MOJ_BASE}/LawContentExShow.aspx?id={native_id}&type=E"
    r = await http.get(url)
    r.raise_for_status()
    if "LawContentExShow" not in str(r.url):
        raise LookupError(native_id)
    soup = BeautifulSoup(r.text, "html.parser")
    fields = {
        _text(row.select_one(".col-th")).rstrip("：: "): row.select_one(".col-td")
        for row in soup.select(".div-extent > .col-row")
        if row.select_one(".col-th") and row.select_one(".col-td")
    }
    full = soup.select_one("#cp_content_EFULLtr pre")
    summary = soup.select_one("#cp_content_EDATAtr pre")
    return {
        "agency": _text(fields.get("發文單位")),
        "doc_number": _fold(_text(fields.get("發文字號"))),
        "date": _date(_text(fields.get("發文日期"))),
        "data_source": _text(fields.get("資料來源")),
        "related_laws": [_text(a) for a in soup.select("#cp_content_Erela li a")],
        "summary": _unwrap(summary.get_text()) if summary else "",
        "full_text": _RECIPIENTS.sub("", _unwrap(full.get_text())) if full else "",
        "notes": _text(soup.select_one("#cp_content_Etr .col-td")),
        "attachments": [
            {"title": _text(a), "url": str(r.url.join(a["href"]))}
            for a in soup.select("a[href*='LawGetFile'], a[href*='GetFile.ashx']")
        ],
        "source_url": url,
    }


# ─────────────────────────────────────────────────────────────
# 勞動部（laws.mol.gov.tw）
# ─────────────────────────────────────────────────────────────

# 頁籤 type 參數 → (明細頁 mode, 名稱)；實質法規命令不是函釋，不查
_MOL_CATEGORIES = {"etype,": ("e", "行政函釋"), "etype,007": ("007", "解釋令")}
_MOL_DOC_NUMBER = re.compile(r"^(.*?)字第\s*(\S+)\s*號")


async def _mol_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    word, no = "", ""
    if q.number:
        m = _MOL_DOC_NUMBER.match(q.number)
        word, no = (m.group(1), m.group(2)) if m else ("", q.number_digits)
    out = []
    for tab, (mode, label) in _MOL_CATEGORIES.items():
        r = await http.get(f"{MOL_BASE}/FINT/results.aspx", params={
            "etype": "*, 002, 007", "now": 1, "lnabndn": 1, "keyword": q.keyword,
            "N1": word, "N2": no, "sdate": q.start, "edate": q.end, "title": "out", "type": tab, "page": q.page,
        })
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        badges = [int(_text(b) or 0) for b in soup.select("#cph_content_ulLawCate .badge")]
        total = badges[2 if mode == "007" else 0] if len(badges) == 3 else 0
        items = []
        for row in soup.select(".fint-list > .row"):
            a = row.select_one("a[id*=hlkTitle]")
            tds = row.select(".col-td")
            m = _MOL_DOC_NUMBER.match(_text(a)) if a else None
            if not m:
                continue
            items.append({
                "id": f"mol:{mode}:{m.group(1).strip()}:{m.group(2)}", "agency": "勞動部", "category": label,
                "doc_number": _fold(_text(a)), "date": _date(_text(tds[1])) if len(tds) > 1 else "",
                "summary": _unwrap(row.select_one("pre").get_text()) if row.select_one("pre") else "",
            })
        out.append(_group("勞動部", label, total, items, q.page * PAGE_SIZE < total))
    return out


async def _mol_get(http: httpx.AsyncClient, native_id: str) -> dict:
    mode, word, no = native_id.split(":", 2)
    params = {"datatype": "etype", "N1": word, "N2": no, "now": 1, "lnabndn": 1, "recordno": 1}
    if mode != "e":
        params["mode"] = mode
    r = await http.get(f"{MOL_BASE}/FLAW/FLAWDOC03.aspx", params=params)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    key = soup.select_one("#cph_content_FDLink")
    # 字別是部分比對（「台勞保二」也會找到「（77）台勞保二」），號碼必須完全相同
    if not key or key.get("value", "").split(",")[3:4] != [no]:
        raise LookupError(native_id)
    fields, full = {}, None
    for tr in soup.select("table.fint-table tr"):
        th = tr.select_one("td.th")
        if th:
            fields[_text(th).rstrip("：: ").replace(" ", "")] = tr.find_all("td")[-1]
        elif tr.select_one("pre"):
            full = tr.select_one("pre")
    return {
        "agency": _text(fields.get("發文單位")),
        "doc_number": _fold(_text(fields.get("發文字號"))),
        "date": _date(_text(fields.get("發文日期"))),
        "data_source": _text(fields.get("資料來源")),
        "related_laws": [_text(a) for a in fields["相關法條"].select("li a")] if "相關法條" in fields else [],
        "summary": _unwrap(fields["要旨"].get_text()) if "要旨" in fields else "",
        "full_text": _RECIPIENTS.sub("", _unwrap(full.get_text())) if full else "",
        "notes": _text(fields.get("編註")),
        "attachments": [
            {"title": _text(a), "url": str(r.url.join(a["href"]))}
            for a in soup.select("a[href*='Download.ashx']")
        ],
        "source_url": str(r.url),
    }


# ─────────────────────────────────────────────────────────────
# 衛生福利部（mohwlaw.mohw.gov.tw）：清單與明細都是 GET；明細以字別＋號碼取，不用會變動的清單序號
# ─────────────────────────────────────────────────────────────

_MOHW_PAGE = 10


def _mohw_params(q: Query, word: str = "", no: str = "") -> dict:
    return {
        "starDate": q.start or "00000000", "endDate": q.end or "99991231", "no": "", "n1": word, "n2": no,
        "kt": "", "kw": q.keyword, "kw2": "", "kw3": "", "kw4": "", "valid": "", "type": "etype_",
    }


async def _mohw_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    word, no = "", ""
    if q.number:
        m = _MOL_DOC_NUMBER.match(q.number)
        word, no = (m.group(1).strip(), m.group(2)) if m else ("", q.number_digits)
    r = await http.get(MOHW_BASE + "FINTQRY03.aspx", params={**_mohw_params(q, word, no), "page": q.page})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    m = re.search(r"共\s*([\d,]+)\s*筆", soup.get_text(" "))
    items: list[dict] = []
    for tr in soup.select("table#dat02 tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 3:
            continue
        label, value = _text(tds[1]).replace(" ", ""), tds[2]
        if label == "發文字號：":
            dm = _MOL_DOC_NUMBER.match(_text(value))
            if dm:
                items.append({
                    "id": f"mohw:{dm.group(1).strip()}:{dm.group(2)}", "agency": "衛生福利部", "category": "行政函釋",
                    "doc_number": _fold(_text(value)), "date": "", "summary": "",
                })
        elif items and label == "發文日期：":
            items[-1]["date"] = _date(_text(value))
        elif items and (label.startswith("主") or not label):  # 主旨跨多列，後續列標籤空白
            items[-1]["summary"] += value.get_text(strip=True)
    total = int(m.group(1).replace(",", "")) if m else len(items)
    return [_group("衛生福利部", "行政函釋", total, items, q.page * _MOHW_PAGE < total)]


async def _mohw_get(http: httpx.AsyncClient, native_id: str) -> dict:
    word, _, no = native_id.partition(":")
    r = await http.get(MOHW_BASE + "FINTQRY04.aspx", params={**_mohw_params(Query(), word, no), "RowNo": 1})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for x in soup(["script", "style"]):
        x.decompose()
    lines = soup.get_text("\n").splitlines()
    fields = {}
    for line in lines:
        k, sep, v = line.strip().partition("：")
        if sep and k in ("發文單位", "發文字號", "發文日期", "資料來源") and k not in fields:
            fields[k] = v.strip()
    if no not in re.sub(r"\s", "", fields.get("發文字號", "")):
        raise LookupError(native_id)
    text = "\n".join(lines)
    start = re.search(r"\n\s*主[\s\u3000]*旨[\s\u3000]*：", text)
    end = re.search(r"\n\s*共\s*1\s*筆", text[start.start():]) if start else None
    body = text[start.start(): start.start() + end.start()] if start and end else ""
    laws = re.search(r"相關法條：(.*?)(?:\n\s*主[\s\u3000]*旨|$)", text, re.S)
    return {
        "agency": fields.get("發文單位", ""), "doc_number": _fold(fields.get("發文字號", "")),
        "date": _date(fields.get("發文日期", "")), "data_source": fields.get("資料來源", ""),
        "summary": "",
        "full_text": _RECIPIENTS.sub("", _unwrap(body)),
        "related_laws": [
            l for l in (re.sub(r"\s+", " ", x).strip() for x in re.split(r"\n\s*\n|\)\s*\n", laws.group(1)))
            if l
        ] if laws else [],
        "source_url": str(r.url),
    }


# ─────────────────────────────────────────────────────────────
# 工程會（planpe.pcc.gov.tw）：表單要 session cookie + CSRF token
# ─────────────────────────────────────────────────────────────

def _roc_slash(yyyymmdd: str) -> str:
    return f"{int(yyyymmdd[:4]) - 1911}/{yyyymmdd[4:6]}/{yyyymmdd[6:]}" if yyyymmdd else ""


async def _pcc_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    async with _session() as s:
        page = await s.get(PCC_BASE + "readPrmsExplainLetterSearch")
        page.raise_for_status()
        csrf = re.search(r'name="_csrf"\s+value="([^"]+)"', page.text)
        if not csrf:
            raise ValueError("工程會查詢頁找不到 CSRF token")
        r = await s.post(PCC_BASE + "readPrmsExplainLetter", data={
            "_csrf": csrf.group(1), "type": ["1", "2", "3"], "keyword1": q.keyword, "links": "and", "keyword2": "",
            "explainNumberNo": q.number_digits, "startDate": _roc_slash(q.start), "endDate": _roc_slash(q.end),
            "startNetDate": "", "endNetDate": "", "sorts": "issuedDate",
            "paginationPage": str(q.page - 1), "pageSize": str(PAGE_SIZE),
        })
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    m = re.search(r"共有\s*([\d,]+)\s*筆", soup.get_text())
    items = []
    for tr in soup.select("table.tb_01 tr:has(td)"):
        tds = tr.find_all("td")
        pk = re.search(r"readExplainLetter\((\d+)\)", str(tr))
        if not pk or len(tds) < 4:
            continue
        stamp = _text(tds[3])
        d = re.search(r"(\d{2,3})-(\d{1,2})-(\d{1,2})", stamp)
        items.append({
            "id": f"pcc:{pk.group(1)}", "agency": "行政院公共工程委員會", "category": "採購法規解釋函令",
            "doc_number": _fold(stamp[d.end():] if d else stamp),
            "date": _date(d.group()) if d else "",
            "summary": _text(tds[2]),
            "related_laws": [x.strip() for x in tds[1].get_text("\n").split("\n") if x.strip()],
        })
    total = int(m.group(1).replace(",", "")) if m else len(items)
    return [_group("工程會", "採購法規解釋函令", total, items, q.page * PAGE_SIZE < total)]


async def _pcc_get(http: httpx.AsyncClient, native_id: str) -> dict:
    url = f"{PCC_BASE}readPrmsExplainLetterContentDetail?pkPrmsRuleContent={native_id}"
    r = await http.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    tables = soup.select("#printExplain table")
    if not tables:
        raise LookupError(native_id)
    head = [_text(td) for td in tables[0].select("td")]

    def pick(label: str) -> str:
        return next((h.split("：", 1)[1].strip() for h in head if h.startswith(label + "：")), "")

    title = _text(soup.select_one(".title_1s"))
    return {
        "agency": title.rsplit(" ", 1)[0] if " " in title else title,
        "doc_type": title.rsplit(" ", 1)[1] if " " in title else "",
        "doc_number": _fold(pick("發文字號")),
        "date": _date(pick("發文日期")),
        "related_laws": [h.removeprefix("根據").strip() for h in head if h.startswith("根據")],
        "summary": "",
        "full_text": _html_text(str(tables[1])) if len(tables) > 1 else "",
        "notes": "附件需在官網頁面下載。" if "downloadFile" in r.text else "",
        "source_url": url,
    }


# ─────────────────────────────────────────────────────────────
# 財政部（ttc.mof.gov.tw，JSON API）
# ─────────────────────────────────────────────────────────────

_MOF_TRAILER = re.compile(r"（([^（）]*?號[令函]?)）\s*$")
_MOF_DATE = re.compile(r"(\d{2,3})/(\d{1,2})/(\d{1,2})")


def _mof_stamp(body: str) -> dict:
    """法令彙編的字號與日期只寫在內文最後的括號，例如（財政部83/02/28台財稅第831585153號函）；早期的沒有日期。"""
    m = _MOF_TRAILER.search(body)
    d = _MOF_DATE.search(m.group(1)) if m else None
    return {"doc_number": _fold(m.group(1)) if m else "", "date": _date("/".join(d.groups())) if d else ""}


def _mof_params(function_id: str, **params) -> dict:
    return {"FunctionID": function_id, **{f"ObjParams[{k}]": v for k, v in params.items()}}


async def _mof_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    out = []
    # 1. 法令彙編（依稅法條次整理的現行有效函釋；無日期欄位，不支援日期篩選）
    content, extra = q.keyword, {}
    if q.number_digits:
        content, extra = (q.keyword or q.number_digits), (
            {"Operator01": "1", "Content01": q.number_digits} if q.keyword else {}
        )
    r = await http.post(f"{MOF_BASE}/Api/GetData", data=_mof_params(
        "FB10001", TaxAct="請選擇", TaxVer="請選擇", Chapter="請選擇", Article="請選擇", Content=content,
        Operator01=extra.get("Operator01", "0"), Content01=extra.get("Content01", ""), Operator02="0", Content02="",
        start=(q.page - 1) * PAGE_SIZE, length=PAGE_SIZE, orderColumn="", orderDir="",
    ))
    r.raise_for_status()
    rows = r.json().get("Data", {}).get("Table", []) or []
    items = []
    for row in rows:
        body = (row.get("Content") or "").strip()
        items.append({
            "id": f"mof:fb:{row['TaxSN']}", "agency": "財政部", "category": "稅務法令彙編",
            **_mof_stamp(body),
            "summary": re.sub(r"^[一二三四五六七八九十]*\d*", "", row.get("Title") or "").strip(),
            "law": f"{row.get('TaxAct', '')} {row.get('Article', '')}（{row.get('Part', '').strip()}）".strip(),
        })
    total = rows[0].get("TotalCount", len(rows)) if rows else 0
    note = {"note": "法令彙編沒有發文日期欄位，日期篩選未套用"} if q.start or q.end else {}
    out.append(_group("財政部", "稅務法令彙編", total, items, q.page * PAGE_SIZE < total, **note))

    # 2. 新頒令釋（法令彙編出版後發布的令釋；一次回傳全部，本機分頁）
    r = await http.post(f"{MOF_BASE}/Api/GetData", data=_mof_params(
        "FF10001", DateStart=_date(q.start) if q.start else "1911-01-01",
        DateEnd=_date(q.end) if q.end else date.today().isoformat(),
        SearchContent1=q.keyword, SearchContent2="", SearchContent3="", Condition1="0", Condition2="0",
        LawClass1="", LawClass2="", Law1="", Law2="", Lawno1="", Lawno2="",
    ))
    r.raise_for_status()
    rows = [
        x for x in r.json().get("Data", {}).get("Table", []) or []
        if not q.number_digits or q.number_digits in (x.get("DocNum") or "")
    ]
    rows.sort(key=lambda x: x.get("PosterDateRaw", ""), reverse=True)
    page_rows, more = _paged(rows, q.page)
    out.append(_group("財政部", "新頒令釋", len(rows), [{
        "id": f"mof:ff:{x['Serno']}", "agency": "財政部", "category": "新頒令釋",
        "doc_number": x.get("CombinedField", ""), "date": _date(x.get("PosterDateRaw", "")),
        "summary": x.get("Subject", ""),
    } for x in page_rows], more))
    return out


async def _mof_get(http: httpx.AsyncClient, native_id: str) -> dict:
    kind, _, key = native_id.partition(":")
    if kind == "fb":
        r = await http.post(f"{MOF_BASE}/Api/PostData", data=_mof_params("FB12001", TaxSN=key))
        r.raise_for_status()
        rows = r.json().get("Data", {}).get("Table", []) or []
        if not rows:
            raise LookupError(native_id)
        row = rows[0]
        body = (row.get("Content") or "").strip()
        return {
            "agency": "財政部", **_mof_stamp(body),
            "summary": re.sub(r"^[一二三四五六七八九十]*\d*", "", row.get("Title") or "").strip(),
            "full_text": body,
            "related_laws": [f"{row.get('TaxAct', '')} {row.get('Article', '')}".strip()],
            "law_text": (row.get("ArticleContent") or "").strip(),
            "notes": f"財政部{row.get('Part', '').strip()}{row.get('TaxAct', '')}法令彙編",
            "source_url": f"{MOF_BASE}/FB/FB120/{key}",
        }
    if kind == "ff":
        r = await http.post(f"{MOF_BASE}/Api/GetData", data=_mof_params("FF20001", Serno=key))
        r.raise_for_status()
        rows = r.json().get("Data", {}).get("Table", []) or []
        if not rows:
            raise LookupError(native_id)
        row = rows[0]
        return {
            "agency": row.get("Dept", "財政部"),
            "doc_number": f"{row.get('DocTitle', '')}第{row.get('DocNum', '')}號",
            "date": _date(row.get("PosterDate", "")),
            "summary": row.get("Subject", ""),
            "full_text": _html_text(row.get("DetailContent", "")),
            "related_laws": [f"{x.get('Law', '')} 第 {x.get('Lawno', '')} 條" for x in rows if x.get("Law")],
            "source_url": f"{MOF_BASE}/FF/FF200?Serno={key}",
        }
    raise LookupError(native_id)


# ─────────────────────────────────────────────────────────────
# 經濟部商業發展署（gcis.nat.gov.tw，JSON API）
# ─────────────────────────────────────────────────────────────

def _slash(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}/{yyyymmdd[4:6]}/{yyyymmdd[6:]}" if yyyymmdd else ""


async def _gcis_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    if q.number:
        return [_group("經濟部商業發展署", "商工行政法規函釋", 0, [], False, note="此來源不支援以發文字號查詢")]
    words = q.keyword.split() or [""]
    params = [("keyword", w) for w in words] + [("andOr", "0")]
    if q.start:
        params.append(("startDate", _slash(q.start)))
    if q.end:
        params.append(("endDate", _slash(q.end)))
    r = await http.post(GCIS_BASE + "getSearchConstructionView", params=params, json={})
    r.raise_for_status()
    rows = sorted(r.json() or [], key=lambda x: x.get("pmgDate", ""), reverse=True)
    page_rows, more = _paged(rows, q.page)
    return [_group("經濟部商業發展署", "商工行政法規函釋", len(rows), [{
        "id": f"gcis:{x['consCd']}", "agency": "經濟部", "category": "商工行政法規函釋",
        "doc_number": "", "date": _date(x.get("pmgDate", "")), "summary": _html_text(x.get("consSmy", "")),
    } for x in page_rows], more)]


async def _gcis_get(http: httpx.AsyncClient, native_id: str) -> dict:
    r = await http.post(GCIS_BASE + "getElawConstructionDetail", params={"consCd": native_id}, json={})
    r.raise_for_status()
    rows = (r.json() or {}).get("dataList") or []
    if not rows:
        raise LookupError(native_id)
    x = rows[0]
    issued = x.get("pmgDate") or ""
    if x.get("year"):
        issued = f"{x['year']}.{x.get('month', '')}.{x.get('day', '')}"
    return {
        "agency": "經濟部", "doc_number": x.get("consNo", ""), "date": _date(issued),
        "summary": _html_text(x.get("consSmy", "")),
        "full_text": _html_text(x.get("consContent", "")),
        "related_laws": [y.get("fullLawName", "") for y in x.get("refELawList") or []],
        "notes": x.get("aboDesc") or "",
        "source_url": "https://gcis.nat.gov.tw/elaw/",
    }


# ─────────────────────────────────────────────────────────────
# 內政部戶政司（www.ris.gov.tw）：DataTables API + CSRF；全文是 DOCX / ODT 附檔
# ─────────────────────────────────────────────────────────────

def _roc7(yyyymmdd: str) -> str:
    return f"{int(yyyymmdd[:4]) - 1911:03d}{yyyymmdd[4:]}" if yyyymmdd else ""


async def _ris_query(s: httpx.AsyncClient, q: Query, start: int, length: int) -> dict:
    main = await s.get(f"{RIS_BASE}/toMain")
    main.raise_for_status()
    token = re.search(r'name="_csrf"\s+content="([^"]+)"', main.text)
    if not token:
        raise ValueError("戶政司查詢頁找不到 CSRF token")
    r = await s.post(f"{RIS_BASE}/query/list", headers={"X-CSRF-TOKEN": token.group(1)}, data={
        "draw": 1, "start": start, "length": length, "index_content": q.keyword,
        "s_date": _roc7(q.start), "e_date": _roc7(q.end), "posting_number_id": "",
        "posting_number": q.number_digits, "law_type": "", "law_number": "",
    })
    r.raise_for_status()
    return r.json()


def _ris_item(x: dict) -> dict:
    return {
        "id": f"ris:{x.get('v4', '')}", "agency": "內政部", "category": "戶政法令解釋",
        "doc_number": f"{x.get('v3', '')}{x.get('v4', '')}號",
        "date": _date(x.get("v1", "")), "summary": (x.get("v6") or "").strip(),
    }


async def _ris_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    async with _session() as s:
        data = await _ris_query(s, q, (q.page - 1) * PAGE_SIZE, PAGE_SIZE)
    filtered = data.get("recordsFiltered")  # 0 是合法的「查無資料」，不能退回全部筆數
    total = int(filtered if filtered is not None else data.get("recordsTotal") or 0)
    items = [_ris_item(x) for x in data.get("data") or [] if x.get("v4")]
    return [_group("內政部戶政司", "戶政法令解釋", total, items, q.page * PAGE_SIZE < total)]


def _office_text(blob: bytes) -> str:
    """DOCX / ODT（皆為 zip）→ 純文字，只用標準庫。"""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = z.namelist()
        if "word/document.xml" in names:
            xml, para, run = z.read("word/document.xml").decode("utf-8"), r"</w:p>", r"<w:t[^>]*>([^<]*)</w:t>"
        elif "content.xml" in names:
            xml, para, run = z.read("content.xml").decode("utf-8"), r"</text:p>|</text:h>", r">([^<]+)<"
        else:
            return ""
    lines = ["".join(re.findall(run, p)) for p in re.split(para, xml)]
    return html.unescape("\n".join(l.strip() for l in lines if l.strip()))


async def _ris_get(http: httpx.AsyncClient, native_id: str) -> dict:
    async with _session() as s:
        data = await _ris_query(s, Query(number=native_id), 0, 10)
        row = next((x for x in data.get("data") or [] if x.get("v4") == native_id), None)
        if row is None:
            raise LookupError(native_id)
        full, notes = "", ""
        if row.get("v2"):
            f = await s.get(f"{RIS_BASE}/getFile", params={"fileId": row["v2"]})
            if f.status_code == 200 and f.content[:2] == b"PK":
                full = _office_text(f.content)
        if not full:
            notes = "官網未提供此函全文檔，僅有要旨。"
    item = _ris_item(row)
    return {
        "agency": "內政部", "doc_number": item["doc_number"], "date": item["date"], "summary": item["summary"],
        "full_text": full,
        "related_laws": [f"{x.get('name', '')} 第 {x.get('legis', '')}" for x in row.get("list") or []],
        "notes": notes,
        "source_url": f"{RIS_BASE}/toMain",
    }


# ─────────────────────────────────────────────────────────────
# 整份下載、在本機比對的來源（國土管理署、智慧局）：官方沒有站內搜尋 API，只有全量清單
# ─────────────────────────────────────────────────────────────

class _Snapshot:
    """官方全量清單的本機副本：存在使用者資料目錄，超過 refresh 秒才重抓；重抓失敗沿用舊副本。"""

    def __init__(self, url: str, filename: str, parse, min_rows: int, refresh: int = 7 * 86400):
        self.url, self.path, self.parse, self.min_rows, self.refresh = url, USER_DATA_DIR / filename, parse, min_rows, refresh
        self.rows: list[dict] | None = None
        self.loaded_mtime = 0.0
        self.lock = asyncio.Lock()

    async def load(self, http: httpx.AsyncClient) -> list[dict]:
        async with self.lock:
            fresh = self.path.exists() and time.time() - self.path.stat().st_mtime < self.refresh
            if not fresh:
                try:
                    r = await http.get(self.url, timeout=180.0)
                    r.raise_for_status()
                    rows = self.parse(r.content)
                    if len(rows) < self.min_rows:
                        raise ValueError(f"{self.url} 只有 {len(rows)} 筆，不覆蓋本機副本")
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    # 同一使用者可能同時開多個 MCP 程序，暫存檔要各自獨立，最後原子替換
                    fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
                    with os.fdopen(fd, "wb") as f:
                        f.write(r.content)
                    os.replace(tmp, self.path)
                except (httpx.HTTPError, ValueError, ElementTree.ParseError) as e:  # 維護頁、壞檔
                    if not self.path.exists():
                        raise
                    logger.warning("清單更新失敗，沿用本機副本 %s：%s", self.path.name, e)
            if self.rows is None or self.loaded_mtime != self.path.stat().st_mtime:
                self.rows = self.parse(self.path.read_bytes())
                self.rows.sort(key=lambda x: x["date"], reverse=True)
                self.loaded_mtime = self.path.stat().st_mtime
            return self.rows

    async def search(self, http: httpx.AsyncClient, q: Query, source: str, category: str) -> list[dict]:
        words = q.keyword.split()
        rows = [
            x for x in await self.load(http)
            if all(w in x["summary"] or w in x["_text"] for w in words)
            and (not q.number_digits or q.number_digits in x["doc_number"])
            and _in_range(x["date"], q)
        ]
        page_rows, more = _paged(rows, q.page)
        return [_group(source, category, len(rows), [{k: v for k, v in x.items() if k != "_text"} for x in page_rows], more)]

    async def get(self, http: httpx.AsyncClient, item_id: str) -> dict:
        row = next((x for x in await self.load(http) if x["id"] == item_id), None)
        if row is None:
            raise LookupError(item_id)
        return {**{k: v for k, v in row.items() if k not in ("id", "_text")}, "full_text": row["_text"]}


_NLMA_HEAD = re.compile(r"^(?P<agency>.*?)(?P<kind>函|令)\s*(?P<date>\d{2,3}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2})\s*\.?\s*(?P<no>.*?號)")


def _nlma_parse(blob: bytes) -> list[dict]:
    out = []
    for x in json.loads(blob):
        text = _html_text(x.get("content", ""))
        m = _NLMA_HEAD.match(text.split("\n", 1)[0].replace("\u3000", " "))
        out.append({
            "id": f"nlma:{x['id']}", "agency": m.group("agency").strip() if m else "內政部國土管理署",
            "category": f"解釋函彙編（{(x.get('efa_unit') or {}).get('name', '')}）",
            "doc_number": _fold(m.group("no")) if m else "",
            "date": _date(m.group("date")) if m else _date((x.get("publish_up") or "")[:10]),
            "summary": (x.get("title") or "").strip(),
            "source_url": NLMA_PAGE_URL.format(x["id"]),
            "_text": text,
        })
    return out


def _tipo_parse(blob: bytes) -> list[dict]:
    out, seen = [], set()
    for x in ElementTree.fromstring(blob):
        number = (x.findtext("令函案號") or "").strip()
        text = _html_text(x.findtext("令函要旨") or "")
        # 少數案號重複；id 用案號＋日期（仍重複再加內容雜湊），不依清單順序，官方檔重排也不會錯配
        key = f"{number}@{x.findtext('發布日期') or ''}"
        if key in seen:
            key += "#" + hashlib.sha1(text.encode()).hexdigest()[:8]
        seen.add(key)
        out.append({
            "id": f"tipo:{key}", "agency": "經濟部智慧財產局", "category": "著作權解釋令函",
            "doc_number": number, "date": _date(x.findtext("發布日期") or ""),
            "summary": text.split("\n", 1)[0][:200],
            "source_url": TIPO_XML_URL,  # 開放資料沒有個別頁面
            "_text": text,
        })
    return out


_NLMA = _Snapshot(NLMA_LIST_URL, "nlma_interpcomp.json", _nlma_parse, min_rows=1000)
_TIPO = _Snapshot(TIPO_XML_URL, "tipo_copyright.xml", _tipo_parse, min_rows=1000, refresh=86400)


async def _nlma_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    return await _NLMA.search(http, q, "內政部國土管理署", "解釋函彙編")


async def _nlma_get(http: httpx.AsyncClient, native_id: str) -> dict:
    return await _NLMA.get(http, f"nlma:{native_id}")


async def _tipo_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    return await _TIPO.search(http, q, "經濟部智慧財產局", "著作權解釋令函")


async def _tipo_get(http: httpx.AsyncClient, native_id: str) -> dict:
    return await _TIPO.get(http, f"tipo:{native_id}")


# ─────────────────────────────────────────────────────────────
# 行政院公報（gazette.nat.gov.tw）：解釋性規定及裁量基準（行政程序法第 159 條第 2 項第 2 款）
# ─────────────────────────────────────────────────────────────

_GAZETTE_PAGE = 10
# 公報標題用機關全銜，簡稱要先換成全銜才比對得到
_GAZETTE_AGENCY_NAMES = {
    "金管會": "金融監督管理委員會", "公平會": "公平交易委員會", "通傳會": "國家通訊傳播委員會",
    "NCC": "國家通訊傳播委員會", "國發會": "國家發展委員會", "國科會": "國家科學及技術委員會",
    "原民會": "原住民族委員會", "客委會": "客家委員會", "海委會": "海洋委員會", "陸委會": "大陸委員會",
    "僑委會": "僑務委員會", "退輔會": "國軍退除役官兵輔導委員會", "主計總處": "行政院主計總處",
    "人事總處": "行政院人事行政總處", "央行": "中央銀行", "環保署": "環境部", "農委會": "農業部",
    "數發部": "數位發展部", "衛福部": "衛生福利部", "勞委會": "勞動部",
}


async def _gazette_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    """指定多個機關時每個機關各查一次（站方只接受三組條件）。"""
    return [g for name in q.agency_names or [""] for g in await _gazette_search_one(q, name)]


async def _gazette_search_one(q: Query, agency_name: str) -> list[dict]:
    # 站方要求「關鍵字、欄位、邏輯」三個一組依序出現、共三組；httpx 會把同名參數併在一起，所以自己組 query string
    # 第三組：有發文字號就比對字號，否則限定標題含「釋」（核釋、釋示、解釋）——這個類型也收一般要點的
    # 訂定修正，不篩會淹沒函釋
    agency_title = _GAZETTE_AGENCY_NAMES.get(agency_name, agency_name)
    third = (q.number_digits, "gazetteid") if q.number_digits else ("釋", "title")
    terms = [(q.keyword, "text"), (agency_title, "title"), third]
    params = [("action", "doQuery"), ("styleL", "2"), ("styleS", "1")]
    for word, scope in terms:
        params += [("keywords", word), ("fields", scope), ("logics", "AND")]
    if q.start:
        params.append(("pubdateStart", _date(q.start)))
    if q.end:
        params.append(("pubdateEnd", _date(q.end)))
    async with _session() as s:  # 翻頁靠 session 記住查詢條件
        r = await s.get(GAZETTE_BASE + "advancedSearchResult.do?" + urlencode(params))
        if q.page > 1:
            r = await s.get(GAZETTE_BASE + "advancedSearchResult.do",
                            params={"action": "doChangePage", "pageNum": q.page})
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    m = re.search(r"共\s*([\d,]+)\s*筆資料", soup.get_text())
    items = []
    for block in soup.select("div.List_Item"):
        a = block.select_one('a[href^="detail.do?metaid="]')
        if not a:
            continue
        title = a.get("title") or _text(a)
        agency, sep, subject = title.partition("：")
        d = re.search(r"\d{4}-\d{2}-\d{2}", _text(block))
        metaid = re.search(r"metaid=(\d+)", a["href"]).group(1)
        items.append({
            "id": f"gazette:{metaid}",
            "agency": re.sub(r"(令|函|公告)$", "", agency) if sep else "",
            "category": "解釋性規定（行政院公報）", "doc_number": "",
            "date": d.group() if d else "", "summary": subject if sep else title,
        })
    total = int(m.group(1).replace(",", "")) if m else len(items)
    category = f"解釋性規定及裁量基準（{agency_name}）" if agency_name else "解釋性規定及裁量基準"
    return [_group("行政院公報", category, total, items, q.page * _GAZETTE_PAGE < total)]


async def _gazette_get(http: httpx.AsyncClient, native_id: str) -> dict:
    url = f"{GAZETTE_BASE}detail.do?metaid={native_id}&log=detailLog"
    r = await http.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    pdf = re.search(r"/EG_FileManager/[^\"'\s&]+/Eg\.pdf", r.text)
    if not pdf:
        raise LookupError(native_id)
    pdf_url = str(r.url.join(pdf.group()))
    title = (soup.title.get_text(strip=True) if soup.title else "").removeprefix("行政院公報資訊網").strip(" -|")
    body = await http.get(pdf_url.replace("Eg.pdf", "Eg.htm"))
    full = ""
    if body.status_code == 200:
        page = BeautifulSoup(body.content, "html.parser")
        lines = [l for l in page.get_text("\n", strip=True).split("\n")]
        # 去掉公報網站固定的頁首（站名、說明）與頁尾（TOP 以下）
        start = next((i for i, l in enumerate(lines) if l.startswith("對於本網站")), -1) + 1
        end = next((i for i, l in enumerate(lines) if l == "TOP"), len(lines))
        full = "\n".join(lines[start:end])
    head = full.split("\n")[:6]
    issued = next((l for l in head if re.match(r"中華民國\d+年\d+月\d+日", l)), "")
    number = next((l for l in head if _DOC_NUMBER.fullmatch(l)), "")
    agency, _, subject = title.partition("：")
    return {
        "agency": re.sub(r"(令|函|公告)$", "", agency),
        "doc_number": number,
        "date": _date(issued),
        "summary": subject or title,
        "full_text": full,
        "attachments": [{"title": "公報 PDF", "url": pdf_url}],
        "source_url": url,
    }


# ─────────────────────────────────────────────────────────────
# 司法院法學資料檢索系統（跨機關行政函釋）
# ─────────────────────────────────────────────────────────────

async def _fint_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    year_from = int(q.start[:4]) - 1911 if q.start else 0
    year_to = int(q.end[:4]) - 1911 if q.end else 0
    # 站方只有一個全文關鍵字欄；有發文字號時用字號查（比關鍵字精確），關鍵字不套用並註明
    (group,) = await fint.search(q.number_digits or q.keyword, ["行政函釋"], year_from, year_to, q.page)
    items = []
    for x in group["items"]:
        agency, _, number = x["title"].partition(" ")
        items.append({
            "id": f"fint:{x['id']}", "agency": agency, "category": "行政函釋（司法院法學資料檢索）",
            "doc_number": _fold(number), "date": x["date"], "summary": x["summary"],
        })
    note = {"note": "此來源以發文字號查詢，關鍵字未套用"} if q.number_digits and q.keyword else {}
    return [_group("司法院法學資料檢索系統", "行政函釋", group["total"], items, group["has_more"], **note)]


async def _fint_get(http: httpx.AsyncClient, native_id: str) -> dict:
    d = await fint.get(http, native_id)
    f = d["fields"]
    return {
        "agency": f.get("發文單位", ""), "doc_number": _fold(f.get("發文字號", "")),
        "date": _date(f.get("發文日期", "")), "summary": f.get("要旨", ""),
        "full_text": _RECIPIENTS.sub("", d["full_text"]), "related_laws": d["related_laws"],
        "notes": f.get("編註", ""),  # 例如「停止適用」，引用前必看
        "data_source": f.get("資料來源", ""), "attachments": d["attachments"], "source_url": d["source_url"],
    }


# ─────────────────────────────────────────────────────────────
# 對外介面
# ─────────────────────────────────────────────────────────────

# 來源代碼 → (名稱, 可用來指定的機關名／別名, search, get)；順序即去重時的優先序（機關自己的系統優先）
SOURCES = {
    "moj": ("法務部", ("法務部",), _moj_search, _moj_get),
    "mol": ("勞動部", ("勞動部", "勞委會", "行政院勞工委員會"), _mol_search, _mol_get),
    "pcc": ("工程會", ("工程會", "公共工程委員會", "行政院公共工程委員會"), _pcc_search, _pcc_get),
    "mof": ("財政部", ("財政部", "賦稅署"), _mof_search, _mof_get),
    "gcis": ("經濟部商業發展署", ("經濟部", "商業發展署", "商業司"), _gcis_search, _gcis_get),
    "ris": ("內政部戶政司", ("內政部", "戶政司"), _ris_search, _ris_get),
    "nlma": ("內政部國土管理署", ("內政部", "國土管理署", "營建署"), _nlma_search, _nlma_get),
    "mohw": ("衛生福利部", ("衛生福利部", "衛福部"), _mohw_search, _mohw_get),
    "tipo": ("經濟部智慧財產局", ("經濟部", "智慧財產局", "智慧局", "著作權"), _tipo_search, _tipo_get),
    "fint": ("司法院法學資料檢索系統", ("司法院",), _fint_search, _fint_get),
    "gazette": ("行政院公報", ("行政院公報", "公報"), _gazette_search, _gazette_get),
}


def resolve_sources(agency: str) -> tuple[list[str], list[str]]:
    """機關名稱 → (來源代碼, 沒有專屬來源、改用行政院公報標題篩選的機關名)。空字串 = 全部來源。"""
    if not agency.strip():
        return list(SOURCES), []
    keys, others = [], []
    for name in [n for n in re.split(r"[,，、\s]+", agency.strip()) if n]:
        hit = [k for k, (label, aliases, *_) in SOURCES.items() if name == k or name in aliases or name == label]
        if hit:
            keys += hit
        else:
            others.append(name)
    if others:
        keys.append("gazette")
    return list(dict.fromkeys(keys)), others


def _dedupe_key(item: dict) -> str:
    """同一件函釋在不同來源的字號寫法只差空白與結尾的「令／函」；字別（如「法律」）區分不同機關的同號公文。"""
    number = re.sub(r"\s|[令函]$", "", item.get("doc_number", ""))
    return f"{item.get('date', '')}:{number}" if "字第" in number else item["id"]


class AgencyInterpretationClient:
    def __init__(self, cache: CacheDB):
        self.cache = cache
        self.http = httpx.AsyncClient(timeout=60.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True)

    async def close(self):
        await self.http.aclose()

    async def search(
        self, keyword: str, agency: str, year_from: int, year_to: int, doc_number: str, page: int
    ) -> dict:
        keys, other_agencies = resolve_sources(agency)
        today = date.today().strftime("%Y%m%d")
        # 勞動部的解釋令頁籤：迄日晚於今天就查不到任何資料，所以迄日一律截到今天
        q = Query(
            keyword=keyword.strip(),
            start=f"{year_from + 1911}0101" if year_from else "",
            end=min(f"{year_to + 1911}1231", today) if year_to else "",
            number=doc_number.strip(),
            page=page,
            agency_names=other_agencies,
        )
        params = {"tool": "agency_interpretations", "sources": keys, **q.__dict__}
        cached = await self.cache.get_search(params)
        if cached:
            return {**cached, "cached": True}

        async def run(key: str) -> list[dict]:
            label, _, search, _ = SOURCES[key]
            try:
                return await search(self.http, q)
            except Exception as e:  # 單一來源掛掉（連線、改版、壞檔）不能拖垮其他來源
                logger.warning("函釋搜尋失敗 %s: %s", key, e, exc_info=not isinstance(e, httpx.HTTPError))
                return [{"source": label, "error": f"{type(e).__name__}: {e}"}]

        groups = [g for gs in await asyncio.gather(*(run(k) for k in keys)) for g in gs]
        seen, results = set(), []
        for g in groups:  # SOURCES 順序：機關自己的系統優先，公報、司法院彙整的重複項目略過
            for item in g.get("items", []):
                key = _dedupe_key(item)
                if key not in seen:
                    seen.add(key)
                    results.append(item)
        results.sort(key=lambda i: i.get("date", ""), reverse=True)
        result = {
            "success": True,
            "keyword": keyword,
            "page": page,
            "categories": [
                {k: v for k, v in g.items() if k != "items"} | ({"returned": len(g["items"])} if "items" in g else {})
                for g in groups
            ],
            "total_count": sum(g.get("total", 0) for g in groups),
            "results": results,
            "timestamp": datetime.now().isoformat(),
        }
        if other_agencies:
            result["note"] = (
                f"{'、'.join(other_agencies)}沒有專屬的函釋系統，改查行政院公報中該機關發布的解釋性規定"
                "（以標題機關名篩選）；個別函復多半不刊登公報。"
            )
        if not any("error" in g for g in groups):
            await self.cache.set_search(params, result)
        return result

    async def get(self, interpretation_id: str) -> dict:
        key, _, native_id = interpretation_id.partition(":")
        if key not in SOURCES or not native_id:
            return error_response(
                f"函釋 id 格式錯誤：「{interpretation_id}」，請使用 search_agency_interpretations 回傳的 id",
            )
        cache_key = f"interp:{interpretation_id}"
        cached = await self.cache.get_judgment(cache_key)
        if cached:
            return {"success": True, "cached": True, **cached}
        label, _, _, get = SOURCES[key]
        try:
            data = await get(self.http, native_id)
        except LookupError:
            return error_response(f"{label}查無此函釋：{interpretation_id}")
        except (httpx.HTTPError, ValueError, KeyError, ElementTree.ParseError) as e:
            return error_response(f"{label}連線或解析失敗：{type(e).__name__}: {e}")
        full = data.get("full_text", "")
        data["full_text"], data["full_text_truncated"] = full[:MAX_FULL_TEXT], len(full) > MAX_FULL_TEXT
        data = {"id": interpretation_id, "source": label, **data}
        await self.cache.set_judgment(cache_key, data, source="agency_interpretation")
        return {"success": True, "cached": False, **data}
