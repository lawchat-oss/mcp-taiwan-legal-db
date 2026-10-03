"""法學研究文獻查詢：司法院專題研究報告、國圖期刊論文索引、GRB 研究計畫、開放取用法學期刊全文

| 代碼 | 來源 | 內容 |
|---|---|---|
| jirs | 司法院電子出版品檢索 jirs.judicial.gov.tw | 專題研究報告（含司法研究年報），全文 PDF 按章分檔 |
| ncl | 國家圖書館 臺灣期刊論文索引 tpl.ncl.edu.tw | 期刊論文書目、摘要；已授權者附全文 PDF |
| grb | 政府研究資訊系統 GRB | 政府補助研究計畫書目與摘要（成果報告下載需 reCAPTCHA，只給連結） |
| journals | 中研院法學期刊、政大法學評論、臺大法學論叢 | 以國圖索引檢索，全文取自期刊官網的免費 PDF |

id 一律為「來源代碼:原站識別碼」，例如 ncl:A15001353、grb:13540821、
jirs:202603:韓國量刑準則及保護監護制度之研究（司法院沒有穩定的報告代碼可從列表取得，以年月＋報告名稱定位）。
只做使用者觸發的單次查詢，不批次抓取，不碰付費資料庫。
國圖授權全文僅供個人查閱、不得轉存其他資料庫：只回傳給呼叫端，不寫入快取。
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from functools import partial
from datetime import date, datetime
from urllib.parse import unquote, urljoin

import httpx
from bs4 import BeautifulSoup

from mcp_server.cache.db import CacheDB
from mcp_server.tools import fint
from mcp_server.tools._errors import error_response
from mcp_server.tools.agency_interpretations import _text
from mcp_server.tools.pdf_text import pdf_to_text

logger = logging.getLogger(__name__)

USER_AGENT = fint.USER_AGENT

JIRS_BASE = "https://jirs.judicial.gov.tw/JudLib/"
NCL_BASE = "https://tpl.ncl.edu.tw/NclService/"
GRB_API = "https://grbdef.stpi.niar.org.tw/searcher"
GRB_PLAN_URL = "https://www.grb.gov.tw/search/planDetail?id={}"
IIAS_BASE = "https://www.iias.sinica.edu.tw/"
NCCU_BASE = "http://review.law.nccu.edu.tw"  # HTTPS 憑證過期，http 可直接用
NTU_BASE = "https://www.law.ntu.edu.tw/center/"

_CJK = "㐀-鿿豈-﫿"
_REQUIRED = ("title", "authors", "venue", "date", "abstract", "source_url")


def _norm(s: str) -> str:
    """比對用：只留中英數字（破折號、標點、空白寫法各站不同）。"""
    return re.sub(f"[^0-9A-Za-z{_CJK}]", "", s or "")


def _ym(s: str) -> str:
    """「2014.10[民103.10]」「民國 115 年 03 月」「民國 113 年 11 月 30 日」→ 2014-10、2026-03、2024-11-30。"""
    m = re.search(r"(\d{2,4})\s*[.年]\s*(\d{1,2})(?:\s*月\s*(\d{1,2})\s*日)?", s or "")
    if not m:
        return ""
    y = int(m.group(1))
    day = f"-{int(m.group(3)):02d}" if m.group(3) else ""
    return f"{y + 1911 if y < 1911 else y:04d}-{int(m.group(2)):02d}{day}"


def _plain(s: str | None) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def _doc(full_text: str, **fields) -> dict:
    return {**{k: v for k, v in fields.items() if v or k in _REQUIRED},
            "full_text": full_text, "full_text_truncated": False}


def _group(key: str, total: int, items: list[dict], has_more: bool, note: str = "") -> dict:
    group = {"source": SOURCES[key][0], "category": _CATEGORY[key], "total": total, "items": items,
             "has_more": has_more}
    return {**group, "note": note} if note else group


async def _soup(http: httpx.AsyncClient, url: str, **kw) -> BeautifulSoup:
    r = await http.get(url, **kw)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


async def _pdf_text(http: httpx.AsyncClient, url: str) -> str:
    r = await http.get(url)
    r.raise_for_status()
    start = r.content.find(b"%PDF")  # 司法院的下載檔前面多一段 HTML
    return await asyncio.to_thread(pdf_to_text, r.content[start:]) if start >= 0 else ""


# ─────────────────────────────────────────────────────────────
# 司法院 專題研究報告（含司法研究年報）：無狀態 GET，每頁 20 筆
# ─────────────────────────────────────────────────────────────

_JIRS_ID = re.compile(r"(\d{6}):(\S.{0,199})")
_JIRS_FIELDS = {"報告名稱": "title", "報告人": "authors", "報告日期": "date", "資料來源": "venue"}


def _jirs_params(**filters) -> dict:
    return {"S": "V", "scode": "V", **{k: v for k, v in filters.items() if v}}


async def _jirs_list(http, params: dict) -> tuple[int, list[dict]]:
    soup = await _soup(http, JIRS_BASE + "EBookQRY03.asp", params=params)
    m = re.search(r"共\s*(\d+)\s*筆", soup.get_text())
    if not m:
        raise ValueError("司法院電子出版品查詢頁格式不符（找不到筆數）")
    rows = []
    for a in soup.select('a[href*="EBookQry04.asp"]'):
        seq = re.search(r"seq=(\d+)", a["href"])
        tr = a.find_parent("tr")
        info = tr.find_next_siblings("tr", limit=2) if tr else []  # 報告日期、報告人
        if seq and len(info) == 2:
            rows.append({"seq": seq.group(1), "title": re.sub(r"\s+", " ", a.get_text()).strip(),
                         "date": _ym(_text(info[0])),
                         "authors": re.split(r"[、，,;；\s]+", _text(info[1].find_all("td")[-1]))})
    return int(m.group(1)), rows


async def _jirs_search(http, keyword: str, year_from: int, year_to: int, page: int) -> dict:
    words = keyword.split()
    dates = {"sdate": f"{year_from + 1911}0100" if year_from else "", "edate": f"{year_to + 1911}1231" if year_to else ""}
    # 篇名欄位不接受空白與運算子：單一詞先比對篇名，多詞或篇名無結果時改比對全文（& = AND）
    tries = ([{"sname": words[0]}] if len(words) == 1 else []) + [{"keyword": "&".join(words)}]
    for t in tries:
        total, rows = await _jirs_list(http, _jirs_params(page=page, **t, **dates))
        if total:
            break
    items = [{
        "id": f"jirs:{r['date'].replace('-', '')[:6] or '000000'}:{r['title']}", "title": r["title"],
        "authors": [a for a in r["authors"] if a], "venue": "司法院專題研究報告", "date": r["date"], "summary": "",
    } for r in rows]
    note = "以全文關鍵字比對（篇名無符合或輸入多個詞），排序非依相關度" if words and "keyword" in t else ""
    return _group("jirs", total, items, page * 20 < total, note)


async def _jirs_get(http, native_id: str) -> dict:
    m = _JIRS_ID.fullmatch(native_id)
    if not m:
        raise LookupError(native_id)
    ym, title = m.groups()
    dates = {"sdate": ym + "00", "edate": ym + "31"} if ym != "000000" else {}
    params = _jirs_params(sname=max(title.split(), key=len), **dates)  # 篇名欄位不接受空白
    _, rows = await _jirs_list(http, params)
    row = next((r for r in rows if _norm(r["title"]) == _norm(title)), None)
    if row is None:
        raise LookupError(native_id)
    soup = await _soup(http, JIRS_BASE + "EBookQry04.asp", params={**params, "seq": row["seq"]})
    fields = {}
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        label = re.sub(r"[\s：:]", "", tds[0].get_text()) if len(tds) == 2 else ""
        if label in _JIRS_FIELDS:
            fields[_JIRS_FIELDS[label]] = _text(tds[1])
    links = soup.select('a[href*="EBookDownload.asp"]')
    chapters = [{"title": re.sub(r"\.pdf\b.*$", "", _text(a), flags=re.I), "pdf_url": urljoin(JIRS_BASE, a["href"])}
                for a in links]
    parts: list[str] = []
    for ch in chapters:  # 一章一個 PDF：完整讀取所選報告的章節
        parts.append(f"【{ch['title']}】\n{await _pdf_text(http, ch['pdf_url'])}")
    lk = re.search(r"lk=([^&]+)", links[0]["href"]) if links else None
    return _doc(
        "\n\n".join(parts), title=fields.get("title", row["title"]),
        authors=[a for a in re.split(r"[、，,;；\s]+", fields.get("authors", "")) if a] or row["authors"],
        venue=fields.get("venue") or "司法院專題研究報告", date=_ym(fields.get("date", "")) or row["date"],
        abstract="", source_url=str(httpx.URL(JIRS_BASE + "EBookQRY03.asp", params=params)),
        report_key=unquote(lk.group(1)) if lk else "", chapters=chapters,
    )


# ─────────────────────────────────────────────────────────────
# 國家圖書館 臺灣期刊論文索引：GET 查詢，每頁 20 筆
# ─────────────────────────────────────────────────────────────

NCL_PAGE = 20
NCL_MAX_ROWS = 300  # 超過 300 筆時國圖只列前 300 筆
NCL_LICENSE = "全文由作者／出版者授權國家圖書館提供，僅供個人研究查閱，請勿轉載或存入其他資料庫；本工具不快取全文。"
_NCL_ID = re.compile(r"[A-Z]\d{6,12}")
_NCL_TITLE = re.compile(r"(.*[^\x00-\x7f].*?)[:=][\x00-\x7f]*", re.S)  # 「中文題名:English title」


def _ncl_title(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    m = _NCL_TITLE.fullmatch(s)
    return (m.group(1) if m else s).strip().rstrip(":=")


def _names(names: list[str]) -> list[str]:
    """國圖每位作者列中文名與羅馬拼音，有中文名時只留中文名。"""
    names = [n.strip() for n in names if n.strip()]
    return [n for n in names if re.search(f"[{_CJK}]", n)] or names


def _volume(s: str) -> str:
    m = re.match(r"(\S+)\s+\d{4}\.", s or "")  # 「190  2014.10[民103.10]」「54:1 2025.03…」
    return m.group(1) if m else ""


def _ncl_terms(keyword: str) -> list[tuple[str, str, str]]:
    """空白分隔的詞以 AND 串接；「刊名:xxx」限定刊名。"""
    return [("0", "JT", w[3:]) if re.match("刊名[:：].", w) else ("0", "*", w) for w in keyword.split()]


async def _ncl_query(http, rows: list[tuple[str, str, str]], year_from: int, year_to: int, page: int, key: str) -> dict:
    if not rows:
        return _group(key, 0, [], False, "請提供關鍵字")
    params: dict = {}
    for n, (op, field, term) in enumerate(rows):  # 布林條件由左至右結合
        if n:
            params[f"q[{n}].o"] = op
        params[f"q[{n}].f"], params[f"q[{n}].i"] = field, term
    params.update(directQuery="true", nestedSearch="false", queryType="normal", page=page, pageSize=NCL_PAGE,
                  orderField="score", orderType="desc")
    if year_from:
        params["pys"] = year_from + 1911
    if year_to:
        params["pye"] = year_to + 1911
    soup = await _soup(http, NCL_BASE + "JournalContent", params=params)
    m = re.search(r"檢索結果筆數\s*\((\d+)\)", soup.get_text())
    if not m:
        raise ValueError("國圖期刊索引查詢頁格式不符（找不到筆數）")
    items = []
    for li in soup.select("ul.page-list > li"):
        a = li.select_one("a.articleTitle")
        sysid = re.search(r"SysId=(\w+)", a.get("href", "")) if a else None
        if not sysid:
            continue
        vol = _text(li.select_one("p.volumeNo"))
        pages = next((t for t in map(_text, li.select("p")) if re.match(r"頁\s*\d", t)), "")
        item = {
            "id": f"{key}:{sysid.group(1)}", "title": _ncl_title(a.get("title") or _text(a)),
            "authors": _names([x.get("title") or _text(x) for x in li.select("p.authorName a")]),
            "venue": _text(li.select_one("p.journalName")), "volume": _volume(vol), "pages": pages.lstrip("頁"),
            "date": _ym(vol), "summary": "",
        }
        if key == "ncl":  # 開放期刊的全文來自官網，國圖有無授權不重要
            item["has_full_text"] = bool(li.select_one('a[href*="pdfdownload"]'))
        items.append(item)
    total = int(m.group(1))
    note = f"國圖只列出前 {NCL_MAX_ROWS} 筆，請加關鍵字或年份縮小範圍" if total > NCL_MAX_ROWS else ""
    return _group(key, total, items, page * NCL_PAGE < min(total, NCL_MAX_ROWS), note)


async def _ncl_search(http, keyword: str, year_from: int, year_to: int, page: int) -> dict:
    return await _ncl_query(http, _ncl_terms(keyword), year_from, year_to, page, "ncl")


async def _ncl_detail(http, sysid: str) -> tuple[dict, str]:
    """回傳（書目與摘要, 國圖全文 PDF 連結；未授權者為空字串）。"""
    if not _NCL_ID.fullmatch(sysid):
        raise LookupError(sysid)
    url = f"{NCL_BASE}JournalContentDetail?SysId={sysid}"
    soup = await _soup(http, url)
    title = soup.select_one("#articleTitle")
    if title is None:  # 查無此筆時轉回首頁
        raise LookupError(sysid)
    # 頁面 HTML 缺 <tr>，不能逐列取，改用 th 的下一個 td
    field = {_norm(th.get_text()): _text(th.find_next_sibling("td"))
             for th in soup.select("table.data-detail th") if th.find_next_sibling("td")}
    meta = {
        "title": _ncl_title(_text(title)),
        "authors": _names([a.get("title") or _text(a) for a in soup.select("#authorName a")]),
        "venue": _text(soup.select_one("#journalName")), "volume": _volume(field.get("卷期", "")),
        "pages": field.get("頁次", "").lstrip("頁"), "date": _ym(field.get("卷期", "")),
        "keywords": [k.strip() for k in field.get("關鍵詞", "").split(";") if k.strip()],
        "abstract": "\n\n".join(x for x in (field.get("中文摘要"), field.get("英文摘要")) if x),
        "source_url": url,
    }
    pdf = soup.select_one('a[href*="pdfdownload"]')
    return meta, urljoin(NCL_BASE, pdf["href"]) if pdf else ""


async def _ncl_get(http, sysid: str) -> dict:
    meta, pdf_url = await _ncl_detail(http, sysid)
    text = await _pdf_text(http, pdf_url) if pdf_url else ""
    if not pdf_url:
        note = "國圖未取得此文的公開全文授權，只提供書目與摘要。"
    elif not text:
        note = "全文 PDF 為掃描影像、無文字層，請開 pdf_url 閱讀。" + NCL_LICENSE
    else:
        note = NCL_LICENSE
    return _doc(text, **meta, pdf_url=pdf_url, note=note)


# ─────────────────────────────────────────────────────────────
# GRB 政府研究資訊系統：JSON API；成果報告全文需 reCAPTCHA，不處理
# ─────────────────────────────────────────────────────────────

GRB_PAGE = 10  # rowsPerPage 只接受 10/50/100/200
GRB_HEADERS = {"Referer": "https://www.grb.gov.tw/"}  # 沒帶 Referer 會回 500


async def _grb_search(http, keyword: str, year_from: int, year_to: int, page: int) -> dict:
    r = await http.post(GRB_API, headers=GRB_HEADERS, data={
        "keyword": keyword, "queryType": "GRB05", "projNums": "", "planYearSt": year_from or 0,
        "planYearEn": year_to or 999, "excuOrganPrefix": "", "planOrgans": "", "nowPage": page,
        "rowsPerPage": GRB_PAGE, "orderType": "SIMILARITY",
    })
    r.raise_for_status()
    data = r.json()
    items = [{
        "id": f"grb:{x['id']}", "title": _plain(x.get("title")),
        "authors": x.get("hostNameList") or x.get("host1NameC") or [], "venue": x.get("excuOrganName") or "",
        "date": str(x["planYear"] + 1911) if x.get("planYear") else "", "summary": _plain(x.get("abstractC"))[:200],
    } for x in data.get("obj") or []]
    total = int(data.get("totalRows") or 0)
    return _group("grb", total, items, page * GRB_PAGE < total)


async def _grb_get(http, native_id: str) -> dict:
    if not re.fullmatch(r"\d{1,12}", native_id):
        raise LookupError(native_id)
    r = await http.post(f"{GRB_API}/{native_id}", json={}, headers=GRB_HEADERS)
    if r.status_code == 500:  # 查無此 id 時站方回 500
        raise LookupError(native_id)
    r.raise_for_status()
    d = r.json()
    report = d.get("grb05Report2") or {}
    sections = [("計畫摘要", d.get("abstractC")), ("Abstract", d.get("abstractE")),
                ("成果報告摘要", report.get("abstractC")), ("Report abstract", report.get("abstractE"))]
    period = d.get("periodStym") or ""  # 民國年月，如 11008
    return _doc(
        "", title=_plain(d.get("title") or d.get("pnchDesc")),
        authors=d.get("hostNameList") or d.get("host1NameC") or [],
        venue="／".join(x for x in (d.get("planOrganName"), d.get("excuOrganName")) if x),
        date=f"{int(period[:-2]) + 1911}-{period[-2:]}" if re.fullmatch(r"\d{4,5}", period)
        else (str(d["planYear"] + 1911) if d.get("planYear") else ""),
        abstract="\n\n".join(f"【{t}】\n{_plain(v)}" for t, v in sections if _plain(v)),
        source_url=GRB_PLAN_URL.format(native_id), plan_no=d.get("planNo"), keywords=d.get("keywordC"),
        note="GRB 只提供書目與摘要；完整成果報告須在 source_url 頁面通過 reCAPTCHA 後下載。",
    )


# ─────────────────────────────────────────────────────────────
# 開放取用法學期刊：國圖索引限定刊名檢索，全文到期刊官網依卷期／篇名找 PDF
# ─────────────────────────────────────────────────────────────

def _pick(cands: list[tuple[str, str]], meta: dict) -> str | None:
    """先比篇名（前 12 字，避開副標題寫法差異），比不到再比作者；作者同期有多篇時無法確定，不猜。"""
    key = _norm(meta["title"])[:12]
    hit = next((url for text, url in cands if key and key in _norm(text)), None)
    if hit:
        return hit
    by_author = {url for text, url in cands if any(len(a) > 1 and a in text for a in meta["authors"])}
    return by_author.pop() if len(by_author) == 1 else None


async def _iias_pdf(http, meta: dict) -> str | None:
    soup = await _soup(http, IIAS_BASE + "publication_list/9")
    issue = next((a["href"] for a in soup.select('a[href*="publication_post/"][title]')
                  if re.sub(r"\s", "", a["title"]) == f"第{meta['volume']}期"), None)
    if not issue:
        return None
    soup = await _soup(http, urljoin(IIAS_BASE, issue))
    return _pick([(_text(ch), urljoin(IIAS_BASE, a["href"]))
                  for ch in soup.select(".chapter") for a in ch.select("ul.download a[href]")[:1]], meta)


async def _nccu_pdf(http, meta: dict) -> str | None:
    for page in (1, 2, 3):  # 每頁 30 期，2009 年起共 3 頁
        soup = await _soup(http, NCCU_BASE + "/zh_tw/articles", params={"page_no": page})
        issue = next((a["href"] for a in soup.select('a[href^="/zh_tw/articles/"][title]')
                      if f"第{meta['volume']}期" in a["title"]), None)
        if issue:
            break
    else:
        return None
    soup = await _soup(http, urljoin(NCCU_BASE, issue))
    # 連結文字是篇名，檔名含作者與頁碼
    return _pick([(_text(a) + unquote(a["href"]), urljoin(NCCU_BASE, a["href"]))
                  for a in soup.select('a[href*="/uploads/asset/"]')], meta)


async def _ntu_pdf(http, meta: dict) -> str | None:
    # 只讀指定卷的一頁目錄、該期及選中的一篇；不遍歷歷年卷期。
    volume = re.fullmatch(r"(\d{1,3}):(\d{1,2})", meta["volume"])
    if not volume:
        return None
    vol, number = volume.groups()
    soup = await _soup(http, NTU_BASE + f"index.php/itemlist/tag/第{vol}卷")
    issue = next((a["href"] for a in soup.select('a[href^="/center/"][href*="/item/"]')
                  if f"第{vol}卷第{number}期" in re.sub(r"\s", "", _text(a))), None)
    if not issue:
        return None
    soup = await _soup(http, urljoin(NTU_BASE, issue))
    candidates = []
    for a in soup.select('.itemAttachments a[href^="/center/media/k2/attachments/"]'):
        label = a.get("title", "") + unquote(a["href"])
        # 同期可能同時放中文摘要、英文摘要與定稿；只取明確標為全文／定稿的檔案。
        if re.search(r"全文|定稿", label) and not re.search(r"摘要|abstract", label, re.I):
            candidates.append((label, urljoin(NTU_BASE, a["href"])))
    return _pick(candidates, meta)


JOURNALS = {"中研院法學期刊": _iias_pdf, "政大法學評論": _nccu_pdf, "國立臺灣大學法學論叢": _ntu_pdf}


async def _journals_search(http, keyword: str, year_from: int, year_to: int, page: int,
                           journals: tuple[str, ...] = tuple(JOURNALS)) -> dict:
    rows = [("1", "JT", j) for j in journals] + _ncl_terms(keyword)  # (刊名1 OR 刊名2 …) AND 關鍵字
    return await _ncl_query(http, rows, year_from, year_to, page, "journals")


async def _journals_get(http, sysid: str) -> dict:
    meta, _ = await _ncl_detail(http, sysid)  # 只用國圖書目與摘要，全文取自期刊官網
    resolve = next((f for name, f in JOURNALS.items() if name in meta["venue"]), None)
    pdf_url, text, note = None, "", ""
    if resolve and re.fullmatch(r"\d+(?::\d+)?", meta["volume"]):
        try:
            pdf_url = await resolve(http, meta)
            text = await _pdf_text(http, pdf_url) if pdf_url else ""
        except (httpx.HTTPError, ValueError) as e:
            logger.warning("期刊官網全文解析失敗 %s: %s", sysid, e)
            note = f"期刊官網連線失敗（{type(e).__name__}: {str(e)[:80]}），只提供國圖摘要。"
    if not text and not note:
        note = ("官網 PDF 無法擷取文字，請開 pdf_url 閱讀。" if pdf_url
                else "期刊官網找不到本文全文（最新一兩期或早期卷期可能未開放），只提供國圖摘要。")
    return _doc(text, **meta, pdf_url=pdf_url, note=note)


# ─────────────────────────────────────────────────────────────
# 對外介面
# ─────────────────────────────────────────────────────────────

SOURCES = {
    "jirs": ("司法院專題研究報告", ("司法研究年報", "專題研究報告", "研究報告", "司法院"), _jirs_search, _jirs_get),
    "ncl": ("國家圖書館臺灣期刊論文索引", ("國圖", "國家圖書館", "期刊", "期刊論文", "期刊索引"), _ncl_search, _ncl_get),
    "grb": ("政府研究資訊系統", ("GRB", "研究計畫", "國科會", "政府研究計畫"), _grb_search, _grb_get),
    "journals": ("開放取用法學期刊", ("開放期刊", "法學期刊", "全文期刊", "臺大法學論叢", *JOURNALS), _journals_search, _journals_get),
}
_CATEGORY = {"jirs": "專題研究報告", "ncl": "期刊論文", "grb": "研究計畫", "journals": "期刊論文（官網全文）"}
_NO_CACHE = {"ncl"}  # 國圖授權全文不得轉存


def resolve_sources(source: str) -> list[str] | None:
    if not source.strip():
        return list(SOURCES)
    keys = []
    for name in [n for n in re.split(r"[,，、\s]+", source.strip()) if n]:
        hit = [k for k, (label, aliases, *_) in SOURCES.items() if name in (k, label) or name in aliases]
        if not hit:
            return None
        keys += hit
    return list(dict.fromkeys(keys))


class LiteratureClient:
    def __init__(self, cache: CacheDB):
        self.cache = cache
        # 司法院 PDF 偶爾要等一分鐘以上才開始傳
        self.http = httpx.AsyncClient(timeout=90.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True)

    async def close(self):
        await self.http.aclose()

    async def search(self, keyword: str, source: str = "", year_from: int = 0, year_to: int = 0, page: int = 1) -> dict:
        source = source.replace("臺大法學論叢", "國立臺灣大學法學論叢")
        keys = resolve_sources(source)
        if keys is None:
            return error_response(f"不支援的來源「{source}」",
                                  supported_sources=[label for label, *_ in SOURCES.values()])
        # 指定單一期刊時只查那一本（resolve_sources 只會回傳 journals）
        journals = tuple(n for n in re.split(r"[,，、\s]+", source) if n in JOURNALS) or tuple(JOURNALS)
        params = {"tool": "literature", "keyword": keyword, "sources": keys, "journals": journals,
                  "year_from": year_from, "year_to": min(year_to, date.today().year - 1911) if year_to else 0,
                  "page": page}
        cached = await self.cache.get_search(params)
        if cached:
            return {**cached, "cached": True}

        async def run(key: str) -> dict:
            label, _, search, _ = SOURCES[key]
            if key == "journals":
                search = partial(search, journals=journals)
            try:
                return await search(self.http, keyword, year_from, params["year_to"], page)
            except Exception as e:  # 單一來源掛掉不拖垮其他來源
                logger.warning("文獻搜尋失敗 %s: %s", key, e, exc_info=not isinstance(e, httpx.HTTPError))
                return {"source": label, "error": f"{type(e).__name__}: {e}"}

        groups = await asyncio.gather(*(run(k) for k in keys))
        # 同一篇同時出現在國圖與開放期刊時只留開放期刊那筆（可取全文）
        oa = {i["id"].partition(":")[2] for k, g in zip(keys, groups) if k == "journals" for i in g.get("items", [])}
        result = {
            "success": True, "keyword": keyword, "page": page,
            "categories": [{k: v for k, v in g.items() if k != "items"}
                           | ({"returned": len(g["items"])} if "items" in g else {}) for g in groups],
            "total_count": sum(g.get("total", 0) for g in groups),
            # 各來源已依相關度排序，不跨來源重排
            "results": [i for g in groups for i in g.get("items", [])
                        if not (i["id"].startswith("ncl:") and i["id"][4:] in oa)],
            "timestamp": datetime.now().isoformat(),
        }
        if not any("error" in g for g in groups):
            await self.cache.set_search(params, result)
        return result

    async def get(self, item_id: str) -> dict:
        key, _, native_id = item_id.partition(":")
        if key not in SOURCES or not native_id:
            return error_response(f"id 格式錯誤：「{item_id}」，請使用文獻搜尋回傳的 id")
        cache_key = f"literature:{item_id}"
        if key not in _NO_CACHE:
            cached = await self.cache.get_judgment(cache_key)
            if cached and not cached.get("full_text_truncated"):
                return {"success": True, "cached": True, **cached}
        label, _, _, get = SOURCES[key]
        try:
            data = await get(self.http, native_id)
        except LookupError:
            return error_response(f"{label}查無此筆：{item_id}")
        except (httpx.HTTPError, ValueError) as e:
            return error_response(f"{label}連線或解析失敗：{type(e).__name__}: {e}")
        data = {"id": item_id, "source": label, **data}
        # 沒有全文的結果（官網暫時失敗、尚未公開）不長期快取，下次再試
        if key not in _NO_CACHE and data.get("full_text"):
            await self.cache.set_judgment(cache_key, data, source="literature")
        return {"success": True, "cached": False, **data}
