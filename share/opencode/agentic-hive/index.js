// Agentic Hive adapter for OpenCode (V2 server plugin).
//
// Maps OpenCode's plugin hooks onto the same lifecycle that `hive-hook` serves
// for Claude Code and Codex: SessionStart, UserPromptSubmit, PostToolUse, Stop
// and SessionEnd. Inert unless HIVE_MEMBER is set, so it is safe to load for
// every OpenCode session of the hive account. hive-launch runs members with
// `--standalone` so this server inherits the member's environment.
//
// Context returned by hive-hook is persisted where the model will keep seeing
// it: appended to the submitted prompt, or to a completed tool result. Context
// that arrives where neither is possible waits in the member's nest until the
// next such point, so a delivered Room entry is never silently dropped.
import { spawn } from "node:child_process"
import { appendFileSync, existsSync, readFileSync, renameSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const member = process.env.HIVE_MEMBER || ""
const root = process.env.HIVE_ROOT || "/srv/hive"
const here = dirname(fileURLToPath(import.meta.url))

function share() {
  if (process.env.HIVE_SHARE && existsSync(join(process.env.HIVE_SHARE, "member-instruction.md"))) return process.env.HIVE_SHARE
  return join(here, "..", "..")
}

function hookCommand() {
  const bin = process.env.HIVE_BIN_DIR
  if (bin && existsSync(join(bin, "hive-hook"))) return join(bin, "hive-hook")
  return "hive-hook"
}

// Run `hive-hook opencode <event>` with a Claude-shaped payload and return the
// additionalContext it produced. Never throws: Hive must not break the harness.
function hook(event, payload) {
  return new Promise((resolve) => {
    let out = ""
    let child
    try {
      child = spawn(hookCommand(), ["opencode", event], { stdio: ["pipe", "pipe", "ignore"], env: process.env })
    } catch {
      return resolve("")
    }
    const timer = setTimeout(() => child.kill("SIGKILL"), 15_000)
    child.on("error", () => {
      clearTimeout(timer)
      resolve("")
    })
    child.stdout.on("data", (chunk) => (out += chunk))
    child.on("close", () => {
      clearTimeout(timer)
      const parts = []
      for (const line of out.split("\n")) {
        if (!line.trim()) continue
        try {
          const text = JSON.parse(line)?.hookSpecificOutput?.additionalContext
          if (typeof text === "string" && text.trim()) parts.push(text.trim())
        } catch {}
      }
      resolve(parts.join("\n\n"))
    })
    child.stdin.on("error", () => {})
    child.stdin.end(JSON.stringify({ hook_event_name: event, ...payload }))
  })
}

const pendingFile = () => join(root, "members", member, "state", "opencode-pending-context")

function stash(text) {
  if (!text) return
  try {
    appendFileSync(pendingFile(), text + "\n\n", { mode: 0o664 })
  } catch {}
}

function takePending() {
  const file = pendingFile()
  const claimed = `${file}.${process.pid}`
  try {
    renameSync(file, claimed)
  } catch {
    return ""
  }
  try {
    return readFileSync(claimed, "utf8").trim()
  } catch {
    return ""
  } finally {
    try {
      renameSync(claimed, `${file}.delivered`)
    } catch {}
  }
}

const joined = (...parts) => parts.filter(Boolean).join("\n\n")
const wrap = (text) => `<hive>\n${text}\n</hive>`

export default {
  id: "agentic-hive",
  async setup(ctx) {
    if (!member) return

    const cwd = () => ctx.location?.directory || process.cwd()
    let instruction = ""
    try {
      instruction = readFileSync(join(share(), "member-instruction.md"), "utf8")
    } catch {}

    let current = process.env.HIVE_OPENCODE_SESSION || ""
    const children = new Map()
    const isChild = async (sessionID) => {
      if (sessionID === current) return false
      if (!children.has(sessionID)) {
        try {
          const info = await ctx.session.get({ sessionID })
          children.set(sessionID, Boolean((info?.data ?? info)?.parentID))
        } catch {
          children.set(sessionID, false)
        }
      }
      return children.get(sessionID)
    }

    const start = async (sessionID, source) => {
      current = sessionID
      stash(await hook("SessionStart", { session_id: sessionID, cwd: cwd(), source }))
    }
    // Identity must be visible before the first prompt so hive-member can
    // deliver a wake message into a fresh conversation.
    const ready = current ? start(current, "startup") : Promise.resolve()

    // A new main conversation (for example /new) starts a new Hive session.
    const follow = async (sessionID) => {
      await ready
      if (await isChild(sessionID)) return false
      if (sessionID !== current) await start(sessionID, "new")
      return true
    }

    await ctx.session.hook("prompt", async (event) => {
      if (!(await follow(event.sessionID))) return
      const fresh = await hook("UserPromptSubmit", { session_id: event.sessionID, cwd: cwd(), prompt: event.prompt.text })
      const text = joined(takePending(), fresh)
      if (text) event.prompt.text = `${event.prompt.text}\n\n${wrap(text)}`
    })

    await ctx.session.hook("context", (event) => {
      if (instruction && event.sessionID === current) event.system.push({ type: "text", text: instruction })
    })

    await ctx.tool.hook("execute.after", async (event) => {
      if (event.sessionID !== current) return
      const text = joined(takePending(), await hook("PostToolUse", { session_id: event.sessionID, cwd: cwd(), tool_name: event.tool }))
      if (!text) return
      if (event.status !== "completed") return stash(text)
      const content = event.result?.content
      const note = wrap(text)
      event.result = {
        ...event.result,
        content:
          typeof content === "string"
            ? `${content}\n\n${note}`
            : Array.isArray(content)
              ? [...content, { type: "text", text: note }]
              : note,
      }
    })

    // Turn end. A finished turn reaches the idle hook; an interrupted or
    // failed one does not, so those come from the event stream.
    const stop = (sessionID) => sessionID === current ? hook("Stop", { session_id: sessionID, cwd: cwd() }) : undefined
    await ctx.session.hook("experimental.idle", (event) => stop(event.sessionID))
    const ended = new Set(["session.execution.interrupted", "session.execution.failed"])
    const controller = new AbortController()
    void (async () => {
      try {
        for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
          if (ended.has(event?.type)) await stop(event.data?.sessionID)
        }
      } catch {}
    })()

    return async () => {
      controller.abort()
      if (current) await hook("SessionEnd", { session_id: current, cwd: cwd() })
    }
  },
}
