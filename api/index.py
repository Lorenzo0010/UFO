import base64
import json
import logging
import sys
import os
from pathlib import Path
from string import Template
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from mangum import Mangum

# Assicura che la cartella api/ sia nel path per gli import assoluti
api_dir = Path(__file__).parent
if str(api_dir) not in sys.path:
    sys.path.insert(0, str(api_dir))

from config import ADDON_NAME, ADDON_LOGO  # noqa: E402
from resolver import get_streams  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI(title=f"{ADDON_NAME} Addon")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

STATIC_DIR = api_dir / "static"

# Handler per Vercel (ASGI -> Lambda-style)
handler = Mangum(app, lifespan="off")


def respond_with(data: Any) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


def decode_config(token: str) -> dict:
    rem = len(token) % 4
    if rem:
        token += "=" * (4 - rem)
    try:
        return json.loads(base64.urlsafe_b64decode(token).decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Token non valido")


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    base = str(request.base_url).rstrip("/")
    html = (STATIC_DIR / "config.html").read_text(encoding="utf-8")
    html = Template(html).safe_substitute(
        BASE=base,
        ADDON_NAME=ADDON_NAME,
        ADDON_LOGO=ADDON_LOGO,
    )
    return HTMLResponse(content=html)


@app.get("/{token}/manifest.json")
async def manifest(token: str):
    cfg = decode_config(token)
    proxy_url = cfg.get("u", "")
    return respond_with({
        "id": f"org.stremio.ufo.{token[:12]}",
        "version": "2.0.0",
        "name": ADDON_NAME,
        "description": f"VixSrc via {proxy_url or 'proxy'}",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": [],
        "behaviorHints": {
            "configurable": False,
            "configurationRequired": False,
        },
    })


@app.get("/{token}/stream/{type}/{id}.json")
async def streams_route(token: str, type: str, id: str):
    if type not in ("movie", "series"):
        raise HTTPException(status_code=404)
    cfg = decode_config(token)
    proxy_url = cfg.get("u", "")
    proxy_psw = cfg.get("p", "")
    if not proxy_url:
        raise HTTPException(status_code=400, detail="Proxy URL mancante")
    try:
        data = await get_streams(id, type, proxy_url, proxy_psw)
    except Exception as e:
        logging.error(f"streams error: {e}")
        data = {"streams": []}
    return respond_with(data)


@app.get("/{token}/meta/{type}/{id}.json")
async def meta(token: str, type: str, id: str):
    return respond_with({"meta": {"id": id, "type": type, "name": ADDON_NAME, "poster": ADDON_LOGO}})


@app.get("/{token}/catalog/{type}/{id}.json")
async def catalog(token: str, type: str, id: str):
    return respond_with({"metas": []})
