import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import ADDON_NAME, ADDON_LOGO, ADDON_PATH, EASYPROXY_URL, validate_config
from .resolver import get_streams
from .tmdb import close_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

P = f"/{ADDON_PATH}"  # prefisso route, es. "/U0MQ"


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_config()
    logger.info(f"🛸 Addon path: {P}")
    yield
    await close_session()
    logger.info("🔌 Sessione HTTP chiusa")


app = FastAPI(title=f"{ADDON_NAME} Addon", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        "easyproxy": bool(EASYPROXY_URL),
        "manifest": f"{base}{P}/manifest.json",
    })


@app.get(P + "/manifest.json")
async def manifest():
    return respond_with({
        "id": "org.stremio.mammamia.ufo",
        "version": "1.4.0",
        "name": ADDON_NAME,
        "description": "VixSrc via EasyProxy",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": [],
        "behaviorHints": {"configurable": False},
    })


@app.get(P + "/stream/{type}/{id}.json")
async def streams_route(type: str, id: str):
    if type not in ("movie", "series"):
        raise HTTPException(status_code=404)
    try:
        data = await get_streams(id, type)
    except Exception:
        logger.exception(f"❌ Errore non gestito in streams_route per id={id!r} type={type!r}")
        data = {"streams": []}
    return respond_with(data)


@app.get(P + "/meta/{type}/{id}.json")
async def meta(type: str, id: str):
    return respond_with({"meta": {"id": id, "type": type, "name": ADDON_NAME, "poster": ADDON_LOGO}})


@app.get(P + "/catalog/{type}/{id}.json")
async def catalog(type: str, id: str):
    return respond_with({"metas": []})
