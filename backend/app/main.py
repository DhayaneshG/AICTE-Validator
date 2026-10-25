"""AICTE EoA validator - FastAPI entrypoint.

API lives under /api; the built React frontend is served at "/".
Findings are advisory indicators for human review, never proof of misconduct.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..services import scheduler
from .db import init_db
from .routes import alerts, applications, audit, auth_routes, documents, findings, meta, reports

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()


app = FastAPI(title="AICTE EoA Validator (MVP, synthetic data)", version="0.9.0", lifespan=lifespan)
api = APIRouter(prefix="/api")


@api.get("/health")
def health():
    return {"status": "ok", "advisory": "Findings are advisory indicators for human review."}


api.include_router(meta.router)
api.include_router(auth_routes.router)
api.include_router(alerts.router)
api.include_router(applications.router)
api.include_router(documents.router)
api.include_router(findings.router)
api.include_router(audit.router)
api.include_router(reports.router)
app.include_router(api)


@app.get("/health")
def health_root():
    return health()


if FRONTEND_DIST.exists():
    assets = FRONTEND_DIST / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404)
        f = FRONTEND_DIST / full_path
        if full_path and f.is_file():
            return FileResponse(f)
        return FileResponse(FRONTEND_DIST / "index.html")
