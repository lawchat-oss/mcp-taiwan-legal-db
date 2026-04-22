"""共用錯誤回傳格式。

MCP 工具的失敗回傳應該有一組統一 key，讓 client 不用針對每個工具特判。

保證存在的 key：
    - success: bool（此函式永遠 False）
    - error:   str
    - timestamp: ISO 8601 str

工具專屬上下文（如 jid / query / law / suggestions）透過 **context 以 kwargs 傳入。
"""

from datetime import datetime


def error_response(message: str, **context) -> dict:
    """建構結構化錯誤回傳。

    Args:
        message: 人類可讀的錯誤訊息（會出現在 result["error"]）。
        **context: 工具專屬欄位（如 jid="...", query={...}, law={...}），
            會併入回傳 dict。

    Returns:
        dict with guaranteed keys {success=False, error, timestamp} + context.
    """
    return {
        "success": False,
        "error": message,
        "timestamp": datetime.now().isoformat(),
        **context,
    }
