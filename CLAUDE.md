# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:6cd5cc61 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->


## Project Status

**Pre-implementation.** As of this writing, only `docs/`, the bd issue tracker,
and this harness scaffolding exist — no `pyproject.toml`, no `src/`, no tests,
no Dockerfile, no CI. Run `bd ready` to see what's next; the first issue is
always the environment/code-scaffolding task. Don't skip ahead of it — the
verification commands below don't exist until it lands.

## Session Start Checklist

Initialization and implementation optimize for different things — explore
first, then execute (harness-engineering Lecture 06). Before writing any
code, confirm all four:

- [ ] **Can start?** `bd ready` shows an unblocked issue, and (once
      `E_SCAFFOLD` is closed) `pip install -e .` / the project's declared
      dependency install command succeeds.
- [ ] **Can test?** The verification commands in "Build & Test" below
      actually run (even if `pyproject.toml` doesn't exist yet, confirm
      *that* — don't assume).
- [ ] **Can see progress?** `git status` is clean or its state is understood;
      `bd list --status=in_progress` matches what you're about to claim.
- [ ] **Can pick up next steps?** `bd show <id>` on the ready issue has a
      clear Definition of Done in `--acceptance` (Behavior/Verification/
      State). If it doesn't, fix the issue before claiming it — don't guess.

Only after this checklist passes: `bd update <id> --claim` (WIP=1 is
enforced by `.claude/hooks/bd-wip-gate.sh` — a second claim while one issue
is `in_progress` will be denied).

## Build & Test

Not yet scaffolded (see Project Status above). Once the scaffolding issue is
closed, these are the commands this harness expects to exist and enforces via
`.claude/hooks/`:

```bash
ruff check .                                    # lint
ruff format --check .                           # format check
mypy --strict src/                              # type check
pytest -q                                       # unit + integration tests
pytest --cov=adhd_dash --cov-report=term-missing  # with coverage
```

`.claude/hooks/lint-python.sh` runs `ruff check --fix` + `ruff format` after every
Edit/Write to a `*.py` file (no-ops until `ruff` exists). `.claude/hooks/stop-gate.sh`
blocks session Stop if `pytest`/`ruff check` fail on changed Python files (also a
no-op pre-scaffolding). Both degrade gracefully rather than erroring — see the
scripts' comments.

## Architecture Overview

Full implementation decisions live in `docs/architecture.md`; product
requirements live in `docs/adhd-project-dashboard-prd.md`. One-paragraph
summary: FastAPI (async) serves a versioned JSON API (`/api/v1/...`) that is
the single source of business logic; a server-rendered Jinja2 + HTMX + Alpine +
Tailwind UI consumes that same API (never a parallel code path — see the
Frontend/API Boundary Rule below). SQLite (`state.db`, via SQLModel) holds
mutable state; `config.yaml` holds static tuning. An adapter pair (`BdAdapter` /
`BrAdapter`) normalizes the two Beads CLI implementations tracked projects
actually use. APScheduler polls; `asyncssh` reaches remote Tailscale hosts;
`httpx` hits the GitHub REST API for releases and commit activity. Ships as a
service block inside the existing `apps-mine` module in the separate
`homelab-iac` repo — this repo holds only application source.

## Conventions & Patterns

- **Config as source of truth.** `config.yaml` = every static tunable
  (staleness thresholds, poll interval, SSH hosts/roots, GitHub check TTL, log
  level) — secret fields ship blank, overridden by env vars at deploy.
  `state.db` (SQLite) = mutable runtime state (tracked-project registry,
  snooze/archive/last-seen) — this is user-generated data (PRD R3), not
  tuning, so it never goes in `config.yaml`. Full rationale: `docs/architecture.md` §3.
- **Frontend/API boundary rule.** No business logic (percent-complete calc,
  staleness transitions, threshold evaluation) may live only in a Jinja
  template or an HTMX-only route. It lives in `/api/v1/...`; the UI is just a
  consumer of that same API. This is what keeps a future React+Storybook
  rewrite cheap if this project is ever publicized. Full rationale:
  `docs/architecture.md` §4.
- **Staleness is per-signal, not `max()`.** A project is stale as soon as
  *either* its Beads signal or its GitHub signal has gone silent past the
  threshold — a fresh signal on one side does NOT mask staleness on the
  other. A separate, display-only "last active" timestamp may use `max()` for
  human-readable UI purposes, but that is never the staleness gate. See PRD
  R17/R18 and `docs/architecture.md` §5.
- **Two Beads CLIs, one normalized type.** Tracked projects use a mix of `bd`
  (gastownhall, confirmed `bd status --json` schema) and `br` (Dicklesworthstone
  beads_rust, schema NOT yet verified against a live install — confirm before
  implementing `BrAdapter`). Both normalize to a common `ProjectStatus`.
- **Never commit secrets.** This repo holds only application source; runtime
  secrets (SSH keys, API keys) are injected via Infisical at deploy time in
  `homelab-iac`, never committed here. `config.yaml`'s secret fields ship blank.
- **Pin everything.** Dependency versions and the Docker base image are
  pinned (never `:latest`), matching `homelab-iac` convention — a rebuild
  months from now must reproduce the same system.
- **This project's own dev work is tracked in `bd`, not markdown.** No
  `FEATURES.md`/`PROGRESS.md` — `bd ready` / `bd list --status=in_progress`
  is the live task list. See `DECISIONS.md` for why, and `.claude/hooks/bd-wip-gate.sh`
  for the WIP=1 enforcement mechanism.

## Definition of Done

Every bd issue's `--acceptance` field should be a checklist of **verifiable**
conditions, not vibes. Full format and the three-layer termination check
(self-check / verification / validation) live in `docs/quality-standards.md`.
Minimal shape:

```
- [ ] <behavior implemented>
- [ ] pytest -q passes (new + existing tests)
- [ ] ruff check . / mypy --strict src/ clean
- [ ] <validation: curl a live endpoint, or manual check against the running dev server>
```
