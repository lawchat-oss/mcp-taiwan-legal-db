"""經濟部智慧財產局審查基準：專利審查基準（網頁版全文，53 章）與商標審查基準（25 份，只有 PDF）

站方沒有全文檢索；清單共 6 頁，抓下來在本機比對標題（程序內快取一天）。審查基準是經濟部令發布的
行政規則，依著作權法第 9 條不受著作權保護。
"""

from __future__ import annotations

import asyncio
import re
import time

import httpx
from bs4 import BeautifulSoup

from mcp_server.tools.pdf_text import pdf_to_text

TIPO = "https://www.tipo.gov.tw/tw/"
# 清單頁 → 類別；專利審查基準網頁版每篇一頁
_LISTS = {
    "patents/997": "專利審查基準", "patents/998": "專利審查基準", "patents/999": "專利審查基準",
    "patents/1000": "專利審查基準", "patents/1001": "專利審查基準", "trademarks/576": "商標審查基準",
}
_ID = re.compile(r"^(997|998|999|1000|1001|576)-\d{3,7}$")
_CJK_GAP = re.compile(r"(?<=[㐀-鿿＀-￯])[ \t]+(?=[㐀-鿿＀-￯])")
_LIST_TTL = 86400
PAGE_SIZE = 20
_rows: list[dict] = []
_rows_at = 0.0
_lock = asyncio.Lock()


def _date(roc: str) -> str:
    """「114-11-26」→ 2025-11-26。"""
    m = re.search(r"(\d{2,3})\D(\d{1,2})\D(\d{1,2})", roc or "")
    return f"{int(m.group(1)) + 1911:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


async def _list(http: httpx.AsyncClient, path: str, category: str) -> list[dict]:
    r = await http.get(f"{TIPO}{path}.html", params={"nowPage": 1, "pageSize": 50})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    # 頁面標題最後一段是篇名，例如「第二篇 發明專利實體審查」
    part = (soup.title.get_text(strip=True).rsplit("－", 1)[-1] if soup.title else "") if category.startswith("專利") else ""
    rows = []
    for a in soup.select(".list ul li a[href]"):
        m = re.search(r"/(\d+-\d+)\.html", a["href"])
        title = a.select_one(".listTitle")
        if m and title:
            name = re.sub(r"\s+", " ", title.get_text(" ", strip=True))
            rows.append({"native": m.group(1), "category": category,
                         "title": f"{part}－{name}" if part and part not in name else name})
    return rows


async def _all_rows(http: httpx.AsyncClient) -> list[dict]:
    global _rows, _rows_at
    async with _lock:
        if not _rows or time.time() - _rows_at > _LIST_TTL:
            pages = await asyncio.gather(*(_list(http, p, c) for p, c in _LISTS.items()))
            _rows, _rows_at = [r for page in pages for r in page], time.time()
        return _rows


async def search(http: httpx.AsyncClient, q) -> list[dict]:
    """q 是 agency_interpretations.Query（只用 keyword、page）。"""
    if q.number or q.start or q.end:  # 審查基準沒有發文字號與發文日期，這些條件無法套用
        return [{"source": "經濟部智慧財產局", "category": "專利、商標審查基準", "total": 0, "items": [],
                 "has_more": False, "note": "審查基準沒有發文字號與日期，指定字號或年度時不查此來源。"}]
    words = q.keyword.split()
    hits = [r for r in await _all_rows(http) if all(w in r["title"] for w in words)]
    page_rows = hits[(q.page - 1) * PAGE_SIZE: q.page * PAGE_SIZE]
    items = [{"id": f"tipo_guide:{r['native']}", "agency": "經濟部智慧財產局", "category": r["category"],
              "doc_number": "", "date": "", "summary": r["title"]} for r in page_rows]
    return [{"source": "經濟部智慧財產局", "category": "專利、商標審查基準", "total": len(hits), "items": items,
             "has_more": q.page * PAGE_SIZE < len(hits),
             "note": "審查基準以標題比對（站方沒有全文檢索）；請用章名用語，例如「專利要件」「說明書」「混淆誤認」「識別性」（新穎性、進步性在「專利要件」章）。"}]


async def get(http: httpx.AsyncClient, native_id: str) -> dict:
    if not _ID.fullmatch(native_id):
        raise LookupError(native_id)
    url = f"{TIPO}{'trademarks' if native_id.startswith('576-') else 'patents'}/{native_id}.html"
    r = await http.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    info = {k.strip(): v.strip() for li in soup.select(".bottomInfo li")
            for k, _, v in [li.get_text(" ", strip=True).partition("：")]}
    title = (soup.title.get_text(strip=True) if soup.title else "").split("－", 1)[-1]
    pdf = soup.select_one(".file_download a[href$='.pdf']")
    attachments, full = [], ""
    if pdf:
        pdf_url = str(r.url.join(pdf["href"]))
        attachments.append({"title": "PDF", "url": pdf_url})
        blob = (await http.get(pdf_url)).content
        full = _CJK_GAP.sub("", await asyncio.to_thread(pdf_to_text, blob))
    if not full:
        body = soup.select_one("section.cp")
        full = re.sub(r"\n{3,}", "\n\n", body.get_text("\n", strip=True)) if body else ""
    if not full and not attachments:
        raise LookupError(native_id)
    return {
        "agency": "經濟部智慧財產局", "doc_number": "",
        "date": _date(info.get("更新日期") or info.get("發布日期", "")),
        "summary": title, "full_text": full, "attachments": attachments,
        "notes": f"發布日期 {_date(info.get('發布日期', ''))}，發布單位 {info.get('發布單位', '')}",
        "source_url": url,
    }
