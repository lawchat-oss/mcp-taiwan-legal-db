"""司法院裁判書頁面解析器"""

import logging
from bs4 import BeautifulSoup
from urllib.parse import unquote
import re

logger = logging.getLogger(__name__)


# ============================================================
# 正規表達式模式（預編譯）
# ============================================================

# 空白字元集（含全形空格 \u3000）
_WS = r'[\s\u3000]'

# 案號：「114年度台上字第1498號」
CASE_ID_PATTERN = re.compile(r'\d+\s*年度?\s*.*字\s*第?\s*\d+\s*號')

# 法院名稱（從判決首行擷取）
COURT_PATTERN = re.compile(
    r'((?:最高(?:行政)?法院'
    r'|(?:臺北|臺中|高雄)高等行政法院'
    r'|臺灣高等法院(?:\S{2,3}分院)?'
    r'|臺灣\S+?(?:地方|少年及家事)法院'
    r'|智慧財產(?:及商業)?法院'
    r'|懲戒法院'
    r'|福建\S*?(?:地方|高等)法院(?:\S{2,3}分院)?))'
)

# 裁判日期：「中　華　民　國　115 年 1 月 21 日」
DATE_PATTERN = re.compile(
    rf'中{_WS}*華{_WS}*民{_WS}*國{_WS}*(\d{{2,3}}){_WS}*年'
    rf'{_WS}*(\d{{1,2}}){_WS}*月{_WS}*(\d{{1,2}}){_WS}*日'
)

# 法官：「審判長法官  鄭  純  惠」或「法官  吳  青  蓉」
JUDGE_PATTERN = re.compile(rf'(?:審判長)?法{_WS}*官{_WS}+(.+?)$')

# 當事人角色（含不定量空白）
_ROLE_KEYWORDS = [
    '共同', '上訴人', '被上訴人', '原告', '被告',
    '抗告人', '相對人', '聲請人', '再抗告人',
    '再審原告', '再審被告',
    '法定代理人', '訴訟代理人',
]

def _build_role_pattern():
    """動態建構當事人角色的正規表達式"""
    # 每個關鍵字中間插入可選空白
    parts = []
    for kw in _ROLE_KEYWORDS:
        spaced = rf'{_WS}*'.join(kw)
        parts.append(spaced)
    role_group = '|'.join(parts)
    return re.compile(
        rf'^{_WS}*((?:{role_group})){_WS}+(.+?)$'
    )

PARTY_ROLE_PATTERN = _build_role_pattern()

# 案由：「間請求清償債務事件」
CAUSE_PATTERN = re.compile(r'(?:間|因)(?:請求)?(.{2,20}?)事件')


def parse_search_results(html: str) -> list[dict]:
    """解析裁判書搜尋結果頁面（iframe 中的 qryresultlst.aspx）

    實際結構：
    - table#jud.jub-table 包含所有結果
    - 每筆結果有兩個 <tr>：資料列 + 摘要列(class="summary")
    - 連結：<a class="hlTitle_scroll" href="data.aspx?ty=JD&id=...">
    """
    soup = BeautifulSoup(html, "lxml")
    results = []

    # 主表格：table#jud 或 table.jub-table
    table = soup.select_one("table#jud") or soup.select_one("table.jub-table")
    if not table:
        return results

    rows = table.select("tr")
    i = 0
    while i < len(rows):
        row = rows[i]

        # 跳過標題列和摘要列
        if row.select_one("th") and not row.get("class"):
            i += 1
            continue
        if "summary" in (row.get("class") or []):
            i += 1
            continue

        cells = row.select("td")
        if len(cells) < 3:
            i += 1
            continue

        entry = _parse_result_row(cells)

        # 嘗試取得下一列的摘要
        if i + 1 < len(rows) and "summary" in (rows[i + 1].get("class") or []):
            summary_td = rows[i + 1].select_one("span.tdCut")
            if summary_td:
                entry["summary"] = summary_td.get_text(strip=True)
            i += 2
        else:
            i += 1

        if entry.get("case_id"):
            results.append(entry)

    return results


