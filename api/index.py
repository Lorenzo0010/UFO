import logging
import os
from typing import Dict, Optional, Any
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ============================================================================
# CONFIG
# ============================================================================
ADDON_NAME   = "UFO addon"
ADDON_LOGO   = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"
SC_DOMAIN    = os.getenv("SC_DOMAIN", "https://vixsrc.to")
TMDB_API_KEY = os.getenv("TMDB_KEY", "536b1c46da222eb34b69d168f092b495")
USER_AGENT   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"

# EasyProxy (MediaFlow Proxy) — necessario per far funzionare vixsrc da IP datacenter
EASYPROXY_URL = os.getenv("EASYPROXY_URL", "").rstrip("/")
EASYPROXY_PSW = os.getenv("EASYPROXY_PASSWORD", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ============================================================================
# EASYPROXY  —  parametro ?d= come usa streamvix (extractor.ts)
# ============================================================================
def build_easyproxy_url(vixsrc_page_url: str) -> str:
    """
    Costruisce URL EasyProxy nel formato usato da streamvix:
    EASYPROXY/proxy/hls/manifest.m3u8?d=<encoded_vixsrc_page>&api_password=<pw>
    EasyProxy fa lo scraping + fetch manifest + proxy automaticamente.
    """
    encoded = quote(vixsrc_page_url, safe="")
    url = f"{EASYPROXY_URL}/proxy/hls/manifest.m3u8?d={encoded}"
    if EASYPROXY_PSW:
        url += f"&api_password={quote(EASYPROXY_PSW, safe='')}"
    return url

# ============================================================================
# TMDB
# ============================================================================
async def get_tmdb_id(content_id: str, content_type: str) -> Optional[int]:
    """Risolve IMDb ID → TMDB ID se necessario."""
    if not content_id.startswith("tt"):
        try:
            return int(content_id)
        except ValueError:
            return None
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"https://api.themoviedb.org/3/find/{content_id}",
                params={
                    "external_source": "imdb_id",
                    "api_key": TMDB_API_KEY,
                    "language": "it",
                },
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                prefer = "tv_results" if content_type == "series" else "movie_results"
                fallback = "movie_results" if content_type == "series" else "tv_results"
                if data.get(prefer):
                    return data[prefer][0]["id"]
                if data.get(fallback):
                    return data[fallback][0]["id"]
    except Exception as e:
        logger.error(f"❌ TMDb error: {e}")
    return None

# ============================================================================
# STREAM RESOLVER
# ============================================================================
async def get_streams(stremio_id: str, content_type: str) -> Dict:
    result: Dict = {"streams": []}
    try:
        parts = stremio_id.split(":")
        content_id = parts[0]
        season  = parts[1] if len(parts) > 1 else None
        episode = parts[2] if len(parts) > 2 else None
        is_series = content_type == "series" and season and episode

        tmdb_id = await get_tmdb_id(content_id, content_type)
        if not tmdb_id:
            logger.warning(f"⚠️ TMDB ID non trovato per {content_id}")
            return result

        # URL pagina vixsrc.to — identico alla logica di extractor.ts
        if is_series:
            page_url = f"{SC_DOMAIN}/tv/{tmdb_id}/{season}/{episode}/"
        else:
            page_url = f"{SC_DOMAIN}/movie/{tmdb_id}/"

        logger.info(f"🎬 VixSrc page: {page_url}")

        if not EASYPROXY_URL:
            logger.error("❌ EASYPROXY_URL non configurato — nessuno stream possibile")
            return result

        # Costruisci URL EasyProxy con ?d= (come streamvix extractor.ts)
        stream_url = build_easyproxy_url(page_url)
        logger.info(f"✅ EasyProxy stream: {stream_url[:80]}...")

        result["streams"].append({
            "name": "🛸 UFO",
            "title": f"VixSrc • EasyProxy",
            "url": stream_url,
            "behaviorHints": {
                "notWebReady": False,   # EasyProxy rende il m3u8 web-ready
                "bingeGroup": "ufo-sc",
            },
        })
    except Exception as e:
        logger.error(f"❌ get_streams error: {e}")
    return result

# ============================================================================
# FASTAPI
# ============================================================================
app = FastAPI(title=f"{ADDON_NAME} Addon")
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
async def streams_route(request: Request, type: str, id: str):
    if type not in ["movie", "series"]:
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
