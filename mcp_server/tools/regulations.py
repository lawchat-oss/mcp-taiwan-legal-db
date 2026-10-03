"""全國法規資料庫查詢工具"""

import asyncio
import io
import json
import logging
import re
import unicodedata
import zipfile
from urllib.parse import quote

import httpx
from mcp_server.config import (
    REGULATION_API_BASE,
    REGULATION_SINGLE_URL,
    REGULATION_ALL_URL,
    REGULATION_OLDVERLIST_URL,
    REGULATION_OLDVER_URL,
    PCODE_MAP,
    BUNDLED_DATA_DIR,
    pcode_data_dir,
    validate_url_domain,
)
from mcp_server.cache.db import CacheDB
from mcp_server.parsers.regulation_parser import parse_single_article, parse_law_all, _looks_like_article, INVALID_LAW_NAMES
from mcp_server.tools._errors import error_response
from mcp_server.tools.agency_interpretations import _Snapshot
from mcp_server.updater import _extract_pcode_from_url

logger = logging.getLogger(__name__)

# 載入完整 pcode 清單（11,747 部法規，從 law.moj.gov.tw API 生成）
_PCODE_ALL_PATH = BUNDLED_DATA_DIR / "pcode_all.json"  # 內建版；執行期讀 pcode_data_dir()
_PCODE_ALL: dict[str, str] = {}
_PCODE_REVERSE: dict[str, str] = {}  # pcode → name
_ABOLISHED_SET: set[str] = set()  # 已廢止法規的 pcode 集合
_LAW_HISTORIES: dict[str, str] = {}  # pcode → 修法沿革文字
_LAW_META: dict[str, dict] = {}  # pcode → amended / effective / effective_note / category


