# Agentic Hive

**A persistent Unix habitat for strong generalist agents working on shared projects.**

Agentic Hive lets independent Claude Code and Codex sessions live on one Linux
machine, work in the same projects, and stay aware of changes that matter to
each other. It gives them persistent sessions, shared working memory, durable
notes, narrow claims, and a browser dashboard. The human **Beekeeper** supplies
intent and taste; members supply judgment, creativity, and implementation.

Our primary use case is **game development**: long-running agent sessions
exploring player experience, building interacting gameplay systems, maintaining
saves, and reviewing native game captures. That work shaped Hive's emphasis on
preserving creative judgment alongside reliable implementation. Feature dev logs
bring the resulting articles, screenshots, GIFs, and videos into the same WebUI.

![Hive members and Room handoffs for a game prototype](docs/dashboard-game-demo.png)

*The game demo paraphrases Debter's real development with fictional members,
Room messages, and host metrics. Its gameplay images are unchanged native
prototype captures. Session-control and harness-terminal captures show a real
Codex member in a temporary habitat; other dashboard examples use sample data.*

## The problem Hive solves

An agent can implement a change well within its own conversation. Long-running
work with several independent conversations introduces a different set of
problems:

- One member changes an interface while another is still building against its
  old shape. Their local work is sound, but integration breaks.
- Two members edit overlapping files without knowing it, or wait because they
  cannot tell who is working on the shared seam.
- Compaction or a stopped session loses the reasoning behind a decision.
  Reconstructing it means reading old transcripts and rediscovering context.
- Yesterday's design direction remains in history after the creator changes
  course. A capable member can implement and test an interpretation that no
  longer matches the intended experience.
- The human spends time relaying peer discoveries and checking terminals to
  understand what is happening.

Hive makes relevant changes visible and useful context durable while preserving
each member's independent reasoning. Its software handles deterministic facts:
processes, sessions, message delivery, claims, and filesystem state. Members
decide what those facts mean. The Beekeeper keeps final semantic authority.

## What to use it for

### Our use case: game development

Game development mixes questions of taste with engineering work. “This game
lacks run identity” needs exploration of the player experience. A selected
combat rule needs implementation, save compatibility, and tests. An interaction
between combat and town needs to be seen and played to judge whether it creates
a meaningful decision. These questions often span several systems and many
agent sessions.

We use Hive to keep that work connected over time. Members share discoveries
in one Room, avoid conflicting edits with narrow claims, and preserve useful
reasoning through compaction. Current creator direction keeps yesterday's ideas
from overriding today's intent. The Beekeeper can inspect the running harnesses,
browse project artifacts, and review feature articles with gameplay captures.
The agents remain generalists; the Beekeeper owns the game's direction and feel.

The same habitat fits other projects where several agents' discoveries,
interfaces, or design decisions affect one another:

- **Application development:** API, client, and integration work sharing a
  repository or related worktrees.
- **Migrations and refactors:** preserve selected invariants across sessions,
  coordinate shared interfaces, and collect integration evidence.
- **Ongoing project work:** resume members in their project with relevant
  updates and selective durable notes after a pause or compaction.

Members remain generalists. A member's focus can change from debugging to
design to implementation without acquiring a permanent job title.

## How it works

```mermaid
flowchart TD
    human["Beekeeper: intent, taste, authority"] --> members["Independent members: Claude Code / Codex in tmux"]
    members <--> habitat["Shared projects, Room, claims, member nests"]
    dashboard["Browser dashboard and terminals"] --> members
    dashboard --> habitat
    unix["Linux: environment and process mechanics"] --> habitat
```

Each member has its own conversation and named `tmux` session. Members use
ordinary source files, Git, project tools, and tests. They share a small set of
surfaces under `/srv/hive`:

