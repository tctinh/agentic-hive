# Operational visibility and recovery — 2026-11-06

## What works and what remains blocked

The live service still runs the `5pj2...` package corresponding to `7330a09`.
Read-only GET probes succeeded for `/api/state` (1.72 s, about 160 KiB), channels,
messages, devlogs and project file listings. This is not a complete service outage.
These probes did not test POST actions or every file/browser interaction.

Canonical posts show sol-new-combat accepted card scope 33003 at 33005 and Spriter
read the new wardrobe direction 33004 at 33006. A later exact native user turn
also proves submission of 33009; its journal was observed acknowledged afterward.
Those journal changes were not performed by this investigation. No duplicate
message was sent to either agent, and no task-completion claim follows from a
transport receipt or acceptance statement.

The live dashboard incorrectly reused sol-new-combat's old process's idle state:
telemetry PID 2484769 versus actual live PID 3823069. That telemetry's last hook
activity was from the previous day. The current process was visibly working.
The cause of missing current-process hook events remains unverified; selected
process environment reads were permission-denied. Configuration/transcript reads
did not establish a specific hook failure. We did not change registration or
restart that agent.

Spriter's terminal send path is still guarded by its old null-session journal
for request 32806, which the owner canonically closed at 32858. Hook delivery and
outgoing Room posts can continue independently. The stopped Root has a different
legacy ID-less journal and a large queue; the closed-request retirement flow
cannot safely resolve that case. No old backlog was replayed or removed.

The `851ea8e` code was source-tested but not activated. Its default retirement
record is not understood by the old sender, so merely marking that record
`retired` would not restore the old dashboard's send button.

## Visibility available immediately

The workspace launcher can be run by the owner/operator account now:

```sh
/home/tctinh/Documents/Codex/2026-11-06/task/inspect-hive.sh
/home/tctinh/Documents/Codex/2026-11-06/task/inspect-hive.sh sol-new-combat --json
```

It uses only existing dashboard GETs and persisted files. It sends no prompt,
receipt, recovery, build, terminal key or control action. It was run against live
Hive successfully. `--all` includes offline members; `--limit` bounds recent
requests. With unavailable web data, process liveness is unknown rather than
invented. A high cursor does not manufacture a request receipt. Exact retained
submission proof is displayed separately from ledger handoff and task closure.

The packaged command is `hive inspect [member] [--all] [--json] [--no-web]`.
Its read model rejects lifecycle state belonging to another PID while preserving
long-running working state for the same PID. The candidate dashboard uses that
same PID rule, shows retained-delivery warnings, and provides read-only
`/api/inspect?member=NAME` evidence. Those dashboard changes are not live yet.

## Missing-hook confirmation

During a guarded send, the candidate can corroborate a missing hook receipt from
an exact persisted Codex user turn. It uses the existing session/path boundary,
complete prompt hash, IDs and time bound. Reads are bounded and throttled to
once per second after submit/accept. Composer reset, quoted text, another role,
old timestamp or unrelated ID is insufficient. This avoids requiring a second
attempt merely to find evidence that already exists. It establishes submission,
not that an agent read or finished the task. The behavior passed synthetic
positive/negative tests; no new live send was made to pilot it.

## Non-disruptive messaging recovery

The candidate adds an explicit owner operation:

```sh
hive-member delivery-retire-compatible spriter
```

It retains every closed-request retirement guard and strengthens process binding:
the current lifecycle PID must belong to the current tmux pane before and after
inspection. Thus another process's stale idle marker cannot license recovery.
All IDs must be known requests addressed to this member and authoritatively
closed; session/turn/journal/history must remain stable; the current identified
session must be idle and its composer empty in two stable captures. The prompt
lock is held throughout. An unrelated uncertain queued attempt or a concurrent
queue change causes refusal.

After those checks, the operation first fsyncs the immutable audit containing the
exact original journal and canonical closure evidence, then a readable
`last-retired-prompt-delivery.json` marker. Only afterward it writes `{}` to the
active `prompt-delivery.json` slot. It does not unlink evidence, create an ACK,
advance a cursor, clear a draft, clear an unknown queue or send any input. The
empty active slot means there is no active obsolete attempt; the old sender can
then use its normal fresh guarded path for genuinely new work. The new sender
still refuses an explicit replay of the retired old body.

This explicit compatibility action was rehearsed against the actual `7330a09`
sender: the old sender refused the stale null journal before recovery, then
submitted exactly one new request with one paste and one Enter afterward.
Uncertain unrelated queue, stale lifecycle PID, occupied draft, working session,
history/pane/session races and interrupted audit controls preserve refusal.

No live retirement was attempted. It requires separate owner authorization and
an ordinary idle boundary. Do not interrupt Spriter to create one. Do not resend
33004 or other already-received work as a test. Use the next genuine owner request
for any later live send pilot.

## Build and deployment boundary

Root currently has about 1 GiB available and reports 100% used. `/home/ssd` has
roughly 119 GiB free, but `/nix/store` still shares the root Btrfs pool.
Automatic approval review rejected the isolated Nix build because rebuilding was
reserved for the owner and root storage is critically low. The build never
executed. User-room authorization was checked; no new relevant approval was
found, and no retry or alternative build/deployment workaround was attempted.

The Hive account cannot read/execute this private source checkout. Therefore the
earlier raw-source recovery command is not a usable operator path. The owner can
build only the package after checking adequate space, without activating a system:

```sh
hive_candidate=$(nix-build --no-out-link -E 'with import <nixpkgs> {}; callPackage /home/tctinh/Documents/Codex/2026-11-06/task/agentic-hive/nix/package.nix {}')
"$hive_candidate/bin/hive" inspect spriter
```

Only after approving the specific live retirement, and at an identified idle
empty target, the owner can use that world-readable store package:

```sh
sudo -u hive env -u HIVE_DELIVERY_STARTUP_EVENT "$hive_candidate/bin/hive-member" delivery-status spriter
sudo -u hive env -u HIVE_DELIVERY_STARTUP_EVENT "$hive_candidate/bin/hive-member" delivery-retire-compatible spriter
```

Expected result is `status=retired`, `submission_proven=false`, original null
identity preserved in the audit/marker, and `active_journal_empty=true`.
Exit 2 means preserve state and inspect; there is no force option. This restores
the supported old-sender path without restarting the old dashboard or agents.
It does not repair Root's ID-less attempt or refresh E1's missing lifecycle hooks.
Use the candidate package's CLI for its native-confirmation and inspection fixes
until a coherent deployment is approved.

Do not casually run `nixos-rebuild switch` during active work. The actual service
has `KillMode=control-group`; Spriter, A1 and the resumed card agent are in that
service cgroup. Changing `ExecStart` can restart it and terminate those sessions.
Source editing and read-only inspection have not activated that change.

For permanent normal rebuilds, the staged `nixos/hive-package-override.nix` and
`configuration-persistent.nix` in the parent workspace add one import and select
this source. `/etc/nixos/configuration.nix` remains unchanged and still selects
the old dirty checkout on a normal rebuild. Installing the reviewed persistent
configuration and activating the new UI belong to a deliberate owner maintenance
window, separate from the non-activating package build and targeted retirement.

The final operational manifest records the source hash, validation, denied build,
live read-only observations and remaining approvals. No live journal, composer,
queue, service, process, hook configuration or host configuration was changed by
this investigation. Tests used temporary fixtures; the candidate package's Nix
install checks remain unrun because the package build was blocked.
