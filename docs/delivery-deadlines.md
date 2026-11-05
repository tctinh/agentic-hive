# Canonical delivery, deadlines and receipt integrity

This is the current delivery design, superseding the indexed implementation in
`e8d7096` and its timing claims. It is source implementation and isolated
validation. The running service and original dirty checkout remain unchanged.

The subsequent [pending-offer correction](offer-upgrade.md) covers old saved
offers and restart boundaries, including the conditional migration failure
missed by the first cache-disable rehearsal. Its validation is current.

## Integrity correction

Independent review found that the persistent SQLite headers could change delivery
state without canonical validation. Corrupting recipients excluded a real open
request. Turning an unrelated, unselected row into an acknowledgment falsely
resolved that request. Reconciliation could then discard its accepted receipt,
allowing replay after an index rebuild. Verifying selected bodies was insufficient:
metadata used to exclude rows and resolve work was already acting as authority.

The implementation takes the explicitly permitted **cache-disable path**. No
persistent metadata index is used anywhere in delivery. `records()` decodes the
canonical `messages.jsonl` including unselected rows before eligibility, request
resolution, receipt reconciliation or cursor advancement. Old `cache=True` and
`use_cache=True` Python arguments remain accepted but cannot enable the index.
The unsafe module is removed from the source and both package asset lists.

`HIVE_DELIVERY_CACHE=off` is the only supported mode and the default. The wrapper
exports that default. A nonempty alternative fails before delivery-state mutation;
its nonzero exit becomes a durable diagnostic. `hive preflight` reports the mode,
`enabled:false`, whether it is supported and `messages.jsonl` as metadata authority.
Old index, WAL, ownership and lock files are ignored: no reads, cleanup, rebuild or
symlink following. This also preserves unrelated files at those paths.

A hook can reuse its own process-local canonical snapshot when source signatures
are unchanged. For an append it compares every prefix byte before reusing decoded
rows, then parses the suffix. A changed prefix causes a full parse. An old indexed
snapshot cannot be reused. Context stamps include `canonical-jsonl-v2` so a stamp
written by the former indexed or first cache-disable helper cannot suppress
the first check with versioned offers.
New offers carry a version. Unversioned or unknown-version offers are rebuilt
from canonical history before output and cannot be accepted directly. Skipped
IDs advance bookkeeping separately and never create request receipts. Current
offer reuse and acceptance recheck exclusions against canonical eligibility.
Confirmed tmux sends still reload current records under the member state lock,
preserving concurrently accepted hook receipts.

`test/metadata_integrity.py` seeds the exact previous SQLite schema and corrupts
only its headers, leaving canonical bytes unchanged. The three review cases first
failed against `e8d7096` and now pass. Tests also cover legacy API flags, an old
indexed snapshot, default/explicit disable mode, unsupported modes, untouched
cache files/symlinks, a stale context stamp, no-ID prompts, timeout diagnostics
and a forced exit between stdout and durable acceptance.

This prevents new cache-induced state damage. It cannot reconstruct a receipt
already erased by an earlier broken helper. If such a version was used in a
habitat, preserve state and compare original message IDs and receipt backups
before resuming; do not infer accepted work from missing metadata.

## Bounded latency and failure reporting

The old 0.5-second message-hook allowance killed some long-history helpers after
stdout but before durable acceptance. Under four simultaneous members, the prior
canonical source produced 0/40 clean exits and only 7/40 committed handoffs.
The wrapper's historical `|| true` obscured those failures.

The current source retains **3 seconds per message hook/receipt helper**, with the
same context character budget. No timeout was increased in this integrity fix.
`hook-delivery-error.json` or `receipt-delivery-error.json` records a nonzero exit
and `receipt_confirmed:null`; stderr points to that record. Successful completion
clears that phase's marker. The wrapper remains non-fatal to the harness. Its zero
exit code alone proves neither delivery nor acceptance.

