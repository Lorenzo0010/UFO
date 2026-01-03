import logging
import re
import os
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
ADDON_NAME = "UFO Addon"
ADDON_VERSION = "1.3.2"
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"
TMDB_API_KEY = os.getenv('TMDB_KEY', '536b1c46da222eb34b69d168f092b495')
TARGET_URL = "https://vixsrc.to"

# LOGGING
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

# UTILS
limiter = Limiter(key_func=get_remote_address)

async def get_tmdb_id(imdb_id: str, client: AsyncSession) -> str | None:
    if not imdb_id.startswith("tt"):
        return imdb_id
    try:
        res = await client.get(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            params={"external_source": "imdb_id", "api_key": TMDB_API_KEY},
            timeout=5
        )
        data = res.json()
        if data.get('movie_results'): return str(data['movie_results'][0]['id'])
        if data.get('tv_results'): return str(data['tv_results'][0]['id'])
    except Exception as e:
        logger.error(f"TMDB Error: {e}")
    return None

# ============================================================================
# EXTRACTOR LOGIC
# ============================================================================
class VixExtractor:
    def __init__(self):
        self.headers_gen = Headers(browser="chrome", os="win", headers=True)

    async def get_stream_url(self, direct_link: str, client: AsyncSession) -> str | None:
        try:
            headers = self.headers_gen.generate()
            headers['Referer'] = f"{TARGET_URL}/"
            
            response = await client.get(direct_link, headers=headers, timeout=10)
            if response.status_code != 200: return None

            soup = BeautifulSoup(response.text, "lxml")
            scripts = soup.find_all("script")
            
            for script in scripts:
                if not script.string or "token" not in script.string: continue
                
                txt = script.string
                token = re.search(r"'token':\s*'(\w+)'", txt)
                expires = re.search(r"'expires':\s*'(\d+)'", txt)
                url_src = re.search(r"url:\s*'([^']+)'", txt)

                if token and expires and url_src:
                    base_url = url_src.group(1)
                    sep = "&" if "?" in base_url else "?"
                    final_url = f"{base_url}{sep}token={token.group(1)}&expires={expires.group(1)}"
                    
                    if "b=1" not in final_url: final_url += "&b=1"
                    if "window.canPlayFHD = true" in txt: final_url += "&h=1"
                    
                    if ".m3u8" not in final_url:
                        parts = final_url.split("?", 1)
                        final_url = f"{parts[0]}.m3u8?{parts[1]}" if len(parts) > 1 else f"{final_url}.m3u8"
                        
                    return final_url
            return None
        except Exception as e:
            logger.error(f"Extraction Error: {e}")
            return None

    async def handle_request(self, stream_type: str, stream_id: str) -> dict:
        streams = []
        try:
            parts = stream_id.split(':')
            imdb_id = parts[0]
            season = parts[1] if len(parts) > 1 else None
            episode = parts[2] if len(parts) > 2 else None

            async with AsyncSession() as client:
                tmdb_id = await get_tmdb_id(imdb_id, client)
                if not tmdb_id: return {"streams": []}

                path = f"/movie/{tmdb_id}/" if stream_type == "movie" else f"/tv/{tmdb_id}/{season}/{episode}/"
                full_url = f"{TARGET_URL}{path}"
                
                decoded_url = await self.get_stream_url(full_url, client)
                
                if decoded_url:
                    streams.append({
                        "name": f"🛸 {ADDON_NAME}",
                        "title": "VixCloud Source (720p/1080p)",
                        "url": decoded_url,
                        "behaviorHints": {
                            "notWebReady": True,
                            "proxyHeaders": {"request": {"User-Agent": "Mozilla/5.0"}}
                        }
                    })
        except Exception as e:
            logger.error(f"Handler Error: {e}")
        
        return {"streams": streams}

# ============================================================================
# FASTAPI APP
# ============================================================================
app = FastAPI(title=ADDON_NAME, docs_url=None, redoc_url=None)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

extractor = VixExtractor()

@app.get("/")
async def root():
    return {"status": "online", "message": "Addon is running. Install via manifest.json"}

@app.get("/manifest.json")
async def get_manifest(request: Request):
    return JSONResponse(content={
        "id": "org.stremio.ufo.addon",
        "version": ADDON_VERSION,
        "name": ADDON_NAME,
        "description": "Stream from VixSrc",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": [],
        "idPrefixes": ["tt", "tmdb"]
    }, headers={"Access-Control-Allow-Origin": "*"})

@app.get("/stream/{type}/{id}.json")
@limiter.limit("5/second")
async def get_streams(request: Request, type: str, id: str):
    if type not in ["movie", "series"]:
        raise HTTPException(status_code=400, detail="Invalid type")
    
    data = await extractor.handle_request(type, id)
    return JSONResponse(content=data, headers={"Access-Control-Allow-Origin": "*"})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
