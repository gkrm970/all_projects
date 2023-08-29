from fastapi.routing import APIRouter

from app.api.api_v1.endpoints import mirrormaker_service

api_router = APIRouter()
api_router.include_router(mirrormaker_service.router, tags=["MirrorMaker"])
