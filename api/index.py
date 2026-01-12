import os
import re
import logging
from typing import Dict, Optional
from curl_cffi.requests import AsyncSession
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# ============================================================================
# CONFIGURAZIONE
# ============================================================================
ADDON_NAME = "UFO Addon Lite"
# URL base di VixSrc (puoi cambiarlo se il dominio cambia)
BASE_URL = "https://vixsrc.to"
# Chiave TMDB (Prende quella dalle env vars di HuggingFace, o usa quella di default)
TMDB_API_KEY = os.getenv('TMDB_KEY', '536b1c46da222eb34b69d168f092b495')

# Configurazione Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title=ADDON_NAME)

# CORS (Essenziale per Stremio Web)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================================
# LOGICA DI ESTRAZIONE
# ============================================================================

async def get_tmdb_id(imdb_id: str, client: AsyncSession) -> Optional[str]:
    """Converte ID IMDb (tt12345) in ID TMDB (12345)"""
    if not imdb_id.startswith("tt"):
        return imdb_id  # È già un ID TMDB o altro
    
    try:
        response = await client.get(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            params={"external_source": "imdb_id", "api_key": TMDB_API_KEY},
            timeout=5
        )
        if response.status_code == 200:
            data = response.json()
            for cat in ['movie_results', 'tv_results']:
                if data.get(cat):
                    return str(data[cat][0]['id'])
    except Exception as e:
        logger.error(f"Errore conversione TMDB: {e}")
    return None

async def extract_stream_url(url: str, client: AsyncSession) -> Optional[str]:
    """Estrae il link .m3u8 dalla pagina usando Regex (più veloce di BS4)"""
    try:
        logger.info(f"🔍 Scraping: {url}")
        # 'impersonate' simula un browser reale meglio di fake-headers
        response = await client.get(
            url, 
            impersonate="chrome120", 
            headers={"Referer": f"{BASE_URL}/"},
            timeout=10
        )
        
        if response.status_code != 200:
            return None

        # Cerca i parametri direttamente nel testo dello script
        # Pattern ottimizzato per trovare url, token e scadenza
        pattern = r"url:\s*'([^']+)'[\s\S]*?'token':\s*'([^']+)'[\s\S]*?'expires':\s*'(\d+)'"
        match = re.search(pattern, response.text)

        if match:
            base_stream, token, expires = match.groups()
            
            # Costruzione URL finale
            separator = "&" if "?" in base_stream else "?"
            final_url = f"{base_stream}{separator}token={token}&expires={expires}"
            
            # Aggiunge parametri per FHD se disponibili
            if "window.canPlayFHD = true" in response.text:
                final_url += "&h=1"
            
            # Assicura estensione .m3u8 per compatibilità player
            if ".m3u8" not in final_url:
                if "?" in final_url:
                    final_url = final_url.replace("?", ".m3u8?")
                else:
                    final_url += ".m3u8"
            
            return final_url
            
    except Exception as e:
        logger.error(f"Errore estrazione: {e}")
    return None

# ============================================================================
# ENDPOINTS STREMIO
# ============================================================================

@app.get("/")
async def root():
    return {"status": "online", "addon": ADDON_NAME}

@app.get("/manifest.json")
async def manifest():
    return {
        "id": "org.ufo.addon.lite",
        "version": "1.0.1",
        "name": ADDON_NAME,
        "description": "VixSrc Scraper ottimizzato per Docker",
        "resources": ["stream"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt", "tmdb"],  # FONDAMENTALE: Dice a Stremio di usare questo addon per gli ID IMDB
        "catalogs": []
    }

@app.get("/stream/{type}/{id}.json")
async def stream(type: str, id: str):
    streams_list = []
    
    # Pulisce l'ID da estensioni (es. tt123.json -> tt123)
    clean_id = id.replace(".json", "")
    
    async with AsyncSession() as client:
        # Parsing ID: tt12345 (film) oppure tt12345:1:2 (serie)
        parts = clean_id.split(':')
        imdb_id = parts[0]
        season = parts[1] if len(parts) > 1 else None
        episode = parts[2] if len(parts) > 2 else None

        # Ottieni ID numerico TMDB
        tmdb_id = await get_tmdb_id(imdb_id, client)
        
        if tmdb_id:
            # Costruisci URL pagina target
            if type == "series" and season and episode:
                target_url = f"{BASE_URL}/tv/{tmdb_id}/{season}/{episode}/"
            else:
                target_url = f"{BASE_URL}/movie/{tmdb_id}/"

            # Estrai link
            video_url = await extract_stream_url(target_url, client)
            
            if video_url:
                streams_list.append({
                    "name": "🛸 UFO",
                    "title": "VixCloud [ITA]",
                    "url": video_url,
                    "behaviorHints": {
                        "notWebReady": True, # Suggerisce a Stremio di usare un proxy se necessario
                        "proxyHeaders": {"request": {"User-Agent": "Mozilla/5.0"}}
                    }
                })

    return {"streams": streams_list}
