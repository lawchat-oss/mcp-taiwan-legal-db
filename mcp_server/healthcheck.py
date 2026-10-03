"""官方來源健康檢查：每個來源實際查一次、再取第一筆全文，列出改版或連不上的來源。

單元測試用 MockTransport，看不出官網改版；發版前、或有人回報某來源查不到東西時跑這支：

    python -m mcp_server.healthcheck                          # 全部
    python -m mcp_server.healthcheck interpretations          # 只查函釋
    python -m mcp_server.healthcheck interpretations mof mol  # 只查指定來源

每個來源只發兩、三個請求，用暫存快取（不讀也不寫使用者的快取）。有任何來源 FAIL / EMPTY 時 exit code 為 1。
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

import httpx

from mcp_server.cache.db import CacheDB
from mcp_server.tools import admin_decisions, agency_interpretations, literature, other_regulations, statistics
from mcp_server.tools.admin_decisions import AdminDecisionClient
from mcp_server.tools.agency_interpretations import AgencyInterpretationClient
from mcp_server.tools.constitutional_docket import ConstitutionalDocketClient
from mcp_server.tools.fint import USER_AGENT, PrecedentClient
from mcp_server.tools.judicial_doc import JudgmentDocClient
from mcp_server.tools.judicial_search import JudicialSearchClient
from mcp_server.tools.legislative import LegislativeHistoryClient
from mcp_server.tools.legislative_records import LegislativeRecordsClient
from mcp_server.tools.literature import LiteratureClient
from mcp_server.tools.other_regulations import OtherRegulationClient
from mcp_server.tools.regulations import RegulationClient
from mcp_server.tools.sentencing import sentencing_statistics
from mcp_server.tools.statistics import StatisticsClient
from mcp_server.tools.waf_bypass import JudicialWAFBypass

# 工具 → (client 類別, 來源表, 以來源代碼查詢的方式, 預設關鍵字)
REGISTRIES = {
    "interpretations": (AgencyInterpretationClient, agency_interpretations.SOURCES,
                        lambda c, src, kw: c.search(kw, src, 0, 0, "", 1), "申請"),
    "decisions": (AdminDecisionClient, admin_decisions.SOURCES,
                  lambda c, src, kw: c.search(kw, src, 0, 0, "", 1), "訴願"),
    "literature": (LiteratureClient, literature.SOURCES, lambda c, src, kw: c.search(kw, src), "契約"),
    "statistics": (StatisticsClient, statistics.SOURCES, lambda c, src, kw: c.search(kw, src), "民事"),
    "other_regulations": (OtherRegulationClient, other_regulations.SOURCES,
                          lambda c, src, kw: c.search(kw, src), "自治條例"),
}

# 預設關鍵字在這些來源查不到東西（只比對標題、或領域不同）
KEYWORDS = {
    ("interpretations", "tipo_guide"): "專利要件",
    ("decisions", "ey"): "罰鍰",
    ("decisions", "pcc_complaint"): "",  # 只能比對爭議類型與條號，空白 = 最近 12 個月
    ("decisions", "cy_impeachment"): "罰鍰",
    ("decisions", "cy_censure"): "處分",
    ("decisions", "yilan"): "府訴",  # 標題只有字號
    ("statistics", "moj"): "起訴",
    ("statistics", "cprc"): "起訴",
    ("other_regulations", "moj_treaty"): "協定",
    ("other_regulations", "mofa"): "協定",
    ("other_regulations", "mof_tax"): "所得稅",
    ("other_regulations", "twse"): "上市",
    ("other_regulations", "tpex"): "上市",
    ("other_regulations", "taifex"): "上市",
}

# 沒標「廢」就說「適用中」的來源：官網把標示改名時，停止適用的會全被當成適用中。用已知停止適用的函釋確認還讀得到
STOPPED = ("fsc:FL049913", "mocs:15251", "mol:e:勞檢一:0930031687", "mohw:衛授疾:1142100020",
           "land:8806998:1999-06-07")

CONCURRENCY = 6
THIN = 40  # 正文最長一段不到這個字數，多半是版面改了、抓到空殼
# 正文所在的欄位；摘要、標題、提示文字不算（正文擷取失敗時它們通常還在）
CONTENT_KEYS = ("full_text", "table_text", "articles", "documents", "abstract")


def _items(result: dict) -> list[dict]:
    return result.get("results") or result.get("items") or []


def _search_problem(result: dict) -> str:
    if not result.get("success", True):
        return result.get("error", "success=false")
    errors = [f"{g.get('source', '')}: {g['error']}" for g in result.get("categories", []) if g.get("error")]
    if errors:
        return "；".join(errors)
    if any(not i.get("id") and not i.get("jid") for i in _items(result)):
        return "版面可能改了：結果缺 id"
    return ""


def _longest(value, key: str = "") -> int:
    """最長一段文字的長度（網址不算）；條文、卷內文書在巢狀的 list / dict 裡。"""
    if isinstance(value, str):
        return 0 if "url" in key else len(value)
    if isinstance(value, dict):
        return max((_longest(v, k) for k, v in value.items()), default=0)
    if isinstance(value, list):
        return max((_longest(v, key) for v in value), default=0)
    return 0


def _body_length(detail: dict) -> int:
    return max((_longest(detail[k], k) for k in CONTENT_KEYS if k in detail), default=0)


async def _check(tool: str, source: str, search, get) -> tuple[str, str, str, str, float]:
    started = time.monotonic()
    try:
        result = await search()
        problem = _search_problem(result)
        if problem:
            return tool, source, "FAIL", problem, time.monotonic() - started
        items = _items(result)
        if not items:
            return tool, source, "EMPTY", "查無結果（關鍵字不適用，或版面改了）", time.monotonic() - started
        if get is None:
            return tool, source, "OK", f"{len(items)} 筆", time.monotonic() - started
        detail = await get(items[0])
        if not detail.get("success", True):
            return tool, source, "FAIL", f"取全文：{detail.get('error', '')}", time.monotonic() - started
        size = _body_length(detail)
        status = "OK" if size >= THIN else "THIN"
        return tool, source, status, f"{len(items)} 筆；全文 {size} 字", time.monotonic() - started
    except Exception as e:  # noqa: BLE001 — 健康檢查要把任何例外都列出來
        return tool, source, "FAIL", f"{type(e).__name__}: {e}", time.monotonic() - started


def _single_checks(cache: CacheDB, clients: list) -> dict[str, tuple]:
    """沒有來源表的工具：各給一組固定查詢。"""
    waf = JudicialWAFBypass()
    jud, doc = JudicialSearchClient(cache, waf), JudgmentDocClient(cache, waf)
    reg, prec, docket = RegulationClient(cache), PrecedentClient(cache), ConstitutionalDocketClient(cache)
    records, history = LegislativeRecordsClient(cache), LegislativeHistoryClient(cache)
    http = httpx.AsyncClient(timeout=60.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    clients += [jud, doc, reg, prec, docket, records, http]

    async def article():
        return {"results": [{"id": "B0000001"}]}

    return {
        "judgments": (lambda: jud.search(keyword="損害賠償", max_results=3), lambda i: doc.get_by_jid(i["jid"])),
        "regulations": (article, lambda i: reg.get_article(i["id"], "184")),
        "precedents": (lambda: prec.search("契約", "", 0, 0, 1), lambda i: prec.get(i["id"])),
        "docket": (lambda: docket.search("", "pending"), lambda i: docket.case_file(i["id"], "")),
        "legislative_bills": (lambda: records.search("勞動基準法", "bills", "all", 0, 1), lambda i: records.get(i["id"])),
        "legislative_gazette": (lambda: records.search("勞動基準法", "gazette", "", 0, 1), lambda i: records.get(i["id"])),
        "drafts": (lambda: records.search("草案", "drafts", "", 0, 1), lambda i: records.get(i["id"])),
        "legislative_history": (lambda: _as_items(history.get("民法", "184")), None),
        "sentencing": (lambda: _as_items(sentencing_statistics(http, "竊盜")), None),
    }


async def _expect_stopped(client: AgencyInterpretationClient, item_id: str) -> dict:
    detail = await client.get(item_id)
    if detail.get("success") and detail.get("status") != "停止適用":
        return {"success": False, "error": f"沒讀到停止適用標示（status={detail.get('status')}），官網標示可能改了"}
    return detail


async def _as_items(coro) -> dict:
    """單筆結果的工具：成功就當作一筆。"""
    result = await coro
    return {**result, "results": [{"id": "-"}]} if result.get("success", True) and "error" not in result else result


async def main(argv: list[str]) -> int:
    tool_filter, source_filter = (argv[0], set(argv[1:])) if argv else ("", set())
    with tempfile.TemporaryDirectory() as tmp:
        cache = CacheDB(db_path=Path(tmp) / "healthcheck.db")
        for snap in (agency_interpretations._NLMA, agency_interpretations._TIPO):  # 不沿用使用者目錄裡的全量清單
            snap.path, snap.rows = Path(tmp) / snap.path.name, None
        await cache.initialize()
        clients: list = []
        checks = []
        for tool, (cls, sources, search, default_kw) in REGISTRIES.items():
            if tool_filter and tool != tool_filter:
                continue
            client = cls(cache)
            clients.append(client)
            for src in sources:
                if source_filter and src not in source_filter:
                    continue
                kw = KEYWORDS.get((tool, src), default_kw)
                checks.append((tool, src, lambda c=client, s=src, k=kw, f=search: f(c, s, k),
                               lambda i, c=client: c.get(i["id"])))
        for name, (search, get) in _single_checks(cache, clients).items():
            if not tool_filter or tool_filter == name:
                checks.append((name, "-", search, get))
        if not tool_filter or tool_filter == "status":
            interp = AgencyInterpretationClient(cache)
            clients.append(interp)
            for item_id in STOPPED:
                async def one(i=item_id):
                    return {"results": [{"id": i}]}
                checks.append(("status", item_id, one, lambda i, c=interp: _expect_stopped(c, i["id"])))

        gate = asyncio.Semaphore(CONCURRENCY)

        async def run(tool, src, search, get):
            async with gate:
                row = await _check(tool, src, search, get)
            print(f"{row[2]:<5} {row[0]}/{row[1]}  {row[3]}  ({row[4]:.1f}s)", flush=True)
            return row

        rows = await asyncio.gather(*(run(*c) for c in checks))
        for client in clients:
            await client.aclose() if isinstance(client, httpx.AsyncClient) else await client.close()
        await cache.close()

    bad = [r for r in rows if r[2] in ("FAIL", "EMPTY")]
    print(f"\n{len(rows)} 項檢查：OK {sum(r[2] == 'OK' for r in rows)}、THIN {sum(r[2] == 'THIN' for r in rows)}、"
          f"EMPTY {sum(r[2] == 'EMPTY' for r in rows)}、FAIL {sum(r[2] == 'FAIL' for r in rows)}")
    for r in sorted(bad):
        print(f"  {r[2]:<5} {r[0]}/{r[1]}  {r[3]}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
