"""Selected documents retain their tails, including when an old cache entry was truncated."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from mcp_server.tools import agency_interpretations as ai, admin_decisions as ad, literature as lit
from mcp_server.tools import fint, legislative_records as lr, statistics as stats, constitutional_court as cc
from mcp_server.tools.constitutional_docket import ConstitutionalDocketClient

TEXT = "本文" * 60000 + "末段關鍵理由：釋字第748號"


def cache():
    return SimpleNamespace(
        get_judgment=AsyncMock(return_value={"full_text": "舊截斷文字", "full_text_truncated": True, "truncated": True}),
        get_search=AsyncMock(return_value=None), set_judgment=AsyncMock(), set_search=AsyncMock())


@pytest.mark.parametrize("module,cls,key", [
    (ai, ai.AgencyInterpretationClient, "ncc"),
    (ad, ad.AdminDecisionClient, "moea"),
    (lit, lit.LiteratureClient, "journals"),
    (stats, stats.StatisticsClient, "judicial"),
])
async def test_source_clients_return_long_document_and_refresh_truncated_cache(monkeypatch, module, cls, key):
    saved = cache()
    if module in (ad, stats):
        # Legacy entries were cut by character limits; v2 keys bypass them.
        prefix = "decision:v2:" if module is ad else "statistics:v2:"

        async def previous(cache_key):
            assert cache_key.startswith(prefix)
            return None
        saved.get_judgment.side_effect = previous
    label, aliases, search, _ = module.SOURCES[key]
    fetch = AsyncMock(return_value={"full_text": TEXT})
    monkeypatch.setitem(module.SOURCES, key, (label, aliases, search, fetch))
    client = cls(saved)
    try:
        result = await client.get(key + ":test")
    finally:
        await client.close()
    assert result["success"] and not result["cached"] and result["full_text"] == TEXT
    assert not result.get("full_text_truncated") and not result.get("truncated")
    fetch.assert_awaited_once()


@pytest.mark.parametrize("kind", ["precedent", "legislative", "docket"])
async def test_other_document_clients_return_tail(monkeypatch, kind):
    saved = cache()
    if kind == "precedent":
        client = fint.PrecedentClient(saved)
        monkeypatch.setattr(fint, "get", AsyncMock(return_value={"full_text": TEXT}))
        call = lambda: client.get("D:test")
    elif kind == "legislative":
        client = lr.LegislativeRecordsClient(saved)
        monkeypatch.setattr(lr, "get_join", AsyncMock(return_value={"full_text": TEXT}))
        call = lambda: client.get("join:test")
    else:
        from mcp_server.tools import constitutional_docket as cd
        client = ConstitutionalDocketClient(saved)
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"%PDF")))
        monkeypatch.setattr(cd, "pdf_to_text", lambda _: TEXT)
        call = lambda: client.document("123456")
    try:
        result = await call()
    finally:
        await client.close()
    assert result["success"] and not result["cached"] and result["full_text"] == TEXT
    assert result["full_text_truncated"] is False


def test_reasoning_citations_include_the_document_tail(monkeypatch):
    monkeypatch.setattr(cc, "_load_old_cases", lambda: {"1": {"reasoning": TEXT}})
    text, truncated, error = cc._get_reasoning_text("釋字", 1, 0)
    assert text == TEXT and not truncated and error is None
    assert cc._extract_citations(text) == [{"type": "釋字", "case_id": "釋字第748號", "number": 748}]


def test_statistics_does_not_drop_sheets_after_long_text():
    rendered = stats.sheets_to_text([("一", [[TEXT]]), ("二", [["最後一張表"]])])
    assert TEXT in rendered and "最後一張表" in rendered


async def test_statistics_reuses_cached_sheet_limited_table(monkeypatch):
    saved = cache()
    saved.get_judgment = AsyncMock(return_value={"table_text": "表" + stats.SHEETS_OMITTED, "truncated": True})
    fetch = AsyncMock()
    label, aliases, search, _ = stats.SOURCES["judicial"]
    monkeypatch.setitem(stats.SOURCES, "judicial", (label, aliases, search, fetch))
    client = stats.StatisticsClient(saved)
    try:
        result = await client.get("judicial:test")
    finally:
        await client.close()
    assert result["cached"] and fetch.await_count == 0
