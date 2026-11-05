# Independent review corrections and pilot assessment

This records `7bc1845`. Re-review showed its 0.5-second timing margin was not
reliable. The subsequent cache, deadline and receipt work is documented in
[delivery-deadlines.md](delivery-deadlines.md), which supersedes the timing and
pilot assessment below.

This follows review of `1f49eb9`. Work remains in the isolated village branch;
the original dirty checkout, configured service and active members were not
changed. Nothing was deployed, pushed or merged.

## Correctness fixes

1. **Confirmed delivery merges current history.** `dispatch` now reloads records
   while holding the member state lock after confirmation, before advancing and
   reconciling fresh state. The regression posts a second request and accepts both
   through a hook while the first sender awaits confirmation. Both receipts and
   the later cursor survive; the next checkpoint does not replay the second.
2. **Explicit hive follows obey their baseline.** Only an absent subscription
   retains the implicit legacy global feed. Leave → sixteen posts → rejoin without
   a checkpoint produces bounded catch-up, no historical live message IDs, and
   then follows future messages normally. Named outstanding requests remain
   independent of this passive baseline.
3. **Choosing a seeded feed makes it explicit.** Joining an already-followed
   team feed changes `source` to `explicit`, retaining its baseline, subscription
   ID and catch-up state. A later team change preserves that choice.
4. **Notification handling and request completion are distinct.** The dashboard
   labels the old Done action **Dismiss notice**. Ordinary replies and dismissal
   leave the original request open. An unchecked resolution option appears only
   after fetching a recorded request. Selecting it sends an explicit decision
   with its request ID and `resolution:ack`; the server targets the recorded
   requester and preserves a deterministic ID for identical retries. This can
   wake a waiting requester once, just as an outstanding result would. It does
   not create a new reverse request. Unrelated peers cannot resolve it. A test
   confirms that reply/dismiss still block archive, explicit resolution permits
   archive, and retry neither appends nor wakes again. The original requester
   can also close after reading an ordinary answer using the documented CLI.

The reply UI guards asynchronous lookup identity: a late response for an earlier
or canceled draft cannot enable resolution of the currently selected request.

## Processing representative history

A read-only habitat snapshot had 30,922 messages, 23,835,300 bytes, 12,646 request
records and 12,765 unresolved recipient references under the current schema.
References are not proof of present work: the inherited answer/dismiss behavior
can leave historical requests open. No requests were automatically closed.

`python3 test/village_costs.py` generates **invented content**, with matching
record/kind counts and a 22,522,274-byte log. Its recipient has 569 open requests.
No production message bodies are copied. Each case has five unrestricted runs
and five separate runs with the actual `timeout 0.5s` helper boundary. The final
probe ran while the regression suite was also running.

The original reviewed path took about 1.03–1.04 seconds and missed every deadline
before emitting context. Repeated list membership checks are now sets. Complete
JSONL lines use one parser pass with strict physical-line boundaries and a precise
corruption fallback. Within a hook process, prepare/accept can share an ephemeral
snapshot. A changed file is reread and every cached prefix byte compared before
only its append is parsed; a changed prefix or truncation triggers full parsing.
The snapshot is never persisted or trusted instead of current receipts.

| Final case | Median helper time | Maximum | Clean deadline exits | Handoff emitted and committed |
| --- | ---: | ---: | ---: | ---: |
| Recent checkpoint | 451.72 ms | 477.88 ms | 5/5 | 5/5 |
| Unreceipted outstanding requests | 459.24 ms | 505.42 ms | 5/5 | 5/5 |
| Append between prepare and accept | 494.11 ms | 520.43 ms | 4/5 | 5/5 |

The one deadline termination occurred after stdout and receipt state committed:
there was no retained offer, and the concurrent new request remained recorded as
outstanding. This is still **limited timing margin**, not a full-fleet capacity
guarantee. Larger logs, cold storage and heavier contention may move the cutoff
earlier. Whole-log byte reads, initial parsing and ROOM projection remain linear;
the optimization adds no database or persistent index. The partial-write,
corruption, prefix rewrite/truncation and concurrent-receipt cases are regressions.

