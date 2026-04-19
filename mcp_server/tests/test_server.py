"""server.py tool-level validation tests."""

import importlib
import sys

import pytest

import mcp_server.server as server


@pytest.mark.asyncio
async def test_search_judgments_rejects_non_positive_max_results():
    result = await server.search_judgments(keyword="契約", max_results=0)
    assert result["success"] is False
    assert "max_results" in result["error"]


@pytest.mark.asyncio
async def test_search_regulations_rejects_negative_offset():
    result = await server.search_regulations("法", offset=-1)
    assert result["success"] is False
    assert "offset" in result["error"]


def test_updater_import_bootstraps_ssl_setup():
    """驗證 updater 獨立 import 時會觸發 inject_os_trust_store（非只檢查 module 存在）"""
    import mcp_server.ssl_setup as ssl_setup

    ssl_setup._INJECTED = False
    sys.modules.pop("mcp_server.updater", None)

    importlib.import_module("mcp_server.updater")

    assert ssl_setup._INJECTED is True
