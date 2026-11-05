# Long-history delivery and durable receipt revision

This supersedes the timing assessment of `7bc1845`. It is source implementation
and isolated validation, not a production deployment. The running service and
the original dirty checkout remain unchanged.

## What failed

The 0.5-second helper boundary could kill a hook after stdout but before durable
acceptance. An ordinary wrapper exit hid that failure because the wrapper used
`|| true`. A retained offer then permitted the same IDs to be replayed.

The review's failures were workload-sensitive. A quiet rerun could pass, while
the same 30,922-record fixture under four simultaneous members produced:

| Previous source, 0.5-second bound | Clean exits | Committed handoffs |
| --- | ---: | ---: |
| Unreceipted requests | 0/20 | 7/20 |
| Append before accept | 0/20 | 0/20 |

The probe checks the direct helper's return code, emitted IDs, current receipts,
cursor/seen state and retained offer. It never treats the Bash wrapper's exit as
proof. All contents are invented; the fixture matches the earlier record/kind
counts and contains 22,522,274 bytes and 569 open requests for its first recipient.

## Architecture change

Delivery paths can build `.messages-cache.sqlite3`, a disposable **header and
byte-offset index** using Python's standard SQLite module. JSONL messages and
member receipt files remain canonical. There is no new service, broker, planner,
background polling loop or task authority.

- Automatic delivery fetches current-member obligations, relevant resolution
  headers and unread candidates. Older unrelated records need only ID/sequence
  bookkeeping. Initial catch-up can read the full header set.
- Bodies are fetched from the canonical log by offset only when selected for
  context. Their raw-record hash and full metadata, including authority, must
  match before emission. A mismatch invalidates the derived cache and retries
  from canonical history before anything reaches stdout.
- Source fingerprints invalidate stale indexes. A changed log prefix triggers
  a rebuild; a verified append decodes only the new suffix. Partial trailing
  records keep the last complete offset. Within one hook, unchanged snapshots
  still avoid redundant work; changed snapshots preserve newer receipt state.
- One builder coordinates cold/index refresh work. SQLite transactions and WAL
  keep partial index writes from becoming a committed view. An interrupted
  builder, damaged index, missing rows or unknown schema can fall back to JSONL.
- Read-only discovery/history do not create or modify this cache. Small logs
  bypass it. Unrelated pre-existing databases and symlinks are not adopted or
  overwritten. A private ownership marker identifies disposable cache files.
  Cache/journal permission tests preserve the canonical log's mode boundary.

The index reduces repeated body decoding and allocation. It does not make all
history work constant-time: header bookkeeping, byte-prefix verification, cold
rebuilds and the compatibility ROOM projection still scale with history.

## The deadline tradeoff

The source now gives each `hive_messages.py hook` and `receipt` helper a **3-second
bounded allowance**, replacing 0.5 and 0.3 seconds respectively. The prompt-journal
evidence helper's separate allowance is unchanged.

This is intentional: a cold index rebuild and concurrent readers cannot reliably
complete in half a second on this host. The larger bound preserves receipt
completion under tested contention; the index improves normal repeated work.
We do **not** claim that 0.5 seconds is now a reliable operating limit.

A rare cold, contended or fallback call can delay a checkpoint by up to three
seconds. UserPromptSubmit can invoke both receipt and delivery phases, so its
combined worst-case allowance is larger; normally the first phase warms the
shared index. No unbounded daemon or retry loop was added.

Helper failure is no longer silent. The hook records `hook-delivery-error.json`
or `receipt-delivery-error.json` in the member state and emits an stderr diagnostic.
The record preserves the exit code and marks receipt confirmation as **unknown**,
not successful or failed by inference. A later successful helper clears that
phase's error marker. Hooks remain non-fatal to the harness for compatibility.
Inspect actual receipts and offered IDs before deciding whether to retry work.

## Quantitative validation

`test/deadline_stress.py` launches real helper processes against a shared synthetic
log, drains every stdout concurrently, and inspects each member's durable state.
The append case writes a new named request between prepare and accept under a
test-only writer lock. Cold runs remove the index and request advisory eviction
of this fixture's log pages; they are not system-wide cache drops or a power-cycle.
Every sample starts fresh Python processes.

The final matrix used eight simultaneous members, five samples per case and the
configured 3-second bound, while the regression suite was also running. All
**160/160 calls exited cleanly, emitted context and committed their receipts**,
with no retained offers. Every concurrent new request remained tracked.

