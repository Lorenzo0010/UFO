import json
import logging
import re
import os
from typing import Dict, Optional, Any, List
from urllib.parse import urljoin, quote
from datetime import datetime

from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from fake_headers import Headers
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware

load_dotenv()

# ============================================================================
# LOGGING AVANZATO
# ============================================================================
class ColoredFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[41m',   # Red bg
    }
    RESET = '\033[0m'
    
    def format(self, record):
        log_color = self.COLORS.get(record.levelname, self.RESET)
        record.msg = f"{log_color}{record.msg}{self.RESET}"
        return super().format(record)

log_handler = logging.StreamHandler()
log_formatter = ColoredFormatter(
    '%(asctime)s - [%(levelname)s] - %(name)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log_handler.setFormatter(log_formatter)

logging.basicConfig(level=logging.DEBUG, handlers=[log_handler])
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURAZIONE
# ============================================================================
ADDON_NAME = "UFO addon DIRECT"
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"

CONFIG = {
    "Siti": {
        "StreamingCommunity": {
            "url": "https://vixsrc.to", 
            "enabled": "1"
        }
    }
}

User_Agent = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0"
TMDB_API_KEY = os.getenv('TMDB_KEY', '536b1c46da222eb34b69d168f092b495')

logger.info(f"🚀 ADDON INIZIALIZZATO: {ADDON_NAME}")
logger.info(f"📍 DOMINIO: {CONFIG['Siti']['StreamingCommunity']['url']}")
logger.info(f"🔑 TMDB API KEY: {TMDB_API_KEY[:10]}...")

def clean_id(id_str: str) -> str:
    """Pulisce l'ID rimuovendo suffissi."""
    cleaned = id_str.split(':')[0] if ':' in id_str else id_str
    logger.debug(f"🧹 ID Cleaning: {id_str} → {cleaned}")
    return cleaned

# ============================================================================
# TMDB HELPERS CON DEBUG
# ============================================================================
async def get_tmdb_id_from_imdb(imdb_id: str, client: AsyncSession) -> Optional[int]:
    """Converte IMDb ID a TMDB ID con logging."""
    logger.info(f"🔄 TMDB Lookup: IMDb ID {imdb_id}")
    
    try:
        url = f"https://api.themoviedb.org/3/find/{imdb_id}"
        params = {"external_source": "imdb_id", "api_key": TMDB_API_KEY, "language": "it"}
        
        logger.debug(f"   GET {url}")
        response = await client.get(url, params=params, timeout=10)
        logger.debug(f"   Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            if data.get('movie_results'):
                tmdb_id = data['movie_results'][0].get('id')
                logger.info(f"✅ IMDb {imdb_id} → TMDB {tmdb_id} (Movie)")
                return tmdb_id
            elif data.get('tv_results'):
                tmdb_id = data['tv_results'][0].get('id')
                logger.info(f"✅ IMDb {imdb_id} → TMDB {tmdb_id} (TV)")
                return tmdb_id
            else:
                logger.warning(f"⚠️ Nessun risultato trovato per {imdb_id}")
                return None
        else:
            logger.error(f"❌ TMDB API Error: {response.status_code}")
            return None
            
    except Exception as e:
        logger.error(f"❌ TMDB Exception: {type(e).__name__}: {str(e)}")
        return None

async def get_media_title(client: AsyncSession, tmdb_id: int, is_series: bool, season: str = None, episode: str = None) -> str:
    """Recupera titolo da TMDB."""
    logger.info(f"📝 GET TITLE - TMDB ID: {tmdb_id}, Serie: {is_series}")
    
    try:
        language = "it-IT"
        params = {"api_key": TMDB_API_KEY, "language": language}
        
        if not is_series:
            url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
            logger.debug(f"   Film Mode - GET {url}")
            response = await client.get(url, params=params, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                title = data.get("title", f"Film {tmdb_id}")
                logger.info(f"✅ Titolo Film: {title}")
                return title
            else:
                logger.warning(f"⚠️ Film not found: {response.status_code}")
                return f"Film {tmdb_id}"
        else:
            ep_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}"
            logger.debug(f"   Serie Mode - GET {ep_url}")
            ep_resp = await client.get(ep_url, params=params, timeout=5)
            
            if ep_resp.status_code == 200:
                ep_data = ep_resp.json()
                title = ep_data.get('name', f"Episodio {episode}")
                logger.info(f"✅ Titolo Episodio: {title}")
                return title
            else:
                logger.warning(f"⚠️ Episodio not found: {ep_resp.status_code}")
                return f"Episodio {episode}"
            
    except Exception as e:
        logger.error(f"❌ GET TITLE Exception: {type(e).__name__}: {str(e)}")
        return "Film" if not is_series else f"Episodio {episode}"

# ============================================================================
# EXTRACTOR CON DEBUG
# ============================================================================
class StreamingCommunityExtractor:
    def __init__(self):
        self.domain = CONFIG['Siti']['StreamingCommunity']['url']
        self.random_headers = Headers()
        logger.info(f"🎬 StreamingCommunityExtractor inizializzato - Domain: {self.domain}")

    async def extract_vixcloud_url(self, link: str, client: AsyncSession) -> List[Dict]:
        """Estrae M3U8 URL da vixcloud."""
        logger.info(f"=" * 80)
        logger.info(f"🔍 EXTRACT VIXCLOUD URL STARTED")
        logger.info(f"   Target: {link}")
        logger.info(f"=" * 80)
        
        try:
            headers = self.random_headers.generate()
            headers['Referer'] = f"{self.domain}/"
            headers['User-Agent'] = User_Agent
            
            logger.info(f"🌐 Fetching HTML page...")
            response = await client.get(link, headers=headers, timeout=15)
            logger.info(f"✅ Response - Status: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"❌ Status code error: {response.status_code}")
                return []

            logger.info(f"🔎 Parsing HTML con BeautifulSoup...")
            soup = BeautifulSoup(response.text, "lxml")
            scripts = soup.find_all("script")
            logger.info(f"   Found {len(scripts)} script tags")
            
            video_data = None
            for i, script in enumerate(scripts):
                if script.string and "token" in script.string and "expires" in script.string:
                    video_data = script.string
                    logger.info(f"✅ Video data script trovato (Script[{i}])")
                    break
            
            if not video_data:
                logger.error(f"❌ Video data script non trovato!")
                return []

            logger.info(f"🔧 Extracting token, expires, url...")
            
            token_match = re.search(r"'token':\s*'(\w+)'", video_data)
            expires_match = re.search(r"'expires':\s*'(\d+)'", video_data)
            url_match = re.search(r"url:\s*'([^']+)'", video_data)
            
            if not all([token_match, expires_match, url_match]):
                logger.error(f"❌ Non tutti i parametri trovati!")
                return []

            token = token_match.group(1)
            expires = expires_match.group(1)
            server_url = url_match.group(1)
            
            logger.info(f"✅ Parametri estratti:")
            logger.info(f"   Token: {token}")
            logger.info(f"   Expires: {expires}")
            logger.info(f"   Server URL: {server_url}")
            
            separator = "&" if "?" in server_url else "?"
            final_url = f"{server_url}{separator}token={token}&expires={expires}"
            
            if "?b=1" in server_url and "b=1" not in final_url:
                final_url += "&b=1"
            if "window.canPlayFHD = true" in video_data:
                final_url += "&h=1"

            if ".m3u8" not in final_url:
                if "?" in final_url:
                    base, params = final_url.split("?", 1)
                    if not base.endswith(".m3u8"):
                        final_url = f"{base}.m3u8?{params}"
                else:
                    final_url += ".m3u8"

            logger.info(f"✅ Final URL: {final_url[:80]}...")

            # Analisi M3U8 per qualità
            logger.info(f"📊 Analyzing M3U8 playlist...")
            detected_quality = "Auto"
            max_height = 0

            try:
                logger.debug(f"   GET M3U8 playlist...")
                m3u8_res = await client.get(final_url, headers=headers, timeout=6)
                logger.info(f"   M3U8 Response: {m3u8_res.status_code}")
                
                if m3u8_res.status_code == 200:
                    lines = m3u8_res.text.splitlines()
                    logger.debug(f"   M3U8 Lines: {len(lines)}")
                    
                    for i, line in enumerate(lines):
                        if "RESOLUTION=" in line:
                            res_match = re.search(r'RESOLUTION=(\d+)x(\d+)', line)
                            if res_match:
                                width = int(res_match.group(1))
                                height = int(res_match.group(2))
                                if height > max_height:
                                    max_height = height
                    
                    if max_height > 0:
                        detected_quality = f"{max_height}p"
                        logger.info(f"✅ Quality detected: {detected_quality}")
                        
            except Exception as e:
                logger.warning(f"⚠️ M3U8 Analysis failed: {str(e)}")

            if max_height == 0:
                detected_quality = "1080p" if "window.canPlayFHD = true" in video_data else "720p"
                logger.info(f"✅ Default quality: {detected_quality}")

            logger.info(f"=" * 80)
            logger.info(f"✅ EXTRACTION COMPLETED - {detected_quality}")
            logger.info(f"=" * 80)
            
            return [{
                "quality": detected_quality,
                "url": final_url,
                "height": max_height
            }]

        except Exception as e:
            logger.error(f"❌ EXTRACTOR EXCEPTION: {type(e).__name__}: {str(e)}")
            return []

    async def get_streams(self, id: str, client: AsyncSession) -> Dict:
        """Genera lista stream - DIRECT MODE (senza proxy)."""
        logger.info(f"=" * 80)
        logger.info(f"📺 GET_STREAMS CALLED (DIRECT MODE)")
        logger.info(f"   Input ID: {id}")
        logger.info(f"=" * 80)
        
        streams = {'streams': []}
        try:
            is_series = False
            season = None
            episode = None
            content_id = clean_id(id)
            
            logger.info(f"🔍 Parsing ID...")
            if ':' in id:
                parts = id.split(':')
                content_id = parts[0]
                if len(parts) >= 3:
                    season, episode = parts[1], parts[2]
                    is_series = True
                    logger.info(f"✅ Tipo: SERIE - S{season}E{episode}")
            else:
                logger.info(f"✅ Tipo: FILM")

            logger.info(f"🔄 Determining TMDB ID...")
            tmdb_id = None
            if content_id.startswith('tt'):
                logger.info(f"   IMDb format detected: {content_id}")
                tmdb_id = await get_tmdb_id_from_imdb(content_id, client)
                if not tmdb_id:
                    logger.error(f"❌ Impossibile convertire IMDb a TMDB")
                    return streams
            else:
                try:
                    tmdb_id = int(content_id)
                    logger.info(f"✅ TMDB ID diretto: {tmdb_id}")
                except ValueError:
                    logger.error(f"❌ Invalid content_id format: {content_id}")
                    return streams

            logger.info(f"📝 Fetching media title...")
            media_title = await get_media_title(client, tmdb_id, is_series, season, episode)

            logger.info(f"🔗 Building streaming URL...")
            if is_series:
                url = f'{self.domain}/tv/{tmdb_id}/{season}/{episode}/'
            else:
                url = f'{self.domain}/movie/{tmdb_id}/'
            logger.info(f"   URL: {url}")
            
            logger.info(f"🎥 Extracting vixcloud URL...")
            results = await self.extract_vixcloud_url(url, client)
            
            logger.info(f"📊 Processing {len(results)} result(s)...")
            for i, res in enumerate(results):
                logger.info(f"   Result[{i}]:")
                logger.info(f"      Quality: {res['quality']}")
                logger.info(f"      URL: {res['url'][:80]}...")
                
                # ✅ DIRECT MODE: Return M3U8 URL directly (no proxy)
                stream_entry = {
                    "name": f"🛸 {res['quality']}", 
                    "title": media_title,
                    "url": res['url'],  # ✅ DIRECT M3U8 URL
                    "behaviorHints": {
                        "proxyHeaders": {
                            "request": {
                                "User-Agent": User_Agent,
                                "Referer": "https://vixsrc.to/"
                            }
                        },
                        "notWebReady": False,
                        "bingeGroup": "streamingcommunity"
                    }
                }
                
                logger.info(f"✅ Stream aggiunto (DIRECT MODE)")
                streams['streams'].append(stream_entry)

            logger.info(f"=" * 80)
            logger.info(f"✅ GET_STREAMS COMPLETED - {len(streams['streams'])} stream(s)")
            logger.info(f"=" * 80)

        except Exception as e:
            logger.error(f"❌ GET_STREAMS EXCEPTION: {type(e).__name__}: {str(e)}")
        
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
    return resp

# ============================================================================
# ROUTES
# ============================================================================
@app.get("/")
async def root(request: Request):
    logger.info(f"📡 ROOT endpoint called from {request.client.host}")
    base_url = str(request.base_url).rstrip("/")
    return respond_with({
        "status": "online",
        "addon": ADDON_NAME,
        "manifest": f"{base_url}/U0MQ/manifest.json"
    })

@app.get("/U0MQ/manifest.json")
async def manifest(request: Request):
    logger.info(f"📄 MANIFEST endpoint called")
    config = {
        "id": "org.stremio.mammamia.ufo",
        "version": "1.5.0",
        "name": ADDON_NAME,
        "description": "VixSrc Stream Direct Mode",
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
    logger.info(f"=" * 80)
    logger.info(f"🎬 STREAM REQUEST")
    logger.info(f"   From: {request.client.host}")
    logger.info(f"   Type: {type}")
    logger.info(f"   ID: {id}")
    logger.info(f"=" * 80)
    
    try:
        if type not in ["movie", "series"]:
            logger.error(f"❌ Invalid type: {type}")
            raise HTTPException(status_code=404)
        
        async with AsyncSession() as client:
            streams_data = await extractor.get_streams(id, client)
        
        if not streams_data:
            logger.warning(f"⚠️ No streams data returned")
            streams_data = {"streams": []}
        
        logger.info(f"✅ Responding with {len(streams_data.get('streams', []))} stream(s)")
        return respond_with(streams_data)
        
    except Exception as e:
        logger.error(f"❌ STREAM ENDPOINT ERROR: {type(e).__name__}: {str(e)}")
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

@app.get("/debug/status")
async def debug_status(request: Request):
    logger.info(f"🔧 DEBUG STATUS endpoint called")
    return respond_with({
        "status": "online",
        "addon": ADDON_NAME,
        "mode": "DIRECT (No Proxy)",
        "timestamp": str(datetime.now()),
        "domain": CONFIG['Siti']['StreamingCommunity']['url']
    })

if __name__ == "__main__":
    logger.info(f"🚀 Starting uvicorn server...")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860, log_level="info")
