"""FastAPI app entrypoint.

Boot with: uvicorn adhd_dash.main:app
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import ValidationError

from adhd_dash.api.v1 import router as api_v1_router
from adhd_dash.config import load_config
from adhd_dash.db import create_db_engine, init_db


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

    engine = create_db_engine()
    init_db(engine)

    app.state.config = config
    app.state.db_engine = engine

    yield


def create_app() -> FastAPI:
    """App factory: builds and returns the FastAPI application instance."""
    app = FastAPI(title="adhd-dash", lifespan=lifespan)
    app.include_router(api_v1_router)
    return app


app = create_app()
