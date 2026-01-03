import json
import logging
import re
import os
from typing import Dict, Optional, Any
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
# CONFIGURAZIONE ADATTATA PER VERCEL
# ============================================================================
ADDON_NAME = "UFO addon"
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"

# Configurazioni statiche (senza file json)
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

# UTILITIES
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

# ============================================================================
# EXTRACTOR
# ============================================================================
class StreamingCommunityExtractor:
    def __init__(self):
        self.domain = CONFIG['Siti']['StreamingCommunity']['url']
        self.random_headers = Headers()

    async def extract_vixcloud_url(self, link: str, client: AsyncSession) -> Optional[str]:
        try:
            logger.info(f"🔍 Fetching: {link}")
            headers = self.random_headers.generate()
            headers['Referer'] = f"{self.domain}/"
            headers['User-Agent'] = User_Agent
            
            response = await client.get(link, headers=headers, timeout=15)
            if response.status_code != 200:
                return None

            soup = BeautifulSoup(response.text, "lxml")
            scripts = soup.find_all("script")
            
            for script in scripts:
                if not script.string: continue
                if "token" in script.string and "expires" in script.string:
                    video_data = script.string
                    token_match = re.search(r"'token':\s*'(\w+)'", video_data)
                    expires_match = re.search(r"'expires':\s*'(\d+)'", video_data)
                    url_match = re.search(r"url:\s*'([^']+)'", video_data)
                    
                    if all([token_match, expires_match, url_match]):
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
                        return final_url
            return None
        except Exception as e:
            logger.error(f"❌ Extractor Error: {e}")
            return None

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

            url = f'{self.domain}/tv/{tmdb_id}/{season}/{episode}/' if is_series else f'{self.domain}/movie/{tmdb_id}/'
            stream_url = await self.extract_vixcloud_url(url, client)
            
            if stream_url:
                streams['streams'].append({
                    "name": "🛸UFO",
                    "title": f"{self.domain}",
                    "url": stream_url,
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
    # Rilevamento automatico dell'URL
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
        "description": "VixSrc Stream",
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
