# Pending-offer upgrade and restart integrity

This correction follows `d6bcf9e`. A subsequent inherited mixed-path replay is
covered by the [confirmed-dispatch correction](mixed-delivery.md); its validation
is current. Disabling the persistent metadata index fixed
fresh delivery but did not make previously saved offers trustworthy. This is an
isolated source change; no production deployment occurred.

## Reproduction and correction

The exact reproduction runs the actual `e8d7096` message helper against invented
canonical JSONL. Cached row 999 is changed into an acknowledgment of request
`row-5`, without changing the JSONL. With cursor 4, the old helper prepares an
offer that puts `row-5` in `ignored` and omits its envelope. It is not accepted.

When that habitat was resumed through the `d6bcf9e` wrapper, the old offer was
reused. Acceptance added the unseen `row-5` to `request_receipts`. The before-fix
log, `offer-upgrade-before-d6.log`, records emitted IDs `row-6` through `row-16`
and a receipt for `row-5`. The prior migration rehearsal missed pending offers
produced by the faulty indexed helper; its fresh-state passes did not cover this.

New offers now have `version:1`. A missing, obsolete or unknown version causes
`prepare` to rebuild the batch from canonical history without advancing cursors
or receipts. Direct `accept` of an old offer is rejected before ledger mutation.
Current-version retained offers recheck every selected and ignored ID against
canonical delivery eligibility before reuse. The context-stamp prefix advances
to `canonical-jsonl-v2`, forcing a first check after either previous helper.

Skipped IDs and accepted IDs are now separate inputs to cursor advancement.
**An ignored request never becomes a request receipt.** Acceptance also rechecks
skipped rows against current canonical records and subscriptions; a row that has
become deliverable is left pending, including its queued wake. This protects
against a stale exclusion even independently of offer versioning. Existing
confirmed receipts survive rebuilding.

Rebuilding an older batch may select additional missing work and may replay
previously emitted messages. Message IDs remain stable. The replay warning does
not promise identical batch selection across an upgrade. Once a current-version
batch is saved, unchanged restart retries reuse that batch and its IDs. Handoff
remains at least once; receipts do not establish task completion.

## Exact migration and interruption checks

`test/offer_upgrade.py --baseline ../integrity-baseline-e8` runs the real old helper
in a separate process, then the actual new wrapper with its 3-second internal
allowance. The original checkout and production habitat are never test targets.
The script checks four scenarios:

1. Upgrade a prepared, unaccepted poisoned offer.
2. Exit before saving the rebuilt offer: the old ledger remains intact.
3. Save the rebuilt offer, then exit before stdout: no receipt is recorded.
4. Emit the rebuilt offer, then exit before acceptance: no receipt is recorded.

Each restart must emit the real `row-5` before recording its receipt. Scenarios
3 and 4 must reuse the saved canonical batch IDs. A subsequent actual wrapper
checkpoint must preserve the accepted receipt without replaying that request.
Canonical log bytes remain unchanged, and failure markers report unknown receipt
status until successful retry clears them. `offer-upgrade-rehearsal-final.json`
contains the results and synthetic wrapper timings.

Six additional unit regressions cover missing/future offer versions, direct
acceptance rejection, bad current exclusions, wake preservation, interrupted
atomic-save entry and preservation of another previously accepted request. The
existing fast-stamp test covers both unversioned and `canonical-jsonl-v1` stamps.

## Load and compatibility results

| Event / state / pages | First handoffs | Median: old requests / append | Maximum: old requests / append |
| --- | ---: | ---: | ---: |
| PostToolUse / fresh / warm | 80/80 | 899.64 / 1122.12 ms | 1092.93 / 1334.23 ms |
| PostToolUse / fresh / cold | 80/80 | 1071.87 / 1140.23 ms | 1279.39 / 1307.04 ms |
| PostToolUse / upgrade / warm | 48/48 | 1027.77 / 1100.07 ms | 1268.45 / 1488.35 ms |
| PostToolUse / upgrade / cold | 80/80 | 1011.07 / 1108.26 ms | 1130.32 / 1275.28 ms |
| UserPromptSubmit plain / upgrade / cold | 48/48 | 1416.57 / 1445.49 ms | 1681.03 / 1708.36 ms |
| UserPromptSubmit transport / upgrade / cold | 80/80 | 2042.97 / 1925.92 ms | 2408.10 / 2172.80 ms |

