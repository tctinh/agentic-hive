# Owner rebuild handoff — 2026-11-07 UTC

## What changed

The existing `/etc/nixos/configuration.nix` imports
`/home/tctinh/agentic-hive/nix/module.nix`. That module's default package now
reaches the maintained local checkout through a relative import:

```nix
# /home/tctinh/agentic-hive/nix/package.nix
import ../../agentic-hive-fixed/nix/package.nix
```

The fixed source lives at `/home/tctinh/agentic-hive-fixed`. It began at
`c3bf6901ef7454a6df3316b287db18d0893272ff` and includes the subsequent inspection
regressions described in the current source diff. Both dated task source and the
original checkout remain available. Every preexisting edited file in the original
checkout was hashed before the one package-selector change and verified unchanged.
The old package definition, preexisting diff, and hashes are backed up at
`/home/tctinh/hive-source-selection-backup-20261107`.

No `/etc` file, live delivery record, pending message, service, agent process,
terminal input, or system generation was changed. No Nix build or activation was run.

## Build without activating

The owner's normal configuration now selects this source. No custom `-I` or
copying a staged `/etc` configuration is required:

```sh
sudo nixos-rebuild build
```

This builds but does not switch the running system. At inspection time root had
about 5.9 GiB available and was 98% used. Check `df -h /` before building; no disk
cleanup was performed. Source tests do not replace the package install checks
that run during this build.

## Activation is a separate maintenance step

The live dashboard still used `/nix/store/5pj2sbgkizisngn2lff530isw70a0c6s-agentic-hive-0.1.0`
from the earlier `7330a09` build. It has `KillMode=control-group`. Some running
agents belong to that service, so a plain `nixos-rebuild switch` can terminate
them. Do not switch casually during active work. Use an explicitly reviewed
member-preserving maintenance procedure, or wait until the agents are stopped.
No older hard-coded activation script should be assumed to select this new build.

After owner activation, verify the actual service rather than relying on a
successful source test or rebuild exit code:

```sh
systemctl show hive-web --property=ExecStart,MainPID,ActiveState,KillMode
curl -fsS http://127.0.0.1/api/state | python3 -c 'import json,sys; print(json.load(sys.stdin).get("runtime", "old dashboard: no runtime identity"))'
curl -fsS http://127.0.0.1/api/inspect?member=spriter
```

The new runtime reports the executable and share paths. `/api/inspect` is read-only.
Use the next genuine owner request, not a duplicate historic task, to confirm a
normal message reaches an agent. A transport receipt does not establish task completion.

## Existing retained attempts are not magically resolved

At this read-only inspection, Root had an old ID-less pasted journal and 19 queued
requests. Sol-new-combat also had an unconfirmed direct attempt. Spriter had the
old null-session journal whose request had been canonically closed. The code
preserves uncertain evidence; a deployment alone cannot infer what happened.
No queue was flushed and no pending text was resubmitted.

Spriter's supported targeted retirement is described in `operational-recovery.md`.
It needs separate approval and a verified idle, empty target. Root's ID-less
historical attempt cannot use request-closure retirement. Do not clear its journal
or invent a receipt to make the UI appear healthy.

## Restore source selection if needed

This changes the next build's source only; it does not roll back an active system:

```sh
cp /home/tctinh/hive-source-selection-backup-20261107/package.nix.original /home/tctinh/agentic-hive/nix/package.nix
```

Do not delete the fixed checkout while the package-selector import points to it.

## Final source verification

- 286 Python unit tests passed, with the exact running `7330a09` sender used in
  the compatibility test. No tests in that final invocation were skipped.
- 56 installer checks, 65 smoke checks, session-routing fixtures and all three
  dashboard JavaScript suites passed.
- Nix syntax and host-configuration evaluation passed. The selected install
  phase matches the durable fixed package, and package install checks are enabled.
- The corrected inspector ran against live Hive read-only and reported no errors.
- Nix package build/install checks and live submission after activation remain unrun.

Test evidence is in `/home/tctinh/agentic-hive-validation-20261107`.
The final Python invocation was:

```sh
PYTHONPATH=test HIVE_LEGACY_SOURCE=/home/tctinh/Documents/Codex/2026-11-06/task/evidence/followup/baseline-7330a09 python3 -m unittest config devlogs installer runtime messages prompt web delegation village metadata_integrity mixed_delivery delivery_reconciliation obsolete_delivery inspection
```

Generic discovery is unsuitable here: `hook_costs.py` is a standalone benchmark
that takes a source path argument, not an importable unittest module. An initial
attempt using discovery failed on that benchmark; the documented module invocation
above completed successfully. The source-selector wrapper is a local deployment
choice and should not be published as a portable upstream package definition.
