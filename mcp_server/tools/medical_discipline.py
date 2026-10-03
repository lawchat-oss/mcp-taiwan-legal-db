"""衛福部公開醫事懲戒：一個人員類別的現有公告清單、按需讀取一份 PDF。"""

from __future__ import annotations

import re
from urllib.parse import unquote, urljoin, urlparse

from mcp_server.tools import appeals
from mcp_server.tools.agency_interpretations import _group

BASE = "https://ma.mohw.gov.tw/Accessibility/"
CATEGORY = "醫事懲戒決議"
_CLASSES = {"西醫師": "A", "牙醫師": "C", "中醫師": "B", "藥師": "D", "藥劑生": "E", "醫師": "A"}


def _filename(url: str) -> str:
    parsed = urlparse(url)
    name = unquote(parsed.path.removeprefix("/Accessibility/Downloads/"))
    if (parsed.scheme != "https" or parsed.netloc != "ma.mohw.gov.tw" or
            not parsed.path.startswith("/Accessibility/Downloads/") or parsed.query or parsed.fragment or
            not name.lower().endswith(".pdf") or any(c in name for c in ("/", "\\", "\x00", "%")) or len(name) > 160):
        raise ValueError("醫事懲戒附件不在官方公開下載目錄")
    return name


async def search(http, keyword, year_from, year_to, doc_number, page):
    kind = next((x for x in _CLASSES if keyword.startswith(x)), "")
    term = keyword[len(kind):].strip() if kind else keyword
    async with appeals._client() as client:
        r = await client.get(BASE + "DISSearch/MASearchDIS")
        r.raise_for_status()
        data = appeals._form(appeals._soup(r))
        data["CER_REF_ID"] = _CLASSES.get(kind, "A")
        r = await client.post(BASE + "DISSearch/DISDataList", data=data)
        r.raise_for_status()
    soup = appeals._soup(r)
    table = soup.select_one("#disTable")
    if table is None:
        raise RuntimeError("醫事懲戒查詢頁未回傳公告表格")
    rows, skipped = [], 0
    for tr in table.select("tbody tr"):
        cells = tr.find_all("td")
        a = tr.select_one('a[href]')
        if len(cells) < 5 or a is None:
            continue
        try:
            name = _filename(urljoin(BASE + "DISSearch/DISDataList", a["href"]))
        except ValueError:
            skipped += 1
            continue
        text = [appeals._text(x) for x in cells]
        rows.append(appeals._item(name.encode().hex(), text[2], appeals._iso(text[4].split("~")[0].strip()),
                                  f"{text[0]}；{text[1]}；上架期間：{text[4]}"))
    result = appeals._local(rows, term, year_from, year_to, doc_number, page)
    items = [{**x, "id": "medical_discipline:" + x["id"], "agency": "衛生福利部", "category": CATEGORY}
             for x in result["items"]]
    note = (f"本次查 {kind or '西醫師'}；可用關鍵字前綴指定西醫師、牙醫師、中醫師、藥師、藥劑生；"
            "只比對姓名、縣市、證書字號；日期為公告上架日期，僅含目前上架資料")
    if skipped:
        note += f"；{skipped} 筆附件連至非官方主機，未納入可取全文的結果"
    return _group("衛生福利部", CATEGORY, result["total"], items, result["has_more"], note=note)


async def get(http, native_id):
    if not re.fullmatch(r"(?:[0-9a-f]{2}){5,480}", native_id):
        raise LookupError(native_id)
    try:
        from urllib.parse import quote
        name = bytes.fromhex(native_id).decode()
        url = BASE + "Downloads/" + quote(name, safe="")
        _filename(url)
    except (ValueError, UnicodeDecodeError) as exc:
        raise LookupError(native_id) from exc
    text = await appeals._pdf_text(http, url)
    return {"agency": "衛生福利部", "category": CATEGORY,
            **appeals._result(appeals._doc_no(text), appeals._signed(text), name[:-4], text,
                              BASE + "DISSearch/MASearchDIS", url)}
