# Hive incidents

Concrete evidence behind changes to Hive physics (SPEC §2.6, §21).

## 2026-09-30 — members start new work without announcing it

Crew: claude-opus-game, claude-opus-game-2 on debter (Claude Code).

Observed (Room generation 1–5, member transcripts):
- Both members announced their first task and later shared incidental findings
  (git sync state).
- New Beekeeper prompts ("check the Blacksmith work", "a new sync, check
  again") went straight to reading and `git` with no Room line. One member
  found a shared-code bug ("Crop care slot can never fill in a new run") and
  kept it in its own context.

Cause: the member instruction explained what the Room is but not when to use
it, and ended with "Keep coordination cheap", which reads as "stay quiet".

Change:
- member-instruction.md names three moments to post (starting, finding,
  done/blocked) and what not to post.
- hive-hook adds a one-line reminder on UserPromptSubmit only when the member
  has not posted for HIVE_QUIET_MINUTES (default 20; 0 disables).

Watch for: chatter volume rising, reminders ignored, or reminders prompting
low-value posts. Revert or tune if so.

## 2026-10-05 — WebUI mention delivery cannot allocate temporary files

Observed: Send reported `mktemp: failed to create directory via template
‘/tmp/tmp.XXXXXXXXXX’: Read-only file system`. The running dashboard unit had
`ProtectSystem=strict` and writable paths for `/srv/hive` and `/home/hive`,
but omitted `/tmp`. A mention post had already been recorded before delivery
allocated its temporary result directory, so a retry could duplicate it.

Change: allow the service to write shared `/tmp`, retaining access to existing
tmux sockets. Allocate delivery scratch before appending the Room post. A
focused regression test verifies that an unavailable temporary directory leaves
Room history and generation unchanged and sends no prompts.

## 2026-10-05 — accepted prompt reported as unconfirmed

Observed: a WebUI post to `sol-3` at 03:38:54 reported unconfirmed delivery.
The member received and answered it; its matching UserPromptSubmit receipt was
written at 03:39:14, after the roughly eleven-second confirmation window.
The informational `already running (idle)` line obscured that timeout.

Change: allow roughly 45 seconds for submission receipts, including queued
input on busy harnesses, and give WebUI subprocesses enough time for delivery
batches. Working and idle members still share the same paste/submit path;
stopped members launch with the prompt. Send results omit the running-state
line. Confirmation still requires the matching hook receipt and never repastes.
An isolated real Codex check queued a second prompt during a running tool and
confirmed its submission 31 seconds later. A delayed-receipt regression also
checks that accepted input gets one paste and one Enter.

## 2026-10-05 — waiting for processing makes accepted mentions look failed

Observed again after extending the receipt window: `sol-3` displayed the
Beekeeper's reply to Room #2706 in its transcript while Send reported unconfirmed
delivery. A busy Codex can accept input into its own queue before running the
prompt hook, so a longer receipt timeout delays the WebUI without establishing
that delivery failed.

Change: return on a matching hook receipt or two consecutive composer resets
after Hive's guarded Enter, with the harness still alive. A queued prompt is
accepted input, not evidence of processing or completed work. Live confirmation
that remains uncertain returns exit 2 and a separate `unconfirmed` report;
definite delivery errors remain failures. Retry Enter only while the submitted
text stays in the composer, never repaste it, and leave dialogs untouched.

Evidence: an isolated real Codex accepted idle input in 2621ms and busy input in
2858ms while its hook receipt remained unchanged, then answered the queued prompt
once. Regression checks cover swallowed Enter, an unchanged composer, missing
receipts, dialogs, and concurrent submissions. Production member sessions were
not changed.

## 2026-10-05 — packaged Room posting cannot find Python

Observed after activating member names and teams: WebUI Send failed at
`python3: command not found` in the packaged `hive` command. Python was a build
input and the WebUI's patched interpreter, but was absent from the shell
commands' runtime PATH. Checks inherited the host PATH and missed the omission.

Change: include Python in the package wrappers. Every package build now checks
joining, renaming, team mentions, posting, Room rendering, hooks, member status,
and the WebUI snapshot with an empty host PATH and an isolated Hive root. This
check reproduces the failure against the previous package and passes with the
fix.

## 2026-10-05 — multiline input is pasted but never submitted

Observed in an isolated real Codex session: a two-line message remained in the
editable input and delivery timed out without sending Enter. The submission
guard compared 32 characters from the message against the first visible input
line. A newline or a narrow terminal can end that line before 32 characters.

Change: compare the visible line as a literal prefix of the submitted message.
Keep the cursor-based composer check, dialog protection, matching hook receipt,
and accepted-input detection. Do not repaste or send Enter into transcript text.

Evidence: regression tests reproduce the old failure and verify one paste and
one Enter for multiline and narrow input. The real Codex session submitted and
answered multiline, long wrapped, and 28-column messages with the fix, including
input queued during a running turn. Production member sessions were unchanged.

## 2026-10-14 — blank composer rows strand pasted Codex input

Observed: the installed sender captured trimmed blank continuation rows and
rejected them before finding the active prompt. An isolated reproduction of its
exact functions pasted once, sent zero Enters, and produced no prompt receipt.
Room #9169 separately records four real members with the same trailing-blank
failure on October 12. Current live pane inspection was permission-blocked.

Change: read the full contiguous composer only for a journaled paste; blank
rows cannot license a search across dialogs or unrelated output. Match every
visible input row, refuse occupied drafts before paste, and retain hash/session/
pane/stage across interruptions. Retrying an owned draft submits it without
repasting. Accepted input stays pending until a new matching UserPromptSubmit
receipt. Explicit no-input failures can queue again. Stop triggers one bounded
deferred drain to cover a request arriving after the final tool checkpoint;
legacy attempted batches without the journal are not automatically retried.

Evidence: synthetic sender tests cover the original one-paste/zero-Enter case
now reaching one paste/one Enter/one acknowledgment, occupied/edited drafts,
blank/wrapped/narrow rows, dialogs, copy mode, delayed/stale ACK, duplicate IDs,
interrupted paste/Enter, session/pane changes and working-state races. Queue
tests include an actual Stop hook over an isolated root with a fake transport.
These checks do not establish live rollout or recovery of existing drafts.
