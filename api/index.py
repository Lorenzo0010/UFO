import base64
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from .config import ADDON_NAME, ADDON_LOGO
from .resolver import get_streams

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI(title=f"{ADDON_NAME} Addon")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"


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
    # Inietta base URL, nome e logo come meta tag prima di </head>
    meta_tags = (
        f'<meta name="base" content="{base}">\n'
        f'<meta name="logo" content="{ADDON_LOGO}">\n'
        f'<meta name="aname" content="{ADDON_NAME}">\n'
    )
    html = html.replace("</head>", meta_tags + "</head>", 1)
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
