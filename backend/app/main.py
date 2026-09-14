import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import Base, engine
from .routers import auth, services

# Overridable so the packaged (PyInstaller) app can point at its bundled dist.
DIST = Path(
    os.environ.get("FRONTEND_DIST")
    or Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Simplispace API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(services.router)


@app.get("/health")
def health():
    return {"status": "ok"}


# Serve the built frontend (desktop / single-origin mode). API routes above win;
# unknown paths fall through to the SPA. Absent in dev — use the Vite server then.
if DIST.exists():
    app.mount("/", StaticFiles(directory=str(DIST), html=True), name="frontend")
