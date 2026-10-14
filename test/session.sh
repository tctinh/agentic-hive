#!/usr/bin/env bash
# Exercise session routing and harness flags without starting real agents.
set -euo pipefail
repo=$(readlink -f "$(dirname "$0")/..")
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
export HOME="$tmp/home" CODEX_HOME="$tmp/codex"
mkdir -p "$HOME" "$CODEX_HOME"
export HIVE_ROOT="$tmp/hive" HIVE_USER="$(id -un)" HIVE_TEST_LOG="$tmp/log"
export HIVE_TEST_AGENT_PID=$$
export HIVE_MEMBER=bob
export HIVE_TEST_HOOK="$repo/bin/hive-hook"
export HIVE_BIN_DIR="$repo/bin"
mkdir -p "$HIVE_ROOT/members/bob/state" "$HIVE_ROOT/telemetry/members" "$HIVE_ROOT/projects" "$tmp/bin" "$tmp/share"
printf '%s\n' '{"harness":"codex","settings":{"profile":"review","reasoning_effort":"high"},"dir":"'"$HIVE_ROOT/projects"'"}' >"$HIVE_ROOT/members/bob/state/launch.json"
printf '%s\n' '{"session_id":"test-session","harness":"codex"}' >"$HIVE_ROOT/telemetry/members/bob.json"
cat >"$tmp/bin/tmux" <<'EOF'
#!/usr/bin/env bash
if [[ $1 == has-session ]]; then [[ ${HIVE_TEST_LIVE:-0} == 1 ]]; exit; fi
if [[ $1 == list-panes ]]; then [[ ${HIVE_TEST_LIVE:-0} == 1 ]] && printf '%s\n' "$HIVE_TEST_AGENT_PID"; exit; fi
printf 'tmux %s\n' "$*" >>"$HIVE_TEST_LOG"
case $1 in
  load-buffer) cat >"$HIVE_ROOT/pending-prompt"; echo 0 >"$HIVE_ROOT/enters"; rm -f "$HIVE_ROOT/queued-prompt" "$HIVE_ROOT/ack-ticks" "$HIVE_ROOT/composer-empty" "$HIVE_ROOT/pasted" ;;
  paste-buffer) touch "$HIVE_ROOT/pasted" ;;
  display-message)
    if [[ -f $HIVE_ROOT/pasted && -f $HIVE_ROOT/pending-prompt && ( ${HIVE_TEST_MULTILINE:-0} == 1 || ${HIVE_TEST_WRAP:-0} == 1 ) ]]; then echo 2
    else echo 1; fi
    ;;
  capture-pane)
    if [[ ${HIVE_TEST_DIALOG:-0} == 1 ]]; then printf 'Old transcript\nPermission required\n'; exit; fi
    if [[ ! -f $HIVE_ROOT/pasted || -f $HIVE_ROOT/composer-empty ]]; then printf '› Ask Codex to do anything\n› Ask Codex to do anything\n'; exit; fi
    prefix='› '; placeholder='Ask Codex to do anything'
    case ${HIVE_TEST_PROMPT_STYLE:-codex} in
      claude) prefix='❯ '; placeholder='' ;;
      claude-nbsp) prefix=$'❯\u00a0'; placeholder='' ;;
    esac
    if [[ -f $HIVE_ROOT/queued-prompt ]]; then printf '%s%s\n%s%s\n' "$prefix" "$placeholder" "$prefix" "$placeholder"; exit; fi
    if [[ -f $HIVE_ROOT/pasted && -f $HIVE_ROOT/pending-prompt && ( ${HIVE_TEST_MULTILINE:-0} == 1 || ${HIVE_TEST_WRAP:-0} == 1 ) ]]; then
      prompt=$(cat "$HIVE_ROOT/pending-prompt")
      printf 'Previous transcript output\n'
      if [[ ${HIVE_TEST_MULTILINE:-0} == 1 ]]; then printf '%s%s\n  %s\n' "$prefix" "${prompt%%$'\n'*}" "${prompt#*$'\n'}"
      else printf '%s%s\n  %s\n' "$prefix" "${prompt:0:20}" "${prompt:20}"; fi
      exit
    fi
    printf '%s%s\n' "$prefix" "$(cat "$HIVE_ROOT/pending-prompt" 2>/dev/null || true)"
    if [[ ${HIVE_TEST_DIALOG:-0} == 1 && -f $HIVE_ROOT/pending-prompt ]]; then echo 'Permission required'
    else printf '%s%s\n' "$prefix" "$(cat "$HIVE_ROOT/pending-prompt" 2>/dev/null || true)"; fi
    ;;
  send-keys)
    n=$(cat "$HIVE_ROOT/enters"); n=$((n + 1)); echo "$n" >"$HIVE_ROOT/enters"
    if [[ ${HIVE_TEST_NO_ACK:-0} != 1 && $n -gt ${HIVE_TEST_DROP_ENTER:-0} ]]; then
      if [[ ${HIVE_TEST_ACK_DELAY_N:-0} -gt 0 ]]; then
        : >"$HIVE_ROOT/queued-prompt"
        printf '%s\n' "$(cat "$HIVE_ROOT/pending-prompt")" >>"$HIVE_ROOT/accepted-prompts"
        exit
      fi
      jq -n --rawfile prompt "$HIVE_ROOT/pending-prompt" '{prompt: $prompt}' |
        "$HIVE_TEST_HOOK" "${HIVE_TEST_HARNESS:-codex}" UserPromptSubmit >/dev/null
      printf '%s\n' "$(cat "$HIVE_ROOT/pending-prompt")" >>"$HIVE_ROOT/accepted-prompts"
      touch "$HIVE_ROOT/composer-empty"
    fi
    ;;