def _parse_result_row(cells) -> dict:
    """解析單一搜尋結果列

    cells 結構: [序號, 裁判字號(含連結), 裁判日期, 裁判案由]
    """
    entry = {
        "case_id": "",
        "court": "",
        "case_type": "",
        "court_level": 0,
        "date": "",
        "cause": "",
        "summary": "",
        "url": "",
        "jid": "",
    }

    # 從第二個 cell 取連結（裁判字號）
    link_cell = cells[1] if len(cells) > 1 else None
    if link_cell:
        link = link_cell.select_one("a")
        if link:
            entry["case_id"] = link.get_text(strip=True)
            href = link.get("href", "")
            if href:
                # 組合完整 URL
                if href.startswith("http"):
                    entry["url"] = href
                else:
                    entry["url"] = f"https://judgment.judicial.gov.tw/FJUD/{href}"

                # 從 URL 擷取 JID（URL decode）
                id_match = re.search(r"id=([^&]+)", href)
                if id_match:
                    entry["jid"] = unquote(id_match.group(1))

    # 裁判日期（第三個 cell，格式如 "115.02.10"）
    if len(cells) > 2:
        date_text = cells[2].get_text(strip=True)
        # 115.02.10 → 115-02-10
        date_match = re.match(r"(\d{2,3})[./](\d{1,2})[./](\d{1,2})", date_text)
        if date_match:
            entry["date"] = f"{date_match.group(1)}-{date_match.group(2).zfill(2)}-{date_match.group(3).zfill(2)}"

    # 裁判案由（第四個 cell）
    if len(cells) > 3:
        entry["cause"] = cells[3].get_text(strip=True)

    # 從 JID 解析法院名稱、案件類型、法院層級
    _enrich_from_jid(entry)

    return entry


# 預先計算：按 code 長度降序排列，確保 TPPD 優先匹配於 TPP
_SORTED_COURT_CODES: list[tuple[str, str]] | None = None


def _get_sorted_court_codes() -> list[tuple[str, str]]:
    """延遲載入並快取排序後的法院代碼（避免模組載入循環）"""
    global _SORTED_COURT_CODES
    if _SORTED_COURT_CODES is None:
        from mcp_server.config import COURT_CODE_TO_NAME
        _SORTED_COURT_CODES = sorted(
            COURT_CODE_TO_NAME.items(), key=lambda x: len(x[0]), reverse=True
        )
    return _SORTED_COURT_CODES


def _enrich_from_jid(entry: dict) -> None:
    """從 JID 解析法院名稱、案件類型、法院層級

    JID 格式：TPSV,104,台上,472,20150326,1
    第一段 TPSV = TPS（法院代碼）+ V（案件類型）
    """
    jid = entry.get("jid", "")
    if not jid:
        return

    prefix = jid.split(",")[0]
    if not prefix:
        return

    from mcp_server.config import COURT_LEVEL, CASE_TYPE_CODE_TO_NAME

    for code, court_name in _get_sorted_court_codes():
        if prefix.startswith(code):
            entry["court"] = court_name
            entry["court_level"] = COURT_LEVEL.get(code, 3)
            remaining = prefix[len(code):]
            if remaining in CASE_TYPE_CODE_TO_NAME:
                entry["case_type"] = CASE_TYPE_CODE_TO_NAME[remaining]
            break


def _clean_judgment_text(text: str) -> str:
    """清理裁判書全文：移除 UI 垃圾、正規化空白

    司法院 data.aspx #jud 元素包含字體大小選擇器（「版面大小 120% 100% 80%」）
    和大量 &nbsp;（\\xa0）/ 全形空格（\\u3000）用於排版。
    """
    # 1. 移除「版面大小 120% 100% 80%」UI 垃圾（開頭，含前導空白）
    text = re.sub(r'^\s*版面大小[\s\d%]*', '', text)

    # 2. \xa0 (non-breaking space) → 普通空格
    text = text.replace('\xa0', ' ')

    # 3. 每行尾端空白清除 + 連續空行壓縮為一個
    lines = text.split('\n')
    lines = [line.rstrip() for line in lines]
    text = '\n'.join(lines)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()


def _extract_metadata_rows(result: dict, container) -> None:
    """從結構化 metadata rows 擷取裁判字號、日期、案由

    新版 data.aspx HTML 格式：
    <div class="row"><div class="col-th">裁判字號：</div><div class="col-td">...</div></div>
    """
    for row in container.select(".row"):
        th = row.select_one(".col-th")
        td = row.select_one(".col-td:not(.jud_content)")
        if not th or not td:
            continue
        label = th.get_text(strip=True)
        value = td.get_text(strip=True)
        if "裁判字號" in label and value:
            result["case_id"] = value
        elif "裁判日期" in label and value:
            # 「民國 115 年 02 月 26 日」→ 115-02-26
            m = re.search(r'(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', value)
            if m:
                result["date"] = f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
        elif "裁判案由" in label and value:
            result["cause"] = value


