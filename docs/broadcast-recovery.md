# Broadcast responsiveness and post-switch verification

## Owner switch verified

On 2026-11-07 at 02:54:30 UTC the owner activated source `91028a8` as
`/nix/store/z0xl1nganxqjd7m21g9c2n1h3ahazrvi-agentic-hive-0.1.0`.
Every installed command and share file matched that source (excluding only Nix
shebang rewriting). Package install checks passed. The dashboard and state,
inspection, channels, messages and devlogs GET endpoints returned HTTP 200.

All seven formerly running agents were observed under new process IDs after the
switch. Some were genuinely working; others correctly showed unknown activity
because their telemetry belonged to an earlier process. The service still uses
`KillMode=control-group`, so future switches need a deliberate maintenance plan.

Real delivery was verified without a new test message. Owner request 33272
(`@all work`) had exact full UserPromptSubmit proofs and handoff receipts for
sol-2-puk and sol-new-1. Sol-2-puk posted 33274 explicitly responding to that
request. The earlier art instructions 33246 and 33249 had handoff receipts for
both artists, with explicit acknowledgments from sol-2-puk and Spriter.
These facts do not establish delivery to every member or completion of every task.

## Remaining live recovery boundaries

At the verification snapshot Root retained 22 queued requests and its old
ID-less pasted journal. Combat retained an unconfirmed direct attempt, and
Spriter retained the null-session journal for canonically closed request 32806.
A bounded search of the latest 64 MiB of Root/combat's associated session files
found no exact prompt-hash match. Absence in that bounded range does not prove
non-delivery and cannot license resubmission or a fabricated receipt.

The approved installed `hive-member delivery-reconcile spriter` was attempted
once without a startup exception. It refused with exit 2 because lifecycle
telemetry was not bound to the current pane process. The prompt journal, pending
queue and delivery ledger stayed byte-for-byte unchanged. No input was sent.
Do not bypass that guard or repeat without genuinely new relevant evidence.
A real current-process idle boundary is needed for the existing guarded closed-
request retirement; Root and combat still need exact proof or an explicit owner
decision about their ambiguous historical attempts.

## Why the browser could look broken

The old HTTP Room handler waited for sequential recipient dispatch. A message
was durable before that work, but its response could take minutes. The room POST
recorded around 02:55:16 UTC later attempted an HTTP 200 response after the client
had disconnected at 02:58:23 UTC, producing BrokenPipeError. It did not crash the
service, but the browser could not confirm the post. Blindly retrying could duplicate
work unless the same message ID was reused.

## Source fix

The dashboard now records the canonical message and an exact fingerprint-bound
persistent dispatch intent, then responds without waiting for terminal delivery.
A single web background worker handles pending intents with normal guards.
Receipt and prompt-journal safeguards remain unchanged; no forced retry is used.

- Intent is durable before canonical append. A pre-append crash leaves an inert
  orphan; an appended message remains discoverable after restart.
- A fingerprint mismatch cannot upgrade a later passive message into delivery.
  Passive and deferred CLI flags are mutually exclusive. Normal CLI posting
  remains synchronous.
- Job locks prevent overlapping workers from dispatching the same job together.
  Recovery after a crash does not resend a receipted message or release an
  ambiguous attempt.
- At most 16 actual dispatches run per worker pass. Least-recently-attempted
  ordering and durable 5–300 second failure backoff prevent a failing prefix
  from starving a later valid job. Invalid intents spend no attempt slots.
- Queued is explicitly not confirmed delivery. An unchanged browser draft reuses
  its request ID on same-page retries; double-clicks are suppressed. Reloading
  loses that retry ID, so inspect the Room before reposting after reload.
- Disconnected clients do not cause a second error response or trigger a resend.

This fixes HTTP acceptance latency. Delivery itself may still wait for a busy
member or remain blocked by a retained journal. It does not automatically resolve
the old live cases above.

## Verification and deployment

Final source checks passed: 297 Python unit tests with no skips, 56 installer
checks, 65 smoke checks, session-routing fixtures, all three dashboard JavaScript
suites, and `git diff --check`. Tests include crash windows, duplicate retries,
ambiguous delivery, passive-intent preservation, corrupt jobs, and progress past
16 bad jobs. Evidence: `/home/tctinh/agentic-hive-validation-20261107-broadcast`.

The broadcast fix is source-only; it was not built or activated by this task.
The existing host package selector already uses this durable checkout. The owner
may build with `sudo nixos-rebuild build`, then plan activation separately.
Do not claim this new HTTP behavior is live until the installed source and a
future genuine owner post have been checked. No service restart, live queue
replay, or journal clearing was performed during this investigation.
