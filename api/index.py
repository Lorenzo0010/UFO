import json
import logging
import re
import os
import time
from typing import Dict, Optional, Any, List
from urllib.parse import urljoin

# Librerie di terze parti
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from fake_headers import Headers
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware

load_dotenv()

# ============================================================================
# CONFIGURAZIONE
# ============================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

ADDON_NAME = "UFO addon"
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"
TMDB_API_KEY = os.getenv('TMDB_KEY', '536b1c46da222eb34b69d168f092b495')
DOMAIN = "https://vixsrc.to"

# USER AGENT STATICO (Importante per Windows/MPV)
# Usiamo un UA di Chrome Windows per massimizzare la compatibilità col player desktop
User_Agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# ============================================================================
# UTILITIES
# ============================================================================
def clean_id(id_str: str) -> str:
    return id_str.split(':')[0] if ':' in id_str else id_str

async def get_tmdb_id_from_imdb(imdb_id: str, client: AsyncSession) -> Optional[int]:
    try:
        response = await client.get(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            params={"external_source": "imdb_id", "api_key": TMDB_API_KEY, "language": "it"},
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            if data.get('movie_results'): return data['movie_results'][0].get('id')
            if data.get('tv_results'): return data['tv_results'][0].get('id')
        return None
    except Exception:
        return None

async def get_media_title(client: AsyncSession, tmdb_id: int, is_series: bool, season: str = None, episode: str = None) -> str:
    try:
        params = {"api_key": TMDB_API_KEY, "language": "it-IT"}
        if not is_series:
            url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
            response = await client.get(url, params=params, timeout=5)
            if response.status_code == 200:
                return response.json().get("title", f"Film {tmdb_id}")
            return f"Film {tmdb_id}"
        else:
            ep_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}"
            ep_resp = await client.get(ep_url, params=params, timeout=5)
            if ep_resp.status_code == 200:
                return ep_resp.json().get('name', f"Episodio {episode}")
            return f"Episodio {episode}"
    except Exception:
        return "Video"

# ============================================================================
# EXTRACTOR
# ============================================================================
class StreamingCommunityExtractor:
    def __init__(self):
        pass

    async def extract_vixcloud_url(self, link: str, client: AsyncSession) -> List[Dict]:
        logger.info(f"🔍 Fetching: {link}")
        try:
            # Header specifici per la richiesta di estrazione
            headers = {
                'User-Agent': User_Agent,
                'Referer': f"{DOMAIN}/",
                'Origin': DOMAIN,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7'
            }
            
            response = await client.get(link, headers=headers, timeout=15)
            if response.status_code != 200: return []

            soup = BeautifulSoup(response.text, "lxml")
            scripts = soup.find_all("script")
            video_data = None
            
            for script in scripts:
                if script.string and "token" in script.string and "expires" in script.string:
                    video_data = script.string
                    break
            
            if not video_data: return []

            token_match = re.search(r"'token':\s*'(\w+)'", video_data)
            expires_match = re.search(r"'expires':\s*'(\d+)'", video_data)
            url_match = re.search(r"url:\s*'([^']+)'", video_data)
            
            if not all([token_match, expires_match, url_match]): return []

            server_url = url_match.group(1).strip() # Strip rimuove spazi invisibili
            token = token_match.group(1)
            expires = expires_match.group(1)

            # --- COSTRUZIONE URL ROBUSTA ---
            separator = "&" if "?" in server_url else "?"
            final_url = f"{server_url}{separator}token={token}&expires={expires}"
            
            if "b=1" not in final_url: 
                if "b=1" in server_url: pass 
                else: final_url += "&b=1"

            # Nota: 'h=1' (PlayFHD) a volte rompe MPV su connessioni lente/instabili, 
            # lo lasciamo solo se esplicitamente richiesto dallo script
            if "window.canPlayFHD = true" in video_data: final_url += "&h=1"
            
            if ".m3u8" not in final_url:
                if "?" in final_url:
                    base, params = final_url.split("?", 1)
                    if not base.endswith(".m3u8"): final_url = f"{base}.m3u8?{params}"
                else:
                    final_url += ".m3u8"

            logger.info(f"✅ URL Generato")
            
            return [{
                "quality": "1080p", 
                "url": final_url
            }]

        except Exception as e:
            logger.error(f"❌ Extractor Error: {e}")
            return []

    async def get_streams(self, id: str, client: AsyncSession) -> Dict:
        streams = {'streams': []}
        try:
            is_series = ':' in id
            season = episode = None
            content_id = clean_id(id)

            if is_series:
                parts = id.split(':')
                content_id = parts[0]
                if len(parts) >= 3: season, episode = parts[1], parts[2]

            tmdb_id = None
            if content_id.startswith('tt'):
                tmdb_id = await get_tmdb_id_from_imdb(content_id, client)
            else:
                try: tmdb_id = int(content_id)
                except: pass

            if not tmdb_id: return streams

            media_title = await get_media_title(client, tmdb_id, is_series, season, episode)
            target_url = f'{DOMAIN}/tv/{tmdb_id}/{season}/{episode}/' if is_series else f'{DOMAIN}/movie/{tmdb_id}/'
            
            results = await self.extract_vixcloud_url(target_url, client)
            
            for res in results:
                streams['streams'].append({
                    "name": f"🛸 {res['quality']}", 
                    "title": media_title,
                    "url": res['url'],
                    "behaviorHints": {
                        "notWebReady": True,
                        "bingeGroup": "streamingcommunity",
                        # --- FIX WINDOWS/MPV HEADERS ---
                        # MPV richiede headers precisi per superare Cloudflare/DDoS-Guard
                        "proxyHeaders": {
                            "request": {
                                "User-Agent": User_Agent,
                                "Referer": f"{DOMAIN}/",
                                "Origin": DOMAIN,
                                "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
                                "Connection": "keep-alive",
                                "Sec-Fetch-Dest": "empty",
                                "Sec-Fetch-Mode": "cors",
                                "Sec-Fetch-Site": "cross-site"
                            }
                        }
                    }
                })
        except Exception as e:
            logger.error(f"❌ Stream Error: {e}")
        
        return streams

