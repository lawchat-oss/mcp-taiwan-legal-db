"""台灣法律資料庫 MCP Server — MCPServer 入口"""

import asyncio
import logging
import re
from contextlib import asynccontextmanager

from mcp.server.mcpserver import MCPServer

from mcp_server.cache.db import CacheDB
from mcp_server.tools._errors import error_response
from mcp_server.tools.regulations import RegulationClient
from mcp_server.tools.judicial_search import JudicialSearchClient
from mcp_server.tools.judicial_doc import JudgmentDocClient
from mcp_server.tools.waf_bypass import JudicialWAFBypass
from mcp_server.tools.agency_interpretations import AgencyInterpretationClient, _date as parse_date
from mcp_server.tools.fint import PrecedentClient
from mcp_server.tools.admin_decisions import AdminDecisionClient
from mcp_server.tools.legislative import LegislativeHistoryClient
from mcp_server.tools.constitutional_docket import ConstitutionalDocketClient
from mcp_server.tools.legislative_records import BILL_APIS, LegislativeRecordsClient
from mcp_server.tools.statistics import StatisticsClient
from mcp_server.tools.literature import LiteratureClient
from mcp_server.tools.other_regulations import OtherRegulationClient
from mcp_server.tools.sentencing import sentencing_statistics
from mcp_server.tools.fint import USER_AGENT
import httpx
from mcp_server.tools.constitutional_court import (
    get_interpretation as _cc_get_interpretation,
    search_interpretations as _cc_search_interpretations,
    get_citations as _cc_get_citations,
)
from mcp_server.tools.regulations import (
    _PCODE_ALL, _PCODE_REVERSE, _ABOLISHED_SET,
    law_meta_fields,
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
jud_search: JudicialSearchClient | None = None
jud_doc: JudgmentDocClient | None = None
waf: JudicialWAFBypass | None = None
interp: AgencyInterpretationClient | None = None
precedents: PrecedentClient | None = None
decisions: AdminDecisionClient | None = None
legislative: LegislativeHistoryClient | None = None
docket: ConstitutionalDocketClient | None = None
leg_records: LegislativeRecordsClient | None = None
stats: StatisticsClient | None = None
sentencing_http: httpx.AsyncClient | None = None
literature: LiteratureClient | None = None
other_regs: OtherRegulationClient | None = None


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


def _log_background_task_exception(task: asyncio.Task) -> None:
    """background task 的 done callback：cancelled 無聲，例外必留 traceback。"""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Background task %r failed", task.get_name(), exc_info=exc
        )


@asynccontextmanager
async def lifespan(server: MCPServer):
    """伺服器生命週期：啟動時初始化，關閉時清理"""
    global cache, reg_client, jud_search, jud_doc, waf, interp, precedents, decisions, legislative
    global docket, leg_records, stats, sentencing_http, literature, other_regs

    # 啟動
    cache = CacheDB()
    await cache.initialize()
    await cache.cleanup_expired()
    await cache.cleanup_invalid_regulation_names()

    waf = JudicialWAFBypass()
    reg_client = RegulationClient(cache)
    jud_search = JudicialSearchClient(cache, waf)
    jud_doc = JudgmentDocClient(cache, waf)
    interp = AgencyInterpretationClient(cache)
    precedents = PrecedentClient(cache)
    decisions = AdminDecisionClient(cache)
    legislative = LegislativeHistoryClient(cache)
    docket = ConstitutionalDocketClient(cache)
    leg_records = LegislativeRecordsClient(cache)
    stats = StatisticsClient(cache)
    sentencing_http = httpx.AsyncClient(timeout=60.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True)
    literature = LiteratureClient(cache)
    other_regs = OtherRegulationClient(cache)

    logger.info("台灣法律資料庫 MCP Server 已啟動")

    _pcode_task = asyncio.create_task(
        _maybe_update_pcode_all(), name="pcode_all_update"
    )
    _pcode_task.add_done_callback(_log_background_task_exception)

    # WAF cookies 預熱：沒預熱的話，第一個請求會在 search handler 內同步等
    # Playwright warmup，使用者看到的只會是籠統的「搜尋逾時」。
    _waf_task = asyncio.create_task(waf.ensure_ready(), name="waf_warmup")
    _waf_task.add_done_callback(_log_background_task_exception)

    yield

    # 關閉
    await reg_client.close()
    await jud_search.close()
    await jud_doc.close()
    await interp.close()
    await precedents.close()
    await decisions.close()
    await docket.close()
    await leg_records.close()
    await stats.close()
    await sentencing_http.aclose()
    await literature.close()
    await other_regs.close()
    await cache.close()
    logger.info("MCP Server 已關閉")


