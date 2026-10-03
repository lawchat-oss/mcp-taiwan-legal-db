"""全國法規資料庫「中央法規」以外的法律文本：地方自治法規、條約協定、交易所規章

| 代碼 | 來源 | 內容 |
|---|---|---|
| taipei | 臺北市法規查詢系統 laws.gov.taipei | 自治條例、自治規則、委辦規則、行政規則 |
| ntpc | 新北市政府電子法規查詢系統 web.law.ntpc.gov.tw | 同上（全文檢索，一次回傳全部命中） |
| taichung 等 | 各縣市「主管法規共用系統」（同一套產品，共用 _glrs_*） | 自治條例、自治規則（含委辦規則）、行政規則（現行） |
| moj_treaty | 全國法規資料庫 條約協定 law.moj.gov.tw | 我國簽署之條約協定中、英文本（以名稱查詢） |
| mofa | 外交部 中華民國條約協定資料庫 no06.mofa.gov.tw | 條約協定 PDF（不少為掃描檔，無文字層） |
| mof_tax | 財政部 我國所得稅協定網絡 www.mof.gov.tw | 全面性及海空運輸所得稅協定 PDF |
| twse | 臺灣證券交易所 法規分享知識庫（行動版 /m/；桌面版常逾時） | 證交所規章 |
| tpex、taifex | 證券暨期貨法令判解查詢系統 www.selaw.com.tw | 櫃買中心、期交所規章（以名稱查詢） |

全國法規資料庫與財政部 /download/ 的 robots.txt 不允許爬蟲；selaw 頁尾載明「本網站內容非經提供單位正式書面授權，
不得轉載」，其結果一律附 note 提醒。所有來源只做使用者觸發的單次查詢（每次呼叫數個請求），不批次抓取、不建本機副本。
桃園市 law.tycg.gov.tw（連線遭重設）、雲林縣（Cloudflare 驗證頁）、基隆市與宜蘭縣（憑證鏈不完整）目前無法連線，未收錄。
id 一律為「來源代碼:原站識別碼」，例如 taichung:GL001385、moj_treaty:Y0040274、twse:FL007274、tpex:LW10812093。
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from functools import partial

import httpx
from bs4 import BeautifulSoup

from mcp_server.cache.db import CacheDB
from mcp_server.tools import fint
from mcp_server.tools._errors import error_response
from mcp_server.tools.agency_interpretations import _date, _text
from mcp_server.tools.pdf_text import pdf_to_text

logger = logging.getLogger(__name__)

MAX_TEXT = 60000
PAGE_SIZE = 20
SELAW_NOTE = ("資料取自證券暨期貨法令判解查詢系統（selaw.com.tw），該站載明非經提供單位書面授權不得轉載；"
              "僅供個案查閱，引用前請向發布單位核對。")
PDF_NOTE = "PDF 沒有文字層（掃描檔），無法擷取全文，請開 pdf_url 閱讀；名稱與簽署日期見搜尋結果。"

# ─────────────────────────────────────────────────────────────
# 共用：條號、段落
# ─────────────────────────────────────────────────────────────

_NUM = "0-9０-９零〇一二三四五六七八九十百千廿卅"
_CN_DIGIT = {c: i for i, c in enumerate("零一二三四五六七八九")} | {"〇": 0}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}
# 條文開頭須為「第X條」後接空白或行尾：內文換行後剛好以「第十六條所定…」開頭的行不會被誤認
# 增訂條文有「第 1 條之 1」與「第 1-1 條」兩種寫法
_ART_HEAD = re.compile(rf"^第\s*([{_NUM}]+)(?:\s*[-－]\s*([{_NUM}]+))?\s*條(?:\s*之\s*([{_NUM}]+))?(?:\s+|$)(.*)")
_CHAPTER = re.compile(rf"^第\s*[{_NUM}]+\s*[編章節](?:\s|$)")
_CN_ITEM = re.compile(r"^[一二三四五六七八九十]+\s")  # 新北「一  拆除…」款次只用空白隔開


def _cn_num(s: str) -> int:
    s = re.sub(r"\s", "", s).replace("廿", "二十").replace("卅", "三十")
    if s.isdigit():
        return int(s)
    total = n = 0
    for ch in s:
        if ch in _CN_UNIT:
            total, n = total + (n or 1) * _CN_UNIT[ch], 0
        else:
            n = _CN_DIGIT[ch]
    return total + n


def _article_no(s: str) -> str:
    """「第 15 條之 1」「十五之一」「15-1」「第十五條」→「15-1」「15」"""
    parts = [p for p in re.split(r"[-－之]", re.sub(r"[第條\s]", "", s)) if p]
    try:
        return "-".join(str(_cn_num(p)) for p in parts)
    except KeyError:
        return s.strip()


def _join(lines) -> str:
    """接回固定寬度換行：前一行以句號／冒號結尾，或本行是款項、條次、章節開頭，才另起一段。"""
    paras: list[str] = []
    for s in (re.sub(r"\s+", " ", line).strip() for line in lines):
        if not s:
            continue
        if paras and not paras[-1].endswith(("。", "：", ":")) and not any(
            p.match(s) for p in (fint._PARA_START, _CN_ITEM, _ART_HEAD, _CHAPTER)
        ):
            sep = " " if re.search(r"[A-Za-z,;)]$", paras[-1]) and re.match(r"[A-Za-z(]", s) else ""
            paras[-1] += sep + s
        else:
            paras.append(s)
    return "\n".join(paras)


def _split_articles(lines) -> list[dict]:
    """逐行文字 → 條文；章節標題與第一條之前的前言略過。"""
    arts: list[tuple[str, list[str]]] = []
    buf: list[str] | None = None
    for line in lines:
        s = line.strip()
        m = _ART_HEAD.match(s)
        if m:
            buf = [m.group(4)]
            sub = m.group(2) or m.group(3)
            arts.append((_article_no(f"{m.group(1)}-{sub}" if sub else m.group(1)), buf))
        elif _CHAPTER.match(s):
            buf = None
        elif buf is not None:
            buf.append(s)
    return [{"number": no, "content": _join(body)} for no, body in arts]


def _lines(el) -> list[str]:
    return el.get_text("\n").splitlines() if el else []


def _fields(rows) -> dict[str, str]:
    """`<tr><th>標籤：</th><td>值</td></tr>` → {標籤: 值}"""
    return {re.sub(r"\s", "", _text(tr.th)).rstrip("：:"): _text(tr.td) for tr in rows if tr.th and tr.td}


def _local_category(title: str, hint: str = "") -> str:
    for kind in ("自治條例", "自治規則及委辦規則", "自治規則", "委辦規則", "自律規則", "行政規則"):
        if kind in hint:
            return kind
    # 頁面沒標位階時依名稱推定（地方制度法第 26、27、29 條的命名規定）
    # ponytail: 名稱推定分不出自治規則與委辦規則；要精確就得多抓一次各站的分類頁
    if title.endswith("自治條例"):
        return "自治條例"
    if title.endswith(("規程", "規則", "細則", "辦法", "綱要", "標準", "準則")):
        return "自治規則或委辦規則"
    if title.endswith(("要點", "規定", "原則", "須知", "基準", "注意事項", "作業程序", "規範")):
        return "行政規則"
    return "地方法規"


def _treaty_category(title: str) -> str:
    return "條約" if re.search(r"條約|公約", title) else "協定"


def _split_country(keyword: str) -> tuple[str, str]:
    """「日本 所得稅」→（日本, 所得稅）；條約來源只接受單一關鍵字，前面的詞當締約國／組織篩選。"""
    parts = keyword.split()
    return (" ".join(parts[:-1]), parts[-1]) if parts else ("", "")


def _tail_date(s: str) -> str:
    """「名稱 (民國 100 年 02 月 18 日 公發布)」「名稱（115.09.30）」→ 取最後一組括號裡的日期，避開名稱中的數字。"""
    return _date(re.split(r"[(（]", s)[-1])


def _soup(r: httpx.Response) -> BeautifulSoup:
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def _total(soup: BeautifulSoup, fallback: int) -> int:
    m = re.search(r"共\s*(\d+)\s*筆", soup.get_text(" "))
    return int(m.group(1)) if m else fallback


def _group(source: str, category: str, total: int, items: list[dict], has_more: bool, **extra) -> dict:
    return {"source": source, "category": category, "total": total, "items": items, "has_more": has_more, **extra}


# ─────────────────────────────────────────────────────────────
# 主管法規共用系統（臺中、高雄、臺南等縣市；同一產品，網址前綴不同）
# ─────────────────────────────────────────────────────────────

_GLRS_ID = re.compile(r"^[A-Z]{2}\d{6}$")


async def _glrs_search(base: str, issuer: str, http, keyword: str, page: int) -> dict:
    # LawQuery.aspx 的 POST 會 302 到這個 GET；GroupID 6 自治條例、7 自治規則（及委辦規則）、2 行政規則（各站一致）
    # 不要帶 content=0 之類的 0 值參數：會被導到錯誤頁 LR007
    params = {"NLawTypeID": "all", "GroupID": "6,7,2", "KW": keyword, "name": "1", "content": "1", "now": "1", "page": page}
    soup = _soup(await http.get(base + "LawResult.aspx", params=params))
    items = []
    for tr in soup.select("table.tab-result tr"):
        tds, a = tr.find_all("td"), tr.select_one("a[id$=hlkLawName]")
        rid = re.search(r"id=(\w+)", a["href"]) if a else None
        if rid and len(tds) >= 4:
            items.append({"id": rid.group(1), "title": a.get_text(strip=True), "issuer": issuer,
                          "category": _text(tds[3]), "date": _date(_text(tds[1]))})
    m = re.search(r"全部\s*(\d+)", soup.get_text(" "))  # 筆數少於一頁時沒有「共 N 筆」，改看類別頁籤
    total = int(m.group(1)) if m else _total(soup, len(items))
    return _group(issuer, "地方法規", total, items, page * 10 < total)


async def _glrs_get(base: str, issuer: str, http, native_id: str) -> dict:
    if not _GLRS_ID.match(native_id):
        raise LookupError(native_id)
    url = f"{base}LawContent.aspx?id={native_id}"
    soup = _soup(await http.get(url))
    fields = _fields(soup.select("table.tab-edit tr"))
    body = soup.select_one("#ctl00_cp_content_divContent")
    if not fields.get("法規名稱") or body is None:
        raise LookupError(native_id)
    title, lines = fields["法規名稱"], _lines(body)
    data = {"title": title, "issuer": issuer, "category": _local_category(title, fields.get("法規體系", "")),
            "date": _date(fields.get("修正日期") or fields.get("公發布日", "")), "source_url": url}
    status = _text(soup.select_one("[id$=divCauseStatusAnnDate]"))
    if status:
        data["status"] = status
    articles = _split_articles(lines)
    return data | ({"articles": articles} if articles else {"full_text": _join(lines)})


# ─────────────────────────────────────────────────────────────
# 臺北市（laws.gov.taipei，ASP.NET Core，GET 即可）
# ─────────────────────────────────────────────────────────────

TAIPEI_BASE = "https://laws.gov.taipei/Law/LawSearch/"
_TAIPEI_ID = re.compile(r"^FL\d{6}$")


async def _taipei_search(http, keyword: str, page: int) -> dict:
    # cursearchtype=2：條文內容命中（1 只比對名稱）；curcateid=001：市法規（002 為中央法規）
    params = {"searchtype": "1,2", "searchstring.keyword1": keyword, "searchstring.operaton1": "1",
              "taipeilevel": "1,2,3,6,4,5", "cursearchtype": "2", "curcateid": "001", "showtype": "1",
              "sort": "2", "page": page}
    soup = _soup(await http.get(TAIPEI_BASE + "SearchResult", params=params))
    items = []
    for a in soup.select("a.law-link"):
        rid = re.search(r"/(?:LawInformation|LawArticleContentResult)/(FL\d{6})", a["href"])
        if not rid:
            continue
        title = (a.get("title") or "").removeprefix("連結至") or _text(a)
        item = {"id": rid.group(1), "title": title, "issuer": "臺北市", "category": _local_category(title), "date": ""}
        if a.select_one(".abolished"):
            item["status"] = "已廢止"
        items.append(item)
    total = _total(soup, len(items))
    return _group("臺北市", "地方法規", total, items, page * PAGE_SIZE < total)


async def _taipei_get(http, native_id: str) -> dict:
    if not _TAIPEI_ID.match(native_id):
        raise LookupError(native_id)
    url = f"{TAIPEI_BASE}LawArticleContent/{native_id}"
    soup = _soup(await http.get(url))
    fields = {re.sub(r"\s", "", _text(g.label)): _text(g.select_one(".col-input"))
              for g in soup.select("div.info-upper div.form-group") if g.label}
    title = fields.get("名稱", "")
    if not title:
        raise LookupError(native_id)
    data = {"title": title.removeprefix("(廢)").strip(), "issuer": "臺北市",
            "category": _local_category(title, fields.get("法規位階", "").split("：")[0]),
            "date": _date(fields.get("修正日期") or fields.get("發布日期", "")), "source_url": url}
    if title.startswith("(廢)"):
        data["status"] = "已廢止"
    articles, points = [], []
    for li in soup.select("ul.law.law-content > li"):
        no, pre = li.select_one(".col-no"), li.select_one(".law-articlepre")
        if no and pre:
            articles.append({"number": _article_no(_text(no)), "content": _join(_lines(pre))})
        elif not any(c.startswith("chapter") for c in li.get("class", [])):
            points.append(_join(_lines(li)))  # 要點類以「一、二、」分點，沒有條號
    return data | ({"articles": articles} if articles else {"full_text": "\n".join(p for p in points if p)})


# ─────────────────────────────────────────────────────────────
# 新北市（web.law.ntpc.gov.tw，WebForms）：綜合查詢的 POST 會 302 到 SimpleQ2.aspx 的 GET
# ─────────────────────────────────────────────────────────────

NTPC_BASE = "https://web.law.ntpc.gov.tw/Scripts/"
_NTPC_ID = re.compile(r"^[A-Z]\d{7}$")


async def _ntpc_search(http, keyword: str, page: int) -> dict:
    soup = _soup(await http.get(NTPC_BASE + "SimpleQ2.aspx", params={"C1": "L", "K1": keyword}))
    rows = []
    # rptLegislationC 是「新北市政府新訂法規」；rptLegislationB 是中央法規，略過
    for a in soup.select("a[id*=rptLegislationC]"):
        m = re.search(r"fname=1([A-Z]\d{7})", a.get("href", ""))
        if m:
            title = _text(a)
            rows.append({"id": m.group(1), "title": title, "issuer": "新北市",
                         "category": _local_category(title), "date": ""})
    items = rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    return _group("新北市", "地方法規", len(rows), items, page * PAGE_SIZE < len(rows))


async def _ntpc_get(http, native_id: str) -> dict:
    if not _NTPC_ID.match(native_id):
        raise LookupError(native_id)
    url = f"{NTPC_BASE}FLAWDAT0202.aspx?fcode={native_id}"  # 「所有條文」頁（FLAWDAT01 只有法規名稱）
    soup = _soup(await http.get(url))
    head = soup.select_one("#cph_content_lawheader_law")
    if head is None or head.a is None:
        raise LookupError(native_id)
    title = _text(head.a)
    articles = [{"number": _article_no(_text(tr.select_one("td.col-th"))), "content": _join(_lines(tr.pre))}
                for tr in soup.select("table.tab-law01 tr") if tr.select_one("td.col-th") and tr.pre]
    data = {"title": title, "issuer": "新北市", "category": _local_category(title), "date": _tail_date(_text(head)),
            "source_url": url}
    if articles:
        return data | {"articles": articles}
    return data | {"full_text": _join(_lines(soup.select_one("table.tab-law01")))}


# ─────────────────────────────────────────────────────────────
# 全國法規資料庫 條約協定（law.moj.gov.tw，pcode Y 開頭）
# ─────────────────────────────────────────────────────────────

MOJ_BASE = "https://law.moj.gov.tw/"
_MOJ_PCODE = re.compile(r"^Y\d{7}$")
_MOJ_STATUS = {"終": "已終止", "廢": "已廢止"}


async def _moj_treaty_search(http, keyword: str, page: int) -> dict:
    # 只比對名稱、只接受單一詞（含空白會被導到錯誤頁）；結果依締約國／組織分組，一次只列一組
    country, kw = _split_country(keyword)
    url = MOJ_BASE + "Law/LawSearchResult.aspx"
    soup = _soup(await http.get(url, params={"ty": "CONVENTION", "kw": kw}))
    facets = []
    for a in soup.select("a[href*='LawSearchResult.aspx?cur=']"):
        cur, badge = re.search(r"cur=(\w+)", a["href"]), a.select_one(".badge")
        if cur and badge:
            facets.append((_text(a).removesuffix(_text(badge)).strip(), cur.group(1), int(_text(badge) or 0)))
    pick = next((f for f in facets if country in f[0]), None) if country else (facets[0] if facets else None)
    if pick and (pick is not facets[0] or page > 1):
        soup = _soup(await http.get(url, params={"ty": "CONVENTION", "kw": kw, "cur": pick[1], "page": page}))
    items = []
    for a in soup.select("a[href*='AddHotLaw.ashx?pcode=Y']") if pick else []:
        pcode = re.search(r"pcode=(Y\d{7})", a["href"])
        title = a.get("title") or _text(a)
        item = {"id": pcode.group(1), "title": title, "issuer": pick[0], "category": _treaty_category(title),
                "date": _tail_date(_text(a.parent))}
        label = _text(a.parent.select_one(".label-fei"))
        if label:
            item["status"] = _MOJ_STATUS.get(label, label)
        items.append(item)
    total = (pick[2] if pick else 0) if country else sum(f[2] for f in facets)
    group = _group("全國法規資料庫條約協定", "條約協定", total, items,
                   bool(pick) and page * PAGE_SIZE < pick[2])
    if len(facets) > 1 or (country and not pick):
        shown = f"本頁列出「{pick[0]}」{pick[2]} 筆；" if pick else f"沒有符合「{country}」的締約國／組織；"
        others = "、".join(f"{label} {n}" for label, _, n in facets[:15]) + ("…" if len(facets) > 15 else "")
        example = next((label for label, *_ in facets if not pick or label != pick[0]), "日本")
        group["note"] = (f"全國法規資料庫依締約國／組織分組（共 {len(facets)} 組：{others}）。{shown}"
                         f"在關鍵字前加國名或組織名（如「{example} {kw}」）可切換分組。")
    return group


async def _moj_treaty_get(http, pcode: str) -> dict:
    if not _MOJ_PCODE.match(pcode):
        raise LookupError(pcode)
    url = f"{MOJ_BASE}LawClass/LawAll.aspx?pcode={pcode}"
    r = await http.get(url)
    if r.status_code in (400, 404):
        raise LookupError(pcode)
    soup = _soup(r)
    fields = _fields(soup.select("table tr"))
    body = soup.select_one(".law-agree .col-data")
    if not fields.get("法規名稱") or body is None:
        raise LookupError(pcode)
    title = fields["法規名稱"]
    data = {"title": title, "issuer": fields.get("簽約國", ""), "category": _treaty_category(title),
            "date": _date(fields.get("簽訂日期", "")), "full_text": _join(_lines(body)), "source_url": url}
    if fields.get("生效日期"):
        data["effective_date"] = _date(fields["生效日期"])
    return data


# ─────────────────────────────────────────────────────────────
# 外交部 中華民國條約協定資料庫（no06.mofa.gov.tw）：查詢表單 POST 會 302 到 Result.aspx 的 GET；全文只有 PDF
# ─────────────────────────────────────────────────────────────

MOFA_BASE = "https://no06.mofa.gov.tw/mofatreatys/"
_MOFA_FILE = re.compile(r"^(\d{2})[0-9A-Za-z_\-]*\.pdf$")  # 檔名前兩碼即 FileFolder
_MOFA_EMPTY = dict.fromkeys(("tysubject_c", "tysubject_e", "tysubject_o", "tycountry_c", "tycountry_e",
                             "tyeffectivedate", "tyeffectivedateE", "tysigneddate", "tysigneddateE",
                             "tykeyword", "tyclass"), "") | {"start": "Y", "Order": "Signing"}


async def _mofa_search(http, keyword: str, page: int) -> dict:
    country, kw = _split_country(keyword)
    r = await http.get(MOFA_BASE + "Result.aspx",
                       params=_MOFA_EMPTY | {"tykeyword": kw, "tycountry_c": country, "tycountry_e": country})
    soup = _soup(r)
    if page > 1:  # 只能用「跳頁」postback 翻頁
        form = {i["name"]: i.get("value", "") for i in soup.select("input[type=hidden]") if i.get("name")}
        form |= {"frmGrd_ASPager$txtPage": str(page), "frmGrd_ASPager$btnPage": "跳頁"}
        soup = _soup(await http.post(r.url, data=form))
    pages = re.search(r"\[(\d+) of (\d+)\]", soup.get_text(" "))
    items = []
    for subject in soup.select("span[id$=_frmGrd_tysubject_c]"):
        tr, a = subject.find_parent("tr"), subject.select_one("a[href*='FileName=']")
        name = re.search(r"FileName=([^&]+)", a["href"]) if a else None
        if not name:
            continue
        title = _text(a)
        party = tr.select_one("span[id$=_frmGrd_tycountry_c]")
        items.append({"id": name.group(1), "title": title,
                      "issuer": party.get_text("\n", strip=True).split("\n")[0] if party else "",
                      "category": _treaty_category(title),
                      "date": _date(_text(tr.select_one("span[id$=_frmGrd_tysigneddate]")))})
    cur, last = (int(pages.group(1)), int(pages.group(2))) if pages else (page, page)
    total = (last - 1) * 10 + len(items) if cur == last else last * 10
    group = _group("外交部條約協定資料庫", "條約協定", total, items, cur < last)
    if cur < last:
        group["note"] = f"外交部資料庫只顯示頁數（共 {last} 頁，每頁 10 筆），total 為估計值。"
    return group


async def _mofa_get(http, name: str) -> dict:
    m = _MOFA_FILE.match(name)
    if not m:
        raise LookupError(name)
    pdf_url = f"{MOFA_BASE}ShowPicOut.aspx?FileFolder={m.group(1)}&FileName={name}"
    r = await http.get(pdf_url)
    r.raise_for_status()
    if r.content[:4] != b"%PDF":
        raise LookupError(name)
    text = await asyncio.to_thread(pdf_to_text, r.content)
    # PDF 沒有標題欄位：取開頭到第一個「協定／協議…（中譯本）」為名稱
    m = re.match(r".{0,100}?(?:協定|協議|條約|公約|換文|換函|備忘錄|議定書)(?:[（(]中譯本[)）])?", text)
    title = m.group() if m else name
    data = {"title": title, "issuer": "", "category": _treaty_category(title), "date": "",
            "full_text": text, "source_url": MOFA_BASE + "Index.aspx", "pdf_url": pdf_url}
    return data | ({} if text else {"note": PDF_NOTE})


# ─────────────────────────────────────────────────────────────
# 財政部 我國所得稅協定網絡（www.mof.gov.tw）：清單頁可抓；/download/ 被 robots.txt 排除，只在使用者指定時單次下載
# ─────────────────────────────────────────────────────────────

MOF_LIST_URL = "https://www.mof.gov.tw/singlehtml/191?cntId=82769"
_MOF_ID = re.compile(r"^(?:\d{1,8}|[0-9a-f]{32})$")


async def _mof_list(http) -> list[dict]:
    soup = _soup(await http.get(MOF_LIST_URL))
    rows: dict[str, dict] = {}
    for a in soup.select("a[href*='/download/']"):
        rid, name = a["href"].rsplit("/", 1)[-1], _text(a)
        if not _MOF_ID.match(rid) or rid in rows:
            continue
        if name in ("舊約", "新約"):
            name = f"新加坡（{name}）"
        shipping = a.find_previous(string=re.compile("二、海")) is not None  # 第二段是海空運輸單項協定
        title = re.sub(r"\(pdf.*$", "", a.get("title", "")).strip()
        if not title or title.lower().endswith(".pdf"):
            title = f"與{name}" + ("海空運輸所得互免所得稅協定" if shipping else "全面性所得稅協定")
        rows[rid] = {"id": rid, "title": title, "issuer": "財政部", "category": "租稅協定", "date": "",
                     "summary": "海、空或海空國際運輸所得互免所得稅單項協定" if shipping else "全面性所得稅協定"}
    return list(rows.values())


async def _mof_search(http, keyword: str, page: int) -> dict:
    words = keyword.split()
    hits = [r for r in await _mof_list(http) if all(w in f"{r['title']} {r['summary']} 租稅協定" for w in words)]
    return _group("財政部所得稅協定", "租稅協定", len(hits), hits[(page - 1) * PAGE_SIZE: page * PAGE_SIZE],
                  page * PAGE_SIZE < len(hits))


async def _mof_get(http, rid: str) -> dict:
    if not _MOF_ID.match(rid):
        raise LookupError(rid)
    row = next((r for r in await _mof_list(http) if r["id"] == rid), None)
    if row is None:
        raise LookupError(rid)
    pdf_url = f"https://www.mof.gov.tw/download/{rid}"
    r = await http.get(pdf_url)
    r.raise_for_status()
    text = await asyncio.to_thread(pdf_to_text, r.content)
    data = {k: row[k] for k in ("title", "issuer", "category", "date")}
    data |= {"full_text": text, "source_url": MOF_LIST_URL, "pdf_url": pdf_url}
    return data | ({} if text else {"note": PDF_NOTE})


# ─────────────────────────────────────────────────────────────
# 臺灣證券交易所 法規分享知識庫（行動版 /m/；每頁 5 筆，只能用「下一頁」postback 逐頁翻）
# ─────────────────────────────────────────────────────────────

TWSE_BASE = "https://twse-regulation.twse.com.tw/m/"
_TWSE_ID = re.compile(r"^FL\d{6}$")
TWSE_MAX_PAGE = 10


async def _twse_search(http, keyword: str, page: int) -> dict:
    if page > TWSE_MAX_PAGE:
        raise ValueError(f"證交所行動版只能逐頁翻，最多查到第 {TWSE_MAX_PAGE} 頁；請改用更精確的關鍵字")
    r = await http.get(TWSE_BASE + "SearchList.aspx", params={"KW": keyword, "lname": "1", "lcontent": "1"})
    soup = _soup(r)
    for _ in range(page - 1):
        form = {i["name"]: i.get("value", "") for i in soup.select("input[type=hidden]") if i.get("name")}
        form["__EVENTTARGET"] = "ctl00$cphMain$ucPager$butNext"
        soup = _soup(await http.post(r.url, data=form))
    items = []
    for a in soup.select("a[href^='LawNoContent.aspx?FID=']"):
        fid = re.search(r"FID=(FL\d{6})", a["href"])
        if fid:
            items.append({"id": fid.group(1), "title": a.get_text(strip=True), "issuer": "臺灣證券交易所",
                          "category": "規章", "date": _tail_date(_text(a.parent))})
    total = _total(soup, len(items))
    return _group("臺灣證券交易所", "規章", total, items, page * 5 < total)


async def _twse_get(http, fid: str) -> dict:
    if not _TWSE_ID.match(fid):
        raise LookupError(fid)
    url = f"{TWSE_BASE}LawContent.aspx?FID={fid}"
    soup = _soup(await http.get(url))
    head = _text(soup.select_one("div.law-name")).removeprefix("法規名稱：").strip()
    if not head:
        raise LookupError(fid)
    m = re.match(r"(.*?)（([\d.]+)）$", head)  # 名稱後綴「（115.09.30）」為最近修正日
    articles = [{"number": _article_no(_text(d.b)), "content": _join(_lines(d.pre))}
                for d in soup.select("div.law-no") if d.b and d.pre]
    data = {"title": m.group(1) if m else head, "issuer": "臺灣證券交易所", "category": "規章",
            "date": _date(m.group(2)) if m else "", "source_url": url}
    if articles:
        return data | {"articles": articles}
    return data | {"full_text": "\n".join(_join(_lines(p)) for p in soup.select("div.Content pre"))}


# ─────────────────────────────────────────────────────────────
# 櫃買中心、期交所：證券暨期貨法令判解查詢系統（www.selaw.com.tw）「法規名稱查詢」可依發布單位篩選
# ─────────────────────────────────────────────────────────────

SELAW_BASE = "https://www.selaw.com.tw/Chinese/"
_SELAW_ID = re.compile(r"^LW\d{8}$")


async def _selaw_search(org: str, label: str, http, keyword: str, page: int) -> dict:
    params = {"criteria.keyWord1": keyword, "criteria.cbxlawSimpleName": org, "criteria.lawPageNumber": page}
    soup = _soup(await http.get(SELAW_BASE + "RegulationNameQuery/LawList", params=params))
    items = []
    for a in soup.select("a[href^='/Chinese/RegulatoryInformationResult?sysNumber=']"):
        sid, rel = re.search(r"sysNumber=(LW\d{8})", a["href"]), re.search(r"releaseDate=([\d-]+)", a["href"])
        if sid:
            items.append({"id": sid.group(1), "title": a.get("title") or _text(a), "issuer": label,
                          "category": "規章", "date": rel.group(1) if rel else ""})
    total = _total(soup, len(items))
    return _group(label, "規章", total, items, page * PAGE_SIZE < total, note=SELAW_NOTE)


async def _selaw_get(org: str, label: str, http, sid: str) -> dict:
    if not _SELAW_ID.match(sid):
        raise LookupError(sid)
    url = f"{SELAW_BASE}RegulatoryInformationResult/Article?sysNumber={sid}"
    soup = _soup(await http.get(url))
    meta = {_text(tds[0]): tds[1] for tds in (tr.find_all("td") for tr in soup.select("table.con-table-top tr"))
            if len(tds) == 2}
    name = meta.get("法規名稱")
    if name is None or name.a is None:
        raise LookupError(sid)
    articles, no = [], None
    for ol in soup.select("div.con-rules > ol"):
        if "rules-lv01" in ol["class"]:
            no = _article_no(_text(ol))
        elif no is not None:  # 每個 div 已是一段或一款，不需再接行
            articles.append({"number": no, "content": "\n".join(
                _text(d) for d in ol.find_all("div") if not d.find("div") and _text(d))})
            no = None
    data = {"title": _text(name.a), "issuer": label, "category": "規章",
            "date": _date(_text(meta.get("發佈日期"))), "source_url": url, "note": SELAW_NOTE}
    status = _text(name.font).strip("()（）")
    if status:
        data["status"] = status
    if articles:
        return data | {"articles": articles}
    return data | {"full_text": _join(_lines(soup.select_one("div.con-rules")))}


# ─────────────────────────────────────────────────────────────
# 對外介面
# ─────────────────────────────────────────────────────────────

# 代碼 → (縣市, 網址前綴, 額外別名)；皆已實測可連線
_GLRS_SITES = {
    "taichung": ("臺中市", "https://law.taichung.gov.tw/", ("中市",)),
    "kaohsiung": ("高雄市", "https://outlaw.kcg.gov.tw/", ("高市",)),
    "tainan": ("臺南市", "https://law01.tainan.gov.tw/glrsnewsout/", ("南市",)),
    "hsinchu_county": ("新竹縣", "https://hclaw.hsinchu.gov.tw/law/", ("竹縣",)),
    "hsinchu_city": ("新竹市", "https://law.hccg.gov.tw/", ("竹市",)),
    "miaoli": ("苗栗縣", "https://law.miaoli.gov.tw/glrsnewsout/", ()),
    "changhua": ("彰化縣", "https://lawsearch.chcg.gov.tw/GLRSNEWSOUT/", ()),
    "chiayi_county": ("嘉義縣", "https://law.cyhg.gov.tw/", ()),
    "chiayi_city": ("嘉義市", "https://law.chiayi.gov.tw/", ()),
    "pingtung": ("屏東縣", "https://ptlaw.pthg.gov.tw/", ()),
    "taitung": ("臺東縣", "https://law.taitung.gov.tw/", ()),
    "penghu": ("澎湖縣", "https://law.penghu.gov.tw/glrsnewsout/", ()),
    "kinmen": ("金門縣", "https://law.kinmen.gov.tw/", ()),
}


def _county_aliases(name: str, *extra: str) -> tuple[str, ...]:
    """臺中市 → 臺中市、台中市、臺中、台中；「新竹」「嘉義」同時對到縣與市。"""
    names = {name, name[:-1], *extra}
    return tuple(sorted(names | {n.replace("臺", "台") for n in names})) + ("地方法規",)


SOURCES = {
    "taipei": ("臺北市", _county_aliases("臺北市", "北市"), _taipei_search, _taipei_get),
    "ntpc": ("新北市", _county_aliases("新北市"), _ntpc_search, _ntpc_get),
    **{key: (name, _county_aliases(name, *extra), partial(_glrs_search, base, name), partial(_glrs_get, base, name))
       for key, (name, base, extra) in _GLRS_SITES.items()},
    "moj_treaty": ("全國法規資料庫條約協定", ("條約", "協定", "條約協定", "全國法規資料庫"),
                   _moj_treaty_search, _moj_treaty_get),
    "mofa": ("外交部條約協定資料庫", ("條約", "協定", "條約協定", "外交部"), _mofa_search, _mofa_get),
    "mof_tax": ("財政部所得稅協定", ("協定", "租稅協定", "所得稅協定", "財政部"), _mof_search, _mof_get),
    "twse": ("臺灣證券交易所", ("證交所", "台灣證券交易所", "交易所規章"), _twse_search, _twse_get),
    "tpex": ("證券櫃檯買賣中心", ("櫃買中心", "櫃買", "櫃檯買賣中心", "交易所規章"),
             partial(_selaw_search, "0203", "證券櫃檯買賣中心"), partial(_selaw_get, "0203", "證券櫃檯買賣中心")),
    "taifex": ("臺灣期貨交易所", ("期交所", "台灣期貨交易所", "交易所規章"),
               partial(_selaw_search, "0207", "臺灣期貨交易所"), partial(_selaw_get, "0207", "臺灣期貨交易所")),
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


def _shape(data: dict, article_no: str) -> dict:
    """依 article_no 篩條文（未分條的全文會先試著切條），再把總長截在 MAX_TEXT。"""
    data = dict(data)
    if article_no:
        target = _article_no(article_no)
        articles = data.get("articles") or _split_articles((data.get("full_text") or "").splitlines())
        hit = [a for a in articles if a["number"] == target]
        if not hit:
            raise LookupError(f"查無第 {article_no} 條" + ("" if articles else "（此文件未分條，請改看 full_text）"))
        data.pop("full_text", None)
        data["articles"] = hit
    if data.get("articles"):
        kept, size = [], 0
        for a in data["articles"]:
            size += len(a["content"])
            if size > MAX_TEXT and kept:
                break
            kept.append(a)
        data["truncated"] = len(kept) < len(data["articles"])
        data["articles"] = kept
    else:
        text = data.get("full_text") or ""
        data["truncated"] = len(text) > MAX_TEXT
        data["full_text"] = text[:MAX_TEXT]
    if data["truncated"]:
        data["note"] = " ".join(filter(None, [data.get("note"), f"內容超過 {MAX_TEXT} 字已截斷，可用 article_no 指定條號。"]))
    return data


class OtherRegulationClient:
    def __init__(self, cache: CacheDB):
        self.cache = cache
        self.http = httpx.AsyncClient(timeout=60.0, headers={"User-Agent": fint.USER_AGENT}, follow_redirects=True)

    async def close(self):
        await self.http.aclose()

    async def search(self, keyword: str, source: str = "", page: int = 1) -> dict:
        if not keyword.strip():
            return error_response("請提供關鍵字")
        keys = resolve_sources(source)
        if keys is None:
            return error_response(f"不支援的來源「{source}」", supported_sources=[label for label, *_ in SOURCES.values()])
        params = {"tool": "other_regulations", "keyword": keyword.strip(), "sources": keys, "page": page}
        cached = await self.cache.get_search(params)
        if cached:
            return {**cached, "cached": True}

        async def run(key: str) -> dict:
            label, _, search, _ = SOURCES[key]
            try:
                group = await search(self.http, keyword.strip(), page)
            except Exception as e:  # 單一來源掛掉不拖垮其他來源
                logger.warning("其他法規搜尋失敗 %s: %s", key, e, exc_info=not isinstance(e, (httpx.HTTPError, ValueError)))
                return {"source": label, "error": f"{type(e).__name__}: {e}"}
            for item in group["items"]:
                item["id"] = f"{key}:{item['id']}"
            return group

        groups = await asyncio.gather(*(run(k) for k in keys))
        result = {
            "success": True, "keyword": keyword.strip(), "page": page,
            "categories": [{k: v for k, v in g.items() if k != "items"}
                           | ({"returned": len(g["items"])} if "items" in g else {}) for g in groups],
            "total_count": sum(g.get("total", 0) for g in groups),
            "results": [i for g in groups for i in g.get("items", [])],
            "timestamp": datetime.now().isoformat(),
        }
        if not any("error" in g for g in groups):
            await self.cache.set_search(params, result)
        return result

    async def get(self, reg_id: str, article_no: str = "") -> dict:
        key, _, native_id = reg_id.strip().partition(":")
        if key not in SOURCES or not native_id:
            return error_response(f"id 格式錯誤：「{reg_id}」，請使用搜尋結果回傳的 id")
        label, _, _, get = SOURCES[key]
        data = await self.cache.get_regulation(reg_id)
        cached = data is not None
        if not cached:
            try:
                data = await get(self.http, native_id)
            except LookupError:
                return error_response(f"{label}查無此件：{reg_id}")
            except (httpx.HTTPError, ValueError) as e:
                return error_response(f"{label}連線或解析失敗：{type(e).__name__}: {e}")
            data = {"id": reg_id, "source": label, **data}
            await self.cache.set_regulation(reg_id, data)
        try:
            return {"success": True, "cached": cached, **_shape(data, article_no)}
        except LookupError as e:
            return error_response(f"{data['title']}{e.args[0]}", id=reg_id)
