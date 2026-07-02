# Decision Log

Chronological record of architecture/design decisions and the rationale
behind them, so future sessions don't have to re-derive "why." Full detail
for stack/deployment/staleness decisions lives in `docs/architecture.md`;
this file is the compressed "what changed and why," in order.

This is a decision log, not a task tracker — task-level work lives in `bd`
(`bd ready` / `bd list`). See the last entry for why.

---

### 2026-07-01 — Stack: Python 3.12 / FastAPI / Jinja2+HTMX+Alpine+Tailwind / SQLite+SQLModel / APScheduler / asyncssh / httpx

**Why:** matches `open-delete` (the other personal app already deployed in
`homelab-iac`'s `apps-mine` module) — one language across self-hosted apps.
Async is required for concurrent multi-host SSH scans. Server-rendered UI
avoids a second toolchain/build step, keeping this a single-container
deploy like every other homelab module.
→ `docs/architecture.md` §1

### 2026-07-01 — Config split: `config.yaml` (static tuning) vs `state.db` (mutable state)

**Why:** "config as source of truth" was a hard constraint, but PRD R3 (add
a tracked project via the UI) is inherently mutable, user-generated data —
it can't live in a static file without turning that file into a de facto
database. Tunables (thresholds, poll interval, SSH hosts) go in
`config.yaml`; the tracked-project registry and per-project runtime state
(snooze/archive/last-seen) go in `state.db`.
→ `docs/architecture.md` §3

### 2026-07-01 — Deploy inside existing `apps-mine` module, not a new module

**Why:** `apps-mine` already exists specifically to hold personal apps as
Compose service blocks (see `open-delete`). A second module per personal app
would fragment a pattern that's meant to consolidate them. This repo
(`adhd-dash`) holds only application source; the deployment wiring
(compose service block, Caddy snippet, secrets) is added to `homelab-iac`
separately.
→ `docs/architecture.md` §2

### 2026-07-01 — Deferred React+Storybook frontend; enforced a Frontend/API boundary rule instead

**Why:** the UI here is deliberately thin (percent-complete indicator, three
status badges, a summary count, a login-time prompt) — it isn't the hard
part of this project. Building React+Storybook now would pay a second
toolchain, a second test runtime, and break the single-container deploy, for
a hypothetical future ("if this gets publicized"). Instead: enforce that all
business logic lives only in the versioned JSON API (`/api/v1/...`) and the
Jinja/HTMX UI is purely a consumer of it. If public launch ever becomes a
real near-term goal, this makes the rewrite "new client against a tested
API," not a backend rewrite.
→ `docs/architecture.md` §4

### 2026-07-02 — Added GitHub activity as a second staleness signal (PRD R17/R18)

**Why:** Beads-only staleness misses two real cases — (a) Beads and GitHub
drift out of sync (active commits, stale issue tracker, or vice versa), and
(b) projects where Beads was never initialized get no staleness signal at
all. GitHub commit/push activity, read via the same `httpx` client already
used for release detection (R6), closes both gaps.
→ PRD §4.5, `docs/architecture.md` §5

### 2026-07-02 — R17 revised: flag staleness on EITHER signal going stale, not `max()` of both

**Why:** the initial draft took the freshest of the two signals
(`max(last_beads_activity_at, last_github_activity_at)`), which hides
exactly the drift case R17 exists to catch — e.g. active commits masking a
genuinely stale issue tracker. Corrected to evaluate each signal
independently against the threshold; a project is stale as soon as *either*
available signal has gone silent, even if the other looks fresh. A separate
`max()`-based "last active" timestamp still exists for human-readable UI
display only — that's cosmetic, not the staleness gate.
→ PRD R17, `docs/architecture.md` §5

### 2026-07-02 — This repo's own development is tracked in `bd`, not a markdown feature list

**Why:** the harness-engineering methodology (see
`~/Dev/harness-engineering`) calls for a structured feature list with a
triple structure (Behavior/Verification/State) and a controlled state
machine (`not_started → active → passing`) as a harness primitive. Rather
than build a parallel `FEATURES.md`/`PROGRESS.md` for that, this repo uses
`bd` (Beads) directly: PRD requirements R1-R18 became bd epics/issues (see
`bd ready`), each issue's `--acceptance` field carries the Behavior/
Verification/State triple, and `bd`'s own status field (`open` →
`in_progress` → `closed`) is the state machine. This dogfoods the exact tool
the product itself is about, and follows the beads skill's own explicit
rule: "Do not create markdown TODO files as the source of truth when Beads
is available." WIP=1 is enforced by `.claude/hooks/bd-wip-gate.sh`, which
denies a second `bd update --claim` while one issue is already
`in_progress`.
→ `AGENTS.md`, `CLAUDE.md`, `.claude/hooks/bd-wip-gate.sh`

### 2026-07-02 — PRD Open Question 1 (R4 update mechanism) marked resolved

**Why:** `docs/architecture.md` §1 already committed to APScheduler polling
over file-watchers/webhooks (avoids needing inotify/webhook infra across
Tailscale-reachable hosts), and R4's bd issue (`adhd-dash-c6f.4`) encodes
that as its Behavior/Verification/State triple. Leaving PRD Open Question 1
marked "unresolved" while the rest of the harness already assumed the
answer was an internal inconsistency worth closing explicitly, unlike Open
Questions 2–5 (still genuinely undecided, tracked as `human`-labeled bd
chores).
→ PRD §5 Open Question 1, `docs/architecture.md` §1, bd issue `adhd-dash-c6f.4`
