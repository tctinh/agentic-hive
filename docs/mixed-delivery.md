# Confirmed dispatch and retained hook offers

This fixes the inherited mixed-path replay in `c43cfc6`, without changing the
village model, canonical-only metadata policy or delivery deadlines. Source work
and validation are isolated; production and active agents are untouched.

## Reproduction and behavior

The regression persists a current hook offer for a request, then dispatches that
request through the real `hive-member` and guarded `hive_prompt.py` sender. A
synthetic tmux terminal supplies an exact prompt-hash/session acknowledgment
without running the separate message callback. Dispatch records the confirmed
request receipt. Previously it left the saved hook offer intact, and offer reuse
checked channel eligibility without checking whether the ID was already handed
off. The next actual `hive-hook` emitted the same request again. The before-fix
failure is recorded in `mixed-path-before.log`.

Confirmed dispatch now retires a saved offer only if its selected IDs overlap
confirmed IDs, under the same member state lock used to merge current receipts.
Explicit callback receipts use the same invalidation helper. Clearing an offer
accepts none of its other messages or channel catch-ups; the next hook rebuilds
remaining work from canonical history and the current ledger. Nonoverlapping
offers remain reusable, and unconfirmed or failed sends leave offers intact.

Offer reuse and fresh selection share a `needs_context` predicate. Accepted
request receipts take precedence, while cursor and seen IDs suppress other
confirmed messages. An unreceived open assignment remains deliverable even if
its sequence is behind the cursor. This also handles a saved offer left by the
previous version after dispatch already confirmed its request. Cursor advancement
uses existing request receipts as handoff evidence if cursor/seen bookkeeping
lags; it does not create receipts for skipped or unseen requests. Wake retirement,
dispatch and drain recognize those receipts consistently.

No offer schema change or migration script is required. Current version-1 offers
are revalidated on restart. Unversioned indexed offers still undergo the prior
canonical rebuild and cannot be accepted directly. Accepted requests remain open
assignments until explicit task resolution; delivery receipt is not completion.

## Regression coverage

`test/mixed_delivery.py` uses the real guarded sender, process/session checks,
prompt journal and actual hook wrapper. Only tmux and sleep are synthetic; it
starts no agents and sends no real messages. Its nine tests cover:

- Full confirmation without the separate callback, reproducing the review case.
- Partial overlap: only the confirmed request is skipped; the remainder arrives.
- Out-of-order confirmation with a cursor gap and seen ID.
- A previously saved confirmed receipt plus retained offer, including restart.
- Durable receipts when cursor and seen bookkeeping lag.
- An uncertain accepted terminal prompt: no receipt inferred, offer retained.
- A real callback and new request arriving while guarded dispatch waits.
- Unrelated confirmation preserving a nonoverlapping offer.
- An in-flight stale hook offer being rejected without losing its unconfirmed
  remainder after another path confirms an overlap.

A hook already prepared before confirmation may have emitted context before its
batch is invalidated. Its stale acceptance is rejected and unconfirmed remaining
IDs can replay; output already in flight cannot be retracted. Genuinely uncertain
handoffs retain the possible-replay warning and stable message IDs. This remains
at-least-once handoff, not exactly-once execution.

## Validation results

| Event / state / pages | First handoffs | Median: old requests / append | Maximum: old requests / append |
| --- | ---: | ---: | ---: |
| PostToolUse / fresh / warm | 48/48 | 1003.47 / 1076.95 ms | 1203.15 / 1236.63 ms |
| PostToolUse / fresh / cold | 48/48 | 1070.78 / 1165.80 ms | 1158.33 / 1512.24 ms |
| PostToolUse / upgrade / cold | 48/48 | 1092.11 / 1155.76 ms | 1328.71 / 1281.06 ms |
| UserPromptSubmit plain / upgrade / cold | 48/48 | 1455.39 / 1544.07 ms | 1739.54 / 1696.42 ms |
| UserPromptSubmit transport / upgrade / cold | 48/48 | 2362.03 / 2511.61 ms | 3129.15 / 3026.27 ms |

