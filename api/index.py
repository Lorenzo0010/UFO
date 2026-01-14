import json
import logging
import re
import os
from typing import Dict, Optional, Any, List
from datetime import datetime

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
# LOGGING CONFIGURAZIONE
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
# CONFIGURAZIONE DA CONFIG.JSON
# ============================================================================
try:
    with open('config.json') as f:
        CONFIG_DATA = json.load(f)
    logger.info("✅ config.json caricato")
except FileNotFoundError:
    logger.warning("⚠️ config.json non trovato, usando default")
    CONFIG_DATA = {
        "Siti": {
            "StreamingCommunity": {
                "url": "https://vixsrc.to",
                "SC_ForwardProxy": "0",
                "SC_PROXY": "0",
                "VX_ForwardProxy": "0",
                "VX_PROXY": "0",
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

# Estrai configurazioni
SITE_CONFIG = CONFIG_DATA.get("Siti", {})
GENERAL_CONFIG = CONFIG_DATA.get("General", {})

SC_DOMAIN = SITE_CONFIG.get("StreamingCommunity", {}).get("url", "https://vixsrc.to")
SC_ENABLED = SITE_CONFIG.get("StreamingCommunity", {}).get("enabled", "1")
SC_PROXY = SITE_CONFIG.get("StreamingCommunity", {}).get("SC_PROXY", "0")
SC_FORWARDPROXY = SITE_CONFIG.get("StreamingCommunity", {}).get("SC_ForwardProxy", "0")
VX_PROXY = SITE_CONFIG.get("StreamingCommunity", {}).get("VX_PROXY", "0")
VX_FORWARDPROXY = SITE_CONFIG.get("StreamingCommunity", {}).get("VX_ForwardProxy", "0")

HOST = GENERAL_CONFIG.get("HOST", "0.0.0.0")
PORT = int(GENERAL_CONFIG.get("PORT", 8080))
ADDON_NAME = GENERAL_CONFIG.get("Name", "UFO Addon")
ADDON_ICON = GENERAL_CONFIG.get("Icon", "🛸")
LOG_LEVEL = GENERAL_CONFIG.get("level", "info")
GLOBAL_PROXY = GENERAL_CONFIG.get("Global_Proxy", "0")

# Carica variabili d'ambiente
TMDB_API_KEY = os.getenv('TMDB_KEY', '536b1c46da222eb34b69d168f092b495')
PROXY_CREDENTIALS = os.getenv('PROXY', '')
FORWARD_PROXY = os.getenv('FORWARDPROXY', '')
PORT_ENV = os.getenv('PORT', None)

if PORT_ENV:
    PORT = int(PORT_ENV)

ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"
User_Agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"

logger.info(f"🚀 {ADDON_NAME} INIZIALIZZATO")
logger.info(f"📍 SC Domain: {SC_DOMAIN}")
logger.info(f"🔧 SC Enabled: {SC_ENABLED} | SC_PROXY: {SC_PROXY} | VX_PROXY: {VX_PROXY}")
logger.info(f"🌐 Host: {HOST}:{PORT}")

# ============================================================================
# PROXY CONFIGURATION
# ============================================================================
def get_proxies():
    """Ritorna proxy configurati se abilitati."""
    proxies = {}
    if GLOBAL_PROXY == "1" and PROXY_CREDENTIALS:
        proxies = {"http": PROXY_CREDENTIALS, "https": PROXY_CREDENTIALS}
        logger.debug(f"🔀 Global Proxy abilitato")
    elif SC_PROXY == "1" and PROXY_CREDENTIALS:
        try:
            proxy_list = json.loads(PROXY_CREDENTIALS)
            if isinstance(proxy_list, list) and proxy_list:
                import random
                proxy = random.choice(proxy_list)
                proxies = {"http": proxy, "https": proxy}
                logger.debug(f"🔀 SC Proxy abilitato (random)")
        except:
            pass
    return proxies

# ============================================================================
# TMDB HELPERS
# ============================================================================
async def get_tmdb_id_from_imdb(imdb_id: str, client: AsyncSession) -> Optional[int]:
    """Converte IMDb ID a TMDB ID."""
    logger.info(f"🔄 TMDB Lookup: {imdb_id}")
    try:
        url = f"https://api.themoviedb.org/3/find/{imdb_id}"
        params = {"external_source": "imdb_id", "api_key": TMDB_API_KEY, "language": "it"}
        
        response = await client.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('movie_results'):
                tmdb_id = data['movie_results'][0].get('id')
                logger.info(f"✅ {imdb_id} → TMDB {tmdb_id} (Movie)")
                return tmdb_id
            elif data.get('tv_results'):
                tmdb_id = data['tv_results'][0].get('id')
                logger.info(f"✅ {imdb_id} → TMDB {tmdb_id} (TV)")
                return tmdb_id
        logger.warning(f"⚠️ TMDB lookup failed: {imdb_id}")
        return None
    except Exception as e:
        logger.error(f"❌ TMDB Exception: {e}")
        return None

async def get_media_title(client: AsyncSession, tmdb_id: int, is_series: bool, season: str = None, episode: str = None) -> str:
    """Recupera titolo da TMDB."""
    try:
        params = {"api_key": TMDB_API_KEY, "language": "it-IT"}
        
        if not is_series:
            url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
            response = await client.get(url, params=params, timeout=5)
            if response.status_code == 200:
                return response.json().get("title", f"Film {tmdb_id}")
            return f"Film {tmdb_id}"
        else:
            url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}"
            response = await client.get(url, params=params, timeout=5)
            if response.status_code == 200:
                return response.json().get('name', f"Episodio {episode}")
            return f"Episodio {episode}"
    except Exception as e:
        logger.warning(f"⚠️ Title fetch failed: {e}")
        return "Titolo non disponibile"

# ============================================================================
# VIXCLOUD EXTRACTOR
# ============================================================================
class VixCloudExtractor:
    def __init__(self):
        self.domain = SC_DOMAIN
        self.random_headers = Headers()

    async def extract_vixcloud_url(self, link: str, client: AsyncSession, proxies: dict) -> List[Dict]:
        """Estrae M3U8 URL da vixcloud."""
        logger.info(f"🔍 Estrattore Vixcloud avviato")
        logger.info(f"   Target: {link}")
        
        try:
            headers = self.random_headers.generate()
            headers['Referer'] = f"{self.domain}/"
            headers['User-Agent'] = User_Agent
            
            response = await client.get(link, headers=headers, timeout=15, proxies=proxies)
            logger.info(f"✅ Response: {response.status_code}")
            
            if response.status_code != 200:
                logger.error(f"❌ Status code: {response.status_code}")
                return []

            soup = BeautifulSoup(response.text, "lxml")
            scripts = soup.find_all("script")
            logger.info(f"   Found {len(scripts)} script tags")
            
            video_data = None
            for i, script in enumerate(scripts):
                if script.string and "token" in script.string and "expires" in script.string:
                    video_data = script.string
                    logger.info(f"✅ Video data trovato (Script[{i}])")
                    break
            
            if not video_data:
                logger.error(f"❌ Video data script non trovato")
                return []

            token_match = re.search(r"'token':\s*'(\w+)'", video_data)
            expires_match = re.search(r"'expires':\s*'(\d+)'", video_data)
            url_match = re.search(r"url:\s*'([^']+)'", video_data)
            
            if not all([token_match, expires_match, url_match]):
                logger.error(f"❌ Parametri non trovati")
                return []

            token = token_match.group(1)
            expires = expires_match.group(1)
            server_url = url_match.group(1)
            
            logger.info(f"✅ Parametri estratti:")
            logger.info(f"   Token: {token[:10]}...")
            logger.info(f"   Expires: {expires}")
            
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

            logger.info(f"✅ Final URL generato")

            # Analizza M3U8 per qualità
            detected_quality = "Auto"
            max_height = 0
            try:
                m3u8_res = await client.get(final_url, headers=headers, timeout=6, proxies=proxies)
                if m3u8_res.status_code == 200:
                    lines = m3u8_res.text.splitlines()
                    for line in lines:
                        if "RESOLUTION=" in line:
                            res_match = re.search(r'RESOLUTION=(\d+)x(\d+)', line)
                            if res_match:
                                height = int(res_match.group(2))
                                if height > max_height:
                                    max_height = height
                    if max_height > 0:
                        detected_quality = f"{max_height}p"
            except:
                detected_quality = "1080p" if "window.canPlayFHD = true" in video_data else "720p"

            logger.info(f"✅ Quality: {detected_quality}")
            return [{
                "quality": detected_quality,
                "url": final_url,
                "height": max_height
            }]

        except Exception as e:
            logger.error(f"❌ EXTRACTOR ERROR: {e}")
            return []

    async def get_streams(self, id: str, client: AsyncSession) -> Dict:
        """Estrae stream da StreamingCommunity."""
        logger.info(f"=" * 80)
        logger.info(f"📺 GET_STREAMS: {id}")
        logger.info(f"=" * 80)
        
        streams = {'streams': []}
        
        try:
            # Parse ID
            is_series = False
            season = None
            episode = None
            content_id = id.split(':')[0] if ':' in id else id
            
            if ':' in id:
                parts = id.split(':')
                content_id = parts[0]
                if len(parts) >= 3:
                    season, episode = parts[1], parts[2]
                    is_series = True
                    logger.info(f"✅ SERIE - S{season}E{episode}")
            else:
                logger.info(f"✅ FILM")

            # Determina TMDB ID
            tmdb_id = None
            if content_id.startswith('tt'):
                logger.info(f"   IMDb format: {content_id}")
                tmdb_id = await get_tmdb_id_from_imdb(content_id, client)
                if not tmdb_id:
                    logger.error(f"❌ IMDb → TMDB conversion fallito")
                    return streams
            else:
                try:
                    tmdb_id = int(content_id)
                    logger.info(f"✅ TMDB ID: {tmdb_id}")
                except ValueError:
                    logger.error(f"❌ Invalid ID format: {content_id}")
                    return streams

            # Fetch title
            media_title = await get_media_title(client, tmdb_id, is_series, season, episode)

            # Build URL
            if is_series:
                url = f'{SC_DOMAIN}/tv/{tmdb_id}/{season}/{episode}/'
            else:
                url = f'{SC_DOMAIN}/movie/{tmdb_id}/'
            logger.info(f"   URL: {url}")
            
            # Get proxies
            proxies = get_proxies()
            
            # Extract streams
            extractor = VixCloudExtractor()
            results = await extractor.extract_vixcloud_url(url, client, proxies)
            
            for res in results:
                stream_entry = {
                    "name": f"{ADDON_ICON} {res['quality']}", 
                    "title": media_title,
                    "url": res['url'],
                    "behaviorHints": {
                        "proxyHeaders": {
                            "request": {
                                "User-Agent": User_Agent,
                                "Referer": f"{SC_DOMAIN}/"
                            }
                        },
                        "notWebReady": False,
                        "bingeGroup": "streamingcommunity"
                    }
                }
                streams['streams'].append(stream_entry)

            logger.info(f"✅ COMPLETED - {len(streams['streams'])} stream(s)")

        except Exception as e:
            logger.error(f"❌ GET_STREAMS ERROR: {e}")
        
        return streams

# ============================================================================
# FASTAPI APP
# ============================================================================
app = FastAPI(title=ADDON_NAME)

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

extractor = VixCloudExtractor()

def respond_with(data: Any) -> JSONResponse:
    resp = JSONResponse(content=data)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp

# ============================================================================
# MANIFEST & ROUTES
# ============================================================================
MANIFEST = {
    "id": "org.stremio.ufo.addon",
    "version": "2.0.0",
    "name": ADDON_NAME,
    "description": "UFO Addon - StreamingCommunity Vixcloud",
    "logo": ADDON_LOGO,
    "resources": ["stream"],
    "types": ["movie", "series"],
    "catalogs": [],
    "behaviorHints": {"configurable": False}
}

@app.get("/")
@app.head("/")
async def root(request: Request):
    logger.info(f"📡 ROOT")
    base_url = str(request.base_url).rstrip("/")
    return respond_with({
        "status": "online ✅",
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
    return respond_with(MANIFEST)

@app.get("/stream/{type}/{id}.json")
@app.head("/stream/{type}/{id}.json")
@limiter.limit("10/second")
async def streams(request: Request, type: str, id: str):
    logger.info(f"🎬 STREAM: {type}/{id}")
    try:
        if type not in ["movie", "series"]:
            raise HTTPException(status_code=404)
        async with AsyncSession() as client:
            streams_data = await extractor.get_streams(id, client)
        return respond_with(streams_data)
    except Exception as e:
        logger.error(f"❌ ERROR: {e}")
        return respond_with({"streams": []})

@app.get("/{config:path}/stream/{type}/{id}.json")
async def streams_config(config: str, type: str, id: str, request: Request):
    logger.info(f"🎬 STREAM (config): {type}/{id}")
    try:
        if type not in ["movie", "series"]:
            raise HTTPException(status_code=404)
        async with AsyncSession() as client:
            streams_data = await extractor.get_streams(id, client)
        return respond_with(streams_data)
    except Exception as e:
        logger.error(f"❌ ERROR: {e}")
        return respond_with({"streams": []})

@app.get("/{config:path}/meta/{type}/{id}.json")
async def meta(config: str, type: str, id: str):
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
    return respond_with({"metas": []})

@app.get("/debug/status")
async def debug_status(request: Request):
    logger.info(f"🔧 DEBUG STATUS")
    return respond_with({
        "status": "online ✅",
        "addon": ADDON_NAME,
        "version": MANIFEST["version"],
        "config": {
            "domain": SC_DOMAIN,
            "enabled": SC_ENABLED,
            "SC_PROXY": SC_PROXY,
            "VX_PROXY": VX_PROXY,
            "FORWARD_PROXY": "✅" if FORWARD_PROXY else "❌",
            "GLOBAL_PROXY": GLOBAL_PROXY
        },
        "timestamp": str(datetime.now())
    })

if __name__ == "__main__":
    logger.info(f"🚀 Avvio {ADDON_NAME}...")
    logger.info(f"📍 http://{HOST}:{PORT}")
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level=LOG_LEVEL)
