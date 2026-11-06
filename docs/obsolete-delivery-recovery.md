# Obsolete legacy delivery recovery — 2026-11-06 follow-up

## Verified diagnosis

The running package is
`/nix/store/5pj2sbgkizisngn2lff530isw70a0c6s-agentic-hive-0.1.0`.
Its prompt helper, message helper and member instructions match `7330a09` byte
for byte. The different store path does not mean the previous fix was absent.

- Request **32932**, `post-rebuild-continue-active-pair-20261106-1010`, has delivery
  receipts for A1 (`sol-new-1`) and Spriter.
- Request **32987**, `archive-avatar-rejected-scale-first-20261106`, now has a
  Spriter receipt and a reply at **32989**. This proves receipt and response, not
  art completion or visual approval. The queue is now empty. The user notified
  Spriter directly; this investigation sent no duplicate message.
- Spriter's unrelated old journal still says `pasted`, session `null`, pane `%57`,
  hash `3246b153c1c268d7fbcdd51066018b745da43a7826f3a48a19ce2b8bfc3f9e0e`,
  for request **32806**, `c246abef-03d6-44f6-97fb-862ef31f9898`.
- The owner explicitly closed that request at **32858**, with `resolution=ack`
  and ID `archive-prototype-delivery-ack-rejected-art-20261106-0440`. Closure does
  not prove the old terminal prompt was submitted.
- The later send for 32987 reported that another delivery/session was pending.
  Its saved outcome is `unconfirmed`; that is not evidence of a timeout. A
  read-only capture found Spriter working in pane `%0` with an empty placeholder
  composer. We did not bind the old identity to this different target.

The missing case was an obsolete transport record. The first recovery command
required exact submission proof tied to the recorded session. A null session
prevents that proof even after the owner closes the underlying request. Hooks
can therefore deliver new work while that journal still blocks a terminal send.

## New audited flow

`hive-member delivery-status MEMBER` remains read-only and never runs tmux. It
now reports `retirement_candidate` and exact canonical closure records alongside
submission evidence. A candidate is not permission to resend.

`hive-member delivery-reconcile MEMBER` can now produce a separate `retired`
outcome. It requires all journal IDs to be known canonical requests addressed to
that member and closed by an authorized actor under existing resolution rules.
A cursor, missing assignment, unrelated reply, damaged cache, unknown ID,
ID-less journal, or mixed unconfirmed batch cannot establish that proof.

Retirement also requires an identified idle current session and two stable,
empty composer captures in the same pane. A freshly launched sender can use only
its exact SessionStart event; subsequent turn activity revokes that exception.
Working/unknown sessions, occupied or edited drafts, dialogs, copy mode, missing
panes, changed identities/turns, and concurrent canonical-history/journal changes
cause refusal. No input is pasted, submitted, cleared, or interrupted.

The original journal, exact request/closure records and current inspection are
saved to a new audit record before changing the active stage. The active journal
retains its null/old session identity and becomes `retired`, explicitly keeping
submission unproven. This creates no message receipt, cursor advancement or task
completion. Reconciliation is idempotent. Exit 0 with `status=retired` means
obsolete transport was retired; it does not mean a message was sent.

The sender uses these same checks before a different new batch, then performs
its normal fresh composer/session/pane guards. Retrying the closed original body
returns a no-input result rather than an ACK. Interrupted retirement preserves
the original and/or audit for the next inspection.

A new batch blocked by a different pending journal now yields an explicit
no-input outcome, so it can safely try later. A queue drops an old latched batch
only when every request is already handed off or canonically closed; closure
never creates a receipt. Retiring another old journal cannot license an unrelated
batch's uncertain prior attempt: dispatch conveys that uncertainty and the
sender requires that batch's own matching journal. Historical unknown attempts
remain protected.

## Owner recovery

No live reconciliation was run. Spriter was working; an empty composer alone is
insufficient. Do not resend 32987: its receipt and response already exist.
After installing the new source, wait for an ordinary idle boundary, then run:

