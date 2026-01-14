import json
import logging
import re
import os
from typing import Dict, Optional, Any, List
from urllib.parse import urljoin, quote

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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

User_Agent = "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0"
TMDB_API_KEY = os.getenv('TMDB_KEY', '536b1c46da222eb34b69d168f092b495')

def clean_id(id_str: str) -> str:
    return id_str.split(':')[0] if ':' in id_str else id_str

# ============================================================================
# PROXY ENDPOINT PER VIXCLOUD STREAMING
# ============================================================================
async def proxy_stream(url: str, client: AsyncSession, range_header: Optional[str] = None) -> tuple:
    """Effettua richiesta al vixcloud server e ritorna il contenuto con headers corretti."""
    try:
        headers = {
            'User-Agent': User_Agent,
            'Referer': 'https://vixsrc.to/',
            'Origin': 'https://vixsrc.to',
            'Connection': 'keep-alive',
        }
        
        if range_header:
            headers['Range'] = range_header
        
        response = await client.get(url, headers=headers, timeout=30, follow_redirects=True)
        
        if response.status_code == 206:
            return response.content, 206, {
                'Content-Range': response.headers.get('Content-Range'),
                'Content-Length': response.headers.get('Content-Length'),
                'Content-Type': 'video/mp2t'
            }
        elif response.status_code == 200:
            return response.content, 200, {
                'Content-Length': str(len(response.content)),
                'Content-Type': response.headers.get('Content-Type', 'video/mp2t'),
                'Accept-Ranges': 'bytes'
            }
        else:
            return None, response.status_code, {}
            
    except Exception as e:
        logger.error(f"❌ Proxy Error: {e}")
        return None, 500, {}

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
        logger.error(f"❌ Error TMDB: {e}")
        return None

async def get_media_title(client: AsyncSession, tmdb_id: int, is_series: bool, season: str = None, episode: str = None) -> str:
    try:
        language = "it-IT"
        params = {"api_key": TMDB_API_KEY, "language": language}
        
        if not is_series:
            url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
            response = await client.get(url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                return data.get("title", f"Film {tmdb_id}")
            return f"Film {tmdb_id}"
        else:
            ep_url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}"
            ep_resp = await client.get(ep_url, params=params, timeout=5)
            if ep_resp.status_code == 200:
                ep_data = ep_resp.json()
                return ep_data.get('name', f"Episodio {episode}")
            return f"Episodio {episode}"
            
    except Exception as e:
        logger.error(f"❌ Errore TMDB: {e}")
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
            
            if "?b=1" in server_url and "b=1" not in final_url: final_url += "&b=1"
            if "window.canPlayFHD = true" in video_data: final_url += "&h=1"
            
            if ".m3u8" not in final_url:
                 if "?" in final_url:
                     base, params = final_url.split("?", 1)
                     if not base.endswith(".m3u8"): final_url = f"{base}.m3u8?{params}"
                 else:
                     final_url += ".m3u8"

            # Analisi qualità
            detected_quality = "Auto"
            max_height = 0

            try:
                m3u8_res = await client.get(final_url, headers=headers, timeout=6)
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
                        
            except Exception as e:
                logger.warning(f"⚠️ Impossibile analizzare m3u8: {e}")

            if max_height == 0 and "window.canPlayFHD = true" in video_data:
                detected_quality = "1080p"
            elif max_height == 0:
                detected_quality = "720p"

            logger.info(f"✅ URL Master: {detected_quality}")
            
            return [{
                "quality": detected_quality,
                "url": final_url,
                "height": max_height
            }]

        except Exception as e:
            logger.error(f"❌ Extractor Error: {e}")
            return []

    async def get_streams(self, id: str, client: AsyncSession, base_url: str) -> Dict:
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

            media_title = await get_media_title(client, tmdb_id, is_series, season, episode)

            url = f'{self.domain}/tv/{tmdb_id}/{season}/{episode}/' if is_series else f'{self.domain}/movie/{tmdb_id}/'
            
            results = await self.extract_vixcloud_url(url, client)
            
            for res in results:
                # ✅ NUOVO: Usa il proxy endpoint locale
                proxy_url = f"{base_url}/proxy?url={quote(res['url'], safe='')}"
                
                streams['streams'].append({
                    "name": f"🛸 {res['quality']}", 
                    "title": media_title,
                    "url": proxy_url,  # ✅ URL del proxy locale
                    "behaviorHints": {
                        "proxyHeaders": {"request": {"user-agent": User_Agent}},
                        "notWebReady": False,  # ✅ CAMBIATO: Il proxy gestisce tutto
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
        "version": "1.3.5",
        "name": ADDON_NAME,
        "description": "VixSrc Stream via Proxy",
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
        if type not in ["movie", "series"]: 
            raise HTTPException(status_code=404)
        
        base_url = str(request.base_url).rstrip("/")
        async with AsyncSession() as client:
            streams_data = await extractor.get_streams(id, client, base_url)
        
        if not streams_data: 
            streams_data = {"streams": []}
        return respond_with(streams_data)
    except Exception as e:
        logger.error(f"❌ Streams endpoint error: {e}")
        return respond_with({"streams": []})

@app.get("/proxy")
@limiter.limit("100/second")
async def proxy(request: Request, url: str):
    """Proxy per bypassare CORS e propagare headers corretti."""
    try:
        range_header = request.headers.get('Range')
        
        async with AsyncSession() as client:
            content, status_code, headers = await proxy_stream(url, client, range_header)
        
        if content is None:
            return JSONResponse({"error": "Stream not available"}, status_code=status_code)
        
        return FileResponse(
            iter([content]),
            status_code=status_code,
            media_type=headers.get('Content-Type', 'video/mp2t'),
            headers=headers
        )
    except Exception as e:
        logger.error(f"❌ Proxy error: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)

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
