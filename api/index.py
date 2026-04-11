import base64
import json
import logging
import sys
from pathlib import Path
from typing import Any

# Aggiunge api/ al path PRIMA di qualsiasi altro import
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum

import config as cfg
from resolver import get_streams

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = FastAPI(title=f"{cfg.ADDON_NAME} Addon")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

# Vercel ASGI handler
handler = Mangum(app, lifespan="off")


def respond_with(data: Any) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.get("/manifest.json")
async def manifest():
    return respond_with({
        "id": "org.stremio.ufo",
        "version": "2.0.0",
        "name": cfg.ADDON_NAME,
        "description": "Stream da VixSrc via MediaFlow proxy",
        "logo": cfg.ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": [],
        "behaviorHints": {
            "configurable": False,
            "configurationRequired": False,
        },
    })


@app.get("/stream/{type}/{id}.json")
async def streams_route(type: str, id: str):
    if type not in ("movie", "series"):
        raise HTTPException(status_code=404)
    if not cfg.PROXY_URL:
        raise HTTPException(status_code=500, detail="PROXY_URL non configurato")
    try:
        data = await get_streams(id, type, cfg.PROXY_URL, cfg.PROXY_PSW)
    except Exception as e:
        logging.error(f"streams error: {e}")
        data = {"streams": []}
    return respond_with(data)


@app.get("/meta/{type}/{id}.json")
async def meta(type: str, id: str):
    return respond_with({"meta": {"id": id, "type": type, "name": cfg.ADDON_NAME}})


@app.get("/catalog/{type}/{id}.json")
async def catalog(type: str, id: str):
    return respond_with({"metas": []})
