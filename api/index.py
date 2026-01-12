import re
import os
import logging
from typing import Optional, Dict
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# Configurazione Log
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# Costanti
ADDON_NAME = "UFO Addon"
DOMAIN = "https://vixsrc.to"
TMDB_API_KEY = os.getenv('TMDB_KEY', '536b1c46da222eb34b69d168f092b495')
# Impersonate chrome rende inutile generare header casuali che potrebbero essere inconsistenti
IMPERSONATE = "chrome110" 

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# LOGICA DI ESTRAZIONE (OTTIMIZZATA)
# ============================================================================
class Extractor:
    @staticmethod
    async def get_tmdb_id(imdb_id: str, client: AsyncSession) -> Optional[str]:
        if not imdb_id.startswith('tt'): return imdb_id
        try:
            url = f"https://api.themoviedb.org/3/find/{imdb_id}"
            params = {"external_source": "imdb_id", "api_key": TMDB_API_KEY, "language": "it"}
            r = await client.get(url, params=params)
            data = r.json()
            res = data.get('movie_results') or data.get('tv_results')
            return str(res[0]['id']) if res else None
        except Exception: return None

    async def get_vix_stream(self, url: str, client: AsyncSession) -> Optional[str]:
        try:
            # Impersonate gestisce TLS e Header corretti automaticamente
            resp = await client.get(url, impersonate=IMPERSONATE, timeout=10)
            if resp.status_code != 200: return None

            # Regex ottimizzata per trovare i parametri del player in un colpo solo
            match = re.search(r"url:\s*'([^']+)'.*?'token':\s*'(\w+)'.*?'expires':\s*'(\d+)'", resp.text, re.DOTALL)
            if not match: return None

            base_url, token, expires = match.groups()
            sep = "&" if "?" in base_url else "?"
            final = f"{base_url}{sep}token={token}&expires={expires}"
            
            if "window.canPlayFHD = true" in resp.text: final += "&h=1"
            if ".m3u8" not in final:
                final = final.replace("?", ".m3u8?") if "?" in final else final + ".m3u8"
            
            return final
        except Exception as e:
            logger.error(f"Extraction failed: {e}")
            return None

extractor = Extractor()

# ============================================================================
# ENDPOINTS STREMIO
# ============================================================================
@app.get("/manifest.json")
async def manifest():
    return {
        "id": "org.ufo.addon.docker",
        "version": "1.0.0",
        "name": ADDON_NAME,
        "description": "ITA Streaming Engine",
        "resources": ["stream"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt", "tmdb"]
    }

@app.get("/stream/{type}/{id}.json")
async def stream_handler(type: str, id: str):
    async with AsyncSession() as client:
        # 1. Parsing ID (tt12345 o tt12345:1:1)
        parts = id.split(':')
        imdb_id = parts[0]
        
        # 2. Converti in TMDB ID
        tmdb_id = await extractor.get_tmdb_id(imdb_id, client)
        if not tmdb_id: return {"streams": []}

        # 3. Costruisci URL sorgente
        if type == "series" and len(parts) >= 3:
            target_url = f"{DOMAIN}/tv/{tmdb_id}/{parts[1]}/{parts[2]}/"
        else:
            target_url = f"{DOMAIN}/movie/{tmdb_id}/"

        # 4. Estrai link finale
        stream_url = await extractor.get_vix_stream(target_url, client)
        
        if stream_url:
            return {"streams": [{
                "name": "🛸 UFO",
                "title": "VixCloud High Quality",
                "url": stream_url,
                "behaviorHints": {"notWebReady": True}
            }]}
        
    return {"streams": []}

@app.get("/")
async def index():
    return {"message": "Addon is running on Docker/HuggingFace", "manifest": "/manifest.json"}
