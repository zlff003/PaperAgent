"""
Entry point for python -m app.mcp.server.

Windows compatibility fixes:
- Forces UTF-8 encoding for stdin/stdout (MCP protocol requirement)
- Fixes chromadb telemetry incompatibility with newer posthog SDK
"""
import os
import sys

# Force UTF-8 for stdin/stdout — MCP protocol uses JSON-RPC over stdio
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# Fix: chromadb telemetry uses old posthog.capture() signature that crashes
# with newer posthog Python SDK
import posthog as _posthog
_posthog.disabled = True

_original_capture = _posthog.capture


def _patched_capture(*args, **kwargs):
    if _posthog.disabled:
        return
    try:
        return _original_capture(*args, **kwargs)
    except TypeError:
        if len(args) >= 2:
            props = args[2] if len(args) > 2 and isinstance(args[2], dict) else {}
            return _original_capture(event=args[1], **props)
        raise


_posthog.capture = _patched_capture

from app.mcp.server.server import main

main()
