#!/usr/bin/env bash
# Unprivileged end-to-end test for the portable installer (install/hive-install).
# Stages a full DESTDIR install into a throwaway tree and asserts the physics the
# host backend produced.  No root, no sudo, no systemd; writes only under mktemp.
set -euo pipefail

repo=$(readlink -f "$(dirname "$0")/..")
installer="$repo/install/hive-install"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

pass=0
ok() { pass=$((pass + 1)); printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1" >&2; exit 1; }
check() { local name="$1"; shift; if "$@"; then ok "$name"; else fail "$name"; fi; }

[[ -x $installer ]] || fail "install/hive-install is not present yet ($installer)"

user=$(id -un)
root="$tmp/root"
BINDIR="$root/usr/local/bin"
SHARE="$root/usr/local/share/agentic-hive"

install_into() { DESTDIR="$1" "$installer" --beekeeper "$user" --no-deps --port 8080 >/dev/null; }
uninstall_from() { DESTDIR="$1" "$installer" --beekeeper "$user" --no-deps --uninstall >/dev/null; }

# --- 1. dry-run plans without touching anything ---------------------------------
# DESTDIR points inside the temp tree so "created nothing" is observable: dry-run
# must not write even to a target it is allowed to write.
dry="$tmp/dry"
dry_out=$(DESTDIR="$dry" "$installer" --beekeeper "$user" --no-deps --port 8080 --dry-run)
check "dry-run prints a would- plan" grep -q 'would ' <<<"$dry_out"
dry_clean() { [[ ! -e $dry ]] || [[ -z $(find "$dry" -mindepth 1 -print -quit 2>/dev/null) ]]; }
check "dry-run creates nothing" dry_clean

# --- 2. stage the real install ---------------------------------------------------
check "install exits 0" install_into "$root"

# --- 3. commands ----------------------------------------------------------------
bin_ok() { [[ -f $BINDIR/$1 && -x $BINDIR/$1 ]] && [[ $(stat -c '%a' "$BINDIR/$1") == 755 ]]; }
for c in hive hive-hook hive-launch hive-dash hive-web hive-attach hive-statusline hive-member hive-session; do
  check "usr/local/bin/$c 0755" bin_ok "$c"
done

# --- 4. shared assets -----------------------------------------------------------
asset_ok() { [[ -f $SHARE/$1 ]] && [[ $(stat -c '%a' "$SHARE/$1") == 644 ]]; }
for a in member-instruction.md dashboard.html xterm.js xterm.css hive_members.py omp-hive.js; do
  check "share/agentic-hive/$a 0644" asset_ok "$a"
done

# --- 5. tmpfiles -----------------------------------------------------------------
tmpfiles="$root/etc/tmpfiles.d/agentic-hive.conf"
check "tmpfiles 2770" grep -q '2770' "$tmpfiles"
check "tmpfiles default group ACL" grep -q 'default:group:hive:rwX' "$tmpfiles"
check "tmpfiles default user ACL" grep -q 'default:user:hive:rwX' "$tmpfiles"

# --- 6. sudoers ------------------------------------------------------------------
sudoers="$root/etc/sudoers.d/agentic-hive"
check "sudoers mode 0440" test "$(stat -c '%a' "$sudoers")" = 440
check "sudoers lets beekeeper run as hive" grep -qF "$user ALL=(hive) NOPASSWD: ALL" "$sudoers"

# --- 7. systemd units ------------------------------------------------------------
init_unit="$root/etc/systemd/system/agentic-hive-init.service"
web_unit="$root/etc/systemd/system/hive-web.service"
no_placeholder() { ! grep -qE '@[A-Za-z_][A-Za-z0-9_]*@' "$1"; }
check "init unit has no placeholders" no_placeholder "$init_unit"
check "web unit has no placeholders" no_placeholder "$web_unit"
check "init unit sets UMask=0002" grep -q 'UMask=0002' "$init_unit"
check "web unit serves --port 8080" grep -q -- '--port 8080' "$web_unit"
check "web unit runs as User=hive" grep -q 'User=hive' "$web_unit"
check "web unit drops unused CAP_NET_BIND_SERVICE" bash -c '! grep -q CAP_NET_BIND_SERVICE "$1"' _ "$web_unit"

# --- 8. Claude Code managed settings --------------------------------------------
claude="$root/etc/claude-code/managed-settings.json"
check "managed-settings parses as JSON" bash -c 'jq -e . "$1" >/dev/null' _ "$claude"
check "managed-settings registers hive-statusline" grep -q 'hive-statusline' "$claude"
for event in SessionStart UserPromptSubmit PostToolUse Stop SessionEnd; do
  check "managed-settings hook $event" grep -q "\"$event\"" "$claude"
done
check "managed-settings uses absolute bin paths" grep -q 'usr/local/bin' "$claude"

# --- 9. shell profile -----------------------------------------------------------
profile="$root/etc/profile.d/agentic-hive.sh"
check "profile exports HIVE_SHARE" grep -q 'HIVE_SHARE' "$profile"
check "profile puts usr/local/bin on PATH" grep -qE 'PATH=.*usr/local/bin' "$profile"

# --- 10. tmux -------------------------------------------------------------------
tmuxconf="$root/etc/tmux.conf.d/agentic-hive.conf"
check "tmux history-limit 100000" grep -q 'history-limit 100000' "$tmuxconf"
check "tmux mouse on" grep -q 'mouse on' "$tmuxconf"

# --- 11. the staged commands run without a Nix wrapper ---------------------------
hive_root="$tmp/hive"
staged() { HIVE_ROOT="$hive_root" HIVE_USER="$user" HOME="$tmp/home" "$@"; }
mkdir -p "$tmp/home"
staged_init() { staged "$BINDIR/hive" init >/dev/null; }
check "staged hive init runs" staged_init
dump=$(staged "$BINDIR/hive-web" --dump)
check "staged hive-web --dump emits hive_root" bash -c 'jq -e .hive_root <<<"$1" >/dev/null' _ "$dump"
check "staged hive-web reports the staged root" test "$(jq -r .hive_root <<<"$dump")" = "$hive_root"

# --- 12. idempotent reinstall ----------------------------------------------------
check "reinstall exits 0" install_into "$root"
check "reinstall keeps a single NOPASSWD line" test "$(grep -c NOPASSWD "$sudoers")" = 1

# --- 13. uninstall removes the host hooks ----------------------------------------
root2="$tmp/root2"
check "second install exits 0" install_into "$root2"
check "uninstall exits 0" uninstall_from "$root2"
check "uninstall drops systemd units" bash -c '! ls "$1"/etc/systemd/system/*.service >/dev/null 2>&1' _ "$root2"
check "uninstall drops sudoers rule" test ! -e "$root2/etc/sudoers.d/agentic-hive"

python3 "$repo/test/installer.py"
python3 "$repo/test/config.py"
echo "all $pass checks passed"
