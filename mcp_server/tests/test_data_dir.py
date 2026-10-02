"""執行期寫入位置：使用者目錄的 pcode_all.json 比內建新才採用"""

import json

from mcp_server import config


def _write(d, update_date):
    d.mkdir(parents=True, exist_ok=True)
    (d / "pcode_all.json").write_text(json.dumps({"update_date": update_date}), "utf-8")


def test_pcode_data_dir_prefers_newer(tmp_path, monkeypatch):
    bundled, user = tmp_path / "bundled", tmp_path / "user"
    _write(bundled, "2026-09-03")
    monkeypatch.setattr(config, "BUNDLED_DATA_DIR", bundled)
    monkeypatch.setattr(config, "USER_DATA_DIR", user)

    assert config.pcode_data_dir() == bundled  # 使用者目錄還沒有副本

    _write(user, "2026-08-01")
    assert config.pcode_data_dir() == bundled  # 升級套件後內建較新

    _write(user, "2026-10-03")
    assert config.pcode_data_dir() == user

    (user / "pcode_all.json").write_text("{broken", "utf-8")
    assert config.pcode_data_dir() == bundled


def test_user_data_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MCP_TAIWAN_LEGAL_DB_HOME", str(tmp_path))
    assert config._user_data_dir() == tmp_path
