"""台灣法律資料庫 MCP Server — FastMCP 入口"""

import asyncio
import logging
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from mcp_server.cache.db import CacheDB
from mcp_server.tools.regulations import RegulationClient
from mcp_server.tools.judicial_search import JudicialSearchBrowser
from mcp_server.tools.judicial_doc import JudgmentDocClient
from mcp_server.tools.regulations import (
    _PCODE_ALL, _PCODE_REVERSE, _ABOLISHED_SET,
    reload_pcode_all,
)

# 日誌設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("taiwan-legal-mcp")

# 全域資源（lifespan 管理）
cache: CacheDB | None = None
reg_client: RegulationClient | None = None
jud_browser: JudicialSearchBrowser | None = None
jud_doc: JudgmentDocClient | None = None


async def _maybe_update_pcode_all():
    """啟動時 Saturday-aware 檢查（MCP = 本地開發，只做啟動補漏）"""
    try:
        from mcp_server.updater import update_pcode_all, should_update_saturday
        should, reason = should_update_saturday()
        if not should:
            logger.info("pcode_all.json %s", reason)
            return
        logger.info("pcode_all.json %s，觸發更新", reason)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, update_pcode_all)
        reload_pcode_all()
        logger.info("pcode_all.json 更新完成")
    except Exception as e:
        logger.warning("pcode_all.json 更新失敗: %s", e)


@asynccontextmanager
async def lifespan(server: FastMCP):
    """伺服器生命週期：啟動時初始化，關閉時清理"""
    global cache, reg_client, jud_browser, jud_doc

    # 啟動
    cache = CacheDB()
    await cache.initialize()
    await cache.cleanup_expired()
    await cache.cleanup_invalid_regulation_names()

    reg_client = RegulationClient(cache)
    jud_browser = JudicialSearchBrowser(cache)
    jud_doc = JudgmentDocClient(cache)

    logger.info("台灣法律資料庫 MCP Server 已啟動")

    # Playwright 預熱 + pcode 更新移到 yield 後的背景 task
    # 避免 12s Playwright 啟動 blocking handshake，導致 Claude Code 連線超時

    async def _warmup():
        try:
            await jud_browser._ensure_browser()
            logger.info("Playwright 背景預熱完成")
        except Exception as e:
            logger.warning("Playwright 背景預熱失敗（首次搜尋時重試）: %s", e)

    _warmup_task = asyncio.create_task(_warmup())
    _warmup_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    _pcode_task = asyncio.create_task(_maybe_update_pcode_all())
    _pcode_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    yield

    # 關閉
    await reg_client.close()
    await jud_browser.close()
    await jud_doc.close()
    await cache.close()
    logger.info("MCP Server 已關閉")


# 建立 FastMCP 伺服器
mcp = FastMCP(
    name="台灣法律資料庫",
    instructions="查詢司法院裁判書、全國法規資料庫的 MCP 工具",
    lifespan=lifespan,
)


# ============================================================
# 工具 1：搜尋裁判書
# ============================================================

