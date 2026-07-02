# PRD: Project Status Dashboard for ADHD Developers

**Working name:** projects.gnostichumor.app
**Status:** Draft — synthesized from critical-thinking session, 07/01/2026
**Amended:** 07/02/2026 — added GitHub activity as a second staleness signal (R17–R18, §4.5); R17 revised same day to flag on either signal going stale, not only when both are silent. Also 07/02/2026: fixed working-name typo; Open Question 1 (R4 update mechanism) marked resolved per `docs/architecture.md` §1.
**Author:** Matt

---

## 1. Problem Statement

People with ADHD who run many concurrent development projects lose track of each project's current state. New projects get spun up quickly whenever a new idea appears, and older projects drift out of attention as "shinier" new ones take over — not because they're finished, but because they've been forgotten. The result is a growing pile of projects at unknown states of completion, with no single place to see them all.

All projects already use **Beads Rust** as the task/epic tracker. Introducing a traditional PM tool (e.g., Linear) on top of this creates duplicate bookkeeping and friction, so it's out of scope.

**Core insight from discussion:** the hard part isn't parsing task data (that's straightforward) — it's *aggregating and interpreting state across machines*, and specifically *surfacing entropy over time* so that stale projects resurface into attention. The dashboard is less a status board and more an **externalized memory system**: a deliberate counterweight to novelty-seeking, forcing periodic, low-effort confrontation with neglected work.

---

## 2. Goals

- Give a single-glance view of every project's state and completion percentage across all machines on the home network.
- Actively resurface stale/forgotten projects rather than passively displaying them.
- Let the user consciously and explicitly decide to stop tracking a project (archive), rather than letting it silently rot.
- Expose project status to external agents/tools, not just humans.

## 3. Non-Goals

- Replacing Beads Rust as the task-tracking system of record.
- Full PM features (assignments, sprints, cross-project dependencies, etc.).
- Real-time sync for all projects — cadence varies from multiple times/day to once/month, and the design should not assume urgency implies necessity of live sync.
- (v1) Adjusting stale thresholds based on *type* of last activity (e.g., commit vs. task update) — explicitly deferred for complexity reasons. (Distinct from R17/§4.5: using GitHub activity *as a signal source* is in scope for v1; varying the *threshold length* by activity type remains deferred.)

---

## 4. Requirements

### 4.1 Project Discovery & Ingestion
- **R1.** Homepage can be pointed at a folder on any computer on the home network (reachable via Tailscale).
- **R2.** System recursively scans that folder and subfolders to detect tracked projects, then surfaces them per the dashboard requirements below. A project is detected either by the presence of a `.beads/` directory or by being a Git repository with no Beads initialized — see §4.5 for how staleness and percent-complete behave when Beads is absent.
- **R3.** User can manually add a new directory/project to be tracked.
- **R4.** Statuses update on a regular cadence. *(Mechanism TBD — needs a follow-up design decision: polling interval, file-watcher, webhook from Beads, etc.)*

### 4.2 Dashboard / Home View
- **R5.** Snapshot view of all tracked projects, each showing a visual percent-complete indicator, calculated from completed vs. total beads/epics in that project.
- **R6.** Each project has one of three states: **In Discovery**, **In Development**, **Released**.
  - "Released" is tied directly to GitHub Releases. When a project is marked Released, the dashboard shows its version number and a link to the GitHub repo.
- **R7.** A summary UI component shows the count of projects in each state (Discovery / Development / Released).
- **R8.** Completed projects that are hosted somewhere (e.g., `llm.gnostichumor.app`) get a direct link from the dashboard to that live URL.

### 4.3 Staleness Detection & Resurfacing (core differentiator)
- **R9.** All state changes are treated with equal weight — the system does not prioritize by project volatility. Instead, staleness is measured by *absence* of updates, not urgency of activity. "State changes" includes both Beads issue activity and GitHub repo activity (commits/pushes) — see §4.5.
- **R10.** Staleness pressure is constant/persistent, not decaying — the point is to keep neglected projects visible until the user makes an explicit decision about them.
- **R11.** User can mark a project **Inactive/Archived** at any time. This is the only way to make the staleness pressure stop for that project — an intentional, explicit signal that the user has chosen to stop working on it (vs. simply forgetting).
- **R12.** On login, if a project has crossed its staleness threshold, the dashboard shows an **active prompt** (not passive/ambient) requiring the user to choose one of:
  - Set a reminder / snooze
  - Archive the project
