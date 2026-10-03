"""準司法機關決定：工程會採購申訴審議判斷、勞動部不當勞動行為裁決、保訓會復審／再申訴決定、金管會裁罰案件、
監察院調查報告／糾正案／彈劾案／糾舉案、律師懲戒決議

查詢時即時向官方網站取得，不批次抓取。id 一律為「來源代碼:原站識別碼」，例如 pcc_complaint:訴1130123、
uflb:114-56、csptc:184254、fsc_sanction:sfb-202608120002、cy_report:133-49407、lawyer_discipline:FF26…。
官方已遮蔽的姓名（○○、ΟΟΟ、ＯＯ）原樣保留；律師懲戒決議的被付懲戒律師姓名為官方公開資訊。

未收錄：NCC（Cloudflare 驗證擋自動連線）、個資會（尚無公開決定）、懲戒法院／職務法庭（已在裁判書查詢）、
工程會履約爭議調解（只公開進度，沒有內容）。
"""

from __future__ import annotations

import asyncio
import base64
import io
import re
import time
from datetime import date, timedelta
from functools import partial
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from mcp_server.tools import medical_discipline
from mcp_server.tools.agency_interpretations import _date, _group, _html_text, _office_text, _paged, _session, _text
from mcp_server.tools.pdf_text import clean_pdf_text, is_garbled, pdf_to_text

PAGE = 20


def _csrf(html: str, site: str) -> str:
    m = re.search(r'name="_csrf"[^>]*value="([^"]+)"', html)
    if not m:
        raise ValueError(f"{site}查詢頁找不到 CSRF token")
    return m.group(1)


