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
| Beads ingestion | Adapter pair: `BdAdapter` (gastownhall `bd`, confirmed `bd status --json` schema) + `BrAdapter` (Dicklesworthstone `br`, schema confirmed against a live v0.2.15 install — see §1a) | Tracked projects use a mix of both CLIs |
| GitHub activity | `httpx` against GitHub REST API — Releases (PRD R6) and commit/push timestamps (PRD R17) | Same client, two uses: release detection and staleness signal |
| Packaging | Dockerfile (`python:3.12-slim`), GHCR-published pinned image (primary), local build on the `docker` LXC (fast-iteration fallback) | Mirrors `open-delete`'s two documented build paths |

## 1a. `br` JSON schema (confirmed)

Verified against a live `br` v0.2.15 install with 67 real issues (mixed
open/closed, some with dependency chains), corroborated with `br schema issue
--format json` (br's own self-described contract). `br schema` is explicitly
non-stable API ("subject to change" per `br schema --help`), so treat this as
a snapshot pinned to v0.2.15, not a permanent guarantee — re-verify on `br`
upgrades.

**Fields that map 1:1 to `bd`'s schema (same names, same apparent meaning):**
`id`, `title`, `description`, `acceptance_criteria`, `status`, `priority`,
`issue_type`, `owner`, `assignee`, `created_at`, `created_by`, `updated_at`,
`closed_at`, `close_reason`, `labels`. `owner`/`assignee` were both present
as distinct fields in the schema, matching `bd`'s two-concept model — but
both were `null` in every sampled issue (no fleet/multi-agent usage in this
project), so their real-world value convention is unconfirmed.

**Enum differences (background only — NOT a `BrAdapter` v1 blocker, see §1b):**
- `status`: `br` has 8 values — `open`, `in_progress`, `blocked`,
  `deferred`, `draft`, `closed`, `tombstone`, `pinned` (a status *value* —
  see §1b for why this is not the same thing as the `pinned` *field* listed
  below) — vs `bd`'s 3 (`open`, `in_progress`, `closed`). `blocked`,
  `deferred`, `draft`, `tombstone`, and `pinned` have no `bd` equivalent.
- `issue_type`: `br` adds `docs` and `question` on top of the `epic`,
  `feature`, `task`, `chore`, `bug` set `bd` uses.
- `priority`: `br` documents `0=Critical … 4=Backlog` (int) in its schema.
  `bd`'s priority is also an int in observed output, but its own 0–4
  convention hasn't been independently confirmed in this project's `bd`
  usage — assume parity, confirm separately if `BrAdapter` needs to compare
  priorities across CLIs.

**`bd` fields `br` has no equivalent of:**
- `started_at` — no such field in `br`'s `Issue` schema. `BrAdapter` should
  default this to `None`, or (lower priority) derive it from the issue's
  `events` array on `show` output if a status-transition-to-`in_progress`
  event is ever needed.
- `comment_count` — `br` has no scalar count; it exposes a `comments` array
  (present on `show`/issue-details output, omitted entirely when empty).
  `BrAdapter` should default to `0` when the key is absent, else `len(comments)`.

**`br`-only fields with no `bd` equivalent:** `design`, `notes`,
`estimated_minutes`, `closed_by_session`, `due_at`, `defer_until`,
`external_ref`, `source_system`, `source_repo`, `source_repo_path`,
`agent_context`, `deleted_at`/`deleted_by`/`delete_reason` (tombstone
soft-delete metadata), `compaction_level`/`compacted_at`/`original_size`,
`ephemeral`, `pinned`, `is_template`. **This `pinned` is a distinct boolean
field, not the same thing as the `pinned` *status value* listed above** —
see §1b for the disambiguation. None of these fields have an obvious
`ProjectStatus` field to map to; `BrAdapter` can ignore them for v1. Full
list: `br schema issue --format json`.

**Gotchas for `BrAdapter` implementation (found empirically, not just from
the schema export):**
- **Absent fields are omitted from the JSON entirely on `null`/empty, not
  emitted as `"field": null`.** E.g. an open issue with no assignee has no
  `assignee` key at all. `BrAdapter` must default-on-missing-key, not assume
  every schema field is always present.
- **`br show <id> --json` returns a JSON array (`[{...}]`), even for a
  single ID** — the command accepts multiple IDs positionally. `bd show`
  (per this project's existing usage) returns a bare object. `BrAdapter`
  must unwrap `[0]`.
- **Dependency shape differs by command.** `br list --json` rows carry
  integer `dependency_count`/`dependent_count` (matching `bd`'s convention).
  `br show --json` (issue-details) instead carries full `dependencies`/
  `dependents` arrays of `{id, title, status, priority, dependency_type}`
  objects — no integer count field there. `BrAdapter` needs to read counts
  from `list` and, if it ever consumes `show`, derive counts via array
  length instead.
- **A `br`-managed project's issue-ID prefix is configurable and is not a
  reliable signal of which CLI (`bd` vs `br`) manages it** — a `br` project
  can use a `bd-`-style prefix. Adapter selection must be based on which
  binary/config is present, never inferred from ID string shape.
- **`br list --format json`'s response is a wrapper object, not a bare
  array** — `{"issues": [...], "total", "limit", "offset", "has_more"}`.
  This differs from `bd list --json`, which returns a bare array directly.
  `BrAdapter` must unwrap `.issues` before indexing; `json.loads(...)` alone
  is not enough the way it is for `bd`.
- **`br` has no `-C`/`--directory` global flag** — confirmed empirically:
  `br -C <path> status --json` errors `unexpected argument '-C' found`. `br
  --help` only exposes `--db <path>`, an explicit database *file* path, not
  a project directory. `BrAdapter` targets a project by other means instead:
  locally, it passes `path` as the subprocess's `cwd` (so `br` auto-discovers
  `.beads/*.db` the way it would run by hand from inside the project);
  remotely (over `asyncssh`), it builds `cd <path> && <argv>` (both
  `shlex`-quoted) rather than appending `-C <path>` the way `BdAdapter`'s
  remote command builder does.
- **`br list --sort updated_at` defaults to descending (most-recent-first)**
  — confirmed empirically against a live install: without `--reverse`, the
  first two results were the two newest `updated_at` values; with
  `--reverse`, the first result was the oldest. Same convention `bd`'s
  `--sort updated` uses (also confirmed empirically, see `BdAdapter`).
  `--reverse` must NOT be passed when the goal is "most recent activity."

## 1b. `BrAdapter` implementation decisions (resolves `adhd-dash-ggq`)

**`ProjectStatus` v1 needs no per-issue `status`/`issue_type` enum mapping
between `bd` and `br`.** `percent_complete` and `last_beads_activity_at` —
the only two fields a Beads adapter populates — are each computed from a
single pre-computed aggregate the CLI itself already produces
(`summary.total_issues`/`summary.closed_issues` for the former, the single
most-recently-updated issue's `updated_at` for the latter), never by
iterating issues and reclassifying each one's `status`/`issue_type` into a
`bd`-shaped bucket. Both aggregate field names are confirmed identical
across `bd` and `br` (§1a above), so `BrAdapter` reads them the same way
`BdAdapter` does, with zero translation logic. The `status`/`issue_type`
enum differences documented in §1a remain useful background for any *future*
feature that does need per-issue classification (e.g. a per-issue detail
view), but they are not a blocker for `BrAdapter` and `ProjectStatus` v1 —
this is the concrete resolution `adhd-dash-ggq` asked for.

**`pinned` names two different things, not one.** §1a's "Enum differences"
list includes `pinned` as one of `status`'s 8 possible *values* (an issue
can be in the `pinned` status, alongside `open`/`closed`/etc.). Separately,
§1a's "`br`-only fields" list includes `pinned` as a distinct boolean
*field* name (grouped with `ephemeral`/`is_template`) — an issue has a
`pinned: true/false` attribute independent of what `status` value it's in.
These share a name by coincidence of `br`'s own schema, not because they're
the same concept; neither is used by `BrAdapter`/`ProjectStatus` v1 either
way.

**`br`'s timestamp format is confirmed, closing the hedge `adhd-dash-8d2.3`
left in `ProjectStatus.last_beads_activity_at`'s docstring.** Verified
against the same live `br` v0.2.15 install as the rest of §1a: `br` emits
`Z`-suffixed ISO8601 timestamps with microsecond fractional seconds (e.g.
`"2026-06-15T04:58:18.381241Z"`), which `datetime.fromisoformat` parses
directly into a timezone-aware UTC `datetime` on Python 3.12 — same format
class as `bd`'s (`bd` lacks the fractional-second component but is
otherwise identical), pinned to v0.2.15 per this doc's usual re-verify-on-
upgrade caveat.

**`total_issues` excludes tombstoned issues, confirmed empirically.** `br`
has two statuses `bd` doesn't (`tombstone`, `draft`) whose effect on
`summary.total_issues` was an open question for `percent_complete`'s
denominator (a tombstoned/draft issue inflating `total_issues` without
being reflected in `closed_issues` would skew the ratio). Verified against
a throwaway `br init`'d project: creating 2 issues gave `total_issues: 2`;
deleting (`br delete`, which tombstones) one dropped it to `total_issues: 1`
with `tombstone_issues: 1` tracked as its own separate counter — tombstoned
issues are excluded from `total_issues`, not counted in it. `draft`'s effect
is unverified (no `br` command was found to create a draft issue in this
pass) but is assumed to follow the same exclude-from-total pattern until
observed otherwise; `BrAdapter` takes no special action for either since
`total_issues`/`closed_issues` are read as-is from `br`'s own summary.

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

## 6. Polling cadence and scope (PRD R4, adhd-dash-c6f.4)

An `AsyncIOScheduler` (`build_scheduler` in `adhd_dash.main`) is started in `lifespan` and shut down on app shutdown, running `adhd_dash.polling.poll` on an interval read from `config.polling.interval_minutes`. `build_scheduler` is kept separate from `lifespan` so it's unit-testable without spinning FastAPI's lifespan/`TestClient` (this codebase deliberately avoids that context-manager form in tests to keep from touching the real default `state.db`). `POST /api/v1/refresh` runs the identical `poll()` pass synchronously, for an out-of-band manual trigger.

`poll()` does exactly two things per configured host/root: re-run `discover_projects` and get-or-create a `TrackedProject` row per match (shared idempotency logic, extracted into `adhd_dash.projects.get_or_create_project` so the manual `POST /api/v1/projects` route and the poll pass don't duplicate the `UniqueConstraint("host", "path")` race-condition handling), then stamp `last_seen_at` on every row touched. No new `last_polled_at` field was added — `last_seen_at`'s existing intent ("last-seen bookkeeping") already means "discovery confirmed this project is still present," which is exactly what a poll pass produces, so it now doubles as the last-poll timestamp.

**Scope, deliberately narrow:** discovery here is local-filesystem-only, matching `discover_projects`'s own documented scope (adhd-dash-c6f.2) — for a `HostConfig` whose `roots` live on a genuinely remote Tailscale host, this pass walks the *dashboard process's own* local filesystem at that path string, which is only correct when the root happens to be reachable locally (same-host deployment, or a bind mount). Real remote directory listing would need a filesystem-walking analog of the asyncssh-based runner `BdAdapter`/`BrAdapter` already use for status, which doesn't exist yet. Separately, this pass does not perform any Beads or GitHub status ingestion (percent-complete, activity timestamps) — that's blocked on two prerequisites not yet in the data model: a stored signal for which Beads CLI variant (`bd` vs `br`) a given `TrackedProject` uses, and a stored GitHub remote/repo association per project. Adding either now would be scope creep for a polling-cadence issue. Consequently, `adhd-dash-v3d` (GithubClient not distinguishing rate-limit responses from genuine no-data) remains open/deferred: its acceptance criteria's fix-condition is "wired into a real poll loop," which this issue does not do.

**`last_seen_at` is permanently `None` for manually-added, out-of-root projects, by design:** `POST /api/v1/projects` (`get_or_create_project` in `adhd_dash.projects`) never stamps `last_seen_at`, and manual-add exists specifically so a project *outside* every configured `HostConfig.roots` can be tracked (PRD R3, adhd-dash-c6f.3) — so that row is structurally unreachable by `poll()`'s discovery walk and `last_seen_at` will stay `None` forever, not just until the next poll. This is harmless today because nothing in the codebase reads `last_seen_at` for any decision. It stops being harmless the moment something does — e.g. the staleness-detection epic (adhd-dash-oui) — at which point that consumer must explicitly decide whether "manually-added, never polled" should count as stale or be exempted; this section is not answering that question, only recording that the gap is deliberate rather than an oversight.

**Scheduled-poll-vs-manual-refresh overlap is an accepted, bounded race (adhd-dash-v28):** `build_scheduler` registers the poll job with `max_instances=1`, so two *scheduled* passes can never overlap each other, but `POST /api/v1/refresh` calls `poll()` directly rather than going through the scheduler, so it can still race a scheduled pass. Rather than adding a cross-process lock for this single-operator home-lab tool, the race is bounded instead: `poll()` commits per-project rather than once for the whole pass (so one project's conflict can't roll back `last_seen_at` stamps already committed for earlier, unrelated projects in the same pass), and `create_db_engine` sets a 5s SQLite busy-timeout so a concurrent writer waits briefly before `/refresh` falls back to returning a `503` instead of an unhandled 500.
