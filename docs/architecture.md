# Architecture Decisions: projects.gnostichumor.app

**Status:** Decided (v1), pending scaffolding
**Companion doc:** `docs/adhd-project-dashboard-prd.md` (product requirements — this doc is implementation)

---

## 1. Stack (v1)

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Matches `open-delete` (the other personal app in `apps-mine`) — one language across self-hosted apps |
| Web/API | FastAPI | Async (needed for concurrent multi-host SSH scans); auto OpenAPI doubles as the external agent-facing API (PRD R16) |
| Frontend | Jinja2 + HTMX + Alpine.js + Tailwind | Server-rendered, no separate JS build/deploy step — stays a single container like every other homelab module. See §3 for the rule that keeps this swappable. |
| Local datastore | SQLite (`state.db`), single file, via SQLModel | Same single-file-bind pattern as `open-delete`'s `state.db` |
| Scheduler | APScheduler (AsyncIO), interval read from config | In-process, no extra infra; satisfies PRD R4 polling cadence |
| Remote access | `asyncssh` | Discovery (`find … -name .beads`) and status reads on Tailscale-reachable hosts |
| Beads ingestion | Adapter pair: `BdAdapter` (gastownhall `bd`, confirmed `bd status --json` schema) + `BrAdapter` (Dicklesworthstone `br`, schema TBD — confirm against a live install before implementing) | Tracked projects use a mix of both CLIs |
| GitHub activity | `httpx` against GitHub REST API — Releases (PRD R6) and commit/push timestamps (PRD R17) | Same client, two uses: release detection and staleness signal |
| Packaging | Dockerfile (`python:3.12-slim`), GHCR-published pinned image (primary), local build on the `docker` LXC (fast-iteration fallback) | Mirrors `open-delete`'s two documented build paths |

## 2. Deployment

Ships as a new service block inside the existing `apps-mine` module in `homelab-iac` (not its own module) — this repo holds only the application source.

- Subdomain: `projects.gnostichumor.app`
- Port: loopback-only on the `docker` LXC (Caddy fronts it), e.g. `127.0.0.1:8096:8000`
- Storage: `{ kind: file, host_path: state.db }` — same pattern as `open-delete`
- Secrets (Infisical, `/homelab/apps-mine`): namespaced to avoid colliding with `open-delete`'s existing keys — e.g. `PROJECTS_DASHBOARD_API_KEY`, plus an SSH private key for the multi-host scans

## 3. Config as source of truth

- **`config.yaml`** — every tunable: staleness threshold(s), poll interval, tracked SSH hosts/roots, GitHub check TTL, log level. Secret fields ship blank, overridden by env vars at deploy (same pattern as `open-delete`'s `config.yaml`).
- **`state.db`** (SQLite) — the tracked-project registry and per-project runtime state (snooze/archive/last-seen). This is user-generated data from PRD R3 ("add a project via the UI"), not tuning, so it doesn't belong in the static config file.

## 4. Frontend/API boundary rule

**Decided:** if this project ever justifies a public-facing rewrite (React + Storybook, mirroring the `offroad-hell` monorepo pattern), that conversion must be cheap. The way to guarantee that:

> The Jinja/HTMX UI must never read or write anything that isn't already exposed through the same versioned JSON API (`/api/v1/...`) that PRD R16 requires for external agents. No business logic (percent-complete calculation, staleness state transitions, threshold evaluation) may live only in a template or an HTMX-only route — it lives in the API layer, and the UI is just a consumer of it.

**Why:** the UI is deliberately thin (a percent-complete indicator, three status badges, a summary count, a login-time prompt) and isn't the hard part of this project — the ingestion/staleness logic is. Building a React+Storybook frontend now would pay real complexity (second toolchain, second test runtime, break the single-container deploy) for a hypothetical future ("if this becomes really helpful and I want to publicize it"), which conflicts with the PRD's own stated complexity discipline (§6: "chose the simpler v1 path... precision can be added once the core loop is validated").

**How to apply:** if/when public launch becomes a real, near-term goal — not before — stand up a separate `apps/web` (React+Vite) and `apps/storybook` against the existing `/api/v1/...` API, following the `offroad-hell` monorepo shape. Because the API boundary was enforced from v1, this is scoped as "build a new client for an existing, tested API," not a rewrite of the backend, adapters, or staleness logic.

## 5. Staleness signal reconciliation (PRD R17–R18)

Beads and GitHub activity are read independently and reconciled at the staleness-evaluation layer, not at ingestion:

- Each project's `ProjectStatus` carries two independent timestamps when available: `last_beads_activity_at` (from the `Bd`/`Br` adapter, §1) and `last_github_activity_at` (from the same GitHub `httpx` client used for R6, hitting the commits/branch API for the default branch's latest push).
- **Staleness is evaluated per signal, independently, against the threshold** — not by taking the freshest of the two. The project is flagged as stale as soon as *either* available signal is older than the threshold, even if the other signal is fresh. This is deliberately more sensitive than a `max()`-based comparison: the whole point of tracking two signals (R17) is to catch drift, e.g. active commits with a stale Beads tracker, or vice versa — a `max()` would hide exactly that case.
- The "last active" timestamp shown in the UI is a separate, display-only concern and can still use `max(last_beads_activity_at, last_github_activity_at)` for a human-readable "last touched" date — that's cosmetic, not the staleness gate.
- Projects with no `.beads/` directory (R18) simply have `last_beads_activity_at = None`; staleness is evaluated on GitHub activity alone. These projects skip percent-complete entirely (no beads to count) rather than rendering 0%.
- Projects with a GitHub remote that's unreachable/unknown (private repo not yet configured, no remote at all) fall back to Beads activity alone — same per-signal logic, just with one side `None`. A project with neither signal available can't be evaluated for staleness at all; flag this at discovery time rather than silently defaulting it to "fresh" or "stale."