def _load_pcode_all():
    """載入完整 pcode 清單 + 修法沿革（啟動時呼叫一次）"""
    global _PCODE_ALL, _PCODE_REVERSE, _ABOLISHED_SET
    if _PCODE_ALL:
        return
    try:
        with open(pcode_data_dir() / "pcode_all.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        _PCODE_ALL = data.get("pcode_map", {})
        _PCODE_REVERSE = {v: k for k, v in _PCODE_ALL.items()}
        _ABOLISHED_SET = set(data.get("abolished_set", []))
        logger.info("載入完整 pcode 清單: %d 部法規（%d 部已廢止）",
                     len(_PCODE_ALL), len(_ABOLISHED_SET))
    except FileNotFoundError:
        logger.warning("pcode_all.json 不存在，使用內建 PCODE_MAP（%d 部）", len(PCODE_MAP))
        _PCODE_ALL.update(PCODE_MAP)
        _PCODE_REVERSE.update({v: k for k, v in PCODE_MAP.items()})
    except Exception as e:
        logger.error("載入 pcode_all.json 失敗: %s", e)
        _PCODE_ALL.update(PCODE_MAP)
        _PCODE_REVERSE.update({v: k for k, v in PCODE_MAP.items()})
    # 載入修法沿革
    _load_law_histories()


def _load_side_file(name: str) -> dict:
    """pcode_all.json 旁的附屬資料：使用者目錄有就用，否則用套件內建版。"""
    path = pcode_data_dir() / name
    if not path.exists():
        path = BUNDLED_DATA_DIR / name
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("%s 不存在，相關功能不可用", name)
    except Exception as e:
        logger.error("載入 %s 失敗: %s", name, e)
    return {}


def _load_law_histories():
    """載入修法沿革與每部法規的公布／施行日期"""
    for target, name in ((_LAW_HISTORIES, "law_histories.json"), (_LAW_META, "law_meta.json")):
        data = _load_side_file(name)
        if data:
            target.clear()
            target.update(data)
    logger.info("載入修法沿革 %d 部、公布日期 %d 部", len(_LAW_HISTORIES), len(_LAW_META))


def reload_pcode_all():
    """原子化重載 pcode_all.json（先載入新資料到 local，再 clear+update）。

    用 clear()+update() 而非重新賦值 global，保持 dict 對象 identity，
    確保其他模組透過 from ... import _PCODE_ALL 取得的 reference 不斷裂。
    空窗從「檔案 I/O + JSON parse」縮短到「記憶體操作」。
    """
    try:
        with open(pcode_data_dir() / "pcode_all.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        new_map = data.get("pcode_map", {})
        if len(new_map) < 10_000:
            logger.warning("reload_pcode_all: 新資料只有 %d 筆 < 10K，拒絕載入", len(new_map))
            return
        new_reverse = {v: k for k, v in new_map.items()}
        new_abolished = set(data.get("abolished_set", []))
        # 最小空窗替換
        _PCODE_ALL.clear()
        _PCODE_ALL.update(new_map)
        _PCODE_REVERSE.clear()
        _PCODE_REVERSE.update(new_reverse)
        _ABOLISHED_SET.clear()
        _ABOLISHED_SET.update(new_abolished)
        logger.info("reload_pcode_all 完成: %d 部法規（%d 部已廢止）",
                     len(_PCODE_ALL), len(_ABOLISHED_SET))
        _load_law_histories()
    except Exception as e:
        logger.error("reload_pcode_all 失敗（保留舊資料）: %s", e)


def get_law_history(pcode: str) -> str | None:
    """查詢法規修法沿革（從 law_histories.json 記憶體查）"""
    return _LAW_HISTORIES.get(pcode)


def law_meta_fields(pcode: str) -> dict:
    """最新公布日、施行日說明、主管機關分類（官方全量資料的欄位）。"""
    meta = _LAW_META.get(pcode, {})
    out = {"last_amended": meta.get("amended", ""), "category": meta.get("category", "")}
    if meta.get("effective"):
        out["effective_date"] = "另定（部分條文施行日期由主管機關另定）" if meta["effective"] == "9999-12-31" else meta["effective"]
    if meta.get("effective_note"):
        out["effective_note"] = meta["effective_note"]
    return {k: v for k, v in out.items() if v}


def _normalize_article_no(no: str) -> str:
    """條號正規化，供比對用：「227之2」「227-2」「 227-2 」→「227-2」"""
    return no.replace("之", "-").replace(" ", "").replace("　", "").strip()


def _format_roc_date(lnndate: str) -> str:
    """西元 YYYYMMDD → 民國日期字串（如 19990421 → 民國88年04月21日）"""
    if not (lnndate and len(lnndate) == 8 and lnndate.isdigit()):
        return lnndate
    year = int(lnndate[:4]) - 1911
    return f"民國{year}年{lnndate[4:6]}月{lnndate[6:8]}日"


# 舊版頁面常見的異體寫法（含 Big5 轉換把「市」轉成「巿」），比對前統一；「︰」須在 NFKC 前換掉，否則會變成「..」
_TEXT_VARIANTS = str.maketrans({"○": "0", "〇": "0", "︰": "：", "巿": "市", "臺": "台"})
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_HISTORY_DATE = re.compile(r"中華民國([零〇一二三四五六七八九十百]+)年([零〇一二三四五六七八九十]+)月([零〇一二三四五六七八九十]+)日")
_HISTORY_ARTICLE_TOKEN = re.compile(r"\d+(-\d+)?([～~至]\d+(-\d+)?)?")


def _cn_int(s: str) -> int:
    """「一百零八」「八十八」「十八」→ 整數（修法沿革日期用，最大到百位）"""
    total = num = 0
    for ch in s:
        if ch == "十":
            total, num = total + (num or 1) * 10, 0
        elif ch == "百":
            total, num = total + num * 100, 0
        else:
            num = _CN_DIGITS[ch]
    return total + num


def _list_tokens(fragment: str) -> set[str]:
    """「第 1、2～5 條」清單的條號。條號被折行或空白隔開時分不出是同一個號碼（「9」換行「85」＝第 985 條）
    還是漏了頓號（民法沿革「993」換行「994」），兩種讀法都收：條號只會多不會少。"""
    return {
        t
        for span in re.findall(r"第\s*([0-9\-～~至、，,及\s]+?)\s*條", fragment)
        for reading in (re.sub(r"\s+", "", span), re.sub(r"\s+", "、", span.strip()))
        for t in re.split(r"[、，,及]", reading) if t
    }


def _parse_history(history: str) -> tuple[dict[str, set[str] | None], dict[str, set[str]]]:
    """修法沿革 → ({公布日 YYYYMMDD: 該次修正列出的條號（含「32～36」範圍）}, {公布日: 其中「增訂」的條號})。

    None 表示該次為全文修正、或沒列條號／無法判讀，視為所有條文都可能變動。
    沿革是 LawOldVerList 版本日期的來源（兩者一一對應），用來略過與目標條文無關的版本。
    """
    changes: dict[str, set[str] | None] = {}
    added: dict[str, set[str]] = {}
    for entry in re.split(r"\r?\n(?=\s*\d+\s*\.)", history):
        text = re.sub(r"\s+", "", entry)
        m = _HISTORY_DATE.search(text)
        if not m:
            continue  # 對不到日期的版本不會被略過（見 _article_changed_in）
        y, mo, d = (_cn_int(g) for g in m.groups())
        date = f"{y + 1911:04d}{mo:02d}{d:02d}"
        spans = re.findall(r"第([0-9\-～~至、，,及]+)條", text)
        tokens = _list_tokens(entry)
        # 只取「增訂」後面那一段的條號（刑法沿革「修正公布第295條條文、並增訂第294-1條條文」中間是頓號不是分號）。
        # 「增訂」本身也可能被折行拆開（行政訴訟法沿革「並增」換行「訂第 194-1 條」），故在去空白的文字上切。
        added.setdefault(date, set()).update(
            t for seg in text.split("增訂")[1:] for t in _list_tokens(re.split(r"[；;]|修正|刪除|訂定", seg)[0]))
        # 清單只要有一段不是「第 1、2～5 條」格式（夾章節名、漏寫「第」、國字條號、「第 5 條之 1」、「第 20 條至第 24 條」），
        # 就無法確定列全了，整筆視為可能動到所有條文
        complete = len(spans) == len(re.findall(r"[0-9]條", text)) and not re.search(r"[一二三四五六七八九十百]條|條之|條[～~至]", text)
        if "全文" in text or not tokens or not complete or not all(_HISTORY_ARTICLE_TOKEN.fullmatch(t) for t in tokens):
            changes[date] = None
        elif changes.get(date, set()) is not None:  # 同日多道公布令：條號合併
            changes.setdefault(date, set()).update(tokens)
    return changes, added


def _history_changes(history: str) -> dict[str, set[str] | None]:
    return _parse_history(history)[0]


def _article_key(no: str) -> tuple[int, int]:
    base, _, sub = _normalize_article_no(no).partition("-")
    return int(base), int(sub or 0)


def _article_changed_in(changes: dict[str, set[str] | None], lnndate: str, article_no: str) -> bool:
    """該版本是否可能改動目標條文；沿革查不到該日期時保守視為可能。"""
    if lnndate not in changes or changes[lnndate] is None:
        return True
    return _tokens_include(changes[lnndate], article_no)


def _tokens_include(tokens: set[str], article_no: str) -> bool:
    try:
        target = _article_key(article_no)
        return any(
            _article_key(lo) <= target <= _article_key(hi or lo)
            for lo, _, hi in (re.sub("[~至]", "～", t).partition("～") for t in tokens)
        )
    except ValueError:
        return True


def _is_retryable_http_error(exc: Exception) -> bool:
    """判斷例外是否值得重試。

    只重試暫時性錯誤：連線/讀取/逾時（TransportError）、429、5xx。
    4xx（如 403/404）為確定性錯誤，重試只會徒增延遲與外部請求量，不重試。
    非 httpx 例外（如解析為空的 ValueError）視為可能暫時性，允許重試。
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or 500 <= code < 600
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPError):
        return False
    return True


def _get_law_status(pcode: str) -> str:
    """根據 pcode 判斷法規狀態"""
    if pcode in _ABOLISHED_SET:
        return "已廢止"
    return "現行法規"


_load_pcode_all()

# 常見法規縮寫 → 正式名稱（使用者和 AI 常用的簡稱）
_LAW_ALIASES: dict[str, str] = {
    "消保法": "消費者保護法",
    "勞基法": "勞動基準法",
    "個資法": "個人資料保護法",
    "國賠法": "國家賠償法",
    "道交條例": "道路交通管理處罰條例",
    "證交法": "證券交易法",
    "公交法": "公平交易法",
    "強執法": "強制執行法",
    "家事法": "家事事件法",
    "少事法": "少年事件處理法",
    "社維法": "社會秩序維護法",
    "行程法": "行政程序法",
    "民訴法": "民事訴訟法",
    "刑訴法": "刑事訴訟法",
    "行訴法": "行政訴訟法",
    "不經條例": "不動產經紀業管理條例",
    "智財法": "智慧財產案件審理法",
    "稅徵法": "稅捐稽徵法",
    "政採法": "政府採購法",
    "遺贈稅法": "遺產及贈與稅法",
    "公寓條例": "公寓大廈管理條例",
    "大廈條例": "公寓大廈管理條例",
    "營業稅法": "加值型及非加值型營業稅法",
    "刑法": "中華民國刑法",
}


EN_LAW_URL = "https://law.moj.gov.tw/api/En/Law/JSON"
EN_ORDER_URL = "https://law.moj.gov.tw/api/En/Order/JSON"
_EN_NOTE = "英譯本僅供參考，法律效力以中文為準。"


def _en_parse(blob: bytes) -> list[dict]:
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name = [n for n in z.namelist() if n.endswith(".json")][0]
            laws = json.loads(z.read(name).decode("utf-8-sig"))["Laws"]
    except (zipfile.BadZipFile, IndexError, KeyError) as e:  # 維護頁、格式變動
        raise ValueError(f"英譯法規檔格式不符：{e}") from e
    return [{
        "id": _extract_pcode_from_url(x["EngLawURL"]),
        "date": x.get("EngLawModifiedDate", ""),
        "name": x.get("EngLawName", ""),
        "url": x["EngLawURL"],
        "articles": [
            {"number": a.get("EngArticleNo", "").replace("Article", "").strip(" .．"), "content": a.get("EngArticleContent", "").replace("\r\n", "\n").strip()}
            for a in x.get("EngLawArticles") or [] if a.get("EngArticleType") == "A"
        ],
    } for x in laws if x.get("EngLawURL")]


# 官方英譯（法律約 970 部、命令另一份），整份下載到使用者資料目錄、每週更新
_EN_LAWS = _Snapshot(EN_LAW_URL, "en_laws.zip", _en_parse, min_rows=500)
_EN_ORDERS = _Snapshot(EN_ORDER_URL, "en_orders.zip", _en_parse, min_rows=500)


class RegulationClient:
    """法規資料庫 HTTP 客戶端"""

    def __init__(self, cache: CacheDB):
        self.cache = cache
        # SSL verification via OS-native trust store (truststore injected
        # at config.py import time). See mcp_server/config.py for the
        # TWCA Global Root CA + OpenSSL 3.6 rationale.
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": "TaiwanLegalMCP/1.0"},
            follow_redirects=True,
        )

    async def close(self):
        await self.client.aclose()

    def _resolve_law_name(self, pcode: str, parsed_name: str = "") -> str:
        """從 pcode 反查法規名稱"""
        # 完整清單反查
        if pcode in _PCODE_REVERSE:
            return _PCODE_REVERSE[pcode]
        # 內建清單反查
        for name, code in PCODE_MAP.items():
            if code == pcode:
                return name
        # 解析器結果 fallback
        if parsed_name and parsed_name not in INVALID_LAW_NAMES:
            return parsed_name
        return ""

    def resolve_pcode(self, name: str) -> str | None:
        """法規名稱 → pcode（完整清單 + 縮寫展開 + 模糊比對）"""
        # 1. 完整清單精確匹配
        if name in _PCODE_ALL:
            return _PCODE_ALL[name]
        # 2. 縮寫展開（如「消保法」→「消費者保護法」）
        if name in _LAW_ALIASES:
            full_name = _LAW_ALIASES[name]
            if full_name in _PCODE_ALL:
                return _PCODE_ALL[full_name]
        # 3. 內建清單精確匹配（fallback）
        if name in PCODE_MAP:
            return PCODE_MAP[name]
        # 4. 模糊比對（長名稱優先，避免「保險法」匹配到「全民健康保險法」）
        candidates = []
        for key, code in _PCODE_ALL.items():
            if name in key or key in name:
                candidates.append((key, code))
        if candidates:
            # 優先選最短的 key（精確度最高），但 key 必須包含查詢名稱
            # 例如查「保險法」→ 精確匹配「保險法」而非「全民健康保險法」
            exact_contains = [(k, c) for k, c in candidates if k == name]
            if exact_contains:
                return exact_contains[0][1]
            # 查詢名稱包含 key（如查「消保法施行細則」→ 匹配「消費者保護法施行細則」）
            name_contains_key = [(k, c) for k, c in candidates if k in name]
            if name_contains_key:
                # 選最長的 key（最精確）
                name_contains_key.sort(key=lambda x: len(x[0]), reverse=True)
                return name_contains_key[0][1]
            # key 包含查詢名稱（如查「勞基法」→ 可能匹配「勞動基準法」）
            key_contains_name = [(k, c) for k, c in candidates if name in k]
            if key_contains_name:
                key_contains_name.sort(key=lambda x: len(x[0]))
                return key_contains_name[0][1]
        return None

    async def get_article(self, pcode: str, article_no: str) -> dict:
        """查詢單一條文"""
        # 快取查詢
        cached = await self.cache.get_regulation(pcode, article_no)
        if cached:
            # 修正舊快取：覆蓋 status + 檢查垃圾內容
            if "law" in cached:
                cached["law"]["status"] = _get_law_status(pcode)
            for art in cached.get("articles", []):
                if not _looks_like_article(art.get("content", "")):
                    # 快取含垃圾內容 → 視為條號不存在
                    law_name = cached.get("law", {}).get("name", pcode)
                    return error_response(
                        f"查無此條號：{law_name} 第 {article_no} 條不存在或已刪除",
                        law=cached.get("law", {"pcode": pcode, "status": _get_law_status(pcode)}),
                    )
            return {"success": True, "cached": True, **cached}

        # HTTP 查詢
        url = f"{REGULATION_SINGLE_URL}?pcode={pcode}&flno={article_no}"
        if not validate_url_domain(url):
            return error_response("域名不在白名單中", url=url)

        try:
            resp = await self.client.get(url)
            resp.raise_for_status()
            parsed = parse_single_article(resp.text)

            # 優先從 PCODE_MAP 反查法規名稱
            law_name = self._resolve_law_name(pcode, parsed.get("law_name", ""))
            status = _get_law_status(pcode)
            article_content = parsed.get("article_content", "")
            logger.info("查詢法規條文: %s 第%s條 (status=%s)", law_name or pcode, article_no, status)

            # 條文內容為空 → 該條號不存在
            if not article_content:
                return error_response(
                    f"查無此條號：{law_name or pcode} 第 {article_no} 條不存在或已刪除",
                    law={"pcode": pcode, "name": law_name, "status": status},
                )

            data = {
                "law": {
                    "pcode": pcode,
                    "name": law_name,
                    "status": status,
                },
                "articles": [{
                    "number": article_no,
                    "content": article_content,
                }],
                "source_url": url,
            }

            # 寫入快取
            await self.cache.set_regulation(pcode, data, article_no)
            return {"success": True, "cached": False, **data}

        except httpx.HTTPError as e:
            logger.warning("查詢條文 HTTP 失敗 (pcode=%s, flno=%s): %s",
                           pcode, article_no, e)
            return error_response(
                "連線全國法規資料庫失敗，請稍後重試",
                law={"pcode": pcode},
            )

    async def get_english(self, pcode: str, article_no: str = "", from_no: str = "", to_no: str = "") -> dict:
        """官方英譯本條文；英譯常落後中文修正，兩者日期不同時在 note 提醒。"""
        law = {"pcode": pcode, "name": _PCODE_REVERSE.get(pcode, ""), "status": _get_law_status(pcode)}
        row = None
        for snap in (_EN_LAWS, _EN_ORDERS):  # 法律找不到才下載命令那份
            try:
                rows = await snap.load(self.client)
            except (httpx.HTTPError, ValueError) as e:
                logger.warning("英譯法規下載失敗: %s", e)
                return error_response("連線全國法規資料庫英文版失敗，請稍後重試", law=law)
            row = next((x for x in rows if x["id"] == pcode), None)
            if row:
                break
        if row is None:
            return error_response(f"「{law['name'] or pcode}」沒有官方英譯本", law=law)
        articles = row["articles"]
        if article_no:
            articles = [a for a in articles if a["number"] == _normalize_article_no(article_no)]
        elif from_no and to_no:
            try:
                lo, hi = _article_key(from_no), _article_key(to_no)
            except ValueError:
                return error_response("條號格式應為「184」「15-1」這類寫法", law=law)
            articles = [a for a in articles if re.fullmatch(r"\d+(-\d+)?", a["number"]) and lo <= _article_key(a["number"]) <= hi]
        if not articles:
            return error_response(f"英譯本查無{f'第 {article_no} 條' if article_no else '指定範圍的條文'}", law=law)
        en_date = f"{row['date'][:4]}-{row['date'][4:6]}-{row['date'][6:]}" if len(row["date"]) == 8 else row["date"]
        zh_date = _LAW_META.get(pcode, {}).get("amended", "")
        note = _EN_NOTE
        if zh_date and en_date and en_date < zh_date:
            note += f"英譯本對應 {en_date} 的版本，中文版已於 {zh_date} 修正，條文可能不一致。"
        return {
            "success": True, "language": "en",
            "law": {**law, "english_name": row["name"]},
            "english_version_date": en_date,
            "articles": articles,
            "note": note,
            "source_url": row["url"],
        }

    async def get_all_articles(self, pcode: str, refresh: bool = False) -> dict:
        """查詢法規全文（所有條文）；refresh=True 略過快取重新抓取"""
        cached = None if refresh else await self.cache.get_regulation(pcode, "")
        if cached:
            # 修正舊快取：覆蓋 status
            if "law" in cached:
                cached["law"]["status"] = _get_law_status(pcode)
            # 已廢止且無條文 → 加提示
            status = _get_law_status(pcode)
            if not cached.get("articles") and status == "已廢止":
                law_name = cached.get("law", {}).get("name", pcode)
                cached["note"] = f"「{law_name}」已廢止，全國法規資料庫不再提供條文全文。"
            return {"success": True, "cached": True, **cached}

        url = f"{REGULATION_ALL_URL}?pcode={pcode}"
        if not validate_url_domain(url):
            return error_response("域名不在白名單中", url=url)

        try:
            resp = await self.client.get(url)
            resp.raise_for_status()
            parsed = parse_law_all(resp.text)

            # 優先從 PCODE_MAP 反查法規名稱
            law_name = self._resolve_law_name(pcode, parsed.get("law_name", ""))
            status = _get_law_status(pcode)
            articles = parsed.get("articles", [])

            # 已廢止且無條文 → 明確提示
            if not articles and status == "已廢止":
                return {
                    "success": True,
                    "law": {"pcode": pcode, "name": law_name, "status": "已廢止"},
                    "articles": [],
                    "note": f"「{law_name or pcode}」已廢止，全國法規資料庫不再提供條文全文。",
                    "source_url": url,
                }

            structure = parsed.get("structure", [])

            data = {
                "law": {
                    "pcode": pcode,
                    "name": law_name,
                    "status": status,
                },
                "articles": articles,
                "structure": structure,
                "last_amended": parsed.get("last_amended", ""),
                "source_url": url,
            }

            await self.cache.set_regulation(pcode, data, "")
            return {"success": True, "cached": False, **data}

        except httpx.HTTPError as e:
            logger.warning("查詢法規全文 HTTP 失敗 (pcode=%s): %s", pcode, e)
            return error_response(
                "連線全國法規資料庫失敗，請稍後重試",
                law={"pcode": pcode},
            )

    async def get_article_range(self, pcode: str, from_no: str, to_no: str) -> dict:
        """查詢條號範圍"""
        # 先取全文，再篩選範圍
        all_result = await self.get_all_articles(pcode)
        if not all_result.get("success"):
            return all_result

        # 嘗試將條號轉為可比較的數字
        def article_sort_key(num: str) -> float:
            # 處理 "247-1" → 247.1, "15-1" → 15.1
            parts = num.replace("之", "-").split("-")
            try:
                base = float(parts[0])
                suffix = float(parts[1]) / 10 if len(parts) > 1 else 0
                return base + suffix
            except (ValueError, IndexError):
                return 0

        from_key = article_sort_key(from_no)
        to_key = article_sort_key(to_no)

        filtered = [
            a for a in all_result.get("articles", [])
            if from_key <= article_sort_key(a["number"]) <= to_key
        ]

        return {
            "success": True,
            "cached": all_result.get("cached", False),
            "law": all_result.get("law", {}),
            "articles": filtered,
            "source_url": all_result.get("source_url", ""),
        }

    async def _fetch_version_list(self, pcode: str) -> list[dict]:
        """抓取某法規的歷史版本清單（LawOldVerList）。

        Returns:
            [{lnndate: '19990421', lser: '001', date: '民國88年04月21日'}, ...]
            依公布時間由舊到新排序。不含現行版本。快取期限同法規快取（7 天）。
        """
        cached = await self.cache.get_regulation(pcode, "__verlist__")
        if cached is not None:
            return cached["versions"]
        if not validate_url_domain(REGULATION_OLDVERLIST_URL):
            return []
        params = {"pcode": pcode}
        last_exc: Exception | None = None
        resp: httpx.Response | None = None
        for attempt in range(3):
            try:
                resp = await self.client.get(REGULATION_OLDVERLIST_URL, params=params)
                resp.raise_for_status()
                last_exc = None
                break
            except httpx.HTTPError as e:
                last_exc = e
                resp = None  # 失敗 response 不可用於解析
                if attempt < 2 and _is_retryable_http_error(e):
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                break  # 不可重試或已用盡：交由下方 raise
        if last_exc is not None or resp is None:
            raise last_exc if last_exc is not None else httpx.HTTPError("版本清單抓取失敗")
        # 每個版本以 lnndate/lser 連結呈現；同一版本可能出現多次（全文/編章節等連結）
        pairs = re.findall(r"lnndate=(\d+)&(?:amp;)?lser=(\d+)", resp.text)
        seen: set[tuple[str, str]] = set()
        versions: list[dict] = []
        for lnndate, lser in pairs:
            key = (lnndate, lser)
            if key in seen:
                continue
            seen.add(key)
            versions.append({
                "lnndate": lnndate,
                "lser": lser,
                "date": _format_roc_date(lnndate),
            })
        # lnndate 為西元 YYYYMMDD，字串排序即時間排序（舊→新）
        versions.sort(key=lambda v: (v["lnndate"], v["lser"]))
        if versions:  # 空清單可能是錯誤頁，不快取
            await self.cache.set_regulation(pcode, {"versions": versions}, "__verlist__")
        return versions

    async def _fetch_version_articles(self, pcode: str, lnndate: str, lser: str) -> list[dict]:
        """抓取某法規某一歷史版本的全部條文（LawOldVer），帶快取與重試。

        失敗時 raise（由呼叫端區分「抓取失敗」與「該版無此條」）。
        """
        cache_key = f"__ver__{lnndate}_{lser}"
        cached = await self.cache.get_regulation(pcode, cache_key)
        if cached is not None:
            return cached.get("articles", [])

        if not validate_url_domain(REGULATION_OLDVER_URL):
            raise ValueError(f"域名不在白名單中: {REGULATION_OLDVER_URL}")
        params = {"pcode": pcode, "lnndate": lnndate, "lser": lser}

        # 全國法規資料庫在連續大量請求時會偶發丟連線，重試 3 次（遞增退避）
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                resp = await self.client.get(REGULATION_OLDVER_URL, params=params)
                resp.raise_for_status()
                parsed = parse_law_all(resp.text)
                articles = parsed.get("articles", [])
                if not articles:
                    # 全文頁竟解析不出任何條文 → 視為失敗（避免誤判整版為空）
                    raise ValueError("版本全文解析為空")
                # 歷史版本不會再變動，可長期快取
                await self.cache.set_regulation(pcode, {"articles": articles}, cache_key)
                return articles
            except (httpx.HTTPError, ValueError) as e:
                last_exc = e
                if attempt < 2 and _is_retryable_http_error(e):
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                break
        raise last_exc  # type: ignore[misc]

    async def get_article_history(self, pcode: str, article_no: str) -> dict:
        """查詢「單一條文」的歷次條文內容（跨版本前後對比）。

        作法：抓版本清單 → 依修法沿革略過沒動到目標條文的版本 → 並發抓其餘版本全文與現行全文
        → 抽出目標條文 → 折疊連續相同內容，得到該條文真正發生變動的時間軸（首次制定/增訂、歷次修正、刪除、回復）。
        LawOldVerList 不含現行版本，最近一次修正要靠現行全文補上。

        正確性原則：刪除只認「（刪除）」標記，抓取失敗或解析缺漏一律跳過並列入
        failed_versions（標記 partial），絕不因「該版查無此條」就捏造刪除事件。
        """
        target = _normalize_article_no(article_no)
        try:
            versions = await self._fetch_version_list(pcode)
        except httpx.HTTPError as e:
            logger.warning("查詢歷史版本清單失敗 (pcode=%s): %s", pcode, e)
            return {"available": False, "reason": "連線全國法規資料庫失敗"}

        history = get_law_history(pcode)
        changes, added = _parse_history(history) if history else ({}, {})
        # 從未修正的法規（沿革只有一筆）沒有歷史版本，只有現行版本；其他情況抓不到版本清單就無從重建
        if not versions and len(changes) != 1:
            return {"available": False, "reason": "查無歷史版本資料"}

        # 沿革未列出目標條文的版本，條文不會變動，不必抓（民法第 184 條：36 版只需抓 5 版）
        to_fetch = [v for v in versions if not changes or _article_changed_in(changes, v["lnndate"], article_no)]
        # 版本清單之後的修正都併入現行版本（通常只有一次；快取的清單過期時可能多次）
        newer = sorted(d for d in changes if d > (versions[-1]["lnndate"] if versions else ""))
        touching = [d for d in newer if _article_changed_in(changes, d, article_no)]
        current_date = touching[-1] if touching else (newer[-1] if newer else "")
        current = {"lnndate": current_date, "lser": "", "date": _format_roc_date(current_date) if current_date else "現行版本"}
        # 舊版頁面偶有與正式修正無關的文字出入（勞基法第 2 條 85–89 年各版「事」字時有時無），
        # 沿革明載這些修正都沒動到本條時，現行條文與前版的差異不算修正。沿革比版本清單舊時無從判斷，照常比對。
        compare_current = not newer or bool(touching)
        # 沿革有記、版本清單卻沒有的修正（清單最後一版之前）：動到本條就無法完整重建
        version_dates = {v["lnndate"] for v in versions}
        gaps = [d for d in sorted(changes) if versions and d < versions[-1]["lnndate"]
                and d not in version_dates and _article_changed_in(changes, d, article_no)]
        first_version = current if not versions else (None if gaps and gaps[0] < versions[0]["lnndate"] else versions[0])

        # 並發抓各版本全文（限流，避免對全國法規資料庫造成壓力）
        sem = asyncio.Semaphore(4)

        async def _one(v: dict) -> tuple[dict, str, str | None]:
            """回傳 (version, status, content)；status: found / absent / error"""
            async with sem:
                try:
                    articles = await self._fetch_version_articles(
                        pcode, v["lnndate"], v["lser"]
                    )
                except Exception as e:
                    logger.warning("查詢歷史版本全文失敗 (pcode=%s, lnndate=%s): %s",
                                   pcode, v["lnndate"], e or type(e).__name__)
                    return v, "error", None
            for a in articles:
                if _normalize_article_no(a.get("number", "")) == target:
                    return v, "found", a.get("content", "")
            return v, "absent", None  # 成功抓到該版，但該版無此條

        async def _current() -> tuple[dict, str, str | None]:
            async with sem:
                # 7 天快取可能早於剛公布的修正，這裡要的是含最新修正的現行條文
                data = await self.get_all_articles(pcode, refresh=True)
            live = data.get("last_amended", "")
            if live and (live not in changes or not current["lnndate"]):
                # 推不出現行版本日期（沒有內建沿革、與前版同日公布）時以官網修正日期標示；
                # 有沿革卻沒收錄這次修正（每週更新）則另列為不完整
                current.update(lnndate=live, date=_format_roc_date(live), unrecorded=bool(changes) and live not in changes)
            if not data.get("success") or not data.get("articles"):
                return current, "error", None
            for a in data["articles"]:
                if _normalize_article_no(a.get("number", "")) == target:
                    return current, "found", a.get("content", "")
            return current, "absent", None

        results = await asyncio.gather(*[_one(v) for v in to_fetch], _current())
        unrecorded = current.get("unrecorded", False)
        # 現行條文平常只用來確認沒有沿革未收錄的新修正；它自己或相關歷史版本讀取失敗時要保留
        last_relevant_failed = bool(to_fetch) and results[len(to_fetch) - 1][1] == "error"
        if compare_current or unrecorded or results[-1][1] == "error":
            pass
        elif last_relevant_failed:
            # 沿革記載之後的修正都沒動到本條，現行條文即最後一個相關版本修正後的文字
            current.update(lnndate=to_fetch[-1]["lnndate"], date=to_fetch[-1]["date"])
        else:
            results = results[:-1]

        # 折疊成真正的修法時間軸
        _DELETED_MARKERS = {"（刪除）", "(刪除)", "刪除"}
        revisions: list[dict] = []
        failed_versions: list[str] = []
        last_state: str | None = None  # None=尚未制定；"deleted"；或現行內容的正規化字串
        incomplete_baseline = False    # 最早可觀測版本即為刪除，缺刪除前原文
        unconfirmed_addition = False   # 沿革記為修正，較早版本卻讀不到本條

        def _emit(v: dict, action: str, content: str | None):
            revisions.append({
                "date": v["date"], "lnndate": v["lnndate"],
                "action": action, "content": content,
            })

        for v, status, content in results:
            if status == "error":
                failed_versions.append(v["date"])
                continue
            if status == "absent":
                if last_state is not None:
                    # 制定後卻在某版查無此條（非「（刪除）」標記）→ 視為解析缺漏，不捏造刪除
                    failed_versions.append(f"{v['date']}（該版解析缺漏）")
                continue

            # status == "found"
            is_deleted = (content or "").strip() in _DELETED_MARKERS
            # 同一條文在不同版本頁面上有排版差異：款次後的「、」時有時無（勞基法第 24 條 85–91 年各版）、
            # 全形半形標點、「︰／：」「０／○」「巿／市」「臺／台」互換（九二一震災重建暫行條例各版）。比對時一律忽略，回傳內容不變。
            norm = unicodedata.normalize("NFKC", re.sub(r"[\s、]+", "", content or "").translate(_TEXT_VARIANTS))
            if last_state is None:
                if is_deleted:
                    # 首次觀測到即為「（刪除）」（通常因更早的「制定/增訂」版本抓取失敗，
                    # 故無法得知原始條文）。仍 emit 可觀測到的刪除事件，不可靜默吞掉，
                    # 並標記 baseline 不完整（不依賴 failed_versions 是否非空）。
                    _emit(v, "刪除", "（刪除）")
                    last_state = "deleted"
                    incomplete_baseline = True
                else:
                    if v is first_version:
                        action = "制定"
                    elif failed_versions or gaps:  # 較早版本讀不到或缺漏，無從判斷本條是這次新增還是早已存在
                        action = "修正或增訂（較早版本缺漏）"
                    elif changes.get(v["lnndate"]) and not _tokens_include(added.get(v["lnndate"], set()), article_no):
                        # 沿革記載這次是修正而非增訂，前面版本卻沒有本條：前版頁面解析缺漏
                        action = "修正或增訂（較早版本缺漏）"
                        unconfirmed_addition = True
                    else:
                        action = "增訂"
                    _emit(v, action, content)
                    last_state = norm
            else:
                if is_deleted and last_state != "deleted":
                    _emit(v, "刪除", "（刪除）")
                    last_state = "deleted"
                elif not is_deleted and last_state == "deleted":
                    _emit(v, "回復", content)  # 刪除後回復條文
                    last_state = norm
                elif not is_deleted and norm != last_state:
                    _emit(v, "修正", content)
                    last_state = norm
                # 內容未變 → 不發事件

        # 若沒有任何版本成功讀取（全部抓取/解析失敗），不可回傳 available:True + 空 revisions，
        # 否則呼叫端會把它誤解為「確實無修法歷史」。
        error_count = sum(1 for (_, status, _) in results if status == "error")
        if error_count == len(results):
            return {
                "available": False,
                "reason": "歷史版本全文皆抓取失敗，無法重建條文時間軸",
                "article_no": article_no,
                "versions_scanned": len(versions) + 1,
                "failed_versions": failed_versions,
            }

        result: dict = {
            "available": True,
            "article_no": article_no,
            "versions_scanned": len(versions) + 1,  # 歷史版本 + 現行版本
            "versions_fetched": len(to_fetch) + 1,  # 含現行版本
            "revision_count": len(revisions),
            "revisions": revisions,
            "source_url": f"{REGULATION_OLDVERLIST_URL}?pcode={quote(pcode, safe='')}",
        }
        notes: list[str] = []
        if unconfirmed_addition:
            notes.append("沿革記載本條首次出現的那次是修正而非增訂，較早版本頁面卻沒有本條，時間軸可能缺漏")
        if gaps:
            notes.append(f"修法沿革中 {'、'.join(_format_roc_date(d) for d in gaps)} 的修正在歷史版本清單中找不到，時間軸可能缺漏")
        if unrecorded:
            notes.append(f"內建修法沿革尚未收錄官網 {current['date']} 的修正，無法確認該次是否動到本條；現行條文與前版的差異一律記在該日期")
        if len(touching) > 1:
            notes.append(f"版本清單缺少 {len(touching) - 1} 次涉及本條的修正版本（{'、'.join(_format_roc_date(d) for d in touching[:-1])}），"
                         "這幾次修正只能以現行條文呈現最後結果")
        if incomplete_baseline:
            notes.append("最早可觀測到的版本即為「（刪除）」，未能取得刪除前的原始條文，時間軸可能不完整")
        if not revisions:
            notes.append("在所有可成功讀取的版本中均未發現此條文")
        # baseline 不完整或有版本讀取失敗 → 時間軸非完整，明確標 partial
        if failed_versions or incomplete_baseline or len(touching) > 1 or unrecorded or gaps or unconfirmed_addition:
            result["partial"] = True
        if failed_versions:
            result["failed_versions"] = failed_versions
        if notes:
            result["notes"] = notes
        return result
