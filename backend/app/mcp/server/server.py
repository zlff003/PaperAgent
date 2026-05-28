"""
PaperAgent MCP Server.

Exposes the paper library memory capabilities as MCP tools, resources, and prompts.

Usage:
    # stdio mode (for Claude Desktop / Claude Code)
    python -m app.mcp.server

    # SSE HTTP mode (for remote clients or PaperAgent self-consumption)
    python -m app.mcp.server --transport sse --port 8002
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


# Module-level MCP instance so tools/resources/prompts can import it for decorator registration.
# Re-created in main() for SSE mode (which needs host/port in constructor).
mcp = FastMCP("paperagent-memory")


def main() -> None:
    global mcp
    import argparse

    parser = argparse.ArgumentParser(description="PaperAgent MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="Transport mode (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8002,
        help="Port for SSE transport (default: 8002)",
    )
    args = parser.parse_args()

    # SSE mode needs host/port in the FastMCP constructor
    if args.transport == "sse":
        mcp = FastMCP("paperagent-memory", host="0.0.0.0", port=args.port)

    # Import sibling modules to register tools/resources/prompts on the mcp instance
    from app.mcp.server import tools  # noqa: E402, F401
    from app.mcp.server import resources  # noqa: E402, F401
    from app.mcp.server import prompts  # noqa: E402, F401

    if args.transport == "sse":
        mcp.run(transport="sse")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
