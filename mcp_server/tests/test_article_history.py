"""單一條文歷次內容：依修法沿革略過無關版本、補上現行版本、忽略排版差異（不連網）"""

import asyncio

from mcp_server.tools import regulations as R

HISTORY = (
    "1.中華民國七十三年七月三十日總統令制定公布全文 86 條\r\n"
    "2.中華民國八十五年十二月二十七日總統令修正公布第 3 條；並增訂第 30-1、\r\n"
    "  84-1～84-2 條條文\r\n"
    "3.中華民國一百零五年十二月二十一日總統令修正公布第 24、34、36～39 條條文\r\n"
    "4.中華民國一百十三年七月三十一日總統令修正公布第 54 條條文"
)
VERSIONS = [
    {"lnndate": d, "lser": "001", "date": R._format_roc_date(d)}
    for d in ("19840730", "19961227", "20161221")
]


def test_cn_int():
    assert [R._cn_int(s) for s in ("十八", "八十八", "一百零五", "一百十三")] == [18, 88, 105, 113]


def test_history_changes_parses_dates_lists_and_full_text():
    changes = R._history_changes(HISTORY)
    assert changes == {
        "19840730": None,
        "19961227": {"3", "30-1", "84-1～84-2"},
        "20161221": {"24", "34", "36～39"},
        "20240731": {"54"},
    }


def test_partially_parsed_article_lists_are_not_trusted():
    """清單夾章節名或漏寫「第」時，不能只憑解析得到的條號略過版本（行政程序法、行政訴訟法沿革的實際寫法）。"""
    heading = "1.中華民國八十九年十一月二十九日總統令修正公布第5、8、第二章第三節節名、20～26、73條條文；增訂第17-1條條文"
    missing_di = "1.中華民國九十六年七月四日總統令修正公布49、98～100、276條條文；並增訂第12-1～12-4條條文"
    sub_article = "1.中華民國九十年一月一日總統令修正公布第5條之1條文"
    written_range = "1.中華民國二十年七月六日國民政府修正公布第20條至第24條條文"
    for history in (heading, missing_di, sub_article, written_range):
        assert list(R._history_changes(history).values()) == [None]


def test_line_wrapped_article_numbers_keep_both_readings():
    """折行切開條號：可能是同一號（「9」換行「85」＝985），也可能漏了頓號（民法沿革「993」換行「994」）。"""
    changes = R._history_changes("1.中華民國八十七年六月十七日總統令修正公布第 983、9 \r\n  85 條；刪除第 986、993 \r\n  994 條條文")
    for article in ("983", "985", "993", "994"):
        assert R._article_changed_in(changes, "19980617", article)
    assert not R._article_changed_in(changes, "19980617", "984")


def test_article_changed_in_handles_ranges_and_unknown_dates():
    changes = R._history_changes(HISTORY)
    assert R._article_changed_in(changes, "19840730", "24")        # 全文
    assert R._article_changed_in(changes, "19961227", "84之2")     # 範圍含子條號
    assert not R._article_changed_in(changes, "19961227", "84-3")
    assert R._article_changed_in(changes, "20161221", "37")        # 36～39
    assert not R._article_changed_in(changes, "19961227", "24")
    assert R._article_changed_in(changes, "20990101", "24")        # 沿革查不到的版本不略過


def _client(monkeypatch, article_no: str, old_texts: dict[str, str], current_text: str,
            versions=VERSIONS, history=HISTORY, live_date="", current_ok=True):
    monkeypatch.setattr(R, "get_law_history", lambda pcode: history)
    client = R.RegulationClient.__new__(R.RegulationClient)
    fetched: list[str] = []

    async def version_list(pcode):
        return versions

    async def version_articles(pcode, lnndate, lser):
        fetched.append(lnndate)
        if old_texts[lnndate] is None:
            raise R.httpx.ConnectError("disconnected")
        return [{"number": article_no, "content": old_texts[lnndate]}]

    async def all_articles(pcode, refresh=False):
        assert refresh, "現行條文要略過快取，避免拿到修正前的全文"
        fetched.append("current")
        return {"success": current_ok, "last_amended": live_date, "articles": [{"number": article_no, "content": current_text}]}

    client._fetch_version_list = version_list
    client._fetch_version_articles = version_articles
    client.get_all_articles = all_articles
    return client, fetched


def _timeline(r):
    return [(x["date"], x["action"]) for x in r["revisions"]]