UserPromptSubmit can invoke two sequential 3-second message phases, plus the
unchanged 0.3-second prompt-journal helper and shell/telemetry work. Thus 3 seconds
is not an end-to-end wrapper limit; slow phases can consume roughly 6.3 seconds
plus that other work. Ordinary human prompts without `[hive-message id=...]`
supply no receipt evidence and now return from the receipt helper without reading
history or modifying the ledger. The following hook phase reconciles obligations.
Transport prompts with IDs still validate canonical history before accepting
receipts and may then run delivery. Both paths are measured separately below.

Canonical decoding and request bookkeeping remain linear in total history. This
trades the unsafe index's single-reader speed benefit for a smaller authority
surface. There is no automatic fallback to indexed delivery. A read, parse or
lock failure leaves unaccepted work retryable and is reported by the wrapper.
The existing strict bulk parser and in-process snapshots reduce redundant work;
more history or contention can still exceed the allowance.

## Validation method and results

`test/deadline_stress.py --wrapper` invokes the actual Bash wrapper with its actual
internal 3-second timeouts and no outer 3-second shortcut. Every run starts fresh
Python processes. A test-only shim intercepts `accept` solely to append a named
request between output and acceptance; the real reader and wrapper are unchanged.

The invented fixture contains **30,922 records / 22,522,274 bytes**, with the same
kind counts as the earlier deadline reproduction. Both unreceipted older requests
and append-before-accept run against a shared log. Eight simultaneous members each
have separate delivery ledgers. Warm means OS pages warmed by a canonical read;
cold requests advisory eviction of this fixture's log pages, not a system-wide
cache drop or physical cold boot. No persistent metadata cache is used in either.

Evidence checks every helper error marker, emitted ID, durable receipt/cursor,
retained offer and new concurrent request. A second actual wrapper checkpoint
checks for replay. The probe exits nonzero on any failed handoff, helper failure,
untracked concurrent request or replay; wrapper success alone is never counted
as proof. Plain UserPromptSubmit and prompts carrying a real delivery ID exercise
the short receipt path and both history scans respectively.

The first cache-disable results are recorded in `metadata-integrity.md`; the
current pending-offer upgrade matrix and commands are in `offer-upgrade.md`.
Historical indexed timings in `e8d7096` are not safety validation for that
implementation.

## Adapter, migration and rollback

The read-only audit found the live installation still using the original hook/tmux
package, with 0.5/0.3-second message-helper allowances. Its helper hash matches the
original dirty checkout. There were no native transport markers or production
cache files. No production messages, registrations, services or sessions changed.

The temporary rehearsal loads the actual previous helper and verifies IDs,
cursors, accepted receipts, outstanding requests and prompt-journal bytes through
forward use, rollback and restoration. During a simulated pause, leave tombstones
are removed only from a backed-up copy for old-helper compatibility. Both helpers
ignore a deliberately damaged old index. Native-marked sessions remain paused and
retain queued requests; no native provider is implemented or certified. Duplicate
owned hook registrations are replaced by one configured command per event.

For an authorized rollout, back up the habitat, install the hook, shared helpers,
CLI, dashboard and instructions as one version, and verify effective registrations
to exclude competing injectors. Keep `HIVE_DELIVERY_CACHE=off`. No cache migration
is needed. Old cache files may be left untouched; source no longer ships or imports
the cache module. Do not mix old/new helpers. Follow the village tombstone cleanup
when rolling back to the original helper. Rolling back to `e8d7096` restores the
known metadata bug and is not a safe fallback.

Forced interruption still permits at-least-once handoff: exiting after stdout
leaves the offer, and retry uses the same IDs with a possible-replay warning before
committing them. This is not exactly-once task execution. Larger histories, more
than eight simultaneous readers, physical cold boot, live-native transport,
production latency and browser usability remain outside this isolated validation.
An independent re-review and an authorized quiet pilot remain the next gates.
