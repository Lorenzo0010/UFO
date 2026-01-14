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
def load_config():
    """Carica config.json, usa fallback se non esiste."""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("⚠️ config.json non trovato, uso config di default")
        return {
            "Siti": {
                "StreamingCommunity": {
                    "url": "https://vixsrc.to",
                    "SC_PROXY": "0",
                    "VX_PROXY": "0",
                    "SC_ForwardProxy": "0",
                    "VX_ForwardProxy": "0",
                    "enabled": "1"
                }
            },
            "General": {
                "load_env": "1",
                "HOST": "0.0.0.0",
                "PORT": 8080,
                "Name": "UFO Addon",
                "Icon": "🛸",
                "level": "info",
                "Global_Proxy": "0"
            }
        }

CONFIG = load_config()
SITES = CONFIG.get("Siti", {})
GENERAL_CONFIG = CONFIG.get("General", {})

SC_DOMAIN = SITES.get("StreamingCommunity", {}).get("url", "https://vixsrc.to")
SC_ENABLED = SITES.get("StreamingCommunity", {}).get("enabled", "1")
SC_PROXY = SITES.get("StreamingCommunity", {}).get("SC_PROXY", "0")
VX_PROXY = SITES.get("StreamingCommunity", {}).get("VX_PROXY", "0")
SC_ForwardProxy = SITES.get("StreamingCommunity", {}).get("SC_ForwardProxy", "0")
VX_ForwardProxy = SITES.get("StreamingCommunity", {}).get("VX_ForwardProxy", "0")

HOST = GENERAL_CONFIG.get("HOST", "0.0.0.0")
PORT = int(GENERAL_CONFIG.get("PORT", 8080))
ADDON_NAME = GENERAL_CONFIG.get("Name", "UFO Addon")
ADDON_ICON = GENERAL_CONFIG.get("Icon", "🛸")
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"
LOAD_ENV = GENERAL_CONFIG.get("load_env", "1")
GLOBAL_PROXY = GENERAL_CONFIG.get("Global_Proxy", "0")

if LOAD_ENV == "1":
    load_dotenv()

User_Agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
TMDB_API_KEY = os.getenv('TMDB_KEY', '536b1c46da222eb34b69d168f092b495')

proxies = {}
FORWARD_PROXY = ""
if GLOBAL_PROXY == "1":
    proxy_creds = os.getenv('PROXY_CREDENTIALS')
    if proxy_creds:
        try:
            proxy_list = json.loads(proxy_creds)
            selected_proxy = random.choice(proxy_list) if proxy_list else ""
            if selected_proxy:
                proxies = {"http": selected_proxy, "https": selected_proxy}
        except json.JSONDecodeError:
            logger.warning("⚠️ PROXY_CREDENTIALS non è JSON valido")

if SC_ForwardProxy == "1" or VX_ForwardProxy == "1":
    FORWARD_PROXY = os.getenv('FORWARDPROXY', "")

if os.getenv('PORT'):
    PORT = int(os.getenv('PORT'))

logger.info(f"🚀 ADDON INIZIALIZZATO: {ADDON_NAME}")
logger.info(f"📍 DOMINIO SC: {SC_DOMAIN}")
logger.info(f"🔑 TMDB API KEY: {TMDB_API_KEY[:10]}...")
logger.info(f"🌐 HOST: {HOST}:{PORT}")
logger.info(f"📦 Proxy Globale: {'✅' if GLOBAL_PROXY == '1' else '❌'}")
logger.info(f"🔀 ForwardProxy: {'✅' if FORWARD_PROXY else '❌'}")
</parameter>

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

def respond_with(data: Any) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp

# ============================================================================
# ROUTES
# ============================================================================
MANIFEST = {
    "id": "org.stremio.ufo.addon",
    "version": "2.0.0",
    "name": ADDON_NAME,
    "description": "UFO Addon - StreamingCommunity Vixcloud Direct Mode",
    "logo": ADDON_LOGO,
    "resources": ["stream"],
    "types": ["movie", "series"],
    "catalogs": [],
    "behaviorHints": {"configurable": False}
}

@app.get("/")
@app.head("/")
async def root(request: Request):
    logger.info(f"📡 ROOT endpoint")
    base_url = str(request.base_url).rstrip("/")
    return respond_with({
        "status": "online",
        "addon": ADDON_NAME,
        "manifest": f"{base_url}/manifest.json"
    })

@app.get("/manifest.json")
@app.head("/manifest.json")
async def manifest(request: Request):
    logger.info(f"📄 MANIFEST")
    return respond_with(MANIFEST)

@app.get("/{config:path}/manifest.json")
async def manifest_config(config: str):
    logger.info(f"📄 MANIFEST (config route)")
    return respond_with(MANIFEST)

@app.get("/stream/{type}/{id}.json")
@app.head("/stream/{type}/{id}.json")
@limiter.limit("10/second")
async def streams(request: Request, type: str, id: str):
    logger.info(f"=" * 80)
    logger.info(f"🎬 STREAM REQUEST")
    logger.info(f"   Type: {type} | ID: {id}")
    logger.info(f"=" * 80)
    
    try:
        if type not in ["movie", "series"]:
            raise HTTPException(status_code=404)
        
        async with AsyncSession() as client:
            streams_data = await get_streams(id, client)
        
        if not streams_data.get('streams'):
            logger.warning(f"⚠️ No streams found")
        
        return respond_with(streams_data)
        
    except Exception as e:
        logger.error(f"❌ ERROR: {type(e).__name__}: {str(e)}")
        return respond_with({"streams": []})

@app.get("/{config:path}/stream/{type}/{id}.json")
async def streams_config(config: str, type: str, id: str, request: Request):
    """Legacy route per config base64."""
    logger.info(f"📺 STREAM (config route): {type}/{id}")
    
    try:
        if type not in ["movie", "series"]:
            raise HTTPException(status_code=404)
        
        async with AsyncSession() as client:
            streams_data = await get_streams(id, client)
        
        return respond_with(streams_data)
        
    except Exception as e:
        logger.error(f"❌ ERROR: {type(e).__name__}: {str(e)}")
        return respond_with({"streams": []})

@app.get("/{config:path}/meta/{type}/{id}.json")
async def meta(config: str, type: str, id: str):
    logger.debug(f"📋 META: {type}/{id}")
    return respond_with({
        "meta": {
            "id": id,
            "type": type,
            "name": ADDON_NAME,
            "poster": ADDON_LOGO
        }
    })

@app.get("/{config:path}/catalog/{type}/{id}.json")
async def catalog(config: str, type: str, id: str):
    logger.debug(f"📚 CATALOG")
    return respond_with({"metas": []})

@app.get("/debug/status")
async def debug_status(request: Request):
    logger.info(f"🔧 DEBUG STATUS")
    return respond_with({
        "status": "online ✅",
        "addon": ADDON_NAME,
        "version": MANIFEST["version"],
        "domain": SC_DOMAIN,
        "sc_enabled": SC_ENABLED,
        "proxies_enabled": {
            "SC_PROXY": SC_PROXY,
            "VX_PROXY": VX_PROXY,
            "ForwardProxy": "✅" if FORWARD_PROXY else "❌"
        },
        "timestamp": str(datetime.now())
    })

if __name__ == "__main__":
    logger.info(f"🚀 Starting {ADDON_NAME}...")
    logger.info(f"📍 http://{HOST}:{PORT}")
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level=GENERAL_CONFIG.get("level", "info"))
