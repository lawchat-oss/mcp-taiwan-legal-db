"""條文分項：官網每一項／款／目是 .law-article 底下獨立的 div，解析後不可黏成一串。

HTML 依 law.moj.gov.tw 的實際結構精簡而成（line-0000 項、line-0004 款、line-0006 目），
HTTP 一律以 MockTransport 模擬。
"""

import httpx
import pytest

from mcp_server.cache.db import CacheDB
from mcp_server.parsers.regulation_parser import (
    REGULATION_PARSER_VERSION,
    parse_law_all,
    parse_single_article,
)
from mcp_server.tools.regulations import RegulationClient


def _single(title: str, article_html: str) -> str:
    return (
        f"<html><head><title>{title}-全國法規資料庫</title></head><body>"
        f'<div class="law-article">{article_html}</div></body></html>'
    )


def _div(cls: str, text: str) -> str:
    return f'<div class="{cls}">{text}</div>'


# 租賃住宅市場發展及管理條例第 10 條：兩項，各有自己的一、二…款
RENTAL_10 = "".join([
    _div("line-0000 show-number", "租賃期間發生下列情形之一者，出租人得提前終止租賃契約，且承租人不得要求任何賠償："),
    _div("line-0004", "一、承租人毀損租賃住宅或附屬設備，不為修繕或相當之賠償。"),
    _div("line-0004", "二、承租人遲付租金或費用，達二個月之租額，經定相當期限催告仍拒繳。"),
    _div("line-0004", "三、承租人未經出租人書面同意，將租賃住宅轉租於他人。"),
    _div("line-0004", "四、出租人為重新建築而必要收回。"),
    _div("line-0004", "五、其他依法律規定得提前終止租賃契約。"),
    _div("line-0000 show-number", "出租人依前項規定提前終止租賃契約者，應依下列規定期限，檢附相關事證，以書面通知承租人："),
    _div("line-0004", "一、依前項第一款至第三款及第五款規定終止者，於終止前三十日。"),
    _div("line-0004", "二、依前項第四款規定終止者，於終止前三個月。"),
])

# 民法第 450 條：三項、沒有款，第 3 項含但書
CIVIL_450 = "".join([
    _div("line-0000 show-number", "租賃定有期限者，其租賃關係，於期限屆滿時消滅。"),
    _div("line-0000 show-number", "未定期限者，各當事人得隨時終止契約。但有利於承租人之習慣者，從其習慣。"),
    _div("line-0000 show-number", "前項終止契約，應依習慣先期通知。但不動產之租金，以星期、半個月或一個月定其支付之期限者，"
                                  "出租人應以曆定星期、半個月或一個月之末日為契約終止期，並應至少於一星期、半個月或一個月前通知之。"),
])