def _strip_inline_tags(html: str) -> str:
    """移除 <abbr>/<span> 等 inline HTML 標籤，保留文字內容

    司法院新版 HTML 用 <abbr class="termhover"> 標記法律術語，
    BeautifulSoup get_text(\"\\n\") 會在每個標籤邊界插入換行，
    導致文字碎片化：「不確定故意」→「不確定\\n故意」。
    在 HTML 字串層級移除標籤（而非 unwrap），避免 NavigableString 分離問題。
    """
    html = re.sub(r'</?abbr[^>]*>', '', html)
    html = re.sub(r'</?span[^>]*>', '', html)
    return html


def parse_judgment_page(html: str) -> dict:
    """解析裁判書全文頁面（printData.aspx）"""
    soup = BeautifulSoup(html, "lxml")

    result = {
        "case_id": "",
        "court": "",
        "date": "",
        "judges": [],
        "parties": {},
        "cause": "",
        "main_text": "",
        "facts": "",
        "reasoning": "",
        "cited_statutes": [],
        "cited_cases": [],
        "full_text": "",
    }

    # 取得全文
    # data.aspx 使用 #jud，printData 使用 #jud_content 或 <pre>
    content_el = (
        soup.select_one("#jud") or
        soup.select_one("#jud_content") or
        soup.select_one(".jud-content") or
        soup.select_one("pre") or
        soup.select_one("#MainContent")
    )

    if not content_el:
        # fallback：取 body 全文
        body = soup.select_one("body")
        if body:
            content_el = body

    if content_el:
        # 新版 data.aspx：從結構化 metadata rows 擷取裁判字號/日期/案由
        _extract_metadata_rows(result, content_el)

        # 找判決書內文
        # 高院/地院 HTML 的 .htmlcontent 可能是空 <div>，內容在 .text-pre。
        # .jud_content 包含頁碼垃圾（1~144），優先用 .text-pre（乾淨）。
        _MIN_BODY_LEN = 50  # 裁判書至少有法院名+案號，遠超 50 字
        _hc = content_el.select_one(".htmlcontent")
        if _hc and len(_hc.get_text(strip=True)) >= _MIN_BODY_LEN:
            body_el = _hc
        else:
            # .htmlcontent 空 → 優先 .text-pre（無頁碼垃圾）> .jud_content > 整個 content_el
            _tp = content_el.select_one(".text-pre")
            if _tp and len(_tp.get_text(strip=True)) >= _MIN_BODY_LEN:
                body_el = _tp
            else:
                _jc = content_el.select_one(".jud_content")
                if _jc and len(_jc.get_text(strip=True)) >= _MIN_BODY_LEN:
                    body_el = _jc
                else:
                    body_el = content_el

        # 移除 <abbr>/<span> inline 標籤，防止 get_text("\n") 在術語邊界斷行
        # 必須在 HTML 字串層級處理（BeautifulSoup unwrap 不合併 NavigableString）
        clean_html = _strip_inline_tags(str(body_el))
        body_el = BeautifulSoup(clean_html, "lxml")

        raw_text = body_el.get_text("\n", strip=False)
        full_text = _clean_judgment_text(raw_text)
        result["full_text"] = full_text
        _extract_sections(result, full_text)

    # 擷取引用法條
    result["cited_statutes"] = _extract_cited_statutes(result["full_text"])
    result["cited_cases"] = _extract_cited_cases(result["full_text"])

    return result


def _normalize_ws(s: str) -> str:
    """移除所有空白（半形、全形 \\u3000、tab），用於標題比對"""
    return re.sub(r'[\s\u3000]+', '', s)


def _extract_sections(result: dict, text: str):
    """從全文中擷取各區段與結構化欄位"""
    lines = text.split("\n")
    _extract_case_id(result, lines)
    _extract_court(result, lines)
    _extract_main_sections(result, lines)
    _extract_parties(result, lines)
    _extract_cause(result, lines)
    _extract_date(result, lines)
    _extract_judges(result, lines)

    logger.debug(
        "裁判書解析: court=%r, date=%r, judges=%d位, parties=%d角色, "
        "cause=%r, 主文=%s, 事實=%s, 理由=%s",
        result["court"], result["date"],
        len(result["judges"]), len(result["parties"]),
        result["cause"][:20] if result["cause"] else "",
        bool(result["main_text"]), bool(result["facts"]), bool(result["reasoning"]),
    )


