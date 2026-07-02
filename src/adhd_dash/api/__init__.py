"""Versioned JSON API package (`/api/v1/...`).

Per docs/architecture.md §4 (Frontend/API Boundary Rule), all business logic
lives behind these routes; the Jinja/HTMX UI is only ever a consumer of them.
"""
