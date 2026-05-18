"""Lex Taiwan — the agent definition.

This module defines the agent and how it should be launched. It does not
start any server by itself. The agent is consumed in two ways:

- `cli.py` imports it for one-shot command-line questions.
- `bindu_agent.py` imports it and exposes it over the A2A protocol as a
  Bindu microservice. This is the primary entry point for the example.

The Model Context Protocol (MCP) server lives in this repository, under
`mcp_server/`. We launch it as a child process via `python -m mcp_server.server`,
so no PyPI installation of `mcp-taiwan-legal-db` is required.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.openrouter import OpenRouter
from mcp import StdioServerParameters

from prompts import AGENT_DESCRIPTION, AGENT_NAME, SYSTEM_PROMPT

HERE = Path(__file__).parent.resolve()
REPO_ROOT = HERE.parent.parent.resolve()  # examples/agno-bindu → repo root
DB_PATH = HERE / "tmp" / "lex_taiwan.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _mcp_server_params() -> StdioServerParameters:
    """Build the launch command for the local Taiwan legal MCP server.

    Uses `python -m mcp_server.server` from the repository root so this
    works against a fresh `pip install -e .` checkout without needing the
    PyPI entry point.
    """
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
        cwd=str(REPO_ROOT),
    )


def _build_model():
    """Select the language model used by the agent.

    OpenRouter is used as a single endpoint that gives access to many
    providers. The default is `anthropic/claude-sonnet-4.5`, which we have
    found to perform well on both tool use and Traditional Chinese. The
    model can be changed by setting the `BINDU_AGENT_MODEL` environment
    variable to any model identifier supported by OpenRouter.
    """
    model_id = os.getenv("BINDU_AGENT_MODEL", "anthropic/claude-sonnet-4.5")
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Add it to your .env file "
            "(see .env.example for the expected layout)."
        )
    return OpenRouter(
        id=model_id,
        api_key=api_key,
        max_tokens=int(os.getenv("BINDU_AGENT_MAX_TOKENS", "4096")),
    )


def build_agent() -> Agent:
    """Create the agent. Tools are attached when the MCP connection opens."""
    return Agent(
        name=AGENT_NAME,
        description=AGENT_DESCRIPTION,
        instructions=SYSTEM_PROMPT,
        model=_build_model(),
        db=SqliteDb(db_file=str(DB_PATH)),
        update_memory_on_run=True,
        enable_session_summaries=True,
        add_history_to_context=True,
        num_history_runs=3,
        add_datetime_to_context=True,
        markdown=True,
    )


# Module-level instance so `cli.py` and `bindu_agent.py` can import it.
agent: Agent = build_agent()
