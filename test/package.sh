#!/usr/bin/env bash
# Verify the installed package without inheriting tools from the host PATH.
set -euo pipefail
bin=$(readlink -f "${1:?usage: test/package.sh <packaged-bin-dir>}")
hive_test_root=$(mktemp -d)
trap 'rm -rf "$hive_test_root"' EXIT

env -i PATH=/nonexistent HIVE_ROOT="$hive_test_root" HIVE_USER="$(id -un)" \
  "$BASH" --noprofile --norc -euo pipefail -s -- "$bin" <<'EOF'
bin=$1
"$bin/hive" init >/dev/null
"$bin/hive" join alice >/dev/null
"$bin/hive-member" configure alice --name cedar --team poke >/dev/null
[[ $("$bin/hive" _resolve cedar) == alice ]]

# Both mentions resolve to the author, so this exercises normal posting and
# team routing without starting a harness or contacting another session.
post=$(HIVE_MEMBER=alice "$bin/hive" say --json '@cedar @poke packaged posting works')
[[ $post == *'"ok": true'* && $post == *'"generation": 1'* ]]
room=$("$bin/hive" room --last 1)
[[ $room == *'cedar (alice) @poke'* && $room == *'packaged posting works'* ]]
status=$("$bin/hive-member" status cedar)
[[ $status == *'cedar (alice) @poke'* ]]

hook=$(HIVE_MEMBER=alice "$bin/hive-hook" claude SessionStart <<<'{"cwd":"/tmp"}')
[[ $hook == *'cedar'* && $hook == *'poke'* ]]
snapshot=$("$bin/hive-web" --dump)
[[ $snapshot == *'"name": "cedar"'* && $snapshot == *'"team": "poke"'* ]]
inspection=$("$bin/hive" inspect cedar --no-web --json)
[[ $inspection == *'"read_only": true'* && $inspection == *'"member": "alice"'* ]]
printf 'packaged commands work with an empty host PATH\n'
EOF
