#!/bin/sh
# Antigravity runs this from the plugin directory. Outside a Hive member
# session, or when Hive is unavailable, answer with an empty JSON object.
if [ -n "${HIVE_MEMBER:-}" ]; then
  hook=hive-hook
  [ -n "${HIVE_BIN_DIR:-}" ] && [ -x "$HIVE_BIN_DIR/hive-hook" ] && hook="$HIVE_BIN_DIR/hive-hook"
  out=$("$hook" antigravity "$1" 2>/dev/null)
  [ -n "$out" ] && { printf '%s\n' "$out"; exit 0; }
else
  cat >/dev/null
fi
printf '{}\n'