Raw before/after measurements are saved beside the repository as
`review-history-costs-before.json` and `review-history-costs-final.json`.

## Actual configured adapter: read-only findings

- The host's `hive`, `hive-hook` and `hive-member` paths resolve through
  `/run/current-system/sw/bin` to package
  `/nix/store/zzqc6vy5kv2x01gvj8l3my4h5apl6p68-agentic-hive-0.1.0`.
  `hive-web.service` uses that package and `/srv/hive`.
- The installed `hive_messages.py` hash matches the untouched original dirty
  source: `dd4867d320d976b042c256ffa151ab108261eb9e3bdb07cc2e718e9a2ded1e4b`.
  The branch was not copied there.
- Managed Claude hooks and both `/home/tctinh/.codex/hooks.json` and
  `/home/hive/.codex/hooks.json` name this hook transport. Each inspected event
  has one Hive hook command; the inspected user Claude files add no hook injector.
- No member `state/transport.json` was present in the inspected habitat, so this
  installed helper selects its default hook/tmux adapter. This establishes
  configured paths, not proof about every unregistered external process or a
  native provider's behavior.
- No native replacement exists in this branch. The temporary rehearsal verifies
  that marking native pauses delivery in **both** old and new helpers and keeps
  the queue. Native sessions must stay paused until their provider's ID/receipt
  contract is independently verified.

Competing owned hook registrations are replaced by the existing preserving merge
function, including differently located old/new Hive commands. Concurrent actual
hook processes in a synthetic nest produce one delivery under `.deliver.lock`.
These guards do not authorize running an unknown second injector. Inspect the
pilot's effective registrations and use a single coherent helper version.

## Migration and rollback rehearsal

`python3 test/village_rollout.py --baseline /home/tctinh/agentic-hive` loads the
actual previous helper read-only and operates only in a temporary habitat:

- Legacy Room migration preserves IDs/sequences and the previous cursor, while
  archived unscoped handoffs remain passive for deliberate review.
- Existing named requests, accepted receipts and a prompt journal survive forward
  use, new follows/leaves, rollback and forward restoration.
- A native-marked queued request is retained without a transport call by either
  version; selecting hooks again in the temporary nest delivers the same ID.
- During a simulated delivery pause, removing one leave tombstone from a copy of
  the registry allows the actual old helper to run. The message log and journal
  remain byte-identical; restoring the forward registry works again.
- Duplicate owned hook registrations collapse to the selected helper. Other
  harness settings are not replaced.

This was a rehearsal, not a production rollback or permission change.

## Final validation and remaining gates

- **192 Python tests passed**, using explicit unittest modules and the installed
  Markdown environment. This includes the four actual-socket tests the review
  sandbox could not verify: both devlog HTTP/media tests, WebSocket upgrade and
  terminal foreground-process resize. They ran with approved temporary local
  sockets/PTYs; no live Hive endpoint was used.
- **Three JavaScript checks passed**: channel UI, explicit resolution/race handling,
  and the pre-existing member-control regression.
- **56 installer checks**, **65 smoke checks**, the session script, syntax and
  diff checks passed. Standalone migration and cost probes ran separately.

There are no unresolved failing correctness regressions or denied implementation
actions. **This is ready for another source review and an authorized quiet,
isolated pilot; it is not approval for a busy full-habitat rollout.** Before
activation, review historical unresolved obligations, choose one verified adapter,
preserve the habitat, and install one consistent version at a safe boundary.
Observe deadline misses/replays under the actual pilot workload before expanding.
Browser visual review and actual live-native delivery remain unverified. The
original legacy global/team defaults and curator controls are still deliberate
compatibility choices, not changed by these corrections.
