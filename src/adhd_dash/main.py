"""FastAPI app entrypoint.

Boot with: uvicorn adhd_dash.main:app
"""

from fastapi import FastAPI

from adhd_dash.api.v1 import router as api_v1_router


def create_app() -> FastAPI:
    """App factory: builds and returns the FastAPI application instance."""
    app = FastAPI(title="adhd-dash")
    app.include_router(api_v1_router)
    return app


app = create_app()
