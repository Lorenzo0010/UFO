import logging
import re
import os
from urllib.parse import quote, unquote, urljoin

from curl_cffi.requests import AsyncSession
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.middleware import SlowAPIMiddleware
import httpx

load_dotenv()

# ============================================================================
# CONFIGURAZIONE
# ============================================================================
ADDON_NAME = "UFO PROXY REWRITER"
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"

# URL Base di VixCloud (usato per i referer)
VIX_DOMAIN = "https://vixsrc.to"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# User-Agent costante per tutto il ciclo di vita
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
TMDB_API_KEY = os.getenv('TMDB_KEY', '536b1c46da222eb34b69d168f092b495')

# ============================================================================
# UTILS
# ============================================================================
async def get_tmdb_id_from_imdb(imdb_id: str, client: AsyncSession) -> int:
    try:
        response = await client.get(
            f"https://api.themoviedb.org/3/find/{imdb_id}",
            params={"external_source": "imdb_id", "api_key": TMDB_API_KEY, "language": "it"},
            timeout=10
        )
        data = response.json()
        if data.get('movie_results'): return data['movie_results'][0].get('id')
        if data.get('tv_results'): return data['tv_results'][0].get('id')
    except:
        pass
    return None

async def get_media_title(client: AsyncSession, tmdb_id: int, is_series: bool, season: str, episode: str) -> str:
    try:
        params = {"api_key": TMDB_API_KEY, "language": "it-IT"}
        if is_series:
            url = f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{season}/episode/{episode}"
            resp = await client.get(url, params=params, timeout=5)
            return resp.json().get('name', f"Episodio {episode}")
        else:
            url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
            resp = await client.get(url, params=params, timeout=5)
            return resp.json().get("title", "Film")
    except:
        return "Video"

# ============================================================================
# EXTRACTOR
# ============================================================================
class StreamingCommunityExtractor:
    async def get_vix_master_url(self, link: str, client: AsyncSession) -> list:
        try:
            headers = {'Referer': f"{VIX_DOMAIN}/", 'User-Agent': USER_AGENT}
            response = await client.get(link, headers=headers, timeout=15)
            if response.status_code != 200: return []

            soup = BeautifulSoup(response.text, "lxml")
            script = next((s.string for s in soup.find_all("script") if s.string and "token" in s.string), None)
            if not script: return []

            token = re.search(r"'token':\s*'(\w+)'", script).group(1)
            expires = re.search(r"'expires':\s*'(\d+)'", script).group(1)
            raw_url = re.search(r"url:\s*'([^']+)'", script).group(1)
            
            # Costruzione URL pulito
            final_url = f"{raw_url}?token={token}&expires={expires}"
            if "b=1" not in final_url: final_url += "&b=1"
            # Forza 1080p se disponibile
            if "window.canPlayFHD = true" in script: final_url += "&h=1"
            
            # Assicuriamoci che finisca per .m3u8 per compatibilità
            if ".m3u8" not in final_url:
                base, params = final_url.split("?", 1)
                final_url = f"{base}.m3u8?{params}"

            quality = "1080p" if "h=1" in final_url else "720p"
            return [{"quality": quality, "url": final_url}]
        except Exception as e:
            logger.error(f"Extractor Error: {e}")
            return []

    async def get_streams(self, id: str, client: AsyncSession, proxy_base: str) -> dict:
        streams = {'streams': []}
        try:
            is_series = ':' in id
            parts = id.split(':')
            content_id = parts[0]
            
            tmdb_id = await get_tmdb_id_from_imdb(content_id, client) if content_id.startswith('tt') else int(content_id)
            if not tmdb_id: return streams

            season = parts[1] if is_series else None
            episode = parts[2] if is_series else None
            
            title = await get_media_title(client, tmdb_id, is_series, season, episode)
            
            target_url = f'{VIX_DOMAIN}/tv/{tmdb_id}/{season}/{episode}/' if is_series else f'{VIX_DOMAIN}/movie/{tmdb_id}/'
            results = await self.get_vix_master_url(target_url, client)

            for res in results:
                # QUI AVVIENE LA MAGIA:
                # Invece di dare l'url diretto, diamo l'url del NOSTRO proxy
                encoded_vix_url = quote(res['url'])
                proxy_link = f"{proxy_base}/proxy/playlist?url={encoded_vix_url}"
                
                streams['streams'].append({
                    "name": f"🛸 UFO\n{res['quality']}",
                    "title": f"{title}\n✅ Windows & Android",
                    "url": proxy_link,
                    "behaviorHints": {
                        "notWebReady": False, # Importante per Windows
                        "bingeGroup": "ufo-rewriter"
                    }
                })
        except Exception as e:
            logger.error(f"Stream generation error: {e}")
        return streams

# ============================================================================
# FASTAPI APP
# ============================================================================
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

extractor = StreamingCommunityExtractor()

