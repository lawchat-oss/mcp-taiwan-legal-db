"""One-shot command-line runner for the Lex Taiwan agent.

Usage:
    python examples/agno-bindu/cli.py "民法 184 條現行條文"
    python examples/agno-bindu/cli.py "Find Supreme Court cases about 預售屋 遲延交屋"

Opens the local MCP server, runs the agent against a single question,
renders the cited answer to the terminal as formatted Markdown, and exits.
"""

from __future__ import annotations

import asyncio
import sys

from agno.tools.mcp import MCPTools
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule

from agent import _mcp_server_params, agent

console = Console()
err_console = Console(stderr=True)


async def ask(question: str) -> str:
    async with MCPTools(server_params=_mcp_server_params()) as mcp_tools:
        agent.tools = [mcp_tools]
        result = await agent.arun(question)
        return result.content if hasattr(result, "content") else str(result)


def main() -> int:
    if len(sys.argv) < 2:
        err_console.print(
            "[bold red]Error:[/bold red] please pass a question as an argument.\n"
            "[dim]example:[/dim] python examples/agno-bindu/cli.py "
            '"民法第 184 條的現行條文是什麼？"'
        )
        return 2

    question = " ".join(sys.argv[1:])

    console.print(Panel.fit(question, title="Question", border_style="cyan"))
    console.print()

    try:
        with err_console.status(
            "[bold cyan]Lex Taiwan is researching…[/bold cyan]", spinner="dots"
        ):
            answer = asyncio.run(ask(question))
    except KeyboardInterrupt:
        err_console.print("[yellow]Cancelled.[/yellow]")
        return 130
    except Exception as exc:
        err_console.print(f"[bold red]Error:[/bold red] {exc}")
        return 1

    console.print(Rule(style="dim"))
    console.print(Markdown(answer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
