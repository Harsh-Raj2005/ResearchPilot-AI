"""
FastAPI application entrypoint.

Task 1 scope: app factory, CORS, and the health router only.
Later tasks register app.api.auth / app.api.documents / app.api.chat
here — this file should stay this short even as the project grows;
each router owns its own logic.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.core.config import settings


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