The representative 30,922-record fixture passed **240/240 first handoffs and
240/240 follow-up wrapper checkpoints** with no unseen-request receipts, lost
handoffs, retained offers, helper failures or replay. All 120 concurrent new
requests remained tracked. There were eight simultaneous members and three
samples per case. Cold means advisory eviction of the fixture's log pages, not a
physical cold boot. The regression suite ran concurrently during part of the
matrix. Peak whole-wrapper times were **1,512.24 ms** for PostToolUse,
**1,739.54 ms** for plain UserPromptSubmit and **3,129.15 ms** for a two-phase
transport prompt. Each helper retained its actual internal 3-second allowance.

Raw JSON filenames in the parent workspace directory:

- `mixed-load-fresh-warm-eight.json`
- `mixed-load-fresh-cold-eight.json`
- `mixed-load-upgrade-cold-eight.json`
- `mixed-load-plain-upgrade-cold-eight.json`
- `mixed-load-transport-upgrade-cold-eight.json`

The actual indexed-helper upgrade rehearsal still passed all four scenarios,
including interrupted save/output/acceptance boundaries, stable retry IDs and no
replay after acceptance (`mixed-path-offer-upgrade-final.json`). Original-helper
migration, rollback and forward restoration passed (`mixed-path-rollout-final.json`).
The read-only transport audit confirms the original dirty checkout, active
package, registrations and service configuration remain unchanged
(`mixed-path-transport-audit-final.json`). Live helper bounds remain 0.5/0.3 seconds.


The full suite passed **219 Python tests**, including all four actual local
socket/PTY tests, the nine new guarded-sender tests and the existing 18 metadata
and migration tests. **56 installer checks**, **65 smoke checks**, the session
script and all three dashboard JavaScript scripts passed. Shell syntax, Python
compilation and diff checks passed. No implementation action is denied or pending.
Ready for independent re-review; no pilot or production activation occurred.

## Reproduction

```sh
PYTHONPATH=test python3 -m unittest config devlogs installer runtime messages prompt web delegation village metadata_integrity mixed_delivery
python3 test/offer_upgrade.py --baseline ../integrity-baseline-e8
python3 test/village_rollout.py --baseline /home/tctinh/agentic-hive
python3 test/deadline_stress.py --wrapper --workers 8 --cache warm --samples 3
python3 test/deadline_stress.py --wrapper --workers 8 --cache cold --samples 3
python3 test/deadline_stress.py --wrapper --legacy-offers --workers 8 --cache cold --samples 3
python3 test/deadline_stress.py --wrapper --legacy-offers --event UserPromptSubmit --workers 8 --cache cold --samples 3
python3 test/deadline_stress.py --wrapper --legacy-offers --event UserPromptSubmit --prompt-kind transport --workers 8 --cache cold --samples 3
```

The deadline fixture now seeds receipts only through its starting cursor. Its
last unread checkpoint record is deliberately unreceipted, so the probe tests a
real remaining handoff rather than demanding replay of an already received
request. The canonical fixture still has 30,922 records / 22,522,274 bytes. It
checks newly created receipts against actual output and explicit prompt IDs,
plus retained offers, helper errors, concurrent new work and follow-up replay.

## Remaining pilot limits

No timeout increased: each message helper retains a 3-second allowance. Two
UserPromptSubmit phases plus the journal helper can consume 6.3 seconds in phase
allowances, plus other wrapper work. Whole-history processing remains linear;
larger histories, more than eight concurrent readers, physical cold boot,
production latency, a live-native provider and browser usability are unvalidated.
The Nix derivation remains unbuilt on the nearly full root disk. Already damaged
receipts still require an ID/backup audit. Rollout must stop old delivery
processes at an authorized quiet boundary and install one coherent version.
No deployment, push, merge, service restart or SSD-offload change occurred.
