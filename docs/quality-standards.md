# Quality Standards & Definition of Done

**Companion docs:** `docs/adhd-project-dashboard-prd.md` (requirements),
`docs/architecture.md` (implementation decisions). This doc is the harness's
Feedback subsystem: what "done" means and how it's checked, mechanically —
not by feeling.

---

## 1. Definition of Done

Every bd issue must have a Definition of Done recorded in its `--acceptance`
field before it's claimed. Conditions must be **verifiable by command**, not
by feeling — this is the single highest-leverage rule in this document.

```
- [ ] <the behavior, stated concretely — not "improve X" but "GET /api/v1/projects returns 200">
- [ ] pytest -q passes (new tests for the behavior + no regressions)
- [ ] ruff check . && ruff format --check . clean
- [ ] mypy --strict src/ clean
- [ ] <validation step — see §2, Layer 3>
```

One Definition of Done per issue. Don't let an issue accumulate multiple
unrelated completion criteria — split it into dependent bd issues instead
(`bd dep add`).

---

## 2. Three-Layer Termination Check

A task is only done when all three layers pass. This is enforced partly by
tooling (`.claude/hooks/stop-gate.sh`) and partly by discipline — the hook
catches layer 2 for Python changes, but layers 1 and 3 require the agent (or
the user) to actually do them.

| Layer | What it checks | For this project |
|---|---|---|
| **1. Self-check** | Agent's own assessment | Code compiles/imports, no obvious logic gaps, matches the issue's Behavior |
| **2. Verification** | Executable commands | `ruff check .`, `mypy --strict src/`, `pytest -q` — enforced automatically on `Stop` once scaffolding exists |
| **3. Validation** | External confirmation | Hit the running FastAPI dev server: `curl localhost:8000/api/v1/...` and check the actual response shape, or exercise the HTMX page in a browser for UI-facing issues |

**Rule:** don't close a bd issue on layer 1 alone. If layer 3 isn't practical
for a given issue (e.g., a pure refactor), say so explicitly in the close
reason rather than silently skipping it.

---

## 3. Verification-Validation Dual Gate

- **Verification gate** (automated): `pytest -q`, `ruff check .`, `mypy --strict src/`.
  All adapters (`BdAdapter`, `BrAdapter`, GitHub client, SSH client) must be
  mockable so this gate never needs real network/SSH access — see PRD's TDD
  requirement and `docs/architecture.md` §1.
- **Validation gate** (does it solve the actual problem): for backend/API
  issues, an actual HTTP call against the dev server. For UI issues, a
  manual or agent-driven browser check of the HTMX page (per the root
  CLAUDE.md instruction: test the golden path and edge cases in a browser
  before declaring a UI task complete — don't rely on the type checker for
  feature correctness).

Both gates must pass. Neither substitutes for the other.

---

## 4. Testing Strategy (once scaffolded)

| Layer | Tooling | Scope |
|---|---|---|
| Unit | `pytest` | Staleness evaluation logic, percent-complete calc, adapter parsing (given fixture JSON) |
| Integration | `pytest` + `pytest-asyncio` | FastAPI routes against a test `state.db`, mocked SSH/GitHub clients |
| Time-dependent | `time-machine` | Staleness threshold crossing — must be deterministic, not wall-clock-dependent |
| E2E (later) | manual / browser check | Full login → stale-project prompt → snooze/archive flow |

Determinism matters most here: `R17`/`R18`'s per-signal staleness logic has
several edge cases (no Beads, no GitHub remote, neither signal available) —
each needs its own test fixture, not just the happy path.

---

## 5. WIP=1 and Verified Completion Rate

- Only one bd issue `in_progress` at a time — mechanically enforced by
  `.claude/hooks/bd-wip-gate.sh`, which denies a second `bd update --claim`
  while one is already claimed.
- Verified Completion Rate (VCR) = closed-with-passing-verification / claimed.
  There's no automated VCR dashboard for a project this size — the practical
  version of this rule is: if you claim an issue and can't get it to a real
  Definition-of-Done pass, don't claim the next one. Report the blocker
  (`bd human <id>` or a comment) instead of moving on.

---

## 6. Clean State / Session-Close Checklist

Beyond bd's own session-completion protocol (see AGENTS.md's managed Beads
block), before ending a session that touched code:

- [ ] `git status` clean or changes are staged with a clear reason to leave them uncommitted
- [ ] `pytest -q` passes (once scaffolded) — `.claude/hooks/stop-gate.sh` blocks Stop otherwise
- [ ] bd issue status matches reality (`in_progress` only if genuinely still being worked; closed issues have a real `--reason`)
- [ ] Any new architecture decision is recorded in `DECISIONS.md`, not left implicit
- [ ] Next session's starting point is discoverable via `bd ready` alone — no tribal knowledge required