def _extract_case_id(result: dict, lines: list[str]):
    """擷取案號（通常在前 10 行）"""
    for line in lines[:10]:
        stripped = line.strip()
        if CASE_ID_PATTERN.search(stripped):
            result["case_id"] = stripped
            break


def _extract_court(result: dict, lines: list[str]):
    """擷取法院名稱（從前 10 行）"""
    for line in lines[:10]:
        stripped = line.strip()
        match = COURT_PATTERN.search(stripped)
        if match:
            result["court"] = match.group(1)
            break


def _extract_date(result: dict, lines: list[str]):
    """擷取裁判日期

    判決日期在末尾以獨立行呈現（字元間有大量空白），
    與內文中的日期（如「中華民國114年4月1日臺灣高等法院」）不同。
    策略：從後往前掃描，找第一個「獨立日期行」（整行幾乎只有日期）。
    """
    # 正向掃描，找第一個獨立日期行（判決日在書記官蓋章日之前）
    for line in lines:
        match = DATE_PATTERN.search(line)
        if match:
            # 確認這是獨立日期行：移除日期部分後剩餘文字很少
            remaining = line[:match.start()] + line[match.end():]
            remaining_clean = re.sub(r'[\s\u3000]+', '', remaining)
            if len(remaining_clean) <= 5:  # 容許少量殘餘（如句點）
                y, m, d = match.group(1), match.group(2), match.group(3)
                result["date"] = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
                break


def _extract_judges(result: dict, lines: list[str]):
    """擷取法官名單（從末 20 行）"""
    judges = []
    for line in lines[-20:]:
        stripped = line.strip()
        match = JUDGE_PATTERN.search(stripped)
        if match:
            # 移除名字中的所有空白
            name = re.sub(r'[\s\u3000]+', '', match.group(1))
            if name and len(name) >= 2:
                judges.append(name)
    result["judges"] = judges


def _extract_parties(result: dict, lines: list[str]):
    """擷取當事人（case_id 行之後 → 主文標題之前）"""
    parties = {}
    current_role = None
    in_party_section = False

    for line in lines:
        stripped = line.strip()
        normalized = _normalize_ws(stripped)

        # 到達主文就停止
        if normalized in ("主文", "據上論結"):
            break

        # 案號行之後開始解析當事人
        if not in_party_section:
            if result.get("case_id") and CASE_ID_PATTERN.search(stripped):
                in_party_section = True
            continue

        # 跳過空行
        if not stripped:
            continue

        # 嘗試匹配角色行
        role_match = PARTY_ROLE_PATTERN.match(line)
        if role_match:
            raw_role = role_match.group(1)
            role = _normalize_ws(raw_role)
            name = role_match.group(2).strip()
            # 移除名字中多餘的空白
            name = re.sub(r'[\s\u3000]{2,}', '', name)
            if role not in parties:
                parties[role] = []
            if name and len(name) >= 2:
                parties[role].append(name)
            current_role = role
        elif current_role and stripped:
            # 跳過「上列當事人間...」等描述行
            if any(kw in stripped for kw in ["上列", "當事人間", "提起", "本院"]):
                break
            # 跳過修飾語（不是人名）
            norm = _normalize_ws(stripped)
            if norm in ("共同", "兼", "即", "即被告"):
                continue
            # 可能是同一角色的另一位當事人
            name = re.sub(r'[\s\u3000]{2,}', '', stripped)
            if len(name) >= 2 and len(name) <= 30:
                # 過濾非人名的行
                if not any(kw in name for kw in ["年度", "字第", "判決", "裁定", "事件"]):
                    parties[current_role].append(name)

    result["parties"] = parties


def _extract_cause(result: dict, lines: list[str]):
    """擷取案由"""
    for line in lines:
        match = CAUSE_PATTERN.search(line)
        if match:
            result["cause"] = match.group(1).strip()
            break


