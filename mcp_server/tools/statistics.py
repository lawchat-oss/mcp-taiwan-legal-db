"""官方統計表查詢：司法院司法統計（年報、月報）、法務部法務統計常用統計表、司法官學院《犯罪狀況及其分析》

| 代碼 | 來源 | 內容 |
|---|---|---|
| judicial | 司法院 司法統計年報 www.judicial.gov.tw | 各年度統計表（ODS／PDF） |
| judicial_monthly | 司法院 司法統計月報 | 指定年度最新一個月的統計表 |
| moj | 法務部 法務統計資訊網 rjsd.moj.gov.tw | 常用統計表（HTML 表格，滾動更新） |
| cprc | 法務部司法官學院 犯罪防治研究中心 | 犯罪狀況及其分析年度報告（篇章 PDF＋數據 XLSX） |

id 一律為「來源代碼:原站識別碼」：judicial:267552-1be7…（檔案編號-雜湊，兩者缺一不能下載）、
moj:INF_COMMON_P/807、cprc:45180（報告）、cprc:45180/20215121（報告中的單一檔案）。
清單頁在行程內快取一天；只在查詢時抓取，不批次下載。
"""

from __future__ import annotations

import asyncio
import io
import itertools
import logging
import re
import time
import zipfile
from datetime import datetime
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from mcp_server.cache.db import CacheDB
from mcp_server.tools import fint
from mcp_server.tools._errors import error_response
from mcp_server.tools.admin_decisions import _roc_to_iso
from mcp_server.tools.pdf_text import CJK, pdf_to_text

logger = logging.getLogger(__name__)

USER_AGENT = fint.USER_AGENT
MAX_TEXT = 60000
PAGE_SIZE = 20
MAX_SHEETS = 12
MAX_COLS = 200
_TTL = 86400.0
_memo: dict[str, tuple[float, object]] = {}


async def _memo_get(key: str, load):
    hit = _memo.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    value = await load()
    _memo[key] = (time.time(), value)
    return value


async def _soup(http: httpx.AsyncClient, url: str, **params) -> BeautifulSoup:
    r = await http.get(url, params=params or None)
    if r.status_code == 404:
        raise LookupError(url)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def _match(keyword: str, *texts: str) -> bool:
    hay = re.sub(r"\s+", "", "".join(texts))
    return all(k in hay for k in keyword.split())


def _paged(items: list, page: int) -> tuple[list, bool]:
    return items[(page - 1) * PAGE_SIZE: page * PAGE_SIZE], page * PAGE_SIZE < len(items)


def _group(source: str, category: str, items: list, page: int, **extra) -> dict:
    shown, more = _paged(items, page)
    return {"source": source, "category": category, "total": len(items), "items": shown, "has_more": more, **extra}


# ─────────────────────────────────────────────────────────────
# 表格 → 文字（ODS／XLSX／HTML 共用）
# ─────────────────────────────────────────────────────────────

_HAN = re.compile("[㐀-鿿]")
_T = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_X = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _cell(s: str) -> str:
    """「總 收 容 人 數」→「總收容人數」：去掉中文字旁的排版空白。"""
    s = re.sub(r"\s+", " ", s or "").strip()
    return re.sub(rf"(?<=[{CJK}]) | (?=[{CJK}])", "", s)


def rows_to_text(rows) -> str:
    lines = []
    for row in rows:
        row = list(row)
        while row and not row[-1]:
            row.pop()
        if row:
            lines.append(" | ".join(row))
    return "\n".join(lines)


def ods_sheets(blob: bytes):
    """逐張工作表產生 (名稱, 列)；重複列／欄（空白欄常重複上萬次）有上限。"""
    root = ElementTree.fromstring(zipfile.ZipFile(io.BytesIO(blob)).read("content.xml"))
    for table in root.iter(_T + "table"):
        rows = []
        for tr in table.iter(_T + "table-row"):
            cells = []
            for c in tr:
                if c.tag in (_T + "table-cell", _T + "covered-table-cell"):
                    paras = [p for p in ("".join(x.itertext()).strip() for x in c.iter(_X + "p")) if p]
                    # 司法統計是中英對照：同一格有中文時只留中文段落
                    text = _cell(" ".join([p for p in paras if _HAN.search(p)] or paras))
                    cells += [text] * min(int(c.get(_T + "number-columns-repeated", 1)), MAX_COLS)
            if any(cells):
                rows += [cells[:MAX_COLS]] * min(int(tr.get(_T + "number-rows-repeated", 1)), 100)
        yield table.get(_T + "name", ""), rows