@mcp.tool()
async def search_judgments(
    keyword: str = "",
    court: str = "",
    case_type: str = "",
    year_from: int = 0,
    year_to: int = 0,
    case_word: str = "",
    case_number: str = "",
    main_text: str = "",
    max_results: int = 10,
) -> dict:
    """搜尋司法院裁判書系統。

    結果自動按法院權威性排序（最高法院→高等法院→地方法院），同層級按原始排序。
    每筆結果含 court（法院名稱）、case_type（民事/刑事/行政）、court_level（1=最高/2=高等/3=地方）。

    【重要】查特定案號時，必須用 case_word + case_number（精確查詢），不要把案號放在 keyword。
    例如查「114年度上易字第503號」→ case_word="上易", case_number="503", year_from=114。
    keyword 僅用於主題式全文檢索（如「預售屋 遲延交屋」）。

    【進階實務研究欄位】:
    - main_text: 裁判主文關鍵字 — 最有效的輸贏方篩選方式。
      主文措辭高度制度化（依民刑訴訟法條生成），substring match 接近
      解析半結構化欄位，精度高：
        * 「被告應將 移轉」→ 被告敗訴（物權移轉類）
        * 「被告應給付」→ 被告敗訴（金錢給付類）
        * 「原告之訴駁回」→ 原告敗訴
        * 「上訴駁回」→ 維持原審
    可與 keyword 併用，例：
        找「借名登記成立、被告敗訴」→
        main_text="被告應將 移轉", keyword="借名登記", case_type="民事"

    Args:
        keyword: 全文檢索關鍵字（對應 jud_kw）
        court: 法院名稱（如「最高法院」「臺灣高等法院」「臺灣臺北地方法院」）
        case_type: 案件類型（民事/刑事/行政/懲戒）
        year_from: 起始年度（民國年，如 110）
        year_to: 截止年度（民國年，如 113）
        case_word: 字別（如「台上」「上易」「重訴」），查特定案號時必填
        case_number: 案號（數字），查特定案號時必填
        main_text: 裁判主文關鍵字（對應 jud_jmain）— 結構化篩選輸贏方
        max_results: 回傳筆數上限（預設 10，上限 200）

    Returns:
        包含搜尋結果的字典：success, query, total_count, results, cached, timestamp
    """
    # 硬上限防止 OOM（100 頁 × 20 筆 = 2000 筆，但實務上 200 已足夠）
    max_results = min(max_results, 200)
    logger.info("search_judgments: keyword=%r, court=%r, case_type=%r, "
                "year=%s~%s, case_word=%r, case_number=%r, main_text=%r",
                keyword, court, case_type, year_from, year_to,
                case_word, case_number, main_text)
    result = await jud_browser.search(
        keyword=keyword,
        court=court,
        case_type=case_type,
        year_from=year_from,
        year_to=year_to,
        case_word=case_word,
        case_number=case_number,
        main_text=main_text,
        max_results=max_results,
    )
    logger.info("search_judgments 完成: success=%s, count=%s, cached=%s",
                result.get("success"), result.get("total_count", 0), result.get("cached", False))
    return result


# ============================================================
# 工具 2：取得裁判書全文
# ============================================================

@mcp.tool()
async def get_judgment(
    jid: str = "",
    url: str = "",
) -> dict:
    """取得單一裁判書全文。

    支援兩種查詢方式：
    1. 以 JID 查詢（優先使用 Open Data API）
    2. 以 URL 查詢（直接載入頁面）

    Args:
        jid: 裁判書 JID（如「TPSV,104,台上,472,20150326,1」），從搜尋結果取得
        url: 裁判書 URL（如 https://judgment.judicial.gov.tw/FJUD/printData.aspx?id=...）

    Returns:
        包含裁判書全文的字典：case_id, court, date, main_text, facts, reasoning,
        cited_statutes, cited_cases, full_text, source_url
    """
    if not jid and not url:
        return {"success": False, "error": "至少需要提供 jid 或 url"}

    logger.info("get_judgment: jid=%r, url=%r", jid, url[:80] if url else "")
    if jid:
        result = await jud_doc.get_by_jid(jid)
    else:
        result = await jud_doc.get_by_url(url)
    logger.info("get_judgment 完成: success=%s, cached=%s, court=%r",
                result.get("success"), result.get("cached", False), result.get("court", ""))

    return result


# ============================================================
# 工具 3：查詢法規條文
# ============================================================

@mcp.tool()
async def query_regulation(
    law_name: str = "",
    pcode: str = "",
    article_no: str = "",
    from_no: str = "",
    to_no: str = "",
    include_history: bool = False,
) -> dict:
    """查詢全國法規資料庫的法規條文。

    可查詢單一條文、條號範圍、或法規全文。

    Args:
        law_name: 法規名稱（如「民法」「勞動基準法」），會自動轉換為 pcode
        pcode: 法規代碼（如「B0000001」），若提供 law_name 可不填
        article_no: 條號（如「184」「247-1」「15-1」），查詢單一條文
        from_no: 起始條號（如「184」），查詢條號範圍時使用
        to_no: 截止條號（如「198」），查詢條號範圍時使用
        include_history: 是否包含修法沿革（使用者詢問修法歷程、修正時間、歷次修正內容時設為 True）

    Returns:
        包含法規條文的字典：law (pcode, name, status), articles, source_url, history（選填）
    """
    from mcp_server.tools.regulations import get_law_history

    # 解析 pcode
    if not pcode and law_name:
        pcode = reg_client.resolve_pcode(law_name)
        if not pcode:
            return {
                "success": False,
                "error": f"找不到法規「{law_name}」的代碼（pcode）。"
                         f"請使用 get_pcode 工具查詢，或直接提供 pcode。",
            }

    if not pcode:
        return {"success": False, "error": "須提供 law_name 或 pcode"}

    logger.info("query_regulation: law_name=%r, pcode=%r, article_no=%r, range=%s~%s, history=%s",
                law_name, pcode, article_no, from_no, to_no, include_history)

    # 查詢邏輯
    if article_no:
        result = await reg_client.get_article(pcode, article_no)
    elif from_no and to_no:
        result = await reg_client.get_article_range(pcode, from_no, to_no)
    else:
        result = await reg_client.get_all_articles(pcode)

    # 附加修法沿革
    if include_history and result.get("success"):
        history = get_law_history(pcode)
        if history:
            result["history"] = history

    return result


