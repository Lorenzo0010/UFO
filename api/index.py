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

load_dotenv()

# ============================================================================
# CONFIGURAZIONE
# ============================================================================
ADDON_NAME = "UFO PROXY V5"
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"
VIX_DOMAIN = "https://vixsrc.to"

# Impostiamo un livello di log più dettagliato per debug
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
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
            # Usiamo headers identici a Chrome per l'estrazione
            headers = {
                'Referer': f"{VIX_DOMAIN}/",
                'User-Agent': USER_AGENT,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7'
            }
            logger.info(f"🔍 Scraping pagina: {link}")
            response = await client.get(link, headers=headers, timeout=15)
            
            if response.status_code != 200: 
                logger.error(f"Errore status pagina: {response.status_code}")
                return []

            soup = BeautifulSoup(response.text, "lxml")
            script = next((s.string for s in soup.find_all("script") if s.string and "token" in s.string), None)
            if not script: 
                logger.warning("Nessun token trovato nello script")
                return []

            token = re.search(r"'token':\s*'(\w+)'", script).group(1)
            expires = re.search(r"'expires':\s*'(\d+)'", script).group(1)
            raw_url = re.search(r"url:\s*'([^']+)'", script).group(1)
            
            final_url = f"{raw_url}?token={token}&expires={expires}"
            if "b=1" not in final_url: final_url += "&b=1"
            if "window.canPlayFHD = true" in script: final_url += "&h=1"
            
            if ".m3u8" not in final_url:
                base, params = final_url.split("?", 1)
                final_url = f"{base}.m3u8?{params}"

            quality = "1080p" if "h=1" in final_url else "720p"
            
            # Logghiamo l'URL generato per debug
            logger.info(f"✅ URL Vix Generato: {final_url[:50]}...")
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
                encoded_vix_url = quote(res['url'])
                # Passiamo anche il referer corretto al proxy
                encoded_referer = quote(f"{VIX_DOMAIN}/")
                
                proxy_link = f"{proxy_base}/proxy/playlist?url={encoded_vix_url}&ref={encoded_referer}"
                
                streams['streams'].append({
                    "name": f"🛸 UFO\n{res['quality']}",
                    "title": f"{title}\n✅ Windows/Android Fix",
                    "url": proxy_link,
                    "behaviorHints": {
                        "notWebReady": False, 
                        "bingeGroup": "ufo-proxy-v5"
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

# Usiamo impersonate="chrome" per bypassare i blocchi 403
extractor = StreamingCommunityExtractor()

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    base_url = str(request.base_url).rstrip("/")
    manifest_url = f"{base_url}/U0MQ/manifest.json"
    install_url = f"stremio://{manifest_url.replace('https://', '').replace('http://', '')}"
    return f"""
    <html>
    <body style="background:#111; color:white; font-family:sans-serif; text-align:center; padding:50px;">
        <img src="{ADDON_LOGO}" width="100" style="border-radius:50%">
        <h1>{ADDON_NAME}</h1>
        <p>Proxy Attivo con bypass <b>TLS Fingerprint (Fix 403)</b></p>
        <div style="background:#222; padding:15px; margin:20px auto; max-width:600px; word-break:break-all; font-family:monospace; border:1px solid #444;">
            {manifest_url}
        </div>
        <a href="{install_url}" style="background:#00d2ff; color:black; padding:10px 20px; text-decoration:none; font-weight:bold; border-radius:5px;">Installa su Stremio</a>
    </body>
    </html>
    """

@app.get("/U0MQ/manifest.json")
async def manifest():
    return JSONResponse({
        "id": "org.ufo.proxy.v5",
        "version": "1.0.5",
        "name": ADDON_NAME,
        "description": "Proxy V5 con Curl Impersonation (Fix 403)",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": []
    }, headers={"Access-Control-Allow-Origin": "*"})

@app.get("/U0MQ/stream/{type}/{id}.json")
async def stream_handler(request: Request, type: str, id: str):
    base_url = str(request.base_url).rstrip("/")
    # Usiamo curl_cffi anche qui
    async with AsyncSession(impersonate="chrome") as client:
        streams = await extractor.get_streams(id, client, base_url)
    return JSONResponse(streams, headers={"Access-Control-Allow-Origin": "*"})

# ============================================================================
# PROXY REWRITER CON CURL_CFFI (FIX 403)
# ============================================================================

@app.get("/proxy/playlist")
async def proxy_playlist(request: Request, url: str, ref: str = f"{VIX_DOMAIN}/"):
    """Scarica la playlist .m3u8 usando un Browser Fingerprint reale"""
    base_url = str(request.base_url).rstrip("/")
    
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": ref,
        "Origin": VIX_DOMAIN,
        "Accept": "*/*"
    }

    try:
        # Usiamo AsyncSession con impersonate="chrome" per evitare il 403
        async with AsyncSession(impersonate="chrome") as client:
            resp = await client.get(url, headers=headers, timeout=20)
            
            if resp.status_code != 200:
                logger.error(f"❌ Proxy Playlist Error: {resp.status_code} - {resp.text[:100]}")
                return Response(f"VixCloud Error: {resp.status_code}", status_code=502)

            content = resp.text
            new_lines = []

            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    new_lines.append(line)
                else:
                    # Riscriviamo i segmenti puntando al nostro proxy
                    full_seg_url = urljoin(url, line)
                    encoded_seg = quote(full_seg_url)
                    encoded_ref = quote(ref)
                    # Passiamo ref e url
                    new_lines.append(f"{base_url}/proxy/segment?url={encoded_seg}&ref={encoded_ref}")

            return Response(
                content="\n".join(new_lines),
                media_type="application/vnd.apple.mpegurl",
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Cache-Control": "no-cache"
                }
            )

    except Exception as e:
        logger.error(f"❌ Proxy Exception: {e}")
        return Response("Internal Error", status_code=500)

@app.get("/proxy/segment")
async def proxy_segment(url: str, ref: str = f"{VIX_DOMAIN}/"):
    """Scarica i segmenti .ts fingendosi Chrome"""
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": ref,
        "Origin": VIX_DOMAIN,
        "Accept": "*/*"
    }

    async def iter_file():
        # Creiamo una sessione che supporti lo streaming
        async with AsyncSession(impersonate="chrome") as client:
            try:
                # Usiamo stream=True di curl_cffi
                async with client.request("GET", url, headers=headers, stream=True) as resp:
                    if resp.status_code != 200:
                        logger.error(f"Segment Error {resp.status_code} on {url}")
                        yield b""
                        return
                        
                    async for chunk in resp.aiter_content():
                        yield chunk
            except Exception as e:
                logger.error(f"Segment Stream Error: {e}")

    return StreamingResponse(
        iter_file(), 
        media_type="video/mp2t",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "public, max-age=31536000"
        }
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
