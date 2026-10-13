"""FastAPI application factory.

Environment variables
---------------------
TEGLAB_DB      SQLite file for imported cases/materials (default: ./data/teglab.db)
TEGLAB_STATIC  directory with the built Angular app to serve on "/" (optional)
TEGLAB_CORS    comma-separated allowed origins (default: http://localhost:4200)
TEGLAB_SEED    "0" disables loading the example cases on first start
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .. import __version__
from ..demo import seed
from ..store import Store
from .routes import router


def create_app(db_path: str | None = None, static_dir: str | None = None, seed_demo: bool | None = None) -> FastAPI:
    db = db_path or os.environ.get("TEGLAB_DB", "data/teglab.db")
    static = static_dir or os.environ.get("TEGLAB_STATIC")
    do_seed = seed_demo if seed_demo is not None else os.environ.get("TEGLAB_SEED", "1") != "0"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.store = Store(db)
        if do_seed:
            seed(app.state.store)
        yield

    app = FastAPI(
        title="teglab API",
        version=__version__,
        description="Thermoelectric pair analysis: ANSYS import, 1D model calibration, geometry/material "
        "optimization and lab experiment planning.",
        lifespan=lifespan,
    )
    origins = [o.strip() for o in os.environ.get("TEGLAB_CORS", "http://localhost:4200").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(KeyError)
    async def key_error(_: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc).strip("'\"")})

    app.include_router(router)

    if static and Path(static).is_dir():
        root = Path(static).resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(404)
            candidate = (root / full_path).resolve()
            if full_path and candidate.is_file() and root in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(root / "index.html")

    return app


app = create_app()
