#!/bin/bash
# Pixel Forge launcher: ./forge.sh serve | make "..." | mcp | ...
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" && exec "$DIR/.venv/bin/python" -m forge "$@"
