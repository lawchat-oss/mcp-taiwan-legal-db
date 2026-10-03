"""立法理由：立法院法律系統（lis.ly.gov.tw）的「法條沿革」，每一條歷次修正的條文與理由（民國 59 年起）

站方是有狀態的舊式系統：每次查詢先取得帶 session 的表單、以法規名稱檢索、進入該法、再開「法條沿革」頁，
共 4 個請求；網址每個 session 不同，不能保存。伺服器需要舊式 TLS 重新協商。
"""

from __future__ import annotations

import re
import ssl

import httpx
from bs4 import BeautifulSoup

from mcp_server.tools import fint
from mcp_server.tools._errors import error_response

BASE = "https://lis.ly.gov.tw"

# 民法在立法院系統是一編一部法律
_CIVIL_BOOKS = [
    (152, "民法第一編總則"), (756, "民法第二編債"), (966, "民法第三編物權"),
    (1137, "民法第四編親屬"), (1225, "民法第五編繼承"),
]
_DIGITS = "零一二三四五六七八九"


def chinese_number(n: int) -> str:
    """184 → 一百八十四；10 → 十；1001 → 一千零一（條號用，0 < n < 10000）。"""
    out, zero = "", False
    for value, unit in ((1000, "千"), (100, "百"), (10, "十"), (1, "")):
        d = n // value % 10
        if d:
            out += ("零" if zero and out else "") + _DIGITS[d] + unit
            zero = False
        elif out:
            zero = True
    return out[1:] if out.startswith("一十") else out


def article_label(article_no: str) -> str:
    """「184」「15-1」→「第一百八十四條」「第十五條之一」。"""
    main, _, sub = article_no.strip().partition("-")
    label = f"第{chinese_number(int(main))}條"
    return label + (f"之{chinese_number(int(sub))}" if sub else "")


def _law_title(law_name: str, article_no: str) -> str:
    if law_name in ("民法", "中華民國民法"):
        n = int(article_no.partition("-")[0])
        return next((title for last, title in _CIVIL_BOOKS if n <= last), _CIVIL_BOOKS[-1][1])
    return law_name


def _client() -> httpx.AsyncClient:
    ctx = ssl.create_default_context()
    ctx.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)  # 不設會 UNSAFE_LEGACY_RENEGOTIATION_DISABLED
    return httpx.AsyncClient(
        timeout=60.0, headers={"User-Agent": fint.USER_AGENT}, follow_redirects=True, verify=ctx
    )


def parse_history(html: str) -> dict[str, list[dict]]:
    """法條沿革頁 → {條號標籤: [{date, action, text, reason}, ...]}（舊到新）。"""
    out: dict[str, list[dict]] = {}
    for cell in BeautifulSoup(html, "html.parser").select("td.row0, td.row1"):
        label = cell.select_one("font.artino")
        if not label:
            continue
        versions: list[dict] = []
        for el in cell.select("font.upddate, td.artiupd_TH_2, td.artiupd_RS_2"):
            if el.name == "font":
                stamp, *action = el.get_text(strip=True).strip("()（）").split()  # 中間是 nbsp
                versions.append({"date": stamp, "action": " ".join(action), "text": "", "reason": ""})
            elif versions:
                key = "text" if "artiupd_TH_2" in el.get("class", []) else "reason"
                versions[-1][key] = el.get_text("\n", strip=True)
        out[re.sub(r"\s", "", label.get_text())] = versions  # 站方寫成「第三十七條 之一」
    return out