def test_skips_versions_that_did_not_touch_the_article(monkeypatch):
    """第 24 條只在全文制定與 105 年修正時變動；113 年修正沒動到本條，現行條文的排版差異不算修正。"""
    old = {"19840730": "一、延長二小時以內者。", "20161221": "一、延長二小時以內者，加給三分之一。"}
    client, fetched = _client(monkeypatch, "24", old, "（排版不同的現行條文）")
    r = asyncio.run(client.get_article_history("N0030001", "24"))
    assert fetched == ["19840730", "20161221", "current"]
    assert _timeline(r) == [("民國73年07月30日", "制定"), ("民國105年12月21日", "修正")]
    assert r["versions_scanned"] == 4 and r["versions_fetched"] == 3 and "partial" not in r


def test_formatting_differences_are_not_amendments(monkeypatch):
    """舊版頁面款次後的「、」、全形半形標點、「︰／：」「０／○」「巿／市」「臺／台」時有時無，不能報成修正。"""
    old = {"19840730": "一、臺北巿新臺幣○○元︰以下。", "20161221": "一 台北市新台幣００元:以下。"}
    client, _ = _client(monkeypatch, "24", old, "")
    assert _timeline(asyncio.run(client.get_article_history("N0030001", "24"))) == [("民國73年07月30日", "制定")]


def test_latest_amendment_comes_from_current_text(monkeypatch):
    """版本清單不含現行版本；沿革最新一次修正列到本條時，由現行全文補上這次修正。"""
    old = {"19840730": "舊條文。"}
    client, fetched = _client(monkeypatch, "54", old, "新條文。")
    r = asyncio.run(client.get_article_history("N0030001", "54"))
    assert fetched == ["19840730", "current"]
    assert r["revisions"][-1] == {"date": "民國113年07月31日", "lnndate": "20240731", "action": "修正", "content": "新條文。"}


def test_never_amended_law_reports_enactment_from_current_text(monkeypatch):
    history = "1.中華民國七十三年七月三十日總統令制定公布全文 86 條"
    client, fetched = _client(monkeypatch, "24", {}, "條文。", versions=[], history=history)
    r = asyncio.run(client.get_article_history("X0000001", "24"))
    assert fetched == ["current"] and _timeline(r) == [("民國73年07月30日", "制定")]


def test_unreadable_baseline_does_not_invent_an_addition(monkeypatch):
    """制定版讀取失敗時，第一個讀到的版本不能直接認定為「增訂」。"""
    old = {"19840730": None, "20161221": "條文。"}
    client, _ = _client(monkeypatch, "24", old, "")
    r = asyncio.run(client.get_article_history("N0030001", "24"))
    assert r["partial"] and _timeline(r) == [("民國105年12月21日", "修正或增訂（較早版本缺漏）")]


def test_amendments_missing_from_a_stale_version_list_are_reported(monkeypatch):
    """快取的版本清單比沿革舊、漏了中間的修正：本條在中間那次被改時仍要比對現行條文，並標示不完整。"""
    history = HISTORY + "\r\n5.中華民國一百十四年一月一日總統令修正公布第 24 條條文\r\n6.中華民國一百十五年一月一日總統令修正公布第 54 條條文"
    old = {"19840730": "舊條文。", "20161221": "舊條文。"}
    client, _ = _client(monkeypatch, "24", old, "新條文。", history=history)
    r = asyncio.run(client.get_article_history("N0030001", "24"))
    assert _timeline(r)[-1] == ("民國114年01月01日", "修正") and "partial" not in r

    history += "\r\n7.中華民國一百十五年六月一日總統令修正公布第 24 條條文"
    client, _ = _client(monkeypatch, "24", old, "新條文。", history=history)
    r = asyncio.run(client.get_article_history("N0030001", "24"))
    assert _timeline(r)[-1] == ("民國115年06月01日", "修正") and r["partial"] and "民國114年01月01日" in r["notes"][0]


def test_amendment_newer_than_bundled_history_is_dated_from_the_live_page(monkeypatch):
    """官網已公布、內建沿革還沒收錄的修正：以官網修正日期記錄，並標示不完整。"""
    old = {"19840730": "舊條文。", "20161221": "舊條文。"}
    client, _ = _client(monkeypatch, "24", old, "新條文。", live_date="20250301")
    r = asyncio.run(client.get_article_history("N0030001", "24"))
    assert _timeline(r)[-1] == ("民國114年03月01日", "修正") and r["partial"] and "114年03月01日" in r["notes"][0]


def test_failed_current_page_is_reported_even_when_not_compared(monkeypatch):
    """現行全文讀取失敗時無法確認有沒有沿革未收錄的新修正，要列出並標示不完整。"""
    old = {"19840730": "條文。", "20161221": "條文。"}
    client, _ = _client(monkeypatch, "24", old, "", current_ok=False)
    r = asyncio.run(client.get_article_history("N0030001", "24"))
    assert r["partial"] and r["failed_versions"] == ["民國113年07月31日"]