def _col(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]*", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def _num(v: str) -> str:
    try:
        f = float(v)
    except ValueError:
        return v
    return str(int(f)) if f.is_integer() else str(round(f, 4))


def xlsx_sheets(blob: bytes):
    z = zipfile.ZipFile(io.BytesIO(blob))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        shared = ["".join(t.text or "" for t in si.iter(_S + "t"))
                  for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")).iter(_S + "si")]
    rels = {r.get("Id"): r.get("Target", "") for r in ElementTree.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
    for sheet in ElementTree.fromstring(z.read("xl/workbook.xml")).iter(_S + "sheet"):
        target = rels.get(sheet.get(_R + "id"), "")
        path = target.lstrip("/") if target.startswith("/") else "xl/" + target
        rows = []
        for row in ElementTree.fromstring(z.read(path)).iter(_S + "row"):
            cells: dict[int, str] = {}
            for c in row.iter(_S + "c"):
                kind, v = c.get("t"), c.findtext(_S + "v") or ""
                if kind == "s" and v:
                    v = shared[int(v)]
                elif kind == "inlineStr":
                    v = "".join(t.text or "" for t in c.iter(_S + "t"))
                elif kind != "str":
                    v = _num(v)
                cells[_col(c.get("r", "")) if c.get("r") else len(cells)] = _cell(v)
            width = min(max(cells, default=-1) + 1, MAX_COLS)
            rows.append([cells.get(i, "") for i in range(width)])
        yield sheet.get("name", ""), rows


SHEETS_OMITTED = f"（只列出前 {MAX_SHEETS} 張工作表，其餘請開原始檔）"


def sheets_to_text(sheets) -> str:
    parts, size, sheets = [], 0, iter(sheets)
    for name, rows in itertools.islice(sheets, MAX_SHEETS):
        body = rows_to_text(rows)
        if body:
            parts.append(f"## {name}\n{body}")
            size += len(parts[-1])
            if size > MAX_TEXT:
                break
    else:
        if next(sheets, None) is not None:
            parts.append(SHEETS_OMITTED)
    return "\n\n".join(parts)


def _span(v) -> int:
    return int(v) if str(v or "").isdigit() and int(v) > 0 else 1


def html_table_rows(table) -> list[list[str]]:
    """展開 colspan／rowspan；被上方跨列格子佔住的欄位補空字串，欄名與數值才對得上。"""
    rows, carry = [], {}  # 欄位 → 還要往下佔用的列數
    for tr in table.select("tr"):
        row, col = [], 0
        for c in tr.find_all(["td", "th"], recursive=False):
            while carry.get(col):
                row.append("")
                carry[col] -= 1
                col += 1
            down = _span(c.get("rowspan"))
            for i in range(min(_span(c.get("colspan")), MAX_COLS)):
                row.append(_cell(c.get_text(" ")) if i == 0 else "")
                if down > 1:
                    carry[col] = down - 1
                col += 1
        for k in [k for k, n in carry.items() if k >= col and n]:  # 本列尾端仍被佔住的欄位
            carry[k] -= 1
        rows.append(row)
    return rows


def html_tables_text(soup: BeautifulSoup) -> str:
    """只取最內層表格（法務統計把同一張表包在外層表格裡，直接取會重複一次），內容相同的再去重。"""
    seen, parts = set(), []
    for t in soup.select("table"):
        if t.find("table"):
            continue
        text = rows_to_text(html_table_rows(t))
        if text and text not in seen:
            seen.add(text)
            parts.append(text)
    return "\n\n".join(parts)


def _file_text(blob: bytes) -> dict:
    """依檔頭判斷：PDF → full_text；ODS／XLSX → table_text；其他回空字串。"""
    if blob[:4] == b"%PDF":
        return {"full_text": pdf_to_text(blob)}
    if blob[:2] == b"PK":
        names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
        if "content.xml" in names:
            return {"table_text": sheets_to_text(ods_sheets(blob))}
        if "xl/workbook.xml" in names:
            return {"table_text": sheets_to_text(xlsx_sheets(blob))}
    return {"full_text": ""}


async def _download(http: httpx.AsyncClient, url: str) -> dict:
    r = await http.get(url)
    if r.status_code == 404:
        raise LookupError(url)
    r.raise_for_status()
    data = await asyncio.to_thread(_file_text, r.content)  # 大檔解析不卡住其他查詢
    text = data.get("table_text") or data.get("full_text") or ""
    if not text:
        data["note"] = "無法擷取此檔內容（可能是掃描檔或不支援的格式），請直接開啟 source_url。"
    data["title"] = next((ln.strip(" |") for ln in text.splitlines() if ln and not ln.startswith("## ")), "")[:100]
    return data


# ─────────────────────────────────────────────────────────────
# 司法院 司法統計（www.judicial.gov.tw）：年度清單頁 → dl- 檔案連結
# ─────────────────────────────────────────────────────────────

J_BASE = "https://www.judicial.gov.tw/tw/"
J_ANNUAL, J_MONTHLY = "np-1260-1.html", "np-1259-1.html"
_J_DL = re.compile(r"/dl-(\d+-[0-9a-f]{32})\.html")
_J_ID = re.compile(r"^\d+-[0-9a-f]{32}$")


async def _j_years(http, index: str) -> dict[int, str]:
    """民國年 → 該年清單頁代碼（lp-2475-1）。"""
    async def load():
        soup = await _soup(http, J_BASE + index)
        years = {}
        for a in soup.select("section.np a[href]"):
            y, lp = re.fullmatch(r"(\d{2,3})年", (a.get("title") or "").strip()), re.search(r"(lp-\d+-1)\.html", a["href"])
            if y and lp:
                years[int(y.group(1))] = lp.group(1)
        return years
    return await _memo_get(J_BASE + index, load)


def _j_rows(soup: BeautifulSoup, period: str) -> list[dict]:
    items = []
    for tr in soup.select("section.lp table tbody tr"):
        cols = {td.get("data-title", ""): td for td in tr.select("td")}
        files = {a.get_text(strip=True).upper(): m.group(1)
                 for a in tr.select("a[href]") if (m := _J_DL.search(a["href"]))}
        if "標題" not in cols or not files:
            continue
        title = cols["標題"].get_text(strip=True).splitlines()[0].strip()  # 月報標題後面接英文
        month = cols["月份"].get_text(strip=True) if "月份" in cols else ""
        items.append({
            "id": "judicial:" + files.get("ODS", next(iter(files.values()))),
            "title": title,
            "period": period + month.replace("份", ""),
            "summary": "／".join(x for x in [cols["項目"].get_text(strip=True) if "項目" in cols else "",
                                            "格式：" + "、".join(files)] if x),
        })
    return items


async def _j_list(http, prefix: str, period: str) -> list[dict]:
    """清單每頁最多 60 筆（更大的頁數站方回空頁），依第一頁的總頁數並行抓其餘頁。"""
    async def load():
        first = await _soup(http, f"{J_BASE}{prefix}-1-60.html")
        m = re.search(r"第\s*1\s*/\s*(\d+)\s*頁", first.get_text())
        pages = int(m.group(1)) if m else 1
        rest = await asyncio.gather(*(_soup(http, f"{J_BASE}{prefix}-{n}-60.html") for n in range(2, min(pages, 10) + 1)))
        return [item for soup in [first, *rest] for item in _j_rows(soup, period)]
    return await _memo_get(J_BASE + prefix, load)


def _j_year(years: dict[int, str], year: int, label: str) -> int:
    if not years:
        raise ValueError(f"{label}索引頁格式不符（找不到年度連結）")
    if year and year not in years:
        raise ValueError(f"{label}沒有民國 {year} 年，可查 {min(years)}–{max(years)} 年")
    return year or max(years)


async def _judicial_search(http, keyword: str, year: int, page: int) -> dict:
    years = await _j_years(http, J_ANNUAL)
    y = _j_year(years, year, "司法統計年報")
    items = [i for i in await _j_list(http, years[y], f"{y}年") if _match(keyword, i["title"], i["summary"])]
    return _group("司法院司法統計", f"{y}年統計年報", items, page)


async def _judicial_monthly_search(http, keyword: str, year: int, page: int) -> dict:
    years = await _j_years(http, J_MONTHLY)
    y = _j_year(years, year, "司法統計月報")
    async def load_months():
        soup = await _soup(http, f"{J_BASE}{years[y]}.html")
        return [int(m.group(1)) for a in soup.select("a[href*='-xCat-']")
                if (m := re.search(rf"{years[y]}-xCat-(\d+)\.html", a["href"]))]

    months = await _memo_get(f"{J_BASE}{years[y]}#months", load_months)
    if not months:
        raise ValueError(f"司法統計月報 {y} 年尚無資料")
    month = max(months)
    rows = await _j_list(http, f"{years[y]}-xCat-{month:02d}", f"{y}年")
    items = [i for i in rows if _match(keyword, i["title"], i["summary"])]
    return _group("司法院司法統計", f"{y}年{month}月統計月報", items, page,
                  note="月報只列該年度最新一個月；其他月份請改查年報或至官網瀏覽。")


async def _judicial_get(http, native_id: str) -> dict:
    if not _J_ID.match(native_id):
        raise LookupError(native_id)
    url = f"{J_BASE}dl-{native_id}.html"
    return {**await _download(http, url), "source_url": url}


# ─────────────────────────────────────────────────────────────
# 法務部 法務統計常用統計表（www.rjsd.moj.gov.tw）：純 HTML 表格
# ─────────────────────────────────────────────────────────────

MOJ_BASE = "https://www.rjsd.moj.gov.tw/RJSDWeb/common/"
MOJ_MENUS = {
    "INF_COMMON_P": "檢察", "INF_COMMON_C": "矯正", "INF_COMMON_Q": "司法保護", "INF_COMMON_A": "行政執行",
    "INF_COMMON_OD": "廉政", "INF_COMMON_O": "通訊監察", "INF_COMMON_LAWYER": "律師",
}
_MOJ_ID = re.compile(r"^(INF_COMMON_[A-Z]+)/(\d{1,6})$")


async def _moj_catalogue(http) -> list[dict]:
    async def load():
        soups = await asyncio.gather(*(_soup(http, MOJ_BASE + "WebList3.aspx", menu=m) for m in MOJ_MENUS))
        items = []
        for menu, soup in zip(MOJ_MENUS, soups):
            for grid in soup.select("table.GridView"):
                head = grid.find_previous("h2")  # 區塊標題，如「性侵害案件」
                for tr in grid.select("tr"):
                    a, tds = tr.select_one("a[href*='list_id=']"), tr.select("td")
                    if not a:
                        continue
                    list_id = re.search(r"list_id=(\d+)", a["href"]).group(1)
                    fields = tds[2].get_text(" ", strip=True) if len(tds) > 2 else ""
                    items.append({
                        "id": f"moj:{menu}/{list_id}", "title": a.get_text(strip=True),
                        "period": tds[1].get_text(strip=True) if len(tds) > 1 else "",
                        "summary": f"{MOJ_MENUS[menu]}／{head.get_text(strip=True) if head else ''}；統計項目：{fields}",
                    })
        return items
    return await _memo_get("moj", load)


async def _moj_search(http, keyword: str, year: int, page: int) -> dict:
    items = [i for i in await _moj_catalogue(http) if _match(keyword, i["title"], i["summary"])]
    extra = {"note": "常用統計表為滾動更新的多年期表格，不分年度；year 參數未套用。"} if year else {}
    return _group("法務部法務統計", "常用統計表", items, page, **extra)


async def _moj_get(http, native_id: str) -> dict:
    m = _MOJ_ID.match(native_id)
    if not m or m.group(1) not in MOJ_MENUS:
        raise LookupError(native_id)
    soup = await _soup(http, MOJ_BASE + "WebList3_Report.aspx", menu=m.group(1), list_id=m.group(2))
    text = html_tables_text(soup)
    if not text:
        raise LookupError(native_id)
    url = f"{MOJ_BASE}WebList3_Report.aspx?menu={m.group(1)}&list_id={m.group(2)}"
    return {"title": text.split("\n", 1)[0].strip(" |"), "source_url": url, "table_text": text}


# ─────────────────────────────────────────────────────────────
# 法務部司法官學院《犯罪狀況及其分析》（www.cprc.moj.gov.tw）
# ─────────────────────────────────────────────────────────────

CPRC_BASE = "https://www.cprc.moj.gov.tw"
CPRC_NODE = "/1563/1590/1592"
CPRC_DEEP = 3  # 關鍵字不在報告標題時，只往下比對最近幾期報告的篇章與簡介
_CPRC_ID = re.compile(r"^(\d{1,9})(?:/(\d{1,12}))?$")


async def _cprc_reports(http) -> list[dict]:
    async def load():
        soup = await _soup(http, f"{CPRC_BASE}{CPRC_NODE}/Lpsimplelist", Page=1, PageSize=100, type="")
        reports = []
        for a in soup.select("section.lp a[href$='/post']"):
            title = (a.get("title") or a.get_text(" ", strip=True)).strip()
            y = re.search(r"民國(\d{2,3})年", title)
            reports.append({"post": re.search(r"/(\d+)/post$", a["href"]).group(1), "title": title,
                            "year": int(y.group(1)) if y else 0})
        return reports
    return await _memo_get("cprc", load)


async def _cprc_post(http, post: str) -> dict:
    async def load():
        url = f"{CPRC_BASE}{CPRC_NODE}/{post}/post"
        soup = await _soup(http, url)
        files = []
        for a in soup.select(".file_download a[href*='/media/']"):
            m = re.search(r"/media/(\d+)/", a["href"])
            if m:
                name = re.sub(r"\s*[(（]另開新視窗[)）]\s*$", "", a.get("title") or a.get_text(strip=True))
                files.append({"id": f"cprc:{post}/{m.group(1)}", "title": name.strip(), "url": CPRC_BASE + a["href"]})
        h2, cp = soup.select_one("h2.title"), soup.select_one("section.cp")
        date = re.search(r"發布日期\s*[:：]\s*(\d{2,3}-\d{1,2}-\d{1,2})", soup.get_text(" "))
        return {"title": h2.get_text(strip=True) if h2 else "", "url": url, "files": files,
                "date": _roc_to_iso(date.group(1)) if date else "",
                "description": cp.get_text("\n", strip=True) if cp else ""}
    return await _memo_get(f"cprc:{post}", load)


def _snippet(text: str, keyword: str, width: int = 60) -> str:
    i = text.find(keyword.split()[0]) if keyword.strip() else -1
    return re.sub(r"\s+", " ", text[max(0, i - width): i + width] if i >= 0 else text[:width * 2])


async def _cprc_search(http, keyword: str, year: int, page: int) -> dict:
    reports = [r for r in await _cprc_reports(http) if not year or r["year"] == year]

    def report_item(r: dict, summary: str) -> dict:
        return {"id": f"cprc:{r['post']}", "title": r["title"], "period": f"{r['year']}年" if r["year"] else "",
                "summary": summary}

    items = [report_item(r, "年度報告；以 id 取得篇章清單（PDF＋數據 XLSX）") for r in reports
             if _match(keyword, r["title"])]
    extra = {}
    if keyword.strip():
        recent = reports[:CPRC_DEEP]
        posts = await asyncio.gather(*(_cprc_post(http, r["post"]) for r in recent))
        for r, post in zip(recent, posts):
            if _match(keyword, post["description"]) and not _match(keyword, r["title"]):
                items.append(report_item(r, "簡介提及：…" + _snippet(post["description"], keyword) + "…"))
            items += [{"id": f["id"], "title": f"{r['title']}／{f['title']}", "period": f"{r['year']}年",
                       "summary": "篇章檔案"} for f in post["files"] if _match(keyword, f["title"])]
        extra["note"] = f"篇章標題與簡介只比對最近 {CPRC_DEEP} 期（或指定年度）報告；更早的報告請以年度查詢。"
    return _group("法務部司法官學院", "犯罪狀況及其分析", items, page, **extra)


async def _cprc_get(http, native_id: str) -> dict:
    m = _CPRC_ID.match(native_id)
    if not m:
        raise LookupError(native_id)
    post = await _cprc_post(http, m.group(1))
    if not m.group(2):
        return {"title": post["title"], "date": post["date"], "source_url": post["url"],
                "full_text": post["description"], "files": post["files"],
                "note": "以 files 中的 id 取得單一篇章 PDF 全文或數據 XLSX 表格。"}
    f = next((x for x in post["files"] if x["id"] == f"cprc:{native_id}"), None)
    if f is None:
        raise LookupError(native_id)
    data = await _download(http, f["url"])
    return {**data, "title": f"{post['title']}／{f['title']}", "date": post["date"], "source_url": f["url"]}


# ─────────────────────────────────────────────────────────────
# 對外介面
# ─────────────────────────────────────────────────────────────

SOURCES = {
    "judicial": ("司法院司法統計年報", ("司法統計", "司法院", "年報", "統計年報"), _judicial_search, _judicial_get),
    "judicial_monthly": ("司法院司法統計月報", ("月報", "統計月報"), _judicial_monthly_search, _judicial_get),
    "moj": ("法務部法務統計", ("法務部", "法務統計", "常用統計表"), _moj_search, _moj_get),
    "cprc": ("犯罪狀況及其分析", ("司法官學院", "犯罪狀況", "犯罪趨勢"), _cprc_search, _cprc_get),
}


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


class StatisticsClient:
    def __init__(self, cache: CacheDB):
        self.cache = cache
        self.http = httpx.AsyncClient(timeout=60.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True)

    async def close(self):
        await self.http.aclose()

    async def search(self, keyword: str, source: str = "", year: int = 0, page: int = 1) -> dict:
        keys = resolve_sources(source)
        if keys is None:
            return error_response(f"不支援的來源「{source}」",
                                  supported_sources=[label for label, *_ in SOURCES.values()])
        params = {"tool": "statistics", "keyword": keyword, "sources": keys, "year": year, "page": page}
        cached = await self.cache.get_search(params)
        if cached:
            return {**cached, "cached": True}

        async def run(key: str) -> dict:
            label, _, search, _ = SOURCES[key]
            try:
                return await search(self.http, keyword, year, page)
            except Exception as e:  # 單一來源掛掉不拖垮其他來源
                logger.warning("統計表搜尋失敗 %s: %s", key, e, exc_info=not isinstance(e, (httpx.HTTPError, ValueError)))
                return {"source": label, "error": f"{type(e).__name__}: {e}"}

        groups = await asyncio.gather(*(run(k) for k in keys))
        result = {
            "success": True, "keyword": keyword, "page": page,
            "categories": [{k: v for k, v in g.items() if k != "items"}
                           | ({"returned": len(g["items"])} if "items" in g else {}) for g in groups],
            "total_count": sum(g.get("total", 0) for g in groups),
            "results": [i for g in groups for i in g.get("items", [])],
            "timestamp": datetime.now().isoformat(),
        }
        if not any("error" in g for g in groups):
            await self.cache.set_search(params, result)
        return result

    async def get(self, stat_id: str) -> dict:
        key, _, native_id = stat_id.partition(":")
        if key not in SOURCES or not native_id:
            return error_response(f"id 格式錯誤：「{stat_id}」，請使用搜尋結果回傳的 id")
        cache_key = f"statistics:{stat_id}"
        # 法務統計常用統計表每月滾動更新，只快取一天；其他來源是固定檔案，走長期快取
        cached = await (self.cache.get_search({"tool": "statistics_get", "id": stat_id}) if key == "moj"
                        else self.cache.get_judgment(cache_key))
        if cached:
            return {"success": True, "cached": True, **cached}
        label, _, _, get = SOURCES[key]
        try:
            data = await get(self.http, native_id)
        except LookupError:
            return error_response(f"{label}查無此件：{stat_id}")
        except (httpx.HTTPError, ValueError, zipfile.BadZipFile, ElementTree.ParseError, KeyError) as e:
            return error_response(f"{label}連線或解析失敗：{type(e).__name__}: {e}")
        data = {"id": stat_id, "source": label, **data}
        for k in ("table_text", "full_text"):
            if k in data:
                data["truncated"] = len(data[k]) > MAX_TEXT or data[k].endswith(SHEETS_OMITTED)
                data[k] = data[k][:MAX_TEXT]
        if key == "moj":
            await self.cache.set_search({"tool": "statistics_get", "id": stat_id}, data, ttl=int(_TTL))
        else:
            await self.cache.set_judgment(cache_key, data, source="statistics")
        return {"success": True, "cached": False, **data}