# ============================================================
# 工具 4：法規名稱轉 pcode
# ============================================================

@mcp.tool()
async def get_pcode(law_name: str) -> dict:
    """將法規名稱轉換為全國法規資料庫的 pcode 代碼。

    涵蓋 11,700+ 部法規（法律 + 命令），支援模糊比對。

    Args:
        law_name: 法規名稱（如「民法」「勞基法」「消保法」）

    Returns:
        包含 pcode 的字典，或模糊比對建議
    """
    # 精確比對（完整清單 11,747 部）
    if law_name in _PCODE_ALL:
        pcode = _PCODE_ALL[law_name]
        return {
            "success": True,
            "law_name": law_name,
            "pcode": pcode,
            "status": "已廢止" if pcode in _ABOLISHED_SET else "現行法規",
        }

    # 模糊比對
    resolved = reg_client.resolve_pcode(law_name)
    if resolved:
        # 從反查表取得完整名稱
        full_name = _PCODE_REVERSE.get(resolved, law_name)
        return {
            "success": True,
            "law_name": full_name,
            "pcode": resolved,
            "matched_from": law_name,
            "status": "已廢止" if resolved in _ABOLISHED_SET else "現行法規",
        }

    # 回傳相似的選項
    suggestions = [
        name for name in _PCODE_ALL
        if law_name in name or name in law_name
    ]

    return {
        "success": False,
        "error": f"找不到「{law_name}」對應的 pcode",
        "suggestions": suggestions[:10],
        "available_count": len(_PCODE_ALL),
    }


# ============================================================
# 工具 5：搜尋法規（關鍵字）
# ============================================================

@mcp.tool()
async def search_regulations(keyword: str, offset: int = 0, exclude_abolished: bool = False) -> dict:
    """以關鍵字搜尋法規名稱。

    在完整法規清單（11,700+ 部）中搜尋，回傳符合的法規名稱與 pcode。
    結果按現行法規優先排序，每頁 50 筆。

    Args:
        keyword: 搜尋關鍵字（如「勞動」「消費」「智慧財產」）
        offset: 分頁偏移（從第幾筆開始，預設 0）
        exclude_abolished: 排除已廢止法規（預設 False，已廢止法規仍可搜尋但標記狀態）

    Returns:
        符合關鍵字的法規列表
    """
    if not keyword:
        return {"success": False, "error": "請提供搜尋關鍵字"}

    logger.info("search_regulations: keyword=%r, offset=%d, exclude_abolished=%s",
                keyword, offset, exclude_abolished)
    matches = []
    for name, pcode in _PCODE_ALL.items():
        if keyword in name:
            if exclude_abolished and pcode in _ABOLISHED_SET:
                continue
            matches.append({
                "law_name": name,
                "pcode": pcode,
                "status": "已廢止" if pcode in _ABOLISHED_SET else "現行法規",
            })

    # 排序：現行法規優先，再依名稱排序
    matches.sort(key=lambda m: (m["status"] != "現行法規", m["law_name"]))

    page_size = 50
    page = matches[offset:offset + page_size]

    return {
        "success": True,
        "keyword": keyword,
        "total_count": len(matches),
        "offset": offset,
        "has_more": offset + page_size < len(matches),
        "results": page,
    }


# ============================================================
# 啟動入口
# ============================================================

if __name__ == "__main__":
    mcp.run()
