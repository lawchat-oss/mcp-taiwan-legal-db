"""Lex Taiwan exposed as a Bindu A2A agent.

Bridges agno's async MCP toolkit to Bindu's sync handler contract:

- A background asyncio event loop runs in a daemon thread.
- The MCP stdio connection is opened ONCE on that loop at module load,
  so the Taiwan legal MCP server starts a single time and stays warm
  across every A2A `message/send` request.
- The sync handler that Bindu invokes hops onto the background loop
  with `run_coroutine_threadsafe(...)` and waits for the result.

Run with:
    python examples/agno-bindu/bindu_agent.py
"""

from __future__ import annotations

import asyncio
import atexit
import os
import threading
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from agno.tools.mcp import MCPTools

from agent import _mcp_server_params, agent
from prompts import AGENT_DESCRIPTION

from bindu.penguin.bindufy import bindufy


HERE = Path(__file__).parent.resolve()


# --- Background event loop ---------------------------------------------------
# Bindu's handler contract is sync, but agno + MCPTools are async-only.
# We run a dedicated asyncio loop in a daemon thread and marshal each
# handler call onto it. This also lets us keep ONE MCP connection alive
# across requests instead of fork-restarting the stdio server every time.

_loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
_mcp_tools: MCPTools | None = None
_ready = threading.Event()


def _run_loop() -> None:
    asyncio.set_event_loop(_loop)
    _loop.run_forever()


_loop_thread = threading.Thread(target=_run_loop, name="lex-taiwan-loop", daemon=True)
_loop_thread.start()


async def _open_mcp() -> None:
    global _mcp_tools
    _mcp_tools = MCPTools(server_params=_mcp_server_params())
    await _mcp_tools.connect()
    agent.tools = [_mcp_tools]


async def _close_mcp() -> None:
    if _mcp_tools is not None:
        await _mcp_tools.close()


asyncio.run_coroutine_threadsafe(_open_mcp(), _loop).result()
_ready.set()


def _shutdown() -> None:
    if not _loop.is_running():
        return
    try:
        asyncio.run_coroutine_threadsafe(_close_mcp(), _loop).result(timeout=5)
    except Exception:
        pass
    _loop.call_soon_threadsafe(_loop.stop)


atexit.register(_shutdown)


# --- Bindu handler -----------------------------------------------------------


async def _arun(content: str) -> str:
    result = await agent.arun(content)
    return result.content if hasattr(result, "content") else str(result)


def handler(messages: list[dict[str, str]]):
    """Sync Bindu handler. Hops the prompt onto the background loop."""
    if not messages:
        return (
            "Ask a Taiwan legal question — judgments (司法院裁判書), "
            "regulations (全國法規資料庫), or constitutional court "
            "interpretations (憲法法庭). I cite primary sources for every answer."
        )

    last = messages[-1]
    content = last.get("content", "") if isinstance(last, dict) else str(last)
    if not content.strip():
        return "Empty message — please send a question."

    _ready.wait(timeout=30)
    future = asyncio.run_coroutine_threadsafe(_arun(content), _loop)
    return future.result()


# --- Bindu config ------------------------------------------------------------

config = {
    "author": os.getenv("BINDU_AGENT_AUTHOR", "you@example.com"),
    "name": os.getenv("BINDU_AGENT_NAME", "lex-taiwan"),
    "description": AGENT_DESCRIPTION,
    "deployment": {
        "url": os.getenv("BINDU_AGENT_URL", "http://localhost:3773"),
        "expose": True,
        "cors_origins": ["http://localhost:5173"],
    },
    "capabilities": {"streaming": False},
}


if __name__ == "__main__":
    bindufy(config, handler)
