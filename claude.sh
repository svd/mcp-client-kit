KIT=~/src/mcp-client-kit   # adjust to your clone path

PATH="$KIT/.venv/bin:$PATH" claude --plugin-dir "$KIT" "$@"

