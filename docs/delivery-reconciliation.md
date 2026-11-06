# Delivery safety and NixOS handoff — 2026-11-06

This records the first handoff at `7330a09`. The later
[closed-journal recovery follow-up](obsolete-delivery-recovery.md) supersedes its
recovery and rebuild instructions. Package hashes and validation totals below
are historical; Codex has not rebuilt or activated the follow-up source.

The local branch `fix/delivery-reconciliation` includes Agent Village through
`24794002d6804be63ff9b5730ae4f56c8871719b` and the delivery fixes described here.
It is staged in an independent checkout. It has not been merged into the original
`main`, pushed, activated, or piloted with live agents.

## What changed

- A terminal paste requires a nonempty string session identity and a pane ID.
  Session checks run before paste, after composer inspection and before Enter.
  `SessionStart` without an identity clears the old identity rather than borrowing
  it from a previous conversation.
- Cold wake launches the harness without task text, waits for a new, identified
  `SessionStart`, then uses the same journaled sender as a live session. The
  startup exception to working-state gating is bound to that exact event; later
  turn activity revokes it. A missing startup identity or a harness that dies
  before input yields an explicit no-input result, allowing a later safe attempt.
- New journals have a version, attempt UUID and creation timestamp. Confirmation
  requires a session-bound hook receipt or an exact journal/native proof. The old
  text receipt remains for compatibility. Version-1 proof bindings remain valid;
  version-2 attempts cannot use the unbound legacy text slot alone.
- ID-less direct prompts get the same journal and durable proof protection. A
  bounded native search can confirm a new ID-less attempt only with its exact
  body hash, session and creation timestamp. A legacy ID-less journal without an
  attempt timestamp remains uncertain even if similar text appears in history.
  A newly confirmed retry returns without pasting again. A subsequent explicit
  direct send after acknowledgment can intentionally repeat the same text.
- Journal transitions and replacements preserve prior records and available
  confirmation evidence in separate `state/delivery-audit/<uuid>.json` records.
  No prompt body is added to the journal. Existing queue text stays where it was.
- Closing an assignment no longer removes its transport receipt. This preserves
  evidence for an old journal after task completion/cancellation. It does not
  reconstruct receipts that earlier versions already deleted.
- A dispatch timeout is reported as `unconfirmed`, with the exact queue batch and
  attempt latch retained. `state/delivery-outcome.json` retains its category and
  diagnostic for later inspection. No deadline was increased.
- The inherited Village fixes continue to revalidate resolved/stale offers from
  canonical history and retire overlaps after confirmed dispatch. They retain
  unconfirmed work and distinguish message receipt from task completion.

## Evidence inspection and reconciliation

```sh
sudo -u hive /run/current-system/sw/bin/hive-member delivery-status MEMBER
sudo -u hive /run/current-system/sw/bin/hive-member delivery-reconcile MEMBER
```

`delivery-status` reads files and, where applicable, a bounded Codex transcript;
it does not run tmux, write a receipt, advance a cursor or create a queue.
`delivery-reconcile` acquires the sender's prompt lock and marks an existing
journal acknowledged only when exact submission evidence already exists. It
archives the original and evidence first. It sends no terminal input and does
not manufacture message receipts or task completion. Exit 2 means inspection is
required; preserve the journal and draft. A hook handoff is reported separately
and cannot stand in for a prompt-submission acknowledgment.

Native searches retain the existing 64 MiB tail and 2 MiB record bounds; ID-based
search checks up to 80 candidate hits, and ID-less search up to 20,000 complete
records. Absence of evidence within those bounds does not establish non-delivery.
Clock timestamps are evidence bounds, not a guarantee of exactly-once execution.
Transport remains at-least-once handoff, with conservative refusal on ambiguity.

## Verified historical blockers

Read-only copies of the existing journal, queue, delivery state, telemetry,
receipt and transcript-cache metadata are preserved outside Git under
`../../evidence/live-delivery-snapshot/`. The original files were not rewritten.

| Member | Preserved journal | Finding |
| --- | --- | --- |
| `spriter` | `pasted`, session `null`, pane `%57`, request `c246abef-03d6-44f6-97fb-862ef31f9898`, hash `3246b153c1c268d7fbcdd51066018b745da43a7826f3a48a19ce2b8bfc3f9e0e` | Current session differs from the absent journal identity; no matching submission proof or recorded request handoff. No automatic identity binding. |
| `codex-sol-6-game-1` (Root) | `pasted`, IDs `[]`, session `01a0f395-1211-7993-b3f4-c0cf468532a4`, pane `%49`, hash `574115cf1962aeb89256dd4a598253d0d07afa3cde4a6ad2530d68d84eb0cf95` | Last receipt still differs from the journal hash; the legacy journal has no attempt timestamp. No safe inference from an ID or native-history text. |

These observations do not prove the cause was a timeout, that either prompt was
submitted, or that a displayed draft still belongs to this attempt. The source
patch does not repair those historical records by guessing.

After rebuilding, the owner can inspect these two members without sending input:

