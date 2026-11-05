# Village implementation validation

This records validation of the first implementation, `1f49eb9`. Independent
review subsequently found additional correctness issues; the fixes, additional
tests and current pilot assessment are in [the review follow-up](village-review-followup.md).

All validation used synthetic habitats, fake harness adapters or temporary local
HTTP/PTY fixtures. No live Hive member was messaged, restarted or reconfigured.
The running service was not changed. The Nix package itself was not rebuilt.

## Passing checks

- **182 Python tests**: `PYTHONPATH=test python3 -m unittest config devlogs
  installer runtime messages prompt web delegation village`. Used the existing
  Nix Python environment containing `markdown_it`. Local socket/PTY tests ran
  outside the restricted tool sandbox after automatic approval.
- **15 new village regressions** cover self-service channel policy, visitors,
  read-only discovery, explicit leave/profile sync, legacy hive opt-out, direct
  delivery and results across leave/restart, passive notices, request priority,
  late catch-up receipts after rejoining, stale canceled offers/queues,
  archive/idempotency, bounded pagination, escaped bodies, CLI compatibility,
  concurrent creation and a 24-workshop attention-isolation scenario.
- **Two new dashboard HTTP tests** cover scoped posts, passive/active delivery,
  discovery, bounded history, complete-body reads, input bounds and same-origin
  rejection. Existing dashboard and transport regressions also pass.
- **JavaScript checks**: `node test/dashboard_channels.js` compiles the shipped
  inline script and exercises actual channel functions with controlled responses:
  switching, search, older pages, out-of-order replies and preserved drafts.
  `node test/dashboard_members.js` preserves the pre-existing running/unknown
  member-control regression.
- **Portable installer:** `bash test/install.sh`, all **56 checks** passed.
- **CLI/hooks:** `bash test/smoke.sh bin`, all **65 checks** passed.
- **Lifecycle:** `bash test/session.sh`, passed cold/live wake, confirmations,
  queued input, retry without duplicate paste, multiline/wrapped composers,
  startup dialogs, concurrent prompt submission, launch flags and deletion.
- `git diff --check` and `bash -n bin/hive` passed.

The unknown `titantwoshot` identity in one old synthetic pin fixture was made
into an actual nest, matching the new requirement that channel actors are known
residents. Its curation behavior is still tested.

## Synthetic cost probe

`PYTHONDONTWRITEBYTECODE=1 python3 test/hook_costs.py /home/tctinh/agentic-hive`
compared this source with the untouched original dirty checkout. Both used
temporary habitats and 1,000 synthetic empty session logs, with 25 no-change hook
samples:

| Source | Median | p95 |
| --- | --- | --- |
| Original dirty checkout | 67.45 ms | 78.20 ms |
| Village implementation | 62.29 ms | 63.98 ms |

This is a small cached/no-change synthetic check, not evidence of a production
speedup or scaling result. Whole-message-log reads and ROOM projection remain.

## Earlier failures and their disposition

An initial generic `unittest discover -p '*.py'` run encountered missing Markdown
dependencies and sandbox-denied sockets. The existing `hook_costs.py` is also a
standalone probe taking a baseline path, not an importable unittest module; generic
discovery interpreted `discover` as that path. The final suite uses its explicit
test modules, the installed Markdown environment and approved temporary sockets.
The cost probe was run separately with its proper argument. All final checks above
passed. One misplaced assertion while adding a web test was corrected before the
final run.

## Remaining verification

No production deployment, actual native/hook pilot, browser visual review,
long-history benchmark or human usability study was performed. Human review
should confirm the retained legacy global/team defaults and legacy curator
subscription controls. Rollback must account for explicit leave tombstones as
described in [the migration notes](village.md). There is no unresolved failing
automated test and no pending or denied implementation action.

The isolated clone occupies roughly 13 MiB. The original seven modified tracked
files and untracked dashboard regression remain untouched; the original binary
diff hash matched the saved baseline after testing.