# --- HOMEPAGE ---
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    base_url = str(request.base_url).rstrip("/")
    manifest_url = f"{base_url}/U0MQ/manifest.json"
    install_url = f"stremio://{manifest_url.replace('https://', '').replace('http://', '')}"
    
    return f"""
    <!DOCTYPE html>
    <html lang="it">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{ADDON_NAME}</title>
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0f0f0f; color: #e0e0e0; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }}
            .card {{ background-color: #1a1a1a; padding: 2rem; border-radius: 12px; box-shadow: 0 8px 24px rgba(0,0,0,0.5); text-align: center; max-width: 500px; width: 90%; }}
            img {{ width: 100px; height: 100px; border-radius: 50%; object-fit: cover; margin-bottom: 1rem; border: 2px solid #00d2ff; }}
            h1 {{ color: #00d2ff; margin: 0 0 1rem 0; font-size: 1.5rem; }}
            p {{ margin-bottom: 1.5rem; color: #aaaaaa; }}
            .link-box {{ background: #000; padding: 12px; border-radius: 6px; border: 1px solid #333; word-break: break-all; font-family: monospace; color: #00ff88; margin-bottom: 1.5rem; user-select: all; }}
            .btn {{ display: inline-block; background: #00d2ff; color: #000; padding: 12px 24px; border-radius: 6px; text-decoration: none; font-weight: bold; transition: background 0.2s; }}
            .btn:hover {{ background: #00a3cc; }}
        </style>
    </head>
    <body>
        <div class="card">
            <img src="{ADDON_LOGO}" alt="Logo">
            <h1>{ADDON_NAME}</h1>
            <p>Addon configurato con <b>Proxy Rewriting</b>.<br>Funziona su Android, Windows e Web.</p>
            
            <div class="link-box">{manifest_url}</div>
            
            <a href="{install_url}" class="btn">Installa su Stremio</a>
        </div>
    </body>
    </html>
    """

@app.get("/U0MQ/manifest.json")
async def manifest():
    return JSONResponse({
        "id": "org.ufo.rewriter.v4",
        "version": "1.0.4",
        "name": ADDON_NAME,
        "description": "StreamingCommunity con Proxy integrato per Windows/Web",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": []
    }, headers={"Access-Control-Allow-Origin": "*"})

@app.get("/U0MQ/stream/{type}/{id}.json")
async def stream_handler(request: Request, type: str, id: str):
    base_url = str(request.base_url).rstrip("/")
    async with AsyncSession() as client:
        streams = await extractor.get_streams(id, client, base_url)
    return JSONResponse(streams, headers={"Access-Control-Allow-Origin": "*"})

# ============================================================================
# IL CUORE DEL SISTEMA: IL PROXY REWRITER
# ============================================================================

async def fetch_upstream(url: str, headers: dict):
    """Scarica il contenuto dal server originale (VixCloud)"""
    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
        resp = await client.get(url, headers=headers)
        return resp

@app.get("/proxy/playlist")
async def proxy_playlist(request: Request, url: str):
    """
    Questo endpoint scarica il file .m3u8, riscrive i link al suo interno
    e lo serve a Stremio.
    """
    base_url = str(request.base_url).rstrip("/")
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": f"{VIX_DOMAIN}/",
        "Origin": VIX_DOMAIN
    }

    try:
        # 1. Scarica il master m3u8 originale
        resp = await fetch_upstream(url, headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Errore VixCloud")

        content = resp.text
        new_lines = []

        # 2. Riscrivi il contenuto linea per linea
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                # È un commento o metadata, lo teniamo così com'è
                new_lines.append(line)
            else:
                # È un URL (assoluto o relativo)
                # Risolviamo il link completo
                full_segment_url = urljoin(url, line)
                encoded_seg = quote(full_segment_url)
                
                # Sostituiamo con il link al nostro proxy segmenti
                # NOTA: Usiamo /proxy/segment per i file video veri e propri
                proxied_line = f"{base_url}/proxy/segment?url={encoded_seg}"
                new_lines.append(proxied_line)

        # 3. Restituisci la nuova playlist modificata
        return Response(
            content="\n".join(new_lines),
            media_type="application/vnd.apple.mpegurl",
            headers={"Access-Control-Allow-Origin": "*"}
        )

    except Exception as e:
        logger.error(f"Proxy Playlist Error: {e}")
        return Response("Error", status_code=500)

@app.get("/proxy/segment")
async def proxy_segment(url: str):
    """
    Questo endpoint scarica i singoli pezzi video (.ts) e li manda al player.
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": f"{VIX_DOMAIN}/",
        "Origin": VIX_DOMAIN
    }

    async def iter_file():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", url, headers=headers) as r:
                async for chunk in r.aiter_bytes():
                    yield chunk

    return StreamingResponse(
        iter_file(), 
        media_type="video/mp2t",
        headers={"Access-Control-Allow-Origin": "*"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
