#!/usr/bin/env bash
# Verify the installed package without inheriting tools from the host PATH.
set -euo pipefail
bin=$(readlink -f "${1:?usage: test/package.sh <packaged-bin-dir>}")
# nix's stdenv exports BASH; elsewhere fall back to whatever bash is on PATH so
# this check is runnable outside a nix build too.
bash_bin=${BASH:-$(command -v bash)}
[[ -n $bash_bin ]] || { echo "test/package.sh: no bash found" >&2; exit 1; }
hive_test_root=$(mktemp -d)
trap 'rm -rf "$hive_test_root"' EXIT

env -i PATH=/nonexistent HIVE_ROOT="$hive_test_root" HIVE_USER="$(id -un)" \
  "$bash_bin" --noprofile --norc -euo pipefail -s -- "$bin" <<'EOF'
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

# omp reaches the same adapter through the shipped extension. The extension
# itself lives in the share dir, so assert it ships and drives hive-hook.
[[ -f "${bin%/*}/share/agentic-hive/omp-hive.js" ]]
omphook=$(HIVE_MEMBER=alice "$bin/hive-hook" omp SessionStart <<<'{"cwd":"/tmp"}')
[[ $omphook == *'cedar'* && $omphook == *'poke'* ]]

snapshot=$("$bin/hive-web" --dump)
[[ $snapshot == *'"name": "cedar"'* && $snapshot == *'"team": "poke"'* ]]
printf 'packaged commands work with an empty host PATH\n'
EOF
