# beom-oss-analyzer

**English** | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md)

**A toolkit that analyzes open-source code exhaustively, lets you port just the features you need into your own repository, and verifies the port behaves like the original.**

![version](https://img.shields.io/badge/version-0.3.0-blue) ![python](https://img.shields.io/badge/python-3.11%2B-blue) ![license](https://img.shields.io/badge/license-use%20only%2C%20no%20modification-lightgrey)

Instead of skimming someone else's feature and imitating it, the toolkit analyzes the whole codebase with evidence
(file:line) for every conclusion, and checks by program that what you ported behaves the same as the original.
It ships a skill, MCP tools and a CLI so that AI agents such as Claude Code follow the procedure, and it blocks
completion of anything an AI should not decide alone until a human approves it.

## Contents

- [What it does](#what-it-does)
- [Key concepts](#key-concepts)
- [Requirements](#requirements)
- [Installation](#installation)
- [Desktop app and automatic runs](#desktop-app-and-automatic-runs)
- [Quick start 1: analyze a whole repository](#quick-start-1-analyze-a-whole-repository)
- [Quick start 2: port a feature and verify it](#quick-start-2-port-a-feature-and-verify-it)
- [Quick start 3: build from a request](#quick-start-3-build-from-a-request)
- [Customizing the original in place](#customizing-the-original-in-place)
- [Case file format](#case-file-format)
- [Command summary](#command-summary)
- [Data and storage](#data-and-storage)
- [Configuration](#configuration)
- [Safety principles](#safety-principles)
- [Known limitations](#known-limitations)
- [License](#license)

## What it does

| You want to | The toolkit |
|---|---|
| **Understand every feature** | Classifies every file, splits every function into review units, and attaches evidence (file:line) to each conclusion. Anything it cannot confirm goes to an "unknowns" list. |
| **Get the real API list** | Finds HTTP and CLI entry points with per-framework rules and expands URLs assembled in code into the actual route list. |
| **See impact before changing** | Shows what a function you plan to change affects (callers, implementations, entry points, tests) and where you can plug in without touching the core (extension points). |
| **Follow upstream updates** | Compares two versions and finds where upstream changes collide with your modifications (removed, renamed or re-signed functions). |
| **Port only the feature** | Cuts out the code, state, configuration and external dependencies one entry point needs, and records in a ledger where each item went. The target may be a different language. |
| **Verify the port** | Feeds the same inputs to the original and to your code and compares the results (HTTP, CLI or function level). Intentionally changed behavior is checked against expected values, and new behavior that does not exist in the original can be checked the same way. |
| **Build what you ask for** | Starting from "build a feature like this" with no original, it gets requirements and acceptance criteria approved first, then verifies the build meets them and survives odd input. |
| **Block AI shortcuts** | Accepted differences, omitted parts, reduced test cases and expected values cannot complete without human approval. Code changed after verification, or records edited outside the toolkit, are detected. |

## Key concepts

**Evidence levels** — how certain a conclusion is.

| Level | Meaning | How |
|---|---|---|
| L0 | Read the code and confirmed with the structure map | default |
| L1 | Calls/references confirmed by a language server | `lsp` |
| L2 | Where a value flows from and to is confirmed | `dataflow` |
| L3 | Confirmed to actually execute | coverage report, differential verification |

**Ledger** — analysis and porting state accumulates in a per-project SQLite ledger (`~/.oss-analyzer/projects/<id>/ledger.db`).
New sessions and several agents working in parallel continue the same record.

**Porting gates (7)** — `port-finish` issues a completion verdict only when all are met.

1. Every item to port is handled (ported / replaced / omitted)
2. Ported locations still exist (down to the line content)
3. Differential verification passes — all cases of the last capture, **against the current code** (invalid if the code changed afterwards)
4. Cases execute every function being ported (original coverage, or your code's coverage when the original cannot be instrumented)
5. Every item needing human review is approved (accepted differences, omissions, case changes, expected values; invalid if the content changes after approval)
6. Records were not changed outside the toolkit (ledger hash chain, golden hash)
7. No files need a license review

Every porting command response includes `next_step` (what to do next).

## Requirements

| Category | Details |
|---|---|
| Required | Python 3.11+, git |
| Required engines | structure-map engine and entry-point rule engine — installed by `setup` (checksum verified) |
| Optional | language servers (L1), data-flow engine (L2, JDK 21+), coverage tools (L3), container engine (isolated execution of third-party code) |
| Language servers (L1) | Rust · Java · Kotlin · TypeScript/JavaScript · Python · Go · C/C++ · C# · Dart · Ruby (other languages up to L0; file and symbol classification is language independent) |
| Entry-point rules | Axum · Spring · Express · FastAPI/Flask · Gin · clap · Next.js (frameworks without rules are automatically recorded as unknowns) |

Engine names, licenses and install locations are listed in [THIRD_PARTY.md](THIRD_PARTY.md).

## Installation

```bash
# 1. Install the toolkit so the oss-analyzer command works from any folder
pipx install git+https://github.com/ParkBeomsoo/beom-oss-analyzer.git
# (or) uv tool install git+https://github.com/ParkBeomsoo/beom-oss-analyzer.git

# 2. Check status — engines, language servers, skill, MCP registration
oss-analyzer doctor

# 3. Install engines — look at the plan first, then install only what you pick
oss-analyzer setup --languages python typescript
oss-analyzer setup --apply codebase-memory-mcp ast-grep lsp:python lsp:typescript coverage
```

- Without options, `setup` only **shows a plan** and changes nothing. Only the items you list after `--apply` are installed.
- Running third-party code in isolation needs a container engine. On Windows, `setup --apply sandbox` installs a container
  engine inside a WSL distribution (no desktop app required). An existing `docker` is used if present.
- Install language runtimes (JDK, Node.js, Go, Rust) yourself.

### AI agent (Claude Code) integration

Use one of the two.

```bash
# A. Register the skill + MCP with setup
oss-analyzer setup --apply skill mcp-register
oss-analyzer setup --apply skill mcp-register --scope project --project-dir <project>   # one project only

# B. Claude Code plugin (install the toolkit with step 1 first)
claude plugin marketplace add ParkBeomsoo/beom-oss-analyzer
claude plugin install beom-oss-analyzer@beom-oss-analyzer
```

- The installed skill copy records its version, so `doctor` reports `outdated` after a toolkit update.
- A skill with the same name you created yourself, or an MCP registration pointing at another installation, is never overwritten — you are told instead.
- You can use the CLI alone without MCP. Rules such as approval and completion verdicts behave the same in the CLI.

### Update and removal

```bash
pipx upgrade beom-oss-analyzer
oss-analyzer setup --update                      # refresh only the skill you already installed (installs nothing new)
oss-analyzer setup --remove skill mcp-register   # undo only what this toolkit installed or registered
pipx uninstall beom-oss-analyzer
```

## Desktop app and automatic runs

```bash
pipx install "beom-oss-analyzer[app] @ git+https://github.com/ParkBeomsoo/beom-oss-analyzer.git"
beom-oss-analyzer                    # open the app
oss-analyzer app --shortcut          # add Start menu (search) + desktop shortcuts; --remove-shortcut removes them
```

The first time you open the app it also offers to create the shortcuts. On Linux it writes a `.desktop` entry.

**Pick a mode on the home screen**

| Mode | When | Stages |
|---|---|---|
| **Request based** | Build a feature like this (no original to follow) | requirements → acceptance criteria → plan → build → verify → handoff |
| **Open-source based** | Bring a feature of a repository into my project | analyze → scope → record original → plan → build → compare → handoff |

In open-source mode only the repository URL (or path) is required. Describe the feature in the text box, or leave it empty —
the agent then lists the repository's features and asks you to pick one.

**Agent, model, effort** — one button next to the send button.

| Agent | Notes |
|---|---|
| Claude Code | asks per action through the toolkit's permission rules. Models: default · Opus · Sonnet · Haiku (aliases always resolve to the latest), effort low → max |
| Codex | blocks with its sandbox instead of per-action questions (writes only inside the target folder). Models and effort levels come from the list Codex keeps for your account, so new models appear automatically |
| Gemini CLI | experimental; no effort setting |

Any other model name can be typed in. Model and effort can be changed during a run (applied from the next round).

**Permissions** — pick a mode; the per-kind rules are under *Options → Permissions*.

| Mode | Behaviour |
|---|---|
| Ask everything | every edit and command is confirmed |
| Allow edits (default) | edits inside the target folder and build/test commands run directly; other commands and the web are confirmed |
| Auto | commands run without asking too; only the web and files outside the folder are confirmed — use for trusted work |

Push, publish, deleting outside the folder and system changes are always refused. Commands the agent passes to verification tools
(including commands inside case files) go through the same rules. Auth: *my login* (subscription) or *API key* (reads the key from an
environment variable, billed per use) — both can do the same things.

**What you decide** — only these appear as cards:

- **Requirements and acceptance criteria**, then the **plan**: *Approve*, *Edit* (change requirements, cases or plan steps yourself, then
  *Save and approve* — recorded in the ledger as written by you and passed on to the agent), or type a correction in the message box
  (the agent revises without approval).
- The agent's **questions** and **permission requests** (`Y` allow · `N` refuse).
- The **handoff** at the end: accept, or keep going by sending a message — sending a message to a stopped or finished run continues it.

Approvals are written to the ledger inside the app only, bound to exactly what was shown; there is no approval endpoint the agent can reach.

**Right panel**

| Tab | Shows |
|---|---|
| Status | progress, plan steps, requirements, completion conditions |
| Criteria | what was agreed — requirements, acceptance cases (input → expected), plan, and who approved them when |
| Checks | what was verified — per requirement, each case's input → expected → actual result, and what every completion condition means |
| Changes | changed files and diffs, per-file revert, checkpoints (the target folder is snapshotted every round; restore any point) |

**Rounds** — one round is one agent session. A typical request takes 4–6 rounds (more with questions or revisions); the default limit
is 8. A run that makes no progress for two rounds stops by itself, and you can always continue with a message, so the limit is only a cost cap.

**Where data lives** — everything stays on your computer. See [Data and storage](#data-and-storage).

Without the app: `oss-analyzer run --mode request --target-root <folder> --request "<request>"` (`--agent codex`, `--resume <run>`); answer in the app.

Install Claude Code by the [official guide](https://code.claude.com/docs) and run `claude auth login`; Codex with `npm i -g @openai/codex`
and `codex login` (check with `oss-analyzer agent-status`). Use a subscription login for your own use on your own computer; use an API key
to serve other people ([Anthropic terms](https://code.claude.com/docs/en/legal-and-compliance)).

## Quick start 1: analyze a whole repository

**Delegating to an AI agent** — in Claude Code:

> Analyze this whole repository: https://github.com/<org>/<repo>

The skill follows the procedure below. For large repositories several sub-agents split the units and analyze in parallel.

**Using the CLI directly**

```bash
oss-analyzer open https://github.com/<org>/<repo> --ref v1.2.0   # analysis-only clone pinned to a commit → project_id
oss-analyzer inventory <pid>                    # file classes, symbols, structure map, entry points, analysis units
oss-analyzer features <pid>                     # actual URL and CLI entry-point list

# analysis loop (repeat until no units are left)
oss-analyzer next <pid> --agent a1              # take the next unit
oss-analyzer context <pid> <unit_id> --agent a1 --raw
oss-analyzer finding <pid> <unit_id> --agent a1 --json finding.json   # conclusion + evidence
oss-analyzer unknown-add <pid> --agent a1 --json unknown.json        # what could not be confirmed
oss-analyzer complete <pid> <unit_id> --agent a1 --summary "what this unit does"

oss-analyzer coverage <pid>                     # completion on both axes (files, symbols) and failing items
oss-analyzer unknowns <pid>                     # remaining unknowns
oss-analyzer export <pid>                       # Markdown + JSON report
```

`finding.json` example:

```json
{"text": "Order creation locks stock first and releases it when payment fails", "severity": "important",
 "evidence": [{"path": "src/order/service.rs", "start_line": 120, "end_line": 158}],
 "symbol_ids": ["s_1a2b3c"]}
```

## Quick start 2: port a feature and verify it

Example: porting the original's `POST /api/v1/orders` into your repository in another language.

```bash
# 1. See the scope → create a porting ledger
oss-analyzer slice <pid> "POST /api/v1/orders"
oss-analyzer port-create <pid> --name orders --targets "POST /api/v1/orders" --language javascript --target-root <your repo>

# 2. Save the original's responses (golden) — the original runs isolated in a container
oss-analyzer port-capture <pid> <port_id> --cases cases.json \
    --source-image <image that runs the original> --source-url http://127.0.0.1:18001 --source-cmd "<command that starts the original server>" \
    --coverage-cmd "<command that writes the coverage report>" --coverage-report cov.json --explore 40

# 3. Write your code → record where each item went
oss-analyzer port-map <pid> <port_id> --json map.json

# 4. Differential verification (including concurrent requests)
oss-analyzer port-verify <pid> <port_id> --target-url http://127.0.0.1:18002 --target-cmd "node server.js" --concurrency 8

# 5. Progress and next step
oss-analyzer port-status <pid> <port_id>

# 6. Human approval — the user runs this in a terminal (refused when an agent runs it)
oss-analyzer port-approve <pid> <port_id>

# 7. Completion verdict — issued only when all 7 gates are met
oss-analyzer port-finish <pid> <port_id>
```

`map.json` example — `ported` needs a `path:line` in your code; `replaced` and `omitted` need a reason.

```json
[{"item": "s_1a2b3c", "status": "ported", "target": "src/orders/create.js:12"},
 {"item": "s_4d5e6f", "status": "replaced", "note": "use the existing message service instead of the original queue"},
 {"item": "s_7a8b9c", "status": "omitted", "note": "admin statistics are out of scope"}]
```

**Porting with changes**

| Situation | How | Verdict |
|---|---|---|
| Must match the original | just write the case | compared with the original response |
| Intentionally different (value can be specified) | `expect` on the case | compared with the expected value instead of the original; expected value needs human approval |
| New behavior not in the original | `"new": true` + `expect` | the original is not run; judged by the expected value |
| Only the format differs, value cannot be specified | `port-accept --case <name> --reason "<reason>"` | proposed accepted difference → human approval |
| Part of the feature is dropped | `omitted` + reason in `port-map` | human approval |

- The approval screen shows the original response next to the expected value (or the actual difference). If the content changes after approval, it must be approved again.
- If the original cannot be instrumented for coverage, measure your code instead with `port-verify --target-coverage-cmd ... --target-coverage-report lcov.info`.
- When edits to your code shift lines, update the ported locations with `port-map --follow`.

## Quick start 3: build from a request

Pick **Request based** in the app, or with the CLI:

```bash
oss-analyzer make-create --name order-total --request "Total from quantity. 2000 each, 10% off for 10 or more, 400 for 0 or less" --target-root <folder>
oss-analyzer make-req <pid> <port_id> --json reqs.json          # [{"text": "total = qty x 2000"}, …] → R1, R2 …
oss-analyzer port-capture <pid> <port_id> --cases cases.json --explore 20   # expect + req for each requirement
oss-analyzer port-approve <pid> <port_id>                       # approve requirements and criteria before building
oss-analyzer port-map <pid> <port_id> --json map.json           # requirement → implementation "path:line"
oss-analyzer port-verify <pid> <port_id> --target-url http://127.0.0.1:8080 --target-cmd "<command that starts the server>"
oss-analyzer port-finish <pid> <port_id>
```

- Each case needs `req` (which requirement it checks) and `expect`; every requirement needs at least one case.
- Automatically varied inputs are judged by **no crash and no 5xx** instead of expected values (odd input must be rejected with a 4xx).
- If you build into an existing repository, `open` and `inventory` it first and pass `--context <its project_id>` so the build follows its structure.

## Customizing the original in place

```bash
oss-analyzer extpoints <pid>                                  # first, places to plug in without touching the core
oss-analyzer impact <pid> pricing.calc_total --verify         # entry points, tests and conclusions affected by a change
oss-analyzer custom-add <pid> --description "change discount calculation" --kind modify --targets pricing.calc_total

# when a new upstream version is released
oss-analyzer open <repo> --ref v1.3.0 && oss-analyzer inventory <new pid>
oss-analyzer compare <base pid> <new pid> --save              # what changed + where it collides with your modifications
```

To verify the modified original against the unmodified one, use the modified repository as `--target-root` and follow [Quick start 2](#quick-start-2-port-a-feature-and-verify-it).

## Case file format

```json
{
  "defaults": {"headers": {"Content-Type": "application/json"}},
  "cases": [
    {"name": "createOrder", "method": "POST", "path": "/api/v1/orders", "json": {"sku": "A1", "qty": 2},
     "match": {"id": "[0-9a-f]{32}"}, "ignore": ["createdAt"]},
    {"name": "getOrder", "method": "GET", "path": "/api/v1/orders/{{createOrder.id}}"},
    {"name": "zeroQty", "method": "POST", "path": "/api/v1/orders", "json": {"sku": "A1", "qty": 0}, "compare": "status"},
    {"name": "bulkDiscount", "method": "POST", "path": "/api/v1/orders", "json": {"sku": "A1", "qty": 10},
     "expect": {"status": 201, "equals": {"total": 18000}}},
    {"name": "coupon_new", "new": true, "method": "POST", "path": "/api/v1/orders",
     "json": {"sku": "A1", "qty": 1, "coupon": "WELCOME"}, "expect": {"equals": {"discount": 1000}}}
  ]
}
```

| Field | Meaning |
|---|---|
| `method`·`path`·`json`·`headers` | HTTP case |
| `argv`·`stdin` | CLI case |
| `call: {source, target}`·`args`·`kwargs` | function-level comparison (`path:function`, languages may differ) |
| `{{caseName.field}}` | use a value from an earlier case's result (no spaces in the name) |
| `ignore` | paths not compared (`items.*.t`) |
| `match` | check with a regex instead of the value (ids, timestamps) |
| `compare: "status"` | compare the status code only |
| `expect` | compare with an expected value instead of the original — `status`·`body`·`equals` (path: value, arrays as `items.0.price`)·`match` |
| `new: true` | behavior not in the original (`expect` required; not run against the original) |
| `explore: false`·`concurrent: false` | exclude from automatic input variation / concurrency checks |

Keys outside this table are rejected when the file is saved (a misspelled key such as `args` instead of `argv` would otherwise run the case with different input). In request mode every case also needs `req`.

## Command summary

| Group | Commands |
|---|---|
| Setup | `doctor` `setup` `open` `list` `inventory` `classify` `agent-status` |
| App and runs | `app` `run` `runs` `data` |
| Analysis loop | `next` `claim` `renew` `context` `finding` `withdraw` `mark` `unknown-add` `unknown-update` `complete` `review` `pending` |
| Deeper checks | `lsp` (L1) `dataflow` (L2) `runtime-ingest` `runtime-run` `runtime-runs` (L3) |
| Results | `coverage` `features` `units` `unknowns` `export` `license` |
| Customizing | `extpoints` `impact` `custom-add` `custom-list` `custom-update` `compare` |
| Request-based build | `make-create` `make-req` (then the shared port-* commands) |
| Porting | `slice` `port-create` `port-map` `port-capture` `port-verify` `port-accept` `port-status` `port-approve` `port-finish` |

Options for each command: `oss-analyzer <command> --help`. Every command prints `{ok, data, warnings, errors}` JSON;
on failure `errors[0].recovery` tells you how to fix it. The detailed agent procedure is in the skill documents (SKILL.md and CLI.md of the `oss-analyze` skill).

## Data and storage

Like Claude Code (`~/.claude`) and Codex (`~/.codex`), everything lives in one folder in your home directory
(`~/.oss-analyzer`, change with `OSS_ANALYZER_HOME`), split by what it is. Nothing is sent anywhere, and no secrets are stored —
keys and tokens are read from environment variables only and masked in output.

| Folder | What | Delete? |
|---|---|---|
| `settings.json` | your preferences (cleanup, defaults) | yes — back to defaults |
| `projects/<id>/` | analysis/porting ledger `ledger.db` (SQLite, versioned schema, hash chain), the pinned clone of the original, golden responses | **no** — the record of approvals and verification |
| `runs/<run>/` | one automatic run = one conversation: `run.json` state, `events.jsonl` append-only timeline, `inbox/` questions and approval requests, `shadow.git` checkpoints of the target folder, `agent/` agent settings regenerated every round | move to the trash instead |
| `trash/` | deleted runs, emptied automatically after 7 days | yes |
| `app/` | app icon used by the shortcuts | no |
| `cache/` · `cbm-cache/` | agent status, verification harness, structure-map index | yes — rebuilt |
| `work/` | temporary copies for verification and coverage, removed after use | yes |
| `bin/` · `engines/` · `node/` | analysis engines installed by `setup` | yes — reinstall with `setup` |

The agent's own conversation stays where its CLI keeps it (`~/.claude/projects`, `~/.codex/sessions`) and is resumed by session id.
In your target folder the toolkit writes only `.oss/cases.json` (acceptance cases) — commit it or ignore it as you like.

| | Claude Code | Codex | this toolkit |
|---|---|---|---|
| Conversation | `projects/<folder>/<session>.jsonl` | `sessions/YYYY/MM/DD/*.jsonl` | `runs/<run>/events.jsonl` (+ the agent's own transcript) |
| Settings | `settings.json` | `config.toml` | `settings.json` |
| State | file-history checkpoints | versioned SQLite | per-project SQLite ledger (versioned, hash chain) + git checkpoints per run |
| Cleanup | old conversations deleted after 30 days by default | kept | kept by default; optional automatic cleanup of finished or hidden runs after 30/90 days; trash emptied after 7 days |

Deleting a run never removes approvals or verification results — those stay in the project ledger.

```bash
oss-analyzer data                        # where everything is and how big
oss-analyzer data --trash <run>          # move a run to the trash (--restore <name>, --empty-trash)
oss-analyzer data --prune 30 --dry-run   # finished or hidden runs untouched for 30 days
oss-analyzer data --set-cleanup 30       # clean those up automatically at start-up (0 = off)
```

In the app: **Storage** at the bottom of the sidebar, and **Move to trash** in a run's ⋯ menu.

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `OSS_ANALYZER_HOME` | `~/.oss-analyzer` | location of ledgers, clones, engines and backups |
| `OSS_ANALYZER_CONTAINER` | `auto` | isolated execution engine: `auto`·`docker`·`wsl`·`none` |
| `OSS_ANALYZER_WSL_DISTRO` | `oss-sandbox` | WSL distribution used on Windows |
| `OSS_ANALYZER_LEASE_SECONDS` | `3600` | analysis unit lease time |
| `OSS_ANALYZER_UNIT_MAX_LINES` / `_UNIT_MAX_CHARS` | `1500` / `45000` | analysis unit size limits |
| `OSS_ANALYZER_CONTEXT_MAX_CHARS` | `24000` | page size of `context` |
| `OSS_ANALYZER_LSP_TIMEOUT_SECONDS` / `_LSP_IDLE_SECONDS` | `900` / `600` | language server response timeout / idle shutdown |
| `OSS_ANALYZER_RULEPACKS` | — | additional entry-point rule folder |
| `OSS_ANALYZER_CBM` · `_ASTGREP` · `_JOERN` · `_JAVA_HOME` | auto-detected | explicit engine executables |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Claude Code config folder the skill is installed into |

## Safety principles

- **The analyzed code is never executed.** Language server build scripts and macro expansion are turned off as well. Verification that needs execution
  (coverage, porting originals) runs in a container with the network blocked, or in a temporary copy only for repositories the user says they trust.
- **Secret files are not read**, and secrets inside ordinary files (passwords, keys, tokens, URL credentials) are masked as `****` in output.
  Tokens and keys are also stripped from the environment passed to executed commands.
- Analysis runs only on **a dedicated clone at a pinned commit**. Uncommitted changes in your working folder are not included.
- Human approval for porting is taken only in the desktop app, the MCP confirmation dialog or the user's own terminal input — never from an agent during an automatic run.

## Known limitations

| Limitation | Mitigation |
|---|---|
| Differential verification proves only the inputs that were run | Automatic input variation and coverage widen the scope; the remaining scope is recorded in the evidence limitations. Performance is not compared |
| Coverage proves only that code ran | Which branch or input must be checked with cases |
| Name-based call relations are candidates | Need a language server (L1) or execution (L3) to count as evidence |
| Dynamic dispatch, reflection, macro-generated code | Automatically recorded as unknowns |
| The data-flow engine is slow the first time (about 15 minutes for 10k lines of Java) | Cached; build ahead with `dataflow --build-only` |
| The CLI starts language servers per call | Queued across processes per language; MCP mode reuses servers |
| Routes that depend on configuration values | Expanded with defaults and marked as unknowns |
| A local program cannot stop someone with the same permissions from deliberately forging records | Forgery attempts surface as gate failures; the final verdict can be re-run in a clean environment |

## License

**Use permitted, no modification** — [LICENSE](LICENSE) (this is not an open source license). The Korean text of the LICENSE governs.

- Anyone may install and use the unmodified software (including business and commercial use). Output you create with the toolkit is yours.
- Modifying it, creating derivative works, distributing modified versions or putting any part of the code into other software requires the author's written permission.
- Suggestions are welcome as issues or pull requests in this repository.

Libraries and external engines keep their own licenses — [THIRD_PARTY.md](THIRD_PARTY.md).
