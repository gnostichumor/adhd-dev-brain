"""v1 API routes.

Only a health check exists here by design -- config/state, Beads adapters,
the GitHub client, and staleness evaluation each own their own routes and
land as those subsystems are implemented, not bundled in here ahead of time.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1")


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness check for the service."""
    return {"status": "ok"}
