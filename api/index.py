import logging
import sys
from pathlib import Path
from typing import Any

# Fix import relativi per Vercel: aggiunge la cartella parent di api/ al path
# in modo che "from api.config import ..." funzioni come package
_root = Path(__file__).parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.config import ADDON_NAME, ADDON_LOGO, EASYPROXY_URL
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
    return respond_with({
        "status": "online",
        "addon": ADDON_NAME,
        "easyproxy": bool(EASYPROXY_URL),
        "manifest": f"{base}/U0MQ/manifest.json",
    })


@app.get("/U0MQ/manifest.json")
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


@app.get("/U0MQ/stream/{type}/{id}.json")
async def streams_route(type: str, id: str):
    if type not in ("movie", "series"):
        raise HTTPException(status_code=404)
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
