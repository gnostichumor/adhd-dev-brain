# adhd-dash

A project-status dashboard for ADHD developers — an externalized memory
system that surfaces stale/forgotten projects across machines instead of
letting them silently rot. Working name: `projects.gnostichumor.app`.

**Status:** pre-implementation. This repo currently holds the harness
(agent instructions, decision log, quality standards) and the `bd` issue
tracker seeded from the PRD — no application code yet.

## Start here

- [`docs/adhd-project-dashboard-prd.md`](docs/adhd-project-dashboard-prd.md) — product requirements
- [`docs/architecture.md`](docs/architecture.md) — implementation decisions (stack, deployment, config split, staleness logic)
- [`docs/quality-standards.md`](docs/quality-standards.md) — Definition of Done, verification gates
- [`DECISIONS.md`](DECISIONS.md) — chronological decision log with rationale
- [`CLAUDE.md`](CLAUDE.md) / [`AGENTS.md`](AGENTS.md) — agent instructions

## Task tracking

This repo tracks its own development in [`bd` (Beads)](https://github.com/gastownhall/beads), not a markdown TODO list:

```bash
bd ready      # what's unblocked right now
bd list       # everything, including blocked work
bd show <id>  # an issue's full Behavior/Verification/State spec
```

## Deployment

Ships as a service block inside the `apps-mine` module of the separate
`homelab-iac` repo — this repo holds only application source. See
`docs/architecture.md` §2.
