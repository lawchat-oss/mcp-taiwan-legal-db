"""司法院法學資料檢索系統「判解函釋」（legal.judicial.gov.tw/FINT）

裁判書系統（FJUD）查不到的幾類：最高法院／最高行政法院決議、法律問題座談、停止適用之判例、
大理院／司法院解釋（院字、院解字）、大法庭專區，以及司法院的行政函釋。

查詢是一次 ASP.NET 表單 POST，回應頁列出各類別筆數與結果清單連結（同條件同一個 q 值、不綁 session）；
清單每頁 20 筆、最多 500 筆。站方 robots.txt 不允許爬蟲：這裡只做使用者觸發的單次查詢，不批次抓取。
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qs, quote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from mcp_server.tools._errors import error_response

BASE = "https://legal.judicial.gov.tw/FINT/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
PAGE_SIZE = 20
MAX_RESULTS = 500  # 站方上限，超過的頁數回錯誤頁

REFERENCE_VALUE_CODES = ("U", "UU", "V", "S", "US", "T")

# 類別 → (結果頁籤 ty, 進階查詢表單要勾的欄位)
CATEGORIES: dict[str, tuple[str, dict[str, list[str]]]] = {
    "決議": ("D", {"dtype": ["A", "UA", "B", "C"]}),
    "法律問題座談": ("Q", {"qtype": ["A", "G", "UA", "B", "C", "D"]}),
    "停止適用判例": ("J1", {"jtype1": ["A", "UA", "B", "E"]}),
    "司法解釋": ("C", {"ctype": ["A", "B", "C"]}),
    "大法庭": ("J2", {"jtype2": ["1", "U1", "2", "3"]}),
    # 站方編輯過、附裁判要旨的各級法院裁判（頁籤名稱「精選裁判」）；代碼依序為民事、家事、刑事、行政、懲戒
    "精選裁判": ("J", {"jtype": [
        "C", "U", "S", "G", "O", "I", "UC", "UU", "US", "UG", "UI",
        "D", "V", "T", "H", "P", "J", "F", "K", "0", "R", "Q", "L",
    ]}),
    # 其中最高法院、高等法院暨所屬法院選為「具參考價值」「足資討論」的裁判
    "具參考價值裁判": ("J", {"jtype": list(REFERENCE_VALUE_CODES)}),
    "行政函釋": ("E", {"etype": ["*"]}),
}
_TY_TO_CATEGORY = {ty: name for name, (ty, _) in reversed(CATEGORIES.items())}  # 同一頁籤取第一個名稱


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


# 公文、決議、座談會紀錄以固定寬度排版（約 35 字一行）；這些開頭才是新段落，其餘行接回上一行
_PARA_START = re.compile(
    r"^(?:[主說正副附][\s　]*[旨明本件][\s　]*[：:]"
    r"|(?:決議文?|法律問題|討論意見|研究意見|初步研討結果|研討結果|審查意見|大會研討結果|[甲乙丙丁戊己]說"
    r"|全文內容|提案機關|討論事項\S{0,3}|備\s*註|編\s*註)[\s　]*[：:]"
    r"|[一二三四五六七八九十]+、|[（(][一二三四五六七八九十\d]+[)）]|[㈠-㈩]|【|\d+[.、．])"
)


def unwrap(pre: str) -> str:
    """把固定寬度換行接回段落，壓掉排版用的連續空白。"""
    paras: list[str] = []
    for line in pre.splitlines():
        s = re.sub(r"[^\S\n]{2,}", " ", line.strip())  # 含全形空白、不斷行空白（nbsp）
        if not s:
            continue
        if paras and not _PARA_START.match(s):
            paras[-1] += s
        else:
            paras.append(s)
    return "\n".join(paras)


def roc_date(s: str) -> str:
    m = re.search(r"(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", s or "")
    if not m:
        return (s or "").strip()
    y, mo, d = (int(g) for g in m.groups())
    return f"{y + 1911:04d}-{mo:02d}-{d:02d}"


def _rows(container) -> list[tuple[str, object]]:
    """`.row > .col-th + .col-td` 的 (標籤, 內容節點)；沒有標籤的列（全文）標籤為空字串。"""
    out = []
    for row in container.select(".row"):
        th = row.select_one(".col-th")
        td = row.select_one(".col-td, .col-all")
        if td is not None:
            out.append((_text(th).rstrip("：: ").replace(" ", "") if th else "", td))
    return out


def _item_id(href: str) -> str:
    q = parse_qs(urlparse(href).query)
    return f"{q['ty'][0]}:{q['id'][0]}"


async def search(
    keyword: str,
    categories: list[str],
    year_from: int = 0,
    year_to: int = 0,
    page: int = 1,
) -> list[dict]:
    """回傳每個類別一組 {category, total, items}；items 為 {id, title, date, summary}。"""
    async with httpx.AsyncClient(
        timeout=30.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as http:  # 每次查詢用新的 session：同一 session 先做過簡易查詢再做進階查詢會拿不到結果
        r = await http.get(BASE + "Default_AD.aspx")
        r.raise_for_status()  # 403/503 等錯誤頁沒有結果頁籤，不檢查會被當成「零筆」
        form_page = BeautifulSoup(r.text, "html.parser")
        data: dict[str, object] = {
            i["name"]: i.get("value", "")
            for i in form_page.select("input[type=hidden]") if i.get("name")
        }
        for name in categories:
            for field, values in CATEGORIES[name][1].items():
                data.setdefault(field, [])
                data[field] += values
        data.update({"txtKW": keyword, "ctl00$cp_content$btnQry": "送出查詢"})
        if year_from:
            data.update({"txtY1": str(year_from), "txtM1": "1", "txtD1": "1"})
        if year_to:
            data.update({"txtY2": str(year_to), "txtM2": "12", "txtD2": "31"})
        r = await http.post(BASE + "Default_AD.aspx", data=data)
        r.raise_for_status()
        result_page = BeautifulSoup(r.text, "html.parser")
        tabs = {
            a.get("data-code"): (a.get("href", ""), int(_text(a.select_one(".badge")) or 0))
            for a in result_page.select("#result-count a[data-code]")
        }

        groups = []
        for name in categories:
            ty = CATEGORIES[name][0]
            href, total = tabs.get(ty, ("", 0))
            items: list[dict] = []
            if total and href and (page - 1) * PAGE_SIZE < min(total, MAX_RESULTS):
                r = await http.get(urljoin(BASE, href) + f"&sort=DS&page={page}")
                r.raise_for_status()
                lst = BeautifulSoup(r.text, "html.parser")
                for tr in lst.select("table.int-table > tr"):
                    link = tr.select_one("a#hlTitle")
                    if not link:
                        continue
                    fields = _rows(tr)
                    item_id = _item_id(urljoin(BASE, link["href"]))
                    item = {
                        "id": item_id,
                        "title": _text(link),
                        "date": next((roc_date(_text(td)) for label, td in fields if "日期" in label), ""),
                        "summary": next(
                            (unwrap(td.get_text()) for label, td in fields
                             if "要旨" in label or label in ("解釋文", "決議", "裁判案由")),
                            "",
                        ),
                    }
                    if ty == "J":
                        item["reference_value"] = item_id.partition(":")[2].split(",")[0] in REFERENCE_VALUE_CODES
                    items.append(item)
            groups.append({"category": name, "total": total, "items": items,
                           "has_more": page * PAGE_SIZE < min(total, MAX_RESULTS)})
        return groups


def detail_url(item_id: str) -> str:
    ty, _, native = item_id.partition(":")
    path = "../FEXE/data.aspx" if ty == "E" else "data.aspx"
    return urljoin(BASE, f"{path}?ty={ty}&id={quote(native, safe='')}")


async def get(http: httpx.AsyncClient, item_id: str) -> dict:
    ty, _, native = item_id.partition(":")
    if ty not in _TY_TO_CATEGORY or not native:
        raise LookupError(item_id)
    url = detail_url(item_id)
    r = await http.get(url, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.select_one(".col-xs-8 .int-table") or soup.select_one(".int-table")
    if table is None:
        raise LookupError(item_id)
    fields: dict[str, str] = {}
    full = ""
    for label, td in _rows(table):
        if label:
            fields.setdefault(label, unwrap(td.get_text()))
        elif not full:
            full = unwrap(td.get_text())
    if not fields and not full:
        raise LookupError(item_id)
    related = [
        _text(li) for area in soup.select(".rela-area") if "相關法條" in _text(area)[:6]
        for li in area.select("li")
    ]
    return {
        "category": _TY_TO_CATEGORY[ty],
        "fields": fields,
        "full_text": full,
        "related_laws": related,
        "attachments": [
            {"title": _text(a), "url": urljoin(str(r.url), a["href"])}
            for a in soup.select("a[href*='GetFile']")
        ],
        "source_url": url,
    }


class PrecedentClient:
    """search_precedents / get_precedent 的快取包裝。"""

    def __init__(self, cache):
        self.cache = cache
        self.http = httpx.AsyncClient(timeout=60.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True)

    async def close(self):
        await self.http.aclose()

    async def search(self, keyword: str, category: str, year_from: int, year_to: int, page: int) -> dict:
        names = [c for c in re.split(r"[,，、\s]+", category.strip()) if c] or [
            c for c in CATEGORIES if c not in ("行政函釋", "具參考價值裁判")  # 函釋由 search_agency_interpretations 查
        ]
        if "精選裁判" in names:  # 兩者同一個頁籤，具參考價值裁判是子集
            names = [c for c in names if c != "具參考價值裁判"]
        unknown = [c for c in names if c not in CATEGORIES]
        if unknown:
            return error_response(f"不支援的類別：{'、'.join(unknown)}", supported_categories=list(CATEGORIES))
        params = {"tool": "precedents", "keyword": keyword, "categories": names,
                  "year_from": year_from, "year_to": year_to, "page": page}
        cached = await self.cache.get_search(params)
        if cached:
            return {**cached, "cached": True}
        try:
            groups = await search(keyword, names, year_from, year_to, page)
        except httpx.HTTPError as e:
            return error_response(f"司法院法學資料檢索系統連線失敗：{type(e).__name__}: {e}")
        result = {
            "success": True,
            "keyword": keyword,
            "page": page,
            "categories": [{k: v for k, v in g.items() if k != "items"} | {"returned": len(g["items"])} for g in groups],
            "total_count": sum(g["total"] for g in groups),
            "results": [{**i, "category": g["category"]} for g in groups for i in g["items"]],
            "timestamp": datetime.now().isoformat(),
        }
        await self.cache.set_search(params, result)
        return result

    async def get(self, precedent_id: str) -> dict:
        cache_key = f"fint:{precedent_id}"
        cached = await self.cache.get_judgment(cache_key)
        if cached:
            return {"success": True, "cached": True, **cached}
        try:
            data = await get(self.http, precedent_id)
        except LookupError:
            return error_response(f"查無此筆資料：{precedent_id}（請使用 search_precedents 回傳的 id）")
        except httpx.HTTPError as e:
            return error_response(f"司法院法學資料檢索系統連線失敗：{type(e).__name__}: {e}")
        full = data["full_text"]
        data = {"id": precedent_id, **data, "full_text": full[:20000], "full_text_truncated": len(full) > 20000}
        await self.cache.set_judgment(cache_key, data, source="fint")
        return {"success": True, "cached": False, **data}
