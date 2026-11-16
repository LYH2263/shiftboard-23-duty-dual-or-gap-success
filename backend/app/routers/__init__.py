from fastapi import APIRouter

from app.routers import batches, dashboard, handovers, reconcile, shifts, zones

api = APIRouter(prefix="/api")
api.include_router(dashboard.router)
api.include_router(shifts.router)
api.include_router(batches.router)
api.include_router(handovers.router)
api.include_router(reconcile.router)
api.include_router(zones.router)
