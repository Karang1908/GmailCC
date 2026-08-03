#!/usr/bin/env python3
"""Entry point registered with Claude Code as the `clientmail` MCP server."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from clientmail.server import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
