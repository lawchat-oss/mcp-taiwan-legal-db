"""行政機關函釋查詢（#9）

各部會的函釋分散在各自的系統、格式各異，沒有共用 API；每個來源一組 search / get，查詢時即時抓官方網站：

| 代碼 | 來源 | 內容 |
|---|---|---|
| moj | 法務部主管法規查詢系統 mojlaw.moj.gov.tw | 行政函釋、法規諮詢意見 |
| mol | 勞動部勞動法令查詢系統 laws.mol.gov.tw | 行政函釋、解釋令 |
| pcc | 工程會政府採購法規解釋函令 planpe.pcc.gov.tw | 採購法令解釋令、函 |
| mof | 財政部各稅法令函釋檢索系統 ttc.mof.gov.tw | 稅務法令彙編、新頒令釋 |
| mof_rules | 財政部主管法規查詢系統 law-out.mof.gov.tw | 部本部與關務署、國有財產署、國庫署的核釋令 |
| customs | 財政部關務署 web.customs.gov.tw | 新頒釋函（只比對標題） |
| gcis | 經濟部商業發展署 商工行政法規 gcis.nat.gov.tw | 公司法、商業登記法等函釋 |
| moea | 經濟部主管法規查詢系統 law.moea.gov.tw | 本部解釋令 |
| bsmi | 經濟部標準檢驗局 www.bsmi.gov.tw | 解釋函令（清單一天下載一次，在本機比對） |
| ris | 內政部戶政司 www.ris.gov.tw | 戶籍、國籍、姓名等函釋 |
| nlma | 內政部國土管理署 www.nlma.gov.tw | 建築管理、都市計畫、住宅等解釋函（整份清單下載後在本機比對） |
| land | 內政部地政司 地政法令 www.land.moi.gov.tw/law | 地政解釋函（含已停止適用；robots.txt 全站禁止，只即時查詢） |
| nfa | 內政部消防署 law.nfa.gov.tw | 消防法令解釋（只能查摘要；函文是掃描 PDF 附件） |
| mohw | 衛生福利部 mohwlaw.mohw.gov.tw | 行政函釋 |
| moenv | 環境部 oaout.moenv.gov.tw | 行政函釋 |
| mocs、csptc、moex、exam | 考試院主管法規共用系統 law.exam.gov.tw | 銓敘部、保訓會、考選部、考試院行政函釋 |
| dgpa | 行政院人事行政總處 law.dgpa.gov.tw | 公務員人事法令 |
| fsc、moe、moa、moi、moc、nstc、cip、oac、ftc、mac、cec、dgbas | 各部會主管法規共用系統（law.fsc.gov.tw、edu.law.moe.gov.tw 等） | 金管會、教育部、農業部、內政部、文化部、國科會、原民會、海委會、公平會、陸委會、中選會、主計總處的行政規則（含解釋令、函） |
| mofa、vac、nusc、ndc、hakka、ocac、sports | 同上 | 外交部、退輔會、核安會、國發會、客委會、僑委會、運動部的行政規則（多為內部要點，只在 agency 指名時查；運動部遇到驗證時用瀏覽器） |
| mac_letters | 陸委會 www.mac.gov.tw | 大陸廣告規範專區的函與參考意見（指名才查） |
| ncc | NCC 法規查詢系統 ncclaw.ncc.gov.tw | 行政函釋、個別函復（指名才查；遇到 Cloudflare 時用瀏覽器） |
| motc | 交通部 motclaw.motc.gov.tw | 行政解釋（令、函、公告） |
| cbc | 中央銀行 www.law.cbc.gov.tw | 行政令函 |
| cpc | 行政院消費者保護處 www.ey.gov.tw | 消保法函釋（清單一天下載一次，在本機比對） |
| sunshine | 監察院陽光法令主題網 sunshine.cy.gov.tw | 政治獻金法、利益衝突迴避法、財產申報法函釋（清單一天下載一次） |
| tipo | 經濟部智慧財產局 www.tipo.gov.tw | 著作權解釋令函（開放資料下載後在本機比對） |
| tipo_guide | 經濟部智慧財產局 www.tipo.gov.tw | 專利審查基準（網頁版全文）、商標審查基準（PDF）；只比對標題（見 ip_guidelines） |
| taipei | 臺北市法規查詢系統 laws.gov.taipei | 臺北市政府解釋令函，及該系統收錄的中央機關函釋 |
| ntpc | 新北市法規查詢系統 web.law.ntpc.gov.tw | 新北市政府與中央機關函釋（依筆數取前 5 類） |
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
from functools import partial
from urllib.parse import parse_qsl, urlencode
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from mcp_server.cache.db import CacheDB
from mcp_server.config import USER_DATA_DIR
from mcp_server.tools import fint, ip_guidelines, public_browser, tls
from mcp_server.tools._errors import error_response

logger = logging.getLogger(__name__)

# 工程會的 WAF 會擋舊版 Chrome 的 User-Agent
USER_AGENT = fint.USER_AGENT
PAGE_SIZE = 20
STATUS_TTL = 7 * 86400  # 函釋可能事後被停止適用，全文快取不放 30 天
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


def _status(stopped: bool | None, note: str = "") -> dict:
    """官網的效力標示 → status（停止適用／適用中）與 status_note；None = 官網沒有標示，不輸出。"""
    if stopped is None:
        return {}
    return {"status": "停止適用" if stopped else "適用中", **({"status_note": note} if note else {})}


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
            fei = tr.select_one(".label-fei")  # 「廢」；官網不標「適用中」
            agency, _, doc_number = _text(a).partition(" ")
            items.append({
                "id": f"moj:{fe.group(1)}", "agency": agency, "category": label,
                "doc_number": _fold(doc_number), "date": _date(_text(tr.select_one("span"))),
                "summary": _unwrap(tr.select_one("pre").get_text()) if tr.select_one("pre") else "",
                **_status(True if fei else None),
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
    fei = soup.select_one(".label-fei")
    if fei:
        fei.extract()  # 不然字號開頭會多一個「廢」
    notes = _text(soup.select_one("#cp_content_Etr .col-td"))
    own = _OWN_STOP.search(notes)
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
        "notes": notes,
        **_status(True if fei else None, own.group() if fei and own else ""),
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
    out = []
    for tab, (mode, label) in _MOL_CATEGORIES.items():
        r = await http.get(f"{MOL_BASE}/FINT/results.aspx", params={
            "etype": "*, 002, 007", "now": 1, "lnabndn": 1, "keyword": q.keyword,
            # 只送號碼：站方字別中間有空格（「勞動條 3」），照使用者寫法送字別會查不到；送空的 N1/N2 解釋令會變 0 筆
            **({"N2": q.number_digits} if q.number_digits else {}),
            "sdate": q.start, "edate": q.end, "title": "out", "type": tab, "page": q.page,
        })
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        badges = [int(_text(b) or 0) for b in soup.select("#cph_content_ulLawCate .badge")]
        total = badges[2 if mode == "007" else 0] if len(badges) == 3 else 0
        items = []
        for row in soup.select(".fint-list > .row") if total else []:  # 本頁籤 0 筆時站方改列其他頁籤的資料
            a = row.select_one("a[id*=hlkTitle]")
            tds = row.select(".col-td")
            m = _MOL_DOC_NUMBER.match(_text(a)) if a else None
            if not m:
                continue
            items.append({
                "id": f"mol:{mode}:{m.group(1).strip()}:{m.group(2)}", "agency": "勞動部", "category": label,
                "doc_number": _fold(_text(a)), "date": _date(_text(tds[1])) if len(tds) > 1 else "",
                "summary": _unwrap(row.select_one("pre").get_text()) if row.select_one("pre") else "",
                # 查詢同時勾「現行」與「廢止」，兩者不重疊、合起來是全部，沒標「廢」即現行
                **_status(bool(row.select_one("span.fei"))),
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
    fei = soup.select_one("#cph_content_spanFeiE")
    if fei:
        fei.extract()  # 不然字號開頭會多一個「廢」
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
        **_status(bool(fei), own.group() if fei and (own := _OWN_STOP.search(_text(fields.get("編註")))) else ""),
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
            fei = value.select_one(".fei")  # 「廢」；valid 不帶 = 現行（1）＋廢止（2），沒標即現行
            if fei:
                fei.extract()  # 不然 id 與字號開頭會多一個「廢」，明細也查不到
            dm = _MOL_DOC_NUMBER.match(_text(value))
            if dm:
                items.append({
                    "id": f"mohw:{dm.group(1).strip()}:{dm.group(2)}", "agency": "衛生福利部", "category": "行政函釋",
                    "doc_number": _fold(_text(value)), "date": "", "summary": "", **_status(bool(fei)),
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
    fei = soup.select_one(".fei")
    if fei:  # 「廢」會把「發文字號：」那一行切斷：拿掉後把前後兩段文字併回一段
        parent = fei.parent
        fei.extract()
        parent.smooth()
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
        "summary": "", **_status(bool(fei)),
        "full_text": _RECIPIENTS.sub("", _unwrap(body)),
        "related_laws": [
            l for l in (re.sub(r"\s+", " ", x).strip() for x in re.split(r"\n\s*\n|\)\s*\n", laws.group(1)))
            if l
        ] if laws else [],
        "source_url": str(r.url),
    }


# ─────────────────────────────────────────────────────────────
# 主管法規共用系統（多個部會共用同一套系統）：解釋令、函收在「行政規則」類別，與一般要點混在一起
# ─────────────────────────────────────────────────────────────

# 來源代碼 → (網址, 機關[, GroupID, 沒標「廢」可否說適用中])；GroupID 是各站自己的類別編號（LawQuery.aspx 的
# chkLawTypes），多數站 2 = 行政規則。行政規則類的「現行」「已廢止」兩個篩選剛好切分全部資料，沒標「廢」即現行
_LAWSYS = {
    "hakka": ("https://law.hakka.gov.tw/", "客家委員會", "2", False),
    "ocac": ("https://law.ocac.gov.tw/law/", "僑務委員會", "2", False),
    "sports": ("https://law.sports.gov.tw/", "運動部", "2", False),
    "fsc": ("https://law.fsc.gov.tw/", "金融監督管理委員會"),
    "moe": ("https://edu.law.moe.gov.tw/", "教育部"),
    "moa": ("https://law.moa.gov.tw/", "農業部"),
    "moi": ("https://glrs.moi.gov.tw/", "內政部"),
    "moc": ("https://law.moc.gov.tw/", "文化部"),
    "nstc": ("https://law.nstc.gov.tw/", "國家科學及技術委員會"),
    "cip": ("https://law.cip.gov.tw/", "原住民族委員會"),
    "oac": ("https://law.oac.gov.tw/", "海洋委員會"),
    "ftc": ("https://law.ftc.gov.tw/law/", "公平交易委員會"),  # 官網「行政解釋」（公研釋）也收在這裡
    "mof_rules": ("https://law-out.mof.gov.tw/", "財政部"),  # 含關務署、國有財產署、國庫署的核釋令
    "moea": ("https://law.moea.gov.tw/", "經濟部"),  # 含水利署、標準檢驗局、國際貿易署等的解釋令
    "mac": ("https://law.mac.gov.tw/", "大陸委員會"),
    # 6 = 行政函釋：停止適用的不標「廢」，而是在標題後加「（本會…號函停止適用）」
    "cec": ("https://law.cec.gov.tw/", "中央選舉委員會", "6,2", False),
    "dgbas": ("https://law.dgbas.gov.tw/", "行政院主計總處", "5,2", False),  # 5 = 其他令函（不標「廢」）
    # 以下多為機關內部作業要點，只在指名機關時查（見 _NAMED_ONLY）
    "mofa": ("https://law.mofa.gov.tw/", "外交部"),
    "vac": ("https://law.vac.gov.tw/vaclaw/", "國軍退除役官兵輔導委員會"),
    "nusc": ("https://erss.nusc.gov.tw/law/", "核能安全委員會", "5,2", False),  # 5 = 行政指導
    "ndc": ("https://theme.ndc.gov.tw/lawout/", "國家發展委員會"),
}
_LAWSYS_CATEGORY = "行政規則（含解釋令、函）"


# 編註裡講「這件」被停掉的句子；「依本筆資料，原…函…停止適用」講的是這件停掉的其他函，不能當成這件的狀態
_OWN_STOP = re.compile(r"(?<!依)本筆資料，依據[^。]*")
_TITLE_STOP = re.compile(r"[（(]([^（()）]*停止適用)[)）]\s*$")  # 標題後手打的「（本會…號函停止適用）」


def _check_id(native_id: str, pattern: str = r"[0-9]+") -> str:
    """原站識別碼放進網址前先驗證格式。"""
    if not re.fullmatch(pattern, native_id):
        raise LookupError(native_id)
    return native_id


def _plain(el) -> str:
    """不在標示關鍵字的 <mark>／<strong> 前後插空白（_text 會插）。"""
    return re.sub(r"\s+", " ", el.get_text()).strip() if el else ""


async def _lawsys_request(key: str, http, url: str, **kwargs):
    if key == "hakka":
        async with httpx.AsyncClient(timeout=60, headers=http.headers, follow_redirects=True,
                                     verify=tls.context_with(tls.TWCA_SSL_CA_2023)) as own:
            r = await own.get(url, **kwargs)
    elif key == "sports":
        return await public_browser.get(http, url, selector=".tab-result, .tab-edit, [id$=lblMsg]", **kwargs)
    else:
        r = await http.get(url, **kwargs)
    r.raise_for_status()
    if key in ("hakka", "ocac") and not public_browser._ready(r.text, ".tab-result, .tab-edit, [id$=lblMsg]"):
        raise RuntimeError("官網未回傳法規查詢內容，可能仍在驗證頁")
    return r


async def _lawsys_search(key: str, http: httpx.AsyncClient, q: Query) -> list[dict]:
    base, agency, group, affirm = (*_LAWSYS[key], "2", True)[:4]
    params = {"NLawTypeID": "all", "GroupID": group, "KW": q.keyword, "name": "1", "content": "1", "page": q.page}
    if q.number_digits:
        params["LNumber"] = q.number_digits
    if q.start:
        params["StartDate"] = q.start
    if q.end:
        params["EndDate"] = q.end
    r = await _lawsys_request(key, http, base + "LawResult.aspx", params=params)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    m = re.search(r"法規類別 全部 (\d+)", soup.get_text(" ", strip=True))
    items = []
    for tr in soup.select("table.tab-result tr"):
        tds = tr.find_all("td")
        a = tds[2].select_one("a[id$=hlkLawName]") if len(tds) >= 4 else None
        rid = re.search(r"id=(\w+)", a["href"]) if a else None
        if rid:
            fei = tds[2].select_one("span.label-fei")  # 「廢」「廢/停」
            title = _plain(a)
            stop = _TITLE_STOP.search(title)
            items.append({
                "id": f"{key}:{rid.group(1)}", "agency": agency, "category": _LAWSYS_CATEGORY,
                "doc_number": "", "date": _date(_text(tds[1])), "summary": title,
                **_status(True if fei or stop else (False if affirm else None),
                          _text(fei) + "（列表日期可能是廢止日）" if fei else (stop.group(1) if stop else "")),
            })
    total = int(m.group(1)) if m else len(items)
    return [_group(agency, _LAWSYS_CATEGORY, total, items, q.page * 10 < total)]


async def _lawsys_get(key: str, http: httpx.AsyncClient, native_id: str) -> dict:
    base, agency, _, affirm = (*_LAWSYS[key], "2", True)[:4]
    url = f"{base}LawContent.aspx?id={_check_id(native_id, '[A-Z]+[0-9]+')}"
    r = await _lawsys_request(key, http, url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    fei = soup.select_one("span.label-fei")
    label = fei.extract().get_text(strip=True) if fei else ""  # 不然法規名稱開頭會多一個「廢」
    fields = {re.sub(r"\s", "", _text(tr.th)).rstrip("："): _text(tr.td)
              for tr in soup.select("table.tab-edit tr") if tr.th and tr.td}
    body = soup.select_one("#ctl00_cp_content_divContent, #ctl00_cp_content_divLawContent50")
    if not fields or body is None:
        raise LookupError(native_id)
    doc_number = _fold(fields.get("發文字號", ""))
    stopped_on = fields.get("廢止/停止適用日期") or fields.get("廢止日期")
    stop = _TITLE_STOP.search(fields.get("法規名稱", ""))
    note = label or (stop.group(1) if stop else "")
    if stopped_on:  # 有廢止日期時，發文字號欄是廢止令的字號，不是這件函釋的
        act = "廢止" if label == "廢" else "廢止或停止適用"
        note, doc_number = f"{_date(stopped_on)} {act}" + (f"（{doc_number}）" if doc_number else ""), ""
    return {
        "agency": agency, "doc_number": doc_number,
        "date": _date(fields.get("公發布日", "")), "summary": fields.get("法規名稱", ""),
        "full_text": _unwrap(body.get_text("\n")),
        "notes": fields.get("法規體系", ""),
        **_status(True if fei or stop else (False if affirm else None), note),
        "source_url": url,
    }


# ─────────────────────────────────────────────────────────────
# 主管法規共用系統的「行政函釋」子系統（環境部、考試院）
# ─────────────────────────────────────────────────────────────

EXAM_BASE = "https://law.exam.gov.tw/"
# 來源代碼 → (網址, 機關, 額外查詢參數)。考試院的系統收院本部與各部會，清單不列機關，以機關分類 Ncid 分開查
_EXEC = {
    "moenv": ("https://oaout.moenv.gov.tw/law/", "環境部", {}),
    "mocs": (EXAM_BASE, "銓敘部", {"Ncid": "03"}),
    "csptc": (EXAM_BASE, "公務人員保障暨培訓委員會", {"Ncid": "04"}),
    "moex": (EXAM_BASE, "考選部", {"Ncid": "02"}),
    "exam": (EXAM_BASE, "考試院", {"Ncid": "01"}),
}


async def _exec_search(key: str, http: httpx.AsyncClient, q: Query) -> list[dict]:
    base, agency, extra = _EXEC[key]
    params = {"ELType": "6", "KW": q.keyword, "page": q.page, **extra}  # 沒有 ELType 會被導到錯誤頁
    if q.number_digits:
        params["LNumber"] = q.number_digits
    if q.start:
        params["StartDate"] = q.start  # 表單顯示民國年，網址參數是西元
    if q.end:
        params["EndDate"] = q.end
    r = await http.get(base + "ExecutiveResult.aspx", params=params)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for title in soup.select("div[id$=_divTitle]"):
        td = title.parent
        a = td.select_one("a[href*='ExecutiveData.aspx']")  # 環境部連結在標題、考試院在發文字號
        rid = re.search(r"[?&]id=(\d+)", a.get("href", "")) if a else None
        if not rid:
            continue
        fei = title.select_one(".label-fei")  # 「廢/停」；站方效力只有現行有效、停止適用兩種，未帶篩選＝兩種都列
        if fei:
            fei.extract()
        kv = {
            re.sub(r"\s", "", _text(d.select_one(".co-th"))).rstrip("："): d.select_one(".co-td")
            for d in td.find_all("div") if d.select_one(".co-th")
        }
        items.append({
            "id": f"{key}:{rid.group(1)}", "agency": agency, "category": "行政函釋",
            "doc_number": _fold(_text(kv.get("發文字號"))), "date": _date(_text(kv.get("發文日期"))),
            "summary": _plain(kv.get("標題")), **_status(bool(fei)),
        })
    m = re.search(r"共\s*(\d+)\s*筆", soup.get_text())  # 只有一頁時不顯示總筆數
    total = int(m.group(1)) if m else len(items)
    return [_group(agency, "行政函釋", total, items, q.page * 10 < total)]


async def _exec_get(key: str, http: httpx.AsyncClient, native_id: str) -> dict:
    base, agency, _ = _EXEC[key]
    return await _exec_detail(http, f"{base}ExecutiveData.aspx?id={_check_id(native_id)}&type=2", agency)


async def _exec_detail(http: httpx.AsyncClient, url: str, agency: str, affirm: bool = True) -> dict:
    """明細頁（不帶 type=2 只回空殼）；人事總處的 LegalData.aspx 是同一套版面。affirm = 沒標示時可說「適用中」。"""
    r = await http.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    fei = soup.select_one("table.tab-edit .label-fei")
    if fei:
        fei.extract()
    stopped_by = _text(soup.select_one("[id$=h3ValidStatus2]"))  # 「本則資料依 113.01.01 …號函 停止適用」
    rows = {re.sub(r"\s", "", _text(tr.th)).rstrip("："): tr.td  # 欄名有全形空白，如「內　　容」
            for tr in soup.select("table.tab-edit tr") if tr.th and tr.td}
    if "發文字號" not in rows:
        raise LookupError(url)
    return {
        "agency": _text(rows.get("發文機關")) or agency, "doc_number": _fold(_text(rows["發文字號"])),
        "date": _date(_text(rows.get("發文日期"))), "summary": _text(rows.get("標題") or rows.get("要旨")),
        "full_text": _RECIPIENTS.sub("", _unwrap(rows["內容"].get_text("\n"))) if "內容" in rows else "",
        "related_laws": [x for x in rows["相關法規"].get_text("\n", strip=True).split("\n") if x] if "相關法規" in rows else [],
        "notes": _text(rows.get("單位業務分類") or rows.get("機關分類") or rows.get("單位分類")),
        **_status(True if fei or stopped_by or rows.get("停止適用日期") else (False if affirm else None), stopped_by or (
            f"停止適用日期 {_date(_text(rows['停止適用日期']))}" if rows.get("停止適用日期") else "")),
        "attachments": [
            {"title": _text(a), "url": str(r.url.join(a["href"]))}
            for a in (rows["圖表附件"].select("a[href]") if "圖表附件" in rows else [])
        ],
        "source_url": url,
    }


# 行政院人事行政總處（law.dgpa.gov.tw）：同一套系統的「人事法令解釋」版（LegalResult / LegalData），清單每頁 10 筆
DGPA_BASE = "https://law.dgpa.gov.tw/"


async def _dgpa_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    params = {"KW": q.keyword, "page": q.page}
    if q.number_digits:
        params["LNumber"] = q.number_digits
    if q.start:
        params["StartDate"] = q.start  # 西元
    if q.end:
        params["EndDate"] = q.end
    r = await http.get(DGPA_BASE + "LegalResult.aspx", params=params)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for a in soup.select("a[href*='LegalData.aspx']"):
        rid, td = re.search(r"[?&]id=(\d+)", a["href"]), a.find_parent("td")
        if not rid or td is None:
            continue
        agency, _, number = _text(a).partition(" ")
        day = re.search(r"\d{2,3}\.\d{1,2}\.\d{1,2}", _text(td))
        items.append({
            "id": f"dgpa:{rid.group(1)}", "agency": agency, "category": "人事法令解釋",
            "doc_number": _fold(number), "date": _date(day.group()) if day else "",
            "summary": _unwrap(td.select_one("pre").get_text()) if td.select_one("pre") else "",
            # 「停止適用」；同一件函偶有重複登錄、只有一筆有標（例如 1014 與 11715），沒標示不代表確認仍適用
            **_status(True if td.select_one(".label-fei") else None),
        })
    m = re.search(r"共\s*(\d+)\s*筆", soup.get_text())  # 只有一頁時不顯示總筆數
    total = int(m.group(1)) if m else len(items)
    return [_group("行政院人事行政總處", "人事法令解釋", total, items, q.page * 10 < total)]


async def _dgpa_get(http: httpx.AsyncClient, native_id: str) -> dict:
    return await _exec_detail(http, f"{DGPA_BASE}LegalData.aspx?id={_check_id(native_id)}&type=2", "行政院人事行政總處",
                              affirm=False)


# ─────────────────────────────────────────────────────────────
# 清單只有「機關 日期 字號」一行的來源（交通部、中央銀行、臺北市、地政司）共用
# ─────────────────────────────────────────────────────────────

_HEAD = re.compile(r"^(\D*?)\s*(\d{2,3}\s*[年.]\s*\d{1,2}\s*[月.]\s*\d{1,2})\s*日?\s*\.?\s*(.*)$")


def _head(line: str, agency: str) -> dict:
    """「交通部 114.02.03. 交運字第1140000670號函」「中央銀行113年10月31日台央外伍字第1130040947號令」→ 機關、日期、字號。"""
    m = _HEAD.match(re.sub(r"[\x00-\x1f\s]+", " ", line).strip())  # 中央銀行舊資料夾帶控制字元
    if not m:
        return {"agency": agency, "date": "", "doc_number": _fold(line)}
    return {"agency": re.sub(r"\s", "", m.group(1)) or agency, "date": _date(m.group(2)), "doc_number": _fold(m.group(3))}


def _page_total(page: int, last: int, count: int, size: int) -> int:
    """站方只給總頁數：在最後一頁算得出確切筆數，否則以頁數估計（上限）。"""
    return (page - 1) * size + count if page >= last else last * size


def _last_page(soup, param: str, page: int) -> int:
    hrefs = " ".join(a["href"] for a in soup.select(f"a[href*='{param}=']"))
    return max([int(x) for x in re.findall(rf"[?&]{param}=(\d+)", hrefs)] + [page])


# ─────────────────────────────────────────────────────────────
# 交通部（motclaw.motc.gov.tw）：清單每頁 25 筆、只列機關日期字號。伺服器沒送中繼憑證，連線時附上（見 tls）
# ─────────────────────────────────────────────────────────────

MOTC_BASE = "https://motclaw.motc.gov.tw/webMotcLaw2018/SLaw/"
_MOTC_STOP = re.compile(r"\s*[（(]((?:部分)?停止適用|廢止)[）)]\s*$")  # 字號後的標示；沒標示不代表確認仍適用


def _motc_head(line: str) -> dict:
    head = _head(line, "交通部")
    m = _MOTC_STOP.search(head["doc_number"])
    if not m:
        return head
    head["doc_number"] = _MOTC_STOP.sub("", head["doc_number"])
    return {**head, "status": "部分停止適用" if m.group(1).startswith("部分") else "停止適用", "status_note": m.group(1)}


def _motc_http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=60.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True,
                             verify=tls.context_with(tls.TWCA_SSL_CA_2023))


async def _motc_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    async with _motc_http() as motc:
        r = await motc.get(MOTC_BASE + "List", params={
            "cKeyword": q.keyword, "titleNo": q.number_digits,
            "startDate": _roc7(q.start), "endDate": _roc7(q.end), "page": q.page,  # 民國 YYYMMDD
        })
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for a in soup.select("table.list-result a[href*='soid=']"):
        soid = re.search(r"soid=(\d+)", a["href"])
        if soid:
            items.append({"id": f"motc:{soid.group(1)}", **_motc_head(_text(a)), "category": "行政解釋", "summary": ""})
    last = _last_page(soup, "page", q.page)
    return [_group("交通部", "行政解釋", _page_total(q.page, last, len(items), 25), items, q.page < last,
                   note="清單只有機關、日期、字號，主旨與全文請用 get 取得")]


async def _motc_get(http: httpx.AsyncClient, native_id: str) -> dict:
    url = f"{MOTC_BASE}Content?soid={_check_id(native_id)}"
    async with _motc_http() as motc:
        r = await motc.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    head, body = soup.select_one(".con-area-top p span"), soup.select_one(".con-explain pre")
    if head is None or body is None:
        raise LookupError(native_id)
    data = _motc_head(_text(head))
    basis = re.findall(r"〔(依[^〕]*)〕", body.get_text())  # 內文末尾「〔依…公告發布廢止，自…起生效〕」
    if "status" in data and basis:
        data["status_note"] = re.sub(r"\s+", "", basis[-1])
    return {
        **data, "summary": _text(soup.select_one(".con-explain > p")),
        "full_text": _RECIPIENTS.sub("", _unwrap(body.get_text())),
        "source_url": url,
    }


# ─────────────────────────────────────────────────────────────
# 中央銀行（www.law.cbc.gov.tw）：與交通部同一廠商，清單每頁 10 筆
# ─────────────────────────────────────────────────────────────

CBC_BASE = "https://www.law.cbc.gov.tw/SOrder/"
# 業務規章、總綱、組織、業務、發行、外匯、國庫、檢查、資訊；站方要求至少勾一類，全勾 = 全部
_CBC_TYPES = ("1", "3", "4", "5", "16", "10", "17", "22", "47")


async def _cbc_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    params = [("criteria.lawCheckBoxs", t) for t in _CBC_TYPES] + [
        ("criteria.keyWord1", q.keyword), ("criteria.number", q.number_digits),
        ("criteria.starDate", _roc7(q.start)), ("criteria.endDate", _roc7(q.end)), ("criteria.pageNumber", q.page),
    ]
    r = await http.get(CBC_BASE + "SearchAgain", params=params)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for td in soup.select("table.list-result td:has(> a[href*='/SOrder/SOrder/'])"):
        a = td.select_one("a[href*='/SOrder/SOrder/']")
        rid = re.search(r"/SOrder/SOrder/(\d+)", a["href"])
        stop = a.find("label", string=re.compile(r"\(停\)"))  # 沒標示不代表確認仍適用（例如被後令取代的利率調整）
        if stop:
            stop.extract()
        summary = re.sub(r"\s*\n\s*", "", "".join(a.stripped_strings))
        a.extract()
        items.append({"id": f"cbc:{rid.group(1)}", **_head(_text(td), "中央銀行"), "category": "行政令函", "summary": summary,
                      **_status(True if stop else None)})
    pages = [int(o["value"]) for o in soup.select("#currentPageChange option") if o.get("value", "").isdigit()]
    last = max(pages + [q.page])
    return [_group("中央銀行", "行政令函", _page_total(q.page, last, len(items), 10), items, q.page < last)]


async def _cbc_get(http: httpx.AsyncClient, native_id: str) -> dict:
    url = f"{CBC_BASE}SOrder/{_check_id(native_id)}"
    r = await http.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    head = soup.select_one(".letters-page-content .jumbotron")  # 查無此 id 時回 204 空白
    if head is None:
        raise LookupError(native_id)
    stop = head.find("label", string=re.compile(r"\(停\)"))
    if stop:
        stop.extract()
    fields = {_text(b).rstrip("："): _text(b.parent)[len(_text(b)):].strip() for b in head.select("b")}
    parts, stopped_on = [], ""
    for row in soup.select(".letters-desc-text > div.row"):
        cells = row.find_all("div", recursive=False)
        for label, value in zip(cells[::2], cells[1::2]):
            name = re.sub(r"\s", "", _text(label))
            if name.startswith("停止適用日期"):
                stopped_on = _date(_text(value))
            elif not name.startswith(("正本", "副本")):
                parts.append(name + _html_text(str(value)))
    return {
        **_head(fields.get("發文字號", ""), "中央銀行"), "summary": fields.get("要旨", ""),
        "full_text": "\n".join(parts),
        **_status(True if stop or stopped_on else None, f"停止適用日期 {stopped_on}" if stopped_on else ""),
        "attachments": [{"title": _text(a), "url": str(r.url.join(a["href"]))}
                        for a in soup.select(".attact-files-div a[href]")],
        "source_url": url,
    }


# ─────────────────────────────────────────────────────────────
# 臺北市法規查詢系統（laws.gov.taipei）：臺北市政府的解釋令函，另收中央機關函釋
# ─────────────────────────────────────────────────────────────

TAIPEI_BASE = "https://laws.gov.taipei/Law/Interpretation/"
_TAIPEI_CATEGORIES = {"002": "解釋令函（臺北市）", "003": "中央機關解釋令函（臺北市法規查詢系統收錄）"}


async def _taipei_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    params = {"CaseNumber": q.number_digits, "DateRange.DateFrom": q.start, "DateRange.DateTo": q.end,
              "showtype": 1, "page": q.page}
    for i, word in enumerate(q.keyword.split()[:3], 1):  # 站方最多三組關鍵字
        params[f"SearchString.Keyword{i}"] = word
        params[f"SearchString.Operaton{i}"] = "AND"
    out = []
    for cate, label in _TAIPEI_CATEGORIES.items():
        r = await http.get(TAIPEI_BASE + "SearchResult", params={**params, "curcateid": cate})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        items = []
        for ul in soup.select("ul.fx-list"):
            a = ul.select_one("li.num a[href*='/Content/']")
            fe = re.search(r"/Content/(FE\d+)", a["href"]) if a else None
            if fe:  # 已停止適用的在字號前標「(廢)」；沒標示不代表確認仍適用
                items.append({"id": f"taipei:{fe.group(1)}", **_head(_text(a), "臺北市政府"), "category": label,
                              "summary": _text(ul.select_one("li.pre")),
                              **_status(True if ul.select_one("span.abolished") else None)})
        m = re.search(r"共\s*([\d,]+)\s*筆", soup.get_text())
        total = int(m.group(1).replace(",", "")) if m else len(items)
        out.append(_group("臺北市政府", label, total, items, q.page * PAGE_SIZE < total))
    return out


async def _taipei_get(http: httpx.AsyncClient, native_id: str) -> dict:
    url = f"{TAIPEI_BASE}Content/{_check_id(native_id, r'FE[0-9]+')}"
    r = await http.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    art = soup.select_one("article.interpretation-content")  # 查無此 id 時頁面沒有這個區塊
    if art is None:
        raise LookupError(native_id)
    rows = {re.sub(r"\s", "", _text(row.select_one(".col-title"))).rstrip("："): row.select_one(".col-data")
            for row in art.select(".row") if row.select_one(".col-title")}
    body = art.select_one("pre[title='內容']")
    abolished = art.select_one("span.abolished")
    if abolished:
        abolished.extract()  # 不然機關名稱會變成「(廢)臺北市政府…」
    head = _head(_text(rows.get("發文字號")), "臺北市政府")
    return {
        **head, "date": _date(_text(rows.get("發文日期"))) or head["date"],
        "summary": _unwrap(rows["要旨"].get_text()) if "要旨" in rows else "",
        "full_text": _RECIPIENTS.sub("", _unwrap(body.get_text())) if body else "",
        "notes": _text(soup.select_one("h3.small-subject span")),  # 業務分類
        **_status(True if abolished else None),
        "source_url": url,
    }


# ─────────────────────────────────────────────────────────────
# 新北市法規查詢系統（web.law.ntpc.gov.tw）：函釋分 66 類，查詢先回各類筆數，清單只能逐類取（沒有跨類清單），
# 每頁 20 筆、只有字號與要旨，發文機關與日期在明細頁。收錄機關混雜（新北市政府與中央機關）；沒有效力標示
# ─────────────────────────────────────────────────────────────

NTPC_BASE = "https://web.law.ntpc.gov.tw/Scripts/"
NTPC_MAX_CATEGORIES = 5  # 一次查詢取筆數最多的幾類清單；其餘只列筆數
_NTPC_ID = re.compile(r"([A-Z]\d{5}):([^:/?&#\s]{1,20}):([0-9A-Za-z\-]{1,20})")


def _ntpc_params(q: Query) -> dict:
    params = {"K1": q.keyword, "sdate": q.start or "00000000", "edate": q.end or "99991231"}  # 西元
    if q.number:
        m = _MOL_DOC_NUMBER.match(q.number)
        params.update({"N1": m.group(1).strip(), "N2": m.group(2)} if m else {"N2": q.number_digits})
    return params


async def _ntpc_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    params = _ntpc_params(q)
    r = await http.get(NTPC_BASE + "SimpleQ2.aspx", params={"C3": "E", **params})
    r.raise_for_status()
    cats = []
    for tr in BeautifulSoup(r.text, "html.parser").select("table.tab-list2 tr"):
        a, n = tr.select_one("a[href*='ecode=']"), re.search(r"\d+", _text(tr.td))
        code = re.search(r"ecode=(\w+)", a["href"]) if a else None
        if code and n:
            cats.append((int(n.group()), code.group(1), _text(a)))
    cats.sort(key=lambda c: -c[0])

    async def listing(count: int, code: str, name: str) -> dict:
        r = await http.get(NTPC_BASE + "FLAWDOC02_Search.aspx", params={"rtype": "E", "ecode": code, **params,
                                                                          "page": q.page})
        r.raise_for_status()
        items = []
        for a in BeautifulSoup(r.text, "html.parser").select("a[href*='FLAWDOC03.aspx']"):
            qs = dict(parse_qsl(a["href"].partition("?")[2]))
            row = a.find_parent("tr")
            data = row.find_next_sibling("tr") if row else None
            items.append({
                "id": f"ntpc:{code}:{qs.get('ecase', '')}:{qs.get('eno', '')}", "agency": "", "category": f"新北市函釋（{name}）",
                "doc_number": _fold(_text(a)), "date": "",
                "summary": _unwrap(data.select_one("pre").get_text()) if data and data.select_one("pre") else "",
            })
        return _group("新北市法規查詢系統", f"函釋（{name}）", count, items, q.page * PAGE_SIZE < count)

    groups = list(await asyncio.gather(*(listing(*c) for c in cats[:NTPC_MAX_CATEGORIES])))
    if not groups:
        return [_group("新北市法規查詢系統", "函釋", 0, [], False)]
    rest = cats[NTPC_MAX_CATEGORIES:]
    if rest:
        groups[0]["note"] = (f"另有 {len(rest)} 類共 {sum(c[0] for c in rest)} 筆未列出（"
                             + "、".join(f"{c[2]} {c[0]}" for c in rest[:10]) + "…）；請改用更精確的關鍵字")
    return groups


async def _ntpc_get(http: httpx.AsyncClient, native_id: str) -> dict:
    m = _NTPC_ID.fullmatch(native_id)
    if not m:
        raise LookupError(native_id)
    params = {"rtype": "E", "ecode": m.group(1), "ecase": m.group(2), "eno": m.group(3)}
    r = await http.get(NTPC_BASE + "FLAWDOC03.aspx", params=params)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    rows = {re.sub(r"\s", "", _text(tr.th)).rstrip("：:"): tr.td
            for tr in soup.select("table.tab-ex tr") if tr.th and tr.td}
    if not _text(rows.get("發文字號")):  # 查無此件時仍回 200，只是沒有發文字號
        raise LookupError(native_id)
    body = soup.select_one("td.m-td pre")
    return {
        "agency": _text(rows.get("發文單位")), "doc_number": _fold(_text(rows["發文字號"])),
        "date": _date(_text(rows.get("發文日期"))), "data_source": _text(rows.get("資料來源")),
        "related_laws": [_text(a) for a in rows["相關法條"].select("a")] if "相關法條" in rows else [],
        "summary": _unwrap(rows["要旨"].get_text()) if "要旨" in rows else "",
        "full_text": _RECIPIENTS.sub("", _unwrap(body.get_text())) if body else "",
        "source_url": str(r.url),
    }


# ─────────────────────────────────────────────────────────────
# 行政院消費者保護處（www.ey.gov.tw）：消保法函釋與法規諮詢意見約 300 則，依條次排列，沒有站內全文檢索：
# 清單（每頁最多 200 筆）整份抓下來在本機比對標題與摘要，一天更新一次。清單日期是上架日，發文日期在摘要開頭
# ─────────────────────────────────────────────────────────────

CPC_LIST_URL = "https://www.ey.gov.tw/Page/B68C1CA8857302A2"
CPC_PAGE_URL = "https://www.ey.gov.tw/Page/24C4B877E850ED4E/"
_CPC_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_DAILY: dict[str, tuple[float, list[dict]]] = {}  # 整份清單的程序內快取：來源 → (抓取時間, 清單)


async def _daily(key: str, load) -> list[dict]:
    """幾百筆、沒有站內全文檢索的清單：一天抓一次，在本機比對。"""
    hit = _DAILY.get(key)
    if hit and time.time() - hit[0] < 86400:
        return hit[1]
    rows = await load()
    if not rows:
        raise ValueError(f"{key} 清單格式不符（沒有任何項目）")
    _DAILY[key] = (time.time(), rows)
    return rows


def _local_search(rows: list[dict], q: Query, source: str, category: str, note: str) -> list[dict]:
    words = q.keyword.split()
    hits = [x for x in rows
            if all(w in x["summary"] or w in x.get("_text", "") for w in words)
            and (not q.number_digits or q.number_digits in x["doc_number"]) and _in_range(x["date"], q)]
    hits.sort(key=lambda x: x["date"], reverse=True)  # 官方清單多依條次排列；沒有日期的排最後
    page_rows, more = _paged(hits, q.page)
    return [_group(source, category, len(hits), [{k: v for k, v in x.items() if k != "_text"} for x in page_rows],
                   more, note=note)]


def _cpc_head(text: str) -> dict:
    """「【行政院消費者保護委員會書函】 中華民國八十七年七月一日 台八十七消保法字第○○七五二號 受文者…」或
    「行政院消費者保護處 函 中華民國111年6月1日 院臺消保字第…號 【主旨】…」→ 機關、日期、字號（只看受文者、主旨之前）。"""
    text = _land_norm(re.split(r"受文者|【主旨】|【說明】|主\s*旨\s*[：:]|(?:^|\s)一、", text, maxsplit=1)[0])
    agency = re.search(r"([^【】\s，。、；：「」（）()]{2,20}?(?:委員會|處|院|部|署|局))\s*(?:書函|函|令)", text)
    day = re.search(r"\d{2,3}年\d{1,2}月\d{1,2}日", text)
    number = re.search(r"\S{1,20}字第?\s*\d+\s*號", text)
    return {"agency": agency.group(1) if agency else "行政院消費者保護處", "date": _date(day.group()) if day else "",
            "doc_number": _fold(number.group()) if number else ""}


async def _cpc_rows(http: httpx.AsyncClient) -> list[dict]:
    rows = []
    for page in range(1, 6):
        r = await http.get(CPC_LIST_URL, params={"page": page, "PS": 200})
        r.raise_for_status()
        links = BeautifulSoup(r.text, "html.parser").select("div.news_box > a[href*='/Page/24C4B877E850ED4E/']")
        for a in links:
            guid = _CPC_ID.search(a["href"])
            preview = _text(a.select_one("p"))
            if guid:
                rows.append({"id": f"cpc:{guid.group()}", **_cpc_head(preview), "category": "消費者保護法函釋",
                             "summary": _text(a.select_one(".title")), "_text": preview})
        if len(links) < 200:
            break
    return rows


async def _cpc_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    rows = await _daily("cpc", partial(_cpc_rows, http))
    return _local_search(rows, q, "行政院消費者保護處", "消費者保護法函釋", "此來源只比對標題與摘要，不是全文檢索")


async def _cpc_get(http: httpx.AsyncClient, native_id: str) -> dict:
    url = CPC_PAGE_URL + _check_id(native_id, _CPC_ID.pattern)
    r = await http.get(url)
    r.raise_for_status()
    words = BeautifulSoup(r.text, "html.parser").select_one("div.words")
    if words is None:
        raise LookupError(native_id)
    lines = [x for x in words.get_text("\n", strip=True).split("\n") if x]
    start = 1
    while start < len(lines) and lines[start].startswith(("日期：", "資料來源：")):  # 上架日期，不是發文日期
        start += 1
    body = "\n".join(lines[start:])
    return {**_cpc_head(body), "summary": lines[0], "full_text": _RECIPIENTS.sub("", body), "source_url": url}


# ─────────────────────────────────────────────────────────────
# 監察院陽光法令主題網（sunshine.cy.gov.tw）：政治獻金法、公職人員利益衝突迴避法、財產申報法等的主管機關函釋
# （法務部、內政部、監察院），約 500 則；清單有機關、日期、文號，只能比對標題
# ─────────────────────────────────────────────────────────────

SUNSHINE_BASE = "https://sunshine.cy.gov.tw/"


async def _sunshine_rows(http: httpx.AsyncClient) -> list[dict]:
    rows = []
    for page in range(1, 11):
        r = await http.get(SUNSHINE_BASE + "News.aspx", params={"n": 24, "sms": 8862, "page": page, "PageSize": 200})
        r.raise_for_status()
        trs = BeautifulSoup(r.text, "html.parser").select("#CCMS_Content table tbody tr")
        for tr in trs:
            cells = {td.get("data-title", ""): td for td in tr.find_all("td")}
            a = tr.select_one("a[href*='News_Content.aspx']")
            sn = re.search(r"[?&]s=(\d+)", a["href"]) if a else None
            if sn:
                rows.append({"id": f"sunshine:{sn.group(1)}", "agency": _text(cells.get("發布機關")),
                             "category": "陽光法令函釋", "doc_number": _fold(_text(cells.get("發布文號"))),
                             "date": _date(_text(cells.get("發布日期"))), "summary": _text(a)})
        if len(trs) < 200:
            break
    return rows


async def _sunshine_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    rows = await _daily("sunshine", partial(_sunshine_rows, http))
    return _local_search(rows, q, "監察院陽光法令主題網", "陽光法令函釋", "此來源只比對標題，不是全文檢索")


async def _sunshine_get(http: httpx.AsyncClient, native_id: str) -> dict:
    url = f"{SUNSHINE_BASE}News_Content.aspx?n=24&s={_check_id(native_id)}"
    r = await http.get(url)
    r.raise_for_status()
    essay = BeautifulSoup(r.text, "html.parser").select_one("#CCMS_Content div.area-essay")
    if essay is None:
        raise LookupError(native_id)
    # 機關、日期、文號只在清單有（內文開頭的文號偶有漏字）；標題在內文區塊之外
    rows = await _daily("sunshine", partial(_sunshine_rows, http))
    row = next((x for x in rows if x["id"] == f"sunshine:{native_id}"), {})
    return {"agency": row.get("agency", ""), "doc_number": row.get("doc_number", ""), "date": row.get("date", ""),
            "summary": row.get("summary", ""), "full_text": _RECIPIENTS.sub("", _html_text(str(essay))),
            "source_url": url}


# ─────────────────────────────────────────────────────────────
# 經濟部標準檢驗局（www.bsmi.gov.tw）：解釋函令約 300 則，JSON 一次取完、含全文，在本機全文比對；
# 標「不公開」或沒有內容的不收
# ─────────────────────────────────────────────────────────────

BSMI_URL = "https://www.bsmi.gov.tw/law/law/LawExplanation"


async def _bsmi_rows(http: httpx.AsyncClient) -> list[dict]:
    r = await http.get(BSMI_URL, params={"queryString": "{}", "criterion": "{}",
                                         "queryPager": json.dumps({"pageSize": 1000, "currentPage": 1})})
    r.raise_for_status()
    rows = []
    for x in r.json().get("resultList") or []:
        text = _html_text(x.get("explanationContent") or "")
        if x.get("explanationStatusName") == "不公開" or not text.strip("　 \n") or not x.get("uuId1"):
            continue
        rows.append({"id": f"bsmi:{x['uuId1']}", "agency": "經濟部標準檢驗局", "category": "解釋函令",
                     "doc_number": _fold((x.get("explanationNo") or "").strip("　 ")),
                     "date": _date(x.get("explanationDateString") or ""),
                     "summary": (x.get("explanationtitle") or "").strip(), "_text": text})
    return rows


async def _bsmi_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    rows = await _daily("bsmi", partial(_bsmi_rows, http))
    return _local_search(rows, q, "經濟部標準檢驗局", "解釋函令", "")


async def _bsmi_get(http: httpx.AsyncClient, native_id: str) -> dict:
    rows = await _daily("bsmi", partial(_bsmi_rows, http))
    row = next((x for x in rows if x["id"] == f"bsmi:{native_id}"), None)
    if row is None:
        raise LookupError(native_id)
    return {**{k: v for k, v in row.items() if k not in ("id", "_text")}, "full_text": row["_text"],
            "source_url": f"{BSMI_URL}/{native_id}"}


# ─────────────────────────────────────────────────────────────
# 內政部地政司 地政法令（www.land.moi.gov.tw/law）：robots.txt 全站禁止，只在使用者查詢時即時查一頁。
# 結果依法條分組、全文直接列在清單；沒有個別函的頁面，取全文是以文號再查一次
# ─────────────────────────────────────────────────────────────

LAND_URL = "https://www.land.moi.gov.tw/law/Resultdet3/99"
_DIGITS = str.maketrans("〇○零一二三四五六七八九０１２３４５６７８９", "0001234567890123456789")


def _cn_number(m: re.Match) -> str:
    """國字數字：「八十八」→ 88、「一百零二」→ 102；逐位寫的「八八○六九九八」「０九二００六九九三七」直接換字。"""
    s = m.group().translate(_DIGITS)
    if not re.search("[十百]", s):
        return s
    n = cur = 0
    for c in s:
        if c in "十百":
            n, cur = n + (cur or 1) * (10 if c == "十" else 100), 0
        else:
            cur = int(c)
    return str(n + cur)


def _land_norm(line: str) -> str:
    """早期的函日期、文號用國字或全形數字（「八十八年六月七日…第八八○六九九八號」），換成阿拉伯數字。"""
    line = re.sub(r"[0-9０-９〇○零一二三四五六七八九十百]+(?=[年月日號])", _cn_number, line)
    return line.replace("中華民國", "")


def _land_params(keyword: str, number: str, page: int) -> list[tuple]:
    return [
        ("showfrom", "y"), ("condition", "eadddate"), ("order1", "desc"), ("Econtent", keyword),
        ("EctntType_search", "1"), ("EctntType_search", "2"),  # 比對要旨與內容
        ("lawOnOFF", "2"), ("lawOnOFF", "0"),  # 適用中與已停止適用
        ("Etext", number), ("PageSize", PAGE_SIZE), ("pagenum", page),
    ]


def _land_parse(soup) -> list[dict]:
    for k in soup.select("strong.keyword"):  # 關鍵字標示前後多了空白
        k.replace_with(k.get_text(strip=True))
    recs = []
    for box in soup.select("div.main3box"):
        law, rec = _text(box.select_one(".main_title a")), None
        for div in box.select("div.main_span"):
            label = div.find("span", recursive=False)
            if _text(div.select_one("strong.icon_t")) == "解釋函":
                rec = {"law": law, "notes": []}
                recs.append(rec)
            elif rec is not None and label:
                rec[_text(label)] = div
                label.extract()
            elif rec is not None and _text(div):
                rec["notes"].append(_text(div))  # 「已停止適用/廢止」
    out: dict[str, dict] = {}
    for rec in recs:
        head = _head(_land_norm(_plain(rec.get("公布日期文號"))), "內政部")
        number = re.findall(r"\d{3,}", head["doc_number"])
        if not number:
            continue
        item_id = f"land:{number[-1]}:{head['date']}"  # 同號不同機關的舊函以日期區分
        if item_id in out:  # 同一函列在多個條文下
            out[item_id]["related_laws"].append(rec["law"])
            continue
        stop = rec.get("停止適用日期文號")
        marked = [n for n in rec["notes"] if "停止適用" in n or "廢止" in n]  # 「已停止適用/廢止」
        out[item_id] = {
            "id": item_id, **head, "category": "地政法令解釋函", "summary": _plain(rec.get("要旨")),
            "related_laws": [rec["law"]],
            "notes": "；".join(n for n in rec["notes"] if n not in marked),
            # 查詢同時勾「適用」與「停止適用/廢止」，停止的每筆都有標示，沒標示即適用中
            **_status(bool(stop or marked), _land_norm(_plain(stop)) if stop else ""),
            "full_text": _html_text(str(rec["內容"])) if "內容" in rec else "",
        }
    return list(out.values())


async def _land_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    r = await http.get(LAND_URL, params=_land_params(q.keyword, q.number_digits, q.page))
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    m = re.search(r"共有\s*(\d+)\s*筆", soup.get_text(" "))
    items = [{k: v for k, v in x.items() if k != "full_text"} for x in _land_parse(soup)]
    total = int(m.group(1)) if m else len(items)
    note = {"note": "此來源沒有發文日期區間查詢，日期篩選未套用"} if q.start or q.end else {}
    return [_group("內政部地政司", "地政法令解釋函", total, items, q.page * PAGE_SIZE < total, **note)]


async def _land_get(http: httpx.AsyncClient, native_id: str) -> dict:
    number = _check_id(native_id, r"[0-9]+:[0-9-]*").split(":")[0]
    params = _land_params("", number, 1)
    r = await http.get(LAND_URL, params=params)
    r.raise_for_status()
    rec = next((x for x in _land_parse(BeautifulSoup(r.text, "html.parser")) if x["id"] == f"land:{native_id}"), None)
    if rec is None:
        raise LookupError(native_id)
    return {
        **{k: v for k, v in rec.items() if k not in ("id", "category")},
        "full_text": _RECIPIENTS.sub("", rec["full_text"]), "source_url": f"{LAND_URL}?{urlencode(params)}",
    }


# ─────────────────────────────────────────────────────────────
# 內政部消防署 消防法令查詢系統（law.nfa.gov.tw/GNFA）：只能查摘要；函文只有附件，多為沒有文字層的掃描 PDF
# ─────────────────────────────────────────────────────────────

NFA_BASE = "https://law.nfa.gov.tw/GNFA/"


async def _nfa_search(http: httpx.AsyncClient, q: Query) -> list[dict]:
    if q.number:
        return [_group("內政部消防署", "法令解釋", 0, [], False, note="此來源不支援以發文字號查詢")]
    # 站方對不認得的參數不報錯、直接回未篩選的全部資料，參數名稱不能打錯
    r = await http.get(NFA_BASE + "index.aspx", params={
        "type": "d", "abstr": q.keyword, "starDate": _roc7(q.start), "endDate": _roc7(q.end), "pg": q.page,
    })
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for tr in soup.select("tr#trRow"):
        a = tr.select_one("a[href*='news.aspx?id=']")
        rid = re.search(r"id=(\d+)", a["href"]) if a else None
        if rid:
            items.append({
                "id": f"nfa:{rid.group(1)}", "agency": "內政部消防署", "category": "法令解釋", "doc_number": "",
                "date": _date(_text(tr.find("td"))), "summary": _text(a),
            })
    last = _last_page(soup, "pg", q.page)
    return [_group("內政部消防署", "法令解釋", _page_total(q.page, last, len(items), 20), items, q.page < last,
                   note="關鍵字只比對摘要")]


async def _nfa_get(http: httpx.AsyncClient, native_id: str) -> dict:
    url = f"{NFA_BASE}news.aspx?id={_check_id(native_id)}"
    r = await http.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    fields = {re.sub(r"\s", "", _text(tr.th)).rstrip("："): tr.td for tr in soup.select("table tr") if tr.th and tr.td}
    if not _text(fields.get("文號")):  # 查無此 id 時欄位都是空的
        raise LookupError(native_id)
    return {
        "agency": _text(fields.get("發布機關")) or "內政部消防署", "doc_number": _fold(_text(fields["文號"])),
        "date": _date(_text(fields.get("公發布日"))), "summary": _text(fields.get("摘要")),
        "full_text": "",
        "notes": "官網只提供函文附件檔（多為掃描 PDF），全文請開啟 attachments。",
        "attachments": [{"title": _text(a), "url": str(r.url.join(a["href"]))}
                        for a in soup.select("a[href*='downloadFile.aspx']")],
        "source_url": url,
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
        form = {
            "_csrf": csrf.group(1), "keyword1": q.keyword, "links": "and", "keyword2": "",
            "explainNumberNo": q.number_digits, "startDate": _roc_slash(q.start), "endDate": _roc_slash(q.end),
            "startNetDate": "", "endNetDate": "", "sorts": "issuedDate",
        }
        r = await s.post(PCC_BASE + "readPrmsExplainLetter", data={
            **form, "type": ["1", "2", "3"], "paginationPage": str(q.page - 1), "pageSize": str(PAGE_SIZE)})
        r.raise_for_status()
        # 類別 3「廢止或停止適用者」是附加標記（停止的函同時屬於類別 2），全站不到百筆，一次取完比對
        stop = await s.post(PCC_BASE + "readPrmsExplainLetter", data={
            **form, "type": ["3"], "paginationPage": "0", "pageSize": "1000"})
        stop.raise_for_status()
    stopped = set(re.findall(r"readExplainLetter\((\d+)\)", stop.text))
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
            **_status(True if pk.group(1) in stopped else None),  # 不在類別 3 不代表確認仍適用（部分停止的不列）
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

    red = soup.select_one(".title_1s font[color=red]")  # 「備註：本解釋函即日起停止適用」
    note = _text(red).removeprefix("備註：").removeprefix("註：") if red else ""
    if red:
        red.extract()  # 不然會混進機關與文別
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
        **_status(True if "停止適用" in note or "廢止" in note else None, note),
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


def _mof_compiled(row: dict) -> dict:
    """法令彙編只查最新版：收錄的函釋經財政部重新研審保留適用（各版適用期限見官網 FC200）；彙編後才明令廢止的不另標示。"""
    return _status(False, f"列入財政部{row.get('Part', '').strip()}法令彙編（{row.get('TaxAct', '')}），經重新研審保留適用；"
                          "彙編後才廢止的官網不另標示")


async def _mof_dropped(http: httpx.AsyncClient, number: str) -> dict:
    """「函釋免列及節錄理由」清單裡的免列函令（例如經明令廢止而免列）；號碼查詢是部分比對，要自己核對全號。"""
    r = await http.post(f"{MOF_BASE}/Api/GetData", data=_mof_params(
        "FB30001", Cate="免列函令", LawName="請選擇", LawYear="", Word="", Number=number, Publish="請選擇",
        start=0, length=50))
    r.raise_for_status()
    hit = next((x for x in r.json().get("Data", {}).get("Table", []) or [] if x.get("Number") == number), None)
    return _status(True, f"{hit.get('LawName', '')}免列：{hit.get('Content', '')}") if hit else {}


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
            **_mof_compiled(row),
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
            **_mof_compiled(row),
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
            # 新頒令釋沒有效力欄位；只能查是否被下一版彙編列為免列（沒列不代表確認仍適用）
            **(await _mof_dropped(http, row.get("DocNum", "")) if row.get("DocNum") else {}),
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
        **_status(True if x.get("aboFlg") == "2" else None),  # 2 = 停止適用／廢止（含部分不再援用）；0 只是未標示
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
        **_status(True if x.get("aboFlg") == "2" else None,
                  _html_text(x.get("aboDesc") or "").removeprefix("【停止適用/廢止】")),
        "source_url": f"https://gcis.nat.gov.tw/elaw/constructionDetail?consCd={native_id}",
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
            root = ElementTree.fromstring(z.read("content.xml"))
            # Plain text:p elements need no nested span; splitting the closing tag loses that text.
            tags = {"{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p",
                    "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}h"}
            return "\n".join("".join(node.itertext()).strip() for node in root.iter() if node.tag in tags).strip()
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
            # 只有少數標題手打「（停止適用）」，很多停止的沒標，沒標示不代表確認仍適用
            **_status(True if re.search(r"[（(]\s*(?:目前)?已?停止適用\s*[）)]", x.get("title") or "") else None),
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
    "數發部": "數位發展部", "數位部": "數位發展部", "衛福部": "衛生福利部", "勞委會": "勞動部",
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
            **{k: x[k] for k in ("status", "status_note") if k in x},
        })
    note = {"note": "此來源以發文字號查詢，關鍵字未套用"} if q.number_digits and q.keyword else {}
    return [_group("司法院法學資料檢索系統", "行政函釋", group["total"], items, group["has_more"], **note)]


async def _fint_get(http: httpx.AsyncClient, native_id: str) -> dict:
    d = await fint.get(http, native_id)
    f = d["fields"]
    status = {k: d[k] for k in ("status", "status_note") if k in d}
    # 編註多半列「依本筆資料，原…函…停止適用」（這件停掉的其他函）；講這件自己被停掉的是「本筆資料，依據…」
    own = re.search(r"(?<!依)本筆資料，[^。]*停止適用", f.get("編註", ""))
    if status and own:
        status["status_note"] = own.group()
    return {
        "agency": f.get("發文單位", ""), "doc_number": _fold(f.get("發文字號", "")),
        "date": _date(f.get("發文日期", "")), "summary": f.get("要旨", ""),
        "full_text": _RECIPIENTS.sub("", d["full_text"]), "related_laws": d["related_laws"],
        "notes": f.get("編註", ""),
        **status,
        "data_source": f.get("資料來源", ""), "attachments": d["attachments"], "source_url": d["source_url"],
    }


# ─────────────────────────────────────────────────────────────
# 對外介面
# ─────────────────────────────────────────────────────────────

# NCC 公開行政函釋（法規解釋專站；每次只查指定條件）
_NCC_BASE = "https://ncclaw.ncc.gov.tw/FINT/"


async def _ncc_search(http, q: Query) -> list[dict]:
    if not (q.keyword or q.number or q.start or q.end):
        raise ValueError("NCC 請指定關鍵字、字號或日期範圍")
    params = {"allfccode": "B***", "btype": "B***", "now": "1", "lnabndn": "1",
              "lpcode": "etype_ALL", "lpcodetype": "E", "keyword": q.keyword,
              "N2": q.number_digits, "sdate": q.start, "edate": q.end}
    r = await public_browser.get(http, _NCC_BASE + "results.aspx", params={k: v for k, v in params.items() if v},
                                 selector=".tab-result2, #ctl00_cph_content_pnlNoData")
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for tr in soup.select(".tab-result2 tr"):
        a = tr.select_one('a[href*="FINTQRY04.aspx"]')
        rid = re.search(r"fecode=(FE\d+)", a.get("href", "")) if a else None
        if not rid:
            continue
        divs = tr.select("td > div")
        items.append({"id": "ncc:" + rid.group(1), "agency": "國家通訊傳播委員會", "category": "行政函釋",
                      "doc_number": _fold(_text(a)), "date": _date(_text(divs[1])) if len(divs) > 1 else "",
                      "summary": re.sub(r"^要\s*旨[：:]\s*", "", _plain(tr.select_one(".list-issue")))})
    # 官網把本次檢索命中放在同頁；只切分這次結果，不巡訪分類或其他分頁。
    return [_group("國家通訊傳播委員會", "行政函釋", len(items), *_paged(items, q.page))]


async def _ncc_get(http, native_id: str) -> dict:
    url = _NCC_BASE + "FINTQRY04.aspx?fecode=" + _check_id(native_id, r"FE\d{6}")
    r = await public_browser.get(http, url, selector=".fint-table")
    soup = BeautifulSoup(r.text, "html.parser")
    fields = {}
    for row in soup.select(".fint-table tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) == 2:
            fields[re.sub(r"[\s：:]", "", _text(cells[0]))] = _text(cells[1])
    body = soup.select_one(".fint-pre pre")
    if body is None or not fields.get("發文字號"):
        raise LookupError(native_id)
    return {"agency": fields.get("發文單位", "國家通訊傳播委員會"), "doc_number": _fold(fields["發文字號"]),
            "date": _date(fields.get("發文日期", "")), "summary": fields.get("要旨", ""),
            "full_text": _unwrap(body.get_text()), "source_url": url}


# 關務署新頒釋函：一次取得 token、POST 一頁標題，不快取整站清單
_CUSTOMS = "https://web.customs.gov.tw/"


async def _customs_search(http, q: Query):
    async with _session() as own:
        r = await own.get(_CUSTOMS + "multiplehtml/41")
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        form = soup.select_one('form[action="/multiplehtml/41"]')
        if form is None:
            raise RuntimeError("關務署查詢表單已變更")
        data = {x["name"]: x.get("value", "") for x in form.select('input[type=hidden][name]')}
        if not data.get("csrfToken"):
            raise RuntimeError("關務署查詢無法取得 CSRF token")
        data.update(title=q.keyword or q.number_digits, page=str(q.page), pageSize="10")
        r = await own.post(_CUSTOMS + "multiplehtml/41", data=data)
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    count = re.search(r"(\d+)\s*筆資料", _text(soup))
    if count is None:
        raise RuntimeError("關務署未回傳可確認的筆數")
    rows = []
    for a in soup.select('a[href*="singlehtml/41?cntId="]'):
        match = re.search(r"cntId=([0-9a-f]{32})", a["href"])
        if match:
            rows.append({"id": "customs:" + match.group(1), "agency": "財政部關務署", "category": "新頒釋函",
                         "doc_number": "", "date": "", "summary": _text(a)})
    total = int(count.group(1))
    return [_group("財政部關務署", "新頒釋函", total, rows, q.page * 10 < total,
                   note="僅查標題；字號只在沒有關鍵字時當標題查詢；日期條件未套用")]


async def _customs_get(http, native_id):
    url = _CUSTOMS + "singlehtml/41?cntId=" + _check_id(native_id, r"[0-9a-f]{32}")
    r = await http.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    article = soup.select_one(".article-page article")
    if article is None or not _text(article):
        raise LookupError(native_id)
    text = _html_text(str(article))
    number = re.search(r"[^\s\d年月日，。、：]{1,10}字第\s*\d+\s*號", text)
    day = re.search(r"中華民國\s*\d+年\s*\d+月\s*\d+日", text)
    return {"agency": "財政部關務署", "summary": _text(soup.select_one("h2")),
            "doc_number": number.group() if number else "", "date": _date(day.group()) if day else "",
            "full_text": text, "source_url": url}


# 陸委會主站「大陸廣告規範平台」：只讀指定專區的一頁目錄
_MAC_MAIN = "https://www.mac.gov.tw/"
_MAC_LETTERS = _MAC_MAIN + "Content_List.aspx?n=8E8FA34452E8DBC2"


async def _mac_letters_search(http, q: Query):
    r = await public_browser.get(http, _MAC_LETTERS, selector="#base-content .content-list")
    soup = BeautifulSoup(r.text, "html.parser")
    rows = []
    for a in soup.select('#base-content .content-list a[href^="cp.aspx?n="]'):
        match = re.fullmatch(r"cp\.aspx\?n=([A-F0-9]{16})", a["href"])
        title = _text(a)
        if match and any(x in title for x in ("疑義", "參考意見", "函")) and all(k in title for k in q.keyword.split()):
            rows.append({"id": "mac_letters:" + match.group(1), "agency": "大陸委員會", "category": "大陸廣告法令函釋",
                         "doc_number": "", "date": "", "summary": title})
    return [_group("大陸委員會", "大陸廣告法令函釋", len(rows), *_paged(rows, q.page),
                   note="限主站大陸廣告專區的函文與參考意見，僅比對標題；字號、日期條件未套用；不代表全部陸委會函釋")]


async def _mac_letters_get(http, native_id):
    url = _MAC_MAIN + "cp.aspx?n=" + _check_id(native_id, r"[A-F0-9]{16}")
    r = await public_browser.get(http, url, selector="#CCMS_Content .area-editor")
    soup = BeautifulSoup(r.text, "html.parser")
    body = soup.select_one("#CCMS_Content .area-editor")
    text = _html_text(str(body)) if body else ""
    if not text:
        raise LookupError(native_id)
    number = re.search(r"[^\s\d年月日，。、：]{1,10}字第\s*\d+\s*號", text)
    day = re.search(r"中華民國\s*\d+年\s*\d+月\s*\d+日", text)
    title = _text(soup.title).removeprefix("大陸委員會-")
    return {"agency": "大陸委員會", "summary": title, "full_text": text, "source_url": url,
            "doc_number": number.group() if number else "", "date": _date(day.group()) if day else ""}


def _lawsys(key: str) -> tuple:
    return partial(_lawsys_search, key), partial(_lawsys_get, key)


def _exec(key: str) -> tuple:
    return partial(_exec_search, key), partial(_exec_get, key)

# 來源代碼 → (名稱, 可用來指定的機關名／別名, search, get)；順序即去重時的優先序（機關自己的系統優先）
SOURCES = {
    "mac_letters": ("陸委會主站廣告函釋", ("陸委會主站", "陸委會主站廣告函釋", "陸委會", "大陸委員會"), _mac_letters_search, _mac_letters_get),
    "customs": ("關務署新頒釋函", ("關務署", "關務署新頒釋函"), _customs_search, _customs_get),
    "ncc": ("NCC", ("NCC", "ncc", "通傳會", "國家通訊傳播委員會"), _ncc_search, _ncc_get),
    "hakka": ("客委會", ("客委會", "客家委員會"), *_lawsys("hakka")),
    "ocac": ("僑委會", ("僑委會", "僑務委員會"), *_lawsys("ocac")),
    "sports": ("運動部", ("運動部",), *_lawsys("sports")),
    "moj": ("法務部", ("法務部",), _moj_search, _moj_get),
    "mol": ("勞動部", ("勞動部", "勞委會", "行政院勞工委員會"), _mol_search, _mol_get),
    "pcc": ("工程會", ("工程會", "公共工程委員會", "行政院公共工程委員會"), _pcc_search, _pcc_get),
    "mof": ("財政部", ("財政部", "賦稅署"), _mof_search, _mof_get),
    "gcis": ("經濟部商業發展署", ("經濟部", "商業發展署", "商業司"), _gcis_search, _gcis_get),
    "ris": ("內政部戶政司", ("內政部", "戶政司"), _ris_search, _ris_get),
    "nlma": ("內政部國土管理署", ("內政部", "國土管理署", "營建署"), _nlma_search, _nlma_get),
    "land": ("內政部地政司", ("內政部", "地政司", "地政", "內政部地政司"), _land_search, _land_get),
    "nfa": ("內政部消防署", ("內政部", "消防署", "內政部消防署"), _nfa_search, _nfa_get),
    "moi": ("內政部", ("內政部",), *_lawsys("moi")),
    "mohw": ("衛生福利部", ("衛生福利部", "衛福部"), _mohw_search, _mohw_get),
    "fsc": ("金融監督管理委員會", ("金融監督管理委員會", "金管會"), *_lawsys("fsc")),
    "moenv": ("環境部", ("環境部", "環保署"), *_exec("moenv")),
    "mocs": ("銓敘部", ("銓敘部", "考試院"), *_exec("mocs")),
    "csptc": ("公務人員保障暨培訓委員會", ("保訓會", "公務人員保障暨培訓委員會", "考試院"), *_exec("csptc")),
    "moex": ("考選部", ("考選部", "考試院"), *_exec("moex")),
    "exam": ("考試院", ("考試院",), *_exec("exam")),
    "moe": ("教育部", ("教育部",), *_lawsys("moe")),
    "moa": ("農業部", ("農業部", "農委會", "行政院農業委員會"), *_lawsys("moa")),
    "moc": ("文化部", ("文化部",), *_lawsys("moc")),
    "nstc": ("國家科學及技術委員會", ("國科會", "國家科學及技術委員會", "科技部"), *_lawsys("nstc")),
    "cip": ("原住民族委員會", ("原民會", "原住民族委員會"), *_lawsys("cip")),
    "oac": ("海洋委員會", ("海委會", "海洋委員會"), *_lawsys("oac")),
    "ftc": ("公平交易委員會", ("公平會", "公平交易委員會"), *_lawsys("ftc")),
    "mof_rules": ("財政部主管法規", ("財政部", "關務署", "國有財產署", "國庫署", "賦稅署"), *_lawsys("mof_rules")),
    "moea": ("經濟部", ("經濟部", "水利署", "標準檢驗局", "國際貿易署", "能源署", "產業發展署"), *_lawsys("moea")),
    "mac": ("大陸委員會", ("陸委會", "大陸委員會", "行政院大陸委員會"), *_lawsys("mac")),
    "cec": ("中央選舉委員會", ("中選會", "中央選舉委員會"), *_lawsys("cec")),
    "dgpa": ("行政院人事行政總處", ("人事總處", "人事行政總處", "行政院人事行政總處", "人事行政局"), _dgpa_search, _dgpa_get),
    "dgbas": ("行政院主計總處", ("主計總處", "行政院主計總處", "主計處"), *_lawsys("dgbas")),
    "mofa": ("外交部", ("外交部",), *_lawsys("mofa")),
    "vac": ("國軍退除役官兵輔導委員會", ("退輔會", "國軍退除役官兵輔導委員會"), *_lawsys("vac")),
    "nusc": ("核能安全委員會", ("核安會", "核能安全委員會", "原能會", "原子能委員會"), *_lawsys("nusc")),
    "ndc": ("國家發展委員會", ("國發會", "國家發展委員會"), *_lawsys("ndc")),
    "motc": ("交通部", ("交通部",), _motc_search, _motc_get),
    "cbc": ("中央銀行", ("中央銀行", "央行"), _cbc_search, _cbc_get),
    "tipo": ("經濟部智慧財產局", ("經濟部", "智慧財產局", "智慧局", "著作權"), _tipo_search, _tipo_get),
    "tipo_guide": ("經濟部智慧財產局審查基準", ("經濟部", "智慧財產局", "智慧局", "專利", "商標", "審查基準"),
                   ip_guidelines.search, ip_guidelines.get),
    "taipei": ("臺北市政府", ("臺北市", "台北市", "臺北市政府", "台北市政府", "北市"), _taipei_search, _taipei_get),
    "ntpc": ("新北市政府", ("新北市", "新北市政府", "新北"), _ntpc_search, _ntpc_get),
    "bsmi": ("經濟部標準檢驗局", ("標準檢驗局", "標檢局", "經濟部標準檢驗局", "經濟部"), _bsmi_search, _bsmi_get),
    "sunshine": ("監察院陽光法令主題網", ("監察院", "陽光法令", "政治獻金", "利益衝突迴避", "財產申報"),
                 _sunshine_search, _sunshine_get),
    "cpc": ("行政院消費者保護處", ("消保處", "行政院消費者保護處", "消保會", "消費者保護處"), _cpc_search, _cpc_get),
    "fint": ("司法院法學資料檢索系統", ("司法院",), _fint_search, _fint_get),
    "gazette": ("行政院公報", ("行政院公報", "公報"), _gazette_search, _gazette_get),
}


# 多為機關內部作業要點：不指定機關時不查，免得每次搜尋都多打這些站、結果混入不相干的要點
_NAMED_ONLY = {"mofa", "vac", "nusc", "ndc", "hakka", "ocac", "sports", "ncc", "customs", "mac_letters"}


def resolve_sources(agency: str) -> tuple[list[str], list[str]]:
    """機關名稱 → (來源代碼, 沒有專屬來源、改用行政院公報標題篩選的機關名)。空字串 = 指名才查以外的全部來源。"""
    if not agency.strip():
        return [k for k in SOURCES if k not in _NAMED_ONLY], []
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
        cache_key = f"interp:v2:{interpretation_id}"  # v2 起有效力標示，舊快取沒有
        cached = await self.cache.get_judgment(cache_key)
        if cached and not cached.get("full_text_truncated"):
            return {"success": True, "cached": True, **cached}
        label, _, _, get = SOURCES[key]
        try:
            data = await get(self.http, native_id)
        except LookupError:
            return error_response(f"{label}查無此函釋：{interpretation_id}")
        except (httpx.HTTPError, ValueError, RuntimeError, KeyError, ElementTree.ParseError) as e:
            return error_response(f"{label}連線或解析失敗：{type(e).__name__}: {e}")
        full = data.get("full_text", "")
        data["full_text"], data["full_text_truncated"] = full, False
        data = {"id": interpretation_id, "source": label, **data}
        await self.cache.set_judgment(cache_key, data, source="agency_interpretation", ttl=STATUS_TTL)
        return {"success": True, "cached": False, **data}
