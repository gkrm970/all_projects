from app.core.config import settings
from app.api.utils.loggers import logger
from tinaa.logger.v1.tinaa_logger import get_app_logger

logger.info("starting the app")

# Intercepting uvicorn logs
import logging

uvicorn_logger = logging.getLogger("uvicorn")
uvicorn_logger.handlers = logger.handlers
gunicorn_logger = logging.getLogger("uvicorn.access")
gunicorn_logger.handlers = logger.handlers

from fastapi.applications import FastAPI
from starlette.middleware.cors import CORSMiddleware

from starlette.staticfiles import StaticFiles
from starlette_exporter import PrometheusMiddleware, handle_metrics
from starlette.responses import RedirectResponse

from app.api.api_v1.api import api_router
from app.api.utils.exception_handler import register_handlers

from fastapi.openapi.docs import (
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)

app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=settings.API_VERSION_STR + "/openapi.json",
    version=settings.VERSION,
    docs_url=None,
    redoc_url=None,
)

# Register Custom Exception/Error Handlers
register_handlers(app)

app.include_router(api_router)

app.add_middleware(PrometheusMiddleware)
app.add_route(settings.API_VERSION_STR + "/metrics", handle_metrics)

# Set all CORS enabled origins
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin) for origin in settings.BACKEND_CORS_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# Off load static file from CDN for Prod
app.mount(
    settings.API_VERSION_STR + "/static",
    StaticFiles(directory="static", check_dir=False),
    name="static",
)


@app.get(settings.API_VERSION_STR + "/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json",
        title=app.title + " - Swagger UI",
        oauth2_redirect_url=settings.AUTH_REDIRECT_URL,
        swagger_js_url=settings.API_VERSION_STR + "/static/swagger-ui-bundle.js",
        swagger_css_url=settings.API_VERSION_STR + "/static/swagger-ui.css",
    )


@app.get(settings.AUTH_REDIRECT_URL, include_in_schema=False)
async def swagger_ui_redirect():
    return get_swagger_ui_oauth2_redirect_html()


@app.get("/", include_in_schema=False)
async def redirect():
    response = RedirectResponse(url=f"{settings.API_VERSION_STR}/docs")
    return response