def _extract_main_sections(result: dict, lines: list[str]):
    """擷取主文、事實、理由分段（使用空白正規化比對標題）"""
    current_section = ""
    section_content = {"主文": [], "事實": [], "理由": []}

    for line in lines:
        stripped = line.strip()
        normalized = _normalize_ws(stripped)

        if normalized == "主文":
            current_section = "主文"
            continue
        elif normalized in ("事實", "事實及理由", "事實與理由", "犯罪事實",
                            "犯罪事實及理由"):
            current_section = "事實"
            continue
        elif normalized == "理由":
            current_section = "理由"
            continue

        # 偵測裁判書結尾標記（僅獨立日期行才觸發截斷）
        # 理由內文中引用的日期（如「114年3月1日因...」）不會觸發
        if current_section:
            date_match = DATE_PATTERN.search(line)
            if date_match:
                remaining = line[:date_match.start()] + line[date_match.end():]
                remaining_clean = re.sub(r'[\s\u3000]+', '', remaining)
                if len(remaining_clean) <= 5:
                    break

        if current_section and stripped:
            # 用 stripped（已清除首尾空白）而非原始 line，避免前端 pre-wrap 排版亂
            section_content[current_section].append(stripped)

    result["main_text"] = "\n".join(section_content["主文"]).strip()
    result["facts"] = "\n".join(section_content["事實"]).strip()
    result["reasoning"] = "\n".join(section_content["理由"]).strip()


# 法規名稱 pattern（具體法名在前、縮寫在後、通用 fallback 最後）
# 與 citation_verifier.py 的 _LAW_NAMES_INNER 對齊
_LAW_NAMES_PATTERN = (
    r"(?:"
    # 長名稱優先（避免「民法」吃掉「民法總則施行法」）
    r"民事訴訟法施行法|刑事訴訟法施行法"
    r"|民事訴訟法|刑事訴訟法|行政訴訟法|行政程序法"
    r"|消費者保護法|個人資料保護法|不動產經紀業管理條例"
    r"|道路交通管理處罰條例|智慧財產案件審理法"
    r"|遺產及贈與稅法|少年事件處理法|社會秩序維護法"
    r"|金融消費者保護法|期貨交易法"
    r"|勞動基準法|勞動事件法|家事事件法|證券交易法"
    r"|公平交易法|強制執行法|政府採購法|稅捐稽徵法"
    r"|國家賠償法|著作權法|營業秘密法|公寓大廈管理條例"
    r"|土地登記規則|商業事件審理法|國民法官法"
    r"|洗錢防制法|信託法|仲裁法|破產法"
    # 基本法律
    r"|民法|刑法|憲法|公司法|土地法|專利法|商標法"
    r"|保險法|海商法|票據法|所得稅法|營業稅法|銀行法|建築法"
    # 通用 fallback（涵蓋所有「X法」「X條例」「X規則」「X辦法」）
    r"|[\u4e00-\u9fff]{2,15}(?:法|條例|規則|辦法)"
    r")"
)

# 引用法條前綴（「依民法」「按刑法」等），用於清理擷取結果
_STATUTE_PREFIX_STRIP = (
    "依", "按", "照", "據", "參", "援", "適用", "準用", "違反",
    "以及", "及", "與", "或", "和", "又", "暨", "另依", "再依",
    "得依", "應依", "得按", "應按", "得適用", "應適用",
)

# 已知法規名稱（全國法規資料庫 11,700+ 部 + 常用簡稱）
def _load_known_statute_names() -> set[str]:
    """從 pcode_all.json 載入完整法規名清單"""
    import json
    from pathlib import Path
    pcode_path = Path(__file__).resolve().parent.parent / "data" / "pcode_all.json"
    names = set()
    try:
        with open(pcode_path, "r") as f:
            data = json.load(f)
        names = set(data["pcode_map"].keys())
    except (FileNotFoundError, KeyError):
        logger.warning("pcode_all.json 未找到，使用內建法規名清單")
    # 判決書常用簡稱
    names.update([
        "刑法", "憲法", "營業稅法", "勞基法", "消保法", "個資法",
        "國賠法", "道交條例",
    ])
    return names

_KNOWN_STATUTE_NAMES = _load_known_statute_names()
_MAX_STATUTE_NAME_LEN = max((len(n) for n in _KNOWN_STATUTE_NAMES), default=20)