class TestParseSingleArticleParagraphs:
    def test_two_paragraphs_with_their_own_items(self):
        r = parse_single_article(_single("租賃住宅市場發展及管理條例", RENTAL_10))
        assert r["paragraph_count"] == 2
        first, second = r["paragraphs"]
        assert [len(first["items"]), len(second["items"])] == [5, 2]
        assert second["no"] == 2 and second["text"].startswith("出租人依前項規定")
        assert second["items"][0]["text"] == "一、依前項第一款至第三款及第五款規定終止者，於終止前三十日。"
        # content 仍是字串（向下相容），但項與項之間有換行、款前縮排一個全形空白
        lines = r["article_content"].split("\n")
        assert len(lines) == 9
        assert lines[6].startswith("出租人依前項規定")
        assert lines[7] == "　一、依前項第一款至第三款及第五款規定終止者，於終止前三十日。"
        assert "契約。出租人依前項" not in r["article_content"]

    def test_paragraphs_without_items_and_proviso_kept_in_place(self):
        r = parse_single_article(_single("民法", CIVIL_450))
        assert r["paragraph_count"] == 3
        assert all(p["items"] == [] for p in r["paragraphs"])
        assert "但不動產之租金" in r["paragraphs"][2]["text"]  # 但書不拆成另一項

    def test_single_paragraph_without_number_still_counts_as_paragraph_1(self):
        html = _single("民法", "".join([
            _div("line-0000", "依照當事人一方預定用於同類契約之條款而訂定之契約，為左列各款之約定，按其情形顯失公平者，該部分約定無效："),
            _div("line-0004", "一、免除或減輕預定契約條款之當事人之責任者。"),
            _div("line-0004", "二、加重他方當事人之責任者。"),
        ]))
        r = parse_single_article(html)
        assert r["paragraph_count"] == 1
        assert len(r["paragraphs"][0]["items"]) == 2

    def test_subitems_and_deeper_levels(self):
        html = _single("所得稅法", "".join([
            _div("line-0000 show-number", "按第十四條及前二條規定計得之個人綜合所得總額，減除下列免稅額及扣除額後之餘額，為個人之綜合所得淨額："),
            _div("line-0004", "一、免稅額：納稅義務人按規定減除其本人、配偶及合於下列規定扶養親屬之免稅額；"),
            _div("line-0006", "（一）納稅義務人及其配偶之直系尊親屬，年滿六十歲，或未滿六十歲而無謀生能力，受納稅義務人扶養者。"),
            _div("line-0006", "（二）納稅義務人之子女未滿二十歲，或滿二十歲以上，而因在校就學、身心障礙或無謀生能力受納稅義務人扶養者。"),
            _div("line-0602", "1.深一層的文字"),
            _div("line-0004", "二、扣除額：納稅義務人就下列標準扣除額或列舉扣除額擇一減除外，並減除特別扣除額："),
        ]))
        r = parse_single_article(html)
        item1, item2 = r["paragraphs"][0]["items"]
        assert [s["no"] for s in item1["subitems"]] == [1, 2]
        assert item1["subitems"][1]["children"] == ["1.深一層的文字"]
        assert item2["subitems"] == []
        assert "\n　　（一）" in r["article_content"]
        assert "\n　　　1.深一層的文字" in r["article_content"]

    def test_deleted_article(self):
        r = parse_single_article(_single("民法", _div("line-0000", "（刪除）")))
        assert r["article_content"] == "（刪除）"
        assert r["deleted"] is True
        assert r["paragraphs"] == [] and r["paragraph_count"] == 0

    def test_plain_text_article_falls_back_unchanged(self):
        """沒有子 div 的 .law-article：照舊整段回傳，不猜結構"""
        r = parse_single_article(_single("民法", "因故意或過失，不法侵害他人之權利者，負損害賠償責任。"))
        assert r["article_content"] == "因故意或過失，不法侵害他人之權利者，負損害賠償責任。"
        assert r["paragraphs"] == [] and r["paragraph_count"] == 0

    def test_unknown_child_class_falls_back(self):
        r = parse_single_article(_single("民法", _div("line-0000", "第一句。") + _div("something-else", "第二句。")))
        assert r["article_content"] == "第一句。第二句。"
        assert r["paragraph_count"] == 0

    def test_loose_text_outside_child_divs_is_never_dropped(self):
        r = parse_single_article(_single("民法", "散落文字" + _div("line-0000", "第一項。")))
        assert "散落文字" in r["article_content"]
        assert r["paragraph_count"] == 0