- **R13.** Snoozing a reminder **resets** the staleness counter for that project. Archiving **turns off** the counter entirely.
- **R14.** Staleness threshold is **configurable per user**, with a **default of 2 weeks**.
- **R15.** Staleness threshold should ultimately vary **by project state** (Discovery vs. Development vs. Released likely warrant different default windows — e.g., Discovery/Development shorter, Released much longer, since released projects naturally see longer gaps). Exact per-state defaults are not yet finalized and need to be set deliberately, not just inherited from the 2-week default.
  - **v1 scope decision:** ship with a single global threshold first; layering in per-state thresholds and activity-type weighting is a fast-follow, not v1, to avoid front-loading configuration complexity.

### 4.4 External Access
- **R16.** A surfaced, queryable interface (API/endpoint) so external agents/tools can programmatically check the status of any tracked project — not just view it via the human UI.

### 4.5 Cross-Source Staleness Reconciliation

**Added 07/02/2026.** Trigger: Beads and GitHub activity can drift out of sync — a project can look stale in Beads (no issue updates) while real work is happening via commits with no matching issue update, or vice versa. Relying on Beads alone as the only staleness signal misses this, and also leaves projects where Beads was never initialized with no staleness signal at all.

- **R17.** Staleness is evaluated *independently per signal*: (a) last Beads issue activity (create/update/close) and (b) last GitHub activity on the project's repo (commit/push), when a GitHub remote is known. A project crosses its staleness threshold as soon as **either** available signal has been silent longer than the threshold — a fresh signal on one side does not mask staleness on the other. This is deliberately the more sensitive rule: the point of tracking two signals is to catch exactly the case where one side goes quiet while the other looks active.
- **R18.** Projects with no Beads initialized are still tracked and still subject to staleness, using GitHub activity as the sole signal. Percent-complete (R5) is not applicable for these projects — the dashboard shows them without a completion indicator (or an explicit "no task data" state) rather than defaulting to 0%, consistent with the zero-beads edge case in Open Question 5.

---

## 5. Open Questions / Known Gaps

These surfaced during discussion and are **not yet resolved** — flagging them explicitly rather than assuming answers:

1. **Update mechanism (R4):** How does the dashboard learn about state changes — polling, file watchers, git hooks, a push from Beads? *Resolved 2026-07-02 in `docs/architecture.md` §1: APScheduler (AsyncIO) polling on a configurable interval, not file-watchers/webhooks — avoids needing inotify/webhook infra across Tailscale-reachable hosts. Tracked as bd issue R4 (`adhd-dash-c6f.4`).*
2. **State-transition handling for staleness (R15/R16 interaction):** If a project moves from Development → Released, should its staleness counter reset? The concern raised: it shouldn't just inherit the (likely longer) Released threshold immediately if it was already stale in Development — that could mask real neglect. Needs an explicit rule.
3. **Per-state threshold values:** What should Discovery / Development / Released default windows actually be? Not yet decided by the user — needs real numbers before implementation, not just directional guesses.
4. **Reminder fatigue / gamification:** Flagged as a real risk (constant pressure to revisit stale work could itself become a source of friction). Needs research into ADHD-specific engagement patterns (gamification, streaks, etc.) before deciding on anything beyond the basic active-prompt-on-login flow. Explicitly out of v1 scope pending that research.
5. **Percent-complete calculation edge cases:** What happens with projects that have zero beads/epics yet (e.g., freshly created, still in ideation)? Not discussed — needs a default (likely "0% / not yet started" or excluded from percent display until beads exist). *Partially resolved by R18: projects with no Beads at all exclude the percent-complete indicator rather than showing 0%. The freshly-created-but-Beads-initialized case (zero issues yet) still needs a decision.*

---

## 6. Design Principles Extracted From Discussion

- **Equal treatment over prioritization:** don't try to be clever about weighting which staleness matters more — treat all silence the same and let the user decide what to do with it.
- **Active over passive:** for interrupting the "forgot this exists" failure mode, an active decision prompt outperforms a passive dashboard tile the user can ignore.
- **Complexity discipline:** repeatedly chose the simpler v1 path (single threshold before per-state; per-state before per-activity-type) to avoid a settings-heavy first release. Precision can be added once the core loop (constant pressure → active prompt → snooze/archive) is validated.
- **Intentionality as the exit condition:** the system's job isn't to guilt the user, it's to force a *conscious* choice — continue, snooze, or archive — rather than letting projects fade by default.

---

## 7. Out of Scope for v1 (explicit deferrals)

- Per-activity-type threshold weighting
- Gamification mechanics
- Per-project-state default thresholds (ship with one global default first)