```sh
sudo -u hive /run/current-system/sw/bin/hive-member delivery-status spriter
sudo -u hive /run/current-system/sw/bin/hive-member delivery-status codex-sol-6-game-1
sudo -u hive tmux capture-pane -p -t '=hive-spriter:0.0'
sudo -u hive tmux capture-pane -p -t '=hive-codex-sol-6-game-1:0.0'
sudo -u hive /run/current-system/sw/bin/hive messages read c246abef-03d6-44f6-97fb-862ef31f9898
```

Inspect/expand a collapsed draft through the UI before judging its contents.
Compare the complete draft and exact session history with the preserved hash,
IDs and baseline receipt. Leave an unrelated or edited draft alone. If exact
proof becomes available in the normal supported formats, use
`delivery-reconcile` and do not resend that confirmed attempt. A null identity
or a legacy ID-less attempt without an exact bound proof requires an explicit
owner recovery decision after inspection: either a reviewed manual submission
of the retained intended draft, or deliberate cancellation/quarantine at a quiet
boundary. Preserve the original evidence in either case. There is intentionally
no force/bind/clear-latch option, automatic resend, or recipe that rewrites these
ambiguous records into an acknowledgment.

## Rebuild the tested package

The current host imports `/home/tctinh/agentic-hive/nix/module.nix`. That original
checkout remains on `main` at `b734e1f`, with its seven modified tracked files and
untracked dashboard regression preserved. Village's `72293cb` already contains
an exact snapshot of those eight files; the comparison matched before work.

The prepared wrapper is:

`/home/tctinh/Documents/Codex/2026-11-06/task/nixos/hive-tested.nix`

It imports the existing `/etc/nixos/configuration.nix` and overrides only
`services.agentic-hive.package` with this tested checkout. The inherited module
has the same functional host configuration; its Village difference is a comment.
Use a quiet delivery boundary and retain a snapshot of coordination state before
the user-run activation, following the [Village migration notes](village.md).
Do not kill a waiting sender to force that boundary.

The exact user-run command is:

```sh
sudo nixos-rebuild switch -I nixos-config=/home/tctinh/Documents/Codex/2026-11-06/task/nixos/hive-tested.nix
```

The selected and independently built package is:

`/nix/store/7190zb8nnpy0x9jpjs0pzsz0nq88g87k-agentic-hive-0.1.0`

Verify activation without submitting work:

```sh
readlink -f /run/current-system/sw/bin/hive
systemctl show hive-web --property=ExecStart --value
sudo -u hive /run/current-system/sw/bin/hive-member delivery-status spriter
sudo -u hive /run/current-system/sw/bin/hive-member delivery-status codex-sol-6-game-1
```

The first two outputs should refer to the package above. Both delivery-status
reports should continue to require inspection unless new exact evidence appeared.
Do not use their absence from an assignment list as submission evidence.

Use this wrapper on subsequent rebuilds until the branch is deliberately
integrated into the normal source/configuration. A plain rebuild using the
unchanged `/etc/nixos/configuration.nix` selects the old source again. Review
Village's leave tombstones and registry migration before a rollback; a binary
rollback is not a restoration of already mutated coordination state.

## Validation

Raw logs and JSON are in `../../evidence/`. The final handoff manifest records
the source commit, package path and log checksums.

- 241 Python tests passed, including 22 new delivery safety/reconciliation tests
  and the existing real local socket/PTY fixtures over synthetic habitats.
- Eight selected new regressions fail on untouched Village `2479400`; the final
  implementation passes them. Subtests produce 11 failure reports on that baseline.
- 56 portable installer checks and 65 CLI/hook smoke checks passed.
- Session routing, cold/live delivery, swallowed Enter, multi-line/wrapped input,
  delayed acknowledgment, concurrent submissions and dialog protection are
  exercised by `test/session.sh`; it also checks pre-input harness death.
- All three shipped dashboard JavaScript checks passed.
- Original-helper forward migration, rollback and forward restoration passed in
  a temporary habitat. Four interrupted indexed-offer upgrade scenarios passed.
- Two 8-worker wrapper probes used 30,922 synthetic records / 22,522,274 bytes:
  32/32 initial handoffs and follow-ups committed without replay, unseen-request
  receipts, helper errors or lost concurrent requests. Maxima were 1,194.31 ms
  for warm PostToolUse and 2,458.75 ms for advisory-cold transport UserPromptSubmit
  with legacy offers. Each helper kept its existing 3-second allowance. These are
  one-sample-per-case synthetic probes, not a production latency guarantee.
- The isolated Nix build and empty-host-PATH install checks passed. NixOS
  evaluation of the wrapper selects the exact built output above. No system
  closure build, switch, activation, service restart or live pilot was performed.

The restricted sandbox initially prevented local sockets and access to the Nix
daemon; approved isolated test/build runs succeeded. Initial fixture and test-mock
errors were corrected. An intermediate shell run was interrupted by a source
edit during execution; the final session run uses stable source. There is no
outstanding denied action or authorization request. Historical recovery and the
live pilot remain owner actions after the rebuild.
