import json
import logging
import re
import os
from typing import Dict, Optional, Any
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ============================================================================
# CONFIG
# ============================================================================
ADDON_NAME = "UFO addon"
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"
SC_DOMAIN    = os.getenv("SC_DOMAIN", "https://vixsrc.to")
TMDB_API_KEY = os.getenv("TMDB_KEY", "536b1c46da222eb34b69d168f092b495")
USER_AGENT   = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"

# EasyProxy — deve essere un'istanza EasyProxy/MediaFlow accessibile pubblicamente
# Es: https://mio-easyproxy.vercel.app  oppure  https://easyproxy.miodominio.com
EASYPROXY_URL      = os.getenv("EASYPROXY_URL", "").rstrip("/")
EASYPROXY_PASSWORD = os.getenv("EASYPROXY_PASSWORD", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ============================================================================
# EASYPROXY HELPERS
# ============================================================================
def wrap_with_easyproxy(m3u8_url: str) -> str:
    """
    Wrappa l'URL m3u8 finale attraverso EasyProxy per il playback.
    Formato: EASYPROXY_URL/proxy/hls/manifest.m3u8?url=<encoded>&api_password=<pw>
    """
    if not EASYPROXY_URL:
        return m3u8_url
    encoded = quote(m3u8_url, safe="")
    url = f"{EASYPROXY_URL}/proxy/hls/manifest.m3u8?url={encoded}"
    if EASYPROXY_PASSWORD:
        url += f"&api_password={quote(EASYPROXY_PASSWORD, safe='')}"
    return url

def get_proxy_for_httpx() -> Optional[str]:
    """
    Ritorna l'URL del proxy HTTP da usare in httpx per il fetching,
    se EasyProxy espone un endpoint /proxy/http (MediaFlow compatibile).
    In alternativa, usa un proxy SOCKS5/HTTP esterno se configurato.
    """
    # Se hai un proxy HTTP/SOCKS5 separato per il fetching (es. Webshare, BrightData)
    http_proxy = os.getenv("HTTP_PROXY", os.getenv("FETCH_PROXY", ""))
    if http_proxy:
        return http_proxy
    return None

# ============================================================================
# UTILITIES
# ============================================================================
def clean_id(id_str: str) -> str:
    return id_str.split(":")[0] if ":" in id_str else id_str

async def get_tmdb_id_from_imdb(imdb_id: str, client: httpx.AsyncClient) -> Optional[int]:
    try:
        r = await client.get(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            params={"external_source": "imdb_id", "api_key": TMDB_API_KEY, "language": "it"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("movie_results"):
                return data["movie_results"][0].get("id")
            if data.get("tv_results"):
                return data["tv_results"][0].get("id")
    except Exception as e:
        logger.error(f"❌ TMDb lookup error: {e}")
    return None

# ============================================================================
# VIXCLOUD EXTRACTOR
# ============================================================================
async def extract_vixcloud_url(page_url: str, client: httpx.AsyncClient) -> Optional[str]:
    """
    Fetcha la pagina embed di vixsrc.to e ne estrae l'URL m3u8 dal
    blocco window.masterPlaylist. Se EASYPROXY_URL è configurato,
    il fetching passa attraverso EasyProxy per bypassare il blocco IP.
    """
    try:
        # Se EasyProxy è configurato, usa l'endpoint /proxy/fetch per il fetching server-side
        # Altrimenti tenta diretto (funziona solo da IP residenziale)
        if EASYPROXY_URL:
            fetch_url = (
                f"{EASYPROXY_URL}/proxy/fetch"
                f"?url={quote(page_url, safe='')}"
                f"&headers={quote(json.dumps({'Referer': SC_DOMAIN + '/', 'User-Agent': USER_AGENT}), safe='')}"
            )
            if EASYPROXY_PASSWORD:
                fetch_url += f"&api_password={quote(EASYPROXY_PASSWORD, safe='')}"
            logger.info(f"🌐 Fetching via EasyProxy: {page_url}")
            r = await client.get(fetch_url, timeout=20, follow_redirects=True)
        else:
            logger.info(f"🔍 Fetching diretto: {page_url}")
            headers = {
                "User-Agent": USER_AGENT,
                "Referer": f"{SC_DOMAIN}/",
            }
            proxies = get_proxy_for_httpx()
            if proxies:
                r = await client.get(page_url, headers=headers, timeout=15,
                                     follow_redirects=True)
            else:
                r = await client.get(page_url, headers=headers, timeout=15,
                                     follow_redirects=True)

        if r.status_code != 200:
            logger.warning(f"⚠️ HTTP {r.status_code}")
            return None

        html = r.text
        soup = BeautifulSoup(html, "lxml")

        script_tag = None
        for s in soup.find_all("script"):
            if s.string and "masterPlaylist" in s.string:
                script_tag = s.string
                break

        if not script_tag:
            logger.warning("⚠️ Nessun script con masterPlaylist trovato")
            return None

        url_match   = re.search(r"url\s*:\s*['\"]([^'\"]+)['\"]", script_tag)
        token_match = re.search(r"token\s*:\s*['\"](\w+)['\"]", script_tag)
        exp_match   = re.search(r"expires\s*:\s*['\"](\d+)['\"]", script_tag)
        fhd_match   = re.search(r"canPlayFHD\s*[=:]\s*true", script_tag)

        if not (url_match and token_match and exp_match):
            logger.warning("⚠️ Parametri masterPlaylist non trovati")
            return None

        base_url = url_match.group(1)
        token    = token_match.group(1)
        expires  = exp_match.group(1)
        can_fhd  = bool(fhd_match)

        base_path = base_url.split("?")[0]
        if not base_path.lower().endswith(".m3u8"):
            base_path = base_path.rstrip("/") + ".m3u8"

        if "?b" in base_url:
            final_url = base_path + f"?b=1&token={token}&expires={expires}"
        else:
            final_url = base_path + f"?token={token}&expires={expires}"

        if can_fhd:
            final_url += "&h=1"

        logger.info(f"✅ m3u8 estratto: {final_url}")
        return final_url

    except Exception as e:
        logger.error(f"❌ Extractor error: {e}")
        return None

# ============================================================================
# STREAM RESOLVER
# ============================================================================
async def get_streams(id: str) -> Dict:
    streams: Dict = {"streams": []}
    try:
        is_series = False
        season = episode = None
        content_id = clean_id(id)
        parts = id.split(":")

        if len(parts) >= 3:
            content_id, season, episode = parts[0], parts[1], parts[2]
            is_series = True

        proxy = get_proxy_for_httpx()
        client_kwargs = {"proxies": proxy} if proxy else {}

        async with httpx.AsyncClient(**client_kwargs) as client:
            tmdb_id: Optional[int] = None
            if content_id.startswith("tt"):
                tmdb_id = await get_tmdb_id_from_imdb(content_id, client)
                if not tmdb_id:
                    return streams
            else:
                try:
                    tmdb_id = int(content_id)
                except ValueError:
                    return streams

            page_url = (
                f"{SC_DOMAIN}/tv/{tmdb_id}/{season}/{episode}/"
                if is_series else
                f"{SC_DOMAIN}/movie/{tmdb_id}/"
            )

            raw_url = await extract_vixcloud_url(page_url, client)

        if raw_url:
            # Wrappa con EasyProxy per il playback se configurato
            final_url = wrap_with_easyproxy(raw_url)
            use_proxy = bool(EASYPROXY_URL)

            streams["streams"].append({
                "name": "🛸 UFO" + (" [EP]" if use_proxy else ""),
                "title": "StreamingCommunity via EasyProxy" if use_proxy else "StreamingCommunity",
                "url": final_url,
                "behaviorHints": {
                    # Se usi EasyProxy il player può aprirlo direttamente
                    "notWebReady": not use_proxy,
                    "bingeGroup": "ufo-sc",
                    # Senza proxy: richiede user-agent spoofato
                    **({"proxyHeaders": {"request": {"user-agent": USER_AGENT}}}
                       if not use_proxy else {}),
                },
            })
    except Exception as e:
        logger.error(f"❌ get_streams error: {e}")
    return streams

# ============================================================================
# FASTAPI APP
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
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp

@app.get("/")
async def root(request: Request):
    base_url = str(request.base_url).rstrip("/")
    return respond_with({
        "status": "online",
        "addon": ADDON_NAME,
        "easyproxy_configured": bool(EASYPROXY_URL),
        "manifest": f"{base_url}/U0MQ/manifest.json",
    })

@app.get("/U0MQ/manifest.json")
async def manifest():
    return respond_with({
        "id": "org.stremio.mammamia.ufo",
        "version": "1.3.3",
        "name": ADDON_NAME,
        "description": "VixSrc Stream via Vercel + EasyProxy",
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
        data = await get_streams(id)
    except Exception:
        data = {"streams": []}
    return respond_with(data)

@app.get("/U0MQ/meta/{type}/{id}.json")
async def meta(type: str, id: str):
    return respond_with({"meta": {"id": id, "type": type, "name": ADDON_NAME, "poster": ADDON_LOGO}})

@app.get("/U0MQ/catalog/{type}/{id}.json")
async def catalog(type: str, id: str):
    return respond_with({"metas": []})