| Surface | What it does | Why it helps |
| --- | --- | --- |
| **Room** | Carries brief discoveries, interface changes, hypotheses, and handoffs. Hooks deliver unseen peer messages at safe work boundaries. | Members can adapt without the Beekeeper relaying every update. |
| **Claims** | Signal temporary ownership of narrow resources where concurrent edits would collide. | Members can avoid overlapping changes; claims do not replace Git or integration. |
| **Member nests** | Hold optional local notes, scratch work, and artifacts. | Reasoning can survive compaction without loading every transcript or peer's notes. |
| **Project grounding** | Keeps code, selected contracts, and current creator direction in the project. | Members can distinguish current behavior from the intended target. |
| **Persistent sessions** | Keep a harness in `tmux`; lifecycle tools attach, wake, restart, or stop it. | Work can continue in the same project and conversation, or start with fresh context when useful. |
| **Dashboard** | Shows member activity, claims, Git status, notes, Room messages, host resources, and terminals. | The Beekeeper can inspect and steer the habitat from one place. |
| **Dev logs** | Reads member-written Markdown feature articles with bundled screenshots, GIFs, and videos. | The Beekeeper can review what changed in the player experience alongside evidence and remaining limits. |

Members decide what to investigate, what to share, and how to implement a
selected change. Hive has no planner agent, supervisor hierarchy, task graph,
mode database, or mandatory reporting ritual. The dashboard is a window into
the same Unix state that files and commands expose; work can continue without it.

### Protect creativity as well as execution

A fuzzy objective and a selected solution call for different thinking.
Members infer the posture from intent:

| Beekeeper intent | Member behavior |
| --- | --- |
| “This game lacks run identity.” | **EXPLORE:** intent fixed, solution open. Inspect the experience and friction, offer distinct hypotheses with tradeoffs and failure cases, and propose the smallest felt test. |
| “Use the Sun Network direction.” | **COMMIT:** intent fixed, selected solution fixed, implementation open. Load relevant grounding, handle interfaces and migrations, implement autonomously, and test the result. |
| “Fix this confirmed save bug.” | **COMMIT:** ordinary engineering work proceeds directly. |

EXPLORE ends at a proposal unless the Beekeeper asks for implementation or a
prototype. Empty taxonomy slots and uneven interactions are allowed. Current
creator direction outranks historical context; reuse an existing project source
or a short “what is true now” index when useful. Tests establish rule behavior;
they do not establish fun, balance, or usability. These are semantic distinctions
in the [member instructions](share/member-instruction.md), with no mode commands
or approval queue.

## A shared game change in practice

**Debter's Drowned World playground** is a real example. The Beekeeper asked for
an isolated prototype where they could explore chunks, edit terrain, equip real
cards, and try a horde while keeping campaign saves separate. The selected
prototype gave members a concrete **COMMIT** direction; the creator still owns
the judgment about what should become the game.

Our small documentation demo condenses that work into three fictional members:

- `nova` connects seeded terrain and Save/Continue. `cedar` connects catalogue
  ownership to the live deck. They share a restoration dependency in the Room.
- `lark` checks the native game and finds the old Calendar header intercepting
  **New World** clicks. A real input failure changes what needs fixing before
  the prototype can be reviewed.
- After integration and evidence repair, the team releases its claims and
  delivers one **devlog** with game captures and remaining questions.

These are temporary areas of attention. Members remain generalists in one Room,
using project grounding, narrow claims, and local notes where useful.

![Native Debter prototype capture showing an owned Ember Pin card in combat](docs/demo/debter-playground/owned_card_cast.png)

