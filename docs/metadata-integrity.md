# Metadata integrity correction evidence

The current design is described in [delivery deadlines](delivery-deadlines.md).
The unsafe persistent index from `e8d7096` is disabled and removed; delivery reads
canonical JSONL. This document records the `d6bcf9e` regression and timing evidence.
A later review found its pending-offer migration gap; see the current
[pending-offer correction and results](offer-upgrade.md). The fresh-state results
below did not cover that conditional upgrade failure.

## Exact review regressions

The fixture seeds the exact SQLite schema and provenance of `e8d7096`, including
all original hashes and offsets. Its JSONL is unchanged during corruption.

| Corruption | Required result |
| --- | --- |
| Request row 5 recipients changed from bob to carol | Bob still receives it and retains its assigned request |
| Unselected row 999 changed into bob's acknowledgment of row 5 | Row 5 remains unresolved and deliverable |
| Same false acknowledgment after row 5 was accepted | Accepted receipt survives, including after cache removal, without replay |

All three failed when run against the reviewed indexed source. The initial failure
log is `../integrity-reproduced-before.log`. They pass on the corrected source.
`test/metadata_integrity.py` also verifies configuration, old API arguments and
snapshots, stale context stamps, interrupted acceptance and wrapper diagnostics.

## Final timing matrix

| Event / condition / members | Handoffs committed | Median: older requests / append | Maximum: older requests / append |
| --- | ---: | ---: | ---: |
| PostToolUse / warm / 8 | 80/80 | 1025.13 / 1150.65 ms | 1377.83 / 1422.80 ms |
| PostToolUse / cold / 8 | 80/80 | 1145.08 / 1212.90 ms | 1337.97 / 1500.82 ms |
| UserPromptSubmit plain / cold / 8 | 80/80 | 1589.27 / 1665.93 ms | 2057.59 / 2063.76 ms |
| UserPromptSubmit transport / cold / 8 | 80/80 | 2424.89 / 2806.64 ms | 2724.84 / 3151.30 ms |
| UserPromptSubmit transport / warm / 8 | 80/80 | 2030.91 / 2070.61 ms | 2377.04 / 2532.08 ms |
| UserPromptSubmit plain / warm / 1 | 10/10 | 726.35 / 753.94 ms | 788.21 / 772.27 ms |
| UserPromptSubmit transport / warm / 1 | 10/10 | 1102.40 / 1068.33 ms | 1244.08 / 1103.52 ms |

All **420/420 first wrapper invocations** committed their offered IDs, and all
420 subsequent wrapper checkpoints had no replay or helper failure. There were
no retained offers or untracked concurrent requests. The maximum whole-wrapper
time was **3,151.30 ms** for a two-phase transport prompt. PostToolUse peaked at
**1,500.82 ms**. These are whole-wrapper timings; each message helper retains
its own 3-second bound. Concurrent suites ran during the main PostToolUse and
plain-prompt matrix. No persistent metadata cache was enabled.

Raw JSON filenames (in the parent workspace directory):

- `integrity-final-wrapper-warm-eight.json`
- `integrity-final-wrapper-cold-eight.json`
- `integrity-final-userprompt-plain-cold-eight.json`
- `integrity-final-userprompt-transport-cold-eight.json`
- `integrity-final-userprompt-transport-warm-eight.json`
- `integrity-final-userprompt-plain-warm-single.json`
- `integrity-final-userprompt-transport-warm-single.json`

An early transport probe receipted the fixture's last unread record, yielding
four legitimate empty-message outputs counted as failed handoffs by the probe.
The corrected fixture receipts an older request and preserves the last unread
record for hook delivery. The earlier data remains in
`integrity-probe-transport-last-id-cold-eight.json`; it had no helper errors or
retained offers. The corrected matrix above requires actual emitted message IDs.

## Cost of disabling the index

A separate sequential comparison ran after the concurrent probes ended, using the
same actual wrapper, 3-second per-helper bound, fixture, warmed pages and five
samples per case. Both sources committed all 10 first handoffs and had no replay
at the following checkpoint. The indexed source is measured only as a performance
baseline in a temporary habitat; it remains unsafe for rollout.

| Source | Case | Median | Maximum |
| --- | --- | ---: | ---: |
| Indexed e8d7096 (known unsafe) | unreceipted requests | 455.03 ms | 496.79 ms |
| Indexed e8d7096 (known unsafe) | concurrent append before accept | 529.38 ms | 670.00 ms |
| Canonical-only correction | unreceipted requests | 590.72 ms | 621.52 ms |
| Canonical-only correction | concurrent append before accept | 591.59 ms | 631.21 ms |

The canonical-only medians added **135.69 ms (29.8%)** for older requests and
**62.21 ms (11.8%)** for append-before-accept. This small sample is a local cost
estimate, not a capacity guarantee. Raw data is in
`integrity-quiet-e8-warm-single.json` and
`integrity-quiet-canonical-warm-single.json`. The concurrent matrix above validates
the selected safe path directly; it does not depend on this comparison.

## Checks and rollout state

- 204 Python tests passed, including all four local socket/PTY tests and all
  12 metadata-integrity, cache-disable and interruption regressions.
- 56 installer checks, 65 smoke checks, the session script and three dashboard
  JavaScript scripts passed. Shell syntax and diff checks passed.
- Forward migration, original-helper rollback, restoration, native pause,
  registration deduplication and damaged-cache ignoring passed in a temporary
  habitat (`integrity-rollout-final.json`).
- Read-only adapter audit and original-change hash comparison passed
  (`integrity-transport-audit-final.json`). The live package still matches the
  original helper and uses 0.5/0.3-second bounds. No cache paths or native
  transport markers were present.
- No deployment, push, merge, service restart or active-agent interruption.
  The Nix derivation was not rebuilt on the nearly full root disk.

Ready for independent re-review. An authorized pilot still needs live adapter,
latency and usability observation; this is not production validation.

## Reproduction

Run from this repository with Python and the ordinary Hive shell dependencies:

```sh
PYTHONPATH=test python3 -m unittest config devlogs installer runtime messages prompt web delegation village metadata_integrity
python3 test/deadline_stress.py --wrapper --workers 8 --cache warm --samples 5
python3 test/deadline_stress.py --wrapper --workers 8 --cache cold --samples 5
python3 test/deadline_stress.py --wrapper --event UserPromptSubmit --workers 8 --cache cold --samples 5
python3 test/deadline_stress.py --wrapper --event UserPromptSubmit --prompt-kind transport --workers 8 --cache cold --samples 5
python3 test/deadline_stress.py --wrapper --event UserPromptSubmit --prompt-kind transport --workers 8 --cache warm --samples 5
python3 test/deadline_stress.py --wrapper --event UserPromptSubmit --workers 1 --cache warm --samples 5
python3 test/deadline_stress.py --wrapper --event UserPromptSubmit --prompt-kind transport --workers 1 --cache warm --samples 5
python3 test/village_rollout.py --baseline /home/tctinh/agentic-hive
```

The four HTTP/PTY tests require permission for temporary local sockets. All probes
use invented data in temporary habitats. The original checkout is read only.