class TestParseLawAllParagraphs:
    def _law_all(self, rows: str) -> str:
        return (
            "<html><head><title>租賃住宅市場發展及管理條例-全國法規資料庫</title></head><body>"
            f'<div class="law-reg-content">{rows}</div></body></html>'
        )

    def _row(self, no: str, article_html: str) -> str:
        return (
            f'<div class="row"><div class="col-no">第 {no} 條</div>'
            f'<div class="col-data"><div class="law-article">{article_html}</div></div></div>'
        )

    def test_each_article_is_split(self):
        html = self._law_all(self._row("10", RENTAL_10) + self._row("11", _div("line-0000", "（刪除）")))
        arts = parse_law_all(html)["articles"]
        assert [a["number"] for a in arts] == ["10", "11"]
        assert arts[0]["paragraph_count"] == 2
        assert [len(p["items"]) for p in arts[0]["paragraphs"]] == [5, 2]
        assert arts[1]["deleted"] is True

    def test_fallback_without_reg_content_container(self):
        html = "<html><body>" + self._row("450", CIVIL_450) + "</body></html>"
        arts = parse_law_all(html)["articles"]
        assert arts[0]["paragraph_count"] == 3

    def test_plain_col_data_keeps_old_shape(self):
        html = self._law_all('<div class="row"><div class="col-no">第 1 條</div><div class="col-data">為規定勞動條件最低標準。</div></div>')
        art = parse_law_all(html)["articles"][0]
        assert art["content"] == "為規定勞動條件最低標準。"
        assert art["paragraphs"] == [] and art["paragraph_count"] == 0


@pytest.fixture
async def cache(tmp_path):
    db = CacheDB(db_path=tmp_path / "test_cache.db")
    await db.initialize()
    yield db
    await db.close()


def _mock_client(client: RegulationClient, html: str, hits: list):
    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(str(request.url))
        return httpx.Response(200, text=html)
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


class TestRegulationClientParagraphs:
    async def test_get_article_returns_paragraphs_and_caches_them(self, cache):
        client = RegulationClient(cache)
        hits: list = []
        _mock_client(client, _single("租賃住宅市場發展及管理條例", RENTAL_10), hits)
        try:
            first = await client.get_article("D0060125", "10")
            again = await client.get_article("D0060125", "10")
        finally:
            await client.close()
        art = first["articles"][0]
        assert art["paragraph_count"] == 2 and len(art["paragraphs"][1]["items"]) == 2
        assert again["cached"] is True and again["articles"][0]["paragraph_count"] == 2
        assert "parser_version" not in again
        assert len(hits) == 1

    async def test_cache_written_by_older_parser_is_refetched(self, cache):
        # 舊版 parser 寫入的快取：項款黏在一起、沒有 parser_version
        await cache.set_regulation("D0060125", {
            "law": {"pcode": "D0060125", "name": "租賃住宅市場發展及管理條例", "status": "現行法規"},
            "articles": [{"number": "10", "content": "租賃期間發生…契約。出租人依前項規定…"}],
            "source_url": "https://law.moj.gov.tw/",
        }, "10")
        client = RegulationClient(cache)
        hits: list = []
        _mock_client(client, _single("租賃住宅市場發展及管理條例", RENTAL_10), hits)
        try:
            result = await client.get_article("D0060125", "10")
        finally:
            await client.close()
        assert result["cached"] is False
        assert result["articles"][0]["paragraph_count"] == 2
        assert len(hits) == 1
        stored = await cache.get_regulation("D0060125", "10")
        assert stored["parser_version"] == REGULATION_PARSER_VERSION

    async def test_get_all_articles_ignores_older_cache(self, cache):
        await cache.set_regulation("D0060125", {
            "law": {"pcode": "D0060125", "name": "租賃住宅市場發展及管理條例", "status": "現行法規"},
            "articles": [{"number": "10", "content": "黏在一起的舊內容"}],
        }, "")
        html = (
            "<html><head><title>租賃住宅市場發展及管理條例-全國法規資料庫</title></head><body>"
            '<div class="law-reg-content"><div class="row"><div class="col-no">第 10 條</div>'
            f'<div class="col-data"><div class="law-article">{RENTAL_10}</div></div></div></div></body></html>'
        )
        client = RegulationClient(cache)
        hits: list = []
        _mock_client(client, html, hits)
        try:
            result = await client.get_all_articles("D0060125")
        finally:
            await client.close()
        assert result["cached"] is False
        assert result["articles"][0]["paragraph_count"] == 2