def _clean_statute_name(raw: str) -> str:
    """清理 regex 擷取的法規名稱

    用 suffix hash lookup（O(max_law_len)）對照 11,700+ 部法規名。
    """
    if raw in _KNOWN_STATUTE_NAMES:
        return raw

    name = raw

    # 1. 清除常見前綴（「依民法」→「民法」）
    for prefix in _STATUTE_PREFIX_STRIP:
        if name.startswith(prefix) and len(name) > len(prefix) + 1:
            name = name[len(prefix):]
            break

    if name in _KNOWN_STATUTE_NAMES:
        return name

    # 2. 尾部匹配：suffix hash lookup（優先最長匹配）
    name_len = len(name)
    for suffix_len in range(min(_MAX_STATUTE_NAME_LEN, name_len - 1), 1, -1):
        suffix = name[-suffix_len:]
        if suffix in _KNOWN_STATUTE_NAMES:
            return suffix

    return name


def _cn_num_to_int(s: str) -> int:
    """中文數字轉阿拉伯數字（支援一~九千九百九十九）

    >>> _cn_num_to_int("三百七十七")
    377
    >>> _cn_num_to_int("十八")
    18
    >>> _cn_num_to_int("一千零一")
    1001
    """
    digit_map = {
        "〇": 0, "０": 0, "零": 0,
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
        "六": 6, "七": 7, "八": 8, "九": 9,
    }
    unit_map = {"十": 10, "百": 100, "千": 1000}

    result = 0
    current = 0

    for ch in s:
        if ch in digit_map:
            current = digit_map[ch]
        elif ch in unit_map:
            unit = unit_map[ch]
            if current == 0 and unit == 10:
                current = 1
            result += current * unit
            current = 0

    result += current
    return result


# 中文數字法條 pattern
_CN_DIGITS = r"[一二三四五六七八九十百千零〇０]"
_STATUTE_PATTERN_CN = re.compile(
    r"(" + _LAW_NAMES_PATTERN + r")"
    r"\s*第\s*(" + _CN_DIGITS + r"{1,8})"
    r"\s*條"
    r"(?:之(" + _CN_DIGITS + r"{1,4}))?",
    re.UNICODE,
)


def _extract_cited_statutes(text: str) -> list[str]:
    """擷取引用的法條（支援阿拉伯數字 + 中文數字）"""
    # 1. 阿拉伯數字（原有邏輯）
    pattern = re.compile(
        r"(" + _LAW_NAMES_PATTERN + r")"
        r"\s*第\s*(\d{1,4}(?:[-之]\d{1,2})?)\s*條"
        r"(?:\s*第\s*\d+\s*項)?",
        re.UNICODE,
    )
    results = []
    seen = set()
    for m in pattern.finditer(text):
        raw_name = m.group(1)
        article = m.group(2)
        name = _clean_statute_name(raw_name)
        entry = f"{name}第{article}條"
        if entry not in seen:
            seen.add(entry)
            results.append(entry)

    # 2. 中文數字（「第三百七十七條」「第十八條之一」）
    for m in _STATUTE_PATTERN_CN.finditer(text):
        raw_name = m.group(1)
        cn_article = m.group(2)
        cn_sub = m.group(3)

        name = _clean_statute_name(raw_name)
        article_int = _cn_num_to_int(cn_article)
        if article_int == 0:
            continue

        if cn_sub:
            sub_int = _cn_num_to_int(cn_sub)
            entry = f"{name}第{article_int}-{sub_int}條"
        else:
            entry = f"{name}第{article_int}條"

        if entry not in seen:
            seen.add(entry)
            results.append(entry)

    return results


# 完整法院名稱 pattern（用於引用判決擷取，與頂部 COURT_PATTERN 對齊）
_CITED_COURT_PATTERN = (
    r"(?:"
    r"最高(?:行政)?法院"
    r"|(?:臺灣|台灣)高等法院(?:\S{2,3}分院)?"
    r"|(?:臺灣|台灣)\S+?(?:地方|少年及家事)法院"
    r"|(?:臺北|臺中|高雄)高等行政法院"
    r"|智慧財產(?:及商業)?法院"
    r"|懲戒法院"
    r"|福建\S*?(?:地方|高等)法院(?:\S{2,3}分院)?"
    r")"
)


def _extract_cited_cases(text: str) -> list[str]:
    """擷取引用的判決"""
    pattern = re.compile(
        r"(" + _CITED_COURT_PATTERN + r"\s*\d+\s*年度?\s*\S+字\s*第?\s*\d+\s*號)",
        re.UNICODE,
    )
    matches = pattern.findall(text)
    return list(dict.fromkeys(matches))