def test_live_date_labels_current_version_without_bundled_history(monkeypatch):
    old = {"19840730": "舊條文。", "19961227": "舊條文。", "20161221": "舊條文。"}
    client, _ = _client(monkeypatch, "24", old, "新條文。", history="", live_date="20240731")
    r = asyncio.run(client.get_article_history("N0030001", "24"))
    assert _timeline(r)[-1] == ("民國113年07月31日", "修正") and "partial" not in r


def test_empty_version_list_needs_a_single_entry_history(monkeypatch):
    """版本清單為空可能是錯誤頁；沒有沿革佐證「從未修正」時不可把現行條文當成制定。"""
    client, _ = _client(monkeypatch, "24", {}, "條文。", versions=[], history="", live_date="20240731")
    assert asyncio.run(client.get_article_history("X0000001", "24"))["available"] is False


def test_versions_missing_from_the_list_are_reported(monkeypatch):
    """版本清單漏了沿革記載的制定版：不可把第一個讀到的版本標成制定，並標示不完整。"""
    old = {"19961227": "條文。", "20161221": "條文。"}
    client, _ = _client(monkeypatch, "24", old, "", versions=VERSIONS[1:])
    r = asyncio.run(client.get_article_history("N0030001", "24"))
    assert r["partial"] and "民國73年07月30日" in r["notes"][0]
    assert _timeline(r)[0][1] == "修正或增訂（較早版本缺漏）"


def test_same_day_current_version_keeps_the_live_date(monkeypatch):
    """現行版本與最後一個歷史版本同日公布時，沿革裡沒有更新的日期，改用官網修正日期。"""
    history = HISTORY.rsplit("\r\n", 1)[0]  # 去掉 113 年那筆
    old = {"19840730": "舊條文。", "20161221": "舊條文。"}
    client, _ = _client(monkeypatch, "24", old, "新條文。", history=history, live_date="20161221")
    r = asyncio.run(client.get_article_history("N0030001", "24"))
    assert _timeline(r)[-1] == ("民國105年12月21日", "修正") and "partial" not in r


def test_amendment_is_not_reported_as_addition_when_earlier_page_lacks_the_article(monkeypatch):
    """沿革記載 105 年是「修正」第 24 條，制定版頁面卻沒有本條（解析缺漏）：不可報成增訂。"""
    client, _ = _client(monkeypatch, "24", {"19840730": "條文。", "20161221": "新條文。"}, "")
    async def version_articles(pcode, lnndate, lser):
        return [] if lnndate == "19840730" else [{"number": "24", "content": "新條文。"}]
    client._fetch_version_articles = version_articles
    r = asyncio.run(client.get_article_history("N0030001", "24"))
    assert r["partial"] and _timeline(r) == [("民國105年12月21日", "修正或增訂（較早版本缺漏）")]


def test_genuine_addition_is_still_reported(monkeypatch):
    """沿革記載 85 年「增訂」第 84-1 條，制定版沒有本條是正常的。"""
    client, _ = _client(monkeypatch, "84-1", {}, "")
    async def version_articles(pcode, lnndate, lser):
        return [] if lnndate == "19840730" else [{"number": "84-1", "content": "增訂條文。"}]
    client._fetch_version_articles = version_articles
    r = asyncio.run(client.get_article_history("N0030001", "84-1"))
    assert _timeline(r) == [("民國85年12月27日", "增訂")] and "partial" not in r


def test_added_articles_come_only_from_the_addition_clause():
    """「修正公布第295條條文、並增訂第294-1條條文」中間是頓號，295 不能算成增訂。"""
    _, added = R._parse_history("1.中華民國八十八年四月二十一日總統令修正公布第295條條文、並增訂第294-1條條文")
    assert added == {"19990421": {"294-1"}}


def test_current_text_fills_in_for_a_failed_historical_version(monkeypatch):
    """105 年那版讀取失敗時，現行條文就是該次修正後的文字，用該次的日期補上並標示不完整。"""
    client, _ = _client(monkeypatch, "24", {"19840730": "舊條文。", "20161221": None}, "新條文。")
    r = asyncio.run(client.get_article_history("N0030001", "24"))
    assert _timeline(r) == [("民國73年07月30日", "制定"), ("民國105年12月21日", "修正")]
    assert r["partial"] and r["failed_versions"] == ["民國105年12月21日"]
