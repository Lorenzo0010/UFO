import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import ADDON_NAME, ADDON_LOGO, EASYPROXY_URL, set_base_url
from .resolver import get_streams
from .proxy import router as proxy_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI(title=f"{ADDON_NAME} Addon")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Monta le route del proxy integrato
app.include_router(proxy_router)


def respond_with(data: Any) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.get("/")
async def root(request: Request):
    base = str(request.base_url).rstrip("/")
    set_base_url(base)  # auto-detect base URL alla prima richiesta
    proxy_mode = "external" if EASYPROXY_URL else "integrated"
    return respond_with({
        "status": "online",
        "addon": ADDON_NAME,
        "proxy_mode": proxy_mode,
        "proxy_base": EASYPROXY_URL or base,
        "manifest": f"{base}/U0MQ/manifest.json",
    })


@app.get("/U0MQ/manifest.json")
async def manifest():
    return respond_with({
        "id": "org.stremio.mammamia.ufo",
        "version": "1.5.0",
        "name": ADDON_NAME,
        "description": "VixSrc · Proxy integrato",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": [],
        "behaviorHints": {"configurable": False},
    })


@app.get("/U0MQ/stream/{type}/{id}.json")
async def streams_route(request: Request, type: str, id: str):
    if type not in ("movie", "series"):
        raise HTTPException(status_code=404)
    base = str(request.base_url).rstrip("/")
    set_base_url(base)
    try:
        data = await get_streams(id, type)
    except Exception:
        data = {"streams": []}
    return respond_with(data)


@app.get("/U0MQ/meta/{type}/{id}.json")
async def meta(type: str, id: str):
    return respond_with({"meta": {"id": id, "type": type, "name": ADDON_NAME, "poster": ADDON_LOGO}})


@app.get("/U0MQ/catalog/{type}/{id}.json")
async def catalog(type: str, id: str):
    return respond_with({"metas": []})
