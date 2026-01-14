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
ADDON_NAME = "UFO PROXY FINAL"
ADDON_LOGO = "https://static.vecteezy.com/system/resources/thumbnails/050/270/611/small/ufo-logo-design-no-background-perfect-for-print-on-demand-t-shirt-design-png.png"
SC_DOMAIN = "https://vixsrc.to"

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# USER AGENT (Linux based - come MammaMia)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
TMDB_API_KEY = os.getenv('TMDB_KEY', '536b1c46da222eb34b69d168f092b495')

# HEADERS GLOBALI PER IL PROXY
PROXY_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": f"{SC_DOMAIN}/",
    "Origin": f"{SC_DOMAIN}",
    "Accept": "*/*",
    "Connection": "keep-alive"
}

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
            headers = {
                'Referer': f"{SC_DOMAIN}/",
                'Origin': f"{SC_DOMAIN}",
                'User-Agent': USER_AGENT
            }
            logger.info(f"🔍 Fetching: {link}")
            response = await client.get(link, headers=headers, timeout=15)
            
            if response.status_code != 200: return []

            soup = BeautifulSoup(response.text, "lxml")
            
            # Ricerca script token più robusta
            script_content = None
            for s in soup.find_all("script"):
                if s.string and "token" in s.string and "expires" in s.string:
                    script_content = s.string
                    break
            
            if not script_content: return []

            token = re.search(r"'token':\s*'(\w+)'", script_content).group(1)
            expires = re.search(r"'expires':\s*'(\d+)'", script_content).group(1)
            server_url = re.search(r"url:\s*'([^']+)'", script_content).group(1)
            
            try:
                quality_match = re.search(r'"quality":(\d+)', script_content)
                quality_lbl = f"{quality_match.group(1)}p" if quality_match else "720p"
            except:
                quality_lbl = "720p"

            separator = "&" if "?b=1" in server_url else "?"
            final_url = f"{server_url}{separator}token={token}&expires={expires}"
            
            if "window.canPlayFHD = true" in script_content:
                final_url += "&h=1"
                quality_lbl = "1080p"
            
            # Aggiunta estensione .m3u8 se mancante
            if ".m3u8" not in final_url:
                parts = final_url.split("?")
                final_url = f"{parts[0]}.m3u8?{parts[1]}"

            return [{"quality": quality_lbl, "url": final_url}]
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
            target_url = f'{SC_DOMAIN}/tv/{tmdb_id}/{season}/{episode}/' if is_series else f'{SC_DOMAIN}/movie/{tmdb_id}/'
            
            results = await self.get_vix_master_url(target_url, client)

            for res in results:
                encoded_vix_url = quote(res['url'])
                proxy_link = f"{proxy_base}/proxy/playlist?url={encoded_vix_url}"
                
                streams['streams'].append({
                    "name": f"🛸 UFO\n{res['quality']}",
                    "title": f"{title}\n✅ Proxy Attivo",
                    "url": proxy_link,
                    "behaviorHints": {
                        "notWebReady": False, 
                        "bingeGroup": "ufo-proxy-fixed"
                    }
                })
        except Exception as e:
            logger.error(f"Stream Error: {e}")
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

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    base_url = str(request.base_url).rstrip("/")
    manifest_url = f"{base_url}/U0MQ/manifest.json"
    install_url = f"stremio://{manifest_url.replace('https://', '').replace('http://', '')}"
    return f"""
    <html>
    <body style="background:#0b0b0b; color:#eee; font-family:sans-serif; text-align:center; padding:40px;">
        <img src="{ADDON_LOGO}" width="120" style="border-radius:20px;">
        <h1 style="color:#00d2ff">{ADDON_NAME}</h1>
        <p>Fix Proxy Segmenti</p>
        <div style="background:#1a1a1a; padding:15px; border-radius:8px; display:inline-block; border:1px solid #333; margin:20px 0;">
            <code style="color:#00ff88; font-size:1.1em;">{manifest_url}</code>
        </div><br>
        <a href="{install_url}" style="background:#00d2ff; color:black; padding:12px 25px; text-decoration:none; font-weight:bold; border-radius:30px; display:inline-block;">INSTALLA SU STREMIO</a>
    </body>
    </html>
    """

@app.get("/U0MQ/manifest.json")
async def manifest():
    return JSONResponse({
        "id": "org.ufo.proxy.fixed",
        "version": "2.0.1",
        "name": ADDON_NAME,
        "description": "Proxy VixSrc Fixed",
        "logo": ADDON_LOGO,
        "resources": ["stream"],
        "types": ["movie", "series"],
        "catalogs": []
    }, headers={"Access-Control-Allow-Origin": "*"})

@app.get("/U0MQ/stream/{type}/{id}.json")
async def stream_handler(request: Request, type: str, id: str):
    base_url = str(request.base_url).rstrip("/")
    async with AsyncSession(impersonate="chrome") as client:
        streams = await extractor.get_streams(id, client, base_url)
    return JSONResponse(streams, headers={"Access-Control-Allow-Origin": "*"})

# ============================================================================
# PROXY ENGINE FIX
# ============================================================================

@app.get("/proxy/playlist")
async def proxy_playlist(request: Request, url: str):
    base_url = str(request.base_url).rstrip("/")
    try:
        async with AsyncSession(impersonate="chrome") as client:
            resp = await client.get(url, headers=PROXY_HEADERS, timeout=20)
            
            if resp.status_code != 200:
                return Response(status_code=502)

            content = resp.text
            new_lines = []

            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    new_lines.append(line)
                else:
                    full_seg_url = urljoin(url, line)
                    encoded_seg = quote(full_seg_url)
                    new_lines.append(f"{base_url}/proxy/segment?url={encoded_seg}")

            return Response(
                content="\n".join(new_lines),
                media_type="application/vnd.apple.mpegurl",
                headers={"Access-Control-Allow-Origin": "*"}
            )

    except Exception as e:
        logger.error(f"Playlist Error: {e}")
        return Response(status_code=500)

@app.get("/proxy/segment")
async def proxy_segment(url: str):
    # Generatore per lo streaming
    async def iter_file():
        # Creiamo la sessione
        async with AsyncSession(impersonate="chrome") as client:
            try:
                # FIX: Usiamo await client.get con stream=True invece di async with client.request
                resp = await client.get(url, headers=PROXY_HEADERS, stream=True)
                
                if resp.status_code != 200:
                    logger.error(f"Segment Error Status: {resp.status_code}")
                    yield b""
                    return

                # Iteriamo sul contenuto
                async for chunk in resp.aiter_content():
                    yield chunk

            except Exception as e:
                logger.error(f"Segment Exception: {e}")

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
