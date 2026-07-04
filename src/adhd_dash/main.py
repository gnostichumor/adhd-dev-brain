"""FastAPI app entrypoint.

Boot with: uvicorn adhd_dash.main:app
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from apscheduler.events import EVENT_JOB_ERROR, JobExecutionEvent
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import Engine

from adhd_dash.api.v1 import router as api_v1_router
from adhd_dash.config import Config, load_config
from adhd_dash.db import create_db_engine, init_db
from adhd_dash.polling import poll

logger = logging.getLogger(__name__)


def _log_poll_job_error(event: JobExecutionEvent) -> None:
    """APScheduler swallows a job's exception by default -- log it so a
    scheduled poll failure (e.g. the busy-timeout exceeded, adhd-dash-s85)
    is visible to an operator instead of silently disappearing."""
    logger.error("Scheduled poll job failed: %s", event.exception, exc_info=event.exception)


def build_scheduler(config: Config, engine: Engine) -> AsyncIOScheduler:
    """Build (but do not start) an `AsyncIOScheduler` running `poll` on the
    interval configured in `config.polling.interval_minutes` (PRD R4,
    adhd-dash-c6f.4).

    Kept separate from `lifespan` specifically so it can be unit tested
    without spinning FastAPI's lifespan/TestClient (which this codebase
    deliberately avoids in tests -- see tests/test_projects_api.py -- to
    keep from touching the real default `state.db`).

    Registers `_log_poll_job_error` on `EVENT_JOB_ERROR` so a scheduled
    poll's failure is logged rather than silently swallowed by APScheduler's
    default handling (adhd-dash-s85).
    """
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        poll,
        "interval",
        minutes=config.polling.interval_minutes,
        args=[config, engine],
        id="poll",
        # Prevent two SCHEDULED poll passes from ever overlapping each
        # other if one pass runs longer than the configured interval
        # (adhd-dash-v28). This does NOT protect against a scheduled poll
        # overlapping a manual `POST /api/v1/refresh` -- that route calls
        # `poll()` directly, bypassing this scheduler's job machinery
        # entirely. See the comment at that call site (api/v1.py's
        # `refresh`) for why that overlap is instead an accepted, bounded
        # race rather than something actively prevented.
        max_instances=1,
    )
    scheduler.add_listener(_log_poll_job_error, EVENT_JOB_ERROR)
    return scheduler


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Load config.yaml and initialize state.db before serving requests.

    Fails loudly if config.yaml is missing or invalid (see
    docs/architecture.md §3) rather than silently falling back to defaults
    -- but wraps the raw exception in a clearer message for the common local
    -dev mistake of running uvicorn from the wrong directory.
    """
    try:
        config = load_config()
    except FileNotFoundError as exc:
        raise RuntimeError(
            "config.yaml not found in the current working directory. "
            "adhd-dash requires a valid config.yaml at startup -- see "
            "config.yaml at the repo root for the expected shape, and run "
            "uvicorn from the repo root (or pass an absolute path via "
            "adhd_dash.config.load_config)."
        ) from exc
    except ValidationError as exc:
        raise RuntimeError(f"config.yaml failed validation:\n{exc}") from exc

    engine = create_db_engine(busy_timeout_seconds=config.db.busy_timeout_seconds)
    init_db(engine)

    app.state.config = config
    app.state.db_engine = engine

    scheduler = build_scheduler(config, engine)
    scheduler.start()
    app.state.scheduler = scheduler

    yield

    scheduler.shutdown()


def create_app() -> FastAPI:
    """App factory: builds and returns the FastAPI application instance."""
    app = FastAPI(title="adhd-dash", lifespan=lifespan)
    app.include_router(api_v1_router)
    return app


app = create_app()