# 建立 MCPServer 伺服器
mcp = MCPServer(
    name="台灣法律資料庫",
    instructions=(
        "查詢司法院裁判書、全國法規資料庫、大法官解釋（釋字）與憲法法庭裁判（憲判字）、"
        "各機關行政函釋與審查基準、最高法院決議／法律問題座談／判例／精選裁判等判解、訴願決定與準司法機關決定、"
        "立法理由與立法紀錄、憲法法庭卷宗、官方統計與量刑資訊的 MCP 工具。"
        "釋字/憲判字預設層與理由書從本地快取即時回傳，無需連網。"
    ),
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
    keyword 用於主題式全文檢索（如「預售屋 遲延交屋」）。
    要找「哪些判決引用了某裁判或釋字」時才把完整字號放進 keyword（如「108年度台上大字第2680號」「釋字第748號」），
    結果就是全文提到該字號的裁判。

    【資料涵蓋範圍】司法院裁判書系統自民國 89 年（2000）起才接近完整；81–88 年（1992–1999）
    僅零星收錄，80 年（1991）以前查無。查詢早於 89 年的裁判若無結果，應告知使用者是資料源
    不涵蓋，而非該判決不存在。

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
    if max_results <= 0:
        return error_response("max_results 必須大於 0")

    # 硬上限防止 OOM（100 頁 × 20 筆 = 2000 筆，但實務上 200 已足夠）
    max_results = min(max_results, 200)
    logger.info("search_judgments: keyword=%r, court=%r, case_type=%r, "
                "year=%s~%s, case_word=%r, case_number=%r, main_text=%r",
                keyword, court, case_type, year_from, year_to,
                case_word, case_number, main_text)
    result = await jud_search.search(
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
        cited_statutes, cited_cases, full_text, source_url，以及 history（同一案件各審級裁判清單，
        每筆含 desc、jid、url、pending_supreme_court）與 history_note。引用判決前應看 history：
        後面還有上級審裁判時，要確認本判決是否已被廢棄或發回。
    """
    if not jid and not url:
        return error_response("至少需要提供 jid 或 url")

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
    language: str = "",
) -> dict:
    """查詢全國法規資料庫的法規條文。

    可查詢單一條文、條號範圍、或法規全文。回傳的 law 另含 last_amended（最新公布日）、category（主管機關分類），
    有特殊施行日時含 effective_date／effective_note（如「自公布後六個月施行」「施行日期由行政院定之」），
    引用新修正條文前應先看這兩欄確認是否已施行。

    Args:
        law_name: 法規名稱（如「民法」「勞動基準法」），會自動轉換為 pcode
        pcode: 法規代碼（如「B0000001」），若提供 law_name 可不填
        article_no: 條號（如「184」「247-1」「15-1」），查詢單一條文
        from_no: 起始條號（如「184」），查詢條號範圍時使用
        to_no: 截止條號（如「198」），查詢條號範圍時使用
        include_history: 是否包含修法沿革（使用者詢問修法歷程、修正時間、歷次修正內容時設為 True）。
            搭配 article_no 時，會額外回傳該條文「歷次條文全文」(article_history)，
            可直接前後對比同一條在不同時間的條文細節。
        language: 「en」取官方英譯本（約 970 部法律與部分命令；英譯常落後中文修正，note 會提醒版本差異）

    Returns:
        包含法規條文的字典：law (pcode, name, status), articles, source_url,
        history（選填，整部法規的修法沿革文字）,
        article_history（選填，僅在 include_history+article_no 時提供，為該條歷次條文全文）
    """
    from mcp_server.tools.regulations import get_law_history

    # 解析 pcode
    if not pcode and law_name:
        pcode = reg_client.resolve_pcode(law_name)
        if not pcode:
            return error_response(
                f"找不到法規「{law_name}」的代碼（pcode）。"
                f"請使用 get_pcode 工具查詢，或直接提供 pcode。",
                law_name=law_name,
            )

    if not pcode:
        return error_response("須提供 law_name 或 pcode")

    logger.info("query_regulation: law_name=%r, pcode=%r, article_no=%r, range=%s~%s, history=%s, language=%r",
                law_name, pcode, article_no, from_no, to_no, include_history, language)

    if language.strip().lower() in ("en", "english", "英文"):
        return await reg_client.get_english(pcode, article_no, from_no, to_no)

    # 查詢邏輯
    if article_no:
        result = await reg_client.get_article(pcode, article_no)
    elif from_no and to_no:
        result = await reg_client.get_article_range(pcode, from_no, to_no)
    else:
        result = await reg_client.get_all_articles(pcode)

    if result.get("success") and isinstance(result.get("law"), dict):
        result["law"].update(law_meta_fields(pcode))

    # 附加修法沿革
    if include_history and result.get("success"):
        history = get_law_history(pcode)
        if history:
            result["history"] = history
    # 查詢單一條文時，額外附上該條歷次條文全文（跨版本前後對比）。現行查無此條（例如已刪除）時
    # 歷史版本仍可能有，照樣查。不論成功與否都回傳 article_history，讓呼叫端能區分「歷史抓取失敗」
    # （available=False + reason）與「確實無歷史/無此條」。
    if include_history and article_no:
        result["article_history"] = await reg_client.get_article_history(pcode, article_no)

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

    return error_response(
        f"找不到「{law_name}」對應的 pcode",
        suggestions=suggestions[:10],
        available_count=len(_PCODE_ALL),
    )


# ============================================================
# 工具 5：搜尋法規（關鍵字）
# ============================================================

@mcp.tool()
async def search_regulations(
    keyword: str = "",
    offset: int = 0,
    exclude_abolished: bool = False,
    amended_since: str = "",
    category: str = "",
) -> dict:
    """以關鍵字搜尋法規名稱，或列出某日之後新制定／修正公布的法規（法遵追蹤）。

    在完整法規清單（11,700+ 部法律與命令）中搜尋，每頁 50 筆。每筆含 law_name、pcode、status、
    last_amended（最新公布日）、category（主管機關分類，如「行政＞勞動部＞勞動條件及就業平等目」）。
    有 amended_since 時依公布日新到舊排列，否則現行法規優先、依名稱排列。

    Args:
        keyword: 法規名稱關鍵字（如「勞動」「消費」「智慧財產」）；有 amended_since 或 category 時可省略
        offset: 分頁偏移（從第幾筆開始，預設 0）
        exclude_abolished: 排除已廢止法規（預設 False，已廢止法規仍可搜尋但標記狀態）
        amended_since: 只列這天以後（含）公布的法規，如「2026-09-01」「115-09-01」
        category: 主管機關或分類關鍵字（如「金融監督管理委員會」「勞動部」「稅務」），比對 category 欄

    Returns:
        符合條件的法規列表
    """
    if not (keyword or amended_since or category):
        return error_response("請提供 keyword、amended_since 或 category")
    if offset < 0:
        return error_response("offset 不可為負數")
    since = ""
    if amended_since:
        since = parse_date(amended_since)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
            return error_response("amended_since 格式應為「2026-09-01」或「115-09-01」")

    logger.info("search_regulations: keyword=%r, offset=%d, exclude_abolished=%s, amended_since=%r, category=%r",
                keyword, offset, exclude_abolished, amended_since, category)
    matches = []
    for name, pcode in _PCODE_ALL.items():
        meta = law_meta_fields(pcode)
        if keyword and keyword not in name:
            continue
        if exclude_abolished and pcode in _ABOLISHED_SET:
            continue
        if since and meta.get("last_amended", "") < since:
            continue
        if category and category not in meta.get("category", ""):
            continue
        matches.append({
            "law_name": name,
            "pcode": pcode,
            "status": "已廢止" if pcode in _ABOLISHED_SET else "現行法規",
            **{k: meta[k] for k in ("last_amended", "category") if k in meta},
        })

    if since:
        matches.sort(key=lambda m: (m.get("last_amended", ""), m["law_name"]), reverse=True)
    else:
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
# 工具 6：大法官解釋 / 憲法法庭裁判
# ============================================================

@mcp.tool()
def get_interpretation(
    case_id: str,
    include_reasoning: bool = False,
    reasoning_keyword: str = "",
    include_opinions: bool = False,
    opinions_keyword: str = "",
    opinion_document: str = "",
    opinions_offset: int = 0,
) -> dict:
    """取得司法院大法官解釋（釋字第 1-813 號）或憲法法庭裁判（憲判字）全文。

    預設層（字號/日期/爭點/解釋文）從本地快取即時回傳，無需連網。
    理由書/意見書支援全文模式與關鍵字片段模式。

    case_id 格式（自動解析）：「釋字第748號」「釋字748」「748」
    「111年憲判字第1號」「111憲判1」

    Args:
        case_id: 解釋/裁判字號字串
        include_reasoning: 回傳理由書全文（最多 15000 字）
        reasoning_keyword: 在理由書中搜尋關鍵字並回片段（覆蓋 include_reasoning）
        include_opinions: 回傳意見書全文
        opinions_keyword: 在意見書中搜尋關鍵字並回片段
        opinion_document: 只取標題含此字串的意見書全文（例如大法官姓名「許宗力」）；
            意見書合計過長被截斷時用。回傳的 opinion_documents 列出每份的標題、官網 PDF 連結與字數
        opinions_offset: 意見書全文從第幾字開始回傳；單份超過 15000 字被截斷時，
            以相同參數加上回傳的 opinions_next_offset 續讀後段
    """
    return _cc_get_interpretation(
        case_id, include_reasoning, reasoning_keyword,
        include_opinions, opinions_keyword, opinion_document, opinions_offset,
    )


# ============================================================
# 工具 7：搜尋大法官解釋 / 憲判字
# ============================================================

@mcp.tool()
def search_interpretations(
    keyword: str = "",
    year: int = 0,
    number_from: int = 0,
    number_to: int = 0,
    include_old: bool = True,
    include_new: bool = True,
    max_results: int = 30,
) -> dict:
    """列舉大法官解釋 / 憲法法庭裁判。支援關鍵字全文搜尋（搜爭點 + 理由書）。

    每筆結果帶 case_id，可直接傳給 get_interpretation()。

    Args:
        keyword: 關鍵字（標題/字號/爭點/理由書全文匹配）
        year: 篩選民國年度（0=不篩選，>0 只回新制憲判字）
        number_from: 起始號次（含），0=不篩選
        number_to: 截止號次（含），0=不篩選
        include_old: 包含舊制釋字（year=0 時才生效）
        include_new: 包含新制憲判字
        max_results: 回傳筆數上限（預設 30）
    """
    return _cc_search_interpretations(
        keyword, year, number_from, number_to,
        include_old, include_new, max_results,
    )


# ============================================================
# 工具 8：大法官解釋引用關係
# ============================================================

@mcp.tool()
def get_citations(
    case_id: str,
    include_context: bool = False,
    direction: str = "cites",
) -> dict:
    """大法官解釋／憲判字之間的引用關係。

    direction="cites"（預設）：從理由書抽出這件引用了哪些釋字／憲判字（往前追溯）。
    direction="cited_by"：列出後來哪些釋字／憲判字的主文或理由書引用了這件（往後追溯）。
    要找引用某件的法院判決，改用 search_judgments，keyword 填完整字號（如「釋字第748號」）。

    Args:
        case_id: 解釋/裁判字號字串（格式同 get_interpretation）
        include_context: 每個引用附上原文前後 80 字片段
        direction: "cites" 或 "cited_by"
    """
    return _cc_get_citations(case_id, include_context, direction)


# ============================================================
# 工具 9：搜尋行政機關函釋
# ============================================================

@mcp.tool()
async def search_agency_interpretations(
    keyword: str = "",
    agency: str = "",
    year_from: int = 0,
    year_to: int = 0,
    doc_number: str = "",
    page: int = 1,
) -> dict:
    """搜尋各機關的行政函釋（解釋令、函釋、法規諮詢意見）與審查基準，即時查詢各機關官方系統。

    來源：法務部（行政函釋、法規諮詢意見）、勞動部（行政函釋、解釋令）、衛生福利部、
    財政部（各稅法令彙編、新頒令釋；主管法規系統另含關務署、國有財產署、國庫署的核釋令）、
    經濟部（本部解釋令、商業發展署公司法等函釋、智慧財產局著作權函釋與專利商標審查基準、標準檢驗局解釋函令）、
    工程會（政府採購法令）、金管會、環境部、交通部、中央銀行、教育部、農業部、文化部、國科會、原民會、海委會、公平會、
    陸委會、中選會、行政院人事行政總處（公務員人事法令）、主計總處、行政院消費者保護處（消保法函釋）、
    內政部（戶政司、國土管理署、地政司、消防署及部本部）、考試院系統（銓敘部、保訓會、考選部）、
    監察院陽光法令主題網（政治獻金法、利益衝突迴避法、財產申報法的主管機關函釋）、
    臺北市政府、新北市政府（兩者都另收中央機關函釋）、司法院法學資料檢索系統（跨機關函釋），
    以及行政院公報（其他機關依行政程序法第 159 條發布的解釋性規定）。
    外交部、退輔會、核安會、國發會的行政規則多為內部作業要點，只在 agency 指名時查。
    部分機關（金管會、教育部等）的函釋放在「行政規則」類別，結果會混有一般行政規則。
    不指定 agency 時查上述指名才查以外的全部來源；同一件函釋在多個來源出現時只保留一筆。

    結果依發文日期新到舊排列，每筆含 id、agency、category、doc_number（發文字號）、date、summary（要旨或主旨）。
    要讀全文請把 id 傳給 get_agency_interpretation。categories 列出每個來源/類別的總筆數與是否還有下一頁；
    某來源連線失敗時該類別帶 error，其他來源照常回傳。

    效力標示（status）是官網對該筆資料的標示，引用前必看：
    - 「停止適用」：官網標示已停止適用或廢止；status_note 附停止日期、依據的函或原標示
    - 「部分停止適用」：交通部的標示
    - 「適用中」：只在官網有「現行／停止適用」兩態欄位的來源出現（勞動部、衛福部、考試院系統、環境部、地政司、
      各部會主管法規共用系統的行政規則），表示官網標為現行；財政部法令彙編的函釋也標「適用中」（經重新研審保留適用，
      彙編後才廢止的不另標示）
    - 沒有 status：官網沒有標示或沒標示，不代表仍然有效（戶政司、消防署、智慧局、行政院公報等官網完全沒有效力欄位）。
      官網偶有漏標，引用前請讀全文、留意 notes（編註）與後續函釋

    Args:
        keyword: 關鍵字（全文檢索；多個詞以空白分隔）。查特定法條時可用「勞動基準法第24條」這類寫法。
            智慧局審查基準只比對章名（如「專利要件」「混淆誤認」）
        agency: 機關名稱，可用逗號分隔多個，例如「勞動部」「財政部,經濟部」「銓敘部」「地政司」「臺北市」「智慧局」。
            沒有專屬系統的機關（如 NCC、數位發展部）改查行政院公報中該機關發布的解釋性規定
        year_from: 起始年度（民國年，如 110）
        year_to: 截止年度（民國年，如 114）
        doc_number: 發文字號或其號碼（如「法律字第11403512580號」或「11403512580」）
        page: 頁數（每個來源各自分頁：多數每頁 20 筆；衛福部、各部會主管法規共用系統、考試院系統、中央銀行、環境部、
            行政院公報每頁 10 筆；交通部每頁 25 筆）
    """
    if page < 1:
        return error_response("page 必須 >= 1")
    if not (keyword.strip() or doc_number.strip() or agency.strip() or year_from or year_to):
        return error_response("請至少提供 keyword、doc_number、agency 或年度範圍其中一項")
    logger.info("search_agency_interpretations: keyword=%r agency=%r year=%s~%s doc_number=%r page=%d",
                keyword, agency, year_from, year_to, doc_number, page)
    return await interp.search(keyword, agency, year_from, year_to, doc_number, page)


# ============================================================
# 工具 10：取得函釋全文
# ============================================================

@mcp.tool()
async def get_agency_interpretation(interpretation_id: str) -> dict:
    """取得單一行政函釋全文（主旨、說明；正本、副本受文者清單省略）。

    Args:
        interpretation_id: search_agency_interpretations 回傳的 id（如「moj:FE393340」「mol:e:勞動條 3:1100130312」）

    Returns:
        agency, doc_number, date, summary, full_text, related_laws（相關法條）, notes（編註）,
        status／status_note（官網的效力標示，見 search_agency_interpretations；沒有 status 表示官網沒標示）,
        attachments, source_url
    """
    return await interp.get(interpretation_id.strip())


# ============================================================
# 工具 11：搜尋判解（決議、座談、判例、司法院解釋、大法庭）
# ============================================================

@mcp.tool()
async def search_precedents(
    keyword: str = "",
    category: str = "",
    year_from: int = 0,
    year_to: int = 0,
    page: int = 1,
) -> dict:
    """搜尋司法院法學資料檢索系統的判解資料（裁判書系統 search_judgments 查不到的類別）。

    類別：
    - 決議：最高法院民刑事庭會議決議、最高行政法院聯席會議決議（108 年大法庭制度施行前）
    - 法律問題座談：各級法院法律座談會、公證法律問題研討、懲戒法律問題座談
    - 停止適用判例：依法院組織法第 57 條之 1 停止適用、無裁判全文可查的判例（僅存判例要旨）
    - 司法解釋：大理院解釋、最高法院解釋、司法院院字／院解字解釋
    - 大法庭：最高法院、最高行政法院大法庭裁定（含不同意見書附件）
    - 精選裁判：司法院編輯、附「裁判要旨」的各級法院裁判（最高法院、最高行政法院、高等法院、地方法院、
      智慧財產及商業法院、懲戒法院）；結果的 reference_value=true 表示該院選為「具參考價值」或「足資討論」的裁判
    - 具參考價值裁判：只查上述 reference_value=true 的裁判

    引用決議、判例時請留意編註（例如「不再援用」「停止適用」）；get_precedent 會回傳編註。
    官網標「廢」（已廢止或不再援用）的項目帶 status=「停止適用」，status_note 是官網的說明。
    每類每頁 20 筆，站方每類最多提供前 500 筆，筆數過多時請加關鍵字或年度縮小範圍。

    Args:
        keyword: 關鍵字（全文檢索）
        category: 類別，可用逗號分隔多個；不填 = 決議、法律問題座談、停止適用判例、司法解釋、大法庭、精選裁判
        year_from: 起始年度（民國年）
        year_to: 截止年度（民國年）
        page: 頁數
    """
    if page < 1:
        return error_response("page 必須 >= 1")
    if not (keyword.strip() or year_from or year_to):
        return error_response("請提供 keyword 或年度範圍")
    logger.info("search_precedents: keyword=%r category=%r year=%s~%s page=%d",
                keyword, category, year_from, year_to, page)
    return await precedents.search(keyword.strip(), category, year_from, year_to, page)


# ============================================================
# 工具 12：取得判解全文
# ============================================================

@mcp.tool()
async def get_precedent(precedent_id: str) -> dict:
    """取得 search_precedents 結果的全文。

    Args:
        precedent_id: search_precedents 回傳的 id（如「D:A,20040316,001」「Q:A,20251119,013」「C:C,3829」）

    Returns:
        category, fields（字號、日期、決議／要旨、編註、資料來源等原站欄位）, full_text, related_laws,
        attachments, source_url
    """
    return await precedents.get(precedent_id.strip())


# ============================================================
# 工具 13：搜尋訴願決定、公平會處分書
# ============================================================

@mcp.tool()
async def search_administrative_decisions(
    keyword: str = "",
    source: str = "",
    year_from: int = 0,
    year_to: int = 0,
    doc_number: str = "",
    page: int = 1,
) -> dict:
    """搜尋訴願決定與準司法機關的決定、處分（即時查詢各機關官方網站）。

    不指定 source 時查：
    - 行政院訴願決定（近 10 年；108 年以前收辦的案件官網未遮蔽訴願人姓名，暫不列出）
    - 公平交易委員會處分書及不處分決議書（約 5,800 件；關鍵字中的空白會被當成詞組的一部分）
    - 勞動部不當勞動行為裁決（搜尋結果沒有日期，讀全文才有；較舊案件請加關鍵字縮小）
    - 保訓會復審、再申訴決定（不含年金改革案件）
    - 金管會裁罰案件（金管會、銀行局、證期局、保險局合併；總數為估計）
    要在 source 指定才查：
    - 「工程會」或「採購申訴」：採購申訴審議判斷（官方沒有關鍵字檢索：用 doc_number 案號如「訴1130123」或年度查，
      keyword 只篩選當頁、total 是整段期間的件數；內文只公開判斷理由）
    - 「監察院」：調查報告、糾正案、彈劾案、糾舉案（也可只指定其中一類；官網回應慢，單次可能數十秒）
    - 「律師懲戒」：律師懲戒、懲戒覆審決議（需姓名或案號這類精確關鍵字，符合超過 100 筆時官方回 0 筆）
    - 各部會與地方政府訴願決定：機關名稱如「臺北市」「新北市」「臺中市」「高雄市」「國防部」「交通部」「法務部」
      「金管會」「退輔會」等，或「訴願」查全部（含行政院）。部分網站只能比對標題、只給頁數，差異見各來源的 note。
      經濟部、農業部、原民會、教育部的官網未遮蔽姓名，勞動部、財政部、內政部、衛福部、臺南市的查詢需要驗證碼，皆未收錄

    每筆含 id、agency、category、date、summary（案由）；要讀全文請把 id 傳給 get_administrative_decision。
    categories 列出各來源的總筆數，某來源連線失敗時帶 error，其他來源照常回傳。

    Args:
        keyword: 關鍵字（全文檢索；部分來源只比對標題）
        source: 來源或機關名稱，可用逗號分隔多個（如「訴願」「公平會」「監察院」「臺北市,新北市」）；不填 = 上述預設來源
        year_from: 起始年度（民國年）
        year_to: 截止年度（民國年）
        doc_number: 案號或字號（如行政院「A-115-000633」、公平會「公處字第115060號」、工程會「訴1130123」、
            裁決「114年勞裁字第56號」）
        page: 頁數（各來源各自分頁）
    """
    if page < 1:
        return error_response("page 必須 >= 1")
    if not (keyword.strip() or doc_number.strip() or year_from or year_to):
        return error_response("請至少提供 keyword、doc_number 或年度範圍其中一項")
    logger.info("search_administrative_decisions: keyword=%r source=%r year=%s~%s doc_number=%r page=%d",
                keyword, source, year_from, year_to, doc_number, page)
    return await decisions.search(keyword.strip(), source, year_from, year_to, doc_number, page)


# ============================================================
# 工具 14：取得訴願決定、處分書全文
# ============================================================

@mcp.tool()
async def get_administrative_decision(decision_id: str) -> dict:
    """取得訴願決定書、處分書、審議判斷、裁決、保障決定、裁罰案件、監察院案文或律師懲戒決議的全文
    （由官網 HTML 或 PDF 擷取；掃描檔無法擷取時回傳 PDF 連結）。

    Args:
        decision_id: search_administrative_decisions 回傳的 id（如「ey:A-115-000633」）
    """
    return await decisions.get(decision_id.strip())


# ============================================================
# 工具 15：立法理由
# ============================================================

@mcp.tool()
async def get_legislative_history(law_name: str, article_no: str) -> dict:
    """取得某一條文歷次制定、修正時的條文與立法理由（立法院法律系統，民國 59 年以後的修正才有理由）。

    與 query_regulation(include_history=True) 的差別：這裡回傳立法院審議時的「理由」，
    適合回答「這條為什麼這樣規定」「當初修法的目的」。

    Args:
        law_name: 法規名稱（如「民法」「勞動基準法」「刑法」）；簡稱會先轉成全國法規資料庫的正式名稱
        article_no: 條號（如「184」「15-1」）

    Returns:
        law, article, versions（舊到新，每版含 date、action（制定／修正／增訂…）、text、reason）, source_url，
        以及 latest_amendment_process（整部法律最近一次修正的一讀、委員會審查、二讀、三讀日期與公報頁次；
        gazette_pdf_id 傳給 get_legislative_record 可讀該次會議紀錄，找立法者原意）
    """
    name = law_name.strip()
    if name not in ("民法", "中華民國民法"):
        pcode = reg_client.resolve_pcode(name)
        name = _PCODE_REVERSE.get(pcode, name) if pcode else name
    logger.info("get_legislative_history: law=%r → %r article=%r", law_name, name, article_no)
    return await legislative.get(name, article_no)


# ============================================================
# 憲法法庭卷宗與案件進度
# ============================================================

@mcp.tool()
async def search_constitutional_docket(keyword: str = "", status: str = "pending") -> dict:
    """列出憲法法庭尚未判決的案件（get_interpretation 只有已公布的裁判）。

    status：
    - pending：已受理、審理中的案件（受理日期、聲請人（人民以甲乙丙代稱）、案號、主案／併案、案由）
    - hearing：已排定或已舉行言詞辯論、說明會的案件
    - amicus：目前公開徵求法庭之友意見的案件

    結果的 id 傳給 get_constitutional_case_file 可看該案公開的書狀。清單在本機快取一天。

    Args:
        keyword: 篩選關鍵字（比對案號、聲請人、案由；多個詞以空白分隔）
        status: pending、hearing 或 amicus
    """
    logger.info("search_constitutional_docket: keyword=%r status=%r", keyword, status)
    return await docket.search(keyword.strip(), status.strip() or "pending")


@mcp.tool()
async def get_constitutional_case_file(case_id: str = "", document_id: str = "", keyword: str = "") -> dict:
    """憲法法庭卷內文書：聲請書、答辯書、關係機關意見、專家諮詢與鑑定意見、法庭之友意見書、言詞辯論筆錄、
    爭點題綱、大法官意見書、確定終局裁判連結等（裁判本文與意見書全文另見 get_interpretation）。

    用法：
    1. 只給 case_id：列出該案全部公開文件（每筆含 id、group、title、url）、announcements（言詞辯論公告等）與案件欄位
       （原分案號、併案、聲請人、案由…）。早期釋字沒有 PDF，聲請書全文在 petition_text。
    2. case_id + keyword：只列出內容含全部關鍵字的文件並附片段（例如找哪些法庭之友意見書談到「人性尊嚴」）；
       比對的是官方擷取的無標點文字，限憲判字與受理中案件。
    3. document_id：讀單一文件全文（PDF 擷取；掃描檔的 OCR 可能有錯字）。news:… 是公告（含爭點題綱）。

    Args:
        case_id: 「113年憲判字第8號」「釋字第748號」、受理中案號「114年度憲立字第3號」，
            或 search_constitutional_docket 回傳的 id（docket:…、hearing:…、amicus:…）
        document_id: 文件 id（數字）或 news:…；給了就只讀這份文件
        keyword: 在卷內文書中找含這些詞的文件（空白分隔）
    """
    logger.info("get_constitutional_case_file: case_id=%r document_id=%r keyword=%r", case_id, document_id, keyword)
    if document_id.strip():
        return await docket.document(document_id.strip())
    if not case_id.strip():
        return error_response("請提供 case_id 或 document_id")
    return await docket.case_file(case_id, keyword)


# ============================================================
# 立法資料：議案、立法院公報、法規命令草案預告
# ============================================================

@mcp.tool()
async def search_legislative_records(
    keyword: str,
    kind: str = "bills",
    status: str = "pending",
    term: int = 0,
    page: int = 1,
) -> dict:
    """搜尋立法動態與立法紀錄。

    kind：
    - bills：立法院議案（法律案草案、修正草案）。status=pending 審查中（預設只看本屆，屆期不連續）、
      all 全部、passed 已三讀。每筆含提案人、提案日期、會期、進度與關係文書 PDF（含條文對照表）
    - gazette：立法院公報（院會、委員會、公聽會紀錄，含委員與官員發言）；全文檢索，matches 是命中片段。
      查立法者原意時可用「法律名稱＋條次」，例如「勞動基準法第五十五條」
    - drafts：行政院公報刊登的法規命令訂定、修正草案預告（各部會的辦法、細則草案，含陳述意見截止日期）

    結果的 id 傳給 get_legislative_record 取得全文。每頁 20 筆（drafts 10 筆）。

    Args:
        keyword: 關鍵字（法律名稱、條次、議題）
        kind: bills、gazette 或 drafts
        status: kind=bills 時使用：pending、all 或 passed
        term: 立法院屆別（如 11）；0 = bills 審查中只看本屆、其他不限
        page: 頁數
    """
    if not keyword.strip():
        return error_response("請提供 keyword")
    if kind not in ("bills", "gazette", "drafts"):
        return error_response("kind 只能是 bills、gazette 或 drafts")
    if kind == "bills" and status not in BILL_APIS:
        return error_response("status 只能是 pending、all 或 passed")
    if page < 1:
        return error_response("page 必須 >= 1")
    logger.info("search_legislative_records: keyword=%r kind=%s status=%s term=%s page=%d",
                keyword, kind, status, term, page)
    return await leg_records.search(keyword.strip(), kind, status, term, page)


@mcp.tool()
async def get_legislative_record(record_id: str) -> dict:
    """取得立法紀錄全文：議案（bill:…，含提案人、審議進度與關係文書內容）、立法院公報（gazette:…）、
    法規命令草案預告（draft:…，含陳述意見截止日期與草案總說明、條文對照表），
    或 get_legislative_history 立法歷程列出的公報頁（lispdf:…）。全文超過 60,000 字會截斷。

    Args:
        record_id: search_legislative_records 或 get_legislative_history 回傳的 id
    """
    logger.info("get_legislative_record: %s", record_id)
    return await leg_records.get(record_id.strip())


# ============================================================
# 官方統計與量刑
# ============================================================

@mcp.tool()
async def search_statistics(keyword: str = "", source: str = "", year: int = 0, page: int = 1) -> dict:
    """搜尋官方法律統計表與報告。

    來源：司法院司法統計年報、司法統計月報（各級法院各類案件收結、終結情形、上訴、發回更審等統計表）、
    法務部法務統計常用統計表（偵查、起訴、定罪、執行、矯正等）、法務部司法官學院《犯罪狀況及其分析》年度報告。
    結果的 id 傳給 get_statistics 取得表格內容（以「|」分欄的文字）或報告全文。

    Args:
        keyword: 表名或報告關鍵字，比對標題（如「收結」「上訴」「民事」「有罪」「詐欺」）
        source: 來源（司法統計、月報、法務統計、犯罪狀況）；不填 = 全部
        year: 民國年（司法統計年報、月報用；不填 = 最新一年）
        page: 頁數
    """
    if page < 1:
        return error_response("page 必須 >= 1")
    logger.info("search_statistics: keyword=%r source=%r year=%s page=%d", keyword, source, year, page)
    return await stats.search(keyword.strip(), source, year, page)


@mcp.tool()
async def get_statistics(statistics_id: str) -> dict:
    """取得統計表內容或統計報告全文（search_statistics 回傳的 id）。

    Args:
        statistics_id: 例如「judicial:267552-…」「moj:INF_COMMON_P/807」「cprc:45180」「cprc:45180/20215121」
    """
    logger.info("get_statistics: %s", statistics_id)
    return await stats.get(statistics_id.strip())


@mcp.tool()
async def get_sentencing_statistics(
    crime: str = "",
    law: str = "",
    court: str = "",
    factors: str = "",
    year_from: int = 0,
    year_to: int = 0,
) -> dict:
    """司法院事實型量刑資訊系統的刑度統計（符合條件的判決數、各刑種平均／最高／最低與分布）。

    涵蓋 10 類案件：殺人、強盜搶奪、傷害、不能安全駕駛、肇事逃逸、詐欺、竊盜、毒品、槍砲、妨害性自主。
    這是過去判決的統計，不是量刑基準。

    用法：不給 crime 先列出罪名與法院；給 crime 後回傳可選的法條（law_options）、量刑因子（factor_options）
    與目前條件的統計，再依需要加上 law、court、factors 縮小範圍。

    Args:
        crime: 罪名（如「竊盜」「詐欺」，或系統代碼 stole、fraud…）
        law: 法條選項，可用逗號分隔多個（如「第320條第1項」）
        court: 法院，可用逗號分隔多個（如「臺北地院」）
        factors: 量刑因子，格式「因子=選項」，多個以分號分隔（如「累犯=是；坦承犯行=是」）
        year_from: 起始年度（民國年）
        year_to: 截止年度（民國年）
    """
    def split(s: str) -> list[str]:
        return [x.strip() for x in re.split(r"[,，、]", s) if x.strip()]

    factor_map: dict[str, list[str]] = {}
    for part in re.split(r"[;；]", factors):
        name, sep, value = part.partition("=")
        if name.strip():
            if not sep or not value.strip():
                return error_response("factors 格式應為「因子=選項；因子=選項」")
            factor_map.setdefault(name.strip(), []).extend(split(value))
    logger.info("get_sentencing_statistics: crime=%r law=%r court=%r factors=%r year=%s~%s",
                crime, law, court, factors, year_from, year_to)
    try:
        data = await sentencing_statistics(sentencing_http, crime, split(law), split(court), factor_map,
                                           year_from, year_to)
    except ValueError as e:
        return error_response(str(e))
    except httpx.HTTPError as e:
        return error_response(f"量刑資訊系統連線失敗：{type(e).__name__}: {e}")
    return {"success": True, **data}


# ============================================================
# 法學研究文獻
# ============================================================

@mcp.tool()
async def search_legal_literature(
    keyword: str,
    source: str = "",
    year_from: int = 0,
    year_to: int = 0,
    page: int = 1,
) -> dict:
    """搜尋法學研究文獻（只用官方與開放取用來源，不含月旦、華藝、法源等付費資料庫）。

    來源：
    - 司法院專題研究報告（含司法研究年報；法官的實務研究，全文按章分檔）
    - 國家圖書館臺灣期刊論文索引（各法學期刊論文的書目與摘要；作者授權者有全文）
    - 政府研究資訊系統 GRB（國科會與各部會補助的研究計畫摘要；報告全文需在官網下載）
    - 開放取用法學期刊：中研院法學期刊、政大法學評論（全文取自期刊官網）
    結果的 id 傳給 get_legal_literature 取得摘要與全文。引用時請附作者、篇名、刊名卷期與年份。

    Args:
        keyword: 關鍵字（題名、作者、主題，多個詞以空白分隔）
        source: 來源（司法研究年報、期刊、GRB、開放期刊或期刊名稱），可用逗號分隔；不填 = 全部
        year_from: 起始年度（民國年）
        year_to: 截止年度（民國年）
        page: 頁數
    """
    if not keyword.strip():
        return error_response("請提供 keyword")
    if page < 1:
        return error_response("page 必須 >= 1")
    logger.info("search_legal_literature: keyword=%r source=%r year=%s~%s page=%d",
                keyword, source, year_from, year_to, page)
    return await literature.search(keyword.strip(), source, year_from, year_to, page)


@mcp.tool()
async def get_legal_literature(literature_id: str) -> dict:
    """取得研究文獻的書目、摘要與全文（有公開全文時；超過 60,000 字會截斷）。

    國家圖書館授權的全文只供個人查閱，請勿轉存或散布。

    Args:
        literature_id: search_legal_literature 回傳的 id（如「ncl:A15001353」「grb:13540821」）
    """
    logger.info("get_legal_literature: %s", literature_id)
    return await literature.get(literature_id.strip())


# ============================================================
# 全國法規資料庫以外的規範：地方自治法規、條約協定、交易所規章
# ============================================================

@mcp.tool()
async def search_other_regulations(keyword: str, source: str = "", page: int = 1) -> dict:
    """搜尋全國法規資料庫法律命令清單以外的規範（query_regulation 查不到的）。

    - 地方自治法規（自治條例、自治規則、委辦規則）：臺北市、新北市、桃園市、臺中市、臺南市、高雄市、基隆市、
      新竹縣市、苗栗縣、彰化縣、南投縣、嘉義縣市、屏東縣、宜蘭縣、花蓮縣、臺東縣、澎湖縣、金門縣、連江縣
      （只收現行法規；雲林縣官網有 Cloudflare 驗證，無法連線）
    - 條約及協定：全國法規資料庫的條約（只比對名稱）、外交部條約協定資料庫（可加國家，如「日本 所得稅」；
      部分舊約是掃描檔只有 PDF 連結）、財政部租稅協定（避免雙重課稅協定，名稱多寫「所得稅」）
    - 交易所規章：臺灣證券交易所、證券櫃檯買賣中心、臺灣期貨交易所（櫃買、期交所規章取自證基會法規系統，
      僅供查閱、不得轉載）
    建議指定 source：不填會同時查全部 27 個來源。結果的 id 傳給 get_other_regulation 取得條文。

    Args:
        keyword: 關鍵字（法規名稱或內容）。多數來源把整串當成一個詞，請一次給一個詞，例如「違章建築」；
            條約可用「國家 主題」，例如「日本 所得稅」
        source: 縣市名（如「臺北市」「高雄」「新竹」）、「地方法規」「條約」「租稅協定」「外交部」
            「交易所規章」「證交所」「櫃買中心」「期交所」，可用逗號分隔；不填 = 全部
        page: 頁數
    """
    if not keyword.strip():
        return error_response("請提供 keyword")
    if page < 1:
        return error_response("page 必須 >= 1")
    logger.info("search_other_regulations: keyword=%r source=%r page=%d", keyword, source, page)
    return await other_regs.search(keyword.strip(), source, page)


@mcp.tool()
async def get_other_regulation(regulation_id: str, article_no: str = "") -> dict:
    """取得地方自治法規、條約協定或交易所規章的全文或單一條文。

    分條的規範回傳 articles（每條含 number、content）；要點、條約等未分條的回傳 full_text。

    Args:
        regulation_id: search_other_regulations 回傳的 id
        article_no: 只取某一條（如「15」「15-1」「第十五條之一」）；不填 = 全文
    """
    logger.info("get_other_regulation: %s article=%r", regulation_id, article_no)
    return await other_regs.get(regulation_id.strip(), article_no.strip())


# ============================================================
# 啟動入口
# ============================================================

if __name__ == "__main__":
    mcp.run()
