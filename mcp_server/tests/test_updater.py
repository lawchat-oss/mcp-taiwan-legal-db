"""updater 的時區設定：Windows 沒有系統時區資料庫時也必須能 import（#10）"""

import builtins
import importlib
import sys
from datetime import datetime, timedelta


def test_updater_imports_without_zoneinfo(monkeypatch):
    """模擬 Windows 缺 tzdata：任何 zoneinfo 查詢都失敗，updater 仍應正常 import。"""
    real_import = builtins.__import__

    def no_zoneinfo(name, *args, **kwargs):
        if name == "zoneinfo":
            raise ImportError("simulated: no time zone database")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "mcp_server.updater", raising=False)
    monkeypatch.setattr(builtins, "__import__", no_zoneinfo)
    updater = importlib.import_module("mcp_server.updater")

    assert updater.TW_TZ.utcoffset(None) == timedelta(hours=8)
    assert datetime(2026, 7, 1, tzinfo=updater.TW_TZ).utcoffset() == timedelta(hours=8)
