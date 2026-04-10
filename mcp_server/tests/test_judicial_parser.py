"""judicial_parser.py 法條擷取測試

補齊中文數字、去重、前綴清理、邊界條件等測試。
既有的 6 個基礎測試在 test/test_mcp_tools.py，此處補齊 Tier 2-4。
"""

import pytest
from mcp_server.parsers.judicial_parser import (
    _extract_cited_statutes,
    _cn_num_to_int,
    _clean_statute_name,
)


# ═══ Tier 2: 中文數字法條 ═══

class TestCnNumToInt:
    """中文數字轉阿拉伯數字"""

    def test_simple_digit(self):
        assert _cn_num_to_int("三") == 3

    def test_tens(self):
        assert _cn_num_to_int("十八") == 18

    def test_hundreds(self):
        assert _cn_num_to_int("三百七十七") == 377

    def test_thousands(self):
        assert _cn_num_to_int("一千零一") == 1001

    def test_ten_alone(self):
        """「十」= 10（省略前面的「一」）"""
        assert _cn_num_to_int("十") == 10

    def test_complex(self):
        assert _cn_num_to_int("九百九十九") == 999


class TestExtractCitedStatutesChinese:
    """中文數字法條擷取"""

    def test_chinese_article_number(self):
        text = "依民法第三百七十七條之規定"
        results = _extract_cited_statutes(text)
        assert "民法第377條" in results

    def test_chinese_sub_article(self):
        """「第十八條之一」→ 第18-1條"""
        text = "勞動基準法第十八條之一"
        results = _extract_cited_statutes(text)
        assert "勞動基準法第18-1條" in results

    def test_chinese_simple_ten(self):
        text = "依刑法第十條之規定"
        results = _extract_cited_statutes(text)
        assert "刑法第10條" in results


# ═══ Tier 3: 去重與多法條 ═══

class TestExtractDedup:
    """同法條多次出現只回傳一次"""

    def test_dedup_same_statute(self):
        text = "民法第184條第1項及民法第184條第2項"
        results = _extract_cited_statutes(text)
        assert results.count("民法第184條") == 1

    def test_multiple_different_statutes(self):
        text = "依民法第184條及刑法第320條之規定"
        results = _extract_cited_statutes(text)
        assert "民法第184條" in results
        assert "刑法第320條" in results
        assert len(results) == 2


# ═══ Tier 4: 前綴清理 ═══

class TestCleanStatuteName:
    """法規名稱前綴清理"""

    def test_strip_prefix_yi(self):
        """「依民法」→「民法」"""
        result = _clean_statute_name("依民法")
        assert result == "民法"

    def test_strip_prefix_an(self):
        """「按刑法」→「刑法」"""
        result = _clean_statute_name("按刑法")
        assert result == "刑法"

    def test_strip_prefix_weifan(self):
        """「違反洗錢防制法」→「洗錢防制法」"""
        result = _clean_statute_name("違反洗錢防制法")
        assert result == "洗錢防制法"

    def test_no_strip_normal_name(self):
        """正常法規名不誤刪"""
        result = _clean_statute_name("消費者保護法")
        assert result == "消費者保護法"

    def test_strip_prefix_shiyong(self):
        """「適用民事訴訟法」→「民事訴訟法」"""
        result = _clean_statute_name("適用民事訴訟法")
        assert result == "民事訴訟法"


# ═══ Tier 5: 邊界條件 ═══

class TestEdgeCases:
    """邊界與異常格式"""

    def test_hyphen_article(self):
        """含 - 的條號（如 247-1）"""
        text = "民法第247-1條"
        results = _extract_cited_statutes(text)
        assert "民法第247-1條" in results

    def test_zhi_article(self):
        """含「之」的條號"""
        text = "民法第247之1條"
        results = _extract_cited_statutes(text)
        assert "民法第247之1條" in results

    def test_generic_law_name(self):
        """通用 fallback pattern 匹配未列舉的法規"""
        text = "依據家庭暴力防治法第10條"
        results = _extract_cited_statutes(text)
        assert any("第10條" in r for r in results)

    def test_ordinance_suffix(self):
        """「條例」結尾的法規"""
        text = "道路交通管理處罰條例第35條"
        results = _extract_cited_statutes(text)
        assert "道路交通管理處罰條例第35條" in results

    def test_empty_text(self):
        """空白文字不應報錯"""
        assert _extract_cited_statutes("") == []

    def test_no_statute_text(self):
        """不含法條引用的文字"""
        assert _extract_cited_statutes("今天天氣很好") == []
