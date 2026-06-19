import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import ADDON_NAME, ADDON_LOGO, IPTV_URLS, IPTV_PAGE_SIZE, validate_config
from .iptv import get_all_channels, get_channel_by_id, get_channels_by_group, get_groups
from .proxy import router as proxy_router, close_proxy_client
from .resolver import get_streams
from .tmdb import close_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_config()
    # Pre-carica i canali IPTV in background all'avvio
    try:
        channels = await get_all_channels(IPTV_URLS)
        logger.info(f"📺 Pre-caricati {len(channels)} canali IPTV all'avvio")
    except Exception as e:
        logger.warning(f"⚠️  Impossibile pre-caricare canali IPTV: {e}")
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


# ── Root ─────────────────────────────────────────────────────────────────────

@app.get("/")
async def root(request: Request):
    base = str(request.base_url).rstrip("/")
    return respond_with({
        "status": "online",
        "addon": ADDON_NAME,
        "proxy": "internal",
        "manifest": f"{base}/manifest.json",
    })


# ── Manifest ─────────────────────────────────────────────────────────────────

@app.get("/manifest.json")
async def manifest():
    return respond_with({
        "id": "org.stremio.mammamia.ufo",
        "version": "2.0.0",
        "name": ADDON_NAME,
        "description": "VixSrc con proxy HLS interno + Live TV IPTV italiana",
        "logo": ADDON_LOGO,
        "resources": ["stream", "meta", "catalog"],
        "types": ["movie", "series", "tv"],
        "catalogs": [
            {
                "type": "tv",
                "id": "iptv-livetv",
                "name": "📺 Live TV Italia",
                "extra": [
                    {"name": "genre", "isRequired": False},
                    {"name": "skip", "isRequired": False},
                ],
                "genres": [],  # popolato dinamicamente ma Stremio accetta lista vuota
            }
        ],
        "behaviorHints": {"configurable": False},
    })


# ── Catalog Live TV ───────────────────────────────────────────────────────────

@app.get("/catalog/tv/iptv-livetv.json")
async def catalog_tv(
    genre: Optional[str] = Query(default=None),
    skip: int = Query(default=0, ge=0),
):
    """Catalogo canali IPTV. Supporta filtro per genere/gruppo e paginazione."""
    channels = await get_channels_by_group(
        IPTV_URLS,
        group=genre,
        skip=skip,
        limit=IPTV_PAGE_SIZE,
    )
    metas = [
        {
            "id": ch["id"],
            "type": "tv",
            "name": ch["name"],
            "poster": ch["logo"] or ADDON_LOGO,
            "background": ch["logo"] or ADDON_LOGO,
            "logo": ch["logo"] or ADDON_LOGO,
            "genres": [ch["group"]],
            "description": f"Gruppo: {ch['group']} | Sorgente: {ch['source']}",
        }
        for ch in channels
    ]
    return respond_with({"metas": metas})


@app.get("/catalog/tv/iptv-livetv/genre={genre}.json")
async def catalog_tv_genre(
    genre: str,
    skip: int = Query(default=0, ge=0),
):
    """Catalogo canali IPTV filtrato per gruppo/genere."""
    channels = await get_channels_by_group(
        IPTV_URLS,
        group=genre,
        skip=skip,
        limit=IPTV_PAGE_SIZE,
    )
    metas = [
        {
            "id": ch["id"],
            "type": "tv",
            "name": ch["name"],
            "poster": ch["logo"] or ADDON_LOGO,
            "background": ch["logo"] or ADDON_LOGO,
            "logo": ch["logo"] or ADDON_LOGO,
            "genres": [ch["group"]],
            "description": f"Gruppo: {ch['group']} | Sorgente: {ch['source']}",
        }
        for ch in channels
    ]
    return respond_with({"metas": metas})


# ── Stream ────────────────────────────────────────────────────────────────────

@app.get("/stream/{type}/{id}.json")
async def streams_route(type: str, id: str, request: Request):
    # ── Live TV ──
    if type == "tv":
        ch = await get_channel_by_id(IPTV_URLS, id)
        if ch is None:
            return respond_with({"streams": []})
        return respond_with({
            "streams": [
                {
                    "url": ch["stream_url"],
                    "name": ch["name"],
                    "title": f"📺 {ch['name']}\n{ch['group']} · {ch['source']}",
                    "behaviorHints": {
                        "notWebReady": False,
                        "bingeGroup": f"iptv-{ch['group']}",
                    },
                }
            ]
        })

    # ── Film / Serie ──
    if type not in ("movie", "series"):
        raise HTTPException(status_code=404)
    try:
        base = str(request.base_url).rstrip("/")
        data = await get_streams(id, type, addon_base_url=base)
    except Exception:
        logger.exception(f"❌ Errore non gestito in streams_route per id={id!r} type={type!r}")
        data = {"streams": []}
    return respond_with(data)


# ── Meta ──────────────────────────────────────────────────────────────────────

@app.get("/meta/{type}/{id}.json")
async def meta(type: str, id: str):
    if type == "tv":
        ch = await get_channel_by_id(IPTV_URLS, id)
        if ch:
            return respond_with({
                "meta": {
                    "id": ch["id"],
                    "type": "tv",
                    "name": ch["name"],
                    "poster": ch["logo"] or ADDON_LOGO,
                    "background": ch["logo"] or ADDON_LOGO,
                    "logo": ch["logo"] or ADDON_LOGO,
                    "genres": [ch["group"]],
                    "description": f"Gruppo: {ch['group']} | Sorgente: {ch['source']}",
                    "links": [],
                }
            })
    return respond_with({"meta": {"id": id, "type": type, "name": ADDON_NAME, "poster": ADDON_LOGO}})


# ── Catalog legacy (film/serie) ───────────────────────────────────────────────

@app.get("/catalog/{type}/{id}.json")
async def catalog(type: str, id: str):
    return respond_with({"metas": []})
