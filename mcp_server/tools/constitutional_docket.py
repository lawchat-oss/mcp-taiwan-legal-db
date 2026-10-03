"""憲法法庭卷宗與案件進度（cons.judicial.gov.tw）

get_interpretation 回傳的是裁判本身；研究者另外需要卷內文書：聲請書、答辯書、專家諮詢／鑑定意見、
法庭之友意見書、言詞辯論筆錄與爭點題綱，以及尚未判決的受理案件。這些都在官網公開，查詢時即時取得。

憲判字與受理中案件頁面內嵌一段 JSON（textarea#jsonLabel），列出全部附件與官方擷取的文字（標點已去除，
只適合關鍵字比對）；舊制釋字與言詞辯論頁沒有 JSON，附件從頁面欄位讀。附件全文一律下載 PDF 擷取。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from mcp_server.cache.db import CacheDB
from mcp_server.tools import constitutional_court as cc
from mcp_server.tools._errors import error_response
from mcp_server.tools.fint import USER_AGENT
from mcp_server.tools.pdf_text import pdf_to_text

logger = logging.getLogger(__name__)

BASE = "https://cons.judicial.gov.tw"
MAX_TEXT = 60000
_PAGE_TTL = 86400  # 卷宗頁 6–8 MB，同一案一天內只抓一次

# 憲判字頁 JSON 的附件分組；openAtt 的名稱以頁面上的標題為準，這裡只是後備
_GROUPS = {
    "openAtt1": "聲請書、補充聲請書", "openAtt2": "答辯書、補充答辯書", "openAtt3": "關係機關意見書",
    "openAtt4": "專家諮詢意見書", "openAtt5": "法庭之友意見書", "openAtt6": "鑑定人意見書", "openAtt7": "其他卷內文書",
    "meetAtt1": "言詞辯論進行流程表", "rulingAtt1": "大法官就主文所採立場表", "rulingAtt3": "裁判全文",
    "rulingAtt4": "判決摘要", "rulingAtt5": "裁判相關文件", "resultAtt2": "大法官意見書",
    "resultAtt3": "確定終局裁判", "resultAtt5": "相關法令",
}
_MEETING_RECORDS = {"1": "言詞辯論筆錄", "2": "說明會紀錄", "3": "準備程序筆錄"}
# 案件清單：status → (fid, 額外參數, 欄位名稱)
_DOCKETS = {
    "pending": ("52", {"type": "1"}, ("項次", "受理日期", "聲請人", "案號", "主案／併案", "案由")),
    "hearing": ("2204", {}, ("項次", "日期", "類型", "案號", "案由")),
    "scheduled": ("2203", {}, ("項次", "日期", "類型", "案號", "案由")),
    "amicus": ("5504", {}, ("項次", "聲請人", "案號", "主案／併案", "案由")),
}
_ID_PREFIX = {"52": "docket", "2204": "hearing", "2203": "hearing", "5504": "amicus"}
_PREFIX_FID = {"docket": "52", "hearing": "2204", "amicus": "5504", "news": "77"}
_DOCKET_NO = re.compile(r"\d{2,3}年度憲\S{1,3}字第\d+號")
# 欄位太長（主文、理由）或已由其他工具提供的，不放進 fields
_SKIP_FIELDS = {"主文", "理由", "解釋文", "理由書", "意見書", "意見書、抄本等文件", "書狀", "判決全文"}


def _clean(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _json_documents(soup: BeautifulSoup) -> tuple[list[dict], list[dict]]:
    """憲判字／受理案件頁：(附件清單, 公告清單)。附件含官方擷取文字 _txt（只供關鍵字比對，不回傳）。"""
    box = soup.find("textarea", id="jsonLabel")
    if box is None:
        return [], []
    data = json.loads(box.get_text() or "{}")
    headings = {}
    for div in soup.select("div.file_list"):
        m = re.search(r"open_att(\d+)$", div.get("id", ""))
        strong = div.find("strong", recursive=False)
        if m and strong:
            headings[f"openAtt{m.group(1)}"] = strong.get_text(strip=True)
    docs = []
    for a in data.get("atts") or []:
        group = a.get("doc_att_group", "")
        label = (_MEETING_RECORDS.get(str(a.get("doc_att_category")), "會議紀錄") if group == "meetAtt2"
                 else headings.get(group) or _GROUPS.get(group, group))
        content = a.get("doc_att_content") or ""
        external = content.startswith("http")  # 確定終局裁判、相關法令是外部連結，不是附件
        docs.append({
            "id": "" if external else str(a.get("doc_att_id", "")),
            "group": label,
            "title": a.get("doc_att_title", ""),
            "url": content if external else f"{BASE}/download/download.aspx?id={a.get('doc_att_id')}",
            "_txt": a.get("doc_att_txt") or "",
        })
    news = [{"id": f"news:{n['doc_id']}", "title": n.get("doc_title", "")} for n in data.get("news") or []]
    return docs, news


def _html_documents(soup: BeautifulSoup) -> list[dict]:
    """舊制釋字、言詞辯論頁：欄位標題就是分組。"""
    docs, seen = [], set()
    for ul in soup.find_all("ul"):
        title_li = ul.find("li", class_="title", recursive=False)
        text_li = ul.find("li", class_="text", recursive=False)
        if not title_li or not text_li:
            continue
        group = title_li.get_text(strip=True)
        for a in text_li.select("a[href*='download.aspx?id=']"):
            m = re.search(r"download\.aspx\?id=(\d+)", a["href"])
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            docs.append({"id": m.group(1), "group": group, "title": a.get_text(" ", strip=True),
                         "url": f"{BASE}/download/download.aspx?id={m.group(1)}", "_txt": ""})
    return docs


def _snippets(txt: str, words: list[str], width: int = 60) -> list[str]:
    out = []
    for w in words:
        i = txt.find(w)
        if i >= 0:
            out.append(txt[max(0, i - width): i + len(w) + width])
    return out


def _docket_rows(html: str, fid: str, names: tuple[str, ...]) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for ul in soup.select("div.caseProcessTb ul.tcont"):
        cells = [d.get_text(" ", strip=True) for d in ul.select("div.cont")]
        link = ul.select_one(f"a[href*='fid={fid}&id=']")
        m = re.search(r"[?&]id=(\d+)", link["href"]) if link else None
        if not m or len(cells) < len(names):
            continue
        row = {k: v for k, v in zip(names, cells) if k != "項次"}
        rows.append({"id": f"{_ID_PREFIX[fid]}:{m.group(1)}", **row})
    return rows


def _last_page(html: str) -> int:
    m = re.search(r"hl_paging_last[^>]*href=\"[^\"]*page=(\d+)|href=\"[^\"]*page=(\d+)\"[^>]*hl_paging_last", html)
    return int(m.group(1) or m.group(2)) if m else 1


class ConstitutionalDocketClient:
    def __init__(self, cache: CacheDB):
        self.cache = cache
        self.http = httpx.AsyncClient(timeout=90.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True)

    async def close(self):
        await self.http.aclose()

    async def _page(self, fid: str, **params) -> str:
        r = await self.http.get(f"{BASE}/docdata.aspx", params={"fid": fid, **params})
        r.raise_for_status()
        return r.text

    async def _docket(self, status: str) -> list[dict]:
        cache_params = {"tool": "constitutional_docket", "status": status}
        cached = await self.cache.get_search(cache_params)
        if cached is not None:
            return cached["rows"]
        fid, extra, names = _DOCKETS[status]
        first = await self._page(fid, **extra)
        pages = [first] + list(await asyncio.gather(
            *(self._page(fid, **extra, page=str(p)) for p in range(2, min(_last_page(first), 20) + 1))
        ))
        rows = [r for html in pages for r in _docket_rows(html, fid, names)]
        await self.cache.set_search(cache_params, {"rows": rows}, ttl=_PAGE_TTL)
        return rows

    async def search(self, keyword: str, status: str) -> dict:
        statuses = ["scheduled", "hearing"] if status == "hearing" else [status]
        if any(s not in _DOCKETS for s in statuses):
            return error_response("status 只能是 pending（受理中）、hearing（言詞辯論／說明會）或 amicus（徵求法庭之友意見）")
        try:
            rows = [r for s in statuses for r in await self._docket(s)]
        except httpx.HTTPError as e:
            return error_response(f"憲法法庭網站連線失敗：{type(e).__name__}: {e}")
        words = keyword.split()
        hits = [r for r in rows if all(any(w in str(v) for v in r.values()) for w in words)]
        return {"success": True, "status": status, "keyword": keyword, "total_count": len(hits), "results": hits,
                "timestamp": datetime.now().isoformat()}

    async def _resolve(self, case_id: str) -> tuple[str, str] | dict:
        """case_id → (fid, 頁面 id)；失敗回傳錯誤 dict。"""
        prefix, _, native = case_id.partition(":")
        if prefix in _PREFIX_FID and native.isdigit():
            return _PREFIX_FID[prefix], native
        docket_no = _DOCKET_NO.search(case_id.replace(" ", ""))
        if docket_no:
            for status in ("pending", "hearing", "scheduled", "amicus"):
                hit = next((r for r in await self._docket(status) if r.get("案號") == docket_no.group()), None)
                if hit:
                    p, _, n = hit["id"].partition(":")
                    return _PREFIX_FID[p], n
            return error_response(f"受理中、言詞辯論與法庭之友清單都找不到「{docket_no.group()}」；"
                                  "已判決的案件請改用憲判字字號查詢")
        try:
            system, number, year = cc._parse_case_id(case_id)
        except ValueError as e:
            return error_response(str(e))
        if system == "釋字":
            page_id = (await asyncio.to_thread(cc._load_old_listing)).get(number)
            return ("100", page_id) if page_id else error_response(f"官網查無釋字第{number}號")
        page_id = (await asyncio.to_thread(cc._load_new_listing)).get((year, number))
        return ("38", page_id) if page_id else error_response(f"官網查無{year}年憲判字第{number}號")

    async def _case(self, fid: str, page_id: str) -> dict:
        cache_params = {"tool": "constitutional_case_file", "fid": fid, "id": page_id}
        cached = await self.cache.get_search(cache_params)
        if cached is not None:
            return cached
        html = await self._page(fid, id=page_id)
        soup = BeautifulSoup(html, "html.parser")
        fields = cc._parse_doc_page(html)
        docs, news = _json_documents(soup)
        if not docs:
            docs = _html_documents(soup)
        data = {
            "fields": {k: v for k, v in fields.items() if v and k not in _SKIP_FIELDS and len(v) <= 3000},
            "petition_text": fields.get("相關文件", ""),  # 早期釋字沒有 PDF，聲請書全文直接寫在頁面
            "documents": docs, "announcements": news,
            "source_url": f"{BASE}/docdata.aspx?fid={fid}&id={page_id}",
        }
        await self.cache.set_search(cache_params, data, ttl=_PAGE_TTL)
        return data

    async def case_file(self, case_id: str, keyword: str = "") -> dict:
        try:
            target = await self._resolve(case_id.strip())
            if isinstance(target, dict):
                return target
            data = await self._case(*target)
        except httpx.HTTPError as e:
            return error_response(f"憲法法庭網站連線失敗：{type(e).__name__}: {e}")
        words = [w for w in re.sub(r"[^\w\s]", " ", keyword).split() if w]
        docs = []
        for d in data["documents"]:
            if words and not all(w in d["_txt"] for w in words):
                continue
            doc = {k: v for k, v in d.items() if k != "_txt"}
            if words:
                doc["matches"] = _snippets(d["_txt"], words)
            docs.append(doc)
        result = {
            "success": True, "case_id": case_id, "fields": data["fields"],
            "documents": docs, "document_count": len(docs), "announcements": data["announcements"],
            "source_url": data["source_url"],
            "note": "documents 的 id 傳給 document_id 可讀全文（PDF 擷取，掃描檔的 OCR 文字可能有錯字；"
                    "法庭之友意見書官方只公開前 20 頁）。announcements 的 id（news:…）可讀言詞辯論公告與爭點題綱。",
        }
        if words:
            result["note"] += "關鍵字比對的是官方擷取的文字（無標點），只列出含全部關鍵字的文件。"
        if data["petition_text"]:
            result["petition_text"] = data["petition_text"][:MAX_TEXT]
        return result

    async def document(self, document_id: str) -> dict:
        prefix, _, native = document_id.partition(":")
        if prefix == "news" and native.isdigit():
            try:
                html = await self._page("77", id=native)
            except httpx.HTTPError as e:
                return error_response(f"憲法法庭網站連線失敗：{type(e).__name__}: {e}")
            article = BeautifulSoup(html, "html.parser").select_one("#Print_area .article")
            if article is None:
                return error_response(f"查無公告：{document_id}")
            return {"success": True, "document_id": document_id, "full_text": _clean(article.get_text("\n", strip=True)),
                    "source_url": f"{BASE}/docdata.aspx?fid=77&id={native}"}
        if not document_id.isdigit():
            return error_response("document_id 應為 get_constitutional_case_file 回傳的文件 id（數字）或 news:…")
        url = f"{BASE}/download/download.aspx?id={document_id}"
        cached = await self.cache.get_judgment(f"consdoc:{document_id}")
        if cached:
            return {"success": True, "cached": True, **cached}
        try:
            r = await self.http.get(url)
            r.raise_for_status()
        except httpx.HTTPError as e:
            return error_response(f"憲法法庭網站連線失敗：{type(e).__name__}: {e}")
        text = await asyncio.to_thread(pdf_to_text, r.content)
        if not text:
            return {"success": True, "document_id": document_id, "full_text": "", "pdf_url": url,
                    "note": "這份文件無法擷取文字（不是 PDF 或為純影像），請開 pdf_url 閱讀。"}
        data = {"document_id": document_id, "full_text": text[:MAX_TEXT], "full_text_truncated": len(text) > MAX_TEXT,
                "pdf_url": url}
        await self.cache.set_judgment(f"consdoc:{document_id}", data, source="constitutional_docket")
        return {"success": True, "cached": False, **data}
