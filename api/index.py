import logging
import re
import os
from typing import Dict, Optional, Any, List
from urllib.parse import quote

from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware
import httpx

load_dotenv()

# ============================================================================
# CONFIGURAZIONE
# ============================================================================
ADDON_NAME = "UFO PROXY Addon"
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

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
TMDB_API_KEY = os.getenv('TMDB_KEY', '536b1c46da222eb34b69d168f092b495')

# ============================================================================
# HELPERS
# ============================================================================
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
        logger.error(f"❌ Error TMDB ID: {e}")
        return None

async def get_media_title(client: AsyncSession, tmdb_id: int, is_series: bool, season: str = None, episode: str = None) -> str:
    try:
        params = {"api_key": TMDB_API_KEY, "language": "it-IT"}
        if not is_series:
            url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
            resp = await client.get(url, params=params, timeout=5)
            return resp.json().get("title", "Film")
        else:
            url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}"
            resp = await client.get(url, params=params, timeout=5)
            return resp.json().get('name', f"S{season}E{episode}")
    except:
        return "Streaming"

# ============================================================================
# EXTRACTOR & PROXY
# ============================================================================
class StreamingCommunityExtractor:
    def __init__(self):
        self.domain = CONFIG['Siti']['StreamingCommunity']['url']

    async def extract_vixcloud_url(self, link: str, client: AsyncSession) -> List[Dict]:
        try:
            headers = {'Referer': f"{self.domain}/", 'User-Agent': USER_AGENT}
            response = await client.get(link, headers=headers, timeout=15)
            if response.status_code != 200: return []

            soup = BeautifulSoup(response.text, "lxml")
            video_data = next((s.string for s in soup.find_all("script") if s.string and "token" in s.string), None)
            if not video_data: return []

            token = re.search(r"'token':\s*'(\w+)'", video_data).group(1)
            expires = re.search(r"'expires':\s*'(\d+)'", video_data).group(1)
            server_url = re.search(r"url:\s*'([^']+)'", video_data).group(1)
            
            separator = "&" if "?" in server_url else "?"
            final_url = f"{server_url}{separator}token={token}&expires={expires}"
            if "window.canPlayFHD = true" in video_data: final_url += "&h=1"
            
            if ".m3u8" not in final_url:
                base, params = final_url.split("?", 1) if "?" in final_url else (final_url, "")
                final_url = f"{base}.m3u8?{params}" if params else f"{base}.m3u8"

            quality = "1080p" if "window.canPlayFHD = true" in video_data else "720p"
            return [{"quality": quality, "url": final_url}]
        except Exception as e:
            logger.error(f"❌ Extractor: {e}")
            return []

    async def get_streams(self, id: str, client: AsyncSession, base_url: str) -> Dict:
        streams = {'streams': []}
        try:
            is_series = ':' in id
            parts = id.split(':')
            content_id = parts[0]
            season, episode = (parts[1], parts[2]) if is_series else (None, None)

            tmdb_id = await get_tmdb_id_from_imdb(content_id, client) if content_id.startswith('tt') else int(content_id)
            if not tmdb_id: return streams

            media_title = await get_media_title(client, tmdb_id, is_series, season, episode)
            target_url = f'{self.domain}/tv/{tmdb_id}/{season}/{episode}/' if is_series else f'{self.domain}/movie/{tmdb_id}/'
            
            results = await self.extract_vixcloud_url(target_url, client)
            
            for res in results:
                # CREAZIONE URL PROXY PER WINDOWS
                proxied_url = f"{base_url}/proxy/stream?url={quote(res['url'])}"
                
                streams['streams'].append({
                    "name": f"🛸 UFO PROXY\n{res['quality']}",
                    "title": f"{media_title}\n(Ottimizzato per Windows/TV)",
                    "url": proxied_url,
                    "behaviorHints": {
                        "notWebReady": False,
                        "bingeGroup": "ufo-addon"
                    }
                })
        except Exception as e:
            logger.error(f"❌ Stream Error: {e}")
        return streams

# ============================================================================
# FASTAPI
# ============================================================================
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

extractor = StreamingCommunityExtractor()

def respond_with(data: Any) -> JSONResponse:
    return JSONResponse(content=data, headers={"Access-Control-Allow-Origin": "*"})

@app.get("/")
@app.head("/")
async def root():
    return respond_with({"status": "online", "addon": ADDON_NAME})

@app.get("/U0MQ/manifest.json")
@app.head("/U0MQ/manifest.json")
async def manifest():
    return respond_with({
        "id": "org.stremio.ufo.proxy",
        "version": "1.4.0",
        "name": ADDON_NAME,
        "description": "Streaming con Bypass Proxy per Windows",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": []
    })

@app.get("/U0MQ/stream/{type}/{id}.json")
@app.head("/U0MQ/stream/{type}/{id}.json")
async def streams(request: Request, type: str, id: str):
    base_url = str(request.base_url).rstrip("/")
    async with AsyncSession() as client:
        streams_data = await extractor.get_streams(id, client, base_url)
    return respond_with(streams_data)

# ============================================================================
# ROTTA PROXY (IL CUORE DEL FIX PER WINDOWS)
# ============================================================================
@app.get("/proxy/stream")
async def proxy_stream(url: str):
    async def stream_generator():
        headers = {
            "User-Agent": USER_AGENT,
            "Referer": "https://vixsrc.to/",
            "Origin": "https://vixsrc.to"
        }
        # httpx gestisce meglio lo streaming di grandi file rispetto a curl_cffi in questo contesto
        async with httpx.AsyncClient() as client:
            try:
                async with client.stream("GET", url, headers=headers, follow_redirects=True, timeout=None) as r:
                    async for chunk in r.aiter_bytes(chunk_size=16384):
                        yield chunk
            except Exception as e:
                logger.error(f"❌ Proxy Stream Error: {e}")

    return StreamingResponse(stream_generator(), media_type="application/x-mpegURL")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