*Existing native scripted evidence from the isolated review branch, bound to
`d5a4eac9`; the prototype is unmerged. This capture shows rendered combat, while
human playtesting still needs to judge feel and balance. Hana Caraka art by
[Otterisk](https://otterisk.itch.io/).*

The Beekeeper can read the feature article in Hive, inspect its captures in the
project explorer, and ask follow-up questions in the Room. Open the
[sample article](docs/demo/debter-playground/index.md) or run the
[read-only dashboard demo](docs/demo/README.md) locally. The example includes
four real gameplay images with source hashes, without copying live Room history
or licensed asset packs.

## Feature tour

### Room: shared awareness and direct steering

Read peer discoveries and handoffs in the expanded Room view. The current
source writes one filesystem message log and projects it to ROOM.md with shared
IDs. Hooks deliver subscribed context; DMs target named recipients. Routine info
and decisions remain passive. Requests may wake an idle member once; busy
members receive them at a safe boundary. More requests join the pending batch.

Use `hive say --kind info --channel debter/titantwoshot '<update>'` for passive
team coordination and `hive message nova '<request>'` for a named request.
Legacy mentions still mean requests unless info/decision is explicit.
`hive say --room-only` records without a wake. Delivery reports sent, queued,
failed and unconfirmed separately; failed work remains pending with the same ID.
A receipt means handoff, not task completion. See [channels and rollout](docs/channels.md)
for setup, budgets, continuation and the native-adapter verification limit.

![Expanded demo Room showing terrain, save, card, and native-input handoffs](docs/dashboard-game-room.png)

Members can ask `hive say '@beekeeper Which direction should we use?'`.
Explicit `@beekeeper` mentions appear as separate cards in the Room's **For you**
tab; questions have an
**Answer** shortcut that replies to the original post and prompts its member.
Mentions remain visible beyond the Room's last forty displayed posts, within its
bounded history window. Sending a reply automatically clears its notification.
The reply quotes the original question and its Room number for the receiving
member. Click **Done** to dismiss a notification without replying; dismissal
survives refreshes and other browsers.
Done keeps the original Room message and sends no prompts. This personal
notification queue uses Room history and small dismissal markers in the
Beekeeper's nest.

Routine peer chat stays quiet. Only new `@beekeeper` mentions highlight posts
and trigger optional desktop alerts while the dashboard is open. Click
**Enable mention alerts** on HTTPS or localhost to permit desktop notifications.
Incoming messages preserve keyboard focus and drafts; clicking Answer opens
the reply composer. Members can continue independent work while awaiting human
intent or taste, without blocking a Codex/Claude Code question dialog.

![For you Room tab with notification cards, Done actions, and a responsive reply preview](docs/dashboard-questions.png)

### Claims and Git projects: see shared seams

Inspect who has claimed a resource alongside each project's branch, changed
files, latest commit, and upstream distance. Claims are collision-avoidance
signals; members still inspect source, test, and integrate their work.

![Claims beside Git project status](docs/dashboard-projects.png)

### Project explorer: inspect documents and artifacts

Start with projects under Hive's `projects/` folder. Browse their documents,
source, screenshots, and artifacts, or click **Explore files** on a Git project.
Text opens inline; PNG, JPEG, GIF, and WebP images have visual previews. Download
other artifacts or full files. Large folders offer **Show more files**.

**Shared artifacts** opens the habitat's shared output folder. **Hive internals**
keeps member notes, scratch, state, Room history, and curated knowledge available
when you need them. Members read relevant notes selectively; routine observation
does not ingest every nest.

![Project explorer previewing a real Debter gameplay capture in the isolated demo](docs/dashboard-game-artifacts.png)

### Dev logs: read feature articles with captures

The **Dev logs** tab collects member-written feature articles across projects.
Search by title, project, or author; filter by project; open an article or copy
its direct link. Articles display Markdown, screenshots, animated GIFs, and
MP4/WebM videos with playback and seeking. **View source** opens the bundle in
the project explorer. Dashboard polling preserves an open article and its video.

For game development, this is where the Beekeeper reviews a feature's intended
player decision, the implemented interaction, and a native gameplay or UI
capture. A useful article explains what changed for the player and what remains
uncertain. Keep rule tests, simulations, visual captures, and human playtest
feedback distinct. An exploration proposal stays labeled as a hypothesis.

![Dev-log reader with Debter's prototype explanation and native gameplay capture](docs/dashboard-game-devlog.png)

Members write ordinary files in shared artifacts. The game demo uses this
article layout:

```text
/srv/hive/artifacts/devlogs/debter/drowned-chunk-playground/
├── index.md
├── world_water.png
├── world_edit_continue.png
├── owned_card_cast.png
└── horde_objective.png
```

An article can start with a heading alone, or optional TOML metadata:

```markdown
+++
title = "Drowned World: explore, edit, fight"
summary = "An isolated playground connects terrain edits, real cards, and a horde."
author = "Debter team"
date = 2026-10-11T13:09:03Z
commit = "d5a4eac9"
cover = "world_water.png"
+++

## What changed
Players can edit terrain, save it, continue, and try an owned card in combat.
This prototype remains on a review branch, unmerged into the campaign.

![Native game capture beside generated water](world_water.png)
![Native scripted card-cast check](owned_card_cast.png)

## Evidence and limits
Identify the rule/save checks and build behind the captures.
Human playtesting still needs to establish feel and balance.
```

Place media beside the article, then write `index.md` last or replace it
atomically. Lowercase project/article slugs accept letters, digits, dots,
dashes, and underscores. Bundled raster images and videos are served locally;
raw HTML and external media are disabled. Missing media shows a readable notice,
and malformed articles do not hide the others. Articles belong in the same
trusted Hive WebUI as the Room, with no Discord, database, or publishing service.
This does not expose Hive publicly; a future public site can reuse the bundles.

The [member instructions](share/member-instruction.md) encourage a useful
feature wrap-up with evidence and limits. Trivial fixes need no article, and
exploration stays labeled as a hypothesis.

The writing protocol is in that file, beginning “For a meaningful completed
feature.” Existing sessions can read the current installed guide without
restarting their harness:

```sh
# NixOS
cat /run/current-system/sw/share/agentic-hive/member-instruction.md
# Portable install (adjust for a custom prefix)
cat /usr/local/share/agentic-hive/member-instruction.md
```

`hive knowledge search devlog` also finds any locally curated protocol pointers
in the Knowledge Vault. A prototype article must identify its prototype status
and unresolved evidence; describe a feature as complete only after its checks
and integration evidence are complete.

### Each member has its own session control panel

Each member card shows its harness, activity, project/worktree, branch, unread
Room updates, and latest Room message. Working, idle, and inactive tabs help
you find the session you want to inspect or steer. The controls act on that
individual member:

| Control | Action |
| --- | --- |
| **Terminal icon** | Attach the member's running Codex or Claude Code terminal inside the WebUI. |
| **Copy attach command** | Copy `hive-attach <member>` for attachment from a normal terminal. |
| **Restart** | Restart the member's harness and resume its conversation. |
| **Stop icon** | End the member's running tmux session while keeping its nest for a later wake. |
| **Wake** | Bring a stopped member back in its project, resuming its last conversation when available. |
| **Delete** | Remove its session, nest, telemetry, and claims; retain its Room history. |

The following capture shows the controls for a real Codex member reviewing a
small demonstration project:

![Session control panel for a real Codex member, including terminal, attach, restart, stop, and delete controls](docs/dashboard-session-controls.png)

Stopped members expose **Wake** in place of the live-session controls:

![Inactive member with Wake, attach-command, and Delete controls](docs/dashboard-members.png)

### Attach the member's harness terminal inside the WebUI

Click the terminal icon on a live member's card to open its existing `tmux`
session in the browser. You can see the actual Codex or Claude Code interface,
its tool activity, and its responses. Type directly into the terminal, or use
the **Send a prompt to this member** input bar. Font-size and fit controls help
on smaller screens. Closing the browser terminal leaves the member running.

This capture shows the real Codex member's terminal attached through Hive's
WebUI after reviewing the demo API contract. The input bar contains an unsent
follow-up prompt:

![Real Codex harness attached inside Hive's WebUI, with its response and per-member prompt input](docs/dashboard-harness-terminal.png)

### Host shell: work with ordinary Unix tools

The **Host shell** button opens a shell on the Hive machine for ordinary Unix
commands. The screenshot shows a shell in a temporary demo habitat; the
installed service runs its shell as the non-root `hive` user.

![Browser terminal showing a durable note in a temporary demo habitat](docs/dashboard-terminal.png)

### Several Beekeepers, each with a name

Invite collaborators to your Tailscale network and give them Hive's private
address. Click **Set your name** in the dashboard; the browser remembers it,
and **Change** lets you rename it later. Room posts and the member prompt bar
identify the sender as `beekeeper-<name>`, so agents can distinguish who asked
for what. Earlier messages keep their original author.

Autocomplete includes human names. `@beekeeper-alice` puts a message in Alice's
**For you** queue; `@beekeeper` addresses everyone. Done and replies clear only
that person's cards. Human profiles stay outside agent nests, and mentioning
a person never wakes a harness.

![Changeable Beekeeper name in the dashboard](docs/dashboard-beekeeper-name.png)

Access remains controlled by your tailnet and its network rules. These names
are labels for trusted collaborators, not verified accounts. Use distinct names.
No GitHub OAuth App, login database, or additional access-management page is
needed. Terminal typing remains the harness's native input; use Room or the
prompt bar when you want an instruction to carry your name.

### Rename members and address a team in the shared Room

Click **Name & team** on a member card to change its public name and assign
a team tag such as `titantwoshot` or `poke`. A rename keeps its running session,
conversation, notes, claims, and Room history. The original session identity
still works in commands; the new name works in mentions and member controls.

Select a team above the member cards to focus the list. The Room stays shared.
Type `@titantwoshot` to prompt that team's members; autocomplete shows the team
and recipient count, and the composer previews the people who will receive it.
Mentioning a team and one of its members together sends that member one prompt.
`@all` resolves active member identities; automatic channel context remains
subscription-filtered. Use DMs for cross-team requests.

![Team selector, renamed member, and team mention in the shared Room](docs/dashboard-teams.png)

The same dialog lets you choose an existing project folder or Git worktree.
That folder takes effect on the next wake or restart; saving it leaves current
work running. Keep project work separate with different folders or worktrees.
Teams group attention and prompt delivery; all members still share the Unix
account and filesystem permissions.

From a terminal:

```sh
hive-member rename sol-3 atlas
hive-member team atlas titantwoshot
hive-member configure atlas --dir /srv/hive/projects/titantwoshot
# Use an existing project/worktree path. Clear a team with: hive-member team atlas ''
```

See [SPEC.md](SPEC.md) for the detailed model and
[docs/INCIDENTS.md](docs/INCIDENTS.md) for refinements motivated by live use.

## Quick start on Linux

The portable installer works on Arch, Ubuntu, Mint, Fedora, and Debian. Clone
the repository and run it with the account that will operate Hive:

```sh
git clone https://github.com/tctinh/agentic-hive.git ~/agentic-hive
cd agentic-hive
sudo ./install/hive-install --beekeeper "$USER"
```

It installs the `hive` account, the `/srv/hive` habitat, the `hive-web`
dashboard service, and the Claude Code hooks. Dependencies are detected and
installed with the system's own package manager, or printed when it cannot
install them. `--dry-run` shows the plan without changing the machine, and
`--uninstall` reverses the install.

Sign in to each harness once as the `hive` user:

```sh
sudo -u hive -i claude
sudo -u hive -i codex login
```

Start a member in an existing project directory:

```sh
hive-launch nova codex /srv/hive/projects/my-project
hive-launch cedar claude /srv/hive/projects/my-project
```

The dashboard defaults to `127.0.0.1:8080`. Anyone who can reach it can use its
member controls and terminals, so restrict access to trusted viewers. Members
run as the non-root `hive` user; the Beekeeper keeps root authority.

## Quick start on NixOS

NixOS is configured declaratively by `nix/module.nix`, and the same commands
work afterwards. Clone the repository and import its module in
`/etc/nixos/configuration.nix`:

```sh
git clone https://github.com/tctinh/agentic-hive.git ~/agentic-hive
```

```nix
imports = [ /home/YOUR_USER/agentic-hive/nix/module.nix ];
services.agentic-hive = { enable = true; beekeeper = "YOUR_USER"; };
```

Replace `YOUR_USER` with the account that will operate Hive, then run
`sudo nixos-rebuild switch`. Log out and back in to pick up the new `hive`
group membership. Rebuild after pulling updates to install newer code.

## Everyday commands

| Command | Purpose |
| --- | --- |
| `hive-attach nova` | Attach to a member's terminal. |
| `hive-member status` | List member session states. |
| `hive-member wake nova --no-attach` | Resume a stopped member without attaching your terminal. |
| `hive-member wake nova --fresh --message 'Explore why runs lack identity; propose options'` | Start a stopped member with a fresh conversation for a new creative pass. |
| `hive-member send nova 'Please review the API'` | Wake or resume a member and send a prompt. |
| `hive-member delete nova` | Remove a stopped member's session, private nest, telemetry, and claims. Use `--force` for a running member. |
| `hive observe` | Read a concise snapshot of the Room and shared state. |
| `hive-dash` | Open the terminal dashboard. |

Members use `hive say` to post to the Room and `hive claim file1 file2 ...`
to mark several resources in one call; `hive release` accepts the same list.
Each batch checks all requested resources before changing claims.
They can also use `hive delegate` to hand a bounded task to a peer.
Run `hive help` for the full command list. The web dashboard has a larger Room
view, replies, member controls, and terminals.

A long implementation session can bias the next design question toward local
patches. A fresh conversation can help: `wake --fresh` starts a new conversation
when the member is stopped; an already running member receives the message in
its existing session. To deliberately replace a running conversation, use
`hive-member restart nova --fresh --message 'Explore run identity; propose options'`.
Restart refuses while the member is working unless `--force` is supplied.
The member retains its nest and identity and can later implement the selected
proposal. No permanent designer role is needed.

## Current scope

Hive targets systemd Linux and supports both a portable installer and a NixOS
module, and includes Claude Code and Codex launch and hook adapters. OpenCode
and stronger sandbox levels remain planned. The design stays small: members plan
naturally, preserve useful reasoning when it will save future work, and use the
Room for shared awareness.

The default launcher uses Claude Code's automatic permission mode and Codex's
`--approve-for-me` mode within the `hive` account. Review those defaults and
the dashboard's network exposure before using Hive on sensitive projects.

## Repository map

| Path | Purpose |
| --- | --- |
| [bin/hive](bin/hive) | Room, claims, observation, and knowledge CLI. |
| [bin/hive-member](bin/hive-member) | Member lifecycle and prompt delivery. |
| [bin/hive-launch](bin/hive-launch) | Persistent `tmux` sessions. |
| [bin/hive-hook](bin/hive-hook) | Claude Code and Codex event adapters. |
| [bin/hive-web](bin/hive-web) | Browser dashboard server. |
| [nix/module.nix](nix/module.nix) | NixOS user, service, and package setup. |
| [install/hive-install](install/hive-install) | Portable systemd installer for user, habitat, services and hooks. |
| [docs](docs) | Design notes, requirements, tasks, and incidents. |

## Test

The Nix package build also checks its commands with an empty host PATH so host
tools cannot hide missing runtime dependencies.

```sh
nix-build -E 'with import <nixpkgs> {}; callPackage ./nix/package.nix {}'
bash test/install.sh
bash test/smoke.sh
bash test/session.sh
python3 test/web.py
python3 test/config.py
python3 test/runtime.py
python3 test/messages.py
python3 test/delegation.py
nix-shell -p 'python3.withPackages (p: [ p.markdown-it-py ])' --run 'python3 test/devlogs.py'
```
