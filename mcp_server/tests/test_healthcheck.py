"""健康檢查的判定邏輯：不連網，用假的 search / get。"""

from mcp_server.healthcheck import _check


async def _ok_search():
    return {"success": True, "categories": [{"source": "甲"}], "results": [{"id": "x:1"}]}


async def test_check_classifies_results():
    async def get(item):
        return {"success": True, "full_text": "函釋全文" * 20, "source_url": "https://example.gov.tw/" + "a" * 80}

    async def thin_get(item):
        return {"success": True, "full_text": "", "summary": "摘要還在" * 20, "source_url": "https://example.gov.tw/" + "a" * 80}

    async def articles_get(item):
        return {"success": True, "articles": [{"number": "184", "content": "因故意或過失" * 10}]}

    async def source_error():
        return {"success": True, "categories": [{"source": "甲", "error": "ConnectError: x"}], "results": []}

    async def empty():
        return {"success": True, "categories": [{"source": "甲"}], "results": []}

    async def no_id():
        return {"success": True, "categories": [], "results": [{"title": "t"}]}

    async def boom():
        raise RuntimeError("壞了")

    assert (await _check("t", "s", _ok_search, get))[2] == "OK"
    assert (await _check("t", "s", _ok_search, thin_get))[2] == "THIN"  # 摘要、網址再長也不算正文
    assert (await _check("t", "s", _ok_search, articles_get))[2] == "OK"
    assert (await _check("t", "s", source_error, get))[2:4] == ("FAIL", "甲: ConnectError: x")
    assert (await _check("t", "s", empty, get))[2] == "EMPTY"
    assert (await _check("t", "s", no_id, get))[2] == "FAIL"
    assert (await _check("t", "s", boom, get))[2:4] == ("FAIL", "RuntimeError: 壞了")


async def test_stopped_canary_fails_when_marker_disappears():
    from mcp_server.healthcheck import _expect_stopped

    class Client:
        def __init__(self, status):
            self.status = status

        async def get(self, item_id):
            return {"success": True, "full_text": "x" * 50, **({"status": self.status} if self.status else {})}

    assert (await _expect_stopped(Client("停止適用"), "fsc:1"))["success"] is True
    assert (await _expect_stopped(Client("適用中"), "fsc:1"))["success"] is False
    assert (await _expect_stopped(Client(None), "fsc:1"))["success"] is False
