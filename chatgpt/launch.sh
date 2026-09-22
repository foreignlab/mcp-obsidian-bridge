#!/bin/sh
set -eu
cd /Users/foreignlab/.local/share/obsidian-chatgpt-mcp
exec /opt/homebrew/bin/uv run --offline --no-project --with mcp==1.29.0 --with requests==2.34.2 --with /Users/foreignlab/.local/share/mcp-obsidian-tls python /Users/foreignlab/.local/share/obsidian-chatgpt-mcp/gateway.py
