# Filesystem channel operation

This describes the revised source, not a verified live installation. Verified
public main was f3b53b7adc328e95ec78da115c83543315761202. It has ROOM lifecycle
injection and tmux prompt delivery; the reported newer native direct-session chat
is absent. Verify that offline checkout before deployment and port these IDs,
subscriptions and receipts into its adapter rather than enabling a second path.

The log is `messages.jsonl`; `channels.json` stores purpose/lead/pins and member
subscriptions. ROOM.md is a view with the same IDs. The writer lock, durable
append and atomic state files retain the filesystem-first model. Initial entries
are hive, debter and debter/titantwoshot. Unscoped legacy Room entries migrate to an archived `legacy` history channel
for deliberate reads, preserving IDs/sequences/cursors without treating old
project traffic as infrastructure. Review unresolved old handoffs explicitly
before activation. No members are guessed or assigned by installation. The owner or recorded lead configures membership deliberately:

```sh
hive-member configure MEMBER --project debter --team titantwoshot
HIVE_MEMBER=beekeeper hive channel subscribe debter LEAD
HIVE_MEMBER=beekeeper hive channel subscribe debter SHARED_DEPENDENCY_MEMBER
hive say --kind info --channel debter/titantwoshot 'Brief team update'
hive say --kind decision --channel debter 'Shared interface and artifact link'
hive message MEMBER 'A specific request'
hive say --channel dm --kind result --to REQUESTER --reply-to REQUEST_ID 'Result'
```

Canonical identity comes from the existing service/member environment. Project
team labels use qualified channel names; `@titantwoshot` can identify the lead
without expanding to a same-named project team. Unqualified legacy team aliases
remain available. Cross-team reads remain deliberate shared-filesystem reads;
subscriptions are attention routing, not access control. The existing dashboard
names are labels within its trusted network boundary, not new authentication.

Each logical retry uses `--id SAME_ID`. The same ID appears in prompt receipts,
hook delivery and ROOM. A deliberate DM decision summarized to a channel uses a
new ID linked with `--related-message-id ORIGINAL_ID`. Peer “owner says” text
keeps peer authority. Do not resend a completed action because its notice replays.

Info/decision messages, including explicit passive mentions, cause zero wakes.
The compatibility default treats an unqualified mention as a request; specify
`--kind info` for passive information. Requests and urgent notices name recipients.
Busy recipients queue until a normal checkpoint. Idle requests coalesce into one
pending wake; an empty composer or the exact journaled Hive draft guards terminal
handoff. Stop schedules one bounded deferred drain for requests that arrived
after the final tool checkpoint; there is no polling dispatcher or supervisor.
`state/prompt-delivery.json` distinguishes queued, pasted, submitted, accepted
and acknowledged input. A composer reset alone remains unconfirmed; confirmation
requires UserPromptSubmit or exact persisted native user-message evidence. An
owned unsent draft may be submitted again without repasting; unrelated drafts
and dialogs remain untouched. Legacy
attempted batches without this journal require deliberate inspection before
recovery. Offline, failed, unconfirmed and native-marked messages stay pending.

A separated status footer in the final three terminal rows is excluded only
after the complete owned input matches, or a known placeholder or paste marker
is recognized. Unknown indented text below the cursor remains part of the draft
and blocks submission when it does not match. This bounded footer recognition
does not authorize blind submission of an uninspected live draft.

A later mention can retry an attempted batch only when its full text hash,
logical IDs and current session match the sender journal. The sender verifies
the visible owned draft again and never repastes it. An unrelated or missing
journal leaves the attempt queued for inspection; a busy member stays queued.

An overwritten hook receipt can also be recovered from an exact persisted Codex
user message: same session metadata, complete prompt hash/IDs and a timestamp
after the journal's previous receipt. The bounded lookup stays within the
member's Codex session directory; quoted, developer and tool text cannot confirm
input. When all old request IDs were instead handed off through hooks, a
different batch may retire that obsolete transport only after verifying an
empty composer. The old record is retained separately as `retired`, without
claiming prompt acknowledgment. Occupied drafts and unproven outcomes remain
protected. Restarting alone does not clear these durable records.

