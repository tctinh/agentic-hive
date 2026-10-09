// share/omp-hive.js — Agentic Hive extension for omp.
//
// Installed into <agent-dir>/extensions/ by hive-launch, so a member's omp
// session picks it up through omp's ambient extension discovery. The extension
// holds no Hive logic of its own: it calls bin/hive-hook once per event and
// carries whatever hive-hook emits into the return value that event supports.
//
// Event mapping. hive-hook is shared verbatim with the claude/codex command
// hooks, so this mirrors their five events one for one (contracts: omp docs,
// extensions.md / hooks.md):
//   session_start      -> hive-hook SessionStart (identity + claims)
//   before_agent_start -> hive-hook UserPromptSubmit
//   tool_result        -> hive-hook PostToolUse, where Room mail is delivered
//   session_stop       -> hive-hook Stop (dashboard idle marker only)
//   session_shutdown   -> hive-hook SessionEnd
//
// Contract (SPEC §12): silent unless something new happened, never fails or
// blocks the session, and inert unless HIVE_MEMBER names a member.
import { spawn } from "node:child_process";
import fs from "node:fs/promises";

// A wedged hive-hook must never wedge the agent loop, but silently dropping
// Room mail is worse than waiting: a healthy call is ~100ms, so this bound
// only ever applies to an already-broken install.
const TIMEOUT_MS = 5000;
const FOOTER_MAX = 80;
const QUOTA_TTL_MS = 10 * 60 * 1000; // `omp usage` costs a full process start
let quotaAt = 0;

// Quota only exists for providers that implement a usage reporter, so ask for
// every provider and keep whatever comes back. omp answers "no reports" for
// the rest, which is how they stay off the dashboard without a hardcoded list.
async function collectQuota(member) {
  const now = Date.now();
  if (now - quotaAt < QUOTA_TTL_MS) return;
  quotaAt = now;
  const root = process.env.HIVE_ROOT;
  if (!root) return;
  let out;
  try {
    out = await new Promise((resolve) => {
      let text = "";
      let child;
      try {
        child = spawn("omp", ["usage", "--json", "--redact"], { cwd: process.cwd() });
      } catch {
        return resolve(null);
      }
      const timer = setTimeout(() => child.kill("SIGKILL"), 20000);
      child.on("error", () => {
        clearTimeout(timer);
        resolve(null);
      });
      child.on("close", () => {
        clearTimeout(timer);
        resolve(text);
      });
      child.stdout.on("data", (chunk) => {
        text += chunk;
      });
      child.stderr.on("data", () => {});
      child.stdin.end();
    });
  } catch {
    return;
  }
  if (!out) return;
  let data;
  try {
    data = JSON.parse(out);
  } catch {
    return;
  }
  // One provider can hold several authenticated accounts, and each returns its
  // own window under the same id (openai-codex gave two "30 days"). Collapse
  // those into one row per window keeping the worst usage, since that is the
  // limit the member will hit first.
  const byProvider = new Map();
  for (const report of data.reports || []) {
    const provider = report.provider;
    if (!provider) continue;
    const entry = byProvider.get(provider) || new Map();
    for (const limit of report.limits || []) {
      const fraction = limit.amount?.usedFraction;
      const percent =
        typeof fraction === "number" ? Math.round(fraction * 1000) / 10 : null;
      const resets = limit.window?.resetsAt ? Math.round(limit.window.resetsAt / 1000) : null;
      const name = limit.label || limit.id || "limit";
      const seen = entry.get(name);
      if (!seen) {
        entry.set(name, { name, used_percent: percent, resets_at: resets });
      } else {
        if (percent != null && (seen.used_percent == null || percent > seen.used_percent)) {
          seen.used_percent = percent;
        }
        if (resets != null && (!seen.resets_at || resets < seen.resets_at)) {
          seen.resets_at = resets;
        }
      }
    }
    if (entry.size) byProvider.set(provider, entry);
  }
  const dir = `${root}/telemetry/quota`;
  try {
    await fs.mkdir(dir, { recursive: true });
  } catch {
    return;
  }
  const stamp = new Date().toISOString();
  await Promise.all(
    [...byProvider].map(([provider, entry]) =>
      fs.writeFile(
        `${dir}/omp-${provider}.json`,
        `${JSON.stringify({
          harness: "omp",
          provider,
          updated_at: stamp,
          reported_by: member,
          windows: [...entry.values()],
        })}\n`,
      ).catch(() => {}),
    ),
  );
}

