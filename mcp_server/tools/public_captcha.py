"""Optional local OCR for a single public query; callers bound attempts to two."""

from __future__ import annotations

import asyncio
import re

_engine = None
_lock = asyncio.Lock()


async def recognize(image: bytes) -> str:
    global _engine
    try:
        import ddddocr
    except ImportError as exc:
        raise RuntimeError("此公開來源需要本機 OCR，請安裝 mcp-taiwan-legal-db[captcha]") from exc
    async with _lock:
        if _engine is None:
            _engine = await asyncio.to_thread(ddddocr.DdddOcr, show_ad=False)
        code = await asyncio.to_thread(_engine.classification, image)
    if not re.fullmatch(r"[A-Za-z0-9]{4,8}", code):
        raise RuntimeError("圖形驗證碼辨識結果格式不符，未執行查詢")
    return code
