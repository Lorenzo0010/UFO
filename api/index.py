import logging
import sys
from pathlib import Path
from typing import Any

_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.config import ADDON_NAME, ADDON_LOGO, EASYPROXY_URL, MEDIAFLOW_URL
from api.resolver import get_streams

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI(title=f"{ADDON_NAME} Addon")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)


def respond_with(data: Any) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.get("/")
async def root(request: Request):
    base = str(request.base_url).rstrip("/")
    proxy_mode = "easyproxy" if EASYPROXY_URL else ("mediaflow" if MEDIAFLOW_URL else "none")
    return respond_with({
        "status": "online",
        "addon": ADDON_NAME,
        "proxy_mode": proxy_mode,
        "manifest": f"{base}/U0MQ/manifest.json",
    })


@app.get("/U0MQ/manifest.json")
async def manifest():
    proxy_mode = "EasyProxy" if EASYPROXY_URL else ("MediaFlow" if MEDIAFLOW_URL else "no proxy")
    return respond_with({
        "id": "org.stremio.mammamia.ufo",
        "version": "1.6.0",
        "name": ADDON_NAME,
        "description": f"VixSrc via {proxy_mode}",
        "logo": ADDON_LOGO,
        "resources": [
            {
                "name": "stream",
                "types": ["movie", "series"],
                "idPrefixes": ["tt", "tmdb"]
            }
        ],
        "types": ["movie", "series"],
        "catalogs": [],
        "behaviorHints": {
            "configurable": False,
            "adult": False
        },
    })


@app.get("/U0MQ/stream/{type}/{id}.json")
async def streams_route(type: str, id: str):
    if type not in ("movie", "series"):
        raise HTTPException(status_code=404)
    try:
        data = await get_streams(id, type)
    except Exception:
        data = {"streams": []}
    # cacheMaxAge=0 forza Stremio a non cachare i token VixSrc (scadono in ~10 min)
    data["cacheMaxAge"] = 0
    data["staleRevalidate"] = 0
    data["staleError"] = 0
    return respond_with(data)


@app.get("/U0MQ/meta/{type}/{id}.json")
async def meta(type: str, id: str):
    return respond_with({"meta": {"id": id, "type": type, "name": ADDON_NAME, "poster": ADDON_LOGO}})


@app.get("/U0MQ/catalog/{type}/{id}.json")
async def catalog(type: str, id: str):
    return respond_with({"metas": []})
