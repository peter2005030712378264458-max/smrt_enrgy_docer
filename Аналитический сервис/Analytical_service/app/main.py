from __future__ import annotations

from fastapi import FastAPI

from app.api.routes import router


app = FastAPI(title="Smart Energy Analytical Service")
app.include_router(router)