def _hidden(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    return {i["name"]: i.get("value", "") for i in soup.select("input[type=hidden]") if i.get("name")}


# 決定書 PDF／純文字多半整段黏在一起：在主文、事實及理由、【…】與句末後的「一、」「（一）」處斷行
_ENUM = r"[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾]+、|[（(][一二三四五六七八九十]+[)）]"


def _layout(text: str) -> str:
    text = re.sub(rf"(?<=[。：」])\s*(主文|事實及理由|事實|理由)\s*(?={_ENUM})", r"\n\1\n", text)
    text = re.sub(r"\s*(【[^】]{1,10}】)\s*", r"\n\1\n", text)
    return re.sub(rf"(?<=[。：」])\s*({_ENUM})", r"\n\1", text).strip()


# ─────────────────────────────────────────────────────────────
# 工程會 採購申訴審議判斷（web.pcc.gov.tw/piat/piaq）：只能用案號或收案日期查，沒有關鍵字檢索
# ─────────────────────────────────────────────────────────────

PCC_BASE = "https://web.pcc.gov.tw/piat/piaq/"
_PCC_CASE = re.compile(r"^訴\d{7}$")
_PCC_PAGE = 100


async def _pcc_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    number, notes = doc_number.strip(), []
    form = {"currentPage": page, "pagesize": _PCC_PAGE, "pkPiatCase": "", "questionName": "",
            "caseRecvStartDate": "", "caseRecvEndDate": ""}
    if number:
        if not _PCC_CASE.match(number):
            return _group("工程會", "採購申訴審議判斷", 0, [], False, note="案號須為完整申訴案號，例如「訴1130123」")
        form["pkPiatCase"] = number
    else:
        today = date.today()
        if not (year_from or year_to):
            year_start = today - timedelta(days=365)
            notes.append("未指定年度，查詢最近 12 個月收案的案件")
        else:
            year_start = date(year_from + 1911, 1, 1) if year_from else date(2000, 1, 1)
        year_end = min(date(year_to + 1911, 12, 31), today) if year_to else today
        # 收案日期只收西元 yyyy/MM/dd（民國格式會靜默回傳 0 筆）
        form["caseRecvStartDate"] = year_start.strftime("%Y/%m/%d")
        form["caseRecvEndDate"] = year_end.strftime("%Y/%m/%d")
        notes.append("總數含履約爭議調解案件（調解內容不公開，已從列表略過）")
    async with _session() as s:
        index = await s.get(PCC_BASE + "index")
        index.raise_for_status()
        r = await s.post(PCC_BASE + "readPiaqCase", data={"_csrf": _csrf(index.text, "工程會"), **form})
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    terms = keyword.split() if not number else []
    items = []
    for tr in soup.select("table.tb_01 tr.tb_b2"):
        tds = [_text(td) for td in tr.find_all("td")]
        if len(tds) < 5 or not _PCC_CASE.match(tds[0]):
            continue
        result = tds[4].replace("判斷理由", "").strip()
        summary = f"{tds[2]}；{result}" + ("" if tr.select_one("a[title=判斷理由]") else "（未公開判斷理由）")
        if not all(t in summary for t in terms):
            continue
        closed = re.search(r"\d{4}/\d{2}/\d{2}", tds[3])
        items.append({
            "id": f"pcc_complaint:{tds[0]}", "agency": "行政院公共工程委員會", "category": "採購申訴審議判斷",
            "doc_number": tds[0], "date": _date(closed.group() if closed else tds[1]), "summary": summary,
        })
    if terms:
        notes.append("此系統沒有關鍵字檢索：關鍵字只比對本頁各案的問題類型與審理結果，請搭配年度或案號查詢")
    m = re.search(r"([\d,]+)", _text(soup.select_one("#pagebanner")))
    total = int(m.group(1).replace(",", "")) if m else len(items)
    return _group("工程會", "採購申訴審議判斷", total, items, page * _PCC_PAGE < total,
                  **({"note": "；".join(notes)} if notes else {}))


async def _pcc_get(http, case_no: str) -> dict:
    if not _PCC_CASE.match(case_no):
        raise LookupError(case_no)
    url = PCC_BASE + "readPiaqCaseDetail/" + quote(case_no)
    r = await http.get(url)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    fields = {_text(k): v for k, v in zip(soup.select("td.tbg_1"), soup.select("td.tbg_2"))}
    if _text(fields.get("案號")) != case_no:
        raise LookupError(case_no)
    full = _html_text(str(fields["判斷理由"])) if "判斷理由" in fields else ""
    return {
        "agency": "行政院公共工程委員會", "category": "採購申訴審議判斷", "doc_number": case_no,
        "date": _date(_text(fields.get("結案日期"))), "summary": _text(fields.get("案件審理結果")),
        "status": _text(fields.get("目前辦理狀態")),
        "full_text": full,
        "notes": "工程會只公開判斷理由，未公開主文及當事人欄。" + ("" if full else "本案尚未公開判斷理由。"),
        "source_url": url,
    }


# ─────────────────────────────────────────────────────────────
# 勞動部 不當勞動行為裁決（uflb.mol.gov.tw）：DataTables JSON + PDF 全文
# ─────────────────────────────────────────────────────────────

UFLB_BASE = "https://uflb.mol.gov.tw"
_UFLB_CASE = re.compile(r"^\d{2,3}-(?:更\([一二三四五六七八九十]+\)-)?\d{1,3}$")
_UFLB_NUMBER = re.compile(r"(\d{2,3})\s*年\s*勞裁字\s*第\s*(\d+)\s*號")
_UFLB_MAX_START = 400  # 官網 start >= 400 一律回 0 筆


async def _uflb_query(keyword: str, start: int, length: int) -> dict:
    async with _session() as s:
        page = await s.get(UFLB_BASE + "/front/querydecision")
        page.raise_for_status()
        r = await s.post(UFLB_BASE + "/front/filterDecisionList", data={
            "draw": 1, "start": start, "length": length, "_csrf": _csrf(page.text, "勞動部裁決"),
            "keyword": keyword, "filterCaseType": "", "filterStatus": "",
        })
        r.raise_for_status()
        return r.json()


def _uflb_doc_number(case_no: str) -> str:
    m = re.fullmatch(r"(\d+)-(\d+)", case_no)
    return f"{m.group(1)}年勞裁字第{int(m.group(2))}號" if m else case_no


def _uflb_item(x: dict) -> dict:
    parts = [x.get("caseType"), f"申請人：{x.get('appoName') or ''}", f"相對人：{x.get('oppoTitle') or ''}",
             x.get("filterStatus")]
    return {
        "id": f"uflb:{x['caseNo']}", "agency": "勞動部不當勞動行為裁決委員會", "category": "不當勞動行為裁決",
        # 列表的上傳時間不是決定日期（有未來日期），日期以決定書全文為準
        "doc_number": _uflb_doc_number(x["caseNo"]), "date": "",
        "summary": "；".join(p for p in parts if p and p != "-" and not p.endswith("：")),
    }


async def _uflb_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    number = doc_number.strip()
    m = _UFLB_NUMBER.search(number)
    kw = f"{m.group(1)}-{int(m.group(2)):02d}" if m else (number or keyword.strip())
    single_year = year_from if year_from and year_from == year_to else 0
    if not kw and single_year:
        kw = f"{single_year}-"  # 案號前綴「114-」= 114 年收案
    start = (page - 1) * PAGE
    notes = []
    if start >= _UFLB_MAX_START:
        data = {"recordsFiltered": 0, "data": []}
    else:
        data = await _uflb_query(kw, start, PAGE)
    total = int(data.get("recordsFiltered") or 0)
    rows = [x for x in data.get("data") or [] if x.get("caseNo")]
    if year_from or year_to:  # 「114-」也會比對到內文，一律再以案號年度過濾
        lo, hi = year_from or 0, year_to or 999
        rows = [x for x in rows if lo <= int(x["caseNo"].split("-")[0]) <= hi]
        notes.append("年度以案號年度（收案年度）比對本頁各案，總數為官網關鍵字命中數")
    if total > _UFLB_MAX_START:
        notes.append("官網最多只能翻到第 400 筆，較舊的案件請加關鍵字（如案號「100-」）縮小範圍")
    return _group("勞動部不當勞動行為裁決委員會", "不當勞動行為裁決", total, [_uflb_item(x) for x in rows],
                  start + PAGE < min(total, _UFLB_MAX_START), **({"note": "；".join(notes)} if notes else {}))


def _strip_line_numbers(page: str) -> list[str]:
    """裁決書每行行尾有 1、2、3… 的行號；連續對上 5 行以上才視為有行號，避免誤刪內文數字。"""
    out, n = [], 1
    for line in page.splitlines():
        s = line.rstrip()
        if s.endswith(str(n)):
            s, n = s[: -len(str(n))], n + 1
        out.append(s)
    return out if n > 5 else page.splitlines()


def _numbered_pdf_text(blob: bytes) -> str:
    if blob[:4] != b"%PDF":
        return ""
    from pypdf import PdfReader

    try:
        pages = [p.extract_text() or "" for p in PdfReader(io.BytesIO(blob)).pages]
    except Exception:  # pypdf 對損壞檔案會拋各種例外
        return ""
    lines = [s for p in pages for s in _strip_line_numbers(p)
             if not re.fullmatch(r"\s*第\s*\d+\s*頁\s*[，,]\s*共\s*\d+\s*頁\s*", s)]
    text = clean_pdf_text("\n".join(lines))
    return "" if is_garbled(text) else _layout(text)


def _decision_date(text: str) -> str:
    """決定書末尾的「中華民國 115 年 9 月 4 日」（PDF 常把年份拆成「1 1 5」）。"""
    hits = re.findall(r"中\s*華\s*民\s*國\s*([\d\s]{2,7}?)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if not hits:
        return ""
    y, mo, d = hits[-1]
    return _date(f"{y.replace(' ', '')}/{mo}/{d}")


async def _uflb_get(http, case_no: str) -> dict:
    if not _UFLB_CASE.match(case_no):
        raise LookupError(case_no)
    data = await _uflb_query(case_no, 0, PAGE)
    row = next((x for x in data.get("data") or [] if x.get("caseNo") == case_no), None)
    if row is None or not (row.get("link") or "").startswith("/"):
        raise LookupError(case_no)
    pdf_url = UFLB_BASE + row["link"]
    r = await http.get(pdf_url)
    r.raise_for_status()
    text = await asyncio.to_thread(_numbered_pdf_text, r.content)
    item = _uflb_item(row)
    return {
        "agency": item["agency"], "category": item["category"], "doc_number": item["doc_number"],
        "date": _decision_date(text), "summary": item["summary"],
        "full_text": text,
        "notes": "" if text else "PDF 無法擷取文字，請開 pdf_url 閱讀。",
        "pdf_url": pdf_url,
        "source_url": UFLB_BASE + "/front/querydecision",
    }


# ─────────────────────────────────────────────────────────────
# 保訓會 保障事件決定書（web13.csptc.gov.tw）：ASP.NET postback，每頁 20 筆
# ─────────────────────────────────────────────────────────────

CSPTC_BASE = "https://web13.csptc.gov.tw/"
_CS = "ctl00$ContentPlaceHolder1$"
_CSPTC_NO = re.compile(r"(\d{2,3})\s*年?\s*(公審決再|公申決再|公審決|公申決)\s*字?\s*第?\s*(\d+)")


def _csptc_number(s: str) -> str:
    """「115 公審決再字 000092 號」→「115公審決再字第000092號」（與決定書內文寫法一致）。"""
    m = re.search(r"(\d+)\s*(\S+?)字\s*(?:第\s*)?(\d+)\s*號", s)
    return f"{m.group(1)}{m.group(2)}字第{m.group(3)}號" if m else s


async def _csptc_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    m = _CSPTC_NO.search(doc_number)
    fields = {
        _CS + "txtCaseYear": m.group(1) if m else "", _CS + "ddlCaseWord": m.group(2) if m else "",
        _CS + "txtCaseNo": m.group(3) if m else "", _CS + "ddlSelect": "",
        _CS + "txtStartDate": f"{year_from:03d}0101" if year_from else "",
        _CS + "txtEndDate": f"{year_to:03d}1231" if year_to else "",
        _CS + "ddlptcresult": "",
        _CS + "ddlSP": "1",  # 官網預設不含退撫給與（年金改革）案件；選「是」時關鍵字檢索幾乎失效
        _CS + "ddlMdetDac": "all", _CS + "ddlMdetDcc": "all",
        # 「含有」欄以逗號分隔多個詞 = 全部都要有；空白會被當成詞的一部分
        _CS + "txtKWD1": ",".join(keyword.split()), _CS + "txtKWD2": "", _CS + "txtKWD3": "", _CS + "txtKWD4": "",
    }
    if doc_number.strip() and not m:
        return _group("保訓會", "復審、再申訴決定", 0, [], False, note="決定字號格式例：114公審決字第000020號")
    async with _session() as s:
        r = await s.get(CSPTC_BASE + "index.aspx")
        r.raise_for_status()
        r = await s.post(CSPTC_BASE + "index.aspx", data={**_hidden(r.text), **fields, _CS + "btnSearch": "送出查詢"})
        r.raise_for_status()
        if page > 1:  # 跳頁選單的值是頁碼 - 1
            pick = {_CS + "ddlSelectPage": str(page - 1), _CS + "ddlSelectPage2": str(page - 1)}
            r = await s.post(CSPTC_BASE + "index.aspx", data={
                **_hidden(r.text), **fields, **pick, "__EVENTTARGET": _CS + "ddlSelectPage"})
            r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    total = int(_text(soup.select_one("#ContentPlaceHolder1_lblTotal")) or 0)
    current = int(_text(soup.select_one("#ContentPlaceHolder1_lblcurPage")) or 1)
    items = []
    for a in soup.select("a[id*=hplCaseNo]") if current == page else []:
        in_id = re.search(r"IN_ID=(\d+)", a.get("href", ""))
        table = a.find_parent("table")
        if not in_id or table is None:
            continue

        def cell(name: str) -> str:
            return _text(table.select_one(f"span[id*={name}]"))

        items.append({
            "id": f"csptc:{in_id.group(1)}", "agency": "公務人員保障暨培訓委員會",
            "category": cell("lblFClassType").split("/")[0] or "保障事件決定",
            "doc_number": _csptc_number(_text(a)), "date": _date(cell("lblJDate")),
            "summary": f"{cell('lblFClassType')}；{cell('lblJSource')}",
        })
    return _group("保訓會", "復審、再申訴決定", total, items, page * PAGE < total)


async def _csptc_get(http, in_id: str) -> dict:
    if not in_id.isdigit():
        raise LookupError(in_id)
    url = f"{CSPTC_BASE}SearchContent.aspx?IN_ID={in_id}"
    r = await http.get(url)
    r.raise_for_status()
    tables = BeautifulSoup(r.text, "html.parser").select("div.frame table.MdetP-Table")
    head = {}
    for td in tables[0].select("td") if tables else []:
        label, _, value = _text(td).partition("：")
        head[label] = value.strip()
    if "字第" not in head.get("決定字號", ""):  # 不存在的 IN_ID 回傳空殼頁
        raise LookupError(in_id)
    lines = [head.get("全文內容", "")] + [
        " ".join(_text(td) for td in tr.find_all("td")).strip() for t in tables[1:] for tr in t.find_all("tr")]
    return {
        "agency": "公務人員保障暨培訓委員會", "category": head.get("案件類型", "").split("/")[0],
        "doc_number": head["決定字號"], "date": _date(head.get("決定日期", "")), "summary": head.get("案件類型", ""),
        "full_text": "\n".join(l for l in lines if l),
        "source_url": url,
    }


# ─────────────────────────────────────────────────────────────
# 金管會 + 銀行局 + 證期局 + 保險局 裁罰案件：同一套 CMS，GET 即可查詢
# ─────────────────────────────────────────────────────────────

_FSC_SITES = {  # 代碼: (機關, 類別, 網站, 清單 id, parentpath)
    "fsc": ("金融監督管理委員會", "重大裁罰", "https://www.fsc.gov.tw", "131", "0,2"),
    "banking": ("金管會銀行局", "非重大裁罰", "https://www.banking.gov.tw", "550", "0,524,547"),
    "sfb": ("金管會證券期貨局", "裁罰案件", "https://www.sfb.gov.tw", "104", "0,2,102"),
    "ib": ("金管會保險局", "裁罰案件", "https://www.ib.gov.tw", "42", "0,2"),
}
_FSC_ID = re.compile(r"^(fsc|banking|sfb|ib)-(\d{8,16})$")
_FSC_PAGE = 10
_FSC_DOC_NO = re.compile(r"金管[\u4e00-\u9fff]{1,6}字第\s*\d+\s*號")  # 標題常寫「(115年9月2日金管證期罰字第…號)」


def _fsc_url(site: str, mcustomize: str, **params) -> tuple[str, dict]:
    _, _, host, list_id, parentpath = _FSC_SITES[site]
    return f"{host}/ch/home.jsp", {"id": list_id, "parentpath": parentpath, "mcustomize": mcustomize, **params}


async def _fsc_list(http, site: str, keyword: str, start: str, end: str, page: int) -> tuple[list[dict], int, bool]:
    agency, category, *_ = _FSC_SITES[site]
    url, params = _fsc_url(site, "multimessages_list.jsp", page=page, pagesize=_FSC_PAGE,
                           keyword=keyword, qptdate=start, qdldate=end)
    r = await http.get(url, params=params)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for row in soup.select("li[role=row], div.whitebackground"):
        a = row.select_one("a[title][href*=dataserno]")
        serial = re.search(r"dataserno=(\d+)", a["href"]) if a else None
        if not serial:
            continue
        unit = _text(row.select_one("span.unit"))  # 金管會總表的「資料來源」（銀行局、保險局…）
        items.append({
            "id": f"fsc_sanction:{site}-{serial.group(1)}",
            "agency": f"{agency}（{unit}）" if unit and unit != agency else agency, "category": category,
            "doc_number": re.sub(r"\s+", "", m.group()) if (m := _FSC_DOC_NO.search(a["title"])) else "",
            "date": _date(_text(row.select_one(".date, .pdate1"))), "summary": a["title"].strip(),
        })
    pages = max(len(soup.select("select[name=page] option")), 1)
    # 官網只給頁數：最後一頁才算得出精確筆數，其餘以滿頁估計
    total = (pages - 1) * _FSC_PAGE + (len(items) if page >= pages else _FSC_PAGE)
    return items, total, page < pages


def _fsc_key(item: dict) -> str:
    """同一案件在金管會總表與各局清單的 dataserno 不同：以發文字號（或日期＋標題）去重。"""
    return item["doc_number"] or item["date"] + re.sub(r"\W", "", item["summary"])


async def _fsc_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    start = f"{year_from + 1911}-01-01" if year_from else ""
    end = f"{year_to + 1911}-12-31" if year_to else ""
    kw = doc_number.strip() or keyword.strip()
    results = await asyncio.gather(*(_fsc_list(http, s, kw, start, end, page) for s in _FSC_SITES),
                                   return_exceptions=True)
    seen, items, total, has_more, failed = set(), [], 0, False, []
    for site, res in zip(_FSC_SITES, results):
        if isinstance(res, BaseException):
            failed.append(_FSC_SITES[site][0])
            continue
        rows, n, more = res
        total, has_more = total + n, has_more or more
        for item in rows:
            if _fsc_key(item) not in seen:
                seen.add(_fsc_key(item))
                items.append(item)
    if len(failed) == len(_FSC_SITES):
        raise next(r for r in results if isinstance(r, BaseException))
    notes = ["四個網站分別查詢後合併、去重；總筆數為估計值（官網只提供頁數）",
             "銀行局「非重大裁罰」只公開摘要，非裁處書全文"]
    if failed:
        notes.append(f"{'、'.join(failed)}連線失敗，本次結果未含")
    items.sort(key=lambda i: i["date"], reverse=True)
    extra = {"partial": True} if failed else {}  # 部分網站失敗：上層不快取這次結果
    return _group("金管會", "裁罰案件", total, items, has_more, note="；".join(notes), **extra)


async def _fsc_get(http, native_id: str) -> dict:
    m = _FSC_ID.match(native_id)
    if not m:
        raise LookupError(native_id)
    site, serial = m.groups()
    agency, category, host, *_ = _FSC_SITES[site]
    url, params = _fsc_url(site, "multimessages_view.jsp", dataserno=serial, dtable="Penalty")
    r = await http.get(url, params=params)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    # 各站內容容器不同：金管會 page-edit、證期局 main-a_03、銀行局／保險局 page_content
    box = soup.select_one("div.page-edit") or soup.select_one("div.main-a_03") or soup.select_one("div.page_content")
    when = _text(soup.select_one("div.date, div.contentdate"))
    if box is None or not when:  # 查無此件時是「網址不存在」頁
        raise LookupError(native_id)
    for el in box.select("div.contentdate"):
        el.decompose()
    text = _html_text(str(box))
    number = re.search(r"發文字號[：:]\s*(\S+?號)", text)
    return {
        "agency": agency, "category": category,
        "doc_number": number.group(1) if number else "",
        "date": _date(when), "summary": _text(soup.select_one("div.subject h3") or soup.select_one("h3")),
        "full_text": text,
        "notes": "銀行局「非重大裁罰」只公開摘要，非裁處書全文。" if site == "banking" else "",
        "source_url": str(r.url),
    }


# ─────────────────────────────────────────────────────────────
# 監察院 調查報告／糾正案／彈劾案／糾舉案（www.cy.gov.tw）：ASP.NET 查詢 POST 後 302 到 _Query=GUID
# ─────────────────────────────────────────────────────────────

CY_BASE = "https://www.cy.gov.tw/"
_CY_LISTS = {"133": ("1", "調查報告"), "134": ("2", "糾正案"), "135": ("4", "彈劾案"), "136": ("3", "糾舉案")}
_CY_ID = re.compile(r"^(13[3-6])-(\d{1,8})$")
_cy_state: dict[str, str] | None = None
_cy_state_at = 0.0


async def _cy_hidden_state(http) -> dict[str, str]:
    """查詢表單的 __VIEWSTATE（不綁 session，四個清單通用），快取一小時。"""
    global _cy_state, _cy_state_at
    if _cy_state is None or time.time() - _cy_state_at > 3600:
        r = await http.get(f"{CY_BASE}CyBsBox.aspx?CSN=1&n=133&sms=0", follow_redirects=True)
        r.raise_for_status()
        _cy_state = {k: v for k, v in _hidden(r.text).items() if k.startswith("__")}
        _cy_state_at = time.time()
    return _cy_state


async def _cy_search(n: str, http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    csn, category = _CY_LISTS[n]
    if keyword.strip() or year_from or year_to or doc_number.strip():
        form = {
            **await _cy_hidden_state(http),
            # 審議日期只收民國「114-06-01」（西元 0 筆、斜線 504）
            "MEETINGSDate_1": f"{year_from}-01-01" if year_from else "",
            "MEETINGEDate_1": f"{year_to}-12-31" if year_to else "",
            "searchNo": doc_number.strip(), "caseAttrLv1": "", "unit": "", "keyword": keyword.strip(),
            "ReadData": "", "jNewsModule_BtnSend": "送出查詢",
        }
        r = await http.post(f"{CY_BASE}CyBsBox.aspx?CSN={csn}&n={n}&sms=0", data=form, follow_redirects=True)
        r.raise_for_status()
        if page > 1:
            r = await http.get(f"{r.url}&page={page}&PageSize={PAGE}", follow_redirects=True)
    else:
        r = await http.get(f"{CY_BASE}CyBsBox.aspx?n={n}&CSN={csn}&page={page}&PageSize={PAGE}",
                           follow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    items = []
    for tr in soup.select("table tr"):
        cells = {td.get("data-title"): td for td in tr.select("td[data-title]")}
        title = cells.get("文件案由/案名")
        a = title.select_one("a[href*=CyBsBoxContent]") if title else None
        s = re.search(r"[?&]s=(\d+)", a["href"]) if a else None
        if not s:
            continue
        items.append({
            "id": f"{_CY_KEYS[n]}:{n}-{s.group(1)}", "agency": "監察院", "category": category,
            "doc_number": _text(cells.get("調查案號") or cells.get("案號")),
            "date": _date(_text(cells.get("審議日期"))),
            "summary": re.sub(r"\s*\.{3}詳全文$", "", _text(title)),
        })
    count = re.search(r"\d+", _text(soup.select_one("span.count")))
    total = int(count.group()) if count else (page - 1) * PAGE + len(items)
    return _group(_CY_LABELS[n], category, total, items, page * PAGE < total)


async def _cy_get(http, native_id: str) -> dict:
    m = _CY_ID.match(native_id)
    if not m:
        raise LookupError(native_id)
    n, s = m.groups()
    url = f"{CY_BASE}CyBsBoxContent.aspx?n={n}&s={s}"
    r = await http.get(url, follow_redirects=True)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    # 「案由」的 th/td 沒包在 <tr> 裡，逐一取 th 的下一個 td
    fields = {re.sub(r"[\s：:]", "", _text(th)): th.find_next_sibling("td") for th in soup.select(".area-essay th")}
    if not _text(fields.get("字號")):
        raise LookupError(native_id)
    docs = [{"name": _text(a), "url": a["href"], "type": (a.get("class") or [""])[0]}
            for a in (fields["本案文件"].select("a[href]") if fields.get("本案文件") else [])]
    text = ""
    for doc in docs[:2]:  # 通常是 docx + pdf 同一份；彈劾案 docx 是彈劾案文、pdf 是審查決定書
        f = await http.get(doc["url"], follow_redirects=True)
        if f.status_code != 200:
            continue
        if f.content[:2] == b"PK":
            text = await asyncio.to_thread(_office_text, f.content)
        else:
            text = await asyncio.to_thread(pdf_to_text, f.content)
        if text:
            break
    return {
        "agency": "監察院", "category": _text(fields.get("類別")) or _CY_LISTS[n][1],
        "doc_number": _text(fields["字號"]), "date": _date(_text(fields.get("審議日期"))),
        "summary": _text(fields.get("案由")), "status": _text(fields.get("案件狀態")),
        "full_text": text,
        "notes": "" if text else "全文檔無法擷取文字，請開 documents 內的連結閱讀。",
        "pdf_url": next((d["url"] for d in docs if d["type"] == "pdf"), ""),
        "documents": docs,
        "source_url": url,
    }


# ─────────────────────────────────────────────────────────────
# 律師懲戒委員會／律師懲戒覆審委員會 決議書（lawyerbc.moj.gov.tw JSON API）
# ─────────────────────────────────────────────────────────────

LAWYER_API = "https://lawyerbc.moj.gov.tw/api/cert/lydatalic/"
_LAWYER_HASH = re.compile(r"^[0-9A-Fa-f]{30}$")
_HEADING = re.compile(r"[主事理][\s　]*[文實由]|事[\s　]*實[\s　]*及[\s　]*理[\s　]*由")


def _unwrap_fixed(text: str) -> str:
    """早期決議書是固定寬度排版：半形空白開頭的行是上一段的續行（「主文／事實／理由」標題除外）。"""
    paras: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if paras and line.startswith(" ") and not _HEADING.fullmatch(s) and not _HEADING.fullmatch(paras[-1]):
            paras[-1] += s
        else:
            paras.append(s)
    return "\n".join(paras)


async def _lawyer_post(http, path: str, body: dict):
    r = await http.post(LAWYER_API + path, json=body)
    if r.status_code == 404:
        raise LookupError(body)
    r.raise_for_status()
    data = r.json()
    if data.get("status") != 1:
        raise ValueError(f"律師懲戒查詢失敗：{data.get('message')}")
    return data.get("data")


def _lawyer_item(x: dict) -> dict:
    return {
        "id": f"lawyer_discipline:{x['hashcode']}", "agency": x.get("sendorg") or "律師懲戒委員會",
        "category": "律師懲戒決議", "doc_number": re.sub(r"、?\s*<br>\s*", "、", x.get("caseno") or ""),
        "date": _date(x.get("resdate") or ""),
        "summary": f"被付懲戒人：{x.get('name') or ''}；{(x.get('restitle') or '').strip()}",
    }


async def _lawyer_search(http, keyword: str, year_from: int, year_to: int, doc_number: str, page: int) -> dict:
    kw = doc_number.strip() or keyword.strip()
    if not kw:
        return _group("律師懲戒決議", "律師懲戒決議", 0, [], False,
                      note="此來源需要關鍵字：律師姓名、案號（如「114年度律懲字」「109年度台覆字第19號」）或決議內容")
    data = await _lawyer_post(http, "searchDiscipline", {"keyword": kw})
    rows = data.get("discipline") or []
    lo, hi = year_from or 0, year_to or 999
    rows = [x for x in rows if lo <= int(re.match(r"\d*", x.get("resdate") or "").group() or 0) <= hi]
    notes = []
    if not rows:
        notes.append("查無資料；官網在符合超過 100 筆時也回傳 0 筆，請改用更具體的關鍵字（律師姓名、完整案號）")
    linked = [x for x in rows if x.get("hashcode")]
    if len(linked) < len(rows):
        notes.append(f"另有 {len(rows) - len(linked)} 件 109 年以前的決議官網未提供全文（刊登於行政院公報），未列出")
    items = sorted((_lawyer_item(x) for x in linked), key=lambda i: i["date"], reverse=True)
    page_items, more = _paged(items, page)
    notes.append("決議書全文自 109 年 12 月起公開，較早的案件可能只有主文")
    return _group("律師懲戒決議", "律師懲戒決議", len(items), page_items, more, note="；".join(notes))


async def _lawyer_get(http, hashcode: str) -> dict:
    if not _LAWYER_HASH.match(hashcode):
        raise LookupError(hashcode)
    row = await _lawyer_post(http, "searchDisciplineDetail", {"hash": hashcode})
    if not row or not row.get("hashcode"):
        raise LookupError(hashcode)
    text = _unwrap_fixed(_html_text(row.get("rescontent") or ""))
    if not text:  # 部分舊決議只有 PDF
        blob = await _lawyer_post(http, "searchDisciplinePdf", {"hash": hashcode})
        if isinstance(blob, str) and blob:
            text = await asyncio.to_thread(pdf_to_text, base64.b64decode(blob))
            text = text if len(text) > 50 else ""  # 沒有全文時官網回傳只有幾個亂碼字元的空白 PDF
    item = _lawyer_item(row)
    return {
        "agency": item["agency"], "category": row.get("header") or item["category"],
        "doc_number": item["doc_number"], "date": item["date"], "summary": item["summary"],
        "full_text": text,
        "notes": "" if text else "官網未提供此決議全文，僅有主文。",
        "source_url": f"https://lawyerbc.moj.gov.tw/discipline/{hashcode}",
    }


# ─────────────────────────────────────────────────────────────
# 對外介面：來源代碼 → (名稱, 別名, search, get)，格式同 admin_decisions.SOURCES
# ─────────────────────────────────────────────────────────────

_CY_KEYS = {"133": "cy_report", "134": "cy_correction", "135": "cy_impeachment", "136": "cy_censure"}
_CY_LABELS = {n: f"監察院{category}" for n, (_, category) in _CY_LISTS.items()}

SOURCES = {
    "medical_discipline": ("醫事懲戒決議", ("醫事懲戒", "醫師懲戒", "藥事懲戒"), medical_discipline.search, medical_discipline.get),
    "pcc_complaint": ("工程會採購申訴審議判斷", ("工程會", "採購申訴", "申訴審議判斷", "政府採購"),
                      _pcc_search, _pcc_get),
    "uflb": ("勞動部不當勞動行為裁決", ("不當勞動行為", "裁決", "勞動部裁決", "工會"), _uflb_search, _uflb_get),
    "csptc": ("保訓會復審、再申訴決定", ("保訓會", "復審", "再申訴", "公務人員保障"), _csptc_search, _csptc_get),
    "fsc_sanction": ("金管會裁罰案件", ("金管會", "裁罰", "銀行局", "證期局", "保險局"), _fsc_search, _fsc_get),
    **{
        _CY_KEYS[n]: (_CY_LABELS[n], ("監察院", category.removesuffix("案"), category),
                      partial(_cy_search, n), _cy_get)
        for n, (_, category) in _CY_LISTS.items()
    },
    "lawyer_discipline": ("律師懲戒決議", ("律師懲戒", "懲戒覆審"), _lawyer_search, _lawyer_get),
}