All **416/416 first wrapper handoffs** committed and all 416 following wrapper
checkpoints passed. There were **zero receipts for unseen requests**, helper
failures, retained offers, replays or untracked concurrent requests. All 208
concurrently appended requests remained tracked. These runs used the same
30,922-record / 22,522,274-byte fixture, eight simultaneous members and actual
internal 3-second wrapper bounds. Fresh runs used five samples per case; upgrade
warm and plain-prompt runs used three; other upgrade runs used five. Suites ran
concurrently during part of the matrix. Cold is advisory eviction of this log's
pages, not a physical cold boot.

Maximum whole-wrapper latency was **1,488.35 ms** for PostToolUse,
**1,708.36 ms** for a plain prompt and **2,408.10 ms** for a two-phase transport
prompt. The actual old-helper upgrade/restart fixtures passed all four scenarios,
with successful synthetic wrapper calls between 368.60 and 509.15 ms. These
are local observations, not universal latency limits.

Raw load JSON filenames in the parent workspace:

- `offer-load-fresh-warm-eight.json`
- `offer-load-fresh-cold-eight.json`
- `offer-load-upgrade-warm-eight.json`
- `offer-load-upgrade-cold-eight.json`
- `offer-load-plain-upgrade-cold-eight.json`
- `offer-load-transport-upgrade-cold-eight.json`

Compatibility validation:

- **210 Python tests passed**, including all four actual socket/PTY tests and
  18 metadata-integrity and pending-offer tests.
- **56 installer checks**, **65 smoke checks**, the session script and three
  dashboard JavaScript scripts passed; shell syntax and diff checks passed.
- Exact indexed-helper upgrade and all three interruption boundaries passed
  (`offer-upgrade-rehearsal-final.json`). Original-helper migration, rollback and
  restoration also passed (`offer-upgrade-rollout-final.json`).
- An initial sandboxed focused run hit two socket permission errors; the full
  suite rerun with approved temporary sockets passed all four socket/PTY tests.
- The read-only transport audit confirms the deployed helper still matches the
  original dirty checkout and retains its old 0.5/0.3-second limits. Original
  tracked/untracked change hashes are unchanged; no live cache files or native
  markers were present (`offer-upgrade-transport-audit-final.json`).

No implementation action is denied or pending. Ready for independent re-review;
production pilot and deployment remain unperformed and unapproved.


## Reproduction

```sh
PYTHONPATH=test python3 -m unittest config devlogs installer runtime messages prompt web delegation village metadata_integrity
python3 test/offer_upgrade.py --baseline ../integrity-baseline-e8
python3 test/village_rollout.py --baseline /home/tctinh/agentic-hive
python3 test/deadline_stress.py --wrapper --workers 8 --cache warm --samples 5
python3 test/deadline_stress.py --wrapper --workers 8 --cache cold --samples 5
python3 test/deadline_stress.py --wrapper --legacy-offers --workers 8 --cache warm --samples 3
python3 test/deadline_stress.py --wrapper --legacy-offers --workers 8 --cache cold --samples 5
python3 test/deadline_stress.py --wrapper --legacy-offers --event UserPromptSubmit --workers 8 --cache cold --samples 3
python3 test/deadline_stress.py --wrapper --legacy-offers --event UserPromptSubmit --prompt-kind transport --workers 8 --cache cold --samples 5
```

`integrity-baseline-e8` contains only `bin` and `share` extracted from commit
`e8d7096`. The exact migration script requires that historical source; it is not
silently substituted with a hand-built legacy offer. The load test separately
seeds unversioned offers and rejects any new request receipt not supported by
emitted IDs or explicit receipt IDs in a transport prompt. It still verifies
helper errors, retained offers, concurrent new work and follow-up replay.

## Pilot constraints

No timeout was increased. Each message helper retains a 3-second allowance;
UserPromptSubmit can run two such phases plus the 0.3-second journal helper and
other wrapper work. Ordinary prompts without delivery IDs avoid the receipt
history scan. Whole-log processing remains linear, and advisory page eviction is
not a physical cold boot. Larger logs, more than eight concurrent readers,
live-native transport, production latency and browser usability remain outside
this validation. The Nix derivation is not rebuilt on the nearly full root disk.

Roll out a coherent version only after old delivery processes have stopped at an
authorized quiet boundary. An already-running old helper still executes its old
acceptance code. Do not mix versions or roll back to the known faulty indexed
helper. An already falsely recorded or erased receipt cannot be reconstructed
from this migration alone; preserve the habitat and audit message IDs/backups
before resuming an affected installation. Independent review and an authorized
pilot remain necessary; no production, active-agent or SSD-offload changes were
made.