# ============================================================================
# FASTAPI SETUP
# ============================================================================
app = FastAPI(title=f"{ADDON_NAME} Addon")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
extractor = StreamingCommunityExtractor()

def respond_with(data: Any) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp

# ============================================================================
# ROUTES
# ============================================================================

@app.api_route("/manifest.json", methods=["GET", "HEAD"])
async def root_manifest(request: Request):
    return RedirectResponse(url="/U0MQ/manifest.json")

@app.head("/")
async def root_head():
    return {"status": "online"}

@app.get("/")
async def root(request: Request):
    base_url = str(request.base_url).rstrip("/")
    return respond_with({
        "status": "online",
        "addon": ADDON_NAME,
        "manifest": f"{base_url}/U0MQ/manifest.json"
    })

@app.api_route("/U0MQ/manifest.json", methods=["GET", "HEAD"])
async def manifest(request: Request):
    config = {
        "id": "org.stremio.mammamia.ufo",
        "version": "1.4.1", # Incremento versione per forzare aggiornamento
        "name": ADDON_NAME,
        "description": "VixSrc Windows Fix",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": [],
        "idPrefixes": ["tt", "tmdb:"],
        "behaviorHints": {"configurable": False}
    }
    return respond_with(config)

@app.get("/U0MQ/stream/{type}/{id}.json")
@limiter.limit("10/second")
async def streams(request: Request, type: str, id: str):
    try:
        if type not in ["movie", "series"]: raise HTTPException(status_code=404)
        async with AsyncSession(impersonate="chrome") as client:
            streams_data = await extractor.get_streams(id, client)
        
        if not streams_data: streams_data = {"streams": []}
        return respond_with(streams_data)
    except Exception:
        return respond_with({"streams": []})

@app.get("/U0MQ/meta/{type}/{id}.json")
async def meta(type: str, id: str):
    return respond_with({
        "meta": {"id": id, "type": type, "name": ADDON_NAME, "poster": ADDON_LOGO}
    })

@app.get("/U0MQ/catalog/{type}/{id}.json")
async def catalog(type: str, id: str):
    return respond_with({"metas": []})