```sh
sudo -u hive /run/current-system/sw/bin/hive-member delivery-status spriter
sudo -u hive /run/current-system/sw/bin/hive-member delivery-reconcile spriter
sudo -u hive /run/current-system/sw/bin/hive-member delivery-status spriter
```

Expected safe result: `status=retired`, `submission_proven=false`, the original
null identity retained and an audit path. Exit 2/`inspection-required` means
preserve state and wait or inspect. Do not stop Spriter to make the command pass.
There is no force, bind, delete, clear-latch or blind-resend option.

The owner may instead separately authorize using the reviewed source directly,
without activating a system build:

```sh
sudo -u hive env HIVE_USER=hive HIVE_SHARE=/home/tctinh/Documents/Codex/2026-11-06/task/agentic-hive/share /home/tctinh/Documents/Codex/2026-11-06/task/agentic-hive/bin/hive-member delivery-reconcile spriter
```

That command can change the live journal only after all checks pass. It was
prepared, not executed. Root's legacy ID-less journal still lacks evidence for
automatic retirement; no IDs or timestamps are invented for it.

## Persist normal rebuilds

The normal `/etc/nixos/configuration.nix` still imports the old dirty checkout
at `/home/tctinh/agentic-hive`. Evaluation selects
`/nix/store/zzqc6vy5kv2x01gvj8l3my4h5apl6p68-agentic-hive-0.1.0`, so a normal
rebuild would revert the update. The temporary `-I nixos-config=...` wrapper did
not modify this configuration.

Two reviewed files are staged under
`/home/tctinh/Documents/Codex/2026-11-06/task/nixos/`:

- `hive-package-override.nix` selects this source without recursively importing
  the host configuration.
- `configuration-persistent.nix` copies the current host configuration and adds
  only `./hive-package-override.nix` to its imports.

The following are owner-run commands. The checksum prevents overwriting host
edits made since preparation; no installation or rebuild was performed here.

```sh
diff -u /etc/nixos/configuration.nix /home/tctinh/Documents/Codex/2026-11-06/task/nixos/configuration-persistent.nix
(
  set -eu
  test "$(sha256sum /etc/nixos/configuration.nix | cut -d' ' -f1)" = b7599e4a8f4dbf5ce060293b505ec37362205e9324f8e55826f5fbe6b5a0e3d8
  sudo cp --archive --no-clobber /etc/nixos/configuration.nix /etc/nixos/configuration.nix.before-hive-recovery-20261106
  sudo install -m 0644 /home/tctinh/Documents/Codex/2026-11-06/task/nixos/hive-package-override.nix /etc/nixos/hive-package-override.nix
  sudo install -m 0644 /home/tctinh/Documents/Codex/2026-11-06/task/nixos/configuration-persistent.nix /etc/nixos/configuration.nix
)
sudo nixos-rebuild switch
```

If the guard fails, retain the new host edits and add that single import manually
instead of copying the snapshot. Preserve the source checkout until deliberately
integrated into the usual source/configuration. After activation, compare
`readlink -f /run/current-system/sw/bin/hive` with package evaluation of the normal
`/etc/nixos/configuration.nix`: both should select the new package and not `zzqc...`.
Evaluation of the prepared module composition is recorded in
`../../evidence/followup/persistent-package-eval.log`; it does not prove a build
or activation happened.

## Validation and limits

Follow-up evidence includes preserved request/state files, before-fix
reproductions, complete tests, configuration evaluation, and a final checksum
manifest. New regressions cover closed null/changed journals, refusal controls,
draft/pane/session/history races, interrupted audit writes, idempotency, guarded
future dispatch, unrelated uncertain attempts, closed latched queues, and the
actual CLI over a read-only synthetic terminal. The manifest records final test
totals and results.

No live journal, queue, composer, hook registration or service was changed. No
system/package rebuild, activation or restart was run. Source is staged on the
integration branch; the original dirty checkout is preserved. Remaining actions
are owner-run configuration/deployment, audited retirement at an idle boundary,
and a live pilot. Art progress and visual approval remain separate from delivery.