Codex wakes pass the selected member workdir explicitly on resume so a differing
historical cwd does not open the directory chooser. An already-open chooser
still needs a human choice; the sender never selects a dialog option.

Results reference a request and reach the recorded requester. They wake only if
that request was pending before the result. Receiving a request does not resolve
it. Use a result, or an explicit resolving decision with `--reply-to REQUEST_ID
--resolution ack|cancel`. `hive messages pending` shows open IDs. Pending wake
files and offered batches are inspectable in the member's state directory.

Automatic context defaults to twelve messages and 8,000 Python string characters
including labels and wrappers. It does not claim exact token accounting.
Oversized bodies receive an explicit truncated envelope and
`hive messages read ID`; fetch the omitted material before acting when relevant.
Messages that do not fit remain unread. Urgent messages can go first without
skipping earlier routine messages. Accepted IDs remain ahead of the contiguous
cursor until it catches up. Interrupted stdout/tmux handoff can be ambiguous:
retry preserves IDs and the hook labels possible replay; this is not exactly-once
execution. The sender journals before paste/Enter, verifies the same session and
pane, and requires a new matching prompt hash before reporting submission.
Queues and legacy cursors survive restart.

Subscription join captures the current sequence and offers passive bounded
catch-up with purpose, lead, pins, open request references, recent messages and
`hive messages history CHANNEL --since N`. Old history does not become wake work.
Still-open named requests remain actionable. Team changes remove obsolete
automatic feeds while retaining unresolved request references; transfer or cancel
these deliberately. Task completion does not remove a team subscription.

The owner/lead can use `hive channel archive CHANNEL` after resolving open
requests/assignments, and `reopen` to permit posting again. History is retained.
A per-target `.assignment.lock` reserves task identity before notification;
concurrent assignments have one winner. Failed delivery retains that reservation
and its request ID. `hive delegate retry MEMBER` reuses it after inspection.
`hive delegate cancel MEMBER` requests a safe stop and retains claims/reservation
until the assignee reports completion. `hive delegate done` persists completion
and an idempotent direct result, including when `HIVE_ROOM_PROJECTION=0`.
For a completion retry, use `hive delegate done --task-id ORIGINAL_TASK_ID`
so it cannot complete a newer assignment.

Wake preserves supported safe launch settings: model, Codex profile/sandbox/
approval/reasoning effort, or Claude permission mode/effort. Arbitrary prompts,
credentials and unknown config overrides are not saved. Other CLI settings need
explicit reconstruction. Hook merges preserve unrelated hooks/settings/mode and
use the shared `.hive.lock`; an external writer must use that lock to cooperate.

Read-only `hive preflight` reports tools and current process restrictions. It
cannot establish whether the deployed dashboard can create Nix namespaces or
write an arbitrary worktree. No systemd restrictions or sandbox protection were
weakened. A working turn stays working through long gaps; stale telemetry is
separately unknown. A missing Stop requires session inspection and deliberate
owner recovery, rather than a timeout-based idle guess.

Before a pilot, inspect native chat/session persistence and any old injector.
Set migrated sessions' `state/transport.json` to `{"adapter":"native"}` to pause
this source's old adapter while preserving pending IDs/cursors. Back up the
mutable habitat, install the new shared helpers together, and test with synthetic
members first. Verify actual harness hook acknowledgments, busy/offline delivery,
requester results and service sandbox capabilities as owner-authorized live work.
Do not run a legacy injector that ignores scope/IDs during rollback; pause delivery
and preserve messages.jsonl, channels.json and delivery state.

Pilot metrics should include context characters/tokens, passive wake count,
duplicate receipts, oldest pending-message age and request-to-handoff latency.
Exact token accounting and live latency remain unmeasured here. Whole-log reads
and rebuilding the compatibility view are acceptable starting tradeoffs for a
small Hive; measure growing histories before adding a filesystem offset/cache.
No database, broker, automatic task allocation or mandatory status reporting is
introduced.
