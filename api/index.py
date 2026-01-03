import logging
import re
import os
from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from fake_headers import Headers
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware

load_dotenv()

# ============================================================================
# CONFIGURAZIONE
# ============================================================================
ADDON_NAME = "UFO Addon"
ADDON_VERSION = "1.3.3"
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"
TMDB_API_KEY = os.getenv('TMDB_KEY', '536b1c46da222eb34b69d168f092b495')
TARGET_URL = "https://vixsrc.to"

# LOGGING
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

# LIMITER (Per evitare troppe richieste)
limiter = Limiter(key_func=get_remote_address)

# UTILS
async def get_tmdb_id(imdb_id: str, client: AsyncSession) -> str | None:
    # Se è già un numero (ID TMDB), lo restituiamo direttamente
    if not imdb_id.startswith("tt"):
        return imdb_id
    
    # Se è un ID IMDB (tt...), chiediamo a TMDB
    try:
        res = await client.get(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            params={"external_source": "imdb_id", "api_key": TMDB_API_KEY},
            timeout=5
        )
        if res.status_code == 200:
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
            content_id = parts[0]
            season = parts[1] if len(parts) > 1 else None
            episode = parts[2] if len(parts) > 2 else None

            async with AsyncSession() as client:
                tmdb_id = await get_tmdb_id(content_id, client)
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

# ---------------------------------------------------------
# ROTTA PRINCIPALE (LANDING PAGE HTML)
# ---------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    base_url = str(request.base_url).rstrip("/")
    manifest_url = f"{base_url}/manifest.json"
    stremio_url = manifest_url.replace("https://", "stremio://").replace("http://", "stremio://")

    html_content = f"""
    <!DOCTYPE html>
    <html lang="it">
    <head>
        <meta charset="UTF-8">
        <title>{ADDON_NAME}</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: 'Segoe UI', sans-serif; background-color: #0f0f0f; color: #fff; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }}
            .card {{ background: #1a1a1a; padding: 2rem; border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,0.5); text-align: center; max-width: 400px; width: 90%; border: 1px solid #333; }}
            img {{ width: 80px; margin-bottom: 1.5rem; }}
            h1 {{ font-size: 1.5rem; margin: 0 0 0.5rem; color: #fff; }}
            p {{ color: #888; margin-bottom: 2rem; font-size: 0.9rem; }}
            .btn {{ display: block; width: 100%; padding: 0.8rem 0; background: #6c5ce7; color: white; text-decoration: none; border-radius: 8px; font-weight: 600; transition: background 0.2s; border: none; cursor: pointer; }}
            .btn:hover {{ background: #5b4cc4; }}
            .link-box {{ margin-top: 1.5rem; padding: 0.8rem; background: #000; border: 1px solid #333; border-radius: 6px; word-break: break-all; font-family: monospace; font-size: 0.8rem; color: #00b894; user-select: all; }}
            .label {{ font-size: 0.75rem; color: #555; margin-top: 1.5rem; margin-bottom: 0.5rem; display: block; }}
        </style>
    </head>
    <body>
        <div class="card">
            <img src="{ADDON_LOGO}" alt="UFO Logo">
            <h1>{ADDON_NAME}</h1>
            <p>Addon per Stremio configurato correttamente.</p>
            
            <a class="btn" href="{stremio_url}">🚀 Installa su Stremio</a>
            
            <span class="label">Oppure copia questo link manualmente:</span>
            <div class="link-box">{manifest_url}</div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

# ---------------------------------------------------------
# MANIFEST
# ---------------------------------------------------------
@app.get("/manifest.json")
async def get_manifest():
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

# ---------------------------------------------------------
# STREAMS
# ---------------------------------------------------------
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
