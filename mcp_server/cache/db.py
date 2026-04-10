"""SQLite 快取管理"""

import json
import hashlib
import logging
import aiosqlite
from datetime import datetime, timedelta
from pathlib import Path

from mcp_server.config import (
    CACHE_DB_PATH,
    CACHE_JUDGMENT_TTL,
    CACHE_SEARCH_TTL,
    CACHE_REGULATION_TTL,
    CACHE_PCODE_TTL,
)

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS judgment_cache (
    cache_key TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'unknown',
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    expires_at TEXT NOT NULL,
    hit_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS regulation_cache (
    pcode TEXT NOT NULL,
    article_no TEXT NOT NULL DEFAULT '',
    data_json TEXT NOT NULL,
    last_amended TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    expires_at TEXT NOT NULL,
    PRIMARY KEY (pcode, article_no)
);

CREATE TABLE IF NOT EXISTS search_cache (
    query_hash TEXT PRIMARY KEY,
    query_params TEXT NOT NULL,
    data_json TEXT NOT NULL,
    result_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_judgment_expires ON judgment_cache(expires_at);
CREATE INDEX IF NOT EXISTS idx_regulation_expires ON regulation_cache(expires_at);
CREATE INDEX IF NOT EXISTS idx_search_expires ON search_cache(expires_at);
"""


def _hash_query(params: dict) -> str:
    """將查詢參數轉為 SHA256 hash"""
    raw = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _expires_at(ttl_seconds: int) -> str:
    """計算過期時間。ttl=-1 表示永不過期（設為 9999 年）"""
    if ttl_seconds < 0:
        return "9999-12-31T23:59:59"
    dt = datetime.now() + timedelta(seconds=ttl_seconds)
    return dt.isoformat()


def _is_expired(expires_at: str) -> bool:
    """檢查是否已過期"""
    if expires_at.startswith("9999"):
        return False
    try:
        exp = datetime.fromisoformat(expires_at)
        return datetime.now() > exp
    except ValueError:
        return True


class CacheDB:
    """非同步 SQLite 快取（持久連線 + WAL mode）"""

    def __init__(self, db_path: Path | None = None):
        self.db_path = str(db_path or CACHE_DB_PATH)
        self._db: aiosqlite.Connection | None = None

    async def initialize(self):
        """建立持久連線、啟用 WAL mode、建立資料表"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self):
        """關閉持久連線"""
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        """取得持久連線（初始化後才可使用）"""
        if self._db is None:
            raise RuntimeError("CacheDB 尚未初始化，請先呼叫 initialize()")
        return self._db

    async def cleanup_expired(self):
        """清理過期快取"""
        now = datetime.now().isoformat()
        await self.db.execute(
            "DELETE FROM search_cache WHERE expires_at < ?", (now,)
        )
        await self.db.execute(
            "DELETE FROM regulation_cache WHERE expires_at < ?", (now,)
        )
        # judgment_cache 不清理（永久快取）
        await self.db.commit()

    # --- 搜尋結果快取 ---

    async def get_search(self, params: dict) -> dict | None:
        """查詢搜尋快取"""
        qhash = _hash_query(params)
        self.db.row_factory = aiosqlite.Row
        cursor = await self.db.execute(
            "SELECT data_json, expires_at FROM search_cache WHERE query_hash = ?",
            (qhash,),
        )
        row = await cursor.fetchone()
        if row and not _is_expired(row["expires_at"]):
            logger.debug("搜尋快取命中: hash=%s", qhash[:12])
            return json.loads(row["data_json"])
        logger.debug("搜尋快取未命中: hash=%s", qhash[:12])
        return None

    async def set_search(self, params: dict, data: dict, ttl: int | None = None):
        """寫入搜尋快取"""
        qhash = _hash_query(params)
        exp = _expires_at(ttl if ttl is not None else CACHE_SEARCH_TTL)
        await self.db.execute(
            "INSERT OR REPLACE INTO search_cache (query_hash, query_params, data_json, result_count, expires_at) VALUES (?, ?, ?, ?, ?)",
            (qhash, json.dumps(params, ensure_ascii=False), json.dumps(data, ensure_ascii=False), data.get("total_count", 0), exp),
        )
        await self.db.commit()

    # --- 裁判書快取 ---

    async def get_judgment(self, key: str) -> dict | None:
        """查詢裁判書快取（key = JID 或 case_id）"""
        self.db.row_factory = aiosqlite.Row
        cursor = await self.db.execute(
            "SELECT data_json, expires_at FROM judgment_cache WHERE cache_key = ?",
            (key,),
        )
        row = await cursor.fetchone()
        if row and not _is_expired(row["expires_at"]):
            await self.db.execute(
                "UPDATE judgment_cache SET hit_count = hit_count + 1 WHERE cache_key = ?",
                (key,),
            )
            await self.db.commit()
            logger.debug("裁判書快取命中: key=%s", key[:30])
            return json.loads(row["data_json"])
        logger.debug("裁判書快取未命中: key=%s", key[:30])
        return None

    async def set_judgment(self, key: str, data: dict, source: str = "unknown"):
        """寫入裁判書快取（永久）"""
        exp = _expires_at(CACHE_JUDGMENT_TTL)
        await self.db.execute(
            "INSERT OR REPLACE INTO judgment_cache (cache_key, data_json, source, expires_at) VALUES (?, ?, ?, ?)",
            (key, json.dumps(data, ensure_ascii=False), source, exp),
        )
        await self.db.commit()

    # --- 法規快取 ---

    async def get_regulation(self, pcode: str, article_no: str = "") -> dict | None:
        """查詢法規快取"""
        self.db.row_factory = aiosqlite.Row
        cursor = await self.db.execute(
            "SELECT data_json, expires_at FROM regulation_cache WHERE pcode = ? AND article_no = ?",
            (pcode, article_no),
        )
        row = await cursor.fetchone()
        if row and not _is_expired(row["expires_at"]):
            return json.loads(row["data_json"])
        return None

    async def set_regulation(self, pcode: str, data: dict, article_no: str = ""):
        """寫入法規快取"""
        exp = _expires_at(CACHE_REGULATION_TTL)
        await self.db.execute(
            "INSERT OR REPLACE INTO regulation_cache (pcode, article_no, data_json, expires_at) VALUES (?, ?, ?, ?)",
            (pcode, article_no, json.dumps(data, ensure_ascii=False), exp),
        )
        await self.db.commit()

    async def invalidate_all_regulations(self) -> int:
        """清除所有 regulation_cache（pcode_all 週六更新後使用）。

        法規修法時 pcode_all.json 無法偵測（只有名稱+廢止），
        一週一次全清確保 100% 一致性，代價很小（通常幾百筆快取）。
        """
        cursor = await self.db.execute("DELETE FROM regulation_cache")
        await self.db.commit()
        count = cursor.rowcount
        if count:
            logger.info("已清除 %d 筆法規快取", count)
        return count

    async def cleanup_invalid_regulation_names(self):
        """清理法規名稱為『條文內容』等無效值的快取條目"""
        invalid_names = {"條文內容", "法規內容", "全國法規資料庫", "歷史法規"}
        cursor = await self.db.execute(
            "SELECT pcode, article_no, data_json FROM regulation_cache"
        )
        rows = await cursor.fetchall()
        invalid_keys = []
        for pcode, article_no, data_json in rows:
            try:
                data = json.loads(data_json)
                law_name = data.get("law", {}).get("name", "")
                if law_name in invalid_names:
                    invalid_keys.append((pcode, article_no))
            except json.JSONDecodeError:
                invalid_keys.append((pcode, article_no))

        for pcode, article_no in invalid_keys:
            await self.db.execute(
                "DELETE FROM regulation_cache WHERE pcode = ? AND article_no = ?",
                (pcode, article_no),
            )
        if invalid_keys:
            await self.db.commit()
            logger.info("清理了 %d 筆無效法規快取", len(invalid_keys))