export default function hiveExtension(pi) {
  if (!process.env.HIVE_MEMBER) return; // the Beekeeper's own session

  const hook =
    (process.env.HIVE_BIN_DIR && `${process.env.HIVE_BIN_DIR}/hive-hook`) || "hive-hook";

  // hive-hook speaks the same protocol as the claude/codex command hooks: JSON
  // on stdin, a hookSpecificOutput.additionalContext envelope on stdout, and
  // nothing at all when there is nothing new. Every failure path here resolves
  // to "" instead of rejecting, so a broken Hive never breaks the session.
  const call = (event, payload) =>
    new Promise((resolve) => {
      let out = "";
      let child;
      try {
        // Own process group: a timeout must be able to kill the hook's whole
        // tree, not just the shell that would otherwise leave a grandchild
        // holding the stdout pipe open past our resolve.
        child = spawn(hook, ["omp", event], {
          cwd: payload.cwd ?? process.cwd(),
          detached: true,
        });
      } catch {
        return resolve("");
      }
      let settled = false;
      const done = (text) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve(text);
      };
      const timer = setTimeout(() => {
        try {
          process.kill(-child.pid, "SIGKILL");
        } catch {
          child.kill("SIGKILL");
        }
        child.stdout.destroy();
        child.stdin.destroy();
        done("");
      }, TIMEOUT_MS);
      child.on("error", () => done(""));
      child.on("close", () => done(extract(out)));
      child.stdout.on("data", (chunk) => {
        out += chunk;
      });
      child.stdin.on("error", () => {}); // EPIPE if the hook exits early
      child.stdin.end(JSON.stringify(payload));
    });

  pi.setLabel("Agentic Hive");

  pi.on("session_start", async (_event, ctx) => {
    // session_start has no return channel, so the header is injected as a
    // message. "nextTurn" keeps it out of the editable pending-message UI and
    // appends it to context without kicking off a turn of its own.
    const header = await call("SessionStart", { cwd: ctx.cwd });
    if (header) {
      pi.sendMessage(
        { customType: "hive-header", content: header, display: false, attribution: "agent" },
        { deliverAs: "nextTurn" },
      );
    }
  });

  // Fire and forget: a member must not wait on a quota fetch to boot.
  void collectQuota(process.env.HIVE_MEMBER).catch(() => {});

  pi.on("session_shutdown", (_event, ctx) => {
    void call("SessionEnd", { cwd: ctx.cwd });
  });

  // Refresh on every settle; collectQuota throttles itself to one fetch per
  // TTL, so an active member does not pay for it on each turn.
  pi.on("session_stop", (event, ctx) => {
    void call("Stop", { cwd: ctx.cwd, session_id: event.session_id ?? "" });
    void collectQuota(process.env.HIVE_MEMBER).catch(() => {});
  });

  pi.on("before_agent_start", async (event, ctx) => {
    const text = await call("UserPromptSubmit", {
      cwd: ctx.cwd,
      prompt: event.prompt ?? "",
    });
    if (!text) return;
    return {
      message: {
        customType: "hive-room",
        content: text,
        display: false,
        details: {},
        attribution: "agent",
      },
    };
  });

  // Room mail after tool activity — the omp counterpart of claude's
  // PostToolUse, which is the event hive-hook actually delivers entries on.
  pi.on("tool_result", async (event, ctx) => {
    const text = await call("PostToolUse", {
      cwd: ctx.cwd,
      tool_name: event.toolName ?? "",
    });
    if (!text) return;
    ctx.ui.setStatus("hive", text.split("\n")[0].slice(0, FOOTER_MAX));
    return { additionalContext: text };
  });

  // Stop only marks the member idle for the dashboard; it delivers nothing.
  // The session id goes with it: hive-member reads it from telemetry to
  // resume this member's conversation on wake.
  pi.on("session_stop", (event, ctx) => {
    void call("Stop", { cwd: ctx.cwd, session_id: event.session_id ?? "" });
  });
}

// Pull the additionalContext string out of the hook envelope, tolerating a
// hook that printed bare text or nothing at all.
function extract(stdout) {
  const raw = stdout.trim();
  if (!raw) return "";
  try {
    return JSON.parse(raw)?.hookSpecificOutput?.additionalContext?.trim() ?? "";
  } catch {
    return raw;
  }
}