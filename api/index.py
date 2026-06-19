import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import ADDON_NAME, ADDON_LOGO, validate_config
from .proxy import router as proxy_router, close_proxy_client
from .resolver import get_streams
from .tmdb import close_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_config()
    yield
    await close_session()
    await close_proxy_client()
    logger.info("🔌 Sessioni HTTP chiuse")


app = FastAPI(title=f"{ADDON_NAME} Addon", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registra il proxy HLS interno
app.include_router(proxy_router)


def respond_with(data: Any) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.get("/")
async def root(request: Request):
    base = str(request.base_url).rstrip("/")
    return respond_with({
        "status": "online",
        "addon": ADDON_NAME,
        "proxy": "internal",
        "manifest": f"{base}/manifest.json",
    })


@app.get("/manifest.json")
async def manifest():
    return respond_with({
        "id": "org.stremio.mammamia.ufo",
        "version": "1.6.0",
        "name": ADDON_NAME,
        "description": "VixSrc con proxy HLS interno",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": [],
        "behaviorHints": {"configurable": False},
    })


@app.get("/stream/{type}/{id}.json")
async def streams_route(type: str, id: str, request: Request):
    if type not in ("movie", "series"):
        raise HTTPException(status_code=404)
    try:
        base = str(request.base_url).rstrip("/")
        data = await get_streams(id, type, addon_base_url=base)
    except Exception:
        logger.exception(f"❌ Errore non gestito in streams_route per id={id!r} type={type!r}")
        data = {"streams": []}
    return respond_with(data)


@app.get("/meta/{type}/{id}.json")
async def meta(type: str, id: str):
    return respond_with({"meta": {"id": id, "type": type, "name": ADDON_NAME, "poster": ADDON_LOGO}})


@app.get("/catalog/{type}/{id}.json")
async def catalog(type: str, id: str):
    return respond_with({"metas": []})
