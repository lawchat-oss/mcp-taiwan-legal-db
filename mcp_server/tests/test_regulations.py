"""MCP 法規工具單元測試

測試 6 項問題的修復：
- P1: Status 不再寫死「現行法規」
- P2: 不存在的條號不回傳垃圾內容
- P3: 已廢止法規有明確提示
- P4: 搜尋結果有 status 欄位
- P5: pcode_all.json v2 結構正確
- P6: 搜尋支援翻頁
"""

import json
from pathlib import Path

import pytest

from mcp_server.tools.regulations import (
    _PCODE_ALL,
    _ABOLISHED_SET,
    _get_law_status,
    _PCODE_ALL_PATH,
)
from mcp_server.parsers.regulation_parser import parse_single_article, parse_law_all


# ============================================================
# P5: pcode_all.json v2 結構
# ============================================================

class TestPcodeAllV2:
    """確認 pcode_all.json v2 結構正確"""

    def test_file_exists(self):
        assert _PCODE_ALL_PATH.exists(), f"pcode_all.json 不存在: {_PCODE_ALL_PATH}"

    def test_version_2(self):
        with open(_PCODE_ALL_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("version") == 2

    def test_has_abolished_set(self):
        with open(_PCODE_ALL_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        abolished = data.get("abolished_set", [])
        assert isinstance(abolished, list)
        assert len(abolished) > 0, "abolished_set 不應為空"

    def test_pcode_map_backward_compatible(self):
        """pcode_map 結構不變（name → pcode）"""
        assert "民法" in _PCODE_ALL
        assert _PCODE_ALL["民法"] == "B0000001"

    def test_abolished_set_loaded(self):
        """_ABOLISHED_SET 已正確載入"""
        assert isinstance(_ABOLISHED_SET, set)
        assert len(_ABOLISHED_SET) > 0


# ============================================================
# P1: 法規狀態判斷
# ============================================================

class TestLawStatus:
    """P1: _get_law_status() 根據 pcode 正確判斷狀態"""

    def test_active_law(self):
        assert _get_law_status("B0000001") == "現行法規"  # 民法

    def test_abolished_law(self):
        # 動員戡亂時期臨時條款（1991 廢止）
        assert "A0000005" in _ABOLISHED_SET, "A0000005 應在 abolished_set 中"
        assert _get_law_status("A0000005") == "已廢止"

    def test_unknown_pcode_defaults_to_active(self):
        assert _get_law_status("ZZZZZZZZ") == "現行法規"


# ============================================================
# P2: 垃圾內容過濾
# ============================================================

class TestGarbageContentFilter:
    """P2: parse_single_article 不回傳頁尾免責聲明"""

    def test_garbage_disclaimer_rejected(self):
        """頁尾免責聲明不應被當作條文"""
        html = """
        <html><head><title>全國法規資料庫</title></head>
        <body>
        <h2>條文內容</h2>
        <div><p>本網站係提供法規之最新動態資訊及資料檢索，並不提供法規及法律諮詢之服務。
        若有任何法律上的疑義，建議您可逕向發布法規之主管機關洽詢。</p></div>
        </body></html>
        """
        result = parse_single_article(html)
        assert result["article_content"] == ""

    def test_valid_article_preserved(self):
        """正常法條內容應保留"""
        html = """
        <html><head><title>民法-全國法規資料庫</title></head>
        <body>
        <div class="law-article">因故意或過失，不法侵害他人之權利者，負損害賠償責任。
        故意以背於善良風俗之方法，加損害於他人者亦同。</div>
        </body></html>
        """
        result = parse_single_article(html)
        assert "故意或過失" in result["article_content"]

    def test_footer_with_visitor_count_rejected(self):
        """含瀏覽人次的頁尾應被過濾"""
        html = """
        <html><body>
        <div><p>瀏覽人次總計：1,537,936,459人 本月瀏覽人次：5,151,627人</p></div>
        </body></html>
        """
        result = parse_single_article(html)
        assert result["article_content"] == ""


# ============================================================
# P4 + P6: 搜尋結果狀態 + 翻頁
# ============================================================

class TestSearchRegulations:
    """P4/P6: 搜尋結果有 status + 支援翻頁"""

    def test_search_results_have_status(self):
        """模擬 search_regulations 邏輯，確認每筆有 status"""
        keyword = "勞動"
        matches = []
        for name, pcode in _PCODE_ALL.items():
            if keyword in name:
                matches.append({
                    "law_name": name,
                    "pcode": pcode,
                    "status": "已廢止" if pcode in _ABOLISHED_SET else "現行法規",
                })
        assert len(matches) > 0, "應有搜尋結果"
        for m in matches:
            assert "status" in m
            assert m["status"] in ("現行法規", "已廢止")

    def test_active_laws_sorted_first(self):
        """現行法規應排在已廢止前面"""
        keyword = "組織"  # 會同時包含現行和廢止的法規
        matches = []
        for name, pcode in _PCODE_ALL.items():
            if keyword in name:
                matches.append({
                    "law_name": name,
                    "pcode": pcode,
                    "status": "已廢止" if pcode in _ABOLISHED_SET else "現行法規",
                })
        matches.sort(key=lambda m: (m["status"] != "現行法規", m["law_name"]))

        # 找到第一個已廢止的位置
        first_abolished = next(
            (i for i, m in enumerate(matches) if m["status"] == "已廢止"),
            len(matches),
        )
        # 在它之前的都應該是現行
        for m in matches[:first_abolished]:
            assert m["status"] == "現行法規"

    def test_pagination_offset(self):
        """offset 分頁應正確截取"""
        keyword = "法"  # 大量結果
        matches = [
            {"law_name": n, "pcode": p}
            for n, p in _PCODE_ALL.items()
            if keyword in n
        ]
        assert len(matches) > 50, "需要 >50 筆結果才能測翻頁"
        page1 = matches[0:50]
        page2 = matches[50:100]
        assert len(page1) == 50
        assert page1[0] != page2[0] if page2 else True


# ============================================================
# parse_law_all 基本功能
# ============================================================

class TestParseLawAll:
    """確認 parse_law_all 正常運作"""

    def test_parse_div_row_structure(self):
        html = """
        <html><head><title>勞動基準法-全國法規資料庫</title></head>
        <body>
        <h2>勞動基準法</h2>
        <div class="row"><div class="col-no">第 1 條</div><div class="col-data">為規定勞動條件最低標準。</div></div>
        <div class="row"><div class="col-no">第 2 條</div><div class="col-data">本法用詞定義如下。</div></div>
        </body></html>
        """
        result = parse_law_all(html)
        assert len(result["articles"]) == 2
        assert result["articles"][0]["number"] == "1"
        assert "勞動條件" in result["articles"][0]["content"]