| Condition | Case | Clean exits | Committed handoffs | Median | Maximum |
| --- | --- | ---: | ---: | ---: | ---: |
| Warm index | Unreceipted requests | 40/40 | 40/40 | 677.40 ms | 1086.09 ms |
| Warm index | Append before accept | 40/40 | 40/40 | 841.90 ms | 1774.69 ms |
| Cold index/pages | Unreceipted requests | 40/40 | 40/40 | 1711.66 ms | 2570.74 ms |
| Cold index/pages | Append before accept | 40/40 | 40/40 | 2229.52 ms | 2496.44 ms |

Raw final evidence: `deadline-release-warm-eight.json` and
`deadline-release-cold-eight.json`. The measured maximum was 2,570.74 ms.

A separate single-member comparison used the **same 3-second bound** for both
sources, so the read-path benefit is distinct from increasing the allowance:

| Source | Case | Median | Maximum |
| --- | --- | ---: | ---: |
| Previous source | Unreceipted | 387.32 ms | 389.76 ms |
| Previous source | Append before accept | 472.38 ms | 509.43 ms |
| Indexed source | Unreceipted | 272.16 ms | 288.46 ms |
| Indexed source | Append before accept | 339.67 ms | 341.88 ms |


Comparison commands and raw JSON live beside this repository:

```sh
python3 test/deadline_stress.py --source ../deadline-baseline-7bc --deadline 0.5 --workers 4 --cache warm --samples 5
python3 test/deadline_stress.py --deadline 3 --workers 8 --cache warm --samples 5
python3 test/deadline_stress.py --deadline 3 --workers 8 --cache cold --samples 5
```

The baseline directory contains only `bin` and `share` from `7bc1845`, extracted
locally for comparison. The original checkout was not used as a writable test
habitat. A single-member comparison at the same 3-second bound separately
measured the benefit of the read path; small sample timings are evidence for this
fixture, not a universal capacity guarantee.

## Recovery, adapter and rollback

The configured live installation still uses the original hook/tmux package,
with its old 0.5/0.3-second helper allowances. Its helper hash matches the untouched
original dirty checkout. The read-only audit found no native markers and no
production cache files. No live messages, hook settings, service files or agent
sessions were changed.

The temporary migration rehearsal loads the actual previous helper. It verifies
IDs, cursors, accepted receipts, outstanding requests and the prompt journal
through forward use, tombstone cleanup during a simulated pause, rollback and
forward restoration. The previous helper ignores the derived index, even when
that index is damaged; the new helper can rebuild it. Both versions remain
paused when transport is explicitly native, retaining the pending request.
No native provider was added or certified.

During an authorized rollout, install the hook, message helper and new cache
module together. Portable and Nix package file lists include the module. Before
rollback, pause delivery and apply the previously documented leave-tombstone
cleanup. The derived index and its WAL/SHM/ownership/lock/invalidation files can
be removed during that pause; deleting them loses no canonical work. Do not
interpret the older helper's old deadline behavior as repaired by rollback.

Forced interruption remains distinct from ordinary deadline reliability. A test
exits immediately after stdout: the offer survives, and retry emits the **same
IDs** with a possible-replay warning before committing them. This is durable
at-least-once handoff, not exactly-once task execution. Agents must still avoid
repeating completed actions when IDs replay.

## Validation and readiness

- **206 Python regressions passed**, including all four actual-socket HTTP/PTY
  tests and 14 cache/timeout/interruption regressions.
- **57 portable installer checks**, **65 smoke checks**, and the session script
  passed; portable/Nix asset lists include `hive_logcache.py`. The Nix derivation
  itself was not rebuilt on the nearly full root disk.
- All three dashboard JavaScript checks, shell syntax and diff checks passed.
- Actual-baseline migration/rollback, native-pausing, damaged-cache restoration
  and canonical journal/receipt preservation passed in temporary habitats.
- The controlled forced-after-stdout interruption test preserved the offer and
  replayed the same IDs explicitly; it did not silently advance or lose work.


The operational deadline failure is corrected within the measured envelope.
Ready for another source review and an authorized isolated pilot using one
coherent helper version. A broad production rollout remains unperformed and
unapproved. Review historical unresolved obligations before activating in the
existing habitat, inspect effective hook registrations to exclude competing
injectors, and observe latency/replays before expanding. Larger histories,
more than eight simultaneous delivery helpers, physical cold boot, live-native
transport and browser usability remain outside this validation.