esac
EOF
cat >"$tmp/bin/hive-launch" <<'EOF'
#!/usr/bin/env bash
printf 'launch %s\n' "$*" >>"$HIVE_TEST_LOG"
if [[ ${HIVE_TEST_NO_ACK:-0} != 1 ]]; then
  jq -n --arg prompt "${@: -1}" '{prompt: $prompt}' | "$HIVE_TEST_HOOK" codex UserPromptSubmit >/dev/null
fi
EOF
cat >"$tmp/bin/sleep" <<'EOF'
#!/usr/bin/env bash
printf 'sleep %s\n' "$*" >>"$HIVE_TEST_LOG"
if [[ ${HIVE_TEST_ACK_DELAY_N:-0} -gt 0 && -f $HIVE_ROOT/queued-prompt ]]; then
  n=$(cat "$HIVE_ROOT/ack-ticks" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" >"$HIVE_ROOT/ack-ticks"
  if [[ $n == "$HIVE_TEST_ACK_DELAY_N" ]]; then
    jq -n --rawfile prompt "$HIVE_ROOT/pending-prompt" '{prompt: $prompt}' |
      "$HIVE_TEST_HOOK" codex UserPromptSubmit >/dev/null
  fi
fi
EOF
chmod +x "$tmp/bin/"*
export PATH="$tmp/bin:$repo/bin:$PATH"
"$repo/bin/hive" init >/dev/null
"$repo/bin/hive-member" send bob 'do the next task' >/dev/null
rg -q '^launch bob codex .* -- --profile review -c model_reasoning_effort=high resume test-session do the next task$' "$HIVE_TEST_LOG"
printf 'cold wake delivered initial prompt\n'
if HIVE_TEST_NO_ACK=1 "$repo/bin/hive-member" send bob 'a cold wake without acknowledgement' >"$tmp/out" 2>"$tmp/error"; then
  echo 'unconfirmed cold wake unexpectedly succeeded' >&2; exit 1
fi
rg -q 'unconfirmed' "$tmp/error"
printf 'unconfirmed cold wake reported\n'

mkdir -p "$HIVE_ROOT/projects/poke"
"$repo/bin/hive-member" configure bob --name cedar --team poke --dir "$HIVE_ROOT/projects/poke" >/dev/null
: >"$HIVE_TEST_LOG"
"$repo/bin/hive-member" send cedar 'continue in the saved folder' >/dev/null
rg -q '^launch bob codex '"$HIVE_ROOT"'/projects/poke -- --profile review -c model_reasoning_effort=high resume test-session continue in the saved folder$' "$HIVE_TEST_LOG"
[[ ! -d $HIVE_ROOT/members/cedar ]]
printf 'renamed member wakes its original conversation in the selected project folder\n'

: >"$HIVE_TEST_LOG"
printf '%s\n' '{"harness":"bash","dir":"/tmp"}' >"$HIVE_ROOT/members/bob/state/launch.json"
printf '%s\n' '{"pid":99999,"harness":"bash","status":"idle","session_id":"test-session"}' >"$HIVE_ROOT/telemetry/members/bob.json"
HIVE_TEST_LIVE=1 "$repo/bin/hive-member" send bob 'do live task' >/dev/null
rg -q '^tmux load-buffer -b hive-send-[0-9]+ -$' "$HIVE_TEST_LOG"
rg -q '^tmux paste-buffer -d -p -r -b hive-send-[0-9]+ -t =hive-bob:0.0$' "$HIVE_TEST_LOG"
rg -q '^tmux send-keys .* Enter$' "$HIVE_TEST_LOG"
[[ $(rg -c '^tmux send-keys' "$HIVE_TEST_LOG") == 1 ]]
printf 'live member submission confirmed by hook\n'

: >"$HIVE_TEST_LOG"
HIVE_TEST_LIVE=1 HIVE_TEST_DROP_ENTER=1 "$repo/bin/hive-member" send bob 'retry the swallowed Enter' >/dev/null
[[ $(rg -c '^tmux load-buffer' "$HIVE_TEST_LOG") == 1 && $(rg -c '^tmux send-keys' "$HIVE_TEST_LOG") == 2 ]]
printf 'swallowed Enter retried without duplicate paste\n'

: >"$HIVE_TEST_LOG"
rm -f "$HIVE_ROOT/pending-prompt"
HIVE_TEST_LIVE=1 HIVE_TEST_MULTILINE=1 "$repo/bin/hive-member" send bob $'DELIVERY_MULTILINE\nPlease submit both lines.' >/dev/null
[[ $(rg -c '^tmux load-buffer' "$HIVE_TEST_LOG") == 1 && $(rg -c '^tmux send-keys' "$HIVE_TEST_LOG") == 1 ]]
printf 'multiline prompt is submitted after paste\n'

: >"$HIVE_TEST_LOG"
rm -f "$HIVE_ROOT/pending-prompt"
HIVE_TEST_LIVE=1 HIVE_TEST_WRAP=1 "$repo/bin/hive-member" send bob 'DELIVERY_WRAP a message wrapped in a narrow terminal' >/dev/null
[[ $(rg -c '^tmux load-buffer' "$HIVE_TEST_LOG") == 1 && $(rg -c '^tmux send-keys' "$HIVE_TEST_LOG") == 1 ]]
printf 'narrow terminal prompt is submitted after paste\n'

for style in claude claude-nbsp; do
  export HIVE_TEST_PROMPT_STYLE="$style" HIVE_TEST_HARNESS=claude
  for layout in single multiline wrapped; do
    : >"$HIVE_TEST_LOG"
    rm -f "$HIVE_ROOT/pending-prompt"
    message="Claude $style prompt delivered exactly once"
    multiline=0; wrapped=0
    [[ $layout != multiline ]] || { multiline=1; message+=$'\nSecond line remains intact.'; }
    [[ $layout != wrapped ]] || wrapped=1
    HIVE_TEST_LIVE=1 HIVE_TEST_MULTILINE="$multiline" HIVE_TEST_WRAP="$wrapped" \
      "$repo/bin/hive-member" send bob "$message" >"$tmp/out"
    [[ $(rg -c '^tmux load-buffer' "$HIVE_TEST_LOG") == 1 && $(rg -c '^tmux send-keys' "$HIVE_TEST_LOG") == 1 ]]
    [[ $(cat "$HIVE_ROOT/pending-prompt") == "$message" ]]
    rg -q 'submission confirmed' "$tmp/out"
  done
  : >"$HIVE_TEST_LOG"
  if HIVE_TEST_LIVE=1 HIVE_TEST_ACK_DELAY_N=9999 "$repo/bin/hive-member" send bob 'Claude queued prompt' >"$tmp/out" 2>"$tmp/error"; then exit 1; else [[ $? == 2 ]]; fi
  [[ $(rg -c '^tmux send-keys' "$HIVE_TEST_LOG") == 1 ]]
  rg -q 'processing acknowledgment pending' "$tmp/error"
  rm -f "$HIVE_ROOT/queued-prompt" "$HIVE_ROOT/ack-ticks" "$HIVE_ROOT/pending-prompt"
  rm -f "$HIVE_ROOT/members/bob/state/prompt-delivery.json"

  : >"$HIVE_TEST_LOG"
  if HIVE_TEST_LIVE=1 HIVE_TEST_DIALOG=1 "$repo/bin/hive-member" send bob 'leave the Claude dialog alone' >"$tmp/out" 2>"$tmp/error"; then
    echo 'Claude dialog unexpectedly accepted delivery' >&2; exit 1
  fi
  if rg -q '^tmux (load-buffer|send-keys)' "$HIVE_TEST_LOG"; then echo 'typed into a Claude dialog' >&2; exit 1; fi
  printf '%s composer supports single, multiline, wrapped and queued prompts; dialog untouched\n' "$style"
done
unset HIVE_TEST_PROMPT_STYLE HIVE_TEST_HARNESS

: >"$HIVE_TEST_LOG"
if HIVE_TEST_LIVE=1 HIVE_TEST_ACK_DELAY_N=9999 "$repo/bin/hive-member" send bob 'accepted now; hook arrives later' >"$tmp/out" 2>"$tmp/error"; then exit 1; else [[ $? == 2 ]]; fi
[[ $(rg -c '^tmux load-buffer' "$HIVE_TEST_LOG") == 1 && $(rg -c '^tmux send-keys' "$HIVE_TEST_LOG") == 1 ]]
rg -q 'processing acknowledgment pending' "$tmp/error"
[[ $(cat "$HIVE_ROOT/ack-ticks") -le 41 ]]
if rg -q 'already running' "$tmp/out"; then echo 'send result includes irrelevant running state' >&2; exit 1; fi
printf 'accepted queued input awaits its hook without repasting or resubmitting\n'
rm -f "$HIVE_ROOT/queued-prompt" "$HIVE_ROOT/ack-ticks" "$HIVE_ROOT/pending-prompt"
rm -f "$HIVE_ROOT/members/bob/state/prompt-delivery.json"

: >"$HIVE_TEST_LOG"
if HIVE_TEST_LIVE=1 HIVE_TEST_NO_ACK=1 "$repo/bin/hive-member" send bob 'Enter has not been accepted' >"$tmp/out" 2>"$tmp/error"; then
  echo 'unchanged composer unexpectedly confirmed submission' >&2; exit 1
else
  [[ $? == 2 ]]
fi
[[ $(rg -c '^tmux load-buffer' "$HIVE_TEST_LOG") == 1 ]]
rg -q 'unconfirmed' "$tmp/error"
printf 'unchanged composer is unconfirmed, never successful or repasted\n'

: >"$HIVE_TEST_LOG"
rm -f "$HIVE_ROOT/pending-prompt" "$HIVE_ROOT/members/bob/state/prompt-delivery.json"
if HIVE_TEST_LIVE=1 HIVE_TEST_NO_ACK=1 HIVE_TEST_DIALOG=1 "$repo/bin/hive-member" send bob 'retry the swallowed Enter' >"$tmp/out" 2>"$tmp/error"; then
  echo 'unconfirmed submission unexpectedly succeeded' >&2; exit 1
else
  [[ $? == 3 ]]
fi
if rg -q '^tmux send-keys' "$HIVE_TEST_LOG"; then echo 'confirmed a dialog after paste' >&2; exit 1; fi
rg -q 'no ready prompt composer' "$tmp/error"
printf 'stale receipt rejected; dialog not confirmed by transcript text\n'

: >"$HIVE_TEST_LOG"
if HIVE_TEST_LIVE=1 HIVE_TEST_DIALOG=1 "$repo/bin/hive-member" send bob 'do not type into the startup dialog' >"$tmp/out" 2>"$tmp/error"; then
  echo 'startup dialog unexpectedly accepted delivery' >&2; exit 1
fi
if rg -q '^tmux (load-buffer|send-keys)' "$HIVE_TEST_LOG"; then echo 'typed into a startup dialog' >&2; exit 1; fi
rg -q 'no ready prompt composer' "$tmp/error"
printf 'startup dialog receives no paste or Enter\n'
rm -f "$HIVE_ROOT/pending-prompt"

: >"$HIVE_TEST_LOG"
: >"$HIVE_ROOT/accepted-prompts"
HIVE_TEST_LIVE=1 "$repo/bin/hive-member" send bob 'first concurrent request' >"$tmp/first" &
first=$!
HIVE_TEST_LIVE=1 "$repo/bin/hive-member" send bob 'second concurrent request' >"$tmp/second" &
second=$!
wait "$first"; wait "$second"
[[ $(wc -l <"$HIVE_ROOT/accepted-prompts") == 2 ]]
rg -qx 'first concurrent request' "$HIVE_ROOT/accepted-prompts"
rg -qx 'second concurrent request' "$HIVE_ROOT/accepted-prompts"
printf 'concurrent prompts independently submitted\n'

cat >"$tmp/bin/claude" <<'EOF'
#!/usr/bin/env bash
printf 'claude %s\n' "$*" >>"$HIVE_TEST_LOG"
EOF
cat >"$tmp/bin/codex" <<'EOF'
#!/usr/bin/env bash
printf 'codex %s\n' "$*" >>"$HIVE_TEST_LOG"
EOF
chmod +x "$tmp/bin/claude" "$tmp/bin/codex"
printf 'member instruction\n' >"$tmp/share/member-instruction.md"
HIVE_SHARE="$tmp/share" "$repo/bin/hive-launch" --run claude 'first task' </dev/null >/dev/null
HIVE_SHARE="$repo/share" "$repo/bin/hive-launch" --run codex 'first task' </dev/null >/dev/null
rg -q '^claude --permission-mode auto .*first task$' "$HIVE_TEST_LOG"
rg -q '^codex --approve-for-me --add-dir '"$HIVE_ROOT"' .*first task$' "$HIVE_TEST_LOG"
printf 'autonomous launch flags applied\n'

mkdir -p "$HIVE_ROOT/members/bob/notes" "$HIVE_ROOT/claims/demo"
printf 'keep work here\n' >"$HIVE_ROOT/members/bob/notes/work.md"
printf 'bob\n' >"$HIVE_ROOT/claims/demo/owner"
"$repo/bin/hive-member" delete bob >/dev/null
[[ ! -e $HIVE_ROOT/members/bob && ! -e $HIVE_ROOT/telemetry/members/bob.json && ! -e $HIVE_ROOT/claims/demo ]]
printf 'delete removes member session state and claims\n'