def parse_process(html: str) -> dict:
    """「立法歷程」頁（法律最近一次修正的一讀、委員會審查、二讀、三讀）→ {summary, steps}。"""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    summary = " ".join(
        f"{k}{m.group(1)}" for k in ("三讀日期：", "審查委員會：", "公布日期：")
        if (m := re.search(re.escape(k) + r"\s*(\S+)", text))
    )
    steps, seen = [], set()
    for tr in soup.select("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) != 5:
            continue
        cells = tuple(td.get_text(" ", strip=True) for td in tds)
        if not re.fullmatch(r"\d{7}", cells[1]) or cells in seen:  # 頁面上同一張表出現兩次
            continue
        seen.add(cells)
        pdf = tds[2].select_one("a[href*='lypdftxt']")
        steps.append({
            "stage": cells[0], "date": _roc7(cells[1]), "gazette": cells[2],
            "proposer": cells[3], "document": cells[4].strip("()（）"),
            "gazette_pdf_id": "lispdf:" + pdf["href"].split("xdd!", 1)[1] if pdf and "xdd!" in pdf["href"] else "",
        })
    return {"summary": summary, "steps": steps}


def _roc7(stamp: str) -> str:
    """「1051206」→ 2016-12-06。"""
    m = re.fullmatch(r"(\d{3})(\d{2})(\d{2})", stamp)
    return f"{int(m.group(1)) + 1911:04d}-{m.group(2)}-{m.group(3)}" if m else stamp


async def fetch_article_history(law_name: str, article_no: str) -> dict:
    title = _law_title(law_name, article_no)
    label = article_label(article_no)
    async with _client() as http:
        form_page = await http.get(f"{BASE}/lglawc/lglawkm")
        form_page.raise_for_status()
        form = BeautifulSoup(form_page.text, "html.parser").select_one("form[name=KM]")
        if form is None:
            raise ValueError("立法院法律系統查詢頁格式不符")
        data = {
            i["name"]: i.get("value", "") for i in form.select("input[type=hidden]")
            if i.get("name") and not i["name"].startswith("@R")
        }
        data.update({"_1_6_T": title, "_IMG_檢索.x": "1", "_IMG_檢索.y": "1"})
        found = await http.post(BASE + form["action"], data=data)
        found.raise_for_status()
        links = {
            a.get_text(strip=True): a["href"]
            for a in BeautifulSoup(found.text, "html.parser").select("a[href^='/lglawc/lawsingle']")
        }
        if title not in links:  # 檢索是部分比對（「民法」也會找到「入出國及移民法」），只接受完全同名
            raise LookupError(title)
        landing = await http.get(BASE + links[title])
        landing.raise_for_status()
        history_link = next(
            (a["href"] for a in BeautifulSoup(landing.text, "html.parser").select("a[href*=lawsingle]")
             if a.get_text(strip=True) == "法條沿革"),
            None,
        )
        if history_link is None:
            raise ValueError(f"{title} 沒有法條沿革頁")
        history = await http.get(BASE + history_link)
        history.raise_for_status()
        # 「立法歷程」是頁面上沒有文字的圖示連結，網址固定含 …0000000000000001E…
        process_link = next(
            (a["href"] for a in BeautifulSoup(landing.text, "html.parser").select("a[href*=lawsingle]")
             if re.search(r"0{16}01E", a["href"])),
            None,
        )
        process = None
        if process_link:
            try:
                page = await http.get(BASE + process_link)
                page.raise_for_status()
                process = parse_process(page.text)
            except httpx.HTTPError:
                process = None
    articles = parse_history(history.text)
    if label not in articles:
        raise KeyError(label)
    result = {
        "law": title,
        "article": label,
        "versions": [{**v, "date": _roc7(v["date"])} for v in articles[label]],
        "source_url": f"{BASE}/lglawc/lglawkm",
    }
    if process and process["steps"]:
        result["latest_amendment_process"] = {
            **process,
            "note": "這是整部法律最近一次修正的立法歷程，不一定修到本條（本條各版本日期見 versions）。"
                    "gazette_pdf_id 傳給 get_legislative_record 可讀該次會議的公報紀錄（委員會審查、院會發言）。",
        }
    return result


class LegislativeHistoryClient:
    def __init__(self, cache):
        self.cache = cache

    async def get(self, law_name: str, article_no: str) -> dict:
        if not re.fullmatch(r"\d{1,4}(-\d{1,2})?", article_no.strip()):
            return error_response("article_no 格式應為「184」或「15-1」", article_no=article_no)
        cache_key = f"lis:{law_name}:{article_no.strip()}"
        cached = await self.cache.get_judgment(cache_key)
        if cached:
            return {"success": True, "cached": True, **cached}
        try:
            data = await fetch_article_history(law_name, article_no.strip())
        except LookupError as e:
            if isinstance(e, KeyError):
                return error_response(f"立法院法律系統的「{law_name}」沒有{e.args[0]}的沿革（條號不存在或已刪除）")
            return error_response(
                f"立法院法律系統找不到名稱完全相同的法律「{e.args[0]}」；請用全稱（可先以 get_pcode 查正式名稱）"
            )
        except (httpx.HTTPError, ValueError, ssl.SSLError) as e:
            return error_response(f"立法院法律系統連線或解析失敗：{type(e).__name__}: {e}")
        await self.cache.set_judgment(cache_key, data, source="legislative_history")
        return {"success": True, "cached": False, **data}
