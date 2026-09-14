"""意見書全文：由官網 PDF 附件擷取、打包於 data/opinions.zip（#11）"""

import importlib.util
from pathlib import Path

from mcp_server.tools import constitutional_court as cc

ROOT = Path(__file__).resolve().parents[2]


def _build_script():
    spec = importlib.util.spec_from_file_location("build_opinions", ROOT / "scripts" / "build_opinions.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_opinions_after_737_come_from_pdf_text():
    """釋字 737 號起官網只列附件標題；打包資料必須提供意見書全文與附件清單。"""
    r = cc.get_interpretation("釋字第758號", include_opinions=True)
    assert r["success"] and r["has_opinions"]
    docs = r["opinion_documents"]
    assert len(docs) == 10 and all(d["chars"] > 1000 and d["url"] for d in docs)
    assert len(r["opinions"]) > 5000


def test_opinion_document_returns_one_full_opinion():
    """意見書合計超過安全閥時，可指定單份意見書讀完整全文。"""
    r = cc.get_interpretation("釋字第758號", opinion_document="許宗力")
    assert r["opinions"].startswith("【許大法官宗力提出之不同意見書】")
    assert not r["opinions_truncated"]
    assert "湯德宗" not in r["opinions"]


def test_opinion_document_not_found_gives_hint():
    r = cc.get_interpretation("釋字第758號", opinion_document="不存在的大法官")
    assert r["opinions_unavailable"] and "opinion_documents" in r["opinions_hint"]


def test_opinion_document_matches_joined_justices():
    """共同意見書的加入者也要能用姓名找到（標題為「詹大法官森林…加入」）。"""
    r = cc.get_interpretation("釋字第758號", opinion_document="黃昭元")
    assert r["opinions"].startswith("【張大法官瓊文提出，詹大法官森林、黃大法官昭元加入之協同意見書】")


def test_html_opinion_text_preferred_over_whole_transcript():
    """釋字 499 號網頁已有意見書內文，不可被整本抄本（含解釋文、聲請書）取代。"""
    r = cc.get_interpretation("釋字第499號", include_opinions=True)
    assert r["opinions"].startswith("部份協同意見書")
    assert cc.get_interpretation("釋字第499號", opinions_keyword="林永謀")["opinions_match_count"] > 0


def test_live_lookup_honors_opinion_document(monkeypatch):
    """打包後才公布的新案走 live 查詢，指定不存在的意見書要明確回報。"""
    result = {}
    cc._attach_html_opinions(result, "黃大法官昭元提出之協同意見書", False, "", "許宗力")
    assert result["opinions_unavailable"] and "許宗力" in result["opinions_hint"]


def test_opinion_document_on_html_opinions_does_not_return_unrelated_text():
    """早期釋字沒有 PDF 意見書，指定不存在的姓名不可回傳別人的意見書。"""
    r = cc.get_interpretation("釋字第80號", opinion_document="許宗力")
    assert r["opinions_unavailable"] and "opinions" not in r
    r = cc.get_interpretation("釋字第80號", opinion_document="王之倧")
    assert "王之倧" in r["opinions"]


def test_opinion_keyword_finds_text_inside_pdf():
    r = cc.get_interpretation("釋字第758號", opinions_keyword="湯德宗")
    assert r["opinions_match_count"] > 0


def test_undecodable_pdf_uses_labelled_transcription():
    """字型無法解碼的 PDF 不打包亂碼；改用頁面影像轉錄稿，並標註 transcribed 與核對提醒。"""
    r = cc.get_interpretation("釋字第735號", opinion_document="林俊益")
    doc = next(d for d in r["opinion_documents"] if "林大法官俊益" in d["title"])
    assert doc["transcribed"] and doc["chars"] > 5000 and doc["url"]
    assert "頁面影像轉錄" in r["opinions"].split("\n", 1)[0]
    assert "林俊益" in r["opinions"]


def test_constitutional_judgment_opinions_bundled():
    r = cc.get_interpretation("111年憲判字第1號", include_opinions=True)
    assert r["success"] and r["has_opinions"]
    assert r["opinion_documents"] and len(r["opinions"]) > 1000


def test_opinions_not_attached_unless_requested():
    r = cc.get_interpretation("釋字第758號")
    assert "opinions" not in r and "opinion_documents" not in r


def test_unavailable_hint_does_not_blame_early_cases():
    result = {}
    cc._attach_long_field(result, "", "opinions", True, "")
    assert result["opinions_unavailable"]
    assert "極早期" not in result["opinions_hint"] and "未收錄" not in result["opinions_hint"]


def test_clean_pdf_text_rejoins_wrapped_lines():
    build = _build_script()
    raw = "  土地法規定徵收補償發給完竣屆滿一\n年，未依徵收計畫開始使用者 ， 原所有權人得\n聲請收回。\n2\n    按依土地法規定，應予補償。"
    assert build.clean_pdf_text(raw) == (
        "土地法規定徵收補償發給完竣屆滿一年，未依徵收計畫開始使用者，原所有權人得聲請收回。\n"
        "按依土地法規定，應予補償。"
    )


def test_clean_pdf_text_normalizes_compatibility_ideographs():
    """Big5 轉出的 PDF 用相容漢字（U+F9F4「林」、U+F9D8「律」），需轉成標準漢字才搜得到。"""
    build = _build_script()
    assert build.clean_pdf_text("\uf9f4永謀大法官：法\uf9d8，第１條（略）") == "林永謀大法官：法律，第１條（略）"


def test_opinion_documents_skip_transcript_when_single_opinions_exist():
    build = _build_script()
    atts = [
        {"title": "450抄本（含解釋文、理由書、意見書、聲請書及附件）", "url": "transcript"},
        {"title": "450董大法官翔飛提出之不同意見書", "url": "single"},
        {"title": "一鍵打包下載", "url": "zip"},
    ]
    assert [a["url"] for a in build.opinion_documents(atts)] == ["single"]
    assert [a["url"] for a in build.opinion_documents(atts[:1])] == ["transcript"]


def test_parse_opinion_title_structures_authors_joined_type():
    build = _build_script()
    assert build.parse_opinion_title("許大法官玉秀提出，林大法官子儀、許大法官宗力加入之部分不同意見書") == {
        "authors": ["許玉秀"], "joined": ["林子儀", "許宗力"], "type": "部分不同"}
    assert build.parse_opinion_title("479董大法官翔飛、劉大法官鐵錚、黃大法官越欽共同提出之不同意見書") == {
        "authors": ["董翔飛", "劉鐵錚", "黃越欽"], "joined": [], "type": "不同"}
    assert build.parse_opinion_title("387意見書") == {"authors": [], "joined": [], "type": None}
    assert build.parse_opinion_title("協同意見書(蘇大法官永欽)")["authors"] == ["蘇永欽"]
    assert build.parse_opinion_title("黃大法官茂榮協同意見書")["authors"] == ["黃茂榮"]
    assert build.parse_opinion_title("憲法法庭112年憲判字第14號判決呂太郎大法官提出之協同意見書")["authors"] == ["呂太郎"]
    assert build.parse_opinion_title("795蔡宗珍大法官提出之不同意見書(吳陳鐶大法官加入)") == {
        "authors": ["蔡宗珍"], "joined": ["吳陳鐶"], "type": "不同"}
    assert build.parse_opinion_title("憲法法庭112年憲判字第1號判決黃大法官昭元提出，許大法官志雄加入、謝大法官銘洋加入（第3至16段）")["joined"] == ["許志雄", "謝銘洋"]


def test_opinion_documents_carry_structured_fields():
    docs = cc.get_interpretation("釋字第758號", include_opinions=True)["opinion_documents"]
    zhang = next(d for d in docs if d["title"].startswith("張大法官瓊文"))
    assert zhang["authors"] == ["張瓊文"] and zhang["joined"] == ["詹森林", "黃昭元"] and zhang["type"] == "協同"


def test_opinion_documents_exclude_non_justice_submissions():
    build = _build_script()
    atts = [
        {"title": "387意見書", "url": "early"},
        {"title": "監察院國家人權委員會1130409鑑定意見書_OCR", "url": "expert"},
        {"title": "11.台北律師公會1130409法庭之友意見書_OCR", "url": "amicus"},
        {"title": "國民黨1111226言詞辯論補充意見書_OCR", "url": "party"},
        {"title": "憲法法庭112年憲判字第1號判決黃大法官昭元提出之協同意見書", "url": "justice"},
    ]
    assert [a["url"] for a in build.opinion_documents(atts)] == ["early", "justice"]
    assert [a["url"] for a in build.opinion_documents(atts, require_justice=True)] == ["justice"]
