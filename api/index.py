import json
import logging
import re
import os
from typing import Dict, Optional, Any, Tuple, List
from urllib.parse import urljoin  # <--- IMPORT NECESSARIO AGGIUNTO

from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from fake_headers import Headers
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware

load_dotenv()

# ============================================================================
# CONFIGURAZIONE
# ============================================================================
ADDON_NAME = "UFO addon"
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"

CONFIG = {
    "Siti": {
        "StreamingCommunity": {
            "url": "https://vixsrc.to",
            "enabled": "1"
        }
    }
}

# LOGGING
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ============================================================================
# UTILITIES & TMDB HELPERS
# ============================================================================
User_Agent = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0"
TMDB_API_KEY = os.getenv('TMDB_KEY', '536b1c46da222eb34b69d168f092b495')

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
    except Exception as e:
        logger.error(f"❌ Error converting IMDb ID: {e}")
        return None

async def get_media_title(client: AsyncSession, tmdb_id: int, is_series: bool, season: str = None, episode: str = None) -> str:
    """Recupera il titolo formattato da TMDB."""
    try:
        language = "it-IT"
        params = {"api_key": TMDB_API_KEY, "language": language}
        
        if not is_series:
            # --- FILM ---
            url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
            response = await client.get(url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                return data.get("title", f"Film {tmdb_id}")
            return f"Film {tmdb_id}"
        else:
            # --- SERIE TV ---
            ep_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}"
            ep_resp = await client.get(ep_url, params=params, timeout=5)
            
            if ep_resp.status_code == 200:
                ep_data = ep_resp.json()
                return ep_data.get('name', f"Episodio {episode}")
            
            return f"Episodio {episode}"
            
    except Exception as e:
        logger.error(f"❌ Errore recupero titolo TMDB: {e}")
        if is_series: return f"Episodio {episode}"
        return "Film"

