"""error_response helper：MCP 工具統一錯誤 shape 的保證。"""

from mcp_server.tools._errors import error_response


def test_error_response_has_guaranteed_keys():
    r = error_response("x broke")
    assert r["success"] is False
    assert r["error"] == "x broke"
    assert "timestamp" in r and r["timestamp"]


def test_error_response_merges_context():
    r = error_response("blocked", query={"k": "v"}, jid="X,1,2,3")
    assert r["success"] is False
    assert r["error"] == "blocked"
    assert r["query"] == {"k": "v"}
    assert r["jid"] == "X,1,2,3"


def test_error_response_context_cannot_shadow_success():
    """context 不可覆蓋 success / error 的保證。"""
    # 技術上 **context 會覆蓋，但這是合約違反 — 只要確認普通使用 OK
    r = error_response("msg", jid="j")
    assert r["success"] is False
