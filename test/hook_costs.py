"""Focused synthetic cost probe: python3 test/hook_costs.py BASELINE_SOURCE_DIR."""

import json, os, pathlib, statistics, subprocess, tempfile, time, sys

sources = {
    "baseline": pathlib.Path(sys.argv[1]).resolve(),
    "fixed": pathlib.Path(__file__).resolve().parents[1],
}
results = {}
for label, source in sources.items():
    with tempfile.TemporaryDirectory() as temporary:
        root = pathlib.Path(temporary)
        habitat = root / "hive"
        home = root / "home"
        codex = root / "codex"
        logs = codex / "sessions/fixture"
        logs.mkdir(parents=True)
        for number in range(1000):
            (logs / f"rollout-{number}.jsonl").write_text(
                '{"payload":{"type":"token_count","rate_limits":null}}\n'
            )
        env = dict(
            os.environ,
            HIVE_ROOT=str(habitat),
            HIVE_MEMBER="alice",
            HOME=str(home),
            CODEX_HOME=str(codex),
            PATH=str(source / "bin") + ":" + os.environ["PATH"],
        )
        subprocess.run([str(source / "bin/hive"), "init"], env=env, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(
            [str(source / "bin/hive"), "join", "alice"], env=env, check=True, stdout=subprocess.DEVNULL
        )
        payload = json.dumps({"cwd": str(root), "session_id": "fixture"})
        command = [str(source / "bin/hive-hook"), "codex", "PostToolUse"]
        subprocess.run(command, env=env, input=payload, text=True, capture_output=True, check=True)
        samples = []
        for _ in range(25):
            start = time.perf_counter()
            completed = subprocess.run(
                command, env=env, input=payload, text=True, capture_output=True, check=True
            )
            assert not completed.stdout
            samples.append((time.perf_counter() - start) * 1000)
        results[label] = {
            "no_change_hook_median_ms": round(statistics.median(samples), 2),
            "p95_ms": round(sorted(samples)[23], 2),
            "samples": 25,
            "synthetic_empty_logs": 1000,
        }
print(json.dumps(results, indent=2))