# ============================================================================
# EXTRACTOR
# ============================================================================
class StreamingCommunityExtractor:
    def __init__(self):
        self.domain = CONFIG['Siti']['StreamingCommunity']['url']
        self.random_headers = Headers()

    async def extract_vixcloud_url(self, link: str, client: AsyncSession) -> List[Dict]:
        """Estrae URL e analizza il master playlist per le risoluzioni reali."""
        try:
            logger.info(f"🔍 Fetching: {link}")
            headers = self.random_headers.generate()
            headers['Referer'] = f"{self.domain}/"
            headers['User-Agent'] = User_Agent
            
            response = await client.get(link, headers=headers, timeout=15)
            if response.status_code != 200:
                return []

            soup = BeautifulSoup(response.text, "lxml")
            scripts = soup.find_all("script")
            
            video_data = None
            for script in scripts:
                if script.string and "token" in script.string and "expires" in script.string:
                    video_data = script.string
                    break
            
            if not video_data:
                return []

            token_match = re.search(r"'token':\s*'(\w+)'", video_data)
            expires_match = re.search(r"'expires':\s*'(\d+)'", video_data)
            url_match = re.search(r"url:\s*'([^']+)'", video_data)
            
            if not all([token_match, expires_match, url_match]):
                return []

            token = token_match.group(1)
            expires = expires_match.group(1)
            server_url = url_match.group(1)
            
            separator = "&" if "?" in server_url else "?"
            final_url = f"{server_url}{separator}token={token}&expires={expires}"
            
            # Parametri opzionali
            if "?b=1" in server_url and "b=1" not in final_url: final_url += "&b=1"
            # Manteniamo la richiesta h=1 per avere la massima qualità disponibile nel master list
            if "window.canPlayFHD = true" in video_data: final_url += "&h=1"
            
            # Fix estensione
            if ".m3u8" not in final_url:
                 if "?" in final_url:
                     base, params = final_url.split("?", 1)
                     if not base.endswith(".m3u8"): final_url = f"{base}.m3u8?{params}"
                 else:
                     final_url += ".m3u8"

            # --- NUOVA LOGICA: CONTROLLO REALE M3U8 ---
            found_streams = []
            try:
                # Scarichiamo la playlist per vedere cosa c'è dentro davvero
                m3u8_res = await client.get(final_url, headers=headers, timeout=6)
                if m3u8_res.status_code == 200:
                    lines = m3u8_res.text.splitlines()
                    for i, line in enumerate(lines):
                        # Cerchiamo le righe con le info sullo stream
                        if "#EXT-X-STREAM-INF" in line and "RESOLUTION=" in line:
                            res_match = re.search(r'RESOLUTION=(\d+)x(\d+)', line)
                            if res_match:
                                height = int(res_match.group(2))
                                quality = f"{height}p" # Es: 1080p, 720p
                                
                                # L'URL effettivo è nella riga successiva
                                if i + 1 < len(lines):
                                    stream_url = lines[i+1].strip()
                                    # Se l'URL è relativo (non inizia con http), uniscilo al dominio base
                                    if not stream_url.startswith("http"):
                                        stream_url = urljoin(final_url, stream_url)
                                    
                                    found_streams.append({
                                        "quality": quality,
                                        "url": stream_url,
                                        "height": height
                                    })
            except Exception as e:
                logger.warning(f"⚠️ Impossibile analizzare m3u8, fallback attivo: {e}")

            # Se abbiamo trovato stream reali, li restituiamo (ordinati per qualità)
            if found_streams:
                # Ordina dal più alto al più basso
                found_streams.sort(key=lambda x: x['height'], reverse=True)
                return found_streams

            # FALLBACK: Se il file non è una master playlist o il parsing fallisce, 
            # usiamo l'URL originale con la logica vecchia (ma come backup).
            fallback_quality = "1080p" if "window.canPlayFHD = true" in video_data else "720p"
            return [{"quality": fallback_quality, "url": final_url}]

        except Exception as e:
            logger.error(f"❌ Extractor Error: {e}")
            return []

    async def get_streams(self, id: str, client: AsyncSession) -> Dict:
        streams = {'streams': []}
        try:
            is_series = False
            season = None
            episode = None
            content_id = clean_id(id)
            
            if ':' in id:
                parts = id.split(':')
                content_id = parts[0]
                if len(parts) >= 3:
                    season, episode = parts[1], parts[2]
                    is_series = True

            tmdb_id = None
            if content_id.startswith('tt'):
                tmdb_id = await get_tmdb_id_from_imdb(content_id, client)
                if not tmdb_id: return streams
            else:
                try: tmdb_id = int(content_id)
                except ValueError: return streams

            # 1. Recupero Titolo
            media_title = await get_media_title(client, tmdb_id, is_series, season, episode)

            # 2. Costruzione URL Scraper
            url = f'{self.domain}/tv/{tmdb_id}/{season}/{episode}/' if is_series else f'{self.domain}/movie/{tmdb_id}/'
            
            # 3. Estrazione Stream Reali
            results = await self.extract_vixcloud_url(url, client)
            
            # 4. Creazione lista per Stremio
            for res in results:
                streams['streams'].append({
                    "name": f"🛸 {res['quality']}", 
                    "title": f"{media_title}",
                    "url": res['url'],
                    "behaviorHints": {
                        "proxyHeaders": {"request": {"user-agent": User_Agent}},
                        "notWebReady": True,
                        "bingeGroup": "streamingcommunity"
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
@app.get("/")
async def root(request: Request):
    base_url = str(request.base_url).rstrip("/")
    return respond_with({
        "status": "online",
        "addon": ADDON_NAME,
        "manifest": f"{base_url}/U0MQ/manifest.json"
    })

@app.get("/U0MQ/manifest.json")
async def manifest():
    config = {
        "id": "org.stremio.mammamia.ufo",
        "version": "1.3.1",
        "name": ADDON_NAME,
        "description": "VixSrc Stream via Vercel",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": [],
        "behaviorHints": {"configurable": False}
    }
    return respond_with(config)

@app.get("/U0MQ/stream/{type}/{id}.json")
@limiter.limit("10/second")
async def streams(request: Request, type: str, id: str):
    try:
        if type not in ["movie", "series"]: raise HTTPException(status_code=404)
        async with AsyncSession() as client:
            streams_data = await extractor.get_streams(id, client)
        if not streams_data: streams_data = {"streams": []}
        return respond_with(streams_data)
    except Exception:
        return respond_with({"streams": []})

@app.get("/U0MQ/meta/{type}/{id}.json")
async def meta(type: str, id: str):
    return respond_with({
        "meta": {
            "id": id,
            "type": type,
            "name": ADDON_NAME,
            "poster": ADDON_LOGO
        }
    })

@app.get("/U0MQ/catalog/{type}/{id}.json")
async def catalog(type: str, id: str):
    return respond_with({"metas": []})
